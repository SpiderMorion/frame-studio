from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from eink_frame.orchestration import (
    GenerationRunRepository,
    build_current_frame_lock_statement,
)
from eink_frame.persistence import Base, CurrentFrame, GenerationRunStatus


def _seed_current_frame(session: Session, **values: object) -> CurrentFrame:
    current = CurrentFrame(id=1, **values)
    session.add(current)
    session.flush()
    return current


def test_generation_run_lifecycle_publishes_current_frame_atomically() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    planned = datetime.now(UTC).replace(microsecond=0)
    source_asset_id = uuid.uuid4()
    generate_job_id = uuid.uuid4()
    render_job_id = uuid.uuid4()
    frame_id = uuid.uuid4()

    with Session(engine) as session:
        _seed_current_frame(session)
        repository = GenerationRunRepository(session)
        run = repository.ensure_planned(
            planned_display_at=planned,
            generation_due_at=planned - timedelta(minutes=15),
            provider="comfyui",
            model="qwen-image-2512",
            now=planned - timedelta(minutes=15),
        )
        duplicate = repository.ensure_planned(
            planned_display_at=planned,
            generation_due_at=planned - timedelta(minutes=15),
            provider="comfyui",
            model="qwen-image-2512",
            now=planned - timedelta(minutes=14),
        )
        assert duplicate.id == run.id
        repository.link_generate_job(
            run_id=run.id, job_id=generate_job_id, now=planned - timedelta(minutes=15)
        )
        assert run.status == GenerationRunStatus.QUEUED
        repository.generation_started(run_id=run.id, now=planned - timedelta(minutes=14))
        assert run.status == GenerationRunStatus.GENERATING
        repository.generation_succeeded(
            run_id=run.id,
            source_asset_id=source_asset_id,
            render_job_id=render_job_id,
            weather_snapshot={"temperature_c": 20},
            prompt_snapshot={"final_prompt": "weather"},
            now=planned - timedelta(minutes=10),
        )
        repository.render_succeeded(
            run_id=run.id, frame_id=frame_id, now=planned - timedelta(minutes=5)
        )
        assert repository.publish_due(now=planned) == run.id
        run_id = run.id
        session.commit()

    with Session(engine) as session:
        current = session.get(CurrentFrame, 1)
        assert current is not None
        assert current.generation_run_id == run_id
        assert current.frame_id == frame_id
        assert session.get(type(run), run_id).status == GenerationRunStatus.PUBLISHED


def test_publish_due_supersedes_older_ready_frame() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(UTC).replace(microsecond=0)
    with Session(engine) as session:
        _seed_current_frame(session)
        repository = GenerationRunRepository(session)
        runs = []
        for minutes in (-20, -10):
            planned = now + timedelta(minutes=minutes)
            run = repository.ensure_planned(
                planned_display_at=planned,
                generation_due_at=planned - timedelta(minutes=15),
                provider="comfyui",
                model="qwen-image-2512",
                now=planned - timedelta(minutes=15),
            )
            run.status = GenerationRunStatus.READY
            run.frame_id = uuid.uuid4()
            runs.append(run)
        assert repository.publish_due(now=now) == runs[1].id
        assert runs[0].status == GenerationRunStatus.SUPERSEDED
        assert runs[1].status == GenerationRunStatus.PUBLISHED


def test_publish_due_requires_migrated_current_frame_singleton() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with (
        Session(engine) as session,
        pytest.raises(RuntimeError, match="current frame singleton is missing"),
    ):
        GenerationRunRepository(session).publish_due(now=datetime.now(UTC))


@pytest.mark.postgresql_sql
@pytest.mark.no_live_postgresql
def test_current_frame_lock_statement_compiles_to_postgresql_for_update() -> None:
    compiled = str(
        build_current_frame_lock_statement().compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "WHERE current_frame.id = 1" in compiled
    assert compiled.rstrip().endswith("FOR UPDATE")


def test_publish_due_keeps_sequence_monotonic_across_scheduled_publications() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    first_planned = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)

    with Session(engine) as session:
        current = _seed_current_frame(session, sequence=2_000_000_000)
        repository = GenerationRunRepository(session)
        sequences = []
        for planned in (first_planned, first_planned + timedelta(hours=1)):
            run = repository.ensure_planned(
                planned_display_at=planned,
                generation_due_at=planned - timedelta(minutes=15),
                provider="comfyui",
                model="qwen-image-2512",
                now=planned - timedelta(minutes=15),
            )
            run.status = GenerationRunStatus.READY
            run.frame_id = uuid.uuid4()
            assert repository.publish_due(now=planned) == run.id
            sequences.append(current.sequence)

        assert sequences == [2_000_000_001, 2_000_000_002]


def test_publish_due_does_not_overwrite_newer_manual_publication() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    planned = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)
    manual_frame_id = uuid.uuid4()

    with Session(engine) as session:
        current = _seed_current_frame(
            session,
            frame_id=manual_frame_id,
            sequence=500,
            activated_at=planned + timedelta(minutes=1),
        )
        repository = GenerationRunRepository(session)
        run = repository.ensure_planned(
            planned_display_at=planned,
            generation_due_at=planned - timedelta(minutes=15),
            provider="comfyui",
            model="qwen-image-2512",
            now=planned - timedelta(minutes=15),
        )
        run.status = GenerationRunStatus.READY
        run.frame_id = uuid.uuid4()

        assert repository.publish_due(now=planned + timedelta(minutes=2)) is None
        assert run.status == GenerationRunStatus.SUPERSEDED
        assert current.frame_id == manual_frame_id
        assert current.sequence == 500
        assert current.activated_at == planned + timedelta(minutes=1)


def test_manual_publication_before_future_schedule_does_not_block_it_when_due() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    manual_at = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)
    planned = manual_at + timedelta(hours=1)

    with Session(engine) as session:
        current = _seed_current_frame(
            session,
            frame_id=uuid.uuid4(),
            sequence=500,
            activated_at=manual_at,
        )
        repository = GenerationRunRepository(session)
        run = repository.ensure_planned(
            planned_display_at=planned,
            generation_due_at=planned - timedelta(minutes=15),
            provider="comfyui",
            model="qwen-image-2512",
            now=planned - timedelta(minutes=15),
        )
        run.status = GenerationRunStatus.READY
        run.frame_id = uuid.uuid4()

        assert repository.publish_due(now=planned - timedelta(seconds=1)) is None
        assert run.status == GenerationRunStatus.READY
        assert repository.publish_due(now=planned) == run.id
        assert current.frame_id == run.frame_id
        assert current.sequence == int(planned.timestamp())
