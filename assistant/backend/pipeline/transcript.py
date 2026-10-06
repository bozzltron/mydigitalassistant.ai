"""Plain-text transcript of a conversation.

A *view* over memory, not a second copy: it renders the episodes that already
exist, so a conversation can leave the brain as a file. Pure and deterministic,
so it is tested without a store.

It also owns the footers the cognitive loop appends to an assistant turn, because
this is a second reader of them (the scheduler's task report is the first) and two
copies of the patterns would drift.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

# Footers the loop appends to an assistant turn. They are scaffolding -- where an
# answer came from -- not conversation, so a transcript drops them.
FOOTER_PATTERNS = (
    re.compile(r"\n*<small>_\(Answered from memory[^\n]*</small>\s*$"),
    re.compile(r"\n+\*\*Sources:\*\*.*\Z", re.DOTALL),
)


def strip_response_footers(text: str) -> str:
    """Remove the orchestrator's appended footers from a turn's text."""
    for pattern in FOOTER_PATTERNS:
        text = pattern.sub("", text)
    return text.rstrip()


def format_transcript(
    episodes: Sequence, *, title: str | None = None, exported_at: str | None = None
) -> str:
    """Render `episodes` as a plain-text transcript.

    `episodes` is anything with ``.role``, ``.content`` and ``.timestamp`` (the
    Episode rows). Deterministic: the same episodes produce the same text.
    Assistant footers are stripped; reasoning traces and search payloads are not
    included -- a transcript for re-use reads as a conversation, not as the
    loop's internals.
    """
    header = [f"Conversation: {title or 'Conversation'}"]
    if exported_at:
        header.append(f"Exported: {exported_at}")
    header.append(f"Turns: {len(episodes)}")

    blocks = ["\n".join(header)]
    for ep in episodes:
        content = ep.content or ""
        if ep.role == "assistant":
            content = strip_response_footers(content)
        stamp = f"[{ep.timestamp}] " if ep.timestamp else ""
        blocks.append(f"{stamp}{ep.role}:\n{content}")
    return "\n\n".join(blocks).rstrip() + "\n"
