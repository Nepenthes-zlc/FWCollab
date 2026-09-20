from __future__ import annotations

import copy

from fwcollab.symbolic.collaboration_eval import collaboration_taxonomy, evaluate_collaboration_trace


def _dag():
    return {
        "format": "fwcollab.symbolic.state_dag.v1",
        "id": "DAG-TEST",
        "difficulty": "L1",
        "evaluation_role": "diagnostic",
        "visibility": "private_reference",
        "title": "test",
        "summary": "test",
        "topology_family": "test",
        "mechanisms": ["M11"],
        "nodes": [
            {"id": "s", "kind": "condition", "owner": "team", "join": "all", "predicate": {"op": "start", "motif": "SYSTEM", "state": "task_ready", "phase": 0}, "label": "start"},
            {"id": "p", "kind": "condition", "owner": "agent_a", "join": "all", "predicate": {"op": "occupy", "motif": "M11", "state": "c1_plate_on", "phase": 1}, "label": "plate"},
            {"id": "d", "kind": "state", "owner": "environment", "join": "all", "predicate": {"op": "activate", "motif": "M11", "state": "a1_door_on", "phase": 2}, "label": "door"},
            {"id": "x", "kind": "event", "owner": "agent_b", "join": "all", "predicate": {"op": "traverse", "motif": "M11", "state": "a1_door_crossed", "phase": 3}, "label": "cross"},
            {"id": "g", "kind": "goal", "owner": "team", "join": "all", "predicate": {"op": "team_success", "motif": "SYSTEM", "state": "all_required_goals", "phase": 4}, "label": "goal"},
        ],
        "edges": [
            {"from": "s", "to": "p", "relation": "requires"},
            {"from": "p", "to": "d", "relation": "enables"},
            {"from": "d", "to": "x", "relation": "enables"},
            {"from": "x", "to": "g", "relation": "synchronizes"},
        ],
    }


def _record():
    return {
        "id": "TEST",
        "difficulty": "L1",
        "primary_capability": "M03",
        "capability": {"kind": "terrain", "traveler": "W", "supporter": "F", "gate_col": 9},
        "stages": [{
            "stage": 1,
            "supporter": "F",
            "traveler": "W",
            "controllers": [{"id": "c1_plate", "kind": "plate", "position": [1, 1]}],
            "actuator": {"id": "a1_door", "kind": "door", "positions": [[1, 2]]},
            "mode": "all",
            "room": {"gate_col": 2, "obstacle_pattern": 0},
        }],
        "node_bindings": [
            {"dag_node_id": "s", "observation_path": "status", "expected_value": "running"},
            {"dag_node_id": "p", "observation_path": "state.controllers.c1_plate", "expected_value": True},
            {"dag_node_id": "d", "observation_path": "state.doors.a1_door", "expected_value": True},
            {"dag_node_id": "x", "observation_path": "state.actors.W", "expected_value": [1, 3]},
            {"dag_node_id": "g", "observation_path": "status", "expected_value": "team_success"},
        ],
    }


def _observation(*, plate: bool, door: bool, water: list[int], status: str = "running"):
    return {
        "status": status,
        "state": {
            "actors": {"F": [1, 1], "W": water},
            "alive": {"F": True, "W": True},
            "controllers": {"c1_plate": plate},
            "doors": {"a1_door": door},
        },
    }


def _round(number, pre, post, *, fire="WAIT", water="WAIT"):
    def actor(move):
        return {"decision": {"action": {"move": move, "steps": 0 if move == "WAIT" else 1}}}
    return {
        "round": number,
        "observation": pre,
        "agents": {"F": actor(fire), "W": actor(water)},
        "result": {"observation": post},
    }


def test_successful_hold_and_pass_is_scored_without_language_judging():
    o0 = _observation(plate=False, door=False, water=[1, 1])
    o1 = _observation(plate=True, door=True, water=[1, 1])
    o2 = _observation(plate=True, door=True, water=[1, 3])
    o3 = _observation(plate=False, door=False, water=[1, 3], status="team_success")
    trace = {
        "map_id": "TEST",
        "outcome": "team_success",
        "rounds": [
            _round(1, o0, o1, fire="WAIT"),
            _round(2, o1, o2, fire="WAIT", water="RIGHT"),
            _round(3, o2, o3),
        ],
    }
    result = evaluate_collaboration_trace(_record(), _dag(), trace)
    assert result["summary"]["dag_completion"] == 1.0
    assert result["failure_analysis"] is None
    assert result["summary"]["handoff_success_rate"] == 1.0
    assert result["summary"]["coordination_violations"] == 0
    assert result["summary"]["harmful_node_regressions"] == 0
    assert result["summary"]["useful_waits"] == 1
    assert result["handoffs"][0]["clean"] is True
    assert next(item for item in result["node_results"] if item["node_id"] == "x")["first_completed_at"] == 2


def test_premature_release_and_closed_gate_attempt_are_objective_violations():
    o0 = _observation(plate=False, door=False, water=[1, 1])
    o1 = _observation(plate=True, door=True, water=[1, 1])
    o2 = _observation(plate=False, door=False, water=[1, 1])
    trace = {
        "map_id": "TEST",
        "outcome": "timeout",
        "rounds": [
            _round(1, o0, o1),
            _round(2, o1, o2, fire="LEFT"),
            _round(3, o2, o2, water="RIGHT"),
        ],
    }
    result = evaluate_collaboration_trace(_record(), _dag(), trace)
    violation_types = {item["type"] for item in result["coordination_violations"]}
    assert violation_types == {"premature_release", "closed_actuator_traverse_attempt"}
    assert result["summary"]["dependency_violations"] == 1
    assert result["summary"]["handoff_success_rate"] == 0.0
    assert result["summary"]["dag_completion"] == 0.5
    assert result["failure_analysis"]["first_incomplete_required_node"] == "x"
    assert result["failure_analysis"]["handoff_id"] == "stage_1"
    assert len(result["failure_analysis"]["related_coordination_violations"]) == 2


def test_unselected_any_join_branch_is_waived_for_observed_path():
    dag = copy.deepcopy(_dag())
    dag["nodes"].insert(
        2,
        {"id": "p2", "kind": "event", "owner": "agent_a", "join": "all", "predicate": {"op": "latch", "motif": "M17", "state": "c2_lever_on", "phase": 1}, "label": "alternative"},
    )
    dag["mechanisms"].append("M17")
    dag["edges"].insert(1, {"from": "s", "to": "p2", "relation": "requires"})
    dag["edges"].insert(3, {"from": "p2", "to": "d", "relation": "enables"})
    next(node for node in dag["nodes"] if node["id"] == "d")["join"] = "any"
    record = copy.deepcopy(_record())
    record["node_bindings"].insert(
        2,
        {"dag_node_id": "p2", "observation_path": "state.controllers.c2_lever", "expected_value": True},
    )
    o0 = _observation(plate=False, door=False, water=[1, 1])
    o1 = _observation(plate=True, door=True, water=[1, 1])
    o2 = _observation(plate=True, door=True, water=[1, 3])
    o3 = _observation(plate=False, door=False, water=[1, 3], status="team_success")
    for observation in (o0, o1, o2, o3):
        observation["state"]["controllers"]["c2_lever"] = False
    trace = {
        "map_id": "TEST",
        "outcome": "team_success",
        "rounds": [
            _round(1, o0, o1),
            _round(2, o1, o2, water="RIGHT"),
            _round(3, o2, o3),
        ],
    }
    result = evaluate_collaboration_trace(record, dag, trace)
    alternative = next(item for item in result["node_results"] if item["node_id"] == "p2")
    assert result["summary"]["dag_completion"] == 1.0
    assert result["summary"]["dag_nodes_required_observed_path"] == 4
    assert alternative["completed"] is False
    assert alternative["required_for_observed_path"] is False
    assert alternative["waived_by_any_join"]["waived_by"] == "d"


def test_taxonomy_does_not_invent_unimplemented_collaboration_structures():
    taxonomy = collaboration_taxonomy(_record(), _dag())
    assert taxonomy["tags"] == ["C1_hold_and_pass"]
    assert "C4_synchronized_action" in taxonomy["coverage_gaps"]
    assert "C5_information_dependent" in taxonomy["coverage_gaps"]
    assert "C7_parallel_subgoal_merge" in taxonomy["coverage_gaps"]
