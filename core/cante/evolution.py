"""WhatsApp channel adapter — Evolution API implementation of ChannelAdapter."""

import structlog
from typing import Literal

from cante.settings import settings

logger = structlog.get_logger(__name__)


class EvolutionAdapter:
    """WhatsApp gateway via Evolution API."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self._base_url = (base_url or settings.evolution_base_url).rstrip("/")
        self._api_key = api_key or settings.evolution_api_key

    async def parse_webhook(self, raw: dict) -> list:
        from cante.channel import InboundMessage

        messages = []
        data = raw.get("data", raw)
        if "key" in data:
            msg = self._extract(data)
            if msg:
                messages.append(msg)
        return messages

    def _extract(self, data: dict):
        from cante.channel import InboundMessage

        key = data.get("key", {})
        message = data.get("message", data)
        msg_id = key.get("id", data.get("id", ""))
        if not msg_id:
            return None

        from_phone = self._normalize(key.get("remoteJid", data.get("from", "")))
        if not from_phone:
            return None

        number_phone = self._normalize(key.get("server", ""))

        body = ""
        if isinstance(message, dict):
            msg_type = message.get("messageType", "conversation")
            if msg_type == "conversation":
                body = message.get("conversation", "")
            elif msg_type == "extendedTextMessage":
                body = message.get("extendedTextMessage", {}).get("text", "")
            elif msg_type == "imageMessage":
                body = message.get("imageMessage", {}).get("caption", "[imagem]")
            elif msg_type == "audioMessage":
                body = "[áudio]"
            elif msg_type == "videoMessage":
                body = "[vídeo]"
            else:
                body = str(message)[:500]

        return InboundMessage(
            channel_message_id=msg_id,
            channel_type="whatsapp_evolution",
            number_phone=number_phone,
            from_phone=from_phone,
            body=body,
            meta={
                "from_me": key.get("fromMe", False),
                "is_group": "@g.us" in key.get("remoteJid", ""),
            },
        )

    async def send_text(self, number_config: dict, to: str, text: str):
        import httpx

        instance = number_config.get("instance", number_config.get("phone", ""))
        url = f"{self._base_url}/message/sendText/{instance}"

        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            resp = await client.post(
                url,
                headers={
                    "apikey": self._api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "number": f"{to}@s.whatsapp.net",
                    "text": text,
                    "delay": 1200,
                },
            )
            resp.raise_for_status()
            result = resp.json()
            from dataclasses import dataclass

            @dataclass
            class SentMessage:
                provider_message_id: str
                channel: str

            return SentMessage(
                provider_message_id=result.get("key", {}).get("id", ""),
                channel="whatsapp_evolution",
            )

    async def send_presence(
        self, number_config: dict, to: str, state: Literal["composing", "paused"]
    ) -> None:
        import httpx

        instance = number_config.get("instance", number_config.get("phone", ""))
        url = f"{self._base_url}/chat/sendPresence/{instance}"

        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            await client.post(
                url,
                headers={"apikey": self._api_key, "Content-Type": "application/json"},
                json={"number": f"{to}@s.whatsapp.net", "presence": state},
            )

    async def connect(self, number_config: dict):
        import httpx

        instance = number_config.get("instance", number_config.get("phone", ""))
        url = f"{self._base_url}/instance/connect/{instance}"

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            resp = await client.get(
                url,
                headers={"apikey": self._api_key},
            )
            resp.raise_for_status()
            data = resp.json()
            from dataclasses import dataclass

            @dataclass
            class ConnectResult:
                qr_code: str
                status: str

            return ConnectResult(
                qr_code=data.get("qrCode", data.get("base64", "")),
                status="qr_pending",
            )

    async def status(self, number_config: dict):
        import httpx

        instance = number_config.get("instance", number_config.get("phone", ""))
        url = f"{self._base_url}/instance/connectionState/{instance}"

        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            resp = await client.get(
                url,
                headers={"apikey": self._api_key},
            )
            resp.raise_for_status()
            data = resp.json()
            from dataclasses import dataclass

            @dataclass
            class ConnectionStatus:
                status: str
                phone: str
                instance_id: str

            return ConnectionStatus(
                status=data.get("state", "disconnected"),
                phone=number_config.get("phone", ""),
                instance_id=instance,
            )

    @staticmethod
    def _normalize(raw: str) -> str:
        for suffix in ("@s.whatsapp.net", "@g.us", "@c.us"):
            raw = raw.replace(suffix, "")
        return raw
