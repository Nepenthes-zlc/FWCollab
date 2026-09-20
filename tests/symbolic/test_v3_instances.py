from __future__ import annotations

import pytest

from fwcollab.symbolic.map import SymbolMapError, parse_symbol_map
from fwcollab.symbolic.world import SymbolAction, SymbolWorld


V3_MAP = """@format fwcollab.symbol_map.v3
@id V3_INSTANCES
@title Independent instance controls
@max_steps 1
@controller l1 lever at 1,3
@controller l2 lever at 1,5
@controller p1 plate at 3,2 accepts W
@actuator door_all door at 3,4 controlled_by all(l1,l2)
@actuator bridge_any platform at 3,6;3,7 controlled_by any(p1,l2)
---
############
#F.L.L..f..#
############
#W1.A.==w..#
############
"""


def move(direction: str) -> SymbolAction:
    return SymbolAction(move=direction, steps=1)  # type: ignore[arg-type]


WAIT = SymbolAction(move="WAIT", steps=0)


def test_v3_instances_and_boolean_actuators_are_independent() -> None:
    symbol_map = parse_symbol_map(V3_MAP)
    world = SymbolWorld(symbol_map)
    assert symbol_map.format == "fwcollab.symbol_map.v3"
    assert set(world.controller_states()) == {"l1", "l2", "p1"}
    assert world.door_states() == {"door_all": False}
    assert world.platform_states() == {"bridge_any": False}

    world.step_joint({"F": move("RIGHT"), "W": move("RIGHT")})
    assert world.controller_states()["p1"] is True
    assert world.platform_states()["bridge_any"] is True
    world.step_joint({"F": move("RIGHT"), "W": WAIT})
    assert world.controller_states()["l1"] is True
    assert world.controller_states()["l2"] is False
    assert world.door_states()["door_all"] is False
    world.step_joint({"F": move("RIGHT"), "W": WAIT})
    world.step_joint({"F": move("RIGHT"), "W": WAIT})
    assert world.controller_states()["l1"] is True
    assert world.controller_states()["l2"] is True
    assert world.door_states()["door_all"] is True

    observation = world.observation()
    assert observation["format"] == "fwcollab.symbol_observation.v3"
    assert observation["layout"]["actuators"]["door_all"]["controlled_by"] == {
        "mode": "all",
        "inputs": ["l1", "l2"],
    }
    assert observation["rules"]["actuators"]["occupied_close"].startswith("an actuator may become inactive")
    assert observation["rules"]["objective"].startswith("team_success iff")
    assert observation["rules"]["version"] == "fwcollab.symbol_rules.v3.2"
    assert observation["rules"]["coordinates"]["deltas"]["UP"] == [-1, 0]
    assert observation["rules"]["special"]["mirror_reflection"]["/"]["RIGHT"] == "UP"


def test_v3_rejects_missing_controller_reference() -> None:
    broken = V3_MAP.replace("all(l1,l2)", "all(l1,missing)")
    with pytest.raises(SymbolMapError, match="missing controllers"):
        parse_symbol_map(broken)


def test_v3_rejects_unbound_repeated_glyph() -> None:
    broken = V3_MAP.replace("#F.L.L..f..#", "#F.LLL..f..#")
    with pytest.raises(SymbolMapError):
        parse_symbol_map(broken)


def test_v3_instance_network_can_coexist_with_light_and_rotatable_mirror() -> None:
    symbol_map = parse_symbol_map(
        """@format fwcollab.symbol_map.v3
@id v3-mixed-light
@title v3 mixed extended mechanisms
@max_steps 1
@controller room_lever lever at 3,3
@actuator room_gate door at 3,5 controlled_by all(room_lever)
@controller mirror_toggle toggle at 2,3
@light + direction RIGHT sensor S opens B
@mirror M initial slash controlled_by mirror_toggle
---
###########
#+..M.....#
#..T......#
#.FL.A.f..#
#...S..BwW#
###########
"""
    )
    world = SymbolWorld(symbol_map)
    mirror = world.observation()["layout"]["mirrors"]["M"]
    assert mirror == {
        "positions": [[1, 4]],
        "initial": "slash",
        "current": "slash",
        "controlled_by": "mirror_toggle",
    }
    assert world.light_sensor_states()["S"] is False
    world.step_joint({"F": move("UP"), "W": WAIT})
    world.step_joint({"F": move("RIGHT"), "W": WAIT})
    assert world.light_sensor_states()["S"] is True
    assert world.door_states()["B"] is True
    assert world.observation()["layout"]["mirrors"]["M"]["current"] == "backslash"
