from __future__ import annotations

import pytest

from fwcollab.symbolic.map import SymbolMapError, parse_symbol_map


SIMPLE = """@format fwcollab.symbol_map.v1
@id simple
@title Simple
@plate 1 opens A accepts F,W,O
---
#########
#F1A..f.#
#..O....#
#W....w.#
#########
"""


def test_parser_builds_strict_symbol_map() -> None:
    symbol_map = parse_symbol_map(SIMPLE)
    assert symbol_map.width == 9
    assert symbol_map.height == 5
    assert symbol_map.unique_position("F").row == 1
    assert symbol_map.plates[0].doors == ("A",)


@pytest.mark.parametrize(
    ("changed", "message"),
    [
        (SIMPLE.replace("#W....w.#", "#W...w.#"), "rectangular"),
        (SIMPLE.replace("A accepts", "B accepts"), "every door"),
        (SIMPLE.replace("#F1A..f.#", "#F1A.?f.#"), "unknown grid symbols"),
        (SIMPLE.replace("@title Simple\n", "@mystery yes\n"), "unknown directive"),
        (SIMPLE.replace("@title Simple\n", "@title Simple\n@max_steps 2\n"), "unit-step ruleset"),
    ],
)
def test_parser_rejects_ambiguous_or_invalid_maps(changed: str, message: str) -> None:
    with pytest.raises(SymbolMapError, match=message):
        parse_symbol_map(changed)
