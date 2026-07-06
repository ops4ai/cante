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


def test_baileys_open_state_is_connected():
    """Evolution/Baileys reports 'open' once a phone is paired; the app must
    treat it as 'connected' (Baileys never emits a state literally named
    'connected' for a paired instance). Regression guard for the QR-flow bug
    where pairing hung forever because the frontend only matched 'connected'.
    """
    from cante.evolution import EvolutionAdapter

    m = EvolutionAdapter._STATE_MAP
    assert m["open"] == "connected"
    assert m["connected"] == "connected"
    assert m["connecting"] == "connecting"
    assert m["close"] == "close"
    assert m["disconnecting"] == "close"


def test_jid_strips_plus():
    """Numbers are stored with a leading '+', but Evolution rejects
    '+351...@s.whatsapp.net' (HTTP 400). _jid must emit digits-only JIDs.
    """
    from cante.evolution import EvolutionAdapter

    assert EvolutionAdapter._jid("+351928300415") == "351928300415@s.whatsapp.net"
    assert EvolutionAdapter._jid("351928300415") == "351928300415@s.whatsapp.net"
    assert EvolutionAdapter._jid("351 928 300 415") == "351928300415@s.whatsapp.net"
