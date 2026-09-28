from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path

from .evidence_query import query_conversation, query_conversations
from .runner import run_parse
from .archive import ExportArchive
from .technical_events import TechnicalEventExtractor, emit_technical_event_evidence
from .logical_entities import (
    LEGACY_ENTITY_TYPES,
    LogicalEntityConstructor,
    emit_logical_entity_evidence,
    legacy_entities_from_migration_rows,
)
from .candidate_edges import CandidateEdgeConstructor, emit_candidate_edge_evidence
from .context_profiles import (
    ContextProfileConstructor,
    emit_context_profile_evidence,
    emit_context_profile_obsidian_projection,
)
from .context_resource_usage import ContextResourceUsageConstructor, emit_context_resource_usage_evidence
from .validation import validate_pack
from .pack_contract import export_pack_contract, validate_contract_bundle
from .ds_store_hygiene import QUARANTINE_CONFIRMATION, DsStoreHygieneError, quarantine_ds_store
from .analytics.cli import run_inspect as run_analytics_inspect
from .analytics.cli import run_install as run_analytics_install
from .analytics.cli import run_refresh as run_analytics_refresh


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openai-export-obsidian",
        description="Parse and inspect forensic Obsidian packs from ChatGPT/OpenAI export zips.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_parse_parser(subparsers)
    add_validate_parser(subparsers)
    add_query_parser(subparsers)
    add_analytics_parser(subparsers)
    add_technical_events_parser(subparsers)
    add_logical_entities_parser(subparsers)
    add_candidate_edges_parser(subparsers)
    add_context_profiles_parser(subparsers)
    add_context_resource_usage_parser(subparsers)
    add_describe_pack_contract_parser(subparsers)
    add_quarantine_ds_store_parser(subparsers)
    return parser


def add_parse_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("parse", help="Parse an OpenAI export zip into an Obsidian pack")
    parser.add_argument("--input", required=True, type=Path, help="Path to OpenAI export zip")
    parser.add_argument("--output", required=True, type=Path, help="Output directory")
    parser.add_argument("--copy-assets", action="store_true", help="Copy found physical assets into the pack")
    parser.add_argument(
        "--copy-unlinked-assets",
        action="store_true",
        help="Copy physical files not linked to emitted conversations or contexts",
    )
    parser.add_argument("--emit-json", action="store_true", help="Emit JSON and JSONL outputs")
    parser.add_argument("--emit-markdown", action="store_true", help="Emit conversation markdown notes")
    parser.add_argument(
        "--markdown-profile",
        choices=["readable", "readable_compact", "forensic"],
        default="readable_compact",
        help="Markdown output profile (default: readable_compact)",
    )
    parser.add_argument("--emit-bases", action="store_true", help="Emit Obsidian .base views")
    parser.add_argument(
        "--emit-dataview",
        action="store_true",
        help="Emit Markdown pages with Dataview fallback queries",
    )
    parser.add_argument("--dry-run", action="store_true", help="Parse and report counts without writing outputs")
    parser.add_argument(
        "--update-existing-pack",
        action="store_true",
        help="When output already exists, preserve contract-defined manual note sections and replace generated blocks",
    )
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parser.add_argument(
        "--conversation-id",
        action="append",
        default=[],
        help="Only emit this conversation ID; repeat for multiple IDs",
    )
    parser.add_argument(
        "--month",
        action="append",
        default=[],
        help="Only emit conversations created in this YYYY-MM month; repeat for multiple months",
    )
    parser.add_argument("--max-conversations", type=int, default=None, help="Only emit the first N parsed conversations")
    parser.set_defaults(func=run_parse_command)


def add_validate_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("validate", help="Validate an emitted Obsidian pack")
    parser.add_argument("--pack", required=True, type=Path, help="Path to an emitted Obsidian export pack")
    parser.set_defaults(func=run_validate_command)


def add_query_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("query", help="Query JSONL evidence from an emitted pack")
    query_subparsers = parser.add_subparsers(dest="query_command", required=True)
    conversation = query_subparsers.add_parser("conversation", help="Summarize one conversation and its evidence rows")
    conversation.add_argument("--pack", required=True, type=Path, help="Path to an emitted Obsidian export pack")
    conversation.add_argument("conversation_id", help="Conversation ID to inspect")
    conversation.set_defaults(func=run_query_conversation_command)
    conversations = query_subparsers.add_parser(
        "conversations",
        help="Emit the closed analytical conversation projection as deterministic JSONL",
    )
    conversations.add_argument("--pack", required=True, type=Path, help="Path to an emitted Obsidian export pack")
    conversations.add_argument("--month", action="append", default=[], help="Only emit this YYYY-MM month; repeatable")
    conversations.add_argument("--needs-review", action="store_true", help="Only emit conversations needing review")
    conversations.set_defaults(func=run_query_conversations_command)


def add_analytics_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("analytics", help="Inspect and maintain a separate analytical application")
    analytics_subparsers = parser.add_subparsers(dest="analytics_command", required=True)
    for name, help_text, handler in (
        ("inspect", "Inspect validation, installation and cache freshness without writing", run_analytics_inspect_command),
        ("install", "Install the versioned analytical application without replacing human state", run_analytics_install_command),
        ("refresh", "Transactionally rebuild only the analytical cache", run_analytics_refresh_command),
    ):
        command = analytics_subparsers.add_parser(name, help=help_text)
        command.add_argument("--pack", required=True, type=Path, help="Path to a validated Parser pack")
        command.add_argument("--output", required=True, type=Path, help="Separate analytical destination in the same vault")
        command.set_defaults(func=handler)


def add_technical_events_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "technical-events",
        help="Stream technical-event evidence from an export into an existing complete pack",
    )
    parser.add_argument("--input", required=True, type=Path, help="Path to the OpenAI export ZIP")
    parser.add_argument("--pack", required=True, type=Path, help="Existing complete pack to enrich")
    parser.set_defaults(func=run_technical_events_command)


def add_logical_entities_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "logical-entities",
        help="Construct logical entities from technical-event evidence in an existing pack",
    )
    parser.add_argument("--pack", required=True, type=Path, help="Existing pack containing technical_events.jsonl")
    parser.set_defaults(func=run_logical_entities_command)


def add_candidate_edges_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("candidate-edges", help="Build non-canonical provenance candidates from an existing pack")
    parser.add_argument("--pack", required=True, type=Path, help="Existing pack with logical entity and structured evidence")
    parser.set_defaults(func=run_candidate_edges_command)


def add_context_profiles_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "context-profiles",
        help="Reconstruct non-canonical Gizmo and Project context profiles from existing pack evidence",
    )
    parser.add_argument("--pack", required=True, type=Path, help="Existing pack containing structured context evidence")
    parser.add_argument(
        "--emit-obsidian",
        action="store_true",
        help="Emit separate forensic profile notes without replacing existing context hubs",
    )
    parser.set_defaults(func=run_context_profiles_command)


def add_context_resource_usage_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "context-resource-usage",
        help="Build an observation-only GPT/project resource usage graph from existing pack evidence",
    )
    parser.add_argument("--pack", required=True, type=Path, help="Existing pack containing context profile evidence")
    parser.add_argument(
        "--owner-confirmations",
        type=Path,
        default=None,
        help="Optional JSONL of explicit owner confirmations; never inferred from Markdown or export usage",
    )
    parser.set_defaults(func=run_context_resource_usage_command)


def add_describe_pack_contract_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "describe-pack-contract",
        help="Export the public, synthetic contract for the readable parser pack",
    )
    parser.add_argument("--output", required=True, type=Path, help="New contract bundle directory")
    parser.set_defaults(func=run_describe_pack_contract_command)


def add_quarantine_ds_store_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "quarantine-ds-store",
        help="Move exact regular macOS .DS_Store files from one pack into a new external quarantine",
    )
    parser.add_argument("--pack", required=True, type=Path, help="Existing pack to inspect; never regenerated")
    parser.add_argument("--quarantine", required=True, type=Path, help="New external quarantine directory")
    parser.add_argument(
        "--confirm",
        default=None,
        metavar="PHRASE",
        help=f"Required for mutation: {QUARANTINE_CONFIRMATION}",
    )
    parser.add_argument("--dry-run", action="store_true", help="Inspect metadata only; do not create or move anything")
    parser.set_defaults(func=run_quarantine_ds_store_command)


def run_parse_command(args: argparse.Namespace) -> int:
    emit_json = args.emit_json or not args.emit_markdown
    emit_markdown = args.emit_markdown or not args.emit_json
    result = run_parse(
        args.input,
        args.output,
        copy_assets=args.copy_assets,
        copy_unlinked_assets=args.copy_unlinked_assets,
        emit_json=emit_json,
        emit_markdown=emit_markdown,
        emit_bases=args.emit_bases,
        emit_dataview=args.emit_dataview,
        dry_run=args.dry_run,
        verbose=args.verbose,
        conversation_ids=set(args.conversation_id) if args.conversation_id else None,
        months=set(args.month) if args.month else None,
        max_conversations=args.max_conversations,
        markdown_profile=args.markdown_profile,
        update_existing_pack=args.update_existing_pack,
    )
    print(f"Conversations: {result['conversation_count']}")
    print(f"File references: {result['file_reference_count']}")
    print(f"Output: {result['output_path']}")
    return 0


def run_validate_command(args: argparse.Namespace) -> int:
    report = validate_pack(args.pack)
    for warning in report["warnings"]:
        print(f"WARN: {warning}")
    for error in report["errors"]:
        print(f"ERROR: {error}")
    if report["errors"]:
        print(f"Pack validation FAILED: {len(report['errors'])} error(s), {len(report['warnings'])} warning(s)")
        return 1
    print(
        f"Pack validation OK: {report['counts']['conversations']} conversation(s), "
        f"{report['counts']['messages']} message row(s)"
    )
    return 0


def run_describe_pack_contract_command(args: argparse.Namespace) -> int:
    output = export_pack_contract(args.output)
    errors = validate_contract_bundle(output)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Parser pack contract exported and verified: {output}")
    return 0


def run_quarantine_ds_store_command(args: argparse.Namespace) -> int:
    try:
        report = quarantine_ds_store(
            args.pack,
            args.quarantine,
            confirmation=args.confirm,
            dry_run=args.dry_run,
        )
    except DsStoreHygieneError as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


def run_query_conversation_command(args: argparse.Namespace) -> int:
    return query_conversation(args.pack, args.conversation_id)


def run_query_conversations_command(args: argparse.Namespace) -> int:
    return query_conversations(args.pack, months=args.month, needs_review=args.needs_review)


def run_analytics_inspect_command(args: argparse.Namespace) -> int:
    return run_analytics_inspect(args.pack, args.output)


def run_analytics_install_command(args: argparse.Namespace) -> int:
    return run_analytics_install(args.pack, args.output)


def run_analytics_refresh_command(args: argparse.Namespace) -> int:
    return run_analytics_refresh(args.pack, args.output)


def run_technical_events_command(args: argparse.Namespace) -> int:
    evidence = args.pack / "90_Evidence"
    conversations_path = evidence / "conversations.jsonl"
    if not conversations_path.exists():
        raise ValueError(f"missing pack conversation evidence: {conversations_path}")
    conversation_ids: set[str] = set()
    for line in conversations_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict) and isinstance(row.get("conversation_id"), str):
            conversation_ids.add(row["conversation_id"])
    runtime_path = evidence / "runtime_artifacts.jsonl"
    runtime_artifacts = [json.loads(line) for line in runtime_path.read_text(encoding="utf-8").splitlines() if line.strip()] if runtime_path.exists() else []
    started = time.perf_counter()
    archive = ExportArchive(args.input)
    result = TechnicalEventExtractor(archive, archive.inventory).extract_streaming(
        conversation_ids, runtime_artifacts=runtime_artifacts
    )
    elapsed_seconds = round(time.perf_counter() - started, 3)
    max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    emit_technical_event_evidence(
        evidence,
        result,
        execution={
            "mode": "streaming_existing_pack",
            "selected_conversations": len(conversation_ids),
            "elapsed_seconds": elapsed_seconds,
            "peak_rss_raw": max_rss,
            "peak_rss_unit": "bytes_on_macos",
        },
    )
    print(f"Technical events: {len(result.records)}")
    print(f"Comparisons: {len(result.comparisons)}")
    print(f"Elapsed seconds: {elapsed_seconds}")
    return 0


def run_logical_entities_command(args: argparse.Namespace) -> int:
    evidence = args.pack / "90_Evidence"
    events_path = evidence / "technical_events.jsonl"
    if not events_path.exists():
        raise ValueError(f"missing technical-event evidence: {events_path}")
    started = time.perf_counter()
    events = read_jsonl_objects(events_path)
    existing_entities = read_jsonl_objects(evidence / "logical_entities.jsonl")
    previous_entities = existing_entities
    if existing_entities and not any(row.get("entity_type") in LEGACY_ENTITY_TYPES for row in existing_entities):
        previous_entities = legacy_entities_from_migration_rows(
            read_jsonl_objects(evidence / "logical_entity_taxonomy_migration.jsonl")
        )
    result = LogicalEntityConstructor().construct(events, previous_entities=previous_entities)
    elapsed_seconds = round(time.perf_counter() - started, 3)
    max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    emit_logical_entity_evidence(
        evidence,
        result,
        execution={
            "mode": "derived_from_existing_technical_event_evidence",
            "technical_events_read": len(events),
            "elapsed_seconds": elapsed_seconds,
            "peak_rss_raw": max_rss,
            "peak_rss_unit": "bytes_on_macos",
        },
    )
    print(f"Logical entities: {len(result.records)}")
    print(f"Logical entity observations: {len(result.observations)}")
    print(f"Elapsed seconds: {elapsed_seconds}")
    return 0


def run_candidate_edges_command(args: argparse.Namespace) -> int:
    evidence = args.pack / "90_Evidence"
    if not (evidence / "logical_entities.jsonl").exists():
        raise ValueError(f"missing logical entity evidence: {evidence / 'logical_entities.jsonl'}")
    started = time.perf_counter()
    result = CandidateEdgeConstructor().construct(evidence)
    emit_candidate_edge_evidence(evidence, result, execution={"mode": "existing_pack_structured_evidence_only", "elapsed_seconds": round(time.perf_counter() - started, 3)})
    print(f"Candidate edges: {len(result.records)}")
    print(f"Candidate ambiguities: {sum(row.concurrent_candidate_count > 1 for row in result.records)}")
    return 0


def run_context_profiles_command(args: argparse.Namespace) -> int:
    evidence = args.pack / "90_Evidence"
    conversations_path = evidence / "conversations.jsonl"
    if not conversations_path.exists():
        raise ValueError(f"missing pack conversation evidence: {conversations_path}")
    started = time.perf_counter()
    result = ContextProfileConstructor().construct(evidence)
    elapsed_seconds = round(time.perf_counter() - started, 3)
    emit_context_profile_evidence(
        evidence,
        result,
        execution={
            "mode": "existing_pack_structured_evidence_only",
            "elapsed_seconds": elapsed_seconds,
            "raw_zip_reparsed": False,
        },
    )
    profile_note_count = emit_context_profile_obsidian_projection(args.pack, result) if args.emit_obsidian else 0
    print(f"Gizmo context profiles: {len(result.gizmo_profiles)}")
    print(f"Project context profiles: {len(result.project_profiles)}")
    print(f"Context file memberships: {len(result.memberships)}")
    if args.emit_obsidian:
        print(f"Forensic profile notes: {profile_note_count}")
    print(f"Elapsed seconds: {elapsed_seconds}")
    return 0


def run_context_resource_usage_command(args: argparse.Namespace) -> int:
    evidence = args.pack / "90_Evidence"
    required = evidence / "gizmo_context_profiles.jsonl"
    if not required.exists():
        raise ValueError(f"missing context profile evidence: {required}")
    default_owner_confirmations = evidence / "context_resource_owner_confirmations.jsonl"
    owner_confirmations = args.owner_confirmations or (default_owner_confirmations if default_owner_confirmations.exists() else None)
    started = time.perf_counter()
    result = ContextResourceUsageConstructor().construct(
        evidence,
        owner_confirmations_path=owner_confirmations,
    )
    elapsed_seconds = round(time.perf_counter() - started, 3)
    emit_context_resource_usage_evidence(
        evidence,
        result,
        execution={
            "mode": "existing_pack_structured_evidence_only",
            "elapsed_seconds": elapsed_seconds,
            "owner_confirmations_input": str(owner_confirmations) if owner_confirmations else None,
            "raw_zip_reparsed": False,
        },
    )
    print(f"Context resource nodes: {len(result.nodes)}")
    print(f"Context resource edges: {len(result.edges)}")
    print(f"Context resource usage metrics: {len(result.metrics)}")
    print(f"Owner confirmation claims: {len(result.owner_claims)}")
    print(f"Elapsed seconds: {elapsed_seconds}")
    return 0


def read_jsonl_objects(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
