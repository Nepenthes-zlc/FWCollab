"""Build twelve same-DAG/new-layout variants from Diagnostic-24."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from fwcollab.symbolic.constructive import ModuleSpec, StageSpec, _binding_and_edge_evidence, _derive_dag
from fwcollab.symbolic.dag import save_json, validate_state_dag
from fwcollab.symbolic.full_spatial import _append_capability_dag, build_full_spatial_map, full_spatial_witness
from fwcollab.symbolic.map import load_symbol_map
from fwcollab.symbolic.runner import map_fingerprint
from fwcollab.symbolic.spatial import SpatialSpec, _spatial_metrics, _topology_signature


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "eval_private" / "spatial_curriculum_full_v4"
DIAGNOSTIC = ROOT / "eval_private" / "collaboration_diagnostic_24" / "manifest.json"
OUT = ROOT / "eval_private" / "heldout_layout_12"
SOURCE_IDS = (
    "V4-L1-001",
    "V4-L2-008",
    "V4-L3-010",
    "V4-L3-004",
    "V4-L4-009",
    "V4-L4-012",
    "V4-L5-011",
    "V4-L5-004",
    "V4-L6-010",
    "V4-L6-007",
    "V4-L7-009",
    "V4-L7-002",
)


def _stage_spec(stage: dict[str, Any]) -> StageSpec:
    return StageSpec(
        stage["supporter"],
        tuple(controller["kind"] for controller in stage["controllers"]),
        stage["actuator"]["kind"],
        stage["mode"],
    )


def _source_variant(stages: list[dict[str, Any]]) -> int:
    return sum(int(stage["room"]["obstacle_pattern"]) * (6 ** index) for index, stage in enumerate(stages))


def _dag_payload(dag: dict[str, Any]) -> dict[str, Any]:
    return {
        "difficulty": dag["difficulty"],
        "evaluation_role": dag["evaluation_role"],
        "topology_family": dag["topology_family"],
        "mechanisms": dag["mechanisms"],
        "nodes": dag["nodes"],
        "edges": dag["edges"],
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "maps").mkdir(exist_ok=True)
    (OUT / "dags").mkdir(exist_ok=True)
    full = json.loads((SOURCE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    diagnostic = json.loads(DIAGNOSTIC.read_text(encoding="utf-8"))
    source_by_id = {record["id"]: record for record in full["records"]}
    diagnostic_ids = {record["id"] for record in diagnostic["records"]}
    if not set(SOURCE_IDS) <= diagnostic_ids:
        raise RuntimeError("held-out sources must all belong to Diagnostic-24")
    existing_topologies = {record["topology_signature"] for record in full["records"]}
    existing_fingerprints = {
        map_fingerprint(load_symbol_map(record["map"]))
        for record in full["records"]
    }
    heldout_topologies: set[str] = set()
    records = []
    witnesses: dict[str, Any] = {}

    for ordinal, source_id in enumerate(SOURCE_IDS, start=1):
        source = source_by_id[source_id]
        source_dag = json.loads((ROOT / source["dag"]).read_text(encoding="utf-8"))
        stages = source["stages"]
        old_variant = _source_variant(stages)
        stage_specs = tuple(_stage_spec(stage) for stage in stages)
        heldout_id = f"HG-{ordinal:03d}"
        module = ModuleSpec(
            heldout_id,
            f"held-out layout paired with {source_id}",
            source["difficulty"],
            stage_specs,
        )
        layout_space = 6 ** len(stage_specs)
        selected = None
        for offset in range(1, layout_space):
            candidate = (old_variant + offset * 5) % layout_space
            if candidate == old_variant:
                continue
            text, symbol_map, new_stages, capability = build_full_spatial_map(
                SpatialSpec(module, candidate), source["primary_capability"]
            )
            topology = _topology_signature(symbol_map)
            fingerprint = map_fingerprint(symbol_map)
            if topology in existing_topologies or topology in heldout_topologies or fingerprint in existing_fingerprints:
                continue
            rounds, capability_evidence = full_spatial_witness(symbol_map, new_stages, capability)
            dag = _derive_dag(replace(module, id=symbol_map.map_id), new_stages)
            base_bindings, base_edges = _binding_and_edge_evidence(symbol_map, rounds, dag, new_stages)
            dag, extra_bindings, extra_edges = _append_capability_dag(
                dag, source["primary_capability"], capability, capability_evidence
            )
            validate_state_dag(dag)
            if _dag_payload(dag) != _dag_payload(source_dag):
                raise RuntimeError(f"same-DAG invariant failed for {source_id}")
            edge_keys = {(edge["from"], edge["to"], edge["relation"]) for edge in dag["edges"]}
            edge_evidence = [
                *[item for item in base_edges if (item["from"], item["to"], item["relation"]) in edge_keys],
                *extra_edges,
            ]
            node_bindings = [*base_bindings, *extra_bindings]
            if {item["dag_node_id"] for item in node_bindings if item["verified"]} != {node["id"] for node in dag["nodes"]}:
                raise RuntimeError(f"incomplete node evidence for {source_id}")
            if {(item["from"], item["to"], item["relation"]) for item in edge_evidence if item["verified"]} != edge_keys:
                raise RuntimeError(f"incomplete edge evidence for {source_id}")
            selected = (candidate, text, symbol_map, new_stages, capability, rounds, dag, topology, fingerprint, node_bindings, edge_evidence)
            break
        if selected is None:
            raise RuntimeError(f"no unseen topology found for {source_id}")
        candidate, text, symbol_map, new_stages, capability, rounds, dag, topology, fingerprint, node_bindings, edge_evidence = selected
        heldout_topologies.add(topology)
        map_path = OUT / "maps" / f"{heldout_id}.fwmap"
        dag_path = OUT / "dags" / f"DAG-{heldout_id}.json"
        map_path.write_text(text, encoding="utf-8")
        save_json(dag_path, dag)
        witnesses[heldout_id] = rounds
        records.append({
            "id": heldout_id,
            "difficulty": source["difficulty"],
            "map": map_path.relative_to(ROOT).as_posix(),
            "dag": dag_path.relative_to(ROOT).as_posix(),
            "source_id": source_id,
            "source_map": source["map"],
            "source_dag": source["dag"],
            "primary_capability": source["primary_capability"],
            "mechanisms": source["mechanisms"],
            "semantic_signature": source["semantic_signature"],
            "source_topology_signature": source["topology_signature"],
            "topology_signature": topology,
            "map_fingerprint": fingerprint,
            "source_layout_variant": old_variant,
            "heldout_layout_variant": candidate,
            "stages": new_stages,
            "capability": capability,
            "witness_rounds": len(rounds),
            "spatial_metrics": _spatial_metrics(symbol_map, rounds),
            "node_bindings": node_bindings,
            "edge_evidence": edge_evidence,
            "all_nodes_bound": True,
            "all_edges_verified": True,
            "capability_verified": True,
            "same_dag_verified": True,
            "unseen_topology_verified": True,
        })

    manifest = {
        "format": "fwcollab.heldout_layout_suite.v1",
        "selection_uses_model_outcomes": False,
        "intervention": "same_executable_dag_new_spatial_layout",
        "count": len(records),
        "source_set": "Diagnostic-24",
        "records": records,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "witnesses.json").write_text(json.dumps({"format": "fwcollab.private_witnesses.v1", "maps": witnesses}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tasks": len(records), "unique_new_topologies": len(heldout_topologies), "all_same_dag": True, "all_witnessed": True}))


if __name__ == "__main__":
    main()
