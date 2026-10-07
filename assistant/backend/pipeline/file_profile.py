"""A deterministic profile of a file: what it is, and where its gaps are.

The model kept hand-rolling this — "~92% radio, ~8% venue" — and getting it
wrong. A profile is arithmetic over the file, so it is **computed, not inferred**:
the model reads the numbers instead of guessing them. It is stored on the file
frame (a ``file_profile`` slot) at the one write path, ``apply_file_to_memory``,
so "what is this file?" is a memory read, not a file read.

Format-agnostic by construction. Each format fills what it can and a format with
nothing special to say still gets its size and shape; nothing here assumes CSV —
the delimited branch is one case among several. See
``plans/2026-10-07-large-file-context.md`` (Phase 5).
"""

from __future__ import annotations

import json

# Bounds so a profile cannot grow without limit and fill the prompt. A wide or
# high-cardinality file reports its shape, not its every value.
MAX_PROFILE_COLUMNS = 40
MAX_PROFILE_HEADINGS = 20
MAX_PROFILE_VALUES = 6

# A column is reported as *categorical* (with its value distribution) when it has
# few distinct values and not every row is unique. This is the deterministic
# stand-in for "category/geo distribution" — the thing the model was guessing at.
CATEGORICAL_MAX_DISTINCT = 20


def build_profile(ext: str, content_bytes: bytes) -> dict:
    """A bounded, deterministic profile of ``content_bytes`` as ``ext``.

    Never raises: a profile is a convenience, and a file that cannot be parsed
    still gets a shape (its size and line count) rather than failing the write.
    """
    text = content_bytes.decode("utf-8", errors="replace")
    if ext in ("csv", "tsv"):
        return _profile_delimited(text, "\t" if ext == "tsv" else ",")
    if ext == "json":
        return _profile_json(text)
    if ext in ("md", "markdown"):
        return _profile_markdown(text)
    return _profile_text(ext, text)


def _profile_delimited(text: str, delimiter: str) -> dict:
    import csv
    from io import StringIO

    rows = list(csv.reader(StringIO(text), delimiter=delimiter))
    if not rows:
        return {"kind": "csv", "rows": 0, "columns": []}

    header = [h.strip() for h in rows[0]][:MAX_PROFILE_COLUMNS]
    body = rows[1:]

    distinct: dict[str, int] = {}
    categorical: dict[str, dict[str, int]] = {}
    for index, column in enumerate(header):
        seen: set[str] = set()
        counts: dict[str, int] = {}
        for row in body:
            value = (row[index] if index < len(row) else "").strip()
            seen.add(value)
            counts[value] = counts.get(value, 0) + 1
        distinct[column] = len(seen)
        if body and _is_categorical(len(seen), len(body)):
            top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            categorical[column] = dict(top[:MAX_PROFILE_VALUES])

    profile: dict = {
        "kind": "csv",
        "rows": len(body),
        "columns": header,
        "distinct": distinct,
    }
    if len(rows[0]) > MAX_PROFILE_COLUMNS:
        profile["columns_total"] = len(rows[0])
    if categorical:
        profile["categorical"] = categorical
    return profile


def _is_categorical(distinct: int, rows: int) -> bool:
    return distinct <= CATEGORICAL_MAX_DISTINCT and distinct < rows


def _profile_json(text: str) -> dict:
    try:
        data = json.loads(text)
    except Exception:
        return _profile_text("json", text)

    profile: dict = {"kind": "json"}
    if isinstance(data, dict):
        profile["keys"] = list(data.keys())[:MAX_PROFILE_COLUMNS]
        sizes = {k: len(v) for k, v in data.items() if isinstance(v, (list, dict))}
        if sizes:
            profile["sizes"] = sizes
    elif isinstance(data, list):
        profile["items"] = len(data)
        if data and isinstance(data[0], dict):
            profile["item_keys"] = list(data[0].keys())[:MAX_PROFILE_COLUMNS]
    else:
        profile["type"] = type(data).__name__
    return profile


def _profile_markdown(text: str) -> dict:
    headings = [
        line.strip()
        for line in text.splitlines()
        if line.lstrip().startswith("#")
    ][:MAX_PROFILE_HEADINGS]
    return {
        "kind": "markdown",
        "headings": headings,
        "lines": text.count("\n") + 1,
        "words": len(text.split()),
    }


def _profile_text(ext: str, text: str) -> dict:
    return {
        "kind": ext or "text",
        "lines": text.count("\n") + 1,
        "words": len(text.split()),
        "chars": len(text),
    }


def profile_summary_lines(profile: dict | None) -> list[str]:
    """Render a profile as a few compact prompt lines, or ``[]`` for none.

    Shared by the memory-context renderer so a file frame carries its shape in the
    prompt without the raw JSON.
    """
    if not profile:
        return []

    parts: list[str] = [str(profile.get("kind", "file"))]
    if "rows" in profile:
        parts.append(f"{profile['rows']} rows")
    if profile.get("columns"):
        parts.append(f"{len(profile['columns'])} cols: " + ", ".join(profile["columns"]))
    if "items" in profile:
        parts.append(f"{profile['items']} items")
    if profile.get("keys"):
        parts.append("keys: " + ", ".join(profile["keys"]))
    if "lines" in profile:
        parts.append(f"{profile['lines']} lines")
    if "words" in profile:
        parts.append(f"{profile['words']} words")

    lines = ["  profile: " + " · ".join(parts)]
    for column, counts in (profile.get("categorical") or {}).items():
        values = ", ".join(f"{k} {v}" for k, v in counts.items())
        lines.append(f"    {column}: {values}")
    if profile.get("headings"):
        lines.append("    headings: " + " | ".join(profile["headings"]))
    return lines
