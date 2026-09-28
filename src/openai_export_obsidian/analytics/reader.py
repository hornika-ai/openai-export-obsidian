from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

from ..conversation_note_locator import (
    CONVERSATION_NOTE_LOCATOR_RELATIVE_PATH,
    LOCATOR_RECORD_FIELDS,
    locator_path_error,
)
from ..evidence_query import EvidenceQueryError, projected_conversations
from ..validation import validate_pack
from .paths import AnalyticsPaths
from .signatures import conversation_signature, sha256_file


class AnalyticsReadError(ValueError):
    pass


@dataclass(frozen=True)
class PackAnalytics:
    source_identity: dict[str, Any]
    conversations: list[dict[str, Any]]
    warnings: list[str]


def read_pack_analytics(paths: AnalyticsPaths) -> PackAnalytics:
    report = validate_pack(paths.pack)
    navigation = _navigation_drift(paths.pack, report["errors"])
    if navigation.fatal_errors:
        raise AnalyticsReadError("pack validation failed: " + "; ".join(navigation.fatal_errors))
    try:
        conversations = projected_conversations(paths.pack, validate=False)
    except EvidenceQueryError as exc:
        raise AnalyticsReadError(str(exc)) from exc
    conversations = [_without_unavailable_note_path(paths.pack, row) for row in conversations]

    evidence = paths.pack / "90_Evidence"
    manifest_path = evidence / "pack_manifest.json"
    manifest = _read_object(manifest_path)
    descriptor = ((manifest.get("navigation") or {}).get("conversation_note_locator") or {})
    locator_path = paths.pack / CONVERSATION_NOTE_LOCATOR_RELATIVE_PATH
    locator_hash = sha256_file(locator_path) if descriptor.get("state") == "emitted" else None
    source_identity = {
        "pack_schema": manifest.get("schema_version"),
        "manifest_sha256": sha256_file(manifest_path),
        "conversations_sha256": sha256_file(evidence / "conversations.jsonl"),
        "locator_sha256": locator_hash,
        "conversation_count": len(conversations),
        "pack_vault_path": paths.pack_vault_path,
    }
    signed = [dict(row, source_signature=conversation_signature(row)) for row in conversations]
    return PackAnalytics(
        source_identity=source_identity,
        conversations=signed,
        warnings=sorted({*report["warnings"], *navigation.warnings}),
    )


@dataclass(frozen=True)
class NavigationDrift:
    fatal_errors: list[str]
    warnings: list[str]


def _navigation_drift(pack: Path, validation_errors: list[str]) -> NavigationDrift:
    """Downgrade only verified post-production Markdown location drift.

    The producer validator remains strict.  Analytics independently confirms
    that each downgraded error is caused by a locator target that is now absent
    or by extra/moved Markdown below ``10_Conversations``.  It never follows a
    symlink, reads another note to discover a replacement, or changes a locator.
    """
    if not validation_errors:
        return NavigationDrift(fatal_errors=[], warnings=[])
    try:
        locator_rows = _read_locator_rows(pack)
    except AnalyticsReadError:
        return NavigationDrift(fatal_errors=list(validation_errors), warnings=[])

    expected_missing_errors: set[str] = set()
    missing_count = 0
    locator_paths: set[str] = set()
    for index, row in enumerate(locator_rows, start=1):
        relative_path = row["relative_note_path"]
        locator_paths.add(relative_path)
        target = pack.joinpath(*PurePosixPath(relative_path).parts)
        if not target.exists() and not target.is_symlink():
            expected_missing_errors.add(
                f"conversation note locator:{index} target missing or not a regular file: {relative_path}"
            )
            missing_count += 1

    notes_root = pack / "10_Conversations"
    emitted_paths = {
        path.relative_to(pack).as_posix()
        for path in notes_root.rglob("*.md")
        if path.is_file() and not path.is_symlink()
    } if notes_root.is_dir() and not notes_root.is_symlink() else set()
    note_set_drift = locator_paths != emitted_paths
    set_mismatch_error = "conversation note locator does not exactly match emitted conversation notes"

    tolerated = set(expected_missing_errors)
    if note_set_drift:
        tolerated.add(set_mismatch_error)
    fatal_errors = [error for error in validation_errors if error not in tolerated]
    warnings: list[str] = []
    if missing_count:
        warnings.append(f"navigation_target_missing:count={missing_count}")
    if note_set_drift:
        warnings.append("navigation_note_set_drift")
    return NavigationDrift(fatal_errors=fatal_errors, warnings=warnings if not fatal_errors else [])


def _read_locator_rows(pack: Path) -> list[dict[str, str]]:
    path = pack / CONVERSATION_NOTE_LOCATOR_RELATIVE_PATH
    rows: list[dict[str, str]] = []
    try:
        stream = path.open("r", encoding="utf-8")
    except OSError as exc:
        raise AnalyticsReadError("cannot read conversation locator") from exc
    with stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AnalyticsReadError("conversation locator is invalid") from exc
            if not isinstance(value, dict) or set(value) != LOCATOR_RECORD_FIELDS:
                raise AnalyticsReadError("conversation locator is invalid")
            conversation_id = value.get("conversation_id")
            relative_path = value.get("relative_note_path")
            if not isinstance(conversation_id, str) or not conversation_id or locator_path_error(relative_path):
                raise AnalyticsReadError(f"conversation locator:{line_number} is invalid")
            assert isinstance(relative_path, str)
            rows.append({"conversation_id": conversation_id, "relative_note_path": relative_path})
    return rows


def _without_unavailable_note_path(pack: Path, row: dict[str, Any]) -> dict[str, Any]:
    note_path = row.get("note_path")
    if not isinstance(note_path, str):
        return row
    target = pack.joinpath(*PurePosixPath(note_path).parts)
    if target.is_symlink() or not target.is_file():
        return dict(row, note_path=None)
    return row


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalyticsReadError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise AnalyticsReadError(f"{path.name} must contain an object")
    return value
