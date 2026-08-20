#!/bin/bash
# CI script: runs the test suite in both plain and encrypted modes.
# Usage: ./run_ci.sh

set -e

echo "=== Cleaning up orphan containers ==="
docker compose -f docker-compose.test.yml down --remove-orphans 2>/dev/null || true

echo ""
echo "=== Running tests in PLAIN mode (DB_KEY unset) ==="
docker compose -f docker-compose.test.yml run --rm test-plain

echo ""
echo "=== Cleaning up between test runs ==="
docker compose -f docker-compose.test.yml down --remove-orphans 2>/dev/null || true

echo ""
echo "=== Running tests in ENCRYPTED mode (DB_KEY from .env) ==="
docker compose -f docker-compose.test.yml run --rm test-encrypted

echo ""
echo "=== All CI checks passed ==="
