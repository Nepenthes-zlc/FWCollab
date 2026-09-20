from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from fwcollab.symbolic.dag import (
    compare_state_dags,
    graph_signature,
    load_json,
    render_state_dag_svg,
    save_manifest_dag_artifacts,
    validate_curriculum_catalog,
    validate_state_dag,
)
from fwcollab.symbolic.dag_curriculum import build_curriculum_catalog, curriculum_summary, write_curriculum

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "eval_private" / "dag_curriculum" / "catalog.json"
EXAMPLE_PATH = ROOT / "examples" / "dag_candidate.json"


def test_generated_curriculum_has_requested_distribution_and_coverage() -> None:
    catalog = build_curriculum_catalog()
    validate_curriculum_catalog(catalog)
    summary = curriculum_summary(catalog)

    assert summary["count"] == 72
    assert summary["difficulty_distribution"] == {"L1": 4, "L2": 8, "L3": 12, "L4": 16, "L5": 18, "L6": 14}
    assert summary["diagnostic"] == 12
    assert summary["scored"] == 60
    assert summary["typed_unique"] == 72
    assert summary["max_topology_reuse"] <= 3
    assert set(summary["mechanism_coverage"]) == {f"M{index:02d}" for index in range(1, 31)}


def test_committed_catalog_matches_deterministic_generator() -> None:
    committed = load_json(CATALOG_PATH)
    generated = build_curriculum_catalog()
    validate_curriculum_catalog(committed)
    assert committed == generated


def test_write_curriculum_creates_individual_json_and_svg_gallery(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.json"
    graph_dir = tmp_path / "graphs"
    artifact_dir = tmp_path / "artifacts"
    catalog = write_curriculum(catalog_path, graph_dir, artifact_dir)

    assert catalog_path.is_file()
    assert len(list(graph_dir.glob("L*/*.json"))) == 72
    assert len(list(artifact_dir.glob("L*/*.svg"))) == 72
    assert (artifact_dir / "index.html").is_file()
    assert "data:image/svg+xml;base64" in (artifact_dir / "index.html").read_text(encoding="utf-8")
    assert Counter(graph["difficulty"] for graph in catalog["dags"])["L6"] == 14


def test_validator_rejects_cycle() -> None:
    graph = copy.deepcopy(build_curriculum_catalog()["dags"][0])
    start = next(node["id"] for node in graph["nodes"] if node["predicate"]["op"] == "start")
    success = next(node["id"] for node in graph["nodes"] if node["predicate"]["op"] == "team_success")
    graph["edges"].append({"from": success, "to": start, "relation": "enables"})
    with pytest.raises(ValueError, match="cycle"):
        validate_state_dag(graph)


def test_comparison_ignores_text_ids_order_phase_numbers_and_agent_swap() -> None:
    reference = copy.deepcopy(build_curriculum_catalog()["dags"][20])
    candidate = copy.deepcopy(reference)
    candidate["id"] = "agent-generated-candidate"
    candidate["title"] = "Completely different prose"
    candidate["summary"] = "Natural language is display-only."
    rename = {node["id"]: f"agent_node_{index}" for index, node in enumerate(reversed(candidate["nodes"]))}
    for node in candidate["nodes"]:
        node["id"] = rename[node["id"]]
        node["label"] = f"free text {node['id']}"
        node["predicate"]["phase"] += 100
        if node["owner"] == "agent_a":
            node["owner"] = "agent_b"
        elif node["owner"] == "agent_b":
            node["owner"] = "agent_a"
    for edge in candidate["edges"]:
        edge["from"] = rename[edge["from"]]
        edge["to"] = rename[edge["to"]]
    candidate["nodes"].reverse()
    candidate["edges"].reverse()

    result = compare_state_dags(reference, candidate)
    assert result["topology_equivalent"] is True
    assert result["typed_equivalent"] is True
    assert result["topology_score"] == 1.0
    assert result["typed_score"] == 1.0


def test_comparison_separates_topology_from_controlled_state_semantics() -> None:
    reference = copy.deepcopy(build_curriculum_catalog()["dags"][20])
    candidate = copy.deepcopy(reference)
    candidate["id"] = "wrong-state-candidate"
    changed = next(node for node in candidate["nodes"] if node["predicate"]["motif"] != "SYSTEM")
    changed["predicate"]["state"] = "incorrect_state"

    result = compare_state_dags(reference, candidate)
    assert result["topology_equivalent"] is True
    assert result["typed_equivalent"] is False
    assert result["topology_score"] == 1.0
    assert result["typed_score"] < 1.0


def test_public_candidate_example_and_svg_are_valid() -> None:
    graph = load_json(EXAMPLE_PATH)
    validate_state_dag(graph)
    rendered = render_state_dag_svg(graph)
    assert rendered.count('<g class="state-node"') == len(graph["nodes"])
    assert "http://www.w3.org/2000/svg" in rendered
    assert "https://" not in rendered


def test_manifest_dag_gallery_links_individual_svg_files(tmp_path: Path) -> None:
    source = build_curriculum_catalog()["dags"][0]
    dag_path = tmp_path / "source.json"
    dag_path.write_text(json.dumps(source), encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({"records": [{"id": "task-1", "difficulty": source["difficulty"], "dag": str(dag_path)}]}),
        encoding="utf-8",
    )

    generated = save_manifest_dag_artifacts(manifest_path, tmp_path / "gallery")

    assert len(generated) == 2
    assert generated[0].suffix == ".svg"
    html = generated[-1].read_text(encoding="utf-8")
    assert generated[0].relative_to(generated[-1].parent).as_posix() in html
    assert "data-level=\"L1\"" in html
