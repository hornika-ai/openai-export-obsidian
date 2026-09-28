from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from .inspection import inspect_analytics
from .installation import install
from .paths import resolve_analytics_paths
from .reader import read_pack_analytics
from .refresh import refresh


def run_inspect(pack: Path, output: Path) -> int:
    return _run(pack, output, lambda paths, analytics: inspect_analytics(paths, analytics))


def run_install(pack: Path, output: Path) -> int:
    return _run(pack, output, lambda paths, analytics: install(paths))


def run_refresh(pack: Path, output: Path) -> int:
    return _run(pack, output, refresh)


def _run(pack: Path, output: Path, operation: Callable[[Any, Any], dict[str, Any]]) -> int:
    try:
        paths = resolve_analytics_paths(pack, output)
        analytics = read_pack_analytics(paths)
        report = operation(paths, analytics)
        payload = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(payload)
    return 0
