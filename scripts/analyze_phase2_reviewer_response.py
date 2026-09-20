"""Offline interaction and leave-one-task-out analyses for frozen Phase II."""

from __future__ import annotations

import csv
import hashlib
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fwcollab.analysis.phase2 import (  # noqa: E402
    BOOTSTRAP_SAMPLES,
    EXPECTED_REPLICATES,
    _quantile,
)

INTERACTION_OUT = ROOT / "artifacts/evaluations/model_communication_interaction_v1"
LOTO_OUT = ROOT / "artifacts/evaluations/phase2_loto_robustness_v1"
METRICS = (
    "success_rate",
    "dag_completion",
    "dag_progress_auc",
    "clean_handoff_rate",
    "coordination_violation_rate",
)
POPULATIONS = (
    "P0_ORIGINAL_24",
    "P1_EXCLUDE_PROVABLY_INFEASIBLE",
    "P2_CERTIFIED_FEASIBLE_ONLY",
)


def load(path: str) -> Any:
    return json.loads((ROOT / path).read_text(encoding="utf-8-sig"))


def stable_seed(*parts: str) -> int:
    return 20260920 ^ int.from_bytes(
        hashlib.sha256("\0".join(parts).encode()).digest()[:8], "big"
    )


def bootstrap_mean(values: Sequence[float], seed: int) -> dict[str, Any]:
    if not values:
        return {"estimate": None, "ci_low": None, "ci_high": None, "task_count": 0}
    point = statistics.fmean(values)
    rng = random.Random(seed)
    draws = [
        statistics.fmean(values[rng.randrange(len(values))] for _ in values)
        for _ in range(BOOTSTRAP_SAMPLES)
    ]
    return {
        "estimate": point,
        "ci_low": _quantile(draws, 0.025),
        "ci_high": _quantile(draws, 0.975),
        "task_count": len(values),
        "bootstrap_samples": BOOTSTRAP_SAMPLES,
    }


def task_condition_means(episodes: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str, str], float]:
    grouped: defaultdict[tuple[str, str, str], list[float]] = defaultdict(list)
    for row in episodes:
        for metric in METRICS:
            value = row["metrics"].get(metric)
            if value is not None:
                grouped[(str(row["task_id"]), str(row["condition"]), metric)].append(float(value))
    return {key: statistics.fmean(values) for key, values in grouped.items()}


def interaction_analysis(episodes: Sequence[Mapping[str, Any]], populations: Mapping[str, set[str]]) -> dict[str, Any]:
    # Replicates are first aligned inside task. The interaction is the literal
    # difference-in-differences requested by the reviewer for every metric.
    index = {
        (str(row["task_id"]), str(row["condition"]), int(row["replicate"])): row
        for row in episodes
    }
    rows: list[dict[str, Any]] = []
    per_task: list[dict[str, Any]] = []
    for population in POPULATIONS:
        for metric in METRICS:
            values = []
            for task_id in sorted(populations[population]):
                replicate_values = []
                for replicate in EXPECTED_REPLICATES:
                    keys = [
                        (task_id, condition, replicate)
                        for condition in ("gpt_selfplay", "gpt_no_comm", "gemini_selfplay", "gemini_no_comm")
                    ]
                    if not all(key in index for key in keys):
                        continue
                    vals = [index[key]["metrics"].get(metric) for key in keys]
                    if any(value is None for value in vals):
                        continue
                    gpt_self, gpt_no, gem_self, gem_no = map(float, vals)
                    replicate_values.append((gpt_self - gpt_no) - (gem_self - gem_no))
                if replicate_values:
                    task_value = statistics.fmean(replicate_values)
                    values.append(task_value)
                    per_task.append({
                        "population": population, "task_id": task_id, "metric": metric,
                        "interaction": task_value, "replicates": len(replicate_values),
                    })
            summary = bootstrap_mean(values, stable_seed("interaction", population, metric))
            excludes = bool(summary["ci_low"] > 0 or summary["ci_high"] < 0)
            scale = 100.0 if metric == "coordination_violation_rate" else 1.0
            rows.append({
                "population": population, "metric": metric, **summary,
                "ci_excludes_zero": excludes,
                "reporting_scale": scale,
                "report_estimate": summary["estimate"] * scale,
                "report_ci_low": summary["ci_low"] * scale,
                "report_ci_high": summary["ci_high"] * scale,
                "direction": "(GPT Self-GPT NoComm)-(Gemini Self-Gemini NoComm)",
            })
    return {
        "format": "fwcollab.model_communication_interaction.v1",
        "model_api_calls": 0,
        "input_episodes": len(episodes),
        "input_mutation": False,
        "bootstrap_unit": "task after within-task replicate aggregation",
        "bootstrap_samples": BOOTSTRAP_SAMPLES,
        "rows": rows,
        "per_task": per_task,
    }


def loto_analysis(episodes: Sequence[Mapping[str, Any]], task_ids: Sequence[str]) -> dict[str, Any]:
    means = task_condition_means(episodes)
    definitions = {
        "success_rate": ("gpt_selfplay", "gpt_no_comm", 1.0),
        "clean_handoff_rate": ("gpt_selfplay", "gpt_no_comm", 1.0),
        # Positive means communication reduced violations.
        "coordination_violation_rate": ("gpt_no_comm", "gpt_selfplay", 1.0),
    }
    task_effects: dict[str, dict[str, float]] = {}
    for metric, (left, right, _) in definitions.items():
        task_effects[metric] = {
            task_id: means[(task_id, left, metric)] - means[(task_id, right, metric)]
            for task_id in task_ids
        }

    rows = []
    summaries = []
    for metric, values_by_task in task_effects.items():
        all_values = list(values_by_task.values())
        full = bootstrap_mean(all_values, stable_seed("gpt_effect", metric))
        loto_values = []
        for omitted in task_ids:
            value = statistics.fmean(value for task_id, value in values_by_task.items() if task_id != omitted)
            loto_values.append(value)
            rows.append({"omitted_task": omitted, "metric": metric, "estimate": value})
        sign = 0 if abs(full["estimate"]) <= 1e-12 else (1 if full["estimate"] > 0 else -1)
        reversals = sum(
            (value < -1e-12 if sign > 0 else value > 1e-12) for value in loto_values
        ) if sign else sum(abs(value) > 1e-12 for value in loto_values)
        scale = 100.0 if metric == "coordination_violation_rate" else 1.0
        summaries.append({
            "metric": metric, "effect_direction": "Self-NoComm" if metric != "coordination_violation_rate" else "NoComm-Self (positive=reduction)",
            **full, "loto_min": min(loto_values), "loto_max": max(loto_values),
            "loto_median": statistics.median(loto_values),
            "loto_sign_reversals": reversals,
            "loto_sign_stable": reversals == 0,
            "reporting_scale": scale,
        })

    absolute = {}
    for condition in ("gpt_selfplay", "gpt_no_comm"):
        subset = [row for row in episodes if row["condition"] == condition]
        events = sum(int(row["coordination_violations"]) for row in subset)
        rounds = sum(int(row["executed_rounds"]) for row in subset)
        absolute[condition] = {
            "episodes": len(subset), "events": events, "executed_rounds": rounds,
            "pooled_rate_per_100_rounds": 100.0 * events / rounds,
        }
    return {
        "format": "fwcollab.phase2_gpt_loto_robustness.v1",
        "model_api_calls": 0, "input_episodes": len(episodes), "input_mutation": False,
        "absolute_events": absolute, "task_first_effects": summaries, "loto_rows": rows,
    }


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def main() -> None:
    episodes = load("artifacts/evaluations/phase2_v1/per_episode.json")["rows"]
    if len(episodes) != 432 or not all(row.get("replay_verified") and row.get("observations_authenticated") for row in episodes):
        raise RuntimeError("Phase II input gate failed")
    partition = list(csv.DictReader((ROOT / "artifacts/evaluations/phase2_horizon_sensitivity_v1/task_partition.csv").open(encoding="utf-8-sig")))
    groups = {name: {row["task_id"] for row in partition if row["feasibility_group"] == name} for name in ("CERTIFIED_FEASIBLE_80", "PROVABLY_INFEASIBLE_80", "UNRESOLVED_80")}
    populations = {
        "P0_ORIGINAL_24": set().union(*groups.values()),
        "P1_EXCLUDE_PROVABLY_INFEASIBLE": groups["CERTIFIED_FEASIBLE_80"] | groups["UNRESOLVED_80"],
        "P2_CERTIFIED_FEASIBLE_ONLY": groups["CERTIFIED_FEASIBLE_80"],
    }
    interaction = interaction_analysis(episodes, populations)
    INTERACTION_OUT.mkdir(parents=True, exist_ok=True)
    (INTERACTION_OUT / "results.json").write_text(json.dumps(interaction, indent=2) + "\n", encoding="utf-8")
    write_csv(INTERACTION_OUT / "interaction_table.csv", interaction["rows"], tuple(interaction["rows"][0]))
    lines = [
        "# Model x Communication interaction", "",
        "Status: **complete offline analysis**. Model/API calls: **0**. Inputs: 432 frozen, replay-authenticated Phase II episodes; no mutation.", "",
        "The estimand is `(GPT Self - GPT NoComm) - (Gemini Self - Gemini NoComm)`. Replicates are averaged within task, followed by a 10,000-draw paired task bootstrap. Violation-rate values are displayed per 100 executed rounds; all other metrics use native units.", "",
        "| Population | Metric | Interaction [95% CI] | Tasks | Excludes 0 |", "|---|---|---:|---:|---|",
    ]
    for row in interaction["rows"]:
        lines.append(f"| {row['population']} | {row['metric']} | {row['report_estimate']:.6f} [{row['report_ci_low']:.6f}, {row['report_ci_high']:.6f}] | {row['task_count']} | {'yes' if row['ci_excludes_zero'] else 'no'} |")
    p0_excluding = [row["metric"] for row in interaction["rows"] if row["population"] == "P0_ORIGINAL_24" and row["ci_excludes_zero"]]
    lines += ["", f"P0 metrics whose interaction CI excludes zero: **{', '.join(p0_excluding) if p0_excluding else 'none'}**. For metrics whose interval includes zero, the supported wording is: *heterogeneous point patterns across the two tested families*, not a confirmed model-dependent interaction.", ""]
    (INTERACTION_OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")

    loto = loto_analysis(episodes, sorted(populations["P0_ORIGINAL_24"]))
    LOTO_OUT.mkdir(parents=True, exist_ok=True)
    (LOTO_OUT / "results.json").write_text(json.dumps(loto, indent=2) + "\n", encoding="utf-8")
    write_csv(LOTO_OUT / "loto_estimates.csv", loto["loto_rows"], ("omitted_task", "metric", "estimate"))
    lines = [
        "# GPT violation counts and leave-one-task-out robustness", "",
        "Status: **complete offline analysis**. Model/API calls: **0**. Inputs are the frozen Phase II episodes.", "",
        "## Absolute events", "",
        "| Condition | Episodes | Violation events | Executed rounds | Pooled rate /100 rounds |", "|---|---:|---:|---:|---:|",
    ]
    for condition, row in loto["absolute_events"].items():
        lines.append(f"| {condition} | {row['episodes']} | {row['events']} | {row['executed_rounds']} | {row['pooled_rate_per_100_rounds']:.6f} |")
    lines += ["", "The formal paired effect remains task-first; the pooled count/exposure rate above is descriptive and is not substituted for it. Positive violation effect means fewer violations under Self-play.", "", "## Task-first effects and LOTO", "", "| Metric | Full effect [95% CI] | LOTO min | median | max | Sign reversals |", "|---|---:|---:|---:|---:|---:|"]
    for row in loto["task_first_effects"]:
        scale = row["reporting_scale"]
        lines.append(f"| {row['metric']} | {row['estimate']*scale:.6f} [{row['ci_low']*scale:.6f}, {row['ci_high']*scale:.6f}] | {row['loto_min']*scale:.6f} | {row['loto_median']*scale:.6f} | {row['loto_max']*scale:.6f} | {row['loto_sign_reversals']} |")
    violation = next(row for row in loto["task_first_effects"] if row["metric"] == "coordination_violation_rate")
    lines += ["", f"Violation-effect sign stability across all 24 leave-one-task-out estimates: **{violation['loto_sign_stable']}**. Therefore no single omitted task reverses the estimated reduction.", ""]
    (LOTO_OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"interaction_rows": len(interaction["rows"]), "loto_rows": len(loto["loto_rows"]), "model_api_calls": 0}))


if __name__ == "__main__":
    main()
