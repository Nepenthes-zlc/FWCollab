from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.audit_benchmark_v1 import (
    EXPECTED,
    _collect_mismatches,
    build_audit,
    main,
    normalized_collaboration_signature,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FREEZE = PROJECT_ROOT / "eval_private/benchmark_v1/freeze_manifest.json"


@pytest.fixture(scope="module")
def audit() -> dict[str, Any]:
    return build_audit(PROJECT_ROOT, FREEZE)


def test_audit_reproduces_all_four_diversity_counts(audit: dict[str, Any]) -> None:
    assert audit["ok"] is True
    assert audit["mismatches"] == []
    for scope, expected in EXPECTED.items():
        actual = audit["results"][scope]
        assert actual["concrete_dag_instances"] == expected["concrete_dag_instances"]
        assert actual["dependency_topology_classes"] == expected["dependency_topology_classes"]
        assert (
            actual["normalized_collaboration_templates"]
            == expected["normalized_collaboration_templates"]
        )
        assert actual["typed_executable_templates"] == expected["typed_executable_templates"]
        assert actual["typed_executable_duplicate_groups"] == []


def test_audit_reproduces_taxonomy_counts_including_zero_coverage(audit: dict[str, Any]) -> None:
    assert audit["results"]["full_benchmark"]["taxonomy_counts"] == {
        "C1_hold_and_pass": 65,
        "C2_sequential_handoff": 70,
        "C3_mutual_unlock": 68,
        "C4_synchronized_action": 0,
        "C5_information_dependent": 0,
        "C6_multi_stage_alternating": 60,
        "C7_parallel_subgoal_merge": 0,
    }
    assert audit["results"]["diagnostic_collaboration_set"]["taxonomy_counts"] == {
        "C1_hold_and_pass": 23,
        "C2_sequential_handoff": 23,
        "C3_mutual_unlock": 22,
        "C4_synchronized_action": 0,
        "C5_information_dependent": 0,
        "C6_multi_stage_alternating": 19,
        "C7_parallel_subgoal_merge": 0,
    }


def test_audit_distinguishes_spatial_and_dependency_topology(audit: dict[str, Any]) -> None:
    spatial = audit["spatial_manifest_metrics"]
    assert spatial["spatial_topology_unique"] == 72
    assert spatial["is_dag_dependency_topology_count"] is False
    assert spatial["dag_dependency_topology_classes"] == 48
    assert "not a DAG dependency-topology count" in audit["definitions"]["spatial_topology_unique"]


def test_diagnostic_dependency_duplicate_groups_are_exact(audit: dict[str, Any]) -> None:
    assert audit["results"]["diagnostic_collaboration_set"][
        "dependency_topology_duplicate_groups"
    ] == [
        ["V4-L2-006", "V4-L2-007"],
        ["V4-L3-004", "V4-L3-010"],
        ["V4-L5-005", "V4-L5-011"],
    ]


def test_normalized_template_ignores_global_role_names_and_persistent_kind() -> None:
    left = {
        "id": "left",
        "stages": [
            {
                "supporter": "F",
                "mode": "all",
                "controllers": [{"kind": "plate"}],
            },
            {
                "supporter": "W",
                "mode": "any",
                "controllers": [{"kind": "lever"}, {"kind": "toggle"}],
            },
        ],
    }
    right = {
        "id": "right",
        "stages": [
            {
                "supporter": "W",
                "mode": "all",
                "controllers": [{"kind": "plate"}],
            },
            {
                "supporter": "F",
                "mode": "any",
                "controllers": [{"kind": "toggle"}, {"kind": "lever"}],
            },
        ],
    }
    assert normalized_collaboration_signature(left) == normalized_collaboration_signature(right)


def test_audit_emits_distributions_and_stable_class_membership(audit: dict[str, Any]) -> None:
    full = audit["results"]["full_benchmark"]
    assert sum(full["difficulty_distribution"].values()) == 72
    assert sum(full["dag_node_count_distribution"].values()) == 72
    assert sum(full["dag_edge_count_distribution"].values()) == 72
    assert sum(full["stage_count_distribution"].values()) == 72
    assert len(full["class_membership"]) == 72
    assert len(audit["instances"]) == 72
    assert all(row["full_typed_template_class"].startswith("X") for row in audit["instances"])


def test_fixed_expectation_mismatch_is_reported_not_hidden() -> None:
    actual = {"count": 47}
    expected = {"count": 48}
    assert _collect_mismatches(actual, expected) == [
        {"field": "count", "expected": 48, "actual": 47}
    ]


def test_audit_cli_writes_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = tmp_path / "audit.json"
    assert (
        main(
            [
                "--root",
                str(PROJECT_ROOT),
                "--freeze-manifest",
                str(FREEZE),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    summary = json.loads(capsys.readouterr().out)
    written = json.loads(output.read_text(encoding="utf-8"))
    assert summary["ok"] is True
    assert written["ok"] is True
    assert summary["full"] == {
        "concrete_dag_instances": 72,
        "dependency_topology_classes": 48,
        "normalized_collaboration_templates": 65,
        "typed_executable_templates": 72,
    }
    assert (tmp_path / "summary.json").read_bytes() == output.read_bytes()
    instances = (tmp_path / "instances.csv").read_text(encoding="utf-8").splitlines()
    taxonomy = (tmp_path / "taxonomy.csv").read_text(encoding="utf-8").splitlines()
    report = (tmp_path / "REPORT.md").read_text(encoding="utf-8")
    assert len(instances) == 73
    assert len(taxonomy) == 15
    assert "full_dependency_topology_class" in instances[0]
    assert "full_benchmark,C4_synchronized_action,0,72,0.000000,uncovered" in taxonomy
    assert "many-to-one canonicalizations" in report
    assert "without access to model performance" in report
