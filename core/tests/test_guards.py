"""Test the guard pipeline."""
import pytest

@pytest.mark.asyncio
async def test_dedup_guard_blocks_identical():
    from cante.guards import DedupGuard
    guard = DedupGuard()
    result = await guard.check("Hello", "Hello", None)
    assert not result.passed
    assert result.action == "regenerate"

@pytest.mark.asyncio
async def test_dedup_guard_allows_different():
    from cante.guards import DedupGuard
    guard = DedupGuard()
    result = await guard.check("Hello", "Hi there", None)
    assert result.passed

@pytest.mark.asyncio
async def test_scope_guard_allows_in_scope():
    from cante.guards import ScopeGuard
    guard = ScopeGuard()
    result = await guard.check("What are your hours?", "We are open 9-5", {"in": ["hours", "services"]}, None)
    assert result.passed

@pytest.mark.asyncio
async def test_scope_guard_redirects_out_of_scope():
    from cante.guards import ScopeGuard
    guard = ScopeGuard()
    result = await guard.check("What is the meaning of life?", "That's deep...", {"in": ["hours", "services"]}, None)
    assert not result.passed
