"""Evaluate one or more existing V4 model runs without calling a model."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from fwcollab.symbolic.collaboration_eval import evaluate_collaboration_trace
from fwcollab.symbolic.dag import load_json, save_json


def _parse_run(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--run must use NAME=PATH")
    name, raw_path = value.split("=", 1)
    if not name.strip() or not raw_path.strip():
        raise argparse.ArgumentTypeError("--run must use non-empty NAME=PATH")
    return name.strip(), Path(raw_path)


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("_") or "run"


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summaries = [row["summary"] for row in rows]
    handoff_opportunities = sum(int(item["handoff_opportunities"]) for item in summaries)
    handoff_successes = sum(int(item["handoff_successes"]) for item in summaries)
    clean_handoffs = sum(
        sum(1 for item in row["handoffs"] if item["clean"])
        for row in rows
    )
    total_waits = sum(int(item["total_waits"]) for item in summaries)
    useful_waits = sum(int(item["useful_waits"]) for item in summaries)
    return {
        "maps": len(rows),
        "outcomes": dict(sorted(Counter(row["outcome"] for row in rows).items())),
        "dag_completion_mean": round(
            sum(float(item["dag_completion"]) for item in summaries) / len(rows), 6
        ),
        "dag_progress_auc_mean": round(
            sum(float(item["dag_progress_auc"]) for item in summaries) / len(rows), 6
        ),
        "dependency_violations": sum(int(item["dependency_violations"]) for item in summaries),
        "node_regressions": sum(int(item["node_regressions"]) for item in summaries),
        "harmful_node_regressions": sum(
            int(item["harmful_node_regressions"]) for item in summaries
        ),
        "handoff_opportunities": handoff_opportunities,
        "handoff_successes": handoff_successes,
        "handoff_success_rate": round(handoff_successes / handoff_opportunities, 6)
        if handoff_opportunities
        else None,
        "clean_handoffs": clean_handoffs,
        "clean_handoff_rate": round(clean_handoffs / handoff_opportunities, 6)
        if handoff_opportunities
        else None,
        "coordination_violations": sum(
            int(item["coordination_violations"]) for item in summaries
        ),
        "useful_hold_rounds": sum(int(item["useful_hold_rounds"]) for item in summaries),
        "useful_waits": useful_waits,
        "total_waits": total_waits,
        "useful_wait_ratio": round(useful_waits / total_waits, 6) if total_waits else None,
    }


def _evaluate_run(
    name: str,
    run_dir: Path,
    records: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    detail_root = output_dir / _slug(name)
    for record in records:
        trace_path = run_dir / record["difficulty"] / f"{record['id']}.json"
        trace = load_json(trace_path)
        dag = load_json(record["dag"])
        evaluation = evaluate_collaboration_trace(record, dag, trace)
        save_json(detail_root / record["difficulty"] / f"{record['id']}.json", evaluation)
        rows.append(evaluation)
    by_difficulty: dict[str, Any] = {}
    groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["difficulty"]].append(row)
    for difficulty, subset in sorted(groups.items()):
        by_difficulty[difficulty] = _aggregate(subset)
    return {
        "name": name,
        "run_dir": run_dir.as_posix(),
        **_aggregate(rows),
        "by_difficulty": by_difficulty,
        "failures": [
            {
                "task_id": row["task_id"],
                "outcome": row["outcome"],
                "failure_analysis": row["failure_analysis"],
            }
            for row in rows
            if row["outcome"] != "team_success"
        ],
        "details_dir": detail_root.as_posix(),
    }


def _report(summary: dict[str, Any]) -> str:
    lines = [
        "# DAG-based Collaboration Evaluation",
        "",
        "所有指标由私有DAG、权威状态和动作记录确定性计算；没有使用LLM judge。",
        "DAG Completion不计永真start节点；AUC为每条episode自身轮数上的归一化梯形积分。",
        "",
        "| Run | Maps | Success | DAG completion | Progress AUC | Handoff | Clean handoff | Dependency violations | Coordination violations | Useful wait |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for run in summary["runs"]:
        success = int(run["outcomes"].get("team_success", 0))
        lines.append(
            f"| {run['name']} | {run['maps']} | {success}/{run['maps']} | "
            f"{run['dag_completion_mean']:.3f} | {run['dag_progress_auc_mean']:.3f} | "
            f"{run['handoff_successes']}/{run['handoff_opportunities']} | "
            f"{run['clean_handoffs']}/{run['handoff_opportunities']} | "
            f"{run['dependency_violations']} | {run['coordination_violations']} | "
            f"{run['useful_wait_ratio']:.3f} |"
        )
    lines.extend(
        [
            "",
            "限制：dependency violation目前只计算可由动作或状态客观确认的提前谓词/关门穿越尝试；",
            "消息语义、IC/RC和因果Communication Gain不在v1中。",
            "",
        ]
    )
    for run in summary["runs"]:
        if not run["failures"]:
            continue
        lines.extend((f"## {run['name']} failure stages", ""))
        for failure in run["failures"]:
            analysis = failure["failure_analysis"]
            lines.append(
                f"- `{failure['task_id']}` `{failure['outcome']}`: "
                f"progress={analysis['progress']:.3f}; first incomplete="
                f"`{analysis['first_incomplete_required_node']}` "
                f"(`{analysis['predicate_state']}`); related coordination violations="
                f"{len(analysis['related_coordination_violations'])}."
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest", default="eval_private/spatial_curriculum_full_v4/manifest.json"
    )
    parser.add_argument("--run", action="append", type=_parse_run, required=True)
    parser.add_argument(
        "--output-dir", default="artifacts/evaluations/collaboration_v1_20260914"
    )
    args = parser.parse_args()

    manifest = load_json(args.manifest)
    records = list(manifest["records"])
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    runs = [
        _evaluate_run(name, run_dir, records, output_dir)
        for name, run_dir in args.run
    ]
    summary = {
        "format": "fwcollab.symbolic.collaboration_suite.v1",
        "manifest": Path(args.manifest).as_posix(),
        "maps": len(records),
        "runs": runs,
    }
    save_json(output_dir / "summary.json", summary)
    (output_dir / "REPORT.md").write_text(_report(summary), encoding="utf-8")
    print(
        json.dumps(
            {
                "maps": len(records),
                "runs": [
                    {
                        "name": item["name"],
                        "outcomes": item["outcomes"],
                        "dag_completion_mean": item["dag_completion_mean"],
                        "dag_progress_auc_mean": item["dag_progress_auc_mean"],
                        "handoff_success_rate": item["handoff_success_rate"],
                        "coordination_violations": item["coordination_violations"],
                    }
                    for item in runs
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
