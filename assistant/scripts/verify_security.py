#!/usr/bin/env python3
"""Security verification for the cognitive assistant.

Run from repository root: python assistant/scripts/verify_security.py

Exits 0 if all checks pass, 1 if any check fails.
"""

import re
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
ASSISTANT_DIR = ROOT / "assistant"
BACKEND_DIR = ASSISTANT_DIR / "backend"
CLI_DIR = ASSISTANT_DIR / "cli"


def check_no_external_urls() -> tuple[bool, str]:
    """Verify no forbidden outbound cloud API URLs in source code.

    Brave Search API (api.search.brave.com) is the only permitted optional
    cloud endpoint. All other cloud LLM/search APIs are forbidden.
    """
    forbidden_patterns = [
        r"https?://api\.openai\.com",
        r"https?://api\.anthropic\.com",
        r"https?://(?:.*\.)?googleapis\.com",
        r"https?://api\.cohere\.ai",
        r"https?://api\.mistral\.ai",
        r"https?://huggingface\.co/(?!.*\.py$)",
        r"https?://api\.bing\.microsoft\.com",
        r"https?://www\.google\.com/search",
        r"https?://api\.serpapi\.com",
    ]

    issues: list[str] = []
    for search_dir in [BACKEND_DIR, CLI_DIR]:
        for py_file in search_dir.rglob("*.py"):
            content = py_file.read_text()

            for pattern in forbidden_patterns:
                matches = re.findall(pattern, content)
                if matches:
                    issues.append(f"   {py_file.relative_to(ROOT)}: {matches}")

    if issues:
        return False, "Forbidden external API URLs found:\n" + "\n".join(issues)
    return True, "No forbidden external API URLs found."


def check_brave_is_gated() -> tuple[bool, str]:
    """Verify Brave Search API is only used when BRAVE_ENABLED is checked."""
    search_py = BACKEND_DIR / "pipeline" / "search.py"
    content = search_py.read_text()
    if "api.search.brave.com" not in content:
        return True, "Brave backend not present (optional check)"
    if "brave_enabled" not in content and "BRAVE_ENABLED" not in content:
        return False, "Brave backend present but not gated by BRAVE_ENABLED"
    return True, "Brave backend is gated by an enablement flag"


def check_ollama_url_localhost() -> tuple[bool, str]:
    """Verify Ollama URL is localhost."""
    config = (BACKEND_DIR / "config.py").read_text()
    if "127.0.0.1" not in config and "localhost" not in config:
        return False, "config.py does not default Ollama URL to localhost"
    return True, "Ollama URL is localhost by default."


def check_search_tool_localhost() -> tuple[bool, str]:
    """Verify search tool only allows localhost endpoints."""
    search_py = (BACKEND_DIR / "pipeline" / "search.py").read_text()
    if "WebSearchTool" not in search_py:
        return True, "No search tool found (optional check)"
     
    # Check for localhost default
    if "127.0.0.1:8080" not in search_py:
        return False, "search.py does not default to localhost SearXNG"
     
    # Check for 0.0.0.0 usage
    if "0.0.0.0" in search_py:
        return False, "search.py contains 0.0.0.0 — should be localhost only"
     
    return True, "Search tool uses localhost-only endpoints."


def check_no_telemetry() -> tuple[bool, str]:
    """Verify no telemetry/analytics code."""
    forbidden = [
        "segment",
        "mixpanel",
        "amplitude",
        "sentry",
        "datadog",
        "analytics",
        "telemetry",
        "track_event",
        "phone_home",
    ]
    issues: list[str] = []
    for search_dir in [BACKEND_DIR, CLI_DIR]:
        for py_file in search_dir.rglob("*.py"):
            content = py_file.read_text().lower()
            lines = content.split("\n")
            for term in forbidden:
                for i, line in enumerate(lines, 1):
                    if term not in line:
                        continue
                    prefix = line.split(term)[0]
                    if "no " in prefix:
                        continue
                    if "without" in prefix:
                        continue
                    if "deny" in prefix or "denied" in prefix:
                        continue
                    if "#" in prefix:
                        continue
                    issues.append(f"  {py_file.relative_to(ROOT)}:{i}: {term}")
                    break
    if issues:
        return False, "Possible telemetry/analytics code:\n" + "\n".join(issues[:10])
    return True, "No telemetry code detected."


def check_bind_address() -> tuple[bool, str]:
    """Verify FastAPI bind address documentation/config is 127.0.0.1."""
    main_py = (BACKEND_DIR / "main.py").read_text()
    if "0.0.0.0" in main_py:
        return False, "main.py contains 0.0.0.0 — FastAPI should bind to 127.0.0.1 only"
    if "host=" in main_py and "127.0.0.1" not in main_py:
        return False, "main.py sets host but not to 127.0.0.1"
    return True, "No insecure bind addresses."


def check_env_gitignored() -> tuple[bool, str]:
    """Verify .env is gitignored at the repository root."""
    gitignore = ROOT / ".gitignore"
    if not gitignore.exists():
        return False, ".gitignore not found at repository root"
    content = gitignore.read_text()
    if ".env" not in content:
        return False, ".env is not in .gitignore"
    return True, ".env is gitignored."


def check_env_example_exists() -> tuple[bool, str]:
    """Verify .env.example exists at the repository root."""
    env_example = ROOT / ".env.example"
    if not env_example.exists():
        return False, ".env.example does not exist at repository root"
    return True, ".env.example exists."


def check_no_secrets_committed() -> tuple[bool, str]:
    """Verify .env can never be committed: either absent, or present only
    while protected by .gitignore. A local .env in a dev checkout is normal;
    the security invariant is that it is never tracked by git."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return True, "No .env file in source tree."
    gitignore = ROOT / ".gitignore"
    if gitignore.exists() and ".env" in gitignore.read_text().splitlines():
        return True, ".env present locally but listed in .gitignore — never committed."
    return False, ".env exists and is not in .gitignore — risk of committing secrets."


def check_localhost_ollama_reachable() -> tuple[bool, str]:
    """If Ollama is running, verify it's on localhost."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        result = sock.connect_ex(("127.0.0.1", 11434))
        if result == 0:
            return True, "Ollama is reachable on localhost:11434 (optional check, pass)"
        return True, "Ollama not running (this check is informational only)"
    finally:
        sock.close()


CHECKS = [
     ("No forbidden external API URLs", check_no_external_urls),
     ("Brave Search is gated", check_brave_is_gated),
     ("Ollama URL is localhost", check_ollama_url_localhost),
     ("SearXNG localhost only", check_search_tool_localhost),
     ("No telemetry code", check_no_telemetry),
     ("No insecure bind addresses", check_bind_address),
     (".env is gitignored", check_env_gitignored),
     (".env.example exists", check_env_example_exists),
     ("No .env file in source tree", check_no_secrets_committed),
     ("Ollama localhost reachable (informational)", check_localhost_ollama_reachable),
]


def main() -> None:
    print("=" * 60)
    print("Cognitive Assistant — Security Verification")
    print("=" * 60)

    all_passed = True
    for name, check_fn in CHECKS:
        passed, message = check_fn()
        status = "PASS" if passed else "FAIL"
        print(f"\n[{status}] {name}")
        print(f"  {message}")
        if not passed and "informational" not in name:
            all_passed = False

    print("\n" + "=" * 60)
    if all_passed:
        print("All security checks passed.")
        sys.exit(0)
    print("SECURITY CHECKS FAILED. Do not deploy.")
    sys.exit(1)


if __name__ == "__main__":
    main()
