"""Per-turn timing must be visible at INFO on the paths users actually take.

The per-phase timers in the orchestrator were always computed but logged at
DEBUG, which no deployment runs. Latency work therefore had no data to stand on,
and two of the three bugs fixed in this project were "a number nobody could
see" -- the mid-frame prompt cut and the stale embedding-model label. These tests
pin the logging itself, so the observability cannot be demoted back to DEBUG
without the suite noticing.

The assertions are on the *shape* of what is logged (which phases, in ms, at
INFO), not on specific durations. A test that asserted "recall takes under 100ms"
would be a flaky test with no diagnostic value: hardware and model load move
those numbers, and a failure would say nothing about whether the bug is fixed.
"""

from __future__ import annotations

import logging

import pytest

from assistant.backend.pipeline.orchestrator import Orchestrator


class TestTurnTimingFormat:
    """The consolidated per-turn line, via `_log_turn_timings`."""

    def test_emits_all_supplied_phases_in_milliseconds(self, caplog):
        caplog.set_level(logging.INFO, logger="assistant.backend.pipeline.orchestrator")

        Orchestrator._log_turn_timings(
            100.0,
            episode_ms=0.010,
            routing_ms=0.020,
            recall_ms=0.030,
            plan_ms=0.040,
            extraction_ms=0.050,
            correction_ms=0.060,
            search_ms=0.070,
            ttft_ms=0.080,
        )

        line = caplog.text
        # turn_start is 100.0 in fake time, so total is small but non-negative.
        assert "turn_timings:" in line
        # Every phase the helper accepts must actually reach the log line. A phase
        # that is computed but never logged is precisely the failure mode here.
        for phase, expected_ms in [
            ("routing_ms", 20),
            ("recall_ms", 30),
            ("plan_ms", 40),
            ("extraction_ms", 50),
            ("correction_ms", 60),
            ("search_ms", 70),
            ("ttft_ms", 80),
            ("episode_ms", 10),
        ]:
            assert f"{phase}={expected_ms}" in line, f"{phase} missing or mis-scaled: {line}"

    def test_omits_phases_that_did_not_run(self, caplog):
        """A phase that never ran should be absent, not reported as zero.

        A hardcoded 0 is indistinguishable from "it genuinely took no time", and
        that difference matters when reading a distribution.
        """
        caplog.set_level(logging.INFO, logger="assistant.backend.pipeline.orchestrator")

        Orchestrator._log_turn_timings(100.0, routing_ms=0.020)

        assert "routing_ms=20" in caplog.text
        assert "ttft_ms" not in caplog.text
        assert "search_ms" not in caplog.text

    def test_logs_at_info_not_debug(self, caplog):
        """The whole point: INFO is on in production, DEBUG is not."""
        caplog.set_level(logging.DEBUG, logger="assistant.backend.pipeline.orchestrator")

        Orchestrator._log_turn_timings(100.0, ttft_ms=0.080)

        record = next(r for r in caplog.records if "turn_timings:" in r.getMessage())
        assert record.levelno == logging.INFO, (
            "per-turn timings dropped below INFO, so production cannot see them"
        )


@pytest.fixture(scope="module")
def streaming_source() -> str:
    """Source of the single loop, the one the UI actually uses."""
    import inspect

    from assistant.backend.pipeline import orchestrator as orch_module

    return inspect.getsource(orch_module.Orchestrator._run_turn)


class TestStreamingPathIsInstrumented:
    """The streaming path is what the UI uses. It must be the instrumented one.

    The non-streaming `chat()` path had timings first. The streaming path is the
    one users experience and the one that can measure time-to-first-token at all,
    so it is the one that must not silently go uninstrumented.
    """

    def test_streaming_logs_pre_generation_phases(self, streaming_source):
        assert "turn_pregen:" in streaming_source
        # Each phase present in the pre-generation span.
        for phase in ("routing_ms", "recall_ms", "plan_ms", "extraction_ms", "search_ms"):
            assert f'"{phase}' in streaming_source or f"{phase}=%.0f" in streaming_source, (
                f"{phase} not reported by the streaming path"
            )

    def test_streaming_measures_time_to_first_token(self, streaming_source):
        """TTFT is the latency users feel, and only streaming can measure it."""
        assert "ttft_s" in streaming_source
        # It must be set from turn entry at the first delta, not measured at the
        # end of the stream (which would just be total turn time in disguise).
        assert "time.monotonic() - turn_start" in streaming_source

    def test_ttft_counts_finalize_not_only_text_delta(self, streaming_source):
        """TTFT must trigger on the first *visible* content, whatever carries it.

        `stream_tool_loop` uses blocking `chat()` calls and emits a whole-answer
        `FinalizeEvent`; `TextDeltaEvent` is defined in `streaming.py` but never
        constructed there. A measurement that only watched for `text_delta` would
        therefore report nothing at all for tool turns -- and if a default were
        substituted for the missing value it would report a small, satisfying,
        completely fictional latency for a wait that never ended early.
        """
        assert 'etype in ("text_delta", "finalize")' in streaming_source

    def test_streaming_logs_turn_timings_on_completion(self, streaming_source):
        assert "_log_turn_timings" in streaming_source


class TestNonStreamingPathIsInstrumented:
    def test_chat_routes_through_the_instrumented_loop(self):
        """chat() no longer logs itself; it drains the loop that does.

        Before unification chat() carried its own timing logging; now both public
        paths go through `_run_turn`, so instrumentation lives in one place and
        the adapter is only a consumer.
        """
        import inspect

        from assistant.backend.pipeline import orchestrator as orch_module

        chat_source = inspect.getsource(orch_module.Orchestrator.chat)
        assert "_run_turn(" in chat_source, "chat() no longer drains the shared loop"

        loop_source = inspect.getsource(orch_module.Orchestrator._run_turn)
        assert "turn_pregen:" in loop_source
        assert "_log_turn_timings" in loop_source
        # Search is logged inside the pre-generation span, so a slow search turn
        # cannot hide behind a low pregen_ms.
        assert "search_ms" in loop_source
