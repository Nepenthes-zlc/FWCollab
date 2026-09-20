"""Deterministic construction and audit of Sync-8 and ParallelJoin-8."""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
from collections import deque
from pathlib import Path
from typing import Any, Mapping, Sequence

from fwcollab.symbolic.agents import AgentDecision, ScriptedPolicy
from fwcollab.symbolic.dag import graph_metrics, save_json, validate_state_dag
from fwcollab.symbolic.extension_eval import evaluate_extension_trace, validate_extension_record
from fwcollab.symbolic.map import Position, SymbolMap, parse_symbol_map
from fwcollab.symbolic.runner import DualAgentSession, replay_trace
from fwcollab.symbolic.world import DIRECTIONS, Role, SymbolAction, SymbolWorld

SYNC_CONFIGS = (
    {"id": "SYNC-01", "variant": "primitive_symmetric_door", "tier": "primitive", "K": 1, "actuator": "door", "starts": {"F": 1, "W": 1}},
    {"id": "SYNC-02", "variant": "primitive_symmetric_bridge", "tier": "primitive", "K": 1, "actuator": "platform", "starts": {"F": 2, "W": 2}},
    {"id": "SYNC-03", "variant": "asymmetric_F_long", "tier": "medium", "K": 1, "actuator": "door", "starts": {"F": 1, "W": 5}},
    {"id": "SYNC-04", "variant": "asymmetric_W_long_K2", "tier": "medium", "K": 2, "actuator": "platform", "starts": {"F": 5, "W": 1}},
    {"id": "SYNC-05", "variant": "bounded_overlap_K3", "tier": "medium", "K": 3, "actuator": "door", "starts": {"F": 2, "W": 4}},
    {"id": "SYNC-06", "variant": "enable_W_to_F_then_sync", "tier": "composition", "K": 2, "actuator": "door", "starts": {"F": 1, "W": 1}, "prefix": {"semantic": "ENABLE", "supporter": "W"}},
    {"id": "SYNC-07", "variant": "maintain_W_to_F_then_sync", "tier": "composition", "K": 2, "actuator": "platform", "starts": {"F": 1, "W": 1}, "prefix": {"semantic": "MAINTAIN", "supporter": "W"}},
    {"id": "SYNC-08", "variant": "enable_F_to_W_then_sync_K3", "tier": "composition", "K": 3, "actuator": "door", "starts": {"F": 1, "W": 1}, "prefix": {"semantic": "ENABLE", "supporter": "F"}},
)

JOIN_CONFIGS = (
    {"id": "JOIN-01", "variant": "primitive_symmetric_levers", "tier": "primitive", "actuator": "door", "branch_kinds": {"F": "lever", "W": "lever"}, "branch_cols": {"F": 4, "W": 4}},
    {"id": "JOIN-02", "variant": "primitive_symmetric_toggles", "tier": "primitive", "actuator": "platform", "branch_kinds": {"F": "toggle", "W": "toggle"}, "branch_cols": {"F": 4, "W": 4}},
    {"id": "JOIN-03", "variant": "asymmetric_lever_toggle", "tier": "asymmetric", "actuator": "door", "branch_kinds": {"F": "lever", "W": "toggle"}, "branch_cols": {"F": 3, "W": 5}},
    {"id": "JOIN-04", "variant": "asymmetric_path_lengths", "tier": "asymmetric", "actuator": "platform", "branch_kinds": {"F": "toggle", "W": "lever"}, "branch_cols": {"F": 6, "W": 2}},
    {"id": "JOIN-05", "variant": "asymmetric_reversed_mechanisms", "tier": "asymmetric", "actuator": "door", "branch_kinds": {"F": "toggle", "W": "lever"}, "branch_cols": {"F": 2, "W": 6}},
    {"id": "JOIN-06", "variant": "join_then_maintain_F_to_W", "tier": "composition", "actuator": "door", "branch_kinds": {"F": "lever", "W": "toggle"}, "branch_cols": {"F": 3, "W": 4}, "final_supporter": "F"},
    {"id": "JOIN-07", "variant": "join_then_maintain_W_to_F", "tier": "composition", "actuator": "platform", "branch_kinds": {"F": "toggle", "W": "lever"}, "branch_cols": {"F": 4, "W": 3}, "final_supporter": "W"},
    {"id": "JOIN-08", "variant": "asymmetric_join_then_maintain", "tier": "composition", "actuator": "door", "branch_kinds": {"F": "lever", "W": "toggle"}, "branch_cols": {"F": 6, "W": 2}, "final_supporter": "F"},
)

ROWS = {"F": 1, "W": 3}
OTHER = {"F": "W", "W": "F"}
WIDTH = 21
EXIT_COL = WIDTH - 2
JOIN_GATE_COL = 10
SYNC_GATE_COL = 11
SYNC_PLATE_COL = SYNC_GATE_COL - 1
FINAL_PLATE_COL = 14
FINAL_GATE_COL = 15

FROZEN_BASELINES = {
    "eval_private/spatial_curriculum_full_v4/manifest.json": "1d7e4240da3fff204fb820f0704501564c05cfa5dc0630644d10382df57c19d0",
    "eval_private/spatial_curriculum_full_v4/witnesses.json": "19827795fc4d3ebe94d075e335764b3b1f663461cb339b81d01f5d915c867298",
    "eval_private/c5_information_12/manifest.json": "cc3861f656c37b4447d8ddd913856923f4d3510f4f649b43432b0319f4197721",
    "eval_private/collaboration_diagnostic_24/manifest.json": "e3f0af67a3b123d0d75f816e760f809d8d6d649bd247868e009ca258eb616eda",
    "eval_private/phase2_v1/freeze_manifest.json": "29b93111afd874cda37e848bacd184da4b799bde8faf4a0912dfd27f008830d6",
    "artifacts/evaluations/phase2_v1/summary.json": "cccf2137248183b1d4d84b3a4f0a73ca9e09b20c8c88fa20d7c80d628524822f",
    "artifacts/evaluations/evaluator_conformance_v1/summary.json": "81a05fd07273e97f125848ebe1461aa8e5b1df114ff21065df8faf17b7b1ea2b",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _blank_grid(starts: Mapping[str, int]) -> list[list[str]]:
    grid = [list("#" * WIDTH) for _ in range(5)]
    for role, row in ROWS.items():
        grid[row] = list("#" + "." * (WIDTH - 2) + "#")
        grid[row][int(starts[role])] = role
        grid[row][EXIT_COL] = role.lower()
    return grid


def _controller_line(identifier: str, kind: str, role: str, position: tuple[int, int]) -> str:
    tail = f" accepts {role}" if kind == "plate" else ""
    return f"@controller {identifier} {kind} at {position[0]},{position[1]}{tail}"


def _glyph(kind: str, ordinal: int = 1) -> str:
    return str(ordinal) if kind == "plate" else "L" if kind == "lever" else "T"


def _actuator_glyph(kind: str, final: bool = False) -> str:
    return "=" if kind == "platform" else "B" if final else "A"


def _controller_actuator_motif(controller_kind: str, actuator_kind: str) -> str:
    return {
        ("plate", "door"): "M11",
        ("plate", "platform"): "M14",
        ("lever", "door"): "M17",
        ("lever", "platform"): "M18",
        ("toggle", "door"): "M19",
        ("toggle", "platform"): "M20",
    }[(controller_kind, actuator_kind)]


def _map_text(task_id: str, title: str, grid: list[list[str]], directives: Sequence[str]) -> str:
    header = [
        "@format fwcollab.symbol_map.v3", f"@id {task_id}", f"@title {title}", "@max_steps 1",
        *directives, "---",
    ]
    return "\n".join([*header, *("".join(row) for row in grid), ""])


def _node(node_id: str, kind: str, owner: str, op: str, motif: str, state: str, phase: int, *, join: str = "all") -> dict[str, Any]:
    return {"id": node_id, "kind": kind, "owner": owner, "join": join, "predicate": {"op": op, "motif": motif, "state": state, "phase": phase}, "label": f"{op}:{state}"}


def _edge(source: str, target: str, relation: str = "requires") -> dict[str, str]:
    return {"from": source, "to": target, "relation": relation}


def _equals(node_id: str, path: str, value: Any) -> dict[str, Any]:
    return {"dag_node_id": node_id, "kind": "equals", "observation_path": path, "expected_value": value}


def _cross(node_id: str, role: str, column: int) -> dict[str, Any]:
    return {"dag_node_id": node_id, "kind": "actor_column_greater_than", "role": role, "column": column}


def _sync_asset(config: Mapping[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    task_id = str(config["id"])
    grid = _blank_grid(config["starts"])
    directives: list[str] = []
    controllers: list[dict[str, Any]] = []
    prefix = config.get("prefix")
    if isinstance(prefix, Mapping):
        supporter = str(prefix["supporter"])
        traveler = OTHER[supporter]
        semantic = str(prefix["semantic"])
        kind = "lever" if semantic == "ENABLE" else "plate"
        identifier = "prefix_controller"
        pos = (ROWS[supporter], 3)
        grid[pos[0]][pos[1]] = _glyph(kind, 3)
        directives.append(_controller_line(identifier, kind, supporter, pos))
        gate_pos = (ROWS[traveler], 6)
        grid[gate_pos[0]][gate_pos[1]] = "B"
        directives.append(f"@actuator prefix_gate door at {gate_pos[0]},{gate_pos[1]} controlled_by all({identifier})")
        controllers.append({"id": identifier, "kind": kind, "role": supporter, "position": list(pos)})
    for ordinal, role in enumerate(("F", "W"), start=1):
        identifier = f"sync_{role.lower()}"
        pos = (ROWS[role], SYNC_PLATE_COL)
        grid[pos[0]][pos[1]] = str(ordinal)
        directives.append(_controller_line(identifier, "plate", role, pos))
        controllers.append({"id": identifier, "kind": "plate", "role": role, "position": list(pos)})
    actuator_kind = str(config["actuator"])
    glyph = _actuator_glyph(actuator_kind)
    gate_positions = [(ROWS[role], SYNC_GATE_COL) for role in ("F", "W")]
    for row, col in gate_positions:
        grid[row][col] = glyph
    pos_text = ";".join(f"{row},{col}" for row, col in gate_positions)
    directives.append(f"@actuator sync_gate {actuator_kind} at {pos_text} controlled_by all(sync_f,sync_w)")
    text = _map_text(task_id, str(config["variant"]), grid, directives)

    nodes = [_node("s", "condition", "team", "start", "SYSTEM", "task_ready", 0)]
    edges: list[dict[str, str]] = []
    bindings = [_equals("s", "status", "running")]
    sync_motif = _controller_actuator_motif("plate", actuator_kind)
    mechanisms = {sync_motif}
    phase = 1
    f_predecessor = "s"
    w_predecessor = "s"
    if isinstance(prefix, Mapping):
        supporter, semantic = str(prefix["supporter"]), str(prefix["semantic"])
        traveler = OTHER[supporter]
        kind = "lever" if semantic == "ENABLE" else "plate"
        motif = _controller_actuator_motif(kind, "door")
        mechanisms.add(motif)
        owner = "agent_a" if supporter == "F" else "agent_b"
        traveler_owner = "agent_a" if traveler == "F" else "agent_b"
        nodes.extend([
            _node("pre_c", "condition" if kind == "plate" else "event", owner, "occupy" if kind == "plate" else "latch", motif, "prefix_controller_on", phase),
            _node("pre_a", "state", "environment", "activate", motif, "prefix_gate_on", phase + 1),
            _node("pre_x", "event", traveler_owner, "traverse", motif, "prefix_gate_crossed", phase + 2),
        ])
        edges.extend([_edge("s", "pre_c"), _edge("pre_c", "pre_a", "maintains" if semantic == "MAINTAIN" else "enables"), _edge("pre_a", "pre_x", "enables")])
        if semantic == "MAINTAIN":
            edges.append(_edge("pre_c", "pre_x", "maintains"))
        bindings.extend([_equals("pre_c", "state.controllers.prefix_controller", True), _equals("pre_a", "state.doors.prefix_gate", True), _cross("pre_x", traveler, 6)])
        if traveler == "F":
            f_predecessor = "pre_x"
        else:
            w_predecessor = "pre_x"
        phase += 3
    nodes.extend([
        _node("cf", "condition", "agent_a", "occupy", sync_motif, "sync_f_on", phase),
        _node("cw", "condition", "agent_b", "occupy", sync_motif, "sync_w_on", phase),
        _node("sync", "state", "environment", "synchronize_window", sync_motif, "bounded_overlap_met", phase + 1),
        _node("gate", "state", "environment", "activate", sync_motif, "sync_gate_on", phase + 2),
        _node("xf", "event", "agent_a", "traverse", sync_motif, "sync_gate_F_crossed", phase + 3),
        _node("xw", "event", "agent_b", "traverse", sync_motif, "sync_gate_W_crossed", phase + 3),
        _node("g", "goal", "team", "team_success", "SYSTEM", "all_required_goals", phase + 4),
    ])
    edges.extend([
        _edge(f_predecessor, "cf"), _edge(w_predecessor, "cw"),
        _edge("cf", "sync", "synchronizes"), _edge("cw", "sync", "synchronizes"),
        _edge("sync", "gate", "enables"), _edge("gate", "xf", "enables"), _edge("gate", "xw", "enables"),
        _edge("xf", "g", "requires"), _edge("xw", "g", "requires"),
    ])
    container = "doors" if actuator_kind == "door" else "platforms"
    bindings.extend([
        _equals("cf", "state.controllers.sync_f", True), _equals("cw", "state.controllers.sync_w", True),
        {"dag_node_id": "sync", "kind": "temporal_overlap", "source_agents": ["F", "W"], "condition_paths": ["state.controllers.sync_f", "state.controllers.sync_w"], "minimum_overlap_rounds": int(config["K"])},
        _equals("gate", f"state.{container}.sync_gate", True), _cross("xf", "F", SYNC_GATE_COL), _cross("xw", "W", SYNC_GATE_COL), _equals("g", "status", "team_success"),
    ])
    dag = {"format": "fwcollab.symbolic.state_dag.v1", "id": f"DAG-{task_id}", "difficulty": "L2" if config["tier"] == "primitive" else "L3" if config["tier"] == "medium" else "L4", "evaluation_role": "diagnostic", "visibility": "private_reference", "title": str(config["variant"]), "summary": "Bounded temporal synchronization extension DAG.", "topology_family": f"sync_{config['tier']}", "mechanisms": sorted(mechanisms), "nodes": nodes, "edges": edges}
    record = {
        "id": task_id, "suite": "Sync-8", "variant": config["variant"], "tier": config["tier"], "mechanisms": sorted(mechanisms),
        "map": f"eval_private/synchronize_8/maps/{task_id}.fwmap", "dag": f"eval_private/synchronize_8/dags/DAG-{task_id}.json",
        "source_agents": ["F", "W"], "dependency_semantics": [*( [str(prefix["semantic"])] if isinstance(prefix, Mapping) else []), "SYNCHRONIZE"], "composition_motifs": [] if config["tier"] == "primitive" else ["ASYMMETRIC_ARRIVAL"] if config["tier"] == "medium" else ["SEQUENTIAL_COMPOSITION"],
        "extension_semantics": {"type": "bounded_temporal_synchronize", "synchronization_node_id": "sync", "source_condition_node_ids": {"F": "cf", "W": "cw"}, "minimum_overlap_rounds": int(config["K"]), "requires_live_overlap": True},
        "node_bindings": bindings, "controllers": controllers, "gate": {"id": "sync_gate", "kind": actuator_kind, "column": SYNC_GATE_COL, "positions": [list(item) for item in gate_positions]},
        "edge_evidence": [{"from": edge["from"], "to": edge["to"], "verified": True, "method": "explicit_temporal_overlap_binding" if edge["to"] == "sync" else "single_lane_corridor_cut" if edge["from"] == "gate" else "executable_precedence"} for edge in edges],
    }
    return text, dag, record


def _join_asset(config: Mapping[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    task_id = str(config["id"])
    starts = {"F": 1, "W": 1}
    grid = _blank_grid(starts)
    directives: list[str] = []
    branch_ids: dict[str, str] = {}
    mechanisms: set[str] = set()
    for role in ("F", "W"):
        kind = str(config["branch_kinds"][role])
        identifier = f"branch_{role.lower()}"
        branch_ids[role] = identifier
        col = int(config["branch_cols"][role])
        grid[ROWS[role]][col] = _glyph(kind)
        directives.append(_controller_line(identifier, kind, role, (ROWS[role], col)))
        mechanisms.add(_controller_actuator_motif(kind, str(config["actuator"])))
    actuator_kind = str(config["actuator"])
    gate_positions = [(ROWS[role], JOIN_GATE_COL) for role in ("F", "W")]
    glyph = _actuator_glyph(actuator_kind)
    for row, col in gate_positions:
        grid[row][col] = glyph
    pos_text = ";".join(f"{row},{col}" for row, col in gate_positions)
    directives.append(f"@actuator join_gate {actuator_kind} at {pos_text} controlled_by all(branch_f,branch_w)")
    final_supporter = config.get("final_supporter")
    if final_supporter:
        supporter, traveler = str(final_supporter), OTHER[str(final_supporter)]
        grid[ROWS[supporter]][FINAL_PLATE_COL] = "3"
        directives.append(_controller_line("final_hold", "plate", supporter, (ROWS[supporter], FINAL_PLATE_COL)))
        grid[ROWS[traveler]][FINAL_GATE_COL] = "B"
        directives.append(f"@actuator final_gate door at {ROWS[traveler]},{FINAL_GATE_COL} controlled_by all(final_hold)")
        mechanisms.add("M11")
    text = _map_text(task_id, str(config["variant"]), grid, directives)

    motif_f = _controller_actuator_motif(str(config["branch_kinds"]["F"]), actuator_kind)
    motif_w = _controller_actuator_motif(str(config["branch_kinds"]["W"]), actuator_kind)
    nodes = [
        _node("s", "condition", "team", "start", "SYSTEM", "task_ready", 0),
        _node("bf", "event", "agent_a", "latch", motif_f, "branch_f_on", 1),
        _node("bw", "event", "agent_b", "latch", motif_w, "branch_w_on", 1),
        _node("join", "state", "environment", "activate", motif_f, "join_gate_on", 2, join="all"),
        _node("xf", "event", "agent_a", "traverse", motif_f, "join_gate_F_crossed", 3),
        _node("xw", "event", "agent_b", "traverse", motif_w, "join_gate_W_crossed", 3),
    ]
    edges = [_edge("s", "bf"), _edge("s", "bw"), _edge("bf", "join", "enables"), _edge("bw", "join", "enables"), _edge("join", "xf", "enables"), _edge("join", "xw", "enables")]
    container = "doors" if actuator_kind == "door" else "platforms"
    bindings = [_equals("s", "status", "running"), _equals("bf", "state.controllers.branch_f", True), _equals("bw", "state.controllers.branch_w", True), _equals("join", f"state.{container}.join_gate", True), _cross("xf", "F", JOIN_GATE_COL), _cross("xw", "W", JOIN_GATE_COL)]
    phase = 4
    goal_predecessors = ["xf", "xw"]
    if final_supporter:
        supporter, traveler = str(final_supporter), OTHER[str(final_supporter)]
        owner = "agent_a" if supporter == "F" else "agent_b"
        traveler_owner = "agent_a" if traveler == "F" else "agent_b"
        supporter_cross = "xf" if supporter == "F" else "xw"
        other_cross = "xw" if supporter == "F" else "xf"
        nodes.extend([
            _node("hold", "condition", owner, "occupy", "M11", "final_hold_on", phase),
            _node("final_gate", "state", "environment", "activate", "M11", "final_gate_on", phase + 1),
            _node("final_x", "event", traveler_owner, "traverse", "M11", "final_gate_crossed", phase + 2),
        ])
        edges.extend([_edge(supporter_cross, "hold"), _edge("hold", "final_gate", "maintains"), _edge("final_gate", "final_x", "enables"), _edge("hold", "final_x", "maintains")])
        bindings.extend([_equals("hold", "state.controllers.final_hold", True), _equals("final_gate", "state.doors.final_gate", True), _cross("final_x", traveler, FINAL_GATE_COL)])
        goal_predecessors = ["final_x", other_cross]
        phase += 3
    nodes.append(_node("g", "goal", "team", "team_success", "SYSTEM", "all_required_goals", phase))
    edges.extend(_edge(source, "g") for source in goal_predecessors)
    bindings.append(_equals("g", "status", "team_success"))
    dag = {"format": "fwcollab.symbolic.state_dag.v1", "id": f"DAG-{task_id}", "difficulty": "L2" if config["tier"] == "primitive" else "L3" if config["tier"] == "asymmetric" else "L4", "evaluation_role": "diagnostic", "visibility": "private_reference", "title": str(config["variant"]), "summary": "Role-owned Parallel Join extension DAG.", "topology_family": f"parallel_join_{config['tier']}", "mechanisms": sorted(mechanisms), "nodes": nodes, "edges": edges}
    record = {
        "id": task_id, "suite": "Join-8", "variant": config["variant"], "tier": config["tier"], "mechanisms": sorted(mechanisms),
        "map": f"eval_private/parallel_join_8/maps/{task_id}.fwmap", "dag": f"eval_private/parallel_join_8/dags/DAG-{task_id}.json",
        "source_agents": ["F", "W"], "dependency_semantics": [*( ["MAINTAIN"] if final_supporter else [])], "composition_motifs": ["PARALLEL_JOIN", *( ["JOIN_THEN_MAINTAIN"] if final_supporter else [])],
        "extension_semantics": {"type": "parallel_join", "branch_node_ids": {"F": "bf", "W": "bw"}, "join_node_id": "join", "join_mode": "all", "requires_temporal_overlap": False, "order_invariant": True},
        "node_bindings": bindings, "gate": {"id": "join_gate", "kind": actuator_kind, "column": JOIN_GATE_COL, "positions": [list(item) for item in gate_positions]}, "final_supporter": final_supporter,
        "edge_evidence": [{"from": edge["from"], "to": edge["to"], "verified": True, "method": "boolean_truth_table_both_inputs_necessary" if edge["to"] == "join" else "single_lane_corridor_cut" if edge["from"] in {"join", "final_gate"} else "executable_precedence"} for edge in edges],
    }
    return text, dag, record


def _path(world: SymbolWorld, role: Role, target: Position) -> list[str]:
    start = world.actors[role]
    queue = deque([start])
    previous: dict[Position, tuple[Position, str]] = {}
    visited = {start}
    doors = world.door_states()
    while queue:
        position = queue.popleft()
        if position == target:
            result: list[str] = []
            while position != start:
                position, move = previous[position]
                result.append(move)
            return list(reversed(result))
        for move, delta in DIRECTIONS.items():
            candidate = Position(position.row + delta[0], position.col + delta[1])
            if candidate in visited or world._actor_cell_error(role, candidate, doors, move):
                continue
            visited.add(candidate)
            previous[candidate] = (position, move)
            queue.append(candidate)
    raise ValueError(f"{world.map.map_id}: {role} cannot reach {target}")


def _step(world: SymbolWorld, actions: list[dict[str, list[Any]]], joint: Mapping[str, str]) -> None:
    normalized = {role: SymbolAction(move=joint.get(role, "WAIT"), steps=0 if joint.get(role, "WAIT") == "WAIT" else 1) for role in ("F", "W")}
    world.step_joint(normalized)
    actions.append({role: [action.move, action.steps] for role, action in normalized.items()})


def _move(world: SymbolWorld, actions: list[dict[str, list[Any]]], role: Role, target: Position) -> None:
    for move in _path(world, role, target):
        _step(world, actions, {role: move})


def _prepare_sync_prefix(
    world: SymbolWorld, actions: list[dict[str, list[Any]]], record: Mapping[str, Any]
) -> Role | None:
    prefix = next((item for item in record["controllers"] if item["id"] == "prefix_controller"), None)
    if not prefix:
        return None
    supporter: Role = prefix["role"]
    traveler: Role = OTHER[supporter]
    _move(world, actions, supporter, Position(*prefix["position"]))
    _move(world, actions, traveler, Position(ROWS[traveler], SYNC_PLATE_COL))
    return supporter


def _establish_sync_overlap(
    world: SymbolWorld, actions: list[dict[str, list[Any]]], record: Mapping[str, Any]
) -> None:
    supporter = _prepare_sync_prefix(world, actions, record)
    if supporter:
        _move(world, actions, supporter, Position(ROWS[supporter], SYNC_PLATE_COL))
    else:
        _move(world, actions, "F", Position(ROWS["F"], SYNC_PLATE_COL))
        _move(world, actions, "W", Position(ROWS["W"], SYNC_PLATE_COL))


def _sync_actions(record: Mapping[str, Any], symbol_map: SymbolMap, *, extra_wait: int = 0) -> list[dict[str, list[Any]]]:
    world, actions = SymbolWorld(symbol_map), []
    _establish_sync_overlap(world, actions, record)
    for _ in range(int(record["extension_semantics"]["minimum_overlap_rounds"]) - 1 + extra_wait):
        _step(world, actions, {})
    _step(world, actions, {"F": "RIGHT", "W": "RIGHT"})
    _step(world, actions, {"F": "RIGHT", "W": "RIGHT"})
    _move(world, actions, "F", symbol_map.unique_position("f"))
    _move(world, actions, "W", symbol_map.unique_position("w"))
    if world.status != "team_success":
        raise ValueError(f"{symbol_map.map_id}: sync witness failed")
    return actions


def _join_actions(
    record: Mapping[str, Any],
    symbol_map: SymbolMap,
    order: Sequence[Role],
    *,
    leading_wait: bool = False,
    detour: bool = False,
    early_release: bool = False,
) -> list[dict[str, list[Any]]]:
    world, actions = SymbolWorld(symbol_map), []
    if leading_wait:
        _step(world, actions, {})
    controllers = {item.id: item for item in symbol_map.controllers}
    for role in order:
        _move(world, actions, role, controllers[f"branch_{role.lower()}"].position)
    for role in ("F", "W"):
        _move(world, actions, role, Position(ROWS[role], JOIN_GATE_COL - 1))
    _step(world, actions, {"F": "RIGHT", "W": "RIGHT"})
    _step(world, actions, {"F": "RIGHT", "W": "RIGHT"})
    if detour:
        # Exercise timing invariance only after the latch/toggle branches and
        # join gate. Re-crossing a toggle before the join would intentionally
        # undo that branch rather than constitute an irrelevant detour.
        _step(world, actions, {"F": "RIGHT"})
        _step(world, actions, {"F": "LEFT"})
    if record.get("final_supporter"):
        supporter: Role = record["final_supporter"]
        traveler: Role = OTHER[supporter]
        _move(world, actions, traveler, Position(ROWS[traveler], FINAL_GATE_COL - 1))
        _move(world, actions, supporter, controllers["final_hold"].position)
        _step(world, actions, {traveler: "RIGHT", **({supporter: "LEFT"} if early_release else {})})
        _step(world, actions, {traveler: "RIGHT"})
        _move(world, actions, traveler, symbol_map.unique_position(traveler.lower()))
        _move(world, actions, supporter, symbol_map.unique_position(supporter.lower()))
    else:
        _move(world, actions, "F", symbol_map.unique_position("f"))
        _move(world, actions, "W", symbol_map.unique_position("w"))
    if world.status != "team_success":
        raise ValueError(f"{symbol_map.map_id}: join witness failed")
    return actions


def _trace(symbol_map: SymbolMap, actions: Sequence[Mapping[str, Sequence[Any]]], name: str) -> dict[str, Any]:
    policies = {
        role: ScriptedPolicy([AgentDecision(action=SymbolAction(move=str(item[role][0]), steps=int(item[role][1])), reason=name) for item in actions], name=f"{name}-{role}")
        for role in ("F", "W")
    }
    return DualAgentSession(symbol_map, policies, max_rounds=len(actions), planning_rounds=0).run().trace


def _always_wait(actions: Sequence[Mapping[str, Sequence[Any]]], role: Role) -> list[dict[str, list[Any]]]:
    result = copy.deepcopy(list(actions))
    for item in result:
        item[role] = ["WAIT", 0]
    return result


def _prefix_trace(symbol_map: SymbolMap, actions: Sequence[Mapping[str, Sequence[Any]]]) -> dict[str, Any]:
    world = SymbolWorld(symbol_map)
    rounds: list[dict[str, Any]] = []
    for number, joint in enumerate(actions, start=1):
        if world.status != "running":
            break
        pre = world.observation()
        normalized = {role: SymbolAction(move=str(joint[role][0]), steps=int(joint[role][1])) for role in ("F", "W")}
        result = world.step_joint(normalized)
        rounds.append({"round": number, "observation": pre, "agents": {role: {"decision": {"action": normalized[role].model_dump(mode="json")}} for role in ("F", "W")}, "result": {"observation": result.observation}})
    return {"map_id": symbol_map.map_id, "outcome": world.status if world.status != "running" else "timeout", "rounds": rounds, "final_observation": world.observation()}


def _role_wait_necessity(record: Mapping[str, Any], dag: Mapping[str, Any], symbol_map: SymbolMap, actions: Sequence[Mapping[str, Sequence[Any]]]) -> bool:
    for role in ("F", "W"):
        evaluation = evaluate_extension_trace(record, dag, _prefix_trace(symbol_map, _always_wait(actions, role)))
        if evaluation["valid"] or evaluation["outcome"] == "team_success":
            return False
    return True


def _static_can_reach(
    symbol_map: SymbolMap, role: Role, target: Position, *, blocked: set[Position]
) -> bool:
    start = symbol_map.unique_position(role)
    queue = deque([start])
    visited = {start}
    while queue:
        position = queue.popleft()
        if position == target:
            return True
        for delta in DIRECTIONS.values():
            candidate = Position(position.row + delta[0], position.col + delta[1])
            if candidate in visited or candidate in blocked:
                continue
            cell = symbol_map.cell(candidate)
            if cell == "#" or cell == OTHER[role].lower():
                continue
            visited.add(candidate)
            queue.append(candidate)
    return False


def _no_bypass_valid(record: Mapping[str, Any], symbol_map: SymbolMap) -> bool:
    gate_positions = {Position(*value) for value in record["gate"]["positions"]}
    for role in ("F", "W"):
        role_gate = {position for position in gate_positions if position.row == ROWS[role]}
        exit_position = symbol_map.unique_position(role.lower())
        if not role_gate or _static_can_reach(symbol_map, role, exit_position, blocked=role_gate):
            return False
        if not _static_can_reach(symbol_map, role, exit_position, blocked=set()):
            return False
    prefix = next((item for item in record.get("controllers", []) if item["id"] == "prefix_controller"), None)
    if prefix:
        traveler: Role = OTHER[str(prefix["role"])]
        prefix_gate = next((item for item in symbol_map.actuators if item.id == "prefix_gate"), None)
        sync_target = Position(ROWS[traveler], SYNC_PLATE_COL)
        if (
            not prefix_gate
            or _static_can_reach(symbol_map, traveler, sync_target, blocked=set(prefix_gate.positions))
            or not _static_can_reach(symbol_map, traveler, sync_target, blocked=set())
        ):
            return False
    final_supporter = record.get("final_supporter")
    if final_supporter:
        traveler: Role = OTHER[str(final_supporter)]
        final_gate = next((item for item in symbol_map.actuators if item.id == "final_gate"), None)
        if not final_gate:
            return False
        blocked = set(final_gate.positions)
        exit_position = symbol_map.unique_position(traveler.lower())
        if _static_can_reach(symbol_map, traveler, exit_position, blocked=blocked):
            return False
    return True


def _structural_dependency_valid(
    record: Mapping[str, Any], dag: Mapping[str, Any], symbol_map: SymbolMap
) -> bool:
    evidence_pairs = {(str(item["from"]), str(item["to"])) for item in record["edge_evidence"] if item.get("verified")}
    dag_pairs = {(str(item["from"]), str(item["to"])) for item in dag["edges"]}
    if evidence_pairs != dag_pairs or len(record["edge_evidence"]) != len(evidence_pairs):
        return False
    gate = next((item for item in symbol_map.actuators if item.id == record["gate"]["id"]), None)
    expected_inputs = {"sync_f", "sync_w"} if record["suite"] == "Sync-8" else {"branch_f", "branch_w"}
    return bool(
        gate
        and gate.controlled_by.mode == "all"
        and set(gate.controlled_by.inputs) == expected_inputs
        and _no_bypass_valid(record, symbol_map)
    )


def _sync_conformance(record: Mapping[str, Any], dag: Mapping[str, Any], symbol_map: SymbolMap, witness: Sequence[Mapping[str, Sequence[Any]]]) -> tuple[list[dict[str, Any]], bool]:
    cases: list[dict[str, Any]] = []
    def add(name: str, actions: Sequence[Mapping[str, Sequence[Any]]], expected: bool, applicable: bool = True) -> None:
        if not applicable:
            cases.append({"task_id": record["id"], "case": name, "applicable": False, "passed": None, "reason": "K=1 has no positive insufficient-overlap duration"})
            return
        result = evaluate_extension_trace(record, dag, _prefix_trace(symbol_map, actions))
        cases.append({"task_id": record["id"], "case": name, "applicable": True, "expected_valid": expected, "observed_valid": result["valid"], "passed": result["valid"] == expected, "first_incomplete": result["first_incomplete_required_node"]})

    add("valid_overlap", witness, True)
    add("extra_legal_wait", _sync_actions(record, symbol_map, extra_wait=1), True)
    world, no_overlap = SymbolWorld(symbol_map), []
    prepared = _prepare_sync_prefix(world, no_overlap, record)
    first: Role = OTHER[prepared] if prepared else "F"
    second: Role = prepared or "W"
    if prepared is None:
        _move(world, no_overlap, first, Position(ROWS[first], SYNC_PLATE_COL))
    _step(world, no_overlap, {first: "LEFT"})
    _move(world, no_overlap, second, Position(ROWS[second], SYNC_PLATE_COL))
    add("no_overlap", no_overlap, False)
    k = int(record["extension_semantics"]["minimum_overlap_rounds"])
    if k > 1:
        base_world, insufficient = SymbolWorld(symbol_map), []
        _establish_sync_overlap(base_world, insufficient, record)
        for _ in range(k - 2): _step(base_world, insufficient, {})
        _step(base_world, insufficient, {"F": "RIGHT", "W": "RIGHT"})
        add("insufficient_overlap", insufficient, False)
        early_world, early = SymbolWorld(symbol_map), []
        _establish_sync_overlap(early_world, early, record)
        for _ in range(k - 2): _step(early_world, early, {})
        _step(early_world, early, {first: "LEFT"})
        add("early_release", early, False)
    else:
        add("insufficient_overlap", [], False, False)
        add("early_release", no_overlap, False)
    return cases, all(item["passed"] is not False for item in cases)


def _join_conformance(record: Mapping[str, Any], dag: Mapping[str, Any], symbol_map: SymbolMap) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, list[Any]]]], bool]:
    variants = {
        "F_first": _join_actions(record, symbol_map, ("F", "W")),
        "W_first": _join_actions(record, symbol_map, ("W", "F")),
        "irrelevant_wait": _join_actions(record, symbol_map, ("F", "W"), leading_wait=True),
        "irrelevant_detour": _join_actions(record, symbol_map, ("F", "W"), detour=True),
    }
    cases: list[dict[str, Any]] = []
    for name, actions in variants.items():
        result = evaluate_extension_trace(record, dag, _prefix_trace(symbol_map, actions))
        cases.append({"task_id": record["id"], "case": name, "applicable": True, "expected_valid": True, "observed_valid": result["valid"], "passed": result["valid"]})
    controllers = {item.id: item for item in symbol_map.controllers}
    for role in ("F", "W"):
        world, actions = SymbolWorld(symbol_map), []
        _move(world, actions, role, controllers[f"branch_{role.lower()}"].position)
        result = evaluate_extension_trace(record, dag, _prefix_trace(symbol_map, actions))
        join_complete = "join" in result["completed_nodes"]
        cases.append({"task_id": record["id"], "case": f"{role}_only", "applicable": True, "expected_valid": False, "observed_valid": result["valid"], "join_completed": join_complete, "passed": not result["valid"] and not join_complete})
    if record.get("final_supporter"):
        early_release = _join_actions(record, symbol_map, ("F", "W"), early_release=True)
        result = evaluate_extension_trace(record, dag, _prefix_trace(symbol_map, early_release))
        cases.append({
            "task_id": record["id"],
            "case": "early_maintain_release",
            "applicable": True,
            "expected_valid": False,
            "observed_valid": result["valid"],
            "persistent_violation_detected": bool(result["persistent_condition_violations"]),
            "passed": not result["valid"] and bool(result["persistent_condition_violations"]),
        })
    else:
        cases.append({
            "task_id": record["id"],
            "case": "early_maintain_release",
            "applicable": False,
            "passed": None,
            "reason": "primitive/asymmetric join has no downstream MAINTAIN edge",
        })
    world, both = SymbolWorld(symbol_map), []
    _move(world, both, "F", controllers["branch_f"].position)
    _move(world, both, "W", controllers["branch_w"].position)
    both_result = evaluate_extension_trace(record, dag, _prefix_trace(symbol_map, both))
    cases.append({
        "task_id": record["id"],
        "case": "both_branches",
        "applicable": True,
        "expected_join_completed": True,
        "join_completed": "join" in both_result["completed_nodes"],
        "passed": "join" in both_result["completed_nodes"],
    })
    return cases, variants, all(item["passed"] is not False for item in cases)


def _write_freeze(
    root: Path, suite_dir: Path, manifest: Mapping[str, Any], extra_files: Sequence[Path]
) -> None:
    files = sorted(
        {
            *(path for path in suite_dir.rglob("*") if path.is_file() and path.name != "freeze_manifest.json"),
            *(path for path in extra_files if path.is_file()),
        }
    )
    freeze = {"format": "fwcollab.extension_freeze.v1", "suite": manifest["suite"], "version": "v1", "task_count": 8, "files": [{"path": path.relative_to(root).as_posix(), "sha256": _sha256(path), "bytes": path.stat().st_size} for path in files]}
    save_json(suite_dir / "freeze_manifest.json", freeze)


def _audit_report(suite: str, records: Sequence[Mapping[str, Any]]) -> str:
    return "\n".join([f"# {suite} audit", "", f"- Tasks: **{len(records)}**", f"- Successful witnesses: **{sum(item['witness_valid'] for item in records)}/8**", f"- Deterministic replays: **{sum(item['replay_valid'] for item in records)}/8**", f"- All node bindings valid: **{sum(item['bindings_valid'] for item in records)}/8**", f"- Both permanent-WAIT interventions fail: **{sum(item['role_wait_valid'] for item in records)}/8**", f"- Executable corridor/no-bypass checks: **{sum(item['no_bypass_valid'] for item in records)}/8**", f"- Combined dependency necessity: **{sum(item['necessity_valid'] for item in records)}/8**", f"- Conformance: **{sum(item['conformance_valid'] for item in records)}/8**", "", "Every edge has explicit structural/executable evidence. No model was called.", ""])


def build_extension_suites(root: Path) -> dict[str, Any]:
    root = root.resolve()
    outputs: dict[str, Any] = {}
    all_rows: list[dict[str, Any]] = []
    for suite_name, directory, configs, builder in (
        ("Sync-8", root / "eval_private/synchronize_8", SYNC_CONFIGS, _sync_asset),
        ("Join-8", root / "eval_private/parallel_join_8", JOIN_CONFIGS, _join_asset),
    ):
        (directory / "maps").mkdir(parents=True, exist_ok=True); (directory / "dags").mkdir(parents=True, exist_ok=True)
        records: list[dict[str, Any]] = []
        witnesses: dict[str, Any] = {}
        all_cases: list[dict[str, Any]] = []
        for config in configs:
            text, dag, record = builder(config)
            map_path = root / record["map"]; dag_path = root / record["dag"]
            map_path.write_text(text, encoding="utf-8", newline="\n"); save_json(dag_path, dag)
            symbol_map = parse_symbol_map(text, source=record["map"]); validate_state_dag(dag); validate_extension_record(record, dag)
            if suite_name == "Sync-8":
                actions = _sync_actions(record, symbol_map)
                cases, conformance_valid = _sync_conformance(record, dag, symbol_map, actions)
                witness_variants = {"primary": actions}
            else:
                cases, order_variants, conformance_valid = _join_conformance(record, dag, symbol_map)
                witness_variants = {"primary": order_variants["F_first"], **order_variants}
                actions = witness_variants["primary"]
            trace = _trace(symbol_map, actions, "extension-witness")
            witness_valid = trace["outcome"] == "team_success" and evaluate_extension_trace(record, dag, trace)["valid"]
            replay_valid = bool(replay_trace(symbol_map, trace)["ok"])
            role_wait_valid = _role_wait_necessity(record, dag, symbol_map, actions)
            structural_valid = _structural_dependency_valid(record, dag, symbol_map)
            necessity_valid = role_wait_valid and structural_valid
            metrics = graph_metrics(dag)
            record.update({"witness_valid": witness_valid, "replay_valid": replay_valid, "bindings_valid": True, "role_wait_valid": role_wait_valid, "no_bypass_valid": structural_valid, "necessity_valid": necessity_valid, "conformance_valid": conformance_valid, "dag_metrics": metrics, "all_edges_evidenced": all(item["verified"] for item in record["edge_evidence"])})
            records.append(record); witnesses[record["id"]] = witness_variants; all_cases.extend(cases)
            all_rows.append({
                "task_id": record["id"], "suite": suite_name, "mechanisms": ";".join(record["mechanisms"]), "source_agents": ";".join(record["source_agents"]), "dependency_semantics": ";".join(record["dependency_semantics"]), "composition_motifs": ";".join(record["composition_motifs"]),
                "dag_nodes": metrics["nodes"], "dag_edges": metrics["edges"], "dag_depth": metrics["longest_path"], "requires_overlap": int(suite_name == "Sync-8"), "minimum_overlap_rounds": record["extension_semantics"].get("minimum_overlap_rounds", 0), "parallel_branch_count": 2 if suite_name == "Join-8" else 0, "join_mode": record["extension_semantics"].get("join_mode", "all" if suite_name == "Sync-8" else ""), "role_switch_count": 0, "witness_valid": int(witness_valid), "replay_valid": int(replay_valid), "necessity_valid": int(necessity_valid), "conformance_valid": int(conformance_valid),
            })
        manifest = {"format": "fwcollab.extension_suite_manifest.v1", "suite": suite_name, "version": "v1", "task_count": 8, "records": records}
        if not all(
            record[key]
            for record in records
            for key in ("witness_valid", "replay_valid", "bindings_valid", "role_wait_valid", "no_bypass_valid", "necessity_valid", "conformance_valid", "all_edges_evidenced")
        ):
            raise RuntimeError(f"{suite_name} did not satisfy every completion gate")
        save_json(directory / "manifest.json", manifest); save_json(directory / "witnesses.json", {"format": "fwcollab.extension_witnesses.v1", "suite": suite_name, "tasks": witnesses})
        audit_dir = root / ("artifacts/audits/synchronize_8" if suite_name == "Sync-8" else "artifacts/audits/parallel_join_8")
        eval_dir = root / ("artifacts/evaluations/synchronize_conformance_v1" if suite_name == "Sync-8" else "artifacts/evaluations/parallel_join_conformance_v1")
        audit_dir.mkdir(parents=True, exist_ok=True); eval_dir.mkdir(parents=True, exist_ok=True)
        audit_summary = {"suite": suite_name, "tasks": 8, "witness_valid": sum(item["witness_valid"] for item in records), "replay_valid": sum(item["replay_valid"] for item in records), "bindings_valid": sum(item["bindings_valid"] for item in records), "role_wait_valid": sum(item["role_wait_valid"] for item in records), "no_bypass_valid": sum(item["no_bypass_valid"] for item in records), "necessity_valid": sum(item["necessity_valid"] for item in records), "conformance_valid": sum(item["conformance_valid"] for item in records), "all_edges_evidenced": sum(item["all_edges_evidenced"] for item in records)}
        save_json(audit_dir / "summary.json", audit_summary); (audit_dir / "REPORT.md").write_text(_audit_report(suite_name, records), encoding="utf-8", newline="\n")
        conformance_summary = {"format": "fwcollab.extension_conformance.v1", "suite": suite_name, "cases_generated": len(all_cases), "cases_applicable": sum(item["applicable"] for item in all_cases), "cases_passed": sum(item["passed"] is True for item in all_cases), "cases_failed": sum(item["passed"] is False for item in all_cases), "cases_not_applicable": sum(not item["applicable"] for item in all_cases)}
        save_json(eval_dir / "summary.json", conformance_summary); save_json(eval_dir / "cases.json", {"cases": all_cases})
        spec = root / "docs" / ("SYNCHRONIZE_SUITE_SPEC.md" if suite_name == "Sync-8" else "PARALLEL_JOIN_SUITE_SPEC.md")
        _write_freeze(
            root,
            directory,
            manifest,
            [
                spec,
                root / "docs/EXTENSION_SUITE_CHANGELOG.md",
                root / "src/fwcollab/symbolic/extension_eval.py",
                root / "src/fwcollab/symbolic/extension_suites.py",
                root / "scripts/build_extension_suites.py",
                audit_dir / "summary.json",
                audit_dir / "REPORT.md",
                eval_dir / "summary.json",
                eval_dir / "cases.json",
            ],
        )
        outputs[suite_name] = {"manifest": str(directory / "manifest.json"), "audit": audit_summary, "conformance": conformance_summary}

    extension_dir = root / "artifacts/audits/extensions_v1"; extension_dir.mkdir(parents=True, exist_ok=True)
    fields = list(all_rows[0]); buffer = io.StringIO(newline=""); writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n"); writer.writeheader(); writer.writerows(all_rows)
    (extension_dir / "extension_tasks.csv").write_text(buffer.getvalue(), encoding="utf-8-sig", newline="")
    summary = """# FWCollab extension coverage summary

| Suite | Scope | Covered dependency/composition | Tasks | Model results |
|---|---|---|---:|---|
| Core Full-72 | frozen main benchmark | ENABLE, MAINTAIN | 72 | existing results unchanged |
| Information-12 | separate extension | INFORMATION | 12 | existing C5 results unchanged |
| Synchronize-8 | separate extension | bounded temporal SYNCHRONIZE | 8 | none |
| ParallelJoin-8 | separate extension | role-owned Parallel Join composition | 8 | none |

Sync-8 contains two primitive, three medium/asymmetric, and three compositional tasks. Join-8 contains two primitive fork/join, three asymmetric, and three join-then-MAINTAIN tasks. These suites are not merged into Full-72 and have no model evaluation.
"""
    (extension_dir / "extension_coverage_summary.md").write_text(summary, encoding="utf-8", newline="\n")
    baseline_records = []
    for relative, expected in FROZEN_BASELINES.items():
        path = root / relative
        actual = _sha256(path) if path.is_file() else "missing"
        baseline_records.append({"path": relative, "expected_sha256": expected, "actual_sha256": actual, "unchanged": actual == expected})
    baseline_verification = {
        "format": "fwcollab.extension_baseline_verification.v1",
        "all_unchanged": all(item["unchanged"] for item in baseline_records),
        "files": baseline_records,
    }
    save_json(extension_dir / "frozen_baseline_verification.json", baseline_verification)
    if not baseline_verification["all_unchanged"]:
        raise RuntimeError("a frozen legacy benchmark/evaluation artifact changed")
    outputs["coverage"] = {"tasks_csv": str(extension_dir / "extension_tasks.csv"), "summary": str(extension_dir / "extension_coverage_summary.md")}
    return outputs
