"""Deterministic conformance checks for the executable-DAG evaluator.

The suite treats the frozen Full-72 witness trajectories as positive fixtures and
derives labeled trace variants without changing maps, DAGs, or evaluator logic.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from fwcollab.symbolic.collaboration_eval import evaluate_collaboration_trace
from fwcollab.symbolic.dag import load_json, topological_order
from fwcollab.symbolic.map import load_symbol_map
from fwcollab.symbolic.world import DIRECTIONS, SymbolAction, SymbolWorld

SUITE_FORMAT = "fwcollab.evaluator_conformance.v1"
CASE_FAMILIES = ("witness", "mutation", "metamorphic", "counterfactual")
MAINTAIN_CASES = {"release_hold", "delete_hold"}
INVALID_CASES = {
    "release_hold",
    "delete_hold",
    "parent_child_order_swap",
    "closed_gate_crossing",
}


class ConformanceError(ValueError):
    """Raised when frozen inputs or a conformance oracle are inconsistent."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_frozen_manifest(root: Path, manifest_path: Path) -> str:
    freeze_path = root / "eval_private/benchmark_v1/freeze_manifest.json"
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    relative = manifest_path.relative_to(root).as_posix()
    entry = freeze.get("source_manifests", {}).get("full_benchmark")
    if not isinstance(entry, Mapping) or entry.get("path") != relative:
        entry = None
    if entry is None:
        raise ConformanceError(f"benchmark freeze does not contain {relative}")
    actual = _sha256(manifest_path)
    if actual != entry.get("sha256"):
        raise ConformanceError(f"frozen manifest hash mismatch for {relative}")
    return freeze_path.relative_to(root).as_posix()


def _run_actions(record: Mapping[str, Any], actions: Sequence[Mapping[str, Sequence[Any]]]) -> dict[str, Any]:
    """Execute joint actions directly through the authoritative transition system."""

    world = SymbolWorld(load_symbol_map(str(record["map"])))
    rounds: list[dict[str, Any]] = []
    for round_number, joint in enumerate(actions, start=1):
        if world.status != "running":
            break
        observation = world.observation()
        normalized = {
            role: SymbolAction(move=str(joint[role][0]), steps=int(joint[role][1]))
            for role in ("F", "W")
        }
        result = world.step_joint(normalized)
        rounds.append(
            {
                "round": round_number,
                "observation": observation,
                "agents": {
                    role: {
                        "decision": {
                            "action": {
                                "move": normalized[role].move,
                                "steps": normalized[role].steps,
                            }
                        },
                        "feedback": result.feedback[role],
                    }
                    for role in ("F", "W")
                },
                "result": {
                    "status": result.status,
                    "feedback": dict(result.feedback),
                    "observation": result.observation,
                },
            }
        )
    return {
        "map_id": record["id"],
        "outcome": world.status if world.status != "running" else "timeout",
        "rounds": rounds,
        "final_observation": world.observation(),
    }


def _trace_actions(trace: Mapping[str, Any]) -> list[dict[str, list[Any]]]:
    return [
        {
            role: [
                item["agents"][role]["decision"]["action"]["move"],
                item["agents"][role]["decision"]["action"]["steps"],
            ]
            for role in ("F", "W")
        }
        for item in trace["rounds"]
    ]


def _node_map(evaluation: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {str(item["node_id"]): item for item in evaluation["node_results"]}


def _case(
    *,
    task_id: str,
    family: str,
    mutation: str,
    applicable: bool,
    passed: bool | None,
    oracle: str,
    observed: Mapping[str, Any] | None = None,
    target_node: str | None = None,
    target_round: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    if family not in CASE_FAMILIES:
        raise ConformanceError(f"unknown conformance family: {family}")
    return {
        "case_id": f"{task_id}:{mutation}",
        "task_id": task_id,
        "family": family,
        "mutation": mutation,
        "applicable": applicable,
        "passed": passed,
        "oracle": oracle,
        "target_node": target_node,
        "target_round": target_round,
        "reason": reason,
        "observed": dict(observed or {}),
    }


def _truncate_case(
    record: Mapping[str, Any],
    dag: Mapping[str, Any],
    trace: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    ordered = [
        item
        for item in baseline["node_results"]
        if item["predicate"].get("path") != "status"
        and item["first_completed_at"] not in (None, 0)
    ]
    if not ordered:
        return _case(
            task_id=str(record["id"]), family="counterfactual", mutation="truncate_at_node",
            applicable=False, passed=None, oracle="first incomplete required node equals the first node after the prefix",
            reason="no non-initial completed node available",
        )
    target = ordered[len(ordered) // 2]
    cut = int(target["first_completed_at"]) - 1
    mutated = {
        "map_id": trace["map_id"],
        "outcome": "timeout",
        "rounds": copy.deepcopy(trace["rounds"][:cut]),
        "final_observation": (
            copy.deepcopy(trace["rounds"][cut - 1]["result"]["observation"])
            if cut else copy.deepcopy(trace["rounds"][0]["observation"])
        ),
    }
    evaluation = evaluate_collaboration_trace(record, dag, mutated)
    baseline_nodes = _node_map(baseline)
    expected = next(
        (
            node_id
            for node_id in topological_order(dag)
            if baseline_nodes[node_id]["required_for_observed_path"]
            and baseline_nodes[node_id]["predicate"].get("path") != "status"
            and (
                baseline_nodes[node_id]["first_completed_at"] is None
                or int(baseline_nodes[node_id]["first_completed_at"]) > cut
            )
        ),
        None,
    )
    observed = evaluation["failure_analysis"]["first_incomplete_required_node"]
    return _case(
        task_id=str(record["id"]), family="counterfactual", mutation="truncate_at_node",
        applicable=True, passed=observed == expected,
        oracle="first incomplete required node equals the graph-derived first incomplete node after truncation",
        target_node=expected, target_round=cut,
        observed={"failure_stage": observed, "dag_completion": evaluation["summary"]["dag_completion"]},
    )


def _irrelevant_wait_case(
    record: Mapping[str, Any],
    dag: Mapping[str, Any],
    trace: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    actions = _trace_actions(trace)
    actions.insert(0, {"F": ["WAIT", 0], "W": ["WAIT", 0]})
    mutated_trace = _run_actions(record, actions)
    evaluation = evaluate_collaboration_trace(record, dag, mutated_trace)
    base_nodes = _node_map(baseline)
    mutated_nodes = _node_map(evaluation)
    shifted = all(
        mutated_nodes[node_id]["first_completed_at"]
        == (None if item["first_completed_at"] is None else int(item["first_completed_at"]) + 1)
        for node_id, item in base_nodes.items()
        if item["predicate"].get("path") != "status" or item["first_completed_at"] != 0
    )
    invariant = (
        evaluation["outcome"] == "team_success"
        and evaluation["summary"]["dag_completion"] == 1.0
        and evaluation["summary"]["coordination_violations"] == 0
        and shifted
    )
    return _case(
        task_id=str(record["id"]), family="metamorphic", mutation="insert_irrelevant_wait",
        applicable=True, passed=invariant,
        oracle="leading joint WAIT preserves completion/violations and shifts non-initial completion rounds by one",
        target_round=1,
        observed={
            "outcome": evaluation["outcome"],
            "dag_completion": evaluation["summary"]["dag_completion"],
            "coordination_violations": evaluation["summary"]["coordination_violations"],
            "completion_rounds_shifted": shifted,
        },
    )


def _irrelevant_move_case(
    record: Mapping[str, Any],
    dag: Mapping[str, Any],
    trace: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    first = trace["rounds"][0]
    world = SymbolWorld(load_symbol_map(str(record["map"])))
    candidate = None
    for role in ("F", "W"):
        start = world.actors[role]
        for move, delta in DIRECTIONS.items():
            other = "LEFT" if move == "RIGHT" else "RIGHT" if move == "LEFT" else "UP" if move == "DOWN" else "DOWN"
            trial = SymbolWorld(load_symbol_map(str(record["map"])))
            first_result = trial.step_joint({role: SymbolAction(move=move, steps=1)})
            if first_result.feedback[role] != "moved" or trial.status != "running":
                continue
            second_result = trial.step_joint({role: SymbolAction(move=other, steps=1)})
            if second_result.feedback[role] == "moved" and trial.actors[role] == start and trial.status == "running":
                candidate = (role, move, other)
                break
        if candidate:
            break
    if candidate is None:
        return _case(
            task_id=str(record["id"]), family="metamorphic", mutation="insert_irrelevant_move",
            applicable=False, passed=None, oracle="reversible two-move detour preserves evaluator outputs except timing",
            reason="no safe reversible initial move found",
        )
    role, move, other = candidate
    idle = "W" if role == "F" else "F"
    actions = _trace_actions(trace)
    actions[0:0] = [
        {role: [move, 1], idle: ["WAIT", 0]},
        {role: [other, 1], idle: ["WAIT", 0]},
    ]
    mutated_trace = _run_actions(record, actions)
    evaluation = evaluate_collaboration_trace(record, dag, mutated_trace)
    passed = (
        evaluation["outcome"] == "team_success"
        and evaluation["summary"]["dag_completion"] == baseline["summary"]["dag_completion"]
        and evaluation["summary"]["coordination_violations"] == baseline["summary"]["coordination_violations"]
    )
    return _case(
        task_id=str(record["id"]), family="metamorphic", mutation="insert_irrelevant_move",
        applicable=True, passed=passed,
        oracle="safe reversible detour preserves outcome, final DAG completion, and violation count",
        target_round=1,
        observed={"role": role, "moves": [move, other], "outcome": evaluation["outcome"], "dag_completion": evaluation["summary"]["dag_completion"], "coordination_violations": evaluation["summary"]["coordination_violations"]},
    )


def _release_case(
    record: Mapping[str, Any],
    dag: Mapping[str, Any],
    trace: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    delete_hold: bool,
) -> dict[str, Any]:
    mutation = "delete_hold" if delete_hold else "release_hold"
    definitions = [item for item in baseline["handoffs"] if item["maintain_required"]]
    if not definitions:
        return _case(
            task_id=str(record["id"]), family="mutation", mutation=mutation, applicable=False, passed=None,
            oracle="mutation is detected as a premature MAINTAIN release before traveler crossing",
            reason="task has no maintained handoff",
        )
    actions = _trace_actions(trace)
    evaluation = None
    selected = None
    dag_nodes = {str(node["predicate"]["state"]): str(node["id"]) for node in dag["nodes"]}
    baseline_nodes = _node_map(baseline)
    for definition in definitions:
        start, cross = definition["started_at"], definition["completed_at"]
        if start is None or cross is None or int(cross) <= int(start):
            continue
        if delete_hold:
            stage_number = int(str(definition["id"]).split("_")[-1])
            stage = next(item for item in record["stages"] if int(item["stage"]) == stage_number)
            plate = next((item for item in stage["controllers"] if item["kind"] == "plate"), None)
            if plate is None:
                continue
            plate_node = dag_nodes[f"{plate['id']}_on"]
            plate_round = baseline_nodes[plate_node]["first_completed_at"]
            rounds = [] if plate_round in (None, 0) else [int(plate_round)]
        else:
            rounds = [int(start) + 1]
        for round_number in rounds:
            trial = copy.deepcopy(actions)
            trial[round_number - 1][definition["source_role"]] = (
                ["WAIT", 0] if delete_hold else ["LEFT", 1]
            )
            candidate_trace = _run_actions(record, trial[: int(cross)])
            candidate = evaluate_collaboration_trace(record, dag, candidate_trace)
            if delete_hold:
                target = next(item for item in candidate["handoffs"] if item["id"] == definition["id"])
                detected = not target["completed"]
                evidence: list[Mapping[str, Any]] = []
            else:
                evidence = [
                    item for item in candidate["coordination_violations"]
                    if item["type"] == "premature_release" and item["handoff_id"] == definition["id"]
                ]
                detected = bool(evidence)
            if detected:
                evaluation, selected = candidate, (definition, round_number, evidence)
                break
        if selected:
            break
    if selected is None or evaluation is None:
        return _case(
            task_id=str(record["id"]), family="mutation", mutation=mutation, applicable=False, passed=None,
            oracle=("deleting the plate-entry action prevents the maintained handoff" if delete_hold else "mutation is detected as a premature MAINTAIN release before traveler crossing"),
            reason=("no plate-entry deletion prevented the maintained handoff" if delete_hold else "no single-step LEFT mutation produced an authoritative release"),
        )
    definition, round_number, violations = selected
    target_handoff = next(item for item in evaluation["handoffs"] if item["id"] == definition["id"])
    passed = (
        not target_handoff["completed"]
        if delete_hold
        else bool(violations) and evaluation["summary"]["harmful_node_regressions"] > 0
    )
    return _case(
        task_id=str(record["id"]), family="mutation", mutation=mutation, applicable=True, passed=passed,
        oracle=("deleting the authoritative plate-entry action prevents the maintained handoff" if delete_hold else "authoritative plate release before crossing yields premature_release and harmful regression"),
        target_node=str(definition["id"]), target_round=round_number,
        observed={"premature_release_count": len(violations), "handoff_completed": target_handoff["completed"], "harmful_node_regressions": evaluation["summary"]["harmful_node_regressions"], "failure_stage": evaluation["failure_analysis"]["first_incomplete_required_node"]},
    )


def _parent_child_swap_case(
    record: Mapping[str, Any],
    dag: Mapping[str, Any],
    trace: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    nodes = {str(node["id"]): node for node in dag["nodes"]}
    first = {str(item["node_id"]): item["first_completed_at"] for item in baseline["node_results"]}
    candidates = [
        (str(edge["from"]), str(edge["to"]))
        for edge in dag["edges"]
        if nodes[str(edge["to"])]["owner"] in {"agent_a", "agent_b"}
        and nodes[str(edge["to"])]["predicate"]["op"] in {"occupy", "toggle", "latch", "traverse"}
        and first.get(str(edge["from"])) not in (None, 0)
        and first.get(str(edge["to"])) is not None
    ]
    if not candidates:
        return _case(
            task_id=str(record["id"]), family="mutation", mutation="parent_child_order_swap",
            applicable=False, passed=None, oracle="child predicate rising before its parent produces predicate_before_dependencies",
            reason="no action-owned child with a delayed parent",
        )
    parent, child = candidates[0]
    cut = int(first[child])
    mutated = {
        "map_id": trace["map_id"], "outcome": "timeout",
        "rounds": copy.deepcopy(trace["rounds"][:cut]),
        "final_observation": copy.deepcopy(trace["rounds"][cut - 1]["result"]["observation"]),
    }
    binding = next(item for item in record["node_bindings"] if item["dag_node_id"] == child)
    path = str(binding["observation_path"]).split(".")
    observation = mutated["rounds"][0]["observation"]
    current = observation
    for segment in path[:-1]:
        current = current[segment]
    current[path[-1]] = copy.deepcopy(binding["expected_value"])
    evaluation = evaluate_collaboration_trace(record, dag, mutated)
    matches = [
        item for item in evaluation["dependency_violations"]
        if item["type"] == "predicate_before_dependencies" and item["node_id"] == child and parent in item["missing_dependencies"]
    ]
    return _case(
        task_id=str(record["id"]), family="mutation", mutation="parent_child_order_swap", applicable=True, passed=bool(matches),
        oracle="synthetic child-state rise before an incomplete direct parent is reported as predicate_before_dependencies",
        target_node=child, target_round=0,
        observed={"matching_violations": len(matches), "parent_node": parent},
    )


def _closed_gate_case(
    record: Mapping[str, Any],
    dag: Mapping[str, Any],
    trace: Mapping[str, Any],
) -> dict[str, Any]:
    for stage in record["stages"]:
        actuator = stage["actuator"]
        traveler = stage["traveler"]
        positions = {tuple(position) for position in actuator["positions"]}
        for round_number, item in enumerate(trace["rounds"], start=1):
            pre = item["observation"]
            position = pre["state"]["actors"][traveler]
            for move, delta in DIRECTIONS.items():
                target = (int(position[0]) + delta[0], int(position[1]) + delta[1])
                if target not in positions:
                    continue
                container = "doors" if actuator["kind"] == "door" else "platforms"
                if pre["state"][container][actuator["id"]]:
                    continue
                mutated_rounds = copy.deepcopy(trace["rounds"][:round_number])
                mutated_rounds[-1]["agents"][traveler]["decision"]["action"] = {"move": move, "steps": 1}
                mutated = {"map_id": trace["map_id"], "outcome": "timeout", "rounds": mutated_rounds, "final_observation": mutated_rounds[-1]["result"]["observation"]}
                evaluation = evaluate_collaboration_trace(record, dag, mutated)
                matches = [
                    violation for violation in evaluation["coordination_violations"]
                    if violation["type"] == "closed_actuator_traverse_attempt" and violation["responsible_agent"] == traveler
                ]
                return _case(
                    task_id=str(record["id"]), family="counterfactual", mutation="closed_gate_crossing",
                    applicable=True, passed=bool(matches),
                    oracle="attempt into a closed actuator cell is detected from pre-state geometry and action",
                    target_node=str(actuator["id"]), target_round=round_number,
                    observed={"matching_violations": len(matches), "traveler": traveler, "move": move},
                )
    return _case(
        task_id=str(record["id"]), family="counterfactual", mutation="closed_gate_crossing",
        applicable=False, passed=None, oracle="attempt into a closed actuator cell is detected from pre-state geometry and action",
        reason="witness never places traveler adjacent to a closed actuator",
    )


def build_conformance(root: Path) -> dict[str, Any]:
    root = root.resolve()
    manifest_path = root / "eval_private/spatial_curriculum_full_v4/manifest.json"
    witness_path = root / "eval_private/spatial_curriculum_full_v4/witnesses.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    witnesses = json.loads(witness_path.read_text(encoding="utf-8"))["maps"]
    freeze_path = _verify_frozen_manifest(root, manifest_path)
    records = manifest.get("records")
    if not isinstance(records, list) or len(records) != 72 or len(witnesses) != 72:
        raise ConformanceError("conformance v1 requires the frozen Full-72 manifest and witnesses")

    cases: list[dict[str, Any]] = []
    for record in records:
        dag = load_json(root / str(record["dag"]))
        trace = _run_actions(record, witnesses[record["id"]])
        baseline = evaluate_collaboration_trace(record, dag, trace)
        binding_nodes = {
            str(binding["dag_node_id"]): int(binding["first_true_round"])
            for binding in record["node_bindings"]
            if next(node for node in dag["nodes"] if node["id"] == binding["dag_node_id"])["predicate"]["op"]
            not in {"engage", "traverse"}
        }
        node_results = _node_map(baseline)
        exact_nodes = sum(
            node_results[node_id]["first_completed_at"] == expected
            for node_id, expected in binding_nodes.items()
        )
        witness_pass = (
            trace["outcome"] == "team_success"
            and baseline["summary"]["dag_completion"] == 1.0
            and baseline["summary"]["coordination_violations"] == 0
            and exact_nodes == len(binding_nodes)
        )
        cases.append(_case(
            task_id=str(record["id"]), family="witness", mutation="successful_witness", applicable=True, passed=witness_pass,
            oracle="successful witness completes all required DAG nodes with zero violations; exact equality-predicate node rounds match construction bindings",
            observed={"outcome": trace["outcome"], "dag_completion": baseline["summary"]["dag_completion"], "coordination_violations": baseline["summary"]["coordination_violations"], "nodes_completed": baseline["summary"]["dag_nodes_completed"], "nodes_scorable": baseline["summary"]["dag_nodes_required_observed_path"], "node_rounds_correct": exact_nodes, "node_rounds_total": len(binding_nodes)},
        ))
        cases.extend([
            _release_case(record, dag, trace, baseline, delete_hold=False),
            _release_case(record, dag, trace, baseline, delete_hold=True),
            _parent_child_swap_case(record, dag, trace, baseline),
            _truncate_case(record, dag, trace, baseline),
            _closed_gate_case(record, dag, trace),
            _irrelevant_wait_case(record, dag, trace, baseline),
            _irrelevant_move_case(record, dag, trace, baseline),
        ])

    applicable = [case for case in cases if case["applicable"]]
    invalid = [case for case in applicable if case["mutation"] in INVALID_CASES]
    valid = [case for case in applicable if case["mutation"] in {"successful_witness", "insert_irrelevant_wait", "insert_irrelevant_move"}]
    witness_cases = [case for case in applicable if case["mutation"] == "successful_witness"]
    node_correct = sum(case["observed"].get("nodes_completed", 0) for case in witness_cases)
    node_total = sum(case["observed"].get("nodes_scorable", 0) for case in witness_cases)
    binding_round_correct = sum(case["observed"].get("node_rounds_correct", 0) for case in witness_cases)
    binding_round_total = sum(case["observed"].get("node_rounds_total", 0) for case in witness_cases)
    failure_cases = [case for case in applicable if case["mutation"] == "truncate_at_node"]
    maintain = [case for case in applicable if case["mutation"] in MAINTAIN_CASES]
    metrics = {
        "invalid_mutation_detection_rate": sum(bool(case["passed"]) for case in invalid) / len(invalid) if invalid else None,
        "false_positive_rate": sum(not bool(case["passed"]) for case in valid) / len(valid) if valid else None,
        "node_completion_accuracy": node_correct / node_total if node_total else None,
        "equality_binding_round_accuracy": binding_round_correct / binding_round_total if binding_round_total else None,
        "failure_stage_accuracy": sum(bool(case["passed"]) for case in failure_cases) / len(failure_cases) if failure_cases else None,
        "maintain_violation_detection_rate": sum(bool(case["passed"]) for case in maintain) / len(maintain) if maintain else None,
    }
    by_mutation: dict[str, dict[str, int]] = {}
    for mutation in sorted({case["mutation"] for case in cases}):
        subset = [case for case in cases if case["mutation"] == mutation]
        by_mutation[mutation] = {
            "generated": len(subset),
            "applicable": sum(bool(case["applicable"]) for case in subset),
            "passed": sum(case["passed"] is True for case in subset),
            "failed": sum(case["passed"] is False for case in subset),
            "not_applicable": sum(not case["applicable"] for case in subset),
        }
    return {
        "format": SUITE_FORMAT,
        "scope": "Frozen Full-72 witnesses; deterministic offline evaluator conformance; no model outputs",
        "inputs": {
            "manifest": manifest_path.relative_to(root).as_posix(),
            "manifest_sha256": _sha256(manifest_path),
            "witnesses": witness_path.relative_to(root).as_posix(),
            "witnesses_sha256": _sha256(witness_path),
            "benchmark_freeze": freeze_path,
            "frozen_manifest_verified": True,
        },
        "tasks": len(records),
        "cases_generated": len(cases),
        "cases_applicable": len(applicable),
        "cases_passed": sum(case["passed"] is True for case in applicable),
        "cases_failed": sum(case["passed"] is False for case in applicable),
        "cases_not_applicable": sum(not case["applicable"] for case in cases),
        "family_counts": dict(Counter(case["family"] for case in cases)),
        "by_mutation": by_mutation,
        "metric_denominators": {
            "invalid_mutations": len(invalid),
            "valid_traces": len(valid),
            "node_completion_events": node_total,
            "equality_binding_round_events": binding_round_total,
            "failure_stage_cases": len(failure_cases),
            "maintain_mutations": len(maintain),
        },
        "metrics": metrics,
        "cases": cases,
    }


def report_markdown(result: Mapping[str, Any]) -> str:
    def pct(value: float | None) -> str:
        return "N/A" if value is None else f"{100 * value:.1f}%"

    lines = [
        "# Evaluator Automatic Conformance v1", "",
        "Deterministic offline validation over the frozen Full-72 witness corpus. No map, DAG, task, evaluator semantic, model output, or frozen experiment result is modified.", "",
        "## Headline", "",
        f"- Tasks: **{result['tasks']}**",
        f"- Applicable cases: **{result['cases_applicable']}** / {result['cases_generated']} generated",
        f"- Passed applicable cases: **{result['cases_passed']} / {result['cases_applicable']}**",
        f"- Invalid mutation detection rate: **{pct(result['metrics']['invalid_mutation_detection_rate'])}** ({result['metric_denominators']['invalid_mutations']} cases)",
        f"- False-positive rate on valid traces: **{pct(result['metrics']['false_positive_rate'])}** ({result['metric_denominators']['valid_traces']} cases)",
        f"- Node completion accuracy: **{pct(result['metrics']['node_completion_accuracy'])}** ({result['metric_denominators']['node_completion_events']} audited events)",
        f"- Equality-binding round accuracy: **{pct(result['metrics']['equality_binding_round_accuracy'])}** ({result['metric_denominators']['equality_binding_round_events']} audited events)",
        f"- Failure-stage accuracy: **{pct(result['metrics']['failure_stage_accuracy'])}** ({result['metric_denominators']['failure_stage_cases']} truncations)",
        f"- MAINTAIN violation detection: **{pct(result['metrics']['maintain_violation_detection_rate'])}** ({result['metric_denominators']['maintain_mutations']} applicable mutations)",
        "", "## Case matrix", "",
        "| Case | Generated | Applicable | Passed | Failed | N/A |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for mutation, counts in result["by_mutation"].items():
        lines.append(f"| `{mutation}` | {counts['generated']} | {counts['applicable']} | {counts['passed']} | {counts['failed']} | {counts['not_applicable']} |")
    lines.extend([
        "", "## Oracle boundary", "",
        "- **Witness:** successful authoritative execution, full DAG completion, zero evaluator violations, and exact first-completion rounds for equality-predicate construction bindings. Traversal/engagement bindings use boundary predicates during evaluation and are checked through successful completion rather than witness coordinates.",
        "- **Mutation:** labeled premature release/deleted hold and parent-before-child violations. Only mutations that actually create the intended authoritative state change are applicable.",
        "- **Metamorphic:** a leading joint WAIT and a safe reversible detour must preserve outcome/completion/violations, with the expected timing shift.",
        "- **Counterfactual:** prefix truncation must localize the graph-derived first incomplete node; a synthetic closed-gate attempt must be detected from pre-state geometry and submitted action.",
        "- This suite establishes implementation conformance against explicit executable oracles. It is not human construct validity and does not validate whether the authored DAG is the unique semantic decomposition.",
        "", "## Reproduction", "",
        "```bash", "python scripts/validate_evaluator_conformance.py", "```", "",
    ])
    return "\n".join(lines)
