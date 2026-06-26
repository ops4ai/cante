"""Channel adapter interface. WhatsApp/Evolution is the first implementation."""

from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass
class InboundMessage:
    """Canonical inbound message envelope — all channels normalise to this."""

    channel_message_id: str
    channel_type: str  # whatsapp_evolution
    number_phone: str
    from_phone: str
    body: str
    meta: dict = field(default_factory=dict)


@dataclass
class SentMessage:
    provider_message_id: str
    channel: str


@dataclass
class ConnectResult:
    qr_code: str  # base64-encoded QR image or pairing string
    status: str  # qr_pending | connected | error


@dataclass
class ConnectionStatus:
    status: str  # disconnected | qr_pending | connected | error
    phone: str
    instance_id: str


class ChannelAdapter(Protocol):
    """Every messaging channel implements this interface."""

    async def parse_webhook(self, raw: dict) -> list[InboundMessage]:
        """Extract canonical inbound messages from a channel's webhook payload."""
        ...

    async def send_text(self, number_config: dict, to: str, text: str) -> SentMessage:
        """Deliver a text reply to the given contact."""
        ...

    async def send_presence(
        self, number_config: dict, to: str, state: Literal["composing", "paused"]
    ) -> None:
        """Optional presence indicator during send-delay window."""
        ...

    async def connect(self, number_config: dict) -> ConnectResult:
        """Initiate QR/pairing connection for a number."""
        ...

    async def status(self, number_config: dict) -> ConnectionStatus:
        """Check the connection status of a number."""
        ...
