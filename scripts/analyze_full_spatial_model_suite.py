"""Replay, render and summarize a full-mechanism spatial model run."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from fwcollab.symbolic.html import save_trace_html
from fwcollab.symbolic.map import load_symbol_map
from fwcollab.symbolic.runner import replay_trace


def _observations(trace: dict[str, Any]) -> list[dict[str, Any]]:
    rounds = trace["rounds"]
    if not rounds:
        return [trace["final_observation"]]
    return [rounds[0]["observation"], *(item["result"]["observation"] for item in rounds)]


def _capability_progress(record: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    cap = record["capability"]
    motif = record["primary_capability"]
    traveler = cap["traveler"]
    gate = int(cap["gate_col"])
    observations = _observations(trace)
    initial = observations[0]
    reached = any(
        max(int(obs["state"]["actors"][role][1]) for role in ("F", "W")) >= gate - 10
        for obs in observations
    )
    crossed = any(int(obs["state"]["actors"][traveler][1]) > gate for obs in observations)
    engaged = False
    if motif in {"M03", "M04", "M05", "M08"}:
        engaged = crossed
    elif motif == "M06":
        engaged = any(obs["state"]["crates"] != initial["state"]["crates"] for obs in observations)
    elif motif == "M07":
        engaged = any(obs["state"]["orbs"] != initial["state"]["orbs"] for obs in observations)
    elif motif == "M09":
        engaged = any(
            item["agents"][role].get("feedback") == "moved_via_portal"
            for item in trace["rounds"]
            for role in ("F", "W")
        )
    elif motif in {"M12", "M13", "M15", "M16"}:
        controller = cap["controller_id"]
        engaged = any(bool(obs["state"]["controllers"].get(controller)) for obs in observations)
    elif motif in {"M21", "M24"}:
        engaged = reached and any(bool(obs["state"]["light_sensors"].get("S")) for obs in observations)
    elif motif in {"M22", "M23", "M25", "M26", "M27", "M28", "M29"}:
        engaged = any(
            not bool(initial["state"]["light_sensors"].get("S"))
            and bool(obs["state"]["light_sensors"].get("S"))
            for obs in observations
        )
    elif motif == "M30":
        engaged = any(bool(obs["state"]["thermal_frozen"].get("I")) for obs in observations)
    base_crossed = sum(
        any(int(obs["state"]["actors"][stage["traveler"]][1]) > int(stage["room"]["gate_col"]) for obs in observations)
        for stage in record["stages"]
    )
    return {
        "base_stages_crossed": base_crossed,
        "base_stages_total": len(record["stages"]),
        "capability_reached": reached,
        "capability_engaged": engaged,
        "capability_crossed": crossed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    run_dir = Path(args.run_dir)
    rows: list[dict[str, Any]] = []
    links: list[str] = []
    replayed = 0
    for record in manifest["records"]:
        trace_path = run_dir / record["difficulty"] / f"{record['id']}.json"
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        symbol_map = load_symbol_map(record["map"])
        replay_trace(symbol_map, trace)
        replayed += 1
        progress = _capability_progress(record, trace)
        row = {
            "id": record["id"],
            "difficulty": record["difficulty"],
            "primary_capability": record["primary_capability"],
            "outcome": trace["outcome"],
            "rounds": len(trace["rounds"]),
            "model_errors": trace["metrics"]["total_model_errors"],
            "diagnosis": trace["diagnosis"]["primary"],
            **progress,
        }
        rows.append(row)
        trace["private_evaluation"] = {"visibility": "private_post_run_only", **progress}
        trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        html_path = trace_path.with_suffix(".html")
        save_trace_html(html_path, trace)
        links.append(
            f'<li><a href="{record["difficulty"]}/{record["id"]}.html">{record["id"]} · '
            f'{record["primary_capability"]} · {trace["outcome"]}</a></li>'
        )

    def aggregate(key: str) -> dict[str, dict[str, Any]]:
        groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[str(row[key])].append(row)
        result: dict[str, dict[str, Any]] = {}
        for name, subset in sorted(groups.items()):
            outcomes = Counter(row["outcome"] for row in subset)
            result[name] = {
                "maps": len(subset),
                "success": outcomes["team_success"],
                "success_rate": round(outcomes["team_success"] / len(subset), 4),
                "team_failure": outcomes["team_failure"],
                "timeout": outcomes["timeout"],
                "capability_reached": sum(row["capability_reached"] for row in subset),
                "capability_engaged": sum(row["capability_engaged"] for row in subset),
                "capability_crossed": sum(row["capability_crossed"] for row in subset),
            }
        return result

    analysis = {
        "format": "fwcollab.full_spatial_model_analysis.v1",
        "maps": len(rows),
        "replayed_exactly": replayed,
        "outcomes": dict(Counter(row["outcome"] for row in rows)),
        "diagnoses": dict(Counter(row["diagnosis"] for row in rows if row["outcome"] != "team_success")),
        "capability_reached": sum(row["capability_reached"] for row in rows),
        "capability_engaged": sum(row["capability_engaged"] for row in rows),
        "capability_crossed": sum(row["capability_crossed"] for row in rows),
        "by_difficulty": aggregate("difficulty"),
        "by_primary_capability": aggregate("primary_capability"),
        "results": rows,
    }
    (run_dir / "analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (run_dir / "index.html").write_text(
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>FWCollab V4模型轨迹</title>"
        "<style>:root{color-scheme:dark;font-family:system-ui}body{max-width:960px;margin:auto;padding:24px;"
        "background:#0b1220;color:#e2e8f0}li{margin:8px;padding:10px;background:#111c31;border-radius:8px}"
        "a{color:#7dd3fc}</style></head><body><h1>72张M01–M30真实双模型轨迹</h1>"
        f"<ol>{''.join(links)}</ol></body></html>",
        encoding="utf-8",
    )
    print(json.dumps({key: analysis[key] for key in ("maps", "replayed_exactly", "outcomes", "diagnoses", "capability_reached", "capability_engaged", "capability_crossed")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
