from __future__ import annotations

import copy
import json

from fwcollab.symbolic.agents import (
    AgentDecision,
    CoordinationMessage,
    PlanReply,
    OpenAICompatiblePolicy,
    PolicyReply,
    ScriptedPolicy,
    build_agent_prompt,
    parse_agent_decision,
)
from fwcollab.symbolic.html import render_trace_html
from fwcollab.symbolic.map import parse_symbol_map
from fwcollab.symbolic.runner import DualAgentSession, replay_trace
from fwcollab.symbolic.world import SymbolAction
from tests.symbolic.test_symbol_map import SIMPLE


def test_model_json_parser_accepts_fenced_but_enforces_unit_step() -> None:
    parsed = parse_agent_decision(
        '```json\n{"move":"RIGHT","steps":1,"message":"go","reason":"clear"}\n```'
    )
    assert parsed.action == SymbolAction(move="RIGHT", steps=1)


def test_model_json_parser_accepts_structured_coordination_message() -> None:
    parsed = parse_agent_decision(
        '{"action":{"move":"WAIT","steps":0},"message":{"stage":"a3","status":"HOLDING",'
        '"fact":"F on c5","request":"W cross a3","until":"CROSSED(a3)"}}'
    )
    assert isinstance(parsed.message, CoordinationMessage)
    assert parsed.message.status == "HOLDING"
    assert parsed.message.until == "CROSSED(a3)"


def test_model_json_parser_accepts_explicit_silence() -> None:
    parsed = parse_agent_decision(
        '{"action":{"move":"WAIT","steps":0},"message":null,"reason":"no new fact"}'
    )
    assert parsed.message is None


def test_openai_policy_builds_responses_and_chat_payloads() -> None:
    responses = OpenAICompatiblePolicy(endpoint="http://provider.test", model="gpt-test")
    assert responses.endpoint == "http://provider.test/v1/responses"
    responses_body = json.loads(responses._payload("hello", max_output_tokens=20))
    assert responses_body["input"] == "hello"
    assert responses_body["max_output_tokens"] == 20
    assert "messages" not in responses_body

    chat = OpenAICompatiblePolicy(
        endpoint="http://provider.test/v1",
        model="gemini-test",
        api_style="chat_completions",
    )
    assert chat.endpoint == "http://provider.test/v1/chat/completions"
    chat_body = json.loads(chat._payload("hello", max_output_tokens=30))
    assert chat_body["messages"] == [{"role": "user", "content": "hello"}]
    assert chat_body["max_tokens"] == 30
    assert chat_body["reasoning_effort"] == "low"
    assert "input" not in chat_body


def test_phase2_sampling_fields_are_explicit_and_optional() -> None:
    baseline = OpenAICompatiblePolicy(endpoint="http://provider.test", model="gpt-test")
    baseline_body = json.loads(baseline._payload("hello", max_output_tokens=20))
    assert "temperature" not in baseline_body
    assert "seed" not in baseline_body

    phase2 = OpenAICompatiblePolicy(
        endpoint="http://provider.test",
        model="gpt-test",
        temperature=1.0,
        seed=2026091401,
    )
    body = json.loads(phase2._payload("hello", max_output_tokens=20))
    assert body["temperature"] == 1.0
    assert body["seed"] == 2026091401


def test_no_communication_prompt_makes_channel_unavailable() -> None:
    prompt = build_agent_prompt(
        "F", {}, [{"sender": "W", "text": "hidden"}], [], communication_enabled=False
    )
    assert "已禁用显式通信" in prompt
    assert '"message":null' in prompt
    assert "hidden" not in prompt

    normal = build_agent_prompt("F", {}, [], [])
    assert "消息不占用动作，但下一轮才送达" in normal


class RecordingPolicy:
    def __init__(self, role: str) -> None:
        self.name = f"recording-{role}"
        self.calls: list[dict[str, object]] = []

    def act(self, role, observation, inbox, own_history):
        self.calls.append(
            {
                "role": role,
                "observation": copy.deepcopy(observation),
                "inbox": copy.deepcopy(inbox),
                "history": copy.deepcopy(own_history),
            }
        )
        message = f"hello-from-{role}" if len(self.calls) == 1 else ""
        return PolicyReply(
            decision=AgentDecision(
                action=SymbolAction(move="WAIT", steps=0),
                message=message,
                reason=f"{role} waits",
            ),
            resolved_model=self.name,
        )


def test_two_agents_receive_frozen_observation_and_delayed_messages() -> None:
    fire, water = RecordingPolicy("F"), RecordingPolicy("W")
    result = DualAgentSession(
        parse_symbol_map(SIMPLE), {"F": fire, "W": water}, max_rounds=2
    ).run()

    assert fire.calls[0]["observation"] == water.calls[0]["observation"]
    assert fire.calls[0]["inbox"] == water.calls[0]["inbox"] == []
    assert fire.calls[1]["inbox"][0]["sender"] == "W"  # type: ignore[index]
    assert water.calls[1]["inbox"][0]["sender"] == "F"  # type: ignore[index]
    assert fire.calls[1]["inbox"][0]["sent_round"] == 1  # type: ignore[index]
    assert fire.calls[1]["inbox"][0]["payload"]["status"] == "INFO"  # type: ignore[index]
    assert len(fire.calls[1]["history"]) == len(water.calls[1]["history"]) == 1
    assert result.trace["outcome"] == "timeout"


def test_agent_observation_is_compact_temporal_and_shared() -> None:
    fire, water = RecordingPolicy("F"), RecordingPolicy("W")
    result = DualAgentSession(
        parse_symbol_map(SIMPLE), {"F": fire, "W": water}, max_rounds=2
    ).run()

    first = fire.calls[0]["observation"]
    second = fire.calls[1]["observation"]
    assert first["format"] == "fwcollab.agent_observation.v1"
    assert first["coordination_mode"] == "emergent"
    assert "held_gate_handshake" not in first["rules"]["communication"]
    assert first["clock"] == {
        "round": 1,
        "max_rounds": 2,
        "rounds_used": 0,
        "rounds_remaining": 2,
    }
    assert set(first["map"]) == {"height", "width", "current_rows", "terrain_rows"}
    assert "walls" not in first["mechanisms"]
    assert "map_rows" not in first and "terrain_map" not in first and "action_space" not in first
    assert first["last_transition"] is None
    assert second["last_transition"]["round"] == 1
    assert second["last_transition"]["feedback"] == {"F": "waited", "W": "waited"}
    assert len(second["shared_message_history"]) == 2
    assert second == water.calls[1]["observation"]
    assert "agent_observation" in result.trace["rounds"][0]


def test_protocol_assisted_mode_exposes_handshake_without_changing_actions() -> None:
    fire, water = RecordingPolicy("F"), RecordingPolicy("W")
    result = DualAgentSession(
        parse_symbol_map(SIMPLE),
        {"F": fire, "W": water},
        max_rounds=1,
        coordination_mode="protocol_assisted",
    ).run()
    observation = fire.calls[0]["observation"]
    assert observation["coordination_mode"] == "protocol_assisted"
    assert observation["rules"]["communication"]["held_gate_handshake"] == [
        "READY",
        "HOLDING",
        "CROSSED",
        "RELEASE",
    ]
    assert result.trace["protocol"]["coordination_mode"] == "protocol_assisted"


def _candidate(plan_id: str) -> dict[str, object]:
    return {
        "format": "fwcollab.symbolic.state_dag.v1",
        "id": plan_id,
        "difficulty": "L1",
        "evaluation_role": "diagnostic",
        "visibility": "agent_candidate",
        "title": "public candidate",
        "summary": "reach both exits",
        "topology_family": "agent_proposal",
        "mechanisms": ["M10"],
        "nodes": [
            {
                "id": "start",
                "kind": "condition",
                "owner": "team",
                "join": "all",
                "predicate": {"op": "start", "motif": "SYSTEM", "state": "initial", "phase": 0},
                "label": "start",
            },
            {
                "id": "exits",
                "kind": "condition",
                "owner": "team",
                "join": "all",
                "predicate": {"op": "goal", "motif": "M10", "state": "both_agents_at_exits", "phase": 1},
                "label": "both exits",
            },
            {
                "id": "success",
                "kind": "goal",
                "owner": "team",
                "join": "all",
                "predicate": {"op": "team_success", "motif": "SYSTEM", "state": "complete", "phase": 2},
                "label": "success",
            },
        ],
        "edges": [
            {"from": "start", "to": "exits", "relation": "requires"},
            {"from": "exits", "to": "success", "relation": "enables"},
        ],
    }


def test_optional_two_agent_dag_negotiation_precedes_actions() -> None:
    class PlanningPolicy(RecordingPolicy):
        def propose_plan(self, role, observation, peer_candidate=None):
            assert observation["status"] == "running"
            if peer_candidate is not None:
                assert peer_candidate["visibility"] == "agent_candidate"
            return PlanReply(plan=_candidate(f"{role}-candidate"), resolved_model=self.name, provider_calls=1)

    fire, water = PlanningPolicy("F"), PlanningPolicy("W")
    result = DualAgentSession(
        parse_symbol_map(SIMPLE),
        {"F": fire, "W": water},
        max_rounds=1,
        planning_rounds=2,
    ).run()

    assert result.trace["planning"]["mode"] == "two_agent_dag_negotiation"
    assert result.trace["planning"]["result"]["consensus"]["typed_equivalent"] is True
    assert fire.calls[0]["observation"]["planning"]["team_plan"]["visibility"] == "agent_candidate"
    assert result.metrics["planning"] == {
        "enabled": True,
        "rounds": 2,
        "model_calls": 4,
        "errors": 0,
        "typed_consensus": True,
    }


def test_trace_replays_with_hash_verification() -> None:
    symbol_map = parse_symbol_map(SIMPLE)
    decisions = {
        role: [
            AgentDecision(action=SymbolAction(move="WAIT", steps=0), reason=f"{role} wait")
            for _ in range(2)
        ]
        for role in ("F", "W")
    }
    result = DualAgentSession(
        symbol_map,
        {role: ScriptedPolicy(decisions[role], name=role) for role in ("F", "W")},
        max_rounds=2,
    ).run()
    report = replay_trace(symbol_map, result.trace)
    assert report["ok"] is True
    assert report["rounds"] == 2
    assert report["outcome"] == "timeout"
    assert result.trace["rounds"][0]["events"] == []  # type: ignore[index]


def test_provider_error_becomes_wait_without_stopping_episode() -> None:
    class BrokenPolicy:
        name = "broken"

        def act(self, role, observation, inbox, own_history):
            raise RuntimeError("deliberate test failure")

    wait = AgentDecision(action=SymbolAction(move="WAIT", steps=0))
    result = DualAgentSession(
        parse_symbol_map(SIMPLE),
        {"F": BrokenPolicy(), "W": ScriptedPolicy([wait, wait], name="wait")},
        max_rounds=2,
    ).run()
    assert result.metrics["total_model_errors"] == 2
    assert len(result.trace["rounds"]) == 2  # type: ignore[arg-type]
    rendered = render_trace_html(result.trace)
    assert "双智能体单步回放" in rendered
    assert "trace-data" in rendered


def test_v3_plate_self_lock_is_classified_as_irreversible_deadlock() -> None:
    symbol_map = parse_symbol_map("""@format fwcollab.symbol_map.v3
@id DEADLOCK
@title plate self lock
@max_steps 1
@controller c1 plate at 1,3 accepts F
@actuator a1 door at 1,4;3,4 controlled_by all(c1)
---
#########
#F.1A.f.#
#########
#W..A.w.#
#########
""")
    right = AgentDecision(action=SymbolAction(move="RIGHT", steps=1))
    wait = AgentDecision(action=SymbolAction(move="WAIT", steps=0))
    result = DualAgentSession(
        symbol_map,
        {
            "F": ScriptedPolicy([right, right, right, right], name="F"),
            "W": ScriptedPolicy([wait, wait, wait, wait], name="W"),
        },
        max_rounds=4,
    ).run()
    diagnosis = result.trace["diagnosis"]
    assert diagnosis["primary"] == "irreversible_deadlock"  # type: ignore[index]
    assert diagnosis["evidence"][0]["plate"] == "c1"  # type: ignore[index]
