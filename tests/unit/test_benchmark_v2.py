from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import fwcollab.unified_eval as unified_eval
from fwcollab.benchmark_v2 import build_benchmark_v2_manifest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load(relative: str) -> dict:
    return json.loads((PROJECT_ROOT / relative).read_text(encoding="utf-8-sig"))


def _sha(relative: str) -> str:
    return hashlib.sha256((PROJECT_ROOT / relative).read_bytes()).hexdigest()


def _task(manifest: dict, suite: str) -> dict:
    return next(item for item in manifest["tasks"] if item["suite_name"] == suite)


def _set_path(root: dict, dotted: str, value: object) -> None:
    current = root
    parts = dotted.split(".")
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


@pytest.fixture(scope="module")
def manifest() -> dict:
    return _load("eval_private/benchmark_v2/manifest.json")


def test_manifest_has_four_tracks_and_two_profiles(manifest: dict) -> None:
    assert manifest == build_benchmark_v2_manifest(PROJECT_ROOT)
    assert manifest["benchmark_name"] == "FWCollab Benchmark v2"
    assert manifest["task_count"] == 100
    assert {item["suite_name"]: item["task_count"] for item in manifest["tracks"]} == {
        "Core-72": 72,
        "Information-12": 12,
        "Sync-8": 8,
        "Join-8": 8,
    }
    assert len({item["task_id"] for item in manifest["tasks"]}) == 100
    assert manifest["evaluation_profiles"]["standard"]["task_count"] == 52
    assert len(manifest["evaluation_profiles"]["standard"]["task_ids"]) == 52
    assert manifest["evaluation_profiles"]["full"]["task_count"] == 100
    assert len(manifest["evaluation_profiles"]["full"]["task_ids"]) == 100


def test_manifest_task_hashes_match_frozen_sources(manifest: dict) -> None:
    for task in manifest["tasks"]:
        hashes = task["hashes"]
        assert hashes["map_sha256"] == _sha(task["map_path"])
        assert hashes["dag_sha256"] == _sha(task["dag_path"])
        assert hashes["witness_source_sha256"] == _sha(task["witness_path"])


def test_benchmark_v2_freeze_manifest_is_valid() -> None:
    freeze = _load("eval_private/benchmark_v2/freeze_manifest.json")
    assert freeze["task_count"] == 100
    assert freeze["track_count"] == 4
    for item in freeze["files"]:
        path = PROJECT_ROOT / item["path"]
        assert path.stat().st_size == item["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]


def test_core_dispatch_preserves_raw_output_and_nulls_na_metrics(monkeypatch: pytest.MonkeyPatch, manifest: dict) -> None:
    task = _task(manifest, "Core-72")
    raw = {
        "summary": {
            "dag_completion": 1.0,
            "dag_progress_auc": 0.75,
            "handoff_opportunities": 1,
            "handoff_successes": 1,
            "handoff_success_rate": 1.0,
            "clean_handoff_rate": 1.0,
            "harmful_node_regressions": 0,
        },
        "dependency_violations": [],
        "handoffs": [{"maintain_required": True}],
    }
    monkeypatch.setattr(unified_eval, "evaluate_collaboration_trace", lambda *_: raw)
    result = unified_eval.evaluate_fwcollab_task(
        task, {"map_id": task["task_id"], "outcome": "team_success", "rounds": []}, root=PROJECT_ROOT
    )
    assert result["raw_evaluation"] == raw
    assert result["raw_evaluation"] is not raw
    assert result["handoff_metrics"] is not None
    assert result["information_metrics"] is None
    assert result["synchronization_metrics"] is None
    assert result["parallel_join_metrics"] is None
    unified_eval.validate_unified_evaluation(result)


def test_information_dispatch_preserves_raw_output_and_nulls_na_metrics(monkeypatch: pytest.MonkeyPatch, manifest: dict) -> None:
    task = _task(manifest, "Information-12")
    raw = {
        "dependency_violation": False,
        "wrong_controller_before_answer": False,
        "clean_information_handoff": True,
        "failure_stage": None,
        "dag_completion": 1.0,
        "dag_progress_auc": 0.8,
    }
    monkeypatch.setattr(unified_eval, "evaluate_c5_trace", lambda *_: raw)
    result = unified_eval.evaluate_fwcollab_task(
        task, {"map_id": task["task_id"], "outcome": "team_success", "rounds": []}, root=PROJECT_ROOT
    )
    assert result["raw_evaluation"] == raw
    assert result["handoff_metrics"] is None
    assert result["information_metrics"]["dependency_satisfied"] is True
    assert result["synchronization_metrics"] is None
    assert result["parallel_join_metrics"] is None
    unified_eval.validate_unified_evaluation(result)


def test_sync_and_join_dispatch_use_extension_evaluator(monkeypatch: pytest.MonkeyPatch, manifest: dict) -> None:
    for suite in ("Sync-8", "Join-8"):
        task = _task(manifest, suite)
        source_manifest = _load(task["source_manifest_path"])
        source = source_manifest["records"][task["source_record_index"]]
        dag = _load(task["dag_path"])
        completed = [node["id"] for node in dag["nodes"]]
        raw = {
            "dag_completion": 1.0,
            "completed_nodes": completed,
            "dependency_violations": [],
            "persistent_condition_violations": [],
            "timeline": [{"completed_nodes": completed}],
        }
        monkeypatch.setattr(unified_eval, "evaluate_extension_trace", lambda *_, raw=raw: raw)
        trace = {
            "map_id": task["task_id"],
            "outcome": "team_success",
            "rounds": [],
            "final_observation": {},
        }
        if suite == "Sync-8":
            binding = next(
                item for item in source["node_bindings"]
                if item["dag_node_id"] == source["extension_semantics"]["synchronization_node_id"]
            )
            for path in binding["condition_paths"]:
                _set_path(trace["final_observation"], path, True)
        result = unified_eval.evaluate_fwcollab_task(task, trace, root=PROJECT_ROOT)
        assert result["raw_evaluation"] == raw
        assert result["information_metrics"] is None
        assert (result["synchronization_metrics"] is not None) == (suite == "Sync-8")
        assert (result["parallel_join_metrics"] is not None) == (suite == "Join-8")
        unified_eval.validate_unified_evaluation(result)


def test_unknown_evaluator_is_rejected(manifest: dict) -> None:
    task = dict(_task(manifest, "Core-72"))
    task["evaluator_type"] = "unknown_evaluator"
    with pytest.raises(ValueError, match="unsupported Benchmark v2 evaluator_type"):
        unified_eval.evaluate_fwcollab_task(task, {}, root=PROJECT_ROOT)
