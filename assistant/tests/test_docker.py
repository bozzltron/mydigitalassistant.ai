"""Tests for Docker configuration.

We don't run Docker in tests (that would require a Docker daemon and slow
everything down). We verify the configuration files are well-formed and contain
the required security properties.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "docker-compose.yml"
COMPOSE_DEV = ROOT / "docker-compose.dev.yml"
DOCKERIGNORE = ROOT / ".dockerignore"


def test_dockerfile_exists():
    assert DOCKERFILE.exists()


def test_dockerfile_binds_to_localhost():
    content = DOCKERFILE.read_text()
    assert "127.0.0.1" in content
    assert "0.0.0.0" not in content


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
    content = COMPOSE.read_text()
    assert "127.0.0.1:8000:8000" in content
    assert "0.0.0.0:8000:8000" not in content


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
