from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).parents[1]


def test_all_contract_schemas_are_valid_draft_2020_12() -> None:
    schemas = sorted((ROOT / "contracts").glob("*.schema.json"))

    assert schemas
    for path in schemas:
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_frame_manifest_accepts_versioned_good_display_dithering() -> None:
    schema = json.loads((ROOT / "contracts" / "frame-manifest.schema.json").read_text())

    assert (
        "good-display-floyd-v1"
        in schema["properties"]["processing"]["properties"]["dithering"]["enum"]
    )
