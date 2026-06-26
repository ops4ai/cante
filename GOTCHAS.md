# Gotchas — Reliability lessons baked into Cante from day one

These caused real multi-day outages in the predecessor system. Follow these rules or the chat bot will die in production.

## 1. Never hold a DB session across external I/O

**Pattern everywhere:** *open session → DB work → commit → close → THEN call LLM/HTTP.*

Holding a session open during a 10–30s LLM call starves the connection pool and kills all webhook processing. This is the single most important rule.

## 2. SELECT-then-INSERT is always a race

Two concurrent webhooks for the same phone both pass the SELECT. Use `ON CONFLICT` upsert, or catch `IntegrityError`, roll back, re-SELECT. Applies to contacts, conversations, dedup.

## 3. Webhook handlers must not swallow unrecoverable errors

Returning `200 OK` on a corrupted session is worse than `500` (which lets the gateway retry). `ingress` returns fast and clean; `worker` fails the stream entry for redelivery — it never fakes success.

## 4. Any "type" enum that fans out into config needs a checklist

A new `preset`, `channel_type`, or `provider.type` must have a validation that fails loudly if its required config/handler is missing.

## 5. After any change to an INSERT/SQL statement, immediately smoke-test

A column/value count mismatch silently broke creation. Run `make smoke` after any schema change.

## 6. Seed data MUST use internal codes, not display labels

Validate enums at insert time; add DB `CHECK` constraints where practical. Never seed a Portuguese UI label where the code expects an enum code.

## 7. Test traffic must be visibly marked and never reuse real identities

Synthetic contacts/conversations flagged in `meta`; never pollute a real contact's history.

## Meta-lesson

**The DB connection pool and the webhook error path are where a chat bot dies.** Spend the reliability budget there.
