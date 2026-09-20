"""Non-benchmark API/interface preflight for leaderboard candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fwcollab.leaderboard_runtime import api_url, provider_request, request_payload  # noqa: E402
from fwcollab.symbolic.agents import parse_agent_decision  # noqa: E402

DEFAULT_MODELS = [
    {"model_id": "gpt-5.5", "provider": "local_copilot_gateway", "api_style": "responses"},
    {"model_id": "gpt-5-mini", "provider": "local_copilot_gateway", "api_style": "responses"},
    {"model_id": "gemini-3.7-flash", "provider": "local_copilot_gateway", "api_style": "chat_completions"},
    {"model_id": "gemini-3.5-flash", "provider": "local_copilot_gateway", "api_style": "chat_completions"},
]
SYNTHETIC_PROMPT = """This is an API schema test with no benchmark content. Return exactly one JSON object and no Markdown: {\"action\":{\"move\":\"WAIT\",\"steps\":0},\"message\":null,\"reason\":\"synthetic schema test\"}"""


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def model_catalog(endpoint: str, catalog_url: str | None, timeout: float) -> dict[str, dict[str, Any]]:
    if catalog_url is None:
        marker = "/api/openai"
        if marker in endpoint:
            catalog_url = endpoint.split(marker, 1)[0] + "/api/v1/lm/chatModels"
        else:
            catalog_url = endpoint.rstrip("/") + ("/models" if endpoint.rstrip("/").endswith("/v1") else "/v1/models")
    with urllib.request.urlopen(catalog_url, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    rows = value.get("data", []) if isinstance(value, dict) else value
    if not isinstance(rows, list):
        raise ValueError("model catalog must be a list or OpenAI data list")
    return {str(row["id"]): row for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)}


def one(
    model: dict[str, str], *, endpoint: str, api_key: str | None, timeout: float,
    temperature: float | None = None, seed: int | None = None, reasoning: str = "off",
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        text, resolved, body = provider_request(
            endpoint=api_url(endpoint, model["api_style"]), api_key=api_key,
            payload=request_payload(
                model=model["model_id"], prompt=SYNTHETIC_PROMPT,
                api_style=model["api_style"], reasoning_setting=reasoning,
                temperature=temperature, seed=seed, max_output_tokens=800,
            ), timeout_seconds=timeout,
        )
        parse_agent_decision(text)
        return {
            "ok": True, "resolved_model_id": resolved,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "schema_valid": True, "usage_present": isinstance(body.get("usage"), dict),
        }
    except BaseException as exc:
        return {
            "ok": False, "error_class": type(exc).__name__,
            "error_type": getattr(exc, "error_type", None),
            "message": str(exc)[:500],
            "latency_ms": round((time.perf_counter() - started) * 1000),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:4141")
    parser.add_argument("--api-key-env", default="FWCOLLAB_API_KEY")
    parser.add_argument("--model-id", action="append")
    parser.add_argument("--api-style", choices=("responses", "chat_completions"))
    parser.add_argument("--provider", default="openai_compatible")
    parser.add_argument("--model-catalog-url")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", default="eval_private/leaderboard_v1/preflight/closed4_preflight.json")
    args = parser.parse_args()
    if args.model_id:
        if not args.api_style:
            parser.error("--api-style is required with --model-id")
        models = [{"model_id": value, "provider": args.provider, "api_style": args.api_style} for value in args.model_id]
    else:
        models = DEFAULT_MODELS
    api_key = os.environ.get(args.api_key_env)
    try:
        catalog = model_catalog(args.endpoint, args.model_catalog_url, args.timeout)
        catalog_error = None
    except Exception as exc:
        catalog, catalog_error = {}, f"{type(exc).__name__}: {exc}"
    records = []
    for model in models:
        catalog_row = catalog.get(model["model_id"])
        if catalog_row is None:
            records.append({
                **model, "endpoint_identity": args.endpoint, "authentication": "not_tested",
                "baseline": None, "benchmark_content_used": False, "max_output_tokens": 800,
                "catalog_exact_match": False, "catalog_error": catalog_error,
                "resolved_model_id": None, "structured_output_compatibility": "not_tested",
                "temperature": None, "seed": None, "reasoning": None,
                "retry_error_classification": "local_conformance_16_of_16_pass",
                "preflight_status": "FAIL_EXACT_MODEL_NOT_AVAILABLE",
            })
            print(f"{model['model_id']}: FAIL_EXACT_MODEL_NOT_AVAILABLE", flush=True)
            continue
        baseline = one(model, endpoint=args.endpoint, api_key=api_key, timeout=args.timeout, reasoning="omit")
        record: dict[str, Any] = {
            **model, "endpoint_identity": args.endpoint, "authentication": "pass" if baseline["ok"] else "fail",
            "baseline": baseline, "benchmark_content_used": False, "catalog_exact_match": True,
            "catalog_record": {key: catalog_row.get(key) for key in ("id", "family", "version", "vendor", "maxInputTokens")},
            "max_output_tokens": 800, "retry_error_classification": "local_conformance_16_of_16_pass",
        }
        if baseline["ok"]:
            reasoning_off = one(model, endpoint=args.endpoint, api_key=api_key, timeout=args.timeout, reasoning="none")
            reasoning_low = None if reasoning_off["ok"] else one(model, endpoint=args.endpoint, api_key=api_key, timeout=args.timeout, reasoning="low")
            selected_reasoning = "none" if reasoning_off["ok"] else ("low" if reasoning_low and reasoning_low["ok"] else "UNSUPPORTED")
            temperature = one(model, endpoint=args.endpoint, api_key=api_key, timeout=args.timeout, temperature=1.0, reasoning=selected_reasoning) if selected_reasoning != "UNSUPPORTED" else {"ok": False, "message": "reasoning unresolved"}
            seed = one(model, endpoint=args.endpoint, api_key=api_key, timeout=args.timeout, seed=2026091801, reasoning=selected_reasoning) if selected_reasoning != "UNSUPPORTED" else {"ok": False, "message": "reasoning unresolved"}
            record.update({
                "resolved_model_id": baseline["resolved_model_id"],
                "structured_output_compatibility": "pass",
                "temperature": {"requested": 1.0, "supported": temperature["ok"], "probe": temperature},
                "seed": {"requested": 2026091801, "accepted": seed["ok"], "determinism_guaranteed": False, "probe": seed},
                "reasoning": {
                    "preferred": "off", "off_probe": reasoning_off, "low_probe": reasoning_low,
                    "selected": selected_reasoning,
                },
            })
            record["preflight_status"] = "PASS" if record["reasoning"]["selected"] != "UNSUPPORTED" else "FAIL"
        else:
            record.update({
                "resolved_model_id": None, "structured_output_compatibility": "fail",
                "temperature": None, "seed": None, "reasoning": None, "preflight_status": "FAIL",
            })
        records.append(record)
        print(f"{model['model_id']}: {record['preflight_status']}", flush=True)
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": "fwcollab.leaderboard.non_benchmark_preflight.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "endpoint_identity": args.endpoint, "benchmark_content_used": False,
        "prompt_contract_hash": "862349ef54d6991f1b20102ac6936edde93bb7a0be8d1f849e993dd1e67b9831",
        "standard52_manifest_hash": "b3970fdd31ef2259d477a33801c7442be92df6ed3cd7bb3cb07399eaa21a0716",
        "models": records, "all_pass": all(row["preflight_status"] == "PASS" for row in records),
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"all_pass": payload["all_pass"], "output": output.relative_to(ROOT).as_posix(), "sha256": sha(output)}))
    return 0 if payload["all_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
