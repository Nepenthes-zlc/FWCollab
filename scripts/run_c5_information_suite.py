"""Run the frozen four-condition C5 communication experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from fwcollab.c5 import evaluate_c5_trace, run_c5_session
from fwcollab.symbolic.agents import OpenAICompatiblePolicy
from fwcollab.symbolic.dag import load_json, save_json
from fwcollab.symbolic.map import load_symbol_map
from fwcollab.symbolic.runner import save_trace


def _verify_freeze(root: Path, freeze_path: Path) -> None:
    freeze = load_json(freeze_path)
    mismatches = []
    for item in freeze["files"]:
        path = root / item["path"]
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
        if actual != item["sha256"]:
            mismatches.append(item["path"])
    if mismatches:
        raise SystemExit(f"C5 freeze mismatch: {mismatches}")


def _run_episode(root, suite_root, output_root, task, condition, replicate, seed, spec, endpoint, api_key):
    path = output_root / "traces" / condition["id"] / f"replicate_{replicate}" / f"{task['task_id']}.json"
    if path.is_file():
        trace = load_json(path)
        return {
            "condition": condition["id"], "replicate": replicate, "task_id": task["task_id"],
            "outcome": trace["outcome"], "rounds": trace["metrics"]["rounds"],
            "model_calls": trace["metrics"]["total_model_calls"],
            "model_errors": trace["metrics"]["total_model_errors"], "resumed": True,
        }
    started = time.perf_counter()
    try:
        runner = spec["runner"]
        common = {
            "endpoint": endpoint, "api_key": api_key,
            "timeout_seconds": float(runner["request_timeout_seconds"]),
            "max_output_tokens": int(runner["max_output_tokens"]),
            "max_retries": int(runner["max_retries"]),
            "communication_enabled": bool(condition["communication_enabled"]),
            "temperature": float(spec["sampling"]["temperature"]), "seed": seed,
        }
        policies = {
            role: OpenAICompatiblePolicy(**common, model=condition[role]["model"], api_style=condition[role]["api_style"])
            for role in ("F", "W")
        }
        symbol_map = load_symbol_map(suite_root / task["map"])
        result = run_c5_session(symbol_map, policies, task, max_rounds=int(runner["max_rounds"]))
        result.trace["c5"] = {
            "format": spec["format"], "benchmark_version": spec["benchmark_version"],
            "condition": condition["id"], "communication_enabled": condition["communication_enabled"],
            "replicate": replicate, "request_seed": seed, "roles": {role: condition[role] for role in ("F", "W")},
            "dag": task["dag"], "reference_visibility": "private_evaluator_only",
        }
        result.trace["c5_evaluation"] = evaluate_c5_trace(task, result.trace)
        save_trace(path, result.trace)
        return {
            "condition": condition["id"], "replicate": replicate, "task_id": task["task_id"],
            "outcome": result.trace["outcome"], "rounds": result.metrics["rounds"],
            "model_calls": result.metrics["total_model_calls"], "model_errors": result.metrics["total_model_errors"],
            "resumed": False,
        }
    except Exception as exc:
        return {
            "condition": condition["id"], "replicate": replicate, "task_id": task["task_id"],
            "outcome": "runner_error", "rounds": 0, "model_calls": 0, "model_errors": 1,
            "error": f"{type(exc).__name__}: {exc}", "wall_time_ms": round((time.perf_counter() - started) * 1000),
            "resumed": False,
        }


def _write_state(path: Path, rows: list[dict[str, Any]], expected: int) -> None:
    rows.sort(key=lambda row: (row["condition"], row["replicate"], row["task_id"]))
    save_json(path, {
        "format": "fwcollab.c5_run_state.v1", "expected_episodes": expected, "recorded_episodes": len(rows),
        "outcomes": dict(sorted(Counter(row["outcome"] for row in rows).items())),
        "total_model_calls": sum(row["model_calls"] for row in rows),
        "total_model_errors": sum(row["model_errors"] for row in rows), "results": rows,
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", default="eval_private/c5_information_12/experiment_spec.json")
    parser.add_argument("--freeze", default="eval_private/c5_information_12/freeze_manifest.json")
    parser.add_argument("--output-dir", default="artifacts/runs/c5_information_v1")
    parser.add_argument("--endpoint", default="http://127.0.0.1:4141")
    parser.add_argument("--api-key-file")
    parser.add_argument("--condition", action="append")
    parser.add_argument("--replicate", action="append", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int)
    args = parser.parse_args()
    root = Path.cwd()
    _verify_freeze(root, Path(args.freeze))
    spec = load_json(args.spec)
    manifest_path = Path(spec["task_manifest"])
    suite_root = manifest_path.parent
    tasks = load_json(manifest_path)["tasks"]
    conditions = spec["conditions"]
    replicates = spec["replicates"]
    if args.condition:
        wanted = set(args.condition)
        conditions = [item for item in conditions if item["id"] in wanted]
    if args.replicate:
        replicates = [value for value in replicates if value in set(args.replicate)]
    if args.limit is not None:
        tasks = tasks[:args.limit]
    jobs = [(task, condition, replicate) for condition in conditions for task in tasks for replicate in replicates]
    if not jobs:
        raise SystemExit("empty C5 episode matrix")
    api_key = Path(args.api_key_file).read_text(encoding="utf-8").strip() if args.api_key_file else None
    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    state_path = output_root / "run_state.json"
    rows = []
    concurrency = args.concurrency or int(spec["runner"]["episode_concurrency"])
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="c5") as executor:
        futures = {
            executor.submit(_run_episode, root, suite_root, output_root, task, condition, replicate,
                            int(spec["request_seeds"][str(replicate)]), spec, args.endpoint, api_key):
            (condition["id"], replicate, task["task_id"])
            for task, condition, replicate in jobs
        }
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            _write_state(state_path, rows, len(jobs))
            print(f"[{len(rows):03d}/{len(jobs):03d}] {row['condition']} r{row['replicate']} {row['task_id']} {row['outcome']}", flush=True)
    state = load_json(state_path)
    print(json.dumps({key: state[key] for key in ("expected_episodes", "recorded_episodes", "outcomes", "total_model_calls", "total_model_errors")}, ensure_ascii=False))
    return 2 if "runner_error" in state["outcomes"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
