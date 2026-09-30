from __future__ import annotations

import os
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, update
from sqlalchemy.orm import Session

from eink_frame.jobs import JobRepository
from eink_frame.orchestration import GenerationRunRepository
from eink_frame.persistence import (
    CurrentFrame,
    GenerationRun,
    GenerationRunStatus,
    Job,
    JobType,
)


@pytest.mark.postgresql_live
@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL is required")
def test_postgresql_skip_locked_claims_distinct_jobs_and_enforces_lease_owner() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    assert engine.dialect.name == "postgresql", "DATABASE_URL must use PostgreSQL"
    now = datetime.now(UTC)
    with Session(engine) as setup:
        setup.execute(delete(Job))
        repository = JobRepository(setup)
        for suffix in ("first", "second"):
            repository.enqueue(
                job_type=JobType.RENDER,
                payload={"name": suffix},
                idempotency_key=f"postgres-live-{suffix}",
                max_attempts=3,
                available_at=now,
                now=now,
            )
        setup.commit()

    first_session = Session(engine)
    second_session = Session(engine)
    try:
        first = JobRepository(first_session).claim_next(
            worker_id="worker-a", now=now, lease_duration=timedelta(minutes=5)
        )
        second = JobRepository(second_session).claim_next(
            worker_id="worker-b", now=now, lease_duration=timedelta(minutes=5)
        )

        assert first is not None
        assert second is not None
        assert first.id != second.id
        assert not JobRepository(second_session).heartbeat(
            job_id=second.id,
            worker_id="wrong-worker",
            now=now + timedelta(seconds=1),
            lease_duration=timedelta(minutes=5),
        )
        assert JobRepository(second_session).heartbeat(
            job_id=second.id,
            worker_id="worker-b",
            now=now + timedelta(seconds=1),
            lease_duration=timedelta(minutes=5),
        )
        second_session.commit()
        first_session.commit()
    finally:
        first_session.close()
        second_session.close()
        engine.dispose()


@pytest.mark.postgresql_live
@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL is required")
def test_postgresql_concurrent_publish_due_preserves_newest_frame_and_sequence() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    assert engine.dialect.name == "postgresql", "DATABASE_URL must use PostgreSQL"
    now = datetime.now(UTC).replace(microsecond=0)
    older_id = uuid.uuid4()
    newer_id = uuid.uuid4()
    initial_sequence = int(now.timestamp()) + 100
    with Session(engine) as setup:
        setup.execute(
            update(GenerationRun)
            .where(GenerationRun.status == GenerationRunStatus.READY)
            .values(status=GenerationRunStatus.SUPERSEDED, updated_at=now)
        )
        current = setup.get(CurrentFrame, 1, with_for_update=True)
        assert current is not None
        current.generation_run_id = None
        current.frame_id = None
        current.sequence = initial_sequence
        current.activated_at = None
        for run_id, planned in (
            (older_id, now - timedelta(minutes=2)),
            (newer_id, now - timedelta(minutes=1)),
        ):
            setup.add(
                GenerationRun(
                    id=run_id,
                    planned_display_at=planned,
                    generation_due_at=planned - timedelta(minutes=15),
                    provider="postgres-live",
                    model="ordering-test",
                    status=GenerationRunStatus.READY,
                    frame_id=uuid.uuid4(),
                    created_at=now,
                    updated_at=now,
                )
            )
        setup.commit()

    barrier = threading.Barrier(2)
    published: list[uuid.UUID | None] = []
    errors: list[Exception] = []

    def publish() -> None:
        try:
            with Session(engine) as session:
                barrier.wait(timeout=10)
                published.append(GenerationRunRepository(session).publish_due(now=now))
                session.commit()
        except Exception as error:  # noqa: BLE001 - report failures from the worker thread
            errors.append(error)

    threads = [threading.Thread(target=publish) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    with Session(engine) as verify:
        current = verify.get(CurrentFrame, 1)
        older = verify.get(GenerationRun, older_id)
        newer = verify.get(GenerationRun, newer_id)
        assert current is not None and older is not None and newer is not None
        assert newer.status == GenerationRunStatus.PUBLISHED
        assert older.status == GenerationRunStatus.SUPERSEDED
        assert current.generation_run_id == newer_id
        assert current.frame_id == newer.frame_id
        assert current.sequence == initial_sequence + 1
        assert published.count(newer_id) == 1
        assert published.count(None) == 1
    engine.dispose()
