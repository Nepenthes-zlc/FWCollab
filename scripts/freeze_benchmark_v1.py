"""Create or verify the immutable Full-72 Benchmark v1 asset freeze."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from fwcollab.symbolic.dag import load_json, validate_state_dag


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path("eval_private/benchmark_v1/freeze_manifest.json")
FULL_MANIFEST = Path("eval_private/spatial_curriculum_full_v4/manifest.json")
DIAGNOSTIC_MANIFEST = Path("eval_private/collaboration_diagnostic_24/manifest.json")
PHASE2_FREEZE = Path("eval_private/phase2_v1/freeze_manifest.json")

FREEZE_FORMAT = "fwcollab.symbolic.benchmark_freeze.v1"
BENCHMARK_VERSION = "v1"
SCOPE = "full_72_task_benchmark"
EXPECTED_TASK_COUNT = 72
EXPECTED_DIAGNOSTIC_COUNT = 24
EXPECTED_PHASE2_FILE_COUNT = 61
EXPECTED_CONDITION_COUNT = 6
EXPECTED_REPLICATE_COUNT = 3
EXPECTED_EPISODE_COUNT = 432
REQUIRED_SELECTION_STATEMENT = (
    "The diagnostic subset was selected solely from task structure and collaboration "
    "primitives, without access to model performance."
)
EXPECTED_DIFFICULTY_DISTRIBUTION = {
    "L1": 4,
    "L2": 8,
    "L3": 10,
    "L4": 12,
    "L5": 14,
    "L6": 14,
    "L7": 10,
}
EXPECTED_MECHANISMS = [f"M{number:02d}" for number in range(1, 31)]
EXPECTED_DIAGNOSTIC_QUOTAS = {
    "L1": 2,
    "L2": 3,
    "L3": 3,
    "L4": 4,
    "L5": 4,
    "L6": 4,
    "L7": 4,
}


class FreezeError(ValueError):
    """Raised when a benchmark freeze or one of its sources is invalid."""


def _output_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _relative_path(value: str | Path, field: str) -> str:
    raw = str(value).replace("\\", "/")
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.parts or ".." in path.parts or "." in path.parts:
        raise FreezeError(f"{field} must be a normalized repository-relative path: {raw!r}")
    normalized = path.as_posix()
    if normalized != raw:
        raise FreezeError(f"{field} must use normalized POSIX separators: {raw!r}")
    return normalized


def _repository_file(root: Path, value: str | Path, field: str) -> tuple[str, Path]:
    relative = _relative_path(value, field)
    root_resolved = root.resolve()
    target = (root_resolved / Path(*PurePosixPath(relative).parts)).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise FreezeError(f"{field} escapes repository root: {relative}") from exc
    if not target.is_file():
        raise FreezeError(f"{field} does not exist as a file: {relative}")
    return relative, target


def _file_record(root: Path, value: str | Path, field: str = "path") -> dict[str, Any]:
    relative, target = _repository_file(root, value, field)
    content = target.read_bytes()
    return {
        "path": relative,
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def _require_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FreezeError(f"{field} must be an object")
    return value


def _require_records(value: object, field: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise FreezeError(f"{field} must be an array of objects")
    return list(value)


def _verify_file_record(root: Path, record: Mapping[str, Any], field: str) -> None:
    if set(record) != {"path", "sha256", "bytes"}:
        raise FreezeError(f"{field} must contain exactly path, sha256, and bytes")
    actual = _file_record(root, record["path"], f"{field}.path")
    if dict(record) != actual:
        raise FreezeError(
            f"{field} hash or size mismatch for {record.get('path')!r}: "
            f"expected sha256={record.get('sha256')} bytes={record.get('bytes')}, "
            f"actual sha256={actual['sha256']} bytes={actual['bytes']}"
        )


def verify_phase2_freeze(root: Path = ROOT) -> dict[str, Any]:
    """Verify the linked 61-file Phase II freeze without changing it."""

    relative, path = _repository_file(root, PHASE2_FREEZE.as_posix(), "phase2 freeze")
    manifest = load_json(path)
    if manifest.get("format") != FREEZE_FORMAT:
        raise FreezeError("Phase II freeze has an unsupported format")
    if manifest.get("benchmark_version") != BENCHMARK_VERSION:
        raise FreezeError("Phase II freeze has an unexpected benchmark version")
    if manifest.get("statement") != REQUIRED_SELECTION_STATEMENT:
        raise FreezeError("Phase II freeze does not preserve the required selection statement")
    expected_metadata = {
        "task_count": EXPECTED_DIAGNOSTIC_COUNT,
        "condition_count": EXPECTED_CONDITION_COUNT,
        "replicates": EXPECTED_REPLICATE_COUNT,
        "expected_episodes": EXPECTED_EPISODE_COUNT,
    }
    for field, expected in expected_metadata.items():
        if manifest.get(field) != expected:
            raise FreezeError(
                f"Phase II freeze {field} must be {expected}, found {manifest.get(field)!r}"
            )
    files = _require_records(manifest.get("files"), "Phase II freeze files")
    if len(files) != EXPECTED_PHASE2_FILE_COUNT:
        raise FreezeError(
            f"Phase II freeze must contain {EXPECTED_PHASE2_FILE_COUNT} files, found {len(files)}"
        )
    paths = [str(item.get("path")) for item in files]
    if len(paths) != len(set(paths)):
        raise FreezeError("Phase II freeze contains duplicate file paths")
    if paths != sorted(paths):
        raise FreezeError("Phase II freeze file paths are not sorted")
    for index, record in enumerate(files):
        _verify_file_record(root, record, f"Phase II freeze files[{index}]")
    link = _file_record(root, relative, "phase2 freeze")
    link.update(
        {
            "declared_file_count": len(files),
            "task_count": manifest["task_count"],
            "condition_count": manifest["condition_count"],
            "replicates": manifest["replicates"],
            "expected_episodes": manifest["expected_episodes"],
        }
    )
    return link


def _load_source_manifests(
    root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    full_relative, full_path = _repository_file(root, FULL_MANIFEST.as_posix(), "full manifest")
    diagnostic_relative, diagnostic_path = _repository_file(
        root, DIAGNOSTIC_MANIFEST.as_posix(), "diagnostic manifest"
    )
    full = load_json(full_path)
    diagnostic = load_json(diagnostic_path)
    links = {
        "full_benchmark": _file_record(root, full_relative, "full manifest"),
        "diagnostic_subset": _file_record(root, diagnostic_relative, "diagnostic manifest"),
    }
    return full, diagnostic, links


def _validate_sources(
    root: Path, full: Mapping[str, Any], diagnostic: Mapping[str, Any]
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    if full.get("format") != "fwcollab.symbolic.full_spatial_curriculum.v1":
        raise FreezeError("full benchmark manifest has an unsupported format")
    records = _require_records(full.get("records"), "full manifest records")
    if full.get("count") != EXPECTED_TASK_COUNT or len(records) != EXPECTED_TASK_COUNT:
        raise FreezeError(
            f"full benchmark must contain {EXPECTED_TASK_COUNT} records; "
            f"declared={full.get('count')!r}, actual={len(records)}"
        )
    distribution = dict(sorted(Counter(str(item.get("difficulty")) for item in records).items()))
    if distribution != EXPECTED_DIFFICULTY_DISTRIBUTION:
        raise FreezeError(f"full benchmark difficulty distribution mismatch: {distribution}")
    if full.get("difficulty_distribution") != EXPECTED_DIFFICULTY_DISTRIBUTION:
        raise FreezeError("full manifest declared difficulty distribution is inconsistent")
    if full.get("semantic_unique") != EXPECTED_TASK_COUNT:
        raise FreezeError("full manifest must declare 72 unique spatial semantic signatures")
    if full.get("topology_unique") != EXPECTED_TASK_COUNT:
        raise FreezeError("full manifest must declare 72 unique spatial topology signatures")
    if full.get("mechanism_coverage") != EXPECTED_MECHANISMS:
        raise FreezeError("full benchmark must cover M01 through M30 exactly")

    required_record_fields = {
        "id",
        "difficulty",
        "map",
        "dag",
        "primary_capability",
        "stages",
        "node_bindings",
        "all_nodes_bound",
        "all_edges_verified",
        "capability_verified",
        "status",
    }
    task_ids: set[str] = set()
    map_paths: set[str] = set()
    dag_paths: set[str] = set()
    tasks: list[dict[str, str]] = []
    files: list[dict[str, Any]] = []
    full_by_id: dict[str, Mapping[str, Any]] = {}
    for index, record in enumerate(records):
        missing = required_record_fields - set(record)
        if missing:
            raise FreezeError(f"full record {index} is missing fields: {sorted(missing)}")
        task_id = str(record["id"])
        difficulty = str(record["difficulty"])
        if not task_id or task_id in task_ids:
            raise FreezeError(f"duplicate or empty full benchmark task id: {task_id!r}")
        if difficulty not in EXPECTED_DIFFICULTY_DISTRIBUTION:
            raise FreezeError(f"invalid difficulty for {task_id}: {difficulty!r}")
        if not all(
            record.get(field) is True
            for field in ("all_nodes_bound", "all_edges_verified", "capability_verified")
        ):
            raise FreezeError(f"task {task_id} is missing a required verification invariant")
        if record.get("status") != "full_mechanism_spatial_verified":
            raise FreezeError(f"task {task_id} is not marked full_mechanism_spatial_verified")

        map_relative, _ = _repository_file(root, record["map"], f"task {task_id} map")
        dag_relative, dag_path = _repository_file(root, record["dag"], f"task {task_id} DAG")
        if not map_relative.endswith(".fwmap"):
            raise FreezeError(f"task {task_id} map path must end in .fwmap")
        if not dag_relative.endswith(".json"):
            raise FreezeError(f"task {task_id} DAG path must end in .json")
        if map_relative in map_paths or dag_relative in dag_paths:
            raise FreezeError(f"task {task_id} reuses a map or DAG path")

        dag = load_json(dag_path)
        try:
            validate_state_dag(dag)
        except (TypeError, ValueError) as exc:
            raise FreezeError(f"task {task_id} has an invalid DAG: {exc}") from exc
        if dag.get("id") != f"DAG-{task_id}":
            raise FreezeError(f"task {task_id} DAG id does not match its task id")
        if dag.get("difficulty") != difficulty:
            raise FreezeError(f"task {task_id} DAG difficulty mismatch")

        task_ids.add(task_id)
        map_paths.add(map_relative)
        dag_paths.add(dag_relative)
        full_by_id[task_id] = record
        tasks.append(
            {
                "id": task_id,
                "difficulty": difficulty,
                "map": map_relative,
                "dag": dag_relative,
            }
        )
        files.extend(
            (
                _file_record(root, map_relative, f"task {task_id} map"),
                _file_record(root, dag_relative, f"task {task_id} DAG"),
            )
        )

    if diagnostic.get("format") != "fwcollab.symbolic.collaboration_diagnostic_set.v1":
        raise FreezeError("diagnostic manifest has an unsupported format")
    policy = _require_mapping(diagnostic.get("selection_policy"), "diagnostic selection_policy")
    if policy.get("uses_model_outcomes") is not False:
        raise FreezeError("diagnostic selection must not use model outcomes")
    if policy.get("target_size") != EXPECTED_DIAGNOSTIC_COUNT:
        raise FreezeError("diagnostic target_size must be 24")
    if policy.get("difficulty_quotas") != EXPECTED_DIAGNOSTIC_QUOTAS:
        raise FreezeError("diagnostic difficulty quotas are inconsistent")
    diagnostic_records = _require_records(diagnostic.get("records"), "diagnostic records")
    if len(diagnostic_records) != EXPECTED_DIAGNOSTIC_COUNT:
        raise FreezeError(
            f"diagnostic manifest must contain {EXPECTED_DIAGNOSTIC_COUNT} records"
        )
    diagnostic_ids: set[str] = set()
    for index, record in enumerate(diagnostic_records):
        task_id = str(record.get("id", ""))
        if not task_id or task_id in diagnostic_ids or task_id not in full_by_id:
            raise FreezeError(f"invalid diagnostic task id at record {index}: {task_id!r}")
        source = full_by_id[task_id]
        for field in ("difficulty", "map", "dag", "primary_capability"):
            if record.get(field) != source.get(field):
                raise FreezeError(f"diagnostic task {task_id} disagrees with full manifest field {field}")
        diagnostic_ids.add(task_id)
    diagnostic_distribution = dict(
        sorted(Counter(str(item.get("difficulty")) for item in diagnostic_records).items())
    )
    if diagnostic_distribution != EXPECTED_DIAGNOSTIC_QUOTAS:
        raise FreezeError(
            f"diagnostic difficulty distribution mismatch: {diagnostic_distribution}"
        )

    tasks.sort(key=lambda item: (int(item["difficulty"][1:]), item["id"]))
    files.sort(key=lambda item: item["path"])
    if len(files) != 2 * EXPECTED_TASK_COUNT:
        raise FreezeError("benchmark freeze must contain exactly 144 asset files")
    return tasks, files


def build_manifest(root: Path = ROOT, *, created_at: str | None = None) -> dict[str, Any]:
    """Build and validate the freeze payload entirely in memory."""

    root = root.resolve()
    full, diagnostic, source_links = _load_source_manifests(root)
    tasks, files = _validate_sources(root, full, diagnostic)
    phase2_link = verify_phase2_freeze(root)
    timestamp = created_at or datetime.now(timezone.utc).isoformat()
    return {
        "format": FREEZE_FORMAT,
        "benchmark_version": BENCHMARK_VERSION,
        "scope": SCOPE,
        "created_at": timestamp,
        "statement": REQUIRED_SELECTION_STATEMENT,
        "source_manifests": source_links,
        "linked_phase2_freeze": phase2_link,
        "invariants": {
            "full_task_count": EXPECTED_TASK_COUNT,
            "diagnostic_task_count": EXPECTED_DIAGNOSTIC_COUNT,
            "map_count": EXPECTED_TASK_COUNT,
            "dag_count": EXPECTED_TASK_COUNT,
            "asset_file_count": 2 * EXPECTED_TASK_COUNT,
            "phase2_file_count": EXPECTED_PHASE2_FILE_COUNT,
            "selection_uses_model_outcomes": False,
            "condition_count": EXPECTED_CONDITION_COUNT,
            "replicates": EXPECTED_REPLICATE_COUNT,
            "expected_episodes": EXPECTED_EPISODE_COUNT,
            "difficulty_distribution": EXPECTED_DIFFICULTY_DISTRIBUTION,
            "diagnostic_difficulty_quotas": EXPECTED_DIAGNOSTIC_QUOTAS,
            "mechanism_coverage": EXPECTED_MECHANISMS,
            "spatial_semantic_unique": EXPECTED_TASK_COUNT,
            "spatial_topology_unique": EXPECTED_TASK_COUNT,
            "all_dags_schema_valid": True,
            "all_nodes_bound": True,
            "all_edges_verified": True,
            "all_capabilities_verified": True,
        },
        "tasks": tasks,
        "files": files,
    }


def _validate_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise FreezeError("freeze created_at must be a non-empty ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise FreezeError("freeze created_at is not a valid ISO-8601 timestamp") from exc
    if parsed.utcoffset() is None:
        raise FreezeError("freeze created_at must include a timezone offset")
    return value


def create_manifest(
    root: Path = ROOT, output: str | Path = OUTPUT
) -> tuple[Path, dict[str, Any]]:
    """Create a new freeze using exclusive-write semantics."""

    root = root.resolve()
    destination = _output_path(root, output)
    if destination.exists():
        raise FreezeError(f"refusing to overwrite existing freeze manifest: {destination}")
    payload = build_manifest(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        with destination.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
    except FileExistsError as exc:
        raise FreezeError(f"refusing to overwrite existing freeze manifest: {destination}") from exc
    return destination, payload


def verify_manifest(
    root: Path = ROOT, manifest_path: str | Path = OUTPUT
) -> dict[str, Any]:
    """Verify manifest schema, source links, invariants, and every frozen byte."""

    root = root.resolve()
    destination = _output_path(root, manifest_path)
    if not destination.is_file():
        raise FreezeError(f"freeze manifest does not exist: {destination}")
    try:
        payload = json.loads(destination.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FreezeError(f"cannot read freeze manifest {destination}: {exc}") from exc
    if not isinstance(payload, dict):
        raise FreezeError("freeze manifest root must be an object")
    timestamp = _validate_timestamp(payload.get("created_at"))
    expected = build_manifest(root, created_at=timestamp)
    if payload != expected:
        differing = sorted(
            key for key in set(payload) | set(expected) if payload.get(key) != expected.get(key)
        )
        raise FreezeError(f"freeze manifest content mismatch in fields: {differing}")
    return {
        "ok": True,
        "manifest": str(destination),
        "files": len(payload["files"]),
        "tasks": len(payload["tasks"]),
        "maps": payload["invariants"]["map_count"],
        "dags": payload["invariants"]["dag_count"],
        "phase2_files": payload["linked_phase2_freeze"]["declared_file_count"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create or verify the immutable FWCollab Full-72 Benchmark v1 freeze."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("create", "verify"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--root", type=Path, default=ROOT)
        subparser.add_argument("--manifest", type=Path, default=OUTPUT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "create":
            path, payload = create_manifest(args.root, args.manifest)
            result = {
                "ok": True,
                "action": "created",
                "manifest": str(path),
                "files": len(payload["files"]),
                "tasks": len(payload["tasks"]),
            }
        else:
            result = {"action": "verified", **verify_manifest(args.root, args.manifest)}
    except (FreezeError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
