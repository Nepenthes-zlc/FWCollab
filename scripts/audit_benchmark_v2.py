"""Pure-local integrity, witness, replay, dispatch, and freeze audit for Benchmark v2."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fwcollab.benchmark_v2 import TRACKS, build_benchmark_v2_manifest, write_benchmark_v2  # noqa: E402
from fwcollab.c5 import evaluate_c5_trace, run_c5_session, validate_information_suite  # noqa: E402
from fwcollab.symbolic.agents import AgentDecision, CoordinationMessage, ScriptedPolicy  # noqa: E402
from fwcollab.symbolic.collaboration_eval import evaluate_collaboration_trace  # noqa: E402
from fwcollab.symbolic.extension_eval import evaluate_extension_trace  # noqa: E402
from fwcollab.symbolic.map import load_symbol_map  # noqa: E402
from fwcollab.symbolic.runner import DualAgentSession, replay_trace  # noqa: E402
from fwcollab.symbolic.world import SymbolAction  # noqa: E402
from fwcollab.unified_eval import evaluate_fwcollab_task, validate_unified_evaluation  # noqa: E402

OUT = ROOT / "artifacts/audits/benchmark_v2_freeze"

LEGACY_HASHES = {
    "eval_private/benchmark_v1/freeze_manifest.json": "f375d465e798e1bdc8161f9662565768299d488c34dc3783c774d22e2847799f",
    "eval_private/spatial_curriculum_full_v4/manifest.json": "1d7e4240da3fff204fb820f0704501564c05cfa5dc0630644d10382df57c19d0",
    "eval_private/spatial_curriculum_full_v4/witnesses.json": "19827795fc4d3ebe94d075e335764b3b1f663461cb339b81d01f5d915c867298",
    "eval_private/collaboration_diagnostic_24/manifest.json": "e3f0af67a3b123d0d75f816e760f809d8d6d649bd247868e009ca258eb616eda",
    "eval_private/c5_information_12/manifest.json": "cc3861f656c37b4447d8ddd913856923f4d3510f4f649b43432b0319f4197721",
    "eval_private/c5_information_12/freeze_manifest.json": "743353e67bc6ad63646199212d2874e3f6d65a7657a766841ac9f08f1475b3e3",
    "eval_private/synchronize_8/manifest.json": "c18afcdbba2ee70f807e11b2198247d9a5bce27fc0187564e76ee67e12b1b90a",
    "eval_private/synchronize_8/witnesses.json": "2cb0977eb2a1403b3b6b3817af717fadf7c136a6946022107519a65cfe26f3c5",
    "eval_private/synchronize_8/freeze_manifest.json": "31ff5bb16bfa68039de19ef3ef0b281b24bf7a84df4f65f096914fd570d19f4c",
    "eval_private/parallel_join_8/manifest.json": "7cefbdc6ea27f52eb14e5fd264cd4e7fff9e43dc4a54a57565ca2589412886c8",
    "eval_private/parallel_join_8/witnesses.json": "78133ec9f1464e1a9669c08ddf5e2c56fbd2534cd3e3fabeea6cbdbd66cfc149",
    "eval_private/parallel_join_8/freeze_manifest.json": "f00aee35a7d37e8c5676d5db9ff50190dd430262d5fe27d5f4ef889bddc59d4c",
    "eval_private/phase2_v1/freeze_manifest.json": "29b93111afd874cda37e848bacd184da4b799bde8faf4a0912dfd27f008830d6",
    "artifacts/evaluations/phase2_v1/summary.json": "cccf2137248183b1d4d84b3a4f0a73ca9e09b20c8c88fa20d7c80d628524822f",
    "artifacts/evaluations/evaluator_conformance_v1/summary.json": "81a05fd07273e97f125848ebe1461aa8e5b1df114ff21065df8faf17b7b1ea2b",
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _decision(move: str, *, message: CoordinationMessage | None = None) -> AgentDecision:
    return AgentDecision(action=SymbolAction(move=move, steps=0 if move == "WAIT" else 1), message=message, reason="offline Benchmark v2 witness audit")


def _trace(symbol_map, actions: Sequence[Mapping[str, Sequence[Any]]]) -> dict[str, Any]:
    policies = {role: ScriptedPolicy([_decision(str(item[role][0])) for item in actions], name="offline-witness") for role in ("F", "W")}
    return DualAgentSession(symbol_map, policies, max_rounds=len(actions), planning_rounds=0, coordination_mode="emergent").run().trace


def _moves(*moves: str) -> list[AgentDecision]:
    return [_decision(move) for move in moves]


def _c5_trace(task: Mapping[str, Any]) -> dict[str, Any]:
    controller_rows = {"c_north": 2, "c_center": 4, "c_south": 6}
    target_rows = {"gate_alpha": 10, "gate_beta": 12, "gate_gamma": 14}
    row = controller_rows[str(task["answer_controller"])]
    outward, back = ("UP", "DOWN") if row == 2 else ("DOWN", "UP")
    fire = [_decision("WAIT")]
    if row != 4:
        fire += _moves(outward, outward)
    fire += _moves(*(["RIGHT"] * 10), *(["LEFT"] * 10))
    if row != 4:
        fire += _moves(back, back)
    fire += _moves("LEFT", "LEFT")
    target = str(task["required_actuator"])
    target_row = target_rows[target]
    outward, back = ("UP", "DOWN") if target_row == 10 else ("DOWN", "UP")
    message = CoordinationMessage(stage=target, status="INFO", fact=f"required actuator {target}", request=f"activate the controller mapped to {target}", until=f"{target} is open")
    water = [_decision("RIGHT", message=message)]
    if target_row != 12:
        water += _moves(outward, outward)
    water += _moves(*(["RIGHT"] * 6), *(["WAIT"] * 6), *(["RIGHT"] * 8))
    if target_row != 12:
        water += _moves(back, back)
    water += _moves("RIGHT", "RIGHT")
    symbol_map = load_symbol_map(ROOT / "eval_private/c5_information_12" / str(task["map"]))
    policies = {"F": ScriptedPolicy(fire, name="offline-c5-F"), "W": ScriptedPolicy(water, name="offline-c5-W")}
    return run_c5_session(symbol_map, policies, task, max_rounds=30).trace


def _verify_freeze(path: Path) -> list[str]:
    value = _load(path)
    errors = []
    for item in value.get("files", []):
        target = ROOT / item["path"]
        if not target.is_file() or _sha(target) != item["sha256"] or target.stat().st_size != item["bytes"]:
            errors.append(str(item["path"]))
    return errors


def audit() -> dict[str, Any]:
    write_benchmark_v2(ROOT)
    manifest = _load(ROOT / "eval_private/benchmark_v2/manifest.json")
    expected = build_benchmark_v2_manifest(ROOT)
    errors: list[str] = []
    if manifest != expected:
        errors.append("manifest is not deterministic/canonical")
    tasks = manifest["tasks"]
    if len(tasks) != 100 or len({item["task_id"] for item in tasks}) != 100:
        errors.append("Benchmark v2 must contain 100 unique task references")
    if len(manifest["evaluation_profiles"]["standard"]["task_ids"]) != 52:
        errors.append("Standard profile must contain 52 tasks")
    if len(manifest["evaluation_profiles"]["full"]["task_ids"]) != 100:
        errors.append("Full profile must contain 100 tasks")

    baseline = []
    for relative, expected_hash in LEGACY_HASHES.items():
        actual = _sha(ROOT / relative) if (ROOT / relative).is_file() else "missing"
        baseline.append({"path": relative, "expected_sha256": expected_hash, "actual_sha256": actual, "unchanged": actual == expected_hash})
        if actual != expected_hash:
            errors.append(f"frozen legacy hash mismatch: {relative}")
    freeze_checks = {}
    for relative in (
        "eval_private/benchmark_v1/freeze_manifest.json", "eval_private/c5_information_12/freeze_manifest.json",
        "eval_private/synchronize_8/freeze_manifest.json", "eval_private/parallel_join_8/freeze_manifest.json",
        "eval_private/phase2_v1/freeze_manifest.json",
    ):
        freeze_checks[relative] = _verify_freeze(ROOT / relative)
        errors.extend(f"freeze entry mismatch: {item}" for item in freeze_checks[relative])

    sources = {name: _load(ROOT / spec["manifest"]) for name, spec in TRACKS.items()}
    core_witnesses = _load(ROOT / TRACKS["Core-72"]["witness"])["maps"]
    extension_witnesses = {
        "Sync-8": _load(ROOT / TRACKS["Sync-8"]["witness"])["tasks"],
        "Join-8": _load(ROOT / TRACKS["Join-8"]["witness"])["tasks"],
    }
    c5_tasks = sources["Information-12"]["tasks"]
    certificate = validate_information_suite(c5_tasks)
    if not certificate["valid"]:
        errors.append("C5 information certificate failed")

    rows = []
    for item in tasks:
        try:
            map_path, dag_path = ROOT / item["map_path"], ROOT / item["dag_path"]
            references_valid = map_path.is_file() and dag_path.is_file() and (ROOT / item["witness_path"]).is_file()
            if not references_valid:
                raise ValueError("referenced map/DAG/witness source is missing")
            source_manifest = sources[item["suite_name"]]
            source_records = source_manifest["tasks"] if item["suite_name"] == "Information-12" else source_manifest["records"]
            source = source_records[item["source_record_index"]]
            symbol_map = load_symbol_map(map_path)
            if item["suite_name"] == "Core-72":
                trace = _trace(symbol_map, core_witnesses[item["task_id"]])
                raw = evaluate_collaboration_trace(source, _load(dag_path), trace)
                raw_valid = trace["outcome"] == "team_success" and raw["summary"]["dag_completion"] == 1.0
            elif item["suite_name"] == "Information-12":
                trace = _c5_trace(source)
                raw = evaluate_c5_trace(source, trace)
                raw_valid = trace["outcome"] == "team_success" and raw["dag_completion"] == 1.0 and raw["clean_information_handoff"]
            else:
                actions = extension_witnesses[item["suite_name"]][item["task_id"]]["primary"]
                trace = _trace(symbol_map, actions)
                raw = evaluate_extension_trace(source, _load(dag_path), trace)
                raw_valid = trace["outcome"] == "team_success" and raw["valid"]
            replay_valid = bool(replay_trace(symbol_map, trace)["ok"])
            unified = evaluate_fwcollab_task(item, trace, root=ROOT)
            validate_unified_evaluation(unified)
            dispatch_valid = unified["raw_evaluation"] == raw and unified["success"] and unified["dag_completion"] == 1.0
            if not (raw_valid and replay_valid and dispatch_valid):
                raise ValueError("witness, replay, or dispatch failed")
            rows.append({"task_id": item["task_id"], "suite": item["suite_name"], "references_valid": True, "witness_valid": True, "replay_valid": True, "dispatch_valid": True})
        except Exception as exc:
            rows.append({"task_id": item["task_id"], "suite": item["suite_name"], "references_valid": False, "witness_valid": False, "replay_valid": False, "dispatch_valid": False, "error": f"{type(exc).__name__}: {exc}"})
            errors.append(f"{item['task_id']}: {type(exc).__name__}: {exc}")

    counts = Counter(item["suite"] for item in rows)
    report = {
        "format": "fwcollab.benchmark_v2_audit.v1", "passed": not errors, "task_count": len(rows),
        "track_counts": dict(counts), "references_valid": sum(item["references_valid"] for item in rows),
        "witness_valid": sum(item["witness_valid"] for item in rows), "replay_valid": sum(item["replay_valid"] for item in rows),
        "dispatch_valid": sum(item["dispatch_valid"] for item in rows), "legacy_hashes_unchanged": all(item["unchanged"] for item in baseline),
        "source_freezes_valid": all(not value for value in freeze_checks.values()), "model_calls": 0,
        "legacy_hashes": baseline, "source_freeze_mismatches": freeze_checks, "errors": errors, "tasks": rows,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# FWCollab Benchmark v2 freeze audit", "", f"Status: **{'PASS' if report['passed'] else 'FAIL'}**", "",
        "| Check | Result |", "|---|---:|", f"| Task references | {report['references_valid']}/100 |",
        f"| Successful deterministic witnesses/certificates | {report['witness_valid']}/100 |",
        f"| Deterministic replays | {report['replay_valid']}/100 |", f"| Unified evaluator dispatch | {report['dispatch_valid']}/100 |",
        f"| Legacy frozen hashes | {'unchanged' if report['legacy_hashes_unchanged'] else 'FAILED'} |",
        f"| Source freeze manifests | {'valid' if report['source_freezes_valid'] else 'FAILED'} |", "| Model/API calls | 0 |", "",
        "Track counts: Core-72 = 72, Information-12 = 12, Sync-8 = 8, Join-8 = 8. The C5 witness is its frozen constructor/budget certificate, replayed locally as an information-handoff trace; no new C5 witness artifact was introduced.",
    ]
    if errors:
        lines += ["", "## Errors", "", *(f"- {value}" for value in errors)]
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    value = audit()
    print(json.dumps({key: value[key] for key in ("passed", "task_count", "track_counts", "references_valid", "witness_valid", "replay_valid", "dispatch_valid", "legacy_hashes_unchanged", "source_freezes_valid", "model_calls")}, ensure_ascii=False))
    raise SystemExit(0 if value["passed"] else 1)
