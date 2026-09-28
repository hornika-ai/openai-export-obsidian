from __future__ import annotations

"""Extract technical events without resolving physical payloads.

This layer indexes explicit message-level observations from the primary export,
embedded historical exports, and (only as a fallback) ``chat.html`` runtime nodes.
It deliberately does not call Physical Resolution or create asset/generation/gizmo
entities.  Every row keeps the source archive and JSON proof path that justified it.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
import gc
import hashlib
import json
import re
from typing import Any, Iterable

from .archive import ExportArchive
from .embedded_historical_exports import iter_json_array_objects
from .inventory import ExportInventory
from .models import ConversationRecord
from .utils import append_jsonl, write_json


SOURCE_PRIMARY = "primary_2026_json"
SOURCE_HISTORICAL = "historical_embedded_json"
SOURCE_CHAT_HTML = "chat_html_auxiliary"
UNKNOWN_FAMILY = "unknown_recipient"
MNT_DATA_RE = re.compile(r"/mnt/data/[^\s\"'`),;]+")
CANMORE_URI_RE = re.compile(r"canmore://[^\s\"'`\]>)]+")
PERSISTENT_TEXTDOC_RE = re.compile(r"[0-9a-f]{32}")
TEMPORARY_TEXTDOC_RE = re.compile(r"temp-td-user:[0-9]+")


@dataclass(frozen=True)
class TechnicalEventRecord:
    """Pipeline-normalized observation; ``technical_event_id`` is not an OpenAI ID."""

    technical_event_id: str
    event_family: str
    operation_observed: str
    source_export: str
    source_archive_path: str
    proof_path: str
    conversation_id: str | None
    node_id: str | None
    message_id: str | None
    parent_node_id: str | None
    child_node_ids: list[str]
    author_role: str | None
    recipient: str | None
    model_slug: str | None
    default_model_slug: str | None
    request_id: str | None
    gizmo_id: str | None
    asset_pointers: list[str]
    original_gen_ids: list[str]
    original_file_ids: list[str]
    mask_file_ids: list[str]
    textdoc_references: list[str]
    library_references: list[str]
    mnt_data_paths: list[str]
    field_presence: dict[str, str]
    relevant_metadata: dict[str, Any]
    confidence: str
    provenance_status: str
    observation_status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TechnicalEventComparisonRecord:
    comparison_id: str
    conversation_id: str | None
    node_id: str | None
    message_id: str | None
    event_family: str
    operation_observed: str
    primary_event_ids: list[str]
    historical_event_ids: list[str]
    auxiliary_event_ids: list[str]
    comparison_status: str
    primary_fields: list[str]
    historical_fields: list[str]
    contradictory_fields: list[str]
    comparison_basis: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TechnicalEventExtractionResult:
    records: list[TechnicalEventRecord]
    comparisons: list[TechnicalEventComparisonRecord]
    scan_warnings: list[str]
    recipient_observations: dict[str, Counter[str]] = field(default_factory=dict)

    def by_conversation(self) -> dict[str, list[TechnicalEventRecord]]:
        grouped: dict[str, list[TechnicalEventRecord]] = defaultdict(list)
        for record in self.records:
            if record.conversation_id:
                grouped[record.conversation_id].append(record)
        return {
            conversation_id: sorted(rows, key=lambda row: (row.source_export, row.proof_path, row.technical_event_id))
            for conversation_id, rows in grouped.items()
        }

    def summary_dict(self) -> dict[str, Any]:
        by_source_family = Counter((row.source_export, row.event_family) for row in self.records)
        by_source_operation = Counter((row.source_export, row.operation_observed) for row in self.records)
        return {
            "counts": {
                "technical_events": len(self.records),
                "unknown_events": sum(row.event_family == UNKNOWN_FAMILY for row in self.records),
                "comparisons": len(self.comparisons),
            },
            "source_family_counts": {
                f"{source}:{family}": count for (source, family), count in sorted(by_source_family.items())
            },
            "source_operation_counts": {
                f"{source}:{operation}": count for (source, operation), count in sorted(by_source_operation.items())
            },
            "comparison_status_counts": dict(
                sorted(Counter(row.comparison_status for row in self.comparisons).items())
            ),
            "unknown_recipients": dict(
                sorted(
                    Counter(row.recipient for row in self.records if row.event_family == UNKNOWN_FAMILY).items()
                )
            ),
            "family_conversation_counts": {
                family: len({row.conversation_id for row in self.records if row.event_family == family and row.conversation_id})
                for family in sorted({row.event_family for row in self.records})
            },
            "recipient_counts": dict(sorted(Counter(row.recipient for row in self.records if row.recipient).items())),
            "recipient_observations_by_source": {
                source: dict(sorted(counts.items())) for source, counts in sorted(self.recipient_observations.items())
            },
            "field_observations": field_observation_summary(self.records),
            "structural_gaps": {
                "events_without_message_id": sum(row.message_id is None for row in self.records),
                "events_without_node_id": sum(row.node_id is None for row in self.records),
                "events_without_parent_node_id": sum(row.parent_node_id is None for row in self.records),
            },
            "integrity": {
                "technical_event_id_collisions": technical_event_id_collisions(self.records),
            },
            "scan_warnings": self.scan_warnings,
            "contract": {
                "physical_resolution_called": False,
                "messages_merged": False,
                "candidate_edges_created": False,
            },
        }


class TechnicalEventExtractor:
    """Extract source-separated technical observations for selected conversations."""

    def __init__(self, archive: ExportArchive, inventory: ExportInventory):
        self.archive = archive
        self.inventory = inventory

    def extract(
        self,
        conversations: Iterable[ConversationRecord],
        primary_raw_by_conversation: dict[str, dict[str, Any]],
        *,
        runtime_artifacts: Iterable[Any] = (),
    ) -> TechnicalEventExtractionResult:
        selected = {conversation.conversation_id: conversation for conversation in conversations}
        records: list[TechnicalEventRecord] = []
        warnings: list[str] = []
        recipient_observations: dict[str, Counter[str]] = defaultdict(Counter)
        for conversation_id, conversation in sorted(selected.items()):
            raw = primary_raw_by_conversation.get(conversation_id)
            if isinstance(raw, dict):
                observe_recipients(raw, recipient_observations[SOURCE_PRIMARY])
                records.extend(
                    events_for_conversation(
                        raw,
                        source_export=SOURCE_PRIMARY,
                        source_archive_path=conversation.source_archive_path,
                        fallback_conversation_id=conversation_id,
                    )
                )
        records.extend(self._historical_events(set(selected), warnings, recipient_observations=recipient_observations))
        records.extend(self._chat_html_fallback_events(runtime_artifacts, records))
        records = sorted(records, key=lambda row: (row.source_export, row.source_archive_path, row.proof_path, row.technical_event_id))
        return TechnicalEventExtractionResult(
            records=records,
            comparisons=build_comparisons(records),
            scan_warnings=warnings,
            recipient_observations=dict(recipient_observations),
        )

    def extract_streaming(
        self,
        conversation_ids: Iterable[str],
        *,
        runtime_artifacts: Iterable[Any] = (),
    ) -> TechnicalEventExtractionResult:
        """Extract a full layer without retaining every primary raw conversation.

        This is used to enrich an existing complete pack when the broader parser
        cannot safely retain all raw conversations and all other evidence layers in
        the same process.  Semantics and source proofs are identical to ``extract``.
        """
        selected = set(conversation_ids)
        records: list[TechnicalEventRecord] = []
        warnings: list[str] = []
        recipient_observations: dict[str, Counter[str]] = defaultdict(Counter)
        for member in sorted(self.archive.members_from_inventory(lambda entry: entry.family == "conversation_shard"), key=lambda item: item.archive_path):
            try:
                data = json.loads(self.archive.read_text(member))
                rows = data if isinstance(data, list) else list(data.values()) if isinstance(data, dict) else []
                for raw in rows:
                    if not isinstance(raw, dict):
                        continue
                    conversation_id = conversation_id_from_raw(raw)
                    if conversation_id not in selected:
                        continue
                    observe_recipients(raw, recipient_observations[SOURCE_PRIMARY])
                    records.extend(
                        events_for_conversation(
                            raw,
                            source_export=SOURCE_PRIMARY,
                            source_archive_path=member.archive_path,
                            fallback_conversation_id=conversation_id,
                        )
                    )
                del data, rows
                gc.collect()
            except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                warnings.append(f"technical_primary_source_unreadable:{member.archive_path}:{exc}")
        records.extend(self._historical_events(selected, warnings, recipient_observations=recipient_observations))
        records.extend(self._chat_html_fallback_events(runtime_artifacts, records))
        records = sorted(records, key=lambda row: (row.source_export, row.source_archive_path, row.proof_path, row.technical_event_id))
        return TechnicalEventExtractionResult(
            records=records,
            comparisons=build_comparisons(records),
            scan_warnings=warnings,
            recipient_observations=dict(recipient_observations),
        )

    def _historical_events(
        self,
        selected_conversation_ids: set[str],
        warnings: list[str],
        *,
        recipient_observations: dict[str, Counter[str]] | None = None,
    ) -> list[TechnicalEventRecord]:
        records: list[TechnicalEventRecord] = []
        members = sorted(
            self.archive.members_from_inventory(lambda entry: entry.family == "conversation_monolith"),
            key=lambda member: member.archive_path,
        )
        seen_content: set[str] = set()
        for member in members:
            if not member.nested_chain:
                continue
            try:
                source_bytes = self.archive.read_bytes(member)
                digest = hashlib.sha256(source_bytes).hexdigest()
                if digest in seen_content:
                    continue
                seen_content.add(digest)
                del source_bytes
                text = self.archive.read_bytes(member).decode("utf-8", errors="replace")
                for raw in iter_json_array_objects(text):
                    conversation_id = conversation_id_from_raw(raw)
                    if conversation_id not in selected_conversation_ids:
                        continue
                    if recipient_observations is not None:
                        observe_recipients(raw, recipient_observations[SOURCE_HISTORICAL])
                    records.extend(
                        events_for_conversation(
                            raw,
                            source_export=SOURCE_HISTORICAL,
                            source_archive_path=member.archive_path,
                            fallback_conversation_id=conversation_id,
                        )
                    )
                del text
            except (OSError, RuntimeError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
                warnings.append(f"technical_historical_source_unreadable:{member.archive_path}:{exc}")
        return records

    def _chat_html_fallback_events(
        self,
        runtime_artifacts: Iterable[Any],
        existing: Iterable[TechnicalEventRecord],
    ) -> list[TechnicalEventRecord]:
        observed_python_nodes = {
            (row.conversation_id, row.node_id)
            for row in existing
            if row.event_family == "runtime_execution" and row.recipient == "python"
        }
        rows: list[TechnicalEventRecord] = []
        for artifact in runtime_artifacts:
            if artifact_value(artifact, "relation_status") != "confirmed_by_chat_html":
                continue
            conversation_id = artifact_value(artifact, "conversation_id")
            node_id = artifact_value(artifact, "execution_message_id")
            recipient = artifact_value(artifact, "execution_recipient")
            source_path = artifact_value(artifact, "chat_html_archive_path")
            proof_path = artifact_value(artifact, "chat_html_proof_path")
            if recipient != "python" or not conversation_id or not node_id or not source_path or not proof_path:
                continue
            if (conversation_id, node_id) in observed_python_nodes:
                continue
            rows.append(
                make_event(
                    event_family="runtime_execution",
                    operation_observed="python",
                    source_export=SOURCE_CHAT_HTML,
                    source_archive_path=source_path,
                    proof_path=proof_path,
                    conversation_id=conversation_id,
                    node_id=node_id,
                    message_id=node_id,
                    parent_node_id=artifact_value(artifact, "execution_parent_node_id"),
                    child_node_ids=[],
                    author_role=artifact_value(artifact, "execution_author_role"),
                    recipient=recipient,
                    model_slug=None,
                    default_model_slug=None,
                    request_id=None,
                    gizmo_id=None,
                    confidence="auxiliary_explicit",
                    provenance_status="chat_html_fallback_explicit",
                )
            )
        return rows


def events_for_conversation(
    raw: dict[str, Any],
    *,
    source_export: str,
    source_archive_path: str,
    fallback_conversation_id: str | None,
) -> list[TechnicalEventRecord]:
    conversation_id = conversation_id_from_raw(raw) or fallback_conversation_id
    mapping = raw.get("mapping") if isinstance(raw.get("mapping"), dict) else {}
    conversation_metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    conversation_gizmo_id = string_or_none(raw.get("gizmo_id")) or string_or_none(conversation_metadata.get("gizmo_id"))
    records: list[TechnicalEventRecord] = []
    for raw_node_id, node in sorted(mapping.items()):
        if not isinstance(raw_node_id, str) or not isinstance(node, dict):
            continue
        message = node.get("message") if isinstance(node.get("message"), dict) else None
        if message is None:
            continue
        node_id = string_or_none(node.get("id")) or raw_node_id
        records.extend(
            events_for_message(
                conversation_id=conversation_id,
                node_id=node_id,
                node=node,
                message=message,
                source_export=source_export,
                source_archive_path=source_archive_path,
                conversation_gizmo_id=conversation_gizmo_id,
            )
        )
    return records


def events_for_message(
    *,
    conversation_id: str | None,
    node_id: str,
    node: dict[str, Any],
    message: dict[str, Any],
    source_export: str,
    source_archive_path: str,
    conversation_gizmo_id: str | None,
) -> list[TechnicalEventRecord]:
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    author = message.get("author") if isinstance(message.get("author"), dict) else {}
    content = message.get("content") if isinstance(message.get("content"), dict) else {}
    observed = observe_message_technical_fields(message)
    qualify_observed_paths(observed, node_id)
    message_id = string_or_none(message.get("id"))
    recipient = string_or_none(message.get("recipient"))
    model_slug = string_or_none(metadata.get("model_slug"))
    default_model_slug = string_or_none(metadata.get("default_model_slug"))
    request_id = string_or_none(metadata.get("request_id"))
    gizmo_id = string_or_none(metadata.get("gizmo_id")) or conversation_gizmo_id
    base = dict(
        source_export=source_export,
        source_archive_path=source_archive_path,
        conversation_id=conversation_id,
        node_id=node_id,
        message_id=message_id,
        parent_node_id=string_or_none(node.get("parent")) or string_or_none(metadata.get("parent_id")),
        child_node_ids=[child for child in node.get("children", []) if isinstance(child, str)],
        author_role=string_or_none(author.get("role")),
        recipient=recipient,
        model_slug=model_slug,
        default_model_slug=default_model_slug,
        request_id=request_id,
        gizmo_id=gizmo_id,
        asset_pointers=observed["asset_pointers"],
        original_gen_ids=observed["original_gen_ids"],
        original_file_ids=observed["original_file_ids"],
        mask_file_ids=observed["mask_file_ids"],
        textdoc_references=observed["textdoc_references"],
        library_references=observed["library_references"],
        mnt_data_paths=observed["mnt_data_paths"],
        field_presence=field_presence(observed, metadata, conversation_gizmo_id),
        relevant_metadata=relevant_metadata(metadata, observed),
        confidence="explicit",
        provenance_status="primary_export_explicit" if source_export == SOURCE_PRIMARY else "historical_export_explicit",
    )
    records: list[TechnicalEventRecord] = []
    recipient_event = recipient_event_family(recipient)
    if recipient_event:
        family, operation = recipient_event
        records.append(make_event(event_family=family, operation_observed=operation, proof_path=f"mapping.{node_id}.message.recipient", **base))
    if observed["image_metadata_paths"] or observed["original_gen_ids"] or observed["original_file_ids"] or observed["mask_file_ids"]:
        operation = "dalle_metadata" if observed["has_dalle"] else "image_gen_metadata" if observed["has_image_gen"] else "image_edit_operation"
        records.append(make_event(event_family="image_operation", operation_observed=operation, proof_path=observed["image_metadata_paths"][0] if observed["image_metadata_paths"] else f"mapping.{node_id}.message.metadata", **base))
    if observed["textdoc_references"] or observed["textdoc_non_identifier_values"]:
        textdoc_proof_path = (
            observed["textdoc_paths"][0]
            if observed["textdoc_paths"]
            else observed["textdoc_non_identifier_values"][0]["path"]
        )
        records.append(make_event(event_family="textdoc_reference", operation_observed="textdoc_metadata", proof_path=textdoc_proof_path, **base))
    if observed["asset_pointers"]:
        records.append(make_event(event_family="asset_reference", operation_observed="asset_pointer", proof_path=observed["asset_pointer_paths"][0], **base))
    if observed["library_references"]:
        records.append(make_event(event_family="library_reference", operation_observed="library_metadata", proof_path=observed["library_paths"][0], **base))
    if observed["mnt_data_paths"]:
        records.append(make_event(event_family="runtime_path", operation_observed="mnt_data_reference", proof_path=observed["mnt_data_paths_proof"][0], **base))
    # A conversation-level gizmo is contextual data for an event, not a separate
    # event repeated for every technical message.  Emit a gizmo observation only
    # when the message itself explicitly carries the field.
    if string_or_none(metadata.get("gizmo_id")):
        records.append(make_event(event_family="gizmo_context", operation_observed="gizmo_id", proof_path=f"mapping.{node_id}.message.metadata.gizmo_id", **base))
    return records


def recipient_event_family(recipient: str | None) -> tuple[str, str] | None:
    if not recipient or recipient == "all":
        return None
    if recipient == "dalle.text2im":
        return "dalle_call", recipient
    if recipient.startswith("canmore."):
        return "canmore_call", recipient
    if recipient == "python":
        return "runtime_execution", recipient
    if recipient == "container.exec":
        return "container_execution", recipient
    if recipient == "assistant":
        return "internal_recipient", recipient
    if recipient.split(".", 1)[0] in {"web", "browser", "file_search", "computer", "bio"}:
        return "tool_recipient", recipient
    return UNKNOWN_FAMILY, recipient


def observe_message_technical_fields(message: dict[str, Any]) -> dict[str, Any]:
    observed: dict[str, Any] = {
        "asset_pointers": [], "asset_pointer_paths": [], "original_gen_ids": [], "original_file_ids": [], "mask_file_ids": [],
        "textdoc_references": [], "textdoc_paths": [], "mnt_data_paths": [], "mnt_data_paths_proof": [],
        "textdoc_non_identifier_values": [], "embedded_canmore_source_texts": [],
        "library_references": [], "library_paths": [],
        "field_presence": {
            "gizmo_id": "absent", "asset_pointer": "absent", "original_gen_id": "absent",
            "original_file_id": "absent", "mask_file_id": "absent", "textdoc_reference": "absent",
        },
        "image_metadata_paths": [], "has_dalle": False, "has_image_gen": False,
    }

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                key_path = f"{path}.{key}"
                normalized = key.casefold()
                if normalized == "asset_pointer" and isinstance(child, str):
                    record_field_presence(observed, "asset_pointer", child)
                    add_unique(observed["asset_pointers"], child)
                    add_unique(observed["asset_pointer_paths"], key_path)
                elif normalized == "asset_pointer":
                    record_field_presence(observed, "asset_pointer", child)
                if normalized == "original_gen_id" and isinstance(child, str):
                    record_field_presence(observed, "original_gen_id", child)
                    add_unique(observed["original_gen_ids"], child)
                    add_unique(observed["image_metadata_paths"], key_path)
                elif normalized == "original_gen_id":
                    record_field_presence(observed, "original_gen_id", child)
                if normalized == "original_file_id" and isinstance(child, str):
                    record_field_presence(observed, "original_file_id", child)
                    add_unique(observed["original_file_ids"], child)
                    add_unique(observed["image_metadata_paths"], key_path)
                elif normalized == "original_file_id":
                    record_field_presence(observed, "original_file_id", child)
                if normalized == "mask_file_id" and isinstance(child, str):
                    record_field_presence(observed, "mask_file_id", child)
                    add_unique(observed["mask_file_ids"], child)
                    add_unique(observed["image_metadata_paths"], key_path)
                elif normalized == "mask_file_id":
                    record_field_presence(observed, "mask_file_id", child)
                if "image_gen" in normalized:
                    observed["has_image_gen"] = True
                    add_unique(observed["image_metadata_paths"], key_path)
                if "dalle" in normalized:
                    observed["has_dalle"] = True
                    add_unique(observed["image_metadata_paths"], key_path)
                if "textdoc" in normalized or "canmore" in normalized:
                    add_textdoc_metadata_observation(observed, child, key_path)
                if "library" in normalized or "knowledge_store" in normalized:
                    add_library_observation(observed, child, key_path)
                walk(child, key_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
        elif isinstance(value, str):
            for match in MNT_DATA_RE.findall(value):
                add_unique(observed["mnt_data_paths"], match)
                add_unique(observed["mnt_data_paths_proof"], path)
            if "canmore://" in value:
                add_embedded_canmore_uris(observed, value, path)

    walk(message, "message")
    return observed


def add_textdoc_metadata_observation(observed: dict[str, Any], value: Any, path: str) -> None:
    """Keep textdoc metadata observable without promoting arbitrary strings to IDs."""
    if isinstance(value, str) and value:
        if "canmore://" in value:
            add_embedded_canmore_uris(observed, value, path)
        elif PERSISTENT_TEXTDOC_RE.fullmatch(value) or TEMPORARY_TEXTDOC_RE.fullmatch(value):
            record_field_presence(observed, "textdoc_reference", value)
            add_unique(observed["textdoc_references"], value)
            add_unique(observed["textdoc_paths"], path)
        else:
            add_textdoc_non_identifier_value(observed, value, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            add_textdoc_metadata_observation(observed, child, f"{path}[{index}]")


def add_embedded_canmore_uris(observed: dict[str, Any], source_text: str, path: str) -> None:
    uris = CANMORE_URI_RE.findall(source_text)
    if not uris:
        add_textdoc_non_identifier_value(observed, source_text, path)
        return
    record_field_presence(observed, "textdoc_reference", source_text)
    for uri in uris:
        add_unique(observed["textdoc_references"], uri)
        add_unique(observed["textdoc_paths"], path)
    source_value = {"path": path, "text": source_text}
    if source_value not in observed["embedded_canmore_source_texts"]:
        observed["embedded_canmore_source_texts"].append(source_value)


def add_textdoc_non_identifier_value(observed: dict[str, Any], value: str, path: str) -> None:
    item = {"path": path, "value": value, "reason": "not_a_supported_textdoc_identifier"}
    if item not in observed["textdoc_non_identifier_values"]:
        observed["textdoc_non_identifier_values"].append(item)


def add_library_observation(observed: dict[str, Any], value: Any, path: str) -> None:
    if isinstance(value, str) and value:
        add_unique(observed["library_references"], value)
        add_unique(observed["library_paths"], path)
    elif isinstance(value, dict):
        if isinstance(value.get("id"), str) and value["id"]:
            add_unique(observed["library_references"], value["id"])
            add_unique(observed["library_paths"], path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            add_library_observation(observed, child, f"{path}[{index}]")


def relevant_metadata(metadata: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    # Event rows can be emitted several times for a single message (asset, image,
    # textdoc, runtime, recipient).  Copying a complete canvas or image payload
    # into each row makes a full export needlessly retain hundreds of megabytes.
    # Explicit identifiers are already normalized in top-level event fields; the
    # source JSON + proof path remains the exhaustive raw evidence.
    scalar_keys = ("request_id", "model_slug", "default_model_slug", "gizmo_id", "image_gen_async")
    result = {key: metadata[key] for key in scalar_keys if key in metadata and isinstance(metadata[key], (str, int, float, bool))}
    result["technical_metadata_keys"] = sorted(
        key for key in metadata if key.casefold() in {"dalle", "image_gen", "canvas"} or "textdoc" in key.casefold()
    )
    canvas = metadata.get("canvas")
    if isinstance(canvas, dict):
        canvas_fields = ("textdoc_id", "textdoc_type", "canmore_uri", "version", "version_number")
        result["canvas_identifiers"] = {
            key: canvas[key] for key in canvas_fields if isinstance(canvas.get(key), (str, int, float, bool))
        }
    result["observed_field_paths"] = {
        "asset_pointer": observed["asset_pointer_paths"],
        "image_operation": observed["image_metadata_paths"],
        "textdoc": observed["textdoc_paths"],
        "library": observed["library_paths"],
        "mnt_data": observed["mnt_data_paths_proof"],
    }
    if observed["textdoc_non_identifier_values"]:
        result["textdoc_non_identifier_values"] = observed["textdoc_non_identifier_values"]
    if observed["embedded_canmore_source_texts"]:
        # The full source string is retained here with its JSON proof path; it is
        # evidence for URI extraction, never an entity identifier.
        result["embedded_canmore_source_texts"] = observed["embedded_canmore_source_texts"]
    return result


def field_presence(observed: dict[str, Any], metadata: dict[str, Any], conversation_gizmo_id: str | None) -> dict[str, str]:
    states = dict(observed["field_presence"])
    if "gizmo_id" in metadata:
        states["gizmo_id"] = presence_state(metadata.get("gizmo_id"))
    elif conversation_gizmo_id:
        states["gizmo_id"] = "value"
    return states


def record_field_presence(observed: dict[str, Any], name: str, value: Any) -> None:
    current = observed["field_presence"].get(name, "absent")
    candidate = presence_state(value)
    precedence = {"absent": 0, "null": 1, "empty": 2, "value": 3}
    if precedence[candidate] > precedence[current]:
        observed["field_presence"][name] = candidate


def presence_state(value: Any) -> str:
    if value is None:
        return "null"
    if value == "" or value == [] or value == {}:
        return "empty"
    return "value"


def qualify_observed_paths(observed: dict[str, Any], node_id: str) -> None:
    for key in ("asset_pointer_paths", "image_metadata_paths", "textdoc_paths", "library_paths", "mnt_data_paths_proof"):
        observed[key] = [f"mapping.{node_id}.{path}" for path in observed[key]]


def make_event(*, event_family: str, operation_observed: str, proof_path: str, **values: Any) -> TechnicalEventRecord:
    for field_name, default in (
        ("asset_pointers", []),
        ("original_gen_ids", []),
        ("original_file_ids", []),
        ("mask_file_ids", []),
        ("textdoc_references", []),
        ("library_references", []),
        ("mnt_data_paths", []),
        ("field_presence", {"gizmo_id": "absent", "asset_pointer": "absent", "original_gen_id": "absent", "original_file_id": "absent", "mask_file_id": "absent", "textdoc_reference": "absent"}),
        ("relevant_metadata", {}),
    ):
        values.setdefault(field_name, default)
    source_identity = "|".join(str(values.get(key) or "") for key in ("source_export", "source_archive_path", "conversation_id", "node_id", "message_id"))
    digest = hashlib.sha256(f"{source_identity}|{event_family}|{operation_observed}|{proof_path}".encode("utf-8")).hexdigest()[:24]
    return TechnicalEventRecord(
        technical_event_id=f"technical-event:{digest}",
        event_family=event_family,
        operation_observed=operation_observed,
        proof_path=proof_path,
        observation_status="observed_no_physical_resolution",
        **values,
    )


def build_comparisons(records: Iterable[TechnicalEventRecord]) -> list[TechnicalEventComparisonRecord]:
    grouped: dict[tuple[str | None, str | None, str | None, str, str], list[TechnicalEventRecord]] = defaultdict(list)
    for row in records:
        grouped[(row.conversation_id, row.node_id, row.message_id, row.event_family, row.operation_observed)].append(row)
    comparisons: list[TechnicalEventComparisonRecord] = []
    for key, rows in sorted(grouped.items(), key=lambda item: tuple(value or "" for value in item[0])):
        primary = [row for row in rows if row.source_export == SOURCE_PRIMARY]
        historical = [row for row in rows if row.source_export == SOURCE_HISTORICAL]
        auxiliary = [row for row in rows if row.source_export == SOURCE_CHAT_HTML]
        contradictory = contradictory_fields(primary, historical)
        if primary and historical:
            status = "contradiction" if contradictory else "present_both"
        elif historical:
            status = "historical_only"
        elif primary:
            status = "primary_only"
        else:
            status = "auxiliary_only"
        conversation_id, node_id, message_id, family, operation = key
        comparison_seed = "|".join(str(value or "") for value in key)
        comparisons.append(
            TechnicalEventComparisonRecord(
                comparison_id="technical-comparison:" + hashlib.sha256(comparison_seed.encode("utf-8")).hexdigest()[:24],
                conversation_id=conversation_id,
                node_id=node_id,
                message_id=message_id,
                event_family=family,
                operation_observed=operation,
                primary_event_ids=sorted(row.technical_event_id for row in primary),
                historical_event_ids=sorted(row.technical_event_id for row in historical),
                auxiliary_event_ids=sorted(row.technical_event_id for row in auxiliary),
                comparison_status=status,
                primary_fields=present_fields(primary),
                historical_fields=present_fields(historical),
                contradictory_fields=contradictory,
                comparison_basis="exact conversation_id + node_id + message_id + event_family + operation_observed",
            )
        )
    return comparisons


def present_fields(records: Iterable[TechnicalEventRecord]) -> list[str]:
    names = ("recipient", "request_id", "gizmo_id", "asset_pointers", "original_gen_ids", "original_file_ids", "mask_file_ids", "textdoc_references", "library_references", "mnt_data_paths")
    return sorted({name for row in records for name in names if getattr(row, name) not in (None, [], "")})


def contradictory_fields(primary: Iterable[TechnicalEventRecord], historical: Iterable[TechnicalEventRecord]) -> list[str]:
    fields = ("recipient", "request_id", "gizmo_id", "asset_pointers", "original_gen_ids", "original_file_ids", "mask_file_ids", "textdoc_references", "library_references", "mnt_data_paths")
    contradictions: list[str] = []
    for field in fields:
        left = {canonical_field_value(getattr(row, field)) for row in primary if getattr(row, field) not in (None, [], "")}
        right = {canonical_field_value(getattr(row, field)) for row in historical if getattr(row, field) not in (None, [], "")}
        if left and right and left != right:
            contradictions.append(field)
    return contradictions


def canonical_field_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def field_observation_summary(records: Iterable[TechnicalEventRecord]) -> dict[str, dict[str, int]]:
    """Count message-scoped field observations without counting sibling events twice."""
    fields = {
        "gizmo_id": lambda row: [row.gizmo_id] if row.gizmo_id else [],
        "asset_pointer": lambda row: row.asset_pointers,
        "original_gen_id": lambda row: row.original_gen_ids,
        "original_file_id": lambda row: row.original_file_ids,
        "mask_file_id": lambda row: row.mask_file_ids,
        "textdoc_reference": lambda row: row.textdoc_references,
    }
    result: dict[str, dict[str, int]] = {}
    rows = list(records)
    for field, values_for in fields.items():
        observations: set[tuple[str, str, str, str, str, str]] = set()
        values: set[str] = set()
        states_by_message: dict[tuple[str, str, str, str, str], str] = {}
        for row in rows:
            message_key = (
                row.source_export,
                row.source_archive_path,
                row.conversation_id or "",
                row.node_id or "",
                row.message_id or "",
            )
            for value in values_for(row):
                observations.add((*message_key, value))
                values.add(value)
            candidate = row.field_presence.get(field, "absent")
            precedence = {"absent": 0, "null": 1, "empty": 2, "value": 3}
            previous = states_by_message.get(message_key)
            if previous is None or precedence[candidate] > precedence[previous]:
                states_by_message[message_key] = candidate
        result[field] = {
            "message_scoped_observations": len(observations),
            "distinct_values": len(values),
            "message_field_states": dict(sorted(Counter(states_by_message.values()).items())),
        }
    return result


def technical_event_id_collisions(records: Iterable[TechnicalEventRecord]) -> int:
    counter = Counter(row.technical_event_id for row in records)
    return sum(count - 1 for count in counter.values() if count > 1)


def observe_recipients(raw: dict[str, Any], counts: Counter[str]) -> None:
    mapping = raw.get("mapping") if isinstance(raw.get("mapping"), dict) else {}
    for node in mapping.values():
        if not isinstance(node, dict):
            continue
        message = node.get("message") if isinstance(node.get("message"), dict) else None
        recipient = message.get("recipient") if message else None
        if isinstance(recipient, str) and recipient:
            counts[recipient] += 1


def emit_technical_event_evidence(output_dir, result: TechnicalEventExtractionResult, *, execution: dict[str, Any] | None = None) -> None:
    append_jsonl(output_dir / "technical_events.jsonl", [record.to_dict() for record in result.records])
    append_jsonl(output_dir / "technical_event_comparisons.jsonl", [record.to_dict() for record in result.comparisons])
    append_jsonl(
        output_dir / "technical_event_unknowns.jsonl",
        [record.to_dict() for record in result.records if record.event_family == UNKNOWN_FAMILY],
    )
    summary = result.summary_dict()
    if execution is not None:
        summary["execution"] = execution
    write_json(output_dir / "technical_event_summary.json", summary)


def conversation_id_from_raw(raw: dict[str, Any]) -> str | None:
    for key in ("conversation_id", "id"):
        if (value := raw.get(key)) and isinstance(value, str):
            return value
    return None


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def add_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def artifact_value(artifact: Any, name: str) -> Any:
    return artifact.get(name) if isinstance(artifact, dict) else getattr(artifact, name, None)
