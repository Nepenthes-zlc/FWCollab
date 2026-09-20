"""Simultaneous two-agent sessions, trace replay, and collaboration metrics."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from fwcollab.symbolic.agents import AgentDecision, CoordinationMessage, PlanReply, Policy, PolicyReply
from fwcollab.symbolic.dag import compare_state_dags
from fwcollab.symbolic.map import Position, SymbolMap
from fwcollab.symbolic.world import DIRECTIONS, Role, SymbolAction, SymbolWorld


def map_fingerprint(symbol_map: SymbolMap) -> str:
    world = SymbolWorld(symbol_map)
    layout = world.public_layout()
    mirrors = layout.get("mirrors")
    if isinstance(mirrors, dict):
        for mirror in mirrors.values():
            if isinstance(mirror, dict):
                mirror.pop("controlled_by", None)
                mirror.pop("current", None)
    value = {
        "format": symbol_map.format,
        "initial_rows": list(symbol_map.rows),
        "terrain_rows": world.observation()["terrain_rows"],
        "layout": layout,
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class SessionResult:
    trace: dict[str, object]
    metrics: dict[str, object]


def _message_payload(message: str | CoordinationMessage) -> tuple[str, dict[str, object]]:
    if isinstance(message, CoordinationMessage):
        payload = message.model_dump(mode="json")
        text = message.fact or message.request or message.status
        return text, payload
    return message, {
        "stage": "",
        "status": "INFO",
        "fact": message,
        "request": "",
        "until": "",
    }


def _state_signature(observation: Mapping[str, object]) -> str:
    """Hash planning state without monotonic round counters."""

    value = {
        "map_rows": observation.get("map_rows"),
        "state": observation.get("state"),
        "status": observation.get("status"),
    }
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _reachable_cells(world: SymbolWorld, role: Role, *, open_positions: frozenset[object] = frozenset()) -> set[object]:
    """Conservative current-state reachability used only for failure diagnosis."""

    start = world.actors[role]
    visited = {start}
    frontier = [start]
    doors = world.door_states()
    while frontier:
        position = frontier.pop()
        for move, delta in DIRECTIONS.items():
            target = Position(position.row + delta[0], position.col + delta[1])
            if target in visited:
                continue
            cell = world.static_cell(target)
            if cell == "#":
                continue
            if cell in "ABCDE" and target not in open_positions and not world._door_open_at(target, doors):
                continue
            if cell == "=" and target not in open_positions and not world._platform_open_at(target):
                continue
            if cell == "J" and not world._one_way_allows(cell, move):
                continue
            if cell == "f" and role != "F" or cell == "w" and role != "W":
                continue
            hazard = "~" if cell == "I" and not world.thermal_frozen.get("I", False) else cell
            if hazard == "x" or role == "F" and hazard == "~" or role == "W" and hazard == "^":
                continue
            visited.add(target)
            frontier.append(target)
    return visited


def _stranded_plate_deadlock(world: SymbolWorld) -> dict[str, object] | None:
    """Prove the common self-lock where the sole valid holder is behind its own gate."""

    if not world.map.controllers or not world.map.actuators:
        return None
    states = world.controller_states()
    for plate in (item for item in world.map.controllers if item.kind == "plate" and not states[item.id]):
        actor_accepts = sorted(set(plate.accepts) & {"F", "W"})
        if len(actor_accepts) != 1 or "O" in plate.accepts:
            continue
        holder: Role = actor_accepts[0]  # type: ignore[assignment]
        for actuator in world.map.actuators:
            expression = actuator.controlled_by
            if plate.id not in expression.inputs or expression.mode != "all":
                continue
            if not all(states[input_id] for input_id in expression.inputs if input_id != plate.id):
                continue
            all_actuator_positions = frozenset(
                position for candidate in world.map.actuators for position in candidate.positions
            )
            other_actuator_positions = all_actuator_positions.difference(actuator.positions)
            if plate.position in _reachable_cells(world, holder, open_positions=other_actuator_positions):
                continue
            if plate.position not in _reachable_cells(world, holder, open_positions=all_actuator_positions):
                continue
            teammate: Role = "W" if holder == "F" else "F"
            teammate_exit = world.map.unique_position("w" if teammate == "W" else "f")
            if teammate_exit in _reachable_cells(world, teammate, open_positions=other_actuator_positions):
                continue
            if teammate_exit not in _reachable_cells(world, teammate, open_positions=all_actuator_positions):
                continue
            return {
                "holder": holder,
                "plate": plate.id,
                "actuator": actuator.id,
                "reason": "the only accepted holder can reach the required plate only if that same actuator is open",
            }
    return None


def diagnose_failure(rounds: Sequence[Mapping[str, object]], world: SymbolWorld) -> dict[str, object]:
    """Classify an unfinished episode without changing its authoritative outcome."""

    provider_errors = sum(
        1
        for item in rounds
        for record in (item.get("agents", {}) or {}).values()  # type: ignore[union-attr]
        if isinstance(record, dict) and record.get("error")
    )
    if world.status != "running":
        return {
            "format": "fwcollab.failure_diagnosis.v1",
            "primary": None,
            "confidence": "not_applicable",
            "provider_errors": provider_errors,
            "evidence": [],
        }

    stranded = _stranded_plate_deadlock(world)
    if stranded is not None:
        return {
            "format": "fwcollab.failure_diagnosis.v1",
            "primary": "irreversible_deadlock",
            "confidence": "proved_for_v3_plate_self_lock",
            "provider_errors": provider_errors,
            "evidence": [stranded],
        }

    signatures = [
        _state_signature(item["result"]["observation"])  # type: ignore[index]
        for item in rounds
        if isinstance(item.get("result"), dict)
    ]
    for period in range(1, 5):
        span = period * 3
        if len(signatures) >= span and signatures[-span:-span + period] * 3 == signatures[-span:]:
            return {
                "format": "fwcollab.failure_diagnosis.v1",
                "primary": "planning_cycle" if period > 1 else "mutual_wait",
                "confidence": "observed_suffix",
                "provider_errors": provider_errors,
                "evidence": [{"period": period, "repetitions": 3, "ending_round": len(rounds)}],
            }

    blocked = 0
    for item in rounds[-8:]:
        agents = item.get("agents", {})
        if not isinstance(agents, dict):
            continue
        blocked += sum(
            str(record.get("feedback", "")) not in {"moved", "moved_via_portal", "waited"}
            for record in agents.values()
            if isinstance(record, dict)
        )
    if blocked >= 6:
        primary = "blocked_action_loop"
        confidence = "observed_suffix"
        evidence = [{"blocked_actions_in_last_8_rounds": blocked}]
    else:
        primary = "budget_exhausted"
        confidence = "fallback"
        evidence = [{"rounds": len(rounds)}]
    return {
        "format": "fwcollab.failure_diagnosis.v1",
        "primary": primary,
        "confidence": confidence,
        "provider_errors": provider_errors,
        "evidence": evidence,
    }


def _state_delta(before: Mapping[str, object], after: Mapping[str, object]) -> list[dict[str, object]]:
    """Emit public, machine-checkable events from two authoritative observations."""

    events: list[dict[str, object]] = []
    before_state = before.get("state", {})
    after_state = after.get("state", {})
    if not isinstance(before_state, dict) or not isinstance(after_state, dict):
        return events
    before_actors = before_state.get("actors", {})
    after_actors = after_state.get("actors", {})
    if isinstance(before_actors, dict) and isinstance(after_actors, dict):
        for role in ("F", "W"):
            if before_actors.get(role) != after_actors.get(role):
                events.append({"kind": "actor_moved", "role": role, "from": before_actors.get(role), "to": after_actors.get(role)})
    for field in ("crates", "orbs"):
        if before_state.get(field) != after_state.get(field):
            events.append({"kind": f"{field}_changed", "before": before_state.get(field), "after": after_state.get(field)})
    for field in ("alive", "plates", "doors", "levers", "toggles", "thermal_frozen", "light_sensors", "platforms"):
        old, new = before_state.get(field, {}), after_state.get(field, {})
        if isinstance(old, dict) and isinstance(new, dict):
            for key in sorted(set(old) | set(new)):
                if old.get(key) != new.get(key):
                    events.append({"kind": "state_changed", "field": field, "key": key, "from": old.get(key), "to": new.get(key)})
    if before.get("status") != after.get("status"):
        events.append({"kind": "status_changed", "from": before.get("status"), "to": after.get("status")})
    return events


def _nonempty_fields(value: Mapping[str, object]) -> dict[str, object]:
    return {key: copy.deepcopy(item) for key, item in value.items() if item not in ({}, [], None)}


def _agent_observation(
    world: SymbolWorld,
    authoritative: Mapping[str, object],
    *,
    max_rounds: int,
    last_transition: Mapping[str, object] | None,
    shared_messages: Sequence[Mapping[str, object]],
    planning_context: Mapping[str, object] | None = None,
    coordination_mode: str = "emergent",
) -> dict[str, object]:
    """Build the compact, complete DTO actually sent to both role models.

    The authoritative observation remains in the trace for rendering and
    legacy replay. This projection removes redundant coordinate maps, wall
    lists and duplicated v3 controller views while adding temporal context.
    """

    layout = authoritative.get("layout", {})
    state = authoritative.get("state", {})
    if not isinstance(layout, dict) or not isinstance(state, dict):
        raise ValueError("authoritative observation requires layout and state objects")

    mechanisms: dict[str, object] = {}
    for field in ("exits", "hazards", "portals", "one_ways", "thermals", "lights", "mirrors"):
        if layout.get(field):
            mechanisms[field] = copy.deepcopy(layout[field])
    if layout.get("controllers"):
        mechanisms["controllers"] = copy.deepcopy(layout["controllers"])
        mechanisms["actuators"] = copy.deepcopy(layout.get("actuators", {}))
    else:
        for field in ("plates", "levers", "toggles", "doors", "platforms"):
            if layout.get(field):
                mechanisms[field] = copy.deepcopy(layout[field])

    # public_layout now includes this directly; populate it defensively for
    # maps/traces created through an older layout projection.
    mirrors = mechanisms.get("mirrors")
    if isinstance(mirrors, dict):
        for rule in world.map.mirrors:
            mirror = mirrors.get(rule.symbol)
            if isinstance(mirror, dict):
                mirror["controlled_by"] = rule.controller
                mirror["current"] = "slash" if world._mirror_cell() == "/" else "backslash"

    dynamic: dict[str, object] = {
        "actors": copy.deepcopy(state.get("actors", {})),
        "alive": copy.deepcopy(state.get("alive", {})),
        "crates": copy.deepcopy(state.get("crates", [])),
        "orbs": copy.deepcopy(state.get("orbs", [])),
    }
    controller_states = state.get("controllers")
    if isinstance(controller_states, dict) and controller_states:
        dynamic["controllers"] = copy.deepcopy(controller_states)
    else:
        legacy = _nonempty_fields(
            {
                "plates": state.get("plates", {}),
                "levers": state.get("levers", {}),
                "toggles": state.get("toggles", {}),
            }
        )
        if legacy:
            dynamic["controllers"] = legacy
    actuators = _nonempty_fields(
        {"doors": state.get("doors", {}), "platforms": state.get("platforms", {})}
    )
    if actuators:
        dynamic["actuators"] = actuators
    for field in ("thermal_frozen", "light_sensors"):
        if state.get(field):
            dynamic[field] = copy.deepcopy(state[field])

    current_rows = authoritative.get("map_rows", [])
    terrain_rows = authoritative.get("terrain_rows", [])
    height = len(terrain_rows) if isinstance(terrain_rows, list) else world.map.height
    width = len(terrain_rows[0]) if isinstance(terrain_rows, list) and terrain_rows else world.map.width
    rounds_used = int(authoritative.get("round", 0))
    rules = copy.deepcopy(authoritative.get("rules", {}))
    if coordination_mode == "emergent" and isinstance(rules, dict):
        communication = rules.get("communication")
        if isinstance(communication, dict):
            communication.pop("held_gate_handshake", None)
    result: dict[str, object] = {
        "format": "fwcollab.agent_observation.v1",
        "map_id": authoritative.get("map_id"),
        "coordination_mode": coordination_mode,
        "clock": {
            "round": rounds_used + 1,
            "max_rounds": max_rounds,
            "rounds_used": rounds_used,
            "rounds_remaining": max_rounds - rounds_used,
        },
        "map": {
            "height": height,
            "width": width,
            "current_rows": copy.deepcopy(current_rows),
            "terrain_rows": copy.deepcopy(terrain_rows),
        },
        "mechanisms": mechanisms,
        "dynamic_state": dynamic,
        "last_transition": copy.deepcopy(last_transition),
        "shared_message_history": copy.deepcopy(list(shared_messages[-8:])),
        "rules": rules,
        "status": authoritative.get("status"),
    }
    if planning_context is not None:
        result["planning"] = copy.deepcopy(planning_context)
    return result


def _safe_reply(policy: Policy, role: Role, observation: dict[str, object], inbox: list[dict[str, object]], history: list[dict[str, object]]) -> tuple[PolicyReply, str | None]:
    try:
        return policy.act(role, observation, inbox, history), None
    except Exception as exc:  # Provider and schema errors are part of benchmark robustness.
        fallback = AgentDecision(
            action=SymbolAction(move="WAIT", steps=0),
            reason="invalid model response; environment substituted WAIT",
        )
        return PolicyReply(
            decision=fallback,
            resolved_model=getattr(policy, "name", "unknown"),
            provider_calls=int(getattr(exc, "attempts", 0)),
        ), f"{type(exc).__name__}: {exc}"


def _safe_plan_reply(
    policy: Policy,
    role: Role,
    observation: dict[str, object],
    peer_candidate: Mapping[str, object] | None,
) -> tuple[PlanReply | None, str | None, int]:
    try:
        proposer = getattr(policy, "propose_plan")
        reply = proposer(role, observation, peer_candidate)
        return reply, None, reply.provider_calls
    except Exception as exc:  # Planning is scored separately and must not abort execution.
        return None, f"{type(exc).__name__}: {exc}", int(getattr(exc, "attempts", 0))


def collaboration_metrics(rounds: Sequence[Mapping[str, object]], *, outcome: str) -> dict[str, object]:
    per_role: dict[str, dict[str, int]] = {
        role: {"moves": 0, "waits": 0, "blocked": 0, "invalid": 0, "messages_sent": 0, "messages_received": 0, "model_calls": 0}
        for role in ("F", "W")
    }
    simultaneous_move_rounds = 0
    support_rounds = 0
    for item in rounds:
        agents = item.get("agents", {})
        if not isinstance(agents, dict):
            continue
        moved_this_round = 0
        for role in ("F", "W"):
            record = agents.get(role, {})
            if not isinstance(record, dict):
                continue
            decision = record.get("decision", {})
            action = decision.get("action", {}) if isinstance(decision, dict) else {}
            move = action.get("move") if isinstance(action, dict) else None
            if move == "WAIT":
                per_role[role]["waits"] += 1
            else:
                per_role[role]["moves"] += 1
                moved_this_round += 1
            feedback = str(record.get("feedback", ""))
            if feedback not in {"moved", "moved_via_portal", "waited"}:
                per_role[role]["blocked"] += 1
            if record.get("error"):
                per_role[role]["invalid"] += 1
            if isinstance(decision, dict) and decision.get("message"):
                per_role[role]["messages_sent"] += 1
            inbox = record.get("inbox", [])
            if isinstance(inbox, list):
                per_role[role]["messages_received"] += len(inbox)
            per_role[role]["model_calls"] += int(record.get("provider_calls", 0))
        if moved_this_round == 2:
            simultaneous_move_rounds += 1
        pre = item.get("observation", {})
        state = pre.get("state", {}) if isinstance(pre, dict) else {}
        plates = state.get("plates", {}) if isinstance(state, dict) else {}
        if isinstance(plates, dict) and any(plates.values()) and moved_this_round:
            support_rounds += 1

    move_counts = [per_role[role]["moves"] for role in ("F", "W")]
    max_moves = max(move_counts, default=0)
    balance = (min(move_counts) / max_moves) if max_moves else None
    return {
        "format": "fwcollab.symbol_metrics.v1",
        "outcome": outcome,
        "success": outcome == "team_success",
        "rounds": len(rounds),
        "simultaneous_move_rounds": simultaneous_move_rounds,
        "support_rounds": support_rounds,
        "action_balance": balance,
        "per_role": per_role,
        "total_model_errors": sum(per_role[role]["invalid"] for role in ("F", "W")),
        "total_messages": sum(per_role[role]["messages_sent"] for role in ("F", "W")),
        "total_model_calls": sum(per_role[role]["model_calls"] for role in ("F", "W")),
    }


class DualAgentSession:
    """Run F and W from identical frozen observations with independent histories."""

    def __init__(
        self,
        symbol_map: SymbolMap,
        policies: Mapping[Role, Policy],
        *,
        max_rounds: int = 80,
        message_delay_rounds: int = 1,
        planning_rounds: int = 0,
        coordination_mode: str = "emergent",
    ) -> None:
        if set(policies) != {"F", "W"}:
            raise ValueError("policies must contain exactly F and W")
        if max_rounds < 1:
            raise ValueError("max_rounds must be positive")
        if message_delay_rounds != 1:
            raise ValueError("the public protocol currently fixes message delay at one round")
        if planning_rounds not in {0, 1, 2}:
            raise ValueError("planning_rounds must be 0, 1, or 2")
        if coordination_mode not in {"emergent", "protocol_assisted"}:
            raise ValueError("coordination_mode must be emergent or protocol_assisted")
        self.world = SymbolWorld(symbol_map)
        self.policies = dict(policies)
        self.max_rounds = max_rounds
        self.message_delay_rounds = message_delay_rounds
        self.planning_rounds = planning_rounds
        self.coordination_mode = coordination_mode
        self.histories: dict[Role, list[dict[str, object]]] = {"F": [], "W": []}
        self.pending_messages: list[dict[str, object]] = []
        self.delivered_messages: list[dict[str, object]] = []

    def run(self) -> SessionResult:
        started = time.perf_counter()
        trace_rounds: list[dict[str, object]] = []
        planning_records: list[dict[str, object]] = []
        planning_context: dict[str, object] | None = None
        candidates: dict[Role, dict[str, object]] = {}
        planning_calls = 0
        planning_errors = 0
        if self.planning_rounds:
            initial = copy.deepcopy(self.world.observation())
            planning_input = _agent_observation(
                self.world,
                initial,
                max_rounds=self.max_rounds,
                last_transition=None,
                shared_messages=[],
                coordination_mode=self.coordination_mode,
            )
            for planning_round in range(1, self.planning_rounds + 1):
                with ThreadPoolExecutor(max_workers=2, thread_name_prefix="fwcollab-planner") as executor:
                    futures = {
                        role: executor.submit(
                            _safe_plan_reply,
                            self.policies[role],
                            role,
                            copy.deepcopy(planning_input),
                            copy.deepcopy(candidates.get("W" if role == "F" else "F")),
                        )
                        for role in ("F", "W")
                    }
                    replies = {role: futures[role].result() for role in ("F", "W")}
                round_record: dict[str, object] = {"round": planning_round, "agents": {}}
                for role in ("F", "W"):
                    reply, error, provider_calls = replies[role]
                    planning_calls += provider_calls
                    if reply is not None:
                        candidates[role] = copy.deepcopy(reply.plan)
                    else:
                        planning_errors += 1
                    round_record["agents"][role] = {  # type: ignore[index]
                        "plan": copy.deepcopy(reply.plan) if reply is not None else None,
                        "raw_output": reply.raw_output if reply is not None else "",
                        "latency_ms": reply.latency_ms if reply is not None else 0,
                        "model": reply.resolved_model if reply is not None else getattr(self.policies[role], "name", "unknown"),
                        "provider_calls": provider_calls,
                        "error": error,
                    }
                planning_records.append(round_record)

            consensus: dict[str, object] | None = None
            team_plan: dict[str, object] | None = None
            if set(candidates) == {"F", "W"}:
                consensus = compare_state_dags(candidates["F"], candidates["W"])
                if consensus["typed_equivalent"]:
                    team_plan = copy.deepcopy(candidates["F"])
            planning_context = {
                "mode": "two_agent_dag_negotiation",
                "rounds": self.planning_rounds,
                "proposals": copy.deepcopy(candidates),
                "consensus": consensus,
                "team_plan": team_plan,
                "instruction": (
                    "execute the agreed team_plan"
                    if team_plan is not None
                    else "no typed consensus; use both public proposals as advisory and resolve differences through messages"
                ),
            }
        for _ in range(self.max_rounds):
            if self.world.status != "running":
                break
            decision_round = self.world.round + 1
            frozen = copy.deepcopy(self.world.observation())
            pre_hash = self.world.state_hash()
            inboxes: dict[Role, list[dict[str, object]]] = {"F": [], "W": []}
            remaining: list[dict[str, object]] = []
            for message in self.pending_messages:
                recipient = message["recipient"]
                if message["delivery_round"] <= decision_round and recipient in inboxes:
                    delivered = copy.deepcopy(message)
                    inboxes[recipient].append(delivered)
                    self.delivered_messages.append(delivered)
                else:
                    remaining.append(message)
            self.pending_messages = remaining

            last_transition: dict[str, object] | None = None
            if trace_rounds:
                previous = trace_rounds[-1]
                previous_result = previous.get("result", {})
                previous_feedback = (
                    previous_result.get("feedback", {}) if isinstance(previous_result, dict) else {}
                )
                last_transition = {
                    "round": previous.get("round"),
                    "public_events": copy.deepcopy(previous.get("events", [])),
                    "feedback": copy.deepcopy(previous_feedback),
                }
            public_input = _agent_observation(
                self.world,
                frozen,
                max_rounds=self.max_rounds,
                last_transition=last_transition,
                shared_messages=self.delivered_messages,
                planning_context=planning_context,
                coordination_mode=self.coordination_mode,
            )

            # Both calls begin from deep copies of the same frozen DTO. The
            # executor also prevents provider call order from exposing a
            # teammate's current answer.
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="fwcollab-agent") as executor:
                futures = {
                    role: executor.submit(
                        _safe_reply,
                        self.policies[role],
                        role,
                        copy.deepcopy(public_input),
                        copy.deepcopy(inboxes[role]),
                        copy.deepcopy(self.histories[role]),
                    )
                    for role in ("F", "W")
                }
                replies = {role: futures[role].result() for role in ("F", "W")}

            actions = {role: replies[role][0].decision.action for role in ("F", "W")}
            transition = self.world.step_joint(actions)
            post_hash = self.world.state_hash()
            agent_records: dict[str, dict[str, object]] = {}
            for role in ("F", "W"):
                reply, error = replies[role]
                decision = reply.decision
                teammate: Role = "W" if role == "F" else "F"
                if decision.message:
                    text, payload = _message_payload(decision.message)
                    self.pending_messages.append(
                        {
                            "sender": role,
                            "recipient": teammate,
                            "sent_round": decision_round,
                            "delivery_round": decision_round + self.message_delay_rounds,
                            "based_on_state_hash": pre_hash,
                            "text": text,
                            "payload": payload,
                        }
                    )
                record = {
                    "decision": decision.model_dump(mode="json"),
                    "feedback": transition.feedback[role],
                    "inbox": copy.deepcopy(inboxes[role]),
                    "error": error,
                    "latency_ms": reply.latency_ms,
                    "model": reply.resolved_model,
                    "raw_output": reply.raw_output,
                    "provider_calls": reply.provider_calls,
                }
                agent_records[role] = record
                self.histories[role].append(
                    {
                        "round": decision_round,
                        "observation_hash": pre_hash,
                        "action": decision.action.model_dump(mode="json"),
                        "subgoal": decision.subgoal,
                        "commitment": decision.commitment,
                        "expected_teammate": decision.expected_teammate,
                        "message": (
                            {
                                "stage": decision.message.stage,
                                "status": decision.message.status,
                                "until": decision.message.until,
                            }
                            if isinstance(decision.message, CoordinationMessage)
                            else decision.message
                        ),
                        "feedback": transition.feedback[role],
                        "resulting_status": transition.status,
                    }
                )
            trace_rounds.append(
                {
                    "round": decision_round,
                    "pre_state_hash": pre_hash,
                    "post_state_hash": post_hash,
                    "observation": frozen,
                    "agent_observation": public_input,
                    "agents": agent_records,
                    "events": _state_delta(frozen, transition.observation),
                    "result": {
                        "status": transition.status,
                        "feedback": dict(transition.feedback),
                        "observation": transition.observation,
                    },
                }
            )

        outcome = self.world.status if self.world.status != "running" else "timeout"
        trace: dict[str, object] = {
            "format": "fwcollab.symbol_trace.v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "map_id": self.world.map.map_id,
            "map_fingerprint": map_fingerprint(self.world.map),
            "protocol": {
                "independent_histories": True,
                "frozen_same_round_observation": True,
                "simultaneous_submission": True,
                "movement_steps": 1,
                "message_delay_rounds": self.message_delay_rounds,
                "max_rounds": self.max_rounds,
                "planning_rounds": self.planning_rounds,
                "coordination_mode": self.coordination_mode,
                "agent_observation": "fwcollab.agent_observation.v1",
                "last_transition_visible": True,
                "round_budget_visible": True,
            },
            "models": {role: getattr(self.policies[role], "name", "unknown") for role in ("F", "W")},
            "planning": {
                "mode": "two_agent_dag_negotiation" if self.planning_rounds else "none",
                "rounds": planning_records,
                "result": planning_context,
            },
            "rounds": trace_rounds,
            "outcome": outcome,
            "final_observation": self.world.observation(),
            "final_state_hash": self.world.state_hash(),
            "wall_time_ms": round((time.perf_counter() - started) * 1000),
        }
        diagnosis = diagnose_failure(trace_rounds, self.world)
        trace["diagnosis"] = diagnosis
        metrics = collaboration_metrics(trace_rounds, outcome=outcome)
        metrics["planning"] = {
            "enabled": bool(self.planning_rounds),
            "rounds": self.planning_rounds,
            "model_calls": planning_calls,
            "errors": planning_errors,
            "typed_consensus": bool(
                planning_context
                and isinstance(planning_context.get("consensus"), dict)
                and planning_context["consensus"].get("typed_equivalent")  # type: ignore[union-attr]
            ),
        }
        metrics["total_model_calls"] = int(metrics["total_model_calls"]) + planning_calls
        metrics["total_model_errors"] = int(metrics["total_model_errors"]) + planning_errors
        metrics["failure_diagnosis"] = diagnosis
        trace["metrics"] = metrics
        return SessionResult(trace=trace, metrics=metrics)


def save_trace(path: str | Path, trace: Mapping[str, object]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")


def replay_trace(symbol_map: SymbolMap, trace: Mapping[str, object]) -> dict[str, object]:
    """Replay recorded submitted actions and verify every authoritative hash."""

    if trace.get("format") != "fwcollab.symbol_trace.v1":
        raise ValueError("unsupported trace format")
    if trace.get("map_id") != symbol_map.map_id or trace.get("map_fingerprint") != map_fingerprint(symbol_map):
        raise ValueError("trace does not belong to this map version")
    rounds = trace.get("rounds")
    if not isinstance(rounds, list):
        raise ValueError("trace rounds must be a list")
    world = SymbolWorld(symbol_map)
    for index, item in enumerate(rounds, start=1):
        if not isinstance(item, dict) or item.get("pre_state_hash") != world.state_hash():
            raise ValueError(f"pre-state hash mismatch at round {index}")
        agents = item.get("agents")
        if not isinstance(agents, dict):
            raise ValueError(f"missing agent records at round {index}")
        actions: dict[Role, SymbolAction] = {}
        for role in ("F", "W"):
            record = agents.get(role)
            if not isinstance(record, dict) or not isinstance(record.get("decision"), dict):
                raise ValueError(f"missing {role} decision at round {index}")
            decision = AgentDecision.model_validate(record["decision"])
            actions[role] = decision.action
        world.step_joint(actions)
        if item.get("post_state_hash") != world.state_hash():
            raise ValueError(f"post-state hash mismatch at round {index}")
    if trace.get("final_state_hash") != world.state_hash():
        raise ValueError("final state hash mismatch")
    expected_outcome = world.status if world.status != "running" else "timeout"
    if trace.get("outcome") != expected_outcome:
        raise ValueError("trace outcome mismatch")
    return {
        "ok": True,
        "map_id": symbol_map.map_id,
        "rounds": len(rounds),
        "outcome": expected_outcome,
        "final_state_hash": world.state_hash(),
    }


def diagnose_recorded_trace(symbol_map: SymbolMap, trace: Mapping[str, object]) -> dict[str, object]:
    """Replay submitted actions and classify an unfinished historical trace."""

    rounds = trace.get("rounds")
    if not isinstance(rounds, list):
        raise ValueError("trace rounds must be a list")
    world = SymbolWorld(symbol_map)
    for index, item in enumerate(rounds, start=1):
        if not isinstance(item, dict) or not isinstance(item.get("agents"), dict):
            raise ValueError(f"invalid trace record at round {index}")
        actions: dict[Role, SymbolAction] = {}
        for role in ("F", "W"):
            record = item["agents"].get(role)
            if not isinstance(record, dict) or not isinstance(record.get("decision"), dict):
                raise ValueError(f"missing {role} decision at round {index}")
            actions[role] = AgentDecision.model_validate(record["decision"]).action
        world.step_joint(actions)
    return diagnose_failure(rounds, world)
