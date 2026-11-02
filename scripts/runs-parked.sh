#!/usr/bin/env bash
# List runs with no verdict line — parked, abandoned, or still in flight.
#
# A run parks when its next step needs something the agents cannot produce: a wall-clock
# soak, a human decision, a failure that has to recur unprompted. The durable record says
# what happened; this says what is *still owed*, which is the thing that otherwise rots in a
# directory nobody opens.
#
# A parked run states its own condition in `parked.md` beside its ledger:
#
#     waiting: the Ollama 500 to recur on a scheduled prod turn
#     since: 2026-10-30
#     resume-when: `Streaming generation failed` appears in prod logs after this build
#     next: observer-after -> measurement.md -> doc-gardener -> disposition pass -> verifier
#
# Nothing here resumes a run. It is a reminder, deliberately: a parked run should be
# reopened consciously, by someone who knows whether the world has changed since.
#
# Usage: scripts/runs-parked.sh
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
RUNS=".agents/runs"
LOG="$RUNS/log.jsonl"

[ -d "$RUNS" ] || { echo "runs-parked: no $RUNS"; exit 0; }

# bash 3.2 (macOS default) has no mapfile; a list file read by redirect keeps this in-process.
LIST="$(mktemp "${TMPDIR:-/tmp}/runs-parked.XXXXXX")"
trap 'rm -f "$LIST"' EXIT
ls -1dt "$RUNS"/*/ 2>/dev/null | sed 's:/$::' > "$LIST"
[ -s "$LIST" ] || { echo "runs-parked: no run directories"; exit 0; }

found=0
while IFS= read -r d; do
  id="$(basename "$d")"
  if [ -f "$LOG" ] && grep -q "\"run_id\": *\"$id\"" "$LOG"; then
    continue
  fi
  found=$((found + 1))
  if [ -f "$d/parked.md" ]; then
    echo "$id"
    sed 's/^/    /' "$d/parked.md"
  else
    echo "$id  — no parked note: abandoned, or still in flight?"
  fi
done < "$LIST"

if [ "$found" -eq 0 ]; then
  echo "runs-parked: every run has a verdict"
else
  echo
  echo "runs-parked: $found run(s) without a verdict. Remind the human; do not resume silently."
fi
