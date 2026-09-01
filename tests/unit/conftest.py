"""Recorded responses are the single source of truth for fixtures.

They live in data/raw_samples/ (committed) rather than being duplicated under
tests/fixtures/, so a re-record cannot leave the tests asserting against a shape the
API no longer returns.
"""

import json
from pathlib import Path
from typing import Any

import pytest

SAMPLES = Path(__file__).resolve().parents[2] / "data" / "raw_samples"


@pytest.fixture(scope="session")
def sample() -> Any:
    def load(name: str) -> Any:
        return json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))

    return load
