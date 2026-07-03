#!/usr/bin/env bash
# match-globs.sh <patterns-file> <path>
#
# Exit 0 if <path> matches any gitignore-style glob in <patterns-file>,
# exit 1 if it matches none. Used by the contamination gate to classify
# paths against promote-allow.txt / promote-deny.txt.
#
# Globs are gitignore-syntax (so '**' matches across '/'), evaluated with a
# scratch git repo + git check-ignore so the semantics match what authors expect.
# Lines starting with '#' and blank lines are ignored.
set -euo pipefail

patterns="${1:?usage: match-globs.sh <patterns-file> <path>}"
target="${2:?usage: match-globs.sh <patterns-file> <path>}"

[ -f "$patterns" ] || exit 1

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

git -C "$tmp" init -q
# Keep only non-comment, non-blank lines.
grep -vE '^\s*(#|$)' "$patterns" > "$tmp/.gitignore" || true
# No patterns at all → nothing matches.
[ -s "$tmp/.gitignore" ] || exit 1

if git -C "$tmp" check-ignore -q -- "$target"; then
  exit 0
fi
exit 1
