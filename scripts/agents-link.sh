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
