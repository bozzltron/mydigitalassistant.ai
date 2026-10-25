---
name: ship-feature
description: Plan, implement, test, review and verify a feature from a spec. Use when asked to ship or build a feature end to end.
---

# ship-feature@v1

Chains the five core agents through handoff files, so any step can run alone and the
run can stop and resume at the last file written. A human approves the plan and
merges the result; no agent grades its own work.

1. **Create or reuse a run ID** — `YYYY-MM-DD-xxxx` — and work in
   `.agents/runs/<run-id>/`. Skip any step whose output file already exists.
2. **planner** → `plan.md`. **STOP** and ask the human to approve or edit it. Do not
   implement an unapproved plan.
3. **implementer** → code + `implementation.md`.
4. **tester** → tests + `test-report.md`.
5. **reviewer** → `review.md`. A blocking verdict goes back to the implementer
   **once**, then re-run steps 4–5.
6. **spec-verifier** → `verification.md` + one line in `.agents/runs/log.jsonl`.
7. **PARTIAL or FAIL** → send the listed gaps to the implementer, then re-run steps
   4–6. **Maximum two retries**, then stop and hand `verification.md` to the human.
8. **PASS** → run the full gates (below), then commit with the trailers:

   ```
   Runbook: ship-feature@v1
   Run-ID: <run-id>
   ```

## Gates (from RUNBOOK.md — non-negotiable)

```bash
docker run --rm -v $(pwd):/app -w /app assistant ruff check .
docker run --rm -v $(pwd):/app -v assistant-test-data:/app/data -w /app \
  -e DATABASE_PATH=/app/data/assistant-test.db \
  assistant pytest assistant/tests/ --timeout=20
cd frontend && npm run check
```

Rebuild **both** environments before claiming a live change works — dev on 8443
(`docker compose up -d --build`), prod on 8444
(`docker compose -f docker-compose.prod.yml up -d --build`). Neither reloads Python,
and a source change does not reach a running container until it is rebuilt.

## Rules

- One run, one change. If the spec grows a second feature, it is the next run.
- A change that writes to memory must follow `RUNBOOK.md` §11 first (portable export,
  restored and compared).
- If two consecutive rounds miss their pre-registered bar, stop — `RUNBOOK.md` §10.
- The verifier, the evals and the log format are human-owned. Agents propose; a human
  edits.
