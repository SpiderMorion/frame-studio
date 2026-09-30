from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from eink_frame.persistence import (
    Asset,
    AssetKind,
    Base,
    ProcessingPreset,
    ProcessingPresetRevision,
)
from eink_frame.presets import PresetRepository


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def test_asset_metadata_round_trips(session: Session) -> None:
    content = b"immutable source"
    asset = Asset(
        id=uuid.uuid4(),
        kind=AssetKind.SOURCE,
        storage_key="sha256/ab/source.png",
        media_type="image/png",
        byte_length=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        metadata_json={"width": 1600, "height": 1200},
        created_at=datetime.now(UTC),
    )

    session.add(asset)
    session.commit()
    session.expire_all()

    stored = session.get(Asset, asset.id)
    assert stored is not None
    assert stored.kind is AssetKind.SOURCE
    assert stored.storage_key == "sha256/ab/source.png"
    assert stored.metadata_json == {"width": 1600, "height": 1200}


def test_asset_metadata_cannot_be_updated(session: Session) -> None:
    asset = Asset(
        id=uuid.uuid4(),
        kind=AssetKind.SOURCE,
        storage_key="sha256/cd/source.png",
        media_type="image/png",
        byte_length=3,
        sha256="a" * 64,
        metadata_json={},
        created_at=datetime.now(UTC),
    )
    session.add(asset)
    session.commit()

    asset.storage_key = "changed"
    with pytest.raises(ValueError, match="asset metadata is immutable"):
        session.commit()


def test_preset_revisions_are_distinct_immutable_snapshots(session: Session) -> None:
    preset_id = uuid.uuid4()
    preset = ProcessingPreset(id=preset_id, name="Outdoor", created_at=datetime.now(UTC))
    first = ProcessingPresetRevision(
        preset_id=preset_id,
        revision=1,
        settings={"dither": "none"},
        created_at=datetime.now(UTC),
    )
    second = ProcessingPresetRevision(
        preset_id=preset_id,
        revision=2,
        settings={"dither": "floyd-steinberg"},
        created_at=datetime.now(UTC),
    )
    session.add_all([preset, first, second])
    session.commit()

    revisions = session.query(ProcessingPresetRevision).order_by(ProcessingPresetRevision.revision)
    assert [(item.revision, item.settings) for item in revisions] == [
        (1, {"dither": "none"}),
        (2, {"dither": "floyd-steinberg"}),
    ]


def test_preset_revision_cannot_be_updated(session: Session) -> None:
    preset_id = uuid.uuid4()
    preset = ProcessingPreset(id=preset_id, name="Indoor", created_at=datetime.now(UTC))
    revision = ProcessingPresetRevision(
        preset_id=preset_id,
        revision=1,
        settings={"contrast": 1.0},
        created_at=datetime.now(UTC),
    )
    session.add_all([preset, revision])
    session.commit()

    revision.settings = {"contrast": 2.0}
    with pytest.raises(ValueError, match="processing preset revisions are immutable"):
        session.commit()


def test_repository_appends_preset_revision_without_changing_prior_snapshot(
    session: Session,
) -> None:
    now = datetime.now(UTC)
    preset = ProcessingPreset(id=uuid.uuid4(), name="Default", created_at=now)
    session.add(preset)
    session.commit()
    repository = PresetRepository(session)

    first = repository.append_revision(preset_id=preset.id, settings={"contrast": 1.0}, now=now)
    second = repository.append_revision(preset_id=preset.id, settings={"contrast": 1.5}, now=now)

    assert first.revision == 1
    assert first.settings == {"contrast": 1.0}
    assert second.revision == 2
    assert second.settings == {"contrast": 1.5}
