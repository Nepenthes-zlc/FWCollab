"""Leaderboard-only response/error handling.

This module deliberately does not alter the historical symbolic runner.  It is
the pre-registered boundary that a future ``leaderboard_v1`` harness must use
before passing a usable decision to that runner.
"""

from __future__ import annotations

import socket
import urllib.error
from dataclasses import dataclass
from typing import Callable, Literal

from pydantic import ValidationError

from fwcollab.symbolic.agents import AgentDecision, parse_agent_decision
from fwcollab.symbolic.world import SymbolAction

FailureClass = Literal["model_behavior", "infrastructure", "systematic_configuration"]


class SafetyRefusalError(RuntimeError):
    """The provider returned an explicit model refusal signal."""


class InfrastructureFailure(RuntimeError):
    """Normalized request/transport/provider failure from an adapter."""

    def __init__(self, error_type: str, message: str = "") -> None:
        super().__init__(message or error_type)
        self.error_type = error_type


class InterfaceUnavailable(RuntimeError):
    """Systematic model/endpoint configuration failure from an adapter."""

    def __init__(self, error_type: str, message: str = "") -> None:
        super().__init__(message or error_type)
        self.error_type = error_type


@dataclass(frozen=True, slots=True)
class LeaderboardDecisionResult:
    status: Literal["usable", "model_behavior_error", "infrastructure_error", "interface_unavailable"]
    failure_class: FailureClass | None
    error_type: str | None
    retry_count: int
    scored: bool
    wait_substitution: bool
    rerunnable_key: bool
    stop_model_entry: bool
    decision: AgentDecision | None


def _classified_exception(exc: BaseException) -> tuple[FailureClass, str]:
    if isinstance(exc, SafetyRefusalError):
        return "model_behavior", "safety_refusal"
    if isinstance(exc, InterfaceUnavailable):
        return "systematic_configuration", exc.error_type
    if isinstance(exc, InfrastructureFailure):
        return "infrastructure", exc.error_type
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 429:
            return "infrastructure", "http_429"
        if 500 <= exc.code <= 599:
            return "infrastructure", f"http_{exc.code}"
        if exc.code in {401, 403}:
            return "systematic_configuration", "authentication_failure"
        return "systematic_configuration", "invalid_endpoint_or_request"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "infrastructure", "http_request_timeout"
    if isinstance(exc, ConnectionResetError):
        return "infrastructure", "connection_reset"
    if isinstance(exc, (ConnectionError, urllib.error.URLError, OSError)):
        return "infrastructure", "transport_error"
    raise TypeError(
        "leaderboard adapter must normalize unknown failures as "
        "InfrastructureFailure, InterfaceUnavailable, or SafetyRefusalError"
    ) from exc


def _parse_model_text(raw: str) -> AgentDecision:
    if not isinstance(raw, str) or not raw.strip():
        raise _ModelBehaviorFailure("empty_model_response")
    try:
        return parse_agent_decision(raw)
    except ValidationError as exc:
        raise _ModelBehaviorFailure("schema_invalid_output") from exc
    except (ValueError, TypeError) as exc:
        raise _ModelBehaviorFailure("malformed_output") from exc


class _ModelBehaviorFailure(RuntimeError):
    def __init__(self, error_type: str) -> None:
        super().__init__(error_type)
        self.error_type = error_type


def run_leaderboard_decision(
    request_once: Callable[[], str], *, max_retries: int = 1
) -> LeaderboardDecisionResult:
    """Run one role decision under the frozen leaderboard error policy.

    ``request_once`` is an adapter boundary: it returns model text, raises an
    explicit refusal, or raises/normalizes a provider/interface exception.
    Infrastructure exhaustion never produces a decision and therefore cannot
    enter the scored symbolic episode as a synthetic WAIT.
    """

    if max_retries != 1:
        raise ValueError("leaderboard_v1 freezes exactly one retry")
    last_class: FailureClass | None = None
    last_type: str | None = None
    for attempt in range(max_retries + 1):
        try:
            decision = _parse_model_text(request_once())
            return LeaderboardDecisionResult(
                status="usable", failure_class=None, error_type=None,
                retry_count=attempt, scored=True, wait_substitution=False,
                rerunnable_key=False, stop_model_entry=False, decision=decision,
            )
        except _ModelBehaviorFailure as exc:
            last_class, last_type = "model_behavior", exc.error_type
        except BaseException as exc:
            last_class, last_type = _classified_exception(exc)
        if last_class == "systematic_configuration":
            return LeaderboardDecisionResult(
                status="interface_unavailable", failure_class=last_class,
                error_type=last_type, retry_count=attempt, scored=False,
                wait_substitution=False, rerunnable_key=False,
                stop_model_entry=True, decision=None,
            )

    if last_class == "model_behavior":
        wait = AgentDecision(
            action=SymbolAction(move="WAIT", steps=0),
            reason="leaderboard model behavior error; substituted WAIT",
        )
        return LeaderboardDecisionResult(
            status="model_behavior_error", failure_class=last_class,
            error_type=last_type, retry_count=max_retries, scored=True,
            wait_substitution=True, rerunnable_key=False,
            stop_model_entry=False, decision=wait,
        )
    return LeaderboardDecisionResult(
        status="infrastructure_error", failure_class="infrastructure",
        error_type=last_type, retry_count=max_retries, scored=False,
        wait_substitution=False, rerunnable_key=True, stop_model_entry=False,
        decision=None,
    )
