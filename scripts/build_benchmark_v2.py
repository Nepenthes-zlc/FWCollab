"""Build the reference-only FWCollab Benchmark v2 manifest and freeze."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fwcollab.benchmark_v2 import write_benchmark_v2  # noqa: E402


if __name__ == "__main__":
    manifest, freeze = write_benchmark_v2(ROOT)
    print(json.dumps({"manifest": manifest.as_posix(), "freeze": freeze.as_posix(), "tasks": 100, "tracks": 4}))
