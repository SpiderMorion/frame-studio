from __future__ import annotations

from pathlib import Path

import pytest

from eink_frame.assets import publish_asset_bytes, read_asset_bytes


def test_publish_asset_bytes_creates_and_reuses_exact_content(tmp_path: Path) -> None:
    assets_root = tmp_path / "assets"
    assets_root.mkdir()
    content = b"immutable provider output"
    storage_key = "provider-output/ab/abcdef"

    publish_asset_bytes(assets_root, storage_key, content)
    publish_asset_bytes(assets_root, storage_key, content)

    assert read_asset_bytes(assets_root, storage_key) == content
    assert not list(assets_root.rglob("*.tmp"))


def test_publish_asset_bytes_refuses_corrupt_existing_target(tmp_path: Path) -> None:
    assets_root = tmp_path / "assets"
    target = assets_root / "provider-output" / "ab" / "abcdef"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"corrupt")

    with pytest.raises(OSError, match="integrity validation"):
        publish_asset_bytes(assets_root, "provider-output/ab/abcdef", b"expected")

    assert target.read_bytes() == b"corrupt"
    assert not list(assets_root.rglob("*.tmp"))


def test_publish_asset_bytes_refuses_symlinked_intermediate_directory(tmp_path: Path) -> None:
    assets_root = tmp_path / "assets"
    outside = tmp_path / "outside"
    assets_root.mkdir()
    outside.mkdir()
    (assets_root / "provider-output").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        publish_asset_bytes(assets_root, "provider-output/ab/abcdef", b"expected")

    assert list(outside.iterdir()) == []
