"""A deterministic profile of a file: what it is, and where its gaps are.

The model kept hand-rolling this — "~92% radio, ~8% venue" — and getting it
wrong. A profile is arithmetic over the file, so it is **computed, not inferred**:
the model reads the numbers instead of guessing them. It is stored on the file
frame (a ``file_profile`` slot) at the one write path, ``apply_file_to_memory``,
so "what is this file?" is a memory read, not a file read.

Format-agnostic by construction. Each format fills what it can and a format with
nothing special to say still gets its size and shape; nothing here assumes CSV —
the delimited branch is one case among several. See ``docs/FILES.md``
("The file profile").
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
        return _profile_delimited(text, "\t" if ext == "tsv" else ",", ext)
    if ext == "json":
        return _profile_json(text)
    if ext in ("md", "markdown"):
        return _profile_markdown(text)
    return _profile_text(ext, text)


def _profile_delimited(text: str, delimiter: str, kind: str) -> dict:
    import csv
    from io import StringIO

    reader = csv.reader(StringIO(text), delimiter=delimiter)
    try:
        header_row = next(reader)
    except StopIteration:
        return {"kind": kind, "rows": 0, "columns": []}

    header = [h.strip() for h in header_row][:MAX_PROFILE_COLUMNS]

    # Stream the body rather than materializing it, and stop counting a column's
    # values once it is past the categorical bound: a high-cardinality column
    # (ids, emails) must not grow a per-value dict the size of the file. The
    # distinct *count* stays exact; only the value distribution is bounded.
    seen: list[set[str]] = [set() for _ in header]
    counts: list[dict[str, int] | None] = [dict() for _ in header]
    rows = 0
    for row in reader:
        rows += 1
        for index in range(len(header)):
            value = (row[index] if index < len(row) else "").strip()
            seen[index].add(value)
            column_counts = counts[index]
            if column_counts is not None:
                column_counts[value] = column_counts.get(value, 0) + 1
                if len(seen[index]) > CATEGORICAL_MAX_DISTINCT:
                    counts[index] = None

    profile: dict = {
        "kind": kind,
        "rows": rows,
        "columns": header,
        "distinct": {column: len(seen[i]) for i, column in enumerate(header)},
    }
    if len(header_row) > MAX_PROFILE_COLUMNS:
        profile["columns_total"] = len(header_row)

    categorical: dict[str, dict[str, int]] = {}
    for index, column in enumerate(header):
        column_counts = counts[index]
        if rows and column_counts is not None and _is_categorical(len(seen[index]), rows):
            top = sorted(column_counts.items(), key=lambda kv: (-kv[1], kv[0]))
            categorical[column] = dict(top[:MAX_PROFILE_VALUES])
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
    if not profile or not isinstance(profile, dict):
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
        values = ", ".join(f"{k or '(blank)'} {v}" for k, v in counts.items())
        lines.append(f"    {column}: {values}")
    if profile.get("headings"):
        lines.append("    headings: " + " | ".join(profile["headings"]))
    return lines
