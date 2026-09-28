#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


EVIDENCE_DIR = "90_Evidence"
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
CONVERSATION_QUERY_FIELDS = (
    "conversation_id",
    "created_at",
    "updated_at",
    "month_key",
    "default_model",
    "models_seen",
    "gpts",
    "space_projects",
    "knowledge_stores",
    "is_archived",
    "message_count",
    "file_reference_count",
    "unique_file_count",
    "resolved_file_count",
    "unresolved_file_count",
    "source_evidence_count",
    "tool_evidence_count",
    "has_unresolved_files",
    "has_warnings",
    "context_evidence",
    "reviewed",
    "needs_review",
    "note_path",
)


class EvidenceQueryError(ValueError):
    """A pack cannot be projected through the closed analytical query."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query JSONL evidence emitted by the OpenAI export parser.")
    parser.add_argument("--pack", required=True, type=Path, help="Path to an emitted Obsidian export pack")
    subparsers = parser.add_subparsers(dest="command", required=True)
    conversation = subparsers.add_parser("conversation", help="Summarize one conversation and its evidence rows")
    conversation.add_argument("conversation_id", help="Conversation ID to inspect")
    conversations = subparsers.add_parser("conversations", help="Stream the closed analytical conversation projection")
    conversations.add_argument("--month", action="append", default=[], help="Only emit this YYYY-MM month; repeatable")
    conversations.add_argument("--needs-review", action="store_true", help="Only emit conversations needing review")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "conversation":
        return query_conversation(args.pack, args.conversation_id)
    if args.command == "conversations":
        return query_conversations(args.pack, months=args.month, needs_review=args.needs_review)
    return 2


def query_conversation(pack: Path, conversation_id: str) -> int:
    evidence = pack / EVIDENCE_DIR
    pack_manifest = read_json(evidence / "pack_manifest.json")
    conversations = read_jsonl(evidence / "conversations.jsonl")
    conversation = next((row for row in conversations if row.get("conversation_id") == conversation_id), None)
    if not conversation:
        print(f"Conversation not found: {conversation_id}", file=sys.stderr)
        return 1

    assets = [row for row in read_jsonl(evidence / "asset_links.jsonl") if row.get("conversation_id") == conversation_id]
    sources = [row for row in read_jsonl(evidence / "message_sources.jsonl") if row.get("conversation_id") == conversation_id]
    contexts = [row for row in read_jsonl(evidence / "context_links.jsonl") if row.get("conversation_id") == conversation_id]
    tool_events = [row for row in read_jsonl(evidence / "tool_events.jsonl") if row.get("conversation_id") == conversation_id]
    textdocs = [row for row in read_jsonl(evidence / "textdocs.jsonl") if row.get("conversation_id") == conversation_id]
    runtime_artifacts = [
        row for row in read_jsonl(evidence / "runtime_artifacts.jsonl") if row.get("conversation_id") == conversation_id
    ]
    historical_comparisons = [
        row for row in read_jsonl(evidence / "historical_conversation_comparisons.jsonl") if row.get("conversation_id") == conversation_id
    ]
    historical_messages = [
        row for row in read_jsonl(evidence / "historical_messages.jsonl") if row.get("conversation_id") == conversation_id
    ]
    historical_signals = [
        row for row in read_jsonl(evidence / "historical_signals.jsonl") if row.get("conversation_id") == conversation_id
    ]
    technical_events = [
        row for row in read_jsonl(evidence / "technical_events.jsonl") if row.get("conversation_id") == conversation_id
    ]
    technical_comparisons = [
        row for row in read_jsonl(evidence / "technical_event_comparisons.jsonl") if row.get("conversation_id") == conversation_id
    ]
    logical_entity_observations = [
        row for row in read_jsonl(evidence / "logical_entity_observations.jsonl") if row.get("conversation_id") == conversation_id
    ]
    logical_entity_ids = {row.get("logical_entity_id") for row in logical_entity_observations if isinstance(row.get("logical_entity_id"), str)}
    logical_entities = [
        row for row in read_jsonl(evidence / "logical_entities.jsonl") if row.get("logical_entity_id") in logical_entity_ids
    ]
    context_resource_edges = [
        row for row in read_jsonl(evidence / "context_resource_edges.jsonl") if row.get("conversation_id") == conversation_id
    ]
    context_resource_profile_ids = {
        row.get("context_profile_id") for row in context_resource_edges if isinstance(row.get("context_profile_id"), str)
    }
    context_resource_metrics = [
        row
        for row in read_jsonl(evidence / "context_resource_usage_metrics.jsonl")
        if row.get("context_profile_id") in context_resource_profile_ids and conversation_id in (row.get("conversation_ids") or [])
    ]

    print("Conversation")
    print(f"- Title: {conversation.get('title') or 'Untitled conversation'}")
    print(f"- ID: {conversation_id}")
    print(f"- Created: {conversation.get('created_at') or 'unknown'}")
    print(f"- Updated: {conversation.get('updated_at') or 'unknown'}")
    print("")

    if pack_manifest:
        print("Pack")
        print(f"- Schema: {pack_manifest.get('schema_version') or 'unknown'}")
        print(f"- Profile: {pack_manifest.get('markdown_profile') or 'unknown'}")
        generated_at = pack_manifest.get("generated_at")
        if generated_at:
            print(f"- Generated: {generated_at}")
        print("")

    print("Contexts")
    for key in ("gpts", "projects", "space_projects", "knowledge_stores", "observed_contexts"):
        values = conversation.get(key) or []
        print(f"- {key}: {', '.join(values) if values else 'none'}")
    if contexts:
        explicit = Counter(row.get("context_kind") or "unknown" for row in contexts)
        print(f"- context_links: {format_counter(explicit)}")
    print("")

    print("Counts")
    print(f"- Messages: {conversation.get('message_count') or 0}")
    print(f"- File references: {len(assets)}")
    print(f"- Source evidence rows: {len(sources)}")
    print(f"- Tool/retrieval evidence rows: {len(tool_events)}")
    print(f"- Textdoc/canvas evidence rows: {len(textdocs)}")
    print(f"- Python runtime artifacts: {len(runtime_artifacts)}")
    print(f"- Historical export comparisons: {len(historical_comparisons)}")
    print(f"- Historical-only messages: {sum(row.get('message_status') == 'historical_only' for row in historical_messages)}")
    print(f"- Historical technical signals: {len(historical_signals)}")
    print(f"- Technical events: {len(technical_events)}")
    print(f"- Logical entities (explicit identifiers only): {len(logical_entities)}")
    print("")

    print("Sources")
    print_rows_by_counter(sources, "source_kind")
    print_examples(sources, ("title", "url", "snippet"))
    print("")

    print("Assets")
    print_rows_by_counter(assets, "asset_status")
    print_examples(assets, ("reconstructed_filename", "raw_file_id", "copied_pack_path", "proof_path"))
    print("")

    print("Python Runtime Artifacts")
    print_rows_by_counter(runtime_artifacts, "relation_status")
    print_examples(runtime_artifacts, ("filename", "execution_message_id", "runtime_path", "copied_pack_path"))
    print("")

    print("Historical Export Evidence (auxiliary; not merged)")
    print_rows_by_counter(historical_messages, "message_status")
    print_rows_by_counter(historical_signals, "signal_kind")
    print_examples(historical_comparisons, ("historical_conversations_archive_path", "historical_only_message_count", "shared_message_count"))
    print_examples(historical_signals, ("signal_kind", "node_id", "value_summary", "proof_path"))
    print("")

    print("Technical Events (observations only; no physical resolution)")
    print_rows_by_counter(technical_events, "source_export")
    print_rows_by_counter(technical_events, "event_family")
    print_rows_by_counter(technical_comparisons, "comparison_status")
    print_examples(technical_events, ("event_family", "operation_observed", "recipient", "proof_path"))
    print("")

    print("Logical Entities (derived identifiers only; no payload relation)")
    print_rows_by_counter(logical_entities, "entity_type")
    print_rows_by_counter(logical_entities, "source_presence_status")
    print_examples(logical_entities, ("entity_type", "identifier_kind", "explicit_identifier", "logical_entity_id"))
    print("")

    print("Context Resource Usage (observations only; no attachment or payload claim)")
    print(f"- Graph edges for conversation: {len(context_resource_edges)}")
    print(f"- Resource metrics involving conversation: {len(context_resource_metrics)}")
    print_rows_by_counter(context_resource_metrics, "forensic_status")
    print_examples(context_resource_metrics, ("resource_name", "distinct_conversation_count", "relative_conversation_frequency"))
    print("")

    print("Textdocs & Canvas")
    print_rows_by_counter(textdocs, "status")
    print_examples(textdocs, ("status", "title", "textdoc_id", "copied_pack_path", "proof_path"))
    print("")

    print("Tools & Retrieval")
    print_rows_by_counter(tool_events, "event_kind")
    print_examples(tool_events, ("event_kind", "tool_name", "title", "url", "proof_path"))
    print("")

    print("Evidence pointers")
    for name in (
        "conversations.jsonl",
        "messages.jsonl",
        "message_sources.jsonl",
        "tool_events.jsonl",
        "textdocs.jsonl",
        "asset_links.jsonl",
        "runtime_artifacts.jsonl",
        "historical_export_snapshots.jsonl",
        "historical_conversation_comparisons.jsonl",
        "historical_messages.jsonl",
        "historical_signals.jsonl",
        "technical_events.jsonl",
        "technical_event_comparisons.jsonl",
        "technical_event_unknowns.jsonl",
        "logical_entities.jsonl",
        "logical_entity_observations.jsonl",
        "logical_entity_unmaterialized_events.jsonl",
        "logical_entity_taxonomy_migration.jsonl",
        "logical_entity_summary.json",
        "context_resource_nodes.jsonl",
        "context_resource_edges.jsonl",
        "context_resource_owner_claims.jsonl",
        "context_resource_usage_metrics.jsonl",
        "context_resource_cooccurrences.jsonl",
        "context_resource_usage_summary.json",
        "context_links.jsonl",
        "file_manifest.jsonl",
    ):
        print(f"- {EVIDENCE_DIR}/{name}")
    return 0


def query_conversations(pack: Path, *, months: list[str] | None = None, needs_review: bool = False) -> int:
    """Validate first, then emit deterministic privacy-bounded JSONL."""
    try:
        rows = projected_conversations(pack, months=months, needs_review=needs_review, validate=True)
        payload = "".join(json_dumps(row) + "\n" for row in rows)
    except EvidenceQueryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(payload)
    return 0


def projected_conversations(
    pack: Path,
    *,
    months: list[str] | None = None,
    needs_review: bool = False,
    validate: bool = True,
) -> list[dict[str, Any]]:
    selected_months = validate_months(months or [])
    if validate:
        from .validation import validate_pack

        report = validate_pack(pack)
        if report["errors"]:
            raise EvidenceQueryError("pack validation failed: " + "; ".join(report["errors"]))

    evidence = pack / EVIDENCE_DIR
    manifest = read_json_strict(evidence / "pack_manifest.json")
    locator = read_declared_locator(pack, manifest)
    rows: list[dict[str, Any]] = []
    conversations_path = evidence / "conversations.jsonl"
    try:
        stream = conversations_path.open("r", encoding="utf-8")
    except OSError as exc:
        raise EvidenceQueryError(f"cannot read conversations evidence: {exc}") from exc
    with stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                source = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvidenceQueryError(f"conversations.jsonl:{line_number} invalid JSON: {exc}") from exc
            if not isinstance(source, dict):
                raise EvidenceQueryError(f"conversations.jsonl:{line_number} row is not an object")
            row = analytical_conversation_row(source, locator)
            if selected_months and row["month_key"] not in selected_months:
                continue
            if needs_review and not row["needs_review"]:
                continue
            rows.append(row)
    return sorted(rows, key=lambda row: (row["created_at"] or "", row["conversation_id"]))


def analytical_conversation_row(source: dict[str, Any], locator: dict[str, str]) -> dict[str, Any]:
    conversation_id = source.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id:
        raise EvidenceQueryError("conversation row has no valid conversation_id")
    required_lists = ("models_seen", "gpts", "space_projects", "knowledge_stores")
    required_counts = (
        "message_count",
        "file_reference_count",
        "unique_file_count",
        "resolved_file_count",
        "unresolved_file_count",
        "source_evidence_count",
        "tool_evidence_count",
    )
    for key in required_lists:
        if not isinstance(source.get(key), list) or not all(isinstance(value, str) for value in source[key]):
            raise EvidenceQueryError(f"conversation {conversation_id} {key} must be a string list")
    for key in required_counts:
        if not isinstance(source.get(key), int) or isinstance(source.get(key), bool) or source[key] < 0:
            raise EvidenceQueryError(f"conversation {conversation_id} {key} must be a non-negative integer")
    for key in ("has_unresolved_files", "has_warnings", "reviewed"):
        if not isinstance(source.get(key), bool):
            raise EvidenceQueryError(f"conversation {conversation_id} {key} must be a boolean")
    for key in ("created_at", "updated_at"):
        if source.get(key) is not None and not isinstance(source.get(key), str):
            raise EvidenceQueryError(f"conversation {conversation_id} {key} must be a string or null")
    if not isinstance(source.get("month_key"), str):
        raise EvidenceQueryError(f"conversation {conversation_id} month_key must be a string")
    if source.get("context_evidence") not in {"mixed", "explicit", "observed", "unknown"}:
        raise EvidenceQueryError(f"conversation {conversation_id} has invalid context_evidence")
    if source.get("default_model") is not None and not isinstance(source.get("default_model"), str):
        raise EvidenceQueryError(f"conversation {conversation_id} default_model must be a string or null")
    if source.get("is_archived") is not None and not isinstance(source.get("is_archived"), bool):
        raise EvidenceQueryError(f"conversation {conversation_id} is_archived must be a boolean or null")

    review_required = not source["reviewed"] and (
        source["has_unresolved_files"] or source["has_warnings"] or source["context_evidence"] == "unknown"
    )
    row = {
        key: source.get(key)
        for key in CONVERSATION_QUERY_FIELDS
        if key not in {"needs_review", "note_path"}
    }
    row["needs_review"] = review_required
    row["note_path"] = locator.get(conversation_id)
    if tuple(row) != CONVERSATION_QUERY_FIELDS:
        raise AssertionError("closed analytical conversation field order drifted")
    return row


def validate_months(months: list[str]) -> set[str]:
    invalid = [month for month in months if not isinstance(month, str) or not MONTH_RE.fullmatch(month)]
    if invalid:
        raise EvidenceQueryError(f"invalid --month value(s): {', '.join(map(str, invalid))}")
    return set(months)


def read_declared_locator(pack: Path, manifest: dict[str, Any]) -> dict[str, str]:
    navigation = manifest.get("navigation")
    descriptor = navigation.get("conversation_note_locator") if isinstance(navigation, dict) else None
    if not isinstance(descriptor, dict):
        raise EvidenceQueryError("pack manifest has no conversation locator descriptor")
    state = descriptor.get("state")
    if state == "not_emitted":
        return {}
    if state != "emitted":
        raise EvidenceQueryError(f"invalid conversation locator state: {state!r}")
    from .conversation_note_locator import CONVERSATION_NOTE_LOCATOR_RELATIVE_PATH, LOCATOR_RECORD_FIELDS, locator_path_error

    path = pack / CONVERSATION_NOTE_LOCATOR_RELATIVE_PATH
    locator: dict[str, str] = {}
    try:
        stream = path.open("r", encoding="utf-8")
    except OSError as exc:
        raise EvidenceQueryError(f"cannot read declared conversation locator: {exc}") from exc
    with stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvidenceQueryError(f"conversation locator:{line_number} invalid JSON: {exc}") from exc
            if not isinstance(row, dict) or set(row) != LOCATOR_RECORD_FIELDS:
                raise EvidenceQueryError(f"conversation locator:{line_number} has unknown or missing fields")
            conversation_id = row.get("conversation_id")
            note_path = row.get("relative_note_path")
            if not isinstance(conversation_id, str) or not conversation_id or locator_path_error(note_path):
                raise EvidenceQueryError(f"conversation locator:{line_number} is invalid")
            if conversation_id in locator:
                raise EvidenceQueryError(f"conversation locator has duplicate conversation_id: {conversation_id}")
            locator[conversation_id] = note_path
    return locator


def read_json_strict(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceQueryError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceQueryError(f"{path.name} is not an object")
    return value


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def print_rows_by_counter(rows: list[dict[str, Any]], key: str) -> None:
    if not rows:
        print("- none")
        return
    print(f"- {key}: {format_counter(Counter(str(row.get(key) or 'unknown') for row in rows))}")


def format_counter(counter: Counter) -> str:
    return ", ".join(f"{key}={count}" for key, count in sorted(counter.items()))


def print_examples(rows: list[dict[str, Any]], keys: tuple[str, ...], limit: int = 5) -> None:
    for row in rows[:limit]:
        values = [str(row.get(key)) for key in keys if row.get(key) not in (None, "", [], {})]
        if values:
            print(f"- {' · '.join(values)}")


if __name__ == "__main__":
    raise SystemExit(main())
