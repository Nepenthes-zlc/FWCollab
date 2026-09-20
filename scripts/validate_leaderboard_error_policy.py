"""Pure-local synthetic conformance audit for leaderboard_v1 error policy."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fwcollab.leaderboard import (  # noqa: E402
    InfrastructureFailure, InterfaceUnavailable, SafetyRefusalError, run_leaderboard_decision,
)

OUT = ROOT / "artifacts/audits/leaderboard_error_policy_v1/REPORT.md"


def _request(value):
    def call():
        if isinstance(value, BaseException):
            raise value
        return value
    return call


def main() -> None:
    cases = [
        ("malformed output", "model_behavior", "not JSON"),
        ("schema invalid output", "model_behavior", '{"action":{"move":"WAIT","steps":1}}'),
        ("empty model text", "model_behavior", ""),
        ("explicit safety refusal", "model_behavior", SafetyRefusalError("synthetic refusal")),
        ("HTTP 429", "infrastructure", InfrastructureFailure("http_429")),
        ("HTTP 500", "infrastructure", InfrastructureFailure("http_500")),
        ("HTTP 502", "infrastructure", InfrastructureFailure("http_502")),
        ("HTTP 503", "infrastructure", InfrastructureFailure("http_503")),
        ("connection reset", "infrastructure", InfrastructureFailure("connection_reset")),
        ("transport error", "infrastructure", InfrastructureFailure("transport_error")),
        ("gateway unavailable", "infrastructure", InfrastructureFailure("gateway_unavailable")),
        ("HTTP request timeout", "infrastructure", InfrastructureFailure("http_request_timeout")),
        ("authentication failure", "systematic_configuration", InterfaceUnavailable("authentication_failure")),
        ("unknown model", "systematic_configuration", InterfaceUnavailable("unknown_model")),
        ("unsupported parameter", "systematic_configuration", InterfaceUnavailable("unsupported_parameter")),
        ("invalid endpoint", "systematic_configuration", InterfaceUnavailable("invalid_endpoint")),
    ]
    rows = []
    for name, expected, value in cases:
        result = run_leaderboard_decision(_request(value))
        expected_retry = 0 if expected == "systematic_configuration" else 1
        expected_scored = expected == "model_behavior"
        expected_wait = expected == "model_behavior"
        expected_rerun = expected == "infrastructure"
        passed = (
            result.failure_class == expected and result.retry_count == expected_retry
            and result.scored == expected_scored and result.wait_substitution == expected_wait
            and result.rerunnable_key == expected_rerun
        )
        rows.append((name, expected, result.failure_class, result.error_type, result.retry_count, result.scored, result.wait_substitution, result.rerunnable_key, passed))
    if not all(row[-1] for row in rows):
        raise RuntimeError("leaderboard error-policy conformance failed")
    lines = [
        "# Leaderboard error-policy local conformance", "",
        "Status: **PASS (16/16)**", "",
        "This audit used synthetic local callables only: model/API calls = **0**. The wrapper is isolated in `fwcollab.leaderboard`; the historical generic adapter and runner semantics are unchanged.", "",
        "| Case | Expected class | Actual class | Error type | Retry count | Scored? | WAIT substitution? | Rerunnable key? | Pass |",
        "|---|---|---|---|---:|---|---|---|---|",
    ]
    for row in rows:
        name, expected, actual, kind, retries, scored, wait, rerun, passed = row
        lines.append(f"| {name} | {expected} | {actual} | `{kind}` | {retries} | {'yes' if scored else 'no'} | {'yes' if wait else 'no'} | {'yes' if rerun else 'no'} | {'PASS' if passed else 'FAIL'} |")
    lines += [
        "", "Systematic configuration failures also set `stop_model_entry=true`; they are not retried and do not create scored failures for remaining tasks. Infrastructure exhaustion creates no decision, preserves the same missing episode key, and permits only integrity recovery of that key. No human repair path exists.", "",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("PASS 16/16; model/API calls=0")


if __name__ == "__main__":
    main()
