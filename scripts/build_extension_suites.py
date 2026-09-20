"""Build and deterministically validate the separate Sync-8 and Join-8 suites."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "src", ROOT):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from fwcollab.symbolic.extension_suites import build_extension_suites  # noqa: E402


if __name__ == "__main__":
    print(json.dumps(build_extension_suites(ROOT), ensure_ascii=False, sort_keys=True))
