"""Build the reference-only FWCollab Benchmark v2 manifest."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping


BENCHMARK_FORMAT = "fwcollab.benchmark.v2"
PROTOCOL_VERSION = "fwcollab.leaderboard_protocol.v1"
ROOT = Path(__file__).resolve().parents[2]

TRACKS = {
    "Core-72": {
        "manifest": "eval_private/spatial_curriculum_full_v4/manifest.json",
        "witness": "eval_private/spatial_curriculum_full_v4/witnesses.json",
        "freeze": "eval_private/benchmark_v1/freeze_manifest.json",
        "evaluator_type": "collaboration_evaluator_v1",
        "observation_format": "fwcollab.agent_observation.v1",
    },
    "Information-12": {
        "manifest": "eval_private/c5_information_12/manifest.json",
        "witness": "eval_private/c5_information_12/manifest.json",
        "freeze": "eval_private/c5_information_12/freeze_manifest.json",
        "evaluator_type": "information_evaluator_v1",
        "observation_format": "fwcollab.c5_agent_observation.v1",
    },
    "Sync-8": {
        "manifest": "eval_private/synchronize_8/manifest.json",
        "witness": "eval_private/synchronize_8/witnesses.json",
        "freeze": "eval_private/synchronize_8/freeze_manifest.json",
        "evaluator_type": "extension_evaluator_v1",
        "observation_format": "fwcollab.agent_observation.v1",
    },
    "Join-8": {
        "manifest": "eval_private/parallel_join_8/manifest.json",
        "witness": "eval_private/parallel_join_8/witnesses.json",
        "freeze": "eval_private/parallel_join_8/freeze_manifest.json",
        "evaluator_type": "extension_evaluator_v1",
        "observation_format": "fwcollab.agent_observation.v1",
    },
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _core_semantics(root: Path) -> dict[str, list[str]]:
    path = root / "artifacts/audits/dependency_edges_v1/dependency_edges.csv"
    result: defaultdict[str, set[str]] = defaultdict(set)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            semantic = str(row["dependency_type"]).upper()
            if semantic in {"ENABLE", "MAINTAIN"}:
                result[str(row["task_id"])].add(semantic)
    return {task_id: sorted(values) for task_id, values in result.items()}


def _resolved_c5_path(relative: str) -> str:
    return (Path("eval_private/c5_information_12") / relative).as_posix()


def _task_hashes(root: Path, map_path: str, dag_path: str, witness_path: str, witness: object) -> dict[str, str]:
    return {
        "map_sha256": _sha(root / map_path),
        "dag_sha256": _sha(root / dag_path),
        "witness_source_sha256": _sha(root / witness_path),
        "witness_entry_sha256": _canonical_sha(witness),
    }


def build_benchmark_v2_manifest(root: Path = ROOT) -> dict[str, Any]:
    root = root.resolve()
    core = _load(root / TRACKS["Core-72"]["manifest"])
    core_witnesses = _load(root / TRACKS["Core-72"]["witness"])["maps"]
    core_semantics = _core_semantics(root)
    information = _load(root / TRACKS["Information-12"]["manifest"])
    sync = _load(root / TRACKS["Sync-8"]["manifest"])
    sync_witnesses = _load(root / TRACKS["Sync-8"]["witness"])["tasks"]
    join = _load(root / TRACKS["Join-8"]["manifest"])
    join_witnesses = _load(root / TRACKS["Join-8"]["witness"])["tasks"]

    tasks: list[dict[str, Any]] = []
    for index, source in enumerate(core["records"]):
        task_id = str(source["id"])
        witness = core_witnesses[task_id]
        tasks.append({
            "suite_name": "Core-72", "task_id": task_id,
            "map_path": source["map"], "dag_path": source["dag"],
            "witness_path": TRACKS["Core-72"]["witness"], "witness_key": f"maps.{task_id}",
            "witness_kind": "stored_action_sequence", "source_manifest_path": TRACKS["Core-72"]["manifest"],
            "source_record_index": index, "evaluator_type": TRACKS["Core-72"]["evaluator_type"],
            "dependency_semantics": core_semantics[task_id], "composition_motifs": [],
            "protocol_version": PROTOCOL_VERSION, "observation_format": TRACKS["Core-72"]["observation_format"],
            "round_budget": max(80, math.ceil(1.5 * len(witness))),
            "hashes": _task_hashes(root, source["map"], source["dag"], TRACKS["Core-72"]["witness"], witness),
        })
    for index, source in enumerate(information["tasks"]):
        task_id = str(source["task_id"])
        map_path, dag_path = _resolved_c5_path(source["map"]), _resolved_c5_path(source["dag"])
        witness = source["budget_certificate"]
        tasks.append({
            "suite_name": "Information-12", "task_id": task_id,
            "map_path": map_path, "dag_path": dag_path,
            "witness_path": TRACKS["Information-12"]["witness"],
            "witness_key": f"tasks.{index}.budget_certificate",
            "witness_kind": "frozen_constructor_certificate", "source_manifest_path": TRACKS["Information-12"]["manifest"],
            "source_record_index": index, "evaluator_type": TRACKS["Information-12"]["evaluator_type"],
            "dependency_semantics": ["INFORMATION"], "composition_motifs": ["INFORMATION_HANDOFF"],
            "protocol_version": PROTOCOL_VERSION, "observation_format": TRACKS["Information-12"]["observation_format"],
            "round_budget": int(source["max_rounds"]),
            "hashes": _task_hashes(root, map_path, dag_path, TRACKS["Information-12"]["witness"], witness),
        })
    for suite_name, suite, witnesses in (("Sync-8", sync, sync_witnesses), ("Join-8", join, join_witnesses)):
        for index, source in enumerate(suite["records"]):
            task_id = str(source["id"])
            witness = witnesses[task_id]["primary"]
            tasks.append({
                "suite_name": suite_name, "task_id": task_id,
                "map_path": source["map"], "dag_path": source["dag"],
                "witness_path": TRACKS[suite_name]["witness"], "witness_key": f"tasks.{task_id}.primary",
                "witness_kind": "stored_action_sequence", "source_manifest_path": TRACKS[suite_name]["manifest"],
                "source_record_index": index, "evaluator_type": TRACKS[suite_name]["evaluator_type"],
                "dependency_semantics": list(source["dependency_semantics"]),
                "composition_motifs": list(source["composition_motifs"]),
                "protocol_version": PROTOCOL_VERSION, "observation_format": TRACKS[suite_name]["observation_format"],
                "round_budget": 80,
                "hashes": _task_hashes(root, source["map"], source["dag"], TRACKS[suite_name]["witness"], witness),
            })

    diagnostic = _load(root / "eval_private/collaboration_diagnostic_24/manifest.json")
    diagnostic_ids = [str(item["id"]) for item in diagnostic["records"]]
    extension_ids = {name: [item["task_id"] for item in tasks if item["suite_name"] == name] for name in ("Information-12", "Sync-8", "Join-8")}
    all_ids = [item["task_id"] for item in tasks]
    track_records = []
    for suite_name, spec in TRACKS.items():
        suite_tasks = [item for item in tasks if item["suite_name"] == suite_name]
        track_records.append({
            "suite_name": suite_name, "task_count": len(suite_tasks),
            "source_manifest_path": spec["manifest"], "source_manifest_sha256": _sha(root / spec["manifest"]),
            "witness_path": spec["witness"], "witness_sha256": _sha(root / spec["witness"]),
            "suite_freeze_path": spec["freeze"], "suite_sha256": _sha(root / spec["freeze"]),
            "evaluator_type": spec["evaluator_type"], "observation_format": spec["observation_format"],
        })
    return {
        "format": BENCHMARK_FORMAT, "benchmark_name": "FWCollab Benchmark v2",
        "description": "Four separately reported evaluation tracks; this benchmark is not named Full-100.",
        "protocol_version": PROTOCOL_VERSION, "task_count": len(tasks), "track_count": 4,
        "tracks": track_records,
        "evaluation_profiles": {
            "standard": {
                "label": "Standard Track", "task_count": 52,
                "task_ids": [*diagnostic_ids, *extension_ids["Information-12"], *extension_ids["Sync-8"], *extension_ids["Join-8"]],
                "core_selection_manifest": "eval_private/collaboration_diagnostic_24/manifest.json",
            },
            "full": {"label": "Full Track", "task_count": 100, "task_ids": all_ids},
        },
        "tasks": tasks,
    }


def write_benchmark_v2(root: Path = ROOT) -> tuple[Path, Path]:
    root = root.resolve()
    destination = root / "eval_private/benchmark_v2"
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(json.dumps(build_benchmark_v2_manifest(root), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    frozen_paths = [
        "eval_private/benchmark_v2/manifest.json",
        "src/fwcollab/benchmark_v2.py", "src/fwcollab/unified_eval.py",
        "schemas/unified_evaluation.schema.json", "docs/LEADERBOARD_PROTOCOL_V1.md",
        "scripts/build_benchmark_v2.py", "scripts/audit_benchmark_v2.py",
        "tests/unit/test_benchmark_v2.py",
    ]
    freeze = {
        "format": "fwcollab.benchmark_v2_freeze.v1", "benchmark_version": "v2",
        "frozen_on": "2026-09-18", "task_count": 100, "track_count": 4,
        "statement": "Reference-only four-track freeze; no task, DAG, witness, evaluator semantic, or prior result is rewritten.",
        "source_suites": {item["suite_name"]: {"path": item["suite_freeze_path"], "sha256": item["suite_sha256"]} for item in build_benchmark_v2_manifest(root)["tracks"]},
        "files": [{"path": path, "sha256": _sha(root / path), "bytes": (root / path).stat().st_size} for path in frozen_paths],
    }
    freeze_path = destination / "freeze_manifest.json"
    freeze_path.write_text(json.dumps(freeze, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_path, freeze_path
