# Verification

## Run 1 — INVALID, not a result

**Verdict: the harness was broken. Nothing about the agent is measured by this run.**

```
trial  1..10: no_attempt   coverage=0.00  fields=False  calls=(none)
outcome classes:  no_attempt 10/10
H1 FAIL · H3 PASS · H5 FAIL
```

Every trial died before the model saw the prompt:

```
httpx.ConnectError: All connection attempts failed
  llm_client.py:740 embed -> retry_transient -> _post_json -> client.post
```

`no_attempt` means "did not write, and never tried". `calls=(none)` means no tool ran at
all — including the `read_file` every trial opens with. Ten identical failures with no
tool calls is not a behaviour, it is a dead process, and the traceback says so.

**The cause, and it is mine.** `OllamaClient.__init__` takes
`base_url: str = "http://127.0.0.1:11434"` — a *default parameter* — so a bare
`OllamaClient()` ignores `settings.ollama_url` and dials localhost. Inside a container
localhost is nothing, so the very first embedding of every turn failed.

Measured, so the diagnosis is not a guess:

```
settings.ollama_url  = http://host.docker.internal:11434
os.environ OLLAMA_URL = http://host.docker.internal:11434
client.base_url       = http://127.0.0.1:11434      ← the default, not the setting
```

The app (`main.py:198`) and every other experiment (`frame_budget/experiment.py:231`) pass
`base_url=settings.ollama_url`. This runner did not. Fixed, and `result.json` from this run
was deleted rather than kept: a file of ten `no_attempt`s is a harness artifact, and leaving
it in the directory invites it being read as a finding.

## What this run *did* establish

1. The scratch-sandbox guard works — the run aborted on `EXP_SANDBOX=/app/data` and
   proceeded only for a path outside it.
2. The runner reaches the real Ollama and the real SearXNG from a container on
   `mydigitalassistantai-dev_appnet`, once the client is pointed at the right host.
3. **A latent trap worth recording:** the `base_url` default is a hardcoded `127.0.0.1`
   while `settings.ollama_url` exists. Any new caller that forgets the argument talks to
   localhost and fails in a way that looks like the agent doing nothing. Not changed here —
   `llm_client` is on the hot path and that is its own change — but it is the second time
   this session a hardcoded default silently produced a wrong result rather than an error.

## Runs 2 and 3 — INVALID, retrospectively. The search tool was never wired.

**Verdict: nothing about the agent's completion behaviour is measured by runs 2 and 3. Every
`web_search` the model made returned an error.**

**Run 1 is a separate question, and it survives.** Its failures never searched — their call
lists are `read_file`, `search_file`, `read_file,search_file` and `read_file x4` — so the
unwired search does not explain them. Run 1's finding, that the `outstanding` judgement
accepted a read as having performed the write, is therefore a measurement and not an artifact.

Run 4 was preceded by an instrument change (`plan.md`, Amendment 2) that added the final
answer text, the tool arguments, and a real nudge count. The **first** trial under it said
this:

```
answer  : "I was able to read the vessels.csv file ... However, I encountered an error when
           trying to search for vessel positions - the search tool wasn't initialized."
call_log: read_file(path=vessels.csv)
          search_file(path=ship-tracker.geojson)
          read_file(path=ship-tracker.geojson)
          web_search(query=...)
          web_search(query=...)
```

That is a literal error string from our own code — `tool_executor.py:655`,
`ToolResult(success=False, error="Search tool not initialized")` — not the model's paraphrase
of a judgement.

**The cause, and it is mine.** `init_store()` takes `search_tool=None` and `orchestrator=None`
as *default parameters*, and `main.py:248-254` passes both. This runner passed neither:

```python
tool_executor.init_store(
    store.db_path, embed_fn=orch.embed_fn(), embedding_model=settings.embedding_model
)
```

`_search_tool` therefore stayed `None` for the whole process. Two silent consequences:

| unwired | effect |
|---|---|
| `search_tool` | every `web_search` returned `Search tool not initialized` |
| `orchestrator` | `fetch_url` ran with `llm_client=None`, so its fact extraction was skipped |

### Why that reads as a behaviour, and cost three runs

`no_attempt` is defined as "did not write, and never tried". Under the fault the model *read
the CSV, read the map, searched, was told the search had failed, and reported that it could
not obtain positions and asked the user for them.* That is not a completion failure — it is an
honest report about a broken tool, and it scores identically to a model that forgot to act.

The same fault explains `refused_stale`, which was read as evidence of good judgement: with no
positions retrievable, an empty FeatureCollection is the only truthful write.

So the conclusions drawn from runs 2 and 3 are **void**:

- "The completion check is not the lever" — untested; the trials were failing before the check
  was reached.
- "The model answers the nudge with more looking" — it was re-searching because searching kept
  failing.
- "The nudge is not the lever" — untested.

The `kind` change is not discredited by this. It fixes a real defect — a lookup genuinely
cannot satisfy a change. It is simply **unmeasured**, because its effect was measured on a rig
whose searches all errored.

### Fixed, and made un-hideable

Both wired to match production, plus a preflight `assert` that the search tool is set. The
instrument now records every call's **success or error**, and the summary prints a tool-error
tally — a line runs 1-3 could not produce. Verified on two trials *before* the run:

```
trial  1: wrote_complete  coverage=1.00 fields=True  nudge=empty,empty,completion
          calls=run_scheduled_task,read_file,search_file,...,web_search,read_file,write_file
trial  2: wrote_complete  coverage=1.00 fields=True  calls=read_file,write_file,write_file,...
tool errors: 1  (write_file: File exists: ship-tracker.geojson (use overwrite=True))
```

`2/2 wrote_complete`, `no_attempt` zero — and one genuine error surfaced that no earlier run
could have shown: `write_file` refuses to replace an existing file without `overwrite=True`,
while the prompt says "update the file".

Trial 1 also called `run_scheduled_task` from inside a scheduled task, which only became
possible once the orchestrator was wired. It did not recurse here, but the exposure is real in
production and is recorded rather than fixed in passing.

### The pattern this is the third instance of

Three harness faults, one shape, all presenting as an agent behaviour:

| # | fault | how it presented |
|---|---|---|
| 1 | `OllamaClient(base_url=...)` hardcoded default | `no_attempt` 10/10 — "the agent did nothing" |
| 2 | `nudges` initialised and never appended | `nudges: 0` — read as "no nudge fired" |
| 3 | `init_store(search_tool=None)` default | `no_attempt` — "the agent chose not to act" |

Each was a defaulted parameter or an unpopulated field; each produced a plausible result; and
each was read as being about the agent. Two of the three were found only *after* a run had been
interpreted and written up.

The lesson is not "be careful". It is that **a harness must assert its own wiring and an
instrument must record tool failures**, because a defaulted parameter fails silently by
construction — and a silent failure is indistinguishable from a decision.

## Run 5 — preflight clean, and one label is ambiguous

The wiring assert passed, every trial recorded an `exit_reason` (no `(unlogged)`), and the tool
errors were surfaced — so the new fields are live rather than assumed.

But `refused_stale` means two different things, and this run shows it. Trial 2's answer says
"I encountered a **tool error** when trying to update the ship-tracker.geojson file", and the
tool-error tally names it: `write_file: File exists: ship-tracker.geojson (use overwrite=True)`.
The class is `refused_stale` only because `classify` sees a write tool in the call list while
the file is unchanged. **A failed write and a deliberate decline are the same observation** —
the same shape as the fault this whole run exists to correct, at a smaller scale, and it means
both `refused_stale` appearances (runs 2 and 5) must be read with the tool-error tally beside
them rather than on their own.
