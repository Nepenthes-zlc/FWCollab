from __future__ import annotations

from fwcollab.symbolic.html import render_symbol_gallery, render_symbol_html
from fwcollab.symbolic.map import parse_symbol_map
from tests.symbolic.test_symbol_map import SIMPLE


def test_html_is_standalone_and_embeds_initial_symbol_state() -> None:
    rendered = render_symbol_html(parse_symbol_map(SIMPLE))
    assert "<canvas" in rendered
    assert "window.renderFWCollabState" in rendered
    assert "fwcollab.symbol_observation.v2" in rendered
    assert "https://" not in rendered
    assert "#F1A..f.#" in rendered


def test_gallery_is_standalone_and_contains_each_map() -> None:
    symbol_map = parse_symbol_map(SIMPLE)
    rendered = render_symbol_gallery([symbol_map, symbol_map])
    assert '<main id="gallery">' in rendered
    assert rendered.count('"observation"') == 2
    assert "https://" not in rendered


def test_gallery_rejects_empty_input() -> None:
    import pytest

    with pytest.raises(ValueError, match="at least one"):
        render_symbol_gallery([])
