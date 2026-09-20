from __future__ import annotations

import pytest

from fwcollab.symbolic.map import SymbolMapError, parse_symbol_map
from fwcollab.symbolic.world import SymbolAction, SymbolWorld


def test_v1_rejects_v2_symbol() -> None:
    text = """@format fwcollab.symbol_map.v1
@id bad-v1
@title Bad
@portal P
---
#########
#FP..Pf.#
#W....w.#
#########
"""
    with pytest.raises(SymbolMapError, match="v2 mechanism"):
        parse_symbol_map(text)


def test_actor_can_use_paired_portal() -> None:
    text = """@format fwcollab.symbol_map.v2
@id portal
@title Portal
@portal P
---
#########
#FP..Pf.#
#W....w.#
#########
"""
    world = SymbolWorld(parse_symbol_map(text))
    actor_result = world.step_joint({"F": SymbolAction(move="RIGHT", steps=1)})
    assert actor_result.feedback["F"] == "moved_via_portal"
    assert world.actors["F"].col == 5



def test_crate_can_use_paired_portal() -> None:
    text = """@format fwcollab.symbol_map.v2
@id crate-portal
@title Crate portal
@portal P
---
#########
#F...f..#
#WOP.Pw.#
#########
"""
    world = SymbolWorld(parse_symbol_map(text))
    world.step_joint({"W": SymbolAction(move="RIGHT", steps=1)})
    assert next(iter(world.crates)).col == 5


def test_toggle_switch_changes_door_only_on_entry() -> None:
    text = """@format fwcollab.symbol_map.v2
@id toggle
@title Toggle
@toggle T toggles A
---
#########
#FTA.f..#
#W...w..#
#########
"""
    world = SymbolWorld(parse_symbol_map(text))
    world.step_joint({"F": SymbolAction(move="RIGHT", steps=1)})
    assert world.door_states()["A"]
    world.step_joint({"W": SymbolAction(move="RIGHT", steps=1)})
    assert world.door_states()["A"]


def test_one_way_gate_blocks_wrong_direction() -> None:
    text = """@format fwcollab.symbol_map.v2
@id oneway
@title One way
@oneway J allows RIGHT
---
#########
#F...f..#
#w.J..W.#
#########
"""
    world = SymbolWorld(parse_symbol_map(text))
    world.step_joint({"W": SymbolAction(move="LEFT", steps=1)})
    world.step_joint({"W": SymbolAction(move="LEFT", steps=1)})
    result = world.step_joint({"W": SymbolAction(move="LEFT", steps=1)})
    assert result.feedback["W"] == "one_way_wrong_direction"
    assert world.actors["W"].col == 4


def test_thermal_controls_change_liquid_hazard_to_safe_ice() -> None:
    text = """@format fwcollab.symbol_map.v2
@id thermal
@title Thermal
@thermal I initial liquid heater H freezer K
---
###########
#F.III.f..#
#WK....w..#
#..H......#
###########
"""
    world = SymbolWorld(parse_symbol_map(text))
    world.step_joint({"W": SymbolAction(move="RIGHT", steps=1)})
    assert world.thermal_frozen["I"]
    for _ in range(6):
        result = world.step_joint({"F": SymbolAction(move="RIGHT", steps=1)})
    assert result.status != "team_failure"
    assert world.static_cell(world.actors["F"]) == "f"


def test_toggle_rotates_mirror_and_lights_sensor() -> None:
    text = """@format fwcollab.symbol_map.v2
@id light
@title Light
@toggle T toggles M
@light + direction RIGHT sensor S opens A
@mirror M initial slash
---
###########
#+.M......#
#F.T.A.f..#
#W.S.A.w..#
###########
"""
    world = SymbolWorld(parse_symbol_map(text))
    assert not world.light_sensor_states()["S"]
    assert not world.door_states()["A"]
    world.step_joint({"F": SymbolAction(move="RIGHT", steps=1)})
    world.step_joint({"F": SymbolAction(move="RIGHT", steps=1)})
    assert world.light_sensor_states()["S"]
    assert world.door_states()["A"]


def test_plate_activates_discrete_platform() -> None:
    text = """@format fwcollab.symbol_map.v2
@id platform
@title Platform
@plate 1 opens A accepts F
@platform = controlled_by 1
---
###########
#F1..A.f..#
###########
#W..==.w..#
###########
"""
    world = SymbolWorld(parse_symbol_map(text))
    world.step_joint({"W": SymbolAction(move="RIGHT", steps=1)})
    world.step_joint({"W": SymbolAction(move="RIGHT", steps=1)})
    blocked = world.step_joint({"W": SymbolAction(move="RIGHT", steps=1)})
    assert blocked.feedback["W"] == "platform_inactive"
    world.step_joint({"F": SymbolAction(move="RIGHT", steps=1)})
    assert world.platform_states()["="]


def test_orb_rolls_until_obstacle_and_can_hold_plate() -> None:
    text = """@format fwcollab.symbol_map.v2
@id orb
@title Orb
@plate 1 opens A accepts O
---
###########
#Wo..1#w..#
#.........#
###########
#F...A.f..#
###########
"""
    world = SymbolWorld(parse_symbol_map(text))
    world.step_joint({"W": SymbolAction(move="RIGHT", steps=1)})
    assert next(iter(world.orbs)) == world.map.unique_position("1")
    assert world.door_states()["A"]
