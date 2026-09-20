"""Select a deterministic, structure-diverse diagnostic subset from the V4 tasks."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

from fwcollab.symbolic.collaboration_eval import collaboration_taxonomy
from fwcollab.symbolic.dag import load_json, save_json

DEFAULT_QUOTAS = {"L1": 2, "L2": 3, "L3": 3, "L4": 4, "L5": 4, "L6": 4, "L7": 4}


def _features(record: dict[str, Any], taxonomy: dict[str, Any]) -> set[str]:
    stages = record["stages"]
    controller_pattern = ">".join(
        "+".join(sorted(controller["kind"] for controller in stage["controllers"]))
        for stage in stages
    )
    mode_pattern = ">".join(stage["mode"] for stage in stages)
    features = {
        *(f"taxonomy:{tag}" for tag in taxonomy["tags"]),
        f"stage_count:{taxonomy['stage_count']}",
        f"controller_pattern:{controller_pattern}",
        f"mode_pattern:{mode_pattern}",
        f"primary_capability:{record['primary_capability']}",
    }
    if taxonomy["has_any_join"]:
        features.add("join:any")
    if taxonomy["has_multi_controller_join"]:
        features.add("join:multi_controller")
    return features


def _select(records: list[dict[str, Any]], quotas: dict[str, int]) -> list[dict[str, Any]]:
    enriched = []
    for record in records:
        dag = load_json(record["dag"])
        taxonomy = collaboration_taxonomy(record, dag)
        enriched.append(
            {
                "record": record,
                "taxonomy": taxonomy,
                "features": _features(record, taxonomy),
            }
        )
    selected: list[dict[str, Any]] = []
    covered: set[str] = set()
    for level, quota in quotas.items():
        candidates = [item for item in enriched if item["record"]["difficulty"] == level]
        for _ in range(quota):
            if not candidates:
                raise ValueError(f"not enough candidates for {level}")
            winner = max(
                candidates,
                key=lambda item: (
                    5 * len({feature for feature in item["features"] - covered if feature.startswith("taxonomy:")}),
                    3 * len({feature for feature in item["features"] - covered if feature.startswith("join:")}),
                    2 * len({feature for feature in item["features"] - covered if feature.startswith("controller_pattern:")}),
                    len(item["features"] - covered),
                    item["taxonomy"]["plate_stages"],
                    item["taxonomy"]["role_alternations"],
                    item["record"]["id"],
                ),
            )
            candidates.remove(winner)
            covered.update(winner["features"])
            selected.append(winner)
    return selected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest", default="eval_private/spatial_curriculum_full_v4/manifest.json"
    )
    parser.add_argument(
        "--output", default="eval_private/collaboration_diagnostic_24/manifest.json"
    )
    args = parser.parse_args()
    manifest = load_json(args.manifest)
    records = list(manifest["records"])
    selected = _select(records, DEFAULT_QUOTAS)
    full_taxonomy = [
        collaboration_taxonomy(record, load_json(record["dag"])) for record in records
    ]
    selected_taxonomy = [item["taxonomy"] for item in selected]

    def tag_counts(items: list[dict[str, Any]]) -> dict[str, int]:
        counts = Counter(tag for item in items for tag in item["tags"])
        return {
            tag: counts[tag]
            for tag in (
                "C1_hold_and_pass",
                "C2_sequential_handoff",
                "C3_mutual_unlock",
                "C4_synchronized_action",
                "C5_information_dependent",
                "C6_multi_stage_alternating",
                "C7_parallel_subgoal_merge",
            )
        }

    output = {
        "format": "fwcollab.symbolic.collaboration_diagnostic_set.v1",
        "source_manifest": Path(args.manifest).as_posix(),
        "selection_policy": {
            "uses_model_outcomes": False,
            "target_size": sum(DEFAULT_QUOTAS.values()),
            "difficulty_quotas": DEFAULT_QUOTAS,
            "objective": "maximize collaboration taxonomy, join, and controller-pattern diversity",
        },
        "full_benchmark_taxonomy_coverage": tag_counts(full_taxonomy),
        "diagnostic_taxonomy_coverage": tag_counts(selected_taxonomy),
        "known_coverage_gaps": [
            "C4_synchronized_action",
            "C5_information_dependent",
            "C7_parallel_subgoal_merge",
        ],
        "records": [
            {
                "id": item["record"]["id"],
                "difficulty": item["record"]["difficulty"],
                "map": item["record"]["map"],
                "dag": item["record"]["dag"],
                "primary_capability": item["record"]["primary_capability"],
                "taxonomy": item["taxonomy"],
                "selection_features": sorted(item["features"]),
            }
            for item in selected
        ],
    }
    save_json(args.output, output)
    print(
        {
            "selected": len(selected),
            "coverage": output["diagnostic_taxonomy_coverage"],
            "gaps": output["known_coverage_gaps"],
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

