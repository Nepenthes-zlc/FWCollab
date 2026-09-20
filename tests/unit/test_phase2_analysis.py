from __future__ import annotations

import copy
import hashlib
import json
import math
import subprocess
from collections import Counter
from pathlib import Path
from statistics import fmean, median

import pytest

from fwcollab.analysis import phase2
from fwcollab.symbolic.map import parse_symbol_map
from fwcollab.symbolic.world import SymbolAction, SymbolWorld


SIMPLE_MAP = """@format fwcollab.symbol_map.v1
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


def _dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _conditions() -> list[dict[str, object]]:
    return [
        {
            "id": condition_id,
            "label": phase2.EXPECTED_CONDITION_LABELS[condition_id],
            "communication_enabled": contract["communication_enabled"],
            "F": dict(contract["F"]),
            "W": dict(contract["W"]),
        }
        for condition_id, contract in phase2.EXPECTED_CONDITION_CONTRACT.items()
    ]


def _make_project(root: Path) -> dict[str, Path]:
    conditions = _conditions()
    records = [
        {
            "id": f"TASK-{index:02d}",
            "difficulty": f"L{1 + (index - 1) % 7}",
            "map": "frozen/map.fwmap",
            "dag": "frozen/dag.json",
        }
        for index in range(1, 25)
    ]
    manifest = {
        "format": "fwcollab.symbolic.collaboration_diagnostic_set.v1",
        "records": records,
    }
    spec = {
        "format": "fwcollab.symbolic.phase2_experiment.v1",
        "benchmark_version": "v1",
        "task_manifest": "frozen/manifest.json",
        "statement": phase2.SELECTION_STATEMENT,
        "selection_uses_model_outcomes": False,
        "replicates": [1, 2, 3],
        "request_seeds": {
            "1": 2026091401,
            "2": 2026091402,
            "3": 2026091403,
        },
        "sampling": dict(phase2.EXPECTED_SAMPLING),
        "runner": dict(phase2.EXPECTED_RUNNER),
        "conditions": conditions,
        "primary_metrics": [
            "success_rate",
            "dag_completion",
            "dag_progress_auc",
            "clean_handoff_rate",
            "coordination_violation_rate",
            "rounds_to_success",
        ],
        "statistics": {
            "task_is_bootstrap_unit": True,
            "average_replicates_within_task_first": True,
            "paired_bootstrap_samples": 10000,
            "confidence_level": 0.95,
        },
    }
    spec_path = root / "frozen/spec.json"
    manifest_path = root / "frozen/manifest.json"
    map_path = root / "frozen/map.fwmap"
    dag_path = root / "frozen/dag.json"
    _dump(spec_path, spec)
    _dump(manifest_path, manifest)
    map_path.write_text("synthetic map; never a live trace\n", encoding="utf-8")
    _dump(dag_path, {"format": "synthetic.dag.v1"})

    run_dir = root / "runs"
    state_rows: list[dict[str, object]] = []
    for condition in conditions:
        for replicate, rounds in zip((1, 2, 3), (1, 79, 3), strict=True):
            for record in records:
                round_records = [
                    {
                        "round": round_index,
                        "agent_observation": {"shared_message_history": []},
                        "agents": {
                            role: {
                                "decision": {
                                    "action": {"move": "WAIT", "steps": 0},
                                    "message": None,
                                },
                                "feedback": "waited",
                                "inbox": [],
                                "error": None,
                                "model": condition[role]["model"],
                                "provider_calls": 1,
                            }
                            for role in ("F", "W")
                        },
                    }
                    for round_index in range(1, rounds + 1)
                ]
                trace = {
                    "format": "fwcollab.symbol_trace.v1",
                    "map_id": record["id"],
                    "protocol": {
                        "independent_histories": True,
                        "frozen_same_round_observation": True,
                        "simultaneous_submission": True,
                        "movement_steps": 1,
                        "message_delay_rounds": 1,
                        "max_rounds": 80,
                        "planning_rounds": 0,
                        "coordination_mode": "emergent",
                        "agent_observation": "fwcollab.agent_observation.v1",
                        "last_transition_visible": True,
                        "round_budget_visible": True,
                    },
                    "models": {
                        role: condition[role]["model"] for role in ("F", "W")
                    },
                    "planning": {"mode": "none", "rounds": [], "result": None},
                    "rounds": round_records,
                    "outcome": "team_success",
                    "metrics": {
                        "rounds": rounds,
                        "total_model_calls": 2 * rounds,
                        "total_model_errors": 0,
                        "total_messages": 0,
                        "planning": {
                            "enabled": False,
                            "rounds": 0,
                            "model_calls": 0,
                            "errors": 0,
                            "typed_consensus": False,
                        },
                    },
                    "diagnosis": {"primary": None},
                    "phase2": {
                        "format": spec["format"],
                        "benchmark_version": "v1",
                        "condition": condition["id"],
                        "condition_label": condition["label"],
                        "communication_enabled": condition["communication_enabled"],
                        "replicate": replicate,
                        "request_seed": spec["request_seeds"][str(replicate)],
                        "seed_reproducibility_guaranteed": False,
                        "temperature": 1.0,
                        "difficulty": record["difficulty"],
                        "dag": record["dag"],
                        "roles": {role: condition[role] for role in ("F", "W")},
                    },
                    "synthetic_evaluation": {
                        "dag_completion": 0.75,
                        "dag_progress_auc": 0.5,
                        "handoff_opportunities": 0,
                        "clean_handoffs": 0,
                        "coordination_violations": rounds,
                    },
                }
                trace_path = (
                    run_dir
                    / "traces"
                    / str(condition["id"])
                    / f"replicate_{replicate}"
                    / str(record["difficulty"])
                    / f"{record['id']}.json"
                )
                _dump(trace_path, trace)
                state_rows.append(
                    {
                        "condition": condition["id"],
                        "replicate": replicate,
                        "task_id": record["id"],
                        "difficulty": record["difficulty"],
                        "outcome": "team_success",
                        "rounds": rounds,
                        "model_calls": 2 * rounds,
                        "model_errors": 0,
                        "messages": 0,
                        "wall_time_ms": 1,
                        "trace": trace_path.as_posix(),
                        "resumed": False,
                    }
                )
    outcomes = dict(sorted(Counter(row["outcome"] for row in state_rows).items()))
    _dump(
        run_dir / "run_state.json",
        {
            "format": "fwcollab.symbolic.phase2_run_state.v1",
            "experiment_spec": spec_path.as_posix(),
            "expected_episodes": 432,
            "recorded_episodes": 432,
            "outcomes": outcomes,
            "total_model_calls": sum(row["model_calls"] for row in state_rows),
            "total_model_errors": sum(row["model_errors"] for row in state_rows),
            "results": state_rows,
        },
    )

    frozen_paths = [spec_path, manifest_path, map_path, dag_path]
    _dump(
        root / "frozen/freeze.json",
        {
            "format": "fwcollab.symbolic.benchmark_freeze.v1",
            "benchmark_version": "v1",
            "statement": phase2.SELECTION_STATEMENT,
            "task_count": 24,
            "condition_count": 6,
            "replicates": 3,
            "expected_episodes": 432,
            "files": [
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "bytes": len(path.read_bytes()),
                }
                for path in frozen_paths
            ],
        },
    )
    return {
        "spec": spec_path,
        "freeze": root / "frozen/freeze.json",
        "run_dir": run_dir,
        "output": root / "analysis",
    }


@pytest.fixture
def synthetic_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    paths = _make_project(tmp_path)

    monkeypatch.setattr(phase2, "load_symbol_map", lambda path: {"path": str(path)})
    monkeypatch.setattr(
        phase2,
        "replay_trace",
        lambda symbol_map, trace: {
            "ok": True,
            "rounds": len(trace["rounds"]),
            "outcome": trace["outcome"],
        },
    )
    monkeypatch.setattr(
        phase2, "_authenticate_trace_observations", lambda symbol_map, trace: trace
    )

    def evaluate(record, dag, trace):
        values = trace["synthetic_evaluation"]
        opportunities = values["handoff_opportunities"]
        clean = values["clean_handoffs"]
        return {
            "summary": {
                "dag_completion": values["dag_completion"],
                "dag_progress_auc": values["dag_progress_auc"],
                "handoff_opportunities": opportunities,
                "coordination_violations": values["coordination_violations"],
                "useful_hold_rounds": 0,
                "useful_waits": 0,
                "total_waits": 0,
                "useful_wait_ratio": None,
            },
            "handoffs": [
                {"clean": index < clean} for index in range(opportunities)
            ],
            "coordination_violations": [
                {"type": "synthetic"}
                for _ in range(values["coordination_violations"])
            ],
            "failure_analysis": None,
        }

    monkeypatch.setattr(phase2, "evaluate_collaboration_trace", evaluate)
    return paths


def _run(paths: dict[str, Path], **kwargs):
    return phase2.analyze_phase2(
        root=paths["spec"].parents[1],
        spec_path=paths["spec"],
        freeze_path=paths["freeze"],
        run_dir=paths["run_dir"],
        output_dir=paths["output"],
        **kwargs,
    )


def test_complete_matrix_emits_task_first_analysis_and_pdf_sources(
    synthetic_project: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr(phase2.shutil, "which", lambda executable: executable)

    def fake_run(command, *, cwd, capture_output, text, timeout, check):
        calls.append(list(command))
        (Path(cwd) / "phase2_report.pdf").write_bytes(b"%PDF-1.4\n% synthetic\n")
        return subprocess.CompletedProcess(command, 0, stdout="synthetic pdflatex", stderr="")

    monkeypatch.setattr(phase2.subprocess, "run", fake_run)
    synthetic_project["output"].mkdir(parents=True)
    (synthetic_project["output"] / "stale.txt").write_text("stale", encoding="utf-8")
    result = _run(synthetic_project, compile_pdf=True)

    assert result.publication_status == "final"
    assert result.gate_passed is True
    assert result.observed_episodes == 432
    assert len(calls) == 2
    assert all("-no-shell-escape" in command for command in calls)
    assert all(Path(command[0]).is_absolute() for command in calls)

    output = result.output_dir
    assert not (output / "stale.txt").exists()
    expected = {
        "per_episode.json",
        "per_episode.csv",
        "task_metrics.json",
        "task_metrics.csv",
        "paired_differences.json",
        "paired_differences.csv",
        "condition_summary.json",
        "condition_summary.csv",
        "headline_summary.json",
        "headline_summary.csv",
        "contrasts.json",
        "contrasts.csv",
        "run_integrity.json",
        "summary.json",
        "REPORT.md",
        "phase2_report.pdf",
    }
    assert expected <= {path.name for path in output.iterdir() if path.is_file()}

    condition_rows = json.loads((output / "condition_summary.json").read_text(encoding="utf-8"))["rows"]
    rounds = next(
        row
        for row in condition_rows
        if row["condition"] == "gpt_selfplay" and row["metric"] == "rounds_to_success"
    )
    assert rounds["estimate"] == 3.0
    assert rounds["ci_low"] == rounds["ci_high"] == 3.0
    assert rounds["aggregation"] == "median_of_task_medians"
    assert rounds["bootstrap_samples"] == 10_000

    handoff = next(
        row
        for row in condition_rows
        if row["condition"] == "gpt_selfplay" and row["metric"] == "clean_handoff_rate"
    )
    assert handoff["estimate"] is None
    assert handoff["bootstrap_samples"] == 0

    episodes = json.loads((output / "per_episode.json").read_text(encoding="utf-8"))["rows"]
    first = episodes[0]
    assert first["metrics"]["coordination_violation_rate"] == 1.0
    assert first["metrics"]["coordination_violation_rate_per_agent_action"] == 0.5
    assert first["watermark"] is None

    integrity = json.loads((output / "run_integrity.json").read_text(encoding="utf-8"))
    assert integrity["freeze"]["valid"] is True
    assert integrity["complete_paired_tasks"] == 24
    assert integrity["run_state"]["runner_errors"] == []
    assert integrity["pdf"]["status"] == "compiled"
    assert integrity["no_provider_calls"] is True
    assert len(integrity["strict_contract"]["condition_contract"]) == 6
    assert integrity["resolved_model_inventory"]["gpt_selfplay"] == {
        "F": ["gpt-5.5"],
        "W": ["gpt-5.5"],
    }
    tikz = (output / "latex/headline_figure.tex").read_text(encoding="utf-8")
    assert "\\begin{tikzpicture}" in tikz
    assert "Task-paired success-rate difference" in tikz


def test_bootstrap_is_deterministic_and_rounds_use_median() -> None:
    first = phase2.bootstrap_task_values(
        [1.0, 100.0, 3.0], metric="rounds_to_success", seed=73
    )
    second = phase2.bootstrap_task_values(
        [1.0, 100.0, 3.0], metric="rounds_to_success", seed=73
    )
    assert first == second
    assert first["estimate"] == 3.0
    assert first["bootstrap_samples"] == 10_000


def test_strict_gate_rejects_runner_error_before_writing_final_output(
    synthetic_project: dict[str, Path]
) -> None:
    state_path = synthetic_project["run_dir"] / "run_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["results"][0]["outcome"] = "runner_error"
    state["results"][0]["error"] = "SyntheticError: deliberate"
    state["outcomes"] = {"runner_error": 1, "team_success": 431}
    _dump(state_path, state)

    with pytest.raises(phase2.AnalysisError, match="runner_error"):
        _run(synthetic_project, compile_pdf=False)
    assert not synthetic_project["output"].exists()


def test_allow_incomplete_is_isolated_and_watermarked(
    synthetic_project: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = next((synthetic_project["run_dir"] / "traces").rglob("*.json"))
    missing.unlink()
    state_path = synthetic_project["run_dir"] / "run_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["results"][0]["outcome"] = "runner_error"
    state["results"][0]["error"] = "SyntheticError: deliberate"
    state["outcomes"] = {"runner_error": 1, "team_success": 431}
    _dump(state_path, state)

    def fast_bootstrap(values, *, metric, seed, samples=10_000):
        present = [float(value) for value in values if value is not None]
        estimate = None
        if present:
            estimate = median(present) if metric == "rounds_to_success" else fmean(present)
        return {
            "estimate": estimate,
            "ci_low": estimate,
            "ci_high": estimate,
            "bootstrap_samples": 10_000 if present else 0,
            "requested_bootstrap_samples": samples,
            "n_tasks": len(present),
            "seed": seed,
            "aggregation": (
                "median_of_task_medians"
                if metric == "rounds_to_success"
                else "mean_of_task_means"
            ),
            "analysis_algorithm_version": phase2.ANALYSIS_ALGORITHM_VERSION,
        }

    monkeypatch.setattr(phase2, "bootstrap_task_values", fast_bootstrap)
    result = _run(synthetic_project, allow_incomplete=True, compile_pdf=False)

    assert result.output_dir == synthetic_project["output"] / "draft_incomplete"
    assert result.publication_status == "draft_incomplete"
    assert result.gate_passed is False
    assert not (synthetic_project["output"] / "REPORT.md").exists()
    report = (result.output_dir / "REPORT.md").read_text(encoding="utf-8")
    assert report.startswith("> **DRAFT - INCOMPLETE/UNVERIFIED - NOT FOR PUBLICATION**")
    integrity = json.loads((result.output_dir / "run_integrity.json").read_text(encoding="utf-8"))
    assert integrity["watermark"] == phase2.DRAFT_WATERMARK
    assert integrity["run_state"]["runner_errors"]
    csv_header, csv_row = (result.output_dir / "per_episode.csv").read_text(
        encoding="utf-8"
    ).splitlines()[:2]
    assert "watermark" in csv_header
    assert "NOT FOR PUBLICATION" in csv_row
    tex = (result.output_dir / "latex/phase2_report.tex").read_text(encoding="utf-8")
    assert "DRAFT -- NOT FOR PUBLICATION" in tex


def test_freeze_mismatch_is_never_bypassed_by_allow_incomplete(
    synthetic_project: dict[str, Path]
) -> None:
    synthetic_project["spec"].write_text("{}\n", encoding="utf-8")
    with pytest.raises(phase2.AnalysisError, match="freeze mismatch"):
        _run(synthetic_project, allow_incomplete=True, compile_pdf=False)
    assert not synthetic_project["output"].exists()


def _fixture_trace(
    paths: dict[str, Path], condition_id: str = "gpt_no_comm"
) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    spec = json.loads(paths["spec"].read_text(encoding="utf-8"))
    manifest = json.loads(
        (paths["spec"].parent / "manifest.json").read_text(encoding="utf-8")
    )
    condition = next(item for item in spec["conditions"] if item["id"] == condition_id)
    record = manifest["records"][0]
    trace_path = paths["run_dir"] / phase2._trace_relative(condition_id, 1, record)
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    return spec, condition, record, trace


def test_trace_counter_and_no_comm_drift_are_rejected(
    synthetic_project: dict[str, Path],
) -> None:
    spec, condition, record, trace = _fixture_trace(synthetic_project)
    trace["metrics"]["total_model_calls"] += 1
    errors = phase2._trace_metadata_errors(
        trace, condition=condition, record=record, replicate=1, spec=spec
    )
    assert any("recomputed" in error for error in errors)

    trace["metrics"]["total_model_calls"] -= 1
    trace["rounds"][0]["agents"]["F"]["decision"]["message"] = "leak"
    trace["metrics"]["total_messages"] = 1
    errors = phase2._trace_metadata_errors(
        trace, condition=condition, record=record, replicate=1, spec=spec
    )
    assert any("No-Comm" in error and "emitted" in error for error in errors)


def test_timeout_must_exhaust_exact_round_cap(
    synthetic_project: dict[str, Path],
) -> None:
    spec, condition, record, trace = _fixture_trace(
        synthetic_project, "gpt_selfplay"
    )
    trace["outcome"] = "timeout"
    errors = phase2._trace_metadata_errors(
        trace, condition=condition, record=record, replicate=1, spec=spec
    )
    assert any(
        "timeout trace must contain exactly 80 rounds" in error for error in errors
    )


def test_observation_authentication_rejects_stored_payload_drift() -> None:
    symbol_map = parse_symbol_map(SIMPLE_MAP)
    world = SymbolWorld(symbol_map)
    before = world.observation()
    decision = {"action": {"move": "WAIT", "steps": 0}, "message": None}
    transition = world.step_joint(
        {
            "F": SymbolAction(move="WAIT", steps=0),
            "W": SymbolAction(move="WAIT", steps=0),
        }
    )
    trace = {
        "rounds": [
            {
                "observation": before,
                "agents": {
                    "F": {"decision": decision},
                    "W": {"decision": decision},
                },
                "result": {
                    "status": transition.status,
                    "feedback": dict(transition.feedback),
                    "observation": transition.observation,
                },
            }
        ],
        "final_observation": world.observation(),
    }
    authenticated = phase2._authenticate_trace_observations(symbol_map, trace)
    assert authenticated is not trace
    assert authenticated["rounds"][0]["observation"] == before

    tampered = copy.deepcopy(trace)
    tampered["rounds"][0]["observation"]["status"] = "tampered"
    with pytest.raises(phase2.AnalysisError, match="pre-observation mismatch"):
        phase2._authenticate_trace_observations(symbol_map, tampered)


def test_paired_differences_use_common_replicates_and_metric_polarity() -> None:
    rows = []
    for condition, replicate, rounds in (
        ("gpt_selfplay", 1, 10.0),
        ("gpt_selfplay", 2, 20.0),
        ("gpt_selfplay", 3, 30.0),
        ("gpt_no_comm", 1, 30.0),
        ("gpt_no_comm", 2, 40.0),
    ):
        metrics = {metric: 0.5 for metric in phase2.METRICS}
        metrics["rounds_to_success"] = rounds
        rows.append(
            {
                "condition": condition,
                "task_id": "T1",
                "replicate": replicate,
                "metrics": metrics,
            }
        )
    paired = phase2._paired_differences(
        rows, [{"id": "T1", "difficulty": "L1"}]
    )
    rounds_row = next(
        row
        for row in paired
        if row["contrast"] == "gpt_communication_gain"
        and row["metric"] == "rounds_to_success"
    )
    assert rounds_row["common_replicates"] == [1, 2]
    assert rounds_row["left_value"] == 15.0
    assert rounds_row["right_value"] == 35.0
    assert rounds_row["difference"] == 20.0
    assert rounds_row["direction"] == "right_minus_left"


def test_direct_crossplay_role_assignment_contrast_is_paired_and_exploratory() -> None:
    rows = []
    for condition, replicate, score, rounds in (
        ("crossplay_gptF_geminiW", 1, 0.7, 10.0),
        ("crossplay_gptF_geminiW", 2, 0.9, 20.0),
        ("crossplay_gptF_geminiW", 3, 0.1, 30.0),
        ("crossplay_geminiF_gptW", 1, 0.5, 30.0),
        ("crossplay_geminiF_gptW", 2, 0.6, 40.0),
    ):
        metrics = {metric: score for metric in phase2.METRICS}
        metrics["rounds_to_success"] = rounds
        rows.append(
            {
                "condition": condition,
                "task_id": "T1",
                "replicate": replicate,
                "metrics": metrics,
            }
        )

    paired = phase2._paired_differences(
        rows, [{"id": "T1", "difficulty": "L1"}]
    )
    direct = [
        row
        for row in paired
        if row["contrast"] == "crossplay_role_assignment_gap"
    ]

    assert len(direct) == len(phase2.METRICS)
    assert {row["analysis_status"] for row in direct} == {
        "exploratory_post_hoc"
    }
    success = next(row for row in direct if row["metric"] == "success_rate")
    assert success["common_replicates"] == [1, 2]
    assert success["left_value"] == pytest.approx(0.8)
    assert success["right_value"] == pytest.approx(0.55)
    assert success["difference"] == pytest.approx(0.25)
    assert success["direction"] == "left_minus_right"
    rounds = next(row for row in direct if row["metric"] == "rounds_to_success")
    assert rounds["difference"] == 20.0
    assert rounds["direction"] == "right_minus_left"

    summaries = phase2._contrast_summaries(paired)
    direct_summaries = [
        row
        for row in summaries
        if row["contrast"] == "crossplay_role_assignment_gap"
    ]
    assert len(direct_summaries) == len(phase2.METRICS)
    assert {row["analysis_status"] for row in direct_summaries} == {
        "exploratory_post_hoc"
    }
    assert all(row["requested_bootstrap_samples"] == 10_000 for row in direct_summaries)
    assert all(row["n_tasks"] == 1 for row in direct_summaries)


def test_latex_report_describes_metric_direction_consistently() -> None:
    source = phase2._latex_report_source(draft=False)
    assert "positive values favor its left expression" in source
    assert "Higher-is-better metrics use" in source
    assert "rounds-to-success use right minus left" in source
    assert "For other contrasts, estimates are left minus right" not in source


def test_bootstrap_is_order_invariant_and_rejects_nonfinite() -> None:
    forward = phase2.bootstrap_task_values(
        [0.1, 0.9, 0.2, 0.7], metric="success_rate", seed=91
    )
    reverse = phase2.bootstrap_task_values(
        [0.7, 0.2, 0.9, 0.1], metric="success_rate", seed=91
    )
    assert forward == reverse
    with pytest.raises(phase2.AnalysisError, match="finite"):
        phase2.bootstrap_task_values(
            [0.1, math.nan], metric="success_rate", seed=91
        )


def test_duplicate_estimands_share_identity() -> None:
    directional = next(
        contrast
        for contrast in phase2.CONTRASTS
        if contrast.name == "crossplay_directional_gap_A"
    )
    partner = next(
        contrast
        for contrast in phase2.CONTRASTS
        if contrast.name == "partner_sensitivity_gpt_F"
    )
    for metric in phase2.METRICS:
        assert phase2._estimand_id(directional, metric) == phase2._estimand_id(
            partner, metric
        )


def test_empty_episode_csv_has_explicit_schema(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    phase2._write_csv(path, [], empty_fields=phase2.EPISODE_CSV_FIELDS)
    assert path.read_text(encoding="utf-8").splitlines()[0].startswith(
        "publication_status,watermark,condition"
    )


def test_output_may_not_overlap_run_inputs(
    synthetic_project: dict[str, Path],
) -> None:
    with pytest.raises(
        phase2.AnalysisError, match="overlaps immutable analysis input"
    ):
        phase2.analyze_phase2(
            root=synthetic_project["spec"].parents[1],
            spec_path=synthetic_project["spec"],
            freeze_path=synthetic_project["freeze"],
            run_dir=synthetic_project["run_dir"],
            output_dir=synthetic_project["run_dir"],
            compile_pdf=False,
        )


def test_observation_authentication_rejects_post_and_final_drift() -> None:
    symbol_map = parse_symbol_map(SIMPLE_MAP)
    world = SymbolWorld(symbol_map)
    before = world.observation()
    decision = {"action": {"move": "WAIT", "steps": 0}, "message": None}
    transition = world.step_joint(
        {
            "F": SymbolAction(move="WAIT", steps=0),
            "W": SymbolAction(move="WAIT", steps=0),
        }
    )
    trace = {
        "rounds": [
            {
                "observation": before,
                "agents": {
                    "F": {"decision": decision},
                    "W": {"decision": decision},
                },
                "result": {
                    "status": transition.status,
                    "feedback": dict(transition.feedback),
                    "observation": transition.observation,
                },
            }
        ],
        "final_observation": world.observation(),
    }

    post_tampered = copy.deepcopy(trace)
    post_tampered["rounds"][0]["result"]["observation"]["round"] = 99
    with pytest.raises(phase2.AnalysisError, match="post-observation mismatch"):
        phase2._authenticate_trace_observations(symbol_map, post_tampered)

    final_tampered = copy.deepcopy(trace)
    final_tampered["final_observation"]["round"] = 99
    with pytest.raises(phase2.AnalysisError, match="final_observation mismatch"):
        phase2._authenticate_trace_observations(symbol_map, final_tampered)


def test_collect_episodes_passes_authenticated_copy_to_evaluator(
    synthetic_project: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = json.loads(synthetic_project["spec"].read_text(encoding="utf-8"))
    manifest = json.loads(
        (synthetic_project["spec"].parent / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    seen = 0

    def authenticate(symbol_map, trace):
        authenticated = copy.deepcopy(trace)
        authenticated["authenticated_test_marker"] = True
        return authenticated

    def evaluate(record, dag, trace):
        nonlocal seen
        assert trace["authenticated_test_marker"] is True
        seen += 1
        values = trace["synthetic_evaluation"]
        return {
            "summary": {
                "dag_completion": values["dag_completion"],
                "dag_progress_auc": values["dag_progress_auc"],
                "handoff_opportunities": 0,
                "coordination_violations": values["coordination_violations"],
                "useful_hold_rounds": 0,
                "useful_waits": 0,
                "total_waits": 0,
                "useful_wait_ratio": None,
            },
            "handoffs": [],
            "coordination_violations": [
                {"type": "synthetic"}
                for _ in range(values["coordination_violations"])
            ],
            "failure_analysis": None,
        }

    monkeypatch.setattr(phase2, "_authenticate_trace_observations", authenticate)
    monkeypatch.setattr(phase2, "evaluate_collaboration_trace", evaluate)
    rows, details = phase2._collect_episodes(
        root=synthetic_project["spec"].parents[1],
        run_dir=synthetic_project["run_dir"],
        spec=spec,
        records=manifest["records"],
        conditions=spec["conditions"],
    )
    assert seen == 432
    assert len(rows) == details["valid_traces"] == 432


def test_run_state_rejects_bool_replicate_and_row_linkage_drift(
    synthetic_project: dict[str, Path],
) -> None:
    spec = json.loads(synthetic_project["spec"].read_text(encoding="utf-8"))
    manifest = json.loads(
        (synthetic_project["spec"].parent / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    matrix = phase2._expected_matrix(manifest["records"], spec["conditions"])
    state_path = synthetic_project["run_dir"] / "run_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["results"][0]["replicate"] = True
    _dump(state_path, state)
    result = phase2._inspect_run_state(
        root=synthetic_project["spec"].parents[1],
        run_dir=synthetic_project["run_dir"],
        spec_path=synthetic_project["spec"],
        matrix=matrix,
        trace_rows=[],
    )
    assert any("invalid key" in error for error in result["errors"])

    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["results"][0]["replicate"] = 1
    state["results"][0]["difficulty"] = "L99"
    state["results"][0]["trace"] = "runs/traces/wrong.json"
    state["results"][0]["model_calls"] += 1
    _dump(state_path, state)
    result = phase2._inspect_run_state(
        root=synthetic_project["spec"].parents[1],
        run_dir=synthetic_project["run_dir"],
        spec_path=synthetic_project["spec"],
        matrix=matrix,
        trace_rows=[],
    )
    assert any("wrong difficulty" in error for error in result["errors"])
    assert any("wrong trace path" in error for error in result["errors"])
    assert any("total_model_calls" in error for error in result["errors"])


def test_malformed_condition_id_and_task_manifest_fail_cleanly(
    synthetic_project: dict[str, Path],
) -> None:
    spec = json.loads(synthetic_project["spec"].read_text(encoding="utf-8"))
    manifest = json.loads(
        (synthetic_project["spec"].parent / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    spec["conditions"][0]["id"] = []
    with pytest.raises(phase2.AnalysisError, match="invalid frozen Phase II contract"):
        phase2._validate_contract(
            spec,
            manifest,
            {
                "raw": {
                    "task_count": 24,
                    "condition_count": 6,
                    "replicates": 3,
                    "expected_episodes": 432,
                }
            },
        )

    spec = json.loads(synthetic_project["spec"].read_text(encoding="utf-8"))
    spec["task_manifest"] = {"not": "a path"}
    _dump(synthetic_project["spec"], spec)
    freeze = json.loads(synthetic_project["freeze"].read_text(encoding="utf-8"))
    spec_entry = next(
        item
        for item in freeze["files"]
        if item["path"] == synthetic_project["spec"].relative_to(
            synthetic_project["spec"].parents[1]
        ).as_posix()
    )
    content = synthetic_project["spec"].read_bytes()
    spec_entry["sha256"] = hashlib.sha256(content).hexdigest()
    spec_entry["bytes"] = len(content)
    _dump(synthetic_project["freeze"], freeze)
    with pytest.raises(phase2.AnalysisError, match="task_manifest"):
        _run(synthetic_project, compile_pdf=False)


def test_resolved_model_must_match_frozen_request(
    synthetic_project: dict[str, Path],
) -> None:
    spec, condition, record, trace = _fixture_trace(
        synthetic_project, "gpt_selfplay"
    )
    trace["rounds"][0]["agents"]["F"]["model"] = "unregistered-model"
    errors = phase2._trace_metadata_errors(
        trace, condition=condition, record=record, replicate=1, spec=spec
    )
    assert any("does not match requested model" in error for error in errors)

    trace["rounds"][0]["agents"]["F"]["model"] = "gpt-5.5-2026-09-15"
    errors = phase2._trace_metadata_errors(
        trace, condition=condition, record=record, replicate=1, spec=spec
    )
    assert not any("resolved model" in error for error in errors)


def test_freeze_rejects_invalid_bytes_and_canonical_duplicates(
    synthetic_project: dict[str, Path],
) -> None:
    freeze = json.loads(synthetic_project["freeze"].read_text(encoding="utf-8"))
    freeze["files"][0]["bytes"] = True
    _dump(synthetic_project["freeze"], freeze)
    with pytest.raises(phase2.AnalysisError, match="freeze mismatch"):
        phase2.verify_freeze(
            synthetic_project["spec"].parents[1], synthetic_project["freeze"]
        )

    freeze = json.loads(synthetic_project["freeze"].read_text(encoding="utf-8"))
    freeze["files"][0]["bytes"] = len(synthetic_project["spec"].read_bytes())
    duplicate = dict(freeze["files"][0])
    duplicate["path"] = "frozen/../frozen/spec.json"
    freeze["files"].append(duplicate)
    _dump(synthetic_project["freeze"], freeze)
    with pytest.raises(phase2.AnalysisError, match="freeze mismatch"):
        phase2.verify_freeze(
            synthetic_project["spec"].parents[1], synthetic_project["freeze"]
        )


def test_atomic_publication_restores_previous_output_on_swap_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = tmp_path / ".analysis.staging"
    destination = tmp_path / "analysis"
    staging.mkdir()
    destination.mkdir()
    (staging / "new.txt").write_text("new", encoding="utf-8")
    (destination / "old.txt").write_text("old", encoding="utf-8")
    real_replace = phase2.os.replace
    calls = 0

    def fail_new_swap(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic swap failure")
        return real_replace(source, target)

    monkeypatch.setattr(phase2.os, "replace", fail_new_swap)
    with pytest.raises(phase2.AnalysisError, match="atomically publish"):
        phase2._publish_staged_directory(staging, destination)
    assert (destination / "old.txt").read_text(encoding="utf-8") == "old"
    assert not (destination / "new.txt").exists()


def test_backup_cleanup_error_states_that_new_output_was_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = tmp_path / ".analysis.staging"
    destination = tmp_path / "analysis"
    staging.mkdir()
    destination.mkdir()
    (staging / "new.txt").write_text("new", encoding="utf-8")
    (destination / "old.txt").write_text("old", encoding="utf-8")

    def fail_cleanup(path):
        raise OSError("synthetic cleanup failure")

    monkeypatch.setattr(phase2.shutil, "rmtree", fail_cleanup)
    with pytest.raises(phase2.AnalysisError, match="new analysis output was published"):
        phase2._publish_staged_directory(staging, destination)
    assert (destination / "new.txt").read_text(encoding="utf-8") == "new"


def test_failed_generation_preserves_old_output_and_removes_staging(
    synthetic_project: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    output = synthetic_project["output"]
    output.mkdir(parents=True)
    (output / "sentinel.txt").write_text("old", encoding="utf-8")

    def fail_write(*args, **kwargs):
        raise phase2.AnalysisError("synthetic generation failure")

    monkeypatch.setattr(phase2, "_write_csv", fail_write)
    with pytest.raises(phase2.AnalysisError, match="synthetic generation failure"):
        _run(synthetic_project, compile_pdf=False)
    assert (output / "sentinel.txt").read_text(encoding="utf-8") == "old"
    assert not list(output.parent.glob(f".{output.name}.staging-*"))


def test_trace_enumeration_errors_are_normalized(
    synthetic_project: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = json.loads(synthetic_project["spec"].read_text(encoding="utf-8"))
    manifest = json.loads(
        (synthetic_project["spec"].parent / "manifest.json").read_text(
            encoding="utf-8"
        )
    )

    def fail_rglob(self, pattern):
        raise OSError("synthetic enumeration failure")

    monkeypatch.setattr(Path, "rglob", fail_rglob)
    with pytest.raises(phase2.AnalysisError, match="cannot enumerate trace directory"):
        phase2._collect_episodes(
            root=synthetic_project["spec"].parents[1],
            run_dir=synthetic_project["run_dir"],
            spec=spec,
            records=manifest["records"],
            conditions=spec["conditions"],
        )


def test_no_comm_rejects_inbox_and_shared_history_leakage(
    synthetic_project: dict[str, Path],
) -> None:
    spec, condition, record, trace = _fixture_trace(synthetic_project)
    trace["rounds"][0]["agents"]["W"]["inbox"] = [{"text": "leak"}]
    trace["rounds"][0]["agent_observation"]["shared_message_history"] = [
        {"text": "leak"}
    ]
    errors = phase2._trace_metadata_errors(
        trace, condition=condition, record=record, replicate=1, spec=spec
    )
    assert any("inbox is not empty" in error for error in errors)
    assert any("exposes shared messages" in error for error in errors)


@pytest.mark.parametrize("invalid", [True, 1.5, "2", -1])
def test_trace_counter_types_are_strict(
    synthetic_project: dict[str, Path], invalid: object
) -> None:
    spec, condition, record, trace = _fixture_trace(
        synthetic_project, "gpt_selfplay"
    )
    trace["metrics"]["total_model_calls"] = invalid
    errors = phase2._trace_metadata_errors(
        trace, condition=condition, record=record, replicate=1, spec=spec
    )
    assert any(
        "metrics.total_model_calls must be a nonnegative integer" in error
        for error in errors
    )


@pytest.mark.parametrize("invalid", [True, 1.5, "1", -1])
def test_round_provider_call_types_are_strict(
    synthetic_project: dict[str, Path], invalid: object
) -> None:
    spec, condition, record, trace = _fixture_trace(
        synthetic_project, "gpt_selfplay"
    )
    trace["rounds"][0]["agents"]["F"]["provider_calls"] = invalid
    errors = phase2._trace_metadata_errors(
        trace, condition=condition, record=record, replicate=1, spec=spec
    )
    assert any("provider_calls must be a nonnegative integer" in error for error in errors)


def test_observation_authentication_rejects_post_terminal_round() -> None:
    symbol_map = parse_symbol_map(
        """@format fwcollab.symbol_map.v1
@id one-step
@title One step
---
#####
#Ff##
#Ww##
#####
"""
    )
    world = SymbolWorld(symbol_map)
    first_before = world.observation()
    first_transition = world.step_joint(
        {
            "F": SymbolAction(move="RIGHT", steps=1),
            "W": SymbolAction(move="RIGHT", steps=1),
        }
    )
    assert first_transition.status == "team_success"
    move = {"action": {"move": "RIGHT", "steps": 1}, "message": None}
    wait = {"action": {"move": "WAIT", "steps": 0}, "message": None}
    trace = {
        "rounds": [
            {
                "observation": first_before,
                "agents": {"F": {"decision": move}, "W": {"decision": move}},
                "result": {
                    "status": first_transition.status,
                    "feedback": dict(first_transition.feedback),
                    "observation": first_transition.observation,
                },
            },
            {
                "observation": world.observation(),
                "agents": {"F": {"decision": wait}, "W": {"decision": wait}},
                "result": {
                    "status": "team_success",
                    "feedback": {"F": "terminal", "W": "terminal"},
                    "observation": world.observation(),
                },
            },
        ],
        "final_observation": world.observation(),
    }
    with pytest.raises(phase2.AnalysisError, match="post-terminal round 2"):
        phase2._authenticate_trace_observations(symbol_map, trace)


def test_frozen_contract_rejects_condition_and_statement_drift(
    synthetic_project: dict[str, Path],
) -> None:
    pristine = json.loads(synthetic_project["spec"].read_text(encoding="utf-8"))
    manifest = json.loads(
        (synthetic_project["spec"].parent / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    freeze = {
        "raw": {
            "task_count": 24,
            "condition_count": 6,
            "replicates": 3,
            "expected_episodes": 432,
        }
    }
    variants = []
    wrong_model = copy.deepcopy(pristine)
    wrong_model["conditions"][0]["F"]["model"] = "other-model"
    variants.append(wrong_model)
    wrong_api = copy.deepcopy(pristine)
    wrong_api["conditions"][0]["F"]["api_style"] = "chat_completions"
    variants.append(wrong_api)
    wrong_communication = copy.deepcopy(pristine)
    wrong_communication["conditions"][0]["communication_enabled"] = False
    variants.append(wrong_communication)
    wrong_statement = copy.deepcopy(pristine)
    wrong_statement["statement"] = "performance selected"
    variants.append(wrong_statement)

    for spec in variants:
        with pytest.raises(
            phase2.AnalysisError, match="invalid frozen Phase II contract"
        ):
            phase2._validate_contract(spec, manifest, freeze)


def test_frozen_contract_accepts_legacy_spec_without_duplicate_statement(
    synthetic_project: dict[str, Path],
) -> None:
    spec = json.loads(synthetic_project["spec"].read_text(encoding="utf-8"))
    spec.pop("statement")
    manifest = json.loads(
        (synthetic_project["spec"].parent / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    freeze = {
        "raw": {
            "task_count": 24,
            "condition_count": 6,
            "replicates": 3,
            "expected_episodes": 432,
        }
    }

    records, conditions = phase2._validate_contract(spec, manifest, freeze)

    assert len(records) == 24
    assert len(conditions) == 6


def test_alias_estimands_share_seed_and_interval() -> None:
    alias_names = {
        "crossplay_directional_gap_A",
        "partner_sensitivity_gpt_F",
    }
    paired = [
        {
            "contrast": contrast,
            "metric": "success_rate",
            "difference": value,
            "direction": "left_minus_right",
        }
        for contrast in alias_names
        for value in (0.1, 0.2, 0.4)
    ]
    summaries = phase2._contrast_summaries(paired)
    aliases = [
        row
        for row in summaries
        if row["contrast"] in alias_names and row["metric"] == "success_rate"
    ]
    assert len(aliases) == 2
    assert aliases[0]["estimand_id"] == aliases[1]["estimand_id"]
    assert aliases[0]["seed"] == aliases[1]["seed"]
    assert aliases[0]["estimate"] == aliases[1]["estimate"]
    assert aliases[0]["ci_low"] == aliases[1]["ci_low"]
    assert aliases[0]["ci_high"] == aliases[1]["ci_high"]
