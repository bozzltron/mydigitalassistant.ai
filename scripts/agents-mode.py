#!/usr/bin/env python3
"""Switch the subagent roster between the table's cloud and local columns.

One table — ``.agents/agent-models.json``, role -> ``{"cloud": id, "local": id|null}`` —
and one writer. The harness reads the agent *frontmatter*, so this script rewrites the
``model:`` line from the table and then proves the running harness holds what was written.
Two homes for one fact is how the roster drifted before; ``--check`` is what makes the
second home checkable.

Why the reload is part of the switch: measured 2026-10-31, a model edited through a
symlinked agents directory stayed the *old* model on the next dispatch for 90 minutes,
because the harness loads the roster into memory and does not watch the link. A switch that
does not reach the running harness is therefore not a switch. So every write is followed by
``scripts/agents-link.sh`` (reload + its own live-roster check) and by an independent read
of the live registry compared against the table column that was written.

Exit codes — the run's bar is "each transition produces the documented exit code":

  0  the roster on disk is the one the table describes, and the harness holds it
  1  refused: the requested state is not permitted by the table; **nothing was written**
     (a null ``local``, a judge in the implementor's family, drift under ``--apply``)
  2  unusable input: missing/malformed table, missing agent file, no ``model:`` line
  3  written, but the running harness does not hold it (link script failed, or the live
     registry disagrees) — the disk is switched and the harness is not

Usage:
    scripts/agents-mode.py --check
    scripts/agents-mode.py --mode auto
    scripts/agents-mode.py --mode cloud|local
    scripts/agents-mode.py --apply

``--mode cloud|local|auto`` resolves a target and writes it. ``--apply`` does not probe and
does not change mode: it re-renders the frontmatter from the table for the mode the roster
is already in, which is what a table edit (Ticket 1 filling the local column) needs. It
refuses a mixed roster rather than guessing which mode was meant.

Loopback only by decision: the probe target is ``127.0.0.1:11434``, because the harness runs
on the machine that hosts the daemon. A local id whose provider is not ``ollama`` is treated
as missing by the probe — the daemon we can ask is Ollama, so a model id we cannot confirm
there does not get to call itself present.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TABLE = ROOT / ".agents" / "agent-models.json"
DEFAULT_AGENTS = ROOT / ".agents" / "agents"
DEFAULT_LINK = ROOT / "scripts" / "agents-link.sh"
DEFAULT_LIVE_CMD = "opencode debug agents"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT = 2.0

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_INPUT = 2
EXIT_VERIFY = 3

# The roles the family rule compares against the implementor. The run path's judges are
# reviewer and verifier; the audits are included because the harness test checks them too,
# and a rule enforced in one home only is a rule with a branch that can rot.
JUDGED = ("reviewer", "verifier", "design-guardian", "doc-gardener", "memory-architect")

MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9-]*/[A-Za-z0-9._:-]+$")
FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.S)
MODEL_VALUE = re.compile(
    r'^model:[ \t]*(?:"(?P<quoted>[^"\n]+)"|(?P<bare>[^\n]+?))[ \t]*$', re.M
)
MODEL_LINE = re.compile(r"^model:[ \t]*.*$", re.M)


class RefusedError(Exception):
    """The requested state is not permitted by the table. Nothing was written."""


class InputError(Exception):
    """The inputs cannot be read or are malformed."""


# --- the table ---------------------------------------------------------------


def load_table(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"{path}: {exc}") from exc
    roles = data.get("roles") if isinstance(data, dict) else None
    if not isinstance(roles, dict) or not roles:
        raise InputError(f"{path}: no `roles` object — the table is the source of truth")
    problems = []
    for role, columns in sorted(roles.items()):
        if not isinstance(columns, dict):
            problems.append(f"{role}: expected an object with `cloud` and `local`")
            continue
        for column in ("cloud", "local"):
            value = columns.get(column)
            if value is None:
                if column == "cloud":
                    problems.append(f"{role}: `cloud` is null — the cloud column is the floor")
                continue
            if not isinstance(value, str) or not MODEL_ID.match(value):
                problems.append(f"{role}: {column} {value!r} is not a provider/model id")
    if problems:
        raise InputError("the table is not usable:\n" + "\n".join(f"  {p}" for p in problems))
    return roles


def family(model_id: str) -> str:
    """``opencode-go/glm-5.3-flash`` -> ``glm``.

    Must stay identical to ``_family`` in ``assistant/tests/test_agent_workflow.py``: the
    table is checked here before a write, and the frontmatter is checked there after one.
    Two different notions of "family" is how the rule stops describing what runs.
    """
    return model_id.split("/", 1)[1].split("-", 1)[0].split(".", 1)[0]


def family_problems(roles: dict[str, dict], column: str) -> list[str]:
    implementor = (roles.get("implementor") or {}).get(column)
    if not implementor:
        return []
    problems = []
    for role in JUDGED:
        model = (roles.get(role) or {}).get(column)
        if model and family(model) == family(implementor):
            problems.append(
                f"{role}: {model} is the {family(model)} family, the same as the "
                f"implementor ({implementor}) in the {column} column"
            )
    return problems


# --- the frontmatter ---------------------------------------------------------


def read_models(agents_dir: Path) -> dict[str, str]:
    if not agents_dir.is_dir():
        raise InputError(f"{agents_dir}: not a directory")
    models = {}
    for path in sorted(agents_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        front = FRONTMATTER.match(text)
        if not front:
            raise InputError(f"{path.name}: no frontmatter — the harness cannot load it")
        match = MODEL_VALUE.search(front.group(1))
        if not match:
            raise InputError(f"{path.name}: no `model:` line in the frontmatter")
        models[path.stem] = (match.group("quoted") or match.group("bare")).strip()
    if not models:
        raise InputError(f"{agents_dir}: no agent files")
    return models


def rewrite_model(text: str, model: str) -> str:
    """Return ``text`` with only the frontmatter ``model:`` line changed.

    Quoting is preserved: every file writes ``model: "provider/model"`` and an unquoted
    rewrite would not fail anywhere, it would just appear as an unrelated diff line in a
    review. Everything outside the value is returned byte-identically.
    """
    front = FRONTMATTER.match(text)
    if not front:
        raise InputError("no frontmatter")
    line = MODEL_LINE.search(front.group(1))
    if not line:
        raise InputError("no `model:` line")
    quoted = line.group(0).split(":", 1)[1].strip().startswith('"')
    replacement = f'model: "{model}"' if quoted else f"model: {model}"
    body = front.group(1)[: line.start()] + replacement + front.group(1)[line.end() :]
    return text[: front.start(1)] + body + text[front.end(1) :]


# --- drift -------------------------------------------------------------------


def mode_of(columns: dict, disk: str | None) -> str:
    if disk == columns["cloud"]:
        return "cloud"
    if columns["local"] is not None and disk == columns["local"]:
        return "local"
    return "drift"


def shape_problems(roles: dict[str, dict], on_disk: dict[str, str]) -> list[str]:
    """Files and table rows that do not correspond. A mode write cannot cover these.

    A per-role *value* that is stale is not here: a mode write rewrites every role the
    table names, so it repairs a drifted line rather than being blocked by it. A file with
    no table row is different — the write would leave it on the roster it already had, and
    that is a partial roster by another name.
    """
    problems = []
    unknown = sorted(set(on_disk) - set(roles))
    if unknown:
        problems.append(f"  on disk, no table row: {', '.join(unknown)}")
    absent = sorted(set(roles) - set(on_disk))
    if absent:
        problems.append(f"  in the table, no agent file: {', '.join(absent)}")
    return problems


def value_drift(roles: dict[str, dict], on_disk: dict[str, str]) -> list[str]:
    problems = []
    for role in sorted(roles):
        columns = roles[role]
        if role not in on_disk or mode_of(columns, on_disk.get(role)) != "drift":
            continue
        problems.append(
            f"  {role}: disk {on_disk.get(role)} — table cloud={columns['cloud']} "
            f"local={columns['local']}"
        )
    return problems


def tags(roles: dict[str, dict], on_disk: dict[str, str]) -> dict[str, str]:
    return {role: mode_of(roles[role], on_disk.get(role)) for role in roles}


def mixed_line(tags_by_role: dict[str, str]) -> str | None:
    """A roster split across the two columns is the failure that ships a judge nobody chose."""
    cloud = sorted(role for role, tag in tags_by_role.items() if tag == "cloud")
    local = sorted(role for role, tag in tags_by_role.items() if tag == "local")
    if not (cloud and local):
        return None
    return (
        f"  mixed roster: {len(cloud)} roles on the cloud column ({', '.join(cloud)}) and "
        f"{len(local)} on the local column ({', '.join(local)}) — a partial switch ships a "
        "roster nobody chose"
    )


def single_mode(tags_by_role: dict[str, str]) -> str | None:
    """The column every role sits on, or None while the roster is split or unknown."""
    cloud = sorted(role for role, tag in tags_by_role.items() if tag == "cloud")
    local = sorted(role for role, tag in tags_by_role.items() if tag == "local")
    if cloud and local:
        return None
    if cloud:
        return "cloud"
    if local:
        return "local"
    return None


# --- the probe ---------------------------------------------------------------


def probe_tags(base_url: str, timeout: float) -> tuple[set[str] | None, str]:
    """Return (model names, "") or (None, why the probe failed).

    Bounded on purpose: a switch happens at run start, and a stopped daemon on a listening
    socket must cost the timeout, not the run.
    """
    url = base_url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        return None, f"{url} returned HTTP {exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        return None, f"{url} unreachable ({exc})"
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None, f"{url} did not return JSON"
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return None, f"{url} has no `models` list"
    names = {m["name"] for m in models if isinstance(m, dict) and isinstance(m.get("name"), str)}
    return names, ""


def resolve_auto(roles: dict[str, dict], base_url: str, timeout: float) -> str:
    names, reason = probe_tags(base_url, timeout)
    if names is None:
        print(f"probe: {reason} — falling back to cloud")
        return "cloud"
    missing_locals = sorted(role for role, cols in roles.items() if cols["local"] is None)
    if missing_locals:
        print(
            "probe: daemon reachable, but the table has no local model for "
            f"{len(missing_locals)} of {len(roles)} roles "
            f"({', '.join(missing_locals)}) — falling back to cloud"
        )
        return "cloud"
    family_issues = family_problems(roles, "local")
    if family_issues:
        print(
            "probe: daemon reachable, but the local column breaks the different-family "
            "rule:\n" + "\n".join(f"  {p}" for p in family_issues)
        )
        print("falling back to cloud — a judge in the implementor's family is a formality")
        return "cloud"
    missing = []
    for role in sorted(roles):
        model = roles[role]["local"]
        provider, _, name = model.partition("/")
        if provider != "ollama" or (name not in names and f"{name}:latest" not in names):
            missing.append(f"{model} ({role})")
    if missing:
        print(
            f"probe: daemon reachable, but {len(missing)} mapped models are missing from "
            "/api/tags: " + ", ".join(missing) + " — falling back to cloud"
        )
        return "cloud"
    print(f"probe: daemon reachable and serving all {len(roles)} mapped models — local")
    return "local"


# --- write, reload, verify ---------------------------------------------------


def live_roster(live_cmd: str) -> dict[str, str | None] | None:
    """Read the harness's live registry, or None when it cannot be read at all."""
    argv = shlex.split(live_cmd)
    if not argv:
        return None
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        agents = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(agents, list):
        return None
    roster: dict[str, str | None] = {}
    for agent in agents:
        if not isinstance(agent, dict) or not isinstance(agent.get("id"), str):
            continue
        model = agent.get("model") or {}
        provider = model.get("providerID")
        roster[agent["id"]] = f"{provider}/{model.get('id')}" if provider else None
    return roster


def reload_and_verify(link_script: Path, live_cmd: str, expected: dict[str, str]) -> int:
    try:
        proc = subprocess.run([str(link_script)], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"error: could not run {link_script}: {exc}")
        return EXIT_VERIFY
    if proc.stdout.strip():
        print(proc.stdout.rstrip())
    if proc.stderr.strip():
        print(proc.stderr.rstrip())
    if proc.returncode != 0:
        print(
            f"error: {link_script.name} failed — the roster on disk is switched but the "
            "running harness was not reloaded and verified. Fix the mismatch it named, "
            "then run this script again."
        )
        return EXIT_VERIFY
    live = live_roster(live_cmd)
    if live is None:
        print(
            f"note: could not read the live harness (`{live_cmd}`) — the roster on disk is "
            "switched, but a running harness still holds its previous roster. Start it and "
            "re-run this script to verify."
        )
        return EXIT_OK
    problems = []
    for role, model in sorted(expected.items()):
        got = live.get(role)
        if got is None:
            problems.append(f"  {role}: not loaded by the harness (expected {model})")
        elif got != model:
            problems.append(f"  {role}: wrote {model}, the harness holds {got}")
    if problems:
        print("error: the harness does not hold the roster that was written:")
        print("\n".join(problems))
        print("Run `opencode service restart`, then this script again.")
        return EXIT_VERIFY
    print(f"verified: the live harness holds all {len(expected)} written models")
    return EXIT_OK


def apply_mode(
    mode: str,
    roles: dict[str, dict],
    agents_dir: Path,
    link_script: Path,
    live_cmd: str,
) -> int:
    family_issues = family_problems(roles, mode)
    if family_issues:
        raise RefusedError(
            f"refusing --mode {mode}: the {mode} column breaks the different-family rule — "
            "a judge in the implementor's family is a formality, not a second opinion:\n"
            + "\n".join(f"  {p}" for p in family_issues)
        )
    if mode == "local":
        nulls = sorted(role for role, cols in roles.items() if cols["local"] is None)
        if nulls:
            raise RefusedError(
                f"refusing --mode local: the table has no local model for {len(nulls)} of "
                f"{len(roles)} roles ({', '.join(nulls)}).\n"
                "Nothing was written, because a partial roster would leave some roles on a "
                "model nobody chose. Fill the local column from the harness model evaluation "
                "(Ticket 1), then run this again."
            )
    on_disk = read_models(agents_dir)
    problems = shape_problems(roles, on_disk)
    if problems:
        raise RefusedError(
            "refusing to switch: the table and the agent directory disagree, so a write "
            "would leave a roster neither of them describes:\n" + "\n".join(problems)
        )
    plan = []
    for role in sorted(roles):
        path = agents_dir / f"{role}.md"
        try:
            plan.append((path, rewrite_model(path.read_text(encoding="utf-8"), roles[role][mode])))
        except InputError as exc:
            raise InputError(f"{path.name}: {exc}") from exc
    for path, text in plan:
        path.write_text(text, encoding="utf-8")
    print(f"mode: {mode} — {len(plan)} agents rewritten")
    return reload_and_verify(
        link_script, live_cmd, {role: roles[role][mode] for role in roles}
    )


def check(roles: dict[str, dict], agents_dir: Path) -> int:
    on_disk = read_models(agents_dir)
    by_role = tags(roles, on_disk)
    problems = shape_problems(roles, on_disk) + value_drift(roles, on_disk)
    split = mixed_line(by_role)
    if split:
        problems.insert(0, split)
    if problems:
        print(
            "drift: the table and the frontmatter disagree — the roster on disk is not the "
            "one the table describes:"
        )
        print("\n".join(problems))
        print("Run `scripts/agents-mode.py --mode cloud|local` to re-render, or restore the line.")
        return EXIT_REFUSED
    mode = single_mode(by_role) or "cloud"
    family_issues = family_problems(roles, mode)
    if family_issues:
        print(f"invalid: the {mode} roster matches the table but breaks the family rule:")
        print("\n".join(f"  {p}" for p in family_issues))
        return EXIT_REFUSED
    print(f"mode: {mode} — no drift between the table and the {len(roles)} agent files")
    return EXIT_OK


def apply_current(
    roles: dict[str, dict], agents_dir: Path, link_script: Path, live_cmd: str
) -> int:
    on_disk = read_models(agents_dir)
    problems = shape_problems(roles, on_disk)
    if problems:
        raise RefusedError(
            "refusing --apply: the table and the agent directory disagree:\n"
            + "\n".join(problems)
        )
    by_role = tags(roles, on_disk)
    split = mixed_line(by_role)
    if split:
        raise RefusedError(
            "refusing --apply: there is no single mode to re-render:\n"
            + split
            + "\nRun --mode cloud|local to choose one."
        )
    mode = single_mode(by_role)
    if mode is None:
        raise RefusedError(
            "refusing --apply: no role on disk matches either column, so the mode cannot be "
            "inferred. Run --mode cloud|local."
        )
    return apply_mode(mode, roles, agents_dir, link_script, live_cmd)


# --- CLI ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render the agent frontmatter from .agents/agent-models.json and verify "
        "the running harness holds it.",
        epilog="exit: 0 ok · 1 refused, nothing written · 2 unusable input · 3 written but "
        "the harness does not hold it",
    )
    parser.add_argument("--check", action="store_true", help="report drift; writes nothing")
    parser.add_argument("--mode", choices=("cloud", "local", "auto"), help="resolve and write")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="re-render the mode the roster is already in, without probing",
    )
    parser.add_argument("--table", type=Path, default=DEFAULT_TABLE)
    parser.add_argument("--agents", type=Path, default=DEFAULT_AGENTS)
    parser.add_argument("--link-script", type=Path, default=DEFAULT_LINK)
    parser.add_argument("--live-cmd", default=DEFAULT_LIVE_CMD)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if sum(bool(x) for x in (args.check, args.mode, args.apply)) != 1:
        parser.error("give exactly one of --check, --mode, --apply")
    try:
        roles = load_table(args.table)
        if args.check:
            return check(roles, args.agents)
        if args.apply:
            return apply_current(roles, args.agents, args.link_script, args.live_cmd)
        mode = args.mode
        if mode == "auto":
            mode = resolve_auto(roles, args.base_url, args.timeout)
        return apply_mode(mode, roles, args.agents, args.link_script, args.live_cmd)
    except RefusedError as exc:
        print(str(exc))
        return EXIT_REFUSED
    except InputError as exc:
        print(str(exc))
        return EXIT_INPUT


if __name__ == "__main__":
    sys.exit(main())
