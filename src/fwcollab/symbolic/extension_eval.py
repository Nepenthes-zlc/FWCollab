"""Extension-only executable evaluation for bounded synchronization and parallel join.

This module is opt-in through extension metadata. It does not alter the legacy
Full-72 evaluator in :mod:`fwcollab.symbolic.collaboration_eval`.
"""

from __future__ import annotations

from typing import Any, Mapping

from fwcollab.symbolic.dag import topological_order, validate_state_dag

EXTENSION_EVALUATION_FORMAT = "fwcollab.symbolic.extension_evaluation.v1"
EXTENSION_TYPES = {"bounded_temporal_synchronize", "parallel_join"}
ROLE_FOR_OWNER = {"agent_a": "F", "agent_b": "W"}


def _path_value(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            raise KeyError(f"observation path is missing: {path}")
        current = current[segment]
    return current


def _observations(trace: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rounds = trace.get("rounds")
    if not isinstance(rounds, list):
        raise ValueError("trace.rounds must be a list")
    if not rounds:
        final = trace.get("final_observation")
        if not isinstance(final, Mapping):
            raise ValueError("empty trace requires final_observation")
        return [final]
    initial = rounds[0].get("observation")
    if not isinstance(initial, Mapping):
        raise ValueError("trace is missing initial observation")
    values = [initial]
    for item in rounds:
        result = item.get("result")
        observation = result.get("observation") if isinstance(result, Mapping) else None
        if not isinstance(observation, Mapping):
            raise ValueError("trace round is missing result.observation")
        values.append(observation)
    return values


def validate_extension_record(record: Mapping[str, Any], dag: Mapping[str, Any]) -> None:
    validate_state_dag(dict(dag))
    semantics = record.get("extension_semantics")
    if not isinstance(semantics, Mapping) or semantics.get("type") not in EXTENSION_TYPES:
        raise ValueError("record requires an explicit supported extension_semantics.type")
    bindings = record.get("node_bindings")
    if not isinstance(bindings, list):
        raise ValueError("record.node_bindings must be a list")
    node_ids = {str(node["id"]) for node in dag["nodes"]}
    binding_ids = {str(binding.get("dag_node_id")) for binding in bindings}
    if node_ids != binding_ids or len(bindings) != len(binding_ids):
        raise ValueError("every DAG node requires exactly one executable binding")
    allowed = {"equals", "actor_column_greater_than", "temporal_overlap"}
    for binding in bindings:
        if binding.get("kind") not in allowed:
            raise ValueError(f"unsupported extension binding: {binding!r}")
    if semantics["type"] == "bounded_temporal_synchronize":
        if record.get("suite") != "Sync-8":
            raise ValueError("bounded temporal synchronization is opt-in for Sync-8 only")
        sync_id = str(semantics.get("synchronization_node_id"))
        sync_binding = next((item for item in bindings if item["dag_node_id"] == sync_id), None)
        if sync_binding is None or sync_binding.get("kind") != "temporal_overlap":
            raise ValueError("synchronization node requires temporal_overlap binding")
        if int(sync_binding.get("minimum_overlap_rounds", 0)) < 1:
            raise ValueError("minimum_overlap_rounds must be positive")
        if sync_binding.get("source_agents") != ["F", "W"]:
            raise ValueError("temporal synchronization requires distinct F/W source agents")
        if int(semantics.get("minimum_overlap_rounds", 0)) != int(sync_binding["minimum_overlap_rounds"]):
            raise ValueError("synchronization metadata and binding disagree on overlap duration")
        source_ids = semantics.get("source_condition_node_ids")
        if not isinstance(source_ids, Mapping) or set(source_ids) != {"F", "W"}:
            raise ValueError("synchronization requires F/W source condition nodes")
        nodes = {str(node["id"]): node for node in dag["nodes"]}
        owners = {role: nodes.get(str(node_id), {}).get("owner") for role, node_id in source_ids.items()}
        if owners != {"F": "agent_a", "W": "agent_b"}:
            raise ValueError("synchronization source conditions must be owned by F and W")
        source_bindings = {str(item["dag_node_id"]): item for item in bindings}
        condition_paths = [
            source_bindings[str(source_ids[role])].get("observation_path") for role in ("F", "W")
        ]
        if condition_paths != list(sync_binding.get("condition_paths", [])):
            raise ValueError("temporal overlap paths must be the two source-condition bindings")
    else:
        if record.get("suite") != "Join-8":
            raise ValueError("parallel join semantics are opt-in for Join-8 only")
        branches = semantics.get("branch_node_ids")
        if not isinstance(branches, Mapping) or set(branches) != {"F", "W"}:
            raise ValueError("parallel join requires F/W branch_node_ids")
        nodes = {str(node["id"]): node for node in dag["nodes"]}
        join_id = str(semantics.get("join_node_id"))
        if nodes.get(join_id, {}).get("join") != "all":
            raise ValueError("parallel join node must use join=all")
        owners = {role: nodes.get(str(node_id), {}).get("owner") for role, node_id in branches.items()}
        if owners != {"F": "agent_a", "W": "agent_b"}:
            raise ValueError("parallel branches must be owned by their respective roles")
        incoming = {str(edge["from"]) for edge in dag["edges"] if str(edge["to"]) == join_id}
        if incoming != {str(branches["F"]), str(branches["W"])}:
            raise ValueError("parallel join must directly require both role-owned branches")
        if semantics.get("join_mode") != "all" or semantics.get("requires_temporal_overlap") is not False:
            raise ValueError("parallel join requires join=all without temporal overlap")


def _binding_truth(binding: Mapping[str, Any], observations: list[Mapping[str, Any]]) -> list[bool]:
    kind = binding["kind"]
    if kind == "equals":
        return [
            _path_value(observation, str(binding["observation_path"])) == binding.get("expected_value")
            for observation in observations
        ]
    if kind == "actor_column_greater_than":
        role, column = str(binding["role"]), int(binding["column"])
        return [
            bool(_path_value(observation, f"state.alive.{role}"))
            and int(_path_value(observation, f"state.actors.{role}")[1]) > column
            for observation in observations
        ]
    if kind == "temporal_overlap":
        paths = list(binding["condition_paths"])
        minimum = int(binding["minimum_overlap_rounds"])
        streak = 0
        values: list[bool] = []
        for observation in observations:
            overlap = all(bool(_path_value(observation, str(path))) for path in paths)
            streak = streak + 1 if overlap else 0
            values.append(streak >= minimum)
        return values
    raise ValueError(f"unsupported extension binding kind: {kind}")


def evaluate_extension_trace(
    record: Mapping[str, Any], dag: Mapping[str, Any], trace: Mapping[str, Any]
) -> dict[str, Any]:
    validate_extension_record(record, dag)
    if trace.get("map_id") != record.get("id"):
        raise ValueError("trace and extension record refer to different maps")
    nodes = {str(node["id"]): node for node in dag["nodes"]}
    bindings = {str(item["dag_node_id"]): item for item in record["node_bindings"]}
    observations = _observations(trace)
    raw_truth = {node_id: _binding_truth(bindings[node_id], observations) for node_id in nodes}
    incoming = {node_id: set() for node_id in nodes}
    for edge in dag["edges"]:
        incoming[str(edge["to"])].add(str(edge["from"]))
    order = topological_order(dag)
    completed: set[str] = set()
    first_completed: dict[str, int] = {}
    violations: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []
    for snapshot in range(len(observations)):
        newly: list[str] = []
        for node_id in order:
            node = nodes[node_id]
            predecessors = incoming[node_id]
            ready = (
                not predecessors
                or (node["join"] == "all" and predecessors <= completed)
                or (node["join"] == "any" and bool(predecessors & completed))
            )
            truth = raw_truth[node_id][snapshot]
            rising = truth and (snapshot == 0 or not raw_truth[node_id][snapshot - 1])
            if truth and ready and node_id not in completed:
                completed.add(node_id)
                first_completed[node_id] = snapshot
                newly.append(node_id)
            elif rising and not ready and node["owner"] in ROLE_FOR_OWNER:
                violations.append(
                    {
                        "snapshot": snapshot,
                        "type": "predicate_before_dependencies",
                        "node_id": node_id,
                        "responsible_agent": ROLE_FOR_OWNER[node["owner"]],
                        "missing_dependencies": sorted(predecessors - completed),
                    }
                )
        timeline.append({"snapshot": snapshot, "newly_completed_nodes": newly, "completed_nodes": sorted(completed)})
    persistent_violations: list[dict[str, Any]] = []
    for edge in dag["edges"]:
        if edge["relation"] != "maintains":
            continue
        source, target = str(edge["from"]), str(edge["to"])
        source_at = first_completed.get(source)
        if source_at is None:
            continue
        target_at = first_completed.get(target, len(observations))
        regression_at = next(
            (snapshot for snapshot in range(source_at, target_at) if not raw_truth[source][snapshot]),
            None,
        )
        if regression_at is not None:
            persistent_violations.append(
                {
                    "snapshot": regression_at,
                    "type": "persistent_condition_released_early",
                    "node_id": source,
                    "required_until_node": target,
                    "responsible_agent": ROLE_FOR_OWNER.get(nodes[source]["owner"]),
                }
            )
    violations.extend(persistent_violations)
    scorable = {node_id for node_id, node in nodes.items() if node["predicate"]["op"] != "start"}
    complete = len(completed & scorable) == len(scorable)
    valid = trace.get("outcome") == "team_success" and complete and not violations
    first_incomplete = next((node_id for node_id in order if node_id in scorable and node_id not in completed), None)
    return {
        "format": EXTENSION_EVALUATION_FORMAT,
        "task_id": record["id"],
        "suite": record["suite"],
        "outcome": trace.get("outcome"),
        "valid": valid,
        "dag_completion": len(completed & scorable) / len(scorable) if scorable else 1.0,
        "completed_nodes": sorted(completed),
        "first_completed_at": first_completed,
        "dependency_violations": violations,
        "persistent_condition_violations": persistent_violations,
        "first_incomplete_required_node": first_incomplete,
        "timeline": timeline,
    }
