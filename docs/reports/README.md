# Reports — ops4ai/cante

Versioned security reviews and code reviews committed directly in the repository. Each report is dated, scoped, and lists a reviewer persona.

## Structure

```
docs/reports/
├── README.md                          ← this file
├── security/
│   └── YYYY-MM-DD-<slug>.md           ← security audit reports
└── code-review/
    └── YYYY-MM-DD-<slug>.md           ← code review reports
```

## Naming convention

`YYYY-MM-DD-<short-slug>.md` — date first so they sort chronologically. New reports supersede older ones where they overlap; the prior report is kept for history and referenced by the new one.

## Report format

Every report carries:

- A metadata table (date, scope, stats, reviewer persona, prior report, method).
- An **executive summary** with a severity count table.
- A **findings index** table (ID · severity · file:line · title) for fast triage.
- Detailed findings, each with: **Location** (file:line), **Problem** (with the offending code), **Impact/Exploit**, **Fix** (concrete code), **Effort**.
- A **remediation checklist** ordered by priority.
- Code reviews additionally include a **test-coverage matrix** (module → tested? → untested behaviour) and a **code-quality scorecard**.
- Security reports additionally include **attack chains** and a **verification-after-fix** list.

## When to add a report

- **Security review**: after major feature additions, before public launch, when a dependency changes, or before tagging a release.
- **Code review**: after completing a milestone (M0–M9), before merging large PRs, or when onboarding new contributors.

## Report index

### Security

| Date | Report | Verdict |
|------|--------|---------|
| 2026-06-27 | [initial-security-review](security/2026-06-27-initial-security-review.md) | 0 critical, 3 medium — *superseded* |
| 2026-06-28 | [comprehensive-security-audit](security/2026-06-28-comprehensive-security-audit.md) | 4 critical, 7 medium, 7 low — not release-ready |

### Code review

| Date | Report | Verdict |
|------|--------|---------|
| 2026-06-27 | [initial-code-review](code-review/2026-06-27-initial-code-review.md) | 0 must-fix, 4 should-fix — *superseded* |
| 2026-06-28 | [comprehensive-code-review](code-review/2026-06-28-comprehensive-code-review.md) | 7 must-fix, 8 should-fix, 6 nice-to-have |

> **Note:** `.gitignore` currently ignores `docs/reports/`. The reports above are tracked as exceptions; new reports must be `git add -f`-ed until the ignore line is removed (tracked as security finding S18 in the 2026-06-28 audit).

## Handoff briefs (for implementing agents)

Self-contained, scope-bounded work orders derived from the 2026-06-28 reports. Each tells one agent exactly which findings to fix, with file:line + concrete fix + tests, and where the other agent's remit begins (to avoid collisions).

| Agent | Brief | Scope |
|-------|-------|-------|
| Security specialist | [2026-06-28-security-agent-brief](handoff/2026-06-28-security-agent-brief.md) | S1–S18 (auth, tenancy, SSRF, inbound, secrets) |
| Senior engineer | [2026-06-28-engineer-brief](handoff/2026-06-28-engineer-brief.md) | C1–C21 (schema, bus, agent loop, worker, perf, tests/CI) |

`.state.json` is the machine-readable source of truth for which findings are fixed; implementing agents should update `findings_fixed` and flip `status` to `remediated` as they close findings.
