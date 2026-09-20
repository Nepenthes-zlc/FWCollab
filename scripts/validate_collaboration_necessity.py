"""Validate permanent-WAIT and structural no-bypass claims for all 72 tasks."""

from __future__ import annotations

import json
from collections import Counter, deque
from pathlib import Path
from typing import Any

from fwcollab.symbolic.map import Position, load_symbol_map
from fwcollab.symbolic.world import DIRECTIONS


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "eval_private" / "spatial_curriculum_full_v4" / "manifest.json"
OUT = ROOT / "artifacts" / "evaluations" / "collaboration_necessity_v1"


def _geometric_reachability(symbol_map, start: Position) -> set[Position]:
    """Optimistic reachability with every actuator treated as open.

    This deliberately removes dynamic gate constraints. If a teammate still
    cannot reach a controller, lane separation is a structural fact rather
    than a consequence of the evaluated controller state.
    """

    seen = {start}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for delta in DIRECTIONS.values():
            target = Position(current.row + delta[0], current.col + delta[1])
            if target in seen or symbol_map.cell(target) == "#":
                continue
            seen.add(target)
            queue.append(target)
    return seen


def _stage_certificate(record: dict[str, Any], symbol_map, stage: dict[str, Any]) -> dict[str, Any]:
    supporter = stage["supporter"]
    traveler = stage["traveler"]
    traveler_start = symbol_map.unique_position(traveler)
    optimistic = _geometric_reachability(symbol_map, traveler_start)
    controller_positions = [Position(*controller["position"]) for controller in stage["controllers"]]
    inaccessible = all(position not in optimistic for position in controller_positions)
    controller_ids = {controller["id"] for controller in stage["controllers"]}
    actuator_id = stage["actuator"]["id"]
    actuator = next(item for item in symbol_map.actuators if item.id == actuator_id)
    exact_wiring = set(actuator.controlled_by.inputs) == controller_ids
    plate_acceptance = all(
        controller["kind"] != "plate"
        or next(item for item in symbol_map.controllers if item.id == controller["id"]).accepts == {supporter}
        for controller in stage["controllers"]
    )
    cut_evidence = any(
        item.get("verified")
        and item.get("method") == "single_lane_corridor_cut"
        and item.get("from") in {node["id"] for node in json.loads((ROOT / record["dag"]).read_text(encoding="utf-8"))["nodes"] if node["predicate"]["state"] == f"{actuator_id}_on"}
        for item in record["edge_evidence"]
    )
    valid = supporter != traveler and inaccessible and exact_wiring and plate_acceptance and cut_evidence
    return {
        "stage": stage["stage"],
        "supporter": supporter,
        "traveler": traveler,
        "actuator_id": actuator_id,
        "teammate_cannot_reach_controllers_even_if_all_gates_open": inaccessible,
        "actuator_uses_exact_controller_set": exact_wiring,
        "role_specific_plate_acceptance": plate_acceptance,
        "verified_single_lane_corridor_cut": cut_evidence,
        "cross_agent_dependency_proved": valid,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    results = []
    for record in manifest["records"]:
        symbol_map = load_symbol_map(record["map"])
        certificates = [_stage_certificate(record, symbol_map, stage) for stage in record["stages"]]
        supporters = {item["supporter"] for item in certificates if item["cross_agent_dependency_proved"]}
        fixed_wait = {}
        for role in ("F", "W"):
            fixed_wait[role] = {
                "team_success_impossible": symbol_map.unique_position(role) != symbol_map.unique_position(role.lower()),
                "reason": "fixed role remains off its required role-specific exit",
                "also_blocks_teammate_at_verified_cross_agent_cut": role in supporters,
            }
        results.append({
            "task_id": record["id"],
            "difficulty": record["difficulty"],
            "stage_certificates": certificates,
            "at_least_one_cross_agent_dependency_proved": bool(certificates) and all(item["cross_agent_dependency_proved"] for item in certificates),
            "support_roles_with_proved_dependency": sorted(supporters),
            "bidirectional_assistance_proved": supporters == {"F", "W"},
            "permanent_wait": fixed_wait,
        })

    counts = Counter()
    counts["tasks"] = len(results)
    counts["all_stages_cross_agent_proved"] = sum(item["at_least_one_cross_agent_dependency_proved"] for item in results)
    counts["bidirectional_assistance_proved"] = sum(item["bidirectional_assistance_proved"] for item in results)
    counts["F_wait_team_unsolvable"] = sum(item["permanent_wait"]["F"]["team_success_impossible"] for item in results)
    counts["W_wait_team_unsolvable"] = sum(item["permanent_wait"]["W"]["team_success_impossible"] for item in results)
    counts["F_wait_blocks_teammate"] = sum(item["permanent_wait"]["F"]["also_blocks_teammate_at_verified_cross_agent_cut"] for item in results)
    counts["W_wait_blocks_teammate"] = sum(item["permanent_wait"]["W"]["also_blocks_teammate_at_verified_cross_agent_cut"] for item in results)
    payload = {
        "format": "fwcollab.collaboration_necessity.v1",
        "method": {
            "permanent_wait": "exact terminal-rule proof: the fixed role never reaches its required exit",
            "no_bypass": "optimistic all-gates-open geometric separation + exact controller wiring + role-specific plate acceptance + verified corridor cut",
        },
        "claim_boundary": "Permanent-WAIT team unsolvability alone is partly induced by the two-exit terminal rule; cross-agent no-bypass and bidirectional assistance are reported separately.",
        "counts": dict(counts),
        "tasks": results,
    }
    (OUT / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Collaboration necessity and no-bypass validation",
        "",
        f"- F permanently WAIT: team success impossible in **{counts['F_wait_team_unsolvable']}/72** tasks.",
        f"- W permanently WAIT: team success impossible in **{counts['W_wait_team_unsolvable']}/72** tasks.",
        f"- Every declared controller stage has a proved cross-agent no-bypass cut in **{counts['all_stages_cross_agent_proved']}/72** tasks.",
        f"- Bidirectional assistance (both roles control at least one gate needed by the teammate) is proved in **{counts['bidirectional_assistance_proved']}/72** tasks.",
        f"- F's inactivity directly blocks the teammate in **{counts['F_wait_blocks_teammate']}/72** tasks; W's inactivity does so in **{counts['W_wait_blocks_teammate']}/72** tasks.",
        "",
        "## Interpretation boundary",
        "",
        "The 72/72 permanent-WAIT result is not, by itself, strong evidence of collaboration because the terminal rule requires both role-specific exits. The stronger dataset-validity result is the controller-stage certificate: the traveler cannot geometrically reach the teammate's controllers even under the optimistic assumption that every gate is open, and the controlled actuator is a verified single-lane corridor cut.",
        "",
        "Bidirectional assistance is intentionally reported separately; tasks that require one-way assistance are collaborative but not mutual-unlock tasks.",
    ]
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if counts["all_stages_cross_agent_proved"] != 72:
        raise SystemExit("not every task passed structural no-bypass validation")
    print(json.dumps(dict(counts)))


if __name__ == "__main__":
    main()
