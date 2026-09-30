from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, cast

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .api import create_app


def build_app() -> FastAPI:
    artifact_root = Path(
        os.environ.get(
            "EINK_ARTIFACT_ROOT",
            str(Path.home() / ".local/share/eink-weather-frame/artifacts"),
        )
    )
    max_upload_bytes = int(os.environ.get("EINK_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
    database_url = os.environ.get("DATABASE_URL")
    factory: sessionmaker[Session] | None = None
    if database_url:
        engine = create_engine(database_url, pool_pre_ping=True)
        factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    rotation_value = os.environ.get("EINK_PANEL_ROTATION", "clockwise")
    if rotation_value not in ("clockwise", "counterclockwise", "transverse"):
        raise ValueError("EINK_PANEL_ROTATION must be clockwise, counterclockwise or transverse")
    panel_rotation = cast(Literal["clockwise", "counterclockwise", "transverse"], rotation_value)
    return create_app(
        artifact_root=artifact_root,
        max_upload_bytes=max_upload_bytes,
        session_factory=factory,
        worker_token=os.environ.get("EINK_FRAME_WORKER_TOKEN"),
        device_token=os.environ.get("EINK_FRAME_DEVICE_TOKEN"),
        panel_rotation=panel_rotation,
        frontend_dist=(
            Path(frontend_path) if (frontend_path := os.environ.get("EINK_FRONTEND_DIST")) else None
        ),
    )


app = build_app()
