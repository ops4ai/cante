# Reports — ops4ai/cante

Versioned security reviews and code reviews committed directly in the repository. Each report is dated and scoped.

## Structure

```
docs/reports/
├── README.md                          ← this file
├── security/
│   └── YYYY-MM-DD-<title>.md          ← security review reports
└── code-review/
    └── YYYY-MM-DD-<title>.md          ← code review reports
```

## Naming convention

`YYYY-MM-DD-<short-slug>.md` — date first so they sort chronologically.

## When to add a report

- **Security review**: after major feature additions, before public launch, or when a dependency changes
- **Code review**: after completing a milestone (M0-M9), before merging large PRs, or when onboarding new contributors
