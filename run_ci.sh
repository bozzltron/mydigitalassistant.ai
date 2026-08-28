#!/bin/bash
# CI script: lint, critical-path tests, then full suite in both plain and encrypted modes.
# Usage: ./run_ci.sh

set -e

echo "=== Checking for dead code (unused imports/variables) ==="
ruff check --select=F401,F811 .

echo ""
echo "=== Cleaning up orphan containers ==="
docker compose -f docker-compose.test.yml down --remove-orphans 2>/dev/null || true

echo ""
echo "=== Running CRITICAL-PATH tests (plain mode) ==="
# Voice, run_now, correction, search extraction — must pass before full suite
docker compose -f docker-compose.test.yml run --rm test-critical

echo ""
echo "=== Running FULL test suite in PLAIN mode (DB_KEY unset) ==="
docker compose -f docker-compose.test.yml run --rm test-plain

echo ""
echo "=== Cleaning up between test runs ==="
docker compose -f docker-compose.test.yml down --remove-orphans 2>/dev/null || true

echo ""
echo "=== Running FULL test suite in ENCRYPTED mode (DB_KEY from .env) ==="
docker compose -f docker-compose.test.yml run --rm test-encrypted

echo ""
echo "=== All CI checks passed ==="
