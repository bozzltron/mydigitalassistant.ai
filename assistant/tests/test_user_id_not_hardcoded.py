"""No production code may invent a user id.

There is no auth layer: ``GET /users`` lists the household's accounts and memory is
scoped by id, so the id is data that travels with a request. It was ``1`` in
seventeen places — a parameter default, a fallback when a value would not parse, a
stand-in for a scheduled task with no owner — and every one assumed an account with
that id existed *and* that it was the one calling. A request could be served as, or
written to, the wrong household member; on an install whose only account is id 3 it
failed a foreign key instead.

The rule is written in ``assistant/AGENTS.md`` ("Accounts: a user id is data, never a
literal"). A rule that lives only in prose fails open — nothing goes red when the
literal comes back — so it is checked here.

**Scope: the application.** `assistant/backend` and `frontend/src` — the code that
serves a request and decides who it is for. Tests are excluded because a test passing
``user_id=1`` to a store method is stating a value, not deciding who the caller is.
The one-off scripts under `assistant/experiments` and `assistant/scripts` are excluded
too: several pin an account as part of a *recorded* run, and rewriting them would make
a committed `result.md` unreproducible. They should use a named constant, as
`experiments/file_write_parity` does with `USER_ID`.

Comments and docstrings are stripped: every explanation of this rule has to name the
pattern it forbids.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The shapes the literal actually took, so the guard is about the defect rather than
# about one spelling of it.
PATTERNS = (
    # `user_id: int = 1`, `user_id=1`, `user_id_int = 1`, `if user_id == 1:`
    # The type part stops at a comma, paren or operator: `get_episodes_for_user(
    # user_id: int, limit: int = 50)` is not a user-id literal, and a regex that ran
    # past the comma said it was.
    re.compile(r"\b(?:user_id|owner_user_id)\w*\s*(?::[^=!,\n()]*)?(?:==|!=|=)\s*[0-9]+\b"),
    # `... or 1` / `... || 1` standing in for an owner that is not known
    re.compile(r"\b(?:user_id|owner_user_id)\w*\b[^\n;]{0,60}?(?:\bor\b|\|\|)\s*[0-9]+\b"),
)

SCANNED_SUFFIXES = frozenset({".py", ".ts", ".tsx"})
SCANNED_ROOTS = ("assistant/backend", "frontend/src")
EXCLUDED_MARKERS = ("/node_modules/", "/dist/", ".test.", "/tests/")


def _blank(match: re.Match[str]) -> str:
    """Replace a span with the same number of newlines.

    The report points at a line in the *original* file, so a multi-line comment or
    docstring has to keep its line count rather than collapse to one.
    """
    return "\n" * match.group(0).count("\n")


def strip_comments(text: str) -> str:
    """Remove comments and docstrings so the guard matches code, not prose.

    Every explanation of this rule names the pattern it forbids — including the
    comment on the fix that removed it — so a comment read as code is a guaranteed
    false positive.
    """
    text = re.sub(r"/\*[\s\S]*?\*/", _blank, text)
    text = re.sub(r'"""[\s\S]*?"""', _blank, text)
    text = re.sub(r"'''[\s\S]*?'''", _blank, text)
    text = re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"#.*$", "", text, flags=re.MULTILINE)
    return text


def production_files() -> list[Path]:
    """Every production source file that could carry the literal."""
    found: list[Path] = []
    for rel in SCANNED_ROOTS:
        for path in (REPO_ROOT / rel).rglob("*"):
            if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
                continue
            posix = path.as_posix()
            if any(marker in posix for marker in EXCLUDED_MARKERS):
                continue
            found.append(path)
    return found


def test_the_scanner_finds_the_shapes_it_forbids() -> None:
    """Guard the guard: a vacuous regex would make this file a no-op."""
    for line in (
        "async def put_map(user_id: int = 1, payload: dict = Body(...)):",
        "    user_id=1,",
        "        user_id_int = 1",
        "owner_user_id: int = 1",
        "if user_id == 1:",
    ):
        assert PATTERNS[0].search(line), line

    for line in (
        "owner_user_id=task['owner_user_id'] or 1,",
        "const id = user_id || 1",
    ):
        assert any(p.search(line) for p in PATTERNS), line

    # A real account is not a literal.
    for line in (
        "async def put_map(user_id: int, payload: dict = Body(...)):",
        "user_id=user.id,",
        "user_id: int | None,",
        "owner_user_id=owner_user_id,",
        "user_id=actingUser(),",
        "const id = actingUserOrNull()",
    ):
        assert not any(p.search(line) for p in PATTERNS), line


def test_no_production_code_hardcodes_a_user_id() -> None:
    offenders: list[str] = []
    for path in production_files():
        text = strip_comments(path.read_text(encoding="utf-8"))
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(pattern.search(line) for pattern in PATTERNS):
                rel = path.relative_to(REPO_ROOT)
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    assert not offenders, (
        "A user id is data, never a literal (assistant/AGENTS.md, 'Accounts'). Take it "
        "from the caller, or write the frame unowned — never invent one:\n"
        + "\n".join(f"  {line}" for line in offenders)
    )
