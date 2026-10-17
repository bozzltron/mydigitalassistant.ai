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
