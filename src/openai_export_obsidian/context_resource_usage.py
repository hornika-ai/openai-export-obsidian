from __future__ import annotations

"""Build an observation-only context resource usage graph from pack evidence.

This is deliberately downstream from ``context-profiles``.  It does not open an
export archive, resolve a payload, promote a candidate edge, or treat repeated
usage as proof that a file belongs to a GPT/project.  Owner confirmations are
accepted only as an explicit, separately sourced JSONL input.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from itertools import combinations
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .utils import append_jsonl, write_json


PRIMARY = "primary_2026_json"
OWNER_SOURCE = "external_owner_confirmation"
PROFILE_FILES = (
    "gizmo_context_profiles.jsonl",
    "project_context_profiles.jsonl",
    "context_profile_observations.jsonl",
    "context_file_memberships.jsonl",
    "message_sources.jsonl",
)
OWNER_CONFIRMATION_INPUT = "context_resource_owner_confirmations.jsonl"


@dataclass(frozen=True)
class ContextResourceNode:
    context_resource_node_id: str
    node_kind: str
    context_profile_id: str | None
    context_type: str | None
    explicit_context_id: str | None
    conversation_id: str | None
    message_id: str | None
    resource_identity_kind: str | None
    resource_identifier: str | None
    display_name: str | None
    display_name_aliases: list[str]
    source_export: str | None
    identity_scope: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextResourceEdge:
    context_resource_edge_id: str
    edge_type: str
    source_node_id: str
    target_node_id: str
    context_profile_id: str | None
    conversation_id: str | None
    message_id: str | None
    source_record_ids: list[str]
    source_export: str
    relation_type: str
    proof_paths: list[str]
    evidence_set_ids: list[str]
    matching_rule: str
    confidence: str
    status: str
    non_promotion_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OwnerConfirmationClaim:
    owner_confirmation_id: str
    context_profile_id: str
    context_type: str
    explicit_context_id: str
    declared_resource_node_id: str
    resource_name: str
    resource_identifier: str | None
    confirmation_scope: str
    source_kind: str
    source_export: str
    proof_path: str
    statement: str | None
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextResourceUsageMetric:
    context_resource_usage_id: str
    context_profile_id: str
    context_type: str
    explicit_context_id: str
    observed_resource_node_id: str | None
    resource_identity_kind: str
    resource_identifier: str | None
    resource_name: str | None
    resource_name_aliases: list[str]
    source_export: str
    source_kinds: list[str]
    source_namespaces: list[str]
    context_conversation_denominator: int
    observed_context_conversation_denominator: int
    conversation_ids: list[str]
    direct_context_conversation_ids: list[str]
    message_ids: list[str]
    distinct_conversation_count: int
    distinct_direct_context_conversation_count: int
    distinct_message_count: int
    reference_occurrence_count: int
    relative_conversation_frequency: float | None
    relative_observed_context_frequency: float | None
    relation_types: list[str]
    owner_confirmation_ids: list[str]
    export_corroboration_status: str
    observed_in_other_gizmo_context_ids: list[str]
    observed_in_other_project_context_ids: list[str]
    resource_identity_caution: str
    forensic_status: str
    proof_paths: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextResourceCooccurrence:
    context_resource_cooccurrence_id: str
    context_profile_id: str
    context_type: str
    explicit_context_id: str
    left_resource_node_id: str
    right_resource_node_id: str
    same_conversation_ids: list[str]
    same_message_scopes: list[str]
    same_conversation_count: int
    same_message_count: int
    status: str
    interpretation_limit: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContextResourceUsageResult:
    nodes: list[ContextResourceNode]
    edges: list[ContextResourceEdge]
    owner_claims: list[OwnerConfirmationClaim]
    metrics: list[ContextResourceUsageMetric]
    cooccurrences: list[ContextResourceCooccurrence]

    def summary_dict(self) -> dict[str, Any]:
        return {
            "counts": {
                "context_resource_nodes": len(self.nodes),
                "context_resource_edges": len(self.edges),
                "owner_confirmation_claims": len(self.owner_claims),
                "context_resource_usage_metrics": len(self.metrics),
                "context_resource_cooccurrences": len(self.cooccurrences),
            },
            "nodes_by_kind": dict(sorted(Counter(row.node_kind for row in self.nodes).items())),
            "edges_by_type": dict(sorted(Counter(row.edge_type for row in self.edges).items())),
            "metrics_by_forensic_status": dict(sorted(Counter(row.forensic_status for row in self.metrics).items())),
            "metrics_by_identity_kind": dict(sorted(Counter(row.resource_identity_kind for row in self.metrics).items())),
            "integrity": {
                "node_id_collisions": collision_count(row.context_resource_node_id for row in self.nodes),
                "edge_id_collisions": collision_count(row.context_resource_edge_id for row in self.edges),
                "owner_confirmation_id_collisions": collision_count(row.owner_confirmation_id for row in self.owner_claims),
                "usage_metric_id_collisions": collision_count(row.context_resource_usage_id for row in self.metrics),
                "cooccurrence_id_collisions": collision_count(row.context_resource_cooccurrence_id for row in self.cooccurrences),
            },
            "contract": {
                "raw_zip_reparsed": False,
                "physical_resolution_called": False,
                "candidate_edges_created": False,
                "canonical_context_memberships_created": False,
                "historical_messages_merged": False,
                "owner_confirmation_is_external_input": True,
                "name_equality_is_not_physical_identity": True,
            },
        }


class ContextResourceUsageConstructor:
    """Construct a typed resource-usage graph from existing pack evidence."""

    def construct(
        self,
        evidence_dir: Path,
        *,
        owner_confirmations_path: Path | None = None,
    ) -> ContextResourceUsageResult:
        rows = {name: read_jsonl(evidence_dir / name) for name in PROFILE_FILES}
        profiles = rows["gizmo_context_profiles.jsonl"] + rows["project_context_profiles.jsonl"]
        profile_by_id = {
            row.get("context_profile_id"): row
            for row in profiles
            if isinstance(row.get("context_profile_id"), str)
        }
        observations_by_profile_conversation: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows["context_profile_observations.jsonl"]:
            profile_id = string_or_none(row.get("context_profile_id"))
            conversation_id = string_or_none(row.get("conversation_id"))
            if profile_id and conversation_id and row.get("status") != "candidate_not_promoted":
                observations_by_profile_conversation[(profile_id, conversation_id)].append(row)

        memberships_by_source: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows["context_file_memberships.jsonl"]:
            profile_id = string_or_none(row.get("context_profile_id"))
            source_id = string_or_none(row.get("source_record_id"))
            if profile_id and source_id:
                memberships_by_source[(profile_id, source_id)] = row

        nodes: dict[str, ContextResourceNode] = {}
        edges: dict[str, ContextResourceEdge] = {}
        uses: list[dict[str, Any]] = []

        def add_node(node: ContextResourceNode) -> None:
            existing = nodes.get(node.context_resource_node_id)
            if existing is None or existing == node:
                nodes[node.context_resource_node_id] = node
                return
            if existing.node_kind == node.node_kind == "observed_resource_reference" and all(
                getattr(existing, key) == getattr(node, key)
                for key in (
                    "context_resource_node_id", "context_profile_id", "context_type", "explicit_context_id",
                    "conversation_id", "message_id", "resource_identity_kind", "resource_identifier",
                    "identity_scope", "status",
                )
            ):
                aliases = sorted({value for value in existing.display_name_aliases + node.display_name_aliases if value})
                names = [value for value in (existing.display_name, node.display_name, *aliases) if value]
                nodes[node.context_resource_node_id] = ContextResourceNode(
                    **{
                        **existing.to_dict(),
                        "display_name": sorted(names)[0] if names else None,
                        "display_name_aliases": aliases,
                        "source_export": source_export_for_values(existing.source_export, node.source_export),
                    }
                )
                return
            raise ValueError(f"non-deterministic node collision: {node.context_resource_node_id}")

        def add_edge(edge: ContextResourceEdge) -> None:
            existing = edges.get(edge.context_resource_edge_id)
            if existing is None or existing == edge:
                edges[edge.context_resource_edge_id] = edge
                return
            # Several source records can legitimately support the same structural
            # conversation -> message edge.  Keep one edge and retain every proof.
            comparable = (
                "edge_type", "source_node_id", "target_node_id", "context_profile_id",
                "conversation_id", "message_id", "source_export", "relation_type",
                "matching_rule", "confidence", "status", "non_promotion_reason",
            )
            if any(getattr(existing, key) != getattr(edge, key) for key in comparable):
                raise ValueError(f"non-deterministic edge collision: {edge.context_resource_edge_id}")
            edges[edge.context_resource_edge_id] = ContextResourceEdge(
                **{
                    **existing.to_dict(),
                    "source_record_ids": sorted(set(existing.source_record_ids) | set(edge.source_record_ids)),
                    "proof_paths": sorted(set(existing.proof_paths) | set(edge.proof_paths)),
                    "evidence_set_ids": sorted(set(existing.evidence_set_ids) | set(edge.evidence_set_ids)),
                }
            )

        for profile_id, profile in sorted(profile_by_id.items()):
            context_type = string_or_none(profile.get("context_type")) or "unknown"
            context_id = string_or_none(profile.get("explicit_context_id")) or "unknown"
            profile_node_id = context_node_id(profile_id)
            add_node(
                ContextResourceNode(
                    context_resource_node_id=profile_node_id,
                    node_kind="context_profile",
                    context_profile_id=profile_id,
                    context_type=context_type,
                    explicit_context_id=context_id,
                    conversation_id=None,
                    message_id=None,
                    resource_identity_kind=None,
                    resource_identifier=None,
                    display_name=None,
                    display_name_aliases=[],
                    source_export=None,
                    identity_scope="explicit_context_identifier",
                    status="observed",
                )
            )
            for (observed_profile_id, conversation_id), observation_rows in sorted(observations_by_profile_conversation.items()):
                if observed_profile_id != profile_id:
                    continue
                conversation_node = conversation_node_id(profile_id, conversation_id)
                add_node(
                    ContextResourceNode(
                        context_resource_node_id=conversation_node,
                        node_kind="context_conversation",
                        context_profile_id=profile_id,
                        context_type=context_type,
                        explicit_context_id=context_id,
                        conversation_id=conversation_id,
                        message_id=None,
                        resource_identity_kind=None,
                        resource_identifier=None,
                        display_name=None,
                        display_name_aliases=[],
                        source_export=source_export_for_rows(observation_rows),
                        identity_scope="context_profile_plus_conversation",
                        status="observed",
                    )
                )
                proof_paths = sorted({value for row in observation_rows for value in [string_or_none(row.get("proof_path"))] if value})
                source_ids = sorted({value for row in observation_rows for value in [string_or_none(row.get("context_profile_observation_id"))] if value})
                add_edge(
                    make_edge(
                        edge_type="context_observed_in_conversation",
                        source_node_id=profile_node_id,
                        target_node_id=conversation_node,
                        context_profile_id=profile_id,
                        conversation_id=conversation_id,
                        message_id=None,
                        source_record_ids=source_ids,
                        source_export=source_export_for_rows(observation_rows),
                        relation_type="observed_context_usage",
                        proof_paths=proof_paths,
                        evidence_set_ids=sorted({item for row in observation_rows for item in row.get("evidence_set_ids", []) if isinstance(item, str)}),
                        matching_rule="explicit_context_identifier_in_structured_pack_evidence",
                        confidence="explicit",
                        status="observed",
                        non_promotion_reason="context use is not a reconstructed context configuration",
                    )
                )

        for source in rows["message_sources.jsonl"]:
            conversation_id = string_or_none(source.get("conversation_id"))
            source_id = string_or_none(source.get("source_ref_id")) or stable_source_id(source)
            message_id = string_or_none(source.get("message_id"))
            if not conversation_id or not message_id:
                continue
            for profile_id, profile in sorted(profile_by_id.items()):
                membership = memberships_by_source.get((profile_id, source_id))
                if membership is None:
                    continue
                context_type = string_or_none(profile.get("context_type")) or "unknown"
                context_id = string_or_none(profile.get("explicit_context_id")) or "unknown"
                conversation_node = conversation_node_id(profile_id, conversation_id)
                message_node = message_node_id(profile_id, conversation_id, message_id)
                add_node(
                    ContextResourceNode(
                        context_resource_node_id=message_node,
                        node_kind="context_message",
                        context_profile_id=profile_id,
                        context_type=context_type,
                        explicit_context_id=context_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        resource_identity_kind=None,
                        resource_identifier=None,
                        display_name=None,
                        display_name_aliases=[],
                        source_export=string_or_none(membership.get("source_export")) or PRIMARY,
                        identity_scope="context_profile_plus_conversation_plus_message",
                        status="observed",
                    )
                )
                add_edge(
                    make_edge(
                        edge_type="conversation_contains_message",
                        source_node_id=conversation_node,
                        target_node_id=message_node,
                        context_profile_id=profile_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        source_record_ids=[source_id],
                        source_export=string_or_none(membership.get("source_export")) or PRIMARY,
                        relation_type="message_contains_observed_resource_reference",
                        proof_paths=as_string_list(membership.get("proof_paths")),
                        evidence_set_ids=as_string_list(membership.get("evidence_set_ids")),
                        matching_rule="message_source_explicit_conversation_and_message_fields",
                        confidence="explicit",
                        status="observed",
                        non_promotion_reason=None,
                    )
                )
                identity_kind, identifier, display_name = resource_identity(source)
                resource_node = observed_resource_node_id(identity_kind, identifier)
                caution = "explicit_identifier" if identity_kind in {"file_id", "library_file_id"} else "title_label_not_physical_identity" if identity_kind == "title_label" else "source_reference_scoped"
                add_node(
                    ContextResourceNode(
                        context_resource_node_id=resource_node,
                        node_kind="observed_resource_reference",
                        context_profile_id=None,
                        context_type=None,
                        explicit_context_id=None,
                        conversation_id=None,
                        message_id=None,
                        resource_identity_kind=identity_kind,
                        resource_identifier=identifier,
                        display_name=display_name,
                        display_name_aliases=[display_name] if display_name else [],
                        source_export=string_or_none(membership.get("source_export")) or PRIMARY,
                        identity_scope=caution,
                        status="observed_reference",
                    )
                )
                relation_type = string_or_none(membership.get("relation_type")) or "observed_in_context_conversation"
                add_edge(
                    make_edge(
                        edge_type="message_references_resource",
                        source_node_id=message_node,
                        target_node_id=resource_node,
                        context_profile_id=profile_id,
                        conversation_id=conversation_id,
                        message_id=message_id,
                        source_record_ids=[source_id],
                        source_export=string_or_none(membership.get("source_export")) or PRIMARY,
                        relation_type=relation_type,
                        proof_paths=as_string_list(membership.get("proof_paths")),
                        evidence_set_ids=as_string_list(membership.get("evidence_set_ids")),
                        matching_rule="source_record_membership_with_explicit_conversation_and_message_fields",
                        confidence=string_or_none(membership.get("confidence")) or "explicit",
                        status=string_or_none(membership.get("status")) or "observed",
                        non_promotion_reason=string_or_none(membership.get("non_promotion_reason")),
                    )
                )
                uses.append(
                    {
                        "profile": profile,
                        "profile_id": profile_id,
                        "context_type": context_type,
                        "context_id": context_id,
                        "resource_node_id": resource_node,
                        "identity_kind": identity_kind,
                        "identifier": identifier,
                        "display_name": display_name,
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "source_id": source_id,
                        "source_export": string_or_none(membership.get("source_export")) or PRIMARY,
                        "source_kind": string_or_none(source.get("source_kind")) or "unknown",
                        "source_namespace": string_or_none(source.get("source")) or "unknown",
                        "relation_type": relation_type,
                        "proof_paths": as_string_list(membership.get("proof_paths")),
                    }
                )

        owner_rows = read_owner_confirmations(owner_confirmations_path) if owner_confirmations_path else []
        owner_claims: list[OwnerConfirmationClaim] = []
        for row in owner_rows:
            context_id = required_string(row, "explicit_context_id")
            profile = next(
                (value for value in profile_by_id.values() if value.get("explicit_context_id") == context_id),
                None,
            )
            if profile is None:
                raise ValueError(f"owner confirmation references unknown context: {context_id}")
            profile_id = str(profile["context_profile_id"])
            context_type = str(profile["context_type"])
            declared_name = required_string(row, "resource_name")
            resource_identifier = string_or_none(row.get("resource_identifier"))
            confirmation_id = string_or_none(row.get("owner_confirmation_id")) or "owner-confirmation:" + digest(
                "|".join((context_id, declared_name, resource_identifier or "", required_string(row, "proof_path")))
            )
            declared_node = declared_resource_node_id(context_id, resource_identifier, declared_name)
            claim = OwnerConfirmationClaim(
                owner_confirmation_id=confirmation_id,
                context_profile_id=profile_id,
                context_type=context_type,
                explicit_context_id=context_id,
                declared_resource_node_id=declared_node,
                resource_name=declared_name,
                resource_identifier=resource_identifier,
                confirmation_scope=string_or_none(row.get("confirmation_scope")) or "knowledge_base",
                source_kind="owner_annotation",
                source_export=OWNER_SOURCE,
                proof_path=required_string(row, "proof_path"),
                statement=string_or_none(row.get("statement")),
                status="owner_confirmed",
            )
            owner_claims.append(claim)
            add_node(
                ContextResourceNode(
                    context_resource_node_id=declared_node,
                    node_kind="owner_declared_resource",
                    context_profile_id=profile_id,
                    context_type=context_type,
                    explicit_context_id=context_id,
                    conversation_id=None,
                    message_id=None,
                    resource_identity_kind="owner_declared_identifier" if resource_identifier else "owner_declared_name",
                    resource_identifier=resource_identifier or normalized_name(declared_name),
                    display_name=declared_name,
                    display_name_aliases=[declared_name],
                    source_export=OWNER_SOURCE,
                    identity_scope="owner_declaration_not_physical_identity",
                    status="owner_confirmed",
                )
            )
            add_edge(
                make_edge(
                    edge_type="context_owner_declares_knowledge_resource",
                    source_node_id=context_node_id(profile_id),
                    target_node_id=declared_node,
                    context_profile_id=profile_id,
                    conversation_id=None,
                    message_id=None,
                    source_record_ids=[confirmation_id],
                    source_export=OWNER_SOURCE,
                    relation_type="owner_declared_knowledge_resource",
                    proof_paths=[claim.proof_path],
                    evidence_set_ids=[],
                    matching_rule="explicit_owner_confirmation_input",
                    confidence="explicit",
                    status="owner_confirmed",
                    non_promotion_reason="owner declaration confirms context membership but does not resolve a physical payload",
                )
            )

        owner_claims = sorted(dedupe(owner_claims, "owner_confirmation_id"), key=lambda row: row.owner_confirmation_id)
        for claim in owner_claims:
            for node in sorted(nodes.values(), key=lambda row: row.context_resource_node_id):
                if node.node_kind != "observed_resource_reference" or not node.display_name:
                    continue
                if normalized_name(claim.resource_name) not in {normalized_name(value) for value in node.display_name_aliases}:
                    continue
                add_edge(
                    make_edge(
                        edge_type="owner_declaration_exact_name_corroborated_by_export_reference",
                        source_node_id=claim.declared_resource_node_id,
                        target_node_id=node.context_resource_node_id,
                        context_profile_id=claim.context_profile_id,
                        conversation_id=None,
                        message_id=None,
                        source_record_ids=[claim.owner_confirmation_id],
                        source_export=OWNER_SOURCE,
                        relation_type="exact_name_export_corroboration",
                        proof_paths=[claim.proof_path],
                        evidence_set_ids=[],
                        matching_rule="exact_normalized_filename_equality",
                        confidence="explicit",
                        status="corroborated_name_reference",
                        non_promotion_reason="exact name corroborates an observed reference; it does not prove physical payload identity",
                    )
                )

        metrics = build_metrics(profile_by_id, observations_by_profile_conversation, uses, owner_claims, nodes)
        cooccurrences = build_cooccurrences(uses)
        return ContextResourceUsageResult(
            nodes=sorted(nodes.values(), key=lambda row: row.context_resource_node_id),
            edges=sorted(edges.values(), key=lambda row: row.context_resource_edge_id),
            owner_claims=owner_claims,
            metrics=metrics,
            cooccurrences=cooccurrences,
        )


def build_metrics(profile_by_id, observations_by_profile_conversation, uses, owner_claims, nodes) -> list[ContextResourceUsageMetric]:
    uses_by_profile_resource: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for use in uses:
        uses_by_profile_resource[(use["profile_id"], use["resource_node_id"])].append(use)
    claims_by_profile_name: dict[tuple[str, str], list[OwnerConfirmationClaim]] = defaultdict(list)
    for claim in owner_claims:
        claims_by_profile_name[(claim.context_profile_id, normalized_name(claim.resource_name))].append(claim)
    for claim in owner_claims:
        matching_profile_nodes = [
            resource_node_id
            for (profile_id, resource_node_id), rows in uses_by_profile_resource.items()
            if profile_id == claim.context_profile_id
            and rows
            and normalized_name(claim.resource_name) in {
                normalized_name(value) for value in nodes[resource_node_id].display_name_aliases
            }
        ]
        if not matching_profile_nodes:
            uses_by_profile_resource.setdefault((claim.context_profile_id, claim.declared_resource_node_id), [])

    profiles_by_resource: dict[str, set[str]] = defaultdict(set)
    for (profile_id, resource_node_id), rows in uses_by_profile_resource.items():
        if rows:
            profiles_by_resource[resource_node_id].add(profile_id)
    output: list[ContextResourceUsageMetric] = []
    for (profile_id, resource_node_id), rows in sorted(uses_by_profile_resource.items()):
        profile = profile_by_id[profile_id]
        context_type = str(profile.get("context_type") or "unknown")
        context_id = str(profile.get("explicit_context_id") or "unknown")
        node = nodes[resource_node_id]
        resource_name = node.display_name
        aliases = node.display_name_aliases or ([resource_name] if resource_name else [])
        claims = sorted(
            {
                claim.owner_confirmation_id: claim
                for alias in aliases
                for claim in claims_by_profile_name.get((profile_id, normalized_name(alias)), [])
            }.values(),
            key=lambda row: row.owner_confirmation_id,
        )
        conversation_ids = sorted({row["conversation_id"] for row in rows})
        message_ids = sorted({row["message_id"] for row in rows})
        source_exports = sorted({row["source_export"] for row in rows})
        source_export = source_exports[0] if len(source_exports) == 1 else "multiple_source_exports" if source_exports else OWNER_SOURCE
        observed_conversation_ids = {
            conversation_id
            for (observed_profile_id, conversation_id), observation_rows in observations_by_profile_conversation.items()
            if observed_profile_id == profile_id
            and any((row.get("source_export") or PRIMARY) == source_export for row in observation_rows)
        } if source_export != OWNER_SOURCE else set()
        direct_conversation_ids = {
            conversation_id
            for (observed_profile_id, conversation_id), observation_rows in observations_by_profile_conversation.items()
            if observed_profile_id == profile_id
            and any(
                (row.get("source_export") or PRIMARY) == source_export
                and row.get("observation_kind") in {"conversation_context_projection", "legacy_context_link_projection"}
                for row in observation_rows
            )
        } if source_export != OWNER_SOURCE else set()
        # The direct projection is the clean GPT/project -> conversation basis.
        # Technical-event context observations remain visible in the separate
        # observed denominator rather than silently changing the prevalence base.
        denominator = len(direct_conversation_ids)
        direct_use_conversations = sorted(set(conversation_ids) & direct_conversation_ids)
        observed_denominator = len(observed_conversation_ids)
        relative = round(len(direct_use_conversations) / denominator, 12) if denominator else None
        observed_relative = round(len(conversation_ids) / observed_denominator, 12) if observed_denominator else None
        other_profiles = sorted(profiles_by_resource.get(resource_node_id, set()) - {profile_id})
        other_gizmos = sorted(
            str(profile_by_id[item].get("explicit_context_id"))
            for item in other_profiles
            if profile_by_id[item].get("context_type") == "gizmo"
        )
        other_projects = sorted(
            str(profile_by_id[item].get("explicit_context_id"))
            for item in other_profiles
            if profile_by_id[item].get("context_type") == "project"
        )
        corroborated = bool(claims and rows)
        status = "confirmed_by_owner_and_export_corroborated" if corroborated else "confirmed_by_owner_not_seen_in_export" if claims else "observed_context_resource_usage"
        output.append(
            ContextResourceUsageMetric(
                context_resource_usage_id="context-resource-usage:" + digest("|".join((profile_id, resource_node_id, source_export))),
                context_profile_id=profile_id,
                context_type=context_type,
                explicit_context_id=context_id,
                observed_resource_node_id=resource_node_id if node.node_kind == "observed_resource_reference" else None,
                resource_identity_kind=node.resource_identity_kind or "owner_declared_name",
                resource_identifier=node.resource_identifier,
                resource_name=resource_name,
                resource_name_aliases=aliases,
                source_export=source_export,
                source_kinds=sorted({row["source_kind"] for row in rows}),
                source_namespaces=sorted({row["source_namespace"] for row in rows}),
                context_conversation_denominator=denominator,
                observed_context_conversation_denominator=observed_denominator,
                conversation_ids=conversation_ids,
                direct_context_conversation_ids=direct_use_conversations,
                message_ids=message_ids,
                distinct_conversation_count=len(conversation_ids),
                distinct_direct_context_conversation_count=len(direct_use_conversations),
                distinct_message_count=len(message_ids),
                reference_occurrence_count=len(rows),
                relative_conversation_frequency=relative,
                relative_observed_context_frequency=observed_relative,
                relation_types=sorted({row["relation_type"] for row in rows}),
                owner_confirmation_ids=sorted(row.owner_confirmation_id for row in claims),
                export_corroboration_status="exact_normalized_filename_observed" if corroborated else "not_observed" if claims else "not_owner_confirmed",
                observed_in_other_gizmo_context_ids=other_gizmos,
                observed_in_other_project_context_ids=other_projects,
                resource_identity_caution=node.identity_scope,
                forensic_status=status,
                proof_paths=sorted({path for row in rows for path in row["proof_paths"]} | {claim.proof_path for claim in claims}),
            )
        )
    return sorted(output, key=lambda row: row.context_resource_usage_id)


def build_cooccurrences(uses: list[dict[str, Any]]) -> list[ContextResourceCooccurrence]:
    by_profile_conversation: dict[tuple[str, str], set[str]] = defaultdict(set)
    by_profile_message: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    context_details: dict[str, tuple[str, str]] = {}
    for row in uses:
        profile_id, conversation_id, message_id, resource = row["profile_id"], row["conversation_id"], row["message_id"], row["resource_node_id"]
        by_profile_conversation[(profile_id, conversation_id)].add(resource)
        by_profile_message[(profile_id, conversation_id, message_id)].add(resource)
        context_details[profile_id] = (row["context_type"], row["context_id"])
    pair_conversations: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    pair_messages: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for (profile_id, conversation_id), resource_ids in by_profile_conversation.items():
        for left, right in combinations(sorted(resource_ids), 2):
            pair_conversations[(profile_id, left, right)].add(conversation_id)
    for (profile_id, conversation_id, message_id), resource_ids in by_profile_message.items():
        for left, right in combinations(sorted(resource_ids), 2):
            pair_messages[(profile_id, left, right)].add(f"{conversation_id}::{message_id}")
    output: list[ContextResourceCooccurrence] = []
    for key in sorted(set(pair_conversations) | set(pair_messages)):
        profile_id, left, right = key
        context_type, context_id = context_details[profile_id]
        conversations = sorted(pair_conversations[key])
        messages = sorted(pair_messages[key])
        output.append(
            ContextResourceCooccurrence(
                context_resource_cooccurrence_id="context-resource-cooccurrence:" + digest("|".join(key)),
                context_profile_id=profile_id,
                context_type=context_type,
                explicit_context_id=context_id,
                left_resource_node_id=left,
                right_resource_node_id=right,
                same_conversation_ids=conversations,
                same_message_scopes=messages,
                same_conversation_count=len(conversations),
                same_message_count=len(messages),
                status="observed_usage_only",
                interpretation_limit="cooccurrence records shared observed use only; it does not establish dependency, attachment, or physical identity",
            )
        )
    return output


def read_owner_confirmations(path: Path) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    for index, row in enumerate(rows, start=1):
        required_string(row, "explicit_context_id", index=index)
        required_string(row, "resource_name", index=index)
        required_string(row, "proof_path", index=index)
        if string_or_none(row.get("status")) not in {None, "owner_confirmed"}:
            raise ValueError(f"owner confirmation row {index} has invalid status")
    return rows


def emit_context_resource_usage_evidence(output_dir: Path, result: ContextResourceUsageResult, *, execution: dict[str, Any] | None = None) -> None:
    append_jsonl(output_dir / "context_resource_nodes.jsonl", [row.to_dict() for row in result.nodes])
    append_jsonl(output_dir / "context_resource_edges.jsonl", [row.to_dict() for row in result.edges])
    append_jsonl(output_dir / "context_resource_owner_claims.jsonl", [row.to_dict() for row in result.owner_claims])
    append_jsonl(output_dir / "context_resource_usage_metrics.jsonl", [row.to_dict() for row in result.metrics])
    append_jsonl(output_dir / "context_resource_cooccurrences.jsonl", [row.to_dict() for row in result.cooccurrences])
    summary = result.summary_dict()
    if execution:
        summary["execution"] = execution
    write_json(output_dir / "context_resource_usage_summary.json", summary)


def make_edge(**values: Any) -> ContextResourceEdge:
    seed = "|".join(
        (
            str(values["edge_type"]),
            str(values["source_node_id"]),
            str(values["target_node_id"]),
            str(values.get("context_profile_id") or ""),
            str(values.get("conversation_id") or ""),
            str(values.get("message_id") or ""),
            str(values.get("source_export") or ""),
            str(values.get("relation_type") or ""),
            str(values.get("matching_rule") or ""),
            str(values.get("status") or ""),
        )
    )
    return ContextResourceEdge(context_resource_edge_id="context-resource-edge:" + digest(seed), **values)


def resource_identity(source: dict[str, Any]) -> tuple[str, str, str | None]:
    file_id = string_or_none(source.get("file_id"))
    library_id = string_or_none(source.get("strict_library_file_id"))
    title = string_or_none(source.get("title"))
    source_id = string_or_none(source.get("source_ref_id")) or stable_source_id(source)
    if file_id:
        return "file_id", file_id, title
    if library_id:
        return "library_file_id", library_id, title
    if title:
        return "title_label", normalized_name(title), title
    return "source_reference", source_id, None


def context_node_id(profile_id: str) -> str:
    return "context-resource-node:context:" + digest(profile_id)


def conversation_node_id(profile_id: str, conversation_id: str) -> str:
    return "context-resource-node:conversation:" + digest(f"{profile_id}|{conversation_id}")


def message_node_id(profile_id: str, conversation_id: str, message_id: str) -> str:
    return "context-resource-node:message:" + digest(f"{profile_id}|{conversation_id}|{message_id}")


def observed_resource_node_id(identity_kind: str, identifier: str) -> str:
    return "context-resource-node:observed-resource:" + digest(f"{identity_kind}|{identifier}")


def declared_resource_node_id(context_id: str, resource_identifier: str | None, resource_name: str) -> str:
    return "context-resource-node:owner-declared-resource:" + digest(f"{context_id}|{resource_identifier or normalized_name(resource_name)}")


def normalized_name(value: str | None) -> str:
    return " ".join((value or "").strip().split())


def source_export_for_rows(rows: Iterable[dict[str, Any]]) -> str:
    values = sorted({string_or_none(row.get("source_export")) or PRIMARY for row in rows})
    return values[0] if len(values) == 1 else "multiple_source_exports"


def source_export_for_values(*values: str | None) -> str | None:
    present = sorted({value for value in values if value})
    return present[0] if len(present) == 1 else "multiple_source_exports" if present else None


def as_string_list(value: Any) -> list[str]:
    return sorted({item for item in value if isinstance(item, str) and item}) if isinstance(value, list) else []


def string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def required_string(row: dict[str, Any], field: str, *, index: int | None = None) -> str:
    value = string_or_none(row.get(field))
    if value:
        return value
    location = f" row {index}" if index is not None else ""
    raise ValueError(f"owner confirmation{location} missing {field}")


def stable_source_id(row: dict[str, Any]) -> str:
    return "context-resource-source:" + digest(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def collision_count(values: Iterable[str]) -> int:
    counts = Counter(values)
    return sum(count - 1 for count in counts.values() if count > 1)


def dedupe(rows: Iterable[Any], field: str) -> list[Any]:
    output: dict[str, Any] = {}
    for row in rows:
        value = getattr(row, field)
        existing = output.get(value)
        if existing is not None and existing != row:
            raise ValueError(f"non-deterministic duplicate {field}: {value}")
        output[value] = row
    return [output[key] for key in sorted(output)]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            rows.append(value)
    return rows
