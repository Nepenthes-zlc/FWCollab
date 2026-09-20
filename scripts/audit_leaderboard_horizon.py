"""Pure-local Standard-52 frozen-horizon feasibility audit."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import deque
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fwcollab.c5 import evaluate_c5_trace, run_c5_session  # noqa: E402
from fwcollab.symbolic.agents import AgentDecision, CoordinationMessage, ScriptedPolicy  # noqa: E402
from fwcollab.symbolic.collaboration_eval import evaluate_collaboration_trace  # noqa: E402
from fwcollab.symbolic.extension_eval import evaluate_extension_trace  # noqa: E402
from fwcollab.symbolic.map import load_symbol_map  # noqa: E402
from fwcollab.symbolic.runner import DualAgentSession, replay_trace  # noqa: E402
from fwcollab.symbolic.world import SymbolAction  # noqa: E402
from fwcollab.unified_eval import evaluate_fwcollab_task, validate_unified_evaluation  # noqa: E402

OUT = ROOT / "artifacts/audits/leaderboard_horizon_feasibility_v1"
FIELDS = [
    "task_id", "track", "budget", "existing_certificate_rounds",
    "best_certified_rounds", "success", "replay_valid", "evaluator_valid",
    "within_budget", "certificate_source", "optimistic_movement_lower_bound", "status",
]


def _load(path: str | Path) -> Any:
    return json.loads((ROOT / path).read_text(encoding="utf-8-sig"))


def _decision(move: str, message: CoordinationMessage | None = None) -> AgentDecision:
    return AgentDecision(action=SymbolAction(move=move, steps=0 if move == "WAIT" else 1), message=message, reason="offline horizon certificate")


def _trace(symbol_map, actions: Sequence[Mapping[str, Sequence[Any]]]) -> dict[str, Any]:
    policies = {role: ScriptedPolicy([_decision(str(item[role][0])) for item in actions], name="offline-horizon") for role in ("F", "W")}
    return DualAgentSession(symbol_map, policies, max_rounds=len(actions), planning_rounds=0, coordination_mode="emergent").run().trace


def _moves(*moves: str) -> list[AgentDecision]:
    return [_decision(move) for move in moves]


def _c5_trace(task: Mapping[str, Any]) -> dict[str, Any]:
    controller_rows = {"c_north": 2, "c_center": 4, "c_south": 6}
    target_rows = {"gate_alpha": 10, "gate_beta": 12, "gate_gamma": 14}
    row = controller_rows[str(task["answer_controller"])]
    outward, back = ("UP", "DOWN") if row == 2 else ("DOWN", "UP")
    fire = [_decision("WAIT")]
    if row != 4: fire += _moves(outward, outward)
    fire += _moves(*(["RIGHT"] * 10), *(["LEFT"] * 10))
    if row != 4: fire += _moves(back, back)
    fire += _moves("LEFT", "LEFT")
    target = str(task["required_actuator"]); target_row = target_rows[target]
    outward, back = ("UP", "DOWN") if target_row == 10 else ("DOWN", "UP")
    message = CoordinationMessage(stage=target, status="INFO", fact=f"required actuator {target}", request=f"activate the controller mapped to {target}", until=f"{target} is open")
    water = [_decision("RIGHT", message)]
    if target_row != 12: water += _moves(outward, outward)
    water += _moves(*(["RIGHT"] * 6), *(["WAIT"] * 6), *(["RIGHT"] * 8))
    if target_row != 12: water += _moves(back, back)
    water += _moves("RIGHT", "RIGHT")
    symbol_map = load_symbol_map(ROOT / "eval_private/c5_information_12" / str(task["map"]))
    return run_c5_session(symbol_map, {"F": ScriptedPolicy(fire), "W": ScriptedPolicy(water)}, task, max_rounds=30).trace


def _optimistic_lower_bound(symbol_map) -> int | None:
    """Lower bound with every non-wall cell freely passable.

    This relaxes doors/platforms/controllers, affinity hazards, one-way rules,
    movables, portals and all coordination. The maximum of the two independent
    shortest paths is therefore no larger than any legal simultaneous solution.
    """
    distances = []
    for role, goal_symbol in (("F", "f"), ("W", "w")):
        start, goal = symbol_map.unique_position(role), symbol_map.unique_position(goal_symbol)
        queue = deque([((start.row, start.col), 0)]); seen = {(start.row, start.col)}; found = None
        while queue:
            (row, col), distance = queue.popleft()
            if (row, col) == (goal.row, goal.col): found = distance; break
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = row + dr, col + dc
                if nxt not in seen and 0 <= nxt[0] < symbol_map.height and 0 <= nxt[1] < symbol_map.width and symbol_map.rows[nxt[0]][nxt[1]] != "#":
                    seen.add(nxt); queue.append((nxt, distance + 1))
        if found is None: return None
        distances.append(found)
    return max(distances)


def _best_historical_core(task_ids: set[str]) -> dict[str, tuple[int, str]]:
    best: dict[str, tuple[int, str]] = {}
    phase2 = ROOT / "artifacts/evaluations/phase2_v1/per_episode.csv"
    if phase2.is_file():
        with phase2.open(encoding="utf-8", newline="") as handle:
            for item in csv.DictReader(handle):
                task_id = item.get("task_id", "")
                if task_id not in task_ids or item.get("outcome") != "team_success": continue
                rounds, relative = int(item["executed_rounds"]), str(item["trace"])
                if task_id not in best or rounds < best[task_id][0]: best[task_id] = (rounds, relative)
    for summary_path in (ROOT / "artifacts/runs").glob("spatial_curriculum_full_v4_*/summary.json"):
        try: summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception: continue
        for item in summary.get("results", []):
            task_id = str(item.get("dag_id", ""))
            if task_id not in task_ids or item.get("outcome") != "team_success": continue
            rounds, relative = int(item["rounds"]), str(item["trace"])
            if task_id not in best or rounds < best[task_id][0]: best[task_id] = (rounds, relative)
    return best


def audit(*, core_budget_override: int | None = None) -> list[dict[str, Any]]:
    run_manifest = _load("eval_private/leaderboard_v1/standard_52_run_manifest.json")
    benchmark = _load("eval_private/benchmark_v2/manifest.json")
    by_task = {item["task_id"]: item for item in benchmark["tasks"]}
    full_manifest = _load("eval_private/spatial_curriculum_full_v4/manifest.json")
    core_source = {item["id"]: item for item in full_manifest["records"]}
    core_witnesses = _load("eval_private/spatial_curriculum_full_v4/witnesses.json")["maps"]
    c5_manifest = _load("eval_private/c5_information_12/manifest.json")
    c5_source = {item["task_id"]: item for item in c5_manifest["tasks"]}
    extension_sources = {
        "Sync-8": {item["id"]: item for item in _load("eval_private/synchronize_8/manifest.json")["records"]},
        "Join-8": {item["id"]: item for item in _load("eval_private/parallel_join_8/manifest.json")["records"]},
    }
    extension_witnesses = {
        "Sync-8": _load("eval_private/synchronize_8/witnesses.json")["tasks"],
        "Join-8": _load("eval_private/parallel_join_8/witnesses.json")["tasks"],
    }
    core_ids = set(run_manifest["tracks"][0]["task_ids"]); historical = _best_historical_core(core_ids)
    rows = []
    for frozen_track in run_manifest["tracks"]:
        track = frozen_track["selection"]; budget = int(frozen_track["budget_max"]); suite = frozen_track["track"]
        if suite == "Core-72" and core_budget_override is not None:
            budget = core_budget_override
        for task_id in frozen_track["task_ids"]:
            item = by_task[task_id]; symbol_map = load_symbol_map(ROOT / item["map_path"]); lower = None
            if suite == "Core-72":
                source = core_source[task_id]; actions = core_witnesses[task_id]; trace = _trace(symbol_map, actions)
                existing = len(actions); best_rounds, best_source = existing, item["witness_path"]
                if task_id in historical and historical[task_id][0] < best_rounds:
                    best_rounds, best_source = historical[task_id]
                    trace = json.loads((ROOT / best_source).read_text(encoding="utf-8"))
                raw = evaluate_collaboration_trace(source, _load(item["dag_path"]), trace)
                evaluator_valid = raw["summary"]["dag_completion"] == 1.0
                lower = _optimistic_lower_bound(symbol_map)
            elif suite == "Information-12":
                source = c5_source[task_id]; trace = _c5_trace(source); raw = evaluate_c5_trace(source, trace)
                evaluator_valid = raw["dag_completion"] == 1.0 and raw["clean_information_handoff"]
                existing = int(source["budget_certificate"]["correct_witness_rounds"]); best_rounds = existing; best_source = "eval_private/c5_information_12/manifest.json#budget_certificate"
            else:
                source = extension_sources[suite][task_id]; actions = extension_witnesses[suite][task_id]["primary"]; trace = _trace(symbol_map, actions)
                raw = evaluate_extension_trace(source, _load(item["dag_path"]), trace); evaluator_valid = bool(raw["valid"] and raw["dag_completion"] == 1.0)
                existing = len(actions); best_rounds = existing; best_source = item["witness_path"] + f"#tasks/{task_id}/primary"
            success = trace.get("outcome") == "team_success"; replay_valid = bool(replay_trace(symbol_map, trace)["ok"])
            unified = evaluate_fwcollab_task(item, trace, root=ROOT); validate_unified_evaluation(unified)
            evaluator_valid = bool(evaluator_valid and unified["success"] and unified["dag_completion"] == 1.0)
            within = bool(success and replay_valid and evaluator_valid and best_rounds <= budget)
            if within: status = "PASS"
            elif lower is not None and lower > budget: status = "HORIZON_FEASIBILITY_FAIL_PROVEN_LOWER_BOUND"
            else: status = "HORIZON_FEASIBILITY_FAIL_NO_WITHIN_BUDGET_CERTIFICATE"
            rows.append({
                "task_id": task_id, "track": track, "budget": budget,
                "existing_certificate_rounds": existing, "best_certified_rounds": best_rounds,
                "success": success, "replay_valid": replay_valid, "evaluator_valid": evaluator_valid,
                "within_budget": within, "certificate_source": best_source,
                "optimistic_movement_lower_bound": "" if lower is None else lower, "status": status,
            })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-budget", type=int, default=None)
    args = parser.parse_args()
    if args.core_budget is not None and args.core_budget <= 0:
        raise ValueError("--core-budget must be positive")
    rows = audit(core_budget_override=args.core_budget); OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "per_task.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS); writer.writeheader(); writer.writerows(rows)
    track_order = ("Diagnostic-24", "Information-12", "Sync-8", "Join-8")
    stats = {}
    for track in track_order:
        group = [row for row in rows if row["track"] == track]; values = [int(row["best_certified_rounds"]) for row in group]
        stats[track] = (sum(bool(row["within_budget"]) for row in group), len(group), max(values), statistics.median(values))
    blockers = [row for row in rows if row["status"] == "HORIZON_FEASIBILITY_FAIL_PROVEN_LOWER_BOUND"]
    uncertified = [row for row in rows if row["status"] == "HORIZON_FEASIBILITY_FAIL_NO_WITHIN_BUDGET_CERTIFICATE"]
    total_pass = sum(bool(row["within_budget"]) for row in rows)
    overall_status = "PASS" if total_pass == 52 else "HORIZON_FEASIBILITY_FAIL"
    budgets = {track: next(int(row["budget"]) for row in rows if row["track"] == track) for track in track_order}
    lines = [
        "# Standard-52 horizon feasibility audit", "", f"Status: **{overall_status}**", "",
        f"The fixed {budgets['Diagnostic-24']}/30/80/80 horizons were audited without changing any task, map, DAG, witness, evaluator, prompt, or result. Model/API calls: **0**.", "",
        "| Track | Within budget | Budget | Maximum best certified rounds | Median best certified rounds |", "|---|---:|---:|---:|---:|",
    ]
    for track in track_order:
        passed, total, maximum, median = stats[track]; lines.append(f"| {track} | {passed}/{total} | {budgets[track]} | {maximum} | {median:g} |")
    lines += ["", f"Total: **{total_pass}/52 within fixed horizon**." + (" Horizon feasibility is complete." if total_pass == 52 else " Formal leaderboard readiness must stop."), "", "## Decisive infeasibility certificates", "", "For Core, an optimistic grid shortest-path lower bound treats every non-wall cell—including closed actuators, hazards, controllers, objects, and special terrain—as freely passable and removes all coordination requirements. The maximum independent F/W distance is therefore a valid lower bound on simultaneous completion rounds. Tasks exceeding the current Core horizon under this relaxation:", "", "| Task | Fixed horizon | Optimistic lower bound | Minimum gap |", "|---|---:|---:|---:|"]
    for row in blockers: lines.append(f"| `{row['task_id']}` | {row['budget']} | {row['optimistic_movement_lower_bound']} | +{int(row['optimistic_movement_lower_bound']) - int(row['budget'])} |")
    if not blockers:
        lines.append("| None | — | — | — |")
    lines += ["", "## Remaining over-budget certificates", "", f"**{len(uncertified)}** Core tasks have no locally certified success within the current fixed horizon. Their best existing successful traces are listed in `per_task.csv`; this audit does not claim those traces are shortest.", "", "`best_certified_rounds` means the shortest successful, evaluator-valid trajectory currently present in frozen witnesses or existing local run artifacts. Historical model traces are used only as offline solvability certificates; they are not new experiments. The audit did not modify or freeze optimized witnesses.", ""]
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"{overall_status} {total_pass}/52; model/API calls=0")


if __name__ == "__main__":
    main()
