"""Deterministic discrete rule interpreter for symbol maps."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Mapping

from pydantic import Field, model_validator

from fwcollab.core.state import StrictModel
from fwcollab.symbolic.map import Position, SymbolMap
from fwcollab.symbolic.rules import public_rulebook

Role = Literal["F", "W"]
Move = Literal["UP", "DOWN", "LEFT", "RIGHT", "WAIT"]
DIRECTIONS: dict[str, tuple[int, int]] = {
    "UP": (-1, 0),
    "DOWN": (1, 0),
    "LEFT": (0, -1),
    "RIGHT": (0, 1),
}


class SymbolAction(StrictModel):
    move: Move
    steps: int = Field(ge=0, le=1)

    @model_validator(mode="after")
    def wait_and_steps_match(self) -> "SymbolAction":
        if self.move == "WAIT" and self.steps != 0:
            raise ValueError("WAIT requires steps=0")
        if self.move != "WAIT" and self.steps != 1:
            raise ValueError("movement requires exactly one step")
        return self


@dataclass(frozen=True, slots=True)
class SymbolStepResult:
    round: int
    micro_steps: int
    status: Literal["running", "team_success", "team_failure"]
    feedback: Mapping[Role, str]
    observation: dict[str, object]


def _add(position: Position, delta: tuple[int, int]) -> Position:
    return Position(position.row + delta[0], position.col + delta[1])


class SymbolWorld:
    def __init__(self, symbol_map: SymbolMap) -> None:
        self.map = symbol_map
        self.static_rows = symbol_map.static_rows()
        self.actors: dict[Role, Position] = {
            "F": symbol_map.unique_position("F"),
            "W": symbol_map.unique_position("W"),
        }
        self.crates: set[Position] = set(symbol_map.positions("O"))
        self.orbs: set[Position] = set(symbol_map.positions("o"))
        self.levers_on: set[str] = set()
        self.toggles_on: set[str] = set()
        self.thermal_frozen: dict[str, bool] = {
            rule.field_symbol: rule.initial == "frozen" for rule in symbol_map.thermals
        }
        self.alive: dict[Role, bool] = {"F": True, "W": True}
        self.round = 0
        self.micro_steps = 0
        self.status: Literal["running", "team_success", "team_failure"] = "running"

    def static_cell(self, position: Position) -> str:
        if position.row < 0 or position.row >= self.map.height or position.col < 0 or position.col >= self.map.width:
            return "#"
        return self.static_rows[position.row][position.col]

    def plate_states(self) -> dict[str, bool]:
        result: dict[str, bool] = {}
        for rule in self.map.controllers:
            if rule.kind != "plate":
                continue
            position = rule.position
            loads: set[str] = set()
            if position in self.crates or position in self.orbs:
                loads.add("O")
            loads.update(role for role, actor_position in self.actors.items() if self.alive[role] and actor_position == position)
            result[rule.id] = bool(loads & rule.accepts)
        for rule in self.map.plates:
            position = self.map.unique_position(rule.symbol)
            loads: set[str] = set()
            if position in self.crates:
                loads.add("O")
            if position in self.orbs:
                loads.add("O")
            loads.update(role for role, actor_position in self.actors.items() if self.alive[role] and actor_position == position)
            result[rule.symbol] = bool(loads & rule.accepts)
        return result

    def controller_states(self) -> dict[str, bool]:
        plates = self.plate_states()
        return {
            rule.id: (
                plates.get(rule.id, False)
                if rule.kind == "plate"
                else rule.id in (self.levers_on if rule.kind == "lever" else self.toggles_on)
            )
            for rule in self.map.controllers
        }

    def _actuator_on(self, rule: object) -> bool:
        states = self.controller_states()
        values = [states[item] for item in rule.controlled_by.inputs]  # type: ignore[attr-defined]
        return all(values) if rule.controlled_by.mode == "all" else any(values)  # type: ignore[attr-defined]

    def door_states(self) -> dict[str, bool]:
        if self.map.actuators:
            states = {rule.id: self._actuator_on(rule) for rule in self.map.actuators if rule.kind == "door"}
            for rule in self.map.lights:
                lit = self.light_sensor_states()[rule.sensor]
                for door in rule.doors:
                    states[door] = states.get(door, False) or lit
            return states
        states = {cell: False for row in self.static_rows for cell in row if cell in "ABCDE"}
        plates = self.plate_states()
        for rule in self.map.plates:
            if plates[rule.symbol]:
                for door in rule.doors:
                    states[door] = True
        for rule in self.map.levers:
            if rule.symbol in self.levers_on:
                for door in rule.doors:
                    states[door] = True
        for rule in self.map.toggles:
            if rule.symbol in self.toggles_on:
                for target in rule.targets:
                    if target in states:
                        states[target] = True
        for rule in self.map.lights:
            if self.light_sensor_states()[rule.sensor]:
                for door in rule.doors:
                    states[door] = True
        return states

    def platform_states(self) -> dict[str, bool]:
        if self.map.actuators:
            return {rule.id: self._actuator_on(rule) for rule in self.map.actuators if rule.kind == "platform"}
        states: dict[str, bool] = {}
        plates = self.plate_states()
        for rule in self.map.platforms:
            controller = rule.controller
            states[rule.symbol] = (
                plates.get(controller, False)
                or controller in self.levers_on
                or controller in self.toggles_on
            )
        return states

    def _actuator_id_at(self, kind: str, position: Position) -> str | None:
        for rule in self.map.actuators:
            if rule.kind == kind and position in rule.positions:
                return rule.id
        return None

    def _door_open_at(self, position: Position, doors: Mapping[str, bool]) -> bool:
        instance_id = self._actuator_id_at("door", position)
        if instance_id is not None:
            return doors[instance_id]
        return doors.get(self.static_cell(position), False)

    def _platform_open_at(self, position: Position) -> bool:
        instance_id = self._actuator_id_at("platform", position)
        if instance_id is not None:
            return self.platform_states()[instance_id]
        return self.platform_states().get("=", False)

    def portal_exit(self, position: Position) -> Position | None:
        cell = self.static_cell(position)
        if not any(rule.symbol == cell for rule in self.map.portals):
            return None
        first, second = self.map.positions(cell)
        return second if position == first else first

    def _mirror_cell(self) -> str:
        if not self.map.mirrors:
            return "/"
        rule = self.map.mirrors[0]
        toggled = (
            rule.controller in self.toggles_on
            if rule.controller is not None
            else any(
                toggle.symbol in self.toggles_on and rule.symbol in toggle.targets
                for toggle in self.map.toggles
            )
        )
        initial = "/" if rule.initial == "slash" else "\\"
        return ("\\" if initial == "/" else "/") if toggled else initial

    def _trace_light(self, rule: object) -> tuple[tuple[Position, ...], bool]:
        source = self.map.unique_position(rule.source)  # type: ignore[attr-defined]
        sensor = self.map.unique_position(rule.sensor)  # type: ignore[attr-defined]
        delta = DIRECTIONS[rule.direction]  # type: ignore[attr-defined]
        position = source
        trace: list[Position] = []
        visited: set[tuple[Position, tuple[int, int]]] = set()
        limit = self.map.width * self.map.height * 4
        for _ in range(limit):
            position = _add(position, delta)
            state = (position, delta)
            if state in visited:
                break
            visited.add(state)
            cell = self.static_cell(position)
            if cell == "#" or position in self.crates or position in self.orbs:
                break
            trace.append(position)
            if position == sensor:
                return tuple(trace), True
            mirror = self._mirror_cell() if cell == "M" else cell
            if mirror == "/":
                delta = (-delta[1], -delta[0])
            elif mirror == "\\":
                delta = (delta[1], delta[0])
        return tuple(trace), False

    def light_sensor_states(self) -> dict[str, bool]:
        return {rule.sensor: self._trace_light(rule)[1] for rule in self.map.lights}

    def _one_way_allows(self, cell: str, move: str) -> bool:
        return all(rule.direction == move for rule in self.map.one_ways if rule.symbol == cell)

    def _actor_cell_error(self, role: Role, target: Position, doors: Mapping[str, bool], move: str) -> str | None:
        cell = self.static_cell(target)
        if cell == "#":
            return "blocked_by_wall"
        if cell in "ABCDE" and not self._door_open_at(target, doors):
            return f"door_{self._actuator_id_at('door', target) or cell}_closed"
        if cell == "=" and not self._platform_open_at(target):
            return "platform_inactive"
        if cell == "J" and not self._one_way_allows(cell, move):
            return "one_way_wrong_direction"
        if cell == "f" and role != "F":
            return "wrong_exit"
        if cell == "w" and role != "W":
            return "wrong_exit"
        return None

    def _movable_can_enter(
        self,
        target: Position,
        doors: Mapping[str, bool],
        moving_from: set[Position],
        move: str,
    ) -> bool:
        cell = self.static_cell(target)
        if cell == "#" or cell in "~^xfw":
            return False
        if cell in "ABCDE" and not self._door_open_at(target, doors):
            return False
        if cell == "=" and not self._platform_open_at(target):
            return False
        if cell == "J" and not self._one_way_allows(cell, move):
            return False
        if target in self.crates and target not in moving_from:
            return False
        if target in self.orbs and target not in moving_from:
            return False
        if target in self.actors.values():
            return False
        return True

    def _update_terminal(self) -> None:
        if not all(self.alive.values()):
            self.status = "team_failure"
            return
        if self.static_cell(self.actors["F"]) == "f" and self.static_cell(self.actors["W"]) == "w":
            self.status = "team_success"

    def _micro_step(
        self,
        moves: Mapping[Role, str | None],
        active: dict[Role, bool],
        feedback: dict[Role, str],
    ) -> None:
        previous_actors = dict(self.actors)
        doors = self.door_states()
        proposals: dict[Role, Position] = {}
        portal_roles: set[Role] = set()
        push_requests: dict[Position, list[tuple[Role, tuple[int, int], Position, str]]] = {}
        orb_requests: dict[Position, list[tuple[Role, tuple[int, int], str]]] = {}
        for role in ("F", "W"):
            move = moves.get(role)
            if not active[role] or move is None or not self.alive[role]:
                continue
            delta = DIRECTIONS[move]
            target = _add(self.actors[role], delta)
            error = self._actor_cell_error(role, target, doors, move)
            if error:
                active[role] = False
                feedback[role] = error
                continue
            proposals[role] = target
            if target in self.crates:
                push_requests.setdefault(target, []).append((role, delta, _add(target, delta), move))
            elif target in self.orbs:
                orb_requests.setdefault(target, []).append((role, delta, move))
            else:
                portal_exit = self.portal_exit(target)
                if portal_exit is not None:
                    if portal_exit in self.crates or portal_exit in self.orbs:
                        active[role] = False
                        feedback[role] = "portal_exit_blocked"
                        continue
                    proposals[role] = portal_exit
                    portal_roles.add(role)

        successful_pushes: dict[Position, Position] = {}
        desired_destinations: dict[Position, list[Position]] = {}
        for crate, requests in push_requests.items():
            deltas = {request[1] for request in requests}
            if len(deltas) != 1:
                for role, _, _, _ in requests:
                    active[role] = False
                    feedback[role] = "conflicting_crate_push"
                continue
            raw_destination = requests[0][2]
            move = requests[0][3]
            destination = self.portal_exit(raw_destination) or raw_destination
            desired_destinations.setdefault(destination, []).append(crate)
            raw_clear = self._movable_can_enter(raw_destination, doors, set(push_requests), move)
            exit_clear = destination == raw_destination or self._movable_can_enter(destination, doors, set(push_requests), move)
            if raw_clear and exit_clear:
                successful_pushes[crate] = destination
            else:
                for role, _, _, _ in requests:
                    active[role] = False
                    feedback[role] = "crate_blocked"
        for destination, crates in desired_destinations.items():
            if len(crates) > 1:
                for crate in crates:
                    successful_pushes.pop(crate, None)
                    for role, _, _, _ in push_requests[crate]:
                        active[role] = False
                        feedback[role] = "crate_destination_conflict"

        successful_orbs: dict[Position, Position] = {}
        for orb, requests in orb_requests.items():
            deltas = {request[1] for request in requests}
            if len(deltas) != 1:
                for role, _, _ in requests:
                    active[role] = False
                    feedback[role] = "conflicting_orb_push"
                continue
            delta = requests[0][1]
            move = requests[0][2]
            destination = orb
            visited = {orb}
            for _ in range(self.map.width * self.map.height):
                raw_candidate = _add(destination, delta)
                candidate = self.portal_exit(raw_candidate) or raw_candidate
                raw_clear = self._movable_can_enter(raw_candidate, doors, set(orb_requests), move)
                exit_clear = candidate == raw_candidate or self._movable_can_enter(candidate, doors, set(orb_requests), move)
                if not raw_clear or not exit_clear or candidate in visited:
                    break
                destination = candidate
                visited.add(candidate)
            if destination == orb:
                for role, _, _ in requests:
                    active[role] = False
                    feedback[role] = "orb_blocked"
            else:
                successful_orbs[orb] = destination

        # Resolve destinations across every movable type before mutating state.
        # The individual crate/orb proposal passes intentionally inspect the
        # same frozen pre-step state; this final arbitration prevents two
        # objects from materialising in one cell after simultaneous pushes.
        movable_destinations: dict[Position, list[tuple[str, Position]]] = {}
        for source, destination in successful_pushes.items():
            movable_destinations.setdefault(destination, []).append(("crate", source))
        for source, destination in successful_orbs.items():
            movable_destinations.setdefault(destination, []).append(("orb", source))
        for destination, movers in movable_destinations.items():
            if len(movers) <= 1:
                continue
            for kind, source in movers:
                requests = push_requests[source] if kind == "crate" else orb_requests[source]
                if kind == "crate":
                    successful_pushes.pop(source, None)
                else:
                    successful_orbs.pop(source, None)
                for request in requests:
                    role = request[0]
                    active[role] = False
                    feedback[role] = "movable_destination_conflict"

        if successful_pushes:
            self.crates.difference_update(successful_pushes)
            self.crates.update(successful_pushes.values())
        if successful_orbs:
            self.orbs.difference_update(successful_orbs)
            self.orbs.update(successful_orbs.values())
        for role, target in proposals.items():
            if not active[role]:
                continue
            if target in push_requests and target not in successful_pushes:
                continue
            if target in orb_requests and target not in successful_orbs:
                continue
            self.actors[role] = target
            feedback[role] = "moved_via_portal" if role in portal_roles else "moved"

        entered_cells = {
            self.static_cell(position)
            for role, position in self.actors.items()
            if position != previous_actors[role]
        }
        for role, position in self.actors.items():
            if position == previous_actors[role]:
                continue
            for controller in self.map.controllers:
                if controller.position != position:
                    continue
                if controller.kind == "lever":
                    self.levers_on.add(controller.id)
                elif controller.kind == "toggle":
                    if controller.id in self.toggles_on:
                        self.toggles_on.remove(controller.id)
                    else:
                        self.toggles_on.add(controller.id)
        if not self.map.controllers and "T" in entered_cells:
            if "T" in self.toggles_on:
                self.toggles_on.remove("T")
            else:
                self.toggles_on.add("T")
        if "H" in entered_cells and "K" in entered_cells:
            for role, position in self.actors.items():
                if position != previous_actors[role] and self.static_cell(position) in {"H", "K"}:
                    feedback[role] = "thermal_control_conflict"
        elif "H" in entered_cells:
            self.thermal_frozen["I"] = False
        elif "K" in entered_cells:
            self.thermal_frozen["I"] = True
        for role, position in self.actors.items():
            if not self.map.controllers and self.static_cell(position) == "L":
                self.levers_on.add("L")
            cell = self.static_cell(position)
            if cell == "I" and not self.thermal_frozen.get("I", False):
                cell = "~"
            if (role == "F" and cell == "~") or (role == "W" and cell == "^") or cell == "x":
                self.alive[role] = False
                feedback[role] = f"entered_hazard_{cell}"
        self.micro_steps += 1
        self._update_terminal()

    def step_joint(self, actions: Mapping[Role, SymbolAction]) -> SymbolStepResult:
        if self.status != "running":
            return SymbolStepResult(self.round, self.micro_steps, self.status, {"F": "terminal", "W": "terminal"}, self.observation())
        unknown = set(actions) - {"F", "W"}
        if unknown:
            raise ValueError(f"unknown roles: {sorted(unknown)}")
        normalized = {role: actions.get(role, SymbolAction(move="WAIT", steps=0)) for role in ("F", "W")}
        if any(action.steps > 1 for action in normalized.values()):
            raise ValueError("symbol actions move at most one grid cell")
        feedback: dict[Role, str] = {"F": "waited", "W": "waited"}
        active: dict[Role, bool] = {role: action.move != "WAIT" for role, action in normalized.items()}
        longest = max(action.steps for action in normalized.values())
        for index in range(longest):
            moves: dict[Role, str | None] = {
                role: action.move if index < action.steps and active[role] else None
                for role, action in normalized.items()
            }
            self._micro_step(moves, active, feedback)
            if self.status != "running":
                break
        self.round += 1
        return SymbolStepResult(self.round, self.micro_steps, self.status, feedback, self.observation())

    def render_rows(self) -> tuple[str, ...]:
        grid = [list(row) for row in self.static_rows]
        for rule in self.map.thermals:
            replacement = "I" if self.thermal_frozen[rule.field_symbol] else "~"
            for position in self.map.positions(rule.field_symbol):
                grid[position.row][position.col] = replacement
        for rule in self.map.platforms:
            replacement = "-" if self.platform_states()[rule.symbol] else "="
            for position in self.map.positions(rule.symbol):
                grid[position.row][position.col] = replacement
        for rule in self.map.actuators:
            opened = self._actuator_on(rule)
            if not opened:
                continue
            replacement = "-" if rule.kind == "platform" else None
            for position in rule.positions:
                cell = grid[position.row][position.col]
                grid[position.row][position.col] = replacement or cell.lower()
        for door, opened in self.door_states().items():
            if opened:
                if len(door) == 1 and door in "ABCDE":
                    for position in self.map.positions(door):
                        if self._actuator_id_at("door", position) is None:
                            grid[position.row][position.col] = door.lower()
        for lever in self.levers_on:
            controller = next((item for item in self.map.controllers if item.id == lever), None)
            positions = (controller.position,) if controller else self.map.positions(lever)
            for position in positions:
                grid[position.row][position.col] = grid[position.row][position.col].lower()
        for toggle in self.toggles_on:
            controller = next((item for item in self.map.controllers if item.id == toggle), None)
            positions = (controller.position,) if controller else self.map.positions(toggle)
            for position in positions:
                grid[position.row][position.col] = grid[position.row][position.col].lower()
        if self.map.mirrors:
            for position in self.map.positions("M"):
                grid[position.row][position.col] = self._mirror_cell()
        for rule in self.map.lights:
            trace, lit = self._trace_light(rule)
            for position in trace:
                if grid[position.row][position.col] == ".":
                    grid[position.row][position.col] = ":"
            if lit:
                sensor = self.map.unique_position(rule.sensor)
                grid[sensor.row][sensor.col] = "s"
        for crate in sorted(self.crates):
            grid[crate.row][crate.col] = "O"
        for orb in sorted(self.orbs):
            grid[orb.row][orb.col] = "o"
        same_live_cell = self.alive["F"] and self.alive["W"] and self.actors["F"] == self.actors["W"]
        for role in ("F", "W"):
            position = self.actors[role]
            grid[position.row][position.col] = role if self.alive[role] else "X"
        if same_live_cell:
            position = self.actors["F"]
            grid[position.row][position.col] = "&"
        return tuple("".join(row) for row in grid)

    def coordinate_map(self) -> str:
        rows = self.render_rows()
        tens = "".join(str(col // 10 % 10) if col >= 10 else " " for col in range(self.map.width))
        ones = "".join(str(col % 10) for col in range(self.map.width))
        return "\n".join([f"    {tens}", f"    {ones}", *(f"{row:02d}  {line}" for row, line in enumerate(rows))])

    def static_coordinate_map(self) -> str:
        """Return the immutable underlay, so occupied mechanisms never disappear."""

        tens = "".join(str(col // 10 % 10) if col >= 10 else " " for col in range(self.map.width))
        ones = "".join(str(col % 10) for col in range(self.map.width))
        return "\n".join(
            [f"    {tens}", f"    {ones}", *(f"{row:02d}  {line}" for row, line in enumerate(self.static_rows))]
        )

    def _positions(self, symbol: str) -> list[list[int]]:
        return [[position.row, position.col] for position in self.map.positions(symbol)]

    def public_layout(self) -> dict[str, object]:
        """Machine-readable immutable layout and public mechanism wiring."""
        layout: dict[str, object] = {
            "walls": self._positions("#"),
            "exits": {"F": self._positions("f"), "W": self._positions("w")},
            "hazards": {symbol: self._positions(symbol) for symbol in ("~", "^", "x")},
            "plates": {
                rule.symbol: {
                    "positions": self._positions(rule.symbol),
                    "opens": list(rule.doors),
                    "accepts": sorted(rule.accepts),
                }
                for rule in self.map.plates
            },
            "doors": {symbol: self._positions(symbol) for symbol in "ABCDE" if self.map.positions(symbol)},
            "levers": {
                rule.symbol: {"positions": self._positions(rule.symbol), "opens": list(rule.doors)}
                for rule in self.map.levers
            },
            "portals": {rule.symbol: self._positions(rule.symbol) for rule in self.map.portals},
            "toggles": {
                rule.symbol: {"positions": self._positions(rule.symbol), "targets": list(rule.targets)}
                for rule in self.map.toggles
            },
            "one_ways": {
                rule.symbol: {"positions": self._positions(rule.symbol), "allows": rule.direction}
                for rule in self.map.one_ways
            },
            "thermals": {
                rule.field_symbol: {
                    "positions": self._positions(rule.field_symbol),
                    "heater": self._positions(rule.heater),
                    "freezer": self._positions(rule.freezer),
                }
                for rule in self.map.thermals
            },
            "lights": {
                rule.sensor: {
                    "source": self._positions(rule.source),
                    "sensor": self._positions(rule.sensor),
                    "direction": rule.direction,
                    "opens": list(rule.doors),
                }
                for rule in self.map.lights
            },
            "mirrors": {
                rule.symbol: {
                    "positions": self._positions(rule.symbol),
                    "initial": rule.initial,
                    "current": "slash" if self._mirror_cell() == "/" else "backslash",
                    "controlled_by": rule.controller,
                }
                for rule in self.map.mirrors
            },
            "platforms": {
                rule.symbol: {"positions": self._positions(rule.symbol), "controlled_by": rule.controller}
                for rule in self.map.platforms
            },
        }
        if self.map.controllers:
            layout["controllers"] = {
                rule.id: {
                    "kind": rule.kind,
                    "position": [rule.position.row, rule.position.col],
                    **({"accepts": sorted(rule.accepts)} if rule.kind == "plate" else {}),
                }
                for rule in self.map.controllers
            }
            layout["actuators"] = {
                rule.id: {
                    "kind": rule.kind,
                    "positions": [[position.row, position.col] for position in rule.positions],
                    "controlled_by": {
                        "mode": rule.controlled_by.mode,
                        "inputs": list(rule.controlled_by.inputs),
                    },
                }
                for rule in self.map.actuators
            }
            layout["plates"] = {
                rule.id: {
                    "positions": [[rule.position.row, rule.position.col]],
                    "accepts": sorted(rule.accepts),
                }
                for rule in self.map.controllers
                if rule.kind == "plate"
            }
            layout["levers"] = {
                rule.id: {"positions": [[rule.position.row, rule.position.col]]}
                for rule in self.map.controllers
                if rule.kind == "lever"
            }
            layout["toggles"] = {
                rule.id: {"positions": [[rule.position.row, rule.position.col]]}
                for rule in self.map.controllers
                if rule.kind == "toggle"
            }
            layout["doors"] = {
                rule.id: [[position.row, position.col] for position in rule.positions]
                for rule in self.map.actuators
                if rule.kind == "door"
            }
            for light in self.map.lights:
                for door in light.doors:
                    positions = [
                        [position.row, position.col]
                        for position in self.map.positions(door)
                        if self._actuator_id_at("door", position) is None
                    ]
                    if positions:
                        layout["doors"][door] = positions
            layout["platforms"] = {
                rule.id: {
                    "positions": [[position.row, position.col] for position in rule.positions],
                    "controlled_by": {
                        "mode": rule.controlled_by.mode,
                        "inputs": list(rule.controlled_by.inputs),
                    },
                }
                for rule in self.map.actuators
                if rule.kind == "platform"
            }
        return layout

    def observation(self) -> dict[str, object]:
        return {
            "format": "fwcollab.symbol_observation.v3" if self.map.controllers else "fwcollab.symbol_observation.v2",
            "map_id": self.map.map_id,
            "round": self.round,
            "micro_steps": self.micro_steps,
            "map": self.coordinate_map(),
            "map_rows": list(self.render_rows()),
            "terrain_map": self.static_coordinate_map(),
            "terrain_rows": list(self.static_rows),
            "layout": self.public_layout(),
            "action_space": {
                "moves": ["UP", "DOWN", "LEFT", "RIGHT", "WAIT"],
                "movement_steps": 1,
                "wait_steps": 0,
                "simultaneous": True,
            },
            "rules": public_rulebook(),
            "state": {
                "actors": {role: [position.row, position.col] for role, position in self.actors.items()},
                "alive": dict(self.alive),
                "crates": [[position.row, position.col] for position in sorted(self.crates)],
                "orbs": [[position.row, position.col] for position in sorted(self.orbs)],
                "plates": self.plate_states(),
                "doors": self.door_states(),
                "levers": {
                    **{rule.symbol: rule.symbol in self.levers_on for rule in self.map.levers},
                    **{rule.id: rule.id in self.levers_on for rule in self.map.controllers if rule.kind == "lever"},
                },
                "toggles": {
                    **{rule.symbol: rule.symbol in self.toggles_on for rule in self.map.toggles},
                    **{rule.id: rule.id in self.toggles_on for rule in self.map.controllers if rule.kind == "toggle"},
                },
                "controllers": self.controller_states(),
                "thermal_frozen": dict(self.thermal_frozen),
                "light_sensors": self.light_sensor_states(),
                "platforms": self.platform_states(),
                "portals": {
                    rule.symbol: [[position.row, position.col] for position in self.map.positions(rule.symbol)]
                    for rule in self.map.portals
                },
            },
            "status": self.status,
        }

    def state_hash(self) -> str:
        # Rules are versioned public protocol metadata, not mutable world
        # state. Excluding them preserves replay hashes created before the
        # compact rulebook was embedded in every observation.
        value = self.observation()
        value.pop("rules", None)
        # v3.2 exposes the mirror wiring and current orientation explicitly.
        # Both were already represented by the immutable map and map_rows, so
        # omit the new convenience fields to keep v3.1 trace hashes replayable.
        layout = value.get("layout")
        if isinstance(layout, dict):
            mirrors = layout.get("mirrors")
            if isinstance(mirrors, dict):
                for mirror in mirrors.values():
                    if isinstance(mirror, dict):
                        mirror.pop("controlled_by", None)
                        mirror.pop("current", None)
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
