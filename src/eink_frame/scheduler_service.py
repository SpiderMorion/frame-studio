from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable
from datetime import UTC, datetime, time
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .jobs import JobRepository
from .orchestration import GenerationRunRepository
from .persistence import GenerationRun, GenerationRunStatus, JobType, ScheduleConfig
from .schedule import ScheduleMode, ScheduleSpec, due_slots
from .weather import WeatherSnapshot, build_prompt_snapshot
from .weather_provider import OpenMeteoProvider


class WeatherProvider(Protocol):
    def fetch_snapshot(
        self,
        *,
        latitude: float,
        longitude: float,
        timezone_name: str,
        target_at: datetime,
    ) -> Awaitable[WeatherSnapshot]: ...


class WeatherScheduler:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        weather_provider: WeatherProvider,
        latitude: float,
        longitude: float,
        timezone_name: str,
        location_name: str,
    ) -> None:
        self._session_factory = session_factory
        self._weather_provider = weather_provider
        self._latitude = latitude
        self._longitude = longitude
        self._timezone_name = timezone_name
        self._location_name = location_name

    async def tick(self, *, now: datetime) -> int:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("scheduler now must be timezone-aware")
        now_utc = now.astimezone(UTC)
        with self._session_factory() as session:
            GenerationRunRepository(session).publish_due(now=now_utc)
            config = session.get(ScheduleConfig, 1)
            session.commit()
        if config is None or not config.enabled:
            return 0
        spec = _schedule_spec(config)
        enqueued = 0
        for due in due_slots(spec, now_utc, lookback_minutes=15):
            display_utc = due.display_at.astimezone(UTC)
            generation_utc = due.generation_at.astimezone(UTC)
            with self._session_factory() as session:
                repository = GenerationRunRepository(session)
                run = repository.ensure_planned(
                    planned_display_at=display_utc,
                    generation_due_at=generation_utc,
                    provider="comfyui",
                    model="qwen-image-2512",
                    now=now_utc,
                )
                run_id = run.id
                already_enqueued = (
                    run.generate_job_id is not None or run.status is not GenerationRunStatus.PLANNED
                )
                session.commit()
            if already_enqueued:
                continue
            snapshot = await self._weather_provider.fetch_snapshot(
                latitude=self._latitude,
                longitude=self._longitude,
                timezone_name=self._timezone_name,
                target_at=due.display_at,
            )
            with self._session_factory() as session:
                prior_scene_filter = (
                    GenerationRun.planned_display_at < display_utc,
                    GenerationRun.prompt_snapshot.is_not(None),
                    GenerationRun.prompt_snapshot["scene_id"].as_string().is_not(None),
                )
                rotation_index = session.scalar(
                    select(func.count(GenerationRun.id)).where(*prior_scene_filter)
                ) or 0
                stored_snapshots = session.scalars(
                    select(GenerationRun.prompt_snapshot)
                    .where(*prior_scene_filter)
                    .order_by(
                        GenerationRun.planned_display_at.desc(), GenerationRun.id.desc()
                    )
                    .limit(6)
                )
                recent_scene_ids = tuple(
                    scene_id
                    for stored_snapshot in stored_snapshots
                    if isinstance(stored_snapshot, dict)
                    and isinstance((scene_id := stored_snapshot.get("scene_id")), str)
                )[:6]
            prompt = build_prompt_snapshot(
                snapshot,
                location_name=self._location_name,
                recent_scene_ids=recent_scene_ids,
                rotation_index=rotation_index,
            )
            with self._session_factory() as session:
                stored_run = session.get(GenerationRun, run_id)
                if stored_run is None or stored_run.generate_job_id is not None:
                    session.rollback()
                    continue
                stored_run.weather_snapshot = snapshot.model_dump(mode="json")
                stored_run.prompt_snapshot = prompt.model_dump(mode="json")
                job = JobRepository(session).enqueue(
                    job_type=JobType.GENERATE,
                    payload={
                        "generation_run_id": str(run_id),
                        "model": "qwen-image-2512",
                        "prompt": prompt.final_prompt,
                        "negative_prompt": prompt.negative_prompt,
                        "seed": int(display_utc.timestamp()),
                        "width": 1600,
                        "height": 1200,
                    },
                    idempotency_key=f"generate:{due.idempotency_key}",
                    max_attempts=3,
                    available_at=now_utc,
                    now=now_utc,
                )
                GenerationRunRepository(session).link_generate_job(
                    run_id=run_id, job_id=job.id, now=now_utc
                )
                session.commit()
            enqueued += 1
        return enqueued


def _schedule_spec(config: ScheduleConfig) -> ScheduleSpec:
    def parse(value: str | None, fallback: str) -> time:
        return time.fromisoformat(value or fallback)

    return ScheduleSpec(
        enabled=config.enabled,
        mode=ScheduleMode(config.mode.value),
        interval_minutes=config.interval_minutes or 120,
        anchor_time=parse(config.anchor_time, "08:00"),
        custom_times=tuple(parse(value, "08:00") for value in config.custom_times),
        quiet_start=parse(config.quiet_start, "00:00") if config.quiet_start else None,
        quiet_end=parse(config.quiet_end, "00:00") if config.quiet_end else None,
        timezone=ZoneInfo(config.timezone),
    )


async def run_scheduler_forever() -> None:
    from sqlalchemy import create_engine

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    engine = create_engine(database_url, pool_pre_ping=True)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    async with httpx.AsyncClient() as client:
        scheduler = WeatherScheduler(
            session_factory=factory,
            weather_provider=OpenMeteoProvider(client=client),
            latitude=float(os.environ.get("EINK_LOCATION_LATITUDE", "47.2392184")),
            longitude=float(os.environ.get("EINK_LOCATION_LONGITUDE", "38.8755031")),
            timezone_name=os.environ.get("EINK_TIMEZONE", "Europe/Moscow"),
            location_name=os.environ.get("EINK_LOCATION_NAME", "Taganrog, Russia"),
        )
        while True:
            try:
                await scheduler.tick(now=datetime.now(UTC))
            except (httpx.HTTPError, KeyError, ValueError, LookupError) as error:
                print(f"scheduler_tick_error {type(error).__name__}: {error}", flush=True)
            await asyncio.sleep(30)


def main() -> None:
    asyncio.run(run_scheduler_forever())
