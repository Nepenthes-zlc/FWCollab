"""Expand Full-72 into one row per evidenced cross-agent dependency edge.

The generated rows are contracted agent-to-agent edges. Environment-owned
controller/actuator nodes remain visible through node IDs, predicates, and
evidence, but are not mislabeled as agents. No model outcomes are read.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for candidate in (str(SRC), str(ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from audit_benchmark_v1 import build_audit  # noqa: E402
from fwcollab.symbolic.dag import load_json, validate_state_dag  # noqa: E402
from fwcollab.symbolic.map import load_symbol_map  # noqa: E402

DEFAULT_OUTPUT_DIR = Path("artifacts/audits/dependency_edges_v1")
MOTIFS = {
    ("plate", "door"): "M11",
    ("plate", "platform"): "M14",
    ("lever", "door"): "M17",
    ("lever", "platform"): "M18",
    ("toggle", "door"): "M19",
    ("toggle", "platform"): "M20",
}
CSV_FIELDS = (
    "task_id",
    "level",
    "edge_id",
    "stage_id",
    "edge_scope",
    "source_agent",
    "target_agent",
    "source_event",
    "target_event",
    "dependency_type",
    "mechanism",
    "controller",
    "actuator",
    "controller_logic",
    "requires_persistence",
    "requires_temporal_overlap",
    "is_role_transfer",
    "is_information_transfer",
    "source_predicate",
    "target_predicate",
    "source_dag_node_ids",
    "environment_dag_node_id",
    "target_dag_node_id",
    "contracted_dag_path",
    "predecessor_stage",
    "successor_stage",
    "evidence_method",
    "source_binding_verified",
    "target_binding_verified",
    "dependency_verified",
    "topology_id",
    "template_id",
)


class DependencyAuditError(ValueError):
    """Raised when dependency evidence is incomplete or inconsistent."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DependencyAuditError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DependencyAuditError(f"expected a JSON object in {path}")
    return value


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _json_cell(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _binding_cell(binding: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "path": str(binding.get("observation_path", "")),
        "equals": binding.get("expected_value"),
    }


def _nodes_by_state(dag: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result = {}
    for node in dag["nodes"]:
        state = str(node["predicate"]["state"])
        if state in result:
            raise DependencyAuditError(f"duplicate predicate state {state!r} in {dag['id']}")
        result[state] = node
    return result


def _edge_evidence(
    record: Mapping[str, Any], source_id: str, target_id: str
) -> Mapping[str, Any]:
    matches = [
        item
        for item in record["edge_evidence"]
        if str(item["from"]) == source_id and str(item["to"]) == target_id
    ]
    if len(matches) != 1:
        raise DependencyAuditError(
            f"expected one edge-evidence record for {record['id']} {source_id}->{target_id}"
        )
    return matches[0]


def _stage_spec(
    record: Mapping[str, Any], dag: Mapping[str, Any], stage: Mapping[str, Any]
) -> dict[str, Any]:
    nodes = _nodes_by_state(dag)
    bindings = {str(item["dag_node_id"]): item for item in record["node_bindings"]}
    controllers = list(stage["controllers"])
    actuator = stage["actuator"]
    controller_nodes = [nodes[f"{item['id']}_on"] for item in controllers]
    actuator_node = nodes[f"{actuator['id']}_on"]
    target_node = nodes[f"{actuator['id']}_crossed"]
    controller_bindings = [bindings[str(node["id"])] for node in controller_nodes]
    actuator_binding = bindings[str(actuator_node["id"])]
    target_binding = bindings[str(target_node["id"])]
    controller_kinds = [str(item["kind"]) for item in controllers]
    requires_hold = "plate" in controller_kinds
    motifs = sorted(
        {MOTIFS[(str(item["kind"]), str(actuator["kind"]))] for item in controllers}
    )

    evidence = []
    for node in controller_nodes:
        evidence.append(_edge_evidence(record, str(node["id"]), str(actuator_node["id"])))
    evidence.append(_edge_evidence(record, str(actuator_node["id"]), str(target_node["id"])))
    controller_expression = {
        "mode": str(stage["mode"]),
        "conditions": [
            {
                "controller_id": str(controller["id"]),
                "controller_kind": str(controller["kind"]),
                **_binding_cell(binding),
            }
            for controller, binding in zip(controllers, controller_bindings, strict=True)
        ],
        "actuator_condition": {
            "actuator_id": str(actuator["id"]),
            **_binding_cell(actuator_binding),
        },
    }
    target_predicate = {
        "kind": "actor_crosses_column_boundary",
        "role": str(stage["traveler"]),
        "alive": True,
        "operator": ">",
        "column": int(stage["room"]["gate_col"]),
        "witness_binding": _binding_cell(target_binding),
    }
    source_ids = [str(node["id"]) for node in controller_nodes]
    environment_id = str(actuator_node["id"])
    target_id = str(target_node["id"])
    return {
        "stage_id": f"S{stage['stage']}",
        "edge_scope": "controller_stage",
        "source_agent": str(stage["supporter"]),
        "target_agent": str(stage["traveler"]),
        "source_event": (
            f"{stage['supporter']} establishes {stage['mode']}({','.join(str(item['id']) for item in controllers)}), "
            f"activating {actuator['id']}"
        ),
        "target_event": (
            f"{stage['traveler']} crosses {actuator['id']} beyond column "
            f"{stage['room']['gate_col']}"
        ),
        "dependency_type": "MAINTAIN" if requires_hold else "ENABLE",
        "mechanism": ";".join(motifs),
        "controller": ";".join(
            f"{item['id']}:{item['kind']}" for item in controllers
        ),
        "actuator": f"{actuator['id']}:{actuator['kind']}",
        "controller_logic": str(stage["mode"]),
        "requires_persistence": int(requires_hold),
        "requires_temporal_overlap": int(requires_hold),
        "is_information_transfer": 0,
        "source_predicate": _json_cell(controller_expression),
        "target_predicate": _json_cell(target_predicate),
        "source_dag_node_ids": ";".join(source_ids),
        "environment_dag_node_id": environment_id,
        "target_dag_node_id": target_id,
        "contracted_dag_path": f"{'+'.join(source_ids)}->{environment_id}->{target_id}",
        "evidence_method": ";".join(sorted({str(item["method"]) for item in evidence})),
        "source_binding_verified": int(
            all(bool(item.get("verified")) for item in controller_bindings)
            and bool(actuator_binding.get("verified"))
        ),
        "target_binding_verified": int(bool(target_binding.get("verified"))),
        "dependency_verified": int(all(bool(item.get("verified")) for item in evidence)),
    }


def _collaborative_capability(record: Mapping[str, Any]) -> bool:
    capability = record["capability"]
    return bool(
        capability.get("kind") in {"heavy_plate", "thermal"}
        or (
            capability.get("kind") == "light"
            and (capability.get("object") or capability.get("rotatable"))
        )
    )


def _capability_components(capability: Mapping[str, Any]) -> tuple[str, str]:
    kind = str(capability["kind"])
    if kind == "heavy_plate":
        controller = f"{capability['controller_id']}:plate"
        actuator_kind = "platform" if str(capability["actuator_id"]).endswith("platform") else "door"
        return controller, f"{capability['actuator_id']}:{actuator_kind}"
    if kind == "thermal":
        return "K:freezer", "I:thermal_route"
    if kind == "light":
        controllers = ["S:light_sensor"]
        if capability.get("rotatable"):
            controllers.insert(0, "cap_mirror_toggle:toggle")
        if capability.get("object"):
            controllers.insert(0, f"{capability['object']}:movable_resource")
        return ";".join(controllers), "A:light_controlled_door"
    raise DependencyAuditError(f"unsupported collaborative capability kind {kind!r}")


def _capability_spec(record: Mapping[str, Any], dag: Mapping[str, Any]) -> dict[str, Any]:
    nodes = _nodes_by_state(dag)
    bindings = {str(item["dag_node_id"]): item for item in record["node_bindings"]}
    motif = str(record["primary_capability"])
    capability = record["capability"]
    source_node = nodes[f"{motif.lower()}_engaged"]
    target_node = nodes[f"{motif.lower()}_crossed"]
    source_id = str(source_node["id"])
    target_id = str(target_node["id"])
    source_binding = bindings[source_id]
    target_binding = bindings[target_id]
    evidence = _edge_evidence(record, source_id, target_id)
    controller, actuator = _capability_components(capability)
    source_predicate = {
        "kind": "equals",
        **_binding_cell(source_binding),
        "responsible_role": str(capability["supporter"]),
    }
    target_predicate = {
        "kind": "actor_crosses_column_boundary",
        "role": str(capability["traveler"]),
        "alive": True,
        "operator": ">",
        "column": int(capability["gate_col"]),
        "witness_binding": _binding_cell(target_binding),
    }
    return {
        "stage_id": "CAP",
        "edge_scope": "capability",
        "source_agent": str(capability["supporter"]),
        "target_agent": str(capability["traveler"]),
        "source_event": (
            f"{capability['supporter']} establishes {motif} capability state "
            f"({capability['title']})"
        ),
        "target_event": (
            f"{capability['traveler']} crosses {motif} capability boundary beyond "
            f"column {capability['gate_col']}"
        ),
        # The capability state is established and then persists without the
        # supporter continuously occupying a controller. It is therefore an
        # ENABLE edge, not active MAINTAIN.
        "dependency_type": "ENABLE",
        "mechanism": motif,
        "controller": controller,
        "actuator": actuator,
        "controller_logic": "all",
        "requires_persistence": 0,
        "requires_temporal_overlap": 0,
        "is_information_transfer": 0,
        "source_predicate": _json_cell(source_predicate),
        "target_predicate": _json_cell(target_predicate),
        "source_dag_node_ids": source_id,
        "environment_dag_node_id": source_id,
        "target_dag_node_id": target_id,
        "contracted_dag_path": f"{source_id}->{target_id}",
        "evidence_method": str(evidence["method"]),
        "source_binding_verified": int(bool(source_binding.get("verified"))),
        "target_binding_verified": int(bool(target_binding.get("verified"))),
        "dependency_verified": int(bool(evidence.get("verified"))),
    }


def _class_membership(root: Path) -> dict[str, Mapping[str, Any]]:
    audit = build_audit(root)
    if not audit.get("ok"):
        raise DependencyAuditError(f"frozen diversity audit failed: {audit.get('mismatches')}")
    return {str(item["task_id"]): item for item in audit["instances"]}


def build_edges(root: Path = ROOT) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    manifest = _read_json(root / "eval_private/spatial_curriculum_full_v4/manifest.json")
    witnesses = _read_json(root / "eval_private/spatial_curriculum_full_v4/witnesses.json").get("maps")
    necessity = _read_json(
        root / "artifacts/evaluations/collaboration_necessity_v1/summary.json"
    )
    necessity_by_id = {str(item["task_id"]): item for item in necessity["tasks"]}
    classes = _class_membership(root)
    records = manifest.get("records")
    if not isinstance(records, list) or len(records) != 72:
        raise DependencyAuditError("V4 manifest must contain exactly 72 records")
    if not isinstance(witnesses, Mapping) or len(witnesses) != 72:
        raise DependencyAuditError("witness file must contain exactly 72 task entries")

    rows: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: str(item["id"])):
        task_id = str(record["id"])
        symbol_map = load_symbol_map(_resolve(root, str(record["map"])))
        if symbol_map.map_id != task_id:
            raise DependencyAuditError(f"map id mismatch for {task_id}")
        dag = load_json(_resolve(root, str(record["dag"])))
        validate_state_dag(dag)
        witness = witnesses.get(task_id)
        if not isinstance(witness, list) or len(witness) != int(record["witness_rounds"]):
            raise DependencyAuditError(f"missing or inconsistent witness for {task_id}")
        if not record.get("all_nodes_bound") or not record.get("all_edges_verified"):
            raise DependencyAuditError(f"unverified DAG bindings or edges for {task_id}")

        task_specs = [_stage_spec(record, dag, stage) for stage in record["stages"]]
        if _collaborative_capability(record):
            task_specs.append(_capability_spec(record, dag))
        if not task_specs:
            raise DependencyAuditError(f"no cross-agent dependencies found for {task_id}")

        certificates = necessity_by_id[task_id]["stage_certificates"]
        if len(certificates) != len(record["stages"]) or not all(
            item.get("cross_agent_dependency_proved") for item in certificates
        ):
            raise DependencyAuditError(f"stage necessity certificate failed for {task_id}")

        stage_ids = [str(item["stage_id"]) for item in task_specs]
        for index, spec in enumerate(task_specs):
            previous = stage_ids[index - 1] if index else ""
            successor = stage_ids[index + 1] if index + 1 < len(stage_ids) else ""
            is_role_transfer = int(
                index + 1 < len(task_specs)
                and str(task_specs[index + 1]["source_agent"]) == str(spec["target_agent"])
            )
            class_record = classes[task_id]
            rows.append(
                {
                    "task_id": task_id,
                    "level": str(record["difficulty"]),
                    "edge_id": f"E{index + 1:02d}",
                    **spec,
                    "is_role_transfer": is_role_transfer,
                    "predecessor_stage": previous,
                    "successor_stage": successor,
                    "topology_id": class_record["full_dependency_topology_class"],
                    "template_id": class_record["full_normalized_template_class"],
                }
            )

    if len(rows) != 367:
        raise DependencyAuditError(f"expected 367 dependency edges, got {len(rows)}")
    if not all(row["source_agent"] != row["target_agent"] for row in rows):
        raise DependencyAuditError("a contracted dependency is not cross-agent")
    if not all(
        row["source_binding_verified"]
        and row["target_binding_verified"]
        and row["dependency_verified"]
        for row in rows
    ):
        raise DependencyAuditError("at least one dependency lacks verified evidence")

    type_counts = Counter(str(row["dependency_type"]) for row in rows)
    direction_counts = Counter(
        f"{row['source_agent']}->{row['target_agent']}" for row in rows
    )
    mechanism_counts = Counter(
        mechanism
        for row in rows
        for mechanism in str(row["mechanism"]).split(";")
        if mechanism
    )
    role_transfer_rows = [row for row in rows if row["is_role_transfer"]]
    mutual_tasks = {
        task_id
        for task_id in {str(row["task_id"]) for row in rows}
        if {
            (str(row["source_agent"]), str(row["target_agent"]))
            for row in rows
            if row["task_id"] == task_id
        }
        >= {("F", "W"), ("W", "F")}
    }
    summary = {
        "format": "fwcollab.dependency_edges.v1",
        "scope": "Full-72 V4 structural evidence; model outcomes excluded",
        "tasks": 72,
        "contracted_cross_agent_edges": len(rows),
        "edge_scope_counts": dict(sorted(Counter(row["edge_scope"] for row in rows).items())),
        "primitive_counts": {
            "ENABLE": type_counts["ENABLE"],
            "MAINTAIN": type_counts["MAINTAIN"],
            "SYNCHRONIZE": 0,
            "INFORMATION": 0,
        },
        "primitive_task_coverage": {
            primitive: len(
                {str(row["task_id"]) for row in rows if row["dependency_type"] == primitive}
            )
            for primitive in ("ENABLE", "MAINTAIN")
        }
        | {"SYNCHRONIZE": 0, "INFORMATION": 0},
        "direction_counts": dict(sorted(direction_counts.items())),
        "composition_counts": {
            "HANDOFF_ROLE_TRANSFER_TRANSITIONS": len(role_transfer_rows),
            "TASKS_WITH_HANDOFF_ROLE_TRANSFER": len(
                {str(row["task_id"]) for row in role_transfer_rows}
            ),
            "TASKS_WITH_MUTUAL_ENABLE": len(mutual_tasks),
            "PARALLEL_JOIN": 0,
        },
        "mechanism_counts": dict(sorted(mechanism_counts.items())),
        "verified": {
            "source_bindings": sum(int(row["source_binding_verified"]) for row in rows),
            "target_bindings": sum(int(row["target_binding_verified"]) for row in rows),
            "dependencies": sum(int(row["dependency_verified"]) for row in rows),
        },
        "separate_extension": {
            "C5_information_tasks": 12,
            "included_in_this_csv": False,
            "reason": "C5 uses role-private observations and a separate executable-DAG schema",
        },
    }
    return rows, summary


def _csv_text(rows: Sequence[Mapping[str, Any]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _summary_markdown(summary: Mapping[str, Any]) -> str:
    primitives = summary["primitive_counts"]
    task_coverage = summary["primitive_task_coverage"]
    compositions = summary["composition_counts"]
    lines = [
        "# FWCollab Full-72 Cross-Agent Dependency Edges",
        "",
        "This audit contracts each evidenced supporter → environment → traveler path into one "
        "agent-to-agent dependency edge. It uses no model outcome.",
        "",
        "## Result",
        "",
        f"- Tasks: **{summary['tasks']}**",
        f"- Contracted cross-agent edges: **{summary['contracted_cross_agent_edges']}**",
        f"- Controller-stage edges: **{summary['edge_scope_counts']['controller_stage']}**",
        f"- Collaborative capability edges: **{summary['edge_scope_counts']['capability']}**",
        "",
        "## Candidate primitive layer",
        "",
        "| Primitive | Edges | Tasks | Interpretation |",
        "|---|---:|---:|---|",
        f"| ENABLE | {primitives['ENABLE']} | {task_coverage['ENABLE']} | A establishes a persistent condition that unlocks B's progress |",
        f"| MAINTAIN | {primitives['MAINTAIN']} | {task_coverage['MAINTAIN']} | A must actively keep a volatile plate condition true while B progresses |",
        f"| SYNCHRONIZE | {primitives['SYNCHRONIZE']} | {task_coverage['SYNCHRONIZE']} | No bounded same-round/window dependency exists |",
        f"| INFORMATION | {primitives['INFORMATION']} | {task_coverage['INFORMATION']} | Full-72 has shared public state |",
        "",
        "Every row has `source_agent != target_agent`. `MAINTAIN` is not counted again as "
        "ENABLE; it is the persistence-constrained subtype in the mutually exclusive edge label.",
        "",
        "## Composition layer",
        "",
        "| Composition | Count | Interpretation |",
        "|---|---:|---|",
        f"| Handoff / role-transfer transition | {compositions['HANDOFF_ROLE_TRANSFER_TRANSITIONS']} | Target of one edge becomes source of the next edge |",
        f"| Tasks containing a role transfer | {compositions['TASKS_WITH_HANDOFF_ROLE_TRANSFER']} | At least one adjacent responsibility transfer |",
        f"| Tasks containing Mutual Enable | {compositions['TASKS_WITH_MUTUAL_ENABLE']} | Both F→W and W→F directions occur |",
        f"| Parallel Join | {compositions['PARALLEL_JOIN']} | No independent role-owned branches with a required AND merge |",
        "",
        "These counts support treating Handoff and Mutual Enable as graph compositions rather "
        "than primitive edge labels. A cross-agent edge itself is not automatically a Handoff: "
        "`is_role_transfer=1` only when its target agent becomes the source agent of the next edge.",
        "",
        "## Direction",
        "",
        "| Direction | Edges |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {direction} | {count} |"
        for direction, count in summary["direction_counts"].items()
    )
    lines.extend(
        [
            "",
            "## Evidence semantics",
            "",
            "- Controller-stage edges contract controller node(s) → environment actuator → traveler crossing.",
            "- `source_predicate` preserves controller and actuator state bindings.",
            "- `target_predicate` uses the evaluator's boundary-crossing predicate and retains the witness coordinate only as audit evidence.",
            "- `requires_persistence=requires_temporal_overlap=1` only for active role-held pressure plates.",
            "- Object placement, mirror toggles, and thermal changes are classified as ENABLE because the established world state persists without continuous supporter occupation.",
            "- `predecessor_stage` and `successor_stage` describe the ordered contracted-edge sequence, not raw DAG node adjacency.",
            "",
            "## Coverage boundary",
            "",
            "- Full-72 contains no Synchronize or Information primitive edges.",
            "- The separate C5-12 suite contains information transfer, but it is not mixed into this CSV because it uses role-private DTOs and a separate DAG schema.",
            "- Full-72 contains no verified Parallel-Join composition. Generic DAG layer width and multi-controller `all(...)` are insufficient evidence.",
            "",
            "## Provisional taxonomy implied by the edge audit",
            "",
            "1. Primitive dependencies: **ENABLE, MAINTAIN, SYNCHRONIZE, INFORMATION**.",
            "2. Composition patterns: **HANDOFF, MUTUAL ENABLE, ROLE ALTERNATION, PARALLEL JOIN**.",
            "3. Full-72 empirically instantiates ENABLE and MAINTAIN; C5 separately instantiates INFORMATION; SYNCHRONIZE remains absent.",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(
    rows: Sequence[Mapping[str, Any]], summary: Mapping[str, Any], output_dir: Path
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "dependency_edges.csv"
    markdown_path = output_dir / "dependency_edges_summary.md"
    json_path = output_dir / "dependency_edges_summary.json"
    csv_path.write_text(_csv_text(rows), encoding="utf-8-sig", newline="")
    markdown_path.write_text(_summary_markdown(summary), encoding="utf-8", newline="\n")
    json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return csv_path, markdown_path, json_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Expand Full-72 into evidenced cross-agent dependency edges."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    output_dir = _resolve(root, args.output_dir)
    try:
        rows, summary = build_edges(root)
        csv_path, markdown_path, json_path = write_outputs(rows, summary, output_dir)
    except (DependencyAuditError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "edges": len(rows),
                "dependency_edges_csv": str(csv_path),
                "summary_md": str(markdown_path),
                "summary_json": str(json_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
