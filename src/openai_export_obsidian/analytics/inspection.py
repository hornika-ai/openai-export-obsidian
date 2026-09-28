from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .installation import managed_statuses
from .paths import AnalyticsPaths
from .reader import PackAnalytics
from .schema import validate_build_manifest, validate_dashboard
from .signatures import sha256_bytes


def inspect_analytics(paths: AnalyticsPaths, analytics: PackAnalytics) -> dict[str, Any]:
    files = managed_statuses(paths)
    dashboard_path = paths.output / "cache/dashboard.json"
    manifest_path = paths.output / "cache/build-manifest.json"
    cache_status = _cache_status(dashboard_path, manifest_path, analytics.source_identity)
    files["cache/dashboard.json"] = cache_status[0]
    files["cache/build-manifest.json"] = cache_status[0]
    install_actions = sorted(path for path, status in files.items() if status == "create" and not path.startswith("cache/"))
    return {
        "status": "ok",
        "pack_schema": analytics.source_identity["pack_schema"],
        "pack_vault_path": paths.pack_vault_path,
        "output_vault_path": paths.output_vault_path,
        "conversation_count": analytics.source_identity["conversation_count"],
        "files": dict(sorted(files.items())),
        "cache_fresh": cache_status[0] == "unchanged",
        "diagnostics": sorted({*analytics.warnings, *cache_status[1]}),
        "planned": {
            "install": install_actions,
            "refresh": ["cache/dashboard.json", "cache/build-manifest.json"],
        },
    }


def _cache_status(dashboard_path: Path, manifest_path: Path, source_identity: dict[str, Any]) -> tuple[str, list[str]]:
    if dashboard_path.is_symlink() or manifest_path.is_symlink():
        return "incompatible", ["cache files must not be symlinks"]
    if not dashboard_path.exists() and not manifest_path.exists():
        return "missing", ["analytics cache is not built"]
    if not dashboard_path.is_file() or not manifest_path.is_file():
        return "incompatible", ["analytics cache is incomplete"]
    try:
        dashboard_bytes = dashboard_path.read_bytes()
        dashboard = json.loads(dashboard_bytes)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return "incompatible", [f"analytics cache cannot be read: {exc}"]
    errors = validate_dashboard(dashboard) + validate_build_manifest(manifest, dashboard_bytes)
    if errors:
        return "incompatible", errors
    if manifest.get("source_identity") != source_identity or dashboard.get("source") != source_identity:
        return "stale", ["analytics cache source identity does not match the validated pack"]
    if manifest.get("row_count") != len(dashboard.get("conversations", [])):
        return "incompatible", ["analytics cache row count is inconsistent"]
    if manifest.get("dashboard_sha256") != sha256_bytes(dashboard_bytes):
        return "incompatible", ["analytics cache dashboard hash is inconsistent"]
    return "unchanged", []
