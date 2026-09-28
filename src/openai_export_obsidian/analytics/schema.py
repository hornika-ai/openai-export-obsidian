from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..evidence_query import CONVERSATION_QUERY_FIELDS
from . import ANALYTICS_SCHEMA, REVIEW_SCHEMA


DASHBOARD_FIELDS = frozenset({"source", "quality", "coverage", "monthly", "distributions", "conversations"})
SOURCE_FIELDS = frozenset({"pack_schema", "manifest_sha256", "conversations_sha256", "locator_sha256", "conversation_count", "pack_vault_path"})
QUALITY_FIELDS = frozenset({
    "conversation_count", "message_count", "file_reference_count", "source_evidence_count", "tool_evidence_count",
    "needs_review_count", "has_warnings_count", "unknown_context_count", "unresolved_conversation_count",
    "unresolved_file_count", "locator_unavailable_count", "file_density", "source_density", "tool_density", "diagnostics",
})
COVERAGE_FIELDS = frozenset({
    "context_known_ratio", "files_resolved_ratio", "conversations_with_sources_ratio",
    "conversations_with_tools_ratio", "locator_ratio", "models", "projects", "gpts", "knowledge_stores", "archive",
})
MONTHLY_FIELDS = frozenset({
    "month_key", "conversation_count", "message_count", "needs_review_count", "unresolved_file_count",
    "source_evidence_count", "tool_evidence_count",
})
DISTRIBUTION_FIELDS = frozenset({"count", "min", "max", "mean", "p25", "p50", "p75", "p90", "p95"})
DISTRIBUTION_METRICS = frozenset({"message_count", "file_reference_count", "unresolved_file_count", "source_evidence_count", "tool_evidence_count"})
CACHE_CONVERSATION_FIELDS = frozenset((*CONVERSATION_QUERY_FIELDS, "source_signature"))
BUILD_MANIFEST_FIELDS = frozenset({
    "package_version", "analytics_schema", "template_version", "source_identity", "row_count", "dashboard_sha256", "warnings",
})
REVIEW_RECORD_FIELDS = frozenset({"status", "status_updated_at", "source_signature"})
REVIEW_STATUSES = frozenset({"todo", "in_progress", "reviewed", "ignored"})
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def validate_dashboard(value: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict) or set(value) != DASHBOARD_FIELDS:
        return ["dashboard root does not match the closed analytics schema"]
    _exact_object(value.get("source"), SOURCE_FIELDS, "dashboard.source", errors)
    _exact_object(value.get("quality"), QUALITY_FIELDS, "dashboard.quality", errors)
    _exact_object(value.get("coverage"), COVERAGE_FIELDS, "dashboard.coverage", errors)
    _validate_source(value.get("source"), "dashboard.source", errors)
    quality = value.get("quality")
    if isinstance(quality, dict):
        for key in QUALITY_FIELDS - {"diagnostics", "file_density", "source_density", "tool_density"}:
            _non_negative_integer(quality.get(key), f"dashboard.quality.{key}", errors)
        for key in ("file_density", "source_density", "tool_density"):
            _number(quality.get(key), f"dashboard.quality.{key}", errors)
        if not isinstance(quality.get("diagnostics"), list) or not all(isinstance(item, str) for item in quality["diagnostics"]):
            errors.append("dashboard.quality.diagnostics must be a string array")
    coverage = value.get("coverage")
    if isinstance(coverage, dict):
        for key in ("context_known_ratio", "files_resolved_ratio", "conversations_with_sources_ratio", "conversations_with_tools_ratio", "locator_ratio"):
            _number(coverage.get(key), f"dashboard.coverage.{key}", errors)
        for key in ("models", "projects", "gpts", "knowledge_stores", "archive"):
            mapping = coverage.get(key)
            if not isinstance(mapping, dict) or not all(isinstance(name, str) and _is_non_negative_integer(count) for name, count in mapping.items()):
                errors.append(f"dashboard.coverage.{key} must map strings to non-negative integers")
    monthly = value.get("monthly")
    if not isinstance(monthly, list):
        errors.append("dashboard.monthly must be an array")
    else:
        for index, row in enumerate(monthly):
            _exact_object(row, MONTHLY_FIELDS, f"dashboard.monthly[{index}]", errors)
            if isinstance(row, dict):
                if not isinstance(row.get("month_key"), str):
                    errors.append(f"dashboard.monthly[{index}].month_key must be a string")
                for key in MONTHLY_FIELDS - {"month_key"}:
                    _non_negative_integer(row.get(key), f"dashboard.monthly[{index}].{key}", errors)
    distributions = value.get("distributions")
    if not isinstance(distributions, dict) or set(distributions) != DISTRIBUTION_METRICS:
        errors.append("dashboard.distributions does not match the closed metric set")
    else:
        for metric, row in distributions.items():
            _exact_object(row, DISTRIBUTION_FIELDS, f"dashboard.distributions.{metric}", errors)
            if isinstance(row, dict):
                for key in DISTRIBUTION_FIELDS:
                    _number(row.get(key), f"dashboard.distributions.{metric}.{key}", errors)
    conversations = value.get("conversations")
    if not isinstance(conversations, list):
        errors.append("dashboard.conversations must be an array")
    else:
        for index, row in enumerate(conversations):
            _exact_object(row, CACHE_CONVERSATION_FIELDS, f"dashboard.conversations[{index}]", errors)
            if isinstance(row, dict) and not _is_sha256(row.get("source_signature")):
                errors.append(f"dashboard.conversations[{index}].source_signature must be a SHA-256")
    if isinstance(value.get("source"), dict) and isinstance(conversations, list):
        if value["source"].get("conversation_count") != len(conversations):
            errors.append("dashboard source conversation_count does not match conversations")
    return errors


def validate_build_manifest(value: Any, dashboard_bytes: bytes | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict) or set(value) != BUILD_MANIFEST_FIELDS:
        return ["build manifest does not match the closed schema"]
    _exact_object(value.get("source_identity"), SOURCE_FIELDS, "build_manifest.source_identity", errors)
    _validate_source(value.get("source_identity"), "build_manifest.source_identity", errors)
    if value.get("analytics_schema") != ANALYTICS_SCHEMA:
        errors.append("build manifest analytics schema is incompatible")
    if not isinstance(value.get("row_count"), int) or isinstance(value.get("row_count"), bool) or value["row_count"] < 0:
        errors.append("build manifest row_count must be a non-negative integer")
    if not isinstance(value.get("warnings"), list) or not all(isinstance(item, str) for item in value["warnings"]):
        errors.append("build manifest warnings must be a string array")
    for key in ("package_version", "template_version"):
        if not isinstance(value.get(key), str) or not value[key]:
            errors.append(f"build manifest {key} must be a non-empty string")
    if not _is_sha256(value.get("dashboard_sha256")):
        errors.append("build manifest dashboard_sha256 must be a SHA-256")
    if dashboard_bytes is not None:
        from .signatures import sha256_bytes

        if value.get("dashboard_sha256") != sha256_bytes(dashboard_bytes):
            errors.append("build manifest dashboard hash does not match dashboard.json")
    return errors


def validate_review_state(value: Any) -> list[str]:
    if not isinstance(value, dict) or set(value) != {"version", "records"}:
        return ["review state does not match the closed schema"]
    errors: list[str] = []
    if value.get("version") != REVIEW_SCHEMA:
        errors.append("review state version is incompatible")
    records = value.get("records")
    if not isinstance(records, dict):
        return [*errors, "review state records must be an object"]
    for conversation_id, record in records.items():
        if not isinstance(conversation_id, str) or not conversation_id:
            errors.append("review state has an invalid conversation id")
            continue
        if not isinstance(record, dict) or set(record) != REVIEW_RECORD_FIELDS:
            errors.append(f"review record {conversation_id} does not match the closed schema")
            continue
        if record.get("status") not in REVIEW_STATUSES:
            errors.append(f"review record {conversation_id} has an invalid status")
        if not _is_iso_datetime(record.get("status_updated_at")):
            errors.append(f"review record {conversation_id} has an invalid timestamp")
        if not _is_sha256(record.get("source_signature")):
            errors.append(f"review record {conversation_id} has an invalid source signature")
    return errors


def _exact_object(value: Any, fields: frozenset[str], label: str, errors: list[str]) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        errors.append(f"{label} does not match the closed schema")


def _validate_source(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        return
    for key in ("pack_schema", "pack_vault_path"):
        if not isinstance(value.get(key), str) or not value[key]:
            errors.append(f"{label}.{key} must be a non-empty string")
    for key in ("manifest_sha256", "conversations_sha256"):
        if not _is_sha256(value.get(key)):
            errors.append(f"{label}.{key} must be a SHA-256")
    if value.get("locator_sha256") is not None and not _is_sha256(value.get("locator_sha256")):
        errors.append(f"{label}.locator_sha256 must be a SHA-256 or null")
    _non_negative_integer(value.get("conversation_count"), f"{label}.conversation_count", errors)


def _is_non_negative_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _non_negative_integer(value: Any, label: str, errors: list[str]) -> None:
    if not _is_non_negative_integer(value):
        errors.append(f"{label} must be a non-negative integer")


def _number(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        errors.append(f"{label} must be a number")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_RE.fullmatch(value))


def _is_iso_datetime(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True
