"""Information-asymmetric C5 mini-suite support.

This module is deliberately separate from the frozen Phase-II runner.  It
projects one authoritative symbolic state into two role-private observations
and provides structural certificates for the information split.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from fwcollab.symbolic.agents import Policy, PolicyReply
from fwcollab.symbolic.map import SymbolMap
from fwcollab.symbolic.runner import DualAgentSession, SessionResult
from fwcollab.symbolic.world import Role


class C5ValidationError(ValueError):
    """Raised when a C5 task collection does not establish information need."""


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def correct_controller(task: Mapping[str, object]) -> str:
    wiring = task.get("wiring")
    required = task.get("required_actuator")
    if not isinstance(wiring, dict) or not isinstance(required, str):
        raise C5ValidationError("task requires object wiring and string required_actuator")
    matches = [controller for controller, actuator in wiring.items() if actuator == required]
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise C5ValidationError("joint private information must identify exactly one controller")
    return matches[0]


def validate_information_suite(tasks: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Prove the finite-world C5 condition for every equivalence class.

    F observes ``f_view_id`` plus the wiring permutation; W observes
    ``w_view_id`` plus the required actuator.  Every F-equivalence class must
    admit multiple required controllers, every W-equivalence class must admit
    multiple required controllers, and their union must identify one.
    """

    if not 8 <= len(tasks) <= 12:
        raise C5ValidationError("the C5 mini-suite must contain 8 to 12 tasks")
    ids: set[str] = set()
    f_classes: dict[str, set[str]] = {}
    w_classes: dict[str, set[str]] = {}
    joint_views: set[tuple[str, str]] = set()
    for task in tasks:
        task_id = task.get("task_id")
        f_view_id = task.get("f_view_id")
        w_view_id = task.get("w_view_id")
        if not all(isinstance(value, str) and value for value in (task_id, f_view_id, w_view_id)):
            raise C5ValidationError("task_id, f_view_id and w_view_id must be non-empty strings")
        if task_id in ids:
            raise C5ValidationError(f"duplicate task_id {task_id}")
        ids.add(task_id)
        answer = correct_controller(task)
        f_classes.setdefault(f_view_id, set()).add(answer)
        w_classes.setdefault(w_view_id, set()).add(answer)
        joint = (f_view_id, w_view_id)
        if joint in joint_views:
            raise C5ValidationError(f"duplicate joint information state {joint}")
        joint_views.add(joint)
    deficient_f = {key: sorted(values) for key, values in f_classes.items() if len(values) < 2}
    deficient_w = {key: sorted(values) for key, values in w_classes.items() if len(values) < 2}
    if deficient_f:
        raise C5ValidationError(f"F alone entails an answer in classes {deficient_f}")
    if deficient_w:
        raise C5ValidationError(f"W alone entails an answer in classes {deficient_w}")
    return {
        "format": "fwcollab.c5_information_certificate.v1",
        "valid": True,
        "task_count": len(tasks),
        "f_equivalence_classes": len(f_classes),
        "w_equivalence_classes": len(w_classes),
        "joint_states": len(joint_views),
        "claims": {
            "F_alone_entails_solution": False,
            "W_alone_entails_solution": False,
            "F_union_W_entails_unique_solution": True,
        },
    }


def _mask_rows(rows: object, visible_rows: range) -> object:
    if not isinstance(rows, list):
        return copy.deepcopy(rows)
    visible = set(visible_rows)
    return [row if index in visible else "?" * len(row) for index, row in enumerate(rows)]


def project_role_observation(
    observation: Mapping[str, object], task: Mapping[str, object], role: Role
) -> dict[str, object]:
    """Return the exact role-private DTO supplied to a C5 policy."""

    if role not in {"F", "W"}:
        raise C5ValidationError(f"unknown role {role}")
    result = copy.deepcopy(dict(observation))
    result["format"] = "fwcollab.c5_agent_observation.v1"
    result["map_id"] = "C5-HIDDEN"
    result["information_condition"] = "role_private_complementary"
    map_value = result.get("map")
    visible_rows = range(1, 8) if role == "F" else range(9, 16)
    if isinstance(map_value, dict):
        map_value["current_rows"] = _mask_rows(map_value.get("current_rows"), visible_rows)
        map_value["terrain_rows"] = _mask_rows(map_value.get("terrain_rows"), visible_rows)

    mechanisms = result.get("mechanisms")
    if isinstance(mechanisms, dict):
        mechanisms.pop("exits", None)
        if role == "F":
            mechanisms.pop("actuators", None)
            mechanisms["private_wiring"] = copy.deepcopy(task["wiring"])
            mechanisms["own_exit"] = {"F": copy.deepcopy(task["f_exit"])}
        else:
            mechanisms.pop("controllers", None)
            actuators = mechanisms.get("actuators")
            if isinstance(actuators, dict):
                for actuator in actuators.values():
                    if isinstance(actuator, dict):
                        actuator.pop("controlled_by", None)
            mechanisms["own_exit"] = {"W": copy.deepcopy(task["w_exit"])}

    dynamic = result.get("dynamic_state")
    if isinstance(dynamic, dict):
        actors = dynamic.get("actors")
        if isinstance(actors, dict):
            dynamic["actors"] = {role: copy.deepcopy(actors.get(role))}
        if role == "F":
            dynamic.pop("actuators", None)
        else:
            dynamic.pop("controllers", None)

    if role == "F":
        result["private_information"] = {
            "known": "controller_wiring",
            "wiring": copy.deepcopy(task["wiring"]),
            "unknown": "which actuator lies on W's required route",
            "instruction": "obtain the required actuator ID from W, then activate its mapped controller",
        }
    else:
        result["private_information"] = {
            "known": "required_route_actuator",
            "required_actuator": task["required_actuator"],
            "unknown": "which F-side controller activates it",
            "instruction": "tell F the required actuator ID; never guess a controller mapping",
        }
    return result


@dataclass(slots=True)
class RolePrivatePolicy:
    """Policy decorator that enforces and audits the C5 observation split."""

    inner: Policy
    task: Mapping[str, object]
    expected_role: Role
    observations: list[dict[str, object]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return getattr(self.inner, "name", "unknown")

    def act(
        self,
        role: Role,
        observation: Mapping[str, object],
        inbox: Sequence[Mapping[str, object]],
        own_history: Sequence[Mapping[str, object]],
    ) -> PolicyReply:
        if role != self.expected_role:
            raise C5ValidationError(f"wrapper for {self.expected_role} received {role}")
        private = project_role_observation(observation, self.task, role)
        self.observations.append(copy.deepcopy(private))
        return self.inner.act(role, private, inbox, own_history)


def run_c5_session(
    symbol_map: SymbolMap,
    policies: Mapping[Role, Policy],
    task: Mapping[str, object],
    *,
    max_rounds: int = 30,
) -> SessionResult:
    """Run through the frozen engine while retaining exact private DTOs.

    The common compact DTO constructed inside ``DualAgentSession`` is only an
    internal pre-projection.  The wrapped policies receive role-private DTOs,
    which are copied into each trace round for independent prompt auditing.
    """

    wrapped = {
        role: RolePrivatePolicy(policies[role], task, role)
        for role in ("F", "W")
    }
    result = DualAgentSession(symbol_map, wrapped, max_rounds=max_rounds).run()
    trace = copy.deepcopy(result.trace)
    rounds = trace.get("rounds", [])
    if not isinstance(rounds, list):
        raise C5ValidationError("runner returned malformed rounds")
    if any(len(wrapped[role].observations) != len(rounds) for role in ("F", "W")):
        raise C5ValidationError("private observation audit log is not aligned with trace rounds")
    for index, record in enumerate(rounds):
        if not isinstance(record, dict):
            raise C5ValidationError("runner returned malformed round record")
        record["runner_internal_preprojection"] = record.pop("agent_observation", None)
        record["role_agent_observations"] = {
            role: copy.deepcopy(wrapped[role].observations[index]) for role in ("F", "W")
        }
    protocol = trace.get("protocol")
    if isinstance(protocol, dict):
        protocol["agent_observation"] = "fwcollab.c5_agent_observation.v1"
        protocol["observation_partition"] = "role_private_complementary"
        protocol["teammate_position_visible"] = False
        protocol["authoritative_world_replayable"] = True
    trace["c5_information_certificate"] = {
        "f_view_id": task["f_view_id"],
        "w_view_id": task["w_view_id"],
        "reference_visibility": "private_evaluator_only",
    }
    return SessionResult(trace=trace, metrics=copy.deepcopy(result.metrics))


def projection_fingerprint(observation: Mapping[str, object], task: Mapping[str, object], role: Role) -> str:
    """Hash a role's initial view for automated no-leakage checks."""

    return _canonical_hash(project_role_observation(observation, task, role))


def _contains_fact(value: object, fact: str) -> bool:
    if isinstance(value, str):
        return fact in value
    if isinstance(value, dict):
        return any(_contains_fact(item, fact) for item in value.values())
    if isinstance(value, list):
        return any(_contains_fact(item, fact) for item in value)
    return False


def evaluate_c5_trace(task: Mapping[str, object], trace: Mapping[str, object]) -> dict[str, object]:
    """Evaluate the information handoff DAG from an auditable C5 trace."""

    rounds = trace.get("rounds")
    if not isinstance(rounds, list):
        raise C5ValidationError("trace rounds must be a list")
    target = task.get("required_actuator")
    answer = correct_controller(task)
    if not isinstance(target, str):
        raise C5ValidationError("required_actuator must be a string")
    target_row = {"gate_alpha": 10, "gate_beta": 12, "gate_gamma": 14}.get(target)
    if target_row is None:
        raise C5ValidationError(f"unsupported actuator {target}")
    first: dict[str, int | None] = {
        "n0": 0,
        "n1": 0,
        "n2": None,
        "n3": None,
        "n4": None,
        "n5": None,
        "n6": None,
        "n7": None,
    }
    wrong_controller_first: int | None = None
    progress_area = 0.0
    for index, item in enumerate(rounds, start=1):
        if not isinstance(item, dict):
            raise C5ValidationError(f"trace round {index} must be an object")
        agents = item.get("agents", {})
        if isinstance(agents, dict):
            w_record = agents.get("W", {})
            f_record = agents.get("F", {})
            if isinstance(w_record, dict):
                decision = w_record.get("decision", {})
                if first["n2"] is None and isinstance(decision, dict) and _contains_fact(decision.get("message"), target):
                    first["n2"] = index
            if isinstance(f_record, dict) and first["n3"] is None and _contains_fact(f_record.get("inbox"), target):
                first["n3"] = index
        result = item.get("result", {})
        observation = result.get("observation", {}) if isinstance(result, dict) else {}
        state = observation.get("state", {}) if isinstance(observation, dict) else {}
        controllers = state.get("controllers", {}) if isinstance(state, dict) else {}
        if isinstance(controllers, dict):
            if first["n4"] is None and controllers.get(answer) is True:
                first["n4"] = index
            if wrong_controller_first is None:
                for controller, enabled in controllers.items():
                    if controller != answer and enabled is True:
                        wrong_controller_first = index
                        break
        doors = state.get("doors", {}) if isinstance(state, dict) else {}
        if first["n5"] is None and isinstance(doors, dict) and doors.get(target) is True:
            first["n5"] = index
        actors = state.get("actors", {}) if isinstance(state, dict) else {}
        if first["n6"] is None and isinstance(actors, dict) and actors.get("W") == [target_row, 10]:
            first["n6"] = index
        if first["n7"] is None and isinstance(observation, dict) and observation.get("status") == "team_success":
            first["n7"] = index
        progress_area += sum(value is not None for value in first.values()) / len(first)

    completion = sum(value is not None for value in first.values()) / len(first)
    received = first["n3"]
    activated = first["n4"]
    dependency_violation = activated is not None and (received is None or activated < received)
    wrong_before_answer = wrong_controller_first is not None and (
        activated is None or wrong_controller_first <= activated
    )
    clean = bool(
        first["n2"] is not None
        and received is not None
        and activated is not None
        and first["n2"] < received <= activated
        and not wrong_before_answer
    )
    return {
        "format": "fwcollab.c5_evaluation.v1",
        "task_id": task.get("task_id"),
        "success": trace.get("outcome") == "team_success",
        "node_first_completed_at": first,
        "dag_completion": completion,
        "dag_progress_auc": progress_area / len(rounds) if rounds else 0.25,
        "clean_information_handoff": clean,
        "dependency_violation": dependency_violation,
        "wrong_controller_before_answer": wrong_before_answer,
        "failure_stage": next((node for node, completed in first.items() if completed is None), None),
    }
