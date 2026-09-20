from __future__ import annotations

import copy

import pytest

from fwcollab.c5 import C5ValidationError, correct_controller, evaluate_c5_trace, project_role_observation, run_c5_session, validate_information_suite
from fwcollab.symbolic.agents import AgentDecision, ScriptedPolicy
from fwcollab.symbolic.map import parse_symbol_map
from fwcollab.symbolic.world import SymbolAction


def _tasks():
    controllers = ("c1", "c2", "c3")
    actuators = ("a", "b", "c")
    permutations = (("a", "b", "c"), ("b", "c", "a"), ("c", "a", "b"))
    result = []
    for p_index, permutation in enumerate(permutations):
        wiring = dict(zip(controllers, permutation, strict=True))
        for actuator in actuators:
            result.append(
                {
                    "task_id": f"t{p_index}-{actuator}",
                    "f_view_id": f"f{p_index}",
                    "w_view_id": f"w-{actuator}",
                    "wiring": wiring,
                    "required_actuator": actuator,
                    "f_exit": [1, 1],
                    "w_exit": [3, 3],
                }
            )
    return result


def test_information_certificate_proves_complementarity() -> None:
    certificate = validate_information_suite(_tasks())
    assert certificate["valid"] is True
    assert certificate["claims"] == {
        "F_alone_entails_solution": False,
        "W_alone_entails_solution": False,
        "F_union_W_entails_unique_solution": True,
    }
    assert correct_controller(_tasks()[0]) == "c1"


def test_information_certificate_rejects_singleton_f_class() -> None:
    tasks = _tasks()
    for index, task in enumerate(tasks):
        task["f_view_id"] = f"unique-{index}"
    with pytest.raises(C5ValidationError, match="F alone entails"):
        validate_information_suite(tasks)


def test_role_projection_redacts_complementary_secret_and_teammate() -> None:
    task = _tasks()[0]
    observation = {
        "format": "fwcollab.agent_observation.v1",
        "map_id": "secret-id",
        "map": {"current_rows": ["#####"] * 17, "terrain_rows": ["#####"] * 17},
        "mechanisms": {
            "exits": {"F": [[1, 1]], "W": [[3, 3]]},
            "controllers": {"c1": {"position": [2, 2]}},
            "actuators": {"a": {"positions": [[10, 2]], "controlled_by": {"inputs": ["c1"]}}},
        },
        "dynamic_state": {
            "actors": {"F": [2, 1], "W": [10, 1]},
            "controllers": {"c1": False},
            "actuators": {"doors": {"a": False}},
        },
    }
    f_view = project_role_observation(copy.deepcopy(observation), task, "F")
    w_view = project_role_observation(copy.deepcopy(observation), task, "W")
    assert f_view["map_id"] == w_view["map_id"] == "C5-HIDDEN"
    assert f_view["dynamic_state"]["actors"] == {"F": [2, 1]}
    assert w_view["dynamic_state"]["actors"] == {"W": [10, 1]}
    assert "required_actuator" not in f_view["private_information"]
    assert "wiring" not in w_view["private_information"]
    assert "actuators" not in f_view["mechanisms"]
    assert "controlled_by" not in w_view["mechanisms"]["actuators"]["a"]


def test_c5_session_stores_exact_role_private_observations() -> None:
    text = """@format fwcollab.symbol_map.v3
@id c5-test
@title audit
@controller c1 lever at 1,2
@actuator a1 door at 10,2 controlled_by all(c1)
---
#####
#.L.#
#F.f#
#####
#####
#####
#####
#####
#####
#####
#WAw#
#####
#####
#####
#####
#####
#####
"""
    symbol_map = parse_symbol_map(text)
    task = {
        "task_id": "test",
        "f_view_id": "f1",
        "w_view_id": "w1",
        "wiring": {"c1": "a1"},
        "required_actuator": "a1",
        "f_exit": [1, 3],
        "w_exit": [3, 3],
    }
    wait = AgentDecision(action=SymbolAction(move="WAIT", steps=0))
    result = run_c5_session(
        symbol_map,
        {"F": ScriptedPolicy([wait]), "W": ScriptedPolicy([wait])},
        task,
        max_rounds=1,
    )
    record = result.trace["rounds"][0]
    assert record["role_agent_observations"]["F"]["private_information"]["wiring"] == {"c1": "a1"}
    assert record["role_agent_observations"]["W"]["private_information"]["required_actuator"] == "a1"
    assert "agent_observation" not in record
    assert result.trace["protocol"]["observation_partition"] == "role_private_complementary"


def test_c5_evaluator_identifies_clean_information_handoff() -> None:
    task = {
        "task_id": "t",
        "f_view_id": "f",
        "w_view_id": "w",
        "wiring": {"c_north": "gate_alpha", "c_center": "gate_beta", "c_south": "gate_gamma"},
        "required_actuator": "gate_beta",
    }

    def record(round_index, *, message=None, inbox=None, controller=False, door=False, water=None, success=False):
        return {
            "round": round_index,
            "agents": {
                "F": {"decision": {"message": None}, "inbox": inbox or []},
                "W": {"decision": {"message": message}, "inbox": []},
            },
            "result": {
                "observation": {
                    "state": {
                        "controllers": {"c_center": controller},
                        "doors": {"gate_beta": door},
                        "actors": {"W": water or [12, 9]},
                    },
                    "status": "team_success" if success else "running",
                }
            },
        }

    trace = {
        "outcome": "team_success",
        "rounds": [
            record(1, message={"fact": "gate_beta"}),
            record(2, inbox=[{"text": "gate_beta"}], controller=True, door=True),
            record(3, controller=True, door=True, water=[12, 10]),
            record(4, controller=True, door=True, water=[12, 19], success=True),
        ],
    }
    result = evaluate_c5_trace(task, trace)
    assert result["success"] is True
    assert result["clean_information_handoff"] is True
    assert result["dependency_violation"] is False
    assert result["dag_completion"] == 1.0


def test_c5_evaluator_marks_correct_guess_as_dependency_violation() -> None:
    task = {
        "task_id": "t",
        "f_view_id": "f",
        "w_view_id": "w",
        "wiring": {"c_north": "gate_alpha", "c_center": "gate_beta", "c_south": "gate_gamma"},
        "required_actuator": "gate_beta",
    }
    trace = {
        "outcome": "timeout",
        "rounds": [{
            "agents": {"F": {"decision": {"message": None}, "inbox": []}, "W": {"decision": {"message": None}, "inbox": []}},
            "result": {"observation": {"state": {"controllers": {"c_center": True}, "doors": {"gate_beta": True}, "actors": {"W": [12, 9]}}, "status": "running"}},
        }],
    }
    result = evaluate_c5_trace(task, trace)
    assert result["dependency_violation"] is True
    assert result["clean_information_handoff"] is False
    assert result["failure_stage"] == "n2"
