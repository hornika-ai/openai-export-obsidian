from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .archive import ExportArchive
from .assets import (
    build_asset_records,
    build_library_origin_asset_records,
    coerce_int,
    extract_id,
    library_has_knowledge_context,
    load_asset_filename_map,
    load_library_files,
    load_shared_conversations,
)
from .conversations import parse_conversation
from .inventory import ExportInventory, InventoryEntry
from .markdown import (
    conditional_models_seen,
    render_compact_conversation_markdown,
    render_context_markdown,
    render_conversation_markdown,
    render_readable_conversation_markdown,
)
from .models import ArchiveMember, AssetRecord, ConversationRecord, is_unresolved_asset_status
from .reference_extraction import ReferenceExtractionResult, ReferenceExtractor
from .physical_resolution import LibraryResolutionIndex, PhysicalResolutionResult, PhysicalResolver
from .runtime_artifacts import RuntimeArtifactLinker, RuntimeArtifactLinkingResult, RuntimeArtifactRecord
from .embedded_historical_exports import (
    EmbeddedHistoricalExportIngestor,
    EmbeddedHistoricalExportResult,
    HistoricalConversationComparison,
    HistoricalSignalRecord,
)
from .technical_events import (
    TechnicalEventExtractor,
    TechnicalEventExtractionResult,
    TechnicalEventRecord,
    emit_technical_event_evidence,
)
from .logical_entities import (
    LogicalEntityConstructionResult,
    LogicalEntityConstructor,
    emit_logical_entity_evidence,
)
from .materialization import MaterializationHint, MaterializedFile, mark_collision, resolve_materialized_file
from .candidate_edges import CandidateEdgeConstructor, CandidateEdgeResult, emit_candidate_edge_evidence
from .context_profiles import ContextProfileConstructor, ContextProfileResult, emit_context_profile_evidence
from .context_resource_usage import (
    ContextResourceUsageConstructor,
    ContextResourceUsageResult,
    emit_context_resource_usage_evidence,
)
from .asset_resolution_migration import AssetResolutionMigrationResult, AssetResolutionMigrator
from .payload_classification import (
    ContentEquivalenceIndex,
    PayloadDispositionClassifier,
    PayloadDispositionResult,
)
from .payload_candidates import (
    UnverifiedPayloadCandidateAnalyzer,
    emit_unverified_payload_candidate_evidence,
)
from .conversation_note_locator import (
    locator_manifest_entry,
    write_conversation_note_locators,
)
from .ds_store_hygiene import is_ds_store_name, remove_residual_ds_store_from_new_output
from .textdocs import build_textdoc_records, materialize_textdoc_records
from .citation_links import build_citation_links
from .utils import (
    append_jsonl,
    basename,
    compact_raw,
    date_prefix,
    extension_from_name,
    safe_filename,
    safe_filename_preserving_suffix,
    short_identifier,
    slugify,
    normalize_file_id,
    unique_filename,
    unique_note_name,
    year_month,
    write_json,
)

HOME_FILE = "00_Home.md"
CONVERSATIONS_DIR = "10_Conversations"
FILES_DIR = "20_Files"
CONTEXTS_DIR = "30_Contexts"
VIEWS_DIR = "40_Views"
EVIDENCE_DIR = "90_Evidence"
LOGS_DIR = "_logs"
SCHEMA_VERSION = "openai-obsidian-pack-v1.4"
READABLE_MARKDOWN_PROFILES = frozenset({"readable", "readable_compact"})


@dataclass(frozen=True)
class ReadablePayloadCopy:
    """The one readable-pack copy retained for a content-identical payload group."""

    physical_archive_path: str
    copied_path: str
    content_sha256: str


def run_parse(
    input_path: Path | str,
    output_path: Path | str,
    *,
    copy_assets: bool = False,
    copy_unlinked_assets: bool = False,
    emit_json: bool = True,
    emit_markdown: bool = True,
    emit_bases: bool = False,
    emit_dataview: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
    conversation_ids: set[str] | None = None,
    months: set[str] | None = None,
    max_conversations: int | None = None,
    markdown_profile: str = "readable_compact",
    update_existing_pack: bool = False,
    manifest_generated_at: str | None = None,
) -> dict[str, Any]:
    if markdown_profile not in {*READABLE_MARKDOWN_PROFILES, "forensic"}:
        raise ValueError("markdown_profile must be 'readable', 'readable_compact', or 'forensic'")

    input_path = Path(input_path)
    output_path = Path(output_path)
    manual_sections = capture_manual_sections(output_path) if update_existing_pack and markdown_profile in READABLE_MARKDOWN_PROFILES else {}
    archive = ExportArchive(input_path)
    inventory = archive.inventory
    logger = configure_logging(output_path, dry_run=dry_run, verbose=verbose, markdown_profile=markdown_profile)
    logger.info("Starting parse input=%s output=%s dry_run=%s profile=%s", input_path, output_path, dry_run, markdown_profile)

    shard_members = archive.members_from_inventory(lambda entry: entry.family == "conversation_shard")
    asset_filename_map = load_asset_filename_map(archive)
    library_by_file_id = load_library_files(archive)
    shared_by_conversation = load_shared_conversations(archive)
    # Finder metadata is never a readable pack payload.  Keep the source
    # inventory forensic, but exclude only this exact basename from every
    # downstream copy/materialization traversal.
    physical_members = [
        member for member in archive.physical_asset_members() if not is_ds_store_name(member.basename)
    ]

    conversations: list[ConversationRecord] = []
    raw_by_conversation: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for shard in sorted(shard_members, key=lambda m: m.archive_path):
        try:
            data = json.loads(archive.read_text(shard))
        except Exception as exc:
            warnings.append(f"failed_to_read_shard:{shard.archive_path}:{exc}")
            logger.exception("Failed to read shard %s", shard.archive_path)
            continue
        rows = data if isinstance(data, list) else list(data.values()) if isinstance(data, dict) else []
        for index, raw in enumerate(rows):
            if not isinstance(raw, dict):
                warnings.append(f"non_object_conversation:{shard.archive_path}:{index}")
                continue
            try:
                conversation = parse_conversation(raw, shard, shared_by_conversation)
                conversations.append(conversation)
                raw_by_conversation[conversation.conversation_id] = raw
            except Exception as exc:
                warnings.append(f"failed_to_parse_conversation:{shard.archive_path}:{index}:{exc}")
                logger.exception("Failed to parse conversation shard=%s index=%s", shard.archive_path, index)

    conversations.sort(key=lambda c: (c.create_time or "", c.conversation_id))
    note_ref_conversations = list(conversations)
    parsed_conversation_count = len(conversations)
    if conversation_ids:
        conversations = [conversation for conversation in conversations if conversation.conversation_id in conversation_ids]
    if months:
        conversations = [conversation for conversation in conversations if year_month(conversation.create_time)[2] in months]
    if max_conversations is not None:
        conversations = conversations[: max(0, max_conversations)]
    filtered_output = bool(conversation_ids or months or max_conversations is not None)
    pack_manifest = build_pack_manifest(
        source_input=input_path,
        markdown_profile=markdown_profile,
        copy_assets=copy_assets,
        copy_unlinked_assets=copy_unlinked_assets,
        emit_json=emit_json,
        emit_markdown=emit_markdown,
        emit_bases=emit_bases,
        emit_dataview=emit_dataview,
        conversation_ids=conversation_ids,
        months=months,
        max_conversations=max_conversations,
        update_existing_pack=update_existing_pack,
        parsed_conversation_count=parsed_conversation_count,
        emitted_conversation_count=len(conversations),
        generated_at=manifest_generated_at,
    )

    assets_by_conversation: dict[str, list[AssetRecord]] = {}
    all_assets: list[AssetRecord] = []
    asset_resolution_comparisons = []
    reference_extractor = ReferenceExtractor(inventory)
    reference_records = []
    selected_reference_conversation_ids = {conversation.conversation_id for conversation in conversations}
    selected_reference_gpt_ids = {conversation.custom_gpt_id for conversation in conversations if conversation.custom_gpt_id}
    for conversation in conversations:
        raw = raw_by_conversation[conversation.conversation_id]
        reference_records.extend(reference_extractor.extract_conversation(conversation, raw))
    reference_records.extend(
        reference_extractor.extract_library_metadata(
            library_by_file_id,
            selected_conversation_ids=selected_reference_conversation_ids if filtered_output else None,
            selected_gpt_ids=selected_reference_gpt_ids if filtered_output else None,
        )
    )
    reference_result = ReferenceExtractor.result(reference_records)
    content_equivalence = ContentEquivalenceIndex.build(archive, inventory)
    physical_resolution_result = PhysicalResolver(
        inventory,
        content_equivalence=content_equivalence,
    ).resolve_all(reference_result.records)
    library_resolution_index = LibraryResolutionIndex(physical_resolution_result)
    inventory_by_archive_path = {entry.archive_path: entry for entry in inventory.entries}
    materialization_hints_by_archive_path = build_materialization_hints(
        reference_result,
        physical_resolution_result,
    )
    runtime_artifact_result = RuntimeArtifactLinker(archive, inventory).link(
        conversation.conversation_id for conversation in conversations
    )
    payload_disposition_result = PayloadDispositionClassifier(content_equivalence).classify(
        physical_resolution_result,
        direct_referenced_archive_paths=(row.physical_archive_path for row in runtime_artifact_result.records),
    )
    runtime_artifacts_by_conversation = runtime_artifact_result.by_conversation()
    historical_export_result = EmbeddedHistoricalExportIngestor(archive, inventory).ingest(conversations)
    historical_comparisons_by_conversation = historical_export_result.comparisons_by_conversation()
    historical_signals_by_conversation = historical_export_result.signals_by_conversation()
    technical_event_result = TechnicalEventExtractor(archive, inventory).extract(
        conversations,
        raw_by_conversation,
        runtime_artifacts=runtime_artifact_result.records,
    )
    logical_entity_result = LogicalEntityConstructor().construct(
        row.to_dict() for row in technical_event_result.records
    )
    technical_events_by_conversation = technical_event_result.by_conversation()
    asset_resolution_migrator = AssetResolutionMigrator(
        physical_resolution_result,
        copyable_archive_paths=(member.archive_path for member in physical_members),
    )
    for conversation in conversations:
        raw = raw_by_conversation[conversation.conversation_id]
        assets = build_asset_records(conversation, raw, asset_filename_map, library_by_file_id)
        existing_file_ids = {asset.raw_file_id for asset in assets if asset.raw_file_id}
        assets.extend(
            asset
            for asset in build_library_origin_asset_records(conversation, library_by_file_id)
            if asset.raw_file_id not in existing_file_ids
        )
        asset_resolution_comparisons.extend(asset_resolution_migrator.apply(assets).comparisons)
        assets_by_conversation[conversation.conversation_id] = assets
        all_assets.extend(assets)
    asset_resolution_migration_result = AssetResolutionMigrationResult(asset_resolution_comparisons)

    if markdown_profile == "forensic":
        audit = emit_forensic_outputs(
            archive=archive,
            output_path=output_path,
            conversations=conversations,
            assets_by_conversation=assets_by_conversation,
            all_assets=all_assets,
            runtime_artifacts_by_conversation=runtime_artifacts_by_conversation,
            runtime_artifact_result=runtime_artifact_result,
            historical_export_result=historical_export_result,
            historical_comparisons_by_conversation=historical_comparisons_by_conversation,
            historical_signals_by_conversation=historical_signals_by_conversation,
            technical_event_result=technical_event_result,
            technical_events_by_conversation=technical_events_by_conversation,
            physical_members=physical_members,
            shard_members=shard_members,
            warnings=warnings,
            copy_assets=copy_assets,
            payload_disposition_result=payload_disposition_result,
            emit_json=emit_json,
            emit_markdown=emit_markdown,
            dry_run=dry_run,
        )
    else:
        audit = emit_readable_outputs(
            archive=archive,
            output_path=output_path,
            conversations=conversations,
            assets_by_conversation=assets_by_conversation,
            all_assets=all_assets,
            runtime_artifacts_by_conversation=runtime_artifacts_by_conversation,
            runtime_artifact_result=runtime_artifact_result,
            historical_export_result=historical_export_result,
            historical_comparisons_by_conversation=historical_comparisons_by_conversation,
            historical_signals_by_conversation=historical_signals_by_conversation,
            technical_event_result=technical_event_result,
            technical_events_by_conversation=technical_events_by_conversation,
            physical_members=physical_members,
            library_resolution_index=library_resolution_index,
            library_by_file_id=library_by_file_id,
            asset_filename_map=asset_filename_map,
            inventory_by_archive_path=inventory_by_archive_path,
            materialization_hints_by_archive_path=materialization_hints_by_archive_path,
            shard_members=shard_members,
            warnings=warnings,
            copy_assets=copy_assets,
            copy_unlinked_assets=copy_unlinked_assets,
            emit_json=emit_json,
            emit_markdown=emit_markdown,
            emit_bases=emit_bases,
            emit_dataview=emit_dataview,
            filtered_output=filtered_output,
            note_ref_conversations=note_ref_conversations,
            pack_manifest=pack_manifest,
            markdown_profile=markdown_profile,
            dry_run=dry_run,
            payload_disposition_result=payload_disposition_result,
            reference_result=reference_result,
            physical_resolution_result=physical_resolution_result,
            asset_resolution_comparisons=[comparison.to_dict() for comparison in asset_resolution_migration_result.comparisons],
        )

    if manual_sections and not dry_run:
        restore_manual_sections(output_path, manual_sections)

    audit["inventory"] = inventory.summary_dict()
    audit["reference_extraction"] = reference_result.summary_dict()
    audit["physical_resolution"] = physical_resolution_result.summary_dict()
    audit["payload_classification"] = {
        **content_equivalence.summary_dict(),
        **payload_disposition_result.summary_dict(),
    }
    audit["asset_resolution_migration"] = asset_resolution_migration_result.summary_dict()
    audit["runtime_artifacts"] = runtime_artifact_result.summary_dict()
    audit["embedded_historical_exports"] = historical_export_result.summary_dict()
    audit["technical_event_extraction"] = technical_event_result.summary_dict()
    audit["logical_entity_construction"] = logical_entity_result.summary_dict()
    candidate_edge_result: CandidateEdgeResult | None = None
    context_profile_result: ContextProfileResult | None = None
    context_resource_usage_result: ContextResourceUsageResult | None = None
    if emit_json and not dry_run:
        evidence_dir = output_path if markdown_profile == "forensic" else output_path / EVIDENCE_DIR
        emit_inventory_evidence(evidence_dir, inventory)
        emit_reference_evidence(evidence_dir, reference_result)
        emit_physical_resolution_evidence(evidence_dir, physical_resolution_result)
        emit_payload_classification_evidence(evidence_dir, payload_disposition_result)
        if "payload_materialization" in audit:
            write_json(evidence_dir / "payload_materialization_summary.json", audit["payload_materialization"])
        emit_asset_resolution_migration_evidence(evidence_dir, asset_resolution_migration_result)
        emit_runtime_artifact_evidence(evidence_dir, runtime_artifact_result)
        emit_embedded_historical_export_evidence(evidence_dir, historical_export_result)
        emit_technical_event_evidence(evidence_dir, technical_event_result)
        emit_logical_entity_evidence(evidence_dir, logical_entity_result)
        if markdown_profile in READABLE_MARKDOWN_PROFILES:
            candidate_edge_result = CandidateEdgeConstructor().construct(evidence_dir)
            emit_candidate_edge_evidence(evidence_dir, candidate_edge_result)
            context_profile_result = ContextProfileConstructor().construct(evidence_dir)
            emit_context_profile_evidence(evidence_dir, context_profile_result)
            context_resource_usage_result = ContextResourceUsageConstructor().construct(evidence_dir)
            emit_context_resource_usage_evidence(evidence_dir, context_resource_usage_result)
            audit["candidate_edge_construction"] = candidate_edge_result.summary_dict()
            audit["context_profile_construction"] = context_profile_result.summary_dict()
            audit["context_resource_usage"] = context_resource_usage_result.summary_dict()
        write_json(evidence_dir / "parse_audit.json", audit)

    # A newly generated output can receive Finder metadata while it is being
    # written.  This bounded metadata-only sweep never touches an explicitly
    # updated existing pack; post-generation contamination of an existing pack
    # is handled by the separate, explicit quarantine command.
    if not dry_run and not update_existing_pack:
        remove_residual_ds_store_from_new_output(output_path)

    logger.info("Completed parse conversations=%s assets=%s", len(conversations), len(all_assets))
    return {
        "conversation_count": len(conversations),
        "parsed_conversation_count": parsed_conversation_count,
        "file_reference_count": len(all_assets),
        "asset_reference_count": len(all_assets),
        "output_path": str(output_path),
        "audit": audit,
    }


def build_pack_manifest(
    *,
    source_input: Path,
    markdown_profile: str,
    copy_assets: bool,
    copy_unlinked_assets: bool,
    emit_json: bool,
    emit_markdown: bool,
    emit_bases: bool,
    emit_dataview: bool,
    conversation_ids: set[str] | None,
    months: set[str] | None,
    max_conversations: int | None,
    update_existing_pack: bool,
    parsed_conversation_count: int,
    emitted_conversation_count: int,
    generated_at: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_input": str(source_input),
        "markdown_profile": markdown_profile,
        "copy_assets": copy_assets,
        "copy_unlinked_assets": copy_unlinked_assets,
        "emit_json": emit_json,
        "emit_markdown": emit_markdown,
        "emit_bases": emit_bases,
        "emit_dataview": emit_dataview,
        "update_existing_pack": update_existing_pack,
        "navigation": {
            "conversation_note_locator": locator_manifest_entry(
                emit_markdown=emit_markdown,
                conversation_count=emitted_conversation_count,
            ),
        },
        "filters": {
            "conversation_ids": sorted(conversation_ids or []),
            "months": sorted(months or []),
            "max_conversations": max_conversations,
        },
        "counts": {
            "parsed_conversations": parsed_conversation_count,
            "emitted_conversations": emitted_conversation_count,
        },
    }


MANUAL_SECTION_SPECS = (
    (CONVERSATIONS_DIR, "conversation_id", "## Manual Review", "<!-- BEGIN GENERATED OPENAI CONVERSATION -->"),
    (CONTEXTS_DIR, "context_id", "## Manual Context", "<!-- BEGIN GENERATED OPENAI CONTEXT -->"),
)


def capture_manual_sections(pack: Path) -> dict[tuple[str, str], str]:
    """Read only contract-defined manual blocks before an explicit pack update."""
    captured: dict[tuple[str, str], str] = {}
    for folder, identifier_key, heading, marker in MANUAL_SECTION_SPECS:
        root = pack / folder
        if not root.exists():
            continue
        for path in sorted(root.glob("**/*.md")):
            text = path.read_text(encoding="utf-8")
            identifier = frontmatter_scalar(text, identifier_key)
            start, end = text.find(heading), text.find(marker)
            if not identifier or start < 0 or end < start:
                continue
            captured[(identifier_key, identifier)] = text[start:end]
    return captured


def restore_manual_sections(pack: Path, captured: dict[tuple[str, str], str]) -> None:
    """Restore manual blocks by stable source ID; generated blocks remain fresh."""
    for folder, identifier_key, heading, marker in MANUAL_SECTION_SPECS:
        root = pack / folder
        if not root.exists():
            continue
        for path in sorted(root.glob("**/*.md")):
            text = path.read_text(encoding="utf-8")
            identifier = frontmatter_scalar(text, identifier_key)
            manual = captured.get((identifier_key, identifier or ""))
            start, end = text.find(heading), text.find(marker)
            if manual is None or start < 0 or end < start:
                continue
            path.write_text(text[:start] + manual.rstrip() + "\n\n" + text[end:], encoding="utf-8")


def frontmatter_scalar(text: str, key: str) -> str | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    match = re.search(rf"^{re.escape(key)}:\s*(.+?)\s*$", text[4:end], re.MULTILINE)
    return match.group(1).strip().strip('"\'') if match else None


def emit_forensic_outputs(
    *,
    archive: ExportArchive,
    output_path: Path,
    conversations: list[ConversationRecord],
    assets_by_conversation: dict[str, list[AssetRecord]],
    all_assets: list[AssetRecord],
    runtime_artifacts_by_conversation: dict[str, list[RuntimeArtifactRecord]],
    runtime_artifact_result: RuntimeArtifactLinkingResult,
    historical_export_result: EmbeddedHistoricalExportResult,
    historical_comparisons_by_conversation: dict[str, list[HistoricalConversationComparison]],
    historical_signals_by_conversation: dict[str, list[HistoricalSignalRecord]],
    technical_event_result: TechnicalEventExtractionResult,
    technical_events_by_conversation: dict[str, list[TechnicalEventRecord]],
    physical_members: list[ArchiveMember],
    shard_members: list[ArchiveMember],
    warnings: list[str],
    copy_assets: bool,
    payload_disposition_result: PayloadDispositionResult,
    emit_json: bool,
    emit_markdown: bool,
    dry_run: bool,
) -> dict[str, Any]:
    if not dry_run:
        output_path.mkdir(parents=True, exist_ok=True)
        (output_path / "conversations").mkdir(parents=True, exist_ok=True)
        (output_path / "logs").mkdir(parents=True, exist_ok=True)

    used_conversation_dirs: set[str] = set()
    used_physical_paths: set[str] = {
        artifact.physical_archive_path
        for artifact in runtime_artifact_result.records
    }
    conversation_index_rows: list[dict[str, Any]] = []

    for conversation in conversations:
        conv_assets = assets_by_conversation.get(conversation.conversation_id, [])
        conv_dir_name = unique_filename(
            f"{date_prefix(conversation.create_time)}__{slugify(conversation.title)}__{conversation.conversation_id[:8]}",
            used_conversation_dirs,
        )
        conv_dir = output_path / "conversations" / conv_dir_name
        assets_dir = conv_dir / "assets"
        copied_names: set[str] = set()
        if not dry_run:
            (conv_dir / "evidence").mkdir(parents=True, exist_ok=True)
            assets_dir.mkdir(parents=True, exist_ok=True)

        for asset in conv_assets:
            if asset.asset_status != "found" or not asset.physical_archive_path:
                continue
            physical = first_physical_for_path(physical_members, asset.physical_archive_path)
            if physical:
                used_physical_paths.add(physical.archive_path)
            if copy_assets and physical:
                copied_name = unique_filename(asset.reconstructed_filename or asset.raw_dat_filename or "asset.dat", copied_names)
                asset.copied_filename = copied_name
                asset.copied_relative_path = str(Path("conversations") / conv_dir_name / "assets" / copied_name)
                if not dry_run:
                    archive.copy_member(physical, assets_dir / copied_name)

        resolved = sum(1 for asset in conv_assets if asset.asset_status == "found")
        missing = sum(1 for asset in conv_assets if is_unresolved_asset_status(asset.asset_status))
        conversation_index_rows.append(conversation.index_dict(len(conv_assets), resolved, missing))

        if not dry_run and emit_json:
            write_json(
                conv_dir / "conversation.json",
                conversation.to_dict()
                | {
                    "assets": [asset.to_dict() for asset in conv_assets],
                    "runtime_artifacts": [artifact.to_dict() for artifact in runtime_artifacts_by_conversation.get(conversation.conversation_id, [])],
                    "historical_export_comparisons": [row.to_dict() for row in historical_comparisons_by_conversation.get(conversation.conversation_id, [])],
                    "historical_export_signals": [row.to_dict() for row in historical_signals_by_conversation.get(conversation.conversation_id, [])],
                    "technical_events": [row.to_dict() for row in technical_events_by_conversation.get(conversation.conversation_id, [])],
                },
            )
            write_json(conv_dir / "evidence" / "asset_links.json", [asset.to_dict() for asset in conv_assets])
            write_json(
                conv_dir / "evidence" / "runtime_artifacts.json",
                [artifact.to_dict() for artifact in runtime_artifacts_by_conversation.get(conversation.conversation_id, [])],
            )
            write_json(
                conv_dir / "evidence" / "historical_export_evidence.json",
                {
                    "comparisons": [row.to_dict() for row in historical_comparisons_by_conversation.get(conversation.conversation_id, [])],
                    "signals": [row.to_dict() for row in historical_signals_by_conversation.get(conversation.conversation_id, [])],
                },
            )
            write_json(
                conv_dir / "evidence" / "technical_events.json",
                [row.to_dict() for row in technical_events_by_conversation.get(conversation.conversation_id, [])],
            )
            write_json(conv_dir / "evidence" / "message_index.json", conversation.node_index)
            write_json(conv_dir / "evidence" / "raw_metadata.json", conversation.raw_metadata)
        if not dry_run and emit_markdown:
            (conv_dir / "conversation.md").write_text(render_conversation_markdown(conversation, conv_assets), encoding="utf-8")

    orphan_assets = [
        {
            "basename": member.basename,
            "archive_path": member.archive_path,
            "size": member.size,
            **payload_disposition_fields(payload_disposition_result, member.archive_path),
        }
        for member in physical_members
        if member.archive_path not in used_physical_paths
        and payload_disposition_result.is_unlinked_candidate(member.archive_path)
    ]
    audit = build_audit(
        shard_members=shard_members,
        conversations=conversations,
        assets=all_assets,
        physical_members=physical_members,
        orphan_assets=orphan_assets,
        warnings=warnings,
    )
    if not dry_run and emit_json:
        append_jsonl(output_path / "conversations_index.jsonl", conversation_index_rows)
        append_jsonl(output_path / "assets_index.jsonl", [asset.to_dict() for asset in all_assets])
        append_jsonl(output_path / "orphan_assets.jsonl", orphan_assets)
        write_json(output_path / "parse_audit.json", audit)
    return audit


def emit_readable_outputs(
    *,
    archive: ExportArchive,
    output_path: Path,
    conversations: list[ConversationRecord],
    assets_by_conversation: dict[str, list[AssetRecord]],
    all_assets: list[AssetRecord],
    runtime_artifacts_by_conversation: dict[str, list[RuntimeArtifactRecord]],
    runtime_artifact_result: RuntimeArtifactLinkingResult,
    historical_export_result: EmbeddedHistoricalExportResult,
    historical_comparisons_by_conversation: dict[str, list[HistoricalConversationComparison]],
    historical_signals_by_conversation: dict[str, list[HistoricalSignalRecord]],
    technical_event_result: TechnicalEventExtractionResult,
    technical_events_by_conversation: dict[str, list[TechnicalEventRecord]],
    physical_members: list[ArchiveMember],
    library_resolution_index: LibraryResolutionIndex,
    library_by_file_id: dict[str, dict[str, Any]],
    asset_filename_map: dict[str, str],
    inventory_by_archive_path: dict[str, InventoryEntry],
    materialization_hints_by_archive_path: dict[str, list[MaterializationHint]],
    shard_members: list[ArchiveMember],
    warnings: list[str],
    copy_assets: bool,
    copy_unlinked_assets: bool,
    emit_json: bool,
    emit_markdown: bool,
    emit_bases: bool,
    emit_dataview: bool,
    filtered_output: bool,
    note_ref_conversations: list[ConversationRecord] | None,
    pack_manifest: dict[str, Any],
    markdown_profile: str,
    dry_run: bool,
    payload_disposition_result: PayloadDispositionResult,
    reference_result: ReferenceExtractionResult,
    physical_resolution_result: PhysicalResolutionResult,
    asset_resolution_comparisons: list[dict[str, Any]],
) -> dict[str, Any]:
    if not dry_run:
        for folder in (CONVERSATIONS_DIR, FILES_DIR, CONTEXTS_DIR, VIEWS_DIR, EVIDENCE_DIR, LOGS_DIR):
            (output_path / folder).mkdir(parents=True, exist_ok=True)

    used_file_keys: set[str] = set()
    used_physical_paths: set[str] = set()
    physical_members_by_archive_path = {member.archive_path: member for member in physical_members}
    asset_presentation_by_archive_path = {
        row.physical_archive_path: {
            "content_equivalence_group_id": row.content_equivalence_group_id,
            "canonical_archive_path": row.canonical_archive_path,
            "payload_disposition_status": row.disposition_status,
        }
        for row in payload_disposition_result.records
    }
    file_manifest: list[dict[str, Any]] = []
    readable_payloads_by_hash: dict[str, ReadablePayloadCopy] = {}
    conversation_rows: list[dict[str, Any]] = []
    message_rows: list[dict[str, Any]] = []
    context_links: list[dict[str, Any]] = []
    message_sources: list[dict[str, Any]] = []
    tool_events: list[dict[str, Any]] = []
    textdoc_rows: list[dict[str, Any]] = []
    citation_links: list[dict[str, Any]] = []
    note_render_jobs: list[dict[str, Any]] = []
    note_locator_records: list[dict[str, str]] = []
    used_textdoc_filenames_by_folder: dict[str, set[str]] = defaultdict(set)
    note_refs = build_conversation_note_refs(note_ref_conversations or conversations)
    note_renderer = render_compact_conversation_markdown if markdown_profile == "readable_compact" else render_readable_conversation_markdown

    gpt_contexts: dict[str, dict[str, Any]] = {}
    project_contexts: dict[str, dict[str, Any]] = {}
    selected_conversation_ids = {conversation.conversation_id for conversation in conversations}
    selected_gpt_ids = {conversation.custom_gpt_id for conversation in conversations if conversation.custom_gpt_id}

    knowledge_contexts: dict[str, dict[str, Any]] = build_knowledge_contexts(
        library_by_file_id,
        selected_conversation_ids=selected_conversation_ids,
        selected_gpt_ids=selected_gpt_ids,
        include_all=not filtered_output,
    )
    observed_contexts: dict[str, dict[str, Any]] = {}

    mark_runtime_artifacts(runtime_artifact_result.records, used_physical_paths)
    if copy_assets:
        copy_runtime_artifacts(
            archive=archive,
            output_path=output_path,
            conversations_by_id={conversation.conversation_id: conversation for conversation in conversations},
            artifacts=runtime_artifact_result.records,
            physical_members=physical_members,
            physical_members_by_archive_path=physical_members_by_archive_path,
            asset_filename_map=asset_filename_map,
            inventory_by_archive_path=inventory_by_archive_path,
            materialization_hints_by_archive_path=materialization_hints_by_archive_path,
            used_file_keys=used_file_keys,
            used_physical_paths=used_physical_paths,
            file_manifest=file_manifest,
            readable_payloads_by_hash=readable_payloads_by_hash,
            payload_disposition_result=payload_disposition_result,
            dry_run=dry_run,
        )

    for conversation in conversations:
        conv_assets = assets_by_conversation.get(conversation.conversation_id, [])
        conv_runtime_artifacts = runtime_artifacts_by_conversation.get(conversation.conversation_id, [])
        conv_historical_comparisons = historical_comparisons_by_conversation.get(conversation.conversation_id, [])
        conv_historical_signals = historical_signals_by_conversation.get(conversation.conversation_id, [])
        conv_technical_events = technical_events_by_conversation.get(conversation.conversation_id, [])
        mark_referenced_conversation_assets(conv_assets, used_physical_paths)
        if copy_assets:
            copy_conversation_assets(
                archive=archive,
                output_path=output_path,
                conversation=conversation,
                assets=conv_assets,
                physical_members=physical_members,
                physical_members_by_archive_path=physical_members_by_archive_path,
                asset_filename_map=asset_filename_map,
                inventory_by_archive_path=inventory_by_archive_path,
                materialization_hints_by_archive_path=materialization_hints_by_archive_path,
                used_file_keys=used_file_keys,
                used_physical_paths=used_physical_paths,
                file_manifest=file_manifest,
                readable_payloads_by_hash=readable_payloads_by_hash,
                payload_disposition_result=payload_disposition_result,
                dry_run=dry_run,
            )

        gpt_links = []
        if conversation.custom_gpt_id:
            title = f"GPT - {conversation.custom_gpt_id}"
            gpt_contexts.setdefault(
                conversation.custom_gpt_id,
                {
                    "title": title,
                    "kind": "gpt",
                    "context_id": conversation.custom_gpt_id,
                    "evidence": "explicit",
                    "status": "parsed",
                    "conversation_ids": set(),
                    "linked_file_count": 0,
                    "custom_gpt_url": context_url(conversation.custom_gpt_id),
                },
            )["conversation_ids"].add(conversation.conversation_id)
            gpt_links.append(f"[[{title}]]")

        project_links = []
        if conversation.project_id or conversation.project_name:
            project_id = conversation.project_id or slugify(conversation.project_name)
            title = f"Project - {conversation.project_name or project_id}"
            project_contexts.setdefault(
                project_id,
                {
                    "title": title,
                    "kind": "project",
                    "context_id": project_id,
                    "evidence": "explicit",
                    "status": "parsed",
                    "conversation_ids": set(),
                    "linked_file_count": 0,
                    "project_url": context_url(project_id),
                    "project_url_evidence": "reconstructed_from_context_id" if context_url(project_id) else None,
                    "project_instruction_status": "unknown",
                },
            )["conversation_ids"].add(conversation.conversation_id)
            project_links.append(f"[[{title}]]")

        knowledge_links = []
        for knowledge_id in sorted({asset.knowledge_store_id for asset in conv_assets if asset.knowledge_store_id}):
            ctx = knowledge_contexts.setdefault(
                knowledge_id,
                {
                    "title": f"Knowledge Store - {knowledge_id}",
                    "kind": "knowledge_store",
                    "context_id": knowledge_id,
                    "evidence": "explicit",
                    "status": "parsed",
                    "conversation_ids": set(),
                    "linked_file_count": 0,
                },
            )
            ctx["conversation_ids"].add(conversation.conversation_id)
            knowledge_links.append(f"[[{ctx['title']}]]")

        observed_links: list[str] = []
        for link in gpt_links:
            context_links.append(context_link(conversation, "gpt", link, "explicit"))
        for link in project_links:
            context_links.append(context_link(conversation, "project", link, "explicit"))
        for link in knowledge_links:
            context_links.append(context_link(conversation, "knowledge_store", link, "explicit"))
        message_context_links = message_context_link_rows(conversation, note_refs, selected_conversation_ids)
        context_links.extend(message_context_links)
        conv_message_sources = build_message_source_rows(conversation, conv_assets, message_context_links)
        message_sources.extend(conv_message_sources)
        conv_tool_events = build_tool_event_rows(conversation)
        tool_events.extend(conv_tool_events)
        conv_textdocs = build_textdoc_records(conversation)
        materialize_textdoc_records(
            output_path,
            conversation,
            conv_textdocs,
            used_textdoc_filenames_by_folder,
            dry_run=dry_run,
        )
        conv_textdoc_rows = [record.to_dict() for record in conv_textdocs]
        textdoc_rows.extend(conv_textdoc_rows)
        conv_citation_records = build_citation_links(conversation, conv_assets)
        conv_citation_links = [record.to_dict() for record in conv_citation_records]
        citation_links.extend(conv_citation_links)
        conversation_rows.append(
            readable_conversation_row(
                conversation,
                conv_assets,
                gpt_links,
                project_links,
                knowledge_links,
                observed_links,
                source_evidence_count=len(conv_message_sources),
                tool_evidence_count=len(conv_tool_events),
            )
        )
        attach_conversation_to_contexts(
            conversation=conversation,
            assets=conv_assets,
            note_ref=note_refs[conversation.conversation_id],
            gpt_contexts=gpt_contexts,
            project_contexts=project_contexts,
            knowledge_contexts=knowledge_contexts,
        )
        for message in evidence_messages(conversation):
            row = message.to_dict()
            row["conversation_id"] = conversation.conversation_id
            message_rows.append(row)

        if emit_markdown and not dry_run:
            note_ref = note_refs[conversation.conversation_id]
            note_path = output_path / note_ref["path"]
            note_render_jobs.append(
                {
                    "conversation": conversation,
                    "assets": conv_assets,
                    "note_path": note_path,
                    "runtime_artifacts": conv_runtime_artifacts,
                    "historical_comparisons": conv_historical_comparisons,
                    "historical_signals": conv_historical_signals,
                    "technical_events": conv_technical_events,
                    "gpt_links": gpt_links,
                    "project_links": project_links,
                    "knowledge_links": knowledge_links,
                    "observed_links": observed_links,
                    "context_evidence_rows": message_context_links,
                    "message_source_rows": conv_message_sources,
                    "tool_event_rows": conv_tool_events,
                    "textdoc_rows": conv_textdoc_rows,
                    "citation_links": conv_citation_records,
                    "asset_presentation_by_archive_path": asset_presentation_by_archive_path,
                }
            )

    mark_referenced_knowledge_library_files(
        library_by_file_id=library_by_file_id,
        library_resolution_index=library_resolution_index,
        used_physical_paths=used_physical_paths,
        selected_conversation_ids=selected_conversation_ids,
        selected_gpt_ids=selected_gpt_ids,
        include_all=not filtered_output,
    )
    if copy_assets:
        copy_knowledge_library_files(
            archive=archive,
            output_path=output_path,
            library_by_file_id=library_by_file_id,
            library_resolution_index=library_resolution_index,
            physical_members=physical_members,
            physical_members_by_archive_path=physical_members_by_archive_path,
            asset_filename_map=asset_filename_map,
            inventory_by_archive_path=inventory_by_archive_path,
            materialization_hints_by_archive_path=materialization_hints_by_archive_path,
            used_file_keys=used_file_keys,
            used_physical_paths=used_physical_paths,
            file_manifest=file_manifest,
            readable_payloads_by_hash=readable_payloads_by_hash,
            payload_disposition_result=payload_disposition_result,
            selected_conversation_ids=selected_conversation_ids,
            selected_gpt_ids=selected_gpt_ids,
            include_all=not filtered_output,
            dry_run=dry_run,
        )
        attach_manifest_files_to_contexts(file_manifest, gpt_contexts, knowledge_contexts)

    orphan_members = [
        member
        for member in physical_members
        if member.archive_path not in used_physical_paths
        and payload_disposition_result.is_unlinked_candidate(member.archive_path)
    ]
    orphan_assets = build_orphan_rows(
        orphan_members,
        asset_filename_map=asset_filename_map,
        library_by_file_id=library_by_file_id,
        inventory_by_archive_path=inventory_by_archive_path,
        materialization_hints_by_archive_path=materialization_hints_by_archive_path,
    )
    if copy_assets and copy_unlinked_assets:
        orphan_assets = copy_orphan_assets(
            archive=archive,
            output_path=output_path,
            orphan_members=orphan_members,
            asset_filename_map=asset_filename_map,
            inventory_by_archive_path=inventory_by_archive_path,
            materialization_hints_by_archive_path=materialization_hints_by_archive_path,
            readable_payloads_by_hash=readable_payloads_by_hash,
            physical_members_by_archive_path=physical_members_by_archive_path,
            payload_disposition_result=payload_disposition_result,
            dry_run=dry_run,
        )
    orphan_assets = [
        row | payload_disposition_fields(payload_disposition_result, str(row["archive_path"]))
        for row in orphan_assets
    ]
    payload_candidate_result = UnverifiedPayloadCandidateAnalyzer().construct(
        all_assets,
        asset_resolution_comparisons,
        [*file_manifest, *orphan_assets],
    )
    unverified_payload_candidates_by_asset_ref_id = payload_candidate_result.by_asset_ref_id()
    sandbox_links_by_conversation = build_sandbox_links_by_conversation(
        reference_result,
        physical_resolution_result,
        [*file_manifest, *orphan_assets],
    )

    if emit_markdown and not dry_run:
        for job in note_render_jobs:
            note_path = job["note_path"]
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text(
                note_renderer(
                    job["conversation"],
                    job["assets"],
                    runtime_artifacts=job["runtime_artifacts"],
                    historical_comparisons=job["historical_comparisons"],
                    historical_signals=job["historical_signals"],
                    technical_events=job["technical_events"],
                    gpt_links=job["gpt_links"],
                    project_links=job["project_links"],
                    knowledge_links=job["knowledge_links"],
                    observed_links=job["observed_links"],
                    context_evidence_rows=job["context_evidence_rows"],
                    message_source_rows=job["message_source_rows"],
                    tool_event_rows=job["tool_event_rows"],
                    textdoc_rows=job["textdoc_rows"],
                    citation_links=job["citation_links"],
                    asset_presentation_by_archive_path=job["asset_presentation_by_archive_path"],
                    unverified_payload_candidates_by_asset_ref_id=unverified_payload_candidates_by_asset_ref_id,
                    sandbox_links_by_message=sandbox_links_by_conversation.get(job["conversation"].conversation_id, {}),
                ),
                encoding="utf-8",
            )
            note_locator_records.append(
                {
                    "conversation_id": job["conversation"].conversation_id,
                    "relative_note_path": note_path.relative_to(output_path).as_posix(),
                }
            )
        emit_context_hubs(output_path, gpt_contexts, project_contexts, knowledge_contexts, observed_contexts)
        (output_path / HOME_FILE).write_text(render_home(), encoding="utf-8")
        write_conversation_note_locators(output_path, note_locator_records)
    if emit_bases and not dry_run:
        emit_base_files(output_path)
    if emit_dataview and not dry_run:
        emit_dataview_files(output_path)

    audit = build_audit(
        shard_members=shard_members,
        conversations=conversations,
        assets=all_assets,
        physical_members=physical_members,
        orphan_assets=orphan_assets,
        warnings=warnings,
    )
    audit["payload_materialization"] = build_payload_materialization_summary(
        payload_disposition_result,
        file_manifest,
        orphan_assets,
    )
    audit["unverified_payload_candidates"] = payload_candidate_result.summary_dict()
    if emit_json and not dry_run:
        proof_dir = output_path / EVIDENCE_DIR
        append_jsonl(proof_dir / "conversations.jsonl", conversation_rows)
        append_jsonl(proof_dir / "messages.jsonl", message_rows)
        append_jsonl(proof_dir / "message_sources.jsonl", message_sources)
        append_jsonl(proof_dir / "tool_events.jsonl", tool_events)
        append_jsonl(proof_dir / "textdocs.jsonl", textdoc_rows)
        append_jsonl(proof_dir / "citation_links.jsonl", citation_links)
        append_jsonl(proof_dir / "asset_links.jsonl", [asset.to_dict() for asset in all_assets])
        append_jsonl(proof_dir / "context_links.jsonl", context_links)
        append_jsonl(proof_dir / "file_manifest.jsonl", file_manifest)
        append_jsonl(proof_dir / "unlinked_assets.jsonl", orphan_assets)
        emit_unverified_payload_candidate_evidence(proof_dir, payload_candidate_result)
        write_json(proof_dir / "pack_manifest.json", pack_manifest)
        write_json(proof_dir / "parse_audit.json", audit)
    return audit


def readable_conversation_row(
    conversation: ConversationRecord,
    assets: list[AssetRecord],
    gpt_links: list[str],
    project_links: list[str],
    knowledge_links: list[str],
    observed_links: list[str],
    *,
    source_evidence_count: int = 0,
    tool_evidence_count: int = 0,
) -> dict[str, Any]:
    year, month, month_key = year_month(conversation.create_time)
    unique_file_count = unique_asset_count(assets)
    resolved_file_count = unique_asset_count(assets, status="found")
    unresolved_file_count = unresolved_asset_count(assets)
    return {
        "type": "openai_conversation",
        "schema_version": SCHEMA_VERSION,
        "status": "parsed",
        "title": conversation.title,
        "reviewed": False,
        "created_at": conversation.create_time,
        "updated_at": conversation.update_time,
        "year": int(year) if year.isdigit() else year,
        "month": month,
        "month_key": month_key,
        "conversation_id": conversation.conversation_id,
        "source_archive": source_part(conversation.source_archive_path),
        "source_archive_path": conversation.source_archive_path,
        "source_shard": conversation.source_json_shard,
        "chat_url": conversation.chat_url,
        "message_count": conversation.message_count,
        "default_model": conversation.default_model_slug,
        "models_seen": conditional_models_seen(conversation.default_model_slug, conversation.resolved_model_slugs),
        "memory_scope": conversation.memory_scope,
        "voice": conversation.voice,
        "is_archived": conversation.is_archived,
        "is_starred": conversation.is_starred,
        "is_do_not_remember": conversation.is_do_not_remember,
        "gpts": gpt_links,
        "projects": [],
        "space_projects": project_links,
        "knowledge_stores": knowledge_links,
        "observed_contexts": observed_links,
        "context_evidence": "mixed" if observed_links and (gpt_links or project_links or knowledge_links) else "explicit" if (gpt_links or project_links or knowledge_links) else "observed" if observed_links else "unknown",
        "file_reference_count": len(assets),
        "unique_file_count": unique_file_count,
        "resolved_file_count": resolved_file_count,
        "unresolved_file_count": unresolved_file_count,
        "source_evidence_count": source_evidence_count,
        "tool_evidence_count": tool_evidence_count,
        "has_unresolved_files": unresolved_file_count > 0,
        "has_warnings": bool(conversation.parse_warnings),
    }


def attach_conversation_to_contexts(
    *,
    conversation: ConversationRecord,
    assets: list[AssetRecord],
    note_ref: dict[str, str],
    gpt_contexts: dict[str, dict[str, Any]],
    project_contexts: dict[str, dict[str, Any]],
    knowledge_contexts: dict[str, dict[str, Any]],
) -> None:
    targets: list[dict[str, Any]] = []
    if conversation.custom_gpt_id and conversation.custom_gpt_id in gpt_contexts:
        targets.append(gpt_contexts[conversation.custom_gpt_id])
    project_id = conversation.project_id or slugify(conversation.project_name) if (conversation.project_id or conversation.project_name) else None
    if project_id and project_id in project_contexts:
        targets.append(project_contexts[project_id])
    for knowledge_id in sorted({asset.knowledge_store_id for asset in assets if asset.knowledge_store_id}):
        if knowledge_id in knowledge_contexts:
            targets.append(knowledge_contexts[knowledge_id])

    if not targets:
        return
    summary = context_conversation_line(conversation, assets, note_ref)
    file_rows = context_asset_rows(assets)
    for context in targets:
        add_unique_context_conversation_row(context, conversation.conversation_id, summary, conversation.create_time)
        for row in file_rows:
            add_unique_context_file_row(context, row)


def context_conversation_line(conversation: ConversationRecord, assets: list[AssetRecord], note_ref: dict[str, str]) -> str:
    linked = unique_asset_count(assets, status="found")
    unresolved = unresolved_asset_count(assets)
    memory = f"memory {conversation.memory_scope}" if conversation.memory_scope else "memory unknown"
    voice = f"voice {conversation.voice}" if conversation.voice else "voice unknown"
    return (
        f"- {note_ref['wikilink']} · {conversation.message_count} messages · "
        f"{linked} files · {unresolved} unresolved · {memory} · {voice}"
    )


def context_asset_rows(assets: list[AssetRecord]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for asset in unique_assets_for_runner(assets):
        key = asset.raw_file_id or asset.raw_dat_filename or asset.asset_ref_id
        if is_unresolved_asset_status(asset.asset_status):
            label = asset.reconstructed_filename or asset.raw_file_id or "unknown file"
            role = "collision" if asset.asset_status == "collision" else "unresolved"
            suffix = " · multiple physical candidates" if role == "collision" else ""
            rows.append({"key": key, "role": role, "line": f"- `{label}`{suffix}", "sort_key": label})
            continue
        role = asset.file_role if asset.file_role in {"knowledge", "generated"} else "attachment"
        label = asset.copied_filename or asset.reconstructed_filename or asset.raw_file_id or "unknown file"
        if asset.copied_filename and is_embeddable_filename(label):
            line = f"![[{label}|300]]"
        elif asset.copied_filename:
            line = f"- [[{label}]]"
        else:
            line = f"- `{label}`"
        rows.append({"key": key, "role": role, "line": line, "sort_key": label})
    return rows


def attach_manifest_files_to_contexts(
    file_manifest: list[dict[str, Any]],
    gpt_contexts: dict[str, dict[str, Any]],
    knowledge_contexts: dict[str, dict[str, Any]],
) -> None:
    for row in file_manifest:
        linked_row = manifest_context_file_row(row)
        if not linked_row:
            continue
        gizmo_id = extract_id(row.get("gizmo_id"))
        if gizmo_id and gizmo_id in gpt_contexts:
            add_unique_context_file_row(gpt_contexts[gizmo_id], linked_row)
        knowledge_id = extract_id(row.get("knowledge_store_id"))
        if knowledge_id and knowledge_id in knowledge_contexts:
            add_unique_context_file_row(knowledge_contexts[knowledge_id], linked_row)


def manifest_context_file_row(row: dict[str, Any]) -> dict[str, Any] | None:
    copied_path = row.get("copied_path")
    label = basename(copied_path) if copied_path else row.get("filename") or row.get("file_id")
    if not label:
        return None
    role = row.get("file_role") if row.get("file_role") in {"knowledge", "generated"} else "attachment"
    line = f"- [[{label}]]"
    if role != "knowledge" and is_embeddable_filename(str(label)):
        line = f"![[{label}|300]]"
    return {
        "key": row.get("file_id") or row.get("dedupe_key") or row.get("archive_path") or label,
        "role": role,
        "line": line,
        "sort_key": str(label),
    }


def add_unique_context_conversation_row(context: dict[str, Any], key: str, line: str, created_at: str | None) -> None:
    seen = context.setdefault("_linked_conversation_seen", set())
    if key in seen:
        return
    seen.add(key)
    context.setdefault("linked_conversation_rows", []).append(
        {"key": key, "line": line, "created_at": created_at or ""}
    )


def add_unique_context_file_row(context: dict[str, Any], row: dict[str, Any]) -> None:
    seen = context.setdefault("_linked_file_seen", set())
    key = row.get("key") or row.get("line")
    if key in seen:
        return
    seen.add(key)
    context.setdefault("linked_file_rows", []).append(row)
    context["linked_file_count"] = len(context["linked_file_rows"])


def context_linked_files_by_role(context: dict[str, Any]) -> dict[str, list[str]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in context.get("linked_file_rows") or []:
        grouped[str(row.get("role") or "attachment")].append(row)
    result: dict[str, list[str]] = {}
    for role, rows in grouped.items():
        result[role] = [
            str(row.get("line"))
            for row in sorted(rows, key=lambda item: (str(item.get("sort_key") or ""), str(item.get("line") or "")))
            if row.get("line")
        ]
    return result


def context_linked_conversation_lines(context: dict[str, Any]) -> list[str]:
    rows = context.get("linked_conversation_rows") or []
    return [
        str(row.get("line"))
        for row in sorted(rows, key=lambda item: str(item.get("created_at") or ""), reverse=True)
        if row.get("line")
    ]


def unique_assets_for_runner(assets: list[AssetRecord]) -> list[AssetRecord]:
    seen: set[str] = set()
    result: list[AssetRecord] = []
    for asset in assets:
        key = asset.raw_file_id or asset.raw_dat_filename or asset.asset_ref_id
        if key in seen:
            continue
        seen.add(key)
        result.append(asset)
    return result


def is_embeddable_filename(filename: str) -> bool:
    extension = extension_from_name(filename)
    return bool(extension and extension.lower() in {"png", "jpg", "jpeg", "gif", "webp", "svg", "pdf"})


def build_conversation_note_refs(conversations: list[ConversationRecord]) -> dict[str, dict[str, str]]:
    used_note_names: dict[Path, set[str]] = defaultdict(set)
    refs: dict[str, dict[str, str]] = {}
    for conversation in conversations:
        year, month, _ = year_month(conversation.create_time)
        folder = Path(CONVERSATIONS_DIR) / year / month
        date = date_prefix(conversation.create_time)
        title = safe_filename(conversation.title or "Untitled conversation")
        note_name = unique_note_name(f"{date} - {title}.md", used_note_names[folder])
        note_stem = Path(note_name).stem
        refs[conversation.conversation_id] = {
            "folder": str(folder),
            "path": str(folder / note_name),
            "note_name": note_name,
            "note_stem": note_stem,
            "wikilink": f"[[{note_stem}]]",
        }
    return refs


def message_context_link_rows(
    conversation: ConversationRecord,
    note_refs: dict[str, dict[str, str]],
    emitted_conversation_ids: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for message in evidence_messages(conversation):
        for citation in message.context_citations:
            cited_id = citation.get("cited_conversation_id")
            target_ref = note_refs.get(cited_id) if isinstance(cited_id, str) else None
            target_emitted = cited_id in emitted_conversation_ids if isinstance(cited_id, str) else False
            rows.append(
                {
                    "conversation_id": conversation.conversation_id,
                    "message_id": message.message_id,
                    "context_kind": citation.get("context_kind") or "past_conversation",
                    "evidence": "explicit",
                    "source_shard": conversation.source_json_shard,
                    "proof_path": citation.get("proof_path"),
                    "citation_uuid": citation.get("citation_uuid"),
                    "cited_conversation_id": cited_id,
                    "cited_title": citation.get("title"),
                    "cited_chat_url": citation.get("chat_url"),
                    "cited_note": target_ref.get("wikilink") if target_ref and target_emitted else None,
                    "cited_note_path": target_ref.get("path") if target_ref and target_emitted else None,
                    "cited_note_candidate": target_ref.get("wikilink") if target_ref else None,
                    "cited_note_candidate_path": target_ref.get("path") if target_ref else None,
                    "snippet": citation.get("snippet"),
                    "memory_id": citation.get("memory_id"),
                    "reason": citation.get("reason"),
                    "category": citation.get("category"),
                    "retrieval_origin": citation.get("retrieval_origin"),
                    "source_url": citation.get("source_url"),
                    "pub_date": citation.get("pub_date"),
                    "attribution": citation.get("attribution"),
                    "status": citation.get("status") or "explicit",
                    "raw_url": citation.get("raw_url"),
                }
            )
    return rows


def build_message_source_rows(
    conversation: ConversationRecord,
    assets: list[AssetRecord],
    context_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    counter = 0
    for context in context_rows:
        if not context.get("message_id"):
            continue
        counter += 1
        rows.append(
            {
                "source_ref_id": f"{conversation.conversation_id}:{context.get('message_id')}:source-{counter:04d}",
                "conversation_id": conversation.conversation_id,
                "message_id": context.get("message_id"),
                "source_shard": conversation.source_json_shard,
                "source_kind": source_kind_for_context(context.get("context_kind")),
                "source_family": "context",
                "evidence": "explicit",
                "proof_path": context.get("proof_path"),
                "title": context.get("cited_title"),
                "snippet": context.get("snippet"),
                "reason": context.get("reason"),
                "attribution": context.get("attribution"),
                "url": context.get("cited_chat_url") or context.get("source_url") or context.get("raw_url"),
                "conversation_context_kind": context.get("context_kind"),
                "cited_conversation_id": context.get("cited_conversation_id"),
                "cited_note": context.get("cited_note"),
                "cited_note_path": context.get("cited_note_path"),
                "cited_note_candidate": context.get("cited_note_candidate"),
                "cited_note_candidate_path": context.get("cited_note_candidate_path"),
                "memory_id": context.get("memory_id"),
                "retrieval_origin": context.get("retrieval_origin"),
                "raw_status": context.get("status"),
            }
        )

    for asset in assets:
        counter += 1
        source_kind = source_kind_for_asset(asset)
        source = asset.raw_reference.get("source") if isinstance(asset.raw_reference, dict) else None
        row = {
            "source_ref_id": f"{conversation.conversation_id}:{asset.message_id or 'unknown-message'}:source-{counter:04d}",
            "conversation_id": conversation.conversation_id,
            "message_id": asset.message_id,
            "source_shard": conversation.source_json_shard,
            "source_kind": source_kind,
            "source_family": "file",
            "evidence": "explicit",
            "proof_path": asset.proof_path,
            "title": asset.reconstructed_filename,
            "file_id": asset.raw_file_id,
            "raw_dat_filename": asset.raw_dat_filename,
            "mime_type": asset.mime_type,
            "source": source,
            "asset_status": asset.asset_status,
            "payload_status": (
                "exported"
                if asset.asset_status == "found"
                else "ambiguous"
                if asset.asset_status == "collision"
                else "not_exported"
            ),
            "copied_pack_path": asset.copied_pack_path,
            "physical_archive_path": asset.physical_archive_path,
            "file_role": asset.file_role,
            "strict_knowledge_store_id": asset.knowledge_store_id,
            "strict_library_file_id": asset.library_file_id,
            "context_at_use_kind": context_at_use_kind(conversation),
            "context_at_use_id": conversation.project_id or conversation.custom_gpt_id,
            "context_at_use_evidence": "conversation_context",
            "candidate_role": candidate_source_role(asset, conversation),
            "candidate_confidence": candidate_source_confidence(asset, conversation),
        }
        rows.append(row)

    for message in evidence_messages(conversation):
        for proof_path, ref, source_kind in web_source_references(message):
            counter += 1
            rows.append(
                {
                    "source_ref_id": f"{conversation.conversation_id}:{message.message_id or message.node_id}:source-{counter:04d}",
                    "conversation_id": conversation.conversation_id,
                    "message_id": message.message_id,
                    "source_shard": conversation.source_json_shard,
                    "source_kind": source_kind,
                    "source_family": "web",
                    "evidence": "explicit",
                    "proof_path": proof_path,
                    "title": ref.get("title") or ref.get("name") or ref.get("attribution"),
                    "snippet": ref.get("snippet"),
                    "attribution": ref.get("attribution") or ref.get("source_name"),
                    "url": ref.get("url"),
                    "pub_date": ref.get("pub_date"),
                    "raw_type": ref.get("type"),
                    "raw_status": ref.get("status"),
                }
            )
    return rows


def build_tool_event_rows(conversation: ConversationRecord) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    counter = 0
    for message in evidence_messages(conversation):
        if message.author_role == "tool":
            counter += 1
            rows.append(
                tool_event_row(
                    conversation,
                    message,
                    counter,
                    event_kind="tool_message",
                    tool_name=message.channel or "tool",
                    proof_path=f"mapping.{message.node_id}.message",
                    raw_event={"content_type": message.content_type, "channel": message.channel},
                )
            )

        metadata = message.raw_metadata if isinstance(message.raw_metadata, dict) else {}
        search_groups = metadata.get("search_result_groups")
        if isinstance(search_groups, list):
            for index, group in enumerate(search_groups):
                if not isinstance(group, dict):
                    continue
                counter += 1
                rows.append(
                    tool_event_row(
                        conversation,
                        message,
                        counter,
                        event_kind="web_search_group",
                        tool_name="web_search",
                        proof_path=f"mapping.{message.node_id}.message.metadata.search_result_groups[{index}]",
                        raw_event=compact_raw(group),
                        title=group.get("domain") or group.get("title"),
                        url=first_url_in_search_group(group),
                    )
                )

        for proof_path, ref, source_kind in web_source_references(message):
            counter += 1
            rows.append(
                tool_event_row(
                    conversation,
                    message,
                    counter,
                    event_kind="web_reference",
                    tool_name="content_reference",
                    proof_path=proof_path,
                    raw_event=compact_raw(ref),
                    title=ref.get("title") or ref.get("name") or ref.get("attribution"),
                    url=ref.get("url"),
                    source_kind=source_kind,
                )
            )

        for key, value in sorted(metadata.items(), key=lambda item: item[0]):
            if key == "code_blocks":
                continue
            if key.startswith("tool_") or key.endswith("_subtool"):
                counter += 1
                rows.append(
                    tool_event_row(
                        conversation,
                        message,
                        counter,
                        event_kind="tool_metadata",
                        tool_name=key,
                        proof_path=f"mapping.{message.node_id}.message.metadata.{key}",
                        raw_event=compact_raw(value),
                    )
                )
    return rows


def tool_event_row(
    conversation: ConversationRecord,
    message,
    counter: int,
    *,
    event_kind: str,
    tool_name: str,
    proof_path: str,
    raw_event: Any,
    title: Any = None,
    url: Any = None,
    source_kind: Any = None,
) -> dict[str, Any]:
    return {
        "tool_event_id": f"{conversation.conversation_id}:{message.message_id or message.node_id}:tool-{counter:04d}",
        "conversation_id": conversation.conversation_id,
        "message_id": message.message_id,
        "source_shard": conversation.source_json_shard,
        "event_kind": event_kind,
        "tool_name": tool_name,
        "evidence": "explicit",
        "proof_path": proof_path,
        "title": title,
        "url": url,
        "source_kind": source_kind,
        "raw_event": raw_event,
    }


def first_url_in_search_group(group: dict[str, Any]) -> str | None:
    entries = group.get("entries")
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("url"), str):
            return entry["url"]
    return None


def evidence_messages(conversation: ConversationRecord):
    return conversation.all_messages or conversation.messages


def source_kind_for_context(context_kind: Any) -> str:
    if context_kind == "past_conversation":
        return "past_conversation"
    if context_kind == "user_memory":
        return "user_memory"
    if context_kind == "user_instructions":
        return "user_instructions"
    if context_kind == "grouped_webpages":
        return "past_conversation_group"
    return str(context_kind or "context")


def source_kind_for_asset(asset: AssetRecord) -> str:
    proof = asset.proof_path
    if ".metadata.attachments[" in proof:
        return "uploaded_file"
    if ".message.content.parts[" in proof:
        return "inline_asset"
    if ".metadata.citations[" in proof or ".metadata.content_references[" in proof:
        return "cited_file"
    return "file_reference"


def context_at_use_kind(conversation: ConversationRecord) -> str | None:
    if conversation.project_id:
        return "project"
    if conversation.custom_gpt_id:
        return "gpt"
    return None


def candidate_source_role(asset: AssetRecord, conversation: ConversationRecord) -> str | None:
    if asset.knowledge_store_id:
        return "knowledge_store_file"
    if source_kind_for_asset(asset) == "cited_file" and conversation.project_id:
        return "project_knowledge_reference"
    if source_kind_for_asset(asset) == "cited_file" and conversation.custom_gpt_id:
        return "gpt_knowledge_reference"
    return None


def candidate_source_confidence(asset: AssetRecord, conversation: ConversationRecord) -> str | None:
    if asset.knowledge_store_id:
        return "explicit"
    if candidate_source_role(asset, conversation):
        return "heuristic"
    return None


def web_source_references(message) -> list[tuple[str, dict[str, Any], str]]:
    rows: list[tuple[str, dict[str, Any], str]] = []
    for index, citation in enumerate(message.citations):
        metadata = citation.get("metadata") if isinstance(citation, dict) and isinstance(citation.get("metadata"), dict) else {}
        if metadata.get("url") or metadata.get("type") == "webpage":
            rows.append((f"mapping.{message.node_id}.message.metadata.citations[{index}].metadata", metadata, "web_citation"))
    for index, ref in enumerate(message.content_references):
        if not isinstance(ref, dict) or is_file_like_source(ref):
            continue
        if isinstance(ref.get("items"), list):
            for item_index, item in enumerate(ref["items"]):
                if isinstance(item, dict):
                    rows.append((f"mapping.{message.node_id}.message.metadata.content_references[{index}].items[{item_index}]", item, ref.get("type") or "grouped_webpages"))
        elif ref.get("url") or ref.get("type") in {"webpage", "webpage_extended", "grouped_webpages"}:
            rows.append((f"mapping.{message.node_id}.message.metadata.content_references[{index}]", ref, ref.get("type") or "web_reference"))
    return rows


def is_file_like_source(ref: dict[str, Any]) -> bool:
    return (
        isinstance(ref.get("id"), str)
        and ref["id"].startswith(("file-", "file_"))
    ) or ref.get("type") == "file" or ref.get("source") in {"my_files", "google_drive"}


def unique_asset_count(assets: list[AssetRecord], *, status: str | None = None) -> int:
    seen: set[str] = set()
    for asset in assets:
        if status is not None and asset.asset_status != status:
            continue
        seen.add(asset.raw_file_id or asset.raw_dat_filename or asset.asset_ref_id)
    return len(seen)


def unresolved_asset_count(assets: list[AssetRecord]) -> int:
    return len(
        {
            asset.raw_file_id or asset.raw_dat_filename or asset.asset_ref_id
            for asset in assets
            if is_unresolved_asset_status(asset.asset_status)
        }
    )


def build_materialization_hints(
    reference_result: ReferenceExtractionResult,
    resolution_result: PhysicalResolutionResult,
) -> dict[str, list[MaterializationHint]]:
    """Index explicit source hints by their uniquely resolved physical member."""
    references_by_id = {record.reference_id: record for record in reference_result.records}
    by_archive_path: dict[str, list[MaterializationHint]] = defaultdict(list)
    for resolution in resolution_result.records:
        if resolution.resolution_status not in {"resolved_unique", "resolved_equivalent_candidates"} or not resolution.selected_archive_path:
            continue
        reference = references_by_id.get(resolution.reference_id)
        if not reference:
            continue
        hint = MaterializationHint(
            logical_path=reference.logical_name,
            declared_mime=reference.declared_mime_type,
            reference_extension=extension_from_name(reference.logical_name),
            source="source_reference",
        )
        if hint.logical_path or hint.declared_mime or hint.reference_extension:
            by_archive_path[resolution.selected_archive_path].append(hint)
    return {
        archive_path: sorted(
            hints,
            key=lambda hint: (
                hint.logical_path or "",
                hint.declared_mime or "",
                hint.reference_extension or "",
                hint.source,
            ),
        )
        for archive_path, hints in by_archive_path.items()
    }


def materialize_member(
    member: ArchiveMember,
    *,
    asset_filename_map: dict[str, str],
    inventory_by_archive_path: dict[str, InventoryEntry],
    reference_hints: list[MaterializationHint] | tuple[MaterializationHint, ...],
) -> MaterializedFile:
    return resolve_materialized_file(
        physical_member=member,
        inventory_entry=inventory_by_archive_path.get(member.archive_path),
        filename_map=asset_filename_map,
        reference_hints=reference_hints,
    )


def materialization_fields(materialized: MaterializedFile) -> dict[str, Any]:
    return {
        "physical_name": materialized.physical_name,
        "physical_archive_path": materialized.physical_archive_path,
        "logical_path": materialized.logical_path,
        "logical_basename": materialized.logical_basename,
        "declared_extension": materialized.declared_extension,
        "detected_extension": materialized.detected_extension,
        "detected_mime": materialized.detected_mime,
        "output_filename": materialized.output_filename,
        "naming_basis": materialized.naming_basis,
        "identity_status": materialized.identity_status,
        "type_status": materialized.type_status,
        "materialization_status": materialized.materialization_status,
        "collision_key": materialized.collision_key,
    }


def copy_payload_once(
    archive: ExportArchive,
    member: ArchiveMember,
    destination: Path,
    copied_path: str,
    readable_payloads_by_hash: dict[str, ReadablePayloadCopy],
    *,
    dry_run: bool,
) -> tuple[str | None, ReadablePayloadCopy | None]:
    """Copy a payload once, retaining an existing readable copy when bytes match."""
    if dry_run:
        return None, None
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        content_sha256 = archive.copy_member_and_hash(member, temporary)
        existing_copy = readable_payloads_by_hash.get(content_sha256)
        if existing_copy:
            temporary.unlink(missing_ok=True)
            return content_sha256, existing_copy
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    readable_payloads_by_hash[content_sha256] = ReadablePayloadCopy(
        physical_archive_path=member.archive_path,
        copied_path=copied_path,
        content_sha256=content_sha256,
    )
    return content_sha256, None


def payload_copy_fields(
    content_sha256: str | None,
    duplicate_of: ReadablePayloadCopy | None = None,
) -> dict[str, Any]:
    return {
        "content_sha256": content_sha256,
        "duplicate_payload_of": duplicate_of.physical_archive_path if duplicate_of else None,
        "duplicate_payload_copied_path": duplicate_of.copied_path if duplicate_of else None,
        "copy_status": "duplicate_payload_not_copied" if duplicate_of else "copied",
    }


def payload_disposition_fields(
    dispositions: PayloadDispositionResult,
    archive_path: str,
) -> dict[str, Any]:
    """Expose the canonical physical classification on derived orphan rows."""
    row = dispositions.disposition_for(archive_path)
    if row is None:
        return {}
    return {
        "physical_origin": row.physical_origin,
        "content_equivalence_group_id": row.content_equivalence_group_id,
        "payload_disposition_status": row.disposition_status,
        "canonical_archive_path": row.canonical_archive_path,
        "referenced_archive_paths": row.referenced_archive_paths,
    }


def canonical_copy_source(
    physical: ArchiveMember,
    *,
    physical_members_by_archive_path: dict[str, ArchiveMember],
    payload_disposition_result: PayloadDispositionResult,
) -> ArchiveMember:
    """Choose the classified representative before any readable-pack write.

    The evidence relation remains attached to ``physical``.  Only the source
    bytes for the readable copy are redirected to the deterministic canonical
    member, which is safe because the group is defined by SHA-256 equality.
    """
    disposition = payload_disposition_result.disposition_for(physical.archive_path)
    if not disposition or not disposition.canonical_archive_path:
        return physical
    return physical_members_by_archive_path.get(disposition.canonical_archive_path, physical)


def materialization_provenance_fields(
    physical: ArchiveMember,
    copy_source: ArchiveMember,
    payload_disposition_result: PayloadDispositionResult,
) -> dict[str, str | None]:
    disposition = payload_disposition_result.disposition_for(physical.archive_path)
    return {
        "canonical_archive_path": disposition.canonical_archive_path if disposition else None,
        "materialized_from_archive_path": copy_source.archive_path,
    }


def discard_reserved_filename(used_names: set[str], filename: str) -> None:
    """Undo a tentative name reservation when the temporary payload is deduplicated."""
    used_names.discard(filename.casefold())


def copy_conversation_assets(
    *,
    archive: ExportArchive,
    output_path: Path,
    conversation: ConversationRecord,
    assets: list[AssetRecord],
    physical_members: list[ArchiveMember],
    physical_members_by_archive_path: dict[str, ArchiveMember],
    asset_filename_map: dict[str, str],
    inventory_by_archive_path: dict[str, InventoryEntry],
    materialization_hints_by_archive_path: dict[str, list[MaterializationHint]],
    used_file_keys: set[str],
    used_physical_paths: set[str],
    file_manifest: list[dict[str, Any]],
    readable_payloads_by_hash: dict[str, ReadablePayloadCopy],
    payload_disposition_result: PayloadDispositionResult,
    dry_run: bool,
) -> None:
    used_names_by_dir: dict[Path, set[str]] = defaultdict(set)
    for asset in assets:
        if asset.asset_status != "found" or not asset.physical_archive_path:
            continue
        physical = first_physical_for_path(physical_members, asset.physical_archive_path)
        if not physical:
            continue
        key = asset.raw_file_id or asset.raw_dat_filename or physical.archive_path
        if key in used_file_keys:
            asset.copied_pack_path = find_manifest_path(file_manifest, key)
            asset.copied_filename = basename(asset.copied_pack_path or "") or None
            continue
        used_file_keys.add(key)
        used_physical_paths.add(physical.archive_path)
        destination_dir = asset_destination_dir(output_path, asset.file_role, conversation.create_time)
        materialized = materialize_member(
            physical,
            asset_filename_map=asset_filename_map,
            inventory_by_archive_path=inventory_by_archive_path,
            reference_hints=(*materialization_hints_by_archive_path.get(physical.archive_path, []), MaterializationHint(
                logical_path=asset.reconstructed_filename,
                declared_mime=asset.mime_type,
                reference_extension=asset.normalized_extension,
                source="asset_reference",
            )),
        )
        desired_name = deduped_asset_filename(materialized.output_filename, key)
        copied_name = unique_filename(desired_name, used_names_by_dir[destination_dir])
        if copied_name != desired_name:
            mark_collision(materialized, copied_name)
        else:
            materialized.output_filename = copied_name
            materialized.collision_key = copied_name.casefold()
        destination = destination_dir / copied_name
        copy_source = canonical_copy_source(
            physical,
            physical_members_by_archive_path=physical_members_by_archive_path,
            payload_disposition_result=payload_disposition_result,
        )
        asset.copied_filename = copied_name
        asset.copied_pack_path = str(destination.relative_to(output_path))
        asset.copied_relative_path = asset.copied_pack_path
        content_sha256, existing_copy = copy_payload_once(
            archive,
            copy_source,
            destination,
            asset.copied_pack_path,
            readable_payloads_by_hash,
            dry_run=dry_run,
        )
        if existing_copy:
            discard_reserved_filename(used_names_by_dir[destination_dir], copied_name)
            asset.copied_filename = basename(existing_copy.copied_path)
            asset.copied_pack_path = existing_copy.copied_path
            asset.copied_relative_path = existing_copy.copied_path
            materialized.output_filename = asset.copied_filename
        file_manifest.append(
            file_manifest_row(
                asset,
                key,
                asset.copied_filename,
                asset.copied_pack_path,
                physical.archive_path,
                materialized,
                content_sha256=content_sha256,
                duplicate_of=existing_copy,
                **materialization_provenance_fields(physical, copy_source, payload_disposition_result),
            )
        )


def mark_referenced_conversation_assets(assets: list[AssetRecord], used_physical_paths: set[str]) -> None:
    for asset in assets:
        if asset.asset_status == "found" and asset.physical_archive_path:
            used_physical_paths.add(asset.physical_archive_path)


def mark_runtime_artifacts(artifacts: list[RuntimeArtifactRecord], used_physical_paths: set[str]) -> None:
    """Runtime paths encode a conversation relation even when there is no file ID."""
    used_physical_paths.update(artifact.physical_archive_path for artifact in artifacts)


def copy_runtime_artifacts(
    *,
    archive: ExportArchive,
    output_path: Path,
    conversations_by_id: dict[str, ConversationRecord],
    artifacts: list[RuntimeArtifactRecord],
    physical_members: list[ArchiveMember],
    physical_members_by_archive_path: dict[str, ArchiveMember],
    asset_filename_map: dict[str, str],
    inventory_by_archive_path: dict[str, InventoryEntry],
    materialization_hints_by_archive_path: dict[str, list[MaterializationHint]],
    used_file_keys: set[str],
    used_physical_paths: set[str],
    file_manifest: list[dict[str, Any]],
    readable_payloads_by_hash: dict[str, ReadablePayloadCopy],
    payload_disposition_result: PayloadDispositionResult,
    dry_run: bool,
) -> None:
    used_names_by_dir: dict[Path, set[str]] = defaultdict(set)
    for artifact in artifacts:
        conversation = conversations_by_id.get(artifact.conversation_id)
        physical = first_physical_for_path(physical_members, artifact.physical_archive_path)
        if not conversation or not physical:
            continue
        key = artifact.artifact_id
        if key in used_file_keys:
            artifact.copied_pack_path = find_manifest_path(file_manifest, key)
            artifact.copied_filename = basename(artifact.copied_pack_path or "") or None
            continue
        used_file_keys.add(key)
        used_physical_paths.add(physical.archive_path)
        destination_dir = asset_destination_dir(output_path, "generated", conversation.create_time)
        materialized = materialize_member(
            physical,
            asset_filename_map=asset_filename_map,
            inventory_by_archive_path=inventory_by_archive_path,
            reference_hints=(*materialization_hints_by_archive_path.get(physical.archive_path, []), MaterializationHint(
                logical_path=artifact.filename,
                declared_mime=artifact.mime_type,
                reference_extension=artifact.extension,
                source="runtime_path",
            )),
        )
        desired_name = deduped_asset_filename(materialized.output_filename, artifact.execution_message_id)
        copied_name = unique_filename(desired_name, used_names_by_dir[destination_dir])
        if copied_name != desired_name:
            mark_collision(materialized, copied_name)
        else:
            materialized.output_filename = copied_name
            materialized.collision_key = copied_name.casefold()
        destination = destination_dir / copied_name
        copy_source = canonical_copy_source(
            physical,
            physical_members_by_archive_path=physical_members_by_archive_path,
            payload_disposition_result=payload_disposition_result,
        )
        artifact.copied_filename = copied_name
        artifact.copied_pack_path = str(destination.relative_to(output_path))
        content_sha256, existing_copy = copy_payload_once(
            archive,
            copy_source,
            destination,
            artifact.copied_pack_path,
            readable_payloads_by_hash,
            dry_run=dry_run,
        )
        if existing_copy:
            discard_reserved_filename(used_names_by_dir[destination_dir], copied_name)
            artifact.copied_filename = basename(existing_copy.copied_path)
            artifact.copied_pack_path = existing_copy.copied_path
            materialized.output_filename = artifact.copied_filename
        file_manifest.append(
            runtime_artifact_manifest_row(
                artifact,
                physical,
                materialized,
                content_sha256=content_sha256,
                duplicate_of=existing_copy,
                **materialization_provenance_fields(physical, copy_source, payload_disposition_result),
            )
        )


def mark_referenced_knowledge_library_files(
    *,
    library_by_file_id: dict[str, dict[str, Any]],
    library_resolution_index: LibraryResolutionIndex,
    used_physical_paths: set[str],
    selected_conversation_ids: set[str],
    selected_gpt_ids: set[str],
    include_all: bool,
) -> None:
    for file_id, library in sorted(library_by_file_id.items()):
        if not library_has_knowledge_context(library):
            continue
        if not include_all and not library_matches_selected_context(library, selected_conversation_ids, selected_gpt_ids):
            continue
        archive_path = library_resolution_index.selected_archive_path(file_id)
        if archive_path:
            used_physical_paths.add(archive_path)


def copy_knowledge_library_files(
    *,
    archive: ExportArchive,
    output_path: Path,
    library_by_file_id: dict[str, dict[str, Any]],
    library_resolution_index: LibraryResolutionIndex,
    physical_members: list[ArchiveMember],
    physical_members_by_archive_path: dict[str, ArchiveMember],
    asset_filename_map: dict[str, str],
    inventory_by_archive_path: dict[str, InventoryEntry],
    materialization_hints_by_archive_path: dict[str, list[MaterializationHint]],
    used_file_keys: set[str],
    used_physical_paths: set[str],
    file_manifest: list[dict[str, Any]],
    readable_payloads_by_hash: dict[str, ReadablePayloadCopy],
    payload_disposition_result: PayloadDispositionResult,
    selected_conversation_ids: set[str],
    selected_gpt_ids: set[str],
    include_all: bool,
    dry_run: bool,
) -> None:
    used_names: set[str] = set()
    destination_dir = output_path / FILES_DIR / "Knowledge"
    for file_id, library in sorted(library_by_file_id.items()):
        if not library_has_knowledge_context(library):
            continue
        if not include_all and not library_matches_selected_context(library, selected_conversation_ids, selected_gpt_ids):
            continue
        key = file_id
        if key in used_file_keys:
            continue
        archive_path = library_resolution_index.selected_archive_path(file_id)
        physical = first_physical_for_path(physical_members, archive_path) if archive_path else None
        if not physical:
            continue
        used_file_keys.add(key)
        used_physical_paths.add(physical.archive_path)
        materialized = materialize_member(
            physical,
            asset_filename_map=asset_filename_map,
            inventory_by_archive_path=inventory_by_archive_path,
            reference_hints=(*materialization_hints_by_archive_path.get(physical.archive_path, []), MaterializationHint(
                logical_path=library.get("file_name") or library.get("normalized_name"),
                declared_mime=library.get("mime_type"),
                reference_extension=extension_from_name(library.get("file_name") or library.get("normalized_name")),
                source="library_metadata",
            )),
        )
        desired_name = deduped_asset_filename(materialized.output_filename, key)
        copied_name = unique_filename(desired_name, used_names)
        if copied_name != desired_name:
            mark_collision(materialized, copied_name)
        else:
            materialized.output_filename = copied_name
            materialized.collision_key = copied_name.casefold()
        destination = destination_dir / copied_name
        copied_path = str(destination.relative_to(output_path))
        copy_source = canonical_copy_source(
            physical,
            physical_members_by_archive_path=physical_members_by_archive_path,
            payload_disposition_result=payload_disposition_result,
        )
        content_sha256, existing_copy = copy_payload_once(
            archive,
            copy_source,
            destination,
            copied_path,
            readable_payloads_by_hash,
            dry_run=dry_run,
        )
        if existing_copy:
            discard_reserved_filename(used_names, copied_name)
            copied_path = existing_copy.copied_path
            materialized.output_filename = basename(copied_path)
        file_manifest.append(
            knowledge_manifest_row(
                file_id,
                library,
                physical,
                materialized,
                copied_path=copied_path,
                content_sha256=content_sha256,
                duplicate_of=existing_copy,
                **materialization_provenance_fields(physical, copy_source, payload_disposition_result),
            )
        )


def build_orphan_rows(
    orphan_members: list[ArchiveMember],
    *,
    asset_filename_map: dict[str, str],
    library_by_file_id: dict[str, dict[str, Any]],
    inventory_by_archive_path: dict[str, InventoryEntry],
    materialization_hints_by_archive_path: dict[str, list[MaterializationHint]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for member in orphan_members:
        file_id = normalize_file_id(member.basename)
        library = library_by_file_id.get(file_id or "") or {}
        materialized = materialize_member(
            member,
            asset_filename_map=asset_filename_map,
            inventory_by_archive_path=inventory_by_archive_path,
            reference_hints=(
                *materialization_hints_by_archive_path.get(member.archive_path, []),
                MaterializationHint(
                    logical_path=library.get("file_name") or library.get("normalized_name"),
                    declared_mime=library.get("mime_type"),
                    reference_extension=extension_from_name(library.get("file_name") or library.get("normalized_name")),
                    source="library_metadata",
                ),
            ),
        )
        rows.append(
            {
                "basename": member.basename,
                "file_id": file_id,
                "archive_path": member.archive_path,
                "size": member.size,
                "copied_path": None,
                "content_sha256": None,
                "duplicate_payload_of": None,
                "duplicate_payload_copied_path": None,
                "copy_status": "not_copied",
                **materialization_fields(materialized),
            }
        )
    return rows


def copy_orphan_assets(
    *,
    archive: ExportArchive,
    output_path: Path,
    orphan_members: list[ArchiveMember],
    asset_filename_map: dict[str, str],
    inventory_by_archive_path: dict[str, InventoryEntry],
    materialization_hints_by_archive_path: dict[str, list[MaterializationHint]],
    readable_payloads_by_hash: dict[str, ReadablePayloadCopy],
    physical_members_by_archive_path: dict[str, ArchiveMember],
    payload_disposition_result: PayloadDispositionResult,
    dry_run: bool,
) -> list[dict[str, Any]]:
    used_names: set[str] = set()
    destination_dir = output_path / FILES_DIR / "Unlinked"
    rows: list[dict[str, Any]] = []
    for member in sorted(
        orphan_members,
        key=lambda item: (
            (payload_disposition_result.disposition_for(item.archive_path).canonical_archive_path or item.archive_path)
            if payload_disposition_result.disposition_for(item.archive_path)
            else item.archive_path,
            item.archive_path,
        ),
    ):
        materialized = materialize_member(
            member,
            asset_filename_map=asset_filename_map,
            inventory_by_archive_path=inventory_by_archive_path,
            reference_hints=materialization_hints_by_archive_path.get(member.archive_path, []),
        )
        copied_name = unique_filename(materialized.output_filename, used_names)
        mark_collision(materialized, copied_name)
        destination = destination_dir / copied_name
        copied_path = str(destination.relative_to(output_path))
        copy_source = canonical_copy_source(
            member,
            physical_members_by_archive_path=physical_members_by_archive_path,
            payload_disposition_result=payload_disposition_result,
        )
        content_sha256, existing_copy = copy_payload_once(
            archive,
            copy_source,
            destination,
            copied_path,
            readable_payloads_by_hash,
            dry_run=dry_run,
        )
        if existing_copy:
            discard_reserved_filename(used_names, copied_name)
            rows.append(
                {
                    "basename": member.basename,
                    "archive_path": member.archive_path,
                    "size": member.size,
                    "copied_path": None,
                    "content_sha256": content_sha256,
                    "duplicate_payload_of": existing_copy.physical_archive_path,
                    "duplicate_payload_copied_path": existing_copy.copied_path,
                    "copy_status": "duplicate_payload_not_copied",
                    **materialization_provenance_fields(member, copy_source, payload_disposition_result),
                    **materialization_fields(materialized),
                }
            )
            continue
        rows.append(
            {
                "basename": member.basename,
                "archive_path": member.archive_path,
                "size": member.size,
                "copied_path": copied_path,
                "content_sha256": content_sha256,
                "duplicate_payload_of": None,
                "duplicate_payload_copied_path": None,
                "copy_status": "copied",
                **materialization_provenance_fields(member, copy_source, payload_disposition_result),
                **materialization_fields(materialized),
            }
        )
    return rows


def file_manifest_row(
    asset: AssetRecord,
    key: str,
    filename: str,
    copied_path: str,
    archive_path: str,
    materialized: MaterializedFile,
    *,
    content_sha256: str | None,
    duplicate_of: ReadablePayloadCopy | None = None,
    canonical_archive_path: str | None = None,
    materialized_from_archive_path: str | None = None,
) -> dict[str, Any]:
    return {
        "file_id": asset.raw_file_id,
        "filename": materialized.logical_basename or asset.reconstructed_filename,
        "copied_path": copied_path,
        "file_role": asset.file_role,
        "mime_type": asset.mime_type,
        "size": asset.size,
        "knowledge_store_id": asset.knowledge_store_id,
        "gizmo_id": asset.library_gizmo_id,
        "origination_thread_id": asset.origination_thread_id,
        "origination_message_id": asset.origination_message_id,
        "evidence": asset.provenance_status,
        "archive_path": archive_path,
        "canonical_archive_path": canonical_archive_path,
        "materialized_from_archive_path": materialized_from_archive_path or archive_path,
        "dedupe_key": key,
        "copied_filename": filename,
        **payload_copy_fields(content_sha256, duplicate_of),
        **materialization_fields(materialized),
    }


def runtime_artifact_manifest_row(
    artifact: RuntimeArtifactRecord,
    physical: ArchiveMember,
    materialized: MaterializedFile,
    *,
    content_sha256: str | None,
    duplicate_of: ReadablePayloadCopy | None = None,
    canonical_archive_path: str | None = None,
    materialized_from_archive_path: str | None = None,
) -> dict[str, Any]:
    return {
        "file_id": None,
        "filename": materialized.logical_basename or artifact.filename,
        "copied_path": artifact.copied_pack_path,
        "file_role": "runtime_artifact",
        "mime_type": artifact.mime_type,
        "size": artifact.size,
        "evidence": "structural_runtime_path",
        "archive_path": physical.archive_path,
        "canonical_archive_path": canonical_archive_path,
        "materialized_from_archive_path": materialized_from_archive_path or physical.archive_path,
        "dedupe_key": artifact.artifact_id,
        "copied_filename": artifact.copied_filename,
        "conversation_id": artifact.conversation_id,
        "execution_message_id": artifact.execution_message_id,
        "runtime_path": artifact.runtime_path,
        "relation_status": artifact.relation_status,
        "chat_html_archive_path": artifact.chat_html_archive_path,
        "chat_html_proof_path": artifact.chat_html_proof_path,
        **payload_copy_fields(content_sha256, duplicate_of),
        **materialization_fields(materialized),
    }


def knowledge_manifest_row(
    file_id: str,
    library: dict[str, Any],
    physical: ArchiveMember,
    materialized: MaterializedFile,
    *,
    copied_path: str,
    content_sha256: str | None,
    duplicate_of: ReadablePayloadCopy | None = None,
    canonical_archive_path: str | None = None,
    materialized_from_archive_path: str | None = None,
) -> dict[str, Any]:
    return {
        "file_id": file_id,
        "filename": materialized.logical_basename or library.get("file_name"),
        "copied_path": copied_path,
        "file_role": "knowledge",
        "mime_type": library.get("mime_type"),
        "size": coerce_int(library.get("file_size_bytes")),
        "knowledge_store_id": extract_id(library.get("knowledge_store_id")),
        "gizmo_id": library.get("gizmo_id"),
        "origination_thread_id": library.get("origination_thread_id"),
        "origination_message_id": library.get("origination_message_id"),
        "evidence": "explicit",
        "archive_path": physical.archive_path,
        "canonical_archive_path": canonical_archive_path,
        "materialized_from_archive_path": materialized_from_archive_path or physical.archive_path,
        **payload_copy_fields(content_sha256, duplicate_of),
        **materialization_fields(materialized),
    }


def find_manifest_path(file_manifest: list[dict[str, Any]], key: str) -> str | None:
    for row in file_manifest:
        if row.get("dedupe_key") == key or row.get("file_id") == key:
            return row.get("copied_path")
    return None


def asset_destination_dir(output_path: Path, role: str, created_at: str | None) -> Path:
    if role == "knowledge":
        return output_path / FILES_DIR / "Knowledge"
    if role == "generated":
        year, month, _ = year_month(created_at)
        return output_path / FILES_DIR / "Generated" / year / month
    if role == "orphan":
        return output_path / FILES_DIR / "Unlinked"
    year, month, _ = year_month(created_at)
    return output_path / FILES_DIR / "Attachments" / year / month


def deduped_asset_filename(logical_name: str | None, identifier: str | None) -> str:
    logical = safe_filename_preserving_suffix(logical_name or identifier or "file")
    path = Path(logical)
    suffix = path.suffix
    stem = path.stem or "file"
    id_part = short_identifier(identifier, 32)
    return safe_filename_preserving_suffix(f"{stem} - {id_part}{suffix}")


def library_matches_selected_context(
    library: dict[str, Any],
    selected_conversation_ids: set[str],
    selected_gpt_ids: set[str],
) -> bool:
    gizmo_id = extract_id(library.get("gizmo_id"))
    if gizmo_id and gizmo_id in selected_gpt_ids:
        return True
    for key in ("origination_thread_id", "initiating_conversation_id", "conversation_id"):
        value = extract_id(library.get(key))
        if value and value in selected_conversation_ids:
            return True
    return False


def build_knowledge_contexts(
    library_by_file_id: dict[str, dict[str, Any]],
    *,
    selected_conversation_ids: set[str],
    selected_gpt_ids: set[str],
    include_all: bool,
) -> dict[str, dict[str, Any]]:
    contexts: dict[str, dict[str, Any]] = {}
    for library in library_by_file_id.values():
        if not include_all and not library_matches_selected_context(library, selected_conversation_ids, selected_gpt_ids):
            continue
        knowledge_id = extract_id(library.get("knowledge_store_id"))
        if not knowledge_id:
            continue
        ctx = contexts.setdefault(
            knowledge_id,
            {
                "title": f"Knowledge Store - {knowledge_id}",
                "kind": "knowledge_store",
                "context_id": knowledge_id,
                "evidence": "explicit",
                "status": "parsed",
                "conversation_ids": set(),
                "linked_file_count": 0,
            },
        )
        ctx["linked_file_count"] += 1
    return contexts


def emit_context_hubs(
    output_path: Path,
    gpt_contexts: dict[str, dict[str, Any]],
    project_contexts: dict[str, dict[str, Any]],
    knowledge_contexts: dict[str, dict[str, Any]],
    observed_contexts: dict[str, dict[str, Any]],
) -> None:
    buckets = [
        ("GPTs", gpt_contexts, ["openai/context", "openai/gpt"]),
        ("Projects", project_contexts, ["openai/context", "openai/project"]),
        ("Knowledge", knowledge_contexts, ["openai/context", "openai/knowledge"]),
        ("Observed", observed_contexts, ["openai/context", "openai/observed"]),
    ]
    used_names: dict[Path, set[str]] = defaultdict(set)
    for folder, contexts, tags in buckets:
        target_dir = output_path / CONTEXTS_DIR / folder
        target_dir.mkdir(parents=True, exist_ok=True)
        for context in sorted(contexts.values(), key=lambda c: c["title"]):
            name = unique_note_name(f"{safe_filename(context['title'])}.md", used_names[target_dir])
            content = render_context_markdown(
                title=context["title"],
                kind=context["kind"],
                context_id=context["context_id"],
                evidence=context["evidence"],
                status=context["status"],
                conversation_count=len(context.get("conversation_ids") or []),
                linked_file_count=int(context.get("linked_file_count") or 0),
                tags=tags,
                basis=context.get("basis"),
                extra_fields=context_extra_fields(context),
                linked_conversations=context_linked_conversation_lines(context),
                linked_files_by_role=context_linked_files_by_role(context),
            )
            (target_dir / name).write_text(content, encoding="utf-8")


def context_link(conversation: ConversationRecord, kind: str, link: str, evidence: str) -> dict[str, Any]:
    return {
        "conversation_id": conversation.conversation_id,
        "context_kind": kind,
        "context_link": link,
        "evidence": evidence,
        "source_shard": conversation.source_json_shard,
    }


def context_extra_fields(context: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in ("custom_gpt_url", "project_url", "project_url_evidence", "project_instruction_status"):
        if context.get(key) not in (None, "", [], {}):
            fields[key] = context.get(key)
    return fields


def context_url(context_id: str | None) -> str | None:
    if isinstance(context_id, str) and context_id.startswith("g-"):
        return f"https://chatgpt.com/g/{context_id}"
    return None


def emit_base_files(output_path: Path) -> None:
    views_dir = output_path / VIEWS_DIR
    views_dir.mkdir(parents=True, exist_ok=True)
    (views_dir / "Contexts.base").write_text(CONTEXTS_BASE, encoding="utf-8")
    (views_dir / "Needs Context Naming.base").write_text(NEEDS_CONTEXT_NAMING_BASE, encoding="utf-8")
    for name in ("Conversations.base", "Review.base", "Needs File Review.base", "Source Heavy Conversations.base"):
        (views_dir / name).unlink(missing_ok=True)


def emit_dataview_files(output_path: Path) -> None:
    views_dir = output_path / VIEWS_DIR
    views_dir.mkdir(parents=True, exist_ok=True)
    (views_dir / "Conversations - Dataview.md").write_text(CONVERSATIONS_DATAVIEW, encoding="utf-8")
    (views_dir / "Contexts - Dataview.md").write_text(CONTEXTS_DATAVIEW, encoding="utf-8")
    (views_dir / "Review - Dataview.md").write_text(REVIEW_DATAVIEW, encoding="utf-8")
    (views_dir / "Needs File Review - Dataview.md").write_text(NEEDS_FILE_REVIEW_DATAVIEW, encoding="utf-8")
    (views_dir / "Source Heavy Conversations - Dataview.md").write_text(SOURCE_HEAVY_CONVERSATIONS_DATAVIEW, encoding="utf-8")


def render_home() -> str:
    return """# OpenAI Export Pack

## Overview

This pack is generated from an OpenAI/ChatGPT export. Conversation and context notes are readable entry points; JSONL files in `90_Evidence/` keep the machine-readable proof layer.

## Start Here

- [[40_Views/Conversations - Dataview|Conversations]]
- [[40_Views/Contexts - Dataview|Contexts]]
- [[40_Views/Review - Dataview|Review]]
- [[40_Views/Needs Context Naming.base|Needs Context Naming]]
- [[40_Views/Needs File Review - Dataview|Needs File Review]]
- [[40_Views/Source Heavy Conversations - Dataview|Source Heavy Conversations]]
- Machine evidence: `90_Evidence/`

## Editing Rules

- Manual sections are safe to edit.
- Generated sections are bounded by `BEGIN GENERATED` / `END GENERATED` comments.
- Context names are source IDs until you add aliases manually.
"""


CONVERSATIONS_BASE = """filters:
  and:
    - 'type == "openai_conversation"'
views:
  - type: table
    name: Conversations
    order:
      - file.link
      - created_at
      - updated_at
      - reviewed
      - message_count
      - default_model
      - gpts
      - projects
      - space_projects
      - knowledge_stores
      - observed_contexts
      - source_evidence_count
      - tool_evidence_count
      - file_reference_count
      - unique_file_count
      - resolved_file_count
      - unresolved_file_count
      - has_unresolved_files
      - has_warnings
      - context_evidence
      - source_shard
"""


CONTEXTS_BASE = """filters:
  and:
    - 'type == "openai_context"'
views:
  - type: table
    name: Contexts
    order:
      - file.link
      - kind
      - evidence
      - status
      - conversation_count
      - linked_file_count
"""


REVIEW_BASE = """filters:
  and:
    - 'type == "openai_conversation"'
    - 'reviewed == false'
    - or:
        - 'has_unresolved_files == true'
        - 'has_warnings == true'
        - 'context_evidence == "unknown"'
views:
  - type: table
    name: Review
    order:
      - file.link
      - created_at
      - updated_at
      - unresolved_file_count
      - has_warnings
      - context_evidence
      - gpts
      - projects
      - space_projects
      - knowledge_stores
"""


NEEDS_CONTEXT_NAMING_BASE = """filters:
  and:
    - 'type == "openai_context"'
    - 'aliases.isEmpty()'
views:
  - type: table
    name: Needs Context Naming
    order:
      - file.link
      - kind
      - context_id
      - evidence
      - conversation_count
      - linked_file_count
"""


NEEDS_FILE_REVIEW_BASE = """filters:
  and:
    - 'type == "openai_conversation"'
    - or:
        - 'unresolved_file_count > 0'
        - 'file_reference_count > 10'
views:
  - type: table
    name: Needs File Review
    order:
      - file.link
      - created_at
      - file_reference_count
      - unique_file_count
      - resolved_file_count
      - unresolved_file_count
      - space_projects
      - gpts
"""


SOURCE_HEAVY_CONVERSATIONS_BASE = """filters:
  and:
    - 'type == "openai_conversation"'
    - or:
        - 'source_evidence_count > 0'
        - 'tool_evidence_count > 0'
views:
  - type: table
    name: Source Heavy Conversations
    order:
      - file.link
      - created_at
      - source_evidence_count
      - tool_evidence_count
      - knowledge_stores
      - space_projects
      - gpts
"""


CONVERSATIONS_DATAVIEW = """# Conversations

```dataview
TABLE created_at, updated_at, message_count, models_seen, gpts, space_projects, knowledge_stores, unresolved_file_count
WHERE type = "openai_conversation"
SORT created_at DESC
```
"""


REVIEW_DATAVIEW = """# Review

```dataview
TABLE created_at, updated_at, unresolved_file_count, gpts, space_projects, knowledge_stores
WHERE type = "openai_conversation"
  AND reviewed = false
  AND (has_unresolved_files = true OR has_warnings = true OR context_evidence = "unknown")
SORT created_at DESC
```
"""


NEEDS_FILE_REVIEW_DATAVIEW = """# Needs File Review

```dataview
TABLE created_at, file_reference_count, unique_file_count, resolved_file_count, unresolved_file_count, space_projects, gpts
WHERE type = "openai_conversation"
  AND (unresolved_file_count > 0 OR file_reference_count > 10)
SORT unresolved_file_count DESC, file_reference_count DESC
```
"""


SOURCE_HEAVY_CONVERSATIONS_DATAVIEW = """# Source Heavy Conversations

```dataview
TABLE created_at, source_evidence_count, tool_evidence_count, knowledge_stores, space_projects, gpts
WHERE type = "openai_conversation"
  AND (source_evidence_count > 0 OR tool_evidence_count > 0)
SORT source_evidence_count DESC, tool_evidence_count DESC
```
"""


CONTEXTS_DATAVIEW = """# Contexts

```dataview
TABLE kind, evidence, status, conversation_count, linked_file_count
WHERE type = "openai_context"
SORT kind ASC, file.name ASC
```
"""


def source_part(source_archive_path: str) -> str | None:
    if "::" not in source_archive_path:
        return None
    return Path(source_archive_path.split("::", 1)[0]).name


def configure_logging(output_path: Path, *, dry_run: bool, verbose: bool, markdown_profile: str) -> logging.Logger:
    logger = logging.getLogger("openai_export_parser")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    stream.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.addHandler(stream)
    if not dry_run:
        log_dir = output_path / ("_logs" if markdown_profile == "readable" else "logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "parse.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)
    return logger


def first_physical_for_path(members: list[ArchiveMember], archive_path: str):
    for member in members:
        if member.archive_path == archive_path:
            return member
    return None


def build_audit(
    *,
    shard_members: list[ArchiveMember],
    conversations: list[ConversationRecord],
    assets: list[AssetRecord],
    physical_members: list[ArchiveMember],
    orphan_assets: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    by_status = defaultdict(int)
    by_provenance = defaultdict(int)
    for asset in assets:
        by_status[asset.asset_status] += 1
        by_provenance[asset.provenance_status] += 1
    unresolved_assets = [asset for asset in assets if is_unresolved_asset_status(asset.asset_status)]
    return {
        "counts": {
            "conversation_shards": len(shard_members),
            "conversations": len(conversations),
            "file_references": len(assets),
            "found_file_references": by_status["found"],
            "missing_file_references": by_status["missing"],
            "collision_file_references": by_status["collision"],
            "physical_assets": len(physical_members),
            "orphan_assets": len(orphan_assets),
        },
        "asset_reference_status_counts": dict(sorted(by_status.items())),
        "asset_provenance_status_counts": dict(sorted(by_provenance.items())),
        "warnings": warnings,
        "unknowns": {
            "unknown_origin_references": sum(1 for asset in assets if asset.origin_classification == "unknown"),
            "unknown_provenance_references": sum(1 for asset in assets if asset.provenance_status == "unknown"),
        },
        "unresolved_link_count": len(unresolved_assets),
        "orphan_asset_count": len(orphan_assets),
        "collisions": [],
        "evidence_files": {
            "unresolved_links": "90_Evidence/asset_links.jsonl",
            "orphan_assets": "90_Evidence/unlinked_assets.jsonl",
        },
    }


def build_payload_materialization_summary(
    dispositions: PayloadDispositionResult,
    file_manifest: list[dict[str, Any]],
    orphan_assets: list[dict[str, Any]],
) -> dict[str, int]:
    """Report physical members separately from the one readable copy per SHA.

    ``file_manifest`` can legitimately include several logical references to a
    single copied payload.  The copied source path, rather than manifest-row
    count, is therefore the reliable materialization unit.
    """
    materialized_rows = [
        row
        for row in [*file_manifest, *orphan_assets]
        if row.get("copy_status") == "copied" and row.get("materialized_from_archive_path")
    ]
    canonical_sources = {
        str(row["materialized_from_archive_path"])
        for row in materialized_rows
    }
    true_unlinked_statuses = {
        "unlinked_payload",
        "duplicate_of_unreferenced_payload",
        "unhashable_payload",
    }
    unlinked_sources = {
        str(row["materialized_from_archive_path"])
        for row in materialized_rows
        if row.get("payload_disposition_status") in true_unlinked_statuses
    }
    return {
        "physical_payloads_observed": len(dispositions.records),
        "canonical_payloads_materialized": len(canonical_sources),
        "physical_copies_not_materialized": len(dispositions.records) - len(canonical_sources),
        "true_unlinked_payloads_materialized_in_unlinked": len(unlinked_sources),
    }


def build_sandbox_links_by_conversation(
    reference_result: ReferenceExtractionResult,
    physical_resolution_result: PhysicalResolutionResult,
    materialized_rows: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, str | None]]]:
    """Provide the renderer only with exact, already-materialized sandbox links.

    This is a display index: references and resolution evidence remain unchanged.
    A missing copied path intentionally becomes a conversation-link fallback in
    Markdown rather than a guessed local file link.
    """
    copied_by_archive_path: dict[str, str] = {}
    for row in materialized_rows:
        copied_path = row.get("copied_path") or row.get("duplicate_payload_copied_path")
        if not isinstance(copied_path, str) or not copied_path:
            continue
        for key in ("archive_path", "physical_archive_path", "materialized_from_archive_path", "canonical_archive_path"):
            archive_path = row.get(key)
            if isinstance(archive_path, str) and archive_path:
                copied_by_archive_path.setdefault(archive_path, copied_path)
    resolutions_by_reference_id = {
        row.reference_id: row
        for row in physical_resolution_result.records
    }
    result: dict[str, dict[str, dict[str, str | None]]] = {}
    for reference in reference_result.records:
        if not reference.raw_identifier.startswith("sandbox:") or not reference.conversation_id or not reference.message_id:
            continue
        resolution = resolutions_by_reference_id.get(reference.reference_id)
        copied_path = copied_by_archive_path.get(resolution.selected_archive_path) if resolution and resolution.selected_archive_path else None
        result.setdefault(reference.conversation_id, {}).setdefault(reference.message_id, {})[reference.raw_identifier] = copied_path
    return result


def emit_inventory_evidence(output_dir: Path, inventory: ExportInventory) -> None:
    """Report the canonical inventory without giving reporting concerns to Inventory."""
    append_jsonl(output_dir / "inventory.jsonl", [entry.to_dict() for entry in inventory.entries])
    append_jsonl(output_dir / "inventory_errors.jsonl", [error.to_dict() for error in inventory.errors])
    write_json(output_dir / "inventory_summary.json", inventory.summary_dict())


def emit_reference_evidence(output_dir: Path, result: ReferenceExtractionResult) -> None:
    append_jsonl(output_dir / "references.jsonl", [record.to_dict() for record in result.records])
    write_json(output_dir / "reference_summary.json", result.summary_dict())


def emit_physical_resolution_evidence(output_dir: Path, result: PhysicalResolutionResult) -> None:
    append_jsonl(output_dir / "physical_resolutions.jsonl", [record.to_dict() for record in result.records])
    write_json(output_dir / "physical_resolution_summary.json", result.summary_dict())


def emit_payload_classification_evidence(output_dir: Path, result: PayloadDispositionResult) -> None:
    append_jsonl(output_dir / "payload_dispositions.jsonl", [record.to_dict() for record in result.records])
    write_json(output_dir / "payload_disposition_summary.json", result.summary_dict())


def emit_asset_resolution_migration_evidence(output_dir: Path, result: AssetResolutionMigrationResult) -> None:
    append_jsonl(
        output_dir / "asset_resolution_comparisons.jsonl",
        [comparison.to_dict() for comparison in result.comparisons],
    )
    write_json(output_dir / "asset_resolution_migration_summary.json", result.summary_dict())


def emit_runtime_artifact_evidence(output_dir: Path, result: RuntimeArtifactLinkingResult) -> None:
    append_jsonl(output_dir / "runtime_artifacts.jsonl", [record.to_dict() for record in result.records])
    write_json(output_dir / "runtime_artifacts_summary.json", result.summary_dict())


def emit_embedded_historical_export_evidence(output_dir: Path, result: EmbeddedHistoricalExportResult) -> None:
    append_jsonl(output_dir / "historical_export_snapshots.jsonl", [row.to_dict() for row in result.snapshots])
    append_jsonl(output_dir / "historical_conversation_comparisons.jsonl", [row.to_dict() for row in result.comparisons])
    append_jsonl(output_dir / "historical_messages.jsonl", [row.to_dict() for row in result.messages])
    append_jsonl(output_dir / "historical_signals.jsonl", [row.to_dict() for row in result.signals])
    write_json(output_dir / "historical_export_summary.json", result.summary_dict())
