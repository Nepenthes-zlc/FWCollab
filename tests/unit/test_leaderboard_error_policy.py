from __future__ import annotations

from fwcollab.leaderboard import (
    InfrastructureFailure,
    InterfaceUnavailable,
    SafetyRefusalError,
    run_leaderboard_decision,
)


def _always(value):
    def call():
        if isinstance(value, BaseException):
            raise value
        return value
    return call


def test_model_behavior_exhaustion_is_scored_wait() -> None:
    for value, expected in (
        ("not JSON", "malformed_output"),
        ('{"action":{"move":"WAIT","steps":1}}', "schema_invalid_output"),
        ("", "empty_model_response"),
        (SafetyRefusalError("refused"), "safety_refusal"),
    ):
        result = run_leaderboard_decision(_always(value))
        assert result.status == "model_behavior_error"
        assert result.error_type == expected
        assert result.retry_count == 1
        assert result.scored and result.wait_substitution
        assert not result.rerunnable_key and not result.stop_model_entry
        assert result.decision is not None and result.decision.action.move == "WAIT"


def test_infrastructure_exhaustion_retains_unscored_key() -> None:
    for kind in ("http_429", "http_500", "http_502", "http_503", "connection_reset", "transport_error", "gateway_unavailable", "http_request_timeout"):
        result = run_leaderboard_decision(_always(InfrastructureFailure(kind)))
        assert result.status == "infrastructure_error"
        assert result.error_type == kind
        assert result.retry_count == 1
        assert not result.scored and not result.wait_substitution
        assert result.rerunnable_key and not result.stop_model_entry
        assert result.decision is None


def test_systematic_configuration_stops_entry_without_retry() -> None:
    for kind in ("authentication_failure", "unknown_model", "unsupported_parameter", "invalid_endpoint"):
        result = run_leaderboard_decision(_always(InterfaceUnavailable(kind)))
        assert result.status == "interface_unavailable"
        assert result.error_type == kind
        assert result.retry_count == 0
        assert not result.scored and not result.wait_substitution and not result.rerunnable_key
        assert result.stop_model_entry and result.decision is None


def test_success_after_retry_remains_a_normal_scored_decision() -> None:
    values = iter(["", '{"action":{"move":"RIGHT","steps":1},"message":null}'])
    result = run_leaderboard_decision(lambda: next(values))
    assert result.status == "usable"
    assert result.retry_count == 1
    assert result.scored and not result.wait_substitution
    assert result.decision is not None and result.decision.action.move == "RIGHT"
