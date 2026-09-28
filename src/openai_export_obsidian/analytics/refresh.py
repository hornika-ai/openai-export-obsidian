from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from openai_export_obsidian import __version__

from . import ANALYTICS_SCHEMA, TEMPLATE_VERSION
from .aggregates import build_dashboard
from .installation import require_compatible_installation
from .paths import AnalyticsPaths
from .reader import PackAnalytics
from .schema import validate_build_manifest, validate_dashboard
from .signatures import sha256_bytes


class AnalyticsRefreshError(ValueError):
    pass


def refresh(paths: AnalyticsPaths, analytics: PackAnalytics) -> dict[str, Any]:
    require_compatible_installation(paths)
    dashboard = build_dashboard(analytics.source_identity, analytics.conversations, analytics.warnings)
    dashboard_bytes = _json_bytes(dashboard)
    manifest = {
        "package_version": __version__,
        "analytics_schema": ANALYTICS_SCHEMA,
        "template_version": TEMPLATE_VERSION,
        "source_identity": analytics.source_identity,
        "row_count": len(analytics.conversations),
        "dashboard_sha256": sha256_bytes(dashboard_bytes),
        "warnings": sorted(set(analytics.warnings)),
    }
    manifest_bytes = _json_bytes(manifest)
    errors = validate_dashboard(dashboard) + validate_build_manifest(manifest, dashboard_bytes)
    if errors:
        raise AnalyticsRefreshError("generated cache failed validation: " + "; ".join(errors))

    paths.output.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".cache-staging-", dir=paths.output))
    backup = paths.output / f".cache-backup-{uuid.uuid4().hex}"
    cache = paths.output / "cache"
    moved_old = False
    installed_new = False
    try:
        _write_fsynced(staging / "dashboard.json", dashboard_bytes)
        _write_fsynced(staging / "build-manifest.json", manifest_bytes)
        _verify_staging(staging)
        if cache.exists() or cache.is_symlink():
            if cache.is_symlink() or not cache.is_dir():
                raise AnalyticsRefreshError("existing cache is not a regular directory")
            os.replace(cache, backup)
            moved_old = True
        os.replace(staging, cache)
        installed_new = True
        cleanup_warning = None
        if moved_old:
            try:
                shutil.rmtree(backup)
            except OSError:
                cleanup_warning = "previous cache backup could not be removed"
        return {
            "status": "refreshed",
            "row_count": len(analytics.conversations),
            "dashboard_sha256": manifest["dashboard_sha256"],
            "warnings": [*manifest["warnings"], *([cleanup_warning] if cleanup_warning else [])],
        }
    except Exception:
        if installed_new and cache.exists():
            failed = paths.output / f".cache-failed-{uuid.uuid4().hex}"
            os.replace(cache, failed)
            if moved_old and backup.exists():
                os.replace(backup, cache)
            shutil.rmtree(failed, ignore_errors=True)
        elif moved_old and backup.exists() and not cache.exists():
            os.replace(backup, cache)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def _verify_staging(staging: Path) -> None:
    dashboard_bytes = (staging / "dashboard.json").read_bytes()
    dashboard = json.loads(dashboard_bytes)
    manifest = json.loads((staging / "build-manifest.json").read_text(encoding="utf-8"))
    errors = validate_dashboard(dashboard) + validate_build_manifest(manifest, dashboard_bytes)
    if errors:
        raise AnalyticsRefreshError("staged cache failed validation: " + "; ".join(errors))


def _write_fsynced(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
