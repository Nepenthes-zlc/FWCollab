from __future__ import annotations

import json

import pytest

from fwcollab.cli import main


def test_top_level_help_names_symbol_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "symbol-validate" in output
    assert "symbol-render" in output
    assert "symbol-diagnose" in output
    assert "spatial-generate" in output
    assert "symbol-based cooperative planning benchmark" in output
    assert "render authoritative map state to a PNG" not in output


def test_doctor_reports_symbolic_route_truthfully(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["implementation_level"] == "symbolic_dual_agent"
    assert report["engine_created"] is True
    assert report["dual_agent_runner"] is True
    assert report["symbol_maps"] == 24
    assert isinstance(report["real_model_calls"], int)
    assert report["real_model_calls"] >= 0
