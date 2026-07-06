#!/usr/bin/env bash
# contamination-scan.sh <git-range>
#
# Deterministic contamination gate for promoting motor changes from a private
# cante fork to the public ops4ai/cante. Pure script — no LLM judgement.
# Deny-wins across layers:
#   6.1 path allow/deny (promote-allow.txt / promote-deny.txt)
#   6.2 secret scan (gitleaks, .gitleaks.toml)
#   6.3 identity-token denylist (identity-tokens.txt, PRIVATE — only runs if
#       present; its absence makes this script degrade to the universal checks,
#       so the SAME script serves as the universal gate in public CI)
#   6.4 generic PII regex (PT mobile, email, IBAN) — excludes test fixtures
#   6.5 commit MESSAGES scanned too, not just diffs
#
# Exit: 0 clean | 1 contamination found | 2 tooling error (e.g. gitleaks missing)
set -euo pipefail

RANGE="${1:?usage: contamination-scan.sh <git-range>}"
# Resolve repo root so the lists resolve regardless of cwd.
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

command -v gitleaks >/dev/null 2>&1 || { echo "ERROR: gitleaks not installed (exit 2)"; exit 2; }
[ -f promote-allow.txt ] || { echo "ERROR: promote-allow.txt missing (exit 2)"; exit 2; }

fail=0
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# --- 6.1 path gate -----------------------------------------------------------
git diff --name-only "$RANGE" > "$tmp/files.txt" 2>/dev/null || true
while IFS= read -r f; do
  [ -z "$f" ] && continue
  if bash scripts/match-globs.sh promote-deny.txt "$f"; then
    echo "DENY-PATH:        $f"
    fail=1
  elif ! bash scripts/match-globs.sh promote-allow.txt "$f"; then
    echo "NOT-ALLOWED-PATH: $f"
    fail=1
  fi
done < "$tmp/files.txt"

# --- build the haystacks -----------------------------------------------------
# Full diff (for path/exclusion context) and added lines only.
# Exclude: test fixtures, and template/example files (.env*, *.example, *.example.*)
# which carry placeholder values (incl. plausible phone numbers) that are NOT data.
git diff "$RANGE" -- . \
  ':(exclude)tests/**/fixtures/**' ':(exclude)core/tests/**' \
  ':(exclude).env*' ':(exclude)**/.env*' ':(exclude)**/*.example' ':(exclude)**/*.example.*' \
  > "$tmp/diff.txt" 2>/dev/null || true
git log --format=%B "$RANGE" > "$tmp/msgs.txt" 2>/dev/null || true
# Added lines with the diff '+' marker stripped, so secret/PII patterns match clean text.
grep '^+' "$tmp/diff.txt" | grep -v '^+++' | sed 's/^+//' > "$tmp/added.txt" 2>/dev/null || true
cat "$tmp/added.txt" "$tmp/msgs.txt" > "$tmp/haystack.txt" 2>/dev/null || true

# --- 6.2 secret scan (gitleaks over added lines + messages) ------------------
# gitleaks exit codes: 0 no leaks | 1 leaks found | other error.
set +e
gitleaks detect --no-git --source "$tmp" --config .gitleaks.toml --verbose --no-banner > "$tmp/gl.txt" 2>&1
gl_rc=$?
set -e
if [ "$gl_rc" -eq 1 ]; then
  echo "SECRETS-FOUND (gitleaks):"
  grep -E '^found|Finding|Secret|secret|leak' "$tmp/gl.txt" | head -20 || cat "$tmp/gl.txt" | head -20
  fail=1
elif [ "$gl_rc" -ne 0 ]; then
  echo "ERROR: gitleaks failed (exit $gl_rc)"
  cat "$tmp/gl.txt" | head -20
  fail=1
fi

# --- 6.3 identity-token denylist (PRIVATE layer; skips if file absent) --------
# A fork keeps identity-tokens.txt (names, domains, phone of ITS org) locally;
# the public CI never has it, so the same script degrades to universal checks.
if [ -f identity-tokens.txt ]; then
  while IFS= read -r tok; do
    tok="${tok%%#*}"          # strip inline comments
    tok="$(echo "$tok" | xargs)"  # trim whitespace
    [ -z "$tok" ] && continue
    if grep -iF -- "$tok" "$tmp/haystack.txt" >/dev/null 2>&1; then
      echo "IDENTITY-TOKEN: $tok"
      fail=1
    fi
  done < identity-tokens.txt
fi

# --- 6.4 generic PII regex (PT mobile / email / IBAN) -----------------------
# Sensitive patterns; deliberately excludes fixtures via the haystack build above.
# First strip obvious placeholder/example content so honest code (placeholder
# emails like admin@example.com, fake phone numbers, Co-Authored-By trailers,
# npm/package-lock boilerplate) doesn't trip the gate. This mirrors the
# gitleaks allowlist concept for the PII layer.
grep -vE \
  'noreply@anthropic\.com|@users\.noreply\.github\.com|@example\.(com|org)|@example\.[a-z]+|placeholder|changeme|example\.com|example\.org|\+351[[:space:]]?900000000|<your-|<email>|<phone>|izs\.me|@i@|@s\.whatsapp\.net|@g\.us|@c\.us' \
  "$tmp/haystack.txt" > "$tmp/pii_hay.txt" 2>/dev/null || true
PII_REGEX='(\+351[[:space:]]?)?9[1236][0-9]{7}|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|PT50[0-9]{21}'
if grep -nE "$PII_REGEX" "$tmp/pii_hay.txt" >/dev/null 2>&1; then
  echo "PII-MATCH:"
  grep -nE "$PII_REGEX" "$tmp/pii_hay.txt" | head -20
  fail=1
fi

exit $fail
