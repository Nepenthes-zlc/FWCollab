"""Run the frozen six-condition Phase II causal collaboration experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from fwcollab.symbolic.agents import OpenAICompatiblePolicy
from fwcollab.symbolic.dag import load_json, save_json
from fwcollab.symbolic.map import load_symbol_map
from fwcollab.symbolic.runner import DualAgentSession, save_trace


def _verify_freeze(root: Path, freeze_path: Path) -> None:
    freeze = load_json(freeze_path)
    mismatches: list[str] = []
    for item in freeze["files"]:
        path = root / item["path"]
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
        if actual != item["sha256"]:
            mismatches.append(item["path"])
    if mismatches:
        raise SystemExit(f"benchmark v1 freeze mismatch: {mismatches}")


def _trace_path(root: Path, condition: str, replicate: int, record: dict[str, Any]) -> Path:
    return root / "traces" / condition / f"replicate_{replicate}" / record["difficulty"] / f"{record['id']}.json"


def _existing_result(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        trace = load_json(path)
        phase2 = trace["phase2"]
        return {
            "condition": phase2["condition"],
            "replicate": phase2["replicate"],
            "task_id": trace["map_id"],
            "difficulty": phase2["difficulty"],
            "outcome": trace["outcome"],
            "rounds": trace["metrics"]["rounds"],
            "model_calls": trace["metrics"]["total_model_calls"],
            "model_errors": trace["metrics"]["total_model_errors"],
            "messages": trace["metrics"]["total_messages"],
            "wall_time_ms": trace["wall_time_ms"],
            "trace": path.as_posix(),
            "resumed": True,
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _run_episode(
    *,
    record: dict[str, Any],
    condition: dict[str, Any],
    replicate: int,
    request_seed: int,
    spec: dict[str, Any],
    endpoint: str,
    api_key: str | None,
    output_root: Path,
) -> dict[str, Any]:
    path = _trace_path(output_root, condition["id"], replicate, record)
    existing = _existing_result(path)
    if existing is not None:
        return existing
    started = time.perf_counter()
    runner_spec = spec["runner"]
    try:
        common = {
            "endpoint": endpoint,
            "api_key": api_key,
            "timeout_seconds": float(runner_spec["request_timeout_seconds"]),
            "max_output_tokens": int(runner_spec["max_output_tokens"]),
            "max_retries": int(runner_spec["max_retries"]),
            "communication_enabled": bool(condition["communication_enabled"]),
            "temperature": float(spec["sampling"]["temperature"]),
            "seed": request_seed,
        }
        policies = {
            role: OpenAICompatiblePolicy(
                **common,
                model=condition[role]["model"],
                api_style=condition[role]["api_style"],
            )
            for role in ("F", "W")
        }
        session = DualAgentSession(
            load_symbol_map(record["map"]),
            policies,
            max_rounds=int(runner_spec["max_rounds"]),
            planning_rounds=int(runner_spec["planning_rounds"]),
            coordination_mode=runner_spec["coordination_mode"],
        )
        result = session.run()
        result.trace["phase2"] = {
            "format": spec["format"],
            "benchmark_version": spec["benchmark_version"],
            "condition": condition["id"],
            "condition_label": condition["label"],
            "communication_enabled": condition["communication_enabled"],
            "replicate": replicate,
            "request_seed": request_seed,
            "seed_reproducibility_guaranteed": spec["sampling"]["seed_reproducibility_guaranteed"],
            "temperature": spec["sampling"]["temperature"],
            "difficulty": record["difficulty"],
            "dag": record["dag"],
            "roles": {role: dict(condition[role]) for role in ("F", "W")},
        }
        save_trace(path, result.trace)
        return {
            "condition": condition["id"],
            "replicate": replicate,
            "task_id": record["id"],
            "difficulty": record["difficulty"],
            "outcome": result.trace["outcome"],
            "rounds": result.metrics["rounds"],
            "model_calls": result.metrics["total_model_calls"],
            "model_errors": result.metrics["total_model_errors"],
            "messages": result.metrics["total_messages"],
            "wall_time_ms": result.trace["wall_time_ms"],
            "trace": path.as_posix(),
            "resumed": False,
        }
    except Exception as exc:
        return {
            "condition": condition["id"],
            "replicate": replicate,
            "task_id": record["id"],
            "difficulty": record["difficulty"],
            "outcome": "runner_error",
            "rounds": 0,
            "model_calls": 0,
            "model_errors": 1,
            "messages": 0,
            "wall_time_ms": round((time.perf_counter() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
            "trace": path.as_posix(),
            "resumed": False,
        }


def _write_state(output_root: Path, spec_path: Path, rows: list[dict[str, Any]], expected: int) -> None:
    rows.sort(key=lambda item: (item["condition"], item["replicate"], item["task_id"]))
    state = {
        "format": "fwcollab.symbolic.phase2_run_state.v1",
        "experiment_spec": spec_path.as_posix(),
        "expected_episodes": expected,
        "recorded_episodes": len(rows),
        "outcomes": dict(sorted(Counter(row["outcome"] for row in rows).items())),
        "total_model_calls": sum(int(row["model_calls"]) for row in rows),
        "total_model_errors": sum(int(row["model_errors"]) for row in rows),
        "results": rows,
    }
    save_json(output_root / "run_state.json", state)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", default="eval_private/phase2_v1/experiment_spec.json")
    parser.add_argument("--freeze", default="eval_private/phase2_v1/freeze_manifest.json")
    parser.add_argument("--output-dir", default="artifacts/runs/phase2_v1")
    parser.add_argument("--endpoint", default="http://127.0.0.1:4141")
    parser.add_argument("--api-key-file")
    parser.add_argument("--condition", action="append")
    parser.add_argument("--replicate", action="append", type=int)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int)
    args = parser.parse_args()

    root = Path.cwd()
    spec_path = Path(args.spec)
    _verify_freeze(root, Path(args.freeze))
    spec = load_json(spec_path)
    diagnostic = load_json(spec["task_manifest"])
    records = list(diagnostic["records"])
    conditions = list(spec["conditions"])
    replicates = list(spec["replicates"])
    if args.condition:
        wanted = set(args.condition)
        conditions = [item for item in conditions if item["id"] in wanted]
        missing = wanted - {item["id"] for item in conditions}
        if missing:
            raise SystemExit(f"unknown conditions: {sorted(missing)}")
    if args.replicate:
        wanted_replicates = set(args.replicate)
        replicates = [item for item in replicates if item in wanted_replicates]
    if args.limit is not None:
        records = records[: args.limit]
    if not conditions or not replicates or not records:
        raise SystemExit("empty Phase II episode matrix")

    api_key = Path(args.api_key_file).read_text(encoding="utf-8").strip() if args.api_key_file else None
    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    jobs = [
        (record, condition, replicate)
        for condition in conditions
        for record in records
        for replicate in replicates
    ]
    rows: list[dict[str, Any]] = []
    concurrency = args.concurrency or int(spec["runner"]["episode_concurrency"])
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="phase2") as executor:
        futures = {
            executor.submit(
                _run_episode,
                record=record,
                condition=condition,
                replicate=replicate,
                request_seed=int(spec["request_seeds"][str(replicate)]),
                spec=spec,
                endpoint=args.endpoint,
                api_key=api_key,
                output_root=output_root,
            ): (condition["id"], replicate, record["id"])
            for record, condition, replicate in jobs
        }
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            _write_state(output_root, spec_path, rows, len(jobs))
            print(
                f"[{len(rows):03d}/{len(jobs):03d}] {row['condition']} r{row['replicate']} "
                f"{row['task_id']} {row['outcome']} rounds={row['rounds']} "
                f"errors={row['model_errors']}{' resumed' if row['resumed'] else ''}",
                flush=True,
            )
    state = load_json(output_root / "run_state.json")
    state["wall_time_ms"] = round((time.perf_counter() - started) * 1000)
    save_json(output_root / "run_state.json", state)
    print(json.dumps({key: state[key] for key in ("expected_episodes", "recorded_episodes", "outcomes", "total_model_calls", "total_model_errors", "wall_time_ms")}, ensure_ascii=False))
    return 0 if "runner_error" not in state["outcomes"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
