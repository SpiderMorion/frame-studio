from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, cast

from sqlalchemy import Select, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from .persistence import Job, JobErrorCode, JobStatus, JobType


class CompletionResult(Enum):
    NEW = "new"
    REPLAY = "replay"
    CONFLICT = "conflict"


def build_claim_statement(
    now: datetime, job_types: tuple[JobType, ...] | None = None
) -> Select[tuple[Job]]:
    statement = (
        select(Job)
        .where(
            Job.status.in_((JobStatus.QUEUED, JobStatus.RETRY_WAIT)),
            Job.available_at <= now,
            Job.attempt < Job.max_attempts,
        )
        .order_by(Job.available_at, Job.created_at, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job_types:
        statement = statement.where(Job.job_type.in_(job_types))
    return statement


class JobRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(
        self,
        *,
        job_type: JobType,
        payload: dict[str, Any],
        idempotency_key: str,
        max_attempts: int,
        available_at: datetime,
        now: datetime,
        payload_schema_version: int = 1,
    ) -> Job:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        existing = self._session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
        if existing is not None:
            return existing

        job = Job(
            id=uuid.uuid4(),
            job_type=job_type,
            payload_schema_version=payload_schema_version,
            payload=payload,
            idempotency_key=idempotency_key,
            status=JobStatus.QUEUED,
            attempt=0,
            max_attempts=max_attempts,
            available_at=available_at,
            created_at=now,
            updated_at=now,
        )
        self._session.add(job)
        self._session.flush()
        return job

    def claim_next(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        job_types: tuple[JobType, ...] | None = None,
    ) -> Job | None:
        statement = build_claim_statement(now, job_types)
        job = self._session.scalar(statement)
        if job is None:
            return None

        job.status = JobStatus.LEASED
        job.attempt += 1
        job.lease_owner = worker_id
        job.lease_expires_at = now + lease_duration
        job.heartbeat_at = now
        job.updated_at = now
        self._session.flush()
        return job

    def heartbeat(
        self,
        *,
        job_id: uuid.UUID,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> bool:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.status == JobStatus.LEASED,
                    Job.lease_owner == worker_id,
                    Job.lease_expires_at > now,
                )
                .values(
                    heartbeat_at=now,
                    lease_expires_at=now + lease_duration,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            ),
        )
        return bool(result.rowcount)

    def complete(
        self,
        *,
        job_id: uuid.UUID,
        worker_id: str,
        result_refs: dict[str, Any],
        now: datetime,
    ) -> CompletionResult:
        result = cast(
            CursorResult[Any],
            self._session.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.status == JobStatus.LEASED,
                    Job.lease_owner == worker_id,
                    Job.lease_expires_at > now,
                )
                .values(
                    status=JobStatus.SUCCEEDED,
                    result_refs=result_refs,
                    completed_at=now,
                    updated_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                )
                .execution_options(synchronize_session=False)
            ),
        )
        if result.rowcount:
            return CompletionResult.NEW
        job = self._session.scalar(
            select(Job).where(Job.id == job_id).execution_options(populate_existing=True)
        )
        if (
            job is not None
            and job.status is JobStatus.SUCCEEDED
            and job.result_refs == result_refs
        ):
            return CompletionResult.REPLAY
        return CompletionResult.CONFLICT

    def fail(
        self,
        *,
        job_id: uuid.UUID,
        worker_id: str,
        error_code: JobErrorCode,
        error_detail: str,
        retryable: bool,
        now: datetime,
        retry_delay: timedelta,
    ) -> bool:
        job = self._session.scalar(
            select(Job)
            .where(
                Job.id == job_id,
                Job.status == JobStatus.LEASED,
                Job.lease_owner == worker_id,
                Job.lease_expires_at > now,
            )
            .with_for_update()
        )
        if job is None:
            return False

        will_retry = retryable and job.attempt < job.max_attempts
        job.status = JobStatus.RETRY_WAIT if will_retry else JobStatus.FAILED
        job.available_at = now + retry_delay if will_retry else now
        job.completed_at = None if will_retry else now
        job.error_code = error_code
        job.error_detail = error_detail
        job.updated_at = now
        job.lease_owner = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        self._session.flush()
        return True

    def recover_expired(
        self, *, now: datetime, retry_delay: timedelta, batch_size: int = 100
    ) -> int:
        jobs = list(
            self._session.scalars(
                select(Job)
                .where(
                    Job.status == JobStatus.LEASED,
                    Job.lease_expires_at <= now,
                )
                .order_by(Job.lease_expires_at, Job.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        for job in jobs:
            will_retry = job.attempt < job.max_attempts
            job.status = JobStatus.RETRY_WAIT if will_retry else JobStatus.FAILED
            job.available_at = now + retry_delay if will_retry else now
            job.completed_at = None if will_retry else now
            job.error_code = JobErrorCode.LEASE_EXPIRED
            job.error_detail = "worker lease expired"
            job.updated_at = now
            job.lease_owner = None
            job.lease_expires_at = None
            job.heartbeat_at = None
        self._session.flush()
        return len(jobs)
