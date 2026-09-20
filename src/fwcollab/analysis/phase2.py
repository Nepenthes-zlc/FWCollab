"""Offline, publication-gated analysis for the frozen Phase II experiment.

This module reads existing traces and frozen benchmark inputs only.  It never
constructs a policy or performs a provider request.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean, median
from typing import Any, Iterable, Mapping, Sequence

from fwcollab.symbolic.agents import AgentDecision
from fwcollab.symbolic.collaboration_eval import evaluate_collaboration_trace
from fwcollab.symbolic.map import load_symbol_map
from fwcollab.symbolic.runner import collaboration_metrics, replay_trace
from fwcollab.symbolic.world import SymbolWorld


EXPECTED_TASKS = 24
EXPECTED_CONDITIONS = 6
EXPECTED_REPLICATES = (1, 2, 3)
EXPECTED_EPISODES = 432
BOOTSTRAP_SAMPLES = 10_000
BOOTSTRAP_SEED = 20260914
CONFIDENCE_LEVEL = 0.95
DRAFT_SUBDIRECTORY = "draft_incomplete"
DRAFT_WATERMARK = "DRAFT - INCOMPLETE/UNVERIFIED - NOT FOR PUBLICATION"
ANALYSIS_ALGORITHM_VERSION = "fwcollab.phase2.analysis.v2"
BENCHMARK_VERSION = "v1"
SELECTION_STATEMENT = (
    "The diagnostic subset was selected solely from task structure and "
    "collaboration primitives, without access to model performance."
)
EXPECTED_REQUEST_SEEDS = {
    "1": 2026091401,
    "2": 2026091402,
    "3": 2026091403,
}
EXPECTED_SAMPLING = {
    "temperature": 1.0,
    "seed_is_requested_from_provider": True,
    "seed_reproducibility_guaranteed": False,
}
EXPECTED_RUNNER = {
    "max_rounds": 80,
    "planning_rounds": 0,
    "coordination_mode": "emergent",
    "movement_steps": 1,
    "message_delay_rounds": 1,
    "request_timeout_seconds": 120.0,
    "max_output_tokens": 800,
    "max_retries": 1,
    "episode_concurrency": 4,
}
EXPECTED_CONDITION_LABELS = {
    "gpt_selfplay": "GPT Self-play",
    "gemini_selfplay": "Gemini Self-play",
    "gpt_no_comm": "GPT No-Comm",
    "gemini_no_comm": "Gemini No-Comm",
    "crossplay_gptF_geminiW": "Cross-play A: GPT(F) + Gemini(W)",
    "crossplay_geminiF_gptW": "Cross-play B: Gemini(F) + GPT(W)",
}
EXPECTED_CONDITION_CONTRACT = {
    "gpt_selfplay": {
        "F": {"model": "gpt-5.5", "api_style": "responses"},
        "W": {"model": "gpt-5.5", "api_style": "responses"},
        "communication_enabled": True,
    },
    "gemini_selfplay": {
        "F": {"model": "gemini-3.7-flash", "api_style": "chat_completions"},
        "W": {"model": "gemini-3.7-flash", "api_style": "chat_completions"},
        "communication_enabled": True,
    },
    "gpt_no_comm": {
        "F": {"model": "gpt-5.5", "api_style": "responses"},
        "W": {"model": "gpt-5.5", "api_style": "responses"},
        "communication_enabled": False,
    },
    "gemini_no_comm": {
        "F": {"model": "gemini-3.7-flash", "api_style": "chat_completions"},
        "W": {"model": "gemini-3.7-flash", "api_style": "chat_completions"},
        "communication_enabled": False,
    },
    "crossplay_gptF_geminiW": {
        "F": {"model": "gpt-5.5", "api_style": "responses"},
        "W": {"model": "gemini-3.7-flash", "api_style": "chat_completions"},
        "communication_enabled": True,
    },
    "crossplay_geminiF_gptW": {
        "F": {"model": "gemini-3.7-flash", "api_style": "chat_completions"},
        "W": {"model": "gpt-5.5", "api_style": "responses"},
        "communication_enabled": True,
    },
}

CONDITION_IDS = (
    "gpt_selfplay",
    "gemini_selfplay",
    "gpt_no_comm",
    "gemini_no_comm",
    "crossplay_gptF_geminiW",
    "crossplay_geminiF_gptW",
)

METRICS = (
    "success_rate",
    "dag_completion",
    "dag_progress_auc",
    "clean_handoff_rate",
    "coordination_violation_rate",
    "coordination_violation_rate_per_agent_action",
    "rounds_to_success",
)

EPISODE_CSV_FIELDS = (
    "publication_status",
    "watermark",
    "condition",
    "condition_label",
    "replicate",
    "task_id",
    "difficulty",
    "outcome",
    *METRICS,
    "executed_rounds",
    "agent_action_opportunities",
    "handoff_opportunities",
    "clean_handoffs",
    "coordination_violations",
    "useful_hold_rounds",
    "useful_waits",
    "total_waits",
    "useful_wait_ratio",
    "model_calls",
    "model_errors",
    "messages",
    "resolved_models_F",
    "resolved_models_W",
    "failure_stage",
    "failure_responsible_agent",
    "diagnosis",
    "trace",
    "trace_sha256",
    "replay_verified",
    "observations_authenticated",
)

PRIMARY_METRICS = (
    "success_rate",
    "dag_completion",
    "dag_progress_auc",
    "clean_handoff_rate",
    "coordination_violation_rate",
    "rounds_to_success",
)

LOWER_IS_BETTER_METRICS = frozenset(
    {
        "coordination_violation_rate",
        "coordination_violation_rate_per_agent_action",
        "rounds_to_success",
    }
)

METRIC_LABELS = {
    "success_rate": "Success rate",
    "dag_completion": "DAG completion",
    "dag_progress_auc": "DAG progress AUC",
    "clean_handoff_rate": "Clean handoff rate",
    "coordination_violation_rate": "Coordination violations / round",
    "coordination_violation_rate_per_agent_action": (
        "Coordination violations / agent action"
    ),
    "rounds_to_success": "Rounds to success",
}


class AnalysisError(RuntimeError):
    """Raised when a frozen input or publication gate is invalid."""


def _strict_int(value: Any, field: str, *, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise AnalysisError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise AnalysisError(f"{field} must be at least {minimum}")
    return value


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnalysisError(f"{field} must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise AnalysisError(f"{field} must be a finite number")
    return numeric


def _json_equal(left: Any, right: Any) -> bool:
    """Compare JSON-shaped values without Python's bool/int equality aliasing."""

    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _json_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    if isinstance(left, float):
        return math.isfinite(left) and math.isfinite(right) and left == right
    return left == right


def _resolved_model_matches(requested: str, resolved: str) -> bool:
    """Allow the frozen request name or a provider-supplied version suffix."""

    return resolved == requested or resolved.startswith(f"{requested}-")


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """Summary returned by :func:`analyze_phase2`."""

    output_dir: Path
    publication_status: str
    gate_passed: bool
    observed_episodes: int
    artifacts: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class ContrastSpec:
    name: str
    label: str
    family: str
    left: tuple[tuple[str, float], ...]
    right: tuple[tuple[str, float], ...]
    analysis_status: str = "prespecified"


CONTRASTS = (
    ContrastSpec(
        "gpt_communication_gain",
        "GPT communication gain",
        "communication",
        (("gpt_selfplay", 1.0),),
        (("gpt_no_comm", 1.0),),
    ),
    ContrastSpec(
        "gemini_communication_gain",
        "Gemini communication gain",
        "communication",
        (("gemini_selfplay", 1.0),),
        (("gemini_no_comm", 1.0),),
    ),
    ContrastSpec(
        "crossplay_directional_gap_A",
        "Cross-play directional gap A",
        "crossplay_direction",
        (("gpt_selfplay", 1.0),),
        (("crossplay_gptF_geminiW", 1.0),),
    ),
    ContrastSpec(
        "crossplay_directional_gap_B",
        "Cross-play directional gap B",
        "crossplay_direction",
        (("gemini_selfplay", 1.0),),
        (("crossplay_geminiF_gptW", 1.0),),
    ),
    ContrastSpec(
        "partner_sensitivity_gpt_F",
        "GPT partner sensitivity (F role)",
        "partner_sensitivity",
        (("gpt_selfplay", 1.0),),
        (("crossplay_gptF_geminiW", 1.0),),
    ),
    ContrastSpec(
        "partner_sensitivity_gpt_W",
        "GPT partner sensitivity (W role)",
        "partner_sensitivity",
        (("gpt_selfplay", 1.0),),
        (("crossplay_geminiF_gptW", 1.0),),
    ),
    ContrastSpec(
        "partner_sensitivity_gemini_F",
        "Gemini partner sensitivity (F role)",
        "partner_sensitivity",
        (("gemini_selfplay", 1.0),),
        (("crossplay_geminiF_gptW", 1.0),),
    ),
    ContrastSpec(
        "partner_sensitivity_gemini_W",
        "Gemini partner sensitivity (W role)",
        "partner_sensitivity",
        (("gemini_selfplay", 1.0),),
        (("crossplay_gptF_geminiW", 1.0),),
    ),
    ContrastSpec(
        "crossplay_gap",
        "Pooled cross-play gap",
        "crossplay_pooled",
        (("gpt_selfplay", 0.5), ("gemini_selfplay", 0.5)),
        (
            ("crossplay_gptF_geminiW", 0.5),
            ("crossplay_geminiF_gptW", 0.5),
        ),
    ),
    ContrastSpec(
        "crossplay_role_assignment_gap",
        "Cross-play A vs B (exploratory post hoc)",
        "crossplay_role_assignment",
        (("crossplay_gptF_geminiW", 1.0),),
        (("crossplay_geminiF_gptW", 1.0),),
        "exploratory_post_hoc",
    ),
)

HEADLINE_CONTRASTS = frozenset(
    {
        "gpt_communication_gain",
        "gemini_communication_gain",
        "crossplay_directional_gap_A",
        "crossplay_directional_gap_B",
        "crossplay_gap",
        "crossplay_role_assignment_gap",
    }
)
HEADLINE_METRICS = frozenset(
    {
        "success_rate",
        "dag_progress_auc",
        "clean_handoff_rate",
        "coordination_violation_rate",
    }
)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except OSError as exc:
        raise AnalysisError(f"cannot read JSON input {path}: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"invalid JSON input {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AnalysisError(f"JSON input must be an object: {path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    except (OSError, TypeError, ValueError) as exc:
        raise AnalysisError(f"cannot write JSON {path}: {exc}") from exc


def _write_text(path: Path, value: str, *, description: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    except OSError as exc:
        raise AnalysisError(f"cannot write {description} {path}: {exc}") from exc


def _resolve(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _inside_root(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _paths_overlap(first: Path, second: Path) -> bool:
    left = first.resolve()
    right = second.resolve()
    return _inside_root(left, right) or _inside_root(right, left)


def _validate_output_isolation(output: Path, inputs: Sequence[Path]) -> None:
    overlaps = [path for path in inputs if _paths_overlap(output, path)]
    if overlaps:
        raise AnalysisError(
            "output path overlaps immutable analysis input: "
            + ", ".join(str(path) for path in overlaps[:8])
        )


def _publish_staged_directory(staging: Path, destination: Path) -> None:
    """Swap one complete generated directory into place, restoring on failure."""

    backup = destination.parent / (
        f".{destination.name}.previous-{os.getpid()}-{random.getrandbits(32):08x}"
    )
    moved_previous = False
    try:
        if destination.exists():
            if not destination.is_dir():
                raise AnalysisError(f"output destination is not a directory: {destination}")
            os.replace(destination, backup)
            moved_previous = True
        os.replace(staging, destination)
    except (OSError, AnalysisError) as exc:
        if moved_previous and backup.exists() and not destination.exists():
            try:
                os.replace(backup, destination)
            except OSError as restore_exc:
                raise AnalysisError(
                    f"output publication failed and rollback failed: {restore_exc}"
                ) from exc
        if isinstance(exc, AnalysisError):
            raise
        raise AnalysisError(f"cannot atomically publish analysis output: {exc}") from exc
    if moved_previous:
        try:
            shutil.rmtree(backup)
        except OSError as exc:
            raise AnalysisError(
                "new analysis output was published, but the replaced-output backup "
                f"could not be removed: {exc}"
            ) from exc


def verify_freeze(root: str | Path, freeze_path: str | Path) -> dict[str, Any]:
    """Verify every frozen file, byte count, and release invariant."""

    root_path = Path(root).resolve()
    manifest_path = _resolve(root_path, freeze_path)
    if not _inside_root(root_path, manifest_path):
        raise AnalysisError("Phase II freeze manifest must be inside the benchmark root")
    freeze = _load_json(manifest_path)
    files = freeze.get("files")
    if freeze.get("format") != "fwcollab.symbolic.benchmark_freeze.v1":
        raise AnalysisError("unsupported Phase II freeze-manifest format")
    if freeze.get("benchmark_version") != BENCHMARK_VERSION:
        raise AnalysisError(f"freeze benchmark_version must be {BENCHMARK_VERSION}")
    if freeze.get("statement") != SELECTION_STATEMENT:
        raise AnalysisError("freeze selection statement does not match the registered text")
    for field, expected in {
        "task_count": EXPECTED_TASKS,
        "condition_count": EXPECTED_CONDITIONS,
        "replicates": len(EXPECTED_REPLICATES),
        "expected_episodes": EXPECTED_EPISODES,
    }.items():
        actual = freeze.get(field)
        if type(actual) is not int or actual != expected:
            raise AnalysisError(f"freeze metadata {field} must equal {expected}")
    if not isinstance(files, list) or not files:
        raise AnalysisError("freeze manifest must contain a non-empty files array")

    mismatches: list[dict[str, Any]] = []
    verified: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            mismatches.append({"index": index, "reason": "entry_not_object"})
            continue
        relative = item.get("path")
        if not isinstance(relative, str) or not relative:
            mismatches.append({"index": index, "reason": "invalid_path"})
            continue
        candidate = (root_path / relative).resolve()
        if not _inside_root(root_path, candidate):
            mismatches.append({"path": relative, "reason": "path_escapes_root"})
            continue
        canonical = candidate.relative_to(root_path).as_posix()
        if canonical in seen:
            mismatches.append({"path": relative, "reason": "duplicate_canonical_path"})
            continue
        seen.add(canonical)
        expected_hash = item.get("sha256")
        expected_bytes = item.get("bytes")
        if (
            not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or any(character not in "0123456789abcdef" for character in expected_hash)
        ):
            mismatches.append({"path": canonical, "reason": "invalid_sha256"})
            continue
        if type(expected_bytes) is not int or expected_bytes < 0:
            mismatches.append({"path": canonical, "reason": "invalid_or_missing_bytes"})
            continue
        try:
            content = candidate.read_bytes()
        except OSError as exc:
            mismatches.append(
                {"path": canonical, "reason": "unreadable", "detail": str(exc)}
            )
            continue
        actual_hash = hashlib.sha256(content).hexdigest()
        actual_bytes = len(content)
        reasons: list[str] = []
        if actual_hash != expected_hash:
            reasons.append("sha256")
        if actual_bytes != expected_bytes:
            reasons.append("bytes")
        if reasons:
            mismatches.append(
                {
                    "path": canonical,
                    "reason": "+".join(reasons),
                    "expected_sha256": expected_hash,
                    "actual_sha256": actual_hash,
                    "expected_bytes": expected_bytes,
                    "actual_bytes": actual_bytes,
                }
            )
        else:
            verified.append(
                {"path": canonical, "sha256": actual_hash, "bytes": actual_bytes}
            )
    if mismatches:
        preview = ", ".join(
            str(item.get("path", item.get("index"))) for item in mismatches[:8]
        )
        raise AnalysisError(
            f"benchmark v1 freeze mismatch ({len(mismatches)} file(s)): {preview}"
        )
    try:
        manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise AnalysisError(f"cannot hash freeze manifest {manifest_path}: {exc}") from exc
    return {
        "valid": True,
        "manifest": _display_path(manifest_path, root_path),
        "benchmark_version": freeze["benchmark_version"],
        "verified_files": len(verified),
        "manifest_sha256": manifest_hash,
        "files": verified,
        "metadata": {
            key: freeze[key]
            for key in ("task_count", "condition_count", "replicates", "expected_episodes")
        },
        "raw": freeze,
    }


def _validate_contract(
    spec: Mapping[str, Any], manifest: Mapping[str, Any], freeze: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    errors: list[str] = []
    records_value = manifest.get("records")
    conditions_value = spec.get("conditions")
    records = list(records_value) if isinstance(records_value, list) else []
    conditions = list(conditions_value) if isinstance(conditions_value, list) else []

    if spec.get("format") != "fwcollab.symbolic.phase2_experiment.v1":
        errors.append("unsupported experiment format")
    if spec.get("benchmark_version") != BENCHMARK_VERSION:
        errors.append(f"benchmark_version must be {BENCHMARK_VERSION}")
    # The original frozen v1 experiment spec predates the duplicated statement
    # field; its signed freeze manifest is the authoritative source.  Reject a
    # conflicting statement when present, but accept the legacy omission.
    if "statement" in spec and spec.get("statement") != SELECTION_STATEMENT:
        errors.append("experiment selection statement does not match the registered text")
    if spec.get("selection_uses_model_outcomes") is not False:
        errors.append("selection_uses_model_outcomes must be false")
    if not isinstance(spec.get("task_manifest"), str) or not spec["task_manifest"]:
        errors.append("task_manifest must be a non-empty path")
    if manifest.get("format") != "fwcollab.symbolic.collaboration_diagnostic_set.v1":
        errors.append("unsupported task-manifest format")
    if len(records) != EXPECTED_TASKS:
        errors.append(f"task manifest must contain exactly {EXPECTED_TASKS} records")
    if len(conditions) != EXPECTED_CONDITIONS:
        errors.append(f"spec must contain exactly {EXPECTED_CONDITIONS} conditions")

    task_ids = [item.get("id") for item in records if isinstance(item, dict)]
    if len(task_ids) != len(records) or any(not isinstance(value, str) or not value for value in task_ids):
        errors.append("every task record must have a non-empty string id")
    elif len(set(task_ids)) != len(task_ids):
        errors.append("task ids must be unique")
    for record in records:
        if not isinstance(record, dict):
            errors.append("every task record must be an object")
            continue
        if not isinstance(record.get("difficulty"), str):
            errors.append(f"task {record.get('id')} has invalid difficulty")
        for field in ("map", "dag"):
            if not isinstance(record.get(field), str) or not record[field]:
                errors.append(f"task {record.get('id')} has invalid {field} path")

    condition_ids = [item.get("id") for item in conditions if isinstance(item, dict)]
    if tuple(condition_ids) != CONDITION_IDS:
        errors.append(f"condition ids/order must be exactly {list(CONDITION_IDS)}")
    for condition in conditions:
        if not isinstance(condition, dict):
            errors.append("every condition must be an object")
            continue
        condition_id = condition.get("id")
        expected = (
            EXPECTED_CONDITION_CONTRACT.get(condition_id)
            if isinstance(condition_id, str)
            else None
        )
        if expected is None:
            continue
        if condition.get("label") != EXPECTED_CONDITION_LABELS[condition_id]:
            errors.append(f"condition {condition_id} has an unexpected label")
        for role in ("F", "W"):
            role_value = condition.get(role)
            if not isinstance(role_value, dict):
                errors.append(f"condition {condition_id} has invalid role {role}")
                continue
            if role_value.get("model") != expected[role]["model"]:
                errors.append(f"condition {condition_id} role {role} has wrong model")
            if role_value.get("api_style") != expected[role]["api_style"]:
                errors.append(f"condition {condition_id} role {role} has wrong api_style")
        if condition.get("communication_enabled") is not expected[
            "communication_enabled"
        ]:
            errors.append(f"condition {condition_id} has wrong communication flag")

    replicates = spec.get("replicates")
    actual_replicates = tuple(replicates) if isinstance(replicates, list) else ()
    if (
        actual_replicates != EXPECTED_REPLICATES
        or any(type(value) is not int for value in actual_replicates)
    ):
        errors.append(f"replicates must be exactly {list(EXPECTED_REPLICATES)}")
    request_seeds = spec.get("request_seeds")
    if (
        not isinstance(request_seeds, dict)
        or set(request_seeds) != set(EXPECTED_REQUEST_SEEDS)
        or any(type(value) is not int for value in request_seeds.values())
        or request_seeds != EXPECTED_REQUEST_SEEDS
        or len(set(request_seeds.values())) != len(EXPECTED_REQUEST_SEEDS)
    ):
        errors.append(f"request_seeds must be exactly {EXPECTED_REQUEST_SEEDS}")

    sampling = spec.get("sampling")
    if not isinstance(sampling, dict):
        errors.append("sampling must be an object")
    else:
        if set(sampling) != set(EXPECTED_SAMPLING):
            errors.append("sampling fields do not match the frozen contract")
        for field, expected in EXPECTED_SAMPLING.items():
            actual = sampling.get(field)
            if field == "temperature":
                if (
                    isinstance(actual, bool)
                    or not isinstance(actual, (int, float))
                    or not math.isfinite(float(actual))
                    or float(actual) != expected
                ):
                    errors.append(f"sampling.{field} must equal {expected}")
            elif isinstance(expected, int) and not isinstance(expected, bool):
                if type(actual) is not int or actual != expected:
                    errors.append(f"sampling.{field} must equal {expected}")
            elif actual is not expected:
                errors.append(f"sampling.{field} must equal {expected}")

    runner = spec.get("runner")
    if not isinstance(runner, dict):
        errors.append("runner must be an object")
    else:
        if set(runner) != set(EXPECTED_RUNNER):
            errors.append("runner fields do not match the frozen contract")
        for field, expected in EXPECTED_RUNNER.items():
            actual = runner.get(field)
            if isinstance(expected, int) and not isinstance(expected, bool):
                if type(actual) is not int or actual != expected:
                    errors.append(f"runner.{field} must equal {expected}")
            elif isinstance(expected, float):
                if type(actual) is not float or not math.isfinite(actual) or actual != expected:
                    errors.append(f"runner.{field} must equal {expected}")
            elif actual != expected:
                errors.append(f"runner.{field} must equal {expected}")

    statistics = spec.get("statistics")
    if not isinstance(statistics, dict):
        errors.append("statistics must be an object")
    else:
        if statistics.get("task_is_bootstrap_unit") is not True:
            errors.append("task_is_bootstrap_unit must be true")
        if statistics.get("average_replicates_within_task_first") is not True:
            errors.append("average_replicates_within_task_first must be true")
        if statistics.get("paired_bootstrap_samples") != BOOTSTRAP_SAMPLES:
            errors.append(f"paired_bootstrap_samples must be exactly {BOOTSTRAP_SAMPLES}")
        confidence = statistics.get("confidence_level")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(float(confidence))
            or float(confidence) != CONFIDENCE_LEVEL
        ):
            errors.append(f"confidence_level must be exactly {CONFIDENCE_LEVEL}")

    primary = spec.get("primary_metrics")
    actual_primary = tuple(primary) if isinstance(primary, list) else ()
    if actual_primary != PRIMARY_METRICS:
        errors.append(f"primary_metrics must be exactly {list(PRIMARY_METRICS)}")

    raw_freeze = freeze.get("raw", {})
    expected_freeze = {
        "task_count": EXPECTED_TASKS,
        "condition_count": EXPECTED_CONDITIONS,
        "replicates": len(EXPECTED_REPLICATES),
        "expected_episodes": EXPECTED_EPISODES,
    }
    for field, expected in expected_freeze.items():
        if raw_freeze.get(field) != expected:
            errors.append(f"freeze metadata {field} must equal {expected}")

    if errors:
        raise AnalysisError("invalid frozen Phase II contract: " + "; ".join(errors))
    return records, conditions


def _require_frozen_analysis_inputs(
    *,
    root: Path,
    spec_path: Path,
    task_manifest_path: Path,
    records: Sequence[Mapping[str, Any]],
    freeze: Mapping[str, Any],
) -> None:
    frozen_paths = {
        str(item["path"])
        for item in freeze.get("files", [])
        if isinstance(item, Mapping) and isinstance(item.get("path"), str)
    }
    required_candidates = [
        spec_path,
        task_manifest_path,
        *(_resolve(root, str(record["map"])) for record in records),
        *(_resolve(root, str(record["dag"])) for record in records),
    ]
    if any(not _inside_root(root, path) for path in required_candidates):
        raise AnalysisError("analysis input path escapes the benchmark root")
    required_paths = {
        path.resolve().relative_to(root.resolve()).as_posix()
        for path in required_candidates
    }
    missing = sorted(required_paths - frozen_paths)
    if missing:
        raise AnalysisError(
            "analysis input is not covered by the verified freeze: "
            + ", ".join(missing[:8])
        )


def _trace_relative(condition: str, replicate: int, record: Mapping[str, Any]) -> Path:
    return (
        Path("traces")
        / condition
        / f"replicate_{replicate}"
        / str(record["difficulty"])
        / f"{record['id']}.json"
    )


def _expected_matrix(
    records: Sequence[Mapping[str, Any]], conditions: Sequence[Mapping[str, Any]]
) -> dict[tuple[str, int, str], tuple[Mapping[str, Any], Mapping[str, Any], Path]]:
    return {
        (str(condition["id"]), replicate, str(record["id"])): (
            condition,
            record,
            _trace_relative(str(condition["id"]), replicate, record),
        )
        for condition in conditions
        for replicate in EXPECTED_REPLICATES
        for record in records
    }


def _resolve_evaluator_records(
    root: Path,
    task_manifest: Mapping[str, Any],
    selected_records: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Any]]:
    """Hydrate compact Diagnostic-24 rows from their pre-existing source manifest.

    The frozen diagnostic manifest intentionally stores selection fields only;
    the v1 evaluator additionally needs generator evidence such as stages and
    node_bindings.  Synthetic/test manifests may already contain those fields.
    """

    source_value = task_manifest.get("source_manifest")
    if source_value is None:
        return (
            {str(record["id"]): record for record in selected_records},
            {"mode": "selected_records", "source_manifest": None},
        )
    if not isinstance(source_value, str) or not source_value:
        raise AnalysisError("task manifest source_manifest must be a non-empty path")
    source_path = _resolve(root, source_value)
    source = _load_json(source_path)
    source_records = source.get("records")
    if not isinstance(source_records, list):
        raise AnalysisError("evaluator source manifest records must be an array")
    by_id = {
        str(record.get("id")): record
        for record in source_records
        if isinstance(record, dict) and isinstance(record.get("id"), str)
    }
    hydrated: dict[str, Mapping[str, Any]] = {}
    errors: list[str] = []
    for selected in selected_records:
        task_id = str(selected["id"])
        full = by_id.get(task_id)
        if full is None:
            errors.append(f"missing source record {task_id}")
            continue
        for field in ("difficulty", "map", "dag", "primary_capability"):
            if field in selected and not _json_equal(selected[field], full.get(field)):
                errors.append(f"source record {task_id} disagrees on {field}")
        for field in ("stages", "capability", "node_bindings"):
            if not full.get(field):
                errors.append(f"source record {task_id} lacks {field}")
        hydrated[task_id] = full
    if errors:
        raise AnalysisError("invalid evaluator source manifest: " + "; ".join(errors))
    content = source_path.read_bytes()
    return hydrated, {
        "mode": "source_manifest_hydration",
        "source_manifest": _display_path(source_path, root),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
        "note": "Source metadata predates the Phase II freeze but was omitted from its file list; selected map/DAG identities are cross-checked.",
    }


def _authenticate_trace_observations(
    symbol_map: Any, trace: Mapping[str, Any]
) -> dict[str, Any]:
    """Rebuild evaluator inputs from actions and reject stored-observation drift."""

    rounds = trace.get("rounds")
    if not isinstance(rounds, list):
        raise AnalysisError("trace rounds must be an array")
    authenticated = copy.deepcopy(dict(trace))
    authenticated_rounds = authenticated.get("rounds")
    if not isinstance(authenticated_rounds, list):  # defensive after deepcopy
        raise AnalysisError("trace rounds must be an array")
    world = SymbolWorld(symbol_map)
    for index, (stored, canonical) in enumerate(
        zip(rounds, authenticated_rounds, strict=True), start=1
    ):
        if not isinstance(stored, dict) or not isinstance(canonical, dict):
            raise AnalysisError(f"round {index} must be an object")
        if world.status != "running":
            raise AnalysisError(f"trace contains post-terminal round {index}")
        pre_observation = world.observation()
        if not _json_equal(stored.get("observation"), pre_observation):
            raise AnalysisError(f"stored pre-observation mismatch at round {index}")
        agents = stored.get("agents")
        if not isinstance(agents, dict):
            raise AnalysisError(f"round {index} agents must be an object")
        actions = {}
        for role in ("F", "W"):
            agent_record = agents.get(role)
            if not isinstance(agent_record, dict) or not isinstance(
                agent_record.get("decision"), dict
            ):
                raise AnalysisError(f"missing {role} decision at round {index}")
            actions[role] = AgentDecision.model_validate(
                agent_record["decision"]
            ).action
        transition = world.step_joint(actions)
        result = stored.get("result")
        if not isinstance(result, dict):
            raise AnalysisError(f"round {index} result must be an object")
        if not _json_equal(result.get("observation"), transition.observation):
            raise AnalysisError(f"stored post-observation mismatch at round {index}")
        if result.get("status") != transition.status:
            raise AnalysisError(f"stored result status mismatch at round {index}")
        if not _json_equal(result.get("feedback"), dict(transition.feedback)):
            raise AnalysisError(f"stored feedback mismatch at round {index}")
        canonical["observation"] = copy.deepcopy(pre_observation)
        canonical_result = canonical.get("result")
        if not isinstance(canonical_result, dict):
            raise AnalysisError(f"round {index} result must be an object")
        canonical_result["observation"] = copy.deepcopy(transition.observation)
        canonical_result["status"] = transition.status
        canonical_result["feedback"] = dict(transition.feedback)
    final_observation = world.observation()
    if not _json_equal(trace.get("final_observation"), final_observation):
        raise AnalysisError("stored final_observation mismatch")
    authenticated["final_observation"] = copy.deepcopy(final_observation)
    return authenticated


def _trace_round_and_counter_errors(
    trace: Mapping[str, Any], *, condition: Mapping[str, Any], spec: Mapping[str, Any]
) -> list[str]:
    errors: list[str] = []
    rounds = trace.get("rounds")
    metrics = trace.get("metrics")
    if not isinstance(rounds, list):
        return ["rounds must be an array"]
    max_rounds = spec["runner"]["max_rounds"]
    if len(rounds) > max_rounds:
        errors.append(f"trace has {len(rounds)} rounds, exceeding cap {max_rounds}")
    outcome = trace.get("outcome")
    if outcome == "timeout" and len(rounds) != max_rounds:
        errors.append(f"timeout trace must contain exactly {max_rounds} rounds")
    if outcome in {"team_success", "team_failure"} and not rounds:
        errors.append("terminal trace must contain at least one round")

    expected_models = {role: condition[role]["model"] for role in ("F", "W")}
    if trace.get("models") != expected_models:
        errors.append("top-level requested models do not match the frozen condition")
    planning = trace.get("planning")
    if not isinstance(planning, dict):
        errors.append("planning must be an object")
    elif planning != {"mode": "none", "rounds": [], "result": None}:
        errors.append("zero-planning contract requires an empty planning record")

    communication_enabled = condition["communication_enabled"]
    for index, round_record in enumerate(rounds, start=1):
        if not isinstance(round_record, dict):
            errors.append(f"round {index} must be an object")
            continue
        if type(round_record.get("round")) is not int or round_record.get("round") != index:
            errors.append(f"round {index} has invalid round index")
        agents = round_record.get("agents")
        if not isinstance(agents, dict) or set(agents) != {"F", "W"}:
            errors.append(f"round {index} agents must contain exactly F and W")
            continue
        agent_observation = round_record.get("agent_observation")
        if not isinstance(agent_observation, dict):
            errors.append(f"round {index} agent_observation must be an object")
        elif not communication_enabled and agent_observation.get(
            "shared_message_history"
        ) != []:
            errors.append(f"No-Comm round {index} exposes shared messages")
        for role in ("F", "W"):
            agent = agents[role]
            if not isinstance(agent, dict):
                errors.append(f"round {index} role {role} record must be an object")
                continue
            decision = agent.get("decision")
            if not isinstance(decision, dict):
                errors.append(f"round {index} role {role} decision must be an object")
            provider_calls = agent.get("provider_calls")
            if type(provider_calls) is not int or provider_calls < 0:
                errors.append(
                    f"round {index} role {role} provider_calls must be a nonnegative integer"
                )
            error = agent.get("error")
            if error is not None and not isinstance(error, str):
                errors.append(f"round {index} role {role} error must be null or text")
            resolved_model = agent.get("model")
            requested_model = condition[role]["model"]
            if not isinstance(resolved_model, str) or not resolved_model:
                errors.append(f"round {index} role {role} resolved model is missing")
            elif not _resolved_model_matches(requested_model, resolved_model):
                errors.append(
                    f"round {index} role {role} resolved model {resolved_model!r} "
                    f"does not match requested model {requested_model!r}"
                )
            inbox = agent.get("inbox")
            if not isinstance(inbox, list):
                errors.append(f"round {index} role {role} inbox must be an array")
            if not communication_enabled:
                if inbox != []:
                    errors.append(f"No-Comm round {index} role {role} inbox is not empty")
                if isinstance(decision, dict) and decision.get("message") is not None:
                    errors.append(
                        f"No-Comm round {index} role {role} emitted a message"
                    )

    if not isinstance(metrics, dict):
        errors.append("metrics must be an object")
        return errors
    stored_fields = (
        "rounds",
        "total_model_calls",
        "total_model_errors",
        "total_messages",
    )
    for field in stored_fields:
        value = metrics.get(field)
        if type(value) is not int or value < 0:
            errors.append(f"metrics.{field} must be a nonnegative integer")
    if errors:
        return errors
    try:
        recomputed = collaboration_metrics(rounds, outcome=str(outcome))
    except Exception as exc:
        return [f"cannot recompute trace counters: {type(exc).__name__}: {exc}"]
    for field in stored_fields:
        if metrics[field] != recomputed[field]:
            errors.append(
                f"metrics.{field}={metrics[field]!r}, recomputed {recomputed[field]!r}"
            )
    planning_metrics = metrics.get("planning")
    expected_planning_metrics = {
        "enabled": False,
        "rounds": 0,
        "model_calls": 0,
        "errors": 0,
        "typed_consensus": False,
    }
    if planning_metrics != expected_planning_metrics:
        errors.append("metrics.planning does not match the zero-planning contract")
    if not communication_enabled and metrics.get("total_messages") != 0:
        errors.append("No-Comm trace must have zero total_messages")
    return errors


def _trace_metadata_errors(
    trace: Mapping[str, Any],
    *,
    condition: Mapping[str, Any],
    record: Mapping[str, Any],
    replicate: int,
    spec: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    expected = {
        "format": "fwcollab.symbol_trace.v1",
        "map_id": record["id"],
    }
    for field, value in expected.items():
        if trace.get(field) != value:
            errors.append(f"{field}={trace.get(field)!r}, expected {value!r}")
    outcome = trace.get("outcome")
    if outcome not in {"team_success", "team_failure", "timeout"}:
        errors.append(f"unsupported outcome {outcome!r}")
    errors.extend(_trace_round_and_counter_errors(trace, condition=condition, spec=spec))

    phase2 = trace.get("phase2")
    if not isinstance(phase2, dict):
        errors.append("phase2 metadata must be an object")
        return errors
    phase_expected = {
        "format": spec["format"],
        "benchmark_version": spec["benchmark_version"],
        "condition": condition["id"],
        "condition_label": condition["label"],
        "communication_enabled": condition["communication_enabled"],
        "replicate": replicate,
        "request_seed": spec["request_seeds"][str(replicate)],
        "seed_reproducibility_guaranteed": spec["sampling"][
            "seed_reproducibility_guaranteed"
        ],
        "temperature": spec["sampling"]["temperature"],
        "difficulty": record["difficulty"],
        "dag": record["dag"],
    }
    for field, value in phase_expected.items():
        if phase2.get(field) != value:
            errors.append(f"phase2.{field}={phase2.get(field)!r}, expected {value!r}")
    expected_roles = {role: dict(condition[role]) for role in ("F", "W")}
    if phase2.get("roles") != expected_roles:
        errors.append("phase2.roles does not match the frozen condition")

    protocol = trace.get("protocol")
    runner = spec["runner"]
    if not isinstance(protocol, dict):
        errors.append("protocol must be an object")
    else:
        protocol_expected = {
            "movement_steps": runner["movement_steps"],
            "message_delay_rounds": runner["message_delay_rounds"],
            "max_rounds": runner["max_rounds"],
            "planning_rounds": runner["planning_rounds"],
            "coordination_mode": runner["coordination_mode"],
            "independent_histories": True,
            "frozen_same_round_observation": True,
            "simultaneous_submission": True,
            "agent_observation": "fwcollab.agent_observation.v1",
            "last_transition_visible": True,
            "round_budget_visible": True,
        }
        if set(protocol) != set(protocol_expected):
            errors.append("protocol fields do not match the frozen runner contract")
        for field, value in protocol_expected.items():
            if protocol.get(field) != value:
                errors.append(
                    f"protocol.{field}={protocol.get(field)!r}, expected {value!r}"
                )
    return errors


def _episode_metrics(
    evaluation: Mapping[str, Any], trace: Mapping[str, Any]
) -> tuple[dict[str, float | None], dict[str, int | float | None]]:
    summary = evaluation.get("summary")
    handoffs = evaluation.get("handoffs")
    if not isinstance(summary, dict) or not isinstance(handoffs, list):
        raise AnalysisError("evaluator output lacks summary or handoffs")
    rounds_value = trace.get("rounds")
    if not isinstance(rounds_value, list):
        raise AnalysisError("trace rounds must be an array")
    rounds = len(rounds_value)
    opportunities = _strict_int(
        summary.get("handoff_opportunities"),
        "evaluation.summary.handoff_opportunities",
        minimum=0,
    )
    clean_handoffs = 0
    for index, handoff in enumerate(handoffs, start=1):
        if not isinstance(handoff, dict) or type(handoff.get("clean")) is not bool:
            raise AnalysisError(f"evaluation handoff {index} has invalid clean flag")
        clean_handoffs += int(handoff["clean"])
    if opportunities != len(handoffs):
        raise AnalysisError("handoff opportunity count does not match evaluator records")
    violations = _strict_int(
        summary.get("coordination_violations"),
        "evaluation.summary.coordination_violations",
        minimum=0,
    )
    coordination_violations = evaluation.get("coordination_violations")
    if not isinstance(coordination_violations, list) or violations != len(
        coordination_violations
    ):
        raise AnalysisError(
            "coordination violation count does not match evaluator records"
        )
    dag_completion = _finite_number(
        summary.get("dag_completion"), "evaluation.summary.dag_completion"
    )
    dag_progress_auc = _finite_number(
        summary.get("dag_progress_auc"), "evaluation.summary.dag_progress_auc"
    )
    if not 0.0 <= dag_completion <= 1.0:
        raise AnalysisError("evaluation.summary.dag_completion is outside [0, 1]")
    if not 0.0 <= dag_progress_auc <= 1.0:
        raise AnalysisError("evaluation.summary.dag_progress_auc is outside [0, 1]")
    diagnostic_counts = {
        field: _strict_int(
            summary.get(field, 0), f"evaluation.summary.{field}", minimum=0
        )
        for field in ("useful_hold_rounds", "useful_waits", "total_waits")
    }
    useful_wait_ratio_raw = summary.get("useful_wait_ratio")
    useful_wait_ratio = (
        None
        if useful_wait_ratio_raw is None
        else _finite_number(
            useful_wait_ratio_raw, "evaluation.summary.useful_wait_ratio"
        )
    )
    if useful_wait_ratio is not None and not 0.0 <= useful_wait_ratio <= 1.0:
        raise AnalysisError("evaluation.summary.useful_wait_ratio is outside [0, 1]")
    per_round = violations / rounds if rounds else None
    per_action = violations / (2 * rounds) if rounds else None
    metrics = {
        "success_rate": float(trace["outcome"] == "team_success"),
        "dag_completion": dag_completion,
        "dag_progress_auc": dag_progress_auc,
        "clean_handoff_rate": (
            clean_handoffs / opportunities if opportunities else None
        ),
        "coordination_violation_rate": per_round,
        "coordination_violation_rate_per_agent_action": per_action,
        "rounds_to_success": (
            float(rounds) if trace["outcome"] == "team_success" else None
        ),
    }
    diagnostics: dict[str, int | float | None] = {
        "executed_rounds": rounds,
        "agent_action_opportunities": 2 * rounds,
        "handoff_opportunities": opportunities,
        "clean_handoffs": clean_handoffs,
        "coordination_violations": violations,
        **diagnostic_counts,
        "useful_wait_ratio": useful_wait_ratio,
    }
    return metrics, diagnostics


def _read_trace_object(path: Path) -> tuple[dict[str, Any], str]:
    try:
        content = path.read_bytes()
        value = json.loads(content)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalysisError(f"cannot read trace {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AnalysisError(f"trace must be a JSON object: {path}")
    return value, hashlib.sha256(content).hexdigest()


def _collect_episodes(
    *,
    root: Path,
    run_dir: Path,
    spec: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    conditions: Sequence[Mapping[str, Any]],
    evaluator_records: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    matrix = _expected_matrix(records, conditions)
    expected_paths = {relative.as_posix() for _, _, relative in matrix.values()}
    traces_root = run_dir / "traces"
    try:
        actual_paths = (
            {
                path.relative_to(run_dir).as_posix()
                for path in traces_root.rglob("*.json")
                if path.is_file()
            }
            if traces_root.is_dir()
            else set()
        )
    except OSError as exc:
        raise AnalysisError(f"cannot enumerate trace directory {traces_root}: {exc}") from exc
    missing = sorted(expected_paths - actual_paths)
    unexpected = sorted(actual_paths - expected_paths)
    invalid: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    trace_hashes: list[tuple[str, str]] = []
    dag_cache: dict[str, dict[str, Any]] = {}
    map_cache: dict[str, Any] = {}

    for key, (condition, record, relative) in matrix.items():
        relative_text = relative.as_posix()
        path = run_dir / relative
        if relative_text not in actual_paths:
            continue
        try:
            trace, trace_hash = _read_trace_object(path)
        except AnalysisError as exc:
            invalid.append({"trace": relative_text, "errors": [str(exc)]})
            continue
        metadata_errors = _trace_metadata_errors(
            trace,
            condition=condition,
            record=record,
            replicate=key[1],
            spec=spec,
        )
        if metadata_errors:
            invalid.append({"trace": relative_text, "errors": metadata_errors})
            continue
        try:
            map_name = str(record["map"])
            if map_name not in map_cache:
                map_cache[map_name] = load_symbol_map(_resolve(root, map_name))
            replay = replay_trace(map_cache[map_name], trace)
            if replay.get("ok") is not True:
                raise AnalysisError(f"replay returned {replay!r}")
            authenticated_trace = _authenticate_trace_observations(
                map_cache[map_name], trace
            )
            dag_name = str(record["dag"])
            if dag_name not in dag_cache:
                dag_cache[dag_name] = _load_json(_resolve(root, dag_name))
            evaluation_record = (
                evaluator_records.get(str(record["id"]), record)
                if evaluator_records is not None
                else record
            )
            evaluation = evaluate_collaboration_trace(
                evaluation_record, dag_cache[dag_name], authenticated_trace
            )
            metrics, diagnostics = _episode_metrics(evaluation, authenticated_trace)
        except Exception as exc:  # deterministic evaluator/replay boundary
            invalid.append(
                {
                    "trace": relative_text,
                    "errors": [f"{type(exc).__name__}: {exc}"],
                }
            )
            continue

        failure = evaluation.get("failure_analysis")
        diagnosis = trace.get("diagnosis")
        trace_metrics = trace["metrics"]
        row = {
            "condition": key[0],
            "condition_label": condition["label"],
            "replicate": key[1],
            "task_id": key[2],
            "difficulty": record["difficulty"],
            "outcome": trace["outcome"],
            "metrics": metrics,
            **diagnostics,
            "model_calls": trace_metrics["total_model_calls"],
            "model_errors": trace_metrics["total_model_errors"],
            "messages": trace_metrics["total_messages"],
            "resolved_models_F": sorted(
                {
                    round_record["agents"]["F"]["model"]
                    for round_record in trace["rounds"]
                }
            ),
            "resolved_models_W": sorted(
                {
                    round_record["agents"]["W"]["model"]
                    for round_record in trace["rounds"]
                }
            ),
            "failure_stage": (
                failure.get("first_incomplete_required_node")
                if isinstance(failure, dict)
                else None
            ),
            "failure_responsible_agent": (
                failure.get("responsible_agent") if isinstance(failure, dict) else None
            ),
            "diagnosis": (
                diagnosis.get("primary") if isinstance(diagnosis, dict) else None
            ),
            "trace": _display_path(path, root),
            "trace_sha256": trace_hash,
            "replay_verified": True,
            "observations_authenticated": True,
        }
        rows.append(row)
        trace_hashes.append((relative_text, trace_hash))

    rows.sort(key=lambda row: (row["condition"], row["replicate"], row["task_id"]))
    digest = hashlib.sha256()
    for relative, trace_hash in sorted(trace_hashes):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(trace_hash.encode("ascii"))
        digest.update(b"\n")
    details = {
        "expected_paths": len(expected_paths),
        "actual_json_paths": len(actual_paths),
        "missing_traces": missing,
        "unexpected_traces": unexpected,
        "invalid_traces": invalid,
        "valid_traces": len(rows),
        "trace_set_sha256": digest.hexdigest(),
    }
    return rows, details


def _inspect_run_state(
    *,
    root: Path,
    run_dir: Path,
    spec_path: Path,
    matrix: Mapping[
        tuple[str, int, str],
        tuple[Mapping[str, Any], Mapping[str, Any], Path],
    ],
    trace_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    state_path = run_dir / "run_state.json"
    expected_keys = set(matrix)
    result: dict[str, Any] = {
        "path": _display_path(state_path, root),
        "exists": state_path.is_file(),
        "valid": False,
        "errors": [],
        "runner_errors": [],
    }
    if not state_path.is_file():
        result["errors"].append("run_state.json is missing")
        return result
    try:
        state = _load_json(state_path)
    except AnalysisError as exc:
        result["errors"].append(str(exc))
        return result
    result["format"] = state.get("format")
    result["expected_episodes"] = state.get("expected_episodes")
    result["recorded_episodes"] = state.get("recorded_episodes")
    result["outcomes"] = state.get("outcomes")
    rows = state.get("results")
    if state.get("format") != "fwcollab.symbolic.phase2_run_state.v1":
        result["errors"].append("unsupported run-state format")
    experiment_spec = state.get("experiment_spec")
    if not isinstance(experiment_spec, str) or _resolve(root, experiment_spec) != spec_path:
        result["errors"].append("run-state experiment_spec does not match the frozen spec")
    expected_episodes = state.get("expected_episodes")
    if type(expected_episodes) is not int or expected_episodes != EXPECTED_EPISODES:
        result["errors"].append(
            f"run-state expected_episodes must equal {EXPECTED_EPISODES}"
        )
    if not isinstance(rows, list):
        result["errors"].append("run-state results must be an array")
        return result
    recorded_episodes = state.get("recorded_episodes")
    if type(recorded_episodes) is not int or recorded_episodes != len(rows):
        result["errors"].append("run-state recorded_episodes must equal result-row count")
    if len(rows) != EXPECTED_EPISODES:
        result["errors"].append(
            f"run-state results contains {len(rows)} rows, expected {EXPECTED_EPISODES}"
        )

    state_keys: list[tuple[str, int, str]] = []
    state_outcomes: Counter[str] = Counter()
    state_by_key: dict[tuple[str, int, str], Mapping[str, Any]] = {}
    totals = {"model_calls": 0, "model_errors": 0}
    allowed_outcomes = {"team_success", "team_failure", "timeout", "runner_error"}
    for index, row in enumerate(rows):
        row_number = index + 1
        if not isinstance(row, dict):
            result["errors"].append(f"run-state result {row_number} is not an object")
            continue
        condition_id = row.get("condition")
        replicate = row.get("replicate")
        task_id = row.get("task_id")
        if (
            not isinstance(condition_id, str)
            or type(replicate) is not int
            or not isinstance(task_id, str)
        ):
            result["errors"].append(f"run-state result {row_number} has an invalid key")
            continue
        key = (condition_id, replicate, task_id)
        state_keys.append(key)
        if key not in state_by_key:
            state_by_key[key] = row
        outcome = row.get("outcome")
        if not isinstance(outcome, str) or outcome not in allowed_outcomes:
            result["errors"].append(
                f"run-state result {row_number} has invalid outcome"
            )
            continue
        state_outcomes[outcome] += 1
        for field in ("rounds", "model_calls", "model_errors", "messages", "wall_time_ms"):
            value = row.get(field)
            if type(value) is not int or value < 0:
                result["errors"].append(
                    f"run-state result {row_number} {field} must be a nonnegative integer"
                )
            elif field in totals:
                totals[field] += value
        if type(row.get("resumed")) is not bool:
            result["errors"].append(
                f"run-state result {row_number} resumed must be boolean"
            )
        expected_entry = matrix.get(key)
        if expected_entry is not None:
            _, record, relative = expected_entry
            if row.get("difficulty") != record["difficulty"]:
                result["errors"].append(
                    f"run-state result {row_number} has wrong difficulty"
                )
            trace_path = row.get("trace")
            expected_trace_path = (run_dir / relative).resolve()
            if not isinstance(trace_path, str) or _resolve(root, trace_path) != expected_trace_path:
                result["errors"].append(
                    f"run-state result {row_number} has wrong trace path"
                )
        if outcome == "runner_error":
            error = row.get("error")
            if not isinstance(error, str) or not error:
                result["errors"].append(
                    f"run-state result {row_number} runner_error lacks error text"
                )
            result["runner_errors"].append(
                {
                    "condition": key[0],
                    "replicate": key[1],
                    "task_id": key[2],
                    "error": error,
                }
            )

    duplicate_keys = sorted(key for key, count in Counter(state_keys).items() if count > 1)
    if duplicate_keys:
        result["errors"].append(f"run-state has {len(duplicate_keys)} duplicate keys")
    missing = sorted(expected_keys - set(state_keys))
    unexpected = sorted(set(state_keys) - expected_keys)
    if missing:
        result["errors"].append(f"run-state is missing {len(missing)} matrix keys")
    if unexpected:
        result["errors"].append(f"run-state has {len(unexpected)} unexpected matrix keys")
    if not _json_equal(state.get("outcomes"), dict(sorted(state_outcomes.items()))):
        result["errors"].append("run-state outcomes does not equal its result-row counts")
    for state_field, total_field in (
        ("total_model_calls", "model_calls"),
        ("total_model_errors", "model_errors"),
    ):
        stored_total = state.get(state_field)
        if type(stored_total) is not int or stored_total != totals[total_field]:
            result["errors"].append(
                f"run-state {state_field} does not equal its result-row sum"
            )
    if result["runner_errors"]:
        result["errors"].append(
            f"run-state contains {len(result['runner_errors'])} runner_error result(s)"
        )

    trace_by_key = {
        (row["condition"], row["replicate"], row["task_id"]): row
        for row in trace_rows
    }
    disagreements: list[list[Any]] = []
    compared_fields = {
        "difficulty": "difficulty",
        "outcome": "outcome",
        "rounds": "executed_rounds",
        "model_calls": "model_calls",
        "model_errors": "model_errors",
        "messages": "messages",
    }
    for key in sorted(expected_keys & set(state_by_key) & set(trace_by_key)):
        state_row = state_by_key[key]
        trace_row = trace_by_key[key]
        changed = [
            state_field
            for state_field, trace_field in compared_fields.items()
            if not _json_equal(state_row.get(state_field), trace_row.get(trace_field))
        ]
        trace_path = state_row.get("trace")
        if not isinstance(trace_path, str) or _resolve(root, trace_path) != _resolve(
            root, trace_row["trace"]
        ):
            changed.append("trace")
        if changed:
            disagreements.append([*key, changed])
    if disagreements:
        result["errors"].append(
            f"run-state and traces disagree on {len(disagreements)} row(s)"
        )
    result["missing_keys"] = [list(key) for key in missing]
    result["unexpected_keys"] = [list(key) for key in unexpected]
    result["duplicate_keys"] = [list(key) for key in duplicate_keys]
    result["trace_row_disagreements"] = disagreements
    result["valid"] = not result["errors"]
    return result


def _present(values: Iterable[float | None]) -> list[float]:
    present: list[float] = []
    for index, value in enumerate(values):
        if value is None:
            continue
        present.append(_finite_number(value, f"statistical value {index}"))
    return present


def _aggregate(values: Sequence[float], metric: str) -> float:
    if metric == "rounds_to_success":
        return float(median(values))
    return float(fmean(values))


def _task_metrics(
    episode_rows: Sequence[Mapping[str, Any]],
    conditions: Sequence[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: defaultdict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in episode_rows:
        grouped[(str(row["condition"]), str(row["task_id"]))].append(row)
    rows: list[dict[str, Any]] = []
    for condition in conditions:
        for record in records:
            episodes = sorted(
                grouped.get((str(condition["id"]), str(record["id"])), []),
                key=lambda row: int(row["replicate"]),
            )
            metrics: dict[str, float | None] = {}
            metric_n: dict[str, int] = {}
            for metric in METRICS:
                values = _present(row["metrics"][metric] for row in episodes)
                metric_n[metric] = len(values)
                metrics[metric] = _aggregate(values, metric) if values else None
            rows.append(
                {
                    "condition": condition["id"],
                    "condition_label": condition["label"],
                    "task_id": record["id"],
                    "difficulty": record["difficulty"],
                    "replicates_present": [int(row["replicate"]) for row in episodes],
                    "replicate_count": len(episodes),
                    "complete_three_replicates": (
                        [int(row["replicate"]) for row in episodes]
                        == list(EXPECTED_REPLICATES)
                    ),
                    "successes": sum(row["outcome"] == "team_success" for row in episodes),
                    "metrics": metrics,
                    "metric_n_replicates": metric_n,
                }
            )
    return rows


def _stable_seed(*parts: str) -> int:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).digest()
    return BOOTSTRAP_SEED ^ int.from_bytes(digest[:8], "big")


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("quantile requires at least one value")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def bootstrap_task_values(
    values: Sequence[float | None],
    *,
    metric: str,
    seed: int,
    samples: int = BOOTSTRAP_SAMPLES,
) -> dict[str, float | int | str | None]:
    """Bootstrap task-level values after all within-task aggregation.

    ``rounds_to_success`` uses the median; every other metric uses the mean.
    """

    if samples != BOOTSTRAP_SAMPLES:
        raise AnalysisError(f"Phase II requires exactly {BOOTSTRAP_SAMPLES} bootstrap draws")
    present = sorted(_present(values))
    aggregation = (
        "median_of_task_medians"
        if metric == "rounds_to_success"
        else "mean_of_task_means"
    )
    if not present:
        return {
            "estimate": None,
            "ci_low": None,
            "ci_high": None,
            "bootstrap_samples": 0,
            "requested_bootstrap_samples": samples,
            "n_tasks": 0,
            "seed": seed,
            "aggregation": aggregation,
            "analysis_algorithm_version": ANALYSIS_ALGORITHM_VERSION,
        }
    point = _aggregate(present, metric)
    rng = random.Random(seed)
    size = len(present)
    draws = [
        _aggregate([present[rng.randrange(size)] for _ in range(size)], metric)
        for _ in range(samples)
    ]
    alpha = (1.0 - CONFIDENCE_LEVEL) / 2.0
    return {
        "estimate": point,
        "ci_low": _quantile(draws, alpha),
        "ci_high": _quantile(draws, 1.0 - alpha),
        "bootstrap_samples": len(draws),
        "requested_bootstrap_samples": samples,
        "n_tasks": size,
        "seed": seed,
        "aggregation": aggregation,
        "analysis_algorithm_version": ANALYSIS_ALGORITHM_VERSION,
    }


def _condition_summaries(
    task_rows: Sequence[Mapping[str, Any]],
    conditions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_condition: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in task_rows:
        by_condition[str(row["condition"])].append(row)
    summaries: list[dict[str, Any]] = []
    for condition in conditions:
        condition_id = str(condition["id"])
        for metric in METRICS:
            result = bootstrap_task_values(
                [row["metrics"][metric] for row in by_condition[condition_id]],
                metric=metric,
                seed=_stable_seed("condition", condition_id, metric),
            )
            summaries.append(
                {
                    "condition": condition_id,
                    "condition_label": condition["label"],
                    "metric": metric,
                    "metric_label": METRIC_LABELS[metric],
                    **result,
                }
            )
    return summaries


def _weighted_replicate_value(
    index: Mapping[tuple[str, str, int], Mapping[str, Any]],
    task_id: str,
    replicate: int,
    metric: str,
    terms: Sequence[tuple[str, float]],
) -> float | None:
    values: list[tuple[float, float]] = []
    for condition, weight in terms:
        row = index.get((condition, task_id, replicate))
        if row is None:
            return None
        value = row["metrics"][metric]
        if value is None:
            return None
        values.append((_finite_number(value, f"{condition}/{task_id}/{metric}"), weight))
    return sum(value * weight for value, weight in values)


def _paired_differences(
    episode_rows: Sequence[Mapping[str, Any]], records: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    index = {
        (str(row["condition"]), str(row["task_id"]), int(row["replicate"])): row
        for row in episode_rows
    }
    rows: list[dict[str, Any]] = []
    for contrast in CONTRASTS:
        for record in records:
            task_id = str(record["id"])
            for metric in METRICS:
                common_replicates: list[int] = []
                left_replicates: list[float] = []
                right_replicates: list[float] = []
                for replicate in EXPECTED_REPLICATES:
                    left_value = _weighted_replicate_value(
                        index, task_id, replicate, metric, contrast.left
                    )
                    right_value = _weighted_replicate_value(
                        index, task_id, replicate, metric, contrast.right
                    )
                    if left_value is None or right_value is None:
                        continue
                    common_replicates.append(replicate)
                    left_replicates.append(left_value)
                    right_replicates.append(right_value)
                left = (
                    _aggregate(left_replicates, metric) if left_replicates else None
                )
                right = (
                    _aggregate(right_replicates, metric) if right_replicates else None
                )
                reverse = metric in LOWER_IS_BETTER_METRICS
                difference = None
                if left is not None and right is not None:
                    difference = right - left if reverse else left - right
                rows.append(
                    {
                        "contrast": contrast.name,
                        "contrast_label": contrast.label,
                        "family": contrast.family,
                        "analysis_status": contrast.analysis_status,
                        "task_id": task_id,
                        "difficulty": record["difficulty"],
                        "metric": metric,
                        "metric_label": METRIC_LABELS[metric],
                        "common_replicates": common_replicates,
                        "common_replicate_count": len(common_replicates),
                        "complete_common_replicates": (
                            common_replicates == list(EXPECTED_REPLICATES)
                        ),
                        "left_value": left,
                        "right_value": right,
                        "difference": difference,
                        "direction": "right_minus_left" if reverse else "left_minus_right",
                    }
                )
    return rows


def _estimand_id(contrast: ContrastSpec, metric: str) -> str:
    payload = json.dumps(
        {
            "left": contrast.left,
            "right": contrast.right,
            "metric": metric,
            "direction": (
                "right_minus_left"
                if metric in LOWER_IS_BETTER_METRICS
                else "left_minus_right"
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _contrast_summaries(
    paired_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: defaultdict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in paired_rows:
        grouped[(str(row["contrast"]), str(row["metric"]))].append(row)
    rows: list[dict[str, Any]] = []
    for contrast in CONTRASTS:
        for metric in METRICS:
            subset = grouped[(contrast.name, metric)]
            estimand_id = _estimand_id(contrast, metric)
            result = bootstrap_task_values(
                [row["difference"] for row in subset],
                metric=metric,
                seed=_stable_seed("contrast", estimand_id),
            )
            direction = next(
                (str(row["direction"]) for row in subset), "left_minus_right"
            )
            rows.append(
                {
                    "contrast": contrast.name,
                    "contrast_label": contrast.label,
                    "family": contrast.family,
                    "analysis_status": contrast.analysis_status,
                    "metric": metric,
                    "metric_label": METRIC_LABELS[metric],
                    "estimand_id": estimand_id,
                    "direction": direction,
                    "left_expression": [
                        {"condition": condition, "weight": weight}
                        for condition, weight in contrast.left
                    ],
                    "right_expression": [
                        {"condition": condition, "weight": weight}
                        for condition, weight in contrast.right
                    ],
                    **result,
                }
            )
    return rows


def _headline_summaries(
    condition_rows: Sequence[Mapping[str, Any]],
    contrast_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in condition_rows:
        if row["metric"] in {"success_rate", "dag_progress_auc"}:
            rows.append({"kind": "condition", "name": row["condition"], **row})
    for row in contrast_rows:
        if row["contrast"] in HEADLINE_CONTRASTS and row["metric"] in HEADLINE_METRICS:
            rows.append({"kind": "contrast", "name": row["contrast"], **row})
    return rows


def _flatten_for_csv(row: Mapping[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in row.items():
        if key == "metrics" and isinstance(value, Mapping):
            flat.update(value)
        elif key == "metric_n_replicates" and isinstance(value, Mapping):
            flat.update({f"n_replicates_{name}": count for name, count in value.items()})
        elif isinstance(value, (dict, list, tuple)):
            flat[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        elif value is None:
            flat[key] = "NA"
        else:
            flat[key] = value
    return flat


def _write_csv(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    *,
    empty_fields: Sequence[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flattened = [_flatten_for_csv(row) for row in rows]
    fields: list[str] = []
    for row in flattened:
        for key in row:
            if key not in fields:
                fields.append(key)
    if not fields:
        if not empty_fields:
            raise AnalysisError(f"cannot write schema-less empty CSV: {path}")
        fields = list(empty_fields)
    try:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(flattened)
    except OSError as exc:
        raise AnalysisError(f"cannot write CSV {path}: {exc}") from exc


def _stamp_rows(
    rows: Sequence[Mapping[str, Any]], publication_status: str, watermark: str | None
) -> list[dict[str, Any]]:
    return [
        {
            "publication_status": publication_status,
            "watermark": watermark,
            **dict(row),
        }
        for row in rows
    ]


def _nested_summary(
    rows: Sequence[Mapping[str, Any]], key_field: str
) -> dict[str, dict[str, dict[str, Any]]]:
    nested: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        nested[str(row[key_field])][str(row["metric"])] = {
            key: row[key]
            for key in (
                "estimate",
                "ci_low",
                "ci_high",
                "bootstrap_samples",
                "requested_bootstrap_samples",
                "n_tasks",
                "seed",
                "aggregation",
                "analysis_algorithm_version",
            )
        }
    return dict(nested)


def _format_interval(row: Mapping[str, Any], digits: int = 3) -> str:
    if row.get("estimate") is None:
        return "NA"
    return (
        f"{float(row['estimate']):.{digits}f} "
        f"[{float(row['ci_low']):.{digits}f}, {float(row['ci_high']):.{digits}f}]"
    )


def _summary_index(
    rows: Sequence[Mapping[str, Any]], key: str
) -> dict[tuple[str, str], Mapping[str, Any]]:
    return {(str(row[key]), str(row["metric"])): row for row in rows}


def _markdown_report(
    *,
    integrity: Mapping[str, Any],
    conditions: Sequence[Mapping[str, Any]],
    condition_rows: Sequence[Mapping[str, Any]],
    contrast_rows: Sequence[Mapping[str, Any]],
) -> str:
    condition_index = _summary_index(condition_rows, "condition")
    contrast_index = _summary_index(contrast_rows, "contrast")
    watermark = integrity.get("watermark")
    lines: list[str] = []
    if watermark:
        lines.extend([f"> **{watermark}**", ""])
    lines.extend(
        [
            "# Phase II Causal Collaboration Results",
            "",
            "## Run integrity",
            "",
            f"- Publication status: **{integrity['publication_status']}**",
            f"- Freeze verified: **{integrity['freeze']['valid']}** "
            f"({integrity['freeze']['verified_files']} files)",
            f"- Valid episodes: **{integrity['observed_episodes']}/{EXPECTED_EPISODES}**",
            f"- Complete three-replicate tasks: **{integrity['complete_paired_tasks']}/{EXPECTED_TASKS}**",
            f"- Publication gate passed: **{integrity['gate_passed']}**",
        ]
    )
    if integrity["gate_errors"]:
        lines.extend(["", "Gate findings:"])
        lines.extend(f"- {error}" for error in integrity["gate_errors"])
    lines.extend(
        [
            "",
            "## Statistical method",
            "",
            "All estimates are task-first. Non-round metrics average replicates within each task, then average across tasks. "
            "Rounds-to-success takes the median across successful replicates within each task and the median across task medians. "
            "Confidence intervals use exactly 10,000 deterministic task-level bootstrap resamples; condition contrasts remain task-paired.",
            "",
            "Clean handoff rate is `NA` when an episode/task has no handoff opportunity; it is never imputed as zero. "
            "The primary coordination-violation rate uses executed rounds. The alternate rate uses two agent-action opportunities per executed round.",
            "",
            "## Condition summaries",
            "",
            "| Condition | Success | DAG completion | Progress AUC | Clean handoff | Viol./round | Viol./agent action | Rounds to success |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for condition in conditions:
        condition_id = str(condition["id"])
        values = [
            _format_interval(condition_index[(condition_id, metric)])
            for metric in METRICS
        ]
        lines.append(f"| {condition['label']} | " + " | ".join(values) + " |")
    lines.extend(
        [
            "",
            "## Headline contrasts",
            "",
            "Every contrast is oriented so positive values favor its left expression. "
            "Higher-is-better metrics use left minus right; violations and rounds-to-success use right minus left. "
            "Thus positive communication gain favors Normal and positive pooled cross-play gap favors self-play. "
            "The direct Cross-play A-versus-B role-assignment contrast was added after inspection of final aggregate outputs and is exploratory rather than confirmatory.",
            "",
            "| Contrast | Delta success | Delta AUC | Delta clean handoff | Delta viol./round |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for contrast in CONTRASTS:
        if contrast.name not in HEADLINE_CONTRASTS:
            continue
        values = [
            _format_interval(contrast_index[(contrast.name, metric)])
            for metric in (
                "success_rate",
                "dag_progress_auc",
                "clean_handoff_rate",
                "coordination_violation_rate",
            )
        ]
        lines.append(f"| {contrast.label} | " + " | ".join(values) + " |")
    lines.extend(
        [
            "",
            "## Partner sensitivity",
            "",
            "| Contrast | Delta success | Delta AUC | Delta rounds to success |",
            "|---|---:|---:|---:|",
        ]
    )
    for contrast in CONTRASTS:
        if contrast.family != "partner_sensitivity":
            continue
        values = [
            _format_interval(contrast_index[(contrast.name, metric)])
            for metric in ("success_rate", "dag_progress_auc", "rounds_to_success")
        ]
        lines.append(f"| {contrast.label} | " + " | ".join(values) + " |")
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            "Machine-readable episode, task, paired-difference, condition, headline, contrast, and integrity files are colocated with this report. "
            "LaTeX sources and the TikZ figure are in `latex/`; `phase2_report.pdf` is the compiled publication artifact when PDF compilation succeeds.",
        ]
    )
    return "\n".join(lines) + "\n"


def _tex_escape(value: Any) -> str:
    text = str(value)
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(character, character) for character in text)


def _tex_interval(row: Mapping[str, Any]) -> str:
    if row.get("estimate") is None:
        return r"\NA"
    return (
        f"{float(row['estimate']):.3f} "
        f"[{float(row['ci_low']):.3f}, {float(row['ci_high']):.3f}]"
    )


def _macro_number(value: Any) -> str:
    if value is None:
        return r"\NA"
    if isinstance(value, int):
        return str(value)
    return f"{float(value):.3f}"


def _latex_macros(
    integrity: Mapping[str, Any],
    condition_rows: Sequence[Mapping[str, Any]],
    contrast_rows: Sequence[Mapping[str, Any]],
) -> str:
    condition_index = _summary_index(condition_rows, "condition")
    contrast_index = _summary_index(contrast_rows, "contrast")
    macro_conditions = {
        "GPTSelfplay": "gpt_selfplay",
        "GeminiSelfplay": "gemini_selfplay",
        "GPTNoComm": "gpt_no_comm",
        "GeminiNoComm": "gemini_no_comm",
        "CrossplayA": "crossplay_gptF_geminiW",
        "CrossplayB": "crossplay_geminiF_gptW",
    }
    macro_contrasts = {
        "GPTCommunicationGain": "gpt_communication_gain",
        "GeminiCommunicationGain": "gemini_communication_gain",
        "CrossplayDirectionalGapA": "crossplay_directional_gap_A",
        "CrossplayDirectionalGapB": "crossplay_directional_gap_B",
        "CrossplayGap": "crossplay_gap",
        "CrossplayRoleAssignmentGap": "crossplay_role_assignment_gap",
    }
    lines = [
        "% Generated by fwcollab.analysis.phase2; do not edit by hand.",
        r"\providecommand{\NA}{\textemdash}",
        rf"\newcommand{{\PhaseTwoPublicationStatus}}{{{_tex_escape(integrity['publication_status'])}}}",
        rf"\newcommand{{\PhaseTwoExpectedEpisodes}}{{{EXPECTED_EPISODES}}}",
        rf"\newcommand{{\PhaseTwoObservedEpisodes}}{{{integrity['observed_episodes']}}}",
        rf"\newcommand{{\PhaseTwoBootstrapSamples}}{{{BOOTSTRAP_SAMPLES}}}",
    ]
    for macro, condition in macro_conditions.items():
        row = condition_index[(condition, "success_rate")]
        lines.extend(
            [
                rf"\newcommand{{\{macro}SR}}{{{_macro_number(row['estimate'])}}}",
                rf"\newcommand{{\{macro}SRLow}}{{{_macro_number(row['ci_low'])}}}",
                rf"\newcommand{{\{macro}SRHigh}}{{{_macro_number(row['ci_high'])}}}",
            ]
        )
    for macro, contrast in macro_contrasts.items():
        for suffix, metric in (("SR", "success_rate"), ("AUC", "dag_progress_auc")):
            row = contrast_index[(contrast, metric)]
            lines.extend(
                [
                    rf"\newcommand{{\{macro}{suffix}}}{{{_macro_number(row['estimate'])}}}",
                    rf"\newcommand{{\{macro}{suffix}Low}}{{{_macro_number(row['ci_low'])}}}",
                    rf"\newcommand{{\{macro}{suffix}High}}{{{_macro_number(row['ci_high'])}}}",
                ]
            )
    return "\n".join(lines) + "\n"


def _latex_condition_table(
    conditions: Sequence[Mapping[str, Any]],
    condition_rows: Sequence[Mapping[str, Any]],
    draft: bool,
) -> str:
    index = _summary_index(condition_rows, "condition")
    caption = "Phase II condition estimates"
    if draft:
        caption = DRAFT_WATERMARK + ": " + caption
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\scriptsize",
        rf"\caption{{{_tex_escape(caption)}. Values are estimates [95\% CI].}}",
        r"\resizebox{\textwidth}{!}{%",
        r"\begin{tabular}{lrrrrrrr}",
        r"\toprule",
        r"Condition & SR & DAG & AUC & Clean & Viol./round & Viol./action & Rounds " + r"\\",
        r"\midrule",
    ]
    for condition in conditions:
        condition_id = str(condition["id"])
        values = " & ".join(
            _tex_interval(index[(condition_id, metric)]) for metric in METRICS
        )
        lines.append(rf"{_tex_escape(condition['label'])} & {values} " + r"\\")
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}%",
            r"}",
            r"\label{tab:phase2-conditions}",
            r"\end{table}",
        ]
    )
    return "\n".join(lines) + "\n"


def _latex_contrast_table(
    contrast_rows: Sequence[Mapping[str, Any]], draft: bool
) -> str:
    index = _summary_index(contrast_rows, "contrast")
    caption = "Phase II headline paired contrasts"
    if draft:
        caption = DRAFT_WATERMARK + ": " + caption
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        r"\scriptsize",
        rf"\caption{{{_tex_escape(caption)}. Values are task-paired differences [95\% CI].}}",
        r"\begin{tabular}{lrrrr}",
        r"\toprule",
        r"Contrast & $\Delta$SR & $\Delta$AUC & $\Delta$Clean & $\Delta$Viol. " + r"\\",
        r"\midrule",
    ]
    for contrast in CONTRASTS:
        if contrast.name not in HEADLINE_CONTRASTS:
            continue
        values = " & ".join(
            _tex_interval(index[(contrast.name, metric)])
            for metric in (
                "success_rate",
                "dag_progress_auc",
                "clean_handoff_rate",
                "coordination_violation_rate",
            )
        )
        lines.append(rf"{_tex_escape(contrast.label)} & {values} " + r"\\")
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}",
            r"\label{tab:phase2-contrasts}",
            r"\end{table}",
        ]
    )
    return "\n".join(lines) + "\n"


def _tikz_figure(contrast_rows: Sequence[Mapping[str, Any]], draft: bool) -> str:
    index = _summary_index(contrast_rows, "contrast")
    selected = [contrast for contrast in CONTRASTS if contrast.name in HEADLINE_CONTRASTS]
    grid_top = max(1.0, len(selected) - 0.3)
    lines = [
        "% Plain TikZ forest plot; the corresponding numeric table is contrast_table.tex.",
        r"\begin{figure}[htbp]",
        r"\centering",
        r"\begin{tikzpicture}[x=5.2cm,y=0.72cm,font=\small]",
        r"\definecolor{phaseblue}{HTML}{2A78D6}",
        r"\definecolor{phasegrid}{HTML}{E1E0D9}",
        rf"\draw[phasegrid,line width=0.3pt] (-1,-0.7) -- (-1,{grid_top:.1f});",
        rf"\draw[phasegrid,line width=0.3pt] (-0.5,-0.7) -- (-0.5,{grid_top:.1f});",
        rf"\draw[black!55,line width=0.5pt] (0,-0.7) -- (0,{grid_top:.1f});",
        rf"\draw[phasegrid,line width=0.3pt] (0.5,-0.7) -- (0.5,{grid_top:.1f});",
        rf"\draw[phasegrid,line width=0.3pt] (1,-0.7) -- (1,{grid_top:.1f});",
    ]
    for position, contrast in enumerate(selected):
        y = len(selected) - position - 1
        row = index[(contrast.name, "success_rate")]
        lines.append(
            rf"\node[anchor=east,text width=5.1cm,align=right] at (-1.05,{y}) "
            rf"{{{_tex_escape(contrast.label)}}};"
        )
        if row["estimate"] is None:
            lines.append(rf"\node[anchor=west] at (-0.98,{y}) {{\NA}};")
            continue
        estimate = max(-1.0, min(1.0, float(row["estimate"])))
        low = max(-1.0, min(1.0, float(row["ci_low"])))
        high = max(-1.0, min(1.0, float(row["ci_high"])))
        lines.extend(
            [
                rf"\draw[phaseblue,line width=1.0pt] ({low:.6f},{y}) -- ({high:.6f},{y});",
                rf"\draw[phaseblue,line width=0.8pt] ({low:.6f},{y - 0.12}) -- ({low:.6f},{y + 0.12});",
                rf"\draw[phaseblue,line width=0.8pt] ({high:.6f},{y - 0.12}) -- ({high:.6f},{y + 0.12});",
                rf"\fill[phaseblue] ({estimate:.6f},{y}) circle[radius=2.1pt];",
            ]
        )
    lines.extend(
        [
            r"\draw[black!55,line width=0.5pt] (-1,-0.55) -- (1,-0.55);",
            r"\foreach \x/\label in {-1/-1.0,-0.5/-0.5,0/0,0.5/0.5,1/1.0}{%",
            r"  \draw[black!55] (\x,-0.55) -- (\x,-0.66);",
            r"  \node[anchor=north] at (\x,-0.69) {\label};",
            r"}",
            r"\node[anchor=north] at (0,-1.15) {Task-paired success-rate difference};",
            r"\end{tikzpicture}",
            rf"\caption{{{_tex_escape((DRAFT_WATERMARK + ': ') if draft else '')}Headline Phase II success-rate contrasts. Thin lines show 95\% task-bootstrap confidence intervals; the vertical rule marks zero.}}",
            r"\label{fig:phase2-headline-contrasts}",
            r"\end{figure}",
        ]
    )
    return "\n".join(lines) + "\n"


def _latex_report_source(draft: bool) -> str:
    watermark_hook = ""
    if draft:
        watermark_hook = "\n".join(
            [
                r"\AddToHook{shipout/background}{%",
                r"  \begin{tikzpicture}[remember picture,overlay]",
                r"  \node[rotate=35,scale=3.0,text=red!18] at (current page.center) {DRAFT -- NOT FOR PUBLICATION};",
                r"  \end{tikzpicture}%",
                r"}",
            ]
        )
    return f"""\\documentclass[10pt]{{article}}
\\usepackage[margin=0.75in]{{geometry}}
\\usepackage{{booktabs}}
\\usepackage{{graphicx}}
\\usepackage{{tikz}}
\\usepackage{{xcolor}}
\\usepackage[hidelinks]{{hyperref}}
\\input{{phase2_macros.tex}}
{watermark_hook}
\\title{{Phase II Causal Collaboration Results}}
\\author{{FWCollab}}
\\date{{}}
\\begin{{document}}
\\maketitle
\\noindent Publication status: \\textbf{{\\PhaseTwoPublicationStatus}}. The analysis contains
\\PhaseTwoObservedEpisodes/\\PhaseTwoExpectedEpisodes valid episodes and uses exactly
\\PhaseTwoBootstrapSamples deterministic task-level bootstrap draws. All estimates are task-first;
rounds-to-success uses task medians and then the median across tasks. Handoff rates remain undefined
when there is no handoff opportunity. Coordination violations are reported per round and per agent action.

\\input{{condition_table.tex}}
\\input{{contrast_table.tex}}
\\input{{headline_figure.tex}}

\\paragraph{{Interpretation.}}
Every contrast is oriented so positive values favor its left expression. Higher-is-better metrics use
left minus right; coordination violations and rounds-to-success use right minus left. Thus positive
communication gain favors Normal, and positive pooled cross-play gap favors self-play.
\\end{{document}}
"""


def _write_latex(
    output_dir: Path,
    *,
    integrity: Mapping[str, Any],
    conditions: Sequence[Mapping[str, Any]],
    condition_rows: Sequence[Mapping[str, Any]],
    contrast_rows: Sequence[Mapping[str, Any]],
    draft: bool,
) -> Path:
    latex_dir = output_dir / "latex"
    _write_text(
        latex_dir / "phase2_macros.tex",
        _latex_macros(integrity, condition_rows, contrast_rows),
        description="LaTeX macros",
    )
    _write_text(
        latex_dir / "condition_table.tex",
        _latex_condition_table(conditions, condition_rows, draft),
        description="LaTeX condition table",
    )
    _write_text(
        latex_dir / "contrast_table.tex",
        _latex_contrast_table(contrast_rows, draft),
        description="LaTeX contrast table",
    )
    _write_text(
        latex_dir / "headline_figure.tex",
        _tikz_figure(contrast_rows, draft),
        description="LaTeX headline figure",
    )
    source = latex_dir / "phase2_report.tex"
    _write_text(source, _latex_report_source(draft), description="LaTeX report")
    return source


def _compile_pdf(source: Path, executable: str) -> tuple[Path, dict[str, Any]]:
    resolved = shutil.which(executable)
    if resolved is None:
        candidate = Path(executable)
        if candidate.is_file():
            resolved = str(candidate.resolve())
    if resolved is None:
        raise AnalysisError(f"pdflatex executable not found: {executable}")
    resolved = str(Path(resolved).resolve())
    command = [
        resolved,
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-no-shell-escape",
        source.name,
    ]
    runs: list[dict[str, Any]] = []
    for pass_number in (1, 2):
        try:
            completed = subprocess.run(
                command,
                cwd=source.parent,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise AnalysisError(f"cannot execute pdflatex pass {pass_number}: {exc}") from exc
        runs.append(
            {
                "pass": pass_number,
                "returncode": completed.returncode,
                "stdout_tail": completed.stdout[-4000:],
                "stderr_tail": completed.stderr[-4000:],
            }
        )
        if completed.returncode != 0:
            raise AnalysisError(
                f"pdflatex failed on pass {pass_number} with exit code "
                f"{completed.returncode}: {completed.stdout[-800:]}{completed.stderr[-800:]}"
            )
    pdf = source.with_suffix(".pdf")
    if not pdf.is_file():
        raise AnalysisError("pdflatex returned success but did not create phase2_report.pdf")
    return pdf, {"status": "compiled", "executable": resolved, "passes": runs}


def _gate_errors(trace_details: Mapping[str, Any], run_state: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if trace_details["missing_traces"]:
        errors.append(f"missing {len(trace_details['missing_traces'])} expected trace(s)")
    if trace_details["unexpected_traces"]:
        errors.append(
            f"found {len(trace_details['unexpected_traces'])} unexpected trace JSON file(s)"
        )
    if trace_details["invalid_traces"]:
        errors.append(f"found {len(trace_details['invalid_traces'])} invalid trace(s)")
    if trace_details["valid_traces"] != EXPECTED_EPISODES:
        errors.append(
            f"valid trace count is {trace_details['valid_traces']}, expected {EXPECTED_EPISODES}"
        )
    errors.extend(str(error) for error in run_state.get("errors", []))
    return list(dict.fromkeys(errors))


def _complete_task_count(task_rows: Sequence[Mapping[str, Any]]) -> int:
    by_task: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in task_rows:
        by_task[str(row["task_id"])].append(row)
    return sum(
        len(rows) == EXPECTED_CONDITIONS
        and all(row["complete_three_replicates"] for row in rows)
        for rows in by_task.values()
    )


def _artifact_payload(
    *,
    format_name: str,
    publication_status: str,
    watermark: str | None,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "format": format_name,
        "publication_status": publication_status,
        "watermark": watermark,
        "rows": list(rows),
    }


def _analyze_phase2_impl(
    *,
    root: str | Path = ".",
    spec_path: str | Path = "eval_private/phase2_v1/experiment_spec.json",
    freeze_path: str | Path = "eval_private/phase2_v1/freeze_manifest.json",
    run_dir: str | Path = "artifacts/runs/phase2_v1",
    output_dir: str | Path = "artifacts/evaluations/phase2_v1",
    allow_incomplete: bool = False,
    compile_pdf: bool = True,
    pdflatex: str = "pdflatex",
    _staging_paths: list[Path],
) -> AnalysisResult:
    """Analyze the frozen Phase II trace matrix without modifying any input.

    A final output is written only after all publication gates pass.  When
    ``allow_incomplete`` is true, output is always isolated under
    ``draft_incomplete`` and visibly watermarked.
    """

    root_path = Path(root).resolve()
    resolved_spec = _resolve(root_path, spec_path)
    resolved_freeze = _resolve(root_path, freeze_path)
    resolved_run = _resolve(root_path, run_dir)
    requested_output = _resolve(root_path, output_dir)
    _validate_output_isolation(
        requested_output, [resolved_spec, resolved_freeze, resolved_run]
    )

    freeze = verify_freeze(root_path, resolved_freeze)
    spec = _load_json(resolved_spec)
    task_manifest_value = spec.get("task_manifest")
    if not isinstance(task_manifest_value, str) or not task_manifest_value:
        raise AnalysisError(
            "invalid frozen Phase II contract: task_manifest must be a non-empty path"
        )
    task_manifest_path = _resolve(root_path, task_manifest_value)
    task_manifest = _load_json(task_manifest_path)
    records, conditions = _validate_contract(spec, task_manifest, freeze)
    evaluator_records, evaluator_record_source = _resolve_evaluator_records(
        root_path, task_manifest, records
    )
    _validate_output_isolation(
        requested_output,
        [
            task_manifest_path,
            *(_resolve(root_path, str(record["map"])) for record in records),
            *(_resolve(root_path, str(record["dag"])) for record in records),
        ],
    )
    _require_frozen_analysis_inputs(
        root=root_path,
        spec_path=resolved_spec,
        task_manifest_path=task_manifest_path,
        records=records,
        freeze=freeze,
    )
    matrix = _expected_matrix(records, conditions)
    expected_keys = set(matrix)

    episode_rows, trace_details = _collect_episodes(
        root=root_path,
        run_dir=resolved_run,
        spec=spec,
        records=records,
        conditions=conditions,
        evaluator_records=evaluator_records,
    )
    run_state = _inspect_run_state(
        root=root_path,
        run_dir=resolved_run,
        spec_path=resolved_spec,
        matrix=matrix,
        trace_rows=episode_rows,
    )
    gate_errors = _gate_errors(trace_details, run_state)
    gate_passed = not gate_errors
    if not gate_passed and not allow_incomplete:
        preview = "; ".join(gate_errors[:8])
        raise AnalysisError(f"Phase II publication gate failed: {preview}")

    publication_status = "draft_incomplete" if allow_incomplete else "final"
    watermark = DRAFT_WATERMARK if allow_incomplete else None
    effective_output = (
        requested_output / DRAFT_SUBDIRECTORY if allow_incomplete else requested_output
    )

    task_rows = _task_metrics(episode_rows, conditions, records)
    complete_tasks = _complete_task_count(task_rows)
    condition_rows = _condition_summaries(task_rows, conditions)
    paired_rows = _paired_differences(episode_rows, records)
    contrast_rows = _contrast_summaries(paired_rows)
    headline_rows = _headline_summaries(condition_rows, contrast_rows)

    integrity = {
        "format": "fwcollab.symbolic.phase2_run_integrity.v2",
        "publication_status": publication_status,
        "watermark": watermark,
        "gate_passed": gate_passed,
        "gate_errors": gate_errors,
        "analysis_algorithm_version": ANALYSIS_ALGORITHM_VERSION,
        "strict_contract": {
            "tasks": EXPECTED_TASKS,
            "conditions": EXPECTED_CONDITIONS,
            "replicates": list(EXPECTED_REPLICATES),
            "expected_episodes": EXPECTED_EPISODES,
            "bootstrap_samples": BOOTSTRAP_SAMPLES,
            "confidence_level": CONFIDENCE_LEVEL,
            "condition_contract": [
                {
                    "id": condition["id"],
                    "label": condition["label"],
                    "communication_enabled": condition["communication_enabled"],
                    "roles": {
                        role: {
                            "requested_model": condition[role]["model"],
                            "api_style": condition[role]["api_style"],
                        }
                        for role in ("F", "W")
                    },
                }
                for condition in conditions
            ],
        },
        "resolved_model_inventory": {
            condition["id"]: {
                role: sorted(
                    {
                        resolved
                        for row in episode_rows
                        if row["condition"] == condition["id"]
                        for resolved in row[f"resolved_models_{role}"]
                    }
                )
                for role in ("F", "W")
            }
            for condition in conditions
        },
        "freeze": {key: value for key, value in freeze.items() if key not in {"raw", "files"}},
        "experiment_spec": _display_path(resolved_spec, root_path),
        "task_manifest": _display_path(task_manifest_path, root_path),
        "evaluator_record_source": evaluator_record_source,
        "run_dir": _display_path(resolved_run, root_path),
        "requested_output_dir": _display_path(requested_output, root_path),
        "effective_output_dir": _display_path(effective_output, root_path),
        "expected_episodes": EXPECTED_EPISODES,
        "observed_episodes": len(episode_rows),
        "complete_paired_tasks": complete_tasks,
        "trace_matrix": trace_details,
        "run_state": run_state,
        "no_provider_calls": True,
        "input_mutation": False,
        "evaluator_observations_authenticated_by_replay": True,
        "pdf": {"status": "pending" if compile_pdf else "skipped_by_caller"},
    }

    stamped_episode_rows = _stamp_rows(episode_rows, publication_status, watermark)
    stamped_task_rows = _stamp_rows(task_rows, publication_status, watermark)
    stamped_paired_rows = _stamp_rows(paired_rows, publication_status, watermark)
    stamped_condition_rows = _stamp_rows(condition_rows, publication_status, watermark)
    stamped_contrast_rows = _stamp_rows(contrast_rows, publication_status, watermark)
    stamped_headline_rows = _stamp_rows(headline_rows, publication_status, watermark)

    try:
        effective_output.parent.mkdir(parents=True, exist_ok=True)
        staging_output = Path(
            tempfile.mkdtemp(
                prefix=f".{effective_output.name}.staging-",
                dir=effective_output.parent,
            )
        )
        _staging_paths.append(staging_output)
    except OSError as exc:
        raise AnalysisError(f"cannot create analysis staging directory: {exc}") from exc
    publication_output = effective_output
    effective_output = staging_output
    _write_json(
        effective_output / "per_episode.json",
        _artifact_payload(
            format_name="fwcollab.symbolic.phase2_episodes.v2",
            publication_status=publication_status,
            watermark=watermark,
            rows=stamped_episode_rows,
        ),
    )
    _write_csv(
        effective_output / "per_episode.csv",
        stamped_episode_rows,
        empty_fields=EPISODE_CSV_FIELDS,
    )
    _write_json(
        effective_output / "task_metrics.json",
        _artifact_payload(
            format_name="fwcollab.symbolic.phase2_task_metrics.v2",
            publication_status=publication_status,
            watermark=watermark,
            rows=stamped_task_rows,
        ),
    )
    _write_csv(effective_output / "task_metrics.csv", stamped_task_rows)
    _write_json(
        effective_output / "paired_differences.json",
        _artifact_payload(
            format_name="fwcollab.symbolic.phase2_paired_differences.v2",
            publication_status=publication_status,
            watermark=watermark,
            rows=stamped_paired_rows,
        ),
    )
    _write_csv(effective_output / "paired_differences.csv", stamped_paired_rows)
    _write_json(
        effective_output / "condition_summary.json",
        _artifact_payload(
            format_name="fwcollab.symbolic.phase2_condition_summary.v2",
            publication_status=publication_status,
            watermark=watermark,
            rows=stamped_condition_rows,
        ),
    )
    _write_csv(effective_output / "condition_summary.csv", stamped_condition_rows)
    _write_json(
        effective_output / "contrasts.json",
        _artifact_payload(
            format_name="fwcollab.symbolic.phase2_contrasts.v2",
            publication_status=publication_status,
            watermark=watermark,
            rows=stamped_contrast_rows,
        ),
    )
    _write_csv(effective_output / "contrasts.csv", stamped_contrast_rows)
    _write_json(
        effective_output / "headline_summary.json",
        _artifact_payload(
            format_name="fwcollab.symbolic.phase2_headline_summary.v2",
            publication_status=publication_status,
            watermark=watermark,
            rows=stamped_headline_rows,
        ),
    )
    _write_csv(effective_output / "headline_summary.csv", stamped_headline_rows)

    condition_nested = _nested_summary(condition_rows, "condition")
    contrast_nested = _nested_summary(contrast_rows, "contrast")
    task_nested: dict[str, dict[str, dict[str, float | None]]] = defaultdict(dict)
    for row in task_rows:
        task_nested[str(row["condition"])][str(row["task_id"])] = dict(row["metrics"])
    compatibility_summary = {
        "format": "fwcollab.symbolic.phase2_analysis.v2",
        "analysis_algorithm_version": ANALYSIS_ALGORITHM_VERSION,
        "publication_status": publication_status,
        "watermark": watermark,
        "experiment_spec": _display_path(resolved_spec, root_path),
        "expected_episodes": EXPECTED_EPISODES,
        "observed_episodes": len(episode_rows),
        "missing_episodes": trace_details["missing_traces"],
        "complete_paired_tasks": complete_tasks,
        "bootstrap_unit": "task_after_aggregating_three_replicates",
        "paired_contrast_support": "common_replicate_ids_with_nonmissing_metric_on_both_sides",
        "bootstrap_samples": BOOTSTRAP_SAMPLES,
        "rounds_aggregation": "median_within_task_then_median_across_tasks",
        "clean_handoff_zero_opportunity": "NA",
        "coordination_violation_rate_definition": (
            "objective coordination violations per executed round"
        ),
        "coordination_violation_rate_per_agent_action_definition": (
            "objective coordination violations per two agent-action opportunities per executed round"
        ),
        "estimates": condition_nested,
        "paired_deltas": contrast_nested,
    }
    _write_json(effective_output / "summary.json", compatibility_summary)
    _write_json(
        effective_output / "task_means.json",
        {
            "format": "fwcollab.symbolic.phase2_task_means.v2",
            "publication_status": publication_status,
            "watermark": watermark,
            "conditions": dict(task_nested),
        },
    )
    _write_json(effective_output / "run_integrity.json", integrity)
    report_path = effective_output / "REPORT.md"
    _write_text(
        report_path,
        _markdown_report(
            integrity=integrity,
            conditions=conditions,
            condition_rows=condition_rows,
            contrast_rows=contrast_rows,
        ),
        description="Markdown report",
    )
    latex_source = _write_latex(
        effective_output,
        integrity=integrity,
        conditions=conditions,
        condition_rows=condition_rows,
        contrast_rows=contrast_rows,
        draft=allow_incomplete,
    )

    if compile_pdf:
        try:
            compiled_pdf, pdf_status = _compile_pdf(latex_source, pdflatex)
            destination_pdf = effective_output / "phase2_report.pdf"
            shutil.copyfile(compiled_pdf, destination_pdf)
            pdf_status["path"] = _display_path(
                publication_output / "phase2_report.pdf", root_path
            )
            integrity["pdf"] = pdf_status
        except Exception as exc:
            integrity["pdf"] = {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
            _write_json(effective_output / "run_integrity.json", integrity)
            _write_text(
                report_path,
                _markdown_report(
                    integrity=integrity,
                    conditions=conditions,
                    condition_rows=condition_rows,
                    contrast_rows=contrast_rows,
                ),
                description="Markdown report",
            )
            shutil.rmtree(staging_output, ignore_errors=True)
            if isinstance(exc, AnalysisError):
                raise
            raise AnalysisError(f"PDF compilation failed: {exc}") from exc

    _write_json(effective_output / "run_integrity.json", integrity)
    _write_text(
        report_path,
        _markdown_report(
            integrity=integrity,
            conditions=conditions,
            condition_rows=condition_rows,
            contrast_rows=contrast_rows,
        ),
        description="Markdown report",
    )
    _publish_staged_directory(staging_output, publication_output)
    effective_output = publication_output
    try:
        artifacts = tuple(
            sorted(path for path in effective_output.rglob("*") if path.is_file())
        )
    except OSError as exc:
        raise AnalysisError(
            "analysis output was published but artifact enumeration failed: "
            f"{exc}"
        ) from exc
    return AnalysisResult(
        output_dir=effective_output,
        publication_status=publication_status,
        gate_passed=gate_passed,
        observed_episodes=len(episode_rows),
        artifacts=artifacts,
    )


def _cleanup_staging_paths(paths: Sequence[Path]) -> list[str]:
    errors: list[str] = []
    for path in reversed(paths):
        if not path.exists():
            continue
        try:
            shutil.rmtree(path)
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    return errors


def analyze_phase2(
    *,
    root: str | Path = ".",
    spec_path: str | Path = "eval_private/phase2_v1/experiment_spec.json",
    freeze_path: str | Path = "eval_private/phase2_v1/freeze_manifest.json",
    run_dir: str | Path = "artifacts/runs/phase2_v1",
    output_dir: str | Path = "artifacts/evaluations/phase2_v1",
    allow_incomplete: bool = False,
    compile_pdf: bool = True,
    pdflatex: str = "pdflatex",
) -> AnalysisResult:
    """Analyze frozen traces and remove every unpublished staging directory."""

    staging_paths: list[Path] = []
    try:
        result = _analyze_phase2_impl(
            root=root,
            spec_path=spec_path,
            freeze_path=freeze_path,
            run_dir=run_dir,
            output_dir=output_dir,
            allow_incomplete=allow_incomplete,
            compile_pdf=compile_pdf,
            pdflatex=pdflatex,
            _staging_paths=staging_paths,
        )
    except Exception as exc:
        cleanup_errors = _cleanup_staging_paths(staging_paths)
        if cleanup_errors:
            raise AnalysisError(
                "analysis failed and staging cleanup failed: "
                + "; ".join(cleanup_errors)
            ) from exc
        raise
    cleanup_errors = _cleanup_staging_paths(staging_paths)
    if cleanup_errors:
        raise AnalysisError(
            "analysis completed but staging cleanup failed: "
            + "; ".join(cleanup_errors)
        )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze frozen Phase II traces without provider calls."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--spec", default="eval_private/phase2_v1/experiment_spec.json")
    parser.add_argument("--freeze", default="eval_private/phase2_v1/freeze_manifest.json")
    parser.add_argument("--run-dir", default="artifacts/runs/phase2_v1")
    parser.add_argument("--output-dir", default="artifacts/evaluations/phase2_v1")
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help=(
            "write a separately isolated, visibly watermarked draft instead of "
            "passing the final-publication gate"
        ),
    )
    parser.add_argument(
        "--no-pdf",
        action="store_true",
        help="skip pdflatex compilation (LaTeX and TikZ sources are still emitted)",
    )
    parser.add_argument("--pdflatex", default="pdflatex")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = analyze_phase2(
            root=args.root,
            spec_path=args.spec,
            freeze_path=args.freeze,
            run_dir=args.run_dir,
            output_dir=args.output_dir,
            allow_incomplete=args.allow_incomplete,
            compile_pdf=not args.no_pdf,
            pdflatex=args.pdflatex,
        )
    except AnalysisError as exc:
        raise SystemExit(str(exc)) from exc
    print(
        json.dumps(
            {
                "publication_status": result.publication_status,
                "gate_passed": result.gate_passed,
                "expected_episodes": EXPECTED_EPISODES,
                "observed_episodes": result.observed_episodes,
                "output_dir": result.output_dir.as_posix(),
                "provider_calls": 0,
            },
            ensure_ascii=False,
        )
    )
    return 0


__all__ = [
    "AnalysisError",
    "AnalysisResult",
    "BOOTSTRAP_SAMPLES",
    "CONDITION_IDS",
    "DRAFT_SUBDIRECTORY",
    "DRAFT_WATERMARK",
    "EXPECTED_EPISODES",
    "EXPECTED_REPLICATES",
    "EXPECTED_TASKS",
    "METRICS",
    "analyze_phase2",
    "bootstrap_task_values",
    "build_parser",
    "main",
    "verify_freeze",
]
