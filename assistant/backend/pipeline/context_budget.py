"""The per-turn context budget: how much of the window tool results may use.

The tool loop shares one window with the **fixed cost** (the system prompt, every
tool schema, the history, and the user turn) and with the **answer**. A per-item
fraction cannot see that fixed cost, so a result sized against the whole window
can overflow it. This derives the content allowance from the *measured* fixed
cost instead:

    content = window − fixed cost − reserved answer

The allowance adapts: a long history or a bigger tool set shrinks it; a short one
grows it. It is set once per turn (from the orchestrator, which is where the
components are known) and read by the tool executors while the loop runs.

Chars, not tokens: we do not tokenize locally, so ``CHARS_PER_TOKEN`` is an
estimate and the real number is Ollama's ``prompt_eval_count`` (logged per turn as
``context_usage``). See ``docs/CONTEXT_THROUGHPUT.md``.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

CHARS_PER_TOKEN = 4

# Room for the model's answer. Matches ``think_num_predict_cap`` so a thinking
# model can reason and still answer, and makes the derived allowance no larger
# than the fraction it replaces in the common case.
RESERVED_OUTPUT_TOKENS = 4096

# A read smaller than this is not worth a marker.
MIN_CONTENT_CHARS = 2_000

# Used only when no turn budget is set (a direct tool call, the CLI, tests): a
# fraction of the configured chat window, the pre-T2 behaviour.
FALLBACK_WINDOW_FRACTION = 0.35


@dataclass(frozen=True)
class TurnBudget:
    """One turn's context split: window, measured fixed cost, reserved answer."""

    window_tokens: int
    fixed_cost_chars: int
    reserved_output_tokens: int = RESERVED_OUTPUT_TOKENS

    @property
    def fixed_cost_tokens(self) -> int:
        # Round up: under-counting the fixed cost over-states the allowance.
        return -(-self.fixed_cost_chars // CHARS_PER_TOKEN)

    @property
    def content_tokens(self) -> int:
        return max(
            0,
            self.window_tokens - self.fixed_cost_tokens - self.reserved_output_tokens,
        )

    @property
    def content_chars(self) -> int:
        """The characters tool results may use this turn."""
        return max(MIN_CONTENT_CHARS, self.content_tokens * CHARS_PER_TOKEN)


def measure_budget(
    window_tokens: int,
    *,
    system_prompt_chars: int = 0,
    tool_schema_chars: int = 0,
    history_chars: int = 0,
    user_message_chars: int = 0,
    reserved_output_tokens: int = RESERVED_OUTPUT_TOKENS,
) -> TurnBudget:
    """Build a turn's budget from its measured components."""
    return TurnBudget(
        window_tokens=window_tokens,
        fixed_cost_chars=(
            system_prompt_chars + tool_schema_chars + history_chars + user_message_chars
        ),
        reserved_output_tokens=reserved_output_tokens,
    )


_budget: ContextVar[TurnBudget | None] = ContextVar("turn_budget", default=None)


def set_turn_budget(budget: TurnBudget):
    """Install this turn's budget; returns a token to reset it with."""
    return _budget.set(budget)


def reset_turn_budget(token) -> None:
    _budget.reset(token)


def current_budget() -> TurnBudget | None:
    return _budget.get()


def content_char_limit() -> int:
    """The characters a content-carrying tool result may use right now.

    Uses the turn's budget when one is set; otherwise falls back to a fraction of
    the configured chat window so a direct tool call still cannot fill it.
    """
    budget = current_budget()
    if budget is not None:
        return budget.content_chars
    from assistant.backend.config import settings

    return max(
        MIN_CONTENT_CHARS,
        int(settings.chat_num_ctx * FALLBACK_WINDOW_FRACTION) * CHARS_PER_TOKEN,
    )
