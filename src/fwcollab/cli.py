"""Command line entry points for the FWCollab symbol benchmark."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path
from typing import Sequence

from fwcollab import __version__


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _symbol_map_status(project_root: Path) -> dict[str, object]:
    config_path = project_root / "maps" / "symbolic" / "S01.fwmap"
    result: dict[str, object] = {
        "path": str(config_path),
        "exists": config_path.is_file(),
        "valid": False,
    }
    if not config_path.is_file():
        result["error"] = "symbol map is missing"
        return result

    try:
        from fwcollab.symbolic.map import load_symbol_map

        symbol_map = load_symbol_map(config_path)
    except (OSError, ValueError) as exc:
        result["error"] = f"cannot load symbol map: {exc}"
        return result
    result["valid"] = symbol_map.format in {
        "fwcollab.symbol_map.v1",
        "fwcollab.symbol_map.v2",
        "fwcollab.symbol_map.v3",
    }
    result["map_id"] = symbol_map.map_id
    return result


def _recorded_real_model_calls(project_root: Path) -> int:
    total = 0
    for path in (project_root / "artifacts" / "runs").rglob("*.json"):
        if "legacy" in path.parts:
            continue
        try:
            trace = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(trace, dict) or trace.get("format") != "fwcollab.symbol_trace.v1":
            continue
        models = trace.get("models", {})
        if isinstance(models, dict) and any(not str(value).startswith("private-witness") for value in models.values()):
            metrics = trace.get("metrics", {})
            if isinstance(metrics, dict):
                total += int(metrics.get("total_model_calls", 0))
    return total


def doctor(_args: argparse.Namespace) -> int:
    """Print a machine-readable bootstrap health report."""

    root = _project_root()
    symbol_map = _symbol_map_status(root)
    package_ok = (root / "src" / "fwcollab" / "__init__.py").is_file()
    checks = {
        "package_import": package_ok,
        "symbol_map": bool(symbol_map["valid"]),
        "python_supported": sys.version_info >= (3, 11),
    }
    report = {
        "format": "fwcollab.doctor.v1",
        "version": __version__,
        "ok": all(checks.values()),
        "checks": checks,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "project_root": str(root),
        "symbol_map": symbol_map,
        "implementation_level": "symbolic_dual_agent",
        "engine_created": True,
        "dual_agent_runner": True,
        "symbol_maps": len(list((root / "maps" / "symbolic").glob("*.fwmap"))),
        "real_model_calls": _recorded_real_model_calls(root),
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


def symbol_validate(args: argparse.Namespace) -> int:
    from fwcollab.symbolic.map import load_symbol_map
    from fwcollab.symbolic.world import SymbolWorld

    try:
        symbol_map = load_symbol_map(args.map)
        world = SymbolWorld(symbol_map)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "format": symbol_map.format,
                "map_id": symbol_map.map_id,
                "width": symbol_map.width,
                "height": symbol_map.height,
                "state_hash": world.state_hash(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def symbol_render(args: argparse.Namespace) -> int:
    from fwcollab.symbolic.html import save_symbol_html
    from fwcollab.symbolic.map import load_symbol_map

    try:
        symbol_map = load_symbol_map(args.map)
        save_symbol_html(args.output, symbol_map)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "map_id": symbol_map.map_id, "output": str(Path(args.output))}, ensure_ascii=False))
    return 0


def symbol_render_suite(args: argparse.Namespace) -> int:
    from fwcollab.symbolic.html import save_symbol_gallery
    from fwcollab.symbolic.map import load_symbol_map

    try:
        paths = sorted(Path(args.map_dir).glob("*.fwmap"))
        if not paths:
            raise ValueError(f"no .fwmap files in {args.map_dir}")
        symbol_maps = [load_symbol_map(path) for path in paths]
        save_symbol_gallery(args.output, symbol_maps)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "maps": len(symbol_maps), "output": str(Path(args.output))}, ensure_ascii=False))
    return 0


def dag_generate(args: argparse.Namespace) -> int:
    from fwcollab.symbolic.dag_curriculum import curriculum_summary, write_curriculum

    try:
        catalog = write_curriculum(args.catalog, args.graph_dir, args.artifact_dir)
        summary = curriculum_summary(catalog)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, **summary, "catalog": str(Path(args.catalog))}, ensure_ascii=False))
    return 0


def dag_validate(args: argparse.Namespace) -> int:
    from fwcollab.symbolic.dag import (
        CATALOG_FORMAT,
        graph_metrics,
        graph_signature,
        load_json,
        validate_curriculum_catalog,
        validate_state_dag,
    )
    from fwcollab.symbolic.dag_curriculum import curriculum_summary

    try:
        data = load_json(args.input)
        if data.get("format") == CATALOG_FORMAT:
            validate_curriculum_catalog(data)
            result = curriculum_summary(data)
        else:
            validate_state_dag(data)
            result = {
                "id": data["id"],
                "metrics": graph_metrics(data),
                "topology_signature": graph_signature(data, "topology"),
                "typed_signature": graph_signature(data, "typed"),
            }
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


def dag_compare(args: argparse.Namespace) -> int:
    from fwcollab.symbolic.dag import compare_state_dags, load_json, save_json

    try:
        result = compare_state_dags(load_json(args.reference), load_json(args.candidate))
        if args.output:
            save_json(args.output, result)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, **result, "output": args.output}, ensure_ascii=False))
    return 0


def dag_render(args: argparse.Namespace) -> int:
    from fwcollab.symbolic.dag import load_json, save_state_dag_svg

    try:
        save_state_dag_svg(args.output, load_json(args.input))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "output": str(Path(args.output))}, ensure_ascii=False))
    return 0


def dag_render_suite(args: argparse.Namespace) -> int:
    from fwcollab.symbolic.dag import save_manifest_dag_artifacts

    try:
        generated = save_manifest_dag_artifacts(args.manifest, args.output_dir)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "dags": len(generated) - 1,
                "index": str(Path(args.output_dir) / "index.html"),
            },
            ensure_ascii=False,
        )
    )
    return 0


def spatial_generate(args: argparse.Namespace) -> int:
    from fwcollab.symbolic.full_spatial import write_full_spatial_curriculum

    try:
        manifest = write_full_spatial_curriculum(args.output_root, args.gallery, args.trace_root)
    except (OSError, AssertionError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "maps": manifest["count"],
                "topology_unique": manifest["topology_unique"],
                "output_root": str(Path(args.output_root)),
                "gallery": str(Path(args.gallery)),
                "trace_root": str(Path(args.trace_root)),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _witness_policies(map_id: str, witness_path: str):
    from fwcollab.symbolic.agents import AgentDecision, ScriptedPolicy
    from fwcollab.symbolic.world import SymbolAction

    catalog = json.loads(Path(witness_path).read_text(encoding="utf-8-sig"))
    rounds = catalog.get("maps", {}).get(map_id)
    if not isinstance(rounds, list):
        raise ValueError(f"no private witness for map {map_id}")
    decisions = {"F": [], "W": []}
    for round_actions in rounds:
        for role in ("F", "W"):
            spec = round_actions.get(role, ["WAIT", 0])
            decisions[role].append(
                AgentDecision(
                    action=SymbolAction(move=spec[0], steps=spec[1]),
                    reason="offline acceptance witness",
                )
            )
    return {
        role: ScriptedPolicy(decisions[role], name=f"private-witness-{role}")
        for role in ("F", "W")
    }


def symbol_run(args: argparse.Namespace) -> int:
    from fwcollab.symbolic.agents import OpenAICompatiblePolicy
    from fwcollab.symbolic.map import load_symbol_map
    from fwcollab.symbolic.runner import DualAgentSession, save_trace

    try:
        symbol_map = load_symbol_map(args.map)
        if args.provider == "witness":
            policies = _witness_policies(symbol_map.map_id, args.witness)
        else:
            if not args.allow_network:
                raise ValueError("real model calls require explicit --allow-network")
            endpoint = args.endpoint or os.environ.get("FWCOLLAB_MODEL_ENDPOINT")
            if not endpoint:
                raise ValueError("--endpoint or FWCOLLAB_MODEL_ENDPOINT is required")
            api_key = os.environ.get("FWCOLLAB_API_KEY")
            if args.api_key_file:
                api_key = Path(args.api_key_file).read_text(encoding="utf-8").strip()
            common = {
                "endpoint": endpoint,
                "api_key": api_key,
                "timeout_seconds": args.request_timeout,
                "max_output_tokens": args.max_output_tokens,
                "api_style": args.api_style,
            }
            policies = {
                "F": OpenAICompatiblePolicy(model=args.fire_model, **common),
                "W": OpenAICompatiblePolicy(model=args.water_model, **common),
            }
        result = DualAgentSession(
            symbol_map,
            policies,
            max_rounds=args.max_rounds,
            planning_rounds=args.planning_rounds,
            coordination_mode=args.coordination_mode,
        ).run()
        save_trace(args.output, result.trace)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "map_id": symbol_map.map_id,
                "provider": args.provider,
                "outcome": result.trace["outcome"],
                "rounds": result.metrics["rounds"],
                "wall_time_ms": result.trace["wall_time_ms"],
                "model_errors": result.metrics["total_model_errors"],
                "output": str(Path(args.output)),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def symbol_replay(args: argparse.Namespace) -> int:
    from fwcollab.symbolic.map import load_symbol_map
    from fwcollab.symbolic.runner import replay_trace

    try:
        symbol_map = load_symbol_map(args.map)
        trace = json.loads(Path(args.trace).read_text(encoding="utf-8"))
        report = replay_trace(symbol_map, trace)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


def symbol_diagnose(args: argparse.Namespace) -> int:
    from fwcollab.symbolic.map import load_symbol_map
    from fwcollab.symbolic.runner import diagnose_recorded_trace

    try:
        symbol_map = load_symbol_map(args.map)
        trace = json.loads(Path(args.trace).read_text(encoding="utf-8"))
        diagnosis = diagnose_recorded_trace(symbol_map, trace)
        if args.output:
            destination = Path(args.output)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(diagnosis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "diagnosis": diagnosis, "output": args.output}, ensure_ascii=False, sort_keys=True))
    return 0


def symbol_generate(args: argparse.Namespace) -> int:
    from fwcollab.symbolic.generator import generate_plate_support

    try:
        generated = generate_plate_support(args.seed)
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(generated.text, encoding="utf-8")
    except (OSError, ValueError, AssertionError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"ok": True, "map_id": generated.symbol_map.map_id, "seed": args.seed, "output": str(destination)},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def symbol_render_trace(args: argparse.Namespace) -> int:
    from fwcollab.symbolic.html import save_trace_html

    try:
        trace = json.loads(Path(args.trace).read_text(encoding="utf-8"))
        if args.diagnosis:
            trace["diagnosis"] = json.loads(Path(args.diagnosis).read_text(encoding="utf-8"))
        save_trace_html(args.output, trace)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, "output": str(Path(args.output))}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fwcollab",
        description="FWCollab symbol-based cooperative planning benchmark.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="check the symbol package and example map",
        description="Validate the installed symbol benchmark and example map.",
    )
    doctor_parser.set_defaults(handler=doctor)

    validate_symbol_parser = subparsers.add_parser("symbol-validate", help="validate a .fwmap symbol map")
    validate_symbol_parser.add_argument("--map", required=True)
    validate_symbol_parser.set_defaults(handler=symbol_validate)

    render_symbol_parser = subparsers.add_parser("symbol-render", help="render a .fwmap as standalone HTML")
    render_symbol_parser.add_argument("--map", required=True)
    render_symbol_parser.add_argument("--output", required=True)
    render_symbol_parser.set_defaults(handler=symbol_render)

    render_suite_parser = subparsers.add_parser("symbol-render-suite", help="render all .fwmap files as one HTML gallery")
    render_suite_parser.add_argument("--map-dir", required=True)
    render_suite_parser.add_argument("--output", required=True)
    render_suite_parser.set_defaults(handler=symbol_render_suite)

    dag_generate_parser = subparsers.add_parser("dag-generate", help="generate the 72-DAG private reference curriculum")
    dag_generate_parser.add_argument("--catalog", default="eval_private/dag_curriculum/catalog.json")
    dag_generate_parser.add_argument("--graph-dir", default="eval_private/dag_curriculum/graphs")
    dag_generate_parser.add_argument("--artifact-dir", default="artifacts/eval_private/dag_curriculum")
    dag_generate_parser.set_defaults(handler=dag_generate)

    dag_validate_parser = subparsers.add_parser("dag-validate", help="validate one state DAG or the 72-DAG catalog")
    dag_validate_parser.add_argument("--input", required=True)
    dag_validate_parser.set_defaults(handler=dag_validate)

    dag_compare_parser = subparsers.add_parser("dag-compare", help="compare an agent candidate DAG with a reference")
    dag_compare_parser.add_argument("--reference", required=True)
    dag_compare_parser.add_argument("--candidate", required=True)
    dag_compare_parser.add_argument("--output")
    dag_compare_parser.set_defaults(handler=dag_compare)

    dag_render_parser = subparsers.add_parser("dag-render", help="render one state DAG as a standalone SVG")
    dag_render_parser.add_argument("--input", required=True)
    dag_render_parser.add_argument("--output", required=True)
    dag_render_parser.set_defaults(handler=dag_render)

    dag_render_suite_parser = subparsers.add_parser(
        "dag-render-suite", help="render every DAG referenced by a task manifest as SVG and HTML"
    )
    dag_render_suite_parser.add_argument(
        "--manifest", default="eval_private/spatial_curriculum_full_v4/manifest.json"
    )
    dag_render_suite_parser.add_argument(
        "--output-dir", default="artifacts/symbolic/spatial_curriculum_full_v4_dags"
    )
    dag_render_suite_parser.set_defaults(handler=dag_render_suite)

    spatial_parser = subparsers.add_parser(
        "spatial-generate",
        help="generate the current 72-map L1-L7 M01-M30 spatial curriculum",
    )
    spatial_parser.add_argument("--output-root", default="eval_private/spatial_curriculum_full_v4")
    spatial_parser.add_argument("--gallery", default="artifacts/symbolic/spatial_curriculum_full_v4_gallery.html")
    spatial_parser.add_argument("--trace-root", default="artifacts/runs/spatial_curriculum_full_v4_witness")
    spatial_parser.set_defaults(handler=spatial_generate)

    run_parser = subparsers.add_parser("symbol-run", help="run two independent simultaneous agents")
    run_parser.add_argument("--map", required=True)
    run_parser.add_argument("--output", required=True)
    run_parser.add_argument("--provider", choices=("witness", "openai"), default="witness")
    run_parser.add_argument("--witness", default="eval_private/core_witnesses.json")
    run_parser.add_argument("--endpoint")
    run_parser.add_argument(
        "--api-style", choices=("responses", "chat_completions"), default="responses"
    )
    run_parser.add_argument("--fire-model", default="gpt-5.4-mini")
    run_parser.add_argument("--water-model", default="gpt-5.4-mini")
    run_parser.add_argument("--api-key-file")
    run_parser.add_argument("--allow-network", action="store_true")
    run_parser.add_argument("--max-rounds", type=int, default=80)
    run_parser.add_argument("--request-timeout", type=float, default=120.0)
    run_parser.add_argument("--max-output-tokens", type=int, default=1600)
    run_parser.add_argument(
        "--planning-rounds",
        type=int,
        choices=(0, 1, 2),
        default=0,
        help="optional pre-action DAG proposal/revision rounds; use 2 for propose-then-reconcile",
    )
    run_parser.add_argument(
        "--coordination-mode",
        choices=("emergent", "protocol_assisted"),
        default="emergent",
        help="test self-organized coordination or expose the recommended held-gate handshake",
    )
    run_parser.set_defaults(handler=symbol_run)

    replay_parser = subparsers.add_parser("symbol-replay", help="verify a recorded two-agent trace")
    replay_parser.add_argument("--map", required=True)
    replay_parser.add_argument("--trace", required=True)
    replay_parser.set_defaults(handler=symbol_replay)

    diagnose_parser = subparsers.add_parser("symbol-diagnose", help="classify a recorded trace failure")
    diagnose_parser.add_argument("--map", required=True)
    diagnose_parser.add_argument("--trace", required=True)
    diagnose_parser.add_argument("--output")
    diagnose_parser.set_defaults(handler=symbol_diagnose)

    generate_parser = subparsers.add_parser("symbol-generate", help="generate a seeded solvable plate-support map")
    generate_parser.add_argument("--seed", required=True, type=int)
    generate_parser.add_argument("--output", required=True)
    generate_parser.set_defaults(handler=symbol_generate)

    trace_render_parser = subparsers.add_parser("symbol-render-trace", help="render a trace as interactive HTML")
    trace_render_parser.add_argument("--trace", required=True)
    trace_render_parser.add_argument("--output", required=True)
    trace_render_parser.add_argument("--diagnosis", help="optional post-run diagnosis JSON to display")
    trace_render_parser.set_defaults(handler=symbol_render_trace)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
