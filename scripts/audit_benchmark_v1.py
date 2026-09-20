"""Reproduce the frozen Benchmark v1 DAG diversity and taxonomy counts."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from fwcollab.symbolic.collaboration_eval import collaboration_taxonomy
from fwcollab.symbolic.dag import (
    graph_signature,
    graphs_isomorphic,
    load_json,
    validate_state_dag,
)

if __package__:
    from .freeze_benchmark_v1 import OUTPUT as DEFAULT_FREEZE
    from .freeze_benchmark_v1 import ROOT, FreezeError, verify_manifest
else:
    from freeze_benchmark_v1 import OUTPUT as DEFAULT_FREEZE
    from freeze_benchmark_v1 import ROOT, FreezeError, verify_manifest


OUTPUT = Path("artifacts/audits/benchmark_v1/audit.json")
AUDIT_FORMAT = "fwcollab.symbolic.benchmark_diversity_audit.v1"
TAXONOMY_TAGS = (
    "C1_hold_and_pass",
    "C2_sequential_handoff",
    "C3_mutual_unlock",
    "C4_synchronized_action",
    "C5_information_dependent",
    "C6_multi_stage_alternating",
    "C7_parallel_subgoal_merge",
)
EXPECTED = {
    "full_benchmark": {
        "concrete_dag_instances": 72,
        "dependency_topology_classes": 48,
        "normalized_collaboration_templates": 65,
        "typed_executable_templates": 72,
        "taxonomy_counts": {
            "C1_hold_and_pass": 65,
            "C2_sequential_handoff": 70,
            "C3_mutual_unlock": 68,
            "C4_synchronized_action": 0,
            "C5_information_dependent": 0,
            "C6_multi_stage_alternating": 60,
            "C7_parallel_subgoal_merge": 0,
        },
    },
    "diagnostic_collaboration_set": {
        "concrete_dag_instances": 24,
        "dependency_topology_classes": 21,
        "normalized_collaboration_templates": 23,
        "typed_executable_templates": 24,
        "taxonomy_counts": {
            "C1_hold_and_pass": 23,
            "C2_sequential_handoff": 23,
            "C3_mutual_unlock": 22,
            "C4_synchronized_action": 0,
            "C5_information_dependent": 0,
            "C6_multi_stage_alternating": 19,
            "C7_parallel_subgoal_merge": 0,
        },
    },
}
EXPECTED_SPATIAL_TOPOLOGY_UNIQUE = 72


class AuditError(ValueError):
    """Raised when frozen audit inputs are internally inconsistent."""


def _output_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _load_freeze(root: Path, freeze_path: str | Path) -> tuple[Path, dict[str, Any]]:
    destination = _output_path(root, freeze_path)
    verify_manifest(root, destination)
    try:
        payload = json.loads(destination.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditError(f"cannot read verified freeze manifest: {exc}") from exc
    return destination, payload


def _source_path(root: Path, freeze: Mapping[str, Any], role: str) -> Path:
    sources = freeze.get("source_manifests")
    if not isinstance(sources, Mapping) or not isinstance(sources.get(role), Mapping):
        raise AuditError(f"freeze is missing source manifest role {role!r}")
    relative = sources[role].get("path")
    if not isinstance(relative, str) or not relative:
        raise AuditError(f"freeze source manifest role {role!r} has no path")
    return root / Path(*relative.split("/"))


def _canonical_role_sequence(record: Mapping[str, Any]) -> tuple[str, ...]:
    stages = record.get("stages")
    if not isinstance(stages, list):
        raise AuditError(f"task {record.get('id')!r} stages must be an array")
    role_map: dict[str, str] = {}
    normalized: list[str] = []
    for index, stage in enumerate(stages):
        if not isinstance(stage, Mapping):
            raise AuditError(f"task {record.get('id')!r} stage {index} must be an object")
        supporter = str(stage.get("supporter", ""))
        if supporter not in {"F", "W"}:
            raise AuditError(
                f"task {record.get('id')!r} stage {index} has invalid supporter {supporter!r}"
            )
        if supporter not in role_map:
            role_map[supporter] = f"role_{len(role_map) + 1}"
        normalized.append(role_map[supporter])
    return tuple(normalized)


def normalized_collaboration_signature(record: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return the documented coordinate/motif/role-name invariant stage signature.

    The signature retains stage order, normalized supporter alternation, whether a
    stage requires a maintained hold or only persistent unlocks, all/any mode, and
    controller count. A mixed stage is a hold because its plate must remain active;
    persistent controllers in that stage are reflected by the controller count.
    """

    stages = record.get("stages")
    if not isinstance(stages, list):
        raise AuditError(f"task {record.get('id')!r} stages must be an array")
    roles = _canonical_role_sequence(record)
    signature: list[tuple[str, str, str, int]] = []
    for index, (role, stage) in enumerate(zip(roles, stages, strict=True)):
        controllers = stage.get("controllers")
        if not isinstance(controllers, list) or not controllers:
            raise AuditError(
                f"task {record.get('id')!r} stage {index} controllers must be non-empty"
            )
        kinds: list[str] = []
        for controller in controllers:
            if not isinstance(controller, Mapping):
                raise AuditError(
                    f"task {record.get('id')!r} stage {index} controller must be an object"
                )
            kind = str(controller.get("kind", ""))
            if kind not in {"plate", "lever", "toggle"}:
                raise AuditError(
                    f"task {record.get('id')!r} stage {index} has unsupported controller kind {kind!r}"
                )
            kinds.append(kind)
        mode = str(stage.get("mode", ""))
        if mode not in {"all", "any"}:
            raise AuditError(
                f"task {record.get('id')!r} stage {index} has invalid mode {mode!r}"
            )
        stage_semantics = "hold" if "plate" in kinds else "persistent_unlock"
        signature.append((role, stage_semantics, mode, len(controllers)))
    return tuple(signature)


def _exact_graph_groups(
    items: list[tuple[Mapping[str, Any], Mapping[str, Any]]], mode: str
) -> list[list[tuple[Mapping[str, Any], Mapping[str, Any]]]]:
    buckets: defaultdict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = defaultdict(list)
    for item in items:
        buckets[graph_signature(item[1], mode)].append(item)
    groups: list[list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = []
    for signature in sorted(buckets):
        exact_groups: list[list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = []
        for item in sorted(buckets[signature], key=lambda pair: str(pair[0]["id"])):
            for group in exact_groups:
                if graphs_isomorphic(group[0][1], item[1], mode):
                    group.append(item)
                    break
            else:
                exact_groups.append([item])
        groups.extend(exact_groups)
    return groups


def _duplicate_ids(
    groups: list[list[tuple[Mapping[str, Any], Mapping[str, Any]]]],
) -> list[list[str]]:
    return sorted(
        [sorted(str(record["id"]) for record, _ in group) for group in groups if len(group) > 1]
    )


def _size_distribution(
    groups: list[list[tuple[Mapping[str, Any], Mapping[str, Any]]]],
) -> dict[str, int]:
    counts = Counter(len(group) for group in groups)
    return {str(size): counts[size] for size in sorted(counts)}


def _taxonomy_counts(
    items: list[tuple[Mapping[str, Any], Mapping[str, Any]]]
) -> dict[str, int]:
    counts = Counter(
        tag
        for record, dag in items
        for tag in collaboration_taxonomy(record, dag)["tags"]
    )
    return {tag: counts[tag] for tag in TAXONOMY_TAGS}


def _normalized_groups(
    records: list[Mapping[str, Any]],
) -> dict[tuple[Any, ...], list[str]]:
    groups: defaultdict[tuple[Any, ...], list[str]] = defaultdict(list)
    for record in records:
        groups[normalized_collaboration_signature(record)].append(str(record["id"]))
    return dict(groups)


def _count_distribution(values: Sequence[int | str]) -> dict[str, int]:
    counts = Counter(str(value) for value in values)
    return {key: counts[key] for key in sorted(counts, key=lambda item: (len(item), item))}


def _group_assignments(
    groups: Sequence[Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]]], prefix: str
) -> dict[str, str]:
    ordered = sorted(
        (sorted(str(record["id"]) for record, _ in group) for group in groups),
        key=lambda ids: tuple(ids),
    )
    return {
        task_id: f"{prefix}{index:03d}"
        for index, ids in enumerate(ordered, start=1)
        for task_id in ids
    }


def _normalized_assignments(
    groups: Mapping[tuple[Any, ...], list[str]], prefix: str
) -> dict[str, str]:
    ordered = sorted((sorted(ids) for ids in groups.values()), key=lambda ids: tuple(ids))
    return {
        task_id: f"{prefix}{index:03d}"
        for index, ids in enumerate(ordered, start=1)
        for task_id in ids
    }


def _scope_result(
    items: list[tuple[Mapping[str, Any], Mapping[str, Any]]]
) -> dict[str, Any]:
    topology_groups = _exact_graph_groups(items, "topology")
    typed_groups = _exact_graph_groups(items, "typed")
    normalized_groups = _normalized_groups([record for record, _ in items])
    topology_assignments = _group_assignments(topology_groups, "T")
    normalized_assignments = _normalized_assignments(normalized_groups, "N")
    typed_assignments = _group_assignments(typed_groups, "X")
    stages = [stage for record, _ in items for stage in record["stages"]]
    class_membership = {
        str(record["id"]): {
            "dependency_topology_class": topology_assignments[str(record["id"])],
            "normalized_collaboration_template": normalized_assignments[str(record["id"])],
            "typed_executable_template": typed_assignments[str(record["id"])],
        }
        for record, _ in items
    }
    return {
        "concrete_dag_instances": len(items),
        "dependency_topology_classes": len(topology_groups),
        "normalized_collaboration_templates": len(normalized_groups),
        "typed_executable_templates": len(typed_groups),
        "taxonomy_counts": _taxonomy_counts(items),
        "difficulty_distribution": dict(
            sorted(Counter(str(record["difficulty"]) for record, _ in items).items())
        ),
        "dag_node_count_distribution": _count_distribution(
            [len(dag["nodes"]) for _, dag in items]
        ),
        "dag_edge_count_distribution": _count_distribution(
            [len(dag["edges"]) for _, dag in items]
        ),
        "dag_any_join_count_distribution": _count_distribution(
            [sum(node["join"] == "any" for node in dag["nodes"]) for _, dag in items]
        ),
        "stage_count_distribution": _count_distribution(
            [len(record["stages"]) for record, _ in items]
        ),
        "controllers_per_stage_distribution": _count_distribution(
            [len(stage["controllers"]) for stage in stages]
        ),
        "controller_kind_counts": dict(
            sorted(
                Counter(
                    str(controller["kind"])
                    for stage in stages
                    for controller in stage["controllers"]
                ).items()
            )
        ),
        "dependency_topology_class_size_distribution": _size_distribution(topology_groups),
        "dependency_topology_duplicate_groups": _duplicate_ids(topology_groups),
        "normalized_collaboration_duplicate_groups": sorted(
            [sorted(ids) for ids in normalized_groups.values() if len(ids) > 1]
        ),
        "typed_executable_duplicate_groups": _duplicate_ids(typed_groups),
        "class_membership": dict(sorted(class_membership.items())),
    }


def _collect_mismatches(
    actual: Mapping[str, Any], expected: Mapping[str, Any], prefix: str = ""
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for key, expected_value in expected.items():
        path = f"{prefix}.{key}" if prefix else key
        if key not in actual:
            mismatches.append({"field": path, "expected": expected_value, "actual": "<missing>"})
            continue
        actual_value = actual[key]
        if isinstance(expected_value, Mapping):
            if not isinstance(actual_value, Mapping):
                mismatches.append({"field": path, "expected": expected_value, "actual": actual_value})
            else:
                mismatches.extend(_collect_mismatches(actual_value, expected_value, path))
        elif actual_value != expected_value:
            mismatches.append({"field": path, "expected": expected_value, "actual": actual_value})
    return mismatches


def build_audit(
    root: Path = ROOT, freeze_path: str | Path = DEFAULT_FREEZE
) -> dict[str, Any]:
    """Verify the freeze and independently compute all published audit quantities."""

    root = root.resolve()
    freeze_file, freeze = _load_freeze(root, freeze_path)
    full_manifest = load_json(_source_path(root, freeze, "full_benchmark"))
    diagnostic_manifest = load_json(_source_path(root, freeze, "diagnostic_subset"))
    full_records_raw = full_manifest.get("records")
    diagnostic_records_raw = diagnostic_manifest.get("records")
    if not isinstance(full_records_raw, list) or not all(
        isinstance(item, Mapping) for item in full_records_raw
    ):
        raise AuditError("full source manifest records must be an array of objects")
    if not isinstance(diagnostic_records_raw, list) or not all(
        isinstance(item, Mapping) for item in diagnostic_records_raw
    ):
        raise AuditError("diagnostic source manifest records must be an array of objects")
    full_records = list(full_records_raw)
    full_by_id = {str(record.get("id")): record for record in full_records}
    if len(full_by_id) != len(full_records):
        raise AuditError("full source manifest has duplicate task ids")
    diagnostic_ids = [str(record.get("id")) for record in diagnostic_records_raw]
    if len(diagnostic_ids) != len(set(diagnostic_ids)):
        raise AuditError("diagnostic source manifest has duplicate task ids")
    missing_ids = sorted(set(diagnostic_ids) - set(full_by_id))
    if missing_ids:
        raise AuditError(f"diagnostic tasks are absent from Full-72: {missing_ids}")
    diagnostic_records = [full_by_id[task_id] for task_id in diagnostic_ids]

    frozen_tasks = freeze.get("tasks")
    if not isinstance(frozen_tasks, list) or not all(isinstance(item, Mapping) for item in frozen_tasks):
        raise AuditError("freeze tasks must be an array of objects")
    frozen_by_id = {str(item.get("id")): item for item in frozen_tasks}
    if set(frozen_by_id) != set(full_by_id):
        raise AuditError("freeze task ids do not match the Full-72 source manifest")

    def load_items(records: list[Mapping[str, Any]]) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
        items: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
        for record in records:
            task_id = str(record["id"])
            frozen = frozen_by_id[task_id]
            if frozen.get("dag") != record.get("dag") or frozen.get("map") != record.get("map"):
                raise AuditError(f"frozen task paths disagree with source record {task_id}")
            dag = load_json(root / Path(*str(frozen["dag"]).split("/")))
            try:
                validate_state_dag(dag)
            except (TypeError, ValueError) as exc:
                raise AuditError(f"invalid DAG for task {task_id}: {exc}") from exc
            items.append((record, dag))
        return items

    full_items = load_items(full_records)
    diagnostic_items = load_items(diagnostic_records)
    results = {
        "full_benchmark": _scope_result(full_items),
        "diagnostic_collaboration_set": _scope_result(diagnostic_items),
    }
    diagnostic_id_set = set(diagnostic_ids)
    diagnostic_membership = results["diagnostic_collaboration_set"]["class_membership"]
    instances = []
    for record, dag in sorted(full_items, key=lambda item: str(item[0]["id"])):
        task_id = str(record["id"])
        taxonomy = collaboration_taxonomy(record, dag)
        full_classes = results["full_benchmark"]["class_membership"][task_id]
        diagnostic_classes = diagnostic_membership.get(task_id, {})
        instances.append(
            {
                "task_id": task_id,
                "difficulty": str(record["difficulty"]),
                "in_diagnostic_24": task_id in diagnostic_id_set,
                "map_path": str(record["map"]),
                "dag_path": str(record["dag"]),
                "full_dependency_topology_class": full_classes["dependency_topology_class"],
                "full_normalized_template_class": full_classes[
                    "normalized_collaboration_template"
                ],
                "full_typed_template_class": full_classes["typed_executable_template"],
                "diagnostic_dependency_topology_class": diagnostic_classes.get(
                    "dependency_topology_class", ""
                ),
                "diagnostic_normalized_template_class": diagnostic_classes.get(
                    "normalized_collaboration_template", ""
                ),
                "diagnostic_typed_template_class": diagnostic_classes.get(
                    "typed_executable_template", ""
                ),
                "taxonomy_tags": ";".join(taxonomy["tags"]),
                "stage_count": taxonomy["stage_count"],
                "controller_count": sum(
                    len(stage["controllers"]) for stage in record["stages"]
                ),
                "any_join_stage_count": sum(
                    stage["mode"] == "any" for stage in record["stages"]
                ),
            }
        )
    spatial_metrics = {
        "spatial_topology_unique": full_manifest.get("topology_unique"),
        "spatial_semantic_unique": full_manifest.get("semantic_unique"),
        "source_field": "eval_private/spatial_curriculum_full_v4/manifest.json:topology_unique",
        "meaning": "unique task/map spatial construction signatures",
        "is_dag_dependency_topology_count": False,
        "dag_dependency_topology_classes": results["full_benchmark"][
            "dependency_topology_classes"
        ],
    }

    mismatches = _collect_mismatches(results, EXPECTED, "results")
    if spatial_metrics["spatial_topology_unique"] != EXPECTED_SPATIAL_TOPOLOGY_UNIQUE:
        mismatches.append(
            {
                "field": "spatial_manifest_metrics.spatial_topology_unique",
                "expected": EXPECTED_SPATIAL_TOPOLOGY_UNIQUE,
                "actual": spatial_metrics["spatial_topology_unique"],
            }
        )
    declared_full_taxonomy = diagnostic_manifest.get("full_benchmark_taxonomy_coverage")
    declared_diagnostic_taxonomy = diagnostic_manifest.get("diagnostic_taxonomy_coverage")
    for field, declared, computed in (
        (
            "source_consistency.full_benchmark_taxonomy_coverage",
            declared_full_taxonomy,
            results["full_benchmark"]["taxonomy_counts"],
        ),
        (
            "source_consistency.diagnostic_taxonomy_coverage",
            declared_diagnostic_taxonomy,
            results["diagnostic_collaboration_set"]["taxonomy_counts"],
        ),
    ):
        if declared != computed:
            mismatches.append({"field": field, "expected": declared, "actual": computed})

    try:
        freeze_display = freeze_file.relative_to(root).as_posix()
    except ValueError:
        freeze_display = str(freeze_file)
    return {
        "format": AUDIT_FORMAT,
        "benchmark_version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "freeze_manifest": {
            "path": freeze_display,
            "sha256": hashlib.sha256(freeze_file.read_bytes()).hexdigest(),
        },
        "definitions": {
            "concrete_dag_instances": "one private executable DAG referenced by one frozen task",
            "dependency_topology_classes": (
                "exact directed-graph isomorphism preserving all/any join and ignoring node ids, "
                "predicates, owners, labels, and edge relations"
            ),
            "normalized_collaboration_templates": (
                "stage sequence preserving normalized supporter alternation, hold versus persistent "
                "unlock, all/any mode, and controller count; coordinates, ids, motifs, and F/W "
                "role names are ignored"
            ),
            "typed_executable_templates": (
                "exact directed-graph isomorphism preserving node kind, owner, join, predicate "
                "op/motif/state, and edge relation, invariant to a global agent A/B swap"
            ),
            "spatial_topology_unique": (
                "the V4 manifest's task/map construction signature count; it is not a DAG "
                "dependency-topology count"
            ),
        },
        "expected": EXPECTED,
        "results": results,
        "instances": instances,
        "spatial_manifest_metrics": spatial_metrics,
        "mismatches": mismatches,
        "ok": not mismatches,
    }


def _csv_text(fieldnames: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _report_markdown(report: Mapping[str, Any]) -> str:
    results = report["results"]
    full = results["full_benchmark"]
    diagnostic = results["diagnostic_collaboration_set"]
    lines = [
        "# FWCollab Benchmark v1 Audit",
        "",
        f"- Status: **{'PASS' if report['ok'] else 'FAIL'}**",
        f"- Generated: `{report['generated_at']}`",
        f"- Freeze: `{report['freeze_manifest']['path']}`",
        f"- Freeze SHA-256: `{report['freeze_manifest']['sha256']}`",
        "",
        "## Diversity",
        "",
        "| Scope | Concrete DAGs | Typed executable | Normalized collaboration | Dependency topology |",
        "|---|---:|---:|---:|---:|",
        (
            f"| Full benchmark | {full['concrete_dag_instances']} | "
            f"{full['typed_executable_templates']} | "
            f"{full['normalized_collaboration_templates']} | "
            f"{full['dependency_topology_classes']} |"
        ),
        (
            f"| Diagnostic-24 | {diagnostic['concrete_dag_instances']} | "
            f"{diagnostic['typed_executable_templates']} | "
            f"{diagnostic['normalized_collaboration_templates']} | "
            f"{diagnostic['dependency_topology_classes']} |"
        ),
        "",
        "The transformations from concrete instances to normalized templates and pure "
        "dependency topologies are many-to-one canonicalizations. The V4 source field "
        "`topology_unique=72` counts spatial construction signatures; it is not the 48-class "
        "DAG dependency-topology result.",
        "",
        "## Collaboration taxonomy",
        "",
        "| Category | Full benchmark | Diagnostic-24 | Status |",
        "|---|---:|---:|---|",
    ]
    for tag in TAXONOMY_TAGS:
        full_count = full["taxonomy_counts"][tag]
        diagnostic_count = diagnostic["taxonomy_counts"][tag]
        status = "covered" if full_count else "uncovered"
        lines.append(f"| {tag} | {full_count} | {diagnostic_count} | {status} |")
    lines.extend(
        [
            "",
            "C4, C5, and C7 are explicitly uncovered; the audit does not infer them from "
            "generic multi-agent activity.",
            "",
            "## Diagnostic selection boundary",
            "",
            (
                "> The diagnostic subset was selected solely from task structure and "
                "collaboration primitives, without access to model performance."
            ),
            "",
            "## Generated files",
            "",
            "- `audit.json`: complete machine-readable audit and exact class memberships",
            "- `summary.json`: stable JSON copy for paper/figure evidence",
            "- `instances.csv`: per-task split, taxonomy, and class membership",
            "- `taxonomy.csv`: full and Diagnostic-24 category counts",
            "- `REPORT.md`: this human-readable report",
            "",
        ]
    )
    return "\n".join(lines)


def write_audit(report: Mapping[str, Any], output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    output.write_text(serialized, encoding="utf-8", newline="\n")
    (output.parent / "summary.json").write_text(
        serialized, encoding="utf-8", newline="\n"
    )

    instances = list(report["instances"])
    instance_fields = list(instances[0]) if instances else []
    (output.parent / "instances.csv").write_text(
        _csv_text(instance_fields, instances), encoding="utf-8", newline="\n"
    )

    taxonomy_rows = []
    for scope, total in (
        ("full_benchmark", report["results"]["full_benchmark"]["concrete_dag_instances"]),
        (
            "diagnostic_collaboration_set",
            report["results"]["diagnostic_collaboration_set"]["concrete_dag_instances"],
        ),
    ):
        for tag in TAXONOMY_TAGS:
            count = report["results"][scope]["taxonomy_counts"][tag]
            taxonomy_rows.append(
                {
                    "scope": scope,
                    "taxonomy": tag,
                    "count": count,
                    "total_instances": total,
                    "coverage_fraction": f"{count / total:.6f}",
                    "status": "covered" if count else "uncovered",
                }
            )
    taxonomy_fields = [
        "scope",
        "taxonomy",
        "count",
        "total_instances",
        "coverage_fraction",
        "status",
    ]
    (output.parent / "taxonomy.csv").write_text(
        _csv_text(taxonomy_fields, taxonomy_rows), encoding="utf-8", newline="\n"
    )
    (output.parent / "REPORT.md").write_text(
        _report_markdown(report), encoding="utf-8", newline="\n"
    )
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit frozen Benchmark v1 DAG diversity and collaboration taxonomy."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--freeze-manifest", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = build_audit(args.root, args.freeze_manifest)
        destination = write_audit(report, _output_path(args.root.resolve(), args.output))
    except (AuditError, FreezeError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    summary = {
        "ok": report["ok"],
        "output": str(destination),
        "full": {
            key: report["results"]["full_benchmark"][key]
            for key in (
                "concrete_dag_instances",
                "dependency_topology_classes",
                "normalized_collaboration_templates",
                "typed_executable_templates",
            )
        },
        "diagnostic": {
            key: report["results"]["diagnostic_collaboration_set"][key]
            for key in (
                "concrete_dag_instances",
                "dependency_topology_classes",
                "normalized_collaboration_templates",
                "typed_executable_templates",
            )
        },
        "mismatches": report["mismatches"],
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
