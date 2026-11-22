import json
from pathlib import Path

import pytest

from pmo import db
from pmo.config import ROOT

SAMPLES = ROOT / "samples"
MEETINGS = sorted((SAMPLES / "meetings").glob("*.md"))


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def gold(source_id: str) -> dict:
    return json.loads((SAMPLES / "gold" / f"{source_id}.json").read_text(encoding="utf-8"))


class FixedRunner:
    """決まった返事を返す偽の実行役。"""
    name = "fixed"

    def __init__(self, output: str):
        self.output = output
        self.prompts: list[str] = []

    def run(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.output
