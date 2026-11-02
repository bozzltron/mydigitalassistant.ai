"""The mode switch is the one script that can change who judges every future change.

So nothing here runs against the live roster. Every test builds a **temp copy** of
`.agents/agents/` plus its own table, its own `agents-link.sh` stand-in and its own
`opencode debug agents` stand-in. The real agent files are read once (to copy them) and
never written — `test_the_switch_never_touches_the_real_roster` is the assertion for
that, and the rest of the file would pass while the real roster stayed dirty without it.

The stubs are not decoration. `.agents/agents/` is read by the harness, so a test that
ran the real `scripts/agents-link.sh` would relink and reload the developer's machine and
then verify the *real* roster, which is not the roster under test. The stand-ins make the
live registry an input the test controls, which is what lets AC4's failure be driven:
`no-reload` makes the stand-in link script succeed while leaving the registry stale, and
the switch must then fail loudly instead of reporting success.

AC references are `plan.md` (`.agents/runs/2026-11-02-oll3/plan.md`).
"""

import importlib.util
import json
import re
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "agents-mode.py"
REAL_AGENTS = ROOT / ".agents" / "agents"
SHIPPED_TABLE = ROOT / ".agents" / "agent-models.json"
WORKFLOW_TEST = ROOT / "assistant" / "tests" / "test_agent_workflow.py"

# The table names eight roles. The plan's prose says "Seven roles" and then lists eight;
# the roster on disk has eight, so eight is the set this file drives.
ROLES = (
    "analyzer",
    "design-guardian",
    "doc-gardener",
    "implementor",
    "memory-architect",
    "planner",
    "reviewer",
    "verifier",
)

# Invented-but-realistic ids. They are the shape the table carries, not a claim about
# which model should take which role — that is Ticket 1's measurement, and its column is
# empty today.
CLOUD = {
    "analyzer": "opencode/flint-40b-free",
    "design-guardian": "opencode-go/gemini-4-argon",
    "doc-gardener": "opencode-go/glm-5.3",
    "implementor": "opencode-go/deepseek-v4.1-flash",
    "memory-architect": "opencode-go/kimi-k3.1-pro",
    "planner": "opencode-go/qwen3.8-max",
    "reviewer": "opencode-go/glm-5.3-flash",
    "verifier": "opencode-go/qwen3.8-max",
}

# implementor is `qwen2`, so every `qwen3` role below satisfies the family rule.
LOCAL = {
    "analyzer": "ollama/qwen3.5:4b",
    "design-guardian": "ollama/qwen3.5:9b",
    "doc-gardener": "ollama/qwen3.5:9b",
    "implementor": "ollama/qwen2.5-coder:32b",
    "memory-architect": "ollama/qwen3.8:27b",
    "planner": "ollama/qwen3.8:27b",
    "reviewer": "ollama/qwen3.5:9b",
    "verifier": "ollama/qwen3.8:27b",
}

ALL_NULL = dict.fromkeys(ROLES)

QUOTED_MODEL_LINE = re.compile(r'^model:[ \t]*"[^"\n]*"[ \t]*$', re.M)
ANY_MODEL_LINE = re.compile(r'^model:[ \t]*.*$', re.M)


def _with_model(text: str, model: str) -> str:
    """Set the frontmatter `model:` line on a copy, with a rewriter of our own.

    Deliberately not the script's rewriter: a fixture that shares the code under test
    agrees with its bugs and cannot show a byte it changed that it should not have.
    """
    new, count = QUOTED_MODEL_LINE.subn(f'model: "{model}"', text, count=1)
    assert count == 1, "a real agent file has no quoted `model:` line to drive"
    return new


def _model_on_disk(path: Path) -> str:
    match = ANY_MODEL_LINE.search(path.read_text(encoding="utf-8"))
    assert match, f"{path.name} has no model line"
    return match.group(0).split(":", 1)[1].strip().strip('"')


def _write_table(path: Path, cloud: dict[str, str], local: dict[str, str | None]) -> None:
    path.write_text(
        json.dumps(
            {
                "note": "role -> cloud / local harness model. `local: null` means no "
                        "measured local model yet (Ticket 1).",
                "roles": {
                    role: {"cloud": cloud[role], "local": local.get(role)} for role in ROLES
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


LIVE_STUB = """\
import pathlib
import sys

path = pathlib.Path(sys.argv[1]) / "live.json"
if not path.exists():
    sys.exit(1)
print(path.read_text())
"""

LINK_STUB = """\
import json
import pathlib
import re
import sys

root = pathlib.Path(__file__).resolve().parent
if (root / "link-fails").exists():
    print("stub link: deliberate failure", file=sys.stderr)
    sys.exit(1)
(root / "link-ran").write_text("yes")
if (root / "no-reload").exists():
    print("stub link: links updated, harness not reloaded")
    sys.exit(0)
records = []
for path in sorted((root / "agents").glob("*.md")):
    front = re.match(r"^---\\n(.*?)\\n---\\n", path.read_text(), re.S).group(1)
    model = re.search(r'^model:[ \\t]*"?([^"\\n]+?)"?[ \\t]*$', front, re.M).group(1)
    provider, _, name = model.partition("/")
    records.append({"id": path.stem, "model": {"providerID": provider, "id": name}})
(root / "live.json").write_text(json.dumps(records))
print("stub link: roster reloaded")
"""


class Bench:
    """A temp roster with its own table, link script and live-registry stub."""

    def __init__(self, tmp_path: Path, local: dict[str, str | None]) -> None:
        self.root = tmp_path
        self.agents = tmp_path / "agents"
        self.agents.mkdir()
        for path in sorted(REAL_AGENTS.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            (self.agents / path.name).write_text(
                _with_model(text, local[path.stem] or CLOUD[path.stem]), encoding="utf-8"
            )
        self.table = tmp_path / "agent-models.json"
        _write_table(self.table, CLOUD, local)
        for name, body in (("link.sh", LINK_STUB), ("live.py", LIVE_STUB)):
            stub = tmp_path / name
            stub.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
            stub.chmod(0o755)

    def run(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--table",
                str(self.table),
                "--agents",
                str(self.agents),
                "--link-script",
                str(self.root / "link.sh"),
                "--live-cmd",
                f"{sys.executable} {self.root / 'live.py'} {self.root}",
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def models(self) -> dict[str, str]:
        return {p.stem: _model_on_disk(p) for p in sorted(self.agents.glob("*.md"))}

    def texts(self) -> dict[str, str]:
        return {p.name: p.read_text(encoding="utf-8") for p in sorted(self.agents.glob("*.md"))}

    def live(self) -> list[dict]:
        return json.loads((self.root / "live.json").read_text(encoding="utf-8"))

    def rewrite(self, role: str, model: str) -> None:
        path = self.agents / f"{role}.md"
        path.write_text(_with_model(path.read_text(encoding="utf-8"), model), encoding="utf-8")


class TagsHandler(BaseHTTPRequestHandler):
    """A stub `/api/tags`; the real daemon is never contacted by a test."""

    names: tuple[str, ...] = ()

    def do_GET(self) -> None:
        body = json.dumps({"models": [{"name": n} for n in self.names]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


@contextmanager
def tags_serving(*names: str):
    handler = type("Handler", (TagsHandler,), {"names": names})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def nothing_listening():
    """A loopback port that refuses connections: the daemon is down."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    yield f"http://127.0.0.1:{port}"


@contextmanager
def black_hole():
    """A socket that accepts into the backlog and never answers: the daemon hangs."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        yield f"http://127.0.0.1:{server.getsockname()[1]}"
    finally:
        server.close()


# --- AC1: cloud rewrites the model line and nothing else ---------------------


def test_cloud_mode_rewrites_every_model_line_and_nothing_else(tmp_path):
    bench = Bench(tmp_path, LOCAL)
    before = bench.texts()

    result = bench.run("--mode", "cloud")

    assert result.returncode == 0, result.stderr
    after = bench.texts()
    for path in sorted(REAL_AGENTS.glob("*.md")):
        role = path.stem
        old, new = before[path.name], after[path.name]
        assert new == _with_model(old, CLOUD[role]), (
            f"{role}.md changed something other than the model line"
        )
        assert len(old.splitlines()) == len(new.splitlines()), f"{role}.md changed line count"
        changed = [
            pair
            for pair in zip(old.splitlines(), new.splitlines(), strict=True)
            if pair[0] != pair[1]
        ]
        assert changed == [(f'model: "{LOCAL[role]}"', f'model: "{CLOUD[role]}"')], (
            f"{role}.md: expected exactly one changed line, got {changed}"
        )
    assert bench.models() == CLOUD


def test_cloud_mode_keeps_the_quoting_style_the_harness_reads(tmp_path):
    """Every agent file writes `model: "provider/model"`; a rewrite must keep the quotes.

    The harness and `agents-link.sh` both tolerate either form, so an unquoted rewrite
    would not fail here — it would fail only as an unrelated-looking diff in a review.
    """
    bench = Bench(tmp_path, LOCAL)
    assert bench.run("--mode", "cloud").returncode == 0
    for path in bench.agents.glob("*.md"):
        assert QUOTED_MODEL_LINE.search(path.read_text(encoding="utf-8")), (
            f"{path.name}: the quoted `model:` line was lost"
        )


def test_cloud_mode_is_idempotent(tmp_path):
    bench = Bench(tmp_path, LOCAL)
    assert bench.run("--mode", "cloud").returncode == 0
    once = bench.texts()
    result = bench.run("--mode", "cloud")
    assert result.returncode == 0, result.stderr
    assert bench.texts() == once, "a second cloud switch rewrote bytes"


def test_check_reports_no_drift_after_a_switch(tmp_path):
    bench = Bench(tmp_path, LOCAL)
    assert bench.run("--mode", "cloud").returncode == 0

    result = bench.run("--check")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "no drift" in result.stdout
    assert "cloud" in result.stdout


def test_check_catches_a_hand_edited_frontmatter_line(tmp_path):
    bench = Bench(tmp_path, LOCAL)
    assert bench.run("--mode", "cloud").returncode == 0
    bench.rewrite("reviewer", "opencode-go/glm-5.3")
    before = bench.texts()

    result = bench.run("--check")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "reviewer" in result.stdout
    assert "opencode-go/glm-5.3" in result.stdout
    assert "opencode-go/glm-5.3-flash" in result.stdout
    assert bench.texts() == before, "--check wrote to the roster"


def test_check_catches_a_partial_roster(tmp_path):
    """Some roles switched and some not is the failure that ships a judge nobody chose."""
    bench = Bench(tmp_path, LOCAL)
    assert bench.run("--mode", "cloud").returncode == 0
    bench.rewrite("verifier", LOCAL["verifier"])

    result = bench.run("--check")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "verifier" in result.stdout
    assert "mixed" in result.stdout.lower()


def test_the_shipped_table_and_roster_agree(tmp_path):
    """The committed state is cloud in the table and cloud in the frontmatter."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no drift" in result.stdout


# --- AC2: local mode is fail-closed -----------------------------------------


def test_local_mode_with_a_null_local_model_writes_nothing(tmp_path):
    bench = Bench(tmp_path, LOCAL)
    _write_table(bench.table, CLOUD, ALL_NULL)
    before = bench.texts()

    result = bench.run("--mode", "local")

    assert result.returncode != 0
    assert "refusing" in (result.stdout + result.stderr).lower()
    for role in ROLES:
        assert role in result.stdout + result.stderr, f"{role} was not named as blocking"
    assert bench.texts() == before, "--mode local wrote a partial roster"
    assert not (bench.root / "link-ran").exists(), "a refused switch still reloaded the harness"


def test_local_mode_with_one_null_local_model_names_that_role_only(tmp_path):
    bench = Bench(tmp_path, LOCAL)
    _write_table(bench.table, CLOUD, {**LOCAL, "implementor": None})
    before = bench.texts()

    result = bench.run("--mode", "local")

    assert result.returncode != 0
    assert "implementor" in result.stdout + result.stderr
    assert bench.texts() == before


def test_local_mode_writes_all_eight_when_the_table_is_filled(tmp_path):
    bench = Bench(tmp_path, LOCAL)
    assert bench.run("--mode", "cloud").returncode == 0

    result = bench.run("--mode", "local")

    assert result.returncode == 0, result.stderr
    assert bench.models() == LOCAL
    assert (bench.root / "link-ran").exists(), "the switch did not reload the harness"


# --- AC3: auto picks local only when the daemon serves every mapped model ----


def test_auto_falls_back_to_cloud_when_the_daemon_is_down(tmp_path):
    bench = Bench(tmp_path, LOCAL)
    with nothing_listening() as base_url:
        result = bench.run("--mode", "auto", "--base-url", base_url)

    assert result.returncode == 0, result.stderr
    assert "unreachable" in result.stdout
    assert bench.models() == CLOUD


def test_auto_falls_back_to_cloud_when_a_mapped_model_is_missing(tmp_path):
    bench = Bench(tmp_path, LOCAL)
    served = tuple(model.split("/", 1)[1] for model in LOCAL.values() if model != LOCAL["reviewer"])
    with tags_serving(*served) as base_url:
        result = bench.run("--mode", "auto", "--base-url", base_url)

    assert result.returncode == 0, result.stderr
    assert "missing" in result.stdout
    assert LOCAL["reviewer"] in result.stdout, "the missing model was not named"
    assert bench.models() == CLOUD


def test_auto_goes_local_when_the_daemon_serves_every_mapped_model(tmp_path):
    bench = Bench(tmp_path, LOCAL)
    served = tuple(model.split("/", 1)[1] for model in LOCAL.values())
    with tags_serving(*served) as base_url:
        result = bench.run("--mode", "auto", "--base-url", base_url)

    assert result.returncode == 0, result.stderr
    assert bench.models() == LOCAL
    assert [entry["id"] for entry in bench.live()], "the live roster was never read"


def test_auto_probe_is_bounded_when_the_daemon_hangs(tmp_path):
    """A switch must not hang a run beside a stopped-but-listening daemon."""
    bench = Bench(tmp_path, LOCAL)
    with black_hole() as base_url:
        started = time.monotonic()
        result = bench.run("--mode", "auto", "--base-url", base_url, "--timeout", "0.5")
        elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert elapsed < 10, f"the probe took {elapsed:.1f}s with a 0.5s timeout"
    assert bench.models() == CLOUD


# --- AC4: a switch that does not reach the harness fails loudly --------------


def test_a_harness_that_did_not_reload_fails_the_switch(tmp_path):
    """Measured 2026-10-31: a model change was inert for 90 minutes, silently."""
    bench = Bench(tmp_path, LOCAL)
    (bench.root / "no-reload").write_text("stale", encoding="utf-8")
    (bench.root / "live.json").write_text(
        json.dumps(
            [
                {
                    "id": role,
                    "model": {"providerID": LOCAL[role].split("/")[0],
                              "id": LOCAL[role].split("/", 1)[1]},
                }
                for role in ROLES
            ]
        ),
        encoding="utf-8",
    )

    result = bench.run("--mode", "cloud")

    assert result.returncode == 3, result.stdout + result.stderr
    assert "harness" in (result.stdout + result.stderr).lower()
    assert "reviewer" in result.stdout + result.stderr


def test_a_failing_link_script_fails_the_switch(tmp_path):
    bench = Bench(tmp_path, LOCAL)
    (bench.root / "link-fails").write_text("fail", encoding="utf-8")

    result = bench.run("--mode", "cloud")

    assert result.returncode == 3, result.stdout + result.stderr
    output = (result.stdout + result.stderr).lower()
    assert "link" in output and "failed" in output


def test_the_switch_runs_the_repo_link_script_by_default():
    """AC4 names `scripts/agents-link.sh`; the default must be that script, not a copy."""
    assert "scripts/agents-link.sh" in SCRIPT.read_text(encoding="utf-8")


# --- AC5: the different-family rule survives a switch ------------------------

JUDGES = ("reviewer", "verifier", "design-guardian", "doc-gardener", "memory-architect")


def _harness_family():
    """The harness's own `_family`, so the script and the test cannot disagree."""
    spec = importlib.util.spec_from_file_location("_agent_workflow_rules", WORKFLOW_TEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._family


def test_local_mode_refuses_a_table_that_puts_a_judge_in_the_implementor_family(tmp_path):
    """The all-`qwen3` mapping the sketch proposed: mechanically the same family."""
    family = _harness_family()
    all_qwen3 = dict.fromkeys(ROLES, "ollama/qwen3.5:9b")
    assert family(all_qwen3["reviewer"]) == family(all_qwen3["implementor"])
    bench = Bench(tmp_path, LOCAL)
    _write_table(bench.table, CLOUD, all_qwen3)
    before = bench.texts()

    result = bench.run("--mode", "local")

    assert result.returncode != 0
    assert "reviewer" in result.stdout + result.stderr
    assert "family" in (result.stdout + result.stderr).lower()
    assert bench.texts() == before
    assert not (bench.root / "link-ran").exists()


def test_the_shipped_table_keeps_every_judge_outside_the_implementor_family():
    family = _harness_family()
    roles = json.loads(SHIPPED_TABLE.read_text(encoding="utf-8"))["roles"]
    assert set(roles) == set(ROLES), "the table does not name the roster on disk"
    for column in ("cloud", "local"):
        implementor = roles["implementor"][column]
        if implementor is None:
            continue
        for role in JUDGES:
            model = roles[role][column]
            if model is None:
                continue
            assert family(model) != family(implementor), (
                f"the {column} column puts {role} ({model}) in the implementor's "
                f"{family(implementor)} family"
            )


def test_the_shipped_table_names_every_agent_file():
    roles = json.loads(SHIPPED_TABLE.read_text(encoding="utf-8"))["roles"]
    on_disk = {p.stem for p in REAL_AGENTS.glob("*.md")}
    assert set(roles) == on_disk


# --- --apply: re-render the mode the disk is already in, without a probe -----


def test_apply_rerenders_the_current_mode_after_a_table_edit(tmp_path):
    """Ticket 1's whole workflow: fill the column, re-render, no mode change, no probe."""
    bench = Bench(tmp_path, LOCAL)
    assert bench.run("--mode", "cloud").returncode == 0
    cloud = {**CLOUD, "planner": "opencode-go/qwen3.8-flash"}
    _write_table(bench.table, cloud, LOCAL)

    result = bench.run("--apply")

    assert result.returncode == 0, result.stderr
    assert bench.models() == cloud
    assert bench.models()["reviewer"] == CLOUD["reviewer"], "apply changed the mode"


def test_apply_overwrites_a_hand_edit_with_the_table_value(tmp_path):
    """The table is the source of truth; `--apply` is the repair `--check` points at."""
    bench = Bench(tmp_path, LOCAL)
    assert bench.run("--mode", "cloud").returncode == 0
    bench.rewrite("doc-gardener", "opencode-go/gemini-4-argon")
    assert bench.run("--check").returncode == 1

    result = bench.run("--apply")

    assert result.returncode == 0, result.stderr
    assert bench.models()["doc-gardener"] == CLOUD["doc-gardener"]


def test_apply_refuses_a_mixed_roster_instead_of_guessing(tmp_path):
    bench = Bench(tmp_path, LOCAL)
    assert bench.run("--mode", "cloud").returncode == 0
    bench.rewrite("doc-gardener", LOCAL["doc-gardener"])
    before = bench.texts()

    result = bench.run("--apply")

    assert result.returncode != 0
    assert "mixed" in (result.stdout + result.stderr).lower()
    assert "doc-gardener" in result.stdout + result.stderr
    assert bench.texts() == before


def test_apply_refuses_a_roster_the_table_cannot_describe(tmp_path):
    """After a whole-table rewrite there is no mode to infer; --apply must not invent one."""
    bench = Bench(tmp_path, LOCAL)
    for role in ROLES:
        bench.rewrite(role, f"opencode-go/{role}-other")
    before = bench.texts()

    result = bench.run("--apply")

    assert result.returncode != 0
    assert "cannot be inferred" in result.stdout + result.stderr
    assert bench.texts() == before


def test_an_unusable_table_is_reported_and_nothing_is_written(tmp_path):
    bench = Bench(tmp_path, LOCAL)
    table = json.loads(bench.table.read_text(encoding="utf-8"))
    table["roles"]["planner"]["cloud"] = None
    bench.table.write_text(json.dumps(table), encoding="utf-8")
    before = bench.texts()

    result = bench.run("--mode", "cloud")

    assert result.returncode == 2, result.stdout + result.stderr
    assert "planner" in result.stdout + result.stderr
    assert bench.texts() == before


# --- the temp-copy guarantee -------------------------------------------------


def test_the_switch_never_touches_the_real_roster(tmp_path):
    before = {
        path.name: (path.read_text(encoding="utf-8"), path.stat().st_mtime_ns)
        for path in REAL_AGENTS.glob("*.md")
    }
    bench = Bench(tmp_path, LOCAL)
    assert bench.run("--mode", "cloud").returncode == 0
    after = {
        path.name: (path.read_text(encoding="utf-8"), path.stat().st_mtime_ns)
        for path in REAL_AGENTS.glob("*.md")
    }
    assert after == before, "a test wrote to the real agent files"
