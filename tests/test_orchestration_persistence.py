from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from eink_frame.persistence import (
    Base,
    CurrentFrame,
    DeviceStatus,
    GenerationRun,
    GenerationRunStatus,
    ScheduleConfig,
    ScheduleMode,
)


def test_singleton_schedule_and_current_frame_are_database_enforced() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    with Session(engine) as session:
        session.add(
            ScheduleConfig(
                id=1,
                enabled=True,
                mode=ScheduleMode.INTERVAL,
                timezone="Europe/Moscow",
                interval_minutes=120,
                anchor_time="08:00",
                custom_times=[],
                quiet_start="23:00",
                quiet_end="07:00",
                lead_minutes=15,
                updated_at=now,
            )
        )
        session.commit()
        session.add(
            ScheduleConfig(
                id=2,
                enabled=True,
                mode=ScheduleMode.CUSTOM,
                timezone="Europe/Moscow",
                interval_minutes=None,
                anchor_time=None,
                custom_times=["08:00"],
                quiet_start=None,
                quiet_end=None,
                lead_minutes=15,
                updated_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_generation_run_planned_display_time_is_unique() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    planned = datetime.now(UTC).replace(microsecond=0)
    with Session(engine) as session:
        for _ in range(2):
            session.add(
                GenerationRun(
                    id=uuid.uuid4(),
                    planned_display_at=planned,
                    generation_due_at=planned - timedelta(minutes=15),
                    provider="comfyui",
                    model="qwen-image-2512",
                    status=GenerationRunStatus.PLANNED,
                    created_at=planned - timedelta(minutes=15),
                    updated_at=planned - timedelta(minutes=15),
                )
            )
        with pytest.raises(IntegrityError):
            session.commit()


def test_current_frame_and_device_status_are_singletons() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    with Session(engine) as session:
        session.add_all(
            [
                CurrentFrame(id=1, generation_run_id=None, frame_id=None, activated_at=None),
                DeviceStatus(
                    id=1,
                    firmware_version=None,
                    last_frame_id=None,
                    wifi_rssi_dbm=None,
                    battery_mv=None,
                    battery_pct=None,
                    error_code=None,
                    error_detail=None,
                    reported_at=now,
                ),
            ]
        )
        session.commit()
        assert session.get(CurrentFrame, 1) is not None
        assert session.get(DeviceStatus, 1) is not None
