#!/usr/bin/env bash
# Snapshot the dev and prod assistant logs for an agent run.
#
# Usage: scripts/log-snapshot.sh [--since 7d] --out DIR
#
# Per environment it writes:
#   <env>.raw.log  everything the container logged in the window (traceable)
#   <env>.log      the metric lines an agent actually reads
# and summary.md with the counts, so a finding starts from numbers.
#
# Both stacks are always read: dev is `docker compose` (8443), prod is
# `docker compose -f docker-compose.prod.yml` (8444). A signal in one says nothing
# about the other.
set -euo pipefail

SINCE="7d"
OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --since) SINCE="${2:?--since needs a value}"; shift 2 ;;
    --out)   OUT="${2:?--out needs a value}"; shift 2 ;;
    *) echo "usage: $0 [--since 7d] --out DIR" >&2; exit 2 ;;
  esac
done
[ -n "$OUT" ] || { echo "usage: $0 [--since 7d] --out DIR" >&2; exit 2; }

# Docker's --since takes a Go duration and rejects days ("7d"), but a runbook that
# says "read the last 7 days" should not have to say "168h". Normalise.
case "$SINCE" in
  *d) SINCE="$((${SINCE%d} * 24))h" ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p "$OUT"

snapshot() {
  local env="$1"
  if [ "$env" = "prod" ]; then
    docker compose -f docker-compose.prod.yml logs --no-color --since "$SINCE" assistant \
      > "$OUT/$env.raw.log" 2>&1 || true
  else
    docker compose logs --no-color --since "$SINCE" assistant \
      > "$OUT/$env.raw.log" 2>&1 || true
  fi
  grep -E \
    'turn_pregen:|turn_timings:|tool_loop_round:|tool_loop_exit:|context_fixed:|context_usage:|tool call:|duplicate \(#|escalated to max|Task .*completed|Task .*failed|Streaming generation failed|WARNING|ERROR|Traceback' \
    "$OUT/$env.raw.log" > "$OUT/$env.log" || true
}

for env in dev prod; do snapshot "$env"; done

# `grep -c` exits 1 when it finds nothing; the count is still the answer.
count() { grep -cE "$1" "$OUT/$2.log" 2>/dev/null || true; }

{
  echo "# Log snapshot — ${SINCE} — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
  for env in dev prod; do
    echo "## $env"
    echo
    echo "| signal | count |"
    echo "|---|---|"
    echo "| turns with a round | $(count 'tool_loop_round:' "$env") |"
    echo "| turns finished | $(count 'tool_loop_exit:' "$env") |"
    echo "| exhausted at the round cap | $(count 'tool_loop_exit: reason=exhausted' "$env") |"
    echo "| Max escalations | $(count 'escalated to max' "$env") |"
    echo "| repeat calls not re-run | $(count 'duplicate \(#' "$env") |"
    echo "| tool failures | $(count 'tool call:.*-> failed' "$env") |"
    echo "| errors | $(count 'ERROR' "$env") |"
    echo "| warnings | $(count 'WARNING' "$env") |"
    echo "| tasks completed | $(count "Task .* completed" "$env") |"
    echo "| tasks failed | $(count "Task .* failed" "$env") |"
    echo
  done
  echo "Raw logs: \`dev.raw.log\`, \`prod.raw.log\`."
  echo "Metric lines: \`dev.log\`, \`prod.log\`."
} > "$OUT/summary.md"

cat "$OUT/summary.md"
