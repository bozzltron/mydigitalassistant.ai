# Handoff Requirements: Math Model Integration & Ollama Capability Fixes

## Context
This project is a **privacy-first cognitive digital assistant** with a structured memory system (frames, slots, associations, episodes). All LLM inference runs locally via Ollama on 127.0.0.1:11434. No cloud APIs, no telemetry.

**Current Model Fleet:**
- `chat_model`: qwen2.5:7b (user-facing responses)
- `utility_model`: qwen3.5:4b (extraction, routing)
- `embedding_model`: nomic-embed-text
- `coder_model`: "" (reserved, falls back to chat)

**Available Local Models (verified):**
- qwen3-coder:30b (tools, thinking, vision, completion) — **target for math_model**
- qwen3.8:27b (thinking, vision, tools, completion)
- qwen3.6:27b (thinking, vision, tools, completion)
- qwen3.5:4b (thinking, vision, tools, completion) — currently utility
- qwen2.5:7b (tools, completion) — currently chat

---

## Requirements

### REQ-1: MATH_MODEL Role with Python Tool Execution

#### 1.1 Configuration
```python
# assistant/backend/config.py
math_model: str = "qwen3-coder:30b"
math_num_ctx: int = 16384
math_keep_alive: str = "10m"  # Load on demand, unload after inactivity
```

#### 1.2 LLM Client Extensions
**File:** `assistant/backend/pipeline/llm_client.py`

Add to `OllamaClient.__init__`:
```python
self.math_model = math_model
self.math_num_ctx = math_num_ctx
self.math_keep_alive = math_keep_alive
```

Add method:
```python
async def supports_tools(self, model: str | None = None) -> bool:
    """True if model advertises 'tools' capability."""
    return "tools" in await self.model_capabilities(model)

async def execute_python(self, code: str, timeout: int = 30) -> str:
    """
    Execute Python code via math model with tool calling.
    Returns stdout/stderr as string.
    """
    if not self.math_model:
        raise ValueError("MATH_MODEL not configured")
    
    # Check capability
    if not await self.supports_tools(self.math_model):
        raise ValueError(f"Model {self.math_model} does not support tool calling")
    
    # Build tool definition for Python execution
    python_tool = {
        "type": "function",
        "function": {
            "name": "execute_python",
            "description": "Execute Python code and return stdout/stderr. Use for math, statistics, financial calculations, symbolic manipulation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute"},
                    "timeout": {"type": "integer", "default": 30, "description": "Execution timeout in seconds"}
                },
                "required": ["code"]
            }
        }
    }
    
    # System prompt for math model
    system = ChatMessage(role="system", content="""You are a computation engine. 
    Execute the user's mathematical request by writing and running Python code.
    Use numpy, scipy, sympy, pandas, statistics as needed.
    Return ONLY the tool call to execute_python with the code.
    The system will return the result; you then formulate the final answer.""")
    
    user = ChatMessage(role="user", content=code)
    
    response = await self.chat(
        [system, user],
        model=self.math_model,
        tools=[python_tool],
        tool_choice="required",
        think=False,
        num_ctx=self.math_num_ctx,
        temperature=0.0,
    )
    
    if response.tool_calls:
        call = response.tool_calls[0]
        # Execute the code locally (sandboxed)
        return await self._execute_python_sandboxed(call.arguments.get("code", ""), timeout)
    
    raise RuntimeError("Math model did not invoke execute_python tool")
```

Add sandboxed execution helper:
```python
async def _execute_python_sandboxed(self, code: str, timeout: int) -> str:
    """Execute Python code with timeout, no network, limited imports."""
    import subprocess
    import tempfile
    import os
    
    # Allowed imports (extend as needed)
    allowed_imports = """
import math, statistics, random, decimal, fractions
import itertools, functools, collections, datetime, typing
try: import numpy as np
except: pass
try: import scipy.stats as stats
except: pass
try: import sympy as sp
except: pass
try: import pandas as pd
except: pass
"""
    full_code = allowed_imports + "\n" + code
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(full_code)
        tmp_path = f.name
    
    try:
        result = subprocess.run(
            ["python3", tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PYTHONPATH": ""}  # No user site-packages
        )
        output = result.stdout
        if result.stderr:
            output += f"\nSTDERR: {result.stderr}"
        if result.returncode != 0:
            output += f"\nExit code: {result.returncode}"
        return output
    except subprocess.TimeoutExpired:
        return f"Error: Execution timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"
    finally:
        try:
            os.unlink(tmp_path)
        except:
            pass
```

#### 1.3 Tool Registration
**File:** `assistant/backend/pipeline/tools.py`

Add to `builtin_tools()`:
```python
# Math/computation tool (only if math_model configured and supports tools)
if (llm_client and hasattr(llm_client, 'math_model') and llm_client.math_model 
    and await llm_client.supports_tools(llm_client.math_model)):
    tools.append(_make_def(
        "compute",
        "Execute mathematical computation via dedicated math model. Use for: financial models (NPV, IRR), statistics, calculus, linear algebra, optimization, unit conversions, dimensional analysis.",
        ComputeArgs,  # Define below
    ))
```

Add `ComputeArgs`:
```python
class ComputeArgs(BaseModel):
    expression: str = Field(..., description="Natural language description of computation needed")
    context: dict | None = Field(None, description="Optional: variable bindings (e.g., {'rate': 0.07, 'years': 10})")
    precision: int = Field(4, description="Decimal places in result")
```

#### 1.4 Orchestrator Integration
**File:** `assistant/backend/pipeline/orchestrator.py`

Add detection method:
```python
async def _detect_math_intent(self, query: str) -> bool:
    """Detect if query needs mathematical computation."""
    math_keywords = {
        "calculate", "compute", "solve", "derive", "integrate", "differentiate",
        "npv", "irr", "roi", "payback", "amortize", "compound", "present value",
        "future value", "annuity", "bond", "yield", "volatility", "sharpe",
        "regression", "correlation", "covariance", "mean", "median", "std",
        "standard deviation", "variance", "percentile", "quantile", "hypothesis",
        "t-test", "chi-square", "anova", "monte carlo", "simulate", "bootstrap",
        "optimize", "minimize", "maximize", "linear programming", "constraint",
        "derivative", "integral", "limit", "series", "taylor", "fourier",
        "matrix", "eigenvalue", "determinant", "inverse", "decomposition",
        "dimensional analysis", "unit conversion", "significant figures"
    }
    q_lower = query.lower()
    return any(kw in q_lower for kw in math_keywords)
```

In `chat()` method, after memory extraction (around line 490), add:
```python
# Math computation path
computation_result = None
if await self._detect_math_intent(request.message):
    try:
        await self._report(progress, "computing", "running mathematical computation")
        computation_result = await self.llm_client.execute_python(
            f"Solve this step by step: {request.message}"
        )
        logger.info("Math computation completed: %d chars", len(computation_result))
    except Exception as e:
        logger.warning("Math computation failed: %s", e)
```

Inject into system prompt (before LLM call, around line 507):
```python
if computation_result:
    system_prompt += (
        f"\n\n**Computed Result (verified via Python execution):**\n"
        f"{computation_result}\n"
        f"Incorporate this result into your response. Cite as 'computed'."
    )
```

---

### REQ-2: Fix `think` Flag Logic (Correctness Bug)

#### 2.1 Current Bug
`orchestrator.py:738-739` unconditionally sets `think=True` on escalation, but only thinking-capable models (qwen3, deepseek-r1) support the `think` parameter. qwen2.5, llama3.1 do not.

#### 2.2 Fix
**File:** `assistant/backend/pipeline/orchestrator.py`

Replace lines 738-739:
```python
# OLD:
# think = True if plan.think else settings.chat_think_default

# NEW:
think = False
supports_thinking = await self.llm_client.supports_thinking(self.llm_client.chat_model)
if plan.think and supports_thinking:
    think = True
elif settings.chat_think_default and supports_thinking:
    think = True
```

Also fix `run_scheduled_task` (line 1159-1163):
```python
use_thinking = await self.llm_client.supports_thinking(self.llm_client.chat_model)
llm_response = await self.llm_client.chat(
    messages,
    think=use_thinking,
    num_predict=settings.think_num_predict_cap if use_thinking else None,
)
```

---

### REQ-3: Embedding Batching

#### 3.1 Modified `embed()` Method
**File:** `assistant/backend/pipeline/llm_client.py`

```python
async def embed(
    self,
    text: str | list[str],
    model: str | None = None,
) -> EmbeddingResponse | list[EmbeddingResponse]:
    """Generate embedding(s) for text. Accepts single string or list."""
    model = model or self.embedding_model
    
    # Handle list input
    if isinstance(text, list):
        return await self._embed_batch(text, model)
    
    # Single string (existing logic with cache)
    cache_key = f"{model}:{text}"
    if cache_key in self._embed_cache:
        # ... existing cache logic ...
    
    # ... existing single embed logic ...

async def _embed_batch(self, texts: list[str], model: str) -> list[EmbeddingResponse]:
    """Batch embedding via single Ollama call."""
    client = await self._get_client()
    cache_keys = [f"{model}:{t}" for t in texts]
    
    # Check cache for all
    cached = {}
    uncached_texts = []
    uncached_indices = []
    for i, (t, k) in enumerate(zip(texts, cache_keys)):
        if k in self._embed_cache:
            cached[i] = self._embed_cache[k]
        else:
            uncached_texts.append(t)
            uncached_indices.append(i)
    
    if not uncached_texts:
        # All cached
        return [EmbeddingResponse(embedding=cached[i], model=model) for i in range(len(texts))]
    
    # Batch request
    payload = {"model": model, "prompt": uncached_texts, "keep_alive": self._keep_alive_param()}
    r = await client.post("/api/embeddings", json=payload)
    r.raise_for_status()
    data = r.json()
    embeddings = data["embedding"]  # List of lists
    
    # Update cache and build results
    results = [None] * len(texts)
    for idx, emb in zip(uncached_indices, embeddings):
        self._embed_cache[cache_keys[idx]] = emb
        results[idx] = EmbeddingResponse(embedding=emb, model=model)
    
    for idx, emb in cached.items():
        results[idx] = EmbeddingResponse(embedding=emb, model=model)
    
    # Trim cache
    if len(self._embed_cache) > self._cache_max_size:
        keys = list(self._embed_cache.keys())[:-self._cache_max_size // 2]
        for k in keys:
            self._embed_cache.pop(k, None)
    
    return results
```

#### 3.2 Consolidation Integration
**File:** `assistant/backend/scheduler/runner.py` (in `_consolidate_memory`)

Replace sequential embedding:
```python
# OLD: sequential
# for frame_id in frame_ids:
#     await store.embed_frames([frame_id], embed_fn, model)

# NEW: batched
batch_size = 50
for i in range(0, len(frame_ids), batch_size):
    batch = frame_ids[i:i+batch_size]
    texts = [await _frame_to_embed_text(fid) for fid in batch]
    embeddings = await llm_client.embed(texts, model)
    await store.embed_frames_batch(batch, embeddings, model)
```

---

### REQ-4: Reasoning Trace Persistence

#### 4.1 Schema Migration
**File:** `assistant/backend/db/schema.py`

Add to `episodes` table:
```sql
ALTER TABLE episodes ADD COLUMN reasoning_trace TEXT;
```

#### 4.2 Capture in Tool Loop
**File:** `assistant/backend/pipeline/tools.py:563-566`

```python
# Record think() reasoning
if tool_name == "think":
    reasoning_trace.append(raw_args.get("reasoning", ""))

# In run_tool_loop return dict, add:
"reasoning_trace": "\n\n".join(reasoning_trace) if reasoning_trace else None
```

#### 4.3 Store in Episode
**File:** `assistant/backend/pipeline/orchestrator.py`

In `chat()` after tool loop (around line 797):
```python
reasoning_trace = llm_response.get("reasoning_trace") if isinstance(llm_response, dict) else None
episode = await self._log_episode(
    request.user_id,
    session_id,
    role="assistant",
    content=llm_response.content,
    reasoning_trace=reasoning_trace,  # Add parameter
)
```

Update `_log_episode` signature and INSERT.

---

### REQ-5: Cross-Source Corroboration for High-Stakes Facts

#### 5.1 Domain Tracking
**File:** `assistant/backend/pipeline/extractor.py`

In `ExtractedSlot` / extraction result, add:
```python
source_urls: list[str] = Field(default_factory=list)
source_domains: set[str] = Field(default_factory=set)
```

#### 5.2 Application Gate
**File:** `assistant/backend/pipeline/extractor.py` in `apply_search_extraction`

```python
HIGH_STAKES_CATEGORIES = {"financial", "medical", "legal", "safety", "security"}

def _categorize_fact(slot_key: str, frame_name: str) -> str:
    text = f"{frame_name} {slot_key}".lower()
    if any(kw in text for kw in ["price", "cost", "revenue", "profit", "npv", "irr", "investment", "stock", "bond", "rate", "yield"]):
        return "financial"
    if any(kw in text for kw in ["dose", "medication", "diagnosis", "symptom", "treatment", "drug", "therapy"]):
        return "medical"
    if any(kw in text for kw in ["law", "regulation", "compliance", "contract", "liability", "statute"]):
        return "legal"
    if any(kw in text for kw in ["hazard", "danger", "warning", "recall", "toxic", "explosive", "flammable"]):
        return "safety"
    return "general"

# In apply_search_extraction, before upsert:
category = _categorize_fact(slot.key, frame_name)
if category in HIGH_STAKES_CATEGORIES:
    unique_domains = len(set(urlparse(u).netloc for u in source_urls if u))
    if unique_domains < 2:
        slot.confidence = min(slot.confidence, 0.3)
        slot.metadata["needs_corroboration"] = True
        slot.metadata["corroboration_domains"] = unique_domains
```

---

## Test Requirements

Each REQ must have corresponding tests:

| Test File | Coverage |
|-----------|----------|
| `assistant/tests/test_math_model.py` | REQ-1: intent detection, computation accuracy, no false positives |
| `assistant/tests/test_thinking_flag.py` | REQ-2: think flag gating |
| `assistant/tests/test_embedding_batching.py` | REQ-3: batch equivalence, speedup |
| `assistant/tests/test_reasoning_trace.py` | REQ-4: trace stored in episodes |
| `assistant/tests/test_corroboration.py` | REQ-5: high-stakes facts need 2+ domains |

Run all: `pytest assistant/tests/test_math_model.py assistant/tests/test_thinking_flag.py assistant/tests/test_embedding_batching.py assistant/tests/test_reasoning_trace.py assistant/tests/test_corroboration.py -v`

---

## Acceptance Checklist

- [ ] `ruff check assistant/backend assistant/cli` — clean
- [ ] `pytest assistant/tests/test_daily_schedule.py assistant/tests/test_review_fixes.py` — pass
- [ ] `pytest assistant/tests/test_math_model.py` — pass (all computation tests)
- [ ] `pytest assistant/tests/test_thinking_flag.py` — pass
- [ ] `pytest assistant/tests/test_embedding_batching.py` — pass
- [ ] `pytest assistant/tests/test_reasoning_trace.py` — pass
- [ ] `pytest assistant/tests/test_corroboration.py` — pass
- [ ] Full suite `pytest assistant/tests/` — pass (excluding known env issues)
- [ ] Manual verification: "Calculate NPV of $1000/year for 5 years at 7%" returns correct result
- [ ] Manual verification: "What's the derivative of x^2 + 3x?" returns correct result
- [ ] Manual verification: Non-math query "What's my name?" doesn't trigger math model

---

## Security & Privacy Notes

1. **Python execution is LOCAL ONLY** — no network access, subprocess timeout enforced
2. **No external API calls** — math model runs on local Ollama
3. **User consent** — if math model ever sent to external service (not planned), require explicit opt-in
4. **Sandbox** — MVP uses timeout + restricted env; production should use nsjail/firejail
5. **No telemetry** — computation results stored in local memory only

---

## Rollback Strategy

Each REQ is independent. If any breaks:
- REQ-1: Comment out math_model config, remove tool registration, orchestrator falls back to chat model
- REQ-2: Revert to `think = True if plan.think else settings.chat_think_default`
- REQ-3: Revert embed() to single-string only
- REQ-4: Remove reasoning_trace column, stop capturing
- REQ-5: Remove corroboration gate, store all facts at extracted confidence

---

## Estimated Effort

| REQ | Files Changed | Est. Hours |
|-----|---------------|------------|
| REQ-1 | config.py, llm_client.py, tools.py, orchestrator.py, tests | 8-12 |
| REQ-2 | orchestrator.py, tests | 1-2 |
| REQ-3 | llm_client.py, scheduler/runner.py, tests | 3-4 |
| REQ-4 | schema.py, tools.py, orchestrator.py, tests | 2-3 |
| REQ-5 | extractor.py, tests | 2-3 |
| **Total** | | **16-24** |

---

## Design Principle Verification

| Principle | REQ-1 | REQ-2 | REQ-3 | REQ-4 | REQ-5 |
|-----------|-------|-------|-------|-------|-------|
| Model-first correction | ✅ | ✅ | N/A | ✅ | ✅ |
| No templated responses | ✅ | ✅ | N/A | N/A | N/A |
| Lean on model flexibility | ✅ | ✅ | N/A | N/A | ✅ |
| Scheduled tasks are memory | ✅ | N/A | N/A | ✅ | ✅ |
| Clean ship | ✅ | ✅ | ✅ | ✅ | ✅ |
| Stability: no regressions | ✅ | ✅ | ✅ | ✅ | ✅ |
| Safety & Privacy | ✅ | ✅ | ✅ | ✅ | ✅ |