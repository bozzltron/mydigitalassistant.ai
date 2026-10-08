"""Tests for the security verification script.

These tests don't verify that the project IS secure (that's a manual review).
They verify that the security CHECKS function correctly — false positives/negatives.
"""

import subprocess
import sys
from pathlib import Path

from assistant.scripts.verify_security import (
    check_bind_address,
    check_env_example_exists,
    check_env_gitignored,
    check_no_external_urls,
    check_no_secrets_committed,
    check_no_telemetry,
    check_ollama_url_localhost,
    check_search_tool_localhost,
)

SCRIPT = Path(__file__).parent.parent / "scripts" / "verify_security.py"
ROOT = Path(__file__).parent.parent


def test_security_script_exists():
    assert SCRIPT.exists()


def test_security_script_runs():
    """The security script should run without error."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"Script failed:\n{result.stdout}\n{result.stderr}"


def test_ollama_url_is_localhost():
    passed, message = check_ollama_url_localhost()
    assert passed is True
    assert "localhost" in message.lower() or "127.0.0.1" in message


def test_no_telemetry_check():
    passed, message = check_no_telemetry()
    assert passed is True
    assert isinstance(message, str)


def test_bind_address_check():
    passed, message = check_bind_address()
    assert passed is True


def test_env_gitignored():
    passed, message = check_env_gitignored()
    assert passed is True


def test_env_example_exists():
    passed, message = check_env_example_exists()
    assert passed is True


def test_no_secrets_committed():
    passed, message = check_no_secrets_committed()
    assert passed is True


def test_security_check_types_are_exhaustive():
    """Ensure all checks return (bool, str)."""
    from assistant.scripts.verify_security import CHECKS

    for _name, check_fn in CHECKS:
        result = check_fn()
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], str)


def test_search_tool_localhost():
    """SearXNG must default to localhost only."""
    passed, message = check_search_tool_localhost()
    assert passed is True


def test_docker_compose_search_base_url_localhost():
    """SEARCH_BASE_URL must point to local SearXNG, not a cloud endpoint."""
    compose = (ROOT.parent / "docker-compose.yml").read_text()
    assert "SEARCH_BASE_URL=http://searxng:8080" in compose or \
           "SEARCH_BASE_URL=http://127.0.0.1:8080" in compose


def test_docker_compose_ollama_url_localhost():
    """OLLAMA_URL must point to local Ollama, not a cloud endpoint."""
    compose = (ROOT.parent / "docker-compose.yml").read_text()
    assert "OLLAMA_URL=http://host.docker.internal:11434" in compose or \
           "OLLAMA_URL=http://127.0.0.1:11434" in compose


def test_no_forbidden_cloud_urls_in_source():
    """The external-URL check passes: no cloud LLM API host is in the source.

    Guards `api.openai.com`, `api.anthropic.com`, `googleapis.com`,
    `api.cohere.ai`, `api.mistral.ai`. This replaced five identical parametrized
    tests whose `forbidden_url` parameter was never used -- they asserted the same
    verdict five times; the check itself scans for every host.
    """
    passed, message = check_no_external_urls()
    assert passed is True, f"External-URL check failed: {message}"
