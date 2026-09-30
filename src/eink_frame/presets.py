from __future__ import annotations

import copy
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .persistence import ProcessingPreset, ProcessingPresetRevision


class PresetRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        name: str,
        settings: dict[str, Any],
        make_default: bool,
        now: datetime,
    ) -> tuple[ProcessingPreset, ProcessingPresetRevision]:
        if make_default:
            self._session.execute(update(ProcessingPreset).values(is_default=False))
        preset = ProcessingPreset(
            id=uuid.uuid4(), name=name, is_default=make_default, created_at=now
        )
        self._session.add(preset)
        self._session.flush()
        revision = self.append_revision(preset_id=preset.id, settings=settings, now=now)
        return preset, revision

    def append_revision(
        self, *, preset_id: uuid.UUID, settings: dict[str, Any], now: datetime
    ) -> ProcessingPresetRevision:
        preset = self._session.scalar(
            select(ProcessingPreset).where(ProcessingPreset.id == preset_id).with_for_update()
        )
        if preset is None:
            raise LookupError("processing preset not found")

        latest = self._session.scalar(
            select(func.max(ProcessingPresetRevision.revision)).where(
                ProcessingPresetRevision.preset_id == preset_id
            )
        )
        revision = ProcessingPresetRevision(
            preset_id=preset_id,
            revision=(latest or 0) + 1,
            settings=copy.deepcopy(settings),
            created_at=now,
        )
        self._session.add(revision)
        self._session.flush()
        return revision

    def list_latest(self) -> list[tuple[ProcessingPreset, ProcessingPresetRevision]]:
        result: list[tuple[ProcessingPreset, ProcessingPresetRevision]] = []
        presets = list(
            self._session.scalars(
                select(ProcessingPreset).order_by(
                    ProcessingPreset.is_default.desc(), ProcessingPreset.created_at
                )
            )
        )
        for preset in presets:
            revision = self._session.scalar(
                select(ProcessingPresetRevision)
                .where(ProcessingPresetRevision.preset_id == preset.id)
                .order_by(ProcessingPresetRevision.revision.desc())
                .limit(1)
            )
            if revision is not None:
                result.append((preset, revision))
        return result
