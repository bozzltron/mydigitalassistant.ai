#!/usr/bin/env bash
# Provision the Ollama models this project needs, on a new machine.
#
# Two sets, because they are pulled for different reasons and only one of them is a
# privacy boundary:
#
#   runtime — what the ASSISTANT needs to answer a turn. Local-only by project rule
#             (`AGENTS.md` §Security constraints): no cloud model may serve a user turn.
#   harness — what the DEV AGENTS need to run. Optional, and only useful when the
#             harness is in local mode; see `.agents/runs/2026-11-02-oll1/`.
#
# Idempotent: it checks before pulling, so running it twice costs nothing.
#
#   scripts/ollama-dev-models.sh --check     # what is missing, no download
#   scripts/ollama-dev-models.sh --pull      # pull what is missing
#   scripts/ollama-dev-models.sh --pull --all # include the large optional candidates
#
# Exit: 0 everything required is present · 1 something required is missing (--check)
#       · 2 Ollama is not reachable.
set -uo pipefail

HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"
MODE="check"
ALL=0
for arg in "$@"; do
  case "$arg" in
    --check) MODE="check" ;;
    --pull)  MODE="pull" ;;
    --all)   ALL=1 ;;
    -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 64 ;;
  esac
done
[ "$ALL" -eq 1 ] && MODE="pull-all"

# name|set|required|approx size|role it serves
MODELS=(
  "qwen3.5:9b|runtime|yes|6.6 GB|chat + tools (one loaded runner serves both, sharing one context window)"
  "qwen3.5:4b|runtime|yes|3.4 GB|utility extraction, task routing, and the compute tool (already warm)"
  "qwen3-embedding:0.6b|runtime|yes|0.6 GB|frame/query embeddings (1024-dim); retrieval is dead without it"
  "tev1:0.8b|runtime|yes|0.8 GB|the decision model: needs_action / completion nudge"
  "qwen3.8:27b|runtime|opt|17 GB|MAX tier, opt-in (MAX_MODEL empty by default); never resident with the warm set"
  "qwen2.5-coder:32b|harness|opt|20 GB|implementor candidate — the only coding model here that is not qwen3.x"
  "devstral:24b|harness|opt|14 GB|implementor candidate, second family for the reviewer/implementor split"
  "qwen3.6:27b|harness|opt|17 GB|planner/verifier candidate; runs hot (B-20 says pin the variant)"
)

if ! command -v ollama >/dev/null 2>&1; then
  echo "ollama: CLI not found on PATH. Install it first: https://ollama.com/download" >&2
  exit 2
fi

if ! curl -fsS -m 5 "$HOST/api/version" >/dev/null 2>&1; then
  echo "ollama: not reachable at $HOST — start it (or set OLLAMA_HOST) and re-run" >&2
  exit 2
fi

present="$(ollama list 2>/dev/null | awk 'NR>1 {print $1}')"

have() { printf '%s\n' "$present" | grep -qx "$1"; }

missing_required=0
missing_list=()
printf '%-26s %-8s %-8s %-9s %-8s %s\n' MODEL SET NEEDED SIZE STATUS ROLE
for entry in "${MODELS[@]}"; do
  IFS='|' read -r name set required size role <<<"$entry"
  if have "$name"; then
    state="present"
  else
    state="MISSING"
    missing_list+=("$name|$set|$required|$size|$role")
    [ "$required" = "yes" ] && missing_required=$((missing_required + 1))
  fi
  # Optional models are only listed when missing or when --all is asked for; required
  # ones are always listed, because a reader needs to see the present's against the
  # required's.
  if [ "$required" = "yes" ] || [ "$state" = "MISSING" ] || [ "$MODE" = "pull-all" ]; then
    printf '%-26s %-8s %-8s %-9s %-8s %s\n' "$name" "$set" "$required" "$size" "$state" "$role"
  fi
done

if [ "${#missing_list[@]}" -eq 0 ]; then
  echo
  echo "all required models present ($(printf '%s\n' "$present" | wc -l | tr -d ' ') installed in total)"
  [ "$MODE" = "pull-all" ] && echo "note: --all was requested but nothing was missing"
  exit 0
fi

if [ "$MODE" = "check" ]; then
  echo
  echo "${#missing_list[@]} model(s) missing, ${missing_required} of them required."
  echo "Re-run with --pull (add --all for the optional harness candidates)."
  [ "$missing_required" -gt 0 ] && exit 1
  exit 0
fi

echo
echo "Pulling ${#missing_list[@]} model(s) into $HOST ..."
failed=0
for entry in "${missing_list[@]}"; do
  IFS='|' read -r name set required size role <<<"$entry"
  if [ "$required" != "yes" ] && [ "$MODE" != "pull-all" ]; then
    echo "  skip $name (optional, $size) — pass --all to include it"
    continue
  fi
  echo "  pull $name ($size) — $role"
  if ! ollama pull "$name"; then
    echo "  FAILED: $name" >&2
    failed=$((failed + 1))
  fi
done

echo
if [ "$failed" -gt 0 ]; then
  echo "$failed model(s) failed to pull; re-run to retry (pulls resume)." >&2
  exit 1
fi
echo "done. Verify with: scripts/ollama-dev-models.sh --check"
echo
echo "Memory note: a 27B-class runner plus the warm 9B exceeds a 16 GB machine."
echo "Keep one harness model resident at a time, or the assistant's first turn after"
echo "a run pays a model load (AGENTS.md §Performance)."
