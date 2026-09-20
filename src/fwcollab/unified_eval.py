"""Dispatch frozen track evaluators and wrap their outputs without mutation."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from fwcollab.c5 import evaluate_c5_trace
from fwcollab.symbolic.collaboration_eval import evaluate_collaboration_trace
from fwcollab.symbolic.extension_eval import evaluate_extension_trace


UNIFIED_EVALUATION_FORMAT = "fwcollab.unified_evaluation.v1"
SUPPORTED_EVALUATORS = {
    "collaboration_evaluator_v1",
    "information_evaluator_v1",
    "extension_evaluator_v1",
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _source_record(task_record: Mapping[str, Any], root: Path) -> dict[str, Any]:
    manifest = _load(root / str(task_record["source_manifest_path"]))
    records = manifest["tasks"] if task_record["suite_name"] == "Information-12" else manifest["records"]
    source = records[int(task_record["source_record_index"])]
    source_id = source.get("task_id", source.get("id"))
    if source_id != task_record["task_id"]:
        raise ValueError("Benchmark v2 record does not match its frozen source record")
    return source


def _extension_progress_auc(raw: Mapping[str, Any], dag: Mapping[str, Any]) -> float:
    scorable = {str(node["id"]) for node in dag["nodes"] if node["predicate"]["op"] != "start"}
    timeline = raw["timeline"]
    values = [len(set(item["completed_nodes"]) & scorable) / len(scorable) if scorable else 1.0 for item in timeline]
    if len(values) <= 1:
        return values[0] if values else 0.0
    return sum((values[index - 1] + values[index]) / 2 for index in range(1, len(values))) / (len(values) - 1)


def _trace_observations(trace: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rounds = trace.get("rounds", [])
    if not isinstance(rounds, list) or not rounds:
        final = trace.get("final_observation")
        return [final] if isinstance(final, Mapping) else []
    values = [rounds[0]["observation"]]
    values.extend(item["result"]["observation"] for item in rounds)
    return values


def _path(value: Mapping[str, Any], dotted: str) -> Any:
    current: Any = value
    for part in dotted.split("."):
        current = current[part]
    return current


def _max_overlap(trace: Mapping[str, Any], paths: list[str]) -> int:
    maximum = streak = 0
    for observation in _trace_observations(trace):
        if all(bool(_path(observation, path)) for path in paths):
            streak += 1
            maximum = max(maximum, streak)
        else:
            streak = 0
    return maximum


def _runner_status(trace: Mapping[str, Any]) -> str:
    outcome = str(trace.get("outcome", "runner_error"))
    if outcome == "team_success":
        return "completed"
    if outcome == "timeout":
        diagnosis = trace.get("diagnosis")
        if isinstance(diagnosis, Mapping) and diagnosis.get("primary"):
            return str(diagnosis["primary"])
    return outcome


def _base(
    task: Mapping[str, Any], trace: Mapping[str, Any], raw: Mapping[str, Any], *, completion: float, auc: float,
    dependency_success: bool, violations: list[Mapping[str, Any]], handoff: Mapping[str, Any] | None = None,
    information: Mapping[str, Any] | None = None, synchronization: Mapping[str, Any] | None = None,
    parallel_join: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "format": UNIFIED_EVALUATION_FORMAT, "task_id": task["task_id"], "suite": task["suite_name"],
        "evaluator_type": task["evaluator_type"], "outcome": trace.get("outcome"),
        "success": trace.get("outcome") == "team_success", "dag_completion": completion,
        "progress_auc": auc, "dependency_semantics": list(task["dependency_semantics"]),
        "dependency_success": dependency_success,
        "dependency_violations": {"count": len(violations), "events": copy.deepcopy(violations)},
        "handoff_metrics": copy.deepcopy(handoff), "information_metrics": copy.deepcopy(information),
        "synchronization_metrics": copy.deepcopy(synchronization),
        "parallel_join_metrics": copy.deepcopy(parallel_join),
        "rounds": len(trace.get("rounds", [])), "runner_status": _runner_status(trace),
        "raw_evaluation": copy.deepcopy(raw),
    }


def evaluate_fwcollab_task(
    task_record: Mapping[str, Any], trace: Mapping[str, Any], *, root: Path | str | None = None
) -> dict[str, Any]:
    """Evaluate a v2 task through its frozen evaluator and add one common envelope.

    ``raw_evaluation`` is a deep-copied, unmodified return value from the
    underlying track evaluator. Missing track-specific metrics are JSON null.
    """

    project = Path(root).resolve() if root is not None else Path(__file__).resolve().parents[2]
    evaluator = task_record.get("evaluator_type")
    if evaluator not in SUPPORTED_EVALUATORS:
        raise ValueError(f"unsupported Benchmark v2 evaluator_type: {evaluator!r}")
    if trace.get("map_id") != task_record.get("task_id"):
        raise ValueError("trace map_id does not match Benchmark v2 task_id")
    source = _source_record(task_record, project)
    dag = _load(project / str(task_record["dag_path"]))

    if evaluator == "collaboration_evaluator_v1":
        raw = evaluate_collaboration_trace(source, dag, trace)
        summary = raw["summary"]
        violations = raw["dependency_violations"]
        opportunities = int(summary["handoff_opportunities"])
        handoff = {
            "opportunities": opportunities, "successes": int(summary["handoff_successes"]),
            "success_rate": summary["handoff_success_rate"], "clean_rate": summary["clean_handoff_rate"],
            "maintain_opportunities": sum(bool(item["maintain_required"]) for item in raw["handoffs"]),
            "harmful_regressions": int(summary["harmful_node_regressions"]),
        }
        dependency_success = int(summary["handoff_successes"]) == opportunities and not violations
        return _base(task_record, trace, raw, completion=float(summary["dag_completion"]), auc=float(summary["dag_progress_auc"]), dependency_success=dependency_success, violations=violations, handoff=handoff)

    if evaluator == "information_evaluator_v1":
        raw = evaluate_c5_trace(source, trace)
        violations: list[Mapping[str, Any]] = []
        if raw["dependency_violation"]:
            violations.append({"type": "activation_before_information_received"})
        if raw["wrong_controller_before_answer"]:
            violations.append({"type": "wrong_controller_before_answer"})
        information = {
            "dependency_satisfied": bool(raw["clean_information_handoff"]),
            "clean_information_handoff": bool(raw["clean_information_handoff"]),
            "wrong_controller_before_answer": bool(raw["wrong_controller_before_answer"]),
            "information_failure_stage": raw["failure_stage"],
        }
        return _base(task_record, trace, raw, completion=float(raw["dag_completion"]), auc=float(raw["dag_progress_auc"]), dependency_success=bool(raw["clean_information_handoff"] and not violations), violations=violations, information=information)

    raw = evaluate_extension_trace(source, dag, trace)
    violations = raw["dependency_violations"]
    completion, auc = float(raw["dag_completion"]), _extension_progress_auc(raw, dag)
    semantics = source["extension_semantics"]
    if task_record["suite_name"] == "Sync-8":
        binding = next(item for item in source["node_bindings"] if item["dag_node_id"] == semantics["synchronization_node_id"])
        maximum = _max_overlap(trace, list(binding["condition_paths"]))
        required = int(binding["minimum_overlap_rounds"])
        satisfied = semantics["synchronization_node_id"] in raw["completed_nodes"]
        synchronization = {
            "synchronization_success": satisfied, "overlap_satisfied": satisfied,
            "minimum_overlap_rounds": required, "maximum_observed_overlap_rounds": maximum,
            "synchronization_violations": int(not satisfied),
        }
        return _base(task_record, trace, raw, completion=completion, auc=auc, dependency_success=bool(satisfied and not violations), violations=violations, synchronization=synchronization)

    branches = semantics["branch_node_ids"]
    completed = set(raw["completed_nodes"])
    branch_completion = {role: str(node_id) in completed for role, node_id in branches.items()}
    joined = str(semantics["join_node_id"]) in completed
    parallel = {
        "branch_completion": branch_completion, "branches_completed": sum(branch_completion.values()),
        "join_satisfied": joined, "join_violations": int(not joined),
        "order_invariance": "validated_task_property_not_episode_metric",
    }
    handoff = None
    if "MAINTAIN" in task_record["dependency_semantics"]:
        handoff = {"maintain_satisfied": not raw["persistent_condition_violations"], "premature_releases": len(raw["persistent_condition_violations"])}
    return _base(task_record, trace, raw, completion=completion, auc=auc, dependency_success=bool(joined and all(branch_completion.values()) and not violations), violations=violations, handoff=handoff, parallel_join=parallel)


def validate_unified_evaluation(value: Mapping[str, Any]) -> None:
    required = {
        "format", "task_id", "suite", "evaluator_type", "outcome", "success",
        "dag_completion", "progress_auc", "dependency_semantics", "dependency_success",
        "dependency_violations", "handoff_metrics", "information_metrics",
        "synchronization_metrics", "parallel_join_metrics", "rounds", "runner_status",
        "raw_evaluation",
    }
    if set(value) != required or value.get("format") != UNIFIED_EVALUATION_FORMAT:
        raise ValueError("invalid unified evaluation envelope")
    for field in ("dag_completion", "progress_auc"):
        number = value[field]
        if not isinstance(number, (int, float)) or not 0 <= number <= 1:
            raise ValueError(f"{field} must be in [0,1]")
    violations = value["dependency_violations"]
    if not isinstance(violations, Mapping) or violations.get("count") != len(violations.get("events", [])):
        raise ValueError("dependency violation count does not match events")
