from __future__ import annotations

"""Evidence-only indexing of historical exports encapsulated in a newer export.

Some OpenAI exports contain a complete earlier export as a nested ZIP (occasionally
stored below a ``.dat`` name).  Its monolithic ``conversations.json`` may retain
technical nodes no longer present in the current ``conversations-*.json`` shards.

This module deliberately does *not* merge those records into the primary parsed
conversation.  It exposes a separately provenance-labelled comparison and a compact
signal index.  Later stages can make an explicit, reviewable choice about whether a
historical field is useful for reconstruction.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
import hashlib
import json
from typing import Any, Iterable, Iterator

from .archive import ExportArchive
from .inventory import ExportInventory
from .models import ArchiveMember, ConversationRecord


HISTORICAL_EXPORT_STATUS = "confirmed_auxiliary_historical_export"
HISTORICAL_EXPORT_DUPLICATE_STATUS = "duplicate_auxiliary_historical_export"
HISTORICAL_MESSAGE_STATUSES = frozenset({"historical_only", "also_in_primary"})


@dataclass(frozen=True)
class HistoricalExportSnapshot:
    snapshot_id: str
    archive_path: str
    conversations_archive_path: str
    chat_html_archive_path: str | None
    depth: int
    content_crc32: str
    content_size: int
    content_sha256: str | None
    duplicate_of_snapshot_id: str | None
    status: str = HISTORICAL_EXPORT_STATUS

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class HistoricalConversationComparison:
    comparison_id: str
    snapshot_id: str
    conversation_id: str
    title: str | None
    historical_conversations_archive_path: str
    historical_proof_path: str
    primary_message_count: int
    historical_mapping_node_count: int
    historical_message_count: int
    shared_message_count: int
    historical_only_message_count: int
    primary_only_message_count: int
    status: str = HISTORICAL_EXPORT_STATUS

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class HistoricalMessageRecord:
    historical_message_id: str
    snapshot_id: str
    conversation_id: str
    node_id: str
    message_id: str | None
    parent_node_id: str | None
    author_role: str | None
    recipient: str | None
    content_type: str | None
    create_time: float | str | None
    message_status: str
    historical_conversations_archive_path: str
    proof_path: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class HistoricalSignalRecord:
    signal_id: str
    snapshot_id: str
    conversation_id: str
    node_id: str
    message_id: str | None
    message_status: str
    signal_kind: str
    source_path: str
    value_summary: str | None
    historical_conversations_archive_path: str
    proof_path: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class EmbeddedHistoricalExportResult:
    snapshots: list[HistoricalExportSnapshot]
    comparisons: list[HistoricalConversationComparison]
    messages: list[HistoricalMessageRecord]
    signals: list[HistoricalSignalRecord]
    scan_warnings: list[str]

    def comparisons_by_conversation(self) -> dict[str, list[HistoricalConversationComparison]]:
        grouped: dict[str, list[HistoricalConversationComparison]] = defaultdict(list)
        for row in self.comparisons:
            grouped[row.conversation_id].append(row)
        return {key: sorted(value, key=lambda row: row.snapshot_id) for key, value in grouped.items()}

    def signals_by_conversation(self) -> dict[str, list[HistoricalSignalRecord]]:
        grouped: dict[str, list[HistoricalSignalRecord]] = defaultdict(list)
        for row in self.signals:
            grouped[row.conversation_id].append(row)
        return {key: sorted(value, key=lambda row: (row.signal_kind, row.proof_path)) for key, value in grouped.items()}

    def summary_dict(self) -> dict[str, object]:
        return {
            "counts": {
                "snapshots": len(self.snapshots),
                "comparisons": len(self.comparisons),
                "historical_messages": len(self.messages),
                "historical_only_messages": sum(row.message_status == "historical_only" for row in self.messages),
                "signals": len(self.signals),
                "duplicate_snapshots": sum(row.duplicate_of_snapshot_id is not None for row in self.snapshots),
            },
            "signal_kinds": dict(sorted(Counter(row.signal_kind for row in self.signals).items())),
            "snapshots": [row.to_dict() for row in self.snapshots],
            "scan_warnings": self.scan_warnings,
        }


class EmbeddedHistoricalExportIngestor:
    """Index monolithic historical conversations without making them primary data."""

    def __init__(self, archive: ExportArchive, inventory: ExportInventory):
        self.archive = archive
        self.inventory = inventory

    def ingest(self, conversations: Iterable[ConversationRecord]) -> EmbeddedHistoricalExportResult:
        primary_by_id = {conversation.conversation_id: conversation for conversation in conversations}
        candidates = self._snapshots()
        snapshots: list[HistoricalExportSnapshot] = []
        comparisons: list[HistoricalConversationComparison] = []
        messages: list[HistoricalMessageRecord] = []
        signals: list[HistoricalSignalRecord] = []
        warnings: list[str] = []
        canonical_by_sha256: dict[str, str] = {}
        for snapshot, member in candidates:
            try:
                source_bytes = self.archive.read_bytes(member)
                source_sha256 = hashlib.sha256(source_bytes).hexdigest()
                duplicate_of = canonical_by_sha256.get(source_sha256)
                snapshot = replace(
                    snapshot,
                    content_sha256=source_sha256,
                    duplicate_of_snapshot_id=duplicate_of,
                    status=HISTORICAL_EXPORT_DUPLICATE_STATUS if duplicate_of else HISTORICAL_EXPORT_STATUS,
                )
                snapshots.append(snapshot)
                if duplicate_of is not None:
                    continue
                canonical_by_sha256[source_sha256] = snapshot.snapshot_id
                # Do not hold the decompressed byte buffer and its decoded text at
                # once.  Historical monoliths can exceed 300 MB, while the primary
                # parser may already retain thousands of current conversations.
                del source_bytes
                text = self.archive.read_bytes(member).decode("utf-8", errors="replace")
                for raw in iter_json_array_objects(text):
                    conversation_id = conversation_id_from_raw(raw)
                    primary = primary_by_id.get(conversation_id or "")
                    if primary is None:
                        continue
                    comparison, message_rows, signal_rows = compare_historical_conversation(snapshot, primary, raw)
                    comparisons.append(comparison)
                    messages.extend(message_rows)
                    signals.extend(signal_rows)
            except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                warnings.append(f"historical_conversations_unreadable:{member.archive_path}:{exc}")
        return EmbeddedHistoricalExportResult(
            snapshots=snapshots,
            comparisons=sorted(comparisons, key=lambda row: (row.conversation_id, row.snapshot_id)),
            messages=sorted(messages, key=lambda row: (row.conversation_id, row.snapshot_id, row.node_id)),
            signals=sorted(signals, key=lambda row: (row.conversation_id, row.snapshot_id, row.proof_path)),
            scan_warnings=warnings,
        )

    def _snapshots(self) -> list[tuple[HistoricalExportSnapshot, ArchiveMember]]:
        monolith_entries = [
            entry
            for entry in self.inventory.entries
            if entry.family == "conversation_monolith" and entry.archive_chain and not entry.is_directory
        ]
        members_by_path = {
            member.archive_path: member
            for member in self.archive.members_from_inventory(lambda entry: entry.family == "conversation_monolith")
        }
        html_by_chain = {
            member.nested_chain: member.archive_path
            for member in self.archive.members_from_inventory(
                lambda entry: entry.family == "html_export" and entry.basename.casefold() == "chat.html"
            )
        }
        snapshots: list[tuple[HistoricalExportSnapshot, ArchiveMember]] = []
        for entry in sorted(monolith_entries, key=lambda row: row.archive_path):
            member = members_by_path[entry.archive_path]
            chain = member.nested_chain
            archive_path = "::".join(chain)
            snapshot = HistoricalExportSnapshot(
                snapshot_id=archive_path,
                archive_path=archive_path,
                conversations_archive_path=member.archive_path,
                chat_html_archive_path=html_by_chain.get(chain),
                depth=len(chain),
                content_crc32=f"{entry.crc:08x}",
                content_size=entry.size,
                content_sha256=None,
                duplicate_of_snapshot_id=None,
            )
            snapshots.append((snapshot, member))
        return snapshots


def iter_json_array_objects(text: str) -> Iterator[dict[str, Any]]:
    """Decode a top-level JSON array one object at a time.

    The source bytes still need to be decompressed from the ZIP, but this avoids
    allocating a second full Python list for a 300 MB historical export.
    """
    decoder = json.JSONDecoder()
    position = 0
    length = len(text)
    while position < length and text[position].isspace():
        position += 1
    if position >= length or text[position] != "[":
        raise ValueError("historical conversations.json is not a JSON array")
    position += 1
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


def conversation_id_from_raw(raw: dict[str, Any]) -> str | None:
    for key in ("conversation_id", "id"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def compare_historical_conversation(
    snapshot: HistoricalExportSnapshot,
    primary: ConversationRecord,
    raw: dict[str, Any],
) -> tuple[HistoricalConversationComparison, list[HistoricalMessageRecord], list[HistoricalSignalRecord]]:
    mapping = raw.get("mapping") if isinstance(raw.get("mapping"), dict) else {}
    primary_ids = {value for message in primary.all_messages for value in (message.node_id, message.message_id) if value}
    historical_message_keys: set[str] = set()
    message_rows: list[HistoricalMessageRecord] = []
    signal_rows: list[HistoricalSignalRecord] = []
    for raw_node_id, node in sorted(mapping.items()):
        if not isinstance(raw_node_id, str) or not isinstance(node, dict):
            continue
        message = node.get("message") if isinstance(node.get("message"), dict) else None
        if message is None:
            continue
        node_id = string_or_none(node.get("id")) or raw_node_id
        message_id = string_or_none(message.get("id"))
        identity_keys = {node_id}
        if message_id:
            identity_keys.add(message_id)
        message_status = "also_in_primary" if identity_keys & primary_ids else "historical_only"
        historical_message_keys.update(identity_keys)
        author = message.get("author") if isinstance(message.get("author"), dict) else {}
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        node_proof = f"$[{conversation_id_from_raw(raw) or primary.conversation_id}].mapping[{node_id}]"
        message_rows.append(
            HistoricalMessageRecord(
                historical_message_id=f"{snapshot.snapshot_id}::{primary.conversation_id}::{node_id}",
                snapshot_id=snapshot.snapshot_id,
                conversation_id=primary.conversation_id,
                node_id=node_id,
                message_id=message_id,
                parent_node_id=string_or_none(node.get("parent")),
                author_role=string_or_none(author.get("role")),
                recipient=string_or_none(message.get("recipient")),
                content_type=string_or_none(content.get("content_type")),
                create_time=message.get("create_time") if isinstance(message.get("create_time"), (float, int, str)) else None,
                message_status=message_status,
                historical_conversations_archive_path=snapshot.conversations_archive_path,
                proof_path=node_proof,
            )
        )
        signal_rows.extend(
            signals_for_message(
                snapshot=snapshot,
                conversation_id=primary.conversation_id,
                node_id=node_id,
                message_id=message_id,
                message_status=message_status,
                message=message,
                node_proof=node_proof,
            )
        )
    historical_only = {row.node_id for row in message_rows if row.message_status == "historical_only"}
    shared = {row.node_id for row in message_rows if row.message_status == "also_in_primary"}
    primary_only = primary_ids - historical_message_keys
    comparison = HistoricalConversationComparison(
        comparison_id=f"{snapshot.snapshot_id}::{primary.conversation_id}",
        snapshot_id=snapshot.snapshot_id,
        conversation_id=primary.conversation_id,
        title=string_or_none(raw.get("title")) or primary.title,
        historical_conversations_archive_path=snapshot.conversations_archive_path,
        historical_proof_path=f"$[{primary.conversation_id}]",
        primary_message_count=len(primary.all_messages),
        historical_mapping_node_count=len(mapping),
        historical_message_count=len(message_rows),
        shared_message_count=len(shared),
        historical_only_message_count=len(historical_only),
        primary_only_message_count=len(primary_only),
    )
    return comparison, message_rows, signal_rows


def signals_for_message(
    *,
    snapshot: HistoricalExportSnapshot,
    conversation_id: str,
    node_id: str,
    message_id: str | None,
    message_status: str,
    message: dict[str, Any],
    node_proof: str,
) -> list[HistoricalSignalRecord]:
    rows: list[HistoricalSignalRecord] = []
    recipient = string_or_none(message.get("recipient"))
    if recipient == "python":
        rows.append(_signal(snapshot, conversation_id, node_id, message_id, message_status, "python_execution", "recipient", recipient, node_proof))
    for source_path, kind, value in iter_historical_signals(message):
        rows.append(_signal(snapshot, conversation_id, node_id, message_id, message_status, kind, source_path, value, node_proof))
    return rows


def iter_historical_signals(value: Any, path: str = "message") -> Iterator[tuple[str, str, Any]]:
    """Yield explicit technical clues with their JSON path, without reclassifying assets."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            kind = signal_kind_for_key(key)
            if kind:
                yield child_path, kind, child
            yield from iter_historical_signals(child, child_path)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_historical_signals(child, f"{path}[{index}]")
        return
    if isinstance(value, str):
        if "/mnt/data/" in value:
            yield path, "mnt_data_path", value
        if "canmore://" in value:
            yield path, "canmore", value
        if "textdoc" in value.casefold():
            yield path, "textdoc", value


def signal_kind_for_key(key: str) -> str | None:
    normalized = key.casefold()
    if normalized == "asset_pointer":
        return "asset_pointer"
    if normalized == "audio_asset_pointer":
        return "audio_asset_pointer"
    if "image_gen" in normalized:
        return "image_gen"
    if "dalle" in normalized:
        return "dalle"
    if "canmore" in normalized:
        return "canmore"
    if "textdoc" in normalized:
        return "textdoc"
    return None


def _signal(
    snapshot: HistoricalExportSnapshot,
    conversation_id: str,
    node_id: str,
    message_id: str | None,
    message_status: str,
    signal_kind: str,
    source_path: str,
    value: Any,
    node_proof: str,
) -> HistoricalSignalRecord:
    proof_path = f"{node_proof}.{source_path}"
    return HistoricalSignalRecord(
        signal_id=f"{snapshot.snapshot_id}::{conversation_id}::{node_id}::{source_path}::{signal_kind}",
        snapshot_id=snapshot.snapshot_id,
        conversation_id=conversation_id,
        node_id=node_id,
        message_id=message_id,
        message_status=message_status,
        signal_kind=signal_kind,
        source_path=source_path,
        value_summary=summarize_value(value),
        historical_conversations_archive_path=snapshot.conversations_archive_path,
        proof_path=proof_path,
    )


def summarize_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        return "object keys: " + ", ".join(sorted(str(key) for key in value)[:20])
    if isinstance(value, list):
        return f"list length: {len(value)}"
    return type(value).__name__


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
