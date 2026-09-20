"""Create the immutable pre-run hash manifest for C5 v1."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "eval_private" / "c5_information_12"


def main() -> None:
    paths = [
        ROOT / "docs" / "C5_INFORMATION_MINISUITE_SPEC.md",
        ROOT / "src" / "fwcollab" / "c5.py",
        ROOT / "scripts" / "build_c5_information_suite.py",
        ROOT / "scripts" / "run_c5_information_suite.py",
        SUITE / "manifest.json",
        SUITE / "experiment_spec.json",
        *sorted((SUITE / "maps").glob("*.fwmap")),
        *sorted((SUITE / "dags").glob("*.json")),
    ]
    files = []
    for path in paths:
        data = path.read_bytes()
        files.append({
            "path": path.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        })
    value = {
        "format": "fwcollab.c5_freeze.v1",
        "benchmark_version": "c5-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "statement": "The C5 suite and protocol were frozen before any C5 model episode was run.",
        "task_count": 12,
        "condition_count": 4,
        "replicates": 3,
        "expected_episodes": 144,
        "files": files,
    }
    (SUITE / "freeze_manifest.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"files": len(files), "tasks": 12, "expected_episodes": 144}))


if __name__ == "__main__":
    main()
