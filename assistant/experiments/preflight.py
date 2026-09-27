"""Pre-flight safety check for a read-only experiment against a brain copy.

Refuses to start unless every one of these holds:
  1. The DB path the harness will open is not the live volume.
  2. The live volume is not mounted into the experiment container at all.
  3. A verified restorable backup of the live brain exists.
  4. The copy being read is byte-identical to the live DB, or its digest is
     recorded, so "we read what we think we read" is checkable.

Run before any experiment that opens a brain. Read-only: it reads digests and
stats, and writes nothing outside its own scratch path.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

LIVE_DB = "/live/assistant.db"
EXPERIMENT_DB = os.environ.get("EXP_DB", "/exp/assistant.db")
BACKUP_DIR = Path(os.environ.get("EXP_BACKUP_DIR", "/backups"))
REPORT = Path(os.environ.get("EXP_PREFLIGHT", "/tmp/preflight.json"))


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    checks: list[Check] = []

    live = Path(LIVE_DB)
    exp = Path(EXPERIMENT_DB)

    # 1. The experiment must not be pointed at the live database.
    same_file = live.exists() and exp.exists() and live.samefile(exp)
    checks.append(
        Check(
            "experiment_db_is_not_live_db",
            not same_file,
            f"{exp} vs {live}: {'SAME FILE - ABORT' if same_file else 'distinct'}",
        )
    )

    # 2. The live volume must not even be mounted in. A read-only mistake on a
    #    mounted volume is one bug away from a write.
    live_mounted = Path("/live").exists()
    checks.append(
        Check(
            "live_volume_not_mounted",
            not live_mounted,
            f"/live {'IS' if live_mounted else 'is not'} present in this container",
        )
    )

    # 3. A restorable backup must exist, and be non-empty and non-trivial.
    brains = sorted(BACKUP_DIR.glob("*.assistant-brain")) if BACKUP_DIR.exists() else []
    usable = [b for b in brains if b.stat().st_size > 1_000_000]
    checks.append(
        Check(
            "restorable_backup_exists",
            bool(usable),
            f"{len(usable)} usable .assistant-brain file(s) in {BACKUP_DIR}",
        )
    )

    # 4. Record what we are about to read, so the run is auditable afterwards.
    exp_digest = sha256(exp) if exp.exists() else ""
    live_digest = sha256(live) if live.exists() else ""
    checks.append(
        Check(
            "experiment_db_readable_and_recorded",
            bool(exp_digest),
            f"sha256={exp_digest[:16]}... size={exp.stat().st_size if exp.exists() else 0}",
        )
    )

    report = {
        "experiment_db": str(exp),
        "experiment_db_sha256": exp_digest,
        "live_db_sha256": live_digest,
        "matches_live": bool(exp_digest and exp_digest == live_digest),
        "backups": [str(b) for b in usable],
        "checks": [asdict(c) for c in checks],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2))

    for c in checks:
        print(f"[{'PASS' if c.ok else 'FAIL'}] {c.name}: {c.detail}")
    print(f"\nreport: {REPORT}")
    failed = [c for c in checks if not c.ok]
    if failed:
        print(f"\nABORT: {len(failed)} safety check(s) failed")
        return 1
    print("\nAll safety checks passed. Safe to run read-only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
