#!/usr/bin/env bash
# Fails if a tracked or staged file contains a value from the local denylist.
#
# Why this exists: on 2026-10-27 a memory audit that quoted live slot values was
# committed to a public remote. No agent had a rule against it, because the agents'
# rules govern what they may *read*, and the failure was one of egress. A rule that
# nothing checks reads as satisfied, so this is the check.
#
# Usage:
#   scripts/pii-guard.sh                 # staged files, else every tracked file
#   scripts/pii-guard.sh <path> [...]    # specific files (used by the self-test)
#
# The denylist (`.agents/pii-denylist.txt`, one value per line, gitignored) is the
# one list that must never be committed. It is deliberately manual: a value only
# gets on it because a human said so. That makes this a ratchet, not a guarantee —
# a new personal value that nobody has added yet still gets through. The rule that
# actually prevents the class is "brain-derived evidence never leaves
# `.agents/runs/`", and this is what catches the day someone forgets.
#
# Portable to bash 3.2 (the macOS default): no mapfile, no associative arrays.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DENY=".agents/pii-denylist.txt"

if [ ! -f "$DENY" ]; then
  echo "pii-guard: no denylist at $DENY — nothing to check"
  exit 0
fi

LIST="$(mktemp "${TMPDIR:-/tmp}/pii-guard.XXXXXX")"
trap 'rm -f "$LIST"' EXIT

if [ $# -gt 0 ]; then
  printf '%s\n' "$@" > "$LIST"
else
  git diff --cached --name-only --diff-filter=ACM > "$LIST" 2>/dev/null || true
  if [ ! -s "$LIST" ]; then
    git ls-files > "$LIST"
  fi
fi

file_count=$(wc -l < "$LIST" | tr -d ' ')
token_count=$(grep -v '^#' "$DENY" | grep -c . || true)
fail=0
entry=0

while IFS= read -r tok || [ -n "$tok" ]; do
  [ -z "$tok" ] && continue
  case "$tok" in \#*) continue ;; esac
  entry=$((entry + 1))
  while IFS= read -r f || [ -n "$f" ]; do
    [ -f "$f" ] || continue
    # -I skips binaries; -q stops at the first hit so the value is never printed.
    if grep -qFI -- "$tok" "$f" 2>/dev/null; then
      line=$(grep -nFI -- "$tok" "$f" 2>/dev/null | cut -d: -f1 | head -1)
      echo "pii-guard: $f line $line — denylist entry $entry (${#tok} chars)"
      fail=1
    fi
  done < "$LIST"
done < "$DENY"

if [ "$fail" -ne 0 ]; then
  cat >&2 <<'EOF'

pii-guard: FAIL — a denylisted value is in a file git would publish.

  Remove the value; do not remove the line from the denylist. If the value is
  genuinely safe to publish, delete it from .agents/pii-denylist.txt and say why
  in the commit message — that is the only way off the list.
EOF
  exit 1
fi

echo "pii-guard: PASS ($file_count files, $token_count denylisted values)"
