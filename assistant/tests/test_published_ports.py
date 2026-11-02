"""Every published port must be bound to loopback.

Regression for a real exposure, found by doc-gardener run `2026-11-02-dg1` (g-06):
`docker-compose.yml` published the Vite dev server as `"5174:5173"` — no host IP,
so Docker bound it to every interface. That server proxies `/api`, `/memory` and
`/files/` to the backend, and the app has **no auth layer**: a request carries the
user id it claims, so anyone on the LAN could have read and written the household's
memory through it.

`AGENTS.md` states the rule in the strongest terms ("FastAPI must bind to
127.0.0.1 only — never 0.0.0.0 or a public interface"; "SearXNG must bind to
127.0.0.1 only"), and `assistant/scripts/verify_security.py` checks the *code*
binds. Nothing checked the compose files, which is where the surfaces are actually
declared — so the one surface that leaked was the one no check read.

This parses every compose file and fails on any `ports:` entry without a
loopback host IP. It is deliberately about the declaration, not about Docker's
state: the file is what a reviewer can see and what a rebuild will apply.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
LOOPBACK = {"127.0.0.1", "::1"}


def _compose_files() -> list[Path]:
    return sorted(p for p in ROOT.glob("docker-compose*.yml"))


def _services(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("services") or {}


def _short_form(entry: str) -> str | None:
    """The host IP of a `HOST:CONTAINER` / `IP:HOST:CONTAINER` string, or None.

    Docker's short syntax: `"80"` and `"8080:80"` publish on every interface;
    `"127.0.0.1:8080:80"` is the loopback form. Anything without three segments
    therefore has no host IP and is a failure by construction.
    """
    parts = entry.rsplit(":", 2)
    return parts[0] if len(parts) == 3 else None


@pytest.mark.parametrize("compose_file", _compose_files(), ids=lambda p: p.name)
def test_every_published_port_is_loopback(compose_file: Path):
    offenders: list[str] = []
    for name, service in _services(compose_file).items():
        for port in service.get("ports") or []:
            if isinstance(port, str):
                host_ip = _short_form(port)
                if host_ip not in LOOPBACK:
                    offenders.append(f"{name}: {port!r}")
            elif isinstance(port, dict):
                if port.get("host_ip") not in LOOPBACK:
                    offenders.append(
                        f"{name}: published {port.get('published')} -> "
                        f"{port.get('target')} (host_ip={port.get('host_ip')!r})"
                    )
    assert not offenders, (
        f"{compose_file.name} publishes on every interface: {offenders}. "
        "Prefix the host IP with 127.0.0.1 — the dev server proxies the API, and "
        "there is no auth layer behind it."
    )


def test_the_dev_server_is_the_thing_that_leaked():
    """Pin the shape of the regression, so a rewrite cannot restore it silently.

    The Vite dev server is the dangerous one: it proxies `/api`, `/memory` and
    `/files/` to the backend. If someone later "simplifies" its port back to
    `"5174:5173"`, the parametrised test above catches it; this one names why it
    matters, and fails loudly if the proxy is removed (at which point the exposure
    would be a different, smaller one).
    """
    vite = (ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    assert "'/api'" in vite and "assistant:8000" in vite, (
        "the dev server no longer proxies the API; revisit why its port must stay loopback"
    )
    services = _services(ROOT / "docker-compose.yml")
    dev = next(
        (s for s in services.values()
         if s.get("container_name") == "solid-dev-server" or "solid-dev" == s.get("_name")),
        None,
    )
    if dev is None:  # fall back to the service whose command runs Vite
        dev = next(
            (s for s in services.values()
             if any("5173" in str(p) for p in (s.get("ports") or []))),
            {},
        )
    ports = dev.get("ports") or []
    assert any(
        isinstance(p, str) and "5174" in p and _short_form(p) in LOOPBACK for p in ports
    ), f"solid-dev-server must publish 5174 on loopback only, got {ports!r}"
