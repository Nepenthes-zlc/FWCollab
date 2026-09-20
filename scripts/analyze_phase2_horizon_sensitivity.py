"""Offline feasibility-aware sensitivity analysis of frozen Phase II."""

from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fwcollab.analysis.phase2 import (  # noqa: E402
    BOOTSTRAP_SAMPLES, CONDITION_IDS, CONTRASTS, METRICS,
    _condition_summaries, _contrast_summaries, _paired_differences, _task_metrics,
)

OUT = ROOT / "artifacts/evaluations/phase2_horizon_sensitivity_v1"
POPULATIONS = ("P0_ORIGINAL_24", "P1_EXCLUDE_PROVABLY_INFEASIBLE", "P2_CERTIFIED_FEASIBLE_ONLY")
FOCAL_CONTRASTS = (
    "gpt_communication_gain", "gemini_communication_gain",
    "crossplay_gap", "crossplay_role_assignment_gap",
)
FOCAL_METRICS = (
    "success_rate", "dag_completion", "dag_progress_auc",
    "clean_handoff_rate", "coordination_violation_rate", "rounds_to_success",
)
CLAIMS = (
    ("gpt_sr", "GPT communication effect on SR", "gpt_communication_gain", "success_rate"),
    ("gpt_clean_handoff", "GPT communication effect on Clean Handoff", "gpt_communication_gain", "clean_handoff_rate"),
    ("gpt_violations", "GPT communication effect on violations", "gpt_communication_gain", "coordination_violation_rate"),
    ("gemini_sr", "Gemini communication effect on SR", "gemini_communication_gain", "success_rate"),
    ("gemini_completion", "Gemini communication effect on DAG Completion", "gemini_communication_gain", "dag_completion"),
    ("gemini_auc", "Gemini communication effect on AUC", "gemini_communication_gain", "dag_progress_auc"),
    ("gemini_clean_handoff", "Gemini communication effect on Clean Handoff", "gemini_communication_gain", "clean_handoff_rate"),
    ("pooled_crossplay_sr", "Pooled self-play vs cross-play SR", "crossplay_gap", "success_rate"),
    ("direct_crossplay_sr", "Direct Cross-play A vs B SR", "crossplay_role_assignment_gap", "success_rate"),
)
# Fixed before inspecting filtered estimates. Same-direction changes beyond these
# absolute floors or 50% of |P0| are DIRECTIONALLY_STABLE, not STABLE.
STABILITY_FLOORS = {
    "success_rate": 0.05, "dag_completion": 0.03, "dag_progress_auc": 0.03,
    "clean_handoff_rate": 0.05, "coordination_violation_rate": 0.002,
    "rounds_to_success": 5.0,
}


def _load(path: str) -> Any:
    return json.loads((ROOT / path).read_text(encoding="utf-8-sig"))


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def _rank(values: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1]); ranks = [0.0] * len(values); index = 0
    while index < len(indexed):
        end = index + 1
        while end < len(indexed) and indexed[end][1] == indexed[index][1]: end += 1
        rank = (index + 1 + end) / 2.0
        for cursor in range(index, end): ranks[indexed[cursor][0]] = rank
        index = end
    return ranks


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 3: return None
    lm, rm = fmean(left), fmean(right); ln = math.sqrt(sum((v - lm) ** 2 for v in left)); rn = math.sqrt(sum((v - rm) ** 2 for v in right))
    if ln == 0 or rn == 0: return None
    return sum((a - lm) * (b - rm) for a, b in zip(left, right, strict=True)) / (ln * rn)


def _spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    return _pearson(_rank(left), _rank(right))


def _residual(values: Sequence[float], control: Sequence[float]) -> list[float] | None:
    if len(values) < 3: return None
    cm, vm = fmean(control), fmean(values); denom = sum((x - cm) ** 2 for x in control)
    if denom == 0: return None
    slope = sum((x - cm) * (y - vm) for x, y in zip(control, values, strict=True)) / denom
    return [y - (vm + slope * (x - cm)) for x, y in zip(control, values, strict=True)]


def _partial_spearman(left: Sequence[float], right: Sequence[float], control: Sequence[float]) -> float | None:
    lr, rr, cr = _rank(left), _rank(right), _rank(control)
    lres, rres = _residual(lr, cr), _residual(rr, cr)
    return None if lres is None or rres is None else _pearson(lres, rres)


def _classification(row: Mapping[str, Any]) -> str:
    low, high = row["ci_low"], row["ci_high"]
    if low is None or high is None: return "not_estimable"
    if low > 0: return "positive_excludes_zero"
    if high < 0: return "negative_excludes_zero"
    return "inconclusive"


def _reporting_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    scale = 100.0 if row["metric"] == "coordination_violation_rate" else 1.0
    unit = "violations_per_100_executed_rounds" if scale == 100.0 else "native_metric_unit"
    return {
        "reporting_scale": scale, "reporting_unit": unit,
        "report_estimate": None if row["estimate"] is None else float(row["estimate"]) * scale,
        "report_ci_low": None if row["ci_low"] is None else float(row["ci_low"]) * scale,
        "report_ci_high": None if row["ci_high"] is None else float(row["ci_high"]) * scale,
    }


def _sign(value: float, tolerance: float = 1e-12) -> int:
    return 1 if value > tolerance else (-1 if value < -tolerance else 0)


def _stability(rows: Sequence[Mapping[str, Any]], metric: str) -> tuple[str, str]:
    ordered = {str(row["population"]): row for row in rows}; values = [ordered[p]["estimate"] for p in POPULATIONS]
    if any(v is None for v in values) or any(int(ordered[p]["n_tasks"]) < 5 for p in POPULATIONS):
        return "NOT_ESTIMABLE", "At least one population has fewer than five paired tasks or no estimate."
    numeric = [float(v) for v in values]; signs = [_sign(v) for v in numeric]; p0 = numeric[0]
    if signs[0] == 0:
        floor = STABILITY_FLOORS[metric]
        if all(abs(v) <= floor for v in numeric) and all(_classification(ordered[p]) == "inconclusive" for p in POPULATIONS):
            return "STABLE", f"P0 is zero; all estimates remain within the pre-set {floor:g} floor and inconclusive."
        return "SENSITIVE", "P0 has no direction and at least one filtered estimate exceeds the pre-set near-zero floor."
    if any(sign not in (0, signs[0]) for sign in signs[1:]):
        return "SENSITIVE", "Effect sign reverses after feasibility filtering."
    threshold = max(STABILITY_FLOORS[metric], 0.5 * abs(p0))
    same_interpretation = len({_classification(ordered[p]) for p in POPULATIONS}) == 1
    if all(abs(value - p0) <= threshold for value in numeric[1:]) and same_interpretation and all(sign == signs[0] for sign in signs):
        return "STABLE", f"Direction and CI interpretation are unchanged; deviations from P0 are <= {threshold:g}."
    return "DIRECTIONALLY_STABLE", "Direction is retained, but magnitude and/or interval interpretation changes."


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    horizon_rows = list(csv.DictReader((ROOT / "artifacts/audits/leaderboard_horizon_feasibility_v1/per_task.csv").open(encoding="utf-8-sig")))
    partition = []
    for row in horizon_rows:
        if row["track"] != "Diagnostic-24": continue
        best, lower = int(row["best_certified_rounds"]), (int(row["optimistic_movement_lower_bound"]) if row["optimistic_movement_lower_bound"] else None)
        if best <= 80: group, reason = "CERTIFIED_FEASIBLE_80", "successful replay/evaluator-valid certificate <=80"
        elif lower is not None and lower > 80: group, reason = "PROVABLY_INFEASIBLE_80", "optimistic movement lower bound >80"
        else: group, reason = "UNRESOLVED_80", "best certificate >80 and no strict lower bound >80"
        partition.append({"task_id": row["task_id"], "level": row["task_id"].split("-")[1], "best_certified_rounds": best, "optimistic_lower_bound": lower if lower is not None else "", "feasibility_group": group, "reason": reason})
    _write_csv(OUT / "task_partition.csv", partition, ("task_id", "level", "best_certified_rounds", "optimistic_lower_bound", "feasibility_group", "reason"))
    groups = {name: {row["task_id"] for row in partition if row["feasibility_group"] == name} for name in ("CERTIFIED_FEASIBLE_80", "PROVABLY_INFEASIBLE_80", "UNRESOLVED_80")}
    populations = {
        "P0_ORIGINAL_24": set().union(*groups.values()),
        "P1_EXCLUDE_PROVABLY_INFEASIBLE": groups["CERTIFIED_FEASIBLE_80"] | groups["UNRESOLVED_80"],
        "P2_CERTIFIED_FEASIBLE_ONLY": groups["CERTIFIED_FEASIBLE_80"],
    }
    episodes = _load("artifacts/evaluations/phase2_v1/per_episode.json")["rows"]
    manifest = _load("eval_private/collaboration_diagnostic_24/manifest.json"); records_all = manifest["records"]
    spec = _load("eval_private/phase2_v1/experiment_spec.json"); conditions = spec["conditions"]
    result: dict[str, Any] = {
        "format": "fwcollab.phase2_horizon_sensitivity.v1", "model_api_calls": 0,
        "input_episode_count": len(episodes), "input_mutation": False,
        "population_task_counts": {name: len(ids) for name, ids in populations.items()},
        "feasibility_group_counts": {name: len(ids) for name, ids in groups.items()},
        "populations": {},
    }
    all_effect_rows = []; task_rows_by_pop = {}
    for population, ids in populations.items():
        records = [row for row in records_all if row["id"] in ids]; subset = [row for row in episodes if row["task_id"] in ids]
        task_rows = _task_metrics(subset, conditions, records); task_rows_by_pop[population] = task_rows
        condition_rows = _condition_summaries(task_rows, conditions)
        paired = _paired_differences(subset, records); contrast_rows = _contrast_summaries(paired)
        selected_conditions = [{**row, **_reporting_fields(row)} for row in condition_rows if row["metric"] in FOCAL_METRICS]
        selected_contrasts = [{**row, **_reporting_fields(row)} for row in contrast_rows if row["contrast"] in FOCAL_CONTRASTS and row["metric"] in FOCAL_METRICS]
        result["populations"][population] = {"task_ids": [row["id"] for row in records], "episode_count": len(subset), "conditions": selected_conditions, "contrasts": selected_contrasts}
        for row in selected_contrasts: all_effect_rows.append({"population": population, **row})
    # Exact P0 reproduction check against frozen Phase II summaries.
    frozen_conditions = {(r["condition"], r["metric"]): r for r in _load("artifacts/evaluations/phase2_v1/condition_summary.json")["rows"]}
    frozen_contrasts = {(r["contrast"], r["metric"]): r for r in _load("artifacts/evaluations/phase2_v1/contrasts.json")["rows"]}
    p0 = result["populations"]["P0_ORIGINAL_24"]
    result["p0_exact_reproduction"] = all(r["estimate"] == frozen_conditions[(r["condition"], r["metric"])]["estimate"] and r["ci_low"] == frozen_conditions[(r["condition"], r["metric"])]["ci_low"] and r["ci_high"] == frozen_conditions[(r["condition"], r["metric"])]["ci_high"] for r in p0["conditions"]) and all(r["estimate"] == frozen_contrasts[(r["contrast"], r["metric"])]["estimate"] and r["ci_low"] == frozen_contrasts[(r["contrast"], r["metric"])]["ci_low"] and r["ci_high"] == frozen_contrasts[(r["contrast"], r["metric"])]["ci_high"] for r in p0["contrasts"])
    if not result["p0_exact_reproduction"]: raise RuntimeError("P0 does not reproduce frozen Phase II")
    stability = []
    for claim_id, label, contrast, metric in CLAIMS:
        rows = [row for row in all_effect_rows if row["contrast"] == contrast and row["metric"] == metric]
        status, reason = _stability(rows, metric)
        stability.append({"claim_id": claim_id, "claim": label, "contrast": contrast, "metric": metric, "status": status, "reason": reason, "populations": {row["population"]: {key: row[key] for key in ("estimate", "ci_low", "ci_high", "n_tasks")} for row in rows}})
    result["stability"] = stability
    impossible_eps = [row for row in episodes if row["task_id"] in groups["PROVABLY_INFEASIBLE_80"]]
    impossible_sr = {condition: fmean(float(row["metrics"]["success_rate"]) for row in impossible_eps if row["condition"] == condition) for condition in CONDITION_IDS}
    result["success_ceiling"] = {"provably_infeasible_tasks": 9, "fraction_of_diagnostic_24": 9 / 24, "theoretical_success_under_80": "impossible", "observed_sr_by_condition": impossible_sr, "all_observed_zero": all(value == 0 for value in impossible_sr.values()), "aggregate_sr_denominator_share": 9 / 24, "maximum_possible_original_task_sr_given_proven_infeasibility": 15 / 24, "feasible_task_sr": {row["condition"]: row["estimate"] for row in result["populations"]["P2_CERTIFIED_FEASIBLE_ONLY"]["conditions"] if row["metric"] == "success_rate"}}
    coverage = {row["task_id"]: row for row in csv.DictReader((ROOT / "artifacts/audits/task_coverage_v1/task_coverage.csv").open(encoding="utf-8-sig"))}
    part_by_id = {row["task_id"]: row for row in partition}; complexity = []
    feature_rows = []
    for condition in CONDITION_IDS:
        task_index = {row["task_id"]: row for row in task_rows_by_pop["P0_ORIGINAL_24"] if row["condition"] == condition}
        complete_ids = [task_id for task_id in populations["P0_ORIGINAL_24"] if part_by_id[task_id]["optimistic_lower_bound"] != ""]
        feasible_ids = sorted(groups["CERTIFIED_FEASIBLE_80"])
        def values(ids: Iterable[str], x):
            ordered = sorted(ids); return [float(x(task_id)) for task_id in ordered], [float(task_index[task_id]["metrics"]["dag_completion"]) for task_id in ordered]
        for feature, getter in (("optimistic_movement_lower_bound", lambda t: part_by_id[t]["optimistic_lower_bound"]), ("best_certified_rounds", lambda t: part_by_id[t]["best_certified_rounds"]), ("dag_depth", lambda t: coverage[t]["dag_depth"])):
            x, y = values(complete_ids, getter); complexity.append({"analysis": "marginal_spearman", "population": "P0_COMPLETE_LOWER_BOUND", "condition": condition, "feature": feature, "outcome": "dag_completion", "rho": _spearman(x, y), "task_n": len(x)})
            feasible_for_feature = [task_id for task_id in feasible_ids if feature != "optimistic_movement_lower_bound" or part_by_id[task_id]["optimistic_lower_bound"] != ""]
            x, y = values(feasible_for_feature, getter); complexity.append({"analysis": "within_certified_feasible_spearman", "population": "P2_CERTIFIED_FEASIBLE_ONLY", "condition": condition, "feature": feature, "outcome": "dag_completion", "rho": _spearman(x, y), "task_n": len(x)})
        ordered = sorted(complete_ids); depth = [float(coverage[t]["dag_depth"]) for t in ordered]; lower = [float(part_by_id[t]["optimistic_lower_bound"]) for t in ordered]; completion = [float(task_index[t]["metrics"]["dag_completion"]) for t in ordered]
        complexity.append({"analysis": "partial_spearman_control_lower_bound", "population": "P0_COMPLETE_LOWER_BOUND", "condition": condition, "feature": "dag_depth", "outcome": "dag_completion", "control": "optimistic_movement_lower_bound", "rho": _partial_spearman(depth, completion, lower), "task_n": len(ordered)})
    all_ids = sorted(populations["P0_ORIGINAL_24"]); lower_ids = [t for t in all_ids if part_by_id[t]["optimistic_lower_bound"] != ""]
    for feature, x_ids, left, right in (("dag_depth_vs_lower_bound", lower_ids, lambda t: float(coverage[t]["dag_depth"]), lambda t: float(part_by_id[t]["optimistic_lower_bound"])), ("dag_depth_vs_best_certified_rounds", all_ids, lambda t: float(coverage[t]["dag_depth"]), lambda t: float(part_by_id[t]["best_certified_rounds"]))):
        complexity.append({"analysis": "feature_spearman", "population": "P0_ORIGINAL_24", "condition": "task_structure", "feature": feature, "outcome": "NA", "rho": _spearman([left(t) for t in x_ids], [right(t) for t in x_ids]), "task_n": len(x_ids)})
    result["complexity_confound"] = complexity
    _write_csv(OUT / "effect_table.csv", all_effect_rows, ("population", "contrast", "contrast_label", "metric", "estimate", "ci_low", "ci_high", "reporting_scale", "reporting_unit", "report_estimate", "report_ci_low", "report_ci_high", "n_tasks", "bootstrap_samples", "direction"))
    (OUT / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Phase II feasibility-aware horizon sensitivity", "",
        "Status: **complete offline sensitivity analysis**", "",
        "- Model/API calls: **0**", "- Inputs: **432/432 existing frozen Phase II episodes**",
        "- Input mutation: **none**; original Phase II artifacts and traces are unchanged",
        f"- P0 exact reproduction of frozen estimates/CIs: **{result['p0_exact_reproduction']}**", "",
        "## Feasibility populations", "",
        f"- P0 ORIGINAL_24: **{len(populations['P0_ORIGINAL_24'])} tasks**",
        f"- P1 EXCLUDE_PROVABLY_INFEASIBLE: **{len(populations['P1_EXCLUDE_PROVABLY_INFEASIBLE'])} tasks**",
        f"- P2 CERTIFIED_FEASIBLE_ONLY: **{len(populations['P2_CERTIFIED_FEASIBLE_ONLY'])} tasks**",
        f"- Partition: certified={len(groups['CERTIFIED_FEASIBLE_80'])}, provably infeasible={len(groups['PROVABLY_INFEASIBLE_80'])}, unresolved={len(groups['UNRESOLVED_80'])}", "",
        "## Claim stability", "", "| Claim | Status | P0 | P1 | P2 |", "|---|---|---:|---:|---:|",
    ]
    for row in stability:
        p = row["populations"]; scale = 100.0 if row["metric"] == "coordination_violation_rate" else 1.0
        lines.append(f"| {row['claim']} | **{row['status']}** | {p['P0_ORIGINAL_24']['estimate'] * scale:.6f} | {p['P1_EXCLUDE_PROVABLY_INFEASIBLE']['estimate'] * scale:.6f} | {p['P2_CERTIFIED_FEASIBLE_ONLY']['estimate'] * scale:.6f} |")
    lines += ["", "Violation effects in the table are violations per 100 executed rounds; other effects use their native units. Rounds-to-Success remains survivor-conditioned. The status rule was fixed in code before inspecting filtered estimates: sign reversal is SENSITIVE; same sign with changed interval interpretation or magnitude is DIRECTIONALLY_STABLE; STABLE additionally requires unchanged interval interpretation and a bounded deviation from P0.", "", "## Success ceiling", "", f"The nine provably infeasible tasks are **37.5%** of Diagnostic-24. Success within 80 rounds is impossible by the relaxed movement lower bound. Observed SR is zero in every condition: **{result['success_ceiling']['all_observed_zero']}**. Their inclusion contributes nine structurally forced zero task cells to every condition's 24-task SR denominator, imposing a maximum possible original-population task SR of **62.5%** even if every other task succeeds. P2 SR is descriptive Feasible-Task SR and does not replace the original SR.", "", "| Condition | Infeasible-task observed SR | Feasible-Task SR (P2) |", "|---|---:|---:|"]
    for condition in CONDITION_IDS:
        lines.append(f"| {condition} | {impossible_sr[condition]:.3f} | {result['success_ceiling']['feasible_task_sr'][condition]:.3f} |")
    lines += ["", "## Complexity confound", "", "The machine-readable results report marginal Spearman correlations, correlations restricted to certified-feasible tasks, and partial rank correlations between DAG depth and completion after controlling the optimistic movement lower bound. With only 10 certified-feasible tasks (and 23 tasks with finite relaxed lower bounds), these are sensitivity diagnostics, not causal estimates."]
    lines += ["", "| Condition | Lower bound vs completion | Best certified rounds vs completion | DAG depth vs completion | DAG depth vs completion, feasible-only | DAG depth partial rho controlling lower bound |", "|---|---:|---:|---:|---:|---:|"]
    for condition in CONDITION_IDS:
        entries = [row for row in complexity if row.get("condition") == condition]
        def rho(analysis: str, feature: str) -> float:
            return float(next(row["rho"] for row in entries if row["analysis"] == analysis and row["feature"] == feature))
        lines.append(
            f"| {condition} | {rho('marginal_spearman', 'optimistic_movement_lower_bound'):.3f} "
            f"| {rho('marginal_spearman', 'best_certified_rounds'):.3f} "
            f"| {rho('marginal_spearman', 'dag_depth'):.3f} "
            f"| {rho('within_certified_feasible_spearman', 'dag_depth'):.3f} "
            f"| {rho('partial_spearman_control_lower_bound', 'dag_depth'):.3f} |"
        )
    structural = {row["feature"]: row for row in complexity if row["analysis"] == "feature_spearman"}
    lines += [
        "",
        f"DAG depth is itself strongly associated with the optimistic movement lower bound "
        f"(rho={structural['dag_depth_vs_lower_bound']['rho']:.3f}, n={structural['dag_depth_vs_lower_bound']['task_n']}) "
        f"and best certified rounds (rho={structural['dag_depth_vs_best_certified_rounds']['rho']:.3f}, "
        f"$n={structural['dag_depth_vs_best_certified_rounds']['task_n']}$). The large marginal depth association therefore has a substantial execution-length confound.",
        "", "See `results.json`, `effect_table.csv`, and `task_partition.csv` for all values and intervals.", "",
    ]
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"populations": result["population_task_counts"], "groups": result["feasibility_group_counts"], "p0_reproduced": result["p0_exact_reproduction"], "impossible_all_zero": result["success_ceiling"]["all_observed_zero"], "model_api_calls": 0}))


if __name__ == "__main__":
    main()
