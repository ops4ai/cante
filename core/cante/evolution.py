"""WhatsApp channel adapter — Evolution API implementation of ChannelAdapter."""

from typing import Literal

import httpx
import structlog

from cante.settings import settings

logger = structlog.get_logger(__name__)


class EvolutionAdapter:
    """WhatsApp gateway via Evolution API."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self._base_url = (base_url or settings.evolution_base_url).rstrip("/")
        self._api_key = api_key or settings.evolution_api_key
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def parse_webhook(self, raw: dict) -> list:

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
        from cante.channel import SentMessage

        instance = number_config.get("instance", number_config.get("phone", ""))
        url = f"{self._base_url}/message/sendText/{instance}"

        resp = await self._client.post(
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
            timeout=httpx.Timeout(15.0),
        )
        resp.raise_for_status()
        result = resp.json()
        return SentMessage(
            provider_message_id=result.get("key", {}).get("id", ""),
            channel="whatsapp_evolution",
        )

    async def send_presence(
        self, number_config: dict, to: str, state: Literal["composing", "paused"]
    ) -> None:
        instance = number_config.get("instance", number_config.get("phone", ""))
        url = f"{self._base_url}/chat/sendPresence/{instance}"

        resp = await self._client.post(
            url,
            headers={"apikey": self._api_key, "Content-Type": "application/json"},
            json={"number": f"{to}@s.whatsapp.net", "presence": state},
            timeout=httpx.Timeout(10.0),
        )
        resp.raise_for_status()

    async def connect(self, number_config: dict):
        from cante.channel import ConnectResult

        instance = number_config.get("instance", number_config.get("phone", ""))
        url = f"{self._base_url}/instance/connect/{instance}"

        resp = await self._client.get(
            url,
            headers={"apikey": self._api_key},
            timeout=httpx.Timeout(30.0),
        )
        resp.raise_for_status()
        data = resp.json()
        return ConnectResult(
            qr_code=data.get("qrCode", data.get("base64", "")),
            status="qr_pending",
        )

    async def status(self, number_config: dict):
        from cante.channel import ConnectionStatus

        instance = number_config.get("instance", number_config.get("phone", ""))
        url = f"{self._base_url}/instance/connectionState/{instance}"

        resp = await self._client.get(
            url,
            headers={"apikey": self._api_key},
            timeout=httpx.Timeout(10.0),
        )
        resp.raise_for_status()
        data = resp.json()
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
