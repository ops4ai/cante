#!/usr/bin/env python3
"""M1 smoke test — send a fake webhook through the pipeline and verify the end-to-end flow.

Usage: make smoke   (from host, services must be up)
       python tests/smoke.py  (from within the worker/api/sender container)
"""

import asyncio
import json
import sys

import httpx

INGRESS_URL = "http://ingress:8001"
WORKER_TIMEOUT = 15  # seconds to wait for worker to process the echo reply

FAKE_EVOLUTION_WEBHOOK = {
    "event": "messages.upsert",
    "data": {
        "key": {
            "id": "SMOKE_TEST_MSG_001",
            "remoteJid": "351912345678@s.whatsapp.net",
            "fromMe": False,
            "server": "351911223344",
        },
        "pushName": "Test User",
        "message": {
            "messageType": "conversation",
            "conversation": "Olá! Este é um teste de smoke do Cante.",
        },
    },
}


async def main() -> int:
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        # 1. Send webhook to ingress — should accept it.
        resp = await client.post(
            f"{INGRESS_URL}/channels/test/webhook",
            json=FAKE_EVOLUTION_WEBHOOK,
        )
        if resp.status_code != 200:
            errors.append(f"Ingress returned {resp.status_code} (expected 200)")
        else:
            print("1. Ingress accepted webhook ✓")

        # 2. Wait for worker to pick up and process the inbound message.
        print("2. Waiting for worker to process...")
        await asyncio.sleep(WORKER_TIMEOUT)

        # 3. Verify stream:outbound has at least one entry with a non-empty body.
        import redis.asyncio as aioredis

        redis_client = aioredis.from_url("redis://redis:6379/0", decode_responses=True)
        try:
            stream_info = await redis_client.xinfo_stream("stream:outbound")
            length = stream_info.get("length", 0)
            if length < 1:
                errors.append("stream:outbound is empty — worker did not produce a reply")
            else:
                # Read the latest entry and verify its shape.
                entries = await redis_client.xrevrange("stream:outbound", count=1)
                if entries:
                    _, fields = entries[0]
                    body = fields.get("body", "")
                    cid = fields.get("conversation_id", "")
                    if not body:
                        errors.append("Outbound entry has empty body")
                    if not cid:
                        errors.append("Outbound entry missing conversation_id")
                    print(
                        f"3. stream:outbound has {length} entries; "
                        f"last body={body[:80]!r}, conv={cid} ✓"
                    )
                else:
                    errors.append("xrevrange returned no entries despite length > 0")
        except Exception as exc:
            errors.append(f"Redis check failed: {exc}")
        finally:
            await redis_client.close()

    if errors:
        print("\n=== Smoke test FAILED ===")
        for e in errors:
            print(f"  ✗ {e}")
        return 1

    print("\n=== Smoke test PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
