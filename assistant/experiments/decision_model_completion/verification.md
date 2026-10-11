# Verification — decision_model_completion

## What was run

`experiment.py` on 2026-10-10 against local Ollama 0.35 (`/v1/systemone`). 16
labelled (request, tool-calls) pairs × 3 decision models. No brain, no tools.

```
docker run --rm -v $(pwd):/app -w /app -e OLLAMA_URL=http://host.docker.internal:11434 \
  assistant python assistant/experiments/decision_model_completion/experiment.py
```

## Non-mutation

Pure model I/O. No brain is opened, so `preflight.py` does not apply.

## Raw output

```
nimble     accuracy  93.8% (15/16)  P(out) 0.790 vs P(sat) 0.163  margin 0.626   604ms
tev1       accuracy  81.2% (13/16)  P(out) 0.643 vs P(sat) 0.426  margin 0.217   286ms
tev1:0.8b  accuracy  62.5% (10/16)  P(out) 0.438 vs P(sat) 0.420  margin 0.017    70ms
```

## The shape of the result

`nimble` **separates the classes** — 0.79 on outstanding against 0.16 on satisfied,
a margin of 0.63, and 15/16 correct. The `tev1` models do not: `tev1:0.8b`'s two
means are **0.438 vs 0.420** — indistinguishable, so no threshold exists and it
scored near chance on the outstanding class (it called 5 of 8 `outstanding` cases
`satisfied`).

That is the whole finding: the model that is **fastest and best at
`needs_action`** (`tev1:0.8b`, 95% / 70 ms) is **useless at this judgement**, and
the model that is good at this judgement (`nimble`, 93.8%) is the *slowest and
weakest* at `needs_action` (90% / 604 ms).

## Threats and limits

- **16 cases is a probe**, not a rate.
- **The `tools_called` string is a summary** the loop must produce; its format is
  part of the design and could itself move the judgement.
- **One threshold, one run.** The API's confidences are low, so a near-tie can flip;
  `nimble`'s margin (0.63) is well outside that, the others are inside it.
- This judges whether the **calls** satisfy the request; it does not verify the
  file's contents, only that the right kind of call happened.
