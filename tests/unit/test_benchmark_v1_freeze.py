from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.freeze_benchmark_v1 import (
    FreezeError,
    create_manifest,
    main,
    verify_manifest,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FREEZE = PROJECT_ROOT / "eval_private/benchmark_v1/freeze_manifest.json"


def test_committed_freeze_verifies_and_covers_full_72(capsys: pytest.CaptureFixture[str]) -> None:
    result = verify_manifest(PROJECT_ROOT, FREEZE)
    assert result == {
        "ok": True,
        "manifest": str(FREEZE),
        "files": 144,
        "tasks": 72,
        "maps": 72,
        "dags": 72,
        "phase2_files": 61,
    }

    assert main(["verify", "--root", str(PROJECT_ROOT), "--manifest", str(FREEZE)]) == 0
    cli_result = json.loads(capsys.readouterr().out)
    assert cli_result["action"] == "verified"
    assert cli_result["files"] == 144


def test_freeze_manifest_has_unique_complete_asset_pairs_and_phase2_link() -> None:
    payload = json.loads(FREEZE.read_text(encoding="utf-8"))
    tasks = payload["tasks"]
    files = payload["files"]
    assert len(tasks) == 72
    assert len({task["id"] for task in tasks}) == 72
    assert len({task["map"] for task in tasks}) == 72
    assert len({task["dag"] for task in tasks}) == 72
    assert sum(item["path"].endswith(".fwmap") for item in files) == 72
    assert sum(item["path"].endswith(".json") for item in files) == 72
    assert [item["path"] for item in files] == sorted(item["path"] for item in files)
    assert payload["linked_phase2_freeze"]["path"] == (
        "eval_private/phase2_v1/freeze_manifest.json"
    )
    assert payload["linked_phase2_freeze"]["declared_file_count"] == 61
    assert payload["linked_phase2_freeze"]["condition_count"] == 6
    assert payload["linked_phase2_freeze"]["replicates"] == 3
    assert payload["linked_phase2_freeze"]["expected_episodes"] == 432
    assert payload["statement"] == (
        "The diagnostic subset was selected solely from task structure and collaboration "
        "primitives, without access to model performance."
    )
    assert payload["invariants"]["selection_uses_model_outcomes"] is False
    assert payload["invariants"]["condition_count"] == 6
    assert payload["invariants"]["replicates"] == 3
    assert payload["invariants"]["expected_episodes"] == 432
    assert payload["invariants"]["spatial_topology_unique"] == 72


def test_create_refuses_to_overwrite(tmp_path: Path) -> None:
    destination = tmp_path / "freeze_manifest.json"
    created, payload = create_manifest(PROJECT_ROOT, destination)
    assert created == destination
    assert len(payload["files"]) == 144
    before = destination.read_bytes()

    with pytest.raises(FreezeError, match="refusing to overwrite"):
        create_manifest(PROJECT_ROOT, destination)

    assert destination.read_bytes() == before


def test_verify_detects_manifest_tampering_without_touching_assets(tmp_path: Path) -> None:
    payload = json.loads(FREEZE.read_text(encoding="utf-8"))
    payload["files"][0]["sha256"] = "0" * 64
    tampered = tmp_path / "tampered_freeze.json"
    tampered.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(FreezeError, match="content mismatch"):
        verify_manifest(PROJECT_ROOT, tampered)
