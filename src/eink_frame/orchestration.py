from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, select, update
from sqlalchemy.orm import Session

from .persistence import CurrentFrame, GenerationRun, GenerationRunStatus


def build_current_frame_lock_statement() -> Select[tuple[CurrentFrame]]:
    return select(CurrentFrame).where(CurrentFrame.id == 1).with_for_update()


def _as_aware(value: datetime) -> datetime:
    return value if value.utcoffset() is not None else value.replace(tzinfo=UTC)


class GenerationRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def ensure_planned(
        self,
        *,
        planned_display_at: datetime,
        generation_due_at: datetime,
        provider: str,
        model: str,
        now: datetime,
    ) -> GenerationRun:
        existing = self._session.scalar(
            select(GenerationRun).where(GenerationRun.planned_display_at == planned_display_at)
        )
        if existing is not None:
            return existing
        run = GenerationRun(
            id=uuid.uuid4(),
            planned_display_at=planned_display_at,
            generation_due_at=generation_due_at,
            provider=provider,
            model=model,
            status=GenerationRunStatus.PLANNED,
            created_at=now,
            updated_at=now,
        )
        self._session.add(run)
        self._session.flush()
        return run

    def link_generate_job(self, *, run_id: uuid.UUID, job_id: uuid.UUID, now: datetime) -> None:
        run = self._required(run_id)
        run.generate_job_id = job_id
        run.status = GenerationRunStatus.QUEUED
        run.updated_at = now
        self._session.flush()

    def generation_started(self, *, run_id: uuid.UUID, now: datetime) -> None:
        run = self._required(run_id)
        run.status = GenerationRunStatus.GENERATING
        run.generation_started_at = now
        run.updated_at = now
        self._session.flush()

    def generation_succeeded(
        self,
        *,
        run_id: uuid.UUID,
        source_asset_id: uuid.UUID,
        render_job_id: uuid.UUID,
        weather_snapshot: dict[str, Any],
        prompt_snapshot: dict[str, Any],
        now: datetime,
    ) -> None:
        run = self._required(run_id)
        run.source_asset_id = source_asset_id
        run.render_job_id = render_job_id
        run.weather_snapshot = weather_snapshot
        run.prompt_snapshot = prompt_snapshot
        run.status = GenerationRunStatus.RENDERING
        run.generation_completed_at = now
        run.render_started_at = now
        run.updated_at = now
        self._session.flush()

    def render_succeeded(self, *, run_id: uuid.UUID, frame_id: uuid.UUID, now: datetime) -> None:
        run = self._required(run_id)
        run.frame_id = frame_id
        run.status = GenerationRunStatus.READY
        run.render_completed_at = now
        run.updated_at = now
        self._session.flush()

    def publish_due(self, *, now: datetime) -> uuid.UUID | None:
        current = self._session.scalar(build_current_frame_lock_statement())
        if current is None:
            raise RuntimeError("current frame singleton is missing")
        selected = self._session.scalar(
            select(GenerationRun)
            .where(
                GenerationRun.status == GenerationRunStatus.READY,
                GenerationRun.planned_display_at <= now,
                GenerationRun.frame_id.is_not(None),
            )
            .order_by(GenerationRun.planned_display_at.desc(), GenerationRun.id.desc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if selected is None:
            return None
        if self._current_publication_wins(current=current, selected=selected):
            self._supersede_older_ready(selected=selected, now=now)
            selected.status = GenerationRunStatus.SUPERSEDED
            selected.updated_at = now
            self._session.flush()
            return None
        self._supersede_older_ready(selected=selected, now=now)
        selected.status = GenerationRunStatus.PUBLISHED
        selected.published_at = now
        selected.updated_at = now
        current.generation_run_id = selected.id
        current.frame_id = selected.frame_id
        scheduled_sequence = int(selected.planned_display_at.timestamp())
        current.sequence = max((current.sequence or 0) + 1, scheduled_sequence)
        current.activated_at = now
        self._session.flush()
        return selected.id

    def _supersede_older_ready(self, *, selected: GenerationRun, now: datetime) -> None:
        self._session.execute(
            update(GenerationRun)
            .where(
                GenerationRun.status == GenerationRunStatus.READY,
                GenerationRun.planned_display_at <= selected.planned_display_at,
                GenerationRun.id != selected.id,
            )
            .values(status=GenerationRunStatus.SUPERSEDED, updated_at=now)
        )

    def _current_publication_wins(
        self, *, current: CurrentFrame, selected: GenerationRun
    ) -> bool:
        if current.activated_at is None:
            return False
        activated_at = _as_aware(current.activated_at)
        planned_display_at = _as_aware(selected.planned_display_at)
        if activated_at < planned_display_at:
            return False
        if current.generation_run_id is None:
            return True
        current_run = self._session.get(GenerationRun, current.generation_run_id)
        if current_run is None:
            raise RuntimeError("current frame references a missing generation run")
        published_at = (
            _as_aware(current_run.published_at) if current_run.published_at is not None else None
        )
        was_scheduled = (
            current_run.status is GenerationRunStatus.PUBLISHED
            and published_at == activated_at
        )
        return not was_scheduled or _as_aware(current_run.planned_display_at) >= planned_display_at

    def _required(self, run_id: uuid.UUID) -> GenerationRun:
        run = self._session.get(GenerationRun, run_id)
        if run is None:
            raise LookupError("generation run not found")
        return run
