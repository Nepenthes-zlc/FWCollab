from __future__ import annotations

from pathlib import Path

import pytest

from fwcollab.symbolic.generator import generate_plate_support, validate_unique_suite
from fwcollab.symbolic.world import SymbolWorld


def test_seeded_generator_is_deterministic_and_witnessed() -> None:
    first = generate_plate_support(42)
    second = generate_plate_support(42)
    assert first.text == second.text
    assert all(action.steps in {0, 1} for joint in first.witness for action in joint.values())
    world = SymbolWorld(first.symbol_map)
    for joint in first.witness:
        world.step_joint(joint)
    assert world.status == "team_success"


def test_suite_duplicate_check_rejects_same_structure(tmp_path: Path) -> None:
    generated = generate_plate_support(7)
    first = tmp_path / "one.fwmap"
    second = tmp_path / "two.fwmap"
    first.write_text(generated.text, encoding="utf-8")
    second.write_text(generated.text.replace("@id G00000007", "@id another"), encoding="utf-8")
    with pytest.raises(ValueError, match="structural duplicate"):
        validate_unique_suite([first, second])
