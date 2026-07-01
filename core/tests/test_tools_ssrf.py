"""DeclaredHttpTool SSRF egress filter tests (S3)."""
from __future__ import annotations

import pytest

from cante.tools import DeclaredHttpTool


def _tool(url: str, method: str = "GET", allowed_hosts=None) -> DeclaredHttpTool:
    return DeclaredHttpTool(
        name="t",
        description="d",
        parameters={"type": "object"},
        http_method=method,
        http_url=url,
        allowed_hosts=allowed_hosts or [],
    )


@pytest.mark.asyncio
async def test_rejects_cloud_metadata():
    tool = _tool("http://169.254.169.254/latest/meta-data/iam/security-credentials/")
    with pytest.raises(ValueError):
        await tool.execute({})


@pytest.mark.asyncio
async def test_rejects_internal_postgres():
    # "postgres" is the compose service hostname — unresolvable outside the
    # compose network, so the SSRF filter rejects it (no request leaves).
    tool = _tool("http://postgres:5432/")
    with pytest.raises(ValueError):
        await tool.execute({})


@pytest.mark.asyncio
async def test_rejects_loopback_redis():
    tool = _tool("http://127.0.0.1:6379/")
    with pytest.raises(ValueError):
        await tool.execute({})


@pytest.mark.asyncio
async def test_rejects_file_scheme():
    tool = _tool("file:///etc/passwd")
    with pytest.raises(ValueError):
        await tool.execute({})


@pytest.mark.asyncio
async def test_rejects_disallowed_method():
    tool = _tool("http://8.8.8.8/", method="PUT")
    with pytest.raises(ValueError):
        await tool.execute({})


@pytest.mark.asyncio
async def test_rejects_non_allowlisted_host():
    tool = _tool("http://8.8.8.8/", allowed_hosts=["api.partner.com"])
    with pytest.raises(ValueError):
        await tool.execute({})


@pytest.mark.asyncio
async def test_allows_allowlisted_public_host(monkeypatch):
    # The URL is safe and allowlisted; stub the shared tools HTTP client so no
    # real request is made. DeclaredHttpTool now uses a module-level long-lived
    # client (C8).
    from cante import tools as tools_mod

    class _Resp:
        is_redirect = False

        def raise_for_status(self):
            pass

        async def aread(self):
            return b'{"ok": true}'

        def json(self):
            return {"ok": True}

        def text(self):
            return "ok"

    class _Client:
        async def request(self, method, url, headers=None, timeout=None):
            assert url == "http://8.8.8.8/"
            return _Resp()

    monkeypatch.setattr(tools_mod, "_get_tools_http_client", lambda: _Client())
    tool = _tool("http://8.8.8.8/", allowed_hosts=["8.8.8.8"])
    result = await tool.execute({})
    assert result == {"ok": True}
