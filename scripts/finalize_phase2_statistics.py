"""Create the Phase II statistical-closure artifact from final paired outputs."""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path
from statistics import fmean, stdev
from typing import Any


CONTRASTS = (
    "gpt_communication_gain",
    "gemini_communication_gain",
    "crossplay_gap",
    "crossplay_directional_gap_A",
    "crossplay_directional_gap_B",
    "crossplay_role_assignment_gap",
)
METRICS = (
    "success_rate",
    "dag_completion",
    "dag_progress_auc",
    "clean_handoff_rate",
    "coordination_violation_rate",
    "rounds_to_success",
)
BOOTSTRAP_SAMPLES = 10_000
EXPECTED_ANALYSIS_VERSION = "fwcollab.phase2.analysis.v2"
EXPECTED_FREEZE_SHA256 = "29b93111afd874cda37e848bacd184da4b799bde8faf4a0912dfd27f008830d6"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def dump(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def paired_dz(values: list[float], seed: int) -> dict[str, Any]:
    if len(values) < 2 or stdev(values) == 0:
        return {"estimate": None, "ci_low": None, "ci_high": None, "valid_draws": 0}
    estimate = fmean(values) / stdev(values)
    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(BOOTSTRAP_SAMPLES):
        sample = [rng.choice(values) for _ in values]
        spread = stdev(sample)
        if spread:
            draws.append(fmean(sample) / spread)
    return {
        "estimate": estimate,
        "ci_low": quantile(draws, 0.025),
        "ci_high": quantile(draws, 0.975),
        "valid_draws": len(draws),
    }


def classification(low: float, high: float) -> str:
    tolerance = 1e-12
    if low > tolerance:
        return "directional_evidence_favors_left"
    if high < -tolerance:
        return "directional_evidence_favors_right"
    return "inconclusive_at_95pct"


def fmt(metric: str, value: float | None) -> str:
    if value is None:
        return "NA"
    if metric in {
        "success_rate",
        "dag_completion",
        "dag_progress_auc",
        "clean_handoff_rate",
    }:
        return f"{100 * value:+.2f} pp"
    if metric == "coordination_violation_rate":
        return f"{100 * value:+.3f} per 100 rounds"
    return f"{value:+.2f} rounds"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis-dir", default="artifacts/evaluations/phase2_v1")
    args = parser.parse_args()
    root = Path(args.analysis_dir)
    integrity = load(root / "run_integrity.json")
    gate_ok = (
        integrity.get("publication_status") == "final"
        and integrity.get("watermark") is None
        and integrity.get("gate_passed") is True
        and integrity.get("gate_errors") == []
        and integrity.get("expected_episodes") == 432
        and integrity.get("observed_episodes") == 432
        and integrity.get("complete_paired_tasks") == 24
        and integrity.get("analysis_algorithm_version") == EXPECTED_ANALYSIS_VERSION
        and integrity.get("no_provider_calls") is True
        and integrity.get("input_mutation") is False
        and integrity.get("evaluator_observations_authenticated_by_replay") is True
        and integrity.get("freeze", {}).get("manifest_sha256")
        == EXPECTED_FREEZE_SHA256
    )
    if not gate_ok:
        raise SystemExit("Phase II final publication gate has not passed")
    contrasts = load(root / "contrasts.json")["rows"]
    paired = load(root / "paired_differences.json")["rows"]
    contrast_index = {(row["contrast"], row["metric"]): row for row in contrasts}
    paired_index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for contrast in CONTRASTS:
        for metric in METRICS:
            paired_index[(contrast, metric)] = [
                row
                for row in paired
                if row["contrast"] == contrast and row["metric"] == metric
            ]

    rows: list[dict[str, Any]] = []
    for contrast_no, contrast in enumerate(CONTRASTS):
        for metric_no, metric in enumerate(METRICS):
            summary = contrast_index[(contrast, metric)]
            if summary["requested_bootstrap_samples"] != BOOTSTRAP_SAMPLES:
                raise SystemExit(f"wrong bootstrap count for {contrast}/{metric}")
            task_values = [
                float(row["difference"])
                for row in paired_index[(contrast, metric)]
                if row["difference"] is not None
            ]
            signs = {
                "positive": sum(value > 1e-12 for value in task_values),
                "zero": sum(abs(value) <= 1e-12 for value in task_values),
                "negative": sum(value < -1e-12 for value in task_values),
            }
            rows.append(
                {
                    "contrast": contrast,
                    "metric": metric,
                    "analysis_status": summary.get("analysis_status", "prespecified"),
                    "direction": summary["direction"],
                    "natural_unit_effect": summary["estimate"],
                    "ci_low": summary["ci_low"],
                    "ci_high": summary["ci_high"],
                    "classification": classification(summary["ci_low"], summary["ci_high"]),
                    "paired_tasks": summary["n_tasks"],
                    "task_signs": signs,
                    "paired_standardized_effect_dz": (
                        None
                        if metric == "rounds_to_success"
                        else paired_dz(task_values, 2026091600 + contrast_no * 100 + metric_no)
                    ),
                    "bootstrap_samples": summary["bootstrap_samples"],
                }
            )

    payload = {
        "format": "fwcollab.symbolic.phase2_statistical_closure.v1",
        "publication_status": "final",
        "primary_effect_size": "task-paired raw difference in the metric's natural unit",
        "secondary_effect_size": "paired standardized mean difference dz with task bootstrap; omitted for conditional median rounds-to-success",
        "interpretation_rule": "A CI crossing or touching zero is inconclusive; it is not evidence of equivalence.",
        "multiplicity": "Exploratory pointwise intervals; no confirmatory multiplicity policy was prespecified.",
        "p_values": "Not reported; paired raw effects and task-bootstrap intervals are the primary inference.",
        "rounds_caveat": "Rounds-to-success is conditional on common successful task/replicate support and has fewer paired tasks.",
        "rows": rows,
    }
    dump(root / "STATISTICAL_CLOSURE.json", payload)

    selected = {
        (row["contrast"], row["metric"]): row for row in rows
    }
    lines = [
        "# Phase II Statistical Closure",
        "",
        "Status: **final**; 432/432 replay-authenticated episodes; 24 task clusters; three aligned replicates per condition.",
        "",
        "Primary effect sizes are paired differences in natural units. CIs are 10,000-draw task-level paired bootstrap intervals after within-task replicate aggregation. A CI touching/crossing zero is reported as inconclusive, never as equivalence.",
        "",
        "| Contrast | Metric | Effect [95% CI] | Paired tasks | Interpretation |",
        "|---|---|---:|---:|---|",
    ]
    for contrast in CONTRASTS:
        for metric in METRICS:
            row = selected[(contrast, metric)]
            effect = (
                f"{fmt(metric, row['natural_unit_effect'])} "
                f"[{fmt(metric, row['ci_low'])}, {fmt(metric, row['ci_high'])}]"
            )
            lines.append(
                f"| `{contrast}` | `{metric}` | {effect} | {row['paired_tasks']} | "
                f"`{row['classification']}` |"
            )
    lines.extend(
        [
            "",
            "## Locked interpretations",
            "",
            "- Gemini SR communication gain is +8.33 pp, but its 95% CI touches zero: promising/borderline, not stable at the 95% level. DAG completion, Progress AUC, and Clean Handoff gains exclude zero.",
            "- GPT communication does not improve SR or AUC. It improves Clean Handoff and reduces coordination violations; DAG completion slightly favors No-Comm.",
            "- Pooled cross-play SR gap is +0.69 pp with a CI spanning zero. State only that no marked degradation was observed; equivalence was not tested or established.",
            "- Direction A modestly favors GPT self-play on DAG/process measures. Direction B favors Gemini(F)+GPT(W) over Gemini self-play on DAG completion, AUC, and Clean Handoff; these are partner-sensitivity comparisons, not a direct A-versus-B test.",
            "- The direct Cross-play A-versus-B contrast is exploratory and post hoc. Its SR effect is zero; the paired intervals for completion, AUC, Clean Handoff, and violations all span zero, so descriptive process-profile differences do not establish a role-assignment effect.",
            "- Rounds-to-success is conditional on common successes (7–10 tasks in the headline contrasts) and must not be interpreted as an unconditional efficiency comparison.",
            "",
        ]
    )
    (root / "STATISTICAL_CLOSURE.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"rows": len(rows), "contrasts": len(CONTRASTS), "metrics": len(METRICS)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
