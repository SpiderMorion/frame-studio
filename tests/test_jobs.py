from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from eink_frame.jobs import CompletionResult, JobRepository, build_claim_statement
from eink_frame.persistence import Base, JobErrorCode, JobStatus, JobType


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        yield db


def test_job_enums_are_stored_as_stable_lowercase_codes(session: Session) -> None:
    repository = JobRepository(session)
    now = datetime.now(UTC)
    repository.enqueue(
        job_type=JobType.RENDER,
        payload={},
        idempotency_key="stable-enums",
        max_attempts=1,
        available_at=now,
        now=now,
    )

    stored = session.execute(
        text("SELECT job_type, status FROM jobs WHERE idempotency_key = :key"),
        {"key": "stable-enums"},
    ).one()

    assert stored == ("render", "queued")


def test_enqueue_rejects_non_positive_max_attempts(session: Session) -> None:
    repository = JobRepository(session)
    now = datetime.now(UTC)

    with pytest.raises(ValueError, match="max_attempts must be positive"):
        repository.enqueue(
            job_type=JobType.RENDER,
            payload={},
            idempotency_key="invalid-attempts",
            max_attempts=0,
            available_at=now,
            now=now,
        )


def test_enqueue_is_idempotent_by_key(session: Session) -> None:
    repository = JobRepository(session)
    now = datetime.now(UTC)

    first = repository.enqueue(
        job_type=JobType.RENDER,
        payload={"asset_id": "asset-1"},
        idempotency_key="render:asset-1:preset-1:1",
        max_attempts=3,
        available_at=now,
        now=now,
    )
    session.commit()
    second = repository.enqueue(
        job_type=JobType.RENDER,
        payload={"ignored": "because first enqueue wins"},
        idempotency_key="render:asset-1:preset-1:1",
        max_attempts=5,
        available_at=now + timedelta(hours=1),
        now=now,
    )

    assert second.id == first.id
    assert second.payload == {"asset_id": "asset-1"}
    assert second.max_attempts == 3
    assert second.status is JobStatus.QUEUED


def test_claim_leases_oldest_available_job(session: Session) -> None:
    repository = JobRepository(session)
    now = datetime.now(UTC)
    first = repository.enqueue(
        job_type=JobType.RENDER,
        payload={"order": 1},
        idempotency_key="first",
        max_attempts=3,
        available_at=now - timedelta(minutes=2),
        now=now - timedelta(minutes=2),
    )
    repository.enqueue(
        job_type=JobType.RENDER,
        payload={"order": 2},
        idempotency_key="second",
        max_attempts=3,
        available_at=now - timedelta(minutes=1),
        now=now - timedelta(minutes=1),
    )
    session.commit()

    claimed = repository.claim_next(
        worker_id="worker-a", now=now, lease_duration=timedelta(minutes=5)
    )

    assert claimed is not None
    assert claimed.id == first.id
    assert claimed.status is JobStatus.LEASED
    assert claimed.attempt == 1
    assert claimed.lease_owner == "worker-a"
    assert claimed.heartbeat_at == now
    assert claimed.lease_expires_at == now + timedelta(minutes=5)


@pytest.mark.postgresql_sql
@pytest.mark.no_live_postgresql
def test_claim_statement_uses_postgresql_skip_locked() -> None:
    statement = build_claim_statement(datetime.now(UTC))

    compiled = str(statement.compile(dialect=postgresql.dialect())).upper()

    assert "FOR UPDATE SKIP LOCKED" in compiled
    assert "ORDER BY" in compiled
    assert "LIMIT" in compiled


def test_heartbeat_is_compare_and_set_by_current_lease_owner(session: Session) -> None:
    repository = JobRepository(session)
    now = datetime.now(UTC)
    job = repository.enqueue(
        job_type=JobType.GENERATE,
        payload={},
        idempotency_key="heartbeat",
        max_attempts=2,
        available_at=now,
        now=now,
    )
    repository.claim_next(worker_id="worker-a", now=now, lease_duration=timedelta(minutes=5))
    session.commit()

    rejected = repository.heartbeat(
        job_id=job.id,
        worker_id="worker-b",
        now=now + timedelta(minutes=1),
        lease_duration=timedelta(minutes=5),
    )
    accepted = repository.heartbeat(
        job_id=job.id,
        worker_id="worker-a",
        now=now + timedelta(minutes=1),
        lease_duration=timedelta(minutes=5),
    )

    assert rejected is False
    assert accepted is True
    session.refresh(job)
    assert job.heartbeat_at is not None
    assert job.lease_expires_at is not None
    assert job.heartbeat_at.replace(tzinfo=UTC) == now + timedelta(minutes=1)
    assert job.lease_expires_at.replace(tzinfo=UTC) == now + timedelta(minutes=6)


def test_completion_is_compare_and_set_and_result_is_written_once(session: Session) -> None:
    repository = JobRepository(session)
    now = datetime.now(UTC)
    job = repository.enqueue(
        job_type=JobType.RENDER,
        payload={},
        idempotency_key="complete",
        max_attempts=2,
        available_at=now,
        now=now,
    )
    repository.claim_next(worker_id="worker-a", now=now, lease_duration=timedelta(minutes=5))
    session.commit()

    rejected = repository.complete(
        job_id=job.id,
        worker_id="worker-b",
        result_refs={"frame_asset_id": "wrong"},
        now=now + timedelta(minutes=1),
    )
    accepted = repository.complete(
        job_id=job.id,
        worker_id="worker-a",
        result_refs={"frame_asset_id": "frame-1"},
        now=now + timedelta(minutes=1),
    )
    idempotent = repository.complete(
        job_id=job.id,
        worker_id="worker-a",
        result_refs={"frame_asset_id": "frame-1"},
        now=now + timedelta(minutes=2),
    )
    repeated = repository.complete(
        job_id=job.id,
        worker_id="worker-a",
        result_refs={"frame_asset_id": "frame-2"},
        now=now + timedelta(minutes=3),
    )

    assert rejected is CompletionResult.CONFLICT
    assert accepted is CompletionResult.NEW
    assert idempotent is CompletionResult.REPLAY
    assert repeated is CompletionResult.CONFLICT
    session.refresh(job)
    assert job.status is JobStatus.SUCCEEDED
    assert job.result_refs == {"frame_asset_id": "frame-1"}
    assert job.lease_owner is None


def test_retryable_failure_is_bounded_by_max_attempts(session: Session) -> None:
    repository = JobRepository(session)
    now = datetime.now(UTC)
    job = repository.enqueue(
        job_type=JobType.GENERATE,
        payload={},
        idempotency_key="bounded-retry",
        max_attempts=2,
        available_at=now,
        now=now,
    )
    repository.claim_next(worker_id="worker-a", now=now, lease_duration=timedelta(minutes=5))

    retried = repository.fail(
        job_id=job.id,
        worker_id="worker-a",
        error_code=JobErrorCode.WORKER_ERROR,
        error_detail="provider timeout",
        retryable=True,
        now=now + timedelta(minutes=1),
        retry_delay=timedelta(minutes=2),
    )

    assert retried is True
    assert job.status is JobStatus.RETRY_WAIT
    assert job.available_at == now + timedelta(minutes=3)
    assert job.error_code is JobErrorCode.WORKER_ERROR
    repository.claim_next(
        worker_id="worker-b",
        now=now + timedelta(minutes=3),
        lease_duration=timedelta(minutes=5),
    )

    exhausted = repository.fail(
        job_id=job.id,
        worker_id="worker-b",
        error_code=JobErrorCode.WORKER_ERROR,
        error_detail="provider timeout again",
        retryable=True,
        now=now + timedelta(minutes=4),
        retry_delay=timedelta(minutes=2),
    )

    assert exhausted is True
    assert job.status is JobStatus.FAILED
    assert job.attempt == 2
    assert job.completed_at == now + timedelta(minutes=4)


def test_expired_leases_are_recovered_or_exhausted(session: Session) -> None:
    repository = JobRepository(session)
    now = datetime.now(UTC)
    exhausted = repository.enqueue(
        job_type=JobType.RENDER,
        payload={},
        idempotency_key="expired-final",
        max_attempts=1,
        available_at=now,
        now=now,
    )
    retrying = repository.enqueue(
        job_type=JobType.RENDER,
        payload={},
        idempotency_key="expired-retry",
        max_attempts=2,
        available_at=now,
        now=now + timedelta(seconds=1),
    )
    repository.claim_next(worker_id="dead-a", now=now, lease_duration=timedelta(minutes=1))
    repository.claim_next(worker_id="dead-b", now=now, lease_duration=timedelta(minutes=1))

    recovered = repository.recover_expired(
        now=now + timedelta(minutes=2), retry_delay=timedelta(minutes=3)
    )

    assert recovered == 2
    assert exhausted.status is JobStatus.FAILED
    assert exhausted.error_code is JobErrorCode.LEASE_EXPIRED
    assert retrying.status is JobStatus.RETRY_WAIT
    assert retrying.available_at == now + timedelta(minutes=5)
    assert retrying.error_code is JobErrorCode.LEASE_EXPIRED
