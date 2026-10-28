# Glossary

The vocabulary this repo uses, so a human and an agent mean the same thing by the same word.
Two families of terms: the **dev harness** (`.agents/`) and the **product** (`assistant/`).

The third column records names the same thing goes by *elsewhere* — including the org standard
("Engineering Standards: Humans and Agents"), which uses **Implementor** and **Verifier** where
this repo says **Implementer** and **spec-verifier**. They are listed there so a reader can map
between the two without anything being renamed. When a term here disagrees with the org copy,
this file wins: it describes the code.

Anything marked *proposed* is not in use yet; it says so rather than describing a practice the
repo does not have.

## Harness

| Term | Definition | Also called (avoid) | In code |
|---|---|---|---|
| **Run** | One pass of a runbook for one change, identified by a run ID. Working state, not history. | ticket, task | `.agents/runs/<run-id>/` |
| **Run ID** | `YYYY-MM-DD-xxxx` — date plus a short suffix. Names the run directory and appears in the commit trailer. | run number | `.agents/runs/`, commit trailers |
| **Runbook** | An ordered procedure that chains agents through handoff files, with human decisions in it. Two exist: `ship-feature` (spec first) and `self-improve` (logs first). | workflow, pipeline | `.agents/skills/*/SKILL.md` |
| **Handoff file** | A file an agent writes so the next agent — or the human — can act without re-reading the conversation. State lives here, which is why a run can stop and resume. | artefact, output | `plan.md`, `implementation.md`, `test-report.md`, `verification.md` |
| **Ledger** | The append-only table of findings for a run. The only place a finding may live; a finding in prose cannot be proved to have been acted on. | backlog, issues | `.agents/runs/<run-id>/ledger.md`, rules in `docs/AGENTIC_WORKFLOW.md` |
| **Row** | One finding in the ledger: `id · src · sev · claim · where · falsifier · disposition · closed`. | item, ticket | `ledger.md` |
| **Falsifier** | The check that would prove a row wrong ("wrong if ⟨test⟩ passes"). A row without one is a **preference**, may not be S1/S2, and can be rejected without a loop. | acceptance, criteria | `ledger.md` column |
| **Disposition** | What the writer did with a row: `fix <commit or test>` · `reject <result>` · `defer → B-<n>` · `escalate`. Only the writer sets it. | resolution, status | `ledger.md` column |
| **Severity** | How bad a **finding** is: S1 correctness/data-loss/privacy · S2 stability/invariant · S3 speed · S4 docs/cleanup. Set by the agent that opens the row. | priority, risk | `docs/AGENTIC_WORKFLOW.md` §Severity |
| **Risk tier** | *Proposed.* How much human reading a **change** needs, as distinct from how bad a finding is. Not yet in a runbook. | — | proposed; `plans/harness-alignment.md` |
| **Decision** | A point where the run stops for a human: plan approval, verification-and-evidence, merge. The org calls these DECISION 1/2/3; this repo names them in the runbook steps rather than numbering them. | gate, checkpoint | `SKILL.md` steps marked **STOP** |
| **Gate** | A command set that must pass before a commit. One script owns the commands so a runbook and an agent cannot carry stale copies. | checks, CI | `scripts/agent-gates.sh` |
| **Verdict** | The outcome of verification: `PASS` / `PARTIAL` / `FAIL`. A missing measurement is `PARTIAL`, never `PASS`. | result, score | `verification.md`, `log.jsonl` |
| **Register** | Findings deferred out of a run, carrying the run that raised them. Read before the logs at the start of a `self-improve` run. *(Also the harness's word for this; the product's memory store is not a register.)* | backlog | `.agents/backlog.md` |
| **Evals** | Golden tasks for regression-testing a runbook itself. *Not built yet* — the directory records why. | benchmarks | `.agents/evals/` |
| **Planner** | Turns a spec into `plan.md` with numbered criteria, inputs, a pre-registered bar and a falsifier. Never writes code. | architect | `.agents/agents/planner.md` |
| **Implementer** | The single writer: turns an approved plan into code and disposes of ledger rows. Never closes a row, never touches the plan. | Implementor (org standard), builder, coder | `.agents/agents/implementer.md` |
| **Tester** | Writes and runs the tests that prove the criteria, and opens rows for defects rather than test gaps. | QA | `.agents/agents/tester.md` |
| **Reviewer** | Reads the **diff** with clean context and opens rows with a severity and a falsifier. Does not read the writer's account of the diff. | code reviewer | `.agents/agents/reviewer.md` |
| **spec-verifier** | Independently re-derives the requirements from the spec, checks the pre-registered bar against fresh evidence, audits the ledger and appends the one line to the log. The measurement; nobody edits it. | Verifier (org standard) | `.agents/agents/spec-verifier.md` |
| **Observer** | Reads the live docker logs and turns them into evidence, ranked by frequency × user-visible cost. The entry point of `self-improve`. | monitor | `.agents/agents/observer.md` |
| **Design guardian** | Checks a product pass against the design principles and reports principles nothing enforces. | design reviewer | `.agents/agents/design-guardian.md` |
| **Doc gardener** | Audits docs for claims the code has falsified. Proposes; a human deletes or rewrites. | tech writer | `.agents/agents/doc-gardener.md` |
| **Memory architect** | Reviews the code that maintains memory — invariants, read/write paths, cost. **Never reads the brain.** Ad hoc. | — | `.agents/agents/memory-architect.md` |

## Product

| Term | Definition | Also called (avoid) | In code |
|---|---|---|---|
| **Assistant** | The shipped system: a local, privacy-first cognitive assistant. All inference is local Ollama. | app, bot, chatbot | `assistant/backend/`, `frontend/` |
| **Brain** | The SQLite database holding everything the assistant knows. Never wiped without a backup; never read by a reviewing agent. | memory DB, store | `assistant/backend/memory/store.py` |
| **Frame** | One learned subject — a person, place, document, project — that slots hang off. | entity, node, record | `memory/store.py` |
| **Slot** | One `key → value` fact on a frame, with provenance, confidence and an observation time. | attribute, field, property | `memory/store.py` |
| **Association** | A weighted link between two frames, learned from co-occurrence. | edge, relation | `memory/store.py` |
| **Episode** | One turn or exchange as it happened, used for recall and summarisation. | transcript entry | `pipeline/transcript.py` |
| **Conflict** | Two incompatible values for the same slot, recorded rather than overwritten. | contradiction, clash | `memory/consolidate.py` |
| **Confidence ladder** | The single place that decides which of two competing claims wins, using source reliability and evidence. | scoring, ranking | `memory/confidence.py` |
| **Consolidation** | The maintenance pass that merges duplicate frames and resolves conflicts between scheduled ticks. | dedupe, merge, GC | `memory/consolidate.py` |
| **Correction** | The user telling the assistant it is wrong, parsed, validated and applied to memory. | feedback, fix | `pipeline/tools.py` |
| **Retrieval** | How memory reaches the prompt: embed → vector search → graph walk → context budget. | RAG, recall | `memory/retrieval.py` |
| **run_now** | A tool that fires a named scheduled task immediately and records when it last ran. | trigger, execute | `pipeline/tools.py` |
| **Queue** | Messages waiting to be sent, drained one at a time so a turn cannot be interleaved. | buffer, outbox | `frontend/src/state/messageQueue.ts`, `services/queueDrainer.ts` |
| **Trace panel** | The UI surface showing what a turn actually used — retrieval, tools, timings. | debug panel | `frontend/src/components/chat/TracePanel.tsx` |
| **SearXNG** | The local, self-hosted search backend. Default, loopback-only. | search engine, meta-search | `pipeline/search.py` |
| **Brave** | The optional, **non-local** search backend, usable only with explicit opt-in. The one time data leaves the machine. | Brave API, search API | `pipeline/search.py` |
| **fetch_url** | A direct HTTP fetch with extraction; robots-checked, SSRF-checked, extracts facts into memory. | scrape, crawl | `pipeline/tools.py` |
