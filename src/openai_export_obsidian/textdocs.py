from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from .models import ConversationRecord, MessageRecord, TextdocRecord
from .utils import extension_from_name, safe_filename, short_identifier, unique_filename, year_month


CANMORE_URI_RE = re.compile(r"canmore://(?:textdoc/)?([A-Za-z0-9:_-]+)")


def build_textdoc_records(conversation: ConversationRecord) -> list[TextdocRecord]:
    records: list[TextdocRecord] = []
    seen_canmore_uris: set[tuple[str | None, str]] = set()
    for message in conversation.all_messages:
        metadata = message.raw_metadata if isinstance(message.raw_metadata, dict) else {}
        records.extend(_canvas_payload_records(conversation, message, metadata))
        records.extend(_open_canvas_reference_records(conversation, message, metadata))
        for uri_record in _canmore_uri_records(conversation, message):
            key = (uri_record.message_id, uri_record.canmore_uri or "")
            if key in seen_canmore_uris:
                continue
            seen_canmore_uris.add(key)
            records.append(uri_record)
    return records


def materialize_textdoc_records(
    output_path: Path,
    conversation: ConversationRecord,
    records: list[TextdocRecord],
    used_filenames_by_folder: dict[str, set[str]],
    *,
    dry_run: bool,
) -> None:
    year, month, _ = year_month(conversation.create_time)
    folder = Path("20_Files") / "Textdocs" / year / month
    folder_key = str(folder)
    used = used_filenames_by_folder[folder_key]
    for record in records:
        content = getattr(record, "_content", None)
        if record.status != "exported_payload" or not isinstance(content, str):
            continue
        filename = unique_filename(_textdoc_filename(record), used)
        record.copied_filename = filename
        record.copied_pack_path = str(folder / filename)
        if not dry_run:
            target = output_path / record.copied_pack_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        delattr(record, "_content")


def textdoc_annotations_from_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_message: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        message_id = row.get("message_id")
        if message_id:
            by_message[str(message_id)].append(row)
    return by_message


def _canvas_payload_records(
    conversation: ConversationRecord,
    message: MessageRecord,
    metadata: dict[str, Any],
) -> list[TextdocRecord]:
    canvas = metadata.get("canvas")
    if not isinstance(canvas, dict):
        return []
    textdocs = canvas.get("user_created_textdocs")
    if not isinstance(textdocs, list):
        return []
    records: list[TextdocRecord] = []
    for index, textdoc in enumerate(textdocs):
        if not isinstance(textdoc, dict):
            continue
        content = textdoc.get("content")
        textdoc_id = _string_or_none(textdoc.get("textdoc_id") or textdoc.get("id"))
        title = _string_or_none(textdoc.get("title") or textdoc.get("name") or textdoc_id)
        textdoc_type = _string_or_none(textdoc.get("type") or textdoc.get("textdoc_type"))
        proof_path = f"mapping.{message.node_id}.message.metadata.canvas.user_created_textdocs[{index}]"
        if isinstance(content, str):
            record = TextdocRecord(
                textdoc_ref_id=_textdoc_ref_id(conversation, message, "canvas", index, textdoc_id),
                conversation_id=conversation.conversation_id,
                message_id=message.message_id,
                node_id=message.node_id,
                status="exported_payload",
                textdoc_id=textdoc_id,
                title=title,
                textdoc_type=textdoc_type,
                content_length=len(content),
                content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                copied_pack_path=None,
                proof_path=proof_path,
                source_shard=conversation.source_json_shard,
            )
            setattr(record, "_content", content)
            records.append(record)
        else:
            records.append(
                TextdocRecord(
                    textdoc_ref_id=_textdoc_ref_id(conversation, message, "canvas-ref", index, textdoc_id),
                    conversation_id=conversation.conversation_id,
                    message_id=message.message_id,
                    node_id=message.node_id,
                    status="canvas_reference_only",
                    textdoc_id=textdoc_id,
                    title=title,
                    textdoc_type=textdoc_type,
                    content_length=None,
                    content_sha256=None,
                    copied_pack_path=None,
                    proof_path=proof_path,
                    source_shard=conversation.source_json_shard,
                )
            )
    return records


def _open_canvas_reference_records(
    conversation: ConversationRecord,
    message: MessageRecord,
    metadata: dict[str, Any],
) -> list[TextdocRecord]:
    open_view = metadata.get("open_in_canvas_view")
    if not isinstance(open_view, dict) or open_view.get("type") != "canvas_textdoc":
        return []
    textdoc_id = _string_or_none(open_view.get("id"))
    return [
        TextdocRecord(
            textdoc_ref_id=_textdoc_ref_id(conversation, message, "open-canvas", 0, textdoc_id),
            conversation_id=conversation.conversation_id,
            message_id=message.message_id,
            node_id=message.node_id,
            status="canvas_reference_only",
            textdoc_id=textdoc_id,
            title=textdoc_id,
            textdoc_type="canvas_textdoc",
            content_length=None,
            content_sha256=None,
            copied_pack_path=None,
            proof_path=f"mapping.{message.node_id}.message.metadata.open_in_canvas_view",
            source_shard=conversation.source_json_shard,
        )
    ]


def _canmore_uri_records(conversation: ConversationRecord, message: MessageRecord) -> list[TextdocRecord]:
    records: list[TextdocRecord] = []
    for index, match in enumerate(CANMORE_URI_RE.finditer(message.text or "")):
        uri = match.group(0)
        textdoc_id = match.group(1)
        records.append(
            TextdocRecord(
                textdoc_ref_id=_textdoc_ref_id(conversation, message, "canmore-uri", index, textdoc_id),
                conversation_id=conversation.conversation_id,
                message_id=message.message_id,
                node_id=message.node_id,
                status="canmore_uri_reference",
                textdoc_id=textdoc_id,
                title=uri,
                textdoc_type="canmore_uri",
                content_length=None,
                content_sha256=None,
                copied_pack_path=None,
                proof_path=f"mapping.{message.node_id}.message.content.parts",
                source_shard=conversation.source_json_shard,
                canmore_uri=uri,
            )
        )
    return records


def _textdoc_filename(record: TextdocRecord) -> str:
    title = record.title or record.textdoc_id or short_identifier(record.textdoc_ref_id)
    desired = safe_filename(title, fallback="textdoc")
    if not extension_from_name(desired):
        desired = f"{desired}.md"
    return desired


def _textdoc_ref_id(
    conversation: ConversationRecord,
    message: MessageRecord,
    family: str,
    index: int,
    textdoc_id: str | None,
) -> str:
    stable = textdoc_id or f"{message.node_id}-{index}"
    return f"{conversation.conversation_id}:{message.message_id or message.node_id}:{family}:{stable}"


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
