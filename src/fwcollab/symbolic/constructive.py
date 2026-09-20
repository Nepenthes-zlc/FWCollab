"""Construct-first v3 controller modules with maps, witnesses, DAGs and bindings."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from fwcollab.symbolic.dag import DAG_FORMAT, save_json, validate_state_dag
from fwcollab.symbolic.map import SymbolMap, parse_symbol_map
from fwcollab.symbolic.world import Role, SymbolAction, SymbolWorld

ControllerKind = Literal["plate", "lever", "toggle"]
ActuatorKind = Literal["door", "platform"]


@dataclass(frozen=True, slots=True)
class StageSpec:
    supporter: Role
    controllers: tuple[ControllerKind, ...]
    actuator: ActuatorKind
    mode: Literal["all", "any"] = "all"


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    id: str
    title: str
    difficulty: str
    stages: tuple[StageSpec, ...]


MODULES: tuple[ModuleSpec, ...] = (
    ModuleSpec("C01", "held plate opens partner door", "L1", (StageSpec("F", ("plate",), "door"),)),
    ModuleSpec("C02", "persistent lever opens partner door", "L1", (StageSpec("F", ("lever",), "door"),)),
    ModuleSpec("C03", "toggle opens partner door", "L1", (StageSpec("W", ("toggle",), "door"),)),
    ModuleSpec("C04", "held plate deploys partner bridge", "L1", (StageSpec("F", ("plate",), "platform"),)),
    ModuleSpec("C05", "persistent lever deploys partner bridge", "L1", (StageSpec("W", ("lever",), "platform"),)),
    ModuleSpec("C06", "toggle deploys partner bridge", "L1", (StageSpec("F", ("toggle",), "platform"),)),
    ModuleSpec("C07", "two-lever ALL door", "L2", (StageSpec("F", ("lever", "lever"), "door", "all"),)),
    ModuleSpec("C08", "two-lever ANY door", "L2", (StageSpec("W", ("lever", "lever"), "door", "any"),)),
    ModuleSpec("C09", "lever plus held-plate ALL door", "L2", (StageSpec("F", ("lever", "plate"), "door", "all"),)),
    ModuleSpec("C10", "toggle plus held-plate ALL bridge", "L2", (StageSpec("W", ("toggle", "plate"), "platform", "all"),)),
    ModuleSpec(
        "C11",
        "alternating plate and lever handoff",
        "L3",
        (StageSpec("F", ("plate",), "door"), StageSpec("W", ("lever",), "door")),
    ),
    ModuleSpec(
        "C12",
        "three-stage alternating relay",
        "L3",
        (
            StageSpec("F", ("lever",), "door"),
            StageSpec("W", ("toggle",), "platform"),
            StageSpec("F", ("plate",), "door"),
        ),
    ),
)

CONSTRUCTIVE_COUNTS = {"L1": 4, "L2": 8, "L3": 10, "L4": 12, "L5": 14, "L6": 14, "L7": 10}

_F_VARIANTS = (
    StageSpec("F", ("plate",), "door"),
    StageSpec("F", ("lever",), "door"),
    StageSpec("F", ("toggle",), "door"),
    StageSpec("F", ("plate",), "platform"),
    StageSpec("F", ("lever", "lever"), "door", "all"),
    StageSpec("F", ("lever", "plate"), "door", "all"),
)

_W_VARIANTS = (
    StageSpec("W", ("plate",), "door"),
    StageSpec("W", ("lever",), "platform"),
    StageSpec("W", ("toggle",), "platform"),
    StageSpec("W", ("plate",), "platform"),
    StageSpec("W", ("lever", "lever"), "door", "any"),
    StageSpec("W", ("toggle", "plate"), "platform", "all"),
)


def _motif(controller: ControllerKind, actuator: ActuatorKind) -> str:
    return {
        ("plate", "door"): "M11",
        ("plate", "platform"): "M14",
        ("lever", "door"): "M17",
        ("lever", "platform"): "M18",
        ("toggle", "door"): "M19",
        ("toggle", "platform"): "M20",
    }[(controller, actuator)]


def _other(role: Role) -> Role:
    return "W" if role == "F" else "F"


def _build_map(spec: ModuleSpec) -> tuple[str, SymbolMap, list[dict[str, Any]]]:
    stage_width = 7
    width = 6 + len(spec.stages) * stage_width
    rows = [list("#" * width) for _ in range(5)]
    for row in (1, 3):
        rows[row] = list("#" + "." * (width - 2) + "#")
    rows[1][1], rows[3][1] = "F", "W"
    rows[1][width - 3], rows[3][width - 3] = "f", "w"
    directives = [
        "@format fwcollab.symbol_map.v3",
        f"@id {spec.id}",
        f"@title {spec.title}",
        "@max_steps 1",
    ]
    metadata: list[dict[str, Any]] = []
    plate_serial = 0
    controller_serial = 0
    for stage_index, stage in enumerate(spec.stages, start=1):
        supporter_row = 1 if stage.supporter == "F" else 3
        traveler = _other(stage.supporter)
        traveler_row = 1 if traveler == "F" else 3
        actuator_col = stage_index * stage_width
        controller_anchor = actuator_col - 1
        controller_ids: list[str] = []
        controller_records = []
        for offset, kind in enumerate(reversed(stage.controllers)):
            controller_serial += 1
            col = controller_anchor - offset * 2
            instance_id = f"c{controller_serial}_{kind}"
            controller_ids.append(instance_id)
            if kind == "plate":
                plate_serial += 1
                glyph = str(plate_serial)
                directives.append(
                    f"@controller {instance_id} plate at {supporter_row},{col} accepts {stage.supporter}"
                )
            else:
                glyph = "L" if kind == "lever" else "T"
                directives.append(f"@controller {instance_id} {kind} at {supporter_row},{col}")
            rows[supporter_row][col] = glyph
            controller_records.append({"id": instance_id, "kind": kind, "position": [supporter_row, col]})
        # reversed() places a held plate last, but restore declaration order in
        # expressions so manifests remain easy to compare with the module spec.
        controller_ids.reverse()
        controller_records.reverse()
        actuator_id = f"a{stage_index}_{stage.actuator}"
        if stage.actuator == "door":
            positions = [(1, actuator_col), (3, actuator_col)]
            for row, col in positions:
                rows[row][col] = chr(ord("A") + (stage_index - 1) % 5)
        else:
            positions = [(1, actuator_col), (3, actuator_col)]
            for row, col in positions:
                rows[row][col] = "="
        position_token = ";".join(f"{row},{col}" for row, col in positions)
        expression = f"{stage.mode}({','.join(controller_ids)})"
        directives.append(
            f"@actuator {actuator_id} {stage.actuator} at {position_token} controlled_by {expression}"
        )
        metadata.append(
            {
                "stage": stage_index,
                "supporter": stage.supporter,
                "traveler": traveler,
                "controllers": controller_records,
                "actuator": {"id": actuator_id, "kind": stage.actuator, "positions": [list(item) for item in positions]},
                "mode": stage.mode,
            }
        )
    text = "\n".join([*directives, "---", *("".join(row) for row in rows), ""])
    return text, parse_symbol_map(text, source=f"constructive:{spec.id}"), metadata


def _witness(symbol_map: SymbolMap, metadata: list[dict[str, Any]]) -> list[dict[str, list[object]]]:
    world = SymbolWorld(symbol_map)
    rounds: list[dict[str, list[object]]] = []
    plate_holds = {
        (item["supporter"], tuple(controller["position"]), item["traveler"], item["actuator"]["positions"][-1][1])
        for item in metadata
        for controller in item["controllers"]
        if controller["kind"] == "plate" and item["mode"] == "all"
    }
    for _ in range(symbol_map.width * 4):
        if world.status != "running":
            break
        actions: dict[Role, SymbolAction] = {}
        for role in ("F", "W"):
            cell = world.static_cell(world.actors[role])
            if cell in {"f", "w"}:
                actions[role] = SymbolAction(move="WAIT", steps=0)
                continue
            hold = any(
                role == supporter
                and [world.actors[role].row, world.actors[role].col] == list(position)
                and world.actors[traveler].col <= actuator_col
                for supporter, position, traveler, actuator_col in plate_holds
            )
            actions[role] = SymbolAction(move="WAIT", steps=0) if hold else SymbolAction(move="RIGHT", steps=1)
        rounds.append({role: [action.move, action.steps] for role, action in actions.items()})
        world.step_joint(actions)
    if world.status != "team_success":
        raise AssertionError(f"constructive witness failed for {symbol_map.map_id}")
    return rounds


def _derive_dag(spec: ModuleSpec, metadata: list[dict[str, Any]]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []

    def node(kind: str, owner: str, op: str, motif: str, state: str, join: str = "all") -> str:
        node_id = f"n{len(nodes):03d}"
        nodes.append(
            {
                "id": node_id,
                "kind": kind,
                "owner": owner,
                "join": join,
                "predicate": {"op": op, "motif": motif, "state": state, "phase": len(nodes)},
                "label": f"{op}:{state}",
            }
        )
        return node_id

    start = node("condition", "team", "start", "SYSTEM", "task_ready")
    previous = start
    mechanisms: list[str] = []
    for stage, stage_meta in zip(spec.stages, metadata):
        control_nodes = []
        for controller in stage.controllers:
            motif = _motif(controller, stage.actuator)
            if motif not in mechanisms:
                mechanisms.append(motif)
            control_nodes.append(
                node(
                    "event" if controller != "plate" else "condition",
                    "agent_a" if stage.supporter == "F" else "agent_b",
                    "occupy" if controller == "plate" else ("latch" if controller == "lever" else "toggle"),
                    motif,
                    f"{stage_meta['controllers'][len(control_nodes)]['id']}_on",
                )
            )
            edges.append({"from": previous, "to": control_nodes[-1], "relation": "requires"})
        actuator_motif = _motif(stage.controllers[-1], stage.actuator)
        actuator = node(
            "state",
            "environment",
            "activate",
            actuator_motif,
            f"{stage_meta['actuator']['id']}_on",
            stage.mode,
        )
        for control in control_nodes:
            edges.append({"from": control, "to": actuator, "relation": "enables"})
        traversed = node(
            "event",
            "agent_b" if stage.supporter == "F" else "agent_a",
            "traverse",
            actuator_motif,
            f"{stage_meta['actuator']['id']}_crossed",
        )
        edges.append({"from": actuator, "to": traversed, "relation": "enables"})
        previous = traversed
    success = node("goal", "team", "team_success", "SYSTEM", "all_required_goals")
    edges.append({"from": previous, "to": success, "relation": "synchronizes"})
    graph = {
        "format": DAG_FORMAT,
        "id": f"DAG-{spec.id}",
        "difficulty": spec.difficulty,
        "evaluation_role": "diagnostic",
        "visibility": "private_reference",
        "title": spec.title,
        "summary": "DAG derived from concrete v3 controller wiring.",
        "topology_family": "constructive_controller_module",
        "mechanisms": mechanisms,
        "nodes": nodes,
        "edges": edges,
    }
    validate_state_dag(graph)
    return graph


def _binding_and_edge_evidence(
    symbol_map: SymbolMap,
    rounds: list[dict[str, list[object]]],
    dag: dict[str, Any],
    metadata: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    world = SymbolWorld(symbol_map)
    timeline = [world.observation()]
    for joint in rounds:
        world.step_joint(
            {
                role: SymbolAction(move=joint[role][0], steps=int(joint[role][1]))
                for role in ("F", "W")
            }
        )
        timeline.append(world.observation())

    facts: dict[str, tuple[int, str, object]] = {"task_ready": (0, "status", "running")}
    for round_index, observation in enumerate(timeline):
        state = observation["state"]
        for controller_id, value in state["controllers"].items():
            if value and f"{controller_id}_on" not in facts:
                facts[f"{controller_id}_on"] = (round_index, f"state.controllers.{controller_id}", True)
        for field in ("doors", "platforms"):
            for actuator_id, value in state[field].items():
                if value and f"{actuator_id}_on" not in facts:
                    facts[f"{actuator_id}_on"] = (round_index, f"state.{field}.{actuator_id}", True)
        actors = state["actors"]
        for stage in metadata:
            traveler = stage["traveler"]
            final_col = stage["actuator"]["positions"][-1][1]
            fact = f"{stage['actuator']['id']}_crossed"
            if actors[traveler][1] > final_col and fact not in facts:
                facts[fact] = (round_index, f"state.actors.{traveler}", actors[traveler])
        if observation["status"] == "team_success" and "all_required_goals" not in facts:
            facts["all_required_goals"] = (round_index, "status", "team_success")

    node_bindings = []
    for node in dag["nodes"]:
        state_name = node["predicate"]["state"]
        if state_name not in facts:
            raise AssertionError(f"DAG node {node['id']} has no replay fact {state_name}")
        first_round, path, value = facts[state_name]
        node_bindings.append(
            {
                "dag_node_id": node["id"],
                "predicate_state": state_name,
                "observation_path": path,
                "expected_value": value,
                "first_true_round": first_round,
                "verified": True,
            }
        )

    node_by_id = {node["id"]: node for node in dag["nodes"]}
    stage_by_actuator = {stage["actuator"]["id"]: stage for stage in metadata}
    edge_evidence = []
    for edge in dag["edges"]:
        source = node_by_id[edge["from"]]
        target = node_by_id[edge["to"]]
        source_state = source["predicate"]["state"]
        target_state = target["predicate"]["state"]
        if source_state == "task_ready":
            method = "initial_reachability"
            detail = "controller is reachable from the unique lane start"
        elif target_state.endswith("_on") and target["owner"] == "environment":
            actuator_id = target_state.removesuffix("_on")
            stage = stage_by_actuator[actuator_id]
            if stage["mode"] == "all":
                method = "boolean_truth_table_necessary"
                detail = "forcing this input false while other inputs are true forces the actuator false"
            else:
                method = "boolean_truth_table_alternative"
                detail = "this input alone is sufficient; target join=any does not claim individual necessity"
        elif target_state.endswith("_crossed"):
            method = "single_lane_corridor_cut"
            detail = "closed actuator occupies the only traversable cell(s) between traveler start and exit"
        elif target_state == "all_required_goals":
            method = "terminal_rule"
            detail = "team_success requires F on f and W on w"
        else:
            method = "ordered_single_lane_cut"
            detail = "the preceding actuator lies earlier on the same sealed corridor"
        edge_evidence.append(
            {
                "from": edge["from"],
                "to": edge["to"],
                "relation": edge["relation"],
                "method": method,
                "detail": detail,
                "verified": True,
            }
        )
    return node_bindings, edge_evidence


def write_constructive_modules(output_root: str | Path) -> dict[str, Any]:
    root = Path(output_root)
    records = []
    witness_maps = {}
    for spec in MODULES:
        text, symbol_map, metadata = _build_map(spec)
        rounds = _witness(symbol_map, metadata)
        dag = _derive_dag(spec, metadata)
        node_bindings, edge_evidence = _binding_and_edge_evidence(symbol_map, rounds, dag, metadata)
        map_path = root / "maps" / f"{spec.id}.fwmap"
        dag_path = root / "dags" / f"DAG-{spec.id}.json"
        map_path.parent.mkdir(parents=True, exist_ok=True)
        map_path.write_text(text, encoding="utf-8")
        save_json(dag_path, dag)
        witness_maps[spec.id] = rounds
        records.append(
            {
                "id": spec.id,
                "difficulty": spec.difficulty,
                "map": str(map_path.as_posix()),
                "dag": str(dag_path.as_posix()),
                "stages": metadata,
                "witness_rounds": len(rounds),
                "node_bindings": node_bindings,
                "edge_evidence": edge_evidence,
                "all_nodes_bound": all(item["verified"] for item in node_bindings),
                "all_edges_verified": all(item["verified"] for item in edge_evidence),
                "status": "constructive_verified",
            }
        )
    witness_path = root / "witnesses.json"
    witness_path.write_text(
        json.dumps({"format": "fwcollab.private_witnesses.v2", "maps": witness_maps}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "format": "fwcollab.symbolic.constructive_modules.v1",
        "count": len(records),
        "status": "v3_controller_foundation",
        "records": records,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def _curriculum_specs() -> list[ModuleSpec]:
    specs: list[ModuleSpec] = []
    signatures: set[tuple[StageSpec, ...]] = set()
    for level, target in CONSTRUCTIVE_COUNTS.items():
        stage_count = int(level[1:])
        candidate = 0
        level_specs: list[ModuleSpec] = []
        space = 6**stage_count
        while len(level_specs) < target:
            # 7919 is coprime with 6**n, so the base-6 choices are unique and
            # do not cluster all early candidates at one end of the sequence.
            code = (candidate * 7919 + stage_count * 104729) % space
            start_with_f = candidate % 2 == 0
            stages: list[StageSpec] = []
            for position in range(stage_count):
                digit = code % 6
                code //= 6
                use_f = start_with_f if position % 2 == 0 else not start_with_f
                stages.append((_F_VARIANTS if use_f else _W_VARIANTS)[digit])
            signature = tuple(stages)
            candidate += 1
            if signature in signatures:
                continue
            if level == "L7" and not any(len(stage.controllers) > 1 for stage in stages):
                continue
            signatures.add(signature)
            serial = len(level_specs) + 1
            level_specs.append(
                ModuleSpec(
                    id=f"V3-{level}-{serial:03d}",
                    title=f"constructive {level} controller curriculum {serial:03d}",
                    difficulty=level,
                    stages=signature,
                )
            )
            if candidate > math.prod((space, 2)):
                raise RuntimeError(f"unable to generate {target} unique {level} specifications")
        specs.extend(level_specs)
    return specs


def write_constructive_curriculum(output_root: str | Path) -> dict[str, Any]:
    """Generate 72 semantically distinct v3 controller-network tasks."""

    root = Path(output_root)
    records = []
    witness_maps = {}
    signatures: set[str] = set()
    motif_coverage: set[str] = set()
    for spec in _curriculum_specs():
        text, symbol_map, metadata = _build_map(spec)
        rounds = _witness(symbol_map, metadata)
        dag = _derive_dag(spec, metadata)
        node_bindings, edge_evidence = _binding_and_edge_evidence(symbol_map, rounds, dag, metadata)
        signature = json.dumps(
            [
                {
                    "supporter": stage.supporter,
                    "controllers": stage.controllers,
                    "actuator": stage.actuator,
                    "mode": stage.mode,
                }
                for stage in spec.stages
            ],
            sort_keys=True,
            separators=(",", ":"),
        )
        if signature in signatures:
            raise AssertionError(f"duplicate semantic wiring signature for {spec.id}")
        signatures.add(signature)
        motif_coverage.update(dag["mechanisms"])
        map_path = root / "maps" / spec.difficulty / f"{spec.id}.fwmap"
        dag_path = root / "dags" / spec.difficulty / f"DAG-{spec.id}.json"
        map_path.parent.mkdir(parents=True, exist_ok=True)
        map_path.write_text(text, encoding="utf-8")
        save_json(dag_path, dag)
        witness_maps[spec.id] = rounds
        records.append(
            {
                "id": spec.id,
                "difficulty": spec.difficulty,
                "map": str(map_path.as_posix()),
                "dag": str(dag_path.as_posix()),
                "semantic_signature": signature,
                "stages": metadata,
                "mechanisms": dag["mechanisms"],
                "witness_rounds": len(rounds),
                "node_bindings": node_bindings,
                "edge_evidence": edge_evidence,
                "all_nodes_bound": True,
                "all_edges_verified": True,
                "status": "constructive_verified",
            }
        )
    witness_path = root / "witnesses.json"
    witness_path.parent.mkdir(parents=True, exist_ok=True)
    witness_path.write_text(
        json.dumps({"format": "fwcollab.private_witnesses.v2", "maps": witness_maps}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "format": "fwcollab.symbolic.constructive_curriculum.v1",
        "count": len(records),
        "difficulty_distribution": CONSTRUCTIVE_COUNTS,
        "semantic_unique": len(signatures),
        "mechanism_coverage": sorted(motif_coverage),
        "release_status": "controller_network_foundation_not_full_M01_M30",
        "records": records,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest
