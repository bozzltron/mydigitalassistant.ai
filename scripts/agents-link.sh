#!/usr/bin/env bash
# Point the vendor folders at .agents/ — the single source of truth.
#
# Run this after editing anything under .agents/agents/. Never edit inside
# .cursor/ or .claude/ directly: those paths are generated links, and an edit there
# is lost the next time this script runs (and on a fresh clone).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mkdir -p .cursor .claude .opencode

# -n so re-running replaces the link instead of nesting inside it.
ln -sfn ../.agents/agents .cursor/agents
ln -sfn ../.agents/agents .claude/agents
ln -sfn ../.agents/agents .opencode/agents

# Claude Code reads CLAUDE.md natively; point it at the open standard rather than
# duplicating the rules there.
if [ ! -f .claude/CLAUDE.md ]; then
  printf '@AGENTS.md\n' > .claude/CLAUDE.md
fi

echo ".cursor/agents   -> .agents/agents"
echo ".claude/agents   -> .agents/agents"
echo ".opencode/agents -> .agents/agents"
echo ".claude/CLAUDE.md -> @AGENTS.md"

# --- make the running harness actually hold this roster -----------------------
#
# The harness reads agent files into memory and does not watch a *symlinked*
# agents directory: measured 2026-10-31, a new file added through
# `.opencode/agents/` was invisible to a running server, and a model edited an
# hour earlier was still the old one on the next dispatch. So editing `.agents/` —
# which is what this script is for — silently leaves the harness running the
# previous roster. `POST /api/location/reload` is the programmatic form of the
# TUI's `/reload` and is the only thing that closed it; a roster that is right on
# disk and wrong in the harness is the failure this check exists to catch.
if ! command -v opencode >/dev/null 2>&1; then
  echo
  echo "note: opencode is not on PATH — links updated, running harness not reloaded"
  exit 0
fi
if ! opencode service status >/dev/null 2>&1; then
  echo
  echo "note: no OpenCode service is running — links updated, nothing to reload"
  exit 0
fi

opencode api post /api/location/reload >/dev/null 2>&1 || true

# Verify against what the *service* holds, not against disk. `debug agents` reports
# the live registry, which is the thing that decides whether a dispatch works.
python3 - "$ROOT" <<'PY'
import json
import pathlib
import re
import subprocess
import sys

root = pathlib.Path(sys.argv[1])
disk = {}
for path in sorted((root / ".agents" / "agents").glob("*.md")):
    text = path.read_text()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        print(f"  {path.stem}: NO FRONTMATTER — the harness cannot load this agent")
        continue
    model = re.search(r'^model:\s*"?([^"\n]+)"?', m.group(1), re.M)
    disk[path.stem] = model.group(1).strip() if model else None

try:
    live = {a["id"]: a.get("model") for a in json.loads(
        subprocess.run(["opencode", "debug", "agents"], capture_output=True,
                       text=True, check=True).stdout)}
except Exception as e:  # the probe itself failing is not a roster pass
    print(f"roster check could not read the live registry: {e}")
    sys.exit(1)

problems = []
for name, model in disk.items():
    if name not in live:
        problems.append(f"  {name}: on disk, NOT loaded by the harness")
        continue
    if model is None:
        continue
    got = live[name] or {}
    live_model = f"{got.get('providerID')}/{got.get('id')}" if got else None
    if live_model != model:
        problems.append(f"  {name}: disk says {model}, harness holds {live_model}")

extra = sorted(set(live) - set(disk) - {
    "build", "plan", "general", "explore", "compaction", "title", "summary"})
for name in extra:
    problems.append(f"  {name}: loaded by the harness, but no file on disk")

if problems:
    print()
    print("roster mismatch — the harness is not running what is on disk:")
    print("\n".join(problems))
    print("Run `opencode service restart`, then this script again.")
    sys.exit(1)

print()
print(f"roster verified: {len(disk)} agents, models match the live harness")
PY

