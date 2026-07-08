"""WhatsApp channel adapter — Evolution API implementation of ChannelAdapter."""

import re
import uuid
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

    @staticmethod
    def _digits(to: str) -> str:
        """Return the recipient as digits only (no JID suffix)."""
        return re.sub(r"[^0-9]", "", str(to or ""))

    @staticmethod
    def _jid(to: str) -> str:
        """Format a recipient as a WhatsApp JID: digits only + @s.whatsapp.net.

        Used for ``sendPresence`` (typing indicators) which does NOT need a Signal
        session and accepts the explicit JID. Do NOT use this for ``sendText``.
        """
        digits = re.sub(r"[^0-9]", "", str(to or ""))
        return f"{digits}@s.whatsapp.net"

    async def _resolve_lid(self, instance: str, phone: str) -> str:
        """Resolve a phone number to its ``@lid`` JID via the Evolution Contact table.

        WhatsApp's multi-device LID addressing: a contact's Signal session may live
        under a ``@lid`` JID, not ``@s.whatsapp.net``. Evolution v2.3.7 delivers
        reliably to ``@lid`` but **fails with ERROR** when sending to
        ``@s.whatsapp.net`` for a contact whose session is under ``@lid``
        (evolution-api #2626). The Evolution webhook normalises ``@lid`` ->
        ``@s.whatsapp.net`` before dispatch (PRs #1955/#2450), so the bot never
        sees the LID on inbound.

        The LID<->phone mapping lives in the Evolution ``Contact`` table: the same
        person has TWO rows — ``<phone>@s.whatsapp.net`` and ``<id>@lid`` — joined
        by an identical ``profilePicUrl``. We read the phone row's
        ``profilePicUrl`` then find the ``@lid`` row with the same picture. Returns
        the ``@lid`` JID if found, else the bare digits (fallback for contacts whose
        session is genuinely under ``@s.whatsapp.net``).

        The Evolution schema lives in the same Postgres as the cante app (schema
        ``evolution_api``), so we query it directly via a short-lived async session.
        """
        digits = self._digits(phone)
        if not digits:
            return ""
        try:
            from sqlalchemy import text as sa_text

            from cante.db import async_session_factory

            async with async_session_factory() as session:
                # Find the @lid row whose profilePicUrl matches the phone row's.
                row = (
                    await session.execute(
                        sa_text(
                            """
                            SELECT l."remoteJid"
                            FROM evolution_api."Contact" p
                            JOIN evolution_api."Contact" l
                              ON l."remoteJid" LIKE '%@lid'
                             AND l."profilePicUrl" = p."profilePicUrl"
                             AND l."profilePicUrl" IS NOT NULL
                             AND l."profilePicUrl" <> ''
                             AND l."instanceId" = p."instanceId"
                            WHERE p."remoteJid" = :phone_jid
                            LIMIT 1
                            """
                        ),
                        {"phone_jid": f"{digits}@s.whatsapp.net"},
                    )
                ).first()
                if row and str(row[0]).endswith("@lid"):
                    return str(row[0])
        except Exception as e:
            logger.warning("evolution.lid_lookup_failed", phone=digits, error=str(e)[:120])
        return digits

    async def send_text(self, number_config: dict, to: str, text: str):
        from cante.channel import SentMessage

        instance = number_config.get("instance", number_config.get("phone", ""))
        url = f"{self._base_url}/message/sendText/{instance}"

        # Resolve to the contact's @lid when available (LID addressing) so the
        # message targets the real Signal session; fall back to bare digits.
        number = await self._resolve_lid(instance, to)

        resp = await self._client.post(
            url,
            headers={
                "apikey": self._api_key,
                "Content-Type": "application/json",
            },
            json={
                "number": number,
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
            json={"number": self._jid(to), "presence": state},
            timeout=httpx.Timeout(10.0),
        )
        resp.raise_for_status()

    async def set_webhook(self, instance: str, webhook_url: str, webhook_token: str) -> dict:
        """Configure the Evolution instance to forward incoming messages to *webhook_url*.

        ``POST /webhook/set/{instance}`` — v2.3 requires the config nested under
        a ``webhook`` key (a flat body returns 400). The token is sent back to
        the ingress as ``X-Webhook-Token`` for authentication.
        """
        url = f"{self._base_url}/webhook/set/{instance}"
        resp = await self._client.post(
            url,
            headers={"apikey": self._api_key, "Content-Type": "application/json"},
            json={
                "webhook": {
                    "enabled": True,
                    "url": webhook_url,
                    "byEvents": True,
                    "events": ["MESSAGES_UPSERT"],
                    "headers": {"x-webhook-token": webhook_token},
                }
            },
            timeout=httpx.Timeout(10.0),
        )
        resp.raise_for_status()
        return resp.json()

    async def logout(self, instance: str) -> dict:
        """Disconnect the WhatsApp session for *instance* (log out, not delete).

        ``DELETE /instance/logout/{instance}`` — v2.3 uses DELETE, not POST.
        A not-connected instance returns 400 ("instance is not connected"); treat
        that as success so disconnecting an already-disconnected Number is safe.
        """
        url = f"{self._base_url}/instance/logout/{instance}"
        resp = await self._client.delete(url, headers={"apikey": self._api_key}, timeout=httpx.Timeout(10.0))
        if resp.status_code == 400 and "not connected" in resp.text.lower():
            return {"success": True, "message": "already disconnected"}
        resp.raise_for_status()
        return resp.json()

    async def create_instance(self, instance: str) -> dict:
        """Create a WhatsApp-Baileys instance in Evolution (v2.3+).

        ``POST /instance/create`` requires an ``integration`` field; the instance
        must exist before ``/instance/connect/{instance}`` can return a QR.
        Returns the raw response. Idempotent: a 400 "already exists" is treated
        as success so re-creating a Number is safe.
        """
        url = f"{self._base_url}/instance/create"
        resp = await self._client.post(
            url,
            headers={"apikey": self._api_key, "Content-Type": "application/json"},
            json={"instanceName": instance, "qrcode": True, "integration": "WHATSAPP-BAILEYS"},
            timeout=httpx.Timeout(30.0),
        )
        if resp.status_code in (400, 403, 409):
            # Instance already exists (should be rare with unique names)
            return {"instance": {"instanceName": instance, "status": "exists"}}
        resp.raise_for_status()
        return resp.json()

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

    # Canonicalise the raw Baileys/Evolution connection state.
    # Evolution API v2 (Baileys) reports these states on /connectionState:
    #   close / disconnecting   -> not paired
    #   connecting             -> requesting QR / opening socket
    #   connected              -> WebSocket open, NOT yet authenticated
    #   open                   -> fully authenticated & paired (ready to send)
    # NB: "open" IS the success state — Baileys never emits a state literally
    # named "connected" for a paired instance. The cante app treats "connected"
    # as "paired and usable", so we map "open" -> "connected" here. Mapping it in
    # one place makes both the backend (number.status, welcome message) and the
    # frontend (stop polling) detect pairing instead of hanging forever at "open".
    _STATE_MAP = {
        "open": "connected",
        "connected": "connected",
        "connecting": "connecting",
        "close": "close",
        "disconnected": "close",
        "disconnecting": "close",
    }

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
        # Evolution v2 nests state under "instance": {"instance": {..., "state": "..."}}
        inner = data.get("instance", data)
        raw_state = inner.get("state", "disconnected")
        state = self._STATE_MAP.get(raw_state, raw_state)
        return ConnectionStatus(
            status=state,
            phone=number_config.get("phone", ""),
            instance_id=instance,
        )

    @staticmethod
    def _normalize(raw: str) -> str:
        for suffix in ("@s.whatsapp.net", "@g.us", "@c.us"):
            raw = raw.replace(suffix, "")
        return raw


def instance_name_for(phone: str) -> str:
    """Build a deterministic Evolution instance name from a phone number.

    Evolution instance names are lowercase alphanumerics; a raw phone like
    ``+351900000000`` is invalid (``+``).

    IMPORTANT — deterministic by design (A2 fix): one phone number maps to
    exactly one Evolution instance. Re-creating a Number reuses the same
    instance, preventing the proliferation that causes WhatsApp's 4-device
    limit to trigger ``device_removed`` / ``stream:error 515`` cycles.
    """
    digits = re.sub(r"[^0-9]", "", phone or "")
    return f"cante{digits}" if digits else f"cante{uuid.uuid4().hex[:8]}"
