"""Create the immutable asset manifest for the Phase II benchmark v1 protocol."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from fwcollab.symbolic.dag import load_json, save_json


ROOT = Path(__file__).resolve().parents[1]
SPEC = Path("eval_private/phase2_v1/experiment_spec.json")
OUTPUT = Path("eval_private/phase2_v1/freeze_manifest.json")


def digest(path: Path) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def main() -> int:
    spec = load_json(ROOT / SPEC)
    diagnostic_path = Path(spec["task_manifest"])
    diagnostic = load_json(ROOT / diagnostic_path)
    fixed = {
        Path("SYMBOL_SPEC.md"),
        Path("docs/PUBLIC_RULEBOOK.md"),
        Path("docs/COLLABORATION_EVALUATION_PROTOCOL.md"),
        Path("schemas/collaboration_evaluation.schema.json"),
        Path("src/fwcollab/symbolic/agents.py"),
        Path("src/fwcollab/symbolic/collaboration_eval.py"),
        Path("src/fwcollab/symbolic/map.py"),
        Path("src/fwcollab/symbolic/rules.py"),
        Path("src/fwcollab/symbolic/runner.py"),
        Path("src/fwcollab/symbolic/world.py"),
        Path("scripts/run_phase2_causal_suite.py"),
        SPEC,
        diagnostic_path,
    }
    for record in diagnostic["records"]:
        fixed.add(Path(record["map"]))
        fixed.add(Path(record["dag"]))
    missing = [path.as_posix() for path in fixed if not (ROOT / path).is_file()]
    if missing:
        raise SystemExit(f"cannot freeze missing files: {missing}")
    files = [
        {"path": path.as_posix(), "sha256": digest(path), "bytes": (ROOT / path).stat().st_size}
        for path in sorted(fixed, key=lambda item: item.as_posix())
    ]
    output = {
        "format": "fwcollab.symbolic.benchmark_freeze.v1",
        "benchmark_version": "v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "statement": "The diagnostic subset was selected solely from task structure and collaboration primitives, without access to model performance.",
        "task_count": len(diagnostic["records"]),
        "condition_count": len(spec["conditions"]),
        "replicates": len(spec["replicates"]),
        "expected_episodes": len(diagnostic["records"]) * len(spec["conditions"]) * len(spec["replicates"]),
        "files": files,
    }
    save_json(ROOT / OUTPUT, output)
    print({"files": len(files), "tasks": output["task_count"], "expected_episodes": output["expected_episodes"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
