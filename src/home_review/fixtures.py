"""Prepared synthetic examples; expected facts are only for offline evaluation."""

import json
from pathlib import Path

from home_review.models import DemoCase

ROOT = Path(__file__).resolve().parents[2]


def load_cases() -> list[DemoCase]:
    return [DemoCase.model_validate(item) for item in json.loads((ROOT / "data" / "examples.json").read_text(encoding="utf-8"))]


def load_investigation_cases() -> list[DemoCase]:
    """Only public case inputs; evaluator expectations are never passed to the UI service."""
    records = json.loads((ROOT / "data" / "investigation-evaluation.json").read_text(encoding="utf-8"))
    return [DemoCase.model_validate(record["case"]) for record in records]

