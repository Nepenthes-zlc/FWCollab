"""Integrity-check and analyze the frozen C5 communication experiment."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from fwcollab.c5 import evaluate_c5_trace, project_role_observation
from fwcollab.symbolic.dag import load_json, save_json
from fwcollab.symbolic.map import load_symbol_map
from fwcollab.symbolic.runner import replay_trace


METRICS = (
    "success_rate",
    "dag_completion",
    "dag_progress_auc",
    "clean_information_handoff_rate",
    "dependency_violation_rate",
    "rounds_to_success",
)
CONTRASTS = (
    ("GPT Comm - NoComm", "gpt_comm", "gpt_no_comm"),
    ("Gemini Comm - NoComm", "gemini_comm", "gemini_no_comm"),
)


def _verify_freeze(root: Path, freeze_path: Path) -> dict[str, Any]:
    freeze = load_json(freeze_path)
    mismatches = []
    for item in freeze["files"]:
        path = root / item["path"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
        if digest != item["sha256"]:
            mismatches.append(item["path"])
    if mismatches:
        raise SystemExit(f"C5 freeze mismatch: {mismatches}")
    return freeze


def _same_json(left: object, right: object) -> bool:
    return json.dumps(left, sort_keys=True, separators=(",", ":")) == json.dumps(right, sort_keys=True, separators=(",", ":"))


def _bootstrap(values: list[float], statistic: Callable[[list[float]], float], *, seed: int, samples: int = 10_000) -> tuple[float, float]:
    if not values:
        return float("nan"), float("nan")
    rng = random.Random(seed)
    draws = []
    for _ in range(samples):
        sample = [values[rng.randrange(len(values))] for _ in values]
        draws.append(statistic(sample))
    draws.sort()
    return draws[int(samples * 0.025)], draws[min(samples - 1, int(samples * 0.975))]


def _dz(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    sd = statistics.stdev(values)
    return statistics.fmean(values) / sd if sd else None


def _episode_metrics(trace: dict[str, Any], evaluation: dict[str, Any]) -> dict[str, float | None]:
    success = trace["outcome"] == "team_success"
    return {
        "success_rate": float(success),
        "dag_completion": float(evaluation["dag_completion"]),
        "dag_progress_auc": float(evaluation["dag_progress_auc"]),
        "clean_information_handoff_rate": float(evaluation["clean_information_handoff"]),
        "dependency_violation_rate": float(
            evaluation["dependency_violation"] or evaluation["wrong_controller_before_answer"]
        ),
        "rounds_to_success": float(trace["metrics"]["rounds"]) if success else None,
    }


def _fmt(value: float | None, *, percent: bool = False) -> str:
    if value is None:
        return "NA"
    return f"{value * 100:.1f}%" if percent else f"{value:.3f}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--suite", default="eval_private/c5_information_12")
    parser.add_argument("--runs", default="artifacts/runs/c5_information_v1")
    parser.add_argument("--output", default="artifacts/evaluations/c5_information_v1")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    suite = root / args.suite
    runs = root / args.runs
    output = root / args.output
    output.mkdir(parents=True, exist_ok=True)

    freeze = _verify_freeze(root, suite / "freeze_manifest.json")
    manifest = load_json(suite / "manifest.json")
    spec = load_json(suite / "experiment_spec.json")
    tasks = {task["task_id"]: task for task in manifest["tasks"]}
    conditions = {item["id"]: item for item in spec["conditions"]}
    expected = {
        (condition, replicate, task_id)
        for condition in conditions
        for replicate in spec["replicates"]
        for task_id in tasks
    }
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[tuple[str, int, str]] = set()
    for condition_id, replicate, task_id in sorted(expected):
        key = (condition_id, replicate, task_id)
        path = runs / "traces" / condition_id / f"replicate_{replicate}" / f"{task_id}.json"
        if not path.is_file():
            errors.append(f"missing trace {path.relative_to(root)}")
            continue
        trace = load_json(path)
        task = tasks[task_id]
        condition = conditions[condition_id]
        metadata = trace.get("c5", {})
        if not isinstance(metadata, dict) or metadata.get("condition") != condition_id or metadata.get("replicate") != replicate:
            errors.append(f"metadata mismatch {key}")
            continue
        if metadata.get("communication_enabled") is not condition["communication_enabled"]:
            errors.append(f"communication flag mismatch {key}")
        symbol_map = load_symbol_map(suite / task["map"])
        try:
            replay_trace(symbol_map, trace)
        except Exception as exc:
            errors.append(f"replay failed {key}: {exc}")
            continue
        for round_index, record in enumerate(trace.get("rounds", []), start=1):
            internal = record.get("runner_internal_preprojection")
            private = record.get("role_agent_observations")
            if not isinstance(internal, dict) or not isinstance(private, dict):
                errors.append(f"missing private observation audit {key} round {round_index}")
                break
            for role in ("F", "W"):
                expected_view = project_role_observation(internal, task, role)
                if not _same_json(private.get(role), expected_view):
                    errors.append(f"private observation mismatch {key} round {round_index} role {role}")
            if not condition["communication_enabled"]:
                agents = record.get("agents", {})
                for role in ("F", "W"):
                    agent = agents.get(role, {}) if isinstance(agents, dict) else {}
                    decision = agent.get("decision", {}) if isinstance(agent, dict) else {}
                    if isinstance(decision, dict) and decision.get("message") is not None:
                        errors.append(f"NoComm emitted message {key} round {round_index} role {role}")
                    if isinstance(agent, dict) and agent.get("inbox"):
                        errors.append(f"NoComm received message {key} round {round_index} role {role}")
        evaluation = evaluate_c5_trace(task, trace)
        if not _same_json(evaluation, trace.get("c5_evaluation")):
            errors.append(f"stored evaluator mismatch {key}")
            continue
        metrics = _episode_metrics(trace, evaluation)
        rows.append({
            "condition": condition_id,
            "replicate": replicate,
            "task_id": task_id,
            "outcome": trace["outcome"],
            "model_errors": trace["metrics"]["total_model_errors"],
            **metrics,
        })
        seen.add(key)

    gate = {
        "format": "fwcollab.c5_integrity.v1",
        "passed": not errors and seen == expected,
        "freeze_verified": True,
        "freeze_created_at": freeze["created_at"],
        "expected_episodes": len(expected),
        "valid_episodes": len(seen),
        "authoritative_replay_verified": not any("replay failed" in error for error in errors),
        "private_observations_recomputed": not any("private observation" in error for error in errors),
        "no_comm_channel_verified": not any("NoComm" in error for error in errors),
        "evaluator_recomputed": not any("evaluator mismatch" in error for error in errors),
        "errors": errors,
    }
    save_json(output / "run_integrity.json", gate)
    if not gate["passed"]:
        raise SystemExit(f"C5 analysis gate failed with {len(errors)} errors")

    with (output / "episodes.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    by_condition_task: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_condition_task[(row["condition"], row["task_id"])].append(row)

    task_means: dict[tuple[str, str, str], float | None] = {}
    for (condition, task_id), items in by_condition_task.items():
        for metric in METRICS:
            values = [float(item[metric]) for item in items if item[metric] is not None]
            task_means[(condition, task_id, metric)] = statistics.fmean(values) if values else None

    summaries = []
    for condition_id in conditions:
        for metric_index, metric in enumerate(METRICS):
            values = [task_means[(condition_id, task_id, metric)] for task_id in tasks]
            numeric = [float(value) for value in values if value is not None]
            mean = statistics.fmean(numeric) if numeric else None
            low, high = _bootstrap(numeric, statistics.fmean, seed=1600 + metric_index) if numeric else (None, None)
            summaries.append({"condition": condition_id, "metric": metric, "mean": mean, "ci_low": low, "ci_high": high, "task_n": len(numeric)})

    paired = []
    for contrast_index, (label, comm, no_comm) in enumerate(CONTRASTS):
        for metric_index, metric in enumerate(METRICS):
            differences = []
            task_ids = []
            for task_id in tasks:
                left = task_means[(comm, task_id, metric)]
                right = task_means[(no_comm, task_id, metric)]
                if left is not None and right is not None:
                    differences.append(float(left) - float(right))
                    task_ids.append(task_id)
            mean = statistics.fmean(differences) if differences else None
            low, high = _bootstrap(differences, statistics.fmean, seed=2600 + contrast_index * 100 + metric_index) if differences else (None, None)
            paired.append({
                "contrast": label, "comm": comm, "no_comm": no_comm, "metric": metric,
                "difference_comm_minus_no_comm": mean, "ci_low": low, "ci_high": high,
                "paired_dz": _dz(differences), "task_n": len(differences),
                "tasks_positive": sum(value > 0 for value in differences),
                "tasks_zero": sum(value == 0 for value in differences),
                "tasks_negative": sum(value < 0 for value in differences),
                "paired_tasks": task_ids,
            })

    result = {
        "format": "fwcollab.c5_statistics.v1",
        "analysis_unit": "task; three replicates averaged within each task before paired inference",
        "bootstrap_samples": 10_000,
        "confidence_level": 0.95,
        "integrity": gate,
        "condition_summaries": summaries,
        "paired_contrasts": paired,
    }
    save_json(output / "summary.json", result)
    with (output / "paired_contrasts.csv").open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [key for key in paired[0] if key != "paired_tasks"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({key: value for key, value in row.items() if key in fieldnames} for row in paired)

    summary_lookup = {(row["condition"], row["metric"]): row for row in summaries}
    lines = [
        "# C5 causal communication results",
        "",
        "Integrity gate: **passed**. All 144 traces were replayed; frozen hashes, role-private DTOs, NoComm channel isolation and stored evaluator outputs were independently verified.",
        "",
        "## Condition summaries",
        "",
        "| Condition | SR | DAG completion | Progress AUC | Clean handoff | Violation | Rounds-to-success |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for condition_id in conditions:
        values = [summary_lookup[(condition_id, metric)]["mean"] for metric in METRICS]
        lines.append(
            f"| {conditions[condition_id]['label']} | {_fmt(values[0], percent=True)} | {_fmt(values[1], percent=True)} | "
            f"{_fmt(values[2], percent=True)} | {_fmt(values[3], percent=True)} | {_fmt(values[4], percent=True)} | {_fmt(values[5])} |"
        )
    lines += ["", "## Paired task-level effects", "", "Positive values are `Comm - NoComm`. For violation and rounds, negative is favorable to communication.", "",
              "| Contrast | Metric | Difference | 95% CI | dz | Paired tasks |", "|---|---|---:|---:|---:|---:|"]
    percent_metrics = set(METRICS[:-1])
    for row in paired:
        percent = row["metric"] in percent_metrics
        lines.append(
            f"| {row['contrast']} | {row['metric']} | {_fmt(row['difference_comm_minus_no_comm'], percent=percent)} | "
            f"[{_fmt(row['ci_low'], percent=percent)}, {_fmt(row['ci_high'], percent=percent)}] | "
            f"{_fmt(row['paired_dz'])} | {row['task_n']} |"
        )
    lines += [
        "",
        "Rounds-to-success is conditional: only tasks with at least one successful seed in both paired conditions enter that contrast.",
        "A confidence interval containing zero is reported as inconclusive, not evidence of equivalence.",
    ]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"integrity": "passed", "episodes": len(rows), "output": str(output), "paired_rows": len(paired)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
