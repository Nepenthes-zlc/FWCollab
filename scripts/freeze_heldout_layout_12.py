"""Freeze Held-out Layout-12 before any model inference."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "eval_private" / "heldout_layout_12"


def main() -> None:
    paths = [
        ROOT / "docs" / "HELDOUT_LAYOUT_12_SPEC.md",
        ROOT / "scripts" / "build_heldout_layout_12.py",
        ROOT / "scripts" / "run_phase2_causal_suite.py",
        ROOT / "src" / "fwcollab" / "symbolic" / "agents.py",
        ROOT / "src" / "fwcollab" / "symbolic" / "runner.py",
        ROOT / "src" / "fwcollab" / "symbolic" / "world.py",
        ROOT / "src" / "fwcollab" / "symbolic" / "map.py",
        ROOT / "src" / "fwcollab" / "symbolic" / "collaboration_eval.py",
        SUITE / "manifest.json",
        SUITE / "witnesses.json",
        SUITE / "experiment_spec.json",
        *sorted((SUITE / "maps").glob("*.fwmap")),
        *sorted((SUITE / "dags").glob("*.json")),
    ]
    files = []
    for path in paths:
        data = path.read_bytes()
        files.append({"path": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    value = {
        "format": "fwcollab.heldout_layout_freeze.v1",
        "benchmark_version": "heldout-layout-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "statement": "Source selection and all held-out layouts were frozen before any held-out model episode was run.",
        "task_count": 12,
        "condition_count": 2,
        "replicates": 3,
        "expected_episodes": 72,
        "files": files,
    }
    (SUITE / "freeze_manifest.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"files": len(files), "tasks": 12, "expected_episodes": 72}))


if __name__ == "__main__":
    main()
