from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from . import REVIEW_SCHEMA
from .paths import AnalyticsPaths
from .schema import validate_review_state
from .templates import template_bytes


class AnalyticsInstallError(ValueError):
    pass


STATE_RELATIVE_PATH = "state/review-state.json"


def managed_statuses(paths: AnalyticsPaths) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for relative, expected in template_bytes().items():
        target = paths.output / relative
        if target.is_symlink():
            statuses[relative] = "incompatible"
        elif not target.exists():
            statuses[relative] = "create"
        elif not target.is_file():
            statuses[relative] = "incompatible"
        else:
            try:
                statuses[relative] = "unchanged" if target.read_bytes() == expected else "divergent"
            except OSError:
                statuses[relative] = "incompatible"
    state_path = paths.output / STATE_RELATIVE_PATH
    if state_path.is_symlink():
        statuses[STATE_RELATIVE_PATH] = "incompatible"
    elif not state_path.exists():
        statuses[STATE_RELATIVE_PATH] = "create"
    elif not state_path.is_file():
        statuses[STATE_RELATIVE_PATH] = "incompatible"
    else:
        try:
            value = json.loads(state_path.read_text(encoding="utf-8"))
            statuses[STATE_RELATIVE_PATH] = "unchanged" if not validate_review_state(value) else "incompatible"
        except (OSError, json.JSONDecodeError):
            statuses[STATE_RELATIVE_PATH] = "incompatible"
    return statuses


def install(paths: AnalyticsPaths) -> dict[str, Any]:
    statuses = managed_statuses(paths)
    blockers = [path for path, status in statuses.items() if status in {"divergent", "incompatible"}]
    if blockers:
        raise AnalyticsInstallError("managed installation is divergent or incompatible: " + ", ".join(sorted(blockers)))
    writes = dict(template_bytes())
    if statuses[STATE_RELATIVE_PATH] == "create":
        writes[STATE_RELATIVE_PATH] = _json_bytes({"version": REVIEW_SCHEMA, "records": {}})
    writes = {path: content for path, content in writes.items() if statuses[path] == "create"}

    created: list[Path] = []
    try:
        for relative, content in writes.items():
            target = paths.output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_new(target, content)
            created.append(target)
    except Exception:
        for target in reversed(created):
            try:
                target.unlink()
            except OSError:
                pass
        raise
    return {"status": "installed", "created": sorted(path.relative_to(paths.output).as_posix() for path in created), "files": managed_statuses(paths)}


def require_compatible_installation(paths: AnalyticsPaths) -> None:
    statuses = managed_statuses(paths)
    incompatible = [path for path, status in statuses.items() if status != "unchanged"]
    if incompatible:
        raise AnalyticsInstallError("compatible installation required: " + ", ".join(sorted(incompatible)))


def _atomic_write_new(target: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists() or target.is_symlink():
            raise AnalyticsInstallError(f"refusing to replace existing file: {target.name}")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
