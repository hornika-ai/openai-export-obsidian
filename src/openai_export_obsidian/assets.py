from __future__ import annotations

import json
from typing import Any

from .archive import ExportArchive
from .models import AssetRecord, ConversationRecord
from .utils import basename, dat_name_for_file_id, extension_from_name, first_present, normalize_file_id, safe_filename


def load_asset_filename_map(archive: ExportArchive) -> dict[str, str]:
    result: dict[str, str] = {}
    members = archive.find_members(lambda m: basename(m.name) == "conversation_asset_file_names.json")
    for member in members:
        data = json.loads(archive.read_text(member))
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, str):
                    result[str(key)] = value
    return result


def load_library_files(archive: ExportArchive) -> dict[str, dict[str, Any]]:
    by_file_id: dict[str, dict[str, Any]] = {}
    members = archive.find_members(lambda m: basename(m.name) in {"libraryfiles.json", "library_files.json"})
    for member in members:
        data = json.loads(archive.read_text(member))
        rows = data if isinstance(data, list) else list(data.values()) if isinstance(data, dict) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            file_id = row.get("file_id") or row.get("id")
            if isinstance(file_id, dict):
                file_id = file_id.get("id")
            if isinstance(file_id, str):
                by_file_id[file_id] = row
    return by_file_id


def load_shared_conversations(archive: ExportArchive) -> dict[str, dict[str, Any]]:
    by_conversation: dict[str, dict[str, Any]] = {}
    members = archive.find_members(lambda m: basename(m.name) in {"sharedconversations.json", "shared_conversations.json"})
    for member in members:
        data = json.loads(archive.read_text(member))
        rows = data if isinstance(data, list) else list(data.values()) if isinstance(data, dict) else []
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("conversation_id"), str):
                by_conversation[row["conversation_id"]] = row
    return by_conversation


def build_asset_records(
    conversation: ConversationRecord,
    raw_conversation: dict[str, Any],
    asset_filename_map: dict[str, str],
    library_by_file_id: dict[str, dict[str, Any]],
) -> list[AssetRecord]:
    mapping = raw_conversation.get("mapping") if isinstance(raw_conversation.get("mapping"), dict) else {}
    records: list[AssetRecord] = []
    counter = 0
    for node_id in sorted(mapping):
        node = mapping.get(node_id) or {}
        message = node.get("message") if isinstance(node, dict) else None
        if not isinstance(message, dict):
            continue
        message_id = message.get("id")
        author_role = ((message.get("author") or {}).get("role") if isinstance(message.get("author"), dict) else None)
        metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        attachments = metadata.get("attachments")
        if isinstance(attachments, list):
            for index, attachment in enumerate(attachments):
                if isinstance(attachment, dict):
                    counter += 1
                    records.append(
                        normalize_reference(
                            counter,
                            conversation,
                            message_id,
                            author_role,
                            f"mapping.{node_id}.message.metadata.attachments[{index}]",
                            attachment,
                            attachment.get("id") or attachment.get("file_id"),
                            attachment.get("name") or attachment.get("filename"),
                            attachment.get("mime_type"),
                            attachment.get("size") or attachment.get("size_bytes"),
                            attachment.get("width"),
                            attachment.get("height"),
                            asset_filename_map,
                            library_by_file_id,
                        )
                    )
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        parts = content.get("parts")
        if isinstance(parts, list):
            for index, part in enumerate(parts):
                if isinstance(part, dict) and part.get("asset_pointer"):
                    counter += 1
                    records.append(
                        normalize_reference(
                            counter,
                            conversation,
                            message_id,
                            author_role,
                            f"mapping.{node_id}.message.content.parts[{index}].asset_pointer",
                            part,
                            part.get("asset_pointer"),
                            part.get("name") or part.get("filename"),
                            part.get("mime_type"),
                            part.get("size") or part.get("size_bytes"),
                            part.get("width"),
                            part.get("height"),
                            asset_filename_map,
                            library_by_file_id,
                        )
                    )
        for path, ref in find_file_references(metadata, f"mapping.{node_id}.message.metadata"):
            counter += 1
            records.append(
                normalize_reference(
                    counter,
                    conversation,
                    message_id,
                    author_role,
                    path,
                    ref,
                    ref.get("file_id") or ref.get("id") or ref.get("asset_pointer") or ref.get("url"),
                    ref.get("name") or ref.get("filename") or ref.get("title"),
                    ref.get("mime_type"),
                    ref.get("size") or ref.get("size_bytes"),
                    ref.get("width"),
                    ref.get("height"),
                    asset_filename_map,
                    library_by_file_id,
                )
            )
    return records


def find_file_references(value: Any, proof_path: str) -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(value, dict):
        keys = set(value)
        has_file_marker = bool(keys & {"file_id", "asset_pointer", "file_name"}) or (
            isinstance(value.get("id"), str) and value["id"].startswith(("file-", "file_"))
        )
        if has_file_marker and "attachments" not in proof_path:
            found.append((proof_path, value))
        for key, child in value.items():
            if key == "attachments":
                continue
            found.extend(find_file_references(child, f"{proof_path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(find_file_references(child, f"{proof_path}[{index}]"))
    return found


def normalize_reference(
    ordinal: int,
    conversation: ConversationRecord,
    message_id: str | None,
    author_role: str | None,
    proof_path: str,
    raw_reference: Any,
    raw_id_value: Any,
    explicit_name: str | None,
    mime_type: str | None,
    size: Any,
    width: Any,
    height: Any,
    asset_filename_map: dict[str, str],
    library_by_file_id: dict[str, dict[str, Any]],
) -> AssetRecord:
    raw_file_id = normalize_file_id(raw_id_value)
    raw_dat_filename = dat_name_for_file_id(raw_file_id) if raw_file_id and raw_file_id.startswith(("file-", "file_")) else None
    library = library_by_file_id.get(raw_file_id or "") or {}
    knowledge_store_id = extract_id(library.get("knowledge_store_id"))
    library_id = library.get("id")
    if isinstance(library_id, dict):
        library_id = library_id.get("id")

    mapped_name = asset_filename_map.get(raw_dat_filename or "") or asset_filename_map.get(raw_file_id or "")
    reconstructed_filename = first_present(mapped_name, explicit_name, library.get("file_name"), library.get("normalized_name"))
    provenance_status = "reconstructed" if mapped_name else "explicit" if explicit_name or library.get("file_name") else "unknown"
    if not reconstructed_filename and isinstance(raw_id_value, str) and "/" in raw_id_value:
        reconstructed_filename = basename(raw_id_value)
        provenance_status = "reconstructed"
    reconstructed_filename = safe_filename(reconstructed_filename, fallback=raw_dat_filename or raw_file_id or f"asset-{ordinal}")

    origin_classification = author_role if author_role in {"user", "assistant", "system"} else "unknown"
    origin_confidence = "explicit" if origin_classification != "unknown" else "unknown"
    file_role = classify_file_role(proof_path, origin_classification, library)

    return AssetRecord(
        asset_ref_id=f"{conversation.conversation_id}:{message_id or 'unknown-message'}:{ordinal:04d}",
        conversation_id=conversation.conversation_id,
        message_id=message_id,
        source_shard=conversation.source_json_shard,
        proof_path=proof_path,
        raw_file_id=raw_file_id,
        raw_dat_filename=raw_dat_filename,
        physical_archive_path=None,
        reconstructed_filename=reconstructed_filename,
        normalized_extension=extension_from_name(reconstructed_filename) or extension_from_name(raw_dat_filename),
        mime_type=first_present(mime_type, library.get("mime_type")),
        size=coerce_int(first_present(size, library.get("file_size_bytes"))),
        width=coerce_int(width),
        height=coerce_int(height),
        source_origin_fields={
            key: library.get(key)
            for key in (
                "content_backing_kind",
                "library_file_category",
                "created_at",
                "file_upload_time",
                "gizmo_id",
                "context_scopes",
                "context_scopes_v2",
                "knowledge_store_id",
            )
            if library.get(key) not in (None, "")
        },
        library_file_id=library_id if isinstance(library_id, str) else None,
        origination_message_id=library.get("origination_message_id"),
        origination_thread_id=library.get("origination_thread_id"),
        asset_status="missing",
        provenance_status=provenance_status,
        origin_classification=origin_classification,
        origin_confidence=origin_confidence,
        file_role=file_role,
        knowledge_store_id=knowledge_store_id,
        library_gizmo_id=library.get("gizmo_id"),
        image_gen_generation_id=library.get("image_gen_generation_id"),
        raw_reference=raw_reference,
    )


def build_library_origin_asset_records(
    conversation: ConversationRecord,
    library_by_file_id: dict[str, dict[str, Any]],
) -> list[AssetRecord]:
    """Expose library/Knowledge files at their declared conversation origin."""
    records: list[AssetRecord] = []
    known_message_ids = {message.message_id for message in conversation.messages if message.message_id}
    for ordinal, (file_id, library) in enumerate(sorted(library_by_file_id.items()), start=1):
        if extract_id(library.get("origination_thread_id")) != conversation.conversation_id:
            continue
        origination_message_id = extract_id(library.get("origination_message_id"))
        message_id = origination_message_id if origination_message_id in known_message_ids else nearest_library_message_id(conversation, library)
        raw_dat_filename = dat_name_for_file_id(file_id)
        filename = safe_filename(library.get("file_name") or library.get("normalized_name"), fallback=raw_dat_filename)
        generation_id = library.get("image_gen_generation_id")
        confidence = "explicit" if message_id == origination_message_id else "reconstructed_time_alignment"
        records.append(
            AssetRecord(
                asset_ref_id=f"{conversation.conversation_id}:{message_id or 'unknown-message'}:library-{ordinal:04d}",
                conversation_id=conversation.conversation_id,
                message_id=message_id,
                source_shard=conversation.source_json_shard,
                proof_path=f"library_files.json[file_id={file_id}]",
                raw_file_id=file_id,
                raw_dat_filename=raw_dat_filename,
                physical_archive_path=None,
                reconstructed_filename=filename,
                normalized_extension=extension_from_name(filename),
                mime_type=library.get("mime_type"),
                size=coerce_int(library.get("file_size_bytes")),
                width=None,
                height=None,
                source_origin_fields={"version_provenance": library.get("version_provenance")},
                library_file_id=extract_id(library.get("id")),
                origination_message_id=origination_message_id,
                origination_thread_id=conversation.conversation_id,
                asset_status="missing",
                provenance_status="explicit",
                origin_classification="assistant" if generation_id else "unknown",
                origin_confidence=confidence,
                file_role="knowledge" if library_has_knowledge_context(library) else "library",
                knowledge_store_id=extract_id(library.get("knowledge_store_id")),
                library_gizmo_id=extract_id(library.get("gizmo_id")),
                image_gen_generation_id=generation_id if isinstance(generation_id, str) else None,
                raw_reference=library,
            )
        )
    return records


def nearest_library_message_id(conversation: ConversationRecord, library: dict[str, Any]) -> str | None:
    created_at = library.get("created_at")
    if not isinstance(created_at, str):
        return None
    try:
        from datetime import datetime
        target = datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
    candidates: list[tuple[float, str]] = []
    for message in conversation.messages:
        if message.author_role != "assistant" or not message.message_id or not message.create_time:
            continue
        try:
            timestamp = datetime.fromisoformat(message.create_time.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
        distance = abs(timestamp - target)
        if distance <= 120:
            candidates.append((distance, message.message_id))
    return min(candidates)[1] if candidates else None


def extract_id(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        inner = value.get("id")
        return inner if isinstance(inner, str) else None
    return None


def library_has_knowledge_context(library: dict[str, Any]) -> bool:
    return any(
        library.get(key) not in (None, "", [], {})
        for key in ("knowledge_store_id", "context_scopes", "context_scopes_v2", "gizmo_id")
    )


def classify_file_role(proof_path: str, origin_classification: str, library: dict[str, Any]) -> str:
    if library_has_knowledge_context(library):
        return "knowledge"
    if origin_classification == "assistant" and (
        "content.parts" in proof_path or library.get("image_gen_generation_id")
    ):
        return "generated"
    return "attachment"


def coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
