from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from eink_frame.persistence import (
    Base,
    CurrentFrame,
    GenerationRun,
    GenerationRunStatus,
    Job,
    JobType,
    ScheduleConfig,
    ScheduleMode,
)
from eink_frame.scheduler_service import WeatherScheduler
from eink_frame.weather import SCENE_CATALOG, WeatherSnapshot, build_prompt_snapshot


def _create_schema_with_current_frame(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(CurrentFrame(id=1))
        session.commit()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeWeatherProvider:
    async def fetch_snapshot(
        self,
        *,
        latitude: float,
        longitude: float,
        timezone_name: str,
        target_at: datetime,
    ) -> WeatherSnapshot:
        return WeatherSnapshot(
            provider="fake",
            latitude=latitude,
            longitude=longitude,
            timezone=timezone_name,
            target_at=target_at,
            fetched_at=target_at,
            sunrise=target_at.replace(hour=5),
            sunset=target_at.replace(hour=19),
            temperature_c=21,
            apparent_temperature_c=20,
            precipitation_probability_pct=10,
            precipitation_mm=0,
            cloud_cover_pct=25,
            visibility_m=20000,
            wind_speed_kmh=12,
            wind_direction_deg=315,
            wind_gust_kmh=20,
            weather_code=1,
            raw_payload_sha256=hashlib.sha256(b"weather").hexdigest(),
        )


@pytest.mark.anyio
async def test_scheduler_enqueues_qwen_at_t_minus_15_idempotently() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_schema_with_current_frame(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 8, 23, 4, 45, tzinfo=UTC)
    with Session(engine) as session:
        session.add(
            ScheduleConfig(
                id=1,
                enabled=True,
                mode=ScheduleMode.CUSTOM,
                timezone="Europe/Moscow",
                interval_minutes=120,
                anchor_time="08:00",
                custom_times=["08:00"],
                quiet_start=None,
                quiet_end=None,
                lead_minutes=15,
                updated_at=now,
            )
        )
        session.commit()

    scheduler = WeatherScheduler(
        session_factory=factory,
        weather_provider=FakeWeatherProvider(),
        latitude=47.2392184,
        longitude=38.8755031,
        timezone_name="Europe/Moscow",
        location_name="Taganrog, Russia",
    )
    assert await scheduler.tick(now=now) == 1
    assert await scheduler.tick(now=now) == 0

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(GenerationRun)) == 1
        assert session.scalar(select(func.count()).select_from(Job)) == 1
        run = session.scalar(select(GenerationRun))
        job = session.scalar(select(Job))
        assert run is not None and job is not None
        assert run.status == GenerationRunStatus.QUEUED
        assert run.planned_display_at.replace(tzinfo=UTC) == datetime(2026, 8, 23, 5, 0, tzinfo=UTC)
        assert run.generate_job_id == job.id
        assert run.weather_snapshot is not None
        assert run.prompt_snapshot is not None
        assert job.job_type == JobType.GENERATE
        assert job.payload["model"] == "qwen-image-2512"
        assert job.payload["generation_run_id"] == str(run.id)
        assert "Taganrog" not in job.payload["prompt"]
        assert run.prompt_snapshot["water_mode"] == "none"
        assert "sea, ocean, bay, river, harbor, port" in job.payload["negative_prompt"]


@pytest.mark.anyio
async def test_scheduler_does_nothing_when_disabled() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_schema_with_current_frame(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    scheduler = WeatherScheduler(
        session_factory=factory,
        weather_provider=FakeWeatherProvider(),
        latitude=47.2392184,
        longitude=38.8755031,
        timezone_name="Europe/Moscow",
        location_name="Taganrog, Russia",
    )
    assert await scheduler.tick(now=datetime.now(ZoneInfo("Europe/Moscow"))) == 0


@pytest.mark.anyio
async def test_scheduler_records_scene_metadata_and_avoids_six_recent_scenes() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_schema_with_current_frame(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 8, 23, 4, 45, tzinfo=UTC)
    provider = FakeWeatherProvider()
    target = datetime(2026, 8, 23, 8, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    snapshot = await provider.fetch_snapshot(
        latitude=47.2392184,
        longitude=38.8755031,
        timezone_name="Europe/Moscow",
        target_at=target,
    )
    baseline_scene = build_prompt_snapshot(
        snapshot, location_name="Taganrog, Russia"
    ).scene_id
    other_scenes = [scene.scene_id for scene in SCENE_CATALOG if scene.scene_id != baseline_scene]
    recent_scene_ids = [baseline_scene, *other_scenes[:5]]
    with Session(engine) as session:
        session.add(
            ScheduleConfig(
                id=1,
                enabled=True,
                mode=ScheduleMode.CUSTOM,
                timezone="Europe/Moscow",
                interval_minutes=120,
                anchor_time="08:00",
                custom_times=["08:00"],
                quiet_start=None,
                quiet_end=None,
                lead_minutes=15,
                updated_at=now,
            )
        )
        for index, scene_id in enumerate(recent_scene_ids):
            planned = now.replace(hour=3) - index * (target - target.replace(hour=7))
            session.add(
                GenerationRun(
                    id=uuid.uuid4(),
                    planned_display_at=planned,
                    generation_due_at=planned,
                    provider="comfyui",
                    model="qwen-image-2512",
                    status=GenerationRunStatus.PLANNED,
                    prompt_snapshot={"scene_id": scene_id},
                    created_at=planned,
                    updated_at=planned,
                )
            )
        session.commit()

    scheduler = WeatherScheduler(
        session_factory=factory,
        weather_provider=provider,
        latitude=47.2392184,
        longitude=38.8755031,
        timezone_name="Europe/Moscow",
        location_name="Taganrog, Russia",
    )
    assert await scheduler.tick(now=now) == 1

    with Session(engine) as session:
        newest = session.scalar(
            select(GenerationRun).order_by(GenerationRun.planned_display_at.desc())
        )
        assert newest is not None and newest.prompt_snapshot is not None
        assert newest.prompt_snapshot["scene_id"] not in recent_scene_ids
        assert newest.prompt_snapshot["scene_family"] in {"city", "landscape"}
        assert newest.prompt_snapshot["visual_profile_version"] == "taganrog-scene-profiles-v2"
        assert newest.prompt_snapshot["water_mode"] == "none"


@pytest.mark.anyio
async def test_scheduler_rotation_counts_only_chronological_prior_scene_runs() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _create_schema_with_current_frame(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    now = datetime(2026, 8, 23, 4, 45, tzinfo=UTC)
    display_at = datetime(2026, 8, 23, 5, 0, tzinfo=UTC)
    with Session(engine) as session:
        session.add(
            ScheduleConfig(
                id=1,
                enabled=True,
                mode=ScheduleMode.CUSTOM,
                timezone="Europe/Moscow",
                interval_minutes=120,
                anchor_time="08:00",
                custom_times=["08:00"],
                quiet_start=None,
                quiet_end=None,
                lead_minutes=15,
                updated_at=now,
            )
        )
        for index in range(7):
            planned = display_at - timedelta(days=7 - index)
            session.add(
                GenerationRun(
                    id=uuid.uuid4(),
                    planned_display_at=planned,
                    generation_due_at=planned,
                    provider="comfyui",
                    model="qwen-image-2512",
                    status=GenerationRunStatus.PLANNED,
                    prompt_snapshot={"scene_id": f"prior-{index}", "scene_family": "city"},
                    created_at=planned,
                    updated_at=planned,
                )
            )
        for index in range(8):
            planned = display_at + timedelta(days=index + 1)
            session.add(
                GenerationRun(
                    id=uuid.uuid4(),
                    planned_display_at=planned,
                    generation_due_at=planned,
                    provider="comfyui",
                    model="qwen-image-2512",
                    status=GenerationRunStatus.PLANNED,
                    prompt_snapshot={
                        "scene_id": SCENE_CATALOG[8 + index % 4].scene_id,
                        "scene_family": SCENE_CATALOG[8 + index % 4].family,
                        "water_mode": SCENE_CATALOG[8 + index % 4].water_mode,
                    },
                    created_at=planned,
                    updated_at=planned,
                )
            )
        session.commit()

    scheduler = WeatherScheduler(
        session_factory=factory,
        weather_provider=FakeWeatherProvider(),
        latitude=47.2392184,
        longitude=38.8755031,
        timezone_name="Europe/Moscow",
        location_name="Taganrog, Russia",
    )
    assert await scheduler.tick(now=now) == 1

    with Session(engine) as session:
        current = session.scalar(
            select(GenerationRun).where(GenerationRun.planned_display_at == display_at)
        )
        assert current is not None and current.prompt_snapshot is not None
        assert current.prompt_snapshot["scene_family"] == "city"
        assert current.prompt_snapshot["water_mode"] == "none"
