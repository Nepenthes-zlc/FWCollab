from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from fwcollab.symbolic.extension_eval import validate_extension_record
from fwcollab.symbolic.extension_suites import FROZEN_BASELINES, build_extension_suites


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load(relative: str) -> dict:
    return json.loads((PROJECT_ROOT / relative).read_text(encoding="utf-8-sig"))


@pytest.fixture(scope="module")
def generated() -> dict:
    return build_extension_suites(PROJECT_ROOT)


def test_sync_8_all_validation_gates_and_conformance(generated: dict) -> None:
    audit = generated["Sync-8"]["audit"]
    assert audit == {
        "suite": "Sync-8",
        "tasks": 8,
        "witness_valid": 8,
        "replay_valid": 8,
        "bindings_valid": 8,
        "role_wait_valid": 8,
        "no_bypass_valid": 8,
        "necessity_valid": 8,
        "conformance_valid": 8,
        "all_edges_evidenced": 8,
    }
    conformance = generated["Sync-8"]["conformance"]
    assert conformance["cases_failed"] == 0
    assert conformance["cases_applicable"] == conformance["cases_passed"] == 37
    cases = _load("artifacts/evaluations/synchronize_conformance_v1/cases.json")["cases"]
    failures = [item for item in cases if item.get("expected_valid") is False and item["applicable"]]
    assert failures and all(item["first_incomplete"] == "sync" for item in failures)


def test_join_8_all_validation_gates_and_conformance(generated: dict) -> None:
    audit = generated["Join-8"]["audit"]
    assert audit == {
        "suite": "Join-8",
        "tasks": 8,
        "witness_valid": 8,
        "replay_valid": 8,
        "bindings_valid": 8,
        "role_wait_valid": 8,
        "no_bypass_valid": 8,
        "necessity_valid": 8,
        "conformance_valid": 8,
        "all_edges_evidenced": 8,
    }
    conformance = generated["Join-8"]["conformance"]
    assert conformance["cases_failed"] == 0
    assert conformance["cases_applicable"] == conformance["cases_passed"] == 59
    assert conformance["cases_not_applicable"] == 5
    cases = _load("artifacts/evaluations/parallel_join_conformance_v1/cases.json")["cases"]
    single = [item for item in cases if item["case"] in {"F_only", "W_only"}]
    both = [item for item in cases if item["case"] == "both_branches"]
    assert len(single) == 16 and all(not item["join_completed"] for item in single)
    assert len(both) == 8 and all(item["join_completed"] for item in both)
    early_release = [item for item in cases if item["case"] == "early_maintain_release" and item["applicable"]]
    assert len(early_release) == 3 and all(item["persistent_violation_detected"] for item in early_release)
    witnesses = _load("eval_private/parallel_join_8/witnesses.json")["tasks"]
    assert all({"primary", "F_first", "W_first"} <= set(value) for value in witnesses.values())


def test_extension_evaluator_requires_explicit_suite_opt_in() -> None:
    manifest = _load("eval_private/synchronize_8/manifest.json")
    record = manifest["records"][0]
    dag = _load(record["dag"])
    wrong_suite = copy.deepcopy(record)
    wrong_suite["suite"] = "Full-72"
    with pytest.raises(ValueError, match="opt-in for Sync-8 only"):
        validate_extension_record(wrong_suite, dag)

    legacy = _load("eval_private/spatial_curriculum_full_v4/manifest.json")["records"][0]
    legacy_dag = _load(legacy["dag"])
    with pytest.raises(ValueError, match="explicit supported extension_semantics"):
        validate_extension_record(legacy, legacy_dag)


def test_extension_freezes_and_frozen_baselines(generated: dict) -> None:
    verification = _load("artifacts/audits/extensions_v1/frozen_baseline_verification.json")
    assert verification["all_unchanged"]
    assert {item["path"]: item["expected_sha256"] for item in verification["files"]} == FROZEN_BASELINES
    for suite in ("synchronize_8", "parallel_join_8"):
        freeze = _load(f"eval_private/{suite}/freeze_manifest.json")
        assert freeze["task_count"] == 8
        assert any(item["path"].endswith("extension_eval.py") for item in freeze["files"])
        assert any("conformance" in item["path"] for item in freeze["files"])
