from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import stat
from collections.abc import Callable
from pathlib import Path, PurePosixPath

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_READ_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
_FILE_WRITE_FLAGS = os.O_WRONLY | os.O_CLOEXEC | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW


def asset_key_parts(storage_key: str) -> tuple[str, ...]:
    raw_parts = storage_key.split("/")
    path = PurePosixPath(storage_key)
    if (
        not storage_key
        or "\\" in storage_key
        or "\x00" in storage_key
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        raise OSError("invalid asset storage key")
    return tuple(raw_parts)


def _read_file_at(directory_fd: int, filename: str) -> bytes:
    file_fd = os.open(filename, _FILE_READ_FLAGS, dir_fd=directory_fd)
    try:
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise OSError("asset is not a regular file")
        chunks: list[bytes] = []
        while chunk := os.read(file_fd, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(file_fd)


def _content_matches(stored: bytes, expected: bytes, expected_sha256: str) -> bool:
    return (
        len(stored) == len(expected)
        and hmac.compare_digest(hashlib.sha256(stored).hexdigest(), expected_sha256)
        and hmac.compare_digest(stored, expected)
    )


def _open_asset_directory(root_fd: int, parts: tuple[str, ...], *, create: bool) -> int:
    directory_fd = os.dup(root_fd)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, mode=0o755, dir_fd=directory_fd)
                except FileExistsError:
                    pass
            next_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd
    except BaseException:
        os.close(directory_fd)
        raise


def read_asset_bytes(assets_root: Path, storage_key: str) -> bytes:
    parts = asset_key_parts(storage_key)
    root_fd = os.open(assets_root, _DIRECTORY_FLAGS)
    try:
        directory_fd = _open_asset_directory(root_fd, parts[:-1], create=False)
        try:
            return _read_file_at(directory_fd, parts[-1])
        finally:
            os.close(directory_fd)
    finally:
        os.close(root_fd)


def ensure_assets_root(artifact_root: Path) -> Path:
    root_fd = os.open(artifact_root, _DIRECTORY_FLAGS)
    try:
        try:
            os.mkdir("assets", mode=0o755, dir_fd=root_fd)
        except FileExistsError:
            pass
        assets_fd = os.open("assets", _DIRECTORY_FLAGS, dir_fd=root_fd)
        os.close(assets_fd)
    finally:
        os.close(root_fd)
    return artifact_root / "assets"


def publish_asset_bytes(
    assets_root: Path,
    storage_key: str,
    content: bytes,
    *,
    after_destination_open: Callable[[], None] | None = None,
) -> None:
    """Publish immutable content without resolving write paths outside trusted dir fds."""
    parts = asset_key_parts(storage_key)
    expected_sha256 = hashlib.sha256(content).hexdigest()
    root_fd = os.open(assets_root, _DIRECTORY_FLAGS)
    destination_fd = -1
    temporary_name: str | None = None
    temporary_created = False
    try:
        destination_fd = _open_asset_directory(root_fd, parts[:-1], create=True)
        if after_destination_open is not None:
            after_destination_open()

        try:
            existing = _read_file_at(destination_fd, parts[-1])
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if not _content_matches(existing, content, expected_sha256):
                raise OSError("existing asset failed integrity validation")
        else:
            temporary_name = f".{parts[-1]}.{secrets.token_hex(16)}.tmp"
            temporary_fd = os.open(
                temporary_name,
                _FILE_WRITE_FLAGS,
                0o600,
                dir_fd=destination_fd,
            )
            temporary_created = True
            try:
                remaining = memoryview(content)
                while remaining:
                    written = os.write(temporary_fd, remaining)
                    if written == 0:
                        raise OSError("asset write made no progress")
                    remaining = remaining[written:]
                os.fsync(temporary_fd)
            finally:
                os.close(temporary_fd)

            try:
                os.link(
                    temporary_name,
                    parts[-1],
                    src_dir_fd=destination_fd,
                    dst_dir_fd=destination_fd,
                    follow_symlinks=False,
                )
                os.fsync(destination_fd)
            except FileExistsError:
                pass

            stored = _read_file_at(destination_fd, parts[-1])
            if not _content_matches(stored, content, expected_sha256):
                raise OSError("existing asset failed integrity validation")

        # Re-traverse from the still-trusted root fd so a renamed/swapped component
        # cannot make a successful publication appear available at an unsafe path.
        verification_fd = _open_asset_directory(root_fd, parts[:-1], create=False)
        try:
            verified = _read_file_at(verification_fd, parts[-1])
        finally:
            os.close(verification_fd)
        if not _content_matches(verified, content, expected_sha256):
            raise OSError("existing asset failed integrity validation")
    finally:
        if temporary_created and temporary_name is not None and destination_fd >= 0:
            try:
                os.unlink(temporary_name, dir_fd=destination_fd)
            except FileNotFoundError:
                pass
        if destination_fd >= 0:
            os.close(destination_fd)
        os.close(root_fd)
