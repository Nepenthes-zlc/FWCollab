"""Small seeded generator and strict suite-level duplicate validation."""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from fwcollab.symbolic.map import SymbolMap, load_symbol_map, parse_symbol_map
from fwcollab.symbolic.runner import map_fingerprint
from fwcollab.symbolic.world import Role, SymbolAction, SymbolWorld


@dataclass(frozen=True, slots=True)
class GeneratedMap:
    text: str
    symbol_map: SymbolMap
    witness: tuple[dict[Role, SymbolAction], ...]


def generate_plate_support(seed: int) -> GeneratedMap:
    """Generate a solvable held-plate relay without embedding its witness in the map."""

    rng = random.Random(seed)
    width = rng.randint(13, 20)
    fire_exit = rng.randint(max(6, width // 2), width - 3)
    water_exit = rng.randint(max(7, width // 2 + 1), width - 2)
    door = rng.randint(3, water_exit - 2)
    top = ["."] * (width - 2)
    bottom = ["."] * (width - 2)
    top[0], top[1], top[fire_exit - 1] = "F", "1", "f"
    bottom[0], bottom[door - 1], bottom[water_exit - 1] = "W", "A", "w"
    rows = ["#" * width, "#" + "".join(top) + "#", "#" * width, "#" + "".join(bottom) + "#", "#" * width]
    map_id = f"G{seed:08x}"
    text = "\n".join(
        [
            "@format fwcollab.symbol_map.v1",
            f"@id {map_id}",
            f"@title Seeded plate support {seed}",
            "@max_steps 1",
            "@plate 1 opens A accepts F",
            "---",
            *rows,
            "",
        ]
    )
    symbol_map = parse_symbol_map(text)
    witness: list[dict[Role, SymbolAction]] = []
    # Both start moving; F then maintains the plate until W reaches its exit.
    witness.append({"F": SymbolAction(move="RIGHT", steps=1), "W": SymbolAction(move="RIGHT", steps=1)})
    for _ in range(1, water_exit - 1):
        witness.append({"F": SymbolAction(move="WAIT", steps=0), "W": SymbolAction(move="RIGHT", steps=1)})
    for _ in range(1, fire_exit - 1):
        witness.append({"F": SymbolAction(move="RIGHT", steps=1), "W": SymbolAction(move="WAIT", steps=0)})
    world = SymbolWorld(symbol_map)
    for actions in witness:
        world.step_joint(actions)
    if world.status != "team_success":
        raise AssertionError(f"generator produced an unsolved certificate for seed {seed}")
    return GeneratedMap(text=text, symbol_map=symbol_map, witness=tuple(witness))


def validate_unique_suite(paths: Iterable[str | Path]) -> dict[str, object]:
    """Load all maps strictly and reject byte-independent structural duplicates."""

    loaded = [load_symbol_map(path) for path in paths]
    ids = [item.map_id for item in loaded]
    if len(ids) != len(set(ids)):
        raise ValueError("map IDs are not unique")
    fingerprints: dict[str, list[str]] = {}
    for item in loaded:
        fingerprints.setdefault(map_fingerprint(item), []).append(item.map_id)
    duplicates = [group for group in fingerprints.values() if len(group) > 1]
    if duplicates:
        raise ValueError(f"structural duplicate maps: {duplicates}")
    return {"ok": True, "maps": len(loaded), "unique_structures": len(fingerprints)}
