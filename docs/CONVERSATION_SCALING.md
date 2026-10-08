# Conversation scaling — how a long thread keeps its thread

How the assistant carries a long-running, **topical** conversation: what is
verbatim, what is recalled by meaning, what is summarized, and how the user can
see it. The guiding rule: the *facts* survive as memory (frames/slots), and the
*conversation* is carried by three layers that must not leave a gap between them.

## The three layers

| Layer | Scope | What it carries | Home |
|---|---|---|---|
| **Verbatim history** | last `verbatim_history_turns` (6) of the current session | the tail, word-for-word | `orchestrator._run_turn` |
| **Episode recall** | every session, by meaning | "Related past conversations" | `retriever._search_past_conversations` |
| **Summarization** | one `conversation_summary_{session}` frame | a 3–5 sentence narrative | `scheduler/summarizer.py` |

Plus the durable layer: extraction turns conversation into **frames/slots**, so a
fact (a decision, a preference) is retrievable forever regardless of which turns
fall out of the window.

## The rule that makes them compose

**The verbatim window and episode recall must partition the conversation, not
overlap or leave a hole.** The turns carried verbatim are excluded from recall
(re-injecting them is redundant); *every other turn* — including the current
session's own, older than the window — is recallable.

- `verbatim_history_turns` (config) is the window. `_run_turn` reads it for the
  prompt; `_search_past_conversations` excludes exactly those episode ids
  (`exclude_episode_ids`), so a thread can reach its own middle.
- It previously excluded the **whole** current session, on the assumption its
  turns were already verbatim — true only for the last few, so turn 7+ of a
  thread was unreachable.

## Summaries track the present, incrementally

`summarize_session` summarizes the **recent** turns (the tail that fits
`summarization_max_chars`) and folds in the **prior summary**, so the result
reflects where the conversation *is* rather than where it started.

- It previously did `episodes_text[:max_chars]` on an oldest-first list — i.e. it
  summarized the *opening* of a long session, and regenerated the same way, so it
  never advanced.

## Transparency — the user can see it

The summary is a memory artifact, made **viewable**, not **pushed** (no new event
type, no alert, no prompt cost):

- `GET /chat/session/{id}/summary` — read-only; never re-summarizes. Owner-scoped
  (`user_id` is required and the summary frame is checked against it), the same
  rule `/chat/session/{id}/messages` follows.
- The current session's summary rides the turn `meta` (`conversation_summary`),
  and the chat's "What I learned" panel shows it. After a long thread is
  summarized, the user sees it on their next turn in that conversation.

## Still open (design)

- **On-demand injection.** When the live session passes the verbatim window,
  inject its summary as a "where this conversation is" section, rather than
  waiting for retrieval to surface it.
- **Topic-scoped state.** Summaries are session-scoped; a topic spanning sessions
  gets N summaries, none of them "the topic's state". The frames/associations
  graph already gives a topic identity to key on.

## Related

- The history budget and the context window: `docs/CONTEXT_THROUGHPUT.md`.
