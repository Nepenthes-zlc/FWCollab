"""Run and report the deterministic evaluator conformance suite."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for candidate in (str(SRC), str(ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from fwcollab.analysis.evaluator_conformance import (  # noqa: E402
    ConformanceError,
    build_conformance,
    report_markdown,
)

DEFAULT_OUTPUT = Path("artifacts/evaluations/evaluator_conformance_v1")


def _csv_text(cases: Sequence[Mapping[str, Any]]) -> str:
    fields = ("case_id", "task_id", "family", "mutation", "applicable", "passed", "target_node", "target_round", "oracle", "reason", "observed")
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for case in cases:
        row = {key: case.get(key) for key in fields}
        row["observed"] = json.dumps(row["observed"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        writer.writerow(row)
    return buffer.getvalue()


def write_outputs(result: Mapping[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {key: value for key, value in result.items() if key != "cases"}
    paths = {
        "summary": output_dir / "summary.json",
        "cases_json": output_dir / "cases.json",
        "cases_csv": output_dir / "cases.csv",
        "report": output_dir / "REPORT.md",
    }
    paths["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    paths["cases_json"].write_text(json.dumps(result["cases"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    paths["cases_csv"].write_text(_csv_text(result["cases"]), encoding="utf-8-sig", newline="")
    paths["report"].write_text(report_markdown(result), encoding="utf-8", newline="\n")
    return {key: str(path) for key, path in paths.items()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    output = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
    try:
        result = build_conformance(root)
        paths = write_outputs(result, output)
    except (ConformanceError, OSError, TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    payload = {
        "ok": result["cases_failed"] == 0,
        "tasks": result["tasks"],
        "cases_applicable": result["cases_applicable"],
        "cases_failed": result["cases_failed"],
        "metrics": result["metrics"],
        "outputs": paths,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
