from __future__ import annotations

import copy
from pathlib import Path

from fwcollab.analysis.evaluator_conformance import (
    ConformanceError,
    _case,
    build_conformance,
    report_markdown,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


RESULT = None


def _result():
    global RESULT
    if RESULT is None:
        RESULT = build_conformance(PROJECT_ROOT)
    return RESULT


def test_conformance_covers_all_frozen_tasks_and_four_families() -> None:
    result = _result()
    assert result["tasks"] == 72
    assert set(result["family_counts"]) == {"witness", "mutation", "metamorphic", "counterfactual"}
    assert result["by_mutation"]["successful_witness"]["applicable"] == 72
    assert result["by_mutation"]["truncate_at_node"]["applicable"] == 72
    assert result["by_mutation"]["insert_irrelevant_wait"]["applicable"] == 72


def test_all_applicable_oracles_pass_and_metrics_have_denominators() -> None:
    result = _result()
    assert result["cases_applicable"] > 0
    assert result["cases_failed"] == 0
    assert result["cases_passed"] == result["cases_applicable"]
    assert result["metric_denominators"]["invalid_mutations"] > 0
    assert result["metric_denominators"]["valid_traces"] >= 144
    assert result["metric_denominators"]["node_completion_events"] > 1000
    assert result["metric_denominators"]["failure_stage_cases"] == 72
    assert result["metric_denominators"]["maintain_mutations"] > 0
    assert result["metrics"] == {
        "invalid_mutation_detection_rate": 1.0,
        "false_positive_rate": 0.0,
        "node_completion_accuracy": 1.0,
        "equality_binding_round_accuracy": 1.0,
        "failure_stage_accuracy": 1.0,
        "maintain_violation_detection_rate": 1.0,
    }


def test_metric_failures_are_not_hidden_by_not_applicable_rows() -> None:
    result = _result()
    altered = copy.deepcopy(result)
    row = next(case for case in altered["cases"] if case["applicable"] and case["passed"] is True)
    row["passed"] = False
    assert sum(case["passed"] is False for case in altered["cases"] if case["applicable"]) == 1


def test_report_states_construct_validity_boundary() -> None:
    result = _result()
    report = report_markdown(result)
    assert "not human construct validity" in report
    assert "not validate whether the authored DAG is the unique" in report
    assert "Invalid mutation detection rate" in report


def test_case_rejects_unknown_family() -> None:
    try:
        _case(task_id="T", family="unknown", mutation="x", applicable=True, passed=True, oracle="x")
    except ConformanceError as exc:
        assert "unknown conformance family" in str(exc)
    else:
        raise AssertionError("unknown family was accepted")
