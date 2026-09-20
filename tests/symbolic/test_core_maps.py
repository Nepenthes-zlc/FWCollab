from __future__ import annotations

import json
from pathlib import Path

from fwcollab.symbolic.map import load_symbol_map
from fwcollab.symbolic.world import SymbolAction, SymbolWorld

ROOT = Path(__file__).resolve().parents[2]
MAP_DIR = ROOT / "maps" / "symbolic"
WITNESS_PATH = ROOT / "eval_private" / "core_witnesses.json"
MANIFEST_PATH = MAP_DIR / "core_manifest.json"


def _core_paths() -> list[Path]:
    return sorted(MAP_DIR.glob("S*.fwmap"))


def test_core_suite_contains_twenty_four_distinct_valid_maps() -> None:
    paths = _core_paths()
    expected_ids = [f"S{index:02d}" for index in range(1, 25)]
    assert [path.stem for path in paths] == expected_ids

    maps = [load_symbol_map(path) for path in paths]
    assert [symbol_map.map_id for symbol_map in maps] == [path.stem for path in paths]
    assert len({symbol_map.rows for symbol_map in maps}) == 24
    assert [symbol_map.format for symbol_map in maps].count("fwcollab.symbol_map.v1") == 12
    assert [symbol_map.format for symbol_map in maps].count("fwcollab.symbol_map.v2") == 12


def test_manifest_describes_every_core_map_once() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["split"] == "development_public"
    assert manifest["difficulty_status"] == "provisional_not_empirically_calibrated"
    entries = manifest["maps"]
    assert [entry["id"] for entry in entries] == [f"S{index:02d}" for index in range(1, 25)]
    assert all(entry["focus"] and entry["tags"] for entry in entries)
    assert all(1 <= entry["difficulty"] <= 6 for entry in entries)
    enhanced = entries[12:]
    assert sum(entry["comparison_reference"] is not None for entry in enhanced) >= 10
    assert not any(entry["controlled_counterfactual"] for entry in enhanced)
    assert all(
        entry["comparison_reference"] is None or entry["comparison_reference"] in {item["id"] for item in entries}
        for entry in enhanced
    )


def test_private_witnesses_reach_team_success_without_failure() -> None:
    witnesses = json.loads(WITNESS_PATH.read_text(encoding="utf-8"))["maps"]
    assert sorted(witnesses) == [f"S{index:02d}" for index in range(1, 25)]

    for map_id, rounds in witnesses.items():
        world = SymbolWorld(load_symbol_map(MAP_DIR / f"{map_id}.fwmap"))
        for round_actions in rounds:
            actions = {
                role: SymbolAction(move=spec[0], steps=spec[1])
                for role, spec in round_actions.items()
            }
            result = world.step_joint(actions)
            assert result.status != "team_failure", f"{map_id} failed at round {result.round}: {result.feedback}"
        assert world.status == "team_success", f"{map_id} witness ended at {world.actors}"


def test_no_core_witness_exceeds_public_action_limit() -> None:
    witnesses = json.loads(WITNESS_PATH.read_text(encoding="utf-8"))["maps"]
    for map_id, rounds in witnesses.items():
        max_steps = load_symbol_map(MAP_DIR / f"{map_id}.fwmap").max_steps
        assert max_steps == 1
        assert all(
            (spec == ["WAIT", 0]) or (spec[0] in {"UP", "DOWN", "LEFT", "RIGHT"} and spec[1] == 1)
            for actions in rounds
            for spec in actions.values()
        )
