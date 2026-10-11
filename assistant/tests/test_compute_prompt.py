"""The compute prompt must not promise a module the image does not have.

Regression: ``execute_python``'s system prompt said *"Use numpy, scipy, sympy, pandas,
statistics as needed"* — and only numpy was installed (2.x, so ``np.irr`` was gone too).
**Every** hard-math failure in the probe was a ``ModuleNotFoundError``: the model wrote
correct code, followed the instructions, and crashed. It was invisible until the generated
code was captured (``assistant/experiments/business_math/``).

The prompt states its availability claims in two lines; this asserts both against reality.
"""

import importlib.util
import re

from assistant.backend.pipeline.llm_client import COMPUTE_SYSTEM_PROMPT


def _named(pattern: str) -> list[str]:
    match = re.search(pattern, COMPUTE_SYSTEM_PROMPT)
    assert match, (
        f"the compute prompt no longer states {pattern!r} — update this test with it, "
        "so the availability claims stay checked"
    )
    return [name.strip() for name in match.group(1).split(",") if name.strip()]


def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def test_compute_prompt_only_names_installed_modules():
    """Every module the prompt offers must actually import."""
    for module in _named(r"Available:\s*([^\n.]+)"):
        assert _installed(module), (
            f"the compute prompt promises {module!r}, which is not installed — the model "
            "will follow the instruction and get a ModuleNotFoundError"
        )


def test_compute_prompt_absent_list_is_really_absent():
    """If one of these gets installed, the prompt is out of date and should say so."""
    for module in _named(r"NOT installed:\s*([^-]+)"):
        assert not _installed(module), (
            f"the compute prompt says {module!r} is missing, but it is installed — "
            "drop it from the NOT-installed line and add it to Available"
        )
