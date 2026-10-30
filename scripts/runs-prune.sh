#!/usr/bin/env bash
# Prune transient run directories, keeping the durable record intact.
#
# A run directory is **scaffolding**: handoffs while the run is live, evidence while it is
# fresh. The durable record is three immutable surfaces — the line in `.agents/runs/log.jsonl`,
# the commit (linked by its `Run-ID:` trailer), and the row in `.agents/backlog.md`. Once a
# run is verified, its directory is redundant; 95% of the bytes are raw log snapshots.
#
# Nothing here deletes a run that has not been verified. A run with no log line is unfinished
# work, not history, and pruning it would destroy the only copy of an open decision.
#
# Usage:
#   scripts/runs-prune.sh              # dry run (default) — prints the plan, deletes nothing
#   scripts/runs-prune.sh --apply      # do it
#   scripts/runs-prune.sh --keep 8 --logs-age 14 --run-age 60
#
# Tiers:
#   1. logs/*.raw.log   older than --logs-age days, when the run is verified
#   2. the run dir      older than --run-age days, when the run is verified and unmentioned
#                       in any commit from the last 7 days
#   3. always kept      the newest --keep runs, and anything unverified
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
RUNS=".agents/runs"
LOG="$RUNS/log.jsonl"

KEEP=5
LOGS_AGE=7
RUN_AGE=30
APPLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --apply)     APPLY=1; shift ;;
    --keep)      KEEP="${2:?--keep needs a number}"; shift 2 ;;
    --logs-age)  LOGS_AGE="${2:?--logs-age needs days}"; shift 2 ;;
    --run-age)   RUN_AGE="${2:?--run-age needs days}"; shift 2 ;;
    -h|--help)   sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "usage: $0 [--apply] [--keep N] [--logs-age D] [--run-age D]" >&2; exit 2 ;;
  esac
done

[ -d "$RUNS" ] || { echo "runs-prune: no $RUNS"; exit 0; }

# Verified = the verifier appended a line for this run. That is the only marker of "settled".
verified() {
  [ -f "$LOG" ] && grep -q "\"run_id\": *\"$1\"\|\"run_id\":\"$1\"" "$LOG"
}

# Mentioned recently = someone is still referring to it, so it is not history yet.
mentioned_recently() {
  git log --since="7 days ago" --format=%B 2>/dev/null | grep -q "Run-ID: *$1"
}

dir_age_days() {
  local path="$1"
  local then now
  then=$(find "$path" -maxdepth 0 -printf '%T@' 2>/dev/null || stat -f %m "$path")
  now=$(date +%s)
  echo $(( (now - ${then%.*}) / 86400 ))
}

# Newest runs are kept regardless: you inspect evidence while it is fresh.
# bash 3.2 (the macOS default) has no `mapfile`, so the list goes through a file — reading
# from a redirect rather than a pipe keeps the counters in this shell and not a subshell.
LIST="$(mktemp "${TMPDIR:-/tmp}/runs-prune.XXXXXX")"
trap 'rm -f "$LIST"' EXIT
ls -1dt "$RUNS"/*/ 2>/dev/null | sed 's:/$::' > "$LIST"
[ -s "$LIST" ] || { echo "runs-prune: no run directories"; exit 0; }

kept=0
while IFS= read -r d; do
  id="$(basename "$d")"
  [ "$id" = "logs" ] && continue
  age=$(dir_age_days "$d")

  if [ "$kept" -lt "$KEEP" ]; then
    printf '  keep  %-22s %3sd  newest %d\n' "$id" "$age" "$KEEP"
    kept=$((kept + 1)); continue
  fi
  if ! verified "$id"; then
    printf '  keep  %-22s %3sd  UNVERIFIED — unfinished work, never pruned\n' "$id" "$age"
    continue
  fi
  if mentioned_recently "$id"; then
    printf '  keep  %-22s %3sd  still named by a commit in the last 7 days\n' "$id" "$age"
    continue
  fi

  if [ "$age" -ge "$RUN_AGE" ]; then
    printf '  PRUNE %-22s %3sd  whole run (verified, idle)\n' "$id" "$age"
    [ "$APPLY" = "1" ] && rm -rf "$d"
    continue
  fi

  reclaimed=$(du -sk "$d/logs" 2>/dev/null | cut -f1)
  reclaimed=${reclaimed:-0}
  if [ -d "$d/logs" ] && [ "$(dir_age_days "$d/logs")" -ge "$LOGS_AGE" ]; then
    printf '  TRIM  %-22s %3sd  logs/*.raw.log (%sK)\n' "$id" "$age" "$reclaimed"
    [ "$APPLY" = "1" ] && rm -f "$d"/logs/*.raw.log
    continue
  fi
  printf '  keep  %-22s %3sd  verified, inside the age window\n' "$id" "$age"
done < "$LIST"

if [ "$APPLY" = "1" ]; then
  echo "runs-prune: applied"
else
  echo "runs-prune: dry run — nothing deleted. Re-run with --apply."
fi
