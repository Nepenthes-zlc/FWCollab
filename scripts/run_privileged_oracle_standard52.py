"""Run a deterministic, certificate-supported privileged Standard-52 planner.

This is an offline planning reference, not a fair agent baseline.  The planner
may use frozen benchmark-authored witnesses, dependency metadata, and existing
successful certificates.  It never invokes a model or network provider.
"""

from __future__ import annotations

import csv
import hashlib
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fwcollab.c5 import run_c5_session  # noqa: E402
from fwcollab.symbolic.agents import (  # noqa: E402
    AgentDecision, CoordinationMessage, ScriptedPolicy,
)
from fwcollab.symbolic.map import load_symbol_map  # noqa: E402
from fwcollab.symbolic.runner import DualAgentSession, replay_trace  # noqa: E402
from fwcollab.symbolic.world import SymbolAction  # noqa: E402
from fwcollab.unified_eval import (  # noqa: E402
    evaluate_fwcollab_task, validate_unified_evaluation,
)

OUT = ROOT / "artifacts/evaluations/privileged_oracle_standard52_v1"
TRACE_OUT = ROOT / "artifacts/runs/privileged_oracle_standard52_v1"
TRACK_SLUG = {"Core-72": "core", "Information-12": "information", "Sync-8": "sync", "Join-8": "join"}


def load(path: str | Path) -> Any:
    path = Path(path); path = path if path.is_absolute() else ROOT / path
    return json.loads(path.read_text(encoding="utf-8-sig"))


def decision(move: str, message: CoordinationMessage | None = None) -> AgentDecision:
    return AgentDecision(
        action=SymbolAction(move=move, steps=0 if move == "WAIT" else 1),
        message=message, reason="privileged deterministic joint plan",
    )


def action_sequence_from_trace(relative: str) -> list[dict[str, list[Any]]]:
    trace = load(relative); result = []
    if trace.get("outcome") != "team_success":
        raise RuntimeError(f"certificate trace is not successful: {relative}")
    for item in trace["rounds"]:
        joint = {}
        for role in ("F", "W"):
            action = item["agents"][role]["decision"]["action"]
            joint[role] = [str(action["move"]), int(action["steps"])]
        result.append(joint)
    return result


def decisions_from_actions(actions: Sequence[Mapping[str, Sequence[Any]]]) -> dict[str, list[AgentDecision]]:
    return {
        role: [decision(str(item[role][0])) for item in actions]
        for role in ("F", "W")
    }


@dataclass(frozen=True)
class JointPlan:
    task_id: str
    track: str
    budget: int
    source: str
    known_certificate_rounds: int
    role_decisions: Mapping[str, Sequence[AgentDecision]]


class PrivilegedJointPlanner:
    """Deterministically select and replay the shortest frozen certificate.

    This implementation establishes a success upper-bound/reference at the
    frozen horizons.  It does not claim shortest-path optimality: for Core it
    uses the shortest already audited successful certificate, and for extension
    tracks it uses the frozen authored certificate.
    """

    def __init__(self) -> None:
        self.run_manifest = load("eval_private/leaderboard_v1/standard_52_run_manifest.json")
        self.benchmark = load("eval_private/benchmark_v2/manifest.json")
        self.task_by_id = {row["task_id"]: row for row in self.benchmark["tasks"]}
        self.core_witnesses = load("eval_private/spatial_curriculum_full_v4/witnesses.json")["maps"]
        self.sync_witnesses = load("eval_private/synchronize_8/witnesses.json")["tasks"]
        self.join_witnesses = load("eval_private/parallel_join_8/witnesses.json")["tasks"]
        self.c5_tasks = {row["task_id"]: row for row in load("eval_private/c5_information_12/manifest.json")["tasks"]}
        horizon_rows = list(csv.DictReader((ROOT / "artifacts/audits/leaderboard_horizon_feasibility_v1/per_task.csv").open(encoding="utf-8-sig")))
        self.horizon = {row["task_id"]: row for row in horizon_rows}
        if len(self.horizon) != 52 or not all(row["status"] == "PASS" for row in horizon_rows):
            raise RuntimeError("Standard-52 horizon audit is not 52/52 PASS")
        self.track_for = {task_id: track for track in self.run_manifest["tracks"] for task_id in track["task_ids"]}

    def plan(self, task_id: str) -> JointPlan:
        frozen_track = self.track_for[task_id]; suite = str(frozen_track["track"]); budget = int(frozen_track["budget_max"]); horizon = self.horizon[task_id]
        known = int(horizon["best_certified_rounds"]); source = str(horizon["certificate_source"])
        if suite == "Core-72":
            witness = self.core_witnesses[task_id]
            actions = action_sequence_from_trace(source) if source.endswith(".json") and source.startswith("artifacts/runs/") else witness
            if len(actions) != known:
                raise RuntimeError(f"Core plan/certificate length mismatch for {task_id}: {len(actions)} != {known}")
            policies = decisions_from_actions(actions)
        elif suite == "Sync-8":
            actions = self.sync_witnesses[task_id]["primary"]; policies = decisions_from_actions(actions)
        elif suite == "Join-8":
            actions = self.join_witnesses[task_id]["primary"]; policies = decisions_from_actions(actions)
        elif suite == "Information-12":
            policies = self._information_plan(self.c5_tasks[task_id])
        else:
            raise RuntimeError(f"unsupported suite {suite}")
        return JointPlan(task_id, suite, budget, source, known, policies)

    @staticmethod
    def _moves(*moves: str) -> list[AgentDecision]:
        return [decision(move) for move in moves]

    def _information_plan(self, task: Mapping[str, Any]) -> dict[str, list[AgentDecision]]:
        controller_rows = {"c_north": 2, "c_center": 4, "c_south": 6}
        target_rows = {"gate_alpha": 10, "gate_beta": 12, "gate_gamma": 14}
        row = controller_rows[str(task["answer_controller"])]; outward, back = ("UP", "DOWN") if row == 2 else ("DOWN", "UP")
        fire = [decision("WAIT")]
        if row != 4: fire += self._moves(outward, outward)
        fire += self._moves(*(["RIGHT"] * 10), *(["LEFT"] * 10))
        if row != 4: fire += self._moves(back, back)
        fire += self._moves("LEFT", "LEFT")
        target = str(task["required_actuator"]); target_row = target_rows[target]; outward, back = ("UP", "DOWN") if target_row == 10 else ("DOWN", "UP")
        info = CoordinationMessage(stage=target, status="INFO", fact=f"required actuator {target}", request=f"activate the controller mapped to {target}", until=f"{target} is open")
        water = [decision("RIGHT", info)]
        if target_row != 12: water += self._moves(outward, outward)
        water += self._moves(*(["RIGHT"] * 6), *(["WAIT"] * 6), *(["RIGHT"] * 8))
        if target_row != 12: water += self._moves(back, back)
        water += self._moves("RIGHT", "RIGHT")
        return {"F": fire, "W": water}

    def run(self, plan: JointPlan) -> dict[str, Any]:
        task = self.task_by_id[plan.task_id]; symbol_map = load_symbol_map(ROOT / task["map_path"]); policies = {role: ScriptedPolicy(list(plan.role_decisions[role]), name="privileged-oracle") for role in ("F", "W")}
        if plan.track == "Information-12":
            result = run_c5_session(symbol_map, policies, self.c5_tasks[plan.task_id], max_rounds=plan.budget)
        else:
            result = DualAgentSession(symbol_map, policies, max_rounds=plan.budget, planning_rounds=0, coordination_mode="emergent").run()
        trace = result.trace
        trace["privileged_oracle"] = {
            "format": "fwcollab.privileged_oracle.v1", "joint_planner": True,
            "deterministic": True, "certificate_source": plan.source,
            "fair_agent_baseline": False, "model_api_calls": 0,
        }
        return trace


def action_counts(trace: Mapping[str, Any]) -> dict[str, int]:
    counts = {"F_actions": 0, "W_actions": 0, "F_waits": 0, "W_waits": 0, "messages": 0}
    for item in trace["rounds"]:
        for role in ("F", "W"):
            move = item["agents"][role]["decision"]["action"]["move"]
            counts[f"{role}_waits"] += move == "WAIT"
            counts[f"{role}_actions"] += move != "WAIT"
            counts["messages"] += item["agents"][role]["decision"].get("message") is not None
    return {key: int(value) for key, value in counts.items()}


def main() -> None:
    planner = PrivilegedJointPlanner(); TRACE_OUT.mkdir(parents=True, exist_ok=True); rows = []
    for task_id in planner.run_manifest["ordered_task_ids"]:
        plan = planner.plan(task_id); task = planner.task_by_id[task_id]; trace = planner.run(plan)
        trace_path = TRACE_OUT / TRACK_SLUG[plan.track] / f"{task_id}.json"; trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        symbol_map = load_symbol_map(ROOT / task["map_path"]); replay = replay_trace(symbol_map, trace)
        unified = evaluate_fwcollab_task(task, trace, root=ROOT); validate_unified_evaluation(unified)
        counts = action_counts(trace); rounds = len(trace["rounds"]); gap = rounds - plan.known_certificate_rounds
        rows.append({
            "task_id": task_id, "track": plan.track, "budget": plan.budget,
            "outcome": trace["outcome"], "success": int(unified["success"]),
            "dependency_success": int(unified["dependency_success"]),
            "dag_completion": unified["dag_completion"], "progress_auc": unified["progress_auc"],
            "rounds_to_success": rounds if unified["success"] else "",
            "non_wait_action_count": counts["F_actions"] + counts["W_actions"],
            "F_action_count": counts["F_actions"], "W_action_count": counts["W_actions"],
            "wait_count": counts["F_waits"] + counts["W_waits"], "F_wait_count": counts["F_waits"], "W_wait_count": counts["W_waits"],
            "message_count": counts["messages"], "horizon_utilization": rounds / plan.budget,
            "best_existing_certificate_rounds": plan.known_certificate_rounds,
            "oracle_minus_certificate_rounds": gap, "certificate_source": plan.source,
            "replay_verified": int(bool(replay["ok"])), "unified_evaluator_valid": 1,
            "trace": trace_path.relative_to(ROOT).as_posix(),
            "trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
            "planner_limitation": "",
        })
    if len(rows) != 52:
        raise RuntimeError("oracle did not emit 52 rows")
    track_rows = []
    for track in ("Core-72", "Information-12", "Sync-8", "Join-8", "Standard-52"):
        subset = rows if track == "Standard-52" else [row for row in rows if row["track"] == track]
        success_rounds = [int(row["rounds_to_success"]) for row in subset if row["success"]]
        track_rows.append({
            "track": track, "tasks": len(subset), "successes": sum(row["success"] for row in subset),
            "success_rate": sum(row["success"] for row in subset) / len(subset),
            "mean_dag_completion": statistics.fmean(float(row["dag_completion"]) for row in subset),
            "mean_progress_auc": statistics.fmean(float(row["progress_auc"]) for row in subset),
            "median_rounds_to_success": statistics.median(success_rounds) if success_rounds else None,
            "max_rounds_to_success": max(success_rounds) if success_rounds else None,
            "median_oracle_minus_certificate_rounds": statistics.median(int(row["oracle_minus_certificate_rounds"]) for row in subset),
            "max_oracle_minus_certificate_rounds": max(int(row["oracle_minus_certificate_rounds"]) for row in subset),
        })
    failures = [row for row in rows if not row["success"] or not row["dependency_success"] or not row["replay_verified"]]
    result = {
        "format": "fwcollab.privileged_oracle_standard52.v1",
        "baseline_type": "privileged planning upper-bound/reference; not a fair agent baseline",
        "planner": "deterministic certificate-supported joint planner",
        "optimality_claim": False,
        "model_api_calls": 0, "network_calls": 0,
        "horizons": {"Core-72": 200, "Information-12": 30, "Sync-8": 80, "Join-8": 80},
        "horizon_audit_52_of_52_pass": True,
        "tasks": 52, "successes": sum(row["success"] for row in rows),
        "all_replay_verified": all(row["replay_verified"] for row in rows),
        "all_unified_evaluator_valid": all(row["unified_evaluator_valid"] for row in rows),
        "planner_implementation_failures": failures,
        "track_summary": track_rows, "per_task": rows,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (OUT / "per_task.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0])); writer.writeheader(); writer.writerows(rows)
    lines = [
        "# Standard-52 privileged oracle reference", "",
        "**This is a privileged planning upper-bound/reference baseline, not a fair agent baseline.** It has benchmark-authored task knowledge and selects deterministic joint action plans from frozen witnesses and previously audited successful certificates. It uses no model, network request, stochastic policy, task mutation, or evaluator change. It establishes an attainable performance ceiling at the frozen horizons; it does **not** claim action-count optimality.", "",
        f"Status: **{sum(row['success'] for row in rows)}/52 success**, **{sum(row['replay_verified'] for row in rows)}/52 replay verified**, **{sum(row['unified_evaluator_valid'] for row in rows)}/52 unified evaluator valid**. Model/API calls: **0**.", "",
        "| Track | Success | Completion | AUC | Median rounds | Max rounds | Median oracle-certificate gap | Max gap |", "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in track_rows:
        lines.append(f"| {row['track']} | {row['successes']}/{row['tasks']} | {row['mean_dag_completion']:.6f} | {row['mean_progress_auc']:.6f} | {row['median_rounds_to_success']:g} | {row['max_rounds_to_success']} | {row['median_oracle_minus_certificate_rounds']:g} | {row['max_oracle_minus_certificate_rounds']} |")
    lines += ["", "## Efficiency interpretation", "", "`oracle_minus_certificate_rounds` compares the regenerated oracle trace with the shortest existing locally audited certificate used by the horizon audit. A zero gap means faithful reproduction of that certificate, not proof that no shorter plan exists. `non_wait_action_count` counts submitted non-WAIT role actions; WAIT counts and role-specific action counts are reported per task.", "", "## Planner failures", ""]
    if failures:
        lines += ["| Task | Reason | Best certificate | Limitation |", "|---|---|---:|---|"]
        for row in failures: lines.append(f"| {row['task_id']} | {row['outcome']} / dependency={row['dependency_success']} / replay={row['replay_verified']} | {row['best_existing_certificate_rounds']} | {row['planner_limitation'] or 'unclassified'} |")
    else:
        lines.append("No oracle implementation failure occurred.")
    lines += ["", "Replayable traces are under `artifacts/runs/privileged_oracle_standard52_v1/`.", ""]
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"successes": result["successes"], "tasks": 52, "failures": len(failures), "model_api_calls": 0}))


if __name__ == "__main__": main()
