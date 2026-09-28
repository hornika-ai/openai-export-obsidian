from __future__ import annotations

"""Reconstruct forensic Gizmo and Project context profiles from pack evidence.

This module is deliberately downstream-only.  It reads structured evidence that
already exists in an emitted pack; it never opens an export ZIP, calls physical
resolution, or promotes candidate edges.  A profile is an index of observed
context identity and evidence, not a reconstructed GPT or Project configuration.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from .utils import append_jsonl, safe_filename, write_json


GIZMO_TYPE = "gizmo"
PROJECT_TYPE = "project"
PRIMARY = "primary_2026_json"
HISTORICAL = "historical_embedded_json"

FILE_RELATIONS = {
    "explicit_context_attachment",
    "explicit_context_library_reference",
    "observed_in_context_conversation",
    "used_in_context_conversation",
    "mentioned_or_cited_in_context_conversation",
    "candidate_context_membership",
    "unresolved",
}
INSTRUCTION_CLASSES = {
    "explicit_context_instruction",
    "candidate_context_instruction",
    "conversation_text_only",
    "manual_placeholder",
}
CONTEXT_EDGE_TYPES = {
    "project_context_matches_space_project": PROJECT_TYPE,
    "gizmo_context_matches_conversation_gpt": GIZMO_TYPE,
}
EXPLICIT_INSTRUCTION_KEYS = {
    "gizmo_instructions",
    "custom_gpt_instructions",
    "project_instructions",
    "workspace_instructions",
    "context_instructions",
}
NAME_KEYS = {"gizmo_name", "custom_gpt_name", "project_name", "workspace_name"}
INSTRUCTION_KEY_RE = re.compile(r"(?:^|[_-])(instruction|instructions|system_prompt|system_instruction)(?:$|[_-])", re.I)
INSTRUCTION_TITLE_RE = re.compile(r"\b(instruction|instructions|system prompt|system instruction)\b", re.I)
CONTEXT_ID_RE = re.compile(r"g(?:-p)?-[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class ContextProfileObservation:
    context_profile_observation_id: str
    context_profile_id: str
    context_type: str
    explicit_context_id: str
    observation_kind: str
    source_export: str
    source_record_id: str
    source_archive_path: str | None
    proof_path: str
    conversation_id: str | None
    message_id: str | None
    node_id: str | None
    candidate_edge_id: str | None
    evidence_set_ids: list[str]
    confidence: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextInstructionRecord:
    context_instruction_id: str
    context_profile_id: str
    context_type: str
    explicit_context_id: str
    instruction_classification: str
    source_record_id: str
    source_export: str
    source_archive_path: str | None
    proof_path: str
    conversation_id: str | None
    message_id: str | None
    node_id: str | None
    title_or_key: str | None
    text_value: str | None
    confidence: str
    status: str
    non_promotion_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextFileMembership:
    context_file_membership_id: str
    context_profile_id: str
    context_type: str
    explicit_context_id: str
    reference_or_entity_id: str
    file_identifier: str | None
    file_name: str | None
    relation_type: str
    source_record_id: str
    source_export: str
    conversation_ids: list[str]
    message_ids: list[str]
    proof_paths: list[str]
    evidence_set_ids: list[str]
    confidence: str
    status: str
    non_promotion_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextProfile:
    context_profile_id: str
    context_type: str
    explicit_context_id: str
    identity_status: str
    identity_source_kinds: list[str]
    explicit_names_or_titles: list[str]
    primary_observation_ids: list[str]
    historical_observation_ids: list[str]
    candidate_observation_ids: list[str]
    primary_conversation_ids: list[str]
    historical_conversation_ids: list[str]
    primary_message_ids: list[str]
    historical_message_ids: list[str]
    observed_models: list[str]
    observed_tools: list[str]
    observed_metadata_keys: list[str]
    explicit_instruction_ids: list[str]
    candidate_instruction_ids: list[str]
    file_membership_ids: list[str]
    file_membership_counts: dict[str, int]
    contradiction_ids: list[str]
    confidence: str
    provenance_status: str
    source_presence_status: str
    profile_status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContextProfileResult:
    gizmo_profiles: list[ContextProfile]
    project_profiles: list[ContextProfile]
    observations: list[ContextProfileObservation]
    instructions: list[ContextInstructionRecord]
    memberships: list[ContextFileMembership]
    contradictions: list[dict[str, Any]]

    def summary_dict(self) -> dict[str, Any]:
        profiles = self.gizmo_profiles + self.project_profiles
        return {
            "counts": {
                "gizmo_context_profiles": len(self.gizmo_profiles),
                "project_context_profiles": len(self.project_profiles),
                "context_profile_observations": len(self.observations),
                "context_instruction_records": len(self.instructions),
                "context_file_memberships": len(self.memberships),
                "context_profile_contradictions": len(self.contradictions),
            },
            "profiles_by_source_presence": dict(sorted(Counter(row.source_presence_status for row in profiles).items())),
            "profile_observation_kinds": dict(sorted(Counter(row.observation_kind for row in self.observations).items())),
            "instruction_classifications": dict(sorted(Counter(row.instruction_classification for row in self.instructions).items())),
            "file_membership_relations": dict(sorted(Counter(row.relation_type for row in self.memberships).items())),
            "file_membership_statuses": dict(sorted(Counter(row.status for row in self.memberships).items())),
            "profiles_with_explicit_names": sum(bool(row.explicit_names_or_titles) for row in profiles),
            "manual_instruction_placeholders": {
                "present_in_existing_obsidian_hubs": True,
                "treated_as_export_evidence": False,
            },
            "integrity": {
                "context_profile_id_collisions": collision_count(row.context_profile_id for row in profiles),
                "context_profile_observation_id_collisions": collision_count(
                    row.context_profile_observation_id for row in self.observations
                ),
                "context_instruction_id_collisions": collision_count(
                    row.context_instruction_id for row in self.instructions
                ),
                "context_file_membership_id_collisions": collision_count(
                    row.context_file_membership_id for row in self.memberships
                ),
            },
            "contract": {
                "raw_zip_reparsed": False,
                "candidate_edges_promoted_automatically": False,
                "physical_resolution_called": False,
                "historical_messages_merged": False,
                "context_file_membership_inferred_from_conversation_only": False,
            },
        }


class ContextProfileConstructor:
    """Create profiles strictly from previously emitted structured evidence."""

    def construct(self, evidence_dir: Path) -> ContextProfileResult:
        rows = {name: read_jsonl(evidence_dir / name) for name in SOURCE_FILES}
        conversations = rows["conversations.jsonl"]
        events = rows["technical_events.jsonl"]
        entities = rows["logical_entities.jsonl"]
        entity_observations = rows["logical_entity_observations.jsonl"]
        candidate_edges = rows["candidate_edges.jsonl"]
        evidence_sets = {row.get("evidence_set_id"): row for row in rows["candidate_edge_evidence_sets.jsonl"] if isinstance(row.get("evidence_set_id"), str)}
        messages = rows["messages.jsonl"]
        sources = rows["message_sources.jsonl"]
        textdocs = rows["textdocs.jsonl"]
        context_links = rows["context_links.jsonl"]

        profiles: dict[tuple[str, str], dict[str, Any]] = {}
        observations: list[ContextProfileObservation] = []
        context_to_conversations: dict[tuple[str, str], set[str]] = defaultdict(set)
        context_evidence_sets: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

        def ensure(context_type: str, context_id: str, source_kind: str) -> dict[str, Any]:
            key = (context_type, context_id)
            profile = profiles.get(key)
            if profile is None:
                profile = {
                    "context_profile_id": profile_id(context_type, context_id),
                    "context_type": context_type,
                    "explicit_context_id": context_id,
                    "identity_source_kinds": set(),
                    "names": set(),
                }
                profiles[key] = profile
            profile["identity_source_kinds"].add(source_kind)
            return profile

        def add_observation(
            *,
            context_type: str,
            context_id: str,
            observation_kind: str,
            source_export: str,
            source_record_id: str,
            source_archive_path: str | None,
            proof_path: str,
            conversation_id: str | None = None,
            message_id: str | None = None,
            node_id: str | None = None,
            candidate_edge_id: str | None = None,
            evidence_set_ids: Iterable[str] = (),
            confidence: str = "explicit",
            status: str = "observed",
        ) -> ContextProfileObservation:
            profile = ensure(context_type, context_id, observation_kind)
            evidence_ids = sorted({value for value in evidence_set_ids if isinstance(value, str) and value})
            seed = "|".join((profile["context_profile_id"], observation_kind, source_record_id, proof_path, candidate_edge_id or ""))
            record = ContextProfileObservation(
                context_profile_observation_id="context-profile-observation:" + digest(seed),
                context_profile_id=profile["context_profile_id"],
                context_type=context_type,
                explicit_context_id=context_id,
                observation_kind=observation_kind,
                source_export=source_export,
                source_record_id=source_record_id,
                source_archive_path=source_archive_path,
                proof_path=proof_path,
                conversation_id=conversation_id,
                message_id=message_id,
                node_id=node_id,
                candidate_edge_id=candidate_edge_id,
                evidence_set_ids=evidence_ids,
                confidence=confidence,
                status=status,
            )
            observations.append(record)
            if conversation_id:
                context_to_conversations[(context_type, context_id)].add(conversation_id)
                context_evidence_sets[(context_type, context_id)][conversation_id].update(evidence_ids)
            return record

        # The parse projection is a structured, explicit context occurrence.  It
        # may establish an observed conversation context, never file ownership.
        for row in conversations:
            conversation_id = string_or_none(row.get("conversation_id"))
            if not conversation_id or conversation_id == "unknown-conversation":
                continue
            for field, context_type in (("gpts", GIZMO_TYPE), ("space_projects", PROJECT_TYPE)):
                values = row.get(field)
                if not isinstance(values, list):
                    continue
                for index, value in enumerate(values):
                    if not isinstance(value, str):
                        continue
                    for context_id in context_ids_from_text(value, context_type):
                        add_observation(
                            context_type=context_type,
                            context_id=context_id,
                            observation_kind="conversation_context_projection",
                            source_export=PRIMARY,
                            source_record_id=conversation_id,
                            source_archive_path=string_or_none(row.get("source_archive_path")),
                            proof_path=f"conversations.jsonl[conversation_id={conversation_id}].{field}[{index}]",
                            conversation_id=conversation_id,
                        )

        # Legacy context links are retained as a compatibility witness.  They do
        # not gain a stronger proof level than their own structured output.
        for index, row in enumerate(context_links):
            kind = row.get("context_kind")
            context_type = GIZMO_TYPE if kind == "gpt" else PROJECT_TYPE if kind == "project" else None
            if context_type is None:
                continue
            value = string_or_none(row.get("context_link"))
            conversation_id = string_or_none(row.get("conversation_id"))
            if not value or not conversation_id:
                continue
            for context_id in context_ids_from_text(value, context_type):
                add_observation(
                    context_type=context_type,
                    context_id=context_id,
                    observation_kind="legacy_context_link_projection",
                    source_export=PRIMARY,
                    source_record_id=f"context_links:{index}",
                    source_archive_path=None,
                    proof_path=f"context_links.jsonl[{index}]",
                    conversation_id=conversation_id,
                    confidence="explicit",
                )

        entity_by_id = {
            row.get("logical_entity_id"): row
            for row in entities
            if isinstance(row.get("logical_entity_id"), str)
        }
        for row in entity_observations:
            entity_type = row.get("entity_type")
            context_type = PROJECT_TYPE if entity_type == "project_context_reference" else GIZMO_TYPE if entity_type == "gizmo_context_reference" else None
            context_id = string_or_none(row.get("explicit_identifier"))
            if context_type is None or not valid_context_id(context_type, context_id):
                continue
            add_observation(
                context_type=context_type,
                context_id=context_id,
                observation_kind="logical_entity_observation",
                source_export=string_or_none(row.get("source_export")) or "unknown",
                source_record_id=string_or_none(row.get("logical_entity_observation_id")) or "unknown-observation",
                source_archive_path=string_or_none(row.get("source_archive_path")),
                proof_path=string_or_none(row.get("proof_path")) or "logical_entity_observations.jsonl",
                conversation_id=string_or_none(row.get("conversation_id")),
                message_id=string_or_none(row.get("message_id")),
                node_id=string_or_none(row.get("node_id")),
            )

        # Candidate context edges are preserved as candidates.  Exact equality is
        # useful to enumerate observed conversations, but never a promotion.
        for row in candidate_edges:
            edge_type = row.get("edge_type")
            context_type = CONTEXT_EDGE_TYPES.get(edge_type)
            source_entity_id = string_or_none(row.get("source_entity_id"))
            entity = entity_by_id.get(source_entity_id)
            context_id = string_or_none(entity.get("explicit_identifier")) if isinstance(entity, dict) else None
            if context_type is None or not valid_context_id(context_type, context_id):
                continue
            target_id = string_or_none(row.get("target_id"))
            conversation_id = target_id.split(":", 1)[1] if target_id and ":" in target_id else None
            evidence_set_ids = [
                value
                for value in (row.get("source_evidence_set_id"), row.get("target_evidence_set_id"))
                if isinstance(value, str) and value in evidence_sets
            ]
            add_observation(
                context_type=context_type,
                context_id=context_id,
                observation_kind="candidate_context_edge",
                source_export="candidate_edge",
                source_record_id=string_or_none(row.get("candidate_edge_id")) or "unknown-candidate-edge",
                source_archive_path=None,
                proof_path=f"candidate_edges.jsonl[candidate_edge_id={row.get('candidate_edge_id')}]",
                conversation_id=conversation_id,
                candidate_edge_id=string_or_none(row.get("candidate_edge_id")),
                evidence_set_ids=evidence_set_ids,
                confidence=string_or_none(row.get("confidence")) or "low",
                status="candidate_not_promoted",
            )

        # Event-level gizmo context observations carry exact message provenance,
        # including historical messages, without merging them into primary ones.
        events_by_context: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            context_id = string_or_none(event.get("gizmo_id"))
            context_type = context_type_for_id(context_id)
            if context_type is None:
                continue
            profile = ensure(context_type, context_id, "technical_event_context")
            for key, value, _ in scoped_text_values(event.get("relevant_metadata"), "relevant_metadata"):
                if key in NAME_KEYS:
                    profile["names"].add(value)
            events_by_context[(context_type, context_id)].append(event)
            add_observation(
                context_type=context_type,
                context_id=context_id,
                observation_kind="technical_event_context",
                source_export=string_or_none(event.get("source_export")) or "unknown",
                source_record_id=string_or_none(event.get("technical_event_id")) or "unknown-technical-event",
                source_archive_path=string_or_none(event.get("source_archive_path")),
                proof_path=string_or_none(event.get("proof_path")) or "technical_events.jsonl",
                conversation_id=string_or_none(event.get("conversation_id")),
                message_id=string_or_none(event.get("message_id")),
                node_id=string_or_none(event.get("node_id")),
            )

        observations = dedupe_observations(observations)
        observations_by_profile: dict[str, list[ContextProfileObservation]] = defaultdict(list)
        for row in observations:
            observations_by_profile[row.context_profile_id].append(row)

        instructions = build_instruction_records(
            profiles=profiles,
            observations_by_profile=observations_by_profile,
            events_by_context=events_by_context,
            messages=messages,
            textdocs=textdocs,
        )
        memberships = build_file_memberships(
            profiles=profiles,
            context_to_conversations=context_to_conversations,
            context_evidence_sets=context_evidence_sets,
            message_sources=sources,
        )
        contradictions = build_contradictions(profiles, events_by_context)

        profiles_out = build_profiles(
            profiles=profiles,
            observations_by_profile=observations_by_profile,
            events_by_context=events_by_context,
            instructions=instructions,
            memberships=memberships,
            contradictions=contradictions,
        )
        gizmo_profiles = [row for row in profiles_out if row.context_type == GIZMO_TYPE]
        project_profiles = [row for row in profiles_out if row.context_type == PROJECT_TYPE]
        return ContextProfileResult(
            gizmo_profiles=gizmo_profiles,
            project_profiles=project_profiles,
            observations=observations,
            instructions=instructions,
            memberships=memberships,
            contradictions=contradictions,
        )


SOURCE_FILES = (
    "conversations.jsonl",
    "messages.jsonl",
    "historical_messages.jsonl",
    "technical_events.jsonl",
    "logical_entities.jsonl",
    "logical_entity_observations.jsonl",
    "candidate_edges.jsonl",
    "candidate_edge_evidence_sets.jsonl",
    "message_sources.jsonl",
    "references.jsonl",
    "physical_resolutions.jsonl",
    "context_links.jsonl",
    "textdocs.jsonl",
    "runtime_artifacts.jsonl",
)


def build_instruction_records(*, profiles, observations_by_profile, events_by_context, messages, textdocs) -> list[ContextInstructionRecord]:
    records: list[ContextInstructionRecord] = []
    profile_conversations = {
        profile["context_profile_id"]: {
            row.conversation_id for row in observations_by_profile.get(profile["context_profile_id"], []) if row.conversation_id
        }
        for profile in profiles.values()
    }
    events_by_message = {
        (string_or_none(row.get("conversation_id")), string_or_none(row.get("message_id"))): row
        for rows in events_by_context.values()
        for row in rows
        if string_or_none(row.get("message_id"))
    }

    for (context_type, context_id), events in events_by_context.items():
        profile = profiles[(context_type, context_id)]
        for event in events:
            for key, value, path in scoped_text_values(event.get("relevant_metadata"), "relevant_metadata"):
                if key not in EXPLICIT_INSTRUCTION_KEYS:
                    continue
                records.append(
                    make_instruction(
                        profile,
                        "explicit_context_instruction",
                        string_or_none(event.get("technical_event_id")) or "unknown-technical-event",
                        string_or_none(event.get("source_export")) or "unknown",
                        string_or_none(event.get("source_archive_path")),
                        f"{event.get('proof_path')}.{path}",
                        string_or_none(event.get("conversation_id")),
                        string_or_none(event.get("message_id")),
                        string_or_none(event.get("node_id")),
                        key,
                        value,
                        "explicit",
                        "observed",
                        None,
                    )
                )

    # A metadata field with an instruction-like key is only a candidate unless
    # that field itself names a GPT/project scope.  Message prose is never read.
    for profile in profiles.values():
        context_id = profile["explicit_context_id"]
        context_type = profile["context_type"]
        known_conversations = profile_conversations[profile["context_profile_id"]]
        for message in messages:
            conversation_id = string_or_none(message.get("conversation_id"))
            if conversation_id not in known_conversations:
                continue
            event = events_by_message.get((conversation_id, string_or_none(message.get("message_id"))))
            direct_event_context = string_or_none(event.get("gizmo_id")) if event else None
            for key, value, path in scoped_text_values(message.get("raw_metadata"), "raw_metadata"):
                if key in EXPLICIT_INSTRUCTION_KEYS and direct_event_context == context_id:
                    continue
                if not INSTRUCTION_KEY_RE.search(key):
                    continue
                records.append(
                    make_instruction(
                        profile,
                        "candidate_context_instruction",
                        string_or_none(message.get("message_id")) or "unknown-message",
                        PRIMARY,
                        None,
                        f"messages.jsonl[message_id={message.get('message_id')}].{path}",
                        conversation_id,
                        string_or_none(message.get("message_id")),
                        string_or_none(message.get("node_id")),
                        key,
                        value,
                        "low",
                        "candidate_not_promoted",
                        "metadata key is instruction-like but GPT/project scope is not demonstrated",
                    )
                )
        for textdoc in textdocs:
            conversation_id = string_or_none(textdoc.get("conversation_id"))
            title = string_or_none(textdoc.get("title"))
            if conversation_id not in known_conversations or not title or not INSTRUCTION_TITLE_RE.search(title):
                continue
            records.append(
                make_instruction(
                    profile,
                    "candidate_context_instruction",
                    string_or_none(textdoc.get("textdoc_ref_id")) or "unknown-textdoc",
                    PRIMARY,
                    None,
                    string_or_none(textdoc.get("proof_path")) or "textdocs.jsonl",
                    conversation_id,
                    string_or_none(textdoc.get("message_id")),
                    string_or_none(textdoc.get("node_id")),
                    title,
                    None,
                    "low",
                    "candidate_not_promoted",
                    "instruction-like Textdoc title was observed in a context conversation, not in context configuration",
                )
            )
    return sorted(dedupe_by_id(records, "context_instruction_id"), key=lambda row: row.context_instruction_id)


def build_file_memberships(*, profiles, context_to_conversations, context_evidence_sets, message_sources) -> list[ContextFileMembership]:
    memberships: list[ContextFileMembership] = []
    for (context_type, context_id), profile in sorted(profiles.items()):
        conversation_ids = context_to_conversations.get((context_type, context_id), set())
        for source in message_sources:
            conversation_id = string_or_none(source.get("conversation_id"))
            if conversation_id not in conversation_ids:
                continue
            relation_type, confidence, status, reason = file_relation_for_source(source)
            if relation_type not in FILE_RELATIONS:
                continue
            source_id = string_or_none(source.get("source_ref_id")) or stable_source_id(source)
            identifier = string_or_none(source.get("file_id")) or string_or_none(source.get("strict_library_file_id")) or string_or_none(source.get("title")) or source_id
            seed = "|".join((profile["context_profile_id"], source_id, relation_type))
            memberships.append(
                ContextFileMembership(
                    context_file_membership_id="context-file-membership:" + digest(seed),
                    context_profile_id=profile["context_profile_id"],
                    context_type=context_type,
                    explicit_context_id=context_id,
                    reference_or_entity_id=source_id,
                    file_identifier=string_or_none(source.get("file_id")) or string_or_none(source.get("strict_library_file_id")),
                    file_name=string_or_none(source.get("title")),
                    relation_type=relation_type,
                    source_record_id=source_id,
                    source_export=PRIMARY,
                    conversation_ids=[conversation_id],
                    message_ids=sorted({value for value in [string_or_none(source.get("message_id"))] if value}),
                    proof_paths=sorted({value for value in [string_or_none(source.get("proof_path"))] if value}),
                    evidence_set_ids=sorted(context_evidence_sets[(context_type, context_id)].get(conversation_id, set())),
                    confidence=confidence,
                    status=status,
                    non_promotion_reason=reason,
                )
            )
    return sorted(dedupe_by_id(memberships, "context_file_membership_id"), key=lambda row: row.context_file_membership_id)


def file_relation_for_source(source: dict[str, Any]) -> tuple[str, str, str, str | None]:
    payload_status = string_or_none(source.get("payload_status"))
    source_kind = string_or_none(source.get("source_kind")) or ""
    candidate_role = string_or_none(source.get("candidate_role"))
    candidate_confidence = string_or_none(source.get("candidate_confidence"))
    if payload_status in {"not_exported", "ambiguous"}:
        return "unresolved", "explicit", "observed", "source reference has no uniquely resolved exported payload"
    if candidate_role and candidate_confidence == "heuristic":
        return "candidate_context_membership", "low", "candidate_not_promoted", "existing parser marks this only as a heuristic context role"
    if source_kind == "cited_file":
        return "mentioned_or_cited_in_context_conversation", "explicit", "observed", "citation in a context conversation does not establish context membership"
    if source_kind == "file_reference":
        return "mentioned_or_cited_in_context_conversation", "explicit", "observed", "file reference in a context conversation does not establish context membership"
    if source_kind in {"uploaded_file", "inline_asset"}:
        return "observed_in_context_conversation", "explicit", "observed", "file was observed in a context conversation; no context attachment field was exported"
    return "observed_in_context_conversation", "explicit", "observed", "source was observed in a context conversation; no context attachment field was exported"


def build_profiles(*, profiles, observations_by_profile, events_by_context, instructions, memberships, contradictions) -> list[ContextProfile]:
    instructions_by_profile: dict[str, list[ContextInstructionRecord]] = defaultdict(list)
    memberships_by_profile: dict[str, list[ContextFileMembership]] = defaultdict(list)
    contradictions_by_profile: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in instructions:
        instructions_by_profile[row.context_profile_id].append(row)
    for row in memberships:
        memberships_by_profile[row.context_profile_id].append(row)
    for row in contradictions:
        if isinstance(row.get("context_profile_id"), str):
            contradictions_by_profile[row["context_profile_id"]].append(row)

    output: list[ContextProfile] = []
    for (_, _), profile in sorted(profiles.items()):
        profile_id_value = profile["context_profile_id"]
        observations = observations_by_profile.get(profile_id_value, [])
        source_exports = {row.source_export for row in observations}
        primary = sorted(row.context_profile_observation_id for row in observations if row.source_export == PRIMARY)
        historical = sorted(row.context_profile_observation_id for row in observations if row.source_export == HISTORICAL)
        candidate = sorted(row.context_profile_observation_id for row in observations if row.status == "candidate_not_promoted")
        primary_conversations = sorted({row.conversation_id for row in observations if row.source_export == PRIMARY and row.conversation_id})
        historical_conversations = sorted({row.conversation_id for row in observations if row.source_export == HISTORICAL and row.conversation_id})
        primary_messages = sorted({row.message_id for row in observations if row.source_export == PRIMARY and row.message_id})
        historical_messages = sorted({row.message_id for row in observations if row.source_export == HISTORICAL and row.message_id})
        events = events_by_context.get((profile["context_type"], profile["explicit_context_id"]), [])
        models = sorted({value for event in events for value in (string_or_none(event.get("model_slug")), string_or_none(event.get("default_model_slug"))) if value})
        tools = sorted({value for event in events for value in [string_or_none(event.get("recipient"))] if value})
        metadata_keys = sorted({key for event in events for key, _, _ in scoped_text_values(event.get("relevant_metadata"), "relevant_metadata")})
        profile_instructions = instructions_by_profile[profile_id_value]
        profile_memberships = memberships_by_profile[profile_id_value]
        source_presence = "present_in_primary_and_historical" if primary and historical else "primary_only" if primary else "historical_only" if historical else "candidate_or_projection_only"
        output.append(
            ContextProfile(
                context_profile_id=profile_id_value,
                context_type=profile["context_type"],
                explicit_context_id=profile["explicit_context_id"],
                identity_status="explicit_identifier_observed",
                identity_source_kinds=sorted(profile["identity_source_kinds"]),
                explicit_names_or_titles=sorted(profile["names"]),
                primary_observation_ids=primary,
                historical_observation_ids=historical,
                candidate_observation_ids=candidate,
                primary_conversation_ids=primary_conversations,
                historical_conversation_ids=historical_conversations,
                primary_message_ids=primary_messages,
                historical_message_ids=historical_messages,
                observed_models=models,
                observed_tools=tools,
                observed_metadata_keys=metadata_keys,
                explicit_instruction_ids=sorted(row.context_instruction_id for row in profile_instructions if row.instruction_classification == "explicit_context_instruction"),
                candidate_instruction_ids=sorted(row.context_instruction_id for row in profile_instructions if row.instruction_classification == "candidate_context_instruction"),
                file_membership_ids=sorted(row.context_file_membership_id for row in profile_memberships),
                file_membership_counts=dict(sorted(Counter(row.relation_type for row in profile_memberships).items())),
                contradiction_ids=sorted(str(row.get("context_profile_contradiction_id")) for row in contradictions_by_profile[profile_id_value]),
                confidence="explicit_identifier_with_source_separated_observations",
                provenance_status="derived_from_existing_structured_evidence",
                source_presence_status=source_presence,
                profile_status="observed_not_configuration_reconstructed",
            )
        )
    return output


def build_contradictions(profiles, events_by_context) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (context_type, context_id), profile in sorted(profiles.items()):
        # This is intentionally narrow: only mutually different explicit names
        # from an already scoped event would be contradictory.  No title/date
        # comparison is performed.
        values: set[str] = set()
        proofs: list[str] = []
        for event in events_by_context.get((context_type, context_id), []):
            for key, value, path in scoped_text_values(event.get("relevant_metadata"), "relevant_metadata"):
                if key in NAME_KEYS:
                    values.add(value)
                    proofs.append(f"{event.get('proof_path')}.{path}")
        if len(values) > 1:
            seed = "|".join((profile["context_profile_id"], *sorted(values)))
            rows.append(
                {
                    "context_profile_contradiction_id": "context-profile-contradiction:" + digest(seed),
                    "context_profile_id": profile["context_profile_id"],
                    "context_type": context_type,
                    "explicit_context_id": context_id,
                    "contradiction_type": "multiple_explicit_context_names",
                    "values": sorted(values),
                    "proof_paths": sorted(set(proofs)),
                    "confidence": "explicit",
                    "status": "unresolved_no_source_winner",
                }
            )
    return rows


def make_instruction(profile, classification, source_record_id, source_export, source_archive_path, proof_path, conversation_id, message_id, node_id, title_or_key, text_value, confidence, status, reason):
    seed = "|".join((profile["context_profile_id"], classification, source_record_id, proof_path, title_or_key or ""))
    return ContextInstructionRecord(
        context_instruction_id="context-instruction:" + digest(seed),
        context_profile_id=profile["context_profile_id"],
        context_type=profile["context_type"],
        explicit_context_id=profile["explicit_context_id"],
        instruction_classification=classification,
        source_record_id=source_record_id,
        source_export=source_export,
        source_archive_path=source_archive_path,
        proof_path=proof_path,
        conversation_id=conversation_id,
        message_id=message_id,
        node_id=node_id,
        title_or_key=title_or_key,
        text_value=text_value,
        confidence=confidence,
        status=status,
        non_promotion_reason=reason,
    )


def scoped_text_values(value: Any, path: str) -> Iterable[tuple[str, str, str]]:
    if isinstance(value, dict):
        for key in sorted(value):
            child = value[key]
            child_path = f"{path}.{key}"
            if isinstance(child, str) and child.strip():
                yield key, child, child_path
            else:
                yield from scoped_text_values(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from scoped_text_values(child, f"{path}[{index}]")


def context_ids_from_text(value: str, context_type: str) -> list[str]:
    return [identifier for identifier in CONTEXT_ID_RE.findall(value) if valid_context_id(context_type, identifier)]


def context_type_for_id(value: str | None) -> str | None:
    if not value:
        return None
    return PROJECT_TYPE if value.startswith("g-p-") else GIZMO_TYPE if value.startswith("g-") else None


def valid_context_id(context_type: str, value: str | None) -> bool:
    return bool(value and ((context_type == PROJECT_TYPE and value.startswith("g-p-")) or (context_type == GIZMO_TYPE and value.startswith("g-") and not value.startswith("g-p-"))))


def profile_id(context_type: str, context_id: str) -> str:
    return f"{context_type}-context-profile:" + digest(f"{context_type}|{context_id}")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def stable_source_id(row: dict[str, Any]) -> str:
    return "context-source:" + digest(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def collision_count(values: Iterable[str]) -> int:
    counts = Counter(values)
    return sum(count - 1 for count in counts.values() if count > 1)


def dedupe_by_id(rows: Iterable[Any], field: str) -> list[Any]:
    unique = {getattr(row, field): row for row in rows}
    return [unique[key] for key in sorted(unique)]


def dedupe_observations(rows: Iterable[ContextProfileObservation]) -> list[ContextProfileObservation]:
    return dedupe_by_id(rows, "context_profile_observation_id")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            result.append(row)
    return result


def emit_context_profile_evidence(output_dir: Path, result: ContextProfileResult, *, execution: dict[str, Any] | None = None) -> None:
    append_jsonl(output_dir / "gizmo_context_profiles.jsonl", [row.to_dict() for row in result.gizmo_profiles])
    append_jsonl(output_dir / "project_context_profiles.jsonl", [row.to_dict() for row in result.project_profiles])
    append_jsonl(output_dir / "context_profile_observations.jsonl", [row.to_dict() for row in result.observations])
    append_jsonl(output_dir / "context_instruction_candidates.jsonl", [row.to_dict() for row in result.instructions])
    append_jsonl(output_dir / "context_file_memberships.jsonl", [row.to_dict() for row in result.memberships])
    append_jsonl(output_dir / "context_profile_contradictions.jsonl", result.contradictions)
    summary = result.summary_dict()
    if execution:
        summary["execution"] = execution
    write_json(output_dir / "context_profile_summary.json", summary)


def emit_context_profile_obsidian_projection(pack: Path, result: ContextProfileResult) -> int:
    """Optionally render separate forensic profile notes without touching hubs.

    Existing GPT/project hubs remain the compatibility navigation surface and
    retain their manual sections.  These notes are a derived inspection view.
    """
    instructions_by_profile: dict[str, list[ContextInstructionRecord]] = defaultdict(list)
    memberships_by_profile: dict[str, list[ContextFileMembership]] = defaultdict(list)
    observations_by_profile: dict[str, list[ContextProfileObservation]] = defaultdict(list)
    for row in result.instructions:
        instructions_by_profile[row.context_profile_id].append(row)
    for row in result.memberships:
        memberships_by_profile[row.context_profile_id].append(row)
    for row in result.observations:
        observations_by_profile[row.context_profile_id].append(row)

    count = 0
    for profile in result.gizmo_profiles + result.project_profiles:
        folder = "GPTs" if profile.context_type == GIZMO_TYPE else "Projects"
        title_prefix = "Gizmo Context Profile" if profile.context_type == GIZMO_TYPE else "Project Context Profile"
        target = pack / "30_Contexts" / "Forensic Profiles" / folder
        target.mkdir(parents=True, exist_ok=True)
        path = target / safe_filename(f"{title_prefix} - {profile.explicit_context_id}.md")
        path.write_text(
            render_context_profile_markdown(
                profile,
                observations_by_profile[profile.context_profile_id],
                instructions_by_profile[profile.context_profile_id],
                memberships_by_profile[profile.context_profile_id],
            ),
            encoding="utf-8",
        )
        count += 1
    return count


def render_context_profile_markdown(
    profile: ContextProfile,
    observations: list[ContextProfileObservation],
    instructions: list[ContextInstructionRecord],
    memberships: list[ContextFileMembership],
) -> str:
    title = f"{'Gizmo' if profile.context_type == GIZMO_TYPE else 'Project'} Context Profile - {profile.explicit_context_id}"
    lines = [
        "---",
        "type: openai_context_profile",
        f"context_type: {profile.context_type}",
        f"context_profile_id: {profile.context_profile_id}",
        f"explicit_context_id: {profile.explicit_context_id}",
        "status: observed_not_configuration_reconstructed",
        "---",
        "",
        f"# {title}",
        "",
        "> [!warning] Forensic boundary",
        "> This profile is derived from structured export evidence. It does not reconstruct a GPT/project configuration, promote candidate edges, or attach physical payloads.",
        "",
        "## Instructions explicitement observées",
        "",
    ]
    explicit = [row for row in instructions if row.instruction_classification == "explicit_context_instruction"]
    if explicit:
        lines.extend(f"- `{row.title_or_key or 'instruction'}` · `{row.proof_path}`" for row in explicit)
    else:
        lines.append("Aucune instruction de contexte explicitement observée.")
    lines.extend(["", "## Instructions candidates", ""])
    candidates = [row for row in instructions if row.instruction_classification == "candidate_context_instruction"]
    if candidates:
        lines.extend(
            f"- `{row.title_or_key or 'candidate'}` · `{row.proof_path}` · {row.non_promotion_reason or 'candidate only'}"
            for row in candidates
        )
    else:
        lines.append("Aucune instruction candidate observée.")
    lines.extend(["", "## Références de fichiers par relation", ""])
    grouped: dict[str, list[ContextFileMembership]] = defaultdict(list)
    for row in memberships:
        grouped[row.relation_type].append(row)
    for relation in sorted(FILE_RELATIONS):
        lines.extend([f"### {relation}", ""])
        rows = grouped.get(relation, [])
        if rows:
            lines.extend(
                f"- `{row.file_name or row.file_identifier or row.reference_or_entity_id}` · `{row.proof_paths[0]}` · {row.status}"
                for row in rows
            )
        else:
            lines.append("Aucune relation observée.")
        lines.append("")
    lines.extend(["## Conversations associées", ""])
    for source, values in (
        ("Primaire", profile.primary_conversation_ids),
        ("Historique", profile.historical_conversation_ids),
    ):
        lines.append(f"### {source}")
        lines.append("")
        if values:
            lines.extend(f"- `{value}`" for value in values)
        else:
            lines.append("Aucune observation.")
        lines.append("")
    lines.extend(
        [
            "## Incertitudes",
            "",
            "- Les fichiers observés dans une conversation de contexte ne sont pas promus comme pièces jointes du GPT/projet.",
            "- Les candidats restent non promus.",
            "- Les observations historiques restent séparées des observations primaires.",
            "",
            "## Preuves",
            "",
            "- `90_Evidence/context_profile_observations.jsonl`",
            "- `90_Evidence/context_instruction_candidates.jsonl`",
            "- `90_Evidence/context_file_memberships.jsonl`",
            "- `90_Evidence/context_profile_contradictions.jsonl`",
            "",
        ]
    )
    return "\n".join(lines)
