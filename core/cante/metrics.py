"""Daily metrics cache — compute once for past days, periodically for today.

Pattern borrowed from Lavandaria Bot: past days are static (computed once,
never recalculated); today is recomputed if the last computation is older
than *REFRESH_SECONDS*.  All upserts are idempotent (ON CONFLICT).
"""

from datetime import UTC, date, datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from cante.db import async_session_factory, engine
from cante.models import Conversation, DailyMetrics, Message

logger = structlog.get_logger(__name__)


async def ensure_table() -> None:
    """Create the ``daily_metrics`` table if it doesn't exist (safe no-op if present)."""
    async with engine.begin() as conn:
        await conn.run_sync(DailyMetrics.metadata.create_all, checkfirst=True)

# Today's metrics are considered "fresh" for this many seconds.
REFRESH_SECONDS = 300  # 5 minutes


async def refresh_daily_metrics(target_date: date, tenant_id: str) -> dict:
    """Compute metrics for *target_date* and upsert into ``daily_metrics``.

    Returns the upserted row. Idempotent — safe to call multiple times.
    """
    async with async_session_factory() as session:
        row = await _compute_day(session, target_date, tenant_id)
        stmt = pg_insert(DailyMetrics).values(
            tenant_id=tenant_id,
            date=target_date,
            conversations_total=row["conversations_total"],
            conversations_escalated=row["conversations_escalated"],
            conversations_closed=row["conversations_closed"],
            conversations_active=row["conversations_active"],
            messages_in=row["messages_in"],
            messages_out=row["messages_out"],
            tokens_total=row["tokens_total"],
            first_reply_seconds=row["first_reply_seconds"],
            resolution_seconds=row["resolution_seconds"],
            message_counts=row["message_counts"],
            computed_at=datetime.now(UTC),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "date"],
            set_={
                "conversations_total": stmt.excluded.conversations_total,
                "conversations_escalated": stmt.excluded.conversations_escalated,
                "conversations_closed": stmt.excluded.conversations_closed,
                "conversations_active": stmt.excluded.conversations_active,
                "messages_in": stmt.excluded.messages_in,
                "messages_out": stmt.excluded.messages_out,
                "tokens_total": stmt.excluded.tokens_total,
                "first_reply_seconds": stmt.excluded.first_reply_seconds,
                "resolution_seconds": stmt.excluded.resolution_seconds,
                "message_counts": stmt.excluded.message_counts,
                "computed_at": stmt.excluded.computed_at,
            },
        )
        await session.execute(stmt)
        await session.commit()

    logger.info("metrics.refreshed", date=str(target_date), tenant=tenant_id)
    return row


async def ensure_days_cached(from_date: date, to_date: date, tenant_id: str) -> None:
    """Ensure every day in [*from_date*, *to_date*] has a cached row.

    Past days (before today) are computed only if missing. Today is always
    recomputed if the cache is older than *REFRESH_SECONDS*.
    """
    today = date.today()
    async with async_session_factory() as session:
        # Check which days are already cached
        existing = (
            await session.execute(
                select(DailyMetrics.date, DailyMetrics.computed_at).where(
                    DailyMetrics.tenant_id == tenant_id,
                    DailyMetrics.date >= from_date,
                    DailyMetrics.date <= to_date,
                )
            )
        ).all()
    cached = {row.date: row.computed_at for row in existing}

    for d in _date_range(from_date, to_date):
        if d > today:
            continue  # don't compute future dates
        if d < today:
            # Past day — compute only if missing
            if d not in cached:
                logger.info("metrics.cache_miss", date=str(d))
                await refresh_daily_metrics(d, tenant_id)
        else:
            # Today — recompute if stale
            last = cached.get(d)
            if last is None or _seconds_since(last) > REFRESH_SECONDS:
                await refresh_daily_metrics(d, tenant_id)


async def get_metrics_overview(
    from_date: date, to_date: date, tenant_id: str
) -> dict:
    """Return aggregated metrics for the period, mixing cache (past) + live (today)."""
    today = date.today()
    async with async_session_factory() as session:
        # Sum cached data
        rows = (
            await session.execute(
                select(
                    func.sum(DailyMetrics.conversations_total),
                    func.sum(DailyMetrics.conversations_escalated),
                    func.sum(DailyMetrics.conversations_closed),
                    func.sum(DailyMetrics.conversations_active),
                    func.sum(DailyMetrics.messages_in),
                    func.sum(DailyMetrics.messages_out),
                    func.sum(DailyMetrics.tokens_total),
                ).where(
                    DailyMetrics.tenant_id == tenant_id,
                    DailyMetrics.date >= from_date,
                    DailyMetrics.date <= to_date,
                    DailyMetrics.date < today,
                )
            )
        ).one()

        cached = {
            "conversations_total": rows[0] or 0,
            "conversations_escalated": rows[1] or 0,
            "conversations_closed": rows[2] or 0,
            "conversations_active": rows[3] or 0,
            "messages_in": rows[4] or 0,
            "messages_out": rows[5] or 0,
            "tokens_total": rows[6] or 0,
        }

        # Add today's live data (always fresh)
        if from_date <= today <= to_date:
            live = await _compute_day(session, today, tenant_id)
            for k in cached:
                cached[k] += live.get(k, 0)

        # Collect daily rows for chart data
        daily_rows = (
            await session.execute(
                select(DailyMetrics).where(
                    DailyMetrics.tenant_id == tenant_id,
                    DailyMetrics.date >= from_date,
                    DailyMetrics.date <= to_date,
                ).order_by(DailyMetrics.date)
            )
        ).scalars().all()

    daily = []
    all_first_reply = []
    all_resolution = []
    all_msg_counts = []
    for r in daily_rows:
        daily.append({
            "date": str(r.date),
            "conversations_total": r.conversations_total,
            "conversations_escalated": r.conversations_escalated,
            "conversations_closed": r.conversations_closed,
            "messages_in": r.messages_in,
            "messages_out": r.messages_out,
            "tokens_total": r.tokens_total,
        })
        all_first_reply.extend(r.first_reply_seconds or [])
        all_resolution.extend(r.resolution_seconds or [])
        all_msg_counts.extend(r.message_counts or [])

    return {
        "period": {"from": str(from_date), "to": str(to_date)},
        "totals": cached,
        "daily": daily,
        "percentiles": {
            "first_reply_p50": _percentile(sorted(all_first_reply), 50),
            "first_reply_p95": _percentile(sorted(all_first_reply), 95),
            "resolution_p50": _percentile(sorted(all_resolution), 50),
            "resolution_p95": _percentile(sorted(all_resolution), 95),
            "avg_messages_per_conversation": (
                round(sum(all_msg_counts) / len(all_msg_counts), 1) if all_msg_counts else 0
            ),
        },
    }


# ── Internal helpers ──────────────────────────────────────────────────────


async def _compute_day(session, day: date, tenant_id: str) -> dict:
    """Compute all metrics for a single day from live tables.

    Returns a dict suitable for upserting into ``daily_metrics``.
    """
    # Conversations that started on or before *day* and had activity on *day*
    convs = (
        await session.execute(
            select(Conversation).where(
                Conversation.tenant_id == tenant_id,
                func.date(Conversation.last_activity_at) == day,
            )
        )
    ).scalars().all()

    conv_ids = [c.id for c in convs]
    total = len(convs)
    escalated = sum(1 for c in convs if c.state == "needs_human")
    closed = sum(1 for c in convs if c.state == "closed")
    active = sum(1 for c in convs if c.state == "active")

    # Messages
    msg_in = 0
    msg_out = 0
    total_tokens = 0
    first_reply_times = []
    resolution_times = []
    msg_counts = []

    if conv_ids:
        msgs = (
            await session.execute(
                select(Message).where(
                    Message.conversation_id.in_(conv_ids),
                    func.date(Message.created_at) == day,
                ).order_by(Message.created_at)
            )
        ).scalars().all()

        for m in msgs:
            if m.direction == "in":
                msg_in += 1
            else:
                msg_out += 1
            total_tokens += m.tokens or 0

        # Per-conversation: first reply time & resolution time
        for c in convs:
            conv_msgs = [m for m in msgs if m.conversation_id == c.id]
            msg_counts.append(len(conv_msgs))

            # Time to first assistant reply (from first user message)
            user_msgs = [m for m in conv_msgs if m.direction == "in"]
            assistant_msgs = [m for m in conv_msgs if m.direction == "out"]
            if user_msgs and assistant_msgs:
                first_user = min(m.created_at for m in user_msgs)
                first_bot = min(m.created_at for m in assistant_msgs)
                if first_bot > first_user:
                    first_reply_times.append((first_bot - first_user).total_seconds())

            # Resolution time: started_at to last activity (for closed convs)
            if c.state == "closed" and c.started_at:
                resolution_times.append(
                    (c.last_activity_at - c.started_at).total_seconds()
                )

    return {
        "conversations_total": total,
        "conversations_escalated": escalated,
        "conversations_closed": closed,
        "conversations_active": active,
        "messages_in": msg_in,
        "messages_out": msg_out,
        "tokens_total": total_tokens,
        "first_reply_seconds": first_reply_times,
        "resolution_seconds": resolution_times,
        "message_counts": msg_counts,
    }


def _date_range(start: date, end: date):
    """Yield each date from *start* to *end* inclusive."""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _seconds_since(dt: datetime) -> float:
    return (datetime.now(UTC) - dt).total_seconds()


def _percentile(sorted_values: list, pct: float) -> float:
    """Return the *pct*-th percentile of *sorted_values* (linear interpolation)."""
    if not sorted_values:
        return 0.0
    k = (pct / 100.0) * (len(sorted_values) - 1)
    f = int(k)
    c = k - f
    if f + 1 < len(sorted_values):
        return sorted_values[f] + c * (sorted_values[f + 1] - sorted_values[f])
    return float(sorted_values[f])
