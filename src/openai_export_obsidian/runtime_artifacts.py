from __future__ import annotations

"""Structural linking for Python/runtime files stored below ``personal/files``.

ChatGPT exports may store a generated runtime payload under the path grammar::

    personal/files/<conversation-id>/<execution-message-id>/mnt/data/<payload>

The primary conversation JSON does not always retain the code execution node.
Some exports retain it in an auxiliary ``chat.html`` ``var jsonData`` payload.
This module joins those two explicit sources without guessing from a basename or
from the order of messages.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import json
from pathlib import PurePosixPath
from typing import Iterable, Iterator

from .archive import ExportArchive
from .inventory import ExportInventory, InventoryEntry


RUNTIME_ARTIFACT_STATUSES = frozenset(
    {
        "confirmed_by_chat_html",
        "conversation_scoped_unconfirmed",
    }
)


@dataclass(frozen=True)
class ChatHtmlMessageEvidence:
    conversation_id: str
    execution_message_id: str
    parent_node_id: str | None
    author_role: str | None
    recipient: str | None
    content_type: str | None
    create_time: float | str | None
    chat_html_archive_path: str
    proof_path: str


@dataclass
class RuntimeArtifactRecord:
    artifact_id: str
    conversation_id: str
    execution_message_id: str
    physical_archive_path: str
    member_path: str
    runtime_path: str
    filename: str
    size: int
    extension: str | None
    detected_extension: str | None
    mime_type: str | None
    signature: str | None
    inventory_status: str
    relation_status: str
    relation_reason: str
    chat_html_archive_path: str | None
    chat_html_proof_path: str | None
    execution_parent_node_id: str | None
    execution_author_role: str | None
    execution_recipient: str | None
    execution_content_type: str | None
    execution_create_time: float | str | None
    copied_filename: str | None = None
    copied_pack_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class RuntimeArtifactLinkingResult:
    records: list[RuntimeArtifactRecord]
    scanned_chat_html_archive_paths: list[str]
    scan_warnings: list[str]

    def by_conversation(self) -> dict[str, list[RuntimeArtifactRecord]]:
        grouped: dict[str, list[RuntimeArtifactRecord]] = defaultdict(list)
        for record in self.records:
            grouped[record.conversation_id].append(record)
        return {conversation_id: sorted(rows, key=lambda row: row.physical_archive_path) for conversation_id, rows in grouped.items()}

    def summary_dict(self) -> dict[str, object]:
        return {
            "counts": {
                "runtime_artifacts": len(self.records),
                "confirmed_by_chat_html": sum(
                    record.relation_status == "confirmed_by_chat_html" for record in self.records
                ),
                "conversation_scoped_unconfirmed": sum(
                    record.relation_status == "conversation_scoped_unconfirmed" for record in self.records
                ),
                "chat_html_sources_scanned": len(self.scanned_chat_html_archive_paths),
            },
            "relation_statuses": dict(sorted(Counter(record.relation_status for record in self.records).items())),
            "scanned_chat_html_archive_paths": self.scanned_chat_html_archive_paths,
            "scan_warnings": self.scan_warnings,
        }


class RuntimeArtifactLinker:
    """Join structural runtime paths with auxiliary ``chat.html`` message nodes."""

    def __init__(self, archive: ExportArchive, inventory: ExportInventory):
        self.archive = archive
        self.inventory = inventory

    def link(self, conversation_ids: Iterable[str]) -> RuntimeArtifactLinkingResult:
        selected_conversation_ids = set(conversation_ids)
        candidates = [
            (entry, parsed)
            for entry in self.inventory.entries
            if (parsed := parse_runtime_artifact_path(entry.member_path)) is not None
            and parsed.conversation_id in selected_conversation_ids
            and entry.is_physical_payload
        ]
        required_pairs = {(parsed.conversation_id, parsed.execution_message_id) for _, parsed in candidates}
        message_index, scanned_paths, warnings = self._chat_html_message_index(required_pairs)
        records = [
            _record_for_candidate(entry, parsed, message_index.get((parsed.conversation_id, parsed.execution_message_id)))
            for entry, parsed in candidates
        ]
        return RuntimeArtifactLinkingResult(
            records=sorted(records, key=lambda record: record.physical_archive_path),
            scanned_chat_html_archive_paths=scanned_paths,
            scan_warnings=warnings,
        )

    def _chat_html_message_index(
        self,
        required_pairs: set[tuple[str, str]],
    ) -> tuple[dict[tuple[str, str], ChatHtmlMessageEvidence], list[str], list[str]]:
        if not required_pairs:
            return {}, [], []
        members = sorted(
            self.archive.members_from_inventory(
                lambda entry: entry.family == "html_export" and entry.basename.casefold() == "chat.html"
            ),
            key=_chat_html_priority,
        )
        index: dict[tuple[str, str], ChatHtmlMessageEvidence] = {}
        scanned_paths: list[str] = []
        warnings: list[str] = []
        unresolved = set(required_pairs)
        for member in members:
            if not unresolved:
                break
            scanned_paths.append(member.archive_path)
            try:
                text = self.archive.read_bytes(member).decode("utf-8", errors="replace")
                for conversation in iter_chat_html_conversations(text):
                    if not unresolved:
                        break
                    conversation_id = _conversation_id(conversation)
                    if not conversation_id:
                        continue
                    needed_execution_ids = {execution_id for candidate_conversation_id, execution_id in unresolved if candidate_conversation_id == conversation_id}
                    if not needed_execution_ids:
                        continue
                    mapping = conversation.get("mapping")
                    if not isinstance(mapping, dict):
                        continue
                    for execution_message_id in needed_execution_ids:
                        node = mapping.get(execution_message_id)
                        if not isinstance(node, dict):
                            continue
                        message = node.get("message") if isinstance(node.get("message"), dict) else {}
                        author = message.get("author") if isinstance(message.get("author"), dict) else {}
                        content = message.get("content") if isinstance(message.get("content"), dict) else {}
                        key = (conversation_id, execution_message_id)
                        index[key] = ChatHtmlMessageEvidence(
                            conversation_id=conversation_id,
                            execution_message_id=execution_message_id,
                            parent_node_id=_string_or_none(node.get("parent")),
                            author_role=_string_or_none(author.get("role")),
                            recipient=_string_or_none(message.get("recipient")),
                            content_type=_string_or_none(content.get("content_type")),
                            create_time=message.get("create_time"),
                            chat_html_archive_path=member.archive_path,
                            proof_path=f"var jsonData[conversation_id={conversation_id}].mapping[{execution_message_id}]",
                        )
                        unresolved.discard(key)
            except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                warnings.append(f"chat_html_unreadable:{member.archive_path}:{exc}")
        return index, scanned_paths, warnings


@dataclass(frozen=True)
class ParsedRuntimeArtifactPath:
    conversation_id: str
    execution_message_id: str
    runtime_path: str
    filename: str


def parse_runtime_artifact_path(member_path: str) -> ParsedRuntimeArtifactPath | None:
    """Recognize the explicit ``personal/files/<conv>/<message>/mnt/data`` grammar."""
    parts = PurePosixPath(member_path.replace("\\", "/").lstrip("/")).parts
    for index in range(len(parts) - 6):
        if parts[index : index + 2] != ("personal", "files"):
            continue
        conversation_id, execution_message_id = parts[index + 2 : index + 4]
        if parts[index + 4 : index + 6] != ("mnt", "data"):
            continue
        payload_parts = parts[index + 6 :]
        if not conversation_id or not execution_message_id or not payload_parts:
            return None
        return ParsedRuntimeArtifactPath(
            conversation_id=conversation_id,
            execution_message_id=execution_message_id,
            runtime_path="/mnt/data/" + "/".join(payload_parts),
            filename=payload_parts[-1],
        )
    return None


def iter_chat_html_conversations(text: str) -> Iterator[dict[str, object]]:
    """Yield ``var jsonData`` array members without retaining the whole array.

    The official HTML export embeds a large JSON array directly in a script tag.
    Decoding one object at a time avoids a second full in-memory representation.
    """
    marker = "var jsonData ="
    marker_index = text.find(marker)
    if marker_index < 0:
        return
    array_start = text.find("[", marker_index + len(marker))
    if array_start < 0:
        return
    decoder = json.JSONDecoder()
    position = array_start + 1
    length = len(text)
    while position < length:
        while position < length and text[position].isspace():
            position += 1
        if position >= length or text[position] == "]":
            return
        value, position = decoder.raw_decode(text, position)
        if isinstance(value, dict):
            yield value
        while position < length and text[position].isspace():
            position += 1
        if position < length and text[position] == ",":
            position += 1
            continue
        if position < length and text[position] == "]":
            return
        raise json.JSONDecodeError("expected ',' or ']' after conversation", text, position)


def _record_for_candidate(
    entry: InventoryEntry,
    parsed: ParsedRuntimeArtifactPath,
    evidence: ChatHtmlMessageEvidence | None,
) -> RuntimeArtifactRecord:
    if evidence:
        relation_status = "confirmed_by_chat_html"
        reason = "runtime folder execution ID matched an auxiliary chat.html mapping node"
    else:
        relation_status = "conversation_scoped_unconfirmed"
        reason = "conversation ID is explicit in the runtime path, but no matching auxiliary chat.html node was found"
    return RuntimeArtifactRecord(
        artifact_id=f"runtime:{entry.archive_path}",
        conversation_id=parsed.conversation_id,
        execution_message_id=parsed.execution_message_id,
        physical_archive_path=entry.archive_path,
        member_path=entry.member_path,
        runtime_path=parsed.runtime_path,
        filename=parsed.filename,
        size=entry.size,
        extension=entry.extension,
        detected_extension=entry.detected_extension,
        mime_type=entry.mime_type,
        signature=entry.signature,
        inventory_status=entry.status,
        relation_status=relation_status,
        relation_reason=reason,
        chat_html_archive_path=evidence.chat_html_archive_path if evidence else None,
        chat_html_proof_path=evidence.proof_path if evidence else None,
        execution_parent_node_id=evidence.parent_node_id if evidence else None,
        execution_author_role=evidence.author_role if evidence else None,
        execution_recipient=evidence.recipient if evidence else None,
        execution_content_type=evidence.content_type if evidence else None,
        execution_create_time=evidence.create_time if evidence else None,
    )


def _chat_html_priority(member) -> tuple[int, int, str]:
    canonical_conversations_html = bool(member.nested_chain) and "conversations__" in member.nested_chain[0].casefold()
    return (0 if canonical_conversations_html and len(member.nested_chain) == 1 else 1, len(member.nested_chain), member.archive_path)


def _conversation_id(row: dict[str, object]) -> str | None:
    return _string_or_none(row.get("conversation_id")) or _string_or_none(row.get("id"))


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
