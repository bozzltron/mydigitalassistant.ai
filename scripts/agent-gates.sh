#!/usr/bin/env bash
# The project's gates, in one place, so a runbook and an agent cannot drift.
#
# Usage:
#   scripts/agent-gates.sh --scoped assistant/tests/test_x.py [--frontend]
#   scripts/agent-gates.sh --full
#
# --scoped   the inner loop: ruff + the named pytest files (all, if none named)
# --frontend add the frontend gate (lint + typecheck + tests; needs host Node)
# --full     the pre-commit gate: ./run_ci.sh (dead code, frontend build,
#            critical paths, plain AND encrypted suites) plus the security check
#
# The commands are the ones RUNBOOK.md mandates. Do not "simplify" them here: the
# image carries the pinned interpreter, and the named-volume test DB is what keeps
# tests off the repo's data/ directory and roughly 4x faster.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# The gates run inside the built image, and both modes assume the `assistant` tag exists.
# A fresh machine — and CI — has no such tag, and `docker run … assistant` then fails with
# "Unable to find image", which reads as a broken gate rather than a missing prerequisite.
if ! docker image inspect assistant >/dev/null 2>&1; then
  echo "== building the gate image (first run on this machine) =="
  docker build -t assistant .
fi

MODE=""
PATHS=()
FRONTEND=0
while [ $# -gt 0 ]; do
  case "$1" in
    --scoped)   MODE="scoped"; shift ;;
    --full)     MODE="full"; shift ;;
    --frontend) FRONTEND=1; shift ;;
    -h|--help)  sed -n '2,16p' "$0"; exit 0 ;;
    *)          PATHS+=("$1"); shift ;;
  esac
done
[ -n "$MODE" ] || { echo "usage: $0 (--scoped [pytest paths...] [--frontend] | --full)" >&2; exit 2; }

if [ "$MODE" = "full" ]; then
  echo "== full gates: evidence hygiene =="
  ./scripts/pii-guard.sh
  echo "== full gates: run_ci.sh =="
  ./run_ci.sh
  echo "== full gates: security =="
  docker run --rm -v "$ROOT":/app -w /app assistant \
    python -m assistant.scripts.verify_security
  # Stamp the tree, not the commit: the gate certifies what is on disk, and the tree
  # exists before the commit does. `.githooks/pre-push` reads this stamp so an
  # unverified tree cannot be published. Only a clean tree is certified — otherwise the
  # stamp would vouch for content that is not what will be committed.
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    echo "gates: PASS (full) — tree is dirty, NOT stamped; commit, then re-run to certify"
    exit 0
  fi
  stamps="$(git rev-parse --absolute-git-dir)/gate-stamps"
  mkdir -p "$stamps"
  tree="$(git rev-parse HEAD^{tree})"
  : > "$stamps/$tree"
  echo "gates: PASS (full) — stamped tree $(echo "$tree" | cut -c1-8) for pre-push"
  exit 0
fi

echo "== ruff =="
docker run --rm -v "$ROOT":/app -w /app assistant ruff check .

echo "== pytest (scoped) =="
if [ ${#PATHS[@]} -eq 0 ]; then
  docker run --rm -v "$ROOT":/app -v assistant-test-data:/app/data -w /app \
    -e DATABASE_PATH=/app/data/assistant-test.db \
    assistant pytest assistant/tests/ --timeout=20
else
  docker run --rm -v "$ROOT":/app -v assistant-test-data:/app/data -w /app \
    -e DATABASE_PATH=/app/data/assistant-test.db \
    assistant pytest "${PATHS[@]}" --timeout=20
fi

if [ "$FRONTEND" = "1" ]; then
  echo "== frontend =="
  (cd frontend && npm run check)
fi

echo "gates: PASS (scoped)"
