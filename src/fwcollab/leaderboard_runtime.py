"""Frozen Standard-52 provider boundary and integrity-safe policy.

This module is additive: it does not change the symbolic runner, prompt builder,
or evaluator. Infrastructure exhaustion aborts an episode before it can become
a scored WAIT trajectory; model-behavior failures use the frozen WAIT rule.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

from fwcollab.leaderboard import (
    InfrastructureFailure, InterfaceUnavailable, SafetyRefusalError,
    run_leaderboard_decision,
)
from fwcollab.symbolic.agents import (
    PolicyReply, _response_text, build_agent_prompt,
)
from fwcollab.symbolic.world import Role

ApiStyle = Literal["responses", "chat_completions"]


class UnscoredInfrastructureAbort(BaseException):
    """Escape the historical runner's scored model-error fallback."""

    def __init__(self, error_type: str, message: str = "") -> None:
        super().__init__(message or error_type)
        self.error_type = error_type


class InterfaceUnavailableAbort(BaseException):
    """Stop a matrix entry on a systematic endpoint/model failure."""

    def __init__(self, error_type: str, message: str = "") -> None:
        super().__init__(message or error_type)
        self.error_type = error_type


def api_url(endpoint: str, api_style: ApiStyle) -> str:
    value = endpoint.rstrip("/")
    suffix = "/responses" if api_style == "responses" else "/chat/completions"
    if value.endswith(suffix):
        return value
    return value + suffix if value.endswith("/v1") else value + "/v1" + suffix


def request_payload(
    *, model: str, prompt: str, api_style: ApiStyle, reasoning_setting: str,
    temperature: float | None, seed: int | None, max_output_tokens: int,
) -> dict[str, Any]:
    if api_style == "responses":
        payload: dict[str, Any] = {
            "model": model, "input": prompt,
            "max_output_tokens": max_output_tokens, "stream": False,
        }
        if reasoning_setting in {"off", "none"}:
            payload["reasoning"] = {"effort": "none"}
        elif reasoning_setting not in {"unsupported", "omit"}:
            payload["reasoning"] = {"effort": reasoning_setting}
    else:
        payload = {
            "model": model, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_output_tokens, "stream": False,
        }
        if reasoning_setting in {"off", "none"}:
            payload["reasoning_effort"] = "none"
        elif reasoning_setting not in {"unsupported", "omit"}:
            payload["reasoning_effort"] = reasoning_setting
    if temperature is not None:
        payload["temperature"] = temperature
    if seed is not None:
        payload["seed"] = seed
    return payload


def provider_request(
    *, endpoint: str, api_key: str | None, payload: Mapping[str, Any],
    timeout_seconds: float,
) -> tuple[str, str, Mapping[str, Any]]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", errors="replace")
        if exc.code == 429 or 500 <= exc.code <= 599:
            raise InfrastructureFailure(f"http_{exc.code}", detail) from exc
        if exc.code in {401, 403}:
            raise InterfaceUnavailable("authentication_failure", detail) from exc
        raise InterfaceUnavailable("invalid_endpoint_model_or_parameter", f"HTTP {exc.code}: {detail}") from exc
    except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as exc:
        error_type = "http_request_timeout" if isinstance(exc, TimeoutError) else "transport_error"
        raise InfrastructureFailure(error_type, str(exc)) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InfrastructureFailure("malformed_provider_envelope", str(exc)) from exc
    if not isinstance(body, Mapping):
        raise InfrastructureFailure("malformed_provider_envelope")
    refusal = body.get("refusal")
    if refusal:
        raise SafetyRefusalError(str(refusal))
    try:
        text = _response_text(body)
    except ValueError:
        text = ""
    return text, str(body.get("model") or payload["model"]), body


@dataclass(slots=True)
class ProviderCounters:
    requests: int = 0
    retries: int = 0
    model_behavior_errors: int = 0
    infrastructure_errors: int = 0
    interface_errors: int = 0
    wait_substitutions: int = 0
    resolved_ids: set[str] = field(default_factory=set)
    error_types: dict[str, int] = field(default_factory=dict)

    def error(self, kind: str) -> None:
        self.error_types[kind] = self.error_types.get(kind, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_requests": self.requests, "retries": self.retries,
            "model_behavior_errors": self.model_behavior_errors,
            "infrastructure_errors": self.infrastructure_errors,
            "interface_errors": self.interface_errors,
            "wait_substitutions": self.wait_substitutions,
            "resolved_model_ids": sorted(self.resolved_ids),
            "error_types": dict(sorted(self.error_types.items())),
        }


class LeaderboardPolicy:
    """Independent role policy using the frozen prompt and error contract."""

    def __init__(
        self, *, endpoint: str, model: str, api_style: ApiStyle, api_key: str | None,
        reasoning_setting: str, temperature: float | None, seed: int | None,
        max_output_tokens: int = 800, timeout_seconds: float = 120.0,
    ) -> None:
        self.endpoint = api_url(endpoint, api_style)
        self.model = model
        self.name = model
        self.api_style = api_style
        self.api_key = api_key
        self.reasoning_setting = reasoning_setting
        self.temperature = temperature
        self.seed = seed
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds
        self.counters = ProviderCounters()

    def act(
        self, role: Role, observation: Mapping[str, object],
        inbox: Sequence[Mapping[str, object]], own_history: Sequence[Mapping[str, object]],
    ) -> PolicyReply:
        prompt = build_agent_prompt(role, observation, inbox, own_history, communication_enabled=True)
        latest: dict[str, str] = {}

        def once() -> str:
            self.counters.requests += 1
            try:
                raw, resolved, _ = provider_request(
                    endpoint=self.endpoint, api_key=self.api_key,
                    payload=request_payload(
                        model=self.model, prompt=prompt, api_style=self.api_style,
                        reasoning_setting=self.reasoning_setting, temperature=self.temperature,
                        seed=self.seed, max_output_tokens=self.max_output_tokens,
                    ), timeout_seconds=self.timeout_seconds,
                )
                latest["raw"], latest["resolved"] = raw, resolved
                self.counters.resolved_ids.add(resolved)
                return raw
            except InfrastructureFailure as exc:
                self.counters.infrastructure_errors += 1; self.counters.error(exc.error_type); raise
            except InterfaceUnavailable as exc:
                self.counters.interface_errors += 1; self.counters.error(exc.error_type); raise
            except SafetyRefusalError:
                raise

        started = time.perf_counter()
        result = run_leaderboard_decision(once, max_retries=1)
        self.counters.retries += result.retry_count
        if result.status == "infrastructure_error":
            raise UnscoredInfrastructureAbort(result.error_type or "infrastructure_error")
        if result.status == "interface_unavailable":
            raise InterfaceUnavailableAbort(result.error_type or "interface_unavailable")
        if result.status == "model_behavior_error":
            self.counters.model_behavior_errors += result.retry_count + 1
            self.counters.wait_substitutions += 1
            self.counters.error(result.error_type or "model_behavior_error")
        assert result.decision is not None
        return PolicyReply(
            decision=result.decision, latency_ms=round((time.perf_counter() - started) * 1000),
            raw_output=latest.get("raw", ""),
            resolved_model=latest.get("resolved", self.model),
            provider_calls=result.retry_count + 1,
        )
