"""Tests for Docker configuration.

We don't run Docker in tests (that would require a Docker daemon and slow
everything down). We verify the configuration files are well-formed and contain
the required security properties.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
REPO = ROOT.parent
DOCKERFILE = REPO / "Dockerfile"
COMPOSE = REPO / "docker-compose.yml"
COMPOSE_PROD = REPO / "docker-compose.prod.yml"
COMPOSE_DEV = REPO / "docker-compose.test.yml"
DOCKERIGNORE = REPO / ".dockerignore"


def test_dockerfile_exists():
    assert DOCKERFILE.exists()


def test_dockerfile_binds_via_env_var():
    content = DOCKERFILE.read_text()
    assert "BACKEND_HOST" in content
    assert "${BACKEND_HOST}" in content or "$BACKEND_HOST" in content


def test_dockerfile_uses_non_root_user():
    content = DOCKERFILE.read_text()
    assert "USER assistant" in content and "useradd" in content


def test_dockerfile_has_healthcheck():
    content = DOCKERFILE.read_text()
    assert "HEALTHCHECK" in content


def test_dockerfile_uses_slim_base():
    content = DOCKERFILE.read_text()
    assert "slim" in content


def test_compose_binds_to_localhost_only():
    """Backend must not be directly exposed. Only Caddy (HTTPS) and SearXNG are exposed."""
    content = COMPOSE.read_text()
    data = yaml.safe_load(content)

    # Assistant service must have NO ports published (it is behind Caddy)
    assistant_ports = data.get("services", {}).get("assistant", {}).get("ports", [])
    assert assistant_ports == [], (
        f"assistant service must not expose any ports directly (got {assistant_ports}). "
        "All traffic goes through the Caddy reverse proxy."
    )

    # Caddy must expose HTTPS on localhost
    caddy_ports = data.get("services", {}).get("caddy", {}).get("ports", [])
    assert any("127.0.0.1:8443" in str(p) for p in caddy_ports), (
        "Caddy must expose HTTPS on 127.0.0.1:8443"
    )

    # SearXNG must be bound to localhost only
    searxng_ports = data.get("services", {}).get("searxng", {}).get("ports", [])
    assert any("127.0.0.1:8080" in str(p) for p in searxng_ports), (
        "SearXNG must be bound to 127.0.0.1:8080"
    )


def test_compose_uses_volume_for_db():
    content = COMPOSE.read_text()
    assert "volumes:" in content
    assert "assistant-data" in content


def test_compose_connects_to_host_ollama():
    content = COMPOSE.read_text()
    assert "host.docker.internal" in content
    assert "extra_hosts" in content


def test_dockerignore_excludes_env():
    content = DOCKERIGNORE.read_text()
    assert ".env" in content
    assert "*.db" in content


def test_dockerignore_excludes_tests():
    """Tests run in a separate dev container, not the runtime image."""
    content = DOCKERIGNORE.read_text()
    assert "tests" in content


def test_docker_compose_dev_exists():
    assert COMPOSE_DEV.exists()


def test_dockerfile_includes_sqlite_vec():
    """sqlite-vec is required for the vector memory extension."""
    content = DOCKERFILE.read_text()
    assert "sqlite-vec" in content


def test_compose_has_cli_service():
    content = COMPOSE.read_text()
    assert "cli:" in content


def test_cli_service_uses_assistant_backend_as_url():
    content = COMPOSE.read_text()
    assert "ASSISTANT_BACKEND=http://assistant-backend:8000" in content


def test_cli_service_no_public_ports():
    """The CLI service should not expose any ports."""
    config = yaml.safe_load(COMPOSE.read_text())
    cli_config = config.get("services", {}).get("cli", {})
    assert "ports" not in cli_config or not cli_config["ports"]


def test_cli_service_uses_cli_profile():
    """The CLI service should only start when explicitly requested."""
    config = yaml.safe_load(COMPOSE.read_text())
    cli_config = config.get("services", {}).get("cli", {})
    assert "cli" in cli_config.get("profiles", [])


def test_bin_assistant_exists():
    wrapper = ROOT / "bin" / "assistant"
    assert wrapper.exists()


def test_bin_assistant_is_executable():
    wrapper = ROOT / "bin" / "assistant"
    import stat

    mode = wrapper.stat().st_mode
    assert mode & stat.S_IXUSR


# --- Resilience (Phase 5) -------------------------------------------------
#
# The failure these guard against is subtle: the app looked resilient while a
# probe that depends on Ollama made it restart itself during an Ollama outage.


def test_healthcheck_is_liveness_not_readiness():
    """The container probe must not call Ollama.

    `/health` probes Ollama and can block for the client timeout (up to
    OLLAMA_TIMEOUT, 600s) when Ollama is down. As a healthcheck that marks the
    app unhealthy during an outage and, with autoheal, restarts a container that
    a restart cannot fix. `/healthz` does no I/O.
    """
    content = DOCKERFILE.read_text()
    assert "/healthz" in content, "the healthcheck should probe the liveness route"
    # `/health` as a whole word, not as the prefix of `/healthz`.
    assert "8000/health " not in content and "8000/health\n" not in content, (
        "the healthcheck still probes /health, which depends on Ollama"
    )


def test_liveness_route_does_no_io():
    """/healthz returns a constant; if it grows an I/O call it becomes a liability."""
    import asyncio

    from assistant.backend.main import healthz

    assert asyncio.run(healthz()) == {"status": "ok"}


def test_prod_compose_restarts_unhealthy_containers():
    """`restart:` only fires on exit; a hung process needs autoheal."""
    data = yaml.safe_load(COMPOSE_PROD.read_text())
    autoheal = data.get("services", {}).get("autoheal")
    assert autoheal, "prod has no autoheal service, so a hung app is never restarted"
    assert "/var/run/docker.sock" in str(autoheal.get("volumes", []))


def test_autoheal_is_scoped_not_global():
    """AUTOHEAL_CONTAINER_LABEL=all would restart unrelated containers on this host.

    The host runs other stacks (Supabase, etc.); a global autoheal would restart
    any unhealthy container on the machine.
    """
    data = yaml.safe_load(COMPOSE_PROD.read_text())
    env = data["services"]["autoheal"].get("environment", [])
    joined = " ".join(str(e) for e in env)
    assert "AUTOHEAL_CONTAINER_LABEL=autoheal" in joined
    assert "AUTOHEAL_CONTAINER_LABEL=all" not in joined


def test_caddy_waits_for_a_healthy_backend():
    """Otherwise Caddy proxies to a half-booted app right after a rebuild."""
    data = yaml.safe_load(COMPOSE_PROD.read_text())
    depends = data["services"]["caddy"].get("depends_on", {})
    assert depends.get("assistant", {}).get("condition") == "service_healthy"


def test_app_services_have_bounded_logs():
    """json-file defaults to unbounded; the app logs per turn and per LLM call."""
    for path in (COMPOSE, COMPOSE_PROD):
        data = yaml.safe_load(path.read_text())
        for name in ("assistant", "caddy", "searxng"):
            logging = data["services"][name].get("logging", {})
            assert logging.get("driver") == "json-file", f"{path.name}:{name}"
            assert logging.get("options", {}).get("max-size"), f"{path.name}:{name}"
