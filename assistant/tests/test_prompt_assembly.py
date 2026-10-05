"""The system prompt must be assembled in one place, not copied per path.

There are three prompt-building paths -- `chat()`, the scheduled-task runner and
`chat_stream()`. They were near-identical copies of the same seven lines, and
they had already drifted: the scheduled path passed a hardcoded `"functional"`
where the other two passed the real task type. That is harmless only while the
scheduled path genuinely is functional, and it is invisible -- nothing fails, the
memory budget and the prompt's own task guidance just quietly describe different
turns.

This is the same shape as the bug class already fixed three times in this
project (mid-frame prompt cut, stale embedding-model label, budget computed from
an incomplete picture): arithmetic that is copied rather than shared. The
regression test for a de-duplication is structural -- it pins that the copies do
not come back -- so that is what these assert.

The behavioural guarantees that the copies used to carry are already covered by
`test_prompt_truncation.py`; this file only guards the sharing itself.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from assistant.backend.pipeline import orchestrator as orch_module
from assistant.backend.pipeline.orchestrator import Orchestrator


def _function_source(fn) -> str:
    return textwrap.dedent(inspect.getsource(fn))


class TestPromptAssemblyIsShared:
    def test_all_paths_delegate_to_the_single_loop(self):
        """No path may re-inline the pipeline; both consume `_run_turn`."""
        paths = {
            "chat": Orchestrator.chat,
            "chat_stream": Orchestrator.chat_stream,
        }
        for name, fn in paths.items():
            source = _function_source(fn)
            assert "_run_turn(" in source, f"{name} does not use the single loop"
            assert (
                "format_memory_context(" not in source
            ), f"{name} inlines prompt assembly instead of using the shared loop"
        # The loop is where assembly lives now, and it must use the helper.
        loop = _function_source(Orchestrator._run_turn)
        assert "_assemble_prompt(" in loop
        assert "memory_context=format_memory_context(" not in loop

    def test_scheduled_path_delegates_to_the_helper(self):
        """The scheduled runner is the third path and the one that drifted."""
        source = _function_source(Orchestrator.run_scheduled_task)
        assert "_assemble_prompt(" in source, "scheduled path bypasses the shared helper"
        assert (
            "memory_context=format_memory_context(" not in source
        ), "scheduled path still inlines prompt assembly"

    def test_task_type_literal_cannot_survive(self):
        """`_memory_char_budget` and `build_system_prompt` must get the same value.

        The drift that motivated this helper was a bare `"functional"` string at
        the scheduled call site. A helper takes `task_type` once and uses it for
        both the budget and the prompt, so the two cannot disagree.
        """
        src = inspect.getsource(orch_module)
        tree = ast.parse(src)
        helper = next(
            node
            for cls in ast.walk(tree)
            if isinstance(cls, ast.ClassDef) and cls.name == "Orchestrator"
            for node in cls.body
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
            and node.name == "_assemble_prompt"
        )
        # Every task_type-ish keyword inside the helper must reference the parameter.
        for call in ast.walk(helper):
            if not isinstance(call, ast.Call):
                continue
            for kw in call.keywords:
                if kw.arg in ("task_type", "tasktype"):
                    assert isinstance(kw.value, ast.Name), (
                        f"_assemble_prompt passes a literal to {kw.arg!r}; it must pass "
                        "the task_type parameter so the budget and the prompt agree"
                    )
                    assert kw.value.id == "task_type"

    def test_every_assemble_call_site_passes_a_task_type_by_keyword(self):
        """`task_type` must be passed by keyword at every call site.

        Positional works today and breaks silently later: reordering the helper's
        parameters would still pass the test suite while routing the wrong value
        into the memory budget. Keyword makes the call site name what it means.
        """
        tree = ast.parse(inspect.getsource(orch_module))
        n = 0
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_assemble_prompt"
            ):
                n += 1
                kwnames = {kw.arg for kw in node.keywords}
                assert "task_type" in kwnames, (
                    f"_assemble_prompt call at line {node.lineno} must pass "
                    "task_type by keyword, not positionally"
                )
        # Two call sites now: the shared `_run_turn` loop (chat + chat_stream) and
        # the scheduled-task path. It was three when chat() carried its own copy.
        assert n == 2, f"expected 2 prompt-assembly call sites, found {n}"


class TestFittingStaysCentralised:
    """The appends differ per path, so the fit stays at 2 sites -- but only 2."""

    def test_fit_prompt_to_cap_has_exactly_two_call_sites(self):
        src = inspect.getsource(orch_module)
        tree = ast.parse(src)
        sites = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_fit_prompt_to_cap"
        ]
        assert len(sites) == 2, f"_fit_prompt_to_cap called at {sites}"

    def test_no_raw_flat_character_cut_survives(self):
        """The system prompt must never be sliced by the cap outside the helper.

        The old flat cut sliced mid-frame: the frame kept its "### name" header
        and so read as present to the model while its tail facts were gone. It is
        now `_fit_prompt_to_cap`'s documented last resort, where dropping whole
        frames has already been tried.

        Deliberately narrow. This module has ~30 legitimate character slices
        (`contents[:500]`, `trace[-2:]`, `messages[-6:]`) and several local
        variables literally named `prompt` that hold a scheduled task's text --
        none of which are the system prompt, and a blanket "no slices" rule would
        forbid all of them. Slices passed to `logger.*` are exempt too:
        `system_prompt[:500]` in a debug log truncates a log line, not the model
        input. Exemption works by not descending into logger calls, since the
        logger call is the slice's *parent* and walking down from the subscript
        cannot see it. What is forbidden is a slice of the two system-prompt
        variables that reaches the model, because a flat cut there slices a frame
        mid-way.
        """
        tree = ast.parse(inspect.getsource(orch_module))
        offenders: list[tuple[str | None, int, str]] = []
        # The system-prompt variables this module builds. If a new one is
        # introduced, this list must gain it -- the test below fails loudly
        # otherwise only if the count of _fit_prompt_to_cap sites changes, so
        # extending the tuple is the deliberate part of keeping this honest.
        system_prompt_vars = {"system_prompt", "prompt_with_memory", "rebuilt"}

        def is_logger_call(node: ast.AST) -> bool:
            return (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "logger"
            )

        def visit(node: ast.AST, fn: str | None) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    visit(child, child.name)
                    continue
                if is_logger_call(child):
                    continue  # log arguments never reach the model
                if isinstance(child, ast.Subscript) and isinstance(child.slice, ast.Slice):
                    sliced = child.value
                    is_prompt = (
                        isinstance(sliced, ast.Name) and sliced.id in system_prompt_vars
                    )
                    if is_prompt and fn != "_fit_prompt_to_cap":
                        offenders.append((fn, child.lineno, ast.unparse(child)[:70]))
                visit(child, fn)

        visit(tree, None)
        assert not offenders, (
            "system prompt sliced outside _fit_prompt_to_cap -- a flat cut can "
            f"slice a frame mid-way: {offenders}"
        )


@pytest.mark.parametrize(
    "fn",
    [Orchestrator.chat, Orchestrator.chat_stream],
    ids=["chat", "chat_stream"],
)
def test_each_public_path_delegates_to_the_loop(fn):
    """Every public path must go through `_run_turn`, not render memory itself.

    Sizing and rendering were separate steps in the old copies, which is what let
    a path forget the sizing. Both public methods now delegate to one loop.
    """
    source = _function_source(fn)
    assert "_run_turn(" in source
    assert "format_memory_context(" not in source, (
        f"{fn.__name__} renders memory directly; sizing must go through the loop"
    )


def test_the_loop_sizes_memory_before_building_the_prompt():
    """The single loop sizes memory and builds the prompt through the helper."""
    source = _function_source(Orchestrator._run_turn)
    assert "_assemble_prompt(" in source
    assert "format_memory_context(" not in source
