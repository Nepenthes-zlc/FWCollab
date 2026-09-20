"""Offline structural comparison of Full-72 and Diagnostic-24."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from collections import deque
from pathlib import Path
from typing import Any, Mapping, Sequence
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/audits/diagnostic24_difficulty_bias_v1"
sys.path.insert(0, str(ROOT / "src"))
from fwcollab.symbolic.map import load_symbol_map  # noqa: E402
SAMPLES = 10_000
FEATURES = (
    "dag_depth", "dag_node_count", "dag_edge_count", "handoff_count",
    "role_switch_count", "controller_stage_count",
    "optimistic_movement_lower_bound", "best_certified_rounds",
    "witness_rounds", "enable_count", "maintain_count",
)


def load(path: str) -> Any:
    return json.loads((ROOT / path).read_text(encoding="utf-8-sig"))


def quantile(values: Sequence[float], p: float) -> float:
    ordered = sorted(values); pos = (len(ordered) - 1) * p
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] if lo == hi else ordered[lo] * (hi - pos) + ordered[hi] * (pos - lo)


def summary(values: Sequence[float]) -> dict[str, float | int]:
    return {
        "n": len(values), "mean": statistics.fmean(values), "median": statistics.median(values),
        "q25": quantile(values, .25), "q75": quantile(values, .75),
    }


def bootstrap_diff(full: Sequence[float], diagnostic: Sequence[float], feature: str) -> tuple[float, float]:
    rng = random.Random(20260920 ^ int.from_bytes(hashlib.sha256(feature.encode()).digest()[:8], "big"))
    draws = []
    for _ in range(SAMPLES):
        a = statistics.fmean(full[rng.randrange(len(full))] for _ in full)
        b = statistics.fmean(diagnostic[rng.randrange(len(diagnostic))] for _ in diagnostic)
        draws.append(b - a)
    return quantile(draws, .025), quantile(draws, .975)


def optimistic_lower_bound(map_path: str) -> int | None:
    symbol_map = load_symbol_map(ROOT / map_path)
    distances = []
    for role, goal_symbol in (("F", "f"), ("W", "w")):
        start, goal = symbol_map.unique_position(role), symbol_map.unique_position(goal_symbol)
        queue = deque([((start.row, start.col), 0)]); seen = {(start.row, start.col)}; found = None
        while queue:
            (row, col), distance = queue.popleft()
            if (row, col) == (goal.row, goal.col): found = distance; break
            for dr, dc in ((1,0),(-1,0),(0,1),(0,-1)):
                nxt = (row+dr, col+dc)
                if nxt not in seen and 0 <= nxt[0] < symbol_map.height and 0 <= nxt[1] < symbol_map.width and symbol_map.rows[nxt[0]][nxt[1]] != "#":
                    seen.add(nxt); queue.append((nxt, distance+1))
        if found is None: return None
        distances.append(found)
    return max(distances)


def best_certified_rounds(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    task_ids = {str(row["id"]) for row in records}
    best = {str(row["id"]): int(row["witness_rounds"]) for row in records}
    phase2_path = ROOT / "artifacts/evaluations/phase2_v1/per_episode.csv"
    if phase2_path.is_file():
        for item in csv.DictReader(phase2_path.open(encoding="utf-8")):
            task_id = item.get("task_id", "")
            if task_id in task_ids and item.get("outcome") == "team_success":
                best[task_id] = min(best[task_id], int(item["executed_rounds"]))
    for summary_path in (ROOT / "artifacts/runs").glob("spatial_curriculum_full_v4_*/summary.json"):
        try: result = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): continue
        for item in result.get("results", []):
            task_id = str(item.get("dag_id", item.get("task_id", "")))
            if task_id in task_ids and item.get("outcome") == "team_success":
                best[task_id] = min(best[task_id], int(item["rounds"]))
    return best


def main() -> None:
    coverage = list(csv.DictReader((ROOT / "artifacts/audits/task_coverage_v1/task_coverage.csv").open(encoding="utf-8-sig")))
    if len(coverage) != 72:
        raise RuntimeError("Full-72 coverage input is incomplete")
    diagnostic = load("eval_private/collaboration_diagnostic_24/manifest.json")
    diagnostic_ids = {row["id"] for row in diagnostic["records"]}
    if len(diagnostic_ids) != 24:
        raise RuntimeError("Diagnostic set is not 24 tasks")
    manifest = load("eval_private/spatial_curriculum_full_v4/manifest.json")
    manifest_by = {row["id"]: row for row in manifest["records"]}
    edges = list(csv.DictReader((ROOT / "artifacts/audits/dependency_edges_v1/dependency_edges.csv").open(encoding="utf-8-sig")))
    edge_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for row in edges:
        edge_counts[row["task_id"]][row["edge_scope"]] += 1
        edge_counts[row["task_id"]][row["dependency_type"]] += 1
    best_rounds = best_certified_rounds(manifest["records"])
    rows = []
    for row in coverage:
        task_id = row["task_id"]
        lower_bound = optimistic_lower_bound(str(manifest_by[task_id]["map"]))
        rows.append({
            "task_id": task_id, "subset": "Diagnostic-24" if task_id in diagnostic_ids else "Full-72 remainder",
            "level": row["level"], "dag_depth": int(row["dag_depth"]),
            "dag_node_count": int(row["dag_node_count"]), "dag_edge_count": int(row["dag_edge_count"]),
            "handoff_count": int(row["handoff_count"]), "role_switch_count": int(row["role_switch_count"]),
            "controller_stage_count": edge_counts[task_id]["controller_stage"],
            "optimistic_movement_lower_bound": lower_bound,
            "best_certified_rounds": best_rounds[task_id],
            "witness_rounds": int(manifest_by[task_id]["witness_rounds"]),
            "enable_count": edge_counts[task_id]["ENABLE"], "maintain_count": edge_counts[task_id]["MAINTAIN"],
            "topology_id": row["topology_id"], "template_id": row["template_id"],
        })
    metric_rows = []
    for feature in FEATURES:
        full = [float(row[feature]) for row in rows if row[feature] is not None]
        diag = [float(row[feature]) for row in rows if row["task_id"] in diagnostic_ids and row[feature] is not None]
        fs, ds = summary(full), summary(diag)
        pooled_sd = math.sqrt(((len(full)-1)*statistics.variance(full)+(len(diag)-1)*statistics.variance(diag))/(len(full)+len(diag)-2)) if statistics.variance(full) or statistics.variance(diag) else 0.0
        diff = ds["mean"] - fs["mean"]
        low, high = bootstrap_diff(full, diag, feature)
        metric_rows.append({
            "feature": feature, "full_n": fs["n"], "full_mean": fs["mean"], "full_median": fs["median"], "full_q25": fs["q25"], "full_q75": fs["q75"],
            "diagnostic_n": ds["n"], "diagnostic_mean": ds["mean"], "diagnostic_median": ds["median"], "diagnostic_q25": ds["q25"], "diagnostic_q75": ds["q75"],
            "mean_difference_diagnostic_minus_full": diff, "bootstrap_ci_low": low, "bootstrap_ci_high": high,
            "standardized_difference": diff / pooled_sd if pooled_sd else 0.0,
        })
    levels_full = Counter(row["level"] for row in rows); levels_diag = Counter(row["level"] for row in rows if row["task_id"] in diagnostic_ids)
    topology_full = Counter(row["topology_id"] for row in rows); topology_diag = Counter(row["topology_id"] for row in rows if row["task_id"] in diagnostic_ids)
    template_full = Counter(row["template_id"] for row in rows); template_diag = Counter(row["template_id"] for row in rows if row["task_id"] in diagnostic_ids)
    # A transparent descriptive decision: several core size/length measures
    # have positive SMD >= .5, or the level distribution overweights L4-L7.
    core = [row for row in metric_rows if row["feature"] in {"dag_depth","dag_node_count","dag_edge_count","controller_stage_count","witness_rounds"}]
    hard_share_full = sum(levels_full[f"L{i}"] for i in range(4,8)) / 72
    hard_share_diag = sum(levels_diag[f"L{i}"] for i in range(4,8)) / 24
    biased = sum(row["standardized_difference"] >= .5 for row in core) >= 2 or hard_share_diag - hard_share_full >= .10
    result = {
        "format": "fwcollab.diagnostic24_difficulty_bias.v1", "model_api_calls": 0,
        "selection_uses_model_outcomes": False, "full_task_count": 72, "diagnostic_task_count": 24,
        "comparison_note": "Diagnostic-24 is a subset of Full-72; intervals are descriptive independent-resample bootstrap sensitivity intervals, not a randomization-based selection test.",
        "difficulty_enriched": biased, "feature_rows": metric_rows,
        "level_distribution": {"Full-72": dict(sorted(levels_full.items())), "Diagnostic-24": dict(sorted(levels_diag.items())), "L4_L7_share_full": hard_share_full, "L4_L7_share_diagnostic": hard_share_diag},
        "diversity": {"Full-72_topologies": len(topology_full), "Diagnostic-24_topologies": len(topology_diag), "Full-72_templates": len(template_full), "Diagnostic-24_templates": len(template_diag)},
        "task_rows": rows,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    with (OUT / "feature_comparison.csv").open("w", encoding="utf-8", newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=tuple(metric_rows[0]));writer.writeheader();writer.writerows(metric_rows)
    with (OUT / "per_task.csv").open("w", encoding="utf-8", newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=tuple(rows[0]));writer.writeheader();writer.writerows(rows)
    lines=["# Diagnostic-24 difficulty-selection audit","","Status: **complete structural audit**. Model/API calls: **0**; no model outcome was used to define difficulty or membership.","","Diagnostic-24 is compared with the complete Full-72 population (which contains it). Bootstrap intervals are descriptive sensitivity intervals because the subset was intentionally selected, not randomly sampled.","","| Feature | Full mean / median [Q1,Q3] | Diagnostic mean / median [Q1,Q3] | Mean diff [95% CI] | SMD |","|---|---:|---:|---:|---:|"]
    for row in metric_rows:
        lines.append(f"| {row['feature']} | {row['full_mean']:.3f} / {row['full_median']:.3f} [{row['full_q25']:.3f},{row['full_q75']:.3f}] | {row['diagnostic_mean']:.3f} / {row['diagnostic_median']:.3f} [{row['diagnostic_q25']:.3f},{row['diagnostic_q75']:.3f}] | {row['mean_difference_diagnostic_minus_full']:.3f} [{row['bootstrap_ci_low']:.3f},{row['bootstrap_ci_high']:.3f}] | {row['standardized_difference']:.3f} |")
    lines += ["","## Level and structural diversity","",f"- Full-72 levels: `{dict(sorted(levels_full.items()))}`",f"- Diagnostic-24 levels: `{dict(sorted(levels_diag.items()))}`",f"- L4--L7 share: Full={hard_share_full:.3f}, Diagnostic={hard_share_diag:.3f}",f"- Distinct topology IDs: Full={len(topology_full)}, Diagnostic={len(topology_diag)}",f"- Distinct template IDs: Full={len(template_full)}, Diagnostic={len(template_diag)}","","## Interpretation",""]
    if biased:
        lines.append("Diagnostic-24 is **systematically difficulty-enriched / longer** on multiple structural measures. Recommended wording: *Diagnostic-24 is a structurally enriched diagnostic set and should not be interpreted as a population estimate of Full-72 performance.*")
    else:
        lines.append("This audit does not find consistent structural evidence that Diagnostic-24 is harder than Full-72; it remains an intentionally enriched diagnostic subset rather than a random population sample.")
    lines.append("")
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"difficulty_enriched": biased, "metrics": len(metric_rows), "model_api_calls": 0}))


if __name__ == "__main__": main()
