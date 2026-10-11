"""A setting's default lives in three places. They must agree.

The **Compose `environment:` block wins over `config.py`**, so it is the real default
layer — and it is hand-maintained against every field. Two incidents in one session, both
silent:

1. **38 of the 68 vars documented in `.env.example` were forwarded by neither Compose
   file.** Setting them in `.env` did nothing; they fell back to `config.py`.
2. **`TOOLS_ENABLED` defaulted to `false` in Compose while `config.py` and the docs said
   `true`** — a fresh install had no tools at all: no files, no search, no compute.

Neither raised an error. That is the signal that this should be mechanical.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.prod.yml")


def _normalise(value: str) -> str:
    """Compare defaults as values, not spellings: `600.0` == `600`, `False` == `false`."""
    value = value.split("#")[0].strip().strip('"').strip("'").lower()
    return value[:-2] if value.endswith(".0") else value


def _config_defaults() -> dict[str, str]:
    src = (ROOT / "assistant/backend/config.py").read_text()
    fields = re.findall(r"^\s{4}([a-z][a-z0-9_]*)\s*:\s*[^=]+=\s*(.+)$", src, re.M)
    return {name.upper(): _normalise(value) for name, value in fields}


def _compose_defaults(filename: str) -> dict[str, str]:
    src = (ROOT / filename).read_text()
    return {
        key: _normalise(value)
        for key, value in re.findall(r"^\s+- ([A-Z][A-Z0-9_]+)=\$\{\1:-([^}]*)\}", src, re.M)
    }


def _documented() -> set[str]:
    src = (ROOT / ".env.example").read_text()
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", src, re.M))


def test_every_config_field_is_documented():
    """`.env.example` is the template; a field it omits cannot be discovered."""
    missing = sorted(set(_config_defaults()) - _documented())
    assert not missing, f".env.example does not document: {missing}"


def test_compose_defaults_match_config():
    """A `${VAR:-default}` that disagrees with `config.py` makes the code default a lie."""
    config = _config_defaults()
    for filename in COMPOSE_FILES:
        for key, value in _compose_defaults(filename).items():
            if key in config:
                assert value == config[key], (
                    f"{filename}: {key} defaults to {value!r} but config.py says "
                    f"{config[key]!r} — the Compose block wins, so the code default never "
                    "applies"
                )


def test_dev_and_prod_ship_the_same_model_fleet():
    """A model that differs between environments is a test that proves nothing."""
    dev = _compose_defaults(COMPOSE_FILES[0])
    prod = _compose_defaults(COMPOSE_FILES[1])
    models = {k: v for k, v in dev.items() if k.endswith("_MODEL")}
    assert models, "no *_MODEL defaults found — has the Compose layout changed?"
    assert models == {k: v for k, v in prod.items() if k.endswith("_MODEL")}, (
        "dev and prod must default to the same model fleet"
    )
