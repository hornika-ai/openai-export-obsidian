from __future__ import annotations

"""Typed reference extraction without physical asset resolution.

The extractor consumes raw conversation metadata together with an already-built
``ExportInventory``. It never opens archives, chooses candidates, or reports a
physical payload as found/missing. Those decisions belong to Physical Resolution.
"""

import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from .inventory import ExportInventory
from .models import ConversationRecord


URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+")
FILE_SERVICE_RE = re.compile(r"file-service://[^\s<>()\[\]{}\"']+")
SEDIMENT_RE = re.compile(r"sediment://[^\s<>()\[\]{}\"']+")
SANDBOX_RE = re.compile(r"sandbox:(?:/+)?/mnt/data/[^\s<>()\[\]{}\"']+")
MNT_DATA_RE = re.compile(r"(?<![\w:/])(/mnt/data/[^\s<>()\[\]{}\"']+)")
DATA_URI_RE = re.compile(r"data:[A-Za-z0-9.+/-]+(?:;[A-Za-z0-9=._-]+)*,[^\s<>()\[\]{}\"']+")
CANMORE_RE = re.compile(r"canmore://[^\s<>()\[\]{}\"']+")
FILE_IDENTIFIER_RE = re.compile(r"(?<![A-Za-z0-9_-])((?:file-|file_)[A-Za-z0-9_-]+(?:\.dat)?)(?![A-Za-z0-9_-])")


KEY_REFERENCE_KINDS = {
    "asset_pointer": ("asset_pointer", "identifier"),
    "file_id": ("file_identifier", "identifier"),
    "original_file_id": ("original_file_identifier", "identifier"),
    "mask_file_id": ("mask_file_identifier", "identifier"),
    "original_gen_id": ("generation_identifier", "identifier"),
    "gen_id": ("generation_identifier", "identifier"),
    "image_gen_generation_id": ("image_generation_identifier", "identifier"),
    "image_send_uuid": ("image_send_identifier", "identifier"),
    "image_prompt_id": ("image_prompt_identifier", "identifier"),
    "canmore_uri": ("canmore_uri", "uri"),
    "url": ("external_url", "url"),
    "raw_url": ("external_url", "url"),
    "source_url": ("external_url", "url"),
    "file_name": ("file_name_hint", "name_hint"),
    "filename": ("file_name_hint", "name_hint"),
}


@dataclass
class ReferenceRecord:
    reference_id: str
    scope: str
    reference_kind: str
    value_kind: str
    conversation_id: str | None
    message_id: str | None
    node_id: str | None
    source_shard: str | None
    proof_path: str
    metadata_key: str | None
    raw_identifier: str
    normalized_identifier: str | None
    namespace: str | None
    logical_name: str | None
    declared_mime_type: str | None
    declared_size: int | None
    inventory_source_archive: str
    inventory_status: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class ReferenceExtractionResult:
    records: list[ReferenceRecord]

    def summary_dict(self) -> dict[str, object]:
        return {
            "counts": {
                "references": len(self.records),
                "conversations": len({row.conversation_id for row in self.records if row.conversation_id}),
                "messages": len(
                    {(row.conversation_id, row.message_id) for row in self.records if row.message_id}
                ),
            },
            "reference_kinds": dict(sorted(Counter(row.reference_kind for row in self.records).items())),
            "value_kinds": dict(sorted(Counter(row.value_kind for row in self.records).items())),
            "namespaces": dict(sorted(Counter(row.namespace or "unknown" for row in self.records).items())),
            "scopes": dict(sorted(Counter(row.scope for row in self.records).items())),
        }


class ReferenceExtractor:
    """Normalize source references while deliberately avoiding resolution."""

    def __init__(self, inventory: ExportInventory):
        self.inventory = inventory

    def extract_conversation(
        self,
        conversation: ConversationRecord,
        raw_conversation: dict[str, Any],
    ) -> list[ReferenceRecord]:
        collector = _ReferenceCollector(self.inventory, conversation, scope="conversation")
        mapping = raw_conversation.get("mapping") if isinstance(raw_conversation.get("mapping"), dict) else {}

        conversation_metadata = {key: value for key, value in raw_conversation.items() if key != "mapping"}
        collector.walk(
            conversation_metadata,
            proof_path="conversation",
            message_id=None,
            node_id=None,
            logical_name=None,
            declared_mime_type=None,
            declared_size=None,
        )

        for node_id in sorted(mapping):
            node = mapping.get(node_id)
            message = node.get("message") if isinstance(node, dict) and isinstance(node.get("message"), dict) else None
            if not message:
                continue
            message_id = _string_or_none(message.get("id"))
            message_path = f"mapping.{node_id}.message"
            metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
            attachments = metadata.get("attachments")
            if isinstance(attachments, list):
                for index, attachment in enumerate(attachments):
                    if not isinstance(attachment, dict):
                        continue
                    attachment_path = f"{message_path}.metadata.attachments[{index}]"
                    collector.add(
                        reference_kind="attachment_file_identifier",
                        value_kind="identifier",
                        raw_identifier=_first_string(attachment.get("id"), attachment.get("file_id")),
                        proof_path=attachment_path,
                        metadata_key="id" if isinstance(attachment.get("id"), str) else "file_id",
                        message_id=message_id,
                        node_id=node_id,
                        logical_name=_first_string(attachment.get("name"), attachment.get("filename")),
                        declared_mime_type=_string_or_none(attachment.get("mime_type")),
                        declared_size=_integer_or_none(attachment.get("size") or attachment.get("size_bytes")),
                    )

            content = message.get("content") if isinstance(message.get("content"), dict) else {}
            collector.walk(
                content,
                proof_path=f"{message_path}.content",
                message_id=message_id,
                node_id=node_id,
                logical_name=None,
                declared_mime_type=None,
                declared_size=None,
            )
            collector.walk(
                {key: value for key, value in metadata.items() if key != "attachments"},
                proof_path=f"{message_path}.metadata",
                message_id=message_id,
                node_id=node_id,
                logical_name=None,
                declared_mime_type=None,
                declared_size=None,
            )
        return collector.records

    def extract_library_metadata(
        self,
        library_by_file_id: dict[str, dict[str, Any]],
        *,
        selected_conversation_ids: set[str] | None = None,
        selected_gpt_ids: set[str] | None = None,
    ) -> list[ReferenceRecord]:
        collector = _ReferenceCollector(self.inventory, None, scope="library_metadata")
        source_entry = next(
            (entry for entry in self.inventory.entries if entry.family == "library_files"),
            None,
        )
        source_path = source_entry.archive_path if source_entry else "library_files.json"
        for file_id, row in sorted(library_by_file_id.items()):
            conversation_id = _first_string(
                _extract_scalar_id(row.get("origination_thread_id")),
                _extract_scalar_id(row.get("initiating_conversation_id")),
                _extract_scalar_id(row.get("conversation_id")),
            )
            if not _library_row_matches_scope(
                row,
                conversation_id=conversation_id,
                selected_conversation_ids=selected_conversation_ids,
                selected_gpt_ids=selected_gpt_ids,
            ):
                continue
            collector.conversation_id = conversation_id
            collector.source_shard = source_path
            collector.walk(
                row,
                proof_path=f"{source_path}[file_id={file_id}]",
                message_id=_extract_scalar_id(row.get("origination_message_id")),
                node_id=None,
                logical_name=_first_string(row.get("file_name"), row.get("normalized_name")),
                declared_mime_type=_string_or_none(row.get("mime_type")),
                declared_size=_integer_or_none(row.get("file_size_bytes")),
            )
        return collector.records

    @staticmethod
    def result(records: list[ReferenceRecord]) -> ReferenceExtractionResult:
        return ReferenceExtractionResult(records=records)


class _ReferenceCollector:
    def __init__(self, inventory: ExportInventory, conversation: ConversationRecord | None, *, scope: str):
        self.inventory = inventory
        self.scope = scope
        self.conversation_id = conversation.conversation_id if conversation else None
        self.source_shard = conversation.source_json_shard if conversation else None
        self.records: list[ReferenceRecord] = []
        self.seen: set[tuple[str, str]] = set()
        self.ordinal = 0

    def walk(
        self,
        value: Any,
        *,
        proof_path: str,
        message_id: str | None,
        node_id: str | None,
        logical_name: str | None,
        declared_mime_type: str | None,
        declared_size: int | None,
        content_types: tuple[str, ...] = (),
    ) -> None:
        if isinstance(value, dict):
            content_type = _string_or_none(value.get("content_type"))
            next_content_types = (*content_types, content_type) if content_type else content_types
            local_name = _first_string(value.get("name"), value.get("filename"), value.get("file_name")) or logical_name
            local_mime = _string_or_none(value.get("mime_type")) or declared_mime_type
            local_size = _integer_or_none(value.get("size") or value.get("size_bytes")) or declared_size
            for key in sorted(value):
                child = value[key]
                child_path = f"{proof_path}.{key}"
                if key in KEY_REFERENCE_KINDS and isinstance(child, str):
                    reference_kind, value_kind = KEY_REFERENCE_KINDS[key]
                    if key == "asset_pointer":
                        reference_kind = _asset_pointer_kind(child_path, next_content_types)
                    self.add(
                        reference_kind=reference_kind,
                        value_kind=value_kind,
                        raw_identifier=child,
                        proof_path=child_path,
                        metadata_key=key,
                        message_id=message_id,
                        node_id=node_id,
                        logical_name=local_name,
                        declared_mime_type=local_mime,
                        declared_size=local_size,
                    )
                elif key == "id" and isinstance(child, str) and _is_file_like_identifier(child):
                    self.add(
                        reference_kind="file_identifier",
                        value_kind="identifier",
                        raw_identifier=child,
                        proof_path=child_path,
                        metadata_key=key,
                        message_id=message_id,
                        node_id=node_id,
                        logical_name=local_name,
                        declared_mime_type=local_mime,
                        declared_size=local_size,
                    )
                self.walk(
                    child,
                    proof_path=child_path,
                    message_id=message_id,
                    node_id=node_id,
                    logical_name=local_name,
                    declared_mime_type=local_mime,
                    declared_size=local_size,
                    content_types=next_content_types,
                )
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                self.walk(
                    child,
                    proof_path=f"{proof_path}[{index}]",
                    message_id=message_id,
                    node_id=node_id,
                    logical_name=logical_name,
                    declared_mime_type=declared_mime_type,
                    declared_size=declared_size,
                    content_types=content_types,
                )
            return
        if isinstance(value, str):
            for reference_kind, value_kind, raw_identifier in _inline_references(value):
                self.add(
                    reference_kind=reference_kind,
                    value_kind=value_kind,
                    raw_identifier=raw_identifier,
                    proof_path=proof_path,
                    metadata_key=None,
                    message_id=message_id,
                    node_id=node_id,
                    logical_name=logical_name,
                    declared_mime_type=declared_mime_type,
                    declared_size=declared_size,
                )

    def add(
        self,
        *,
        reference_kind: str,
        value_kind: str,
        raw_identifier: str | None,
        proof_path: str,
        metadata_key: str | None,
        message_id: str | None,
        node_id: str | None,
        logical_name: str | None,
        declared_mime_type: str | None,
        declared_size: int | None,
    ) -> None:
        if not raw_identifier:
            return
        raw_identifier = raw_identifier.strip()
        if not raw_identifier:
            return
        key = (proof_path, raw_identifier)
        if key in self.seen:
            return
        self.seen.add(key)
        self.ordinal += 1
        normalized_identifier, namespace = normalize_identifier(raw_identifier)
        owner = self.conversation_id or "global"
        location = node_id or "metadata"
        self.records.append(
            ReferenceRecord(
                reference_id=f"{self.scope}:{owner}:{location}:reference-{self.ordinal:04d}",
                scope=self.scope,
                reference_kind=reference_kind,
                value_kind=value_kind,
                conversation_id=self.conversation_id,
                message_id=message_id,
                node_id=node_id,
                source_shard=self.source_shard,
                proof_path=proof_path,
                metadata_key=metadata_key,
                raw_identifier=raw_identifier,
                normalized_identifier=normalized_identifier,
                namespace=namespace,
                logical_name=logical_name,
                declared_mime_type=declared_mime_type,
                declared_size=declared_size,
                inventory_source_archive=self.inventory.source_archive,
                inventory_status=self.inventory.source_status,
            )
        )


def normalize_identifier(raw_identifier: str) -> tuple[str | None, str | None]:
    raw = raw_identifier.strip()
    namespace = namespace_for(raw)
    if raw.startswith("file-service://"):
        normalized = raw[len("file-service://") :]
    elif raw.startswith("sediment://"):
        normalized = raw[len("sediment://") :]
    elif raw.startswith("sandbox:"):
        normalized = raw[len("sandbox:") :]
    elif namespace in {"data-uri", "external-url", "canmore"}:
        normalized = None
    else:
        normalized = raw
    if normalized and normalized.endswith(".dat") and _is_file_like_identifier(normalized[:-4]):
        normalized = normalized[:-4]
    return normalized or None, namespace


def namespace_for(raw_identifier: str) -> str | None:
    if raw_identifier.startswith("file-service://"):
        return "file-service"
    if raw_identifier.startswith("sediment://"):
        return "sediment"
    if raw_identifier.startswith("sandbox:"):
        return "sandbox"
    if raw_identifier.startswith("data:"):
        return "data-uri"
    if raw_identifier.startswith("canmore://"):
        return "canmore"
    if raw_identifier.startswith(("http://", "https://")):
        return "external-url"
    if "/mnt/data/" in raw_identifier:
        return "mnt/data"
    if _is_file_like_identifier(raw_identifier):
        return "file"
    return None


def _inline_references(value: str) -> list[tuple[str, str, str]]:
    matches: list[tuple[int, int, str, str, str]] = []
    patterns = (
        ("file_service_identifier", "identifier", FILE_SERVICE_RE),
        ("sediment_identifier", "identifier", SEDIMENT_RE),
        ("sandbox_path", "path", SANDBOX_RE),
        ("mnt_data_path", "path", MNT_DATA_RE),
        ("data_uri", "data_uri", DATA_URI_RE),
        ("canmore_uri", "uri", CANMORE_RE),
        ("external_url", "url", URL_RE),
        ("file_identifier", "identifier", FILE_IDENTIFIER_RE),
    )
    for reference_kind, value_kind, pattern in patterns:
        for match in pattern.finditer(value):
            raw = match.group(1) if match.lastindex else match.group(0)
            if reference_kind == "file_identifier" and raw.casefold() == "file-service":
                continue
            if reference_kind == "file_identifier" and any(
                start <= match.start() and match.end() <= end
                and prior_kind in {"file_service_identifier", "sediment_identifier"}
                for start, end, prior_kind, _, _ in matches
            ):
                continue
            matches.append((match.start(), match.end(), reference_kind, value_kind, raw.rstrip(".,;:!?")))
    return [(kind, value_kind, raw) for _, _, kind, value_kind, raw in sorted(set(matches))]


def _asset_pointer_kind(proof_path: str, content_types: tuple[str, ...]) -> str:
    if "real_time_user_audio_video_asset_pointer" in content_types:
        if ".video_container_asset_pointer." in proof_path:
            return "realtime_video_asset_pointer"
        if ".frames_asset_pointers" in proof_path:
            return "realtime_frame_asset_pointer"
        return "realtime_audio_asset_pointer"
    if "image_asset_pointer" in content_types:
        return "image_asset_pointer"
    if "audio_asset_pointer" in content_types:
        return "audio_asset_pointer"
    return "asset_pointer"


def _is_file_like_identifier(value: str) -> bool:
    return value.startswith(("file-", "file_"))


def _first_string(*values: Any) -> str | None:
    return next((value for value in values if isinstance(value, str) and value), None)


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _integer_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _extract_scalar_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("id"), str):
        return value["id"]
    return None


def _library_row_matches_scope(
    row: dict[str, Any],
    *,
    conversation_id: str | None,
    selected_conversation_ids: set[str] | None,
    selected_gpt_ids: set[str] | None,
) -> bool:
    """Keep Library evidence in the same conversation/GPT scope as its copy.

    Library rows can be attached to a custom GPT without an originating
    conversation.  A filtered export must retain their direct file-ID evidence,
    otherwise the copy stage has no Physical Resolution record to consume.
    """
    if selected_conversation_ids is None and selected_gpt_ids is None:
        return True
    if selected_conversation_ids and conversation_id in selected_conversation_ids:
        return True
    gizmo_id = _extract_scalar_id(row.get("gizmo_id"))
    return bool(selected_gpt_ids and gizmo_id in selected_gpt_ids)
