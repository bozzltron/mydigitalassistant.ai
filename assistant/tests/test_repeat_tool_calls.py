"""A repeated tool call is not progress — it is a spin, and it is not re-run.

Measured trigger (2026-10-24): a live Max turn searched `universe cosmology
breakthrough October 2026` and `James Webb telescope new discovery October 2026`
in consecutive rounds. On the 27B each of those rounds costs minutes of prefill,
and the second search returns the same snippets the model already had. The guard
is structural — same tool, same arguments, already **succeeded** this turn — not a
guess about the model's wording.

These tests pin the three rules:

1. a successful repeat is answered from the transcript, not executed again;
2. a **failed** call is still retryable (only successes are remembered);
3. a state-changing call clears the ledger, so read-after-write sees the write.
"""

from assistant.backend.pipeline.llm_client import ChatResponse, ToolCall
from assistant.backend.pipeline.streaming import stream_tool_loop
from assistant.backend.pipeline.tool_executor import ToolResult, init_store


class StubLLM:
    """Plays back a scripted list of responses, recording each prompt."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.tools_model = "test-model"
        self.calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        # Snapshot: the loop mutates its message list in place across rounds, so
        # holding the reference would make every recorded prompt look identical.
        self.calls.append({"messages": list(messages), **kwargs})
        return self.responses.pop(0)

    def context_window(self, model=None):
        return 32768


def _call(name: str, **arguments) -> ChatResponse:
    return ChatResponse(
        content="", model="m", done=True,
        tool_calls=[ToolCall(name=name, arguments=arguments)],
    )


def _answer(text: str) -> ChatResponse:
    return ChatResponse(content=text, model="m", done=True)


async def _run(llm: StubLLM):
    events = []
    async for ev in stream_tool_loop(
        llm,
        messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "look the thing up"},
        ],
        tools=[],
        model="test-model",
        user_id="1",
        session_id="s-1",
    ):
        events.append(ev)
    return events


def _tool_messages(llm: StubLLM, index: int) -> list[str]:
    return [m.content for m in llm.calls[index]["messages"] if m.role == "tool"]


async def test_a_successful_repeat_is_not_executed_again(tmp_path, monkeypatch):
    init_store(str(tmp_path / "stream.db"))
    executed: list[tuple] = []

    async def fake_execute(name, args, user_id=None, session_id=""):
        executed.append((name, args))
        return ToolResult(success=True, data={"results": ["one snippet"]})

    monkeypatch.setattr(
        "assistant.backend.pipeline.tool_executor.execute_tool", fake_execute
    )

    search = _call("web_search", query="same query", num_results=5)
    llm = StubLLM([search, search, search, _answer("done")])
    events = await _run(llm)

    # Executed once; both repeats were answered from the ledger.
    assert executed == [("web_search", {"query": "same query", "num_results": 5})]
    assert any('"type": "tool_result"' in ev and '"duplicate": true' in ev for ev in events)

    # The model is told the truth in its own transcript: the call was not run, and
    # where the earlier result is. The third one names the count, not a scolding.
    # (Index +1: the note lands in the *next* prompt.)
    second = "\n".join(_tool_messages(llm, 2))
    assert "already called" in second
    assert "not run again" in second
    assert "still visible" in second
    third = "\n".join(_tool_messages(llm, 3))
    assert "third identical call" in third

    # The first (real) result is still in the transcript for it to use.
    first = "\n".join(_tool_messages(llm, 1))
    assert "one snippet" in first


async def test_a_failed_call_stays_retryable(tmp_path, monkeypatch):
    """Only successes enter the ledger — a failed read must be retryable."""
    init_store(str(tmp_path / "stream.db"))
    outcomes = [False, True]
    executed: list[tuple] = []

    async def fake_execute(name, args, user_id=None, session_id=""):
        executed.append((name, args))
        ok = outcomes.pop(0)
        return ToolResult(
            success=ok, data={}, error="" if ok else "File not found: nope.csv"
        )

    monkeypatch.setattr(
        "assistant.backend.pipeline.tool_executor.execute_tool", fake_execute
    )

    read = _call("read_file", path="nope.csv")
    llm = StubLLM([read, read, _answer("ok")])
    await _run(llm)

    assert len(executed) == 2, "the retry must actually run"


async def test_the_note_is_honest_when_the_earlier_result_was_dropped(
    tmp_path, monkeypatch
):
    """If the aggregate cap collapsed the first result, don't claim it is visible.

    The note is the model's only evidence about its own transcript; telling it the
    result is above when it was replaced by the drop marker would make it answer
    from a fragment it cannot see.
    """
    init_store(str(tmp_path / "stream.db"))
    monkeypatch.setattr(
        "assistant.backend.pipeline.context_budget.content_char_limit", lambda: 120
    )

    async def fake_execute(name, args, user_id=None, session_id=""):
        # Every result is larger than the whole allowance, so adding a second one
        # collapses the first to the drop marker.
        return ToolResult(success=True, data={"payload": "x" * 200})

    monkeypatch.setattr(
        "assistant.backend.pipeline.tool_executor.execute_tool", fake_execute
    )

    first = _call("web_search", query="first")
    other = _call("web_search", query="other")
    llm = StubLLM([first, other, first, _answer("done")])
    await _run(llm)

    note = "\n".join(_tool_messages(llm, 3))
    assert "dropped to fit the context budget" in note
    assert "still visible" not in note


async def test_a_write_clears_the_ledger(tmp_path, monkeypatch):
    """Read after write must see the write, so the cache cannot survive it."""
    init_store(str(tmp_path / "stream.db"))
    executed: list[tuple] = []

    async def fake_execute(name, args, user_id=None, session_id=""):
        executed.append((name, args))
        return ToolResult(success=True, data={"ok": True})

    monkeypatch.setattr(
        "assistant.backend.pipeline.tool_executor.execute_tool", fake_execute
    )

    search = _call("web_search", query="same query")
    write = _call("write_file", path="notes.md", content="hi")
    llm = StubLLM([search, write, search, _answer("done")])
    await _run(llm)

    # search, write, search — the second search re-ran after the write.
    assert [name for name, _ in executed] == ["web_search", "write_file", "web_search"]
