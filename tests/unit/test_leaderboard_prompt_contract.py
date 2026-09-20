from __future__ import annotations

import importlib.util
from pathlib import Path


def test_frozen_prompt_contract_exactly_reproduces() -> None:
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts/freeze_leaderboard_prompt_contract.py"
    spec = importlib.util.spec_from_file_location("freeze_leaderboard_prompt_contract", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._verify_sources()
    expected = module.expected_files()
    out = root / "eval_private/leaderboard_v1/prompt_contract"
    for name, data in expected.items():
        assert (out / name).read_bytes() == data
