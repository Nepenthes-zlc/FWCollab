"""Exploratory task-level DAG complexity analysis on Diagnostic-24."""

from __future__ import annotations

import csv
import json
import math
import random
import statistics
from collections import defaultdict, deque
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DIAGNOSTIC = ROOT / "eval_private" / "collaboration_diagnostic_24" / "manifest.json"
SOURCE = ROOT / "eval_private" / "spatial_curriculum_full_v4" / "manifest.json"
METRICS = ROOT / "artifacts" / "evaluations" / "phase2_v1" / "task_metrics.csv"
OUT = ROOT / "artifacts" / "evaluations" / "dag_complexity_v1"
CONDITIONS = ("gpt_selfplay", "gemini_selfplay")
OUTCOMES = ("success_rate", "dag_completion", "dag_progress_auc", "coordination_violation_rate")


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    result = [0.0] * len(values)
    index = 0
    while index < len(order):
        end = index + 1
        while end < len(order) and values[order[end]] == values[order[index]]:
            end += 1
        rank = (index + 1 + end) / 2
        for cursor in range(index, end):
            result[order[cursor]] = rank
        index = end
    return result


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 3 or len(set(left)) < 2 or len(set(right)) < 2:
        return None
    a, b = statistics.fmean(left), statistics.fmean(right)
    numerator = sum((x - a) * (y - b) for x, y in zip(left, right, strict=True))
    denominator = math.sqrt(sum((x - a) ** 2 for x in left) * sum((y - b) ** 2 for y in right))
    return numerator / denominator if denominator else None


def _spearman(left: list[float], right: list[float]) -> float | None:
    return _pearson(_ranks(left), _ranks(right))


def _inferential_stats(left: list[float], right: list[float], seed: int) -> tuple[float | None, float | None, float | None, float | None]:
    observed = _spearman(left, right)
    if observed is None:
        return None, None, None, None
    rng = random.Random(seed)
    boot = []
    for _ in range(10_000):
        indices = [rng.randrange(len(left)) for _ in left]
        value = _spearman([left[index] for index in indices], [right[index] for index in indices])
        if value is not None:
            boot.append(value)
    boot.sort()
    low = boot[int(len(boot) * 0.025)] if boot else None
    high = boot[min(len(boot) - 1, int(len(boot) * 0.975))] if boot else None
    extreme = 0
    shuffled = list(right)
    for _ in range(10_000):
        rng.shuffle(shuffled)
        value = _spearman(left, shuffled)
        extreme += value is not None and abs(value) >= abs(observed)
    return observed, low, high, (extreme + 1) / 10_001


def _center_within(values: list[float], groups: list[str]) -> list[float]:
    means = {
        group: statistics.fmean(value for value, candidate in zip(values, groups, strict=True) if candidate == group)
        for group in set(groups)
    }
    return [value - means[group] for value, group in zip(values, groups, strict=True)]


def _within_difficulty_stats(left: list[float], right: list[float], groups: list[str], seed: int):
    observed = _pearson(_center_within(left, groups), _center_within(right, groups))
    if observed is None:
        return None, None, None, None
    rng = random.Random(seed)
    group_indices = {group: [index for index, candidate in enumerate(groups) if candidate == group] for group in set(groups)}
    boot = []
    for _ in range(10_000):
        indices = [rng.choice(group_indices[group]) for group in groups]
        sample_groups = list(groups)
        value = _pearson(
            _center_within([left[index] for index in indices], sample_groups),
            _center_within([right[index] for index in indices], sample_groups),
        )
        if value is not None:
            boot.append(value)
    boot.sort()
    low = boot[int(len(boot) * 0.025)] if boot else None
    high = boot[min(len(boot) - 1, int(len(boot) * 0.975))] if boot else None
    extreme = 0
    for _ in range(10_000):
        permuted = list(right)
        for indices in group_indices.values():
            values = [permuted[index] for index in indices]
            rng.shuffle(values)
            for index, value in zip(indices, values, strict=True):
                permuted[index] = value
        value = _pearson(_center_within(left, groups), _center_within(permuted, groups))
        extreme += value is not None and abs(value) >= abs(observed)
    return observed, low, high, (extreme + 1) / 10_001


def _longest_path(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> int:
    incoming = {node["id"]: 0 for node in nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        outgoing[edge["from"]].append(edge["to"])
        incoming[edge["to"]] += 1
    queue = deque(node_id for node_id, degree in incoming.items() if degree == 0)
    depth = {node_id: 0 for node_id in incoming}
    while queue:
        node_id = queue.popleft()
        for target in outgoing[node_id]:
            depth[target] = max(depth[target], depth[node_id] + 1)
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    return max(depth.values())


def _cross_agent_dependencies(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> int:
    owners = {node["id"]: node["owner"] for node in nodes}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        outgoing[edge["from"]].append(edge["to"])
    dependencies = set()
    for source, owner in owners.items():
        if owner not in {"agent_a", "agent_b"}:
            continue
        queue = deque(outgoing[source])
        seen = set()
        while queue:
            target = queue.popleft()
            if target in seen:
                continue
            seen.add(target)
            target_owner = owners[target]
            if target_owner in {"agent_a", "agent_b"}:
                if target_owner != owner:
                    dependencies.add((source, target))
                continue
            queue.extend(outgoing[target])
    return len(dependencies)


def _features(record: dict[str, Any], dag: dict[str, Any]) -> dict[str, float]:
    incoming = defaultdict(int)
    for edge in dag["edges"]:
        incoming[edge["to"]] += 1
    stages = record["stages"]
    return {
        "dag_node_count": float(len(dag["nodes"])),
        "dag_depth": float(_longest_path(dag["nodes"], dag["edges"])),
        "cross_agent_dependency_count": float(_cross_agent_dependencies(dag["nodes"], dag["edges"])),
        "handoff_count": float(len(stages)),
        "role_alternation_count": float(sum(stages[index]["supporter"] != stages[index - 1]["supporter"] for index in range(1, len(stages)))),
        "all_join_count": float(sum(node["join"] == "all" and incoming[node["id"]] > 1 for node in dag["nodes"])),
        "hold_dependency_count": float(sum(controller["kind"] == "plate" for stage in stages for controller in stage["controllers"])),
        "controller_count": float(sum(len(stage["controllers"]) for stage in stages)),
    }


def _bh(rows: list[dict[str, Any]]) -> None:
    valid = sorted((row for row in rows if row["permutation_p"] is not None), key=lambda row: row["permutation_p"])
    adjusted = [0.0] * len(valid)
    running = 1.0
    for index in range(len(valid) - 1, -1, -1):
        running = min(running, valid[index]["permutation_p"] * len(valid) / (index + 1))
        adjusted[index] = running
    for row, value in zip(valid, adjusted, strict=True):
        row["bh_fdr"] = value


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    diagnostic = json.loads(DIAGNOSTIC.read_text(encoding="utf-8"))
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    source_by_id = {record["id"]: record for record in source["records"]}
    feature_rows = []
    for selected in diagnostic["records"]:
        record = source_by_id[selected["id"]]
        dag = json.loads((ROOT / record["dag"]).read_text(encoding="utf-8"))
        feature_rows.append({"task_id": record["id"], "difficulty": record["difficulty"], **_features(record, dag)})
    feature_by_task = {row["task_id"]: row for row in feature_rows}

    outcomes: dict[tuple[str, str], dict[str, float]] = {}
    with METRICS.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["condition"] in CONDITIONS:
                outcomes[(row["condition"], row["task_id"])] = {metric: float(row[metric]) for metric in OUTCOMES}
    if len(outcomes) != len(CONDITIONS) * len(feature_rows):
        raise RuntimeError("Diagnostic-24 self-play task metrics are incomplete")

    analyses = []
    feature_names = [key for key in feature_rows[0] if key not in {"task_id", "difficulty"}]
    for condition_index, condition in enumerate(CONDITIONS):
        for feature_index, feature in enumerate(feature_names):
            left = [feature_by_task[task_id][feature] for task_id in feature_by_task]
            for outcome_index, outcome in enumerate(OUTCOMES):
                right = [outcomes[(condition, task_id)][outcome] for task_id in feature_by_task]
                rho, low, high, p = _inferential_stats(left, right, 4100 + condition_index * 1000 + feature_index * 50 + outcome_index)
                analyses.append({
                    "analysis": "marginal_spearman", "condition": condition, "feature": feature, "outcome": outcome, "association": rho,
                    "ci_low": low, "ci_high": high, "permutation_p": p, "bh_fdr": None, "task_n": len(left),
                })
                groups = [feature_by_task[task_id]["difficulty"] for task_id in feature_by_task]
                value, within_low, within_high, within_p = _within_difficulty_stats(
                    left, right, groups, 8100 + condition_index * 1000 + feature_index * 50 + outcome_index
                )
                analyses.append({
                    "analysis": "within_difficulty_centered_pearson", "condition": condition, "feature": feature,
                    "outcome": outcome, "association": value, "ci_low": within_low, "ci_high": within_high,
                    "permutation_p": within_p, "bh_fdr": None, "task_n": len(left),
                })
    _bh(analyses)

    with (OUT / "task_features.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(feature_rows[0]))
        writer.writeheader(); writer.writerows(feature_rows)
    with (OUT / "correlations.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(analyses[0]))
        writer.writeheader(); writer.writerows(analyses)
    payload = {
        "format": "fwcollab.dag_complexity_analysis.v1",
        "scope": "Diagnostic-24 self-play, three seeds averaged within task",
        "method": "marginal Spearman plus within-difficulty-centered Pearson sensitivity; task/stratified bootstrap CI; permutation p; BH correction across all reported tests",
        "claim_boundary": "exploratory association only; features are collinear with stage count and difficulty",
        "features": feature_rows,
        "correlations": analyses,
    }
    (OUT / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    marginal = sorted((row for row in analyses if row["analysis"] == "marginal_spearman" and row["association"] is not None), key=lambda row: abs(row["association"]), reverse=True)[:12]
    within = sorted((row for row in analyses if row["analysis"] == "within_difficulty_centered_pearson" and row["association"] is not None), key=lambda row: abs(row["association"]), reverse=True)[:12]
    lines = [
        "# DAG complexity analysis",
        "",
        "Scope: Diagnostic-24 self-play, with three seeds averaged within each task. Results are exploratory univariate associations, not causal or independent feature effects.",
        "",
        "| Model | Feature | Outcome | Spearman rho | 95% bootstrap CI | permutation p | BH-FDR |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for row in marginal:
        lines.append(f"| {row['condition']} | {row['feature']} | {row['outcome']} | {row['association']:.3f} | [{row['ci_low']:.3f}, {row['ci_high']:.3f}] | {row['permutation_p']:.4f} | {row['bh_fdr']:.4f} |")
    lines += ["", "Features are strongly collinear in this curriculum (for example, DAG depth, handoff count and role alternation rise together). The table must not be interpreted as a multivariable attribution of difficulty.", "", "## Within-difficulty sensitivity analysis", "", "Each feature and outcome is centered within L1-L7 before computing Pearson association. This asks whether variation among tasks of the same coarse difficulty tracks performance.", "", "| Model | Feature | Outcome | centered r | 95% stratified bootstrap CI | stratified permutation p | BH-FDR |", "|---|---|---|---:|---:|---:|---:|"]
    for row in within:
        lines.append(f"| {row['condition']} | {row['feature']} | {row['outcome']} | {row['association']:.3f} | [{row['ci_low']:.3f}, {row['ci_high']:.3f}] | {row['permutation_p']:.4f} | {row['bh_fdr']:.4f} |")
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"tasks": len(feature_rows), "tests": len(analyses), "output": str(OUT)}))


if __name__ == "__main__":
    main()
