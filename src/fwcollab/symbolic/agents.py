"""Independent policy adapters for the two symbolic agents."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol, Sequence

from pydantic import Field

from fwcollab.core.state import StrictModel
from fwcollab.symbolic.dag import validate_state_dag
from fwcollab.symbolic.world import Role, SymbolAction


class CoordinationMessage(StrictModel):
    """Small public protocol payload; the runner adds round and hash metadata."""

    stage: str = Field(default="", max_length=64)
    status: Literal["INFO", "READY", "HOLDING", "CROSSED", "RELEASE", "BLOCKED"] = "INFO"
    fact: str = Field(default="", max_length=256)
    request: str = Field(default="", max_length=256)
    until: str = Field(default="", max_length=128)


class AgentDecision(StrictModel):
    """The small, auditable answer requested from each role model."""

    action: SymbolAction
    message: str | CoordinationMessage | None = None
    reason: str = Field(default="", max_length=512)
    subgoal: str = Field(default="", max_length=256)
    commitment: str = Field(default="", max_length=256)
    expected_teammate: str = Field(default="", max_length=256)


@dataclass(frozen=True, slots=True)
class PolicyReply:
    decision: AgentDecision
    latency_ms: int = 0
    raw_output: str = ""
    resolved_model: str = "offline"
    provider_calls: int = 0


@dataclass(frozen=True, slots=True)
class PlanReply:
    plan: dict[str, Any]
    latency_ms: int = 0
    raw_output: str = ""
    resolved_model: str = "offline"
    provider_calls: int = 0


class ProviderRequestError(RuntimeError):
    def __init__(self, message: str, *, attempts: int) -> None:
        super().__init__(message)
        self.attempts = attempts


class Policy(Protocol):
    name: str

    def act(
        self,
        role: Role,
        observation: Mapping[str, object],
        inbox: Sequence[Mapping[str, object]],
        own_history: Sequence[Mapping[str, object]],
    ) -> PolicyReply: ...


class ScriptedPolicy:
    """Deterministic offline policy used for engine and replay acceptance."""

    def __init__(self, decisions: Sequence[AgentDecision], *, name: str = "scripted") -> None:
        self.name = name
        self._decisions = list(decisions)
        self._cursor = 0

    def act(
        self,
        role: Role,
        observation: Mapping[str, object],
        inbox: Sequence[Mapping[str, object]],
        own_history: Sequence[Mapping[str, object]],
    ) -> PolicyReply:
        del role, observation, inbox, own_history
        if self._cursor >= len(self._decisions):
            decision = AgentDecision(action=SymbolAction(move="WAIT", steps=0), reason="script exhausted")
        else:
            decision = self._decisions[self._cursor]
            self._cursor += 1
        return PolicyReply(decision=decision, resolved_model=self.name)


def _json_object(text: str) -> dict[str, object]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("model output does not contain a JSON object") from None
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model output must be one JSON object")
    return value


def parse_agent_decision(text: str) -> AgentDecision:
    value = _json_object(text)
    action_value = value.get("action")
    if action_value is None:
        action_value = {"move": value.get("move"), "steps": value.get("steps")}
    return AgentDecision.model_validate(
        {
            "action": action_value,
            "message": value.get("message"),
            "reason": value.get("reason", value.get("subgoal", "")),
            "subgoal": value.get("subgoal", ""),
            "commitment": value.get("commitment", ""),
            "expected_teammate": value.get("expected_teammate", ""),
        }
    )


def parse_agent_plan(text: str) -> dict[str, Any]:
    value = _json_object(text)
    if value.get("visibility") != "agent_candidate":
        raise ValueError("agent plan visibility must be agent_candidate")
    validate_state_dag(value)
    return value


def build_plan_prompt(
    role: Role,
    observation: Mapping[str, object],
    peer_candidate: Mapping[str, object] | None = None,
) -> str:
    from fwcollab.symbolic.dag_curriculum import MOTIFS

    teammate = "W" if role == "F" else "F"
    motif_catalog = {motif_id: motif.name for motif_id, motif in sorted(MOTIFS.items())}
    peer = "null" if peer_candidate is None else json.dumps(peer_candidate, ensure_ascii=False)
    phase = (
        "先独立提出一份完整候选 DAG。"
        if peer_candidate is None
        else "阅读队友候选，修正你自己的完整 DAG；不要只输出差异。双方应尽量收敛到类型等价的图。"
    )
    return f"""你是 FWCollab 角色 {role} 的规划智能体，队友是 {teammate}。现在只做执行前规划，不提交移动动作。{phase}

根据公开地图、机关连接和规则，输出一个状态依赖 DAG。不得使用或猜测私有参考图、标准轨迹和隐藏标签。F 对应 owner=agent_a，W 对应 owner=agent_b；交换角色命名在评分时等价。

只输出一个合法 JSON 对象，不要 Markdown。契约：
- format 固定为 fwcollab.symbolic.state_dag.v1
- visibility 固定为 agent_candidate；difficulty 可填 L1；evaluation_role 填 diagnostic
- mechanisms 只能从 M01-M30 选择实际参与计划的类型
- 每个 node 必须有 id、kind(condition|state|event|goal)、owner(agent_a|agent_b|team|environment)、join(all|any)、label
- predicate 必须恰含 op、motif、state、phase；起点使用 op=start,motif=SYSTEM，终点使用 op=team_success,motif=SYSTEM
- 每条 edge 必须有 from、to、relation(requires|enables|maintains|handoff|synchronizes)
- 恰好一个起点和一个终点；所有节点从起点可达且能到终点；禁止环
- id/label 可以自然表达实例坐标，但结构评分不依赖措辞

公开 M01-M30 名称：
{json.dumps(motif_catalog, ensure_ascii=False)}

队友上一版候选：
{peer}

当前公开观察：
{json.dumps(observation, ensure_ascii=False)}
"""


def _response_text(body: Mapping[str, object]) -> str:
    if isinstance(body.get("output_text"), str):
        return str(body["output_text"])
    pieces: list[str] = []
    output = body.get("output", [])
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content", [])
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    pieces.append(part["text"])
    if pieces:
        return "".join(pieces)
    choices = body.get("choices", [])
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message", {})
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]
    raise ValueError("provider response contains no model text")


def build_agent_prompt(
    role: Role,
    observation: Mapping[str, object],
    inbox: Sequence[Mapping[str, object]],
    own_history: Sequence[Mapping[str, object]],
    *,
    communication_enabled: bool = True,
) -> str:
    teammate = "W" if role == "F" else "F"
    history = list(own_history[-8:])
    coordination_mode = observation.get("coordination_mode", "emergent")
    coordination_guidance = (
        "本局是 protocol_assisted：持续控制机关需要同步时，按 rules.communication.held_gate_handshake 执行。"
        if coordination_mode == "protocol_assisted"
        else "本局是 emergent：系统不规定固定握手顺序，你们需要自行形成并维护协作协议。"
    )
    communication_guidance = (
        "消息不占用动作，但下一轮才送达；没有新的协作事实、请求、承诺或阻塞时使用 message=null，不要重复播报不变状态。"
        if communication_enabled
        else "本实验条件已禁用显式通信：你不会收到队友消息，且你输出的消息不会送达。必须仅依据共享公开状态进行隐式协调，并始终使用 message=null。"
    )
    message_contract = (
        '"message":null或{{"stage":"机关ID或空串","status":"INFO|READY|HOLDING|CROSSED|RELEASE|BLOCKED","fact":"当前已核对事实","request":"希望队友做什么","until":"承诺保持到什么可验证条件"}}'
        if communication_enabled
        else '"message":null'
    )
    visible_inbox = inbox if communication_enabled else ()
    return f"""你是 FWCollab 中唯一控制角色 {role} 的规划智能体，队友 {teammate} 由另一个独立模型控制。

你们每轮基于同一个冻结公开状态同时行动。你看不到队友本轮尚未提交的动作；上一轮已经结算的公开变化见 observation.last_transition。严格遵守 observation.rules，以 mechanisms 的机关连接和 dynamic_state 的当前真假值为准。current_rows 可能覆盖脚下符号，必须结合 terrain_rows 阅读。{communication_guidance}

你每轮只能提交一个相邻单格动作。进入门或桥按本轮冻结状态判断。规划时关注 observation.clock 的剩余轮数。不要假设隐藏答案、自动寻路或队友当前动作；对收到的旧消息必须用当前公开状态重新核对。

{coordination_guidance}

只输出一个 JSON 对象，不要 Markdown：
{{"action":{{"move":"UP|DOWN|LEFT|RIGHT|WAIT","steps":1}},"subgoal":"当前子目标","commitment":"本角色会持续履行的承诺","expected_teammate":"期望队友完成的动作或状态",{message_contract},"reason":"可记录的简短决策理由"}}
WAIT 时把 steps 改为 0。保持 subgoal 和 commitment 稳定，完成或状态变化时再更新。message 只能描述已核对的事实或明确请求，不得把准备执行的动作写成已经完成。reason 只写简短策略依据，不写长篇思维过程。

本轮收到的消息：
{json.dumps(list(visible_inbox), ensure_ascii=False)}

你的近期独立历史（不含队友私有计划）：
{json.dumps(history, ensure_ascii=False)}

当前紧凑但完整的公开观察：
{json.dumps(observation, ensure_ascii=False)}
"""


class OpenAICompatiblePolicy:
    """Minimal Responses/Chat Completions adapter with no credential persistence."""

    def __init__(
        self,
        *,
        endpoint: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 120.0,
        max_output_tokens: int = 1600,
        max_retries: int = 1,
        api_style: Literal["responses", "chat_completions"] = "responses",
        communication_enabled: bool = True,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> None:
        self.api_style = api_style
        self.endpoint = self._api_url(endpoint, api_style)
        self.model = model
        self.name = model
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.max_retries = max_retries
        self.communication_enabled = communication_enabled
        self.temperature = temperature
        self.seed = seed

    @staticmethod
    def _api_url(
        endpoint: str, api_style: Literal["responses", "chat_completions"]
    ) -> str:
        value = endpoint.rstrip("/")
        suffix = "/responses" if api_style == "responses" else "/chat/completions"
        if value.endswith(suffix):
            return value
        if value.endswith("/v1"):
            return value + suffix
        return value + "/v1" + suffix

    def _payload(self, prompt: str, *, max_output_tokens: int) -> bytes:
        if self.api_style == "chat_completions":
            value = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "reasoning_effort": "low",
                "max_tokens": max_output_tokens,
                "stream": False,
            }
        else:
            value = {
                "model": self.model,
                "input": prompt,
                "reasoning": {"effort": "low"},
                "max_output_tokens": max_output_tokens,
                "stream": False,
            }
        if self.temperature is not None:
            value["temperature"] = self.temperature
        if self.seed is not None:
            value["seed"] = self.seed
        return json.dumps(value, ensure_ascii=False).encode("utf-8")

    def act(
        self,
        role: Role,
        observation: Mapping[str, object],
        inbox: Sequence[Mapping[str, object]],
        own_history: Sequence[Mapping[str, object]],
    ) -> PolicyReply:
        prompt = build_agent_prompt(
            role,
            observation,
            inbox if self.communication_enabled else (),
            own_history,
            communication_enabled=self.communication_enabled,
        )
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        started = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 2):
            retry_note = "\n上次响应无效。务必只返回完整、合法的单个 JSON 对象。" if attempt > 1 else ""
            payload = self._payload(
                prompt + retry_note, max_output_tokens=self.max_output_tokens
            )
            request = urllib.request.Request(self.endpoint, data=payload, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    body = json.loads(response.read().decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("provider response must be a JSON object")
                raw = _response_text(body)
                decision = parse_agent_decision(raw)
                if not self.communication_enabled and decision.message is not None:
                    decision = decision.model_copy(update={"message": None})
                return PolicyReply(
                    decision=decision,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    raw_output=raw,
                    resolved_model=str(body.get("model", self.model)),
                    provider_calls=attempt,
                )
            except urllib.error.HTTPError as exc:
                detail = exc.read(2048).decode("utf-8", errors="replace")
                last_error = RuntimeError(f"provider HTTP {exc.code}: {detail}")
            except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
        raise ProviderRequestError(
            f"provider failed after {self.max_retries + 1} attempts: {type(last_error).__name__}: {last_error}",
            attempts=self.max_retries + 1,
        )

    def propose_plan(
        self,
        role: Role,
        observation: Mapping[str, object],
        peer_candidate: Mapping[str, object] | None = None,
    ) -> PlanReply:
        """Produce one schema-valid public candidate DAG for optional plan-first runs."""

        prompt = build_plan_prompt(role, observation, peer_candidate)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        started = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 2):
            retry_note = (
                "\n上次候选无效。务必只返回符合给定 DAG 契约的完整、合法 JSON 对象。"
                if attempt > 1
                else ""
            )
            payload = self._payload(
                prompt + retry_note,
                max_output_tokens=max(3000, self.max_output_tokens),
            )
            request = urllib.request.Request(self.endpoint, data=payload, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    body = json.loads(response.read().decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("provider response must be a JSON object")
                raw = _response_text(body)
                plan = parse_agent_plan(raw)
                return PlanReply(
                    plan=plan,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    raw_output=raw,
                    resolved_model=str(body.get("model", self.model)),
                    provider_calls=attempt,
                )
            except urllib.error.HTTPError as exc:
                detail = exc.read(2048).decode("utf-8", errors="replace")
                last_error = RuntimeError(f"provider HTTP {exc.code}: {detail}")
            except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
        raise ProviderRequestError(
            f"provider failed after {self.max_retries + 1} planning attempts: "
            f"{type(last_error).__name__}: {last_error}",
            attempts=self.max_retries + 1,
        )
