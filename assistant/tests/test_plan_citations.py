"""No tracked file may cite a plan that no longer exists.

RUNBOOK §4.2.3. Plans are deleted when their work ships, but code comments outlive
the work and are read long afterwards. A ``See plans/<file>.md`` pointer is therefore
a promise with an expiry date, and when the plan goes the pointer breaks silently:
nothing fails, the reader is just left wondering whether the comment above it is
still true.

Fifteen such pointers accumulated across three deleted plans before this test
existed. Every one of them turned out to state its rule in full, so no knowledge was
actually lost — which is precisely why nothing caught it. A dangling citation fails
open, so it needs a test rather than a review.

Plans are excluded from the scan: two *active* plans may legitimately reference each
other, and a plan is transient anyway. Only durable files are held to this.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLANS_DIR = REPO_ROOT / "plans"

# A plan filename is the frontmatter `date` plus a slug: 2026-10-02-geojson-maps.md
CITATION_RE = re.compile(r"plans/(\d{4}-\d{2}-\d{2}-[A-Za-z0-9._-]+\.md)")

SCANNED_SUFFIXES = frozenset(
    {".py", ".md", ".ts", ".tsx", ".js", ".yml", ".yaml", ".json", ".css", ".sh",
     ".toml", ".cfg", ".txt", ".html"}
)
PRUNED_DIRS = frozenset(
    {".git", "node_modules", "__pycache__", "plans", "dist", "build", ".venv",
     "venv", "htmlcov", ".pytest_cache", "data", ".mypy_cache", ".ruff_cache"}
)


def find_citations(text: str) -> list[str]:
    """Return the plan filenames cited in ``text``, in order, with duplicates."""
    return CITATION_RE.findall(text)


def iter_scannable_files() -> list[Path]:
    """Every tracked-looking text file that could carry a citation."""
    found: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if PRUNED_DIRS & set(path.relative_to(REPO_ROOT).parts):
            continue
        if path.suffix in SCANNED_SUFFIXES:
            found.append(path)
    return found


def test_detector_finds_a_citation() -> None:
    """Guard the guard: a vacuous regex would make this whole file a no-op."""
    cited = find_citations("See plans/2026-10-02-geojson-maps.md for the reasoning.")
    assert cited == ["2026-10-02-geojson-maps.md"]
    # Near-misses that must not trip it.
    assert find_citations("see plans/README.md") == []
    assert find_citations("plans/2026-10-02-geojson-maps") == []
    assert find_citations("the plans/ directory holds active work") == []


def test_no_dangling_plan_citations() -> None:
    """Every `plans/<file>.md` referenced anywhere must exist.

    All offenders are reported at once: fixing them one run at a time is the slow
    way to learn that the check works.
    """
    missing: list[str] = []
    for path in iter_scannable_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for cited in find_citations(text):
            if not (PLANS_DIR / cited).is_file():
                rel = path.relative_to(REPO_ROOT)
                missing.append(f"{rel} -> plans/{cited}")

    assert not missing, (
        "These files cite plans that are not in plans/. RUNBOOK §4.2: repoint each "
        "at the rule's real home (assistant/AGENTS.md, docs/<SUBSYSTEM>.md, or the "
        "code comment itself) or drop the pointer if the comment already says it.\n"
        + "\n".join(f"  {line}" for line in sorted(set(missing)))
    )
