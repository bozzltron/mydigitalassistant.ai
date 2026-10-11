# Verification — nudge_bound

## What was run

`experiment.py` on 2026-10-11. Arm A (scripted model, scratch store) and Arm B
(10 live turns, the real pipeline on the dev backend).

```
docker run --rm --network mydigitalassistantai-dev_appnet -v $(pwd):/app -w /app \
  -e OLLAMA_URL=http://host.docker.internal:11434 -e EXP_BASE=http://assistant:8000 \
  assistant python assistant/experiments/nudge_bound/experiment.py
```

## Non-mutation

- **Arm A** writes only to a temp store (`tempfile.mkdtemp`) and restores
  `decision.outstanding`. No brain, no network.
- **Arm B** runs against the **dev** backend and mutates the **dev** brain (files,
  episodes). The dev brain is synthetic; the **live brain is never touched**. Every
  `nudge_bound_*` file was deleted again at the end (frame + bytes together) — the
  cleanup list confirms 9 files removed, and the 10th (`template_1`) was never
  created.

## Raw output

**Arm A (deterministic — the loop's policy):**

```
[A S_announce_read_final ] acting=1 wrote=False outstanding_consulted=0 rounds=3 tools=['read_file']
[A C_read_final          ] acting=1 wrote=False outstanding_consulted=1 rounds=3 tools=['read_file']
```

Shape S (announce → read → final) consults the completion judgement **0** times; the
control (read → final) consults it once. **H1 holds.**

**Arm B (live):**

```
[B checklist r1] action_nudge=False wrote=True  tools=['write_file']             40.4s
[B cities    r1] action_nudge=False wrote=True  tools=['write_file']             43.5s
[B bio       r1] action_nudge=False wrote=True  tools=['write_file']             49.7s
[B template  r1] action_nudge=False wrote=False tools=[]                         40.2s
[B plan      r1] action_nudge=False wrote=True  tools=['read_file','write_file','read_file'] 53.4s
[B checklist r2] action_nudge=False wrote=True  tools=['write_file']             38.2s
[B cities    r2] action_nudge=False wrote=True  tools=['write_file']             43.3s
[B bio       r2] action_nudge=False wrote=True  tools=['write_file']             38.5s
[B template  r2] action_nudge=False wrote=True  tools=['write_file']             46.0s
[B plan      r2] action_nudge=False wrote=True  tools=['write_file']             43.2s

live turns 10/10 · action nudge fired 0 · suppressed 0 · warranted 0
```

The backend's own logs confirm the detector: **10 `decision needs_action=True`
lines** (p 0.63–0.71 — the nudge was *armed* on every turn) and **no `Action nudge`
line** — the nudge never *fired*, because the model called `write_file` in round 1.

## The one anomaly

`template r1` did not write: no tool call, no nudge, no file. Re-running the same
phrasing wrote the file immediately (`write_file`, no nudge), so it did not
reproduce. The shape is consistent with a **failed decision call**: `_ask` returns
`None` on any error, `needs_action` returns `None` **without logging**, so
`expects_action` is `False`, the nudge is not armed, and the announce-then-not-act
failure is unprotected. That is the best-effort contract working as designed — but it
means a transient decision failure silently costs the fix. Named, not resolved.

## Threats and limits (what remains after the run)

- **Ten turns is a probe**, not a rate. 0/10 bounds the suppressed-case frequency
  only coarsely (a true rate below ~26% could still produce 0/10).
- **The chat model is non-deterministic**, and these were explicit "create a file
  called X" phrasings. The original live failure used a vaguer turn ("No edits to the
  Why Not Release. We want to create X") — the shape that motivated the nudge — so
  this set may under-sample the announce-then-not-act behaviour.
- **Arm A proves the loop's policy, not the world.** H1 is a statement about the code.
- Arm B **mutated the dev brain**; the live brain was untouched.
- The completion judgement was replayed **offline** on captured tool calls, because
  the loop could not run it.
