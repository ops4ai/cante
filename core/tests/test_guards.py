"""Test the guard pipeline (C16 — GuardContext interface)."""
import pytest


@pytest.mark.asyncio
async def test_dedup_guard_blocks_identical():
    from cante.guards import DedupGuard, GuardContext
    guard = DedupGuard()
    ctx = GuardContext(reply="Hello", last_outbound="Hello")
    result = await guard.check(ctx)
    assert not result.passed
    assert result.action == "regenerate"


@pytest.mark.asyncio
async def test_dedup_guard_allows_different():
    from cante.guards import DedupGuard, GuardContext
    guard = DedupGuard()
    ctx = GuardContext(reply="Hi there", last_outbound="Hello")
    result = await guard.check(ctx)
    assert result.passed


@pytest.mark.asyncio
async def test_scope_guard_allows_in_scope():
    from cante.guards import GuardContext, ScopeGuard
    guard = ScopeGuard()
    ctx = GuardContext(
        user_message="What are your hours?",
        reply="We are open 9-5",
        scope={"in": ["hours", "services"]},
    )
    result = await guard.check(ctx)
    assert result.passed


@pytest.mark.asyncio
async def test_scope_guard_redirects_out_of_scope():
    from cante.guards import GuardContext, ScopeGuard
    guard = ScopeGuard()
    ctx = GuardContext(
        user_message="What is the meaning of life?",
        reply="That's deep...",
        scope={"in": ["hours", "services"]},
    )
    result = await guard.check(ctx)
    assert not result.passed
