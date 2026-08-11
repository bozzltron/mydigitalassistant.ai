# Implementation Plan: Shared Conversation Context + Credible Sources

## Decisions from review

| Topic | Decision |
|-------|----------|
| Conversation history | Include up to **6 prior turns** (3 user + 3 assistant) from the current session in the LLM message list |
| Citation style | **Appended sources** at the end of the assistant response, not inline markers |
| Source URLs | **Per-slot specific URLs** for search-extracted facts |
| Model for context/citation | Use a **stronger model** than the current 3b utility model for extraction, citation, and context-aware tasks |

## Stronger model choice

Current config:
- `CHAT_MODEL=qwen2.5:7b`
- `UTILITY_MODEL=qwen2.5:3b`

Plan:
- Keep `CHAT_MODEL` as-is for user-facing chat.
- Add `REASONING_MODEL` (default `qwen2.5:7b` or `qwen3:8b`) for:
  - Fact extraction from search results
  - Citation generation
  - Context-dependent tasks
- Leave `UTILITY_MODEL` for cheap classification/routing.

> Note: If the user's Ollama host only has one model pulled, `REASONING_MODEL` can be set identical to `CHAT_MODEL`.

## Part A: Shared conversation context

### Files to modify

1. **`backend/memory/store.py`**
   - Add `get_episodes_for_session(user_id, session_id, limit=20)` if not already present.
   - Already exists; ensure it returns user-scoped session episodes.

2. **`backend/memory/retrieval.py`**
   - Modify `retrieve()` to accept `session_id` and use it:
     - Fetch current-session episodes first.
     - If session has fewer than 2 turns, fall back to recent user-wide episodes.
     - Keep `recent_episodes` in `MemoryContext`.
   - Add helper `_get_session_episodes(store, user_id, session_id, limit)`.

3. **`backend/pipeline/orchestrator.py`**
   - Build LLM message list as:
     ```
     [system]
     [user_turn_1, assistant_turn_1]
     [user_turn_2, assistant_turn_2]
     ... up to 6 turns
     [current_user_message]
     ```
   - Cap at 6 prior turns (not including current turn).
   - Use the `recent_episodes` already loaded by the retriever to avoid extra DB calls.

4. **`backend/pipeline/llm_client.py`**
   - Update `build_system_prompt()` to mention that recent conversation history is included and pronouns should be resolved against it.

5. **`tests/test_orchestrator.py`**
   - Add test asserting prior session turns appear as alternating user/assistant messages before the current user message.

6. **`tests/test_retrieval.py`**
   - Add test asserting session-scoped episodes are preferred over user-wide episodes when `session_id` is provided.

## Part B: Credible source references

### Files to modify

1. **`backend/pipeline/extractor.py`**
   - `apply_search_extraction()`:
     - Use per-slot `reliability_map` instead of flat `INITIAL_SEARCH_RELIABILITY`.
     - Set per-slot `source_url` from the first search result whose snippet corroborates the fact.
     - Prefer `.edu`, `wikipedia.org`, and major news domains; otherwise use the first corroborating URL.
   - `extract_and_apply()`:
     - Set `source_type="conversation"` and `source_episode_id` for conversation-extracted facts.

2. **`backend/memory/retrieval.py`**
   - `format_memory_context()`:
     - Render each slot with source info:
       ```
       - uv_index = 8 (conf: 0.50, source: search, url: https://weather.com/uv/austin, reliability: 0.65)
       - name = Adele (conf: 0.50, source: conversation, episode: 12)
       ```

3. **`backend/pipeline/reasoner.py`**
   - Update `format_plan_for_prompt()` to instruct the model to list sources at the end of its response.

4. **`backend/pipeline/orchestrator.py`**
   - Add `citations: list[str]` to `ChatResponse`.
   - After LLM response, build citations from:
     - Search result URLs used
     - Source URLs of slots referenced in memory context
   - De-duplicate and append a "Sources:" section to the response text.

5. **`backend/pipeline/llm_client.py`**
   - Add `reasoning_model` field to `OllamaClient`.
   - Add a `reason()` helper that calls the reasoning model.
   - Use reasoning model for:
     - `extract_facts_from_search()`
     - Citation generation
     - Correction validation (currently uses search snippets directly; can stay as-is or be enhanced)

6. **`backend/config.py`**
   - Add `REASONING_MODEL` setting (default `qwen2.5:7b`).

7. **`.env.example`**
   - Add `REASONING_MODEL=qwen2.5:7b`.

8. **`cli/app.py`**
   - `cmd_memory_show()`:
     - Add `Source`, `URL`, and `Reliability` columns to the slot table.
   - `cmd_chat()`:
     - If `response["citations"]` is non-empty, print a "Sources:" block after the assistant response.

9. **`tests/test_extractor.py`**
   - Update `test_apply_search_extraction_sets_source_type_and_url` to verify per-slot URL and reliability.
   - Add test for corroboration reliability bump.

10. **`tests/test_retrieval.py`**
    - Update `format_memory_context` tests to expect source strings.

11. **`tests/test_orchestrator.py`**
    - Add test for citations in `ChatResponse` when search results are used.

## Security / privacy notes

- Keep all inference local via Ollama.
- Source URLs are stored in the user's local SQLite DB; no external telemetry.
- Bind addresses remain `127.0.0.1`.

## Acceptance criteria

- [ ] Asking "What did I just ask?" mid-session returns the prior user question.
- [ ] Asking "Tell me more about it" after a search-learned fact returns details about that fact.
- [ ] A search-derived response includes an appended "Sources:" section with URLs.
- [ ] `assistant memory show <frame>` displays source URL and reliability for each slot.
- [ ] All existing tests pass; new tests cover context history and citations.
- [ ] `ruff check .` is clean.
