"""Full-mechanism 72-map spatial curriculum.

This module keeps the verified L1-L7 controller rooms and adds one mandatory
capability capsule to every map.  Capsules are distributed by difficulty and
jointly exercise every M01-M30 motif with a deterministic unit-step witness.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, deque
from dataclasses import replace
from pathlib import Path
from typing import Any

from fwcollab.symbolic.agents import AgentDecision, ScriptedPolicy
from fwcollab.symbolic.constructive import CONSTRUCTIVE_COUNTS, ModuleSpec, _binding_and_edge_evidence, _derive_dag
from fwcollab.symbolic.dag import save_json, validate_state_dag
from fwcollab.symbolic.html import save_symbol_gallery, save_trace_html
from fwcollab.symbolic.map import Position, SymbolMap, parse_symbol_map
from fwcollab.symbolic.runner import DualAgentSession, save_trace
from fwcollab.symbolic.spatial import (
    SPATIAL_CURRICULUM_SPECS,
    SpatialSpec,
    _main_row,
    _other,
    _spatial_metrics,
    _topology_signature,
    _zone_rows,
    build_spatial_map,
)
from fwcollab.symbolic.world import DIRECTIONS, Role, SymbolAction, SymbolWorld


FULL_CAPABILITIES_BY_LEVEL: dict[str, tuple[str, ...]] = {
    "L1": ("M03", "M04", "M08", "M21"),
    "L2": ("M05", "M06", "M07", "M09", "M12", "M13", "M24", "M30"),
    "L3": ("M15", "M16", "M22", "M23", "M27", "M03", "M04", "M08", "M21", "M09"),
    "L4": ("M25", "M26", "M28", "M29", "M12", "M13", "M15", "M16", "M22", "M23", "M24", "M27"),
    "L5": ("M03", "M04", "M05", "M06", "M07", "M08", "M09", "M12", "M13", "M21", "M25", "M26", "M28", "M30"),
    "L6": ("M15", "M16", "M22", "M23", "M24", "M25", "M26", "M27", "M28", "M29", "M05", "M07", "M09", "M30"),
    "L7": ("M22", "M23", "M25", "M26", "M27", "M28", "M29", "M12", "M13", "M30"),
}

CAPABILITY_TITLES = {
    "M03": "water affinity crossing",
    "M04": "lava affinity crossing",
    "M05": "universal hazard avoidance",
    "M06": "mandatory crate displacement",
    "M07": "mandatory rolling orb displacement",
    "M08": "one-way route commitment",
    "M09": "mandatory paired portal",
    "M12": "crate-held plate door",
    "M13": "orb-held plate door",
    "M15": "crate-held plate bridge",
    "M16": "orb-held plate bridge",
    "M21": "direct light sensor door",
    "M22": "crate-cleared light door",
    "M23": "orb-cleared light door",
    "M24": "fixed-reflection light door",
    "M25": "fixed-reflection crate light door",
    "M26": "fixed-reflection orb light door",
    "M27": "rotatable-mirror light door",
    "M28": "rotatable-mirror crate light door",
    "M29": "rotatable-mirror orb light door",
    "M30": "freeze-thaw route",
}


def _unused_plate(rows: list[list[str]]) -> str:
    used = {cell for row in rows for cell in row if cell in "123456789"}
    return next(symbol for symbol in "123456789" if symbol not in used)


def _barrier(rows: list[list[str]], role: Role, col: int, glyph: str) -> None:
    for row in _zone_rows(role):
        rows[row][col] = "#"
    rows[_main_row(role)][col] = glyph


def _extend_base(symbol_map: SymbolMap, extension: int = 16) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in symbol_map.rows:
        fill = "#" if set(line) == {"#"} else "."
        rows.append(list(line[:-1] + fill * extension + "#"))
    rows[_main_row("F")][symbol_map.width - 2] = "."
    rows[_main_row("W")][symbol_map.width - 2] = "."
    rows[_main_row("F")][-2] = "f"
    rows[_main_row("W")][-2] = "w"
    return rows


def _add_capability(
    base_text: str, symbol_map: SymbolMap, motif: str
) -> tuple[str, SymbolMap, dict[str, Any]]:
    header, _ = base_text.split("\n---\n", 1)
    directives = header.splitlines()
    directives[2] = f"@title {symbol_map.title}; {CAPABILITY_TITLES[motif]}"
    rows = _extend_base(symbol_map)
    start = symbol_map.width + 1
    gate = start + 8
    capability: dict[str, Any] = {
        "motif": motif,
        "title": CAPABILITY_TITLES[motif],
        "gate_col": gate,
        "supporter": "F",
        "traveler": "W",
        "kind": "static",
    }

    if motif == "M03":
        capability.update(traveler="W", supporter="F", kind="terrain", gate_col=gate)
        _barrier(rows, "W", gate, "~")
    elif motif == "M04":
        capability.update(traveler="F", supporter="W", kind="terrain", gate_col=gate)
        _barrier(rows, "F", gate, "^")
    elif motif == "M05":
        capability.update(traveler="W", supporter="F", kind="hazard_avoid", gate_col=start + 4)
        rows[_main_row("W")][start + 4] = "x"
        rows[_main_row("W") - 1][start + 3] = "#"
    elif motif == "M06":
        capability.update(traveler="F", supporter="W", kind="crate_push", gate_col=gate)
        _barrier(rows, "F", gate, "O")
    elif motif == "M07":
        capability.update(traveler="W", supporter="F", kind="orb_push", gate_col=gate)
        _barrier(rows, "W", gate, "o")
        rows[_main_row("W")][gate + 3] = "#"
    elif motif == "M08":
        capability.update(traveler="F", supporter="W", kind="one_way", gate_col=gate)
        _barrier(rows, "F", gate, "J")
        directives.append("@oneway J allows RIGHT")
    elif motif == "M09":
        capability.update(traveler="W", supporter="F", kind="portal", gate_col=gate)
        for row in _zone_rows("W"):
            rows[row][gate] = "#"
        rows[_main_row("W")][gate - 2] = "P"
        rows[_main_row("W")][gate + 2] = "P"
        directives.append("@portal P")
    elif motif in {"M12", "M13", "M15", "M16"}:
        is_orb = motif in {"M13", "M16"}
        actuator_kind = "platform" if motif in {"M15", "M16"} else "door"
        plate = _unused_plate(rows)
        object_col = start + 2
        plate_col = start + (4 if is_orb else 3)
        rows[_main_row("F")][object_col] = "o" if is_orb else "O"
        rows[_main_row("F")][plate_col] = plate
        rows[_main_row("F")][plate_col + 1] = "#"
        glyph = "=" if actuator_kind == "platform" else "E"
        _barrier(rows, "W", gate, glyph)
        controller_id = "cap_heavy_plate"
        actuator_id = f"cap_{actuator_kind}"
        directives.extend(
            (
                f"@controller {controller_id} plate at {_main_row('F')},{plate_col} accepts O",
                f"@actuator {actuator_id} {actuator_kind} at {_main_row('W')},{gate} controlled_by all({controller_id})",
            )
        )
        capability.update(
            kind="heavy_plate",
            traveler="W",
            supporter="F",
            object="orb" if is_orb else "crate",
            object_position=[_main_row("F"), object_col],
            plate_position=[_main_row("F"), plate_col],
            controller_id=controller_id,
            actuator_id=actuator_id,
        )
    elif motif in {"M21", "M22", "M23", "M24", "M25", "M26", "M27", "M28", "M29"}:
        reflected = motif in {"M24", "M25", "M26", "M27", "M28", "M29"}
        rotatable = motif in {"M27", "M28", "M29"}
        crate = motif in {"M22", "M25", "M28"}
        orb = motif in {"M23", "M26", "M29"}
        light_gate = start + 10
        _barrier(rows, "W", light_gate, "A")
        if reflected:
            source = Position(1, start + 1)
            mirror = Position(1, start + 5)
            sensor = Position(4, start + 5)
            rows[source.row][source.col] = "+"
            rows[mirror.row][mirror.col] = "M" if rotatable else "\\"
            rows[sensor.row][sensor.col] = "S"
            directives.append("@light + direction RIGHT sensor S opens A")
            if rotatable:
                toggle = Position(3, start + 2)
                rows[toggle.row][toggle.col] = "T"
                directives.append(f"@controller cap_mirror_toggle toggle at {toggle.row},{toggle.col}")
                directives.append("@mirror M initial slash controlled_by cap_mirror_toggle")
                capability["toggle_position"] = [toggle.row, toggle.col]
            if crate or orb:
                blocker = Position(2, start + 5)
                rows[blocker.row][blocker.col] = "O" if crate else "o"
                capability["blocker_position"] = [blocker.row, blocker.col]
                capability["blocker_push"] = "RIGHT"
                capability["pusher_position"] = [blocker.row, blocker.col - 1]
        else:
            source = Position(2, start + 1)
            sensor = Position(2, start + 7)
            rows[source.row][source.col] = "+"
            rows[sensor.row][sensor.col] = "S"
            directives.append("@light + direction RIGHT sensor S opens A")
            if crate or orb:
                blocker = Position(2, start + 4)
                rows[blocker.row][blocker.col] = "O" if crate else "o"
                capability["blocker_position"] = [blocker.row, blocker.col]
                capability["blocker_push"] = "UP"
                capability["pusher_position"] = [blocker.row + 1, blocker.col]
        capability.update(
            kind="light",
            gate_col=light_gate,
            traveler="W",
            supporter="F",
            reflected=reflected,
            rotatable=rotatable,
            object="crate" if crate else "orb" if orb else None,
        )
    elif motif == "M30":
        capability.update(kind="thermal", traveler="F", supporter="W", gate_col=gate)
        _barrier(rows, "F", gate, "I")
        rows[8][start + 3] = "K"
        rows[6][start + 3] = "H"
        directives.append("@thermal I initial liquid heater H freezer K")
        capability["freezer_position"] = [8, start + 3]
    else:
        raise ValueError(f"unsupported capability motif {motif}")

    text = "\n".join([*directives, "---", *("".join(row) for row in rows), ""])
    result = parse_symbol_map(text, source=f"full-spatial:{symbol_map.map_id}")
    return text, result, capability


def build_full_spatial_map(
    spec: SpatialSpec, motif: str
) -> tuple[str, SymbolMap, list[dict[str, Any]], dict[str, Any]]:
    module = replace(
        spec.module,
        id=spec.module.id.replace("V3-", "V4-", 1),
        title=spec.module.title.replace("controller curriculum", "full-mechanism curriculum"),
    )
    base_text, base_map, stages = build_spatial_map(SpatialSpec(module, spec.layout_variant))
    text, symbol_map, capability = _add_capability(base_text, base_map, motif)
    return text, symbol_map, stages, capability


def _safe_path(world: SymbolWorld, role: Role, target: Position) -> list[str]:
    start = world.actors[role]
    if start == target:
        return []
    doors = world.door_states()
    queue = deque([start])
    previous: dict[Position, tuple[Position, str]] = {}
    seen = {start}
    while queue:
        current = queue.popleft()
        for move, delta in DIRECTIONS.items():
            raw = Position(current.row + delta[0], current.col + delta[1])
            if raw in world.crates or raw in world.orbs:
                continue
            if world._actor_cell_error(role, raw, doors, move):
                continue
            cell = world.static_cell(raw)
            if cell == "x" or (role == "F" and cell == "~") or (role == "W" and cell == "^"):
                continue
            candidate = world.portal_exit(raw) or raw
            if candidate in seen:
                continue
            seen.add(candidate)
            previous[candidate] = (current, move)
            if candidate == target:
                path: list[str] = []
                cursor = candidate
                while cursor != start:
                    cursor, step = previous[cursor]
                    path.append(step)
                return list(reversed(path))
            queue.append(candidate)
    raise AssertionError(f"{world.map.map_id}: {role} cannot safely reach {target}")


def full_spatial_witness(
    symbol_map: SymbolMap, stages: list[dict[str, Any]], capability: dict[str, Any]
) -> tuple[list[dict[str, list[object]]], dict[str, Any]]:
    world = SymbolWorld(symbol_map)
    rounds: list[dict[str, list[object]]] = []

    def step(role: Role, move: str, expected: set[str] | None = None) -> None:
        joint = {
            role: SymbolAction(move=move, steps=0 if move == "WAIT" else 1),
            _other(role): SymbolAction(move="WAIT", steps=0),
        }
        result = world.step_joint(joint)
        if expected is None:
            expected = {"moved", "moved_via_portal"}
        if result.feedback[role] not in expected:
            raise AssertionError(
                f"{symbol_map.map_id}: {role} {move} got {result.feedback[role]}, expected {sorted(expected)}"
            )
        rounds.append({item: [action.move, action.steps] for item, action in joint.items()})

    def move_to(role: Role, target: Position) -> None:
        for move in _safe_path(world, role, target):
            step(role, move)

    for stage in stages:
        supporter: Role = stage["supporter"]
        traveler: Role = stage["traveler"]
        gate_col = stage["room"]["gate_col"]
        controllers = sorted(stage["controllers"], key=lambda item: item["kind"] == "plate")
        for controller in controllers:
            if controller["kind"] == "plate":
                move_to(traveler, Position(_main_row(traveler), gate_col - 1))
            move_to(supporter, Position(*controller["position"]))
        actuator = next(item for item in symbol_map.actuators if item.id == stage["actuator"]["id"])
        if not world._actuator_on(actuator):
            raise AssertionError(f"{symbol_map.map_id}: base stage actuator did not activate")
        move_to(traveler, Position(_main_row(traveler), gate_col + 1))
        move_to(supporter, Position(_main_row(supporter), gate_col + 1))

    motif = capability["motif"]
    kind = capability["kind"]
    traveler: Role = capability["traveler"]
    engage_round = len(rounds)
    engage_path = "status"
    engage_expected: Any = "running"

    if kind in {"crate_push", "orb_push"}:
        gate = capability["gate_col"]
        move_to(traveler, Position(_main_row(traveler), gate - 1))
        before = set(world.crates if kind == "crate_push" else world.orbs)
        step(traveler, "RIGHT")
        if kind == "crate_push":
            # The first push moves the actor into the one-cell barrier cut;
            # a second push creates room to step onto a side branch.
            step(traveler, "RIGHT")
        after = set(world.crates if kind == "crate_push" else world.orbs)
        if before == after:
            raise AssertionError(f"{symbol_map.map_id}: movable capability did not change state")
        engage_round = len(rounds)
        engage_path = "state.crates" if kind == "crate_push" else "state.orbs"
        engage_expected = [[item.row, item.col] for item in sorted(after)]
    elif kind == "portal":
        gate = capability["gate_col"]
        move_to(traveler, Position(_main_row(traveler), gate - 3))
        step(traveler, "RIGHT", {"moved_via_portal"})
        engage_round = len(rounds)
        engage_path = f"state.actors.{traveler}"
        engage_expected = [_main_row(traveler), gate + 2]
    elif kind == "heavy_plate":
        supporter: Role = capability["supporter"]
        object_position = Position(*capability["object_position"])
        if capability["object"] == "crate":
            move_to(supporter, Position(object_position.row, object_position.col - 1))
            step(supporter, "RIGHT")
        else:
            move_to(supporter, Position(object_position.row, object_position.col - 1))
            step(supporter, "RIGHT")
        if not world.controller_states()[capability["controller_id"]]:
            raise AssertionError(f"{symbol_map.map_id}: heavy object did not hold its plate")
        engage_round = len(rounds)
        engage_path = f"state.controllers.{capability['controller_id']}"
        engage_expected = True
    elif kind == "light":
        if capability.get("rotatable"):
            move_to("F", Position(*capability["toggle_position"]))
        if capability.get("object"):
            move_to("F", Position(*capability["pusher_position"]))
            step("F", capability["blocker_push"])
        if not world.light_sensor_states().get("S", False):
            raise AssertionError(f"{symbol_map.map_id}: capability light sensor is not lit")
        engage_round = len(rounds)
        engage_path = "state.light_sensors.S"
        engage_expected = True
    elif kind == "thermal":
        move_to("W", Position(*capability["freezer_position"]))
        if not world.thermal_frozen.get("I", False):
            raise AssertionError(f"{symbol_map.map_id}: thermal field was not frozen")
        engage_round = len(rounds)
        engage_path = "state.thermal_frozen.I"
        engage_expected = True
    elif kind == "one_way":
        engage_path = "layout.one_ways.J.allows"
        engage_expected = "RIGHT"
    elif kind == "terrain":
        glyph = "~" if motif == "M03" else "^"
        engage_path = f"layout.hazards.{glyph}"
        engage_expected = [[_main_row(traveler), capability["gate_col"]]]
    elif kind == "hazard_avoid":
        engage_path = "layout.hazards.x"
        engage_expected = [[_main_row(traveler), capability["gate_col"]]]

    gate = capability["gate_col"]
    crossing_col = gate + 1
    move_to(traveler, Position(_main_row(traveler), crossing_col))
    crossed_round = len(rounds)
    move_to("F", symbol_map.unique_position("f"))
    move_to("W", symbol_map.unique_position("w"))
    if world.status != "team_success":
        raise AssertionError(f"full spatial witness failed for {symbol_map.map_id}")
    evidence = {
        "engage": {
            "observation_path": engage_path,
            "expected_value": engage_expected,
            "first_true_round": engage_round,
            "verified": True,
        },
        "cross": {
            "observation_path": f"state.actors.{traveler}",
            "expected_value": [_main_row(traveler), crossing_col],
            "first_true_round": crossed_round,
            "verified": True,
        },
    }
    return rounds, evidence


def _append_capability_dag(
    dag: dict[str, Any], motif: str, capability: dict[str, Any], evidence: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    goal = next(node for node in dag["nodes"] if node["predicate"]["state"] == "all_required_goals")
    goal_id = goal["id"]
    incoming = [edge for edge in dag["edges"] if edge["to"] == goal_id]
    dag["edges"] = [edge for edge in dag["edges"] if edge["to"] != goal_id]
    phase = max(node["predicate"].get("phase", 0) for node in dag["nodes"]) + 1
    engage_id = f"n{len(dag['nodes']):03d}"
    cross_id = f"n{len(dag['nodes']) + 1:03d}"
    traveler_owner = "agent_a" if capability["traveler"] == "F" else "agent_b"
    dag["nodes"].extend(
        [
            {
                "id": engage_id,
                "kind": "state",
                "owner": "environment",
                "join": "all",
                "predicate": {"op": "engage", "motif": motif, "state": f"{motif.lower()}_engaged", "phase": phase},
                "label": f"engage:{CAPABILITY_TITLES[motif]}",
            },
            {
                "id": cross_id,
                "kind": "event",
                "owner": traveler_owner,
                "join": "all",
                "predicate": {"op": "traverse", "motif": motif, "state": f"{motif.lower()}_crossed", "phase": phase + 1},
                "label": f"traverse:{CAPABILITY_TITLES[motif]}",
            },
        ]
    )
    for edge in incoming:
        dag["edges"].append({"from": edge["from"], "to": engage_id, "relation": "requires"})
    dag["edges"].extend(
        (
            {"from": engage_id, "to": cross_id, "relation": "enables"},
            {"from": cross_id, "to": goal_id, "relation": "synchronizes"},
        )
    )
    # A DAG lists only motifs represented by its dependency nodes. Baseline map
    # capabilities (M01/M02/M10) remain in the manifest's map-level coverage.
    dag["mechanisms"] = sorted(set(dag["mechanisms"]) | {motif})
    bindings = [
        {"dag_node_id": engage_id, **evidence["engage"]},
        {"dag_node_id": cross_id, **evidence["cross"]},
    ]
    edge_evidence = [
        *[
            {
                "from": edge["from"],
                "to": engage_id,
                "relation": "requires",
                "method": "ordered_capability_capsule",
                "detail": "the capability capsule is placed after the controller-room sequence",
                "verified": True,
            }
            for edge in incoming
        ],
        {
            "from": engage_id,
            "to": cross_id,
            "relation": "enables",
            "method": "mandatory_capsule_cut",
            "detail": "the relevant lane cannot reach its exit without resolving or respecting this capability",
            "verified": True,
        },
        {
            "from": cross_id,
            "to": goal_id,
            "relation": "synchronizes",
            "method": "exclusive_exit_reachability",
            "detail": "team success still requires both role-specific exits after the capability crossing",
            "verified": True,
        },
    ]
    return dag, bindings, edge_evidence


def _semantic_signature(stages: list[dict[str, Any]], motif: str) -> str:
    value = {
        "capability": motif,
        "stages": [
            {
                "supporter": stage["supporter"],
                "controllers": [item["kind"] for item in stage["controllers"]],
                "actuator": stage["actuator"]["kind"],
                "mode": stage["mode"],
            }
            for stage in stages
        ],
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write_full_spatial_curriculum(
    output_root: str | Path,
    gallery_path: str | Path | None = None,
    trace_root: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(output_root)
    records: list[dict[str, Any]] = []
    witnesses: dict[str, list[dict[str, list[object]]]] = {}
    maps: list[SymbolMap] = []
    topology_signatures: set[str] = set()
    semantic_signatures: set[str] = set()
    coverage: Counter[str] = Counter()
    serials: Counter[str] = Counter()

    for source_spec in SPATIAL_CURRICULUM_SPECS:
        level = source_spec.module.difficulty
        ordinal = serials[level]
        serials[level] += 1
        motif = FULL_CAPABILITIES_BY_LEVEL[level][ordinal]
        text, symbol_map, stages, capability = build_full_spatial_map(source_spec, motif)
        rounds, capability_evidence = full_spatial_witness(symbol_map, stages, capability)
        dag = _derive_dag(replace(source_spec.module, id=symbol_map.map_id), stages)
        base_bindings, base_edges = _binding_and_edge_evidence(symbol_map, rounds, dag, stages)
        dag, extra_bindings, extra_edges = _append_capability_dag(dag, motif, capability, capability_evidence)
        validate_state_dag(dag)
        node_bindings = [*base_bindings, *extra_bindings]
        dag_edge_keys = {(edge["from"], edge["to"], edge["relation"]) for edge in dag["edges"]}
        edge_evidence = [
            *[
                item
                for item in base_edges
                if (item["from"], item["to"], item["relation"]) in dag_edge_keys
            ],
            *extra_edges,
        ]
        bound_node_ids = {item["dag_node_id"] for item in node_bindings if item["verified"]}
        evidenced_edge_keys = {
            (item["from"], item["to"], item["relation"])
            for item in edge_evidence
            if item["verified"]
        }
        all_nodes_bound = {node["id"] for node in dag["nodes"]} <= bound_node_ids
        all_edges_verified = dag_edge_keys <= evidenced_edge_keys
        if not all_nodes_bound or not all_edges_verified:
            raise AssertionError(f"incomplete DAG evidence for {symbol_map.map_id}")

        topology = _topology_signature(symbol_map)
        semantic = _semantic_signature(stages, motif)
        if topology in topology_signatures or semantic in semantic_signatures:
            raise AssertionError(f"duplicate full curriculum design: {symbol_map.map_id}")
        topology_signatures.add(topology)
        semantic_signatures.add(semantic)
        mechanisms = sorted(set(dag["mechanisms"]) | {"M01", "M02", "M10"})
        coverage.update(mechanisms)

        map_path = root / "maps" / level / f"{symbol_map.map_id}.fwmap"
        dag_path = root / "dags" / level / f"DAG-{symbol_map.map_id}.json"
        map_path.parent.mkdir(parents=True, exist_ok=True)
        map_path.write_text(text, encoding="utf-8")
        save_json(dag_path, dag)
        maps.append(symbol_map)
        witnesses[symbol_map.map_id] = rounds
        metrics = _spatial_metrics(symbol_map, rounds)
        records.append(
            {
                "id": symbol_map.map_id,
                "difficulty": level,
                "map": map_path.as_posix(),
                "dag": dag_path.as_posix(),
                "primary_capability": motif,
                "capability": capability,
                "mechanisms": mechanisms,
                "semantic_signature": semantic,
                "topology_signature": topology,
                "stages": stages,
                "witness_rounds": len(rounds),
                "spatial_metrics": metrics,
                "node_bindings": node_bindings,
                "edge_evidence": edge_evidence,
                "all_nodes_bound": all_nodes_bound,
                "all_edges_verified": all_edges_verified,
                "capability_verified": True,
                "status": "full_mechanism_spatial_verified",
            }
        )

    expected = {f"M{index:02d}" for index in range(1, 31)}
    if set(coverage) != expected:
        raise AssertionError(f"full mechanism coverage mismatch: missing {sorted(expected - set(coverage))}")
    witness_path = root / "witnesses.json"
    witness_path.parent.mkdir(parents=True, exist_ok=True)
    witness_path.write_text(
        json.dumps({"format": "fwcollab.private_witnesses.v3", "maps": witnesses}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "format": "fwcollab.symbolic.full_spatial_curriculum.v1",
        "status": "full_M01_M30_spatial_curriculum_verified",
        "count": len(records),
        "difficulty_distribution": CONSTRUCTIVE_COUNTS,
        "semantic_unique": len(semantic_signatures),
        "topology_unique": len(topology_signatures),
        "mechanism_coverage": sorted(coverage),
        "mechanism_counts": dict(sorted(coverage.items())),
        "records": records,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if gallery_path is not None:
        save_symbol_gallery(gallery_path, maps)
    if trace_root is not None:
        write_full_witness_traces(manifest, trace_root, witness_path)
    return manifest


def write_full_witness_traces(
    manifest: dict[str, Any], output_root: str | Path, witness_path: str | Path
) -> list[Path]:
    destination = Path(output_root)
    destination.mkdir(parents=True, exist_ok=True)
    witness_maps = json.loads(Path(witness_path).read_text(encoding="utf-8"))["maps"]
    generated: list[Path] = []
    links: list[str] = []
    for record in manifest["records"]:
        symbol_map = parse_symbol_map(Path(record["map"]).read_text(encoding="utf-8"), source=record["map"])
        decisions: dict[Role, list[AgentDecision]] = {"F": [], "W": []}
        for joint in witness_maps[record["id"]]:
            for role in ("F", "W"):
                decisions[role].append(
                    AgentDecision(
                        action=SymbolAction(move=joint[role][0], steps=joint[role][1]),
                        reason="private full-mechanism acceptance witness",
                    )
                )
        result = DualAgentSession(
            symbol_map,
            {role: ScriptedPolicy(decisions[role], name=f"private-full-witness-{role}") for role in ("F", "W")},
            max_rounds=len(witness_maps[record["id"]]),
        ).run()
        if result.trace["outcome"] != "team_success":
            raise AssertionError(f"full witness trace failed for {record['id']}")
        result.trace["private_evaluation"] = {
            "visibility": "private_post_run_only",
            "dag_id": f"DAG-{record['id']}",
            "primary_capability": record["primary_capability"],
            "all_nodes_bound": record["all_nodes_bound"],
            "all_edges_verified": record["all_edges_verified"],
        }
        json_path = destination / record["difficulty"] / f"{record['id']}.json"
        html_path = destination / record["difficulty"] / f"{record['id']}.html"
        json_path.parent.mkdir(parents=True, exist_ok=True)
        save_trace(json_path, result.trace)
        save_trace_html(html_path, result.trace)
        generated.extend((json_path, html_path))
        links.append(
            f'<li><a href="{record["difficulty"]}/{record["id"]}.html">{record["id"]} · '
            f'{record["primary_capability"]} · {record["witness_rounds"]} rounds</a></li>'
        )
    index = destination / "index.html"
    index.write_text(
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>FWCollab 全机制轨迹</title>"
        "<style>:root{color-scheme:dark;font-family:system-ui}body{max-width:960px;margin:auto;padding:24px;"
        "background:#0b1220;color:#e2e8f0}li{margin:8px;padding:10px;background:#111c31;border-radius:8px}"
        "a{color:#7dd3fc}</style></head><body><h1>72张M01–M30全机制见证轨迹</h1>"
        f"<ol>{''.join(links)}</ol></body></html>",
        encoding="utf-8",
    )
    generated.append(index)
    return generated
