#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from .models import ASSET_STATUSES
from .conversation_note_locator import (
    CONVERSATION_NOTE_LOCATOR_RELATIVE_PATH,
    LOCATOR_RECORD_FIELDS,
    locator_path_error,
)


EVIDENCE_DIR = "90_Evidence"
CONVERSATIONS_DIR = "10_Conversations"
JSONL_FILES = (
    "conversations.jsonl",
    "messages.jsonl",
    "message_sources.jsonl",
    "tool_events.jsonl",
    "textdocs.jsonl",
    "asset_links.jsonl",
    "context_links.jsonl",
    "file_manifest.jsonl",
    "unlinked_assets.jsonl",
)
INVENTORY_FILES = (
    "inventory.jsonl",
    "inventory_errors.jsonl",
    "inventory_summary.json",
)
REFERENCE_FILES = (
    "references.jsonl",
    "reference_summary.json",
)
PHYSICAL_RESOLUTION_FILES = (
    "physical_resolutions.jsonl",
    "physical_resolution_summary.json",
)
PAYLOAD_DISPOSITION_FILES = (
    "payload_dispositions.jsonl",
    "payload_disposition_summary.json",
)
ASSET_RESOLUTION_MIGRATION_FILES = (
    "asset_resolution_comparisons.jsonl",
    "asset_resolution_migration_summary.json",
)
UNVERIFIED_PAYLOAD_CANDIDATE_FILES = (
    "unverified_payload_candidates.jsonl",
    "unverified_payload_candidate_summary.json",
)
RUNTIME_ARTIFACT_FILES = (
    "runtime_artifacts.jsonl",
    "runtime_artifacts_summary.json",
)
HISTORICAL_EXPORT_FILES = (
    "historical_export_snapshots.jsonl",
    "historical_conversation_comparisons.jsonl",
    "historical_messages.jsonl",
    "historical_signals.jsonl",
    "historical_export_summary.json",
)
TECHNICAL_EVENT_FILES = (
    "technical_events.jsonl",
    "technical_event_comparisons.jsonl",
    "technical_event_unknowns.jsonl",
    "technical_event_summary.json",
)
LOGICAL_ENTITY_FILES = (
    "logical_entities.jsonl",
    "logical_entity_observations.jsonl",
    "logical_entity_unmaterialized_events.jsonl",
    "logical_entity_taxonomy_migration.jsonl",
    "logical_entity_summary.json",
)
CANDIDATE_EDGE_FILES = (
    "candidate_edges.jsonl", "candidate_edge_unmatched.jsonl", "candidate_edge_evidence.jsonl", "candidate_edge_ambiguity_groups.jsonl",
    "candidate_edge_evidence_sets.jsonl", "candidate_edge_rejections.jsonl", "candidate_edge_summary.json",
)
CONTEXT_PROFILE_FILES = (
    "gizmo_context_profiles.jsonl",
    "project_context_profiles.jsonl",
    "context_profile_observations.jsonl",
    "context_instruction_candidates.jsonl",
    "context_file_memberships.jsonl",
    "context_profile_contradictions.jsonl",
    "context_profile_summary.json",
)
CONTEXT_RESOURCE_USAGE_FILES = (
    "context_resource_nodes.jsonl",
    "context_resource_edges.jsonl",
    "context_resource_owner_claims.jsonl",
    "context_resource_usage_metrics.jsonl",
    "context_resource_cooccurrences.jsonl",
    "context_resource_usage_summary.json",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate an emitted OpenAI Obsidian pack evidence layer.")
    parser.add_argument("--pack", required=True, type=Path, help="Path to an emitted Obsidian export pack")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = validate_pack(args.pack)
    for warning in report["warnings"]:
        print(f"WARN: {warning}")
    for error in report["errors"]:
        print(f"ERROR: {error}")
    if report["errors"]:
        print(f"Pack validation FAILED: {len(report['errors'])} error(s), {len(report['warnings'])} warning(s)")
        return 1
    print(f"Pack validation OK: {report['counts']['conversations']} conversation(s), {report['counts']['messages']} message row(s)")
    return 0


def validate_pack(pack: Path) -> dict[str, Any]:
    evidence = pack / EVIDENCE_DIR
    errors: list[str] = []
    warnings: list[str] = []
    if not pack.exists():
        errors.append(f"pack path does not exist: {pack}")
        return result(errors, warnings, {})

    # The V1.4 layout remains closed.  The local import avoids a module-load
    # cycle because the contract exporter itself uses this validator.
    from .pack_contract import validate_pack_layout

    errors.extend(validate_pack_layout(pack))
    if not evidence.exists():
        errors.append(f"missing evidence directory: {evidence}")
        return result(errors, warnings, {})

    audit_path = evidence / "parse_audit.json"
    audit: dict[str, Any] = {}
    if not audit_path.exists():
        errors.append("missing audit file: 90_Evidence/parse_audit.json")
    else:
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"parse_audit.json invalid JSON: {exc}")
        if isinstance(audit, dict):
            if "orphan_assets" in audit:
                errors.append("parse_audit.json should not embed orphan_assets rows; use unlinked_assets.jsonl")
            if "unresolved_links" in audit:
                errors.append("parse_audit.json should not embed unresolved_links rows; use asset_links.jsonl")

    rows_by_file: dict[str, list[dict[str, Any]]] = {}
    for name in JSONL_FILES:
        path = evidence / name
        if not path.exists():
            errors.append(f"missing JSONL file: {EVIDENCE_DIR}/{name}")
            rows_by_file[name] = []
            continue
        rows, file_errors = read_jsonl(path)
        rows_by_file[name] = rows
        errors.extend(file_errors)

    inventory_rows = validate_optional_inventory_evidence(evidence, errors)
    payload_disposition_rows = validate_optional_payload_disposition_evidence(evidence, errors, inventory_rows)
    reference_rows = validate_optional_reference_evidence(evidence, errors)
    resolution_rows = validate_optional_physical_resolution_evidence(evidence, errors, reference_rows)
    migration_rows = validate_optional_asset_resolution_migration_evidence(evidence, errors)
    unverified_payload_candidate_rows = validate_optional_unverified_payload_candidate_evidence(
        evidence,
        errors,
        rows_by_file["asset_links.jsonl"],
        rows_by_file["file_manifest.jsonl"],
        migration_rows,
    )
    runtime_artifact_rows = validate_optional_runtime_artifact_evidence(evidence, errors)
    historical_export_rows = validate_optional_historical_export_evidence(evidence, errors)
    technical_event_rows, technical_comparison_rows = validate_optional_technical_event_evidence(evidence, errors)
    logical_entity_rows, logical_entity_observation_rows = validate_optional_logical_entity_evidence(
        evidence,
        errors,
        technical_event_rows,
    )
    candidate_edge_rows = validate_optional_candidate_edge_evidence(evidence, errors, logical_entity_rows)
    context_profiles = validate_optional_context_profile_evidence(evidence, errors, conversations=rows_by_file["conversations.jsonl"])
    context_resource_usage = validate_optional_context_resource_usage_evidence(
        evidence,
        errors,
        conversations=rows_by_file["conversations.jsonl"],
    )

    conversations = rows_by_file["conversations.jsonl"]
    messages = rows_by_file["messages.jsonl"]
    sources = rows_by_file["message_sources.jsonl"]
    tools = rows_by_file["tool_events.jsonl"]
    textdocs = rows_by_file["textdocs.jsonl"]
    assets = rows_by_file["asset_links.jsonl"]
    contexts = rows_by_file["context_links.jsonl"]
    manifest = rows_by_file["file_manifest.jsonl"]
    unlinked = rows_by_file["unlinked_assets.jsonl"]
    validate_optional_payload_materialization_evidence(
        evidence,
        errors,
        payload_disposition_rows,
        manifest,
        unlinked,
    )
    citation_links, citation_link_errors = read_jsonl(evidence / "citation_links.jsonl") if (evidence / "citation_links.jsonl").exists() else ([], [])
    errors.extend(citation_link_errors)

    if migration_rows:
        assets_by_ref_id = {
            row.get("asset_ref_id"): row
            for row in assets
            if isinstance(row.get("asset_ref_id"), str) and row.get("asset_ref_id")
        }
        comparisons_by_asset_id = {
            row.get("asset_ref_id"): row
            for row in migration_rows
            if isinstance(row.get("asset_ref_id"), str) and row.get("asset_ref_id")
        }
        if set(comparisons_by_asset_id) != set(assets_by_ref_id):
            errors.append("asset resolution comparisons do not exactly match asset_links.jsonl")
        for asset_ref_id, comparison in comparisons_by_asset_id.items():
            asset = assets_by_ref_id.get(asset_ref_id)
            if asset is None:
                continue
            if comparison.get("effective_asset_status") != asset.get("asset_status"):
                errors.append(f"asset resolution comparison status mismatch for {asset_ref_id}")
            if comparison.get("effective_physical_archive_path") != asset.get("physical_archive_path"):
                errors.append(f"asset resolution comparison path mismatch for {asset_ref_id}")

    conversation_ids = {row.get("conversation_id") for row in conversations}
    message_keys = [
        (row.get("conversation_id"), row.get("message_id"))
        for row in messages
        if isinstance(row.get("conversation_id"), str) and isinstance(row.get("message_id"), str)
    ]
    node_keys = [
        (row.get("conversation_id"), row.get("node_id"))
        for row in messages
        if isinstance(row.get("conversation_id"), str) and isinstance(row.get("node_id"), str)
    ]
    known_message_keys = set(message_keys)
    messages_by_conversation = Counter(row.get("conversation_id") for row in messages)
    assets_by_conversation = Counter(row.get("conversation_id") for row in assets)

    if len(message_keys) != len(known_message_keys):
        errors.append("message IDs are not unique within conversation scope")
    if len(node_keys) != len(set(node_keys)):
        errors.append("node IDs are not unique within conversation scope")

    for row in conversations:
        conversation_id = row.get("conversation_id")
        if not conversation_id:
            errors.append("conversation row missing conversation_id")
            continue
        if "vault_projects" in row:
            errors.append(
                f"conversation {conversation_id} uses obsolete vault_projects; use projects for vault links and "
                "space_projects for ChatGPT/Perplexity project links"
            )
        projects = row.get("projects")
        if not isinstance(projects, list):
            errors.append(f"conversation {conversation_id} projects must be a list")
        if not isinstance(row.get("space_projects"), list):
            errors.append(f"conversation {conversation_id} space_projects must be a list")
        gpts = row.get("gpts")
        if not isinstance(gpts, list):
            errors.append(f"conversation {conversation_id} gpts must be a list")
        if isinstance(gpts, list):
            for value in gpts:
                if looks_like_project_gpt_link(value):
                    errors.append(
                        f"conversation {conversation_id} project-like g-p context must use space_projects, not gpts"
                    )
        if isinstance(projects, list):
            for value in projects:
                if looks_like_space_project_link(value):
                    errors.append(f"conversation {conversation_id} space project links must use space_projects, not projects")
        for key in ("created_at", "updated_at"):
            value = row.get(key)
            if value is not None and not isinstance(value, str):
                errors.append(f"conversation {conversation_id} {key} must be an ISO datetime string in JSONL")
        expected_messages = row.get("message_count")
        actual_messages = messages_by_conversation.get(conversation_id, 0)
        if expected_messages != actual_messages:
            errors.append(
                f"message_count mismatch for {conversation_id}: conversations.jsonl={expected_messages} messages.jsonl={actual_messages}"
            )
        expected_assets = first_present(row, "file_reference_count", "asset_reference_count")
        actual_assets = assets_by_conversation.get(conversation_id, 0)
        if expected_assets != actual_assets:
            errors.append(
                f"file_reference_count mismatch for {conversation_id}: conversations.jsonl={expected_assets} asset_links.jsonl={actual_assets}"
            )

    for name, rows in (
        ("messages.jsonl", messages),
        ("asset_links.jsonl", assets),
        ("message_sources.jsonl", sources),
        ("tool_events.jsonl", tools),
        ("context_links.jsonl", contexts),
        ("textdocs.jsonl", textdocs),
    ):
        for index, row in enumerate(rows, start=1):
            conversation_id = row.get("conversation_id")
            if conversation_id not in conversation_ids:
                errors.append(f"{name}:{index} references unknown conversation_id: {conversation_id}")

    for index, row in enumerate(runtime_artifact_rows, start=1):
        if row.get("conversation_id") not in conversation_ids:
            errors.append(f"runtime_artifacts.jsonl:{index} references unknown conversation_id: {row.get('conversation_id')}")
    for name, rows in historical_export_rows.items():
        for index, row in enumerate(rows, start=1):
            if name == "historical_export_snapshots.jsonl":
                continue
            if row.get("conversation_id") not in conversation_ids:
                errors.append(f"{name}:{index} references unknown conversation_id: {row.get('conversation_id')}")
    for name, rows in (("technical_events.jsonl", technical_event_rows), ("technical_event_comparisons.jsonl", technical_comparison_rows)):
        for index, row in enumerate(rows, start=1):
            conversation_id = row.get("conversation_id")
            if conversation_id is not None and conversation_id not in conversation_ids:
                errors.append(f"{name}:{index} references unknown conversation_id: {conversation_id}")
    for name, rows in (("logical_entities.jsonl", logical_entity_rows), ("logical_entity_observations.jsonl", logical_entity_observation_rows)):
        for index, row in enumerate(rows, start=1):
            conversation_id = row.get("conversation_id")
            if conversation_id is not None and conversation_id not in conversation_ids:
                errors.append(f"{name}:{index} references unknown conversation_id: {conversation_id}")

    for name, rows in (
        ("asset_links.jsonl", assets),
        ("message_sources.jsonl", sources),
        ("tool_events.jsonl", tools),
        ("textdocs.jsonl", textdocs),
        ("context_links.jsonl", contexts),
    ):
        for index, row in enumerate(rows, start=1):
            message_id = row.get("message_id")
            if message_id is None:
                continue
            conversation_id = row.get("conversation_id")
            if (
                not isinstance(conversation_id, str)
                or not isinstance(message_id, str)
                or (conversation_id, message_id) not in known_message_keys
            ):
                errors.append(f"{name}:{index} references unknown conversation-scoped message identity")

    for index, row in enumerate(citation_links, start=1):
        if row.get("conversation_id") not in conversation_ids:
            errors.append(f"citation_links.jsonl:{index} references unknown conversation_id: {row.get('conversation_id')}")
        message_id = row.get("message_id")
        conversation_id = row.get("conversation_id")
        if message_id is not None and (
            not isinstance(conversation_id, str)
            or not isinstance(message_id, str)
            or (conversation_id, message_id) not in known_message_keys
        ):
            errors.append(f"citation_links.jsonl:{index} references unknown conversation-scoped message identity")
        if not row.get("marker_original") or not row.get("footnote_id"):
            errors.append(f"citation_links.jsonl:{index} missing marker or footnote identifier")
        if row.get("resolution_status") not in {"resolved", "unresolved_marker"}:
            errors.append(f"citation_links.jsonl:{index} invalid resolution_status: {row.get('resolution_status')}")
        if row.get("resolution_status") == "resolved" and not row.get("proof_path"):
            errors.append(f"citation_links.jsonl:{index} resolved citation missing proof_path")

    for index, row in enumerate(assets, start=1):
        if not row.get("asset_ref_id"):
            errors.append(f"asset_links.jsonl:{index} missing asset_ref_id")
        if not row.get("proof_path"):
            errors.append(f"asset_links.jsonl:{index} missing proof_path")
        if row.get("asset_status") not in ASSET_STATUSES:
            errors.append(f"asset_links.jsonl:{index} invalid asset_status: {row.get('asset_status')}")

    for name, rows in (("message_sources.jsonl", sources), ("tool_events.jsonl", tools)):
        for index, row in enumerate(rows, start=1):
            if not row.get("proof_path"):
                errors.append(f"{name}:{index} missing proof_path")

    for index, row in enumerate(textdocs, start=1):
        if not row.get("textdoc_ref_id"):
            errors.append(f"textdocs.jsonl:{index} missing textdoc_ref_id")
        if row.get("status") not in {"exported_payload", "canvas_reference_only", "canmore_uri_reference"}:
            errors.append(f"textdocs.jsonl:{index} invalid status: {row.get('status')}")
        if not row.get("proof_path"):
            errors.append(f"textdocs.jsonl:{index} missing proof_path")
        copied_path = row.get("copied_pack_path")
        if copied_path:
            path = pack / copied_path
            if not path.exists():
                errors.append(f"textdocs.jsonl copied_pack_path missing on disk: {copied_path}")
                continue
            expected_hash = row.get("content_sha256")
            if expected_hash:
                actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                if actual_hash != expected_hash:
                    errors.append(f"textdocs.jsonl content_sha256 mismatch for {copied_path}")

    found_archive_paths = {
        row.get("physical_archive_path")
        for row in assets
        if row.get("asset_status") == "found" and row.get("physical_archive_path")
    }
    unlinked_archive_paths = {row.get("archive_path") for row in unlinked if row.get("archive_path")}
    overlap_paths = sorted(found_archive_paths & unlinked_archive_paths)
    if overlap_paths:
        errors.append(f"found asset archive paths also listed as unlinked: {overlap_paths[:5]}")

    copied_paths = [row.get("copied_path") for row in manifest if row.get("copied_path")]
    for copied_path in copied_paths:
        if not (pack / copied_path).exists():
            errors.append(f"file_manifest copied_path missing on disk: {copied_path}")
    if copied_paths:
        missing_asset_copies = [
            row.get("copied_pack_path")
            for row in assets
            if row.get("copied_pack_path") and not (pack / row["copied_pack_path"]).exists()
        ]
        if missing_asset_copies:
            errors.append(f"asset copied_pack_path missing on disk: {missing_asset_copies[:5]}")
    else:
        warnings.append("file_manifest.jsonl is empty; run parser with --copy-assets to validate copied file paths")

    errors.extend(validate_conversation_frontmatter(pack))
    errors.extend(validate_conversation_note_locator(pack, conversations))

    counts = {
        "conversations": len(conversations),
        "messages": len(messages),
        "assets": len(assets),
        "sources": len(sources),
        "tool_events": len(tools),
        "textdocs": len(textdocs),
        "citation_links": len(citation_links),
        "contexts": len(contexts),
        "manifest_files": len(manifest),
        "unlinked_assets": len(unlinked),
        "inventory_entries": len(inventory_rows),
        "payload_dispositions": len(payload_disposition_rows),
        "references": len(reference_rows),
        "physical_resolutions": len(resolution_rows),
        "asset_resolution_comparisons": len(migration_rows),
        "unverified_payload_candidates": len(unverified_payload_candidate_rows),
        "runtime_artifacts": len(runtime_artifact_rows),
        "historical_export_snapshots": len(historical_export_rows["historical_export_snapshots.jsonl"]),
        "historical_messages": len(historical_export_rows["historical_messages.jsonl"]),
        "historical_signals": len(historical_export_rows["historical_signals.jsonl"]),
        "technical_events": len(technical_event_rows),
        "technical_event_comparisons": len(technical_comparison_rows),
        "logical_entities": len(logical_entity_rows),
        "logical_entity_observations": len(logical_entity_observation_rows),
        "candidate_edges": len(candidate_edge_rows),
        "context_profiles": context_profiles["profile_count"],
        "context_profile_observations": context_profiles["observation_count"],
        "context_file_memberships": context_profiles["membership_count"],
        "context_resource_nodes": context_resource_usage["node_count"],
        "context_resource_edges": context_resource_usage["edge_count"],
        "context_resource_usage_metrics": context_resource_usage["metric_count"],
    }
    return result(errors, warnings, counts)


def validate_optional_inventory_evidence(evidence: Path, errors: list[str]) -> list[dict[str, Any]]:
    present = [name for name in INVENTORY_FILES if (evidence / name).exists()]
    if not present:
        return []
    for name in INVENTORY_FILES:
        if not (evidence / name).exists():
            errors.append(f"inventory evidence incomplete: missing {EVIDENCE_DIR}/{name}")
    inventory_path = evidence / "inventory.jsonl"
    if not inventory_path.exists():
        return []
    rows, row_errors = read_jsonl(inventory_path)
    errors.extend(row_errors)
    archive_paths: set[str] = set()
    required_fields = {
        "outer_archive",
        "archive_chain",
        "depth",
        "member_path",
        "archive_path",
        "size",
        "compressed_size",
        "crc",
        "extension",
        "detected_extension",
        "family",
        "namespace",
        "mime_type",
        "signature",
        "sha256",
        "status",
    }
    for index, row in enumerate(rows, start=1):
        missing = sorted(required_fields - set(row))
        if missing:
            errors.append(f"inventory.jsonl:{index} missing fields: {', '.join(missing)}")
            continue
        if not isinstance(row.get("archive_chain"), list):
            errors.append(f"inventory.jsonl:{index} archive_chain must be a list")
        elif row.get("depth") != len(row["archive_chain"]):
            errors.append(f"inventory.jsonl:{index} depth does not match archive_chain")
        archive_path = row.get("archive_path")
        if not isinstance(archive_path, str) or not archive_path:
            errors.append(f"inventory.jsonl:{index} missing archive_path")
        elif archive_path in archive_paths:
            errors.append(f"inventory.jsonl:{index} duplicate archive_path: {archive_path}")
        else:
            archive_paths.add(archive_path)
    summary_path = evidence / "inventory_summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"inventory_summary.json invalid JSON: {exc}")
        else:
            expected = summary.get("counts", {}).get("entries") if isinstance(summary, dict) else None
            if expected is not None and expected != len(rows):
                errors.append(f"inventory_summary.json entries mismatch: {expected} != {len(rows)}")
    return rows


def validate_optional_payload_disposition_evidence(
    evidence: Path,
    errors: list[str],
    inventory_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    present = [name for name in PAYLOAD_DISPOSITION_FILES if (evidence / name).exists()]
    if not present:
        return []
    for name in PAYLOAD_DISPOSITION_FILES:
        if not (evidence / name).exists():
            errors.append(f"payload disposition evidence incomplete: missing {EVIDENCE_DIR}/{name}")
    path = evidence / "payload_dispositions.jsonl"
    if not path.exists():
        return []
    rows, row_errors = read_jsonl(path)
    errors.extend(row_errors)
    expected_paths = {
        row.get("archive_path")
        for row in inventory_rows
        if row.get("is_physical_payload") and isinstance(row.get("archive_path"), str)
    }
    actual_paths: set[str] = set()
    allowed = {
        "referenced_payload",
        "duplicate_of_referenced_payload",
        "duplicate_of_unreferenced_payload",
        "unlinked_payload",
        "unhashable_payload",
    }
    for index, row in enumerate(rows, start=1):
        archive_path = row.get("physical_archive_path")
        if not isinstance(archive_path, str) or not archive_path:
            errors.append(f"payload_dispositions.jsonl:{index} missing physical_archive_path")
            continue
        if archive_path in actual_paths:
            errors.append(f"payload_dispositions.jsonl:{index} duplicate physical_archive_path: {archive_path}")
        actual_paths.add(archive_path)
        if row.get("disposition_status") not in allowed:
            errors.append(f"payload_dispositions.jsonl:{index} invalid disposition_status")
        digest = row.get("content_sha256")
        group = row.get("content_equivalence_group_id")
        if digest is not None and group != f"sha256:{digest}":
            errors.append(f"payload_dispositions.jsonl:{index} invalid content_equivalence_group_id")
        if row.get("disposition_status") == "duplicate_of_referenced_payload" and not row.get("referenced_archive_paths"):
            errors.append(f"payload_dispositions.jsonl:{index} duplicate_of_referenced_payload requires a referenced path")
    if expected_paths and actual_paths != expected_paths:
        errors.append("payload_dispositions.jsonl must contain exactly one row per physical inventory payload")
    return rows


def validate_optional_payload_materialization_evidence(
    evidence: Path,
    errors: list[str],
    disposition_rows: list[dict[str, Any]],
    manifest_rows: list[dict[str, Any]],
    unlinked_rows: list[dict[str, Any]],
) -> None:
    """Validate the post-classification one-copy-per-SHA materialization contract."""
    summary_path = evidence / "payload_materialization_summary.json"
    if not summary_path.exists():
        return
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"payload_materialization_summary.json invalid JSON: {exc}")
        return
    if not isinstance(summary, dict):
        errors.append("payload_materialization_summary.json must contain an object")
        return

    dispositions_by_path = {
        row.get("physical_archive_path"): row
        for row in disposition_rows
        if isinstance(row.get("physical_archive_path"), str)
    }
    all_rows = [*manifest_rows, *unlinked_rows]
    copied_rows = [row for row in all_rows if row.get("copy_status") == "copied"]
    canonical_sources: set[str] = set()
    copied_sha_sources: dict[str, set[str]] = {}
    for index, row in enumerate(all_rows, start=1):
        if row.get("copy_status") == "not_copied":
            continue
        archive_path = row.get("archive_path")
        if not isinstance(archive_path, str):
            continue
        disposition = dispositions_by_path.get(archive_path)
        if not disposition:
            continue
        source = row.get("materialized_from_archive_path")
        if not isinstance(source, str) or not source:
            errors.append(f"materialized payload row {index} missing materialized_from_archive_path")
            continue
        if source != disposition.get("canonical_archive_path"):
            errors.append(f"materialized payload row {index} does not use the canonical archive path")
        if disposition.get("disposition_status") == "duplicate_of_referenced_payload":
            errors.append("duplicate_of_referenced_payload must not be present in materialized output rows")

    for row in copied_rows:
        source = row.get("materialized_from_archive_path")
        if isinstance(source, str) and source:
            canonical_sources.add(source)
        digest = row.get("content_sha256")
        if isinstance(digest, str) and digest:
            copied_sha_sources.setdefault(digest, set()).add(str(source))
    for digest, sources in copied_sha_sources.items():
        if len(sources) > 1:
            errors.append(f"payload SHA-256 materialized from multiple physical sources: {digest}")

    true_unlinked_statuses = {
        "unlinked_payload",
        "duplicate_of_unreferenced_payload",
        "unhashable_payload",
    }
    unlinked_sources = {
        row.get("materialized_from_archive_path")
        for row in copied_rows
        if row in unlinked_rows and row.get("payload_disposition_status") in true_unlinked_statuses
        and isinstance(row.get("materialized_from_archive_path"), str)
    }
    expected = {
        "physical_payloads_observed": len(disposition_rows),
        "canonical_payloads_materialized": len(canonical_sources),
        "physical_copies_not_materialized": len(disposition_rows) - len(canonical_sources),
        "true_unlinked_payloads_materialized_in_unlinked": len(unlinked_sources),
    }
    for key, value in expected.items():
        if summary.get(key) != value:
            errors.append(f"payload_materialization_summary.json {key} mismatch: {summary.get(key)} != {value}")


def validate_optional_reference_evidence(evidence: Path, errors: list[str]) -> list[dict[str, Any]]:
    present = [name for name in REFERENCE_FILES if (evidence / name).exists()]
    if not present:
        return []
    for name in REFERENCE_FILES:
        if not (evidence / name).exists():
            errors.append(f"reference evidence incomplete: missing {EVIDENCE_DIR}/{name}")
    references_path = evidence / "references.jsonl"
    if not references_path.exists():
        return []
    rows, row_errors = read_jsonl(references_path)
    errors.extend(row_errors)
    required_fields = {
        "reference_id",
        "scope",
        "reference_kind",
        "value_kind",
        "conversation_id",
        "message_id",
        "node_id",
        "source_shard",
        "proof_path",
        "metadata_key",
        "raw_identifier",
        "normalized_identifier",
        "namespace",
        "logical_name",
        "declared_mime_type",
        "declared_size",
        "inventory_source_archive",
        "inventory_status",
    }
    reference_ids: set[str] = set()
    forbidden_resolution_fields = {
        "physical_archive_path",
        "asset_status",
        "candidate_archive_paths",
        "resolution_status",
        "resolution_method",
    }
    for index, row in enumerate(rows, start=1):
        missing = sorted(required_fields - set(row))
        if missing:
            errors.append(f"references.jsonl:{index} missing fields: {', '.join(missing)}")
            continue
        reference_id = row.get("reference_id")
        if not isinstance(reference_id, str) or not reference_id:
            errors.append(f"references.jsonl:{index} missing reference_id")
        elif reference_id in reference_ids:
            errors.append(f"references.jsonl:{index} duplicate reference_id: {reference_id}")
        else:
            reference_ids.add(reference_id)
        if not row.get("reference_kind") or not row.get("value_kind"):
            errors.append(f"references.jsonl:{index} missing reference kind")
        if not isinstance(row.get("raw_identifier"), str) or not row["raw_identifier"]:
            errors.append(f"references.jsonl:{index} missing raw_identifier")
        if not row.get("proof_path"):
            errors.append(f"references.jsonl:{index} missing proof_path")
        leaked = sorted(forbidden_resolution_fields & set(row))
        if leaked:
            errors.append(f"references.jsonl:{index} must not contain physical resolution fields: {', '.join(leaked)}")
    summary_path = evidence / "reference_summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"reference_summary.json invalid JSON: {exc}")
        else:
            expected = summary.get("counts", {}).get("references") if isinstance(summary, dict) else None
            if expected is not None and expected != len(rows):
                errors.append(f"reference_summary.json references mismatch: {expected} != {len(rows)}")
    return rows


def validate_optional_physical_resolution_evidence(
    evidence: Path,
    errors: list[str],
    reference_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    present = [name for name in PHYSICAL_RESOLUTION_FILES if (evidence / name).exists()]
    if not present:
        return []
    for name in PHYSICAL_RESOLUTION_FILES:
        if not (evidence / name).exists():
            errors.append(f"physical resolution evidence incomplete: missing {EVIDENCE_DIR}/{name}")
    resolutions_path = evidence / "physical_resolutions.jsonl"
    if not resolutions_path.exists():
        return []
    rows, row_errors = read_jsonl(resolutions_path)
    errors.extend(row_errors)
    allowed_statuses = {
        "resolved_unique",
        "resolved_equivalent_candidates",
        "collision",
        "no_candidate_in_inventory",
        "candidate_unreadable",
        "external_non_exportable",
        "inline_payload_not_archive_member",
        "non_physical_reference",
        "inventory_unavailable",
        "indeterminate_inventory_error",
    }
    required_fields = {
        "resolution_id",
        "reference_id",
        "conversation_id",
        "message_id",
        "node_id",
        "source_shard",
        "proof_path",
        "reference_kind",
        "value_kind",
        "raw_identifier",
        "normalized_identifier",
        "namespace",
        "resolution_status",
        "reason",
        "candidate_count",
        "candidates",
        "selected_archive_path",
        "inventory_source_archive",
        "inventory_status",
    }
    candidate_fields = {
        "archive_path",
        "member_path",
        "archive_chain",
        "family",
        "namespace",
        "size",
        "extension",
        "detected_extension",
        "mime_type",
        "signature",
        "sha256",
        "inventory_status",
        "match_methods",
        "declared_size_matches",
    }
    resolution_ids: set[str] = set()
    resolved_reference_ids: set[str] = set()
    for index, row in enumerate(rows, start=1):
        missing = sorted(required_fields - set(row))
        if missing:
            errors.append(f"physical_resolutions.jsonl:{index} missing fields: {', '.join(missing)}")
            continue
        resolution_id = row.get("resolution_id")
        if not isinstance(resolution_id, str) or not resolution_id:
            errors.append(f"physical_resolutions.jsonl:{index} missing resolution_id")
        elif resolution_id in resolution_ids:
            errors.append(f"physical_resolutions.jsonl:{index} duplicate resolution_id: {resolution_id}")
        else:
            resolution_ids.add(resolution_id)
        reference_id = row.get("reference_id")
        if not isinstance(reference_id, str) or not reference_id:
            errors.append(f"physical_resolutions.jsonl:{index} missing reference_id")
        elif reference_id in resolved_reference_ids:
            errors.append(f"physical_resolutions.jsonl:{index} duplicate reference_id: {reference_id}")
        else:
            resolved_reference_ids.add(reference_id)
        status = row.get("resolution_status")
        if status not in allowed_statuses:
            errors.append(f"physical_resolutions.jsonl:{index} invalid resolution_status: {status}")
        candidates = row.get("candidates")
        if not isinstance(candidates, list):
            errors.append(f"physical_resolutions.jsonl:{index} candidates must be a list")
            continue
        if row.get("candidate_count") != len(candidates):
            errors.append(f"physical_resolutions.jsonl:{index} candidate_count does not match candidates")
        candidate_paths: set[str] = set()
        for candidate_index, candidate in enumerate(candidates, start=1):
            if not isinstance(candidate, dict):
                errors.append(f"physical_resolutions.jsonl:{index} candidate {candidate_index} must be an object")
                continue
            missing_candidate = sorted(candidate_fields - set(candidate))
            if missing_candidate:
                errors.append(
                    f"physical_resolutions.jsonl:{index} candidate {candidate_index} missing fields: {', '.join(missing_candidate)}"
                )
                continue
            archive_path = candidate.get("archive_path")
            if not isinstance(archive_path, str) or not archive_path:
                errors.append(f"physical_resolutions.jsonl:{index} candidate {candidate_index} missing archive_path")
            elif archive_path in candidate_paths:
                errors.append(f"physical_resolutions.jsonl:{index} duplicate candidate archive_path: {archive_path}")
            else:
                candidate_paths.add(archive_path)
            if not isinstance(candidate.get("match_methods"), list) or not candidate["match_methods"]:
                errors.append(f"physical_resolutions.jsonl:{index} candidate {candidate_index} missing match_methods")
        selected = row.get("selected_archive_path")
        if status == "resolved_unique":
            if len(candidates) != 1 or selected != candidates[0].get("archive_path"):
                errors.append(f"physical_resolutions.jsonl:{index} resolved_unique requires its sole candidate as selected_archive_path")
        elif status == "resolved_equivalent_candidates":
            candidate_hashes = {candidate.get("sha256") for candidate in candidates}
            if len(candidates) < 2 or selected not in candidate_paths or None in candidate_hashes or len(candidate_hashes) != 1:
                errors.append(
                    f"physical_resolutions.jsonl:{index} resolved_equivalent_candidates requires two or more same-SHA candidates and one selected path"
                )
        elif selected is not None:
            errors.append(f"physical_resolutions.jsonl:{index} only a selected resolution may select an archive path")
        if status in {"collision", "candidate_unreadable"} and not candidates:
            errors.append(f"physical_resolutions.jsonl:{index} {status} requires at least one candidate")
        if status in {
            "no_candidate_in_inventory",
            "external_non_exportable",
            "inline_payload_not_archive_member",
            "non_physical_reference",
            "inventory_unavailable",
            "indeterminate_inventory_error",
        } and candidates:
            errors.append(f"physical_resolutions.jsonl:{index} {status} must not have physical candidates")
    reference_ids = {row.get("reference_id") for row in reference_rows if isinstance(row.get("reference_id"), str)}
    if reference_rows and resolved_reference_ids != reference_ids:
        errors.append("physical resolution references do not exactly match references.jsonl")
    if rows and not reference_rows:
        errors.append("physical resolution evidence requires references.jsonl")
    summary_path = evidence / "physical_resolution_summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"physical_resolution_summary.json invalid JSON: {exc}")
        else:
            expected = summary.get("counts", {}).get("resolutions") if isinstance(summary, dict) else None
            if expected is not None and expected != len(rows):
                errors.append(f"physical_resolution_summary.json resolutions mismatch: {expected} != {len(rows)}")
    return rows


def validate_optional_asset_resolution_migration_evidence(evidence: Path, errors: list[str]) -> list[dict[str, Any]]:
    present = [name for name in ASSET_RESOLUTION_MIGRATION_FILES if (evidence / name).exists()]
    if not present:
        return []
    for name in ASSET_RESOLUTION_MIGRATION_FILES:
        if not (evidence / name).exists():
            errors.append(f"asset resolution migration evidence incomplete: missing {EVIDENCE_DIR}/{name}")
    comparisons_path = evidence / "asset_resolution_comparisons.jsonl"
    if not comparisons_path.exists():
        return []
    rows, row_errors = read_jsonl(comparisons_path)
    errors.extend(row_errors)
    required_fields = {
        "asset_ref_id",
        "conversation_id",
        "message_id",
        "proof_path",
        "raw_file_id",
        "legacy_asset_status",
        "legacy_physical_archive_path",
        "matching_reference_ids",
        "matching_resolution_ids",
        "matching_resolution_statuses",
        "unique_resolved_archive_paths",
        "migration_status",
        "effective_asset_status",
        "effective_physical_archive_path",
    }
    asset_ids: set[str] = set()
    for index, row in enumerate(rows, start=1):
        missing = sorted(required_fields - set(row))
        if missing:
            errors.append(f"asset_resolution_comparisons.jsonl:{index} missing fields: {', '.join(missing)}")
            continue
        asset_ref_id = row.get("asset_ref_id")
        if not isinstance(asset_ref_id, str) or not asset_ref_id:
            errors.append(f"asset_resolution_comparisons.jsonl:{index} missing asset_ref_id")
        elif asset_ref_id in asset_ids:
            errors.append(f"asset_resolution_comparisons.jsonl:{index} duplicate asset_ref_id: {asset_ref_id}")
        else:
            asset_ids.add(asset_ref_id)
        for field in (
            "matching_reference_ids",
            "matching_resolution_ids",
            "matching_resolution_statuses",
            "unique_resolved_archive_paths",
        ):
            if not isinstance(row.get(field), list):
                errors.append(f"asset_resolution_comparisons.jsonl:{index} {field} must be a list")
        if not isinstance(row.get("migration_status"), str) or not row["migration_status"]:
            errors.append(f"asset_resolution_comparisons.jsonl:{index} missing migration_status")
    summary_path = evidence / "asset_resolution_migration_summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"asset_resolution_migration_summary.json invalid JSON: {exc}")
        else:
            expected = summary.get("counts", {}).get("assets") if isinstance(summary, dict) else None
            if expected is not None and expected != len(rows):
                errors.append(f"asset_resolution_migration_summary.json assets mismatch: {expected} != {len(rows)}")
    return rows


def validate_optional_unverified_payload_candidate_evidence(
    evidence: Path,
    errors: list[str],
    assets: list[dict[str, Any]],
    manifest: list[dict[str, Any]],
    migration_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    present = [name for name in UNVERIFIED_PAYLOAD_CANDIDATE_FILES if (evidence / name).exists()]
    if not present:
        return []
    for name in UNVERIFIED_PAYLOAD_CANDIDATE_FILES:
        if not (evidence / name).exists():
            errors.append(f"unverified payload candidate evidence incomplete: missing {EVIDENCE_DIR}/{name}")
    rows_path = evidence / "unverified_payload_candidates.jsonl"
    if not rows_path.exists():
        return []
    rows, row_errors = read_jsonl(rows_path)
    errors.extend(row_errors)
    assets_by_id = {row.get("asset_ref_id"): row for row in assets if isinstance(row.get("asset_ref_id"), str)}
    migration_by_asset_id = {
        row.get("asset_ref_id"): row
        for row in migration_rows
        if isinstance(row.get("asset_ref_id"), str)
    }
    manifest_identities = {
        (row.get("file_id"), row.get("physical_archive_path") or row.get("archive_path"))
        for row in manifest
        if row.get("materialization_status") == "materialized"
    }
    unlinked_identities: set[tuple[Any, Any]] = set()
    unlinked_path = evidence / "unlinked_assets.jsonl"
    if unlinked_path.exists():
        unlinked_rows, unlinked_errors = read_jsonl(unlinked_path)
        errors.extend(unlinked_errors)
        unlinked_identities = {
            (row.get("file_id"), row.get("physical_archive_path") or row.get("archive_path"))
            for row in unlinked_rows
            if row.get("copy_status") == "not_copied"
        }
    required = {
        "candidate_id", "status", "canonical", "confidence", "matching_rule", "non_promotion_reason",
        "asset_ref_id", "conversation_id", "message_id", "reference_file_id", "reference_logical_basename",
        "reference_normalized_extension", "reference_proof_path", "reference_resolution_statuses",
        "candidate_file_id", "candidate_logical_basename", "candidate_physical_archive_path", "candidate_copied_path",
        "reference_declared_size", "candidate_size", "candidate_payload_disposition_status",
        "candidate_declared_extension", "candidate_detected_extension", "candidate_detected_mime", "type_comparison",
        "candidate_count_for_name", "ambiguities", "proof_paths",
    }
    candidate_ids: set[str] = set()
    for index, row in enumerate(rows, start=1):
        missing = sorted(required - set(row))
        if missing:
            errors.append(f"unverified_payload_candidates.jsonl:{index} missing fields: {', '.join(missing)}")
            continue
        candidate_id = row.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id.startswith("unverified-payload-candidate:"):
            errors.append(f"unverified_payload_candidates.jsonl:{index} invalid candidate_id")
        elif candidate_id in candidate_ids:
            errors.append(f"unverified_payload_candidates.jsonl:{index} duplicate candidate_id")
        else:
            candidate_ids.add(candidate_id)
        if row.get("status") != "unverified_candidate" or row.get("canonical") is not False or row.get("confidence") != "low":
            errors.append(f"unverified_payload_candidates.jsonl:{index} invalid non-canonical status contract")
        if row.get("matching_rule") != "url_decoded_casefolded_logical_basename_equality":
            errors.append(f"unverified_payload_candidates.jsonl:{index} invalid matching_rule")
        if row.get("type_comparison") not in {"same_extension", "extension_conflict", "extension_unavailable"}:
            errors.append(f"unverified_payload_candidates.jsonl:{index} invalid type_comparison")
        if not isinstance(row.get("candidate_count_for_name"), int) or row["candidate_count_for_name"] < 1:
            errors.append(f"unverified_payload_candidates.jsonl:{index} invalid candidate_count_for_name")
        if not isinstance(row.get("ambiguities"), list) or not isinstance(row.get("proof_paths"), list):
            errors.append(f"unverified_payload_candidates.jsonl:{index} ambiguities and proof_paths must be lists")
        asset = assets_by_id.get(row.get("asset_ref_id"))
        if not asset:
            errors.append(f"unverified_payload_candidates.jsonl:{index} unknown asset_ref_id")
        else:
            if asset.get("asset_status") != "missing" or asset.get("raw_file_id") != row.get("reference_file_id"):
                errors.append(f"unverified_payload_candidates.jsonl:{index} reference asset is not the declared missing file")
        comparison = migration_by_asset_id.get(row.get("asset_ref_id"))
        if not comparison or set(comparison.get("matching_resolution_statuses") or []) != {"no_candidate_in_inventory"}:
            errors.append(f"unverified_payload_candidates.jsonl:{index} reference lacks no_candidate_in_inventory proof")
        if row.get("reference_file_id") == row.get("candidate_file_id"):
            errors.append(f"unverified_payload_candidates.jsonl:{index} self candidate is forbidden")
        candidate_identity = (row.get("candidate_file_id"), row.get("candidate_physical_archive_path"))
        if row.get("candidate_copied_path") is not None and candidate_identity not in manifest_identities:
            errors.append(f"unverified_payload_candidates.jsonl:{index} copied candidate does not match a materialized manifest row")
        if row.get("candidate_copied_path") is None and candidate_identity not in unlinked_identities:
            errors.append(f"unverified_payload_candidates.jsonl:{index} uncopied candidate does not match an unlinked payload row")
    summary_path = evidence / "unverified_payload_candidate_summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"unverified_payload_candidate_summary.json invalid JSON: {exc}")
        else:
            counts = summary.get("counts") if isinstance(summary, dict) else None
            if not isinstance(counts, dict) or counts.get("candidate_rows") != len(rows):
                errors.append("unverified_payload_candidate_summary.json candidate_rows mismatch")
            contract = summary.get("contract") if isinstance(summary, dict) else None
            expected = {
                "raw_zip_reparsed": False,
                "physical_resolution_modified": False,
                "payload_dispositions_modified": False,
                "payloads_copied": False,
                "canonical_relations_created": False,
                "name_equality_is_not_physical_identity": True,
            }
            if not isinstance(contract, dict) or any(contract.get(key) != value for key, value in expected.items()):
                errors.append("unverified_payload_candidate_summary.json invalid non-promotion contract")
    return rows


def validate_optional_runtime_artifact_evidence(evidence: Path, errors: list[str]) -> list[dict[str, Any]]:
    present = [name for name in RUNTIME_ARTIFACT_FILES if (evidence / name).exists()]
    if not present:
        return []
    for name in RUNTIME_ARTIFACT_FILES:
        if not (evidence / name).exists():
            errors.append(f"runtime artifact evidence incomplete: missing {EVIDENCE_DIR}/{name}")
    rows_path = evidence / "runtime_artifacts.jsonl"
    if not rows_path.exists():
        return []
    rows, row_errors = read_jsonl(rows_path)
    errors.extend(row_errors)
    required_fields = {
        "artifact_id",
        "conversation_id",
        "execution_message_id",
        "physical_archive_path",
        "runtime_path",
        "filename",
        "relation_status",
        "relation_reason",
    }
    seen_ids: set[str] = set()
    for index, row in enumerate(rows, start=1):
        missing = sorted(required_fields - set(row))
        if missing:
            errors.append(f"runtime_artifacts.jsonl:{index} missing fields: {', '.join(missing)}")
            continue
        artifact_id = row.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            errors.append(f"runtime_artifacts.jsonl:{index} missing artifact_id")
        elif artifact_id in seen_ids:
            errors.append(f"runtime_artifacts.jsonl:{index} duplicate artifact_id: {artifact_id}")
        else:
            seen_ids.add(artifact_id)
        if row.get("relation_status") not in {
            "confirmed_by_chat_html",
            "conversation_scoped_unconfirmed",
        }:
            errors.append(f"runtime_artifacts.jsonl:{index} invalid relation_status: {row.get('relation_status')}")
        if row.get("relation_status") == "confirmed_by_chat_html" and not row.get("chat_html_archive_path"):
            errors.append(f"runtime_artifacts.jsonl:{index} confirmed row missing chat_html_archive_path")
        copied_path = row.get("copied_pack_path")
        if copied_path and not (evidence.parent / copied_path).exists():
            errors.append(f"runtime_artifacts.jsonl copied_pack_path missing on disk: {copied_path}")
    summary_path = evidence / "runtime_artifacts_summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"runtime_artifacts_summary.json invalid JSON: {exc}")
        else:
            expected = summary.get("counts", {}).get("runtime_artifacts") if isinstance(summary, dict) else None
            if expected is not None and expected != len(rows):
                errors.append(f"runtime_artifacts_summary.json runtime_artifacts mismatch: {expected} != {len(rows)}")
    return rows


def validate_optional_historical_export_evidence(evidence: Path, errors: list[str]) -> dict[str, list[dict[str, Any]]]:
    present = [name for name in HISTORICAL_EXPORT_FILES if (evidence / name).exists()]
    empty = {name: [] for name in HISTORICAL_EXPORT_FILES if name.endswith(".jsonl")}
    if not present:
        return empty
    for name in HISTORICAL_EXPORT_FILES:
        if not (evidence / name).exists():
            errors.append(f"historical export evidence incomplete: missing {EVIDENCE_DIR}/{name}")
    rows_by_name = dict(empty)
    for name in rows_by_name:
        path = evidence / name
        if not path.exists():
            continue
        rows, row_errors = read_jsonl(path)
        rows_by_name[name] = rows
        errors.extend(row_errors)

    required_fields = {
        "historical_export_snapshots.jsonl": {"snapshot_id", "archive_path", "conversations_archive_path", "status"},
        "historical_conversation_comparisons.jsonl": {"comparison_id", "snapshot_id", "conversation_id", "historical_conversations_archive_path", "status"},
        "historical_messages.jsonl": {"historical_message_id", "snapshot_id", "conversation_id", "node_id", "message_status", "historical_conversations_archive_path", "proof_path"},
        "historical_signals.jsonl": {"signal_id", "snapshot_id", "conversation_id", "node_id", "message_status", "signal_kind", "historical_conversations_archive_path", "proof_path"},
    }
    id_fields = {
        "historical_export_snapshots.jsonl": "snapshot_id",
        "historical_conversation_comparisons.jsonl": "comparison_id",
        "historical_messages.jsonl": "historical_message_id",
        "historical_signals.jsonl": "signal_id",
    }
    for name, required in required_fields.items():
        seen: set[str] = set()
        for index, row in enumerate(rows_by_name[name], start=1):
            missing = sorted(required - set(row))
            if missing:
                errors.append(f"{name}:{index} missing fields: {', '.join(missing)}")
                continue
            identifier = row.get(id_fields[name])
            if not isinstance(identifier, str) or not identifier:
                errors.append(f"{name}:{index} missing {id_fields[name]}")
            elif identifier in seen:
                errors.append(f"{name}:{index} duplicate {id_fields[name]}: {identifier}")
            else:
                seen.add(identifier)
            if name in {"historical_messages.jsonl", "historical_signals.jsonl"} and row.get("message_status") not in {"historical_only", "also_in_primary"}:
                errors.append(f"{name}:{index} invalid message_status: {row.get('message_status')}")
    summary_path = evidence / "historical_export_summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"historical_export_summary.json invalid JSON: {exc}")
        else:
            counts = summary.get("counts", {}) if isinstance(summary, dict) else {}
            expected = {
                "snapshots": len(rows_by_name["historical_export_snapshots.jsonl"]),
                "comparisons": len(rows_by_name["historical_conversation_comparisons.jsonl"]),
                "historical_messages": len(rows_by_name["historical_messages.jsonl"]),
                "signals": len(rows_by_name["historical_signals.jsonl"]),
            }
            for key, actual in expected.items():
                if counts.get(key) is not None and counts.get(key) != actual:
                    errors.append(f"historical_export_summary.json {key} mismatch: {counts.get(key)} != {actual}")
    return rows_by_name


def validate_optional_technical_event_evidence(
    evidence: Path,
    errors: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    present = [name for name in TECHNICAL_EVENT_FILES if (evidence / name).exists()]
    if not present:
        return [], []
    for name in TECHNICAL_EVENT_FILES:
        if not (evidence / name).exists():
            errors.append(f"technical event evidence incomplete: missing {EVIDENCE_DIR}/{name}")
    event_path = evidence / "technical_events.jsonl"
    comparison_path = evidence / "technical_event_comparisons.jsonl"
    unknown_path = evidence / "technical_event_unknowns.jsonl"
    if not event_path.exists() or not comparison_path.exists() or not unknown_path.exists():
        return [], []
    events, event_errors = read_jsonl(event_path)
    comparisons, comparison_errors = read_jsonl(comparison_path)
    unknowns, unknown_errors = read_jsonl(unknown_path)
    errors.extend(event_errors + comparison_errors + unknown_errors)
    required_event_fields = {
        "technical_event_id", "event_family", "operation_observed", "source_export", "source_archive_path", "proof_path",
        "conversation_id", "node_id", "message_id", "child_node_ids", "relevant_metadata", "confidence",
        "provenance_status", "observation_status", "field_presence",
    }
    valid_sources = {"primary_2026_json", "historical_embedded_json", "chat_html_auxiliary"}
    event_ids: set[str] = set()
    unknown_ids: set[str] = set()
    for index, row in enumerate(events, start=1):
        missing = sorted(required_event_fields - set(row))
        if missing:
            errors.append(f"technical_events.jsonl:{index} missing fields: {', '.join(missing)}")
            continue
        event_id = row.get("technical_event_id")
        if not isinstance(event_id, str) or not event_id.startswith("technical-event:"):
            errors.append(f"technical_events.jsonl:{index} invalid pipeline technical_event_id")
        elif event_id in event_ids:
            errors.append(f"technical_events.jsonl:{index} duplicate technical_event_id: {event_id}")
        else:
            event_ids.add(event_id)
        if row.get("source_export") not in valid_sources:
            errors.append(f"technical_events.jsonl:{index} invalid source_export: {row.get('source_export')}")
        if row.get("observation_status") != "observed_no_physical_resolution":
            errors.append(f"technical_events.jsonl:{index} must not promote a physical resolution")
        if "physical_archive_path" in row or "candidate_archive_paths" in row:
            errors.append(f"technical_events.jsonl:{index} must not contain physical-resolution fields")
        if not isinstance(row.get("child_node_ids"), list) or not isinstance(row.get("relevant_metadata"), dict) or not isinstance(row.get("field_presence"), dict):
            errors.append(f"technical_events.jsonl:{index} structural fields have invalid type")
        if row.get("event_family") == "unknown_recipient":
            unknown_ids.add(event_id)
    actual_unknown_ids = {row.get("technical_event_id") for row in unknowns if isinstance(row.get("technical_event_id"), str)}
    if actual_unknown_ids != unknown_ids:
        errors.append("technical_event_unknowns.jsonl must contain exactly unknown_recipient event rows")

    required_comparison_fields = {
        "comparison_id", "conversation_id", "node_id", "message_id", "event_family", "operation_observed",
        "primary_event_ids", "historical_event_ids", "auxiliary_event_ids", "comparison_status", "comparison_basis",
    }
    valid_statuses = {"present_both", "historical_only", "primary_only", "auxiliary_only", "contradiction"}
    comparison_ids: set[str] = set()
    for index, row in enumerate(comparisons, start=1):
        missing = sorted(required_comparison_fields - set(row))
        if missing:
            errors.append(f"technical_event_comparisons.jsonl:{index} missing fields: {', '.join(missing)}")
            continue
        identifier = row.get("comparison_id")
        if not isinstance(identifier, str) or not identifier.startswith("technical-comparison:"):
            errors.append(f"technical_event_comparisons.jsonl:{index} invalid comparison_id")
        elif identifier in comparison_ids:
            errors.append(f"technical_event_comparisons.jsonl:{index} duplicate comparison_id: {identifier}")
        else:
            comparison_ids.add(identifier)
        if row.get("comparison_status") not in valid_statuses:
            errors.append(f"technical_event_comparisons.jsonl:{index} invalid comparison_status: {row.get('comparison_status')}")
        for field in ("primary_event_ids", "historical_event_ids", "auxiliary_event_ids"):
            values = row.get(field)
            if not isinstance(values, list) or any(value not in event_ids for value in values):
                errors.append(f"technical_event_comparisons.jsonl:{index} invalid {field}")
    summary_path = evidence / "technical_event_summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"technical_event_summary.json invalid JSON: {exc}")
        else:
            counts = summary.get("counts", {}) if isinstance(summary, dict) else {}
            if counts.get("technical_events") is not None and counts.get("technical_events") != len(events):
                errors.append("technical_event_summary.json technical_events mismatch")
            if counts.get("comparisons") is not None and counts.get("comparisons") != len(comparisons):
                errors.append("technical_event_summary.json comparisons mismatch")
            contract = summary.get("contract") if isinstance(summary, dict) else None
            if not isinstance(contract, dict) or any(contract.get(key) is not False for key in ("physical_resolution_called", "messages_merged", "candidate_edges_created")):
                errors.append("technical_event_summary.json invalid no-resolution contract")
    return events, comparisons


def validate_optional_logical_entity_evidence(
    evidence: Path,
    errors: list[str],
    technical_events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    present = [name for name in LOGICAL_ENTITY_FILES if (evidence / name).exists()]
    if not present:
        return [], []
    for name in LOGICAL_ENTITY_FILES:
        if not (evidence / name).exists():
            errors.append(f"logical entity evidence incomplete: missing {EVIDENCE_DIR}/{name}")
    if not technical_events:
        errors.append("logical entity evidence requires complete technical-event evidence")
    entity_path = evidence / "logical_entities.jsonl"
    observation_path = evidence / "logical_entity_observations.jsonl"
    unmaterialized_path = evidence / "logical_entity_unmaterialized_events.jsonl"
    migration_path = evidence / "logical_entity_taxonomy_migration.jsonl"
    if not entity_path.exists() or not observation_path.exists() or not unmaterialized_path.exists() or not migration_path.exists():
        return [], []
    entities, entity_errors = read_jsonl(entity_path)
    observations, observation_errors = read_jsonl(observation_path)
    unmaterialized, unmaterialized_errors = read_jsonl(unmaterialized_path)
    migrations, migration_errors = read_jsonl(migration_path)
    errors.extend(entity_errors + observation_errors + unmaterialized_errors + migration_errors)

    technical_event_ids = {
        row.get("technical_event_id") for row in technical_events if isinstance(row.get("technical_event_id"), str)
    }
    entity_ids: set[str] = set()
    required_entity_fields = {
        "logical_entity_id", "entity_type", "identifier_kind", "explicit_identifier", "entity_status", "confidence",
        "provenance_status", "source_exports", "source_archive_paths", "source_conversation_ids", "source_node_ids",
        "source_message_ids", "source_event_ids", "observation_ids", "observation_count", "source_presence_status",
        "candidate_edges_created", "entity_subtype", "scope_kind", "scope_value",
    }
    valid_types = {
        "remote_asset_pointer", "original_file_reference", "mask_file_reference", "library_file_reference",
        "runtime_path_literal", "original_generation_reference", "persistent_textdoc_reference",
        "temporary_textdoc_handle", "embedded_canmore_uri_reference", "project_context_reference",
        "gizmo_context_reference",
    }
    forbidden_fields = {"physical_archive_path", "candidate_archive_paths", "resolution_status", "copied_pack_path", "asset_status", "candidate_edges"}
    for index, row in enumerate(entities, start=1):
        missing = sorted(required_entity_fields - set(row))
        if missing:
            errors.append(f"logical_entities.jsonl:{index} missing fields: {', '.join(missing)}")
            continue
        entity_id = row.get("logical_entity_id")
        if not isinstance(entity_id, str) or not entity_id.startswith("logical-entity:"):
            errors.append(f"logical_entities.jsonl:{index} invalid pipeline logical_entity_id")
        elif entity_id in entity_ids:
            errors.append(f"logical_entities.jsonl:{index} duplicate logical_entity_id: {entity_id}")
        else:
            entity_ids.add(entity_id)
        if row.get("entity_type") not in valid_types:
            errors.append(f"logical_entities.jsonl:{index} invalid entity_type: {row.get('entity_type')}")
        if row.get("entity_type") in {"logical_asset", "generation", "textdoc", "gizmo"}:
            errors.append(f"logical_entities.jsonl:{index} contains deprecated entity_type")
        if row.get("entity_status") != "explicit_identifier_grouped_no_physical_resolution":
            errors.append(f"logical_entities.jsonl:{index} invalid no-resolution entity_status")
        if row.get("candidate_edges_created") is not False:
            errors.append(f"logical_entities.jsonl:{index} must not create candidate edges")
        if forbidden_fields & set(row):
            errors.append(f"logical_entities.jsonl:{index} contains forbidden resolution or edge fields")
        for field in ("source_exports", "source_archive_paths", "source_conversation_ids", "source_node_ids", "source_message_ids", "source_event_ids", "observation_ids"):
            if not isinstance(row.get(field), list):
                errors.append(f"logical_entities.jsonl:{index} invalid {field}")
        if not isinstance(row.get("observation_count"), int) or row.get("observation_count", 0) < 1:
            errors.append(f"logical_entities.jsonl:{index} invalid observation_count")
        source_event_ids = row.get("source_event_ids")
        if isinstance(source_event_ids, list) and any(event_id not in technical_event_ids for event_id in source_event_ids):
            errors.append(f"logical_entities.jsonl:{index} references unknown technical_event_id")

    required_observation_fields = {
        "logical_entity_observation_id", "logical_entity_id", "entity_type", "identifier_kind", "explicit_identifier",
        "technical_event_id", "source_export", "source_archive_path", "proof_path", "conversation_id", "node_id",
        "message_id", "observation_status", "entity_subtype", "scope_kind", "scope_value",
    }
    observation_ids: set[str] = set()
    observations_by_entity: Counter[str] = Counter()
    observation_event_ids: set[str] = set()
    for index, row in enumerate(observations, start=1):
        missing = sorted(required_observation_fields - set(row))
        if missing:
            errors.append(f"logical_entity_observations.jsonl:{index} missing fields: {', '.join(missing)}")
            continue
        identifier = row.get("logical_entity_observation_id")
        if not isinstance(identifier, str) or not identifier.startswith("logical-entity-observation:"):
            errors.append(f"logical_entity_observations.jsonl:{index} invalid observation ID")
        elif identifier in observation_ids:
            errors.append(f"logical_entity_observations.jsonl:{index} duplicate observation ID: {identifier}")
        else:
            observation_ids.add(identifier)
        if row.get("logical_entity_id") not in entity_ids:
            errors.append(f"logical_entity_observations.jsonl:{index} references unknown logical_entity_id")
        event_id = row.get("technical_event_id")
        if event_id not in technical_event_ids:
            errors.append(f"logical_entity_observations.jsonl:{index} references unknown technical_event_id")
        elif isinstance(event_id, str):
            observation_event_ids.add(event_id)
        if row.get("observation_status") != "derived_from_explicit_technical_event_identifier":
            errors.append(f"logical_entity_observations.jsonl:{index} invalid observation_status")
        observations_by_entity[str(row.get("logical_entity_id"))] += 1
    for row in entities:
        entity_id = row.get("logical_entity_id")
        if isinstance(entity_id, str) and row.get("observation_count") != observations_by_entity.get(entity_id):
            errors.append(f"logical_entities.jsonl observation_count mismatch: {entity_id}")
        if isinstance(entity_id, str) and set(row.get("observation_ids") or []) - observation_ids:
            errors.append(f"logical_entities.jsonl references unknown observation IDs: {entity_id}")

    unmaterialized_event_ids: set[str] = set()
    for index, row in enumerate(unmaterialized, start=1):
        event_id = row.get("technical_event_id")
        if event_id not in technical_event_ids:
            errors.append(f"logical_entity_unmaterialized_events.jsonl:{index} references unknown technical_event_id")
        elif isinstance(event_id, str):
            unmaterialized_event_ids.add(event_id)
        if row.get("reason") not in {"no_explicit_identifier_in_entity_construction_scope", "textdoc_non_identifier_metadata_only"}:
            errors.append(f"logical_entity_unmaterialized_events.jsonl:{index} invalid reason")
    expected_unmaterialized = technical_event_ids - observation_event_ids
    if unmaterialized_event_ids != expected_unmaterialized:
        errors.append("logical_entity_unmaterialized_events.jsonl must contain exactly technical events without an entity observation")

    required_migration_fields = {
        "migration_id", "old_logical_entity_id", "old_entity_type", "old_identifier_kind", "old_explicit_identifier",
        "old_source_event_ids", "new_entity_type", "new_identifier_kind", "new_logical_entity_id", "reason", "status",
    }
    migration_ids: set[str] = set()
    for index, row in enumerate(migrations, start=1):
        missing = sorted(required_migration_fields - set(row))
        if missing:
            errors.append(f"logical_entity_taxonomy_migration.jsonl:{index} missing fields: {', '.join(missing)}")
            continue
        identifier = row.get("migration_id")
        if not isinstance(identifier, str) or not identifier.startswith("logical-entity-migration:"):
            errors.append(f"logical_entity_taxonomy_migration.jsonl:{index} invalid migration_id")
        elif identifier in migration_ids:
            errors.append(f"logical_entity_taxonomy_migration.jsonl:{index} duplicate migration_id: {identifier}")
        else:
            migration_ids.add(identifier)
        if row.get("old_entity_type") not in {"logical_asset", "generation", "textdoc", "gizmo"}:
            errors.append(f"logical_entity_taxonomy_migration.jsonl:{index} invalid old_entity_type")
        new_id = row.get("new_logical_entity_id")
        if new_id is not None and new_id not in entity_ids:
            errors.append(f"logical_entity_taxonomy_migration.jsonl:{index} references unknown new_logical_entity_id")
        if not isinstance(row.get("old_source_event_ids"), list):
            errors.append(f"logical_entity_taxonomy_migration.jsonl:{index} invalid old_source_event_ids")

    summary_path = evidence / "logical_entity_summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"logical_entity_summary.json invalid JSON: {exc}")
        else:
            counts = summary.get("counts", {}) if isinstance(summary, dict) else {}
            if counts.get("logical_entities") is not None and counts.get("logical_entities") != len(entities):
                errors.append("logical_entity_summary.json logical_entities mismatch")
            if counts.get("logical_entity_observations") is not None and counts.get("logical_entity_observations") != len(observations):
                errors.append("logical_entity_summary.json logical_entity_observations mismatch")
            if counts.get("unmaterialized_technical_events") is not None and counts.get("unmaterialized_technical_events") != len(unmaterialized):
                errors.append("logical_entity_summary.json unmaterialized_technical_events mismatch")
            if counts.get("taxonomy_migration_rows") is not None and counts.get("taxonomy_migration_rows") != len(migrations):
                errors.append("logical_entity_summary.json taxonomy_migration_rows mismatch")
            contract = summary.get("contract") if isinstance(summary, dict) else None
            required_contract = {
                "source_layer": "technical_event_evidence_only",
                "physical_resolution_called": False,
                "candidate_edges_created": False,
                "causal_relations_created": False,
                "messages_merged": False,
                "historical_messages_merged": False,
                "payload_relations_created": False,
            }
            if not isinstance(contract, dict) or any(contract.get(key) != value for key, value in required_contract.items()):
                errors.append("logical_entity_summary.json invalid no-edge/no-resolution contract")
    return entities, observations


def validate_optional_candidate_edge_evidence(
    evidence: Path,
    errors: list[str],
    logical_entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    present = [name for name in CANDIDATE_EDGE_FILES if (evidence / name).exists()]
    if not present:
        return []
    for name in CANDIDATE_EDGE_FILES:
        if not (evidence / name).exists():
            errors.append(f"candidate edge evidence incomplete: missing {EVIDENCE_DIR}/{name}")
    path = evidence / "candidate_edges.jsonl"
    if not path.exists():
        return []
    rows, row_errors = read_jsonl(path)
    errors.extend(row_errors)
    entity_ids = {row.get("logical_entity_id") for row in logical_entities if isinstance(row.get("logical_entity_id"), str)}
    ids: set[str] = set()
    required = {"candidate_edge_id", "edge_type", "source_entity_id", "target_id", "target_kind", "target_type", "source_evidence_set_id", "target_evidence_set_id", "compared_identifiers", "matching_rule", "evidence_ids", "proof_paths", "conversation_ids", "message_ids", "node_ids", "relation_scope", "concurrent_candidate_count", "ambiguities", "contradictions", "confidence", "status", "non_promotion_reason"}
    for index, row in enumerate(rows, start=1):
        missing = sorted(required - set(row))
        if missing:
            errors.append(f"candidate_edges.jsonl:{index} missing fields: {', '.join(missing)}")
            continue
        identifier = row.get("candidate_edge_id")
        if not isinstance(identifier, str) or not identifier.startswith("candidate-edge:"):
            errors.append(f"candidate_edges.jsonl:{index} invalid candidate_edge_id")
        elif identifier in ids:
            errors.append(f"candidate_edges.jsonl:{index} duplicate candidate_edge_id")
        else:
            ids.add(identifier)
        if row.get("source_entity_id") not in entity_ids:
            errors.append(f"candidate_edges.jsonl:{index} unknown source_entity_id")
        if row.get("status") not in {"candidate", "insufficient_evidence", "contradicted", "rejected_by_rule"}:
            errors.append(f"candidate_edges.jsonl:{index} invalid status")
        if any(key in row for key in ("physical_archive_path", "candidate_archive_paths", "copied_pack_path", "resolution_status", "canonical_relation")):
            errors.append(f"candidate_edges.jsonl:{index} contains forbidden resolution or canonical relation field")
        if row.get("status") == "candidate" and not row.get("target_id"):
            errors.append(f"candidate_edges.jsonl:{index} candidate status requires target_id")
        if row.get("status") == "candidate" and (row.get("target_kind") not in {"logical_entity", "structured_record", "technical_event", "conversation_observation"} or not row.get("target_type") or not row.get("target_evidence_set_id")):
            errors.append(f"candidate_edges.jsonl:{index} candidate target typing or evidence set missing")
        if row.get("status") != "candidate":
            errors.append(f"candidate_edges.jsonl:{index} must contain only candidate rows")
    summary_path = evidence / "candidate_edge_summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"candidate_edge_summary.json invalid JSON: {exc}")
        else:
            contract = summary.get("contract") if isinstance(summary, dict) else None
            expected = {"canonical_edges_created": False, "physical_resolution_called": False, "payloads_copied": False, "logical_entities_modified": False, "historical_messages_merged": False}
            if not isinstance(contract, dict) or any(contract.get(key) != value for key, value in expected.items()):
                errors.append("candidate_edge_summary.json invalid non-promotion contract")
    return rows


def validate_optional_context_profile_evidence(
    evidence: Path,
    errors: list[str],
    *,
    conversations: list[dict[str, Any]],
) -> dict[str, int]:
    present = [name for name in CONTEXT_PROFILE_FILES if (evidence / name).exists()]
    counts = {"profile_count": 0, "observation_count": 0, "membership_count": 0}
    if not present:
        return counts
    for name in CONTEXT_PROFILE_FILES:
        if not (evidence / name).exists():
            errors.append(f"context profile evidence incomplete: missing {EVIDENCE_DIR}/{name}")
    paths = {name: evidence / name for name in CONTEXT_PROFILE_FILES}
    if any(not path.exists() for path in paths.values()):
        return counts
    loaded: dict[str, list[dict[str, Any]]] = {}
    for name, path in paths.items():
        if path.suffix == ".json":
            try:
                summary = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(f"{name} invalid JSON: {exc}")
                summary = {}
            loaded[name] = [summary] if isinstance(summary, dict) else []
        else:
            rows, row_errors = read_jsonl(path)
            errors.extend(row_errors)
            loaded[name] = rows

    profiles = loaded["gizmo_context_profiles.jsonl"] + loaded["project_context_profiles.jsonl"]
    observations = loaded["context_profile_observations.jsonl"]
    instructions = loaded["context_instruction_candidates.jsonl"]
    memberships = loaded["context_file_memberships.jsonl"]
    contradictions = loaded["context_profile_contradictions.jsonl"]
    counts.update(profile_count=len(profiles), observation_count=len(observations), membership_count=len(memberships))
    profile_ids: set[str] = set()
    profile_types = {"gizmo": "g-", "project": "g-p-"}
    for index, row in enumerate(profiles, start=1):
        required = {
            "context_profile_id", "context_type", "explicit_context_id", "identity_status", "identity_source_kinds",
            "primary_observation_ids", "historical_observation_ids", "candidate_observation_ids", "primary_conversation_ids",
            "historical_conversation_ids", "explicit_instruction_ids", "candidate_instruction_ids", "file_membership_ids",
            "file_membership_counts", "contradiction_ids", "source_presence_status", "profile_status",
        }
        missing = sorted(required - set(row))
        if missing:
            errors.append(f"context profile:{index} missing fields: {', '.join(missing)}")
            continue
        profile_id = row.get("context_profile_id")
        if not isinstance(profile_id, str) or not profile_id.endswith(":") and "-context-profile:" not in profile_id:
            errors.append(f"context profile:{index} invalid context_profile_id")
        elif profile_id in profile_ids:
            errors.append(f"context profile:{index} duplicate context_profile_id: {profile_id}")
        else:
            profile_ids.add(profile_id)
        context_type = row.get("context_type")
        explicit_id = row.get("explicit_context_id")
        prefix = profile_types.get(context_type)
        if prefix is None or not isinstance(explicit_id, str) or not explicit_id.startswith(prefix):
            errors.append(f"context profile:{index} invalid explicit context identity")
        if context_type == "gizmo" and isinstance(explicit_id, str) and explicit_id.startswith("g-p-"):
            errors.append(f"context profile:{index} project identifier cannot be a gizmo profile")
        if row.get("identity_status") != "explicit_identifier_observed":
            errors.append(f"context profile:{index} invalid identity_status")
        for field in (
            "identity_source_kinds", "primary_observation_ids", "historical_observation_ids", "candidate_observation_ids",
            "primary_conversation_ids", "historical_conversation_ids", "explicit_instruction_ids", "candidate_instruction_ids",
            "file_membership_ids", "contradiction_ids",
        ):
            if not isinstance(row.get(field), list):
                errors.append(f"context profile:{index} invalid {field}")
        if not isinstance(row.get("file_membership_counts"), dict):
            errors.append(f"context profile:{index} invalid file_membership_counts")
        if row.get("profile_status") != "observed_not_configuration_reconstructed":
            errors.append(f"context profile:{index} invalid profile_status")

    observation_ids: set[str] = set()
    conversation_ids = {row.get("conversation_id") for row in conversations if isinstance(row.get("conversation_id"), str)}
    for index, row in enumerate(observations, start=1):
        required = {
            "context_profile_observation_id", "context_profile_id", "context_type", "explicit_context_id", "observation_kind",
            "source_export", "source_record_id", "proof_path", "conversation_id", "message_id", "node_id", "evidence_set_ids",
            "confidence", "status",
        }
        missing = sorted(required - set(row))
        if missing:
            errors.append(f"context_profile_observations.jsonl:{index} missing fields: {', '.join(missing)}")
            continue
        identifier = row.get("context_profile_observation_id")
        if not isinstance(identifier, str) or not identifier.startswith("context-profile-observation:"):
            errors.append(f"context_profile_observations.jsonl:{index} invalid observation ID")
        elif identifier in observation_ids:
            errors.append(f"context_profile_observations.jsonl:{index} duplicate observation ID: {identifier}")
        else:
            observation_ids.add(identifier)
        if row.get("context_profile_id") not in profile_ids:
            errors.append(f"context_profile_observations.jsonl:{index} unknown context_profile_id")
        conversation_id = row.get("conversation_id")
        if conversation_id is not None and conversation_id not in conversation_ids:
            errors.append(f"context_profile_observations.jsonl:{index} unknown conversation_id: {conversation_id}")
        if not isinstance(row.get("evidence_set_ids"), list):
            errors.append(f"context_profile_observations.jsonl:{index} invalid evidence_set_ids")
        if row.get("status") == "candidate_not_promoted" and row.get("observation_kind") != "candidate_context_edge":
            errors.append(f"context_profile_observations.jsonl:{index} candidate status requires candidate context edge")

    valid_instruction_classes = {
        "explicit_context_instruction", "candidate_context_instruction", "conversation_text_only", "manual_placeholder",
    }
    for index, row in enumerate(instructions, start=1):
        if row.get("context_profile_id") not in profile_ids:
            errors.append(f"context_instruction_candidates.jsonl:{index} unknown context_profile_id")
        if row.get("instruction_classification") not in valid_instruction_classes:
            errors.append(f"context_instruction_candidates.jsonl:{index} invalid instruction_classification")
        if not row.get("proof_path"):
            errors.append(f"context_instruction_candidates.jsonl:{index} missing proof_path")
        if row.get("instruction_classification") == "manual_placeholder":
            errors.append(f"context_instruction_candidates.jsonl:{index} manual placeholders must not be emitted as evidence rows")

    valid_relations = {
        "explicit_context_attachment", "explicit_context_library_reference", "observed_in_context_conversation",
        "used_in_context_conversation", "mentioned_or_cited_in_context_conversation", "candidate_context_membership", "unresolved",
    }
    membership_ids: set[str] = set()
    for index, row in enumerate(memberships, start=1):
        identifier = row.get("context_file_membership_id")
        if not isinstance(identifier, str) or not identifier.startswith("context-file-membership:"):
            errors.append(f"context_file_memberships.jsonl:{index} invalid membership ID")
        elif identifier in membership_ids:
            errors.append(f"context_file_memberships.jsonl:{index} duplicate membership ID: {identifier}")
        else:
            membership_ids.add(identifier)
        if row.get("context_profile_id") not in profile_ids:
            errors.append(f"context_file_memberships.jsonl:{index} unknown context_profile_id")
        if row.get("relation_type") not in valid_relations:
            errors.append(f"context_file_memberships.jsonl:{index} invalid relation_type")
        if row.get("relation_type") in {"explicit_context_attachment", "explicit_context_library_reference"}:
            errors.append(f"context_file_memberships.jsonl:{index} direct context membership requires a dedicated explicit source, not emitted by this layer")
        if row.get("relation_type") == "candidate_context_membership" and row.get("status") != "candidate_not_promoted":
            errors.append(f"context_file_memberships.jsonl:{index} candidate membership must remain unpromoted")
        if not isinstance(row.get("conversation_ids"), list) or not isinstance(row.get("message_ids"), list):
            errors.append(f"context_file_memberships.jsonl:{index} invalid conversation/message IDs")
        if not isinstance(row.get("proof_paths"), list) or not row.get("proof_paths"):
            errors.append(f"context_file_memberships.jsonl:{index} missing proof paths")
        for conversation_id in row.get("conversation_ids") or []:
            if conversation_id not in conversation_ids:
                errors.append(f"context_file_memberships.jsonl:{index} unknown conversation_id: {conversation_id}")

    for index, row in enumerate(contradictions, start=1):
        if row.get("context_profile_id") not in profile_ids:
            errors.append(f"context_profile_contradictions.jsonl:{index} unknown context_profile_id")
        if row.get("status") != "unresolved_no_source_winner":
            errors.append(f"context_profile_contradictions.jsonl:{index} invalid contradiction status")

    summary_rows = loaded["context_profile_summary.json"]
    summary = summary_rows[0] if summary_rows else {}
    expected_contract = {
        "raw_zip_reparsed": False,
        "candidate_edges_promoted_automatically": False,
        "physical_resolution_called": False,
        "historical_messages_merged": False,
        "context_file_membership_inferred_from_conversation_only": False,
    }
    if not isinstance(summary, dict) or any(summary.get("contract", {}).get(key) != value for key, value in expected_contract.items()):
        errors.append("context_profile_summary.json invalid downstream-only contract")
    elif summary.get("counts", {}).get("gizmo_context_profiles") != len(loaded["gizmo_context_profiles.jsonl"]):
        errors.append("context_profile_summary.json gizmo profile count mismatch")
    elif summary.get("counts", {}).get("project_context_profiles") != len(loaded["project_context_profiles.jsonl"]):
        errors.append("context_profile_summary.json project profile count mismatch")
    return counts


def validate_optional_context_resource_usage_evidence(
    evidence: Path,
    errors: list[str],
    *,
    conversations: list[dict[str, Any]],
) -> dict[str, int]:
    present = [name for name in CONTEXT_RESOURCE_USAGE_FILES if (evidence / name).exists()]
    counts = {"node_count": 0, "edge_count": 0, "metric_count": 0}
    if not present:
        return counts
    for name in CONTEXT_RESOURCE_USAGE_FILES:
        if not (evidence / name).exists():
            errors.append(f"context resource usage evidence incomplete: missing {EVIDENCE_DIR}/{name}")
    paths = {name: evidence / name for name in CONTEXT_RESOURCE_USAGE_FILES}
    if any(not path.exists() for path in paths.values()):
        return counts
    loaded: dict[str, list[dict[str, Any]]] = {}
    for name, path in paths.items():
        if path.suffix == ".json":
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(f"{name} invalid JSON: {exc}")
                value = {}
            loaded[name] = [value] if isinstance(value, dict) else []
        else:
            rows, row_errors = read_jsonl(path)
            errors.extend(row_errors)
            loaded[name] = rows
    nodes = loaded["context_resource_nodes.jsonl"]
    edges = loaded["context_resource_edges.jsonl"]
    claims = loaded["context_resource_owner_claims.jsonl"]
    metrics = loaded["context_resource_usage_metrics.jsonl"]
    cooccurrences = loaded["context_resource_cooccurrences.jsonl"]
    counts.update(node_count=len(nodes), edge_count=len(edges), metric_count=len(metrics))
    node_ids: set[str] = set()
    for index, row in enumerate(nodes, start=1):
        identifier = row.get("context_resource_node_id")
        if not isinstance(identifier, str) or not identifier.startswith("context-resource-node:"):
            errors.append(f"context_resource_nodes.jsonl:{index} invalid node ID")
        elif identifier in node_ids:
            errors.append(f"context_resource_nodes.jsonl:{index} duplicate node ID: {identifier}")
        else:
            node_ids.add(identifier)
        if row.get("node_kind") not in {
            "context_profile", "context_conversation", "context_message", "observed_resource_reference", "owner_declared_resource",
        }:
            errors.append(f"context_resource_nodes.jsonl:{index} invalid node_kind")
    edge_ids: set[str] = set()
    valid_edge_types = {
        "context_observed_in_conversation", "conversation_contains_message", "message_references_resource",
        "context_owner_declares_knowledge_resource", "owner_declaration_exact_name_corroborated_by_export_reference",
    }
    for index, row in enumerate(edges, start=1):
        identifier = row.get("context_resource_edge_id")
        if not isinstance(identifier, str) or not identifier.startswith("context-resource-edge:"):
            errors.append(f"context_resource_edges.jsonl:{index} invalid edge ID")
        elif identifier in edge_ids:
            errors.append(f"context_resource_edges.jsonl:{index} duplicate edge ID: {identifier}")
        else:
            edge_ids.add(identifier)
        if row.get("edge_type") not in valid_edge_types:
            errors.append(f"context_resource_edges.jsonl:{index} invalid edge_type")
        if row.get("source_node_id") not in node_ids or row.get("target_node_id") not in node_ids:
            errors.append(f"context_resource_edges.jsonl:{index} references unknown node")
        if row.get("edge_type") == "owner_declaration_exact_name_corroborated_by_export_reference" and row.get("matching_rule") != "exact_normalized_filename_equality":
            errors.append(f"context_resource_edges.jsonl:{index} invalid owner/export corroboration rule")
        if any(key in row for key in {"physical_archive_path", "payload_status", "candidate_archive_paths", "canonical_edge_id"}):
            errors.append(f"context_resource_edges.jsonl:{index} contains forbidden resolution or canonical relation field")
    claim_ids: set[str] = set()
    for index, row in enumerate(claims, start=1):
        identifier = row.get("owner_confirmation_id")
        if not isinstance(identifier, str) or not identifier.startswith("owner-confirmation:"):
            errors.append(f"context_resource_owner_claims.jsonl:{index} invalid confirmation ID")
        elif identifier in claim_ids:
            errors.append(f"context_resource_owner_claims.jsonl:{index} duplicate confirmation ID: {identifier}")
        else:
            claim_ids.add(identifier)
        if row.get("source_kind") != "owner_annotation" or row.get("source_export") != "external_owner_confirmation":
            errors.append(f"context_resource_owner_claims.jsonl:{index} invalid external owner provenance")
        if row.get("status") != "owner_confirmed" or not row.get("proof_path"):
            errors.append(f"context_resource_owner_claims.jsonl:{index} invalid owner claim status or proof")
    conversation_ids = {row.get("conversation_id") for row in conversations if isinstance(row.get("conversation_id"), str)}
    metric_ids: set[str] = set()
    allowed_statuses = {
        "observed_context_resource_usage", "confirmed_by_owner_not_seen_in_export", "confirmed_by_owner_and_export_corroborated",
    }
    for index, row in enumerate(metrics, start=1):
        identifier = row.get("context_resource_usage_id")
        if not isinstance(identifier, str) or not identifier.startswith("context-resource-usage:"):
            errors.append(f"context_resource_usage_metrics.jsonl:{index} invalid usage metric ID")
        elif identifier in metric_ids:
            errors.append(f"context_resource_usage_metrics.jsonl:{index} duplicate usage metric ID: {identifier}")
        else:
            metric_ids.add(identifier)
        if row.get("forensic_status") not in allowed_statuses:
            errors.append(f"context_resource_usage_metrics.jsonl:{index} invalid forensic status")
        if row.get("observed_resource_node_id") is not None and row.get("observed_resource_node_id") not in node_ids:
            errors.append(f"context_resource_usage_metrics.jsonl:{index} unknown observed resource node")
        for conversation_id in row.get("conversation_ids") or []:
            if conversation_id not in conversation_ids:
                errors.append(f"context_resource_usage_metrics.jsonl:{index} unknown conversation ID: {conversation_id}")
        if row.get("forensic_status") == "confirmed_by_owner_and_export_corroborated" and not row.get("owner_confirmation_ids"):
            errors.append(f"context_resource_usage_metrics.jsonl:{index} corroborated status requires owner confirmation")
    cooccurrence_ids: set[str] = set()
    for index, row in enumerate(cooccurrences, start=1):
        identifier = row.get("context_resource_cooccurrence_id")
        if not isinstance(identifier, str) or not identifier.startswith("context-resource-cooccurrence:"):
            errors.append(f"context_resource_cooccurrences.jsonl:{index} invalid cooccurrence ID")
        elif identifier in cooccurrence_ids:
            errors.append(f"context_resource_cooccurrences.jsonl:{index} duplicate cooccurrence ID: {identifier}")
        else:
            cooccurrence_ids.add(identifier)
        if row.get("status") != "observed_usage_only":
            errors.append(f"context_resource_cooccurrences.jsonl:{index} invalid cooccurrence status")
        if row.get("left_resource_node_id") not in node_ids or row.get("right_resource_node_id") not in node_ids:
            errors.append(f"context_resource_cooccurrences.jsonl:{index} references unknown resource node")
    summary_rows = loaded["context_resource_usage_summary.json"]
    summary = summary_rows[0] if summary_rows else {}
    expected_contract = {
        "raw_zip_reparsed": False,
        "physical_resolution_called": False,
        "candidate_edges_created": False,
        "canonical_context_memberships_created": False,
        "historical_messages_merged": False,
        "owner_confirmation_is_external_input": True,
        "name_equality_is_not_physical_identity": True,
    }
    if not isinstance(summary, dict) or any(summary.get("contract", {}).get(key) != value for key, value in expected_contract.items()):
        errors.append("context_resource_usage_summary.json invalid observation-only contract")
    elif summary.get("counts", {}).get("context_resource_nodes") != len(nodes):
        errors.append("context_resource_usage_summary.json node count mismatch")
    elif summary.get("counts", {}).get("context_resource_edges") != len(edges):
        errors.append("context_resource_usage_summary.json edge count mismatch")
    elif summary.get("counts", {}).get("context_resource_usage_metrics") != len(metrics):
        errors.append("context_resource_usage_summary.json metric count mismatch")
    return counts


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{line_number} invalid JSON: {exc}")
            continue
        if not isinstance(value, dict):
            errors.append(f"{path.name}:{line_number} row is not an object")
            continue
        rows.append(value)
    return rows, errors


def validate_conversation_frontmatter(pack: Path) -> list[str]:
    errors: list[str] = []
    conversations_dir = pack / CONVERSATIONS_DIR
    if not conversations_dir.exists():
        return errors
    for path in sorted(conversations_dir.glob("**/*.md")):
        try:
            frontmatter = extract_frontmatter(path.read_text(encoding="utf-8"))
        except OSError as exc:
            errors.append(f"{path.relative_to(pack)} cannot be read: {exc}")
            continue
        if frontmatter is None:
            continue
        relative_path = path.relative_to(pack)
        for line in frontmatter.splitlines():
            stripped = line.strip()
            if stripped.startswith("vault_projects:"):
                errors.append(f"{relative_path} frontmatter uses obsolete vault_projects")
            if stripped.startswith("created_at:") and frontmatter_datetime_has_timezone(
                stripped.partition(":")[2].strip()
            ):
                errors.append(
                    f"{relative_path} created_at should be an Obsidian date-time without timezone in frontmatter"
                )
    return errors


def validate_conversation_note_locator(pack: Path, conversations: list[dict[str, Any]]) -> list[str]:
    """Validate the derived, producer-owned conversation-to-note relation."""
    errors: list[str] = []
    manifest_path = pack / EVIDENCE_DIR / "pack_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"conversation note locator cannot read pack manifest: {exc}"]
    navigation = manifest.get("navigation")
    descriptor = navigation.get("conversation_note_locator") if isinstance(navigation, dict) else None
    if not isinstance(descriptor, dict):
        return ["pack manifest missing navigation.conversation_note_locator"]

    state = descriptor.get("state")
    relative_path = descriptor.get("relative_path")
    record_count = descriptor.get("record_count")
    locator_path = pack / CONVERSATION_NOTE_LOCATOR_RELATIVE_PATH
    if state == "not_emitted":
        if relative_path is not None or record_count != 0:
            errors.append("markdown-disabled conversation note locator descriptor is invalid")
        if locator_path.exists() or locator_path.is_symlink():
            errors.append("conversation note locator exists while Markdown is disabled")
        return errors
    if state != "emitted":
        return [f"conversation note locator has invalid state: {state!r}"]
    if relative_path != CONVERSATION_NOTE_LOCATOR_RELATIVE_PATH:
        errors.append("conversation note locator path does not match the V1.4 contract")
    if record_count != len(conversations):
        errors.append("conversation note locator record_count does not match conversations.jsonl")
    if locator_path.is_symlink() or not locator_path.is_file():
        return [*errors, "conversation note locator missing or not a regular file"]

    rows, row_errors = read_jsonl(locator_path)
    errors.extend(f"conversation note locator: {error}" for error in row_errors)
    known_ids = {row.get("conversation_id") for row in conversations if isinstance(row.get("conversation_id"), str)}
    locator_ids: list[str] = []
    locator_paths: list[str] = []
    for index, row in enumerate(rows, start=1):
        if set(row) != LOCATOR_RECORD_FIELDS:
            errors.append(f"conversation note locator:{index} has unknown or missing fields")
            continue
        conversation_id = row.get("conversation_id")
        if not isinstance(conversation_id, str) or not conversation_id:
            errors.append(f"conversation note locator:{index} missing conversation_id")
        else:
            locator_ids.append(conversation_id)
            if conversation_id not in known_ids:
                errors.append(f"conversation note locator:{index} references unknown conversation_id: {conversation_id}")
        relative_note_path = row.get("relative_note_path")
        path_error = locator_path_error(relative_note_path)
        if path_error:
            errors.append(f"conversation note locator:{index} {path_error}")
            continue
        assert isinstance(relative_note_path, str)
        locator_paths.append(relative_note_path)
        target = pack.joinpath(*PurePosixPath(relative_note_path).parts)
        if target.is_symlink() or not target.is_file():
            errors.append(f"conversation note locator:{index} target missing or not a regular file: {relative_note_path}")
            continue
        target_conversation_id, frontmatter_error = generated_note_conversation_id(target)
        if frontmatter_error:
            errors.append(f"conversation note locator:{index} target {frontmatter_error}")
        elif target_conversation_id != conversation_id:
            errors.append(f"conversation note locator:{index} target pairwise binding does not match locator conversation_id")

    if len(locator_ids) != len(set(locator_ids)):
        errors.append("conversation note locator conversation_id values are not unique")
    if len(locator_paths) != len(set(locator_paths)):
        errors.append("conversation note locator relative_note_path values are not unique")
    if set(locator_ids) != known_ids:
        errors.append("conversation note locator does not exactly match conversations.jsonl")

    notes_root = pack / "10_Conversations"
    emitted_note_paths = {
        path.relative_to(pack).as_posix()
        for path in notes_root.rglob("*.md")
        if path.is_file() and not path.is_symlink()
    } if notes_root.is_dir() else set()
    if set(locator_paths) != emitted_note_paths:
        errors.append("conversation note locator does not exactly match emitted conversation notes")
    return errors


def generated_note_conversation_id(path: Path) -> tuple[str | None, str | None]:
    """Read only the exact generated frontmatter scalar needed for locator binding."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, "frontmatter cannot be read"
    if not lines or lines[0] != "---":
        return None, "frontmatter is absent"
    try:
        closing = next(index for index, line in enumerate(lines[1:], start=1) if line == "---")
    except StopIteration:
        return None, "frontmatter is unterminated"
    values = [
        line.partition(":")[2]
        for line in lines[1:closing]
        if line.startswith("conversation_id:")
    ]
    if not values:
        return None, "frontmatter conversation_id is absent"
    if len(values) != 1:
        return None, "frontmatter conversation_id is duplicate or ambiguous"
    raw_value = values[0]
    if not raw_value.startswith(" "):
        return None, "frontmatter conversation_id is malformed"
    value = decode_generated_yaml_scalar(raw_value[1:])
    if value is None:
        return None, "frontmatter conversation_id is malformed"
    if not value or any(character.isspace() or ord(character) < 32 for character in value):
        return None, "frontmatter conversation_id is malformed"
    return value, None


def decode_generated_yaml_scalar(value: str) -> str | None:
    """Decode the closed scalar form emitted by ``markdown.yaml_scalar``.

    This intentionally recognizes only the writer's string grammar: unquoted
    strings and double-quoted strings, with ``\\\\`` and ``\\\"`` escapes.  It
    is not a general YAML parser and rejects syntax that the writer cannot
    produce.
    """
    quoted = value.startswith('"')
    if quoted:
        if len(value) < 2 or not value.endswith('"'):
            return None
        body = value[1:-1]
    else:
        body = value

    decoded: list[str] = []
    index = 0
    while index < len(body):
        character = body[index]
        if character == '"':
            return None
        if character != "\\":
            decoded.append(character)
            index += 1
            continue
        index += 1
        if index >= len(body) or body[index] not in {'\\', '"'}:
            return None
        decoded.append(body[index])
        index += 1
    return "".join(decoded)


def extract_frontmatter(text: str) -> str | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[1:index])
    return None


def looks_like_space_project_link(value: Any) -> bool:
    return isinstance(value, str) and ("[[Project - g-p-" in value or "[[Project - " in value)


def looks_like_project_gpt_link(value: Any) -> bool:
    return isinstance(value, str) and "[[GPT - g-p-" in value


def frontmatter_datetime_has_timezone(value: str) -> bool:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()
    return bool(re.search(r"(?:Z|[+-]\d{2}:\d{2})\s*$", value))


def result(errors: list[str], warnings: list[str], counts: dict[str, int]) -> dict[str, Any]:
    return {"errors": errors, "warnings": warnings, "counts": counts}


def first_present(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row.get(key)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
