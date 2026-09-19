# Plan: Math Model Integration & Ollama Capability Fixes

## Objective
Integrate a dedicated math/code model role for verified computation, fix incorrect `think` flag assumptions, and add embedding batching for speed. All changes must preserve local-first privacy, zero telemetry, and the existing cognitive architecture.

---

## Phase 1: MATH_MODEL Role & Python Tool (Highest Impact)

### 1.1 Config Addition
**File:** `assistant/backend/config.py`
- Add `math_model: str = "qwen3-coder:30b"` to Settings
- Add `math_num_ctx: int = 16384` (larger context for code execution)
- Add `math_keep_alive: str = "10m"` (shorter than chat; load on demand)

### 1.2 LLM Client Extension
**File:** `assistant/backend/pipeline/llm_client.py`
- Add `math_model` and `math_num_ctx` to `OllamaClient.__init__`
- Add `supports_tools(model)` probe (checks `tools` capability via `/api/show`)
- Add `async def execute_python(self, code: str) -> str` — sends code to math model with `tools=[python_tool]`, returns stdout/stderr

### 1.3 Python Execution Tool
**File:** `assistant/backend/pipeline/tools.py`
- Add `ExecutePythonArgs(BaseModel)`: `code: str`, `timeout: int = 30`
- Add `execute_python` handler: writes code to temp file, runs `python3 -c`, captures output, enforces timeout
- Register in `builtin_tools()` when `math_model` is configured and supports tools

### 1.4 Orchestrator Integration
**File:** `assistant/backend/pipeline/orchestrator.py`
- Detect math/computation intent in `classify_intent` or new `detect_math_intent(query)`:
  - Keywords: "calculate", "compute", "solve", "derivative", "integral", "regression", "monte carlo", "npv", "irr", "statistics", "optimize"
  - Structural: contains formulas, units, multi-step numeric reasoning
- When math intent detected + `settings.math_model` configured:
  - Call `llm_client.execute_python()` with generated code
  - Inject result into system prompt as "**Computed Result:** ..."
  - Log as `source_type="computation"` with high reliability (0.9)

### 1.5 Tests
**File:** `assistant/tests/test_math_model.py`
- Unit: math intent detection accuracy
- Integration: simple calculation (2+2), financial (NPV), statistical (mean/std), symbolic (sympy if available)
- Regression: non-math queries don't trigger math model

---

## Phase 2: Fix `think` Flag Logic (Correctness Bug)

### 2.1 Capability-Gated Thinking
**File:** `assistant/backend/pipeline/llm_client.py`
- Modify `chat()`: when `think=True` but model doesn't support `thinking` capability, log warning and set `think=False`
- Add `async def get_model_capabilities(model)` returning cached capabilities dict

### 2.2 Orchestrator Fix
**File:** `assistant/backend/pipeline/orchestrator.py:738-739`
- Replace `think = True if plan.think else settings.chat_think_default` with:
  ```python
  think = False
  if plan.think:
      think = await self.llm_client.supports_thinking(self.llm_client.chat_model)
  elif settings.chat_think_default:
      think = await self.llm_client.supports_thinking(self.llm_client.chat_model)
  ```

### 2.3 Tests
**File:** `assistant/tests/test_thinking_flag.py`
- Mock model without `thinking` capability → verify `think` not sent
- Mock model with `thinking` → verify `think` sent on escalation

---

## Phase 3: Embedding Batching (Speed)

### 3.1 Batched Embedding API
**File:** `assistant/backend/pipeline/llm_client.py`
- Modify `embed()` to accept `text: str | list[str]`
- When list: single `/api/embeddings` call with `"prompt": texts`
- Return `list[EmbeddingResponse]` or single `EmbeddingResponse`
- Update cache key to include all texts (hash of concatenated)

### 3.2 Consolidation Integration
**File:** `assistant/backend/scheduler/runner.py` (consolidation path)
- Replace sequential `embed_frames` loop with batched calls (batch size 50)
- Measure and log speedup

### 3.3 Tests
**File:** `assistant/tests/test_embedding_batching.py`
- Unit: batch vs single equivalence
- Integration: consolidation speed benchmark

---

## Phase 4: Reasoning Trace Persistence (Observability)

### 4.1 Episode Metadata
**File:** `assistant/backend/db/schema.py`
- Add `reasoning_trace TEXT` column to `episodes` table (nullable)

### 4.2 Tool Loop Capture
**File:** `assistant/backend/pipeline/tools.py:563-566`
- Store `reasoning_trace` in `tool_results` metadata
- Pass to `_log_episode` in orchestrator

### 4.3 Orchestrator Update
**File:** `assistant/backend/pipeline/orchestrator.py`
- Collect `reasoning_trace` from tool loop result
- Include in `create_episode` call

---

## Phase 5: Cross-Source Corroboration (Accuracy)

### 5.1 Extraction Metadata
**File:** `assistant/backend/pipeline/extractor.py`
- Add `source_urls: list[str]` to extracted slots
- Track unique domains per fact

### 5.2 Application Gate
**File:** `assistant/backend/pipeline/extractor.py:apply_search_extraction`
- For facts tagged `financial`, `medical`, `legal`, `safety`:
  - Require ≥2 unique domains in source_urls
  - Otherwise: store with `confidence=0.3`, flag `needs_corroboration=true`
- Add `corroboration_status` to extraction summary

---

## Acceptance Criteria

| Criterion | Verification |
|-----------|--------------|
| Math model computes NPV/IRR correctly | `pytest assistant/tests/test_math_model.py::test_financial` |
| Math model solves calculus problems | `pytest assistant/tests/test_math_model.py::test_calculus` |
| Non-math queries don't trigger math model | `pytest assistant/tests/test_math_model.py::test_no_false_positive` |
| `think` flag never sent to non-thinking models | `pytest assistant/tests/test_thinking_flag.py` |
| Embedding batching 5x faster for consolidation | Benchmark in `test_embedding_batching.py` |
| Reasoning traces stored in episodes | `pytest assistant/tests/test_reasoning_trace.py` |
| Financial facts need 2+ sources | `pytest assistant/tests/test_corroboration.py` |
| All existing tests pass | `pytest assistant/tests/` (exclude known env issues) |
| Lint clean | `ruff check .` |

---

## Rollback Plan
Each phase is independent. If Phase 1 breaks:
- Revert `config.py`, `llm_client.py`, `tools.py`, `orchestrator.py` changes
- Math model simply unused; existing chat/utility models handle all queries

---

## Dependencies
- `qwen3-coder:30b` must be pulled in Ollama (`docker exec ollama ollama pull qwen3-coder:30b`)
- Python execution sandbox: consider `nsjail` or `firejail` for production (MVP: timeout + no network)

---

## Design Principle Compliance

| Principle | Adherence |
|-----------|-----------|
| **Model-first correction** | Math model reasons about code; no scripted fallbacks |
| **No templated responses** | Computation results injected; chat model speaks naturally |
| **Lean on model flexibility** | Intent detection uses LLM classification, not keywords only |
| **Scheduled tasks are memory** | Computation results stored as slots with source_type="computation" |
| **Clean ship** | No dead code; each phase adds test coverage |
| **Stability: no regressions** | Each phase has regression tests; full suite must pass |
| **Safety & Privacy** | Python execution local only; no external calls; user consent for math model if added later |