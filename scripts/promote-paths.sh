#!/usr/bin/env bash
# promote-paths.sh <commit-sha>
#
# Check whether a single commit touches ONLY motor paths (promote-allow.txt)
# and nothing denied (promote-deny.txt). Used by /cante-promote-upstream to
# flag commits that must be split before promotion.
#
# Exit: 0 commit is path-clean | 1 touches denied/unallowed paths (prints them)
set -euo pipefail

SHA="${1:?usage: promote-paths.sh <commit-sha>}"
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

[ -f promote-allow.txt ] || { echo "ERROR: promote-allow.txt missing"; exit 2; }

bad=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  if bash scripts/match-globs.sh promote-deny.txt "$f"; then
    echo "DENY:        $f"
    bad=1
  elif ! bash scripts/match-globs.sh promote-allow.txt "$f"; then
    echo "NOT-ALLOWED: $f"
    bad=1
  fi
done < <(git diff-tree --no-commit-id --name-only -r "$SHA")

exit $bad
