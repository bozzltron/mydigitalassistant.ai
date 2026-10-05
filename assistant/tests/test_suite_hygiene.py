"""Suite hygiene: a test must actually assert something.

A test with no assertion cannot fail, so it cannot catch a regression — it only
inflates the count and gives false confidence. A test-suite audit found eight
such stubs (empty bodies, `pass`), all of which had real coverage elsewhere.

This guard keeps them from regrowing: every `test_*` function must contain at
least one assertion construct, or be a fixture. "Assertion construct" means:

- a bare ``assert`` (including comparisons and membership),
- ``pytest.raises`` / ``pytest.warns`` / ``pytest.fail``,
- a mock assertion (``mock.assert_called_with(...)``, ``assert_called_once``, …),
- a helper call named ``assert_*``.

A test whose only check is "must not raise" should assert the result explicitly
(e.g. the returned value is contained) rather than rely on the absence of an
exception — the explicit form is both clearer and what this guard requires.

If a test genuinely cannot assert (rare), opt out with a ``# no-assert-ok``
comment in its body, with a reason. The marker is intentionally noisy so it is
not used casually.
"""

from __future__ import annotations

import ast
import pathlib

TESTS_DIR = pathlib.Path(__file__).parent

ALLOW_MARKER = "no-assert-ok"


def _is_assertion(node: ast.AST) -> bool:
    if isinstance(node, ast.Assert):
        return True
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Attribute):
            if fn.attr.startswith("assert"):
                return True
            if isinstance(fn.value, ast.Name) and fn.value.id == "pytest":
                return fn.attr in ("raises", "warns", "fail")
        if isinstance(fn, ast.Name) and fn.id.startswith("assert"):
            return True
    return False


def _test_functions():
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        source = path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            is_test = (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("test")
            )
            if is_test:
                decorators = {
                    (d.attr if isinstance(d, ast.Attribute) else getattr(d, "id", ""))
                    for d in node.decorator_list
                }
                is_fixture = any("fixture" in d for d in decorators)
                body_src = ast.get_source_segment(source, node) or ""
                has_assert = any(_is_assertion(n) for n in ast.walk(node))
                yield path, node, is_fixture, has_assert, body_src


def test_every_test_asserts_something():
    offenders = []
    for path, node, is_fixture, has_assert, body_src in _test_functions():
        if is_fixture:
            continue
        if has_assert or ALLOW_MARKER in body_src:
            continue
        offenders.append(f"{path.name}::{node.name} (line {node.lineno})")
    assert not offenders, (
        "These test functions contain no assertion and cannot fail:\n  "
        + "\n  ".join(offenders)
        + "\nAdd an assertion, or a `# no-assert-ok: <reason>` comment if it truly "
        "cannot assert."
    )


def test_no_fixture_is_named_like_a_test():
    """A fixture named `test_*` is collected as a test and silently skipped.

    Found in the audit: `test_conversations.py::test_user` was a fixture with a
    test-shaped name.
    """
    offenders = [
        f"{path.name}::{node.name} (line {node.lineno})"
        for path, node, is_fixture, _has, _src in _test_functions()
        if is_fixture
    ]
    assert not offenders, (
        "These fixtures are named `test_*`, so pytest collects them as tests:\n  "
        + "\n  ".join(offenders)
    )
