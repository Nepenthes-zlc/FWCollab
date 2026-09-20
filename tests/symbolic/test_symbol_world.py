from __future__ import annotations

import pytest
from pydantic import ValidationError

from fwcollab.symbolic.map import parse_symbol_map
from fwcollab.symbolic.world import SymbolAction, SymbolWorld
from tests.symbolic.test_symbol_map import SIMPLE


def test_plate_opens_door_and_leaving_closes_it() -> None:
    world = SymbolWorld(parse_symbol_map(SIMPLE))
    first = world.step_joint({"F": SymbolAction(move="RIGHT", steps=1)})
    assert first.observation["state"]["doors"]["A"] is True  # type: ignore[index]
    second = world.step_joint({"F": SymbolAction(move="RIGHT", steps=1)})
    assert second.feedback["F"] == "moved"
    assert second.observation["state"]["doors"]["A"] is False  # type: ignore[index]


def test_movement_is_exactly_one_cell() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 1"):
        SymbolAction(move="LEFT", steps=2)


def test_box_can_replace_actor_on_accepted_plate() -> None:
    text = (
        SIMPLE.replace("#F1A..f.#", "#..A..f.#")
        .replace("#..O....#", "#WO1....#")
        .replace("#W....w.#", "#F....w.#")
    )
    world = SymbolWorld(parse_symbol_map(text))
    world.step_joint({"W": SymbolAction(move="RIGHT", steps=1)})
    assert world.plate_states()["1"]
    assert world.door_states()["A"]


def test_hazard_role_difference_and_updated_map_observation() -> None:
    text = SIMPLE.replace("#F1A..f.#", "#F~A.1f.#")
    world = SymbolWorld(parse_symbol_map(text))
    result = world.step_joint({"F": SymbolAction(move="RIGHT", steps=1)})
    assert result.status == "team_failure"
    assert result.observation["state"]["alive"]["F"] is False  # type: ignore[index]
    assert "X" in result.observation["map_rows"][1]  # type: ignore[operator,index]


def test_same_joint_actions_are_deterministic() -> None:
    symbol_map = parse_symbol_map(SIMPLE)
    first, second = SymbolWorld(symbol_map), SymbolWorld(symbol_map)
    actions = {"F": SymbolAction(move="RIGHT", steps=1), "W": SymbolAction(move="RIGHT", steps=1)}
    assert first.step_joint(actions).observation == second.step_joint(actions).observation
    assert first.state_hash() == second.state_hash()


def test_lever_persistently_opens_door() -> None:
    text = """@format fwcollab.symbol_map.v1
@id lever
@title Lever
@lever L opens B
---
#######
#FLB.f#
#W..w.#
#######
"""
    world = SymbolWorld(parse_symbol_map(text))
    activated = world.step_joint({"F": SymbolAction(move="RIGHT", steps=1)})
    assert activated.observation["state"]["levers"]["L"] is True  # type: ignore[index]
    assert world.door_states()["B"]
    passed = world.step_joint({"F": SymbolAction(move="RIGHT", steps=1)})
    assert passed.feedback["F"] == "moved"
    assert "l" in world.render_rows()[1]


def test_observation_keeps_static_underlay_visible_when_occupied() -> None:
    world = SymbolWorld(parse_symbol_map(SIMPLE))
    result = world.step_joint({"F": SymbolAction(move="RIGHT", steps=1)})
    assert result.observation["map_rows"][1][2] == "F"  # type: ignore[index]
    assert result.observation["terrain_rows"][1][2] == "1"  # type: ignore[index]
    assert result.observation["layout"]["plates"]["1"]["positions"] == [[1, 2]]  # type: ignore[index]


def test_cross_type_movable_destination_conflict_is_atomic() -> None:
    text = """@format fwcollab.symbol_map.v2
@id conflict
@title Cross movable conflict
@max_steps 1
---
#########
#fFO.oWw#
#########
"""
    world = SymbolWorld(parse_symbol_map(text))
    result = world.step_joint(
        {"F": SymbolAction(move="RIGHT", steps=1), "W": SymbolAction(move="LEFT", steps=1)}
    )
    assert result.feedback == {"F": "movable_destination_conflict", "W": "movable_destination_conflict"}
    assert world.crates == {world.map.unique_position("O")}
    assert world.orbs == {world.map.unique_position("o")}
