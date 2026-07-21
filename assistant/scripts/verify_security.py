#!/usr/bin/env python3
"""Security verification for the cognitive assistant.

Run from /assistant/ directory: python scripts/verify_security.py

Exits 0 if all checks pass, 1 if any check fails.
"""

import re
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
BACKEND_DIR = ROOT / "backend"
CLI_DIR = ROOT / "cli"


def check_no_external_urls() -> tuple[bool, str]:
    """Verify no outbound URLs to cloud APIs in source code."""
    forbidden_patterns = [
        r"https?://api\.openai\.com",
        r"https?://api\.anthropic\.com",
        r"https?://(?:.*\.)?googleapis\.com",
        r"https?://api\.cohere\.ai",
        r"https?://api\.mistral\.ai",
        r"https?://huggingface\.co/(?!.*\.py$)",
    ]

    issues: list[str] = []
    for search_dir in [BACKEND_DIR, CLI_DIR]:
        for py_file in search_dir.rglob("*.py"):
            content = py_file.read_text()
            for pattern in forbidden_patterns:
                matches = re.findall(pattern, content)
                if matches:
                    issues.append(f"  {py_file.relative_to(ROOT)}: {matches}")

    if issues:
        return False, "External API URLs found:\n" + "\n".join(issues)
    return True, "No external API URLs found."


def check_ollama_url_localhost() -> tuple[bool, str]:
    """Verify Ollama URL is localhost."""
    config = (BACKEND_DIR / "config.py").read_text()
    if "127.0.0.1" not in config and "localhost" not in config:
        return False, "config.py does not default Ollama URL to localhost"
    return True, "Ollama URL is localhost by default."


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
    """Verify .env is gitignored."""
    gitignore = ROOT / ".gitignore"
    root_gitignore = ROOT.parent / ".gitignore"
    content = ""
    if gitignore.exists():
        content += gitignore.read_text()
    if root_gitignore.exists():
        content += root_gitignore.read_text()
    if ".env" not in content:
        return False, ".env is not in .gitignore"
    return True, ".env is gitignored."


def check_env_example_exists() -> tuple[bool, str]:
    """Verify .env.example exists and .env does not."""
    env_example = ROOT / ".env.example"
    if not env_example.exists():
        return False, ".env.example does not exist"
    return True, ".env.example exists."


def check_no_secrets_committed() -> tuple[bool, str]:
    """Verify no .env file is committed (would be in gitignore, but double-check)."""
    env_file = ROOT / ".env"
    if env_file.exists():
        return False, ".env file exists in source tree — should only be local, never committed"
    return True, "No .env file in source tree."


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
    ("No external API URLs", check_no_external_urls),
    ("Ollama URL is localhost", check_ollama_url_localhost),
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
