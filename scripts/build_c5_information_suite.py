"""Generate and validate the 12-task C5 information-dependent mini-suite."""

from __future__ import annotations

import json
from itertools import permutations
from pathlib import Path

from fwcollab.c5 import correct_controller, project_role_observation, validate_information_suite
from fwcollab.symbolic.map import parse_symbol_map
from fwcollab.symbolic.world import SymbolAction, SymbolWorld


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval_private" / "c5_information_12"
CONTROLLERS = ("c_north", "c_center", "c_south")
ACTUATORS = ("gate_alpha", "gate_beta", "gate_gamma")
TARGET_ROWS = {"gate_alpha": 10, "gate_beta": 12, "gate_gamma": 14}
CONTROLLER_ROWS = dict(zip(CONTROLLERS, (2, 4, 6), strict=True))


def _map_text(task_id: str, wiring: dict[str, str], target: str) -> str:
    height, width = 17, 21
    grid = [["#"] * width for _ in range(height)]
    for row in (2, 4, 6):
        for col in range(3, 14):
            grid[row][col] = "."
        grid[row][13] = "L"
    for row in range(2, 7):
        grid[row][3] = "."
    grid[4][1], grid[4][2], grid[4][3] = "f", ".", "F"

    for row in (10, 12, 14):
        for col in range(3, 10):
            grid[row][col] = "."
        grid[row][10] = {10: "A", 12: "B", 14: "C"}[row]
        grid[row][11] = "."
    for row in range(10, 15):
        grid[row][3] = "."
    grid[12][2] = "W"
    target_row = TARGET_ROWS[target]
    for col in range(11, 18):
        grid[target_row][col] = "."
    for row in range(min(target_row, 12), max(target_row, 12) + 1):
        grid[row][17] = "."
    grid[12][18] = "."
    grid[12][19] = "w"

    actuator_rows = dict(zip(ACTUATORS, (10, 12, 14), strict=True))
    directives = [
        "@format fwcollab.symbol_map.v3",
        f"@id {task_id}",
        "@title Private wiring and route handoff",
        "@max_steps 1",
    ]
    directives.extend(
        f"@controller {controller} lever at {CONTROLLER_ROWS[controller]},13" for controller in CONTROLLERS
    )
    inverse = {actuator: controller for controller, actuator in wiring.items()}
    directives.extend(
        f"@actuator {actuator} door at {actuator_rows[actuator]},10 controlled_by all({inverse[actuator]})"
        for actuator in ACTUATORS
    )
    return "\n".join([*directives, "---", *("".join(row) for row in grid), ""])


def _dag(task: dict[str, object]) -> dict[str, object]:
    return {
        "format": "fwcollab.c5_executable_dag.v1",
        "visibility": "private_evaluator_only",
        "task_id": task["task_id"],
        "nodes": [
            {"id": "n0", "owner": "team", "predicate": {"op": "start"}},
            {"id": "n1", "owner": "W", "predicate": {"op": "private_fact_known", "fact": "required_actuator"}},
            {"id": "n2", "owner": "W", "predicate": {"op": "message_sent", "fact": task["required_actuator"]}},
            {"id": "n3", "owner": "F", "predicate": {"op": "message_received", "fact": task["required_actuator"]}},
            {"id": "n4", "owner": "F", "predicate": {"op": "controller_on", "id": task["answer_controller"]}},
            {"id": "n5", "owner": "environment", "predicate": {"op": "actuator_on", "id": task["required_actuator"]}},
            {"id": "n6", "owner": "W", "predicate": {"op": "crossed_actuator", "id": task["required_actuator"]}},
            {"id": "n7", "owner": "team", "predicate": {"op": "team_success"}},
        ],
        "edges": [
            {"from": "n0", "to": "n1", "relation": "requires"},
            {"from": "n1", "to": "n2", "relation": "information_handoff"},
            {"from": "n2", "to": "n3", "relation": "delayed_delivery"},
            {"from": "n3", "to": "n4", "relation": "enables"},
            {"from": "n4", "to": "n5", "relation": "causes"},
            {"from": "n5", "to": "n6", "relation": "enables"},
            {"from": "n6", "to": "n7", "relation": "requires"},
        ],
    }


def _moves(*values: str) -> list[SymbolAction]:
    return [SymbolAction(move=value, steps=0 if value == "WAIT" else 1) for value in values]


def _f_select(controller: str) -> list[SymbolAction]:
    row = CONTROLLER_ROWS[controller]
    outward_vertical = "UP" if row == 2 else "DOWN"
    return_vertical = "DOWN" if row == 2 else "UP"
    actions: list[SymbolAction] = []
    if row != 4:
        actions += _moves(outward_vertical, outward_vertical)
    actions += _moves(*(["RIGHT"] * 10), *(["LEFT"] * 10))
    if row != 4:
        actions += _moves(return_vertical, return_vertical)
    return actions


def _f_route(controller: str, *, initial_wait: bool = True) -> list[SymbolAction]:
    actions = _moves("WAIT") if initial_wait else []
    actions += _f_select(controller)
    actions += _moves("LEFT", "LEFT")
    return actions


def _w_route(target: str) -> list[SymbolAction]:
    row = TARGET_ROWS[target]
    outward_vertical = "UP" if row == 10 else "DOWN"
    return_vertical = "DOWN" if row == 10 else "UP"
    actions = _moves("RIGHT")
    if row != 12:
        actions += _moves(outward_vertical, outward_vertical)
    # Six waits cover the longest F branch plus the frozen-prestate rule: a
    # door activated this round can first be entered on the following round.
    actions += _moves(*(["RIGHT"] * 6), *(["WAIT"] * 6), *(["RIGHT"] * 8))
    if row != 12:
        actions += _moves(return_vertical, return_vertical)
    actions += _moves("RIGHT", "RIGHT")
    return actions


def _simulate(symbol_map, fire: list[SymbolAction], water: list[SymbolAction], rounds: int = 30):
    world = SymbolWorld(symbol_map)
    wait = SymbolAction(move="WAIT", steps=0)
    for index in range(rounds):
        world.step_joint({
            "F": fire[index] if index < len(fire) else wait,
            "W": water[index] if index < len(water) else wait,
        })
        if world.status != "running":
            break
    return world


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "maps").mkdir(exist_ok=True)
    (OUT / "dags").mkdir(exist_ok=True)
    chosen_permutations = list(permutations(ACTUATORS))[:4]
    tasks: list[dict[str, object]] = []
    for p_index, permutation in enumerate(chosen_permutations, start=1):
        wiring = dict(zip(CONTROLLERS, permutation, strict=True))
        for target_index, target in enumerate(ACTUATORS, start=1):
            index = (p_index - 1) * 3 + target_index
            task_id = f"C5-{index:03d}"
            task: dict[str, object] = {
                "task_id": task_id,
                "map": f"maps/{task_id}.fwmap",
                "dag": f"dags/{task_id}.json",
                "family": "C5_information_dependent",
                "f_view_id": f"wiring-{p_index}",
                "w_view_id": f"route-{target}",
                "wiring": wiring,
                "required_actuator": target,
                "answer_controller": next(key for key, value in wiring.items() if value == target),
                "f_exit": [4, 1],
                "w_exit": [12, 19],
                "max_rounds": 30,
                "message_delay_rounds": 1,
            }
            map_text = _map_text(task_id, wiring, target)
            symbol_map = parse_symbol_map(map_text, source=task_id)
            if correct_controller(task) != task["answer_controller"]:
                raise RuntimeError(f"answer mismatch for {task_id}")
            solution_fire = _f_route(task["answer_controller"])
            solution_water = _w_route(target)
            solved = _simulate(symbol_map, solution_fire, solution_water)
            if solved.status != "team_success":
                raise RuntimeError(f"deterministic solution witness failed for {task_id}: {solved.status}")
            wrong_controller = next(controller for controller in CONTROLLERS if controller != task["answer_controller"])
            wrong_then_correct = (
                _moves("WAIT")
                + _f_select(wrong_controller)
                + _f_select(task["answer_controller"])
                + _moves("LEFT", "LEFT")
            )
            wrong_world = _simulate(symbol_map, wrong_then_correct, solution_water)
            if wrong_world.status == "team_success":
                raise RuntimeError(f"wrong-first route unexpectedly solved {task_id}")
            task["budget_certificate"] = {
                "correct_witness_rounds": solved.round,
                "wrong_first_lower_bound_rounds": len(wrong_then_correct),
                "wrong_first_success_within_budget": False,
            }
            (OUT / task["map"]).write_text(map_text, encoding="utf-8")
            (OUT / task["dag"]).write_text(json.dumps(_dag(task), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tasks.append(task)

    certificate = validate_information_suite(tasks)
    f_hashes: dict[str, set[str]] = {}
    w_hashes: dict[str, set[str]] = {}
    for task in tasks:
        symbol_map = parse_symbol_map((OUT / task["map"]).read_text(encoding="utf-8"), source=str(task["map"]))
        public = SymbolWorld(symbol_map).observation()
        # Build the compact shape expected by the projector without touching the frozen runner.
        compact = {
            "format": "fwcollab.agent_observation.v1",
            "map_id": task["task_id"],
            "map": {"current_rows": public["map_rows"], "terrain_rows": public["terrain_rows"]},
            "mechanisms": {
                "exits": public["layout"]["exits"],
                "controllers": public["layout"]["controllers"],
                "actuators": public["layout"]["actuators"],
            },
            "dynamic_state": {
                "actors": public["state"]["actors"],
                "controllers": public["state"]["controllers"],
                "actuators": {"doors": public["state"]["doors"]},
            },
            "shared_message_history": [],
            "status": "running",
        }
        f_view = project_role_observation(compact, task, "F")
        w_view = project_role_observation(compact, task, "W")
        f_hashes.setdefault(task["f_view_id"], set()).add(json.dumps(f_view, sort_keys=True))
        w_hashes.setdefault(task["w_view_id"], set()).add(json.dumps(w_view, sort_keys=True))
    if any(len(values) != 1 for values in f_hashes.values()):
        raise RuntimeError("F projection leaks W-private route information")
    if any(len(values) != 1 for values in w_hashes.values()):
        raise RuntimeError("W projection leaks F-private wiring information")
    certificate["initial_projection_noninterference"] = {"F": True, "W": True}
    certificate["deterministic_witnesses"] = {
        "all_correct_routes_solve_within_budget": True,
        "all_wrong_first_routes_fail_within_budget": True,
    }
    manifest = {
        "format": "fwcollab.c5_suite.v1",
        "selection": "structural_only_without_model_performance",
        "task_count": len(tasks),
        "experimental_matrix": {
            "conditions": ["GPT_Comm", "GPT_NoComm", "Gemini_Comm", "Gemini_NoComm"],
            "seeds": [1, 2, 3],
            "episodes": len(tasks) * 4 * 3,
        },
        "certificate": certificate,
        "tasks": tasks,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tasks": len(tasks), "episodes_planned": len(tasks) * 12, "certificate": certificate}))


if __name__ == "__main__":
    main()
