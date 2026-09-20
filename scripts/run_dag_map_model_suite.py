"""Run two independent real-model sessions over audited DAG-map prototypes."""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from fwcollab.symbolic.agents import OpenAICompatiblePolicy
from fwcollab.symbolic.map import load_symbol_map
from fwcollab.symbolic.runner import DualAgentSession, save_trace


def _run_one(
    record: dict[str, Any],
    *,
    output_dir: Path,
    endpoint: str,
    model: str,
    api_key: str | None,
    max_rounds: int,
    request_timeout: float,
    planning_rounds: int,
    coordination_mode: str,
    api_style: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    dag_id = str(record.get("dag_id", record.get("id")))
    try:
        symbol_map = load_symbol_map(record["map"])
        common = {
            "endpoint": endpoint,
            "model": model,
            "api_key": api_key,
            "timeout_seconds": request_timeout,
            "max_output_tokens": 800,
            "max_retries": 1,
            "api_style": api_style,
        }
        policies = {
            "F": OpenAICompatiblePolicy(**common),
            "W": OpenAICompatiblePolicy(**common),
        }
        result = DualAgentSession(
            symbol_map,
            policies,
            max_rounds=max_rounds,
            planning_rounds=planning_rounds,
            coordination_mode=coordination_mode,
        ).run()
        trace_path = output_dir / record["difficulty"] / f"{dag_id}.json"
        save_trace(trace_path, result.trace)
        return {
            "dag_id": dag_id,
            "difficulty": record["difficulty"],
            "grounding_status": record.get("grounding_status", record.get("status", "unknown")),
            "template_map": record.get("template_map", record.get("id", dag_id)),
            "outcome": result.trace["outcome"],
            "rounds": result.metrics["rounds"],
            "model_calls": result.metrics["total_model_calls"],
            "model_errors": result.metrics["total_model_errors"],
            "messages": result.metrics["total_messages"],
            "wall_time_ms": result.trace["wall_time_ms"],
            "trace": str(trace_path.as_posix()),
        }
    except Exception as exc:  # Provider/schema failures must not abort the suite.
        return {
            "dag_id": dag_id,
            "difficulty": record.get("difficulty"),
            "grounding_status": record.get("grounding_status"),
            "template_map": record.get("template_map"),
            "outcome": "runner_error",
            "rounds": 0,
            "model_calls": 0,
            "model_errors": 1,
            "messages": 0,
            "wall_time_ms": round((time.perf_counter() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="eval_private/spatial_curriculum_full_v4/manifest.json")
    parser.add_argument("--output-dir", default="artifacts/runs/spatial_curriculum_full_v4_model_next")
    parser.add_argument("--endpoint", default="http://127.0.0.1:4141")
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument(
        "--api-style", choices=("responses", "chat_completions"), default="responses"
    )
    parser.add_argument("--api-key-file")
    parser.add_argument("--max-rounds", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--request-timeout", type=float, default=120.0)
    parser.add_argument("--planning-rounds", type=int, choices=(0, 1, 2), default=0)
    parser.add_argument(
        "--coordination-mode", choices=("emergent", "protocol_assisted"), default="emergent"
    )
    parser.add_argument("--coverage", choices=("all", "full"), default="all")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--one-per-level", action="store_true")
    parser.add_argument(
        "--witness-multiplier",
        type=float,
        default=0.0,
        help="raise each map budget to ceil(witness_rounds * multiplier), keeping --max-rounds as the floor",
    )
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    records = list(manifest["records"])
    if args.coverage == "full":
        records = [
            record
            for record in records
            if record.get("grounding_status") == "full_requested_coverage"
            or record.get("status") == "full_mechanism_spatial_verified"
        ]
    if args.one_per_level:
        selected = {}
        for record in records:
            selected.setdefault(record["difficulty"], record)
        records = [selected[level] for level in sorted(selected, key=lambda item: int(item[1:]))]
    if args.limit is not None:
        records = records[: args.limit]
    if not records:
        raise SystemExit("no maps selected")
    api_key = Path(args.api_key_file).read_text(encoding="utf-8").strip() if args.api_key_file else None
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    suite_started = time.perf_counter()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.concurrency, thread_name_prefix="dag-map") as executor:
        futures = {
            executor.submit(
                _run_one,
                record,
                output_dir=output_dir,
                endpoint=args.endpoint,
                model=args.model,
                api_key=api_key,
                max_rounds=max(
                    args.max_rounds,
                    math.ceil(float(record.get("witness_rounds", 0)) * args.witness_multiplier),
                ),
                request_timeout=args.request_timeout,
                planning_rounds=args.planning_rounds,
                coordination_mode=args.coordination_mode,
                api_style=args.api_style,
            ): record
            for record in records
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(
                f"[{len(results):02d}/{len(records):02d}] {result['dag_id']} "
                f"{result['outcome']} rounds={result['rounds']} errors={result['model_errors']}",
                flush=True,
            )

    results.sort(key=lambda item: item["dag_id"])
    outcomes = Counter(item["outcome"] for item in results)
    by_level: dict[str, dict[str, int]] = {}
    for level in sorted({str(item["difficulty"]) for item in results}):
        subset = [item for item in results if item["difficulty"] == level]
        counts = Counter(item["outcome"] for item in subset)
        by_level[level] = {"maps": len(subset), **dict(sorted(counts.items()))}
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in results:
        grouped[str(item["grounding_status"])].append(item)
    by_grounding = {}
    for status, subset in sorted(grouped.items()):
        counts = Counter(item["outcome"] for item in subset)
        by_grounding[status] = {"maps": len(subset), **dict(sorted(counts.items()))}
    summary = {
        "format": "fwcollab.symbolic.dag_map_model_pilot.v1",
        "model": args.model,
        "api_style": args.api_style,
        "two_independent_sessions": True,
        "max_rounds": args.max_rounds,
        "concurrency": args.concurrency,
        "planning_rounds": args.planning_rounds,
        "coordination_mode": args.coordination_mode,
        "selected_coverage": args.coverage,
        "maps": len(results),
        "outcomes": dict(sorted(outcomes.items())),
        "by_difficulty": by_level,
        "by_grounding_status": by_grounding,
        "total_model_calls": sum(int(item["model_calls"]) for item in results),
        "total_model_errors": sum(int(item["model_errors"]) for item in results),
        "wall_time_ms": round((time.perf_counter() - suite_started) * 1000),
        "results": results,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = [
        "# 72-DAG 符号地图双模型试跑",
        "",
        f"- 模型：`{args.model}` × 2 个独立会话",
        f"- 地图：{len(results)}；每图最多 {args.max_rounds} 轮；并发 {args.concurrency}",
        f"- 结果：{dict(sorted(outcomes.items()))}",
        f"- 模型调用：{summary['total_model_calls']}；模型错误：{summary['total_model_errors']}",
        f"- 墙钟时间：{summary['wall_time_ms'] / 1000:.3f} 秒",
        "",
        "当前默认输入是72张V4全机制空间任务；私有参考DAG和见证不会进入模型观察。",
        "",
        "| 难度 | 地图 | 成功 | 超时 | 运行错误 |",
        "|---|---:|---:|---:|---:|",
    ]
    for level, row in by_level.items():
        report.append(
            f"| {level} | {row['maps']} | {row.get('team_success', 0)} | "
            f"{row.get('timeout', 0)} | {row.get('runner_error', 0)} |"
        )
    (output_dir / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {key: summary[key] for key in ("maps", "outcomes", "total_model_calls", "total_model_errors", "wall_time_ms")},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
