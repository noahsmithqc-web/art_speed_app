"""Shared test helpers. Nothing here touches a real project folder or QC Hub."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures" / "speed_fixtures.json"

# The real Datalens exports and the delivered workbooks stay in the design repo (never in git
# here). Tests that need them skip when the folder is not there. SPEED_SAMPLE_REPORTS overrides.
_DEFAULT_SAMPLES = HERE.parent.parent / "claude" / "11 - intersection speed" / "sample reports"


def samples_dir() -> Path:
    return Path(os.environ.get("SPEED_SAMPLE_REPORTS") or _DEFAULT_SAMPLES)


@pytest.fixture(scope="session")
def fixtures() -> dict:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def samples() -> Path:
    folder = samples_dir()
    if not (folder / "raw datalens speed output").is_dir():
        pytest.skip(f"sample reports not found at {folder}")
    return folder
