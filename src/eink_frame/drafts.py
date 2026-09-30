from __future__ import annotations

import fcntl
import json
import os
import shutil
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError


class DraftNotFoundError(Exception):
    pass


class DraftLimitError(Exception):
    pass


class DraftCorruptError(Exception):
    pass


class DraftSupersededError(Exception):
    def __init__(self, *, current_revision: int, requested_revision: int) -> None:
        super().__init__("preview superseded")
        self.current_revision = current_revision
        self.requested_revision = requested_revision


class DraftBusyError(Exception):
    pass


@dataclass(frozen=True)
class DraftSnapshot:
    draft_id: str
    revision: int
    source_path: Path
    settings: dict[str, Any]


PreviewRenderer = Callable[[Image.Image, Mapping[str, Any]], Image.Image]
MAX_DRAFT_REVISION = 2_147_483_647


class DraftStore:
    def __init__(
        self,
        root: Path,
        *,
        limit: int = 16,
        revision_limit: int = 4,
        ttl: timedelta = timedelta(hours=24),
    ) -> None:
        if limit < 1 or revision_limit < 1:
            raise ValueError("draft and revision limits must be positive")
        self.root = root / "drafts"
        self.limit = limit
        self.revision_limit = revision_limit
        self.ttl = ttl

    @contextmanager
    def _lock(self, path: Path) -> Iterator[None]:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        except FileNotFoundError as error:
            raise DraftNotFoundError from error
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _draft_dir(self, draft_id: str) -> Path:
        try:
            normalized = str(uuid.UUID(draft_id))
        except ValueError as error:
            raise DraftNotFoundError from error
        draft_dir = self.root / normalized
        if not draft_dir.is_dir():
            raise DraftNotFoundError
        return draft_dir

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DraftCorruptError from error
        if not isinstance(value, dict):
            raise DraftCorruptError
        return value

    @staticmethod
    def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(dict(value), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            descriptor = os.open(temporary, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def _prune_expired_locked(self, now: datetime) -> None:
        cutoff = now - self.ttl
        for candidate in self.root.iterdir():
            if candidate.name.startswith(".") or not candidate.is_dir():
                continue
            try:
                with self._lock(candidate / ".lock"):
                    pointer = candidate / "current.json"
                    requested = candidate / "requested.json"
                    modified = datetime.fromtimestamp(
                        max(pointer.stat().st_mtime, requested.stat().st_mtime), tz=UTC
                    )
                    if modified < cutoff:
                        shutil.rmtree(candidate, ignore_errors=True)
            except (OSError, DraftNotFoundError):
                continue

    @staticmethod
    def _fsync(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def create(
        self,
        content: bytes,
        settings: Mapping[str, Any],
        renderer: PreviewRenderer,
    ) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            opened = Image.open(BytesIO(content))
            opened.load()
        except (OSError, UnidentifiedImageError) as error:
            raise ValueError("unsupported or corrupt image") from error
        draft_id = str(uuid.uuid4())
        staging = self.root / f".{draft_id}.tmp"
        draft_dir = self.root / draft_id
        with self._lock(self.root / ".drafts.lock"):
            self._prune_expired_locked(datetime.now(UTC))
            count = sum(
                1 for path in self.root.iterdir() if path.is_dir() and not path.name.startswith(".")
            )
            if count >= self.limit:
                raise DraftLimitError
            staging.mkdir()
            try:
                image = opened.convert("RGB")
                image.save(staging / "source.png", format="PNG")
                revision_dir = staging / "revisions" / "1"
                revision_dir.mkdir(parents=True)
                renderer(image, settings).save(revision_dir / "preview.png", format="PNG")
                (revision_dir / "settings.json").write_text(
                    json.dumps(dict(settings), indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                (staging / "current.json").write_text('{"revision": 1}\n', encoding="utf-8")
                (staging / "requested.json").write_text(
                    json.dumps({"revision": 1, "settings": dict(settings)}, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                for path in staging.rglob("*"):
                    if path.is_file():
                        descriptor = os.open(path, os.O_RDONLY)
                        try:
                            os.fsync(descriptor)
                        finally:
                            os.close(descriptor)
                os.replace(staging, draft_dir)
                directory = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
        return self.response(draft_id, 1)

    def response(self, draft_id: str, revision: int) -> dict[str, Any]:
        return {
            "draft_id": draft_id,
            "revision": revision,
            "preview_url": f"/api/v1/drafts/{draft_id}/revisions/{revision}/preview.png",
        }

    def request_revision(self, draft_id: str, revision: int, settings: Mapping[str, Any]) -> None:
        if revision < 2 or revision > MAX_DRAFT_REVISION:
            raise ValueError("revision is outside the supported range")
        draft_dir = self._draft_dir(draft_id)
        with self._lock(draft_dir / ".lock"):
            current = int(self._read_json(draft_dir / "current.json")["revision"])
            requested_state = self._read_json(draft_dir / "requested.json")
            requested = int(requested_state["revision"])
            if revision < requested or revision == current:
                raise DraftSupersededError(current_revision=current, requested_revision=requested)
            if revision == requested:
                if requested_state.get("settings") != dict(settings):
                    raise DraftSupersededError(
                        current_revision=current, requested_revision=requested
                    )
                self._atomic_json(draft_dir / "requested.json", requested_state)
                return
            self._atomic_json(
                draft_dir / "requested.json",
                {"revision": revision, "settings": dict(settings)},
            )

    def render_revision(
        self,
        draft_id: str,
        revision: int,
        settings: Mapping[str, Any],
        renderer: PreviewRenderer,
    ) -> dict[str, Any]:
        draft_dir = self._draft_dir(draft_id)
        with self._lock(draft_dir / ".lock"):
            requested_state = self._read_json(draft_dir / "requested.json")
            if int(requested_state["revision"]) != revision or requested_state.get(
                "settings"
            ) != dict(settings):
                current = int(self._read_json(draft_dir / "current.json")["revision"])
                raise DraftSupersededError(
                    current_revision=current,
                    requested_revision=int(requested_state["revision"]),
                )
        try:
            opened = Image.open(draft_dir / "source.png")
            opened.load()
        except (OSError, UnidentifiedImageError) as error:
            raise DraftCorruptError from error
        preview = renderer(opened.convert("RGB"), settings)
        staging = draft_dir / "revisions" / f".{revision}.{uuid.uuid4().hex}.tmp"
        staging.mkdir(parents=True)
        try:
            preview.save(staging / "preview.png", format="PNG")
            (staging / "settings.json").write_text(
                json.dumps(dict(settings), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self._fsync(staging / "preview.png")
            self._fsync(staging / "settings.json")
            self._fsync(staging)
            with self._lock(draft_dir / ".lock"):
                requested_state = self._read_json(draft_dir / "requested.json")
                current = int(self._read_json(draft_dir / "current.json")["revision"])
                requested = int(requested_state["revision"])
                if requested != revision or requested_state.get("settings") != dict(settings):
                    raise DraftSupersededError(
                        current_revision=current, requested_revision=requested
                    )
                target = draft_dir / "revisions" / str(revision)
                if target.exists():
                    shutil.rmtree(target)
                os.replace(staging, target)
                self._fsync(draft_dir / "revisions")
                self._atomic_json(draft_dir / "current.json", {"revision": revision})
                committed = sorted(
                    (
                        path
                        for path in (draft_dir / "revisions").iterdir()
                        if path.is_dir() and path.name.isdigit()
                    ),
                    key=lambda path: int(path.name),
                )
                for expired in committed[: -self.revision_limit]:
                    shutil.rmtree(expired)
                self._fsync(draft_dir / "revisions")
            return self.response(draft_id, revision)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def snapshot(self, draft_id: str) -> DraftSnapshot:
        draft_dir = self._draft_dir(draft_id)
        with self._lock(draft_dir / ".lock"):
            current = int(self._read_json(draft_dir / "current.json")["revision"])
            requested = int(self._read_json(draft_dir / "requested.json")["revision"])
            if current != requested:
                raise DraftBusyError
            requested_state = self._read_json(draft_dir / "requested.json")
            self._atomic_json(draft_dir / "requested.json", requested_state)
            settings = self._read_json(draft_dir / "revisions" / str(current) / "settings.json")
            return DraftSnapshot(
                draft_id=draft_id,
                revision=current,
                source_path=draft_dir / "source.png",
                settings=settings,
            )

    def commit_if_current(
        self,
        draft_id: str,
        revision: int,
        callback: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        draft_dir = self._draft_dir(draft_id)
        with self._lock(draft_dir / ".lock"):
            current = int(self._read_json(draft_dir / "current.json")["revision"])
            requested = int(self._read_json(draft_dir / "requested.json")["revision"])
            if current != revision or requested != revision:
                raise DraftSupersededError(current_revision=current, requested_revision=requested)
            return callback()

    def delete(self, draft_id: str) -> bool:
        if not self.root.is_dir():
            return False
        with self._lock(self.root / ".drafts.lock"):
            try:
                draft_dir = self._draft_dir(draft_id)
            except DraftNotFoundError:
                return False
            with self._lock(draft_dir / ".lock"):
                shutil.rmtree(draft_dir)
            return True
