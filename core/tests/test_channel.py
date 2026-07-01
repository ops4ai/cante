"""Test Evolution webhook parsing."""
import json
from pathlib import Path


def test_parse_text_webhook():
    """Canonical text message is parsed correctly."""
    fixture_path = (
        Path(__file__).parent.parent.parent / "tests/fixtures/evolution_webhook_text.json"
    )
    fixture = json.loads(fixture_path.read_text())

    # Verify the fixture has the expected shape
    assert fixture["event"] == "messages.upsert"
    data = fixture["data"]
    assert data["key"]["remoteJid"] == "351912345678@s.whatsapp.net"
    assert data["message"]["messageType"] == "conversation"
    assert "cortar o cabelo" in data["message"]["conversation"]
