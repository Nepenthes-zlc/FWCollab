"""Construct-first two-dimensional room maps for cooperative planning."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fwcollab.symbolic.constructive import (
    CONSTRUCTIVE_COUNTS,
    ModuleSpec,
    StageSpec,
    _binding_and_edge_evidence,
    _curriculum_specs,
    _derive_dag,
)
from fwcollab.symbolic.agents import AgentDecision, ScriptedPolicy
from fwcollab.symbolic.dag import save_json
from fwcollab.symbolic.html import save_symbol_gallery, save_trace_html
from fwcollab.symbolic.map import Position, SymbolMap, parse_symbol_map
from fwcollab.symbolic.runner import DualAgentSession, save_trace
from fwcollab.symbolic.world import DIRECTIONS, Role, SymbolAction, SymbolWorld


@dataclass(frozen=True, slots=True)
class SpatialSpec:
    module: ModuleSpec
    layout_variant: int


SPATIAL_SPECS: tuple[SpatialSpec, ...] = (
    SpatialSpec(ModuleSpec("R01", "upper branch held plate", "L1", (StageSpec("F", ("plate",), "door"),)), 0),
    SpatialSpec(ModuleSpec("R02", "wide fork persistent lever", "L1", (StageSpec("W", ("lever",), "door"),)), 1),
    SpatialSpec(ModuleSpec("R03", "deep fork toggle bridge", "L1", (StageSpec("F", ("toggle",), "platform"),)), 2),
    SpatialSpec(ModuleSpec("R04", "two branch ALL handoff", "L2", (StageSpec("F", ("lever", "plate"), "door"),)), 3),
    SpatialSpec(ModuleSpec("R05", "alternative branch levers", "L2", (StageSpec("W", ("lever", "lever"), "door", "any"),)), 4),
    SpatialSpec(ModuleSpec("R06", "alternating lever and plate rooms", "L2", (StageSpec("F", ("lever",), "door"), StageSpec("W", ("plate",), "platform"))), 0),
    SpatialSpec(ModuleSpec("R07", "alternating toggle and support rooms", "L2", (StageSpec("W", ("toggle",), "platform"), StageSpec("F", ("plate",), "door"))), 1),
    SpatialSpec(ModuleSpec("R08", "three room mixed relay", "L3", (StageSpec("F", ("lever",), "door"), StageSpec("W", ("plate",), "door"), StageSpec("F", ("toggle",), "platform"))), 0),
    SpatialSpec(ModuleSpec("R09", "three room AND OR relay", "L3", (StageSpec("W", ("lever", "lever"), "door", "any"), StageSpec("F", ("lever", "plate"), "door"), StageSpec("W", ("toggle",), "platform"))), 1),
    SpatialSpec(ModuleSpec("R10", "three held-window relay", "L3", (StageSpec("F", ("plate",), "platform"), StageSpec("W", ("plate",), "door"), StageSpec("F", ("lever",), "door"))), 2),
    SpatialSpec(ModuleSpec("R11", "four room alternating commitment", "L4", (StageSpec("F", ("lever",), "door"), StageSpec("W", ("toggle", "plate"), "platform"), StageSpec("F", ("plate",), "door"), StageSpec("W", ("lever",), "platform"))), 3),
    SpatialSpec(ModuleSpec("R12", "five room branch planning relay", "L5", (StageSpec("W", ("lever",), "platform"), StageSpec("F", ("lever", "plate"), "door"), StageSpec("W", ("toggle",), "door"), StageSpec("F", ("plate",), "platform"), StageSpec("W", ("lever", "lever"), "door", "any"))), 5),
)


def _spatial_curriculum_specs() -> tuple[SpatialSpec, ...]:
    """Pair all 72 semantic curricula with distinct two-dimensional layouts."""

    level_serials: dict[str, int] = {level: 0 for level in CONSTRUCTIVE_COUNTS}
    specs: list[SpatialSpec] = []
    for module in _curriculum_specs():
        serial = level_serials[module.difficulty]
        level_serials[module.difficulty] += 1
        stage_count = len(module.stages)
        layout_space = 6**stage_count
        # Five is coprime with every 6**n. Within a level this maps each
        # serial to a different base-6 obstacle sequence without clustering.
        layout_variant = (serial * 5 + stage_count * 104729) % layout_space
        specs.append(SpatialSpec(module=module, layout_variant=layout_variant))
    return tuple(specs)


SPATIAL_CURRICULUM_SPECS: tuple[SpatialSpec, ...] = _spatial_curriculum_specs()


def _main_row(role: Role) -> int:
    return 2 if role == "F" else 7


def _zone_rows(role: Role) -> range:
    return range(1, 5) if role == "F" else range(6, 10)


def _other(role: Role) -> Role:
    return "W" if role == "F" else "F"


def _apply_room_obstacle(rows: list[list[str]], role: Role, pillar_col: int, pattern: int) -> None:
    main = _main_row(role)
    if pattern < 3:
        for offset in range(pattern + 1):
            rows[main][pillar_col + offset] = "#"
        return
    if pattern == 4:
        rows[main][pillar_col] = "#"
        rows[main - 1][pillar_col + 2] = "#"
        return
    if pattern == 5:
        rows[main][pillar_col] = "#"
        rows[main + 1][pillar_col + 2] = "#"
        return
    # A staggered chicane preserves a route but requires switching branches.
    rows[main][pillar_col] = "#"
    rows[main - 1][pillar_col + 2] = "#"
    rows[main + 1][pillar_col - 2] = "#"


def build_spatial_map(spec: SpatialSpec) -> tuple[str, SymbolMap, list[dict[str, Any]]]:
    """Build two separated 2-D room networks with real branch choices."""

    stage_width = 9
    width = 11 + len(spec.module.stages) * stage_width
    rows = [list("#" * width) for _ in range(11)]
    for row in (*_zone_rows("F"), *_zone_rows("W")):
        rows[row] = list("#" + "." * (width - 2) + "#")
    rows[_main_row("F")][1], rows[_main_row("W")][1] = "F", "W"
    rows[_main_row("F")][width - 2], rows[_main_row("W")][width - 2] = "f", "w"

    directives = [
        "@format fwcollab.symbol_map.v3",
        f"@id {spec.module.id}",
        f"@title {spec.module.title}",
        "@max_steps 1",
    ]
    metadata: list[dict[str, Any]] = []
    plate_serial = 0
    controller_serial = 0
    variant_code = spec.layout_variant

    for stage_index, stage in enumerate(spec.module.stages, start=1):
        gate_col = stage_index * stage_width
        pillar_col = gate_col - 4
        pattern = variant_code % 6
        variant_code //= 6
        for role in ("F", "W"):
            _apply_room_obstacle(rows, role, pillar_col, pattern)
            for row in _zone_rows(role):
                rows[row][gate_col] = "#"
            rows[_main_row(role)][gate_col] = "."

        supporter = stage.supporter
        traveler = _other(supporter)
        controller_records: list[dict[str, Any]] = []
        controller_ids: list[str] = []
        for offset, kind in enumerate(stage.controllers):
            controller_serial += 1
            branch_delta = -1 if (spec.layout_variant + stage_index + offset) % 2 == 0 else 1
            row = _main_row(supporter) + branch_delta
            col = pillar_col + min(offset, 1)
            instance_id = f"c{controller_serial}_{kind}"
            if kind == "plate":
                plate_serial += 1
                glyph = str(plate_serial)
                directives.append(f"@controller {instance_id} plate at {row},{col} accepts {supporter}")
            else:
                glyph = "L" if kind == "lever" else "T"
                directives.append(f"@controller {instance_id} {kind} at {row},{col}")
            if rows[row][col] == "#":
                raise AssertionError(f"controller {instance_id} conflicts with obstacle")
            rows[row][col] = glyph
            controller_ids.append(instance_id)
            controller_records.append({"id": instance_id, "kind": kind, "position": [row, col]})

        actuator_id = f"a{stage_index}_{stage.actuator}"
        actuator_position = Position(_main_row(traveler), gate_col)
        rows[actuator_position.row][actuator_position.col] = (
            chr(ord("A") + (stage_index - 1) % 5) if stage.actuator == "door" else "="
        )
        expression = f"{stage.mode}({','.join(controller_ids)})"
        directives.append(
            f"@actuator {actuator_id} {stage.actuator} at {actuator_position.row},{actuator_position.col} "
            f"controlled_by {expression}"
        )
        metadata.append(
            {
                "stage": stage_index,
                "supporter": supporter,
                "traveler": traveler,
                "controllers": controller_records,
                "actuator": {
                    "id": actuator_id,
                    "kind": stage.actuator,
                    "positions": [[actuator_position.row, actuator_position.col]],
                },
                "mode": stage.mode,
                "room": {"gate_col": gate_col, "obstacle_pattern": pattern},
            }
        )

    text = "\n".join([*directives, "---", *("".join(row) for row in rows), ""])
    return text, parse_symbol_map(text, source=f"spatial:{spec.module.id}"), metadata


def _shortest_path(world: SymbolWorld, role: Role, target: Position) -> list[str]:
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
            candidate = Position(current.row + delta[0], current.col + delta[1])
            if candidate in seen or world._actor_cell_error(role, candidate, doors, move):
                continue
            seen.add(candidate)
            previous[candidate] = (current, move)
            if candidate == target:
                path: list[str] = []
                cursor = candidate
                while cursor != start:
                    cursor, step = previous[cursor]
                    path.append(step)
                path.reverse()
                return path
            queue.append(candidate)
    raise AssertionError(f"{world.map.map_id}: {role} cannot reach {target}")


def spatial_witness(symbol_map: SymbolMap, metadata: list[dict[str, Any]]) -> list[dict[str, list[object]]]:
    world = SymbolWorld(symbol_map)
    rounds: list[dict[str, list[object]]] = []

    def move_to(role: Role, target: Position) -> None:
        for move in _shortest_path(world, role, target):
            joint = {
                role: SymbolAction(move=move, steps=1),
                _other(role): SymbolAction(move="WAIT", steps=0),
            }
            transition = world.step_joint(joint)
            if transition.feedback[role] not in {"moved", "moved_via_portal"}:
                raise AssertionError(f"{symbol_map.map_id}: witness move blocked: {role} {move}")
            rounds.append({item: [action.move, action.steps] for item, action in joint.items()})

    for stage in metadata:
        supporter: Role = stage["supporter"]
        traveler: Role = stage["traveler"]
        gate_col = stage["room"]["gate_col"]
        controllers = sorted(stage["controllers"], key=lambda item: item["kind"] == "plate")
        has_plate = any(item["kind"] == "plate" for item in controllers)
        for controller in controllers:
            if controller["kind"] == "plate":
                move_to(traveler, Position(_main_row(traveler), gate_col - 1))
            move_to(supporter, Position(*controller["position"]))
        if not world._actuator_on(next(item for item in symbol_map.actuators if item.id == stage["actuator"]["id"])):
            raise AssertionError(f"{symbol_map.map_id}: stage actuator did not activate")
        move_to(traveler, Position(_main_row(traveler), gate_col + 1))
        if has_plate and world.controller_states()[next(item["id"] for item in controllers if item["kind"] == "plate")]:
            # Leaving is intentionally delayed until the traveler is completely beyond the gate.
            pass
        move_to(supporter, Position(_main_row(supporter), gate_col + 1))

    move_to("F", symbol_map.unique_position("f"))
    move_to("W", symbol_map.unique_position("w"))
    if world.status != "team_success":
        raise AssertionError(f"spatial witness failed for {symbol_map.map_id}")
    return rounds


def _topology_signature(symbol_map: SymbolMap) -> str:
    mask = ["".join("#" if cell == "#" else "." for cell in row) for row in symbol_map.rows]
    return hashlib.sha256("\n".join(mask).encode("utf-8")).hexdigest()


def _spatial_metrics(symbol_map: SymbolMap, rounds: list[dict[str, list[object]]]) -> dict[str, int]:
    walkable = {
        Position(row, col)
        for row, line in enumerate(symbol_map.rows)
        for col, cell in enumerate(line)
        if cell != "#"
    }
    branch_points = 0
    for position in walkable:
        degree = sum(
            Position(position.row + delta[0], position.col + delta[1]) in walkable
            for delta in DIRECTIONS.values()
        )
        branch_points += degree >= 3
    vertical_steps = sum(
        joint[role][0] in {"UP", "DOWN"}
        for joint in rounds
        for role in ("F", "W")
    )
    return {"walkable_cells": len(walkable), "branch_points": branch_points, "vertical_witness_steps": vertical_steps}


def annotate_trace_dag_progress(trace: dict[str, object], record: dict[str, Any]) -> None:
    """Attach private post-run DAG progress; this data is never an agent observation."""

    dag = json.loads(Path(record["dag"]).read_text(encoding="utf-8"))
    nodes = dag["nodes"]
    state_to_node = {node["predicate"]["state"]: node["id"] for node in nodes}
    stages = record["stages"]
    trace_rounds = trace["rounds"]
    observations = [trace_rounds[0]["observation"], *(item["result"]["observation"] for item in trace_rounds)]
    completed: set[str] = set()
    timeline: list[dict[str, object]] = []
    for round_index, observation in enumerate(observations):
        state = observation["state"]
        true_states = {"task_ready"}
        true_states.update(f"{key}_on" for key, value in state["controllers"].items() if value)
        true_states.update(f"{key}_on" for key, value in state["doors"].items() if value)
        true_states.update(f"{key}_on" for key, value in state["platforms"].items() if value)
        for stage in stages:
            traveler = stage["traveler"]
            gate_col = stage["room"]["gate_col"]
            if state["actors"][traveler][1] > gate_col:
                true_states.add(f"{stage['actuator']['id']}_crossed")
        if observation["status"] == "team_success":
            true_states.add("all_required_goals")
        completed.update(state_to_node[item] for item in true_states if item in state_to_node)
        timeline.append(
            {
                "round": round_index,
                "completed_nodes": sorted(completed),
                "completed": len(completed),
                "total": len(nodes),
            }
        )
    trace["private_evaluation"] = {
        "visibility": "private_post_run_only",
        "dag_id": dag["id"],
        "progress": timeline,
    }


def write_spatial_witness_traces(
    manifest: dict[str, Any], output_root: str | Path, witness_path: str | Path
) -> list[Path]:
    """Create deterministic replayable traces and HTML for all spatial core maps."""

    destination = Path(output_root)
    destination.mkdir(parents=True, exist_ok=True)
    witness_maps = json.loads(Path(witness_path).read_text(encoding="utf-8"))["maps"]
    generated: list[Path] = []
    index_rows: list[str] = []
    for record in manifest["records"]:
        symbol_map = parse_symbol_map(Path(record["map"]).read_text(encoding="utf-8"), source=record["map"])
        decisions = {"F": [], "W": []}
        for joint in witness_maps[record["id"]]:
            for role in ("F", "W"):
                decisions[role].append(
                    AgentDecision(
                        action=SymbolAction(move=joint[role][0], steps=joint[role][1]),
                        reason="private spatial acceptance witness",
                    )
                )
        result = DualAgentSession(
            symbol_map,
            {role: ScriptedPolicy(decisions[role], name=f"private-witness-{role}") for role in ("F", "W")},
            max_rounds=len(witness_maps[record["id"]]),
        ).run()
        if result.trace["outcome"] != "team_success":
            raise AssertionError(f"witness trace failed for {record['id']}")
        annotate_trace_dag_progress(result.trace, record)
        json_path = destination / f"{record['id']}.json"
        html_path = destination / f"{record['id']}.html"
        save_trace(json_path, result.trace)
        save_trace_html(html_path, result.trace)
        generated.extend((json_path, html_path))
        index_rows.append(
            f'<li><a href="{record["id"]}.html">{record["id"]} · {record["difficulty"]} · '
            f'{record["witness_rounds"]} rounds</a></li>'
        )
    index = destination / "index.html"
    index.write_text(
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>FWCollab 二维核心轨迹</title>"
        "<style>:root{color-scheme:dark;font-family:system-ui}body{max-width:900px;margin:auto;padding:24px;background:#0b1220;"
        "color:#e2e8f0}li{margin:10px;padding:12px;background:#111c31;border-radius:8px}a{color:#7dd3fc}</style></head>"
        f"<body><h1>12 张二维核心图见证轨迹</h1><p>仅供开发验收，不注入游戏 agent。</p><ol>{''.join(index_rows)}</ol></body></html>",
        encoding="utf-8",
    )
    # Keep the index title accurate for both the 12-map core and the 72-map
    # curriculum. ASCII static labels also avoid terminal-codepage ambiguity.
    index.write_text(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<title>FWCollab spatial witness traces</title>"
        "<style>:root{color-scheme:dark;font-family:system-ui}body{max-width:900px;margin:auto;padding:24px;"
        "background:#0b1220;color:#e2e8f0}li{margin:10px;padding:12px;background:#111c31;border-radius:8px}"
        "a{color:#7dd3fc}</style></head><body>"
        f"<h1>{len(manifest['records'])} spatial witness traces</h1>"
        "<p>Private development witnesses; never injected into a model observation.</p>"
        f"<ol>{''.join(index_rows)}</ol></body></html>",
        encoding="utf-8",
    )
    generated.append(index)
    return generated


def write_spatial_core(
    output_root: str | Path,
    gallery_path: str | Path | None = None,
    trace_root: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(output_root)
    records: list[dict[str, Any]] = []
    witnesses: dict[str, list[dict[str, list[object]]]] = {}
    topology_signatures: set[str] = set()
    maps: list[SymbolMap] = []
    for spec in SPATIAL_SPECS:
        text, symbol_map, metadata = build_spatial_map(spec)
        rounds = spatial_witness(symbol_map, metadata)
        dag = _derive_dag(spec.module, metadata)
        node_bindings, edge_evidence = _binding_and_edge_evidence(symbol_map, rounds, dag, metadata)
        for evidence in edge_evidence:
            if evidence["method"] == "single_lane_corridor_cut":
                evidence["method"] = "two_dimensional_room_cut"
                evidence["detail"] = "a full room wall leaves this actuator as the traveler's only crossing"
            elif evidence["method"] == "ordered_single_lane_cut":
                evidence["method"] = "witnessed_room_order"
                evidence["detail"] = "the preceding room crossing is observed before this controller becomes reachable"
            elif evidence["method"] == "initial_reachability":
                evidence["method"] = "branch_route_reachability"
                evidence["detail"] = "the controller is reachable through an upper or lower room branch"
        signature = _topology_signature(symbol_map)
        if signature in topology_signatures:
            raise AssertionError(f"duplicate spatial topology for {symbol_map.map_id}")
        topology_signatures.add(signature)
        map_path = root / "maps" / f"{symbol_map.map_id}.fwmap"
        dag_path = root / "dags" / f"DAG-{symbol_map.map_id}.json"
        map_path.parent.mkdir(parents=True, exist_ok=True)
        map_path.write_text(text, encoding="utf-8")
        save_json(dag_path, dag)
        maps.append(symbol_map)
        witnesses[symbol_map.map_id] = rounds
        metrics = _spatial_metrics(symbol_map, rounds)
        if metrics["branch_points"] < 2 or metrics["vertical_witness_steps"] < 1:
            raise AssertionError(f"{symbol_map.map_id} is not meaningfully two-dimensional")
        records.append(
            {
                "id": symbol_map.map_id,
                "difficulty": spec.module.difficulty,
                "map": map_path.as_posix(),
                "dag": dag_path.as_posix(),
                "stages": metadata,
                "witness_rounds": len(rounds),
                "topology_signature": signature,
                "spatial_metrics": metrics,
                "node_bindings": node_bindings,
                "edge_evidence": edge_evidence,
                "all_nodes_bound": all(item["verified"] for item in node_bindings),
                "all_edges_verified": all(item["verified"] for item in edge_evidence),
                "status": "spatial_core_verified",
            }
        )
    (root / "witnesses.json").write_text(
        json.dumps({"format": "fwcollab.private_witnesses.v2", "maps": witnesses}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "format": "fwcollab.symbolic.spatial_core.v1",
        "status": "two_dimensional_controller_core_not_full_M01_M30",
        "count": len(records),
        "topology_unique": len(topology_signatures),
        "records": records,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if gallery_path is not None:
        save_symbol_gallery(gallery_path, maps)
    if trace_root is not None:
        write_spatial_witness_traces(manifest, trace_root, root / "witnesses.json")
    return manifest


def write_spatial_curriculum(
    output_root: str | Path,
    gallery_path: str | Path | None = None,
    trace_root: str | Path | None = None,
) -> dict[str, Any]:
    """Generate the complete 72-map L1-L7 two-dimensional curriculum."""

    root = Path(output_root)
    records: list[dict[str, Any]] = []
    witnesses: dict[str, list[dict[str, list[object]]]] = {}
    topology_signatures: set[str] = set()
    semantic_signatures: set[str] = set()
    motif_coverage: set[str] = set()
    maps: list[SymbolMap] = []
    for spec in SPATIAL_CURRICULUM_SPECS:
        text, symbol_map, metadata = build_spatial_map(spec)
        rounds = spatial_witness(symbol_map, metadata)
        dag = _derive_dag(spec.module, metadata)
        node_bindings, edge_evidence = _binding_and_edge_evidence(symbol_map, rounds, dag, metadata)
        for evidence in edge_evidence:
            if evidence["method"] == "single_lane_corridor_cut":
                evidence["method"] = "two_dimensional_room_cut"
                evidence["detail"] = "a full room wall leaves this actuator as the traveler's only crossing"
            elif evidence["method"] == "ordered_single_lane_cut":
                evidence["method"] = "witnessed_room_order"
                evidence["detail"] = "the preceding room crossing is observed before this controller becomes reachable"
            elif evidence["method"] == "initial_reachability":
                evidence["method"] = "branch_route_reachability"
                evidence["detail"] = "the controller is reachable through an upper or lower room branch"

        topology_signature = _topology_signature(symbol_map)
        if topology_signature in topology_signatures:
            raise AssertionError(f"duplicate spatial topology for {symbol_map.map_id}")
        topology_signatures.add(topology_signature)
        semantic_signature = json.dumps(
            [
                {
                    "supporter": stage.supporter,
                    "controllers": stage.controllers,
                    "actuator": stage.actuator,
                    "mode": stage.mode,
                }
                for stage in spec.module.stages
            ],
            sort_keys=True,
            separators=(",", ":"),
        )
        if semantic_signature in semantic_signatures:
            raise AssertionError(f"duplicate semantic wiring for {symbol_map.map_id}")
        semantic_signatures.add(semantic_signature)
        motif_coverage.update(dag["mechanisms"])

        map_path = root / "maps" / spec.module.difficulty / f"{symbol_map.map_id}.fwmap"
        dag_path = root / "dags" / spec.module.difficulty / f"DAG-{symbol_map.map_id}.json"
        map_path.parent.mkdir(parents=True, exist_ok=True)
        map_path.write_text(text, encoding="utf-8")
        save_json(dag_path, dag)
        maps.append(symbol_map)
        witnesses[symbol_map.map_id] = rounds
        metrics = _spatial_metrics(symbol_map, rounds)
        if metrics["branch_points"] < 2 or metrics["vertical_witness_steps"] < 1:
            raise AssertionError(f"{symbol_map.map_id} is not meaningfully two-dimensional")
        records.append(
            {
                "id": symbol_map.map_id,
                "difficulty": spec.module.difficulty,
                "map": map_path.as_posix(),
                "dag": dag_path.as_posix(),
                "semantic_signature": semantic_signature,
                "topology_signature": topology_signature,
                "stages": metadata,
                "mechanisms": dag["mechanisms"],
                "witness_rounds": len(rounds),
                "spatial_metrics": metrics,
                "node_bindings": node_bindings,
                "edge_evidence": edge_evidence,
                "all_nodes_bound": all(item["verified"] for item in node_bindings),
                "all_edges_verified": all(item["verified"] for item in edge_evidence),
                "status": "spatial_curriculum_verified",
            }
        )

    witness_path = root / "witnesses.json"
    witness_path.parent.mkdir(parents=True, exist_ok=True)
    witness_path.write_text(
        json.dumps({"format": "fwcollab.private_witnesses.v2", "maps": witnesses}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    manifest = {
        "format": "fwcollab.symbolic.spatial_curriculum.v1",
        "status": "two_dimensional_controller_curriculum_not_full_M01_M30",
        "count": len(records),
        "difficulty_distribution": CONSTRUCTIVE_COUNTS,
        "semantic_unique": len(semantic_signatures),
        "topology_unique": len(topology_signatures),
        "mechanism_coverage": sorted(motif_coverage),
        "records": records,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if gallery_path is not None:
        save_symbol_gallery(gallery_path, maps)
    if trace_root is not None:
        write_spatial_witness_traces(manifest, trace_root, witness_path)
    return manifest
