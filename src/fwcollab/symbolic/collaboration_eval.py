"""Deterministic, private post-run collaboration evaluation for V4 traces."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from fwcollab.symbolic.dag import topological_order, validate_state_dag

EVALUATION_FORMAT = "fwcollab.symbolic.collaboration_evaluation.v1"
ROLE_FOR_OWNER = {"agent_a": "F", "agent_b": "W"}
MOVE_DELTAS = {
    "UP": (-1, 0),
    "DOWN": (1, 0),
    "LEFT": (0, -1),
    "RIGHT": (0, 1),
}


def _path_value(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            raise KeyError(f"observation path is missing: {path}")
        current = current[segment]
    return current


def _observations(trace: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rounds = trace.get("rounds", [])
    if not isinstance(rounds, list):
        raise ValueError("trace.rounds must be a list")
    if not rounds:
        final = trace.get("final_observation")
        if not isinstance(final, Mapping):
            raise ValueError("empty trace requires final_observation")
        return [final]
    first = rounds[0].get("observation")
    if not isinstance(first, Mapping):
        raise ValueError("trace round is missing observation")
    result = [first]
    for item in rounds:
        post = item.get("result", {})
        observation = post.get("observation") if isinstance(post, Mapping) else None
        if not isinstance(observation, Mapping):
            raise ValueError("trace round is missing result.observation")
        result.append(observation)
    return result


def _normalized_predicate(
    node: Mapping[str, Any], binding: Mapping[str, Any], record: Mapping[str, Any]
) -> dict[str, Any]:
    """Turn generator evidence into a predicate that is executable on every snapshot."""

    predicate = node["predicate"]
    path = str(binding["observation_path"])
    if predicate["op"] == "traverse":
        predicate_state = str(predicate["state"])
        for stage in record["stages"]:
            if predicate_state == f"{stage['actuator']['id']}_crossed":
                return {
                    "kind": "actor_column_greater_than",
                    "role": str(stage["traveler"]),
                    "column": int(stage["room"]["gate_col"]),
                    "alive": True,
                }
        capability = record["capability"]
        return {
            "kind": "actor_column_greater_than",
            "role": str(capability["traveler"]),
            "column": int(capability["gate_col"]),
            "alive": True,
        }
    if predicate["op"] == "engage" and path.startswith("layout."):
        capability = record["capability"]
        traveler = str(capability["traveler"])
        gate_col = int(capability["gate_col"])
        if predicate["motif"] == "M05":
            return {
                "kind": "actor_column_at_least",
                "role": traveler,
                "column": gate_col,
                "alive": True,
            }
        cross_binding = next(
            candidate
            for candidate in record["node_bindings"]
            if candidate["dag_node_id"] != node["id"]
            and str(candidate.get("observation_path")) == f"state.actors.{traveler}"
            and isinstance(candidate.get("expected_value"), list)
            and int(candidate["expected_value"][1]) > gate_col
        )
        return {
            "kind": "equals",
            "path": f"state.actors.{traveler}",
            "value": [int(cross_binding["expected_value"][0]), gate_col],
        }
    return {"kind": "equals", "path": path, "value": binding.get("expected_value")}


def _predicate_true(spec: Mapping[str, Any], observation: Mapping[str, Any]) -> bool:
    kind = spec["kind"]
    if kind == "equals":
        return _path_value(observation, str(spec["path"])) == spec.get("value")
    if kind in {"actor_column_at_least", "actor_column_greater_than"}:
        role = str(spec["role"])
        position = _path_value(observation, f"state.actors.{role}")
        alive = _path_value(observation, f"state.alive.{role}")
        compare = (
            int(position[1]) >= int(spec["column"])
            if kind == "actor_column_at_least"
            else int(position[1]) > int(spec["column"])
        )
        return bool(alive) == bool(spec["alive"]) and compare
    raise ValueError(f"unsupported evaluator predicate kind: {kind}")


def _node_maps(
    dag: Mapping[str, Any], record: Mapping[str, Any]
) -> tuple[dict[str, Mapping[str, Any]], dict[str, dict[str, Any]]]:
    nodes = {str(node["id"]): node for node in dag["nodes"]}
    bindings = {str(binding["dag_node_id"]): binding for binding in record["node_bindings"]}
    if set(nodes) != set(bindings):
        missing = sorted(set(nodes) - set(bindings))
        extra = sorted(set(bindings) - set(nodes))
        raise ValueError(f"node binding mismatch; missing={missing}, extra={extra}")
    specs = {
        node_id: _normalized_predicate(node, bindings[node_id], record)
        for node_id, node in nodes.items()
    }
    return nodes, specs


def _stage_handoffs(
    record: Mapping[str, Any], nodes: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    state_to_id = {str(node["predicate"]["state"]): node_id for node_id, node in nodes.items()}
    handoffs: list[dict[str, Any]] = []
    for stage in record["stages"]:
        actuator_id = str(stage["actuator"]["id"])
        controller_ids = [str(item["id"]) for item in stage["controllers"]]
        handoffs.append(
            {
                "id": f"stage_{stage['stage']}",
                "kind": "controller_pass",
                "source_role": stage["supporter"],
                "target_role": stage["traveler"],
                "controller_ids": controller_ids,
                "controller_kinds": {
                    str(item["id"]): str(item["kind"]) for item in stage["controllers"]
                },
                "controller_positions": {
                    str(item["id"]): list(item["position"]) for item in stage["controllers"]
                },
                "controller_node_ids": [state_to_id[f"{item}_on"] for item in controller_ids],
                "actuator_id": actuator_id,
                "actuator_kind": stage["actuator"]["kind"],
                "actuator_positions": [list(item) for item in stage["actuator"]["positions"]],
                "actuator_node_id": state_to_id[f"{actuator_id}_on"],
                "cross_node_id": state_to_id[f"{actuator_id}_crossed"],
                "mode": stage["mode"],
            }
        )

    capability = record.get("capability", {})
    collaborative_capability = bool(
        capability.get("kind") in {"heavy_plate", "thermal"}
        or (
            capability.get("kind") == "light"
            and (capability.get("object") or capability.get("rotatable"))
        )
    )
    if collaborative_capability:
        motif = str(record["primary_capability"])
        engage_id = state_to_id[f"{motif.lower()}_engaged"]
        cross_id = state_to_id[f"{motif.lower()}_crossed"]
        handoffs.append(
            {
                "id": "capability",
                "kind": str(capability["kind"]),
                "source_role": capability["supporter"],
                "target_role": capability["traveler"],
                "controller_ids": [],
                "controller_kinds": {},
                "controller_positions": {},
                "controller_node_ids": [],
                "actuator_id": capability.get("actuator_id"),
                "actuator_kind": None,
                "actuator_positions": [],
                "actuator_node_id": engage_id,
                "cross_node_id": cross_id,
                "mode": "all",
            }
        )
    return handoffs


def collaboration_taxonomy(
    record: Mapping[str, Any], dag: Mapping[str, Any]
) -> dict[str, Any]:
    """Classify only collaboration structures that are evidenced by the task graph."""

    stages = list(record["stages"])
    pairs = {(str(item["supporter"]), str(item["traveler"])) for item in stages}
    plate_stages = sum(
        any(controller["kind"] == "plate" for controller in stage["controllers"])
        for stage in stages
    )
    persistent_stages = sum(
        any(controller["kind"] in {"lever", "toggle"} for controller in stage["controllers"])
        for stage in stages
    )
    alternations = sum(
        stages[index]["supporter"] != stages[index - 1]["supporter"]
        for index in range(1, len(stages))
    )
    owner = {str(node["id"]): str(node["owner"]) for node in dag["nodes"]}
    incoming_owners: defaultdict[str, set[str]] = defaultdict(set)
    for edge in dag["edges"]:
        incoming_owners[str(edge["to"])].add(owner[str(edge["from"])])
    parallel_agent_merge = any(
        {"agent_a", "agent_b"} <= sources for sources in incoming_owners.values()
    )

    tags: list[str] = []
    if plate_stages:
        tags.append("C1_hold_and_pass")
    if persistent_stages:
        tags.append("C2_sequential_handoff")
    if {("F", "W"), ("W", "F")} <= pairs:
        tags.append("C3_mutual_unlock")
    # Current rules have no bounded same-window action primitive and both roles
    # receive the same full public state, so C4/C5 are deliberately not inferred.
    if len(stages) >= 3 and alternations >= 2:
        tags.append("C6_multi_stage_alternating")
    if parallel_agent_merge:
        tags.append("C7_parallel_subgoal_merge")
    return {
        "tags": tags,
        "stage_count": len(stages),
        "plate_stages": plate_stages,
        "persistent_stages": persistent_stages,
        "role_alternations": alternations,
        "has_any_join": any(stage["mode"] == "any" for stage in stages),
        "has_multi_controller_join": any(len(stage["controllers"]) > 1 for stage in stages),
        "coverage_gaps": [
            tag
            for tag in (
                "C1_hold_and_pass",
                "C2_sequential_handoff",
                "C3_mutual_unlock",
                "C4_synchronized_action",
                "C5_information_dependent",
                "C6_multi_stage_alternating",
                "C7_parallel_subgoal_merge",
            )
            if tag not in tags
        ],
    }


def _actor_action(round_record: Mapping[str, Any], role: str) -> str:
    agents = round_record.get("agents", {})
    actor = agents.get(role, {}) if isinstance(agents, Mapping) else {}
    decision = actor.get("decision", {}) if isinstance(actor, Mapping) else {}
    action = decision.get("action", {}) if isinstance(decision, Mapping) else {}
    return str(action.get("move", "WAIT")) if isinstance(action, Mapping) else "WAIT"


def evaluate_collaboration_trace(
    record: Mapping[str, Any], dag: Mapping[str, Any], trace: Mapping[str, Any]
) -> dict[str, Any]:
    """Evaluate one trace using only private DAG metadata and authoritative observations."""

    validate_state_dag(dag)
    if trace.get("map_id") != record.get("id"):
        raise ValueError("trace and manifest record refer to different maps")
    nodes, specs = _node_maps(dag, record)
    order = topological_order(dag)
    incoming: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    outgoing: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    for edge in dag["edges"]:
        incoming[str(edge["to"])].add(str(edge["from"]))
        outgoing[str(edge["from"])].add(str(edge["to"]))

    handoff_defs = _stage_handoffs(record, nodes)
    handoff_node_ids = {
        node_id
        for handoff in handoff_defs
        for node_id in (
            *handoff["controller_node_ids"],
            handoff["actuator_node_id"],
            handoff["cross_node_id"],
        )
    }
    observations = _observations(trace)
    raw_truth = {
        node_id: [_predicate_true(specs[node_id], observation) for observation in observations]
        for node_id in nodes
    }
    completed: set[str] = set()
    first_completed: dict[str, int] = {}
    first_true = {
        node_id: next((index for index, value in enumerate(values) if value), None)
        for node_id, values in raw_truth.items()
    }
    timeline: list[dict[str, Any]] = []
    dependency_violations: list[dict[str, Any]] = []
    node_regressions: list[dict[str, Any]] = []
    scorable = [
        node_id for node_id in nodes if nodes[node_id]["predicate"]["op"] != "start"
    ]

    for round_index in range(len(observations)):
        newly_completed: list[str] = []
        round_dependency: list[dict[str, Any]] = []
        round_regressions: list[dict[str, Any]] = []
        for node_id in order:
            node = nodes[node_id]
            predecessors = incoming[node_id]
            ready = (
                not predecessors
                or (node["join"] == "all" and predecessors <= completed)
                or (node["join"] == "any" and bool(predecessors & completed))
            )
            truth = raw_truth[node_id][round_index]
            rising = truth and (round_index == 0 or not raw_truth[node_id][round_index - 1])
            if truth and ready and node_id not in completed:
                completed.add(node_id)
                first_completed[node_id] = round_index
                newly_completed.append(node_id)
            elif (
                rising
                and not ready
                and node["owner"] in ROLE_FOR_OWNER
                and node["predicate"]["op"] in {"occupy", "toggle", "latch", "traverse"}
            ):
                event = {
                    "round": round_index,
                    "type": "predicate_before_dependencies",
                    "node_id": node_id,
                    "responsible_agent": ROLE_FOR_OWNER[node["owner"]],
                    "missing_dependencies": sorted(predecessors - completed),
                }
                dependency_violations.append(event)
                round_dependency.append(event)
        if round_index > 0:
            for node_id, node in nodes.items():
                # Traversal/engagement predicates are momentary even when an older DAG
                # labels them as state nodes.  Regression is meaningful only for
                # conditions that are expected to remain live while enabling work.
                if node["predicate"]["op"] not in {"occupy", "activate"}:
                    continue
                if raw_truth[node_id][round_index - 1] and not raw_truth[node_id][round_index]:
                    # A controller may legally release in the same simultaneous
                    # transition in which the traveler crosses.  Use post-round
                    # completion, not the pre-round set, for harmfulness.
                    pending = sorted(outgoing[node_id] - completed)
                    event = {
                        "round": round_index,
                        "node_id": node_id,
                        "pending_direct_dependents": pending,
                        "harmful": bool(pending),
                    }
                    node_regressions.append(event)
                    round_regressions.append(event)
        timeline.append(
            {
                "round": round_index,
                "newly_completed_nodes": newly_completed,
                "completed_nodes": sorted(completed),
                "progress": 0.0,
                "dependency_violations": round_dependency,
                "node_regressions": round_regressions,
            }
        )

    # An ANY join records alternative valid branches.  A branch that was not
    # needed when the join became satisfied must not lower completion for the
    # actually observed legal path.
    waived: dict[str, dict[str, Any]] = {}
    for target_id, target in nodes.items():
        target_round = first_completed.get(target_id)
        if target["join"] != "any" or target_round is None:
            continue
        for source_id in incoming[target_id]:
            source_round = first_completed.get(source_id)
            if source_round is None or source_round > target_round:
                waived[source_id] = {"waived_by": target_id, "waived_at": target_round}

    for item in timeline:
        round_index = int(item["round"])
        required_at_round = {
            node_id
            for node_id in scorable
            if node_id not in waived or int(waived[node_id]["waived_at"]) > round_index
        }
        completed_at_round = set(item["completed_nodes"])
        item["progress"] = round(
            len(completed_at_round & required_at_round) / len(required_at_round), 6
        ) if required_at_round else 1.0

    rounds = trace.get("rounds", [])
    coordination_violations: list[dict[str, Any]] = []
    useful_hold_rounds = 0
    useful_waits = 0
    total_waits = 0
    for item in rounds:
        for role in ("F", "W"):
            total_waits += _actor_action(item, role) == "WAIT"

    for handoff in handoff_defs:
        cross_round = first_completed.get(handoff["cross_node_id"])
        for round_number, item in enumerate(rounds, start=1):
            pre = observations[round_number - 1]
            post = observations[round_number]
            if cross_round is not None and round_number > cross_round:
                continue
            for controller_id in handoff["controller_ids"]:
                pre_active = bool(_path_value(pre, f"state.controllers.{controller_id}"))
                post_active = bool(_path_value(post, f"state.controllers.{controller_id}"))
                supporter = handoff["source_role"]
                if handoff["controller_kinds"][controller_id] == "plate":
                    supporter_position = _path_value(pre, f"state.actors.{supporter}")
                    on_plate = list(supporter_position) == handoff["controller_positions"][controller_id]
                    if pre_active and on_plate and (cross_round is None or round_number <= cross_round):
                        useful_hold_rounds += 1
                        if _actor_action(item, supporter) == "WAIT":
                            useful_waits += 1
                if pre_active and not post_active and (cross_round is None or round_number < cross_round):
                    actuator_id = handoff["actuator_id"]
                    post_actuator = False
                    if actuator_id:
                        container = "doors" if handoff["actuator_kind"] == "door" else "platforms"
                        post_actuator = bool(_path_value(post, f"state.{container}.{actuator_id}"))
                    if not post_actuator:
                        coordination_violations.append(
                            {
                                "round": round_number,
                                "type": "premature_release",
                                "handoff_id": handoff["id"],
                                "controller_id": controller_id,
                                "responsible_agent": supporter,
                                "target_agent": handoff["target_role"],
                            }
                        )

            if (
                handoff["id"] == "capability"
                and raw_truth[handoff["actuator_node_id"]][round_number - 1]
                and not raw_truth[handoff["actuator_node_id"]][round_number]
                and (cross_round is None or round_number < cross_round)
            ):
                coordination_violations.append(
                    {
                        "round": round_number,
                        "type": "capability_regression_before_cross",
                        "handoff_id": handoff["id"],
                        "node_id": handoff["actuator_node_id"],
                        "responsible_agent": handoff["source_role"],
                        "target_agent": handoff["target_role"],
                    }
                )

            if not handoff["actuator_positions"]:
                continue
            traveler = handoff["target_role"]
            move = _actor_action(item, traveler)
            if move not in MOVE_DELTAS:
                continue
            position = _path_value(pre, f"state.actors.{traveler}")
            delta = MOVE_DELTAS[move]
            target = [int(position[0]) + delta[0], int(position[1]) + delta[1]]
            if target not in handoff["actuator_positions"]:
                continue
            actuator_id = handoff["actuator_id"]
            container = "doors" if handoff["actuator_kind"] == "door" else "platforms"
            if not bool(_path_value(pre, f"state.{container}.{actuator_id}")):
                event = {
                    "round": round_number,
                    "type": "closed_actuator_traverse_attempt",
                    "handoff_id": handoff["id"],
                    "node_id": handoff["cross_node_id"],
                    "responsible_agent": traveler,
                    "missing_dependencies": [handoff["actuator_node_id"]],
                }
                dependency_violations.append(event)
                coordination_violations.append(event)

    handoffs: list[dict[str, Any]] = []
    for definition in handoff_defs:
        related_coordination = [
            item
            for item in coordination_violations
            if item.get("handoff_id") == definition["id"]
        ]
        release_events = [
            item for item in related_coordination if item["type"] == "premature_release"
        ]
        completed_at = first_completed.get(definition["cross_node_id"])
        handoffs.append(
            {
                "id": definition["id"],
                "kind": definition["kind"],
                "source_role": definition["source_role"],
                "target_role": definition["target_role"],
                "maintain_required": any(
                    value == "plate" for value in definition["controller_kinds"].values()
                ),
                "started_at": first_completed.get(definition["actuator_node_id"]),
                "completed": completed_at is not None,
                "completed_at": completed_at,
                "premature_releases": len(release_events),
                "clean": completed_at is not None and not related_coordination,
            }
        )

    progress_values = [float(item["progress"]) for item in timeline]
    if len(progress_values) <= 1:
        progress_auc = progress_values[0] if progress_values else 0.0
    else:
        progress_auc = sum(
            (progress_values[index - 1] + progress_values[index]) / 2
            for index in range(1, len(progress_values))
        ) / (len(progress_values) - 1)

    node_results = []
    for node_id in order:
        node = nodes[node_id]
        regressions = [item["round"] for item in node_regressions if item["node_id"] == node_id]
        node_results.append(
            {
                "node_id": node_id,
                "kind": node["kind"],
                "owner": node["owner"],
                "responsible_agent": ROLE_FOR_OWNER.get(node["owner"]),
                "cross_agent_dependency": node_id in handoff_node_ids,
                "required_for_observed_path": node_id not in waived,
                "waived_by_any_join": waived.get(node_id),
                "predicate": specs[node_id],
                "first_true_at": first_true[node_id],
                "completed": node_id in completed,
                "first_completed_at": first_completed.get(node_id),
                "regression_rounds": regressions,
            }
        )

    required_scorable = set(scorable) - set(waived)
    completed_scorable = len(completed & required_scorable)
    action_denominator = max(1, 2 * len(rounds))
    handoff_successes = sum(item["completed"] for item in handoffs)
    clean_handoffs = sum(item["clean"] for item in handoffs)
    failure_analysis = None
    if trace["outcome"] != "team_success":
        first_incomplete = next(
            (node_id for node_id in order if node_id in required_scorable and node_id not in completed),
            None,
        )
        related_handoff = next(
            (
                item
                for item in handoff_defs
                if first_incomplete
                in {
                    *item["controller_node_ids"],
                    item["actuator_node_id"],
                    item["cross_node_id"],
                }
            ),
            None,
        )
        related_violations = [
            item
            for item in coordination_violations
            if related_handoff is not None and item.get("handoff_id") == related_handoff["id"]
        ]
        node = nodes[first_incomplete] if first_incomplete else None
        state = node["predicate"]["state"] if node else "unknown"
        failure_analysis = {
            "first_incomplete_required_node": first_incomplete,
            "predicate_state": state,
            "responsible_agent": ROLE_FOR_OWNER.get(node["owner"]) if node else None,
            "blocked_by_incomplete_nodes": sorted(
                incoming[first_incomplete] - completed
            ) if first_incomplete else [],
            "handoff_id": related_handoff["id"] if related_handoff else None,
            "related_coordination_violations": related_violations,
            "last_progress_round": max(first_completed.values(), default=0),
            "progress": round(completed_scorable / len(required_scorable), 6)
            if required_scorable
            else 1.0,
            "explanation": (
                f"Task stopped before required node {first_incomplete} ({state}); "
                f"related coordination violations={len(related_violations)}."
            ),
        }
    result = {
        "format": EVALUATION_FORMAT,
        "task_id": record["id"],
        "dag_id": dag["id"],
        "difficulty": record["difficulty"],
        "outcome": trace["outcome"],
        "rounds": len(rounds),
        "summary": {
            "dag_nodes_total": len(nodes),
            "dag_nodes_scorable": len(scorable),
            "dag_nodes_required_observed_path": len(required_scorable),
            "dag_nodes_completed": completed_scorable,
            "dag_completion": round(completed_scorable / len(required_scorable), 6)
            if required_scorable
            else 1.0,
            "dag_progress_auc": round(progress_auc, 6),
            "dependency_violations": len(dependency_violations),
            "dependency_violation_rate_per_agent_action": round(
                len(dependency_violations) / action_denominator, 6
            ),
            "node_regressions": len(node_regressions),
            "harmful_node_regressions": sum(item["harmful"] for item in node_regressions),
            "handoff_opportunities": len(handoffs),
            "handoff_successes": handoff_successes,
            "handoff_success_rate": round(handoff_successes / len(handoffs), 6) if handoffs else None,
            "clean_handoff_rate": round(clean_handoffs / len(handoffs), 6) if handoffs else None,
            "coordination_violations": len(coordination_violations),
            "coordination_violation_rate_per_agent_action": round(
                len(coordination_violations) / action_denominator, 6
            ),
            "useful_hold_rounds": useful_hold_rounds,
            "useful_waits": useful_waits,
            "total_waits": total_waits,
            "useful_wait_ratio": round(useful_waits / total_waits, 6) if total_waits else None,
        },
        "node_results": node_results,
        "handoffs": handoffs,
        "dependency_violations": dependency_violations,
        "coordination_violations": coordination_violations,
        "node_regressions": node_regressions,
        "failure_analysis": failure_analysis,
        "timeline": timeline,
    }
    validate_collaboration_evaluation(result)
    return result


def validate_collaboration_evaluation(value: Mapping[str, Any]) -> None:
    if value.get("format") != EVALUATION_FORMAT:
        raise ValueError("unsupported collaboration evaluation format")
    for field in ("task_id", "dag_id", "difficulty", "outcome"):
        if not isinstance(value.get(field), str) or not value[field]:
            raise ValueError(f"{field} must be a non-empty string")
    summary = value.get("summary")
    if not isinstance(summary, Mapping):
        raise ValueError("summary must be an object")
    for field in ("dag_completion", "dag_progress_auc"):
        number = summary.get(field)
        if not isinstance(number, (int, float)) or not 0 <= number <= 1:
            raise ValueError(f"summary.{field} must be between 0 and 1")
    node_results = value.get("node_results")
    timeline = value.get("timeline")
    if not isinstance(node_results, Sequence) or isinstance(node_results, (str, bytes)):
        raise ValueError("node_results must be an array")
    if not isinstance(timeline, Sequence) or isinstance(timeline, (str, bytes)):
        raise ValueError("timeline must be an array")
