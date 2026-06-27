"""Shared test fixtures."""
import pytest
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

@pytest.fixture
def text_webhook():
    return json.loads((FIXTURES / "evolution_webhook_text.json").read_text())

@pytest.fixture
def media_webhook():
    return json.loads((FIXTURES / "evolution_webhook_media.json").read_text())
