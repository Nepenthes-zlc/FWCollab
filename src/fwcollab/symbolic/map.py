"""Parser and validator for the human-readable .fwmap format."""

from __future__ import annotations

import shlex
import re
from dataclasses import dataclass
from pathlib import Path

V1_GRID_SYMBOLS = frozenset("#.FWO~^xfw123456789ABCDEL")
V2_GRID_SYMBOLS = V1_GRID_SYMBOLS | frozenset("PQRTMIHK+S=/\\Jo=")
ALLOWED_GRID_SYMBOLS = V2_GRID_SYMBOLS
DOOR_SYMBOLS = frozenset("ABCDE")
ROLE_SYMBOLS = frozenset("FW")
LOAD_SYMBOLS = frozenset("FWO")
PORTAL_SYMBOLS = frozenset("PQR")
DIRECTION_NAMES = frozenset({"UP", "DOWN", "LEFT", "RIGHT"})
INSTANCE_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*$")


class SymbolMapError(ValueError):
    """A map cannot be parsed without guessing its meaning."""


@dataclass(frozen=True, order=True, slots=True)
class Position:
    row: int
    col: int


@dataclass(frozen=True, slots=True)
class PlateRule:
    symbol: str
    doors: tuple[str, ...]
    accepts: frozenset[str]


@dataclass(frozen=True, slots=True)
class LeverRule:
    symbol: str
    doors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PortalRule:
    symbol: str


@dataclass(frozen=True, slots=True)
class ToggleRule:
    symbol: str
    targets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OneWayRule:
    symbol: str
    direction: str


@dataclass(frozen=True, slots=True)
class ThermalRule:
    field_symbol: str
    initial: str
    heater: str
    freezer: str


@dataclass(frozen=True, slots=True)
class LightRule:
    source: str
    direction: str
    sensor: str
    doors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MirrorRule:
    symbol: str
    initial: str
    controller: str | None = None


@dataclass(frozen=True, slots=True)
class PlatformRule:
    symbol: str
    controller: str


@dataclass(frozen=True, slots=True)
class SignalExpression:
    mode: str
    inputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ControllerRule:
    id: str
    kind: str
    position: Position
    accepts: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ActuatorRule:
    id: str
    kind: str
    positions: tuple[Position, ...]
    controlled_by: SignalExpression


@dataclass(frozen=True, slots=True)
class SymbolMap:
    format: str
    map_id: str
    title: str
    rows: tuple[str, ...]
    plates: tuple[PlateRule, ...]
    levers: tuple[LeverRule, ...]
    max_steps: int = 1
    portals: tuple[PortalRule, ...] = ()
    toggles: tuple[ToggleRule, ...] = ()
    one_ways: tuple[OneWayRule, ...] = ()
    thermals: tuple[ThermalRule, ...] = ()
    lights: tuple[LightRule, ...] = ()
    mirrors: tuple[MirrorRule, ...] = ()
    platforms: tuple[PlatformRule, ...] = ()
    controllers: tuple[ControllerRule, ...] = ()
    actuators: tuple[ActuatorRule, ...] = ()

    @property
    def width(self) -> int:
        return len(self.rows[0])

    @property
    def height(self) -> int:
        return len(self.rows)

    def positions(self, symbol: str) -> tuple[Position, ...]:
        return tuple(
            Position(row, col)
            for row, line in enumerate(self.rows)
            for col, cell in enumerate(line)
            if cell == symbol
        )

    def unique_position(self, symbol: str) -> Position:
        matches = self.positions(symbol)
        if len(matches) != 1:
            raise SymbolMapError(f"symbol {symbol!r} must appear exactly once, found {len(matches)}")
        return matches[0]

    def cell(self, position: Position) -> str:
        if position.row < 0 or position.row >= self.height or position.col < 0 or position.col >= self.width:
            return "#"
        return self.rows[position.row][position.col]

    def static_rows(self) -> tuple[str, ...]:
        return tuple(row.translate(str.maketrans({"F": ".", "W": ".", "O": ".", "o": "."})) for row in self.rows)


def _split_symbols(token: str, *, allowed: frozenset[str], field: str) -> tuple[str, ...]:
    values = tuple(part for part in token.split(",") if part)
    if not values or any(len(value) != 1 or value not in allowed for value in values):
        raise SymbolMapError(f"invalid {field}: {token!r}")
    if len(values) != len(set(values)):
        raise SymbolMapError(f"duplicate symbol in {field}: {token!r}")
    return values


def _position(token: str, *, source: str) -> Position:
    try:
        row_text, col_text = token.split(",", 1)
        position = Position(int(row_text), int(col_text))
    except (ValueError, TypeError) as exc:
        raise SymbolMapError(f"{source}: invalid row,col position {token!r}") from exc
    if position.row < 0 or position.col < 0:
        raise SymbolMapError(f"{source}: position must be non-negative: {token!r}")
    return position


def _positions(token: str, *, source: str) -> tuple[Position, ...]:
    values = tuple(_position(item, source=source) for item in token.split(";") if item)
    if not values or len(values) != len(set(values)):
        raise SymbolMapError(f"{source}: actuator positions must be a unique non-empty ';' list")
    return values


def _signal_expression(token: str, *, source: str) -> SignalExpression:
    match = re.fullmatch(r"(all|any)\(([A-Za-z][A-Za-z0-9_-]*(?:,[A-Za-z][A-Za-z0-9_-]*)*)\)", token)
    if not match:
        raise SymbolMapError(f"{source}: expected all(id[,id]) or any(id[,id]), got {token!r}")
    inputs = tuple(match.group(2).split(","))
    if len(inputs) != len(set(inputs)):
        raise SymbolMapError(f"{source}: duplicate controller in expression {token!r}")
    return SignalExpression(mode=match.group(1), inputs=inputs)


def parse_symbol_map(text: str, *, source: str = "<memory>") -> SymbolMap:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    try:
        separator = lines.index("---")
    except ValueError as exc:
        raise SymbolMapError(f"{source}: missing --- separator") from exc

    header_lines = [line.strip() for line in lines[:separator] if line.strip()]
    grid_lines = lines[separator + 1 :]
    while grid_lines and grid_lines[-1] == "":
        grid_lines.pop()
    if not grid_lines or any(not row for row in grid_lines):
        raise SymbolMapError(f"{source}: grid cannot contain empty rows")

    scalar: dict[str, str] = {}
    plates: list[PlateRule] = []
    levers: list[LeverRule] = []
    portals: list[PortalRule] = []
    toggles: list[ToggleRule] = []
    one_ways: list[OneWayRule] = []
    thermals: list[ThermalRule] = []
    lights: list[LightRule] = []
    mirrors: list[MirrorRule] = []
    platforms: list[PlatformRule] = []
    controllers: list[ControllerRule] = []
    actuators: list[ActuatorRule] = []
    for line in header_lines:
        if not line.startswith("@"):
            raise SymbolMapError(f"{source}: header line must start with @: {line!r}")
        try:
            parts = shlex.split(line[1:])
        except ValueError as exc:
            raise SymbolMapError(f"{source}: invalid directive quoting: {line!r}") from exc
        if not parts:
            raise SymbolMapError(f"{source}: empty directive")
        name = parts[0]
        if name in {"format", "id", "title", "max_steps"}:
            if name in scalar:
                raise SymbolMapError(f"{source}: duplicate @{name}")
            if len(parts) < 2:
                raise SymbolMapError(f"{source}: @{name} requires a value")
            scalar[name] = " ".join(parts[1:])
        elif name == "plate":
            if len(parts) != 6 or parts[2] != "opens" or parts[4] != "accepts":
                raise SymbolMapError(f"{source}: expected @plate S opens A[,B] accepts F,W[,O]")
            symbol = parts[1]
            if len(symbol) != 1 or symbol not in "123456789":
                raise SymbolMapError(f"{source}: invalid pressure plate symbol {symbol!r}")
            plates.append(
                PlateRule(
                    symbol=symbol,
                    doors=_split_symbols(parts[3], allowed=DOOR_SYMBOLS, field="plate doors"),
                    accepts=frozenset(_split_symbols(parts[5], allowed=LOAD_SYMBOLS, field="accepted loads")),
                )
            )
        elif name == "lever":
            if len(parts) != 4 or parts[2] != "opens":
                raise SymbolMapError(f"{source}: expected @lever L opens A[,B]")
            symbol = parts[1]
            if symbol != "L":
                raise SymbolMapError(f"{source}: v1 lever symbol must be 'L'")
            levers.append(
                LeverRule(symbol=symbol, doors=_split_symbols(parts[3], allowed=DOOR_SYMBOLS, field="lever doors"))
            )
        elif name == "portal":
            if len(parts) != 2 or parts[1] not in PORTAL_SYMBOLS:
                raise SymbolMapError(f"{source}: expected @portal P|Q|R")
            portals.append(PortalRule(symbol=parts[1]))
        elif name == "toggle":
            if len(parts) != 4 or parts[1] != "T" or parts[2] != "toggles":
                raise SymbolMapError(f"{source}: expected @toggle T toggles A[,M]")
            toggles.append(
                ToggleRule(
                    symbol="T",
                    targets=_split_symbols(parts[3], allowed=DOOR_SYMBOLS | frozenset("M"), field="toggle targets"),
                )
            )
        elif name == "oneway":
            if len(parts) != 4 or parts[1] != "J" or parts[2] != "allows" or parts[3] not in DIRECTION_NAMES:
                raise SymbolMapError(f"{source}: expected @oneway J allows UP|DOWN|LEFT|RIGHT")
            one_ways.append(OneWayRule(symbol="J", direction=parts[3]))
        elif name == "thermal":
            if (
                len(parts) != 8
                or parts[1] != "I"
                or parts[2] != "initial"
                or parts[3] not in {"frozen", "liquid"}
                or parts[4:8:2] != ["heater", "freezer"]
                or parts[5] != "H"
                or parts[7] != "K"
            ):
                raise SymbolMapError(f"{source}: expected @thermal I initial frozen|liquid heater H freezer K")
            thermals.append(ThermalRule(field_symbol="I", initial=parts[3], heater="H", freezer="K"))
        elif name == "light":
            if (
                len(parts) != 8
                or parts[1] != "+"
                or parts[2] != "direction"
                or parts[3] not in DIRECTION_NAMES
                or parts[4] != "sensor"
                or parts[5] != "S"
                or parts[6] != "opens"
            ):
                raise SymbolMapError(f"{source}: expected @light + direction DIR sensor S opens A[,B]")
            lights.append(
                LightRule(
                    source="+",
                    direction=parts[3],
                    sensor="S",
                    doors=_split_symbols(parts[7], allowed=DOOR_SYMBOLS, field="light doors"),
                )
            )
        elif name == "mirror":
            if (
                len(parts) not in {4, 6}
                or parts[1] != "M"
                or parts[2] != "initial"
                or parts[3] not in {"slash", "backslash"}
                or (len(parts) == 6 and parts[4] != "controlled_by")
            ):
                raise SymbolMapError(
                    f"{source}: expected @mirror M initial slash|backslash [controlled_by CONTROLLER_ID]"
                )
            mirrors.append(
                MirrorRule(symbol="M", initial=parts[3], controller=parts[5] if len(parts) == 6 else None)
            )
        elif name == "platform":
            if len(parts) != 4 or parts[1] != "=" or parts[2] != "controlled_by" or parts[3] not in "123456789LT":
                raise SymbolMapError(f"{source}: expected @platform = controlled_by 1|L|T")
            platforms.append(PlatformRule(symbol="=", controller=parts[3]))
        elif name == "controller":
            if len(parts) not in {5, 7} or parts[3] != "at":
                raise SymbolMapError(
                    f"{source}: expected @controller ID plate|lever|toggle at row,col [accepts F,W,O]"
                )
            instance_id, kind = parts[1], parts[2]
            if not INSTANCE_ID_RE.fullmatch(instance_id) or kind not in {"plate", "lever", "toggle"}:
                raise SymbolMapError(f"{source}: invalid controller id or kind")
            accepts = frozenset()
            if kind == "plate":
                if len(parts) != 7 or parts[5] != "accepts":
                    raise SymbolMapError(f"{source}: plate controller requires accepts F,W[,O]")
                accepts = frozenset(_split_symbols(parts[6], allowed=LOAD_SYMBOLS, field="accepted loads"))
            elif len(parts) != 5:
                raise SymbolMapError(f"{source}: only plate controllers accept loads")
            controllers.append(
                ControllerRule(id=instance_id, kind=kind, position=_position(parts[4], source=source), accepts=accepts)
            )
        elif name == "actuator":
            if len(parts) != 7 or parts[3] != "at" or parts[5] != "controlled_by":
                raise SymbolMapError(
                    f"{source}: expected @actuator ID door|platform at r,c[;r,c] controlled_by all(...)|any(...)"
                )
            instance_id, kind = parts[1], parts[2]
            if not INSTANCE_ID_RE.fullmatch(instance_id) or kind not in {"door", "platform"}:
                raise SymbolMapError(f"{source}: invalid actuator id or kind")
            actuators.append(
                ActuatorRule(
                    id=instance_id,
                    kind=kind,
                    positions=_positions(parts[4], source=source),
                    controlled_by=_signal_expression(parts[6], source=source),
                )
            )
        else:
            raise SymbolMapError(f"{source}: unknown directive @{name}")

    required = {"format", "id", "title"}
    missing = required - scalar.keys()
    if missing:
        raise SymbolMapError(f"{source}: missing directives: {sorted(missing)}")
    if scalar["format"] not in {"fwcollab.symbol_map.v1", "fwcollab.symbol_map.v2", "fwcollab.symbol_map.v3"}:
        raise SymbolMapError(f"{source}: unsupported format {scalar['format']!r}")
    if not scalar["id"].replace("_", "").replace("-", "").isalnum():
        raise SymbolMapError(f"{source}: map id must be alphanumeric with _ or -")
    try:
        max_steps = int(scalar.get("max_steps", "1"))
    except ValueError as exc:
        raise SymbolMapError(f"{source}: max_steps must be an integer") from exc
    if max_steps != 1:
        raise SymbolMapError(f"{source}: max_steps must be 1 in the unit-step ruleset")

    width = len(grid_lines[0])
    if width < 3 or len(grid_lines) < 3 or any(len(row) != width for row in grid_lines):
        raise SymbolMapError(f"{source}: grid must be rectangular and at least 3x3")
    unknown = sorted({cell for row in grid_lines for cell in row} - ALLOWED_GRID_SYMBOLS)
    if unknown:
        raise SymbolMapError(f"{source}: unknown grid symbols: {unknown}")
    if scalar["format"] == "fwcollab.symbol_map.v1":
        extended = sorted({cell for row in grid_lines for cell in row} - V1_GRID_SYMBOLS)
        if extended or any((portals, toggles, one_ways, thermals, lights, mirrors, platforms)):
            raise SymbolMapError(f"{source}: v2 mechanism used with v1 format: {extended}")
    if any(cell != "#" for cell in grid_lines[0] + grid_lines[-1]):
        raise SymbolMapError(f"{source}: top and bottom boundaries must be walls")
    if any(row[0] != "#" or row[-1] != "#" for row in grid_lines):
        raise SymbolMapError(f"{source}: left and right boundaries must be walls")

    symbol_map = SymbolMap(
        format=scalar["format"],
        map_id=scalar["id"],
        title=scalar["title"],
        rows=tuple(grid_lines),
        plates=tuple(sorted(plates, key=lambda item: item.symbol)),
        levers=tuple(sorted(levers, key=lambda item: item.symbol)),
        max_steps=max_steps,
        portals=tuple(sorted(portals, key=lambda item: item.symbol)),
        toggles=tuple(sorted(toggles, key=lambda item: item.symbol)),
        one_ways=tuple(sorted(one_ways, key=lambda item: item.symbol)),
        thermals=tuple(thermals),
        lights=tuple(lights),
        mirrors=tuple(mirrors),
        platforms=tuple(platforms),
        controllers=tuple(sorted(controllers, key=lambda item: item.id)),
        actuators=tuple(sorted(actuators, key=lambda item: item.id)),
    )
    for symbol in ("F", "W", "f", "w"):
        symbol_map.unique_position(symbol)
    if scalar["format"] != "fwcollab.symbol_map.v3" and controllers:
        raise SymbolMapError(f"{source}: instance controllers require fwcollab.symbol_map.v3")
    if scalar["format"] != "fwcollab.symbol_map.v3" and actuators:
        raise SymbolMapError(f"{source}: instance actuators require fwcollab.symbol_map.v3")
    if scalar["format"] == "fwcollab.symbol_map.v3" and any((plates, levers, toggles, platforms)):
        raise SymbolMapError(f"{source}: v3 cannot mix legacy plate/lever/toggle/platform rules with instance rules")
    if scalar["format"] == "fwcollab.symbol_map.v3":
        ids = [rule.id for rule in (*controllers, *actuators)]
        if len(ids) != len(set(ids)):
            raise SymbolMapError(f"{source}: controller and actuator IDs must be globally unique")
        controller_ids = {rule.id for rule in controllers}
        for rule in actuators:
            missing_inputs = set(rule.controlled_by.inputs) - controller_ids
            if missing_inputs:
                raise SymbolMapError(f"{source}: actuator {rule.id} references missing controllers {sorted(missing_inputs)}")
        expected_glyph = {"plate": "123456789", "lever": "L", "toggle": "T"}
        controller_positions: set[Position] = set()
        for rule in controllers:
            if rule.position in controller_positions:
                raise SymbolMapError(f"{source}: two controllers occupy {rule.position}")
            controller_positions.add(rule.position)
            if symbol_map.cell(rule.position) not in expected_glyph[rule.kind]:
                raise SymbolMapError(f"{source}: {rule.kind} {rule.id} is not on its expected grid glyph")
        expected_actuator_glyph = {"door": DOOR_SYMBOLS, "platform": frozenset("=")}
        actuator_positions: set[Position] = set()
        for rule in actuators:
            for position in rule.positions:
                if position in actuator_positions:
                    raise SymbolMapError(f"{source}: two actuators occupy {position}")
                actuator_positions.add(position)
                if symbol_map.cell(position) not in expected_actuator_glyph[rule.kind]:
                    raise SymbolMapError(f"{source}: {rule.kind} {rule.id} is not on its expected grid glyph")
        grid_controller_positions = {
            Position(row, col)
            for row, line in enumerate(grid_lines)
            for col, cell in enumerate(line)
            if cell in "123456789LT"
        }
        grid_actuator_positions = {
            Position(row, col)
            for row, line in enumerate(grid_lines)
            for col, cell in enumerate(line)
            if cell in DOOR_SYMBOLS or cell == "="
        }
        if controller_positions != grid_controller_positions:
            raise SymbolMapError(f"{source}: every v3 controller glyph must have exactly one instance rule")
        light_door_symbols = {door for rule in lights for door in rule.doors}
        light_door_positions = {
            Position(row, col)
            for row, line in enumerate(grid_lines)
            for col, cell in enumerate(line)
            if cell in light_door_symbols
        }
        if actuator_positions | light_door_positions != grid_actuator_positions:
            raise SymbolMapError(
                f"{source}: every v3 actuator glyph must have an instance rule or be controlled by a light rule"
            )
        for rule in portals:
            if len(symbol_map.positions(rule.symbol)) != 2:
                raise SymbolMapError(f"{source}: portal {rule.symbol} must appear exactly twice")
        v3_exact = {
            "J": bool(one_ways),
            "I": bool(thermals),
            "H": bool(thermals),
            "K": bool(thermals),
            "+": bool(lights),
            "S": bool(lights),
            "M": bool(mirrors),
        }
        for symbol, expected in v3_exact.items():
            present = bool(symbol_map.positions(symbol))
            if present != expected:
                raise SymbolMapError(f"{source}: symbol {symbol!r} and its directive must appear together")
            if present and symbol not in {"I"}:
                symbol_map.unique_position(symbol)
        if any(cell in "/\\" for row in grid_lines for cell in row) and not lights:
            raise SymbolMapError(f"{source}: fixed mirrors require a light rule")
        controller_by_id = {rule.id: rule for rule in controllers}
        for rule in mirrors:
            if rule.controller is None:
                continue
            controller = controller_by_id.get(rule.controller)
            if controller is None or controller.kind != "toggle":
                raise SymbolMapError(
                    f"{source}: mirror controller {rule.controller!r} must reference an instance toggle"
                )
        return symbol_map
    if len({rule.symbol for rule in plates}) != len(plates):
        raise SymbolMapError(f"{source}: each pressure plate symbol can have only one rule")
    if len({rule.symbol for rule in levers}) != len(levers):
        raise SymbolMapError(f"{source}: each lever symbol can have only one rule")
    if len({rule.symbol for rule in portals}) != len(portals):
        raise SymbolMapError(f"{source}: each portal symbol can have only one rule")
    if len(toggles) > 1 or len(one_ways) > 1 or len(thermals) > 1 or len(lights) > 1 or len(mirrors) > 1 or len(platforms) > 1:
        raise SymbolMapError(f"{source}: v2 currently supports one rule per extended mechanism kind")
    grid_plates = {cell for row in grid_lines for cell in row if cell in "123456789"}
    grid_levers = {cell for row in grid_lines for cell in row if cell == "L"}
    grid_doors = {cell for row in grid_lines for cell in row if cell in DOOR_SYMBOLS}
    if grid_plates != {rule.symbol for rule in plates}:
        raise SymbolMapError(f"{source}: pressure plate directives must exactly match grid symbols")
    if grid_levers != {rule.symbol for rule in levers}:
        raise SymbolMapError(f"{source}: lever directives must exactly match grid symbols")
    grid_portals = {cell for row in grid_lines for cell in row if cell in PORTAL_SYMBOLS}
    if grid_portals != {rule.symbol for rule in portals}:
        raise SymbolMapError(f"{source}: portal directives must exactly match grid symbols")
    for rule in portals:
        if len(symbol_map.positions(rule.symbol)) != 2:
            raise SymbolMapError(f"{source}: portal {rule.symbol} must appear exactly twice")

    extended_exact = {
        "T": bool(toggles),
        "J": bool(one_ways),
        "I": bool(thermals),
        "H": bool(thermals),
        "K": bool(thermals),
        "+": bool(lights),
        "S": bool(lights),
        "M": bool(mirrors),
        "=": bool(platforms),
    }
    for symbol, expected in extended_exact.items():
        present = bool(symbol_map.positions(symbol))
        if present != expected:
            raise SymbolMapError(f"{source}: symbol {symbol!r} and its directive must appear together")
        if present and symbol not in {"I", "="}:
            symbol_map.unique_position(symbol)
    if mirrors and not lights:
        raise SymbolMapError(f"{source}: rotatable mirror requires a light rule")
    if any(cell in "/\\" for row in grid_lines for cell in row) and not lights:
        raise SymbolMapError(f"{source}: fixed mirrors require a light rule")
    if toggles:
        mirror_targeted = "M" in toggles[0].targets
        if mirror_targeted != bool(mirrors):
            raise SymbolMapError(f"{source}: toggle target M must exactly match the mirror rule")
    if platforms:
        controller = platforms[0].controller
        controllers = grid_plates | grid_levers | ({"T"} if toggles else set())
        if controller not in controllers:
            raise SymbolMapError(f"{source}: platform controller {controller!r} is missing")

    controlled_doors = {door for rule in (*plates, *levers) for door in rule.doors}
    controlled_doors.update(
        target for rule in toggles for target in rule.targets if target in DOOR_SYMBOLS
    )
    controlled_doors.update(door for rule in lights for door in rule.doors)
    if grid_doors != controlled_doors:
        raise SymbolMapError(f"{source}: every door must exist and have at least one controller")
    return symbol_map


def load_symbol_map(path: str | Path) -> SymbolMap:
    source = Path(path)
    return parse_symbol_map(source.read_text(encoding="utf-8"), source=str(source))
