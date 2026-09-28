from __future__ import annotations

"""Construct conservative, taxonomy-specific entities from technical events.

This layer groups only exact source values.  It does not inspect archive members,
call Physical Resolution, create candidate edges, or turn identifier similarity
into a relation.  Runtime paths are deliberately scoped to a conversation so an
identical ``/mnt/data`` literal cannot become a global file identity.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import hashlib
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from .utils import append_jsonl, write_json


REMOTE_ASSET_POINTER = "remote_asset_pointer"
ORIGINAL_FILE_REFERENCE = "original_file_reference"
MASK_FILE_REFERENCE = "mask_file_reference"
LIBRARY_FILE_REFERENCE = "library_file_reference"
RUNTIME_PATH_LITERAL = "runtime_path_literal"
ORIGINAL_GENERATION_REFERENCE = "original_generation_reference"
PERSISTENT_TEXTDOC_REFERENCE = "persistent_textdoc_reference"
TEMPORARY_TEXTDOC_HANDLE = "temporary_textdoc_handle"
EMBEDDED_CANMORE_URI_REFERENCE = "embedded_canmore_uri_reference"
PROJECT_CONTEXT_REFERENCE = "project_context_reference"
GIZMO_CONTEXT_REFERENCE = "gizmo_context_reference"

ENTITY_TYPES = {
    REMOTE_ASSET_POINTER,
    ORIGINAL_FILE_REFERENCE,
    MASK_FILE_REFERENCE,
    LIBRARY_FILE_REFERENCE,
    RUNTIME_PATH_LITERAL,
    ORIGINAL_GENERATION_REFERENCE,
    PERSISTENT_TEXTDOC_REFERENCE,
    TEMPORARY_TEXTDOC_HANDLE,
    EMBEDDED_CANMORE_URI_REFERENCE,
    PROJECT_CONTEXT_REFERENCE,
    GIZMO_CONTEXT_REFERENCE,
}
LEGACY_ENTITY_TYPES = {"logical_asset", "generation", "textdoc", "gizmo"}

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
OPAQUE_GENERATION_RE = re.compile(r"[A-Za-z0-9_-]{16}")
PERSISTENT_TEXTDOC_RE = re.compile(r"[0-9a-f]{32}")
TEMPORARY_TEXTDOC_RE = re.compile(r"temp-td-user:[0-9]+")


@dataclass(frozen=True)
class EntityInput:
    entity_type: str
    identifier_kind: str
    explicit_identifier: str
    entity_subtype: str
    scope_kind: str
    scope_value: str


@dataclass(frozen=True)
class LogicalEntityObservation:
    """One explicit identifier or literal occurrence that materializes an entity."""

    logical_entity_observation_id: str
    logical_entity_id: str
    entity_type: str
    identifier_kind: str
    explicit_identifier: str
    entity_subtype: str
    scope_kind: str
    scope_value: str
    technical_event_id: str
    source_export: str
    source_archive_path: str
    proof_path: str
    conversation_id: str | None
    node_id: str | None
    message_id: str | None
    observation_status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LogicalEntityRecord:
    """A deterministic grouping; its internal ID is never an OpenAI ID."""

    logical_entity_id: str
    entity_type: str
    identifier_kind: str
    explicit_identifier: str
    entity_subtype: str
    scope_kind: str
    scope_value: str
    entity_status: str
    confidence: str
    provenance_status: str
    source_exports: list[str]
    source_archive_paths: list[str]
    source_conversation_ids: list[str]
    source_node_ids: list[str]
    source_message_ids: list[str]
    source_event_ids: list[str]
    observation_ids: list[str]
    observation_count: int
    source_presence_status: str
    candidate_edges_created: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LogicalEntityMigrationRecord:
    migration_id: str
    old_logical_entity_id: str
    old_entity_type: str
    old_identifier_kind: str
    old_explicit_identifier: str
    old_source_event_ids: list[str]
    new_entity_type: str | None
    new_identifier_kind: str | None
    new_logical_entity_id: str | None
    reason: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LogicalEntityConstructionResult:
    records: list[LogicalEntityRecord]
    observations: list[LogicalEntityObservation]
    unmaterialized_events: list[dict[str, Any]]
    migration_records: list[LogicalEntityMigrationRecord]
    legacy_entity_counts: Counter[str]
    textdoc_non_identifier_metadata: list[dict[str, Any]]

    def summary_dict(self) -> dict[str, Any]:
        entity_type_counts = Counter(row.entity_type for row in self.records)
        entity_subtype_counts = Counter(f"{row.entity_type}:{row.entity_subtype}" for row in self.records)
        identifier_kind_counts = Counter(row.identifier_kind for row in self.records)
        source_presence_counts = Counter(row.source_presence_status for row in self.records)
        source_entity_counts = Counter(source for row in self.records for source in row.source_exports)
        migration_statuses = Counter(row.status for row in self.migration_records)
        return {
            "counts": {
                "logical_entities": len(self.records),
                "logical_entity_observations": len(self.observations),
                "unmaterialized_technical_events": len(self.unmaterialized_events),
                "taxonomy_migration_rows": len(self.migration_records),
                "legacy_entities_available_for_migration": sum(self.legacy_entity_counts.values()),
            },
            "legacy_entity_type_counts": dict(sorted(self.legacy_entity_counts.items())),
            "entity_type_counts": dict(sorted(entity_type_counts.items())),
            "entity_subtype_counts": dict(sorted(entity_subtype_counts.items())),
            "identifier_kind_counts": dict(sorted(identifier_kind_counts.items())),
            "source_entity_counts": dict(sorted(source_entity_counts.items())),
            "source_presence_status_counts": dict(sorted(source_presence_counts.items())),
            "runtime_path_quality_counts": dict(sorted(Counter(
                row.entity_subtype for row in self.records if row.entity_type == RUNTIME_PATH_LITERAL
            ).items())),
            "generation_format_counts": dict(sorted(Counter(
                row.entity_subtype for row in self.records if row.entity_type == ORIGINAL_GENERATION_REFERENCE
            ).items())),
            "gizmo_context_counts": dict(sorted(Counter(
                row.entity_type for row in self.records if row.entity_type in {PROJECT_CONTEXT_REFERENCE, GIZMO_CONTEXT_REFERENCE}
            ).items())),
            "textdoc_entity_type_counts": dict(sorted(Counter(
                row.entity_type for row in self.records if row.entity_type in {
                    PERSISTENT_TEXTDOC_REFERENCE, TEMPORARY_TEXTDOC_HANDLE, EMBEDDED_CANMORE_URI_REFERENCE,
                }
            ).items())),
            "textdoc_non_identifier_metadata": {
                "occurrences": len(self.textdoc_non_identifier_metadata),
                "distinct_values": len({
                    row.get("value") for row in self.textdoc_non_identifier_metadata if isinstance(row.get("value"), str)
                }),
                "technical_events": len({
                    row.get("technical_event_id") for row in self.textdoc_non_identifier_metadata if isinstance(row.get("technical_event_id"), str)
                }),
            },
            "embedded_canmore_uri_observations": sum(
                row.entity_type == EMBEDDED_CANMORE_URI_REFERENCE for row in self.observations
            ),
            "migration_status_counts": dict(sorted(migration_statuses.items())),
            "isolated_legacy_false_textdocs": len({
                row.old_logical_entity_id
                for row in self.migration_records
                if row.status in {"deprecated_non_identifier_textdoc", "replaced_by_extracted_canmore_uri"}
            }),
            "unmaterialized_event_family_counts": dict(
                sorted(Counter(row["event_family"] for row in self.unmaterialized_events).items())
            ),
            "integrity": {
                "logical_entity_id_collisions": collision_count(row.logical_entity_id for row in self.records),
                "logical_entity_observation_id_collisions": collision_count(
                    row.logical_entity_observation_id for row in self.observations
                ),
                "migration_id_collisions": collision_count(row.migration_id for row in self.migration_records),
            },
            "contract": {
                "source_layer": "technical_event_evidence_only",
                "physical_resolution_called": False,
                "candidate_edges_created": False,
                "causal_relations_created": False,
                "messages_merged": False,
                "historical_messages_merged": False,
                "payload_relations_created": False,
            },
        }


class LogicalEntityConstructor:
    """Construct taxonomy-specific entities from explicit event fields only."""

    def construct(
        self,
        technical_events: Iterable[Mapping[str, Any]],
        *,
        previous_entities: Iterable[Mapping[str, Any]] = (),
    ) -> LogicalEntityConstructionResult:
        observations: list[LogicalEntityObservation] = []
        materialized_event_ids: set[str] = set()
        source_rows = sorted(
            (dict(row) for row in technical_events if isinstance(row, Mapping)),
            key=lambda row: (
                str(row.get("source_export") or ""),
                str(row.get("source_archive_path") or ""),
                str(row.get("proof_path") or ""),
                str(row.get("technical_event_id") or ""),
            ),
        )
        for event in source_rows:
            for entity_input in entity_inputs_for_event(event):
                observation = make_observation(event, entity_input)
                observations.append(observation)
                materialized_event_ids.add(observation.technical_event_id)

        observations.sort(
            key=lambda row: (
                row.logical_entity_id,
                row.source_export,
                row.source_archive_path,
                row.proof_path,
                row.logical_entity_observation_id,
            )
        )
        grouped: dict[str, list[LogicalEntityObservation]] = defaultdict(list)
        for observation in observations:
            grouped[observation.logical_entity_id].append(observation)
        records = [build_entity_record(rows) for _, rows in sorted(grouped.items())]
        unmaterialized_events = [
            unmaterialized_event_row(event)
            for event in source_rows
            if isinstance(event.get("technical_event_id"), str)
            and event["technical_event_id"] not in materialized_event_ids
        ]
        old_rows = [dict(row) for row in previous_entities if isinstance(row, Mapping)]
        migration_records = build_migration_records(old_rows, records)
        legacy_entity_counts = Counter(
            str(row.get("entity_type")) for row in old_rows if row.get("entity_type") in LEGACY_ENTITY_TYPES
        )
        textdoc_non_identifier_metadata = [
            {**item, "technical_event_id": event.get("technical_event_id")}
            for event in source_rows
            for item in textdoc_non_identifier_metadata_for_event(event)
        ]
        return LogicalEntityConstructionResult(
            records=records,
            observations=observations,
            unmaterialized_events=unmaterialized_events,
            migration_records=migration_records,
            legacy_entity_counts=legacy_entity_counts,
            textdoc_non_identifier_metadata=textdoc_non_identifier_metadata,
        )


def entity_inputs_for_event(event: Mapping[str, Any]) -> list[EntityInput]:
    """Map only explicit source fields to their taxonomy-specific types."""

    family = event.get("event_family")
    result: list[EntityInput] = []
    if family == "image_operation":
        result.extend(
            EntityInput(ORIGINAL_GENERATION_REFERENCE, "original_gen_id", value, generation_format(value), "global_exact_identifier", "global")
            for value in string_values(event.get("original_gen_ids"))
        )
        result.extend(
            EntityInput(ORIGINAL_FILE_REFERENCE, "original_file_id", value, file_identifier_format(value), "global_exact_identifier", "global")
            for value in string_values(event.get("original_file_ids"))
        )
        result.extend(
            EntityInput(MASK_FILE_REFERENCE, "mask_file_id", value, file_identifier_format(value), "global_exact_identifier", "global")
            for value in string_values(event.get("mask_file_ids"))
        )
    elif family == "textdoc_reference":
        for value in string_values(event.get("textdoc_references")):
            textdoc_input = textdoc_entity_input(value)
            if textdoc_input:
                result.append(textdoc_input)
    elif family == "gizmo_context":
        value = event.get("gizmo_id")
        if isinstance(value, str) and value:
            if value.startswith("g-p-"):
                result.append(EntityInput(PROJECT_CONTEXT_REFERENCE, "gizmo_id", value, "g_p_namespace", "global_exact_identifier", "global"))
            else:
                result.append(EntityInput(GIZMO_CONTEXT_REFERENCE, "gizmo_id", value, gizmo_namespace(value), "global_exact_identifier", "global"))
    elif family == "asset_reference":
        result.extend(
            EntityInput(REMOTE_ASSET_POINTER, "asset_pointer", value, pointer_namespace(value), "global_exact_identifier", "global")
            for value in string_values(event.get("asset_pointers"))
        )
    elif family == "library_reference":
        result.extend(
            EntityInput(LIBRARY_FILE_REFERENCE, "library_reference", value, library_namespace(value), "global_exact_identifier", "global")
            for value in string_values(event.get("library_references"))
        )
    elif family == "runtime_path":
        scope_kind, scope_value = runtime_scope(event)
        result.extend(
            EntityInput(RUNTIME_PATH_LITERAL, "mnt_data_path", value, runtime_path_quality(value), scope_kind, scope_value)
            for value in string_values(event.get("mnt_data_paths"))
        )
    return result


def textdoc_entity_input(value: str) -> EntityInput | None:
    if PERSISTENT_TEXTDOC_RE.fullmatch(value):
        return EntityInput(PERSISTENT_TEXTDOC_REFERENCE, "textdoc_reference", value, "hex32", "global_exact_identifier", "global")
    if TEMPORARY_TEXTDOC_RE.fullmatch(value):
        return EntityInput(TEMPORARY_TEXTDOC_HANDLE, "textdoc_reference", value, "temp_td_user", "global_exact_identifier", "global")
    if value.startswith("canmore://"):
        return EntityInput(EMBEDDED_CANMORE_URI_REFERENCE, "canmore_uri", value, "canmore_uri", "global_exact_identifier", "global")
    return None


def make_observation(event: Mapping[str, Any], entity_input: EntityInput) -> LogicalEntityObservation:
    entity_id = logical_entity_id(
        entity_input.entity_type,
        entity_input.identifier_kind,
        entity_input.explicit_identifier,
        entity_input.entity_subtype,
        entity_input.scope_kind,
        entity_input.scope_value,
    )
    event_id = required_string(event, "technical_event_id")
    seed = "|".join((entity_id, event_id, entity_input.identifier_kind, entity_input.explicit_identifier))
    return LogicalEntityObservation(
        logical_entity_observation_id="logical-entity-observation:" + short_digest(seed),
        logical_entity_id=entity_id,
        entity_type=entity_input.entity_type,
        identifier_kind=entity_input.identifier_kind,
        explicit_identifier=entity_input.explicit_identifier,
        entity_subtype=entity_input.entity_subtype,
        scope_kind=entity_input.scope_kind,
        scope_value=entity_input.scope_value,
        technical_event_id=event_id,
        source_export=required_string(event, "source_export"),
        source_archive_path=required_string(event, "source_archive_path"),
        proof_path=required_string(event, "proof_path"),
        conversation_id=optional_string(event.get("conversation_id")),
        node_id=optional_string(event.get("node_id")),
        message_id=optional_string(event.get("message_id")),
        observation_status="derived_from_explicit_technical_event_identifier",
    )


def build_entity_record(observations: list[LogicalEntityObservation]) -> LogicalEntityRecord:
    first = observations[0]
    source_exports = sorted({row.source_export for row in observations})
    return LogicalEntityRecord(
        logical_entity_id=first.logical_entity_id,
        entity_type=first.entity_type,
        identifier_kind=first.identifier_kind,
        explicit_identifier=first.explicit_identifier,
        entity_subtype=first.entity_subtype,
        scope_kind=first.scope_kind,
        scope_value=first.scope_value,
        entity_status="explicit_identifier_grouped_no_physical_resolution",
        confidence="confirmed_explicit_source_identifier",
        provenance_status="derived_from_technical_event_evidence",
        source_exports=source_exports,
        source_archive_paths=sorted({row.source_archive_path for row in observations}),
        source_conversation_ids=sorted({row.conversation_id for row in observations if row.conversation_id}),
        source_node_ids=sorted({row.node_id for row in observations if row.node_id}),
        source_message_ids=sorted({row.message_id for row in observations if row.message_id}),
        source_event_ids=sorted({row.technical_event_id for row in observations}),
        observation_ids=sorted({row.logical_entity_observation_id for row in observations}),
        observation_count=len(observations),
        source_presence_status=source_presence_status(source_exports),
        candidate_edges_created=False,
    )


def build_migration_records(
    previous_entities: Iterable[Mapping[str, Any]],
    records: Iterable[LogicalEntityRecord],
) -> list[LogicalEntityMigrationRecord]:
    """Map legacy rows mechanically; no similarity or physical lookup is used."""

    new_rows = list(records)
    by_kind_value: dict[tuple[str, str], list[LogicalEntityRecord]] = defaultdict(list)
    by_event_id: dict[str, list[LogicalEntityRecord]] = defaultdict(list)
    for record in new_rows:
        by_kind_value[(record.identifier_kind, record.explicit_identifier)].append(record)
        for event_id in record.source_event_ids:
            by_event_id[event_id].append(record)
    output: list[LogicalEntityMigrationRecord] = []
    for old in sorted(previous_entities, key=lambda row: str(row.get("logical_entity_id") or "")):
        old_type = old.get("entity_type")
        if old_type not in LEGACY_ENTITY_TYPES:
            continue
        old_id = string_or_empty(old.get("logical_entity_id"))
        old_kind = string_or_empty(old.get("identifier_kind"))
        old_value = string_or_empty(old.get("explicit_identifier"))
        old_events = sorted(value for value in old.get("source_event_ids", []) if isinstance(value, str))
        candidates = sorted(
            by_kind_value.get((old_kind, old_value), []), key=lambda row: row.logical_entity_id
        )
        if not candidates and old_type == "textdoc":
            uri_candidates = {
                row.logical_entity_id: row
                for event_id in old_events
                for row in by_event_id.get(event_id, [])
                if row.entity_type == EMBEDDED_CANMORE_URI_REFERENCE
            }
            candidates = [uri_candidates[key] for key in sorted(uri_candidates)]
            if candidates:
                output.extend(migration_rows_for(old_id, old_type, old_kind, old_value, old_events, candidates, "replaced_by_extracted_canmore_uri", "embedded Canmore URI extracted from the same technical event"))
                continue
        if candidates:
            status = "split_by_conversation_scope" if len(candidates) > 1 else "reclassified"
            reason = migration_reason(old_type, old_kind, candidates[0])
            output.extend(migration_rows_for(old_id, old_type, old_kind, old_value, old_events, candidates, status, reason))
        else:
            status = "deprecated_non_identifier_textdoc" if old_type == "textdoc" else "not_reconstructed_from_current_explicit_events"
            reason = "legacy textdoc value is not an identifier" if old_type == "textdoc" else "no exact current entity with the same source identifier"
            output.append(make_migration_row(old_id, old_type, old_kind, old_value, old_events, None, reason, status))
    return sorted(output, key=lambda row: (row.old_logical_entity_id, row.new_logical_entity_id or "", row.status))


def migration_rows_for(
    old_id: str,
    old_type: str,
    old_kind: str,
    old_value: str,
    old_events: list[str],
    candidates: Iterable[LogicalEntityRecord],
    status: str,
    reason: str,
) -> list[LogicalEntityMigrationRecord]:
    return [
        make_migration_row(old_id, old_type, old_kind, old_value, old_events, candidate, reason, status)
        for candidate in candidates
    ]


def make_migration_row(
    old_id: str,
    old_type: str,
    old_kind: str,
    old_value: str,
    old_events: list[str],
    candidate: LogicalEntityRecord | None,
    reason: str,
    status: str,
) -> LogicalEntityMigrationRecord:
    new_id = candidate.logical_entity_id if candidate else None
    seed = "|".join((old_id, new_id or "", status, reason))
    return LogicalEntityMigrationRecord(
        migration_id="logical-entity-migration:" + short_digest(seed),
        old_logical_entity_id=old_id,
        old_entity_type=old_type,
        old_identifier_kind=old_kind,
        old_explicit_identifier=old_value,
        old_source_event_ids=old_events,
        new_entity_type=candidate.entity_type if candidate else None,
        new_identifier_kind=candidate.identifier_kind if candidate else None,
        new_logical_entity_id=new_id,
        reason=reason,
        status=status,
    )


def migration_reason(old_type: str, old_kind: str, candidate: LogicalEntityRecord) -> str:
    if old_kind == "mnt_data_path":
        return "runtime path literal is now scoped to its conversation"
    if old_type == "generation":
        return "original_gen_id is a reference, not a confirmed generation"
    if old_type == "gizmo" and candidate.entity_type == PROJECT_CONTEXT_REFERENCE:
        return "g-p namespace is corroborated by exact space_projects values"
    return "legacy generic type replaced by the explicit source-field role"


def source_presence_status(source_exports: list[str]) -> str:
    sources = set(source_exports)
    if {"primary_2026_json", "historical_embedded_json"} <= sources:
        return "present_in_primary_and_historical_by_exact_identifier"
    if sources == {"primary_2026_json"}:
        return "primary_only_by_exact_identifier"
    if sources == {"historical_embedded_json"}:
        return "historical_only_by_exact_identifier"
    if sources == {"chat_html_auxiliary"}:
        return "auxiliary_only_by_exact_identifier"
    return "observed_in_multiple_or_mixed_sources_by_exact_identifier"


def unmaterialized_event_row(event: Mapping[str, Any]) -> dict[str, Any]:
    metadata = event.get("relevant_metadata") if isinstance(event.get("relevant_metadata"), dict) else {}
    reason = "textdoc_non_identifier_metadata_only" if metadata.get("textdoc_non_identifier_values") else "no_explicit_identifier_in_entity_construction_scope"
    return {
        "technical_event_id": event.get("technical_event_id"),
        "event_family": event.get("event_family"),
        "operation_observed": event.get("operation_observed"),
        "source_export": event.get("source_export"),
        "source_archive_path": event.get("source_archive_path"),
        "proof_path": event.get("proof_path"),
        "conversation_id": event.get("conversation_id"),
        "node_id": event.get("node_id"),
        "message_id": event.get("message_id"),
        "reason": reason,
    }


def textdoc_non_identifier_metadata_for_event(event: Mapping[str, Any]) -> list[dict[str, Any]]:
    metadata = event.get("relevant_metadata")
    if not isinstance(metadata, dict):
        return []
    values = metadata.get("textdoc_non_identifier_values")
    return [dict(value) for value in values if isinstance(value, dict)] if isinstance(values, list) else []


def emit_logical_entity_evidence(output_dir, result: LogicalEntityConstructionResult, *, execution: dict[str, Any] | None = None) -> None:
    append_jsonl(output_dir / "logical_entities.jsonl", [row.to_dict() for row in result.records])
    append_jsonl(output_dir / "logical_entity_observations.jsonl", [row.to_dict() for row in result.observations])
    append_jsonl(output_dir / "logical_entity_unmaterialized_events.jsonl", result.unmaterialized_events)
    append_jsonl(output_dir / "logical_entity_taxonomy_migration.jsonl", [row.to_dict() for row in result.migration_records])
    summary = result.summary_dict()
    if execution is not None:
        summary["execution"] = execution
    write_json(output_dir / "logical_entity_summary.json", summary)


def logical_entity_id(
    entity_type: str,
    identifier_kind: str,
    explicit_identifier: str,
    entity_subtype: str,
    scope_kind: str,
    scope_value: str,
) -> str:
    return "logical-entity:" + short_digest("|".join((entity_type, identifier_kind, explicit_identifier, entity_subtype, scope_kind, scope_value)))


def pointer_namespace(value: str) -> str:
    if value.startswith("sediment://"):
        return "sediment"
    if value.startswith("file-service://"):
        return "file_service"
    return "other"


def generation_format(value: str) -> str:
    if UUID_RE.fullmatch(value):
        return "uuid"
    if value.startswith("s_"):
        return "session_reference"
    if OPAQUE_GENERATION_RE.fullmatch(value):
        return "opaque"
    return "other"


def file_identifier_format(value: str) -> str:
    if value.startswith("file_"):
        return "file_underscore"
    if value.startswith("file-"):
        return "file_hyphen"
    return "other"


def gizmo_namespace(value: str) -> str:
    return "g_namespace" if value.startswith("g-") else "other"


def library_namespace(value: str) -> str:
    return "libfile" if value.startswith("libfile_") else "other"


def runtime_path_quality(value: str) -> str:
    if any(marker in value for marker in ("<", ">", "...")):
        return "malformed_or_truncated"
    if "{" in value or "}" in value:
        return "template"
    if value.endswith("/"):
        return "directory_like"
    if Path(value).suffix:
        return "file_like"
    return "unknown"


def runtime_scope(event: Mapping[str, Any]) -> tuple[str, str]:
    conversation_id = optional_string(event.get("conversation_id"))
    if conversation_id:
        return "conversation", conversation_id
    fallback = "|".join(
        str(event.get(field) or "") for field in ("source_export", "source_archive_path", "node_id", "message_id")
    )
    return "source_message_fallback", fallback


def short_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def collision_count(values: Iterable[str]) -> int:
    counts = Counter(values)
    return sum(count - 1 for count in counts.values() if count > 1)


def string_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({item for item in value if isinstance(item, str) and item})


def required_string(event: Mapping[str, Any], field: str) -> str:
    value = event.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"technical event missing required {field}")
    return value


def optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def string_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def legacy_entities_from_migration_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Recover the legacy migration baseline when a migrated pack is rerun."""
    recovered: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        old_id = row.get("old_logical_entity_id")
        old_type = row.get("old_entity_type")
        if not isinstance(old_id, str) or not isinstance(old_type, str) or old_type not in LEGACY_ENTITY_TYPES:
            continue
        recovered.setdefault(
            old_id,
            {
                "logical_entity_id": old_id,
                "entity_type": old_type,
                "identifier_kind": row.get("old_identifier_kind"),
                "explicit_identifier": row.get("old_explicit_identifier"),
                "source_event_ids": row.get("old_source_event_ids") if isinstance(row.get("old_source_event_ids"), list) else [],
            },
        )
    return [recovered[key] for key in sorted(recovered)]
