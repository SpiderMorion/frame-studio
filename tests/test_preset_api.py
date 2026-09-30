from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from eink_frame.api import create_app
from eink_frame.persistence import Base


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_preset_api_creates_default_and_appends_immutable_revision(tmp_path: Path) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    app = create_app(artifact_root=tmp_path, session_factory=factory)
    transport = httpx.ASGITransport(app=app)
    settings = {
        "brightness": 5,
        "contrast": 10,
        "saturation": 0,
        "gamma": 1,
        "sharpness": 10,
        "dithering": "bayer-8x8",
        "dithering_strength": 0.75,
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/v1/presets",
            json={"name": "Outdoor", "settings": settings, "make_default": True},
        )
        listed = await client.get("/api/v1/presets")
        revised = await client.post(
            f"/api/v1/presets/{created.json()['id']}/revisions",
            json={"settings": {**settings, "contrast": 15}},
        )
        source = io.BytesIO()
        Image.new("RGB", (32, 32), "red").save(source, format="PNG")
        converted = await client.post(
            "/api/v1/conversions",
            files={"source": ("red.png", source.getvalue(), "image/png")},
        )

    assert created.status_code == 201
    assert created.json()["revision"] == 1
    assert created.json()["is_default"] is True
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == created.json()["id"]
    assert listed.json()[0]["settings"] == settings
    assert revised.status_code == 201
    assert revised.json()["revision"] == 2
    assert revised.json()["settings"]["contrast"] == 15
    assert converted.status_code == 201
    assert converted.json()["preset"] == {"id": created.json()["id"], "revision": 2}
    assert converted.json()["processing"]["contrast"] == 15
