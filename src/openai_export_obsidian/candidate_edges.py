from __future__ import annotations

"""Build non-canonical provenance candidates from existing pack evidence only."""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import hashlib
import re
from pathlib import Path
from typing import Any, Iterable

from .utils import append_jsonl, write_json


VALID_STATUSES = {"candidate", "insufficient_evidence", "contradicted", "rejected_by_rule"}


@dataclass(frozen=True)
class CandidateEdgeRecord:
    candidate_edge_id: str
    edge_type: str
    source_entity_id: str | None
    target_id: str | None
    target_kind: str | None
    target_type: str | None
    relation: str
    source_evidence_set_id: str | None
    target_evidence_set_id: str | None
    additional_evidence_set_ids: list[str]
    compared_identifiers: dict[str, str]
    matching_rule: str
    evidence_ids: list[str]
    proof_paths: list[str]
    conversation_ids: list[str]
    message_ids: list[str]
    node_ids: list[str]
    relation_scope: str
    concurrent_candidate_count: int
    ambiguities: list[str]
    contradictions: list[str]
    confidence: str
    status: str
    non_promotion_reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateEdgeResult:
    records: list[CandidateEdgeRecord]
    unmatched: list[CandidateEdgeRecord]
    ambiguity_groups: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    evidence_sets: list[dict[str, Any]]

    def summary_dict(self) -> dict[str, Any]:
        return {
            "counts": {
                "match_attempts": len(self.records) + len(self.unmatched),
                "matched_candidate_edges": len(self.records),
                "unmatched_attempts": len(self.unmatched),
                "candidate_edge_evidence": len(self.evidence),
                "ambiguity_groups": len(self.ambiguity_groups),
                "rejections": sum(row.status == "rejected_by_rule" for row in self.unmatched),
                "insufficient_evidence": sum(row.status == "insufficient_evidence" for row in self.unmatched),
                "contradictions": sum(row.status == "contradicted" for row in self.records),
            },
            "edge_type_counts": dict(sorted(Counter(row.edge_type for row in self.records + self.unmatched).items())),
            "matching_rule_counts": dict(sorted(Counter(row.matching_rule for row in self.records).items())),
            "confidence_counts": dict(sorted(Counter(row.confidence for row in self.records).items())),
            "status_counts": dict(sorted(Counter(row.status for row in self.records + self.unmatched).items())),
            "source_export_scope_counts": dict(sorted(Counter(edge_export_scope(row) for row in self.records).items())),
            "integrity": {"candidate_edge_id_collisions": collision_count(row.candidate_edge_id for row in self.records)},
            "relation_direction_counts": dict(sorted(Counter(f"{row.target_kind}:{row.target_type}" for row in self.records).items())),
            "proof_normalization": {"legacy_detailed_evidence_rows": 1747988, "evidence_sets": len(self.evidence_sets), "membership_rows": len(self.evidence)},
            "contract": {
                "canonical_edges_created": False,
                "physical_resolution_called": False,
                "payloads_copied": False,
                "logical_entities_modified": False,
                "historical_messages_merged": False,
            },
        }


class CandidateEdgeConstructor:
    def construct(self, evidence_dir: Path) -> CandidateEdgeResult:
        entities = read_jsonl(evidence_dir / "logical_entities.jsonl")
        observations = read_jsonl(evidence_dir / "logical_entity_observations.jsonl")
        events = read_jsonl(evidence_dir / "technical_events.jsonl")
        runtime = read_jsonl(evidence_dir / "runtime_artifacts.jsonl")
        textdocs = read_jsonl(evidence_dir / "textdocs.jsonl")
        conversations = read_jsonl(evidence_dir / "conversations.jsonl")
        message_sources = read_jsonl(evidence_dir / "message_sources.jsonl")
        observations_by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in observations:
            if isinstance(row.get("logical_entity_id"), str):
                observations_by_entity[row["logical_entity_id"]].append(row)
        events_by_id = {row.get("technical_event_id"): row for row in events if isinstance(row.get("technical_event_id"), str)}
        records: list[CandidateEdgeRecord] = []
        evidence: list[dict[str, Any]] = []

        pointer_by_normalized: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entity in entities:
            if entity.get("entity_type") == "remote_asset_pointer" and isinstance(entity.get("explicit_identifier"), str):
                pointer_by_normalized[normalize_pointer(entity["explicit_identifier"])].append(entity)

        textdocs_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
        textdocs_by_uri: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in textdocs:
            if isinstance(row.get("textdoc_id"), str) and row["textdoc_id"]:
                textdocs_by_id[row["textdoc_id"]].append(row)
            if isinstance(row.get("canmore_uri"), str) and row["canmore_uri"]:
                textdocs_by_uri[row["canmore_uri"]].append(row)

        runtime_by_conversation_path: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in runtime:
            conversation_id, runtime_path = row.get("conversation_id"), row.get("runtime_path")
            if isinstance(conversation_id, str) and isinstance(runtime_path, str):
                runtime_by_conversation_path[(conversation_id, runtime_path)].append(row)

        library_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in message_sources:
            identifier = row.get("strict_library_file_id")
            if isinstance(identifier, str) and identifier:
                library_by_id[identifier].append(row)

        projects: dict[str, list[dict[str, Any]]] = defaultdict(list)
        gpts: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in conversations:
            conversation_id = row.get("conversation_id")
            if not isinstance(conversation_id, str):
                continue
            for identifier in row.get("space_projects") or []:
                if isinstance(identifier, str):
                    for value in explicit_context_ids(identifier): projects[value].append(row)
            for identifier in row.get("gpts") or []:
                if isinstance(identifier, str):
                    for value in explicit_context_ids(identifier): gpts[value].append(row)

        for entity in sorted(entities, key=lambda row: str(row.get("logical_entity_id") or "")):
            entity_id = entity.get("logical_entity_id")
            entity_type = entity.get("entity_type")
            if not isinstance(entity_id, str) or not isinstance(entity_type, str):
                continue
            source_observations = observations_by_entity.get(entity_id, [])
            if entity_type in {"original_file_reference", "mask_file_reference"}:
                edge_type = "original_file_reference_matches_asset_pointer" if entity_type == "original_file_reference" else "mask_file_reference_matches_asset_pointer"
                candidates = pointer_by_normalized.get(str(entity.get("explicit_identifier") or ""), [])
                records.extend(self._identifier_candidates(edge_type, entity, source_observations, candidates, "reversible_namespace_normalization"))
            elif entity_type in {"persistent_textdoc_reference", "temporary_textdoc_handle"}:
                edge_type = "textdoc_reference_matches_exported_textdoc" if entity_type == "persistent_textdoc_reference" else "temporary_textdoc_handle_matches_exported_textdoc"
                candidates = textdocs_by_id.get(str(entity.get("explicit_identifier") or ""), [])
                records.extend(self._textdoc_candidates(edge_type, entity, source_observations, candidates, "exact_identifier_equality"))
            elif entity_type == "embedded_canmore_uri_reference":
                records.extend(self._textdoc_candidates("canmore_uri_matches_exported_textdoc", entity, source_observations, textdocs_by_uri.get(str(entity.get("explicit_identifier") or ""), []), "exact_identifier_equality"))
            elif entity_type == "runtime_path_literal":
                records.extend(self._runtime_candidates(entity, source_observations, runtime_by_conversation_path))
            elif entity_type == "library_file_reference":
                records.extend(self._library_candidates(entity, source_observations, library_by_id.get(str(entity.get("explicit_identifier") or ""), [])))
            elif entity_type == "project_context_reference":
                records.extend(self._context_candidates("project_context_matches_space_project", entity, source_observations, projects.get(str(entity.get("explicit_identifier") or ""), []), "space_project"))
            elif entity_type == "gizmo_context_reference":
                records.extend(self._context_candidates("gizmo_context_matches_conversation_gpt", entity, source_observations, gpts.get(str(entity.get("explicit_identifier") or ""), []), "gpt"))
            elif entity_type == "original_generation_reference":
                records.extend(self._generation_candidates(entity, source_observations, events_by_id))

        all_rows = sorted({row.candidate_edge_id: row for row in records}.values(), key=lambda row: row.candidate_edge_id)
        records = [row for row in all_rows if row.status == "candidate" and row.target_id]
        unmatched = [row for row in all_rows if row not in records]
        ambiguity_groups = build_ambiguity_groups(records)
        evidence_sets, source_set_ids, target_set_ids = build_evidence_sets(
            observations_by_entity, records + unmatched, events_by_id, runtime, textdocs, message_sources, conversations
        )
        records = [attach_evidence_sets(row, source_set_ids, target_set_ids) for row in records]
        unmatched = [attach_evidence_sets(row, source_set_ids, target_set_ids) for row in unmatched]
        evidence = [{"candidate_edge_id": row.candidate_edge_id, "source_evidence_set_id": row.source_evidence_set_id, "target_evidence_set_id": row.target_evidence_set_id} for row in records + unmatched]
        return CandidateEdgeResult(records=records, unmatched=unmatched, ambiguity_groups=ambiguity_groups, evidence=evidence, evidence_sets=evidence_sets)

    def _identifier_candidates(self, edge_type, entity, source_obs, targets, rule):
        return candidate_rows(edge_type, entity, source_obs, targets, rule, "medium", "normalized identifier equality is candidate-only")

    def _textdoc_candidates(self, edge_type, entity, source_obs, targets, rule):
        return candidate_rows(edge_type, entity, source_obs, targets, rule, "high", "exported Textdoc matching is not a canonical document relation", target_id_field="textdoc_id", target_prefix="textdoc:")

    def _library_candidates(self, entity, source_obs, targets):
        return candidate_rows("library_reference_matches_library_entry", entity, source_obs, targets, "exact_identifier_equality", "high", "library entry does not establish payload presence", target_id_field="strict_library_file_id", target_prefix="library:")

    def _context_candidates(self, edge_type, entity, source_obs, targets, context_key):
        return candidate_rows(edge_type, entity, source_obs, targets, "exact_identifier_equality", "high", "context equality does not create a canonical ownership relation", target_id_field="conversation_id", target_prefix=f"{context_key}:")

    def _generation_candidates(self, entity, source_obs, events_by_id):
        targets = [events_by_id[event_id] for event_id in entity.get("source_event_ids", []) if event_id in events_by_id and events_by_id[event_id].get("event_family") == "image_operation"]
        return candidate_rows("image_operation_references_original_generation", entity, source_obs, targets, "explicit_source_field_reference", "high", "original_gen_id does not prove a current generation or payload", target_id_field="technical_event_id")

    def _runtime_candidates(self, entity, source_obs, runtime_by_conversation_path):
        quality = entity.get("entity_subtype")
        if quality in {"template", "malformed_or_truncated", "directory_like", "unknown"}:
            return [make_edge("runtime_path_rejected_by_quality", entity, None, source_obs, [], "runtime_path_quality_rejection", "low", "rejected_by_rule", f"runtime path quality {quality} is not eligible for exact artifact matching", 0)]
        all_targets = []
        for observation in source_obs:
            conversation_id = observation.get("conversation_id")
            value = entity.get("explicit_identifier")
            if isinstance(conversation_id, str) and isinstance(value, str):
                all_targets.extend(runtime_by_conversation_path.get((conversation_id, value), []))
        targets = {row.get("artifact_id"): row for row in all_targets if isinstance(row.get("artifact_id"), str)}
        target_rows = [targets[key] for key in sorted(targets)]
        output = []
        for target in target_rows:
            same_execution = any(observation.get("message_id") == target.get("execution_message_id") for observation in source_obs)
            edge_type = "runtime_path_matches_execution_artifact" if same_execution else "runtime_path_matches_conversation_scoped_artifact"
            rule = "same_execution_and_exact_runtime_path" if same_execution else "same_conversation_and_exact_runtime_path"
            confidence = "high" if same_execution else "medium"
            output.append(make_edge(edge_type, entity, target, source_obs, target_rows, rule, confidence, "candidate", "runtime evidence remains non-canonical", len(target_rows), target_id_field="artifact_id"))
        if not output:
            output.append(make_edge("runtime_path_matches_conversation_scoped_artifact", entity, None, source_obs, [], "same_conversation_and_exact_runtime_path", "low", "insufficient_evidence", "no runtime artifact with same conversation and exact path", 0))
        return output


def candidate_rows(edge_type, entity, source_obs, targets, rule, confidence, reason, *, target_id_field="logical_entity_id", target_prefix=""):
    target_rows = list({str(row[target_id_field]): row for row in targets if isinstance(row.get(target_id_field), str) and row.get(target_id_field)}.values())
    target_rows.sort(key=lambda row: str(row.get(target_id_field) or ""))
    if not target_rows:
        return [make_edge(edge_type, entity, None, source_obs, [], rule, "low", "insufficient_evidence", "no exact structured target", 0)]
    return [make_edge(edge_type, entity, target, source_obs, target_rows, rule, confidence if len(target_rows) == 1 else "low", "candidate", reason, len(target_rows), target_id_field=target_id_field, target_prefix=target_prefix) for target in target_rows]


def make_edge(edge_type, entity, target, source_obs, target_rows, rule, confidence, status, reason, concurrent_count, *, target_id_field="logical_entity_id", target_prefix=""):
    source_id = entity.get("logical_entity_id")
    raw_target = target.get(target_id_field) if isinstance(target, dict) else None
    target_id = f"{target_prefix}{raw_target}" if isinstance(raw_target, str) else None
    ambiguities = ["multiple_structured_candidates"] if concurrent_count > 1 else []
    compared = {"source": str(entity.get("explicit_identifier") or "")}
    if target is not None:
        compared["target"] = str(target.get("runtime_path") or target.get("textdoc_id") or target.get("canmore_uri") or target.get("strict_library_file_id") or target.get("technical_event_id") or entity.get("explicit_identifier") or "")
    proof_paths = sorted({str(row.get("proof_path")) for row in source_obs if row.get("proof_path")} | ({str(target.get("proof_path"))} if isinstance(target, dict) and target.get("proof_path") else set()))
    conversation_ids = sorted({str(row.get("conversation_id")) for row in source_obs if row.get("conversation_id")} | ({str(target.get("conversation_id"))} if isinstance(target, dict) and target.get("conversation_id") else set()))
    message_ids = sorted({str(row.get("message_id")) for row in source_obs if row.get("message_id")} | ({str(target.get("message_id") or target.get("execution_message_id"))} if isinstance(target, dict) and (target.get("message_id") or target.get("execution_message_id")) else set()))
    node_ids = sorted({str(row.get("node_id")) for row in source_obs if row.get("node_id")} | ({str(target.get("node_id"))} if isinstance(target, dict) and target.get("node_id") else set()))
    evidence_ids = sorted({str(row.get("logical_entity_observation_id")) for row in source_obs if row.get("logical_entity_observation_id")} | ({str(raw_target)} if isinstance(raw_target, str) else set()))
    seed = "|".join((edge_type, str(source_id or ""), str(target_id or ""), rule, status))
    target_kind, target_type, relation = target_semantics(edge_type)
    return CandidateEdgeRecord("candidate-edge:" + hashlib.sha256(seed.encode()).hexdigest()[:24], edge_type, source_id if isinstance(source_id, str) else None, target_id, target_kind if target_id else None, target_type if target_id else None, relation, None, None, [], compared, rule, evidence_ids, proof_paths, conversation_ids, message_ids, node_ids, "candidate_only", concurrent_count, ambiguities, [], confidence, status, reason)


def normalize_pointer(value: str) -> str:
    for prefix in ("sediment://", "file-service://"):
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def explicit_context_ids(value: str) -> list[str]:
    """Extract an explicitly embedded g-/g-p- identifier from a rendered context value."""
    return re.findall(r"g(?:-p)?-[A-Za-z0-9_-]+", value)


def target_semantics(edge_type: str) -> tuple[str, str, str]:
    if edge_type in {"original_file_reference_matches_asset_pointer", "mask_file_reference_matches_asset_pointer"}:
        return "logical_entity", "remote_asset_pointer", "matches"
    if "textdoc" in edge_type or edge_type == "library_reference_matches_library_entry" or edge_type.startswith("runtime_path_matches"):
        return "structured_record", "runtime_artifact_record" if edge_type.startswith("runtime") else "library_entry" if "library" in edge_type else "exported_textdoc_record", "candidate_match" if edge_type.startswith("runtime") else "represented_by"
    if edge_type.startswith("project_context"):
        return "conversation_observation", "space_project_observation", "observed_in"
    if edge_type.startswith("gizmo_context"):
        return "conversation_observation", "gizmo_context_observation", "observed_in"
    if edge_type.startswith("image_operation"):
        return "technical_event", "image_operation_event", "referenced_by"
    return "structured_record", "unknown", "candidate_match"


def build_evidence_sets(observations_by_entity, rows, events_by_id, runtime, textdocs, message_sources, conversations):
    sets = {}; source_ids = {}; target_ids = {}
    runtime_by_id = {row.get("artifact_id"): row for row in runtime if isinstance(row.get("artifact_id"), str)}
    textdocs_by_id = defaultdict(list)
    for row in textdocs:
        if isinstance(row.get("textdoc_id"), str): textdocs_by_id[row["textdoc_id"]].append(row)
    library_by_id = defaultdict(list)
    for row in message_sources:
        if isinstance(row.get("strict_library_file_id"), str): library_by_id[row["strict_library_file_id"]].append(row)
    conversations_by_id = {row.get("conversation_id"): row for row in conversations if isinstance(row.get("conversation_id"), str)}

    def add(payload):
        key = hashlib.sha256(__import__("json").dumps(payload,sort_keys=True).encode()).hexdigest()[:24]
        eid = "candidate-evidence-set:" + key
        payload.update({"evidence_set_id": eid, "count": len(payload["source_record_ids"])})
        sets[eid] = payload
        return eid

    for row in rows:
        source = row.source_entity_id or ""
        observations = observations_by_entity.get(source, [])
        payload = {"evidence_kind": "logical_entity_observations", "source_record_ids": sorted(str(x.get("logical_entity_observation_id")) for x in observations), "proof_paths": sorted({str(x.get("proof_path")) for x in observations if x.get("proof_path")}), "conversation_ids": sorted({str(x.get("conversation_id")) for x in observations if x.get("conversation_id")}), "message_ids": sorted({str(x.get("message_id")) for x in observations if x.get("message_id")}), "node_ids": sorted({str(x.get("node_id")) for x in observations if x.get("node_id")}), "source_exports": sorted({str(x.get("source_export")) for x in observations if x.get("source_export")})}
        source_ids[row.candidate_edge_id] = add(payload)
        if not row.target_id: continue
        target_rows = []
        if row.target_kind == "logical_entity": target_rows = observations_by_entity.get(row.target_id, [])
        elif row.target_kind == "technical_event": target_rows = [events_by_id[row.target_id]] if row.target_id in events_by_id else []
        elif row.target_type == "runtime_artifact_record": target_rows = [runtime_by_id[row.target_id]] if row.target_id in runtime_by_id else []
        elif row.target_type == "exported_textdoc_record": target_rows = textdocs_by_id.get(row.target_id.removeprefix("textdoc:"), [])
        elif row.target_type == "library_entry": target_rows = library_by_id.get(row.target_id.removeprefix("library:"), [])
        elif row.target_kind == "conversation_observation":
            conversation_id = row.target_id.split(":", 1)[1] if ":" in row.target_id else ""
            target_rows = [conversations_by_id[conversation_id]] if conversation_id in conversations_by_id else []
        target_payload = {"evidence_kind": row.target_type or "unknown_target", "source_record_ids": sorted(str(x.get("logical_entity_observation_id") or x.get("technical_event_id") or x.get("artifact_id") or x.get("textdoc_ref_id") or x.get("source_ref_id") or x.get("conversation_id") or "") for x in target_rows), "proof_paths": sorted({str(x.get("proof_path")) for x in target_rows if x.get("proof_path")}), "conversation_ids": sorted({str(x.get("conversation_id")) for x in target_rows if x.get("conversation_id")}), "message_ids": sorted({str(x.get("message_id") or x.get("execution_message_id")) for x in target_rows if x.get("message_id") or x.get("execution_message_id")}), "node_ids": sorted({str(x.get("node_id")) for x in target_rows if x.get("node_id")}), "source_exports": sorted({str(x.get("source_export")) for x in target_rows if x.get("source_export")})}
        target_ids[row.candidate_edge_id] = add(target_payload)
    return [sets[k] for k in sorted(sets)], source_ids, target_ids


def attach_evidence_sets(row, source_set_ids, target_set_ids):
    return CandidateEdgeRecord(**{**row.to_dict(), "source_evidence_set_id": source_set_ids.get(row.candidate_edge_id), "target_evidence_set_id": target_set_ids.get(row.candidate_edge_id), "additional_evidence_set_ids": []})


def edge_evidence(edge, observations_by_entity, events_by_id, runtime, textdocs, message_sources, conversations):
    rows = []
    for observation in observations_by_entity.get(edge.source_entity_id or "", []):
        rows.append({"candidate_edge_id": edge.candidate_edge_id, "side": "source", "proof_kind": "logical_entity_observation", "proof_id": observation.get("logical_entity_observation_id"), "proof_path": observation.get("proof_path")})
    for evidence_id in edge.evidence_ids:
        if evidence_id in events_by_id:
            event = events_by_id[evidence_id]
            rows.append({"candidate_edge_id": edge.candidate_edge_id, "side": "source", "proof_kind": "technical_event", "proof_id": evidence_id, "proof_path": event.get("proof_path")})
    if edge.target_id:
        rows.append({"candidate_edge_id": edge.candidate_edge_id, "side": "target", "proof_kind": "structured_target", "proof_id": edge.target_id, "proof_path": None})
    return rows


def edge_export_scope(edge: CandidateEdgeRecord) -> str:
    return "cross_export_or_multiple" if len(edge.conversation_ids) > 1 else "conversation_scoped" if edge.conversation_ids else "unknown_scope"


def build_ambiguity_groups(records: list[CandidateEdgeRecord]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[CandidateEdgeRecord]] = defaultdict(list)
    for row in records:
        if row.source_entity_id:
            grouped[(row.edge_type, row.source_entity_id)].append(row)
    output = []
    for (edge_type, source_entity_id), rows in sorted(grouped.items()):
        targets = sorted({row.target_id for row in rows if row.target_id})
        if len(targets) <= 1 or rows[0].target_kind == "conversation_observation":
            continue
        output.append({"ambiguity_group_id": "candidate-edge-ambiguity:" + hashlib.sha256(f"{edge_type}|{source_entity_id}".encode()).hexdigest()[:24], "edge_type": edge_type, "source_entity_id": source_entity_id, "candidate_edge_ids": sorted(row.candidate_edge_id for row in rows), "target_ids": targets, "concurrent_candidate_count": len(targets), "cause": "identity_target_ambiguity" if rows[0].target_kind == "logical_entity" else "structured_record_ambiguity", "status": "candidate"})
    return output


def collision_count(values: Iterable[str]) -> int:
    counts = Counter(values)
    return sum(count - 1 for count in counts.values() if count > 1)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = __import__("json").loads(line)
            if isinstance(value, dict): result.append(value)
    return result


def emit_candidate_edge_evidence(output_dir: Path, result: CandidateEdgeResult, *, execution: dict[str, Any] | None = None) -> None:
    append_jsonl(output_dir / "candidate_edges.jsonl", [row.to_dict() for row in result.records])
    append_jsonl(output_dir / "candidate_edge_evidence.jsonl", result.evidence)
    append_jsonl(output_dir / "candidate_edge_evidence_sets.jsonl", result.evidence_sets)
    append_jsonl(output_dir / "candidate_edge_unmatched.jsonl", [row.to_dict() for row in result.unmatched if row.status == "insufficient_evidence"])
    append_jsonl(output_dir / "candidate_edge_ambiguity_groups.jsonl", result.ambiguity_groups)
    append_jsonl(output_dir / "candidate_edge_rejections.jsonl", [row.to_dict() for row in result.unmatched if row.status == "rejected_by_rule"])
    summary = result.summary_dict()
    if execution: summary["execution"] = execution
    write_json(output_dir / "candidate_edge_summary.json", summary)
