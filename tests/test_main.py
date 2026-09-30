from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine

from eink_frame.main import build_app
from eink_frame.persistence import Base


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_build_app_wires_database_and_worker_auth_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'app.sqlite3'}"
    Base.metadata.create_all(create_engine(database_url))
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("EINK_FRAME_WORKER_TOKEN", "environment-worker-token")
    monkeypatch.setenv("EINK_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    frontend_dist = tmp_path / "frontend"
    frontend_dist.mkdir()
    (frontend_dist / "index.html").write_text("<h1>Frame Studio</h1>", encoding="utf-8")
    monkeypatch.setenv("EINK_FRONTEND_DIST", str(frontend_dist))

    app = build_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        queued = await client.post(
            "/api/v1/jobs",
            json={"job_type": "render", "payload": {}, "max_attempts": 2},
            headers={"Idempotency-Key": "env-job"},
        )
        unauthorized = await client.post(
            "/api/v1/workers/claim",
            json={"worker_id": "spark-a", "lease_seconds": 60},
        )
        authorized = await client.post(
            "/api/v1/workers/claim",
            json={"worker_id": "spark-a", "lease_seconds": 60},
            headers={"Authorization": "Bearer environment-worker-token"},
        )
        frontend = await client.get("/")

    assert queued.status_code == 202
    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert frontend.status_code == 200
    assert "Frame Studio" in frontend.text
