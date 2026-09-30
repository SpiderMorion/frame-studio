from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from eink_frame.api import create_app
from eink_frame.persistence import Base, ScheduleConfig, ScheduleMode


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_schedule_api_saves_custom_times_quiet_period_and_next_runs(tmp_path: Path) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    app = create_app(artifact_root=tmp_path, session_factory=factory)
    transport = httpx.ASGITransport(app=app)
    payload = {
        "enabled": True,
        "mode": "custom",
        "interval_minutes": 120,
        "anchor_time": "08:00",
        "custom_times": ["18:00", "08:00", "14:00"],
        "quiet_start": "23:00",
        "quiet_end": "07:00",
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        initial = await client.get("/api/v1/schedule")
        saved = await client.put("/api/v1/schedule", json=payload)
        loaded = await client.get("/api/v1/schedule")
        invalid = await client.put("/api/v1/schedule", json={**payload, "custom_times": []})

    assert initial.status_code == 200
    assert initial.json()["enabled"] is False
    assert initial.json()["next_runs"] == []
    assert saved.status_code == 200
    assert saved.json()["custom_times"] == ["08:00", "14:00", "18:00"]
    assert saved.json()["lead_minutes"] == 15
    assert saved.json()["next_runs"]
    assert loaded.json() == saved.json()
    assert invalid.status_code == 422
    with Session(engine) as session:
        config = session.get(ScheduleConfig, 1)
        assert config is not None
        assert config.mode == ScheduleMode.CUSTOM
        assert config.timezone == "Europe/Moscow"
        assert config.lead_minutes == 15
        assert config.custom_times == ["08:00", "14:00", "18:00"]
        updated_at = config.updated_at
        if updated_at.utcoffset() is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        assert updated_at <= datetime.now(UTC)
