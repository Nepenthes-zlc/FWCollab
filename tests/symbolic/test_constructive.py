from __future__ import annotations

import json
import statistics
from pathlib import Path

from fwcollab.symbolic.constructive import (
    CONSTRUCTIVE_COUNTS,
    MODULES,
    write_constructive_curriculum,
    write_constructive_modules,
)
from fwcollab.symbolic.dag import load_json, validate_state_dag
from fwcollab.symbolic.map import load_symbol_map
from fwcollab.symbolic.world import SymbolAction, SymbolWorld


def test_constructive_modules_emit_solved_maps_and_derived_dags(tmp_path: Path) -> None:
    manifest = write_constructive_modules(tmp_path)
    assert manifest["count"] == len(MODULES) == 12
    witnesses = json.loads((tmp_path / "witnesses.json").read_text(encoding="utf-8"))["maps"]
    for record in manifest["records"]:
        symbol_map = load_symbol_map(record["map"])
        assert symbol_map.format == "fwcollab.symbol_map.v3"
        world = SymbolWorld(symbol_map)
        for joint in witnesses[record["id"]]:
            world.step_joint(
                {
                    role: SymbolAction(move=joint[role][0], steps=joint[role][1])
                    for role in ("F", "W")
                }
            )
        assert world.status == "team_success"
        dag = load_json(record["dag"])
        validate_state_dag(dag)
        assert set(dag["mechanisms"]) <= {"M11", "M14", "M17", "M18", "M19", "M20"}


def test_constructive_curriculum_has_72_semantic_signatures_and_monotonic_witness_bands(tmp_path: Path) -> None:
    manifest = write_constructive_curriculum(tmp_path)
    assert manifest["count"] == manifest["semantic_unique"] == 72
    assert manifest["difficulty_distribution"] == CONSTRUCTIVE_COUNTS
    assert manifest["mechanism_coverage"] == ["M11", "M14", "M17", "M18", "M19", "M20"]
    previous_median = 0.0
    for level in CONSTRUCTIVE_COUNTS:
        records = [record for record in manifest["records"] if record["difficulty"] == level]
        assert len(records) == CONSTRUCTIVE_COUNTS[level]
        median = statistics.median(record["witness_rounds"] for record in records)
        assert median > previous_median
        previous_median = median
        assert all(record["all_nodes_bound"] and record["all_edges_verified"] for record in records)
