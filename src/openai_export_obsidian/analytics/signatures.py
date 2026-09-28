from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


SIGNATURE_FIELDS = (
    "conversation_id",
    "updated_at",
    "message_count",
    "file_reference_count",
    "unique_file_count",
    "resolved_file_count",
    "unresolved_file_count",
    "source_evidence_count",
    "tool_evidence_count",
    "context_evidence",
    "default_model",
    "models_seen",
    "gpts",
    "space_projects",
    "knowledge_stores",
    "is_archived",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def conversation_signature(row: dict[str, Any]) -> str:
    payload: dict[str, Any] = {}
    for key in SIGNATURE_FIELDS:
        value = row.get(key)
        payload[key] = sorted(value) if key in {"models_seen", "gpts", "space_projects", "knowledge_stores"} else value
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(encoded)
