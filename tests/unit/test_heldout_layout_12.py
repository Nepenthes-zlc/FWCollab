from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _dag_payload(dag):
    return {key: dag[key] for key in ("difficulty", "evaluation_role", "topology_family", "mechanisms", "nodes", "edges")}


def test_heldout_layout_suite_is_paired_and_structurally_valid() -> None:
    manifest = json.loads((ROOT / "eval_private/heldout_layout_12/manifest.json").read_text(encoding="utf-8"))
    full = json.loads((ROOT / "eval_private/spatial_curriculum_full_v4/manifest.json").read_text(encoding="utf-8"))
    source = {record["id"]: record for record in full["records"]}
    assert manifest["count"] == len(manifest["records"]) == 12
    assert manifest["selection_uses_model_outcomes"] is False
    assert len({record["source_id"] for record in manifest["records"]}) == 12
    assert len({record["topology_signature"] for record in manifest["records"]}) == 12
    old_topologies = {record["topology_signature"] for record in full["records"]}
    for record in manifest["records"]:
        assert record["topology_signature"] not in old_topologies
        assert record["topology_signature"] != record["source_topology_signature"]
        assert record["semantic_signature"] == source[record["source_id"]]["semantic_signature"]
        assert record["same_dag_verified"] and record["all_nodes_bound"] and record["all_edges_verified"]
        heldout_dag = json.loads((ROOT / record["dag"]).read_text(encoding="utf-8"))
        source_dag = json.loads((ROOT / record["source_dag"]).read_text(encoding="utf-8"))
        assert _dag_payload(heldout_dag) == _dag_payload(source_dag)


def test_heldout_experiment_is_only_normal_selfplay() -> None:
    spec = json.loads((ROOT / "eval_private/heldout_layout_12/experiment_spec.json").read_text(encoding="utf-8"))
    assert [condition["id"] for condition in spec["conditions"]] == ["gpt_selfplay", "gemini_selfplay"]
    assert spec["replicates"] == [1, 2, 3]
    assert spec["runner"]["max_rounds"] == 80
    assert 12 * len(spec["conditions"]) * len(spec["replicates"]) == 72
