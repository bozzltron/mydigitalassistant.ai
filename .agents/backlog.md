# Backlog — findings that outlived the run that raised them

Findings deferred out of a run land here with the run id that raised them, so the next
`self-improve` run reads this **before** it reads the logs. Without a register, a finding that
needs its own run dies: si01's "log the measured chars/token ratio" gap was never picked up by
si02, while si02's CLI-traceback finding was fixed only because it happened to land inside the
same diff.

Rules: one row per finding. `state` is `open` / `scheduled` (with a run id) / `done` (with a
commit) / `dropped` (with a reason). Only a human closes a row. Severity uses the harness
ladder: S1 correctness/data-loss/privacy · S2 stability/regression · S3 speed · S4 docs/cleanup.

| id | sev | raised by | finding | state |
|---|---|---|---|---|
| B-01 | S1 | reviewer `2026-10-27-dsp01` (r-01) | `docs/audits/memory-health-2026-10-26.md` committed brain-derived personal content — real third-party names extracted from live slot values, a personal document filename, origin-city data — to a public GitHub remote. | **contained 2026-10-27.** Values redacted (`0216b24`) and the repo made **private** (`gh repo edit --visibility private`; unauthenticated API and HTML both 404). The whole class, including history, is contained. **Residual: the values remain in pushed history.** If the repo is ever made public again, the history must be rewritten first — redaction alone does not cover the 19 commits already on `origin/main`. |
| B-01b | S2 | B-01 follow-up | The redaction landed locally but the push that would carry it has not happened, so `origin/main` still holds the unredacted blobs — inside a private repo now. | open — push when the tree passes the gate; the next release tag will carry it. |
| B-05 | S1 | `2026-10-27-dsp01` follow-up | The class, not the file: no agent rule in the repo governed **egress** of brain-derived values, only access. `memory-architect` said "never read the brain" and was bypassed; `observer` reads logs that carry user content; `doc-gardener` promotes content into `docs/`. Two of the three rules now exist (`memory-architect`, `observer`, both runbooks) plus `scripts/pii-guard.sh` in the `--full` gate. | open — the hard rule belongs in `AGENTS.md` §Security constraints, which is human-owned. Text proposed 2026-10-27, awaiting the owner. |
| B-06 | S3 | `2026-10-27-dsp01` | `docs/audits/memory-health-2026-10-26.md` consumed a 224-line doc to land one finding. Whether the audit form is worth its maintenance is now a `doc-gardener` question, not this run's. | open |
| B-02 | S2 | reviewer `2026-10-27-dsp01` (r-02) | `pyproject.toml:84` sets `timeout = 120` while `AGENTS.md`, `RUNBOOK.md` and `scripts/agent-gates.sh` all state 20 as the gate ceiling, and `docker-compose.test.yml:60,78` run bare `pytest assistant/tests/` — so `./run_ci.sh` still inherits the 120 s ceiling the docs say was eliminated, and the intermittent hang can stall the pre-merge gate. | open — smallest fix: `timeout = 20` in pyproject, or put `--timeout=20` on the compose services and name the layer that owns the value. |
| B-03 | S2 | reviewer `2026-10-27-dsp01` (r-04) | `consolidate.py:519-523` closes the store on the dry-run path so the CLI stops printing `Event loop is closed`, but no test pins the behaviour — the fix can regress silently. | open |
| B-04 | S4 | reviewer `2026-10-27-dsp01` (r-03) | Four stale `--timeout=120` doc copies remain, one of them 40 lines from the README edit that changed the same fact. | open |
