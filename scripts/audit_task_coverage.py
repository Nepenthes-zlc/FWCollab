"""Generate a structural coverage matrix for the frozen Full-72 benchmark.

This audit is intentionally outcome-independent. It reads maps, executable DAGs,
the V4 manifest, deterministic witnesses, the frozen diversity audit inputs, and
the collaboration-necessity certificates. It never reads model-run results.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for candidate in (str(SRC), str(ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from audit_benchmark_v1 import build_audit  # noqa: E402
from fwcollab.symbolic.agents import AgentDecision, ScriptedPolicy  # noqa: E402
from fwcollab.symbolic.collaboration_eval import collaboration_taxonomy  # noqa: E402
from fwcollab.symbolic.dag import graph_metrics, load_json, validate_state_dag  # noqa: E402
from fwcollab.symbolic.map import load_symbol_map  # noqa: E402
from fwcollab.symbolic.runner import DualAgentSession, replay_trace  # noqa: E402
from fwcollab.symbolic.world import SymbolAction  # noqa: E402

DEFAULT_OUTPUT_DIR = Path("artifacts/audits/task_coverage_v1")
MAIN_DEPENDENCIES = (
    "Enable",
    "Maintain",
    "Handoff",
    "Mutual Enable",
    "Synchronize",
    "Parallel-Join",
    "Information",
)
CSV_FIELDS = (
    "task_id",
    "level",
    "map_id",
    "mechanisms",
    "controllers",
    "actuators",
    "mechanism_count",
    "supporter_sequence",
    "traveler_sequence",
    "cross_agent_dependencies",
    "has_enable",
    "has_maintain",
    "has_handoff",
    "has_mutual_enable",
    "has_synchronize",
    "has_parallel_join",
    "has_information_dependency",
    "dependency_type_count",
    "role_switch_count",
    "handoff_count",
    "controller_handoff_count",
    "capability_handoff_count",
    "maintain_count",
    "dag_node_count",
    "dag_edge_count",
    "dag_depth",
    "branch_count",
    "join_count",
    "max_parallel_width",
    "alternation_length",
    "persistent_dependency",
    "all_any_logic",
    "required_bottleneck_count",
    "bidirectional_collaboration",
    "single_agent_solvable",
    "F_wait_success",
    "W_wait_success",
    "F_wait_blocks_teammate",
    "W_wait_blocks_teammate",
    "topology_id",
    "template_id",
    "semantic_signature",
    "spatial_signature",
    "witness_valid",
    "replay_valid",
)


class CoverageAuditError(ValueError):
    """Raised when source evidence is incomplete or inconsistent."""


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CoverageAuditError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CoverageAuditError(f"expected a JSON object in {path}")
    return value


def _longest_alternation(roles: Sequence[str]) -> int:
    if not roles:
        return 0
    longest = current = 1
    for previous, current_role in zip(roles, roles[1:], strict=False):
        current = current + 1 if current_role != previous else 1
        longest = max(longest, current)
    return longest


def _cross_agent_dependency_count(dag: Mapping[str, Any]) -> int:
    owners = {str(node["id"]): str(node["owner"]) for node in dag["nodes"]}
    outgoing: defaultdict[str, list[str]] = defaultdict(list)
    for edge in dag["edges"]:
        outgoing[str(edge["from"])].append(str(edge["to"]))
    dependencies: set[tuple[str, str]] = set()
    for source, owner in owners.items():
        if owner not in {"agent_a", "agent_b"}:
            continue
        queue = deque(outgoing[source])
        seen: set[str] = set()
        while queue:
            target = queue.popleft()
            if target in seen:
                continue
            seen.add(target)
            target_owner = owners[target]
            if target_owner in {"agent_a", "agent_b"}:
                if target_owner != owner:
                    dependencies.add((source, target))
                continue
            queue.extend(outgoing[target])
    return len(dependencies)


def _is_collaborative_capability(record: Mapping[str, Any]) -> bool:
    capability = record.get("capability", {})
    if not isinstance(capability, Mapping):
        return False
    return bool(
        capability.get("kind") in {"heavy_plate", "thermal"}
        or (
            capability.get("kind") == "light"
            and (capability.get("object") or capability.get("rotatable"))
        )
    )


def _dependency_description(record: Mapping[str, Any]) -> str:
    descriptions: list[str] = []
    for stage in record["stages"]:
        kinds = {str(item["kind"]) for item in stage["controllers"]}
        primitives = ["Enable"]
        if "plate" in kinds:
            primitives.append("Maintain")
        primitives.append("Handoff")
        descriptions.append(
            f"S{stage['stage']}:{stage['supporter']}->{stage['traveler']}"
            f"[{'+'.join(primitives)}]"
        )
    if _is_collaborative_capability(record):
        capability = record["capability"]
        descriptions.append(
            f"CAP:{capability['supporter']}->{capability['traveler']}"
            "[Enable+Handoff]"
        )
    return ";".join(descriptions)


def _witness_decisions(
    witness: Sequence[Mapping[str, Sequence[Any]]], role: str
) -> list[AgentDecision]:
    decisions: list[AgentDecision] = []
    for round_index, joint in enumerate(witness, start=1):
        raw = joint.get(role)
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) != 2:
            raise CoverageAuditError(
                f"invalid witness decision for {role} at round {round_index}"
            )
        move, steps = str(raw[0]), int(raw[1])
        decisions.append(
            AgentDecision(
                action=SymbolAction(move=move, steps=steps),
                reason="coverage audit witness replay",
            )
        )
    return decisions


def _verify_witness(symbol_map: Any, witness: Sequence[Mapping[str, Sequence[Any]]]) -> tuple[int, int]:
    policies = {
        role: ScriptedPolicy(_witness_decisions(witness, role), name=f"coverage-witness-{role}")
        for role in ("F", "W")
    }
    result = DualAgentSession(
        symbol_map,
        policies,
        max_rounds=len(witness),
        planning_rounds=0,
        coordination_mode="emergent",
    ).run()
    witness_valid = int(result.trace.get("outcome") == "team_success")
    replay_valid = int(bool(replay_trace(symbol_map, result.trace).get("ok")))
    return witness_valid, replay_valid


def _distribution(values: Iterable[Any]) -> dict[str, int]:
    counts = Counter(str(value) for value in values)
    return dict(sorted(counts.items(), key=lambda item: (int(item[0]) if item[0].isdigit() else item[0])))


def build_coverage(root: Path = ROOT) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = root.resolve()
    manifest = _read_json(root / "eval_private/spatial_curriculum_full_v4/manifest.json")
    witness_payload = _read_json(root / "eval_private/spatial_curriculum_full_v4/witnesses.json")
    necessity = _read_json(
        root / "artifacts/evaluations/collaboration_necessity_v1/summary.json"
    )
    diversity = build_audit(root)
    if not diversity.get("ok"):
        raise CoverageAuditError(
            f"frozen diversity audit failed: {diversity.get('mismatches')}"
        )

    records = manifest.get("records")
    witnesses = witness_payload.get("maps")
    necessity_tasks = necessity.get("tasks")
    if not isinstance(records, list) or len(records) != 72:
        raise CoverageAuditError("V4 manifest must contain exactly 72 records")
    if not isinstance(witnesses, Mapping) or len(witnesses) != 72:
        raise CoverageAuditError("witness file must contain exactly 72 maps")
    if not isinstance(necessity_tasks, list) or len(necessity_tasks) != 72:
        raise CoverageAuditError("necessity audit must contain exactly 72 tasks")

    necessity_by_id = {str(item["task_id"]): item for item in necessity_tasks}
    classes_by_id = {str(item["task_id"]): item for item in diversity["instances"]}
    if set(necessity_by_id) != {str(item["id"]) for item in records}:
        raise CoverageAuditError("necessity task ids do not match the V4 manifest")
    if set(classes_by_id) != {str(item["id"]) for item in records}:
        raise CoverageAuditError("diversity task ids do not match the V4 manifest")

    rows: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: str(item["id"])):
        task_id = str(record["id"])
        symbol_map = load_symbol_map(_resolve(root, str(record["map"])))
        dag = load_json(_resolve(root, str(record["dag"])))
        validate_state_dag(dag)
        witness = witnesses.get(task_id)
        if not isinstance(witness, list) or not witness:
            raise CoverageAuditError(f"missing witness for {task_id}")
        if len(witness) != int(record["witness_rounds"]):
            raise CoverageAuditError(f"witness round mismatch for {task_id}")
        witness_valid, replay_valid = _verify_witness(symbol_map, witness)

        stages = list(record["stages"])
        supporters = [str(stage["supporter"]) for stage in stages]
        travelers = [str(stage["traveler"]) for stage in stages]
        controller_kinds = sorted(
            {str(controller["kind"]) for stage in stages for controller in stage["controllers"]}
        )
        actuator_kinds = sorted({str(stage["actuator"]["kind"]) for stage in stages})
        maintain_count = sum(
            any(str(controller["kind"]) == "plate" for controller in stage["controllers"])
            for stage in stages
        )
        persistent_count = sum(
            any(
                str(controller["kind"]) in {"lever", "toggle"}
                for controller in stage["controllers"]
            )
            for stage in stages
        )
        role_switch_count = sum(
            supporters[index] != supporters[index - 1]
            for index in range(1, len(supporters))
        )
        taxonomy = collaboration_taxonomy(record, dag)
        tags = set(taxonomy["tags"])
        evidence = necessity_by_id[task_id]
        certificates = list(evidence["stage_certificates"])
        required_bottlenecks = sum(
            bool(item.get("cross_agent_dependency_proved")) for item in certificates
        )
        capability_handoff = int(_is_collaborative_capability(record))
        controller_handoffs = len(stages)
        handoff_count = controller_handoffs + capability_handoff
        metrics = graph_metrics(dag)
        class_record = classes_by_id[task_id]

        has_enable = int(required_bottlenecks > 0)
        has_maintain = int(maintain_count > 0)
        has_handoff = int(handoff_count > 0)
        has_mutual = int(bool(evidence["bidirectional_assistance_proved"]))
        has_synchronize = int("C4_synchronized_action" in tags)
        has_information = int("C5_information_dependent" in tags)
        has_parallel_join = int("C7_parallel_subgoal_merge" in tags)
        dependency_type_count = sum(
            (
                has_enable,
                has_maintain,
                has_handoff,
                has_mutual,
                has_synchronize,
                has_parallel_join,
                has_information,
            )
        )
        permanent_wait = evidence["permanent_wait"]
        f_wait_success = int(not permanent_wait["F"]["team_success_impossible"])
        w_wait_success = int(not permanent_wait["W"]["team_success_impossible"])

        rows.append(
            {
                "task_id": task_id,
                "level": str(record["difficulty"]),
                "map_id": symbol_map.map_id,
                "mechanisms": ";".join(str(value) for value in record["mechanisms"]),
                "controllers": ";".join(controller_kinds),
                "actuators": ";".join(actuator_kinds),
                "mechanism_count": len(record["mechanisms"]),
                "supporter_sequence": "->".join(supporters),
                "traveler_sequence": "->".join(travelers),
                "cross_agent_dependencies": _dependency_description(record),
                "has_enable": has_enable,
                "has_maintain": has_maintain,
                "has_handoff": has_handoff,
                "has_mutual_enable": has_mutual,
                "has_synchronize": has_synchronize,
                "has_parallel_join": has_parallel_join,
                "has_information_dependency": has_information,
                "dependency_type_count": dependency_type_count,
                "role_switch_count": role_switch_count,
                "handoff_count": handoff_count,
                "controller_handoff_count": controller_handoffs,
                "capability_handoff_count": capability_handoff,
                "maintain_count": maintain_count,
                "dag_node_count": metrics["nodes"],
                "dag_edge_count": metrics["edges"],
                "dag_depth": metrics["longest_path"],
                "branch_count": metrics["branch_nodes"],
                "join_count": metrics["join_nodes"],
                "max_parallel_width": metrics["max_parallel_width"],
                "alternation_length": _longest_alternation(supporters),
                "persistent_dependency": int(persistent_count > 0),
                "all_any_logic": ";".join(sorted({str(stage["mode"]) for stage in stages})),
                "required_bottleneck_count": required_bottlenecks,
                "bidirectional_collaboration": has_mutual,
                "single_agent_solvable": int(f_wait_success or w_wait_success),
                "F_wait_success": f_wait_success,
                "W_wait_success": w_wait_success,
                "F_wait_blocks_teammate": int(
                    permanent_wait["F"]["also_blocks_teammate_at_verified_cross_agent_cut"]
                ),
                "W_wait_blocks_teammate": int(
                    permanent_wait["W"]["also_blocks_teammate_at_verified_cross_agent_cut"]
                ),
                "topology_id": class_record["full_dependency_topology_class"],
                "template_id": class_record["full_normalized_template_class"],
                "semantic_signature": str(record["semantic_signature"]),
                "spatial_signature": str(record["topology_signature"]),
                "witness_valid": witness_valid,
                "replay_valid": replay_valid,
            }
        )

    if len(rows) != 72:
        raise CoverageAuditError(f"expected 72 coverage rows, got {len(rows)}")
    if not all(row["witness_valid"] and row["replay_valid"] for row in rows):
        raise CoverageAuditError("at least one deterministic witness or replay failed")
    if len({row["topology_id"] for row in rows}) != 48:
        raise CoverageAuditError("coverage matrix does not reproduce 48 topology classes")
    if len({row["template_id"] for row in rows}) != 65:
        raise CoverageAuditError("coverage matrix does not reproduce 65 normalized templates")

    flag_for_dependency = {
        "Enable": "has_enable",
        "Maintain": "has_maintain",
        "Handoff": "has_handoff",
        "Mutual Enable": "has_mutual_enable",
        "Synchronize": "has_synchronize",
        "Parallel-Join": "has_parallel_join",
        "Information": "has_information_dependency",
    }
    dependency_coverage = {}
    for dependency, flag in flag_for_dependency.items():
        matching = [row for row in rows if row[flag]]
        dependency_coverage[dependency] = {
            "tasks": len(matching),
            "fraction": len(matching) / len(rows),
            "primitive_only": sum(row["dependency_type_count"] == 1 for row in matching),
            "two_way_composition": sum(row["dependency_type_count"] == 2 for row in matching),
            "three_plus_composition": sum(row["dependency_type_count"] >= 3 for row in matching),
        }

    combinations = Counter(
        " + ".join(
            dependency
            for dependency, flag in flag_for_dependency.items()
            if row[flag]
        )
        for row in rows
    )
    summary = {
        "format": "fwcollab.task_coverage.v1",
        "scope": "Full-72 V4 benchmark; model outcomes excluded",
        "tasks": len(rows),
        "dependency_topology_classes": len({row["topology_id"] for row in rows}),
        "normalized_collaboration_templates": len({row["template_id"] for row in rows}),
        "semantic_signatures": len({row["semantic_signature"] for row in rows}),
        "spatial_signatures": len({row["spatial_signature"] for row in rows}),
        "dependency_coverage": dependency_coverage,
        "dependency_combinations": dict(sorted(combinations.items())),
        "distributions": {
            "mechanism_count": _distribution(row["mechanism_count"] for row in rows),
            "dag_depth": _distribution(row["dag_depth"] for row in rows),
            "handoff_count": _distribution(row["handoff_count"] for row in rows),
            "maintain_count": _distribution(row["maintain_count"] for row in rows),
            "role_switch_count": _distribution(row["role_switch_count"] for row in rows),
            "max_parallel_width": _distribution(row["max_parallel_width"] for row in rows),
        },
        "totals": {
            "controller_stages": sum(row["controller_handoff_count"] for row in rows),
            "capability_handoffs": sum(row["capability_handoff_count"] for row in rows),
            "evaluator_handoff_opportunities": sum(row["handoff_count"] for row in rows),
            "maintain_stages": sum(row["maintain_count"] for row in rows),
            "required_cross_agent_bottlenecks": sum(
                row["required_bottleneck_count"] for row in rows
            ),
            "transitive_cross_agent_node_dependencies": sum(
                _cross_agent_dependency_count(
                    load_json(_resolve(root, str(record["dag"])))
                )
                for record in records
            ),
        },
        "validity": {
            "witness_valid": sum(row["witness_valid"] for row in rows),
            "replay_valid": sum(row["replay_valid"] for row in rows),
            "all_nodes_bound": sum(bool(record["all_nodes_bound"]) for record in records),
            "all_edges_verified": sum(bool(record["all_edges_verified"]) for record in records),
            "all_stages_cross_agent_proved": necessity["counts"][
                "all_stages_cross_agent_proved"
            ],
        },
        "separate_extensions": {
            "C5_information_suite_tasks": 12,
            "included_in_this_matrix": False,
        },
    }
    return rows, summary


def _csv_text(rows: Sequence[Mapping[str, Any]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def _distribution_table(title: str, values: Mapping[str, int]) -> list[str]:
    lines = [f"### {title}", "", "| Value | Tasks |", "|---:|---:|"]
    lines.extend(f"| {value} | {count} |" for value, count in values.items())
    lines.append("")
    return lines


def _summary_markdown(summary: Mapping[str, Any]) -> str:
    coverage = summary["dependency_coverage"]
    lines = [
        "# FWCollab Full-72 Task Coverage",
        "",
        "This is a structure-only audit. No model outcome is used to define a label.",
        "",
        "## Headline",
        "",
        f"- Tasks: **{summary['tasks']}**",
        f"- Dependency topology classes: **{summary['dependency_topology_classes']}**",
        f"- Normalized collaboration templates: **{summary['normalized_collaboration_templates']}**",
        f"- Unique semantic/spatial signatures: **{summary['semantic_signatures']}/{summary['spatial_signatures']}**",
        "- Main-set dependency families covered: **4/7**",
        "- Separate C5 information suite: **12 tasks**, not merged into this matrix",
        "",
        "## Dependency coverage",
        "",
        "| Dependency | Tasks | Coverage | Primitive-only | 2-way composition | 3+ composition |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for dependency in MAIN_DEPENDENCIES:
        item = coverage[dependency]
        lines.append(
            f"| {dependency} | {item['tasks']} | {item['fraction']:.1%} | "
            f"{item['primitive_only']} | {item['two_way_composition']} | "
            f"{item['three_plus_composition']} |"
        )
    lines.extend(
        [
            "",
            "`Primitive-only`, `2-way`, and `3+` count how many of the seven dependency-family "
            "flags coexist in a task. Mutual Enable is treated as a task-level composite motif.",
            "",
            "## Exact dependency combinations",
            "",
            "| Combination | Tasks |",
            "|---|---:|",
        ]
    )
    for combination, count in summary["dependency_combinations"].items():
        lines.append(f"| {combination} | {count} |")
    lines.extend(["", "## Structural totals", ""])
    totals = summary["totals"]
    lines.extend(
        [
            f"- Controller-stage handoffs: **{totals['controller_stages']}**",
            f"- Additional collaborative capability handoffs: **{totals['capability_handoffs']}**",
            f"- Evaluator handoff opportunities: **{totals['evaluator_handoff_opportunities']}**",
            f"- Maintain stages: **{totals['maintain_stages']}**",
            f"- Verified required cross-agent bottlenecks: **{totals['required_cross_agent_bottlenecks']}**",
            "",
            "## Distributions",
            "",
        ]
    )
    labels = {
        "mechanism_count": "Mechanism count",
        "dag_depth": "DAG depth",
        "handoff_count": "Handoff count",
        "maintain_count": "Maintain count",
        "role_switch_count": "Role-switch count",
        "max_parallel_width": "Maximum DAG layer width",
    }
    for key, label in labels.items():
        lines.extend(_distribution_table(label, summary["distributions"][key]))
    validity = summary["validity"]
    lines.extend(
        [
            "## Validity checks",
            "",
            f"- Deterministic witnesses succeeded: **{validity['witness_valid']}/72**",
            f"- Fresh witness traces replayed exactly: **{validity['replay_valid']}/72**",
            f"- DAGs with all nodes bound: **{validity['all_nodes_bound']}/72**",
            f"- DAGs with all edges verified: **{validity['all_edges_verified']}/72**",
            f"- Tasks with proved cross-agent stage cuts: **{validity['all_stages_cross_agent_proved']}/72**",
            "",
            "## Coverage gaps",
            "",
            "- **Synchronize: 0/72.** Existing `synchronizes` DAG edges only join a path into "
            "team success; they do not impose a bounded same-round action window.",
            "- **Parallel-Join: 0/72.** Generic graph width and multi-controller `all(...)` "
            "do not establish two independent role-owned branches followed by an AND join.",
            "- **Information: 0/72.** Full-72 uses shared public state. Information dependence "
            "is covered only by the separate frozen 12-task C5 suite.",
            "",
            "## Field semantics",
            "",
            "- `handoff_count` is the evaluator-recognized opportunity count: controller stages "
            "plus collaborative capability capsules. It is not a count of literal `handoff` edge labels.",
            "- `maintain_count` counts controller stages with a required pressure plate.",
            "- `persistent_dependency=1` means at least one stage uses a latched lever or "
            "toggle; maintained pressure-plate state is represented separately by `has_maintain`.",
            "- `max_parallel_width` is a graph-layer width statistic, not evidence of Parallel-Join.",
            "- `single_agent_solvable=0` follows the two-role exit objective. The stronger columns "
            "`F_wait_blocks_teammate` and `W_wait_blocks_teammate` identify verified cross-agent cuts.",
            "- `topology_id` and `template_id` reproduce the frozen Benchmark v1 audit.",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(
    rows: Sequence[Mapping[str, Any]], summary: Mapping[str, Any], output_dir: Path
) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "task_coverage.csv"
    markdown_path = output_dir / "coverage_summary.md"
    json_path = output_dir / "coverage_summary.json"
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
        description="Audit structural dependency coverage in the frozen Full-72 benchmark."
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    output_dir = _resolve(root, args.output_dir)
    try:
        rows, summary = build_coverage(root)
        csv_path, markdown_path, json_path = write_outputs(rows, summary, output_dir)
    except (CoverageAuditError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "tasks": len(rows),
                "task_coverage_csv": str(csv_path),
                "coverage_summary_md": str(markdown_path),
                "coverage_summary_json": str(json_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
