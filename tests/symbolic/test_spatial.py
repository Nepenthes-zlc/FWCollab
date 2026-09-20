from __future__ import annotations

import json

from fwcollab.symbolic.dag import load_json, validate_state_dag
from fwcollab.symbolic.map import load_symbol_map
from fwcollab.symbolic.full_spatial import FULL_CAPABILITIES_BY_LEVEL, write_full_spatial_curriculum
from fwcollab.symbolic.spatial import (
    SPATIAL_CURRICULUM_SPECS,
    SPATIAL_SPECS,
    write_spatial_core,
    write_spatial_curriculum,
)
from fwcollab.symbolic.world import SymbolAction, SymbolWorld


def test_spatial_core_has_twelve_distinct_two_dimensional_maps(tmp_path) -> None:
    gallery = tmp_path / "gallery.html"
    traces = tmp_path / "traces"
    manifest = write_spatial_core(tmp_path / "private", gallery, traces)
    assert manifest["count"] == manifest["topology_unique"] == len(SPATIAL_SPECS) == 12
    assert gallery.is_file()
    assert "FWCollab 核心地图集" in gallery.read_text(encoding="utf-8")
    assert len(list(traces.glob("R*.html"))) == 12
    rendered_trace = (traces / "R01.html").read_text(encoding="utf-8")
    assert "DAG=${progress.completed}/${progress.total}" in rendered_trace
    trace = json.loads((traces / "R01.json").read_text(encoding="utf-8"))
    assert trace["private_evaluation"]["visibility"] == "private_post_run_only"
    assert trace["private_evaluation"]["progress"][-1]["completed"] == trace["private_evaluation"]["progress"][-1]["total"]

    witnesses = json.loads((tmp_path / "private" / "witnesses.json").read_text(encoding="utf-8"))["maps"]
    for record in manifest["records"]:
        assert record["all_nodes_bound"] is True
        assert record["all_edges_verified"] is True
        assert record["spatial_metrics"]["branch_points"] >= 2
        assert record["spatial_metrics"]["vertical_witness_steps"] >= 1
        symbol_map = load_symbol_map(record["map"])
        world = SymbolWorld(symbol_map)
        for joint in witnesses[record["id"]]:
            world.step_joint(
                {
                    role: SymbolAction(move=joint[role][0], steps=joint[role][1])
                    for role in ("F", "W")
                }
            )
        assert world.status == "team_success"


def test_spatial_curriculum_has_72_unique_verified_maps(tmp_path) -> None:
    manifest = write_spatial_curriculum(tmp_path / "curriculum")
    assert manifest["count"] == len(SPATIAL_CURRICULUM_SPECS) == 72
    assert manifest["semantic_unique"] == 72
    assert manifest["topology_unique"] == 72
    assert manifest["difficulty_distribution"] == {
        "L1": 4,
        "L2": 8,
        "L3": 10,
        "L4": 12,
        "L5": 14,
        "L6": 14,
        "L7": 10,
    }
    assert all(record["all_nodes_bound"] for record in manifest["records"])
    assert all(record["all_edges_verified"] for record in manifest["records"])
    assert all(record["spatial_metrics"]["vertical_witness_steps"] > 0 for record in manifest["records"])


def test_full_spatial_curriculum_actively_covers_m01_through_m30(tmp_path) -> None:
    output = tmp_path / "full"
    manifest = write_full_spatial_curriculum(output)
    assert manifest["status"] == "full_M01_M30_spatial_curriculum_verified"
    assert manifest["count"] == manifest["semantic_unique"] == manifest["topology_unique"] == 72
    assert manifest["difficulty_distribution"] == {
        "L1": 4,
        "L2": 8,
        "L3": 10,
        "L4": 12,
        "L5": 14,
        "L6": 14,
        "L7": 10,
    }
    assert manifest["mechanism_coverage"] == [f"M{index:02d}" for index in range(1, 31)]
    assert {record["primary_capability"] for record in manifest["records"]} == {
        motif for values in FULL_CAPABILITIES_BY_LEVEL.values() for motif in values
    }
    assert all(record["capability_verified"] for record in manifest["records"])
    assert all(record["all_nodes_bound"] for record in manifest["records"])
    assert all(record["all_edges_verified"] for record in manifest["records"])
    for record in manifest["records"]:
        validate_state_dag(load_json(record["dag"]))

    witnesses = json.loads((output / "witnesses.json").read_text(encoding="utf-8"))["maps"]
    for record in manifest["records"]:
        symbol_map = load_symbol_map(record["map"])
        world = SymbolWorld(symbol_map)
        for joint in witnesses[record["id"]]:
            world.step_joint(
                {
                    role: SymbolAction(move=joint[role][0], steps=joint[role][1])
                    for role in ("F", "W")
                }
            )
        assert world.status == "team_success"
