from __future__ import annotations

import re
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .models import AssetRecord, ConversationRecord, MessageRecord, is_unresolved_asset_status
from .runtime_artifacts import RuntimeArtifactRecord
from .embedded_historical_exports import HistoricalConversationComparison, HistoricalSignalRecord
from .technical_events import TechnicalEventRecord
from .citation_links import (
    CitationLinkRecord,
    citation_source_footnote_id,
    citation_source_key,
    replace_resolved_citation_markers,
)
from .reconstruction import (
    BranchInfo,
    MessageAnnotation,
    add_branch_annotations,
    branch_anchor,
    branch_infos,
    build_message_annotations,
)
from .payload_candidates import UnverifiedPayloadCandidateRecord
from .utils import year_month

SCHEMA_VERSION = "openai-obsidian-pack-v1.4"
SANDBOX_REFERENCE_RE = re.compile(r"sandbox:(?:/+)?/mnt/data/[^\s<>()\[\]{}\"']+")
SANDBOX_MARKDOWN_LINK_RE = re.compile(
    r"(?:👉\s*)?\[(?P<label>[^\]]*)\]\s*\((?P<target>sandbox:(?:/+)?/mnt/data/[^\s<>()\[\]{}\"']+)\)"
)


def render_conversation_markdown(conversation: ConversationRecord, assets: list[AssetRecord]) -> str:
    """Legacy forensic note renderer retained for --markdown-profile forensic."""
    resolved = sum(1 for asset in assets if asset.asset_status == "found")
    missing = sum(1 for asset in assets if is_unresolved_asset_status(asset.asset_status))
    frontmatter = {
        "title": conversation.title,
        "conversation_id": conversation.conversation_id,
        "create_time": conversation.create_time,
        "update_time": conversation.update_time,
        "source_json_file": conversation.source_json_file,
        "source_json_shard": conversation.source_json_shard,
        "chat_url": conversation.chat_url,
        "conversation_template_id": conversation.conversation_template_id,
        "gizmo_type": conversation.gizmo_type,
        "custom_gpt_id": conversation.custom_gpt_id,
        "custom_gpt_name": conversation.custom_gpt_name,
        "custom_gpt_url": conversation.custom_gpt_url,
        "project_id": conversation.project_id,
        "project_name": conversation.project_name,
        "shared_status": conversation.shared_status,
        "message_count": conversation.message_count,
        "asset_count": len(assets),
        "resolved_asset_count": resolved,
        "missing_asset_count": missing,
        "tags": ["openai-export", "forensic-parse"],
    }
    lines = render_frontmatter(frontmatter)
    lines.extend(["", f"# {conversation.title or 'Untitled conversation'}", ""])

    lines.extend(
        [
            "## Metadata Summary",
            "",
            f"- Conversation ID: `{conversation.conversation_id}`",
            f"- Source shard: `{conversation.source_json_shard}`",
            f"- Source archive path: `{conversation.source_archive_path}`",
            f"- Created: `{conversation.create_time or 'unknown'}`",
            f"- Updated: `{conversation.update_time or 'unknown'}`",
            f"- Chat URL: {conversation.chat_url or '`unknown`'}",
            "",
            "## Model / Context Summary",
            "",
            f"- Default model slug: `{conversation.default_model_slug or 'unknown'}`",
            f"- Resolved model slugs: {', '.join(f'`{slug}`' for slug in conversation.resolved_model_slugs) if conversation.resolved_model_slugs else '`unknown`'}",
            f"- Memory scope: `{conversation.memory_scope if conversation.memory_scope is not None else 'unknown'}`",
            "",
            "## Project / GPT Summary",
            "",
            f"- Conversation template ID: `{conversation.conversation_template_id or 'unknown'}`",
            f"- Gizmo type: `{conversation.gizmo_type or 'unknown'}`",
            f"- Custom GPT ID: `{conversation.custom_gpt_id or 'unknown'}`",
            f"- Custom GPT name: `{conversation.custom_gpt_name or 'unknown'}`",
            f"- Custom GPT URL: {conversation.custom_gpt_url or '`unknown`'}",
            f"- Project ID: `{conversation.project_id or 'unknown'}`",
            f"- Project name: `{conversation.project_name or 'unknown'}`",
            f"- Shared status: `{conversation.shared_status}`",
            "",
            "## Assets",
            "",
        ]
    )
    if assets:
        lines.append("| Logical filename | Original .dat | Status | Role | Proof path | Copied file |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for asset in assets:
            note_relative_path = f"assets/{asset.copied_filename}" if asset.copied_filename else None
            copied = f"[{asset.copied_filename}]({note_relative_path})" if note_relative_path else ""
            lines.append(
                "| "
                + " | ".join(
                    [
                        escape_table(asset.reconstructed_filename or ""),
                        escape_table(asset.raw_dat_filename or ""),
                        asset.asset_status,
                        asset.origin_classification,
                        f"`{escape_table(asset.proof_path)}`",
                        escape_table(copied),
                    ]
                )
                + " |"
            )
    else:
        lines.append("No explicit asset references were recovered.")
    lines.extend(["", "## Linked Files", ""])
    linked = [asset for asset in assets if asset.copied_relative_path]
    if linked:
        for asset in linked:
            lines.append(f"- [{asset.copied_filename}](assets/{asset.copied_filename}) from `{asset.physical_archive_path}`")
    else:
        lines.append("No physical asset payloads were copied for this conversation.")

    lines.extend(["", "## Conversation Transcript", ""])
    for message in conversation.messages:
        role = message.author_role or "unknown"
        stamp = message.create_time or "unknown-time"
        lines.extend([f"### {role} · `{message.message_id or message.node_id}` · `{stamp}`", ""])
        lines.append(message.text if message.text else "_No textual content recovered._")
        if message.non_text_parts:
            lines.extend(["", f"_Non-text parts preserved in `evidence/message_index.json`: {len(message.non_text_parts)}._"])
        lines.append("")

    lines.extend(
        [
            "## Evidence / Forensic Notes",
            "",
            "- Full node/message index: `evidence/message_index.json`",
            "- Asset reference evidence: `evidence/asset_links.json`",
            "- Raw conversation metadata: `evidence/raw_metadata.json`",
            "- Missing assets are retained as first-class rows in the asset table and JSONL indexes.",
            "",
            "## Raw Proof Summary",
            "",
            f"- Node count: `{len(conversation.node_index)}`",
            f"- Linear transcript message count: `{len(conversation.messages)}`",
            f"- Asset references: `{len(assets)}`",
            f"- Found assets: `{resolved}`",
            f"- Missing assets: `{missing}`",
            "",
        ]
    )
    return "\n".join(lines)


def render_readable_conversation_markdown(
    conversation: ConversationRecord,
    assets: list[AssetRecord],
    *,
    runtime_artifacts: list[RuntimeArtifactRecord] | None = None,
    historical_comparisons: list[HistoricalConversationComparison] | None = None,
    historical_signals: list[HistoricalSignalRecord] | None = None,
    technical_events: list[TechnicalEventRecord] | None = None,
    gpt_links: list[str],
    project_links: list[str],
    knowledge_links: list[str],
    observed_links: list[str],
    context_evidence_rows: list[dict[str, Any]] | None = None,
    message_source_rows: list[dict[str, Any]] | None = None,
    tool_event_rows: list[dict[str, Any]] | None = None,
    textdoc_rows: list[dict[str, Any]] | None = None,
    citation_links: list[CitationLinkRecord] | None = None,
    asset_presentation_by_archive_path: dict[str, dict[str, str | None]] | None = None,
    unverified_payload_candidates_by_asset_ref_id: dict[str, list[UnverifiedPayloadCandidateRecord]] | None = None,
    sandbox_links_by_message: dict[str, dict[str, str | None]] | None = None,
) -> str:
    unique_file_count = unique_asset_count(assets)
    resolved_file_count = unique_asset_count(assets, status="found")
    unresolved = unresolved_asset_count(assets)
    has_warnings = bool(conversation.parse_warnings)
    models_seen = conditional_models_seen(conversation.default_model_slug, conversation.resolved_model_slugs)
    message_source_rows = message_source_rows or []
    tool_event_rows = tool_event_rows or []
    textdoc_rows = textdoc_rows or []
    runtime_artifacts = runtime_artifacts or []
    historical_comparisons = historical_comparisons or []
    historical_signals = historical_signals or []
    technical_events = technical_events or []
    branches = branch_infos(conversation)
    message_annotations = build_message_annotations(
        conversation,
        assets,
        message_source_rows,
        tool_event_rows,
        textdoc_rows,
        asset_presentation_by_archive_path,
        unverified_payload_candidates_by_asset_ref_id,
    )
    message_annotations = add_branch_annotations(message_annotations, branches)
    frontmatter = {
        "type": "openai_conversation",
        "schema_version": SCHEMA_VERSION,
        "status": "parsed",
        "title": conversation.title or "Untitled conversation",
        "reviewed": False,
        "created_at": obsidian_datetime(conversation.create_time),
        "updated_at": obsidian_datetime(conversation.update_time),
        "conversation_id": conversation.conversation_id,
        "source_shard": conversation.source_json_shard,
        "chat_url": conversation.chat_url,
        "message_count": conversation.message_count,
        "default_model": conversation.default_model_slug,
        "models_seen": models_seen,
        "gpts": gpt_links,
        "projects": [],
        "space_projects": project_links,
        "knowledge_stores": knowledge_links,
        "observed_contexts": observed_links,
        "context_evidence": context_evidence(gpt_links, project_links, knowledge_links, observed_links),
        "file_reference_count": len(assets),
        "runtime_artifact_count": len(runtime_artifacts),
        "historical_export_comparison_count": len(historical_comparisons),
        "historical_export_signal_count": len(historical_signals),
        "technical_event_count": len(technical_events),
        "unique_file_count": unique_file_count,
        "resolved_file_count": resolved_file_count,
        "unresolved_file_count": unresolved,
        "source_evidence_count": len(message_source_rows),
        "tool_evidence_count": len(tool_event_rows),
        "has_unresolved_files": unresolved > 0,
        "has_warnings": has_warnings,
        "tags": ["openai/conversation"],
    }
    lines = render_frontmatter(
        frontmatter,
        keep_empty_lists={"gpts", "projects", "space_projects", "knowledge_stores", "observed_contexts"},
    )
    title = conversation.title or "Untitled conversation"
    lines.extend(
        [
            "",
            f"# {title}",
            "",
            "## Manual Review",
            "",
            "### Summary",
            "",
            "### Restart Notes",
            "",
            "### Links",
            "",
            "<!-- BEGIN GENERATED OPENAI CONVERSATION -->",
            "",
            "> [!info] Source",
            f"> Created: {conversation.create_time or 'unknown'}  ",
            f"> Updated: {conversation.update_time or 'unknown'}  ",
            f"> Messages: {conversation.message_count} · Files: {unique_file_count} unique · {resolved_file_count} resolved · {unresolved} unresolved · Runtime artifacts: {len(runtime_artifacts)}  ",
            f"> Sources: {len(message_source_rows)} evidence rows · Tools & retrieval: {len(tool_event_rows)} evidence rows",
            "",
            "## Contexts",
            "",
            f"- GPT: {', '.join(gpt_links) if gpt_links else 'no explicit context'}",
            f"- Space project: {', '.join(project_links) if project_links else 'no explicit context'}",
            f"- Knowledge store: {', '.join(knowledge_links) if knowledge_links else 'no explicit context'}",
            f"- Observed: {', '.join(observed_links) if observed_links else 'none'}",
            "",
        ]
    )
    lines.extend(render_evidence_summary_section(conversation, assets, message_source_rows, tool_event_rows))
    lines.extend(render_export_metadata_section(conversation))
    lines.extend(render_chat_files_section(assets))
    lines.extend(render_runtime_artifacts_section(runtime_artifacts))
    lines.extend(render_historical_export_evidence_section(historical_comparisons, historical_signals))
    lines.extend(render_technical_events_section(technical_events))
    lines.extend(render_sources_used_section(conversation, message_source_rows))
    lines.extend(render_tools_retrieval_section(tool_event_rows))
    lines.extend(
        [
            "",
            "## Evidence",
            "",
            f"Canonical machine evidence for `conversation_id: {conversation.conversation_id}` is in:",
            "",
            "Message-level callouts above are a readable index of these proof rows.",
            "Canonical machine evidence remains in JSONL.",
            "",
            "- `90_Evidence/conversations.jsonl`",
            "- `90_Evidence/messages.jsonl`",
            "- `90_Evidence/message_sources.jsonl`",
            "- `90_Evidence/tool_events.jsonl`",
            "- `90_Evidence/textdocs.jsonl`",
            "- `90_Evidence/asset_links.jsonl`",
            "- `90_Evidence/runtime_artifacts.jsonl`",
            "- `90_Evidence/historical_conversation_comparisons.jsonl`",
            "- `90_Evidence/historical_messages.jsonl`",
            "- `90_Evidence/historical_signals.jsonl`",
            "- `90_Evidence/technical_events.jsonl`",
            "- `90_Evidence/technical_event_comparisons.jsonl`",
            "- `90_Evidence/technical_event_unknowns.jsonl`",
            "- `90_Evidence/context_links.jsonl`",
            "",
            "## Full Conversation",
            "",
        ]
    )
    skipped_technical = technical_empty_message_count(conversation.messages, message_annotations)
    if skipped_technical:
        lines.extend(
            [
                f"_Technical empty assistant records omitted from this readable transcript: {skipped_technical}. Full records remain in `90_Evidence/messages.jsonl`._",
                "",
            ]
        )
    for message in conversation.messages:
        lines.extend(render_readable_message(message, message_annotations.get(message.message_id or "", [])))
    lines.extend(render_alternate_branches_section(conversation, branches, message_annotations))
    lines.extend(["<!-- END GENERATED OPENAI CONVERSATION -->", ""])
    return "\n".join(lines)


def render_compact_conversation_markdown(
    conversation: ConversationRecord,
    assets: list[AssetRecord],
    *,
    runtime_artifacts: list[RuntimeArtifactRecord] | None = None,
    historical_comparisons: list[HistoricalConversationComparison] | None = None,
    historical_signals: list[HistoricalSignalRecord] | None = None,
    technical_events: list[TechnicalEventRecord] | None = None,
    gpt_links: list[str],
    project_links: list[str],
    knowledge_links: list[str],
    observed_links: list[str],
    context_evidence_rows: list[dict[str, Any]] | None = None,
    message_source_rows: list[dict[str, Any]] | None = None,
    tool_event_rows: list[dict[str, Any]] | None = None,
    textdoc_rows: list[dict[str, Any]] | None = None,
    citation_links: list[CitationLinkRecord] | None = None,
    asset_presentation_by_archive_path: dict[str, dict[str, str | None]] | None = None,
    unverified_payload_candidates_by_asset_ref_id: dict[str, list[UnverifiedPayloadCandidateRecord]] | None = None,
    sandbox_links_by_message: dict[str, dict[str, str | None]] | None = None,
) -> str:
    """Render a conversation-first note while keeping exhaustive rows in JSONL."""
    unique_file_count = unique_asset_count(assets)
    resolved_file_count = unique_asset_count(assets, status="found")
    unresolved = unresolved_asset_count(assets)
    has_warnings = bool(conversation.parse_warnings)
    models_seen = conditional_models_seen(conversation.default_model_slug, conversation.resolved_model_slugs)
    message_source_rows = message_source_rows or []
    tool_event_rows = tool_event_rows or []
    textdoc_rows = textdoc_rows or []
    citation_links = citation_links or []
    runtime_artifacts = runtime_artifacts or []
    historical_comparisons = historical_comparisons or []
    historical_signals = historical_signals or []
    technical_events = technical_events or []
    branches = branch_infos(conversation)
    message_annotations = build_message_annotations(
        conversation,
        assets,
        message_source_rows,
        tool_event_rows,
        textdoc_rows,
        asset_presentation_by_archive_path,
        unverified_payload_candidates_by_asset_ref_id,
    )
    message_annotations = add_branch_annotations(message_annotations, branches)
    canvas_context_count = sum(
        annotation.status == "canvas_textdoc_reference_found"
        for annotations in message_annotations.values()
        for annotation in annotations
    )
    frontmatter = {
        "type": "openai_conversation",
        "schema_version": SCHEMA_VERSION,
        "conversation_id": conversation.conversation_id,
        "title": conversation.title or "Untitled conversation",
        "created_at": obsidian_datetime(conversation.create_time),
        "updated_at": obsidian_datetime(conversation.update_time),
        "status": "parsed",
        "reviewed": False,
        "message_count": conversation.message_count,
        "unique_file_count": unique_file_count,
        "provider": "openai",
        "default_model": conversation.default_model_slug,
        "models_seen": models_seen,
        "space_projects": project_links,
        "tags": ["openai/conversation"],
    }
    lines = render_frontmatter(frontmatter, keep_empty_lists={"models_seen", "space_projects"})
    title = conversation.title or "Untitled conversation"
    identity_lines = [
        f"> source_shard:: {conversation.source_json_shard or 'unknown'}",
        "> markdown_profile:: readable_compact",
        f"> file_reference_count:: {len(assets)}",
        f"> resolved_file_count:: {resolved_file_count}",
        f"> unresolved_file_count:: {unresolved}",
    ]
    if unresolved:
        identity_lines.append("> has_unresolved_files:: true")
    if has_warnings:
        identity_lines.append("> has_warnings:: true")
    context_lines = [
        f"Chat URL: {conversation.chat_url}" if conversation.chat_url else "",
        f"gpts:: {', '.join(gpt_links)}" if gpt_links else "",
        f"knowledge_stores:: {', '.join(knowledge_links)}" if knowledge_links else "",
        f"observed_contexts:: {', '.join(observed_links)}" if observed_links else "",
    ]
    skipped_technical = technical_empty_message_count(conversation.messages, message_annotations)
    lines.extend(
        [
            "",
            f"# {title}",
            "",
            "> [!abstract] Summary",
            f"> Created: {readable_timestamp(conversation.create_time)}  ",
            f"> Last updated: {readable_timestamp(conversation.update_time)}  ",
            *(f"> {line}" for line in context_lines if line),
            f"> {conversation.message_count} messages · {len(assets)} file references → {unique_file_count} distinct identities · {resolved_file_count} resolved payloads",
            "",
            "## Manual Review",
            "",
            "### Summary",
            "",
            "### Restart Notes",
            "",
            "<!-- BEGIN GENERATED OPENAI CONVERSATION -->",
            "",
        ]
    )
    if has_warnings:
        lines.extend(
            [
                "> [!warning] Reconstruction warning",
                "> This conversation has parser warnings. See canonical evidence before relying on a reconstructed detail.",
                "",
            ]
        )
    lines.extend(
        [
            "## Full conversation",
            "",
        ]
    )
    citation_links_by_message = citation_links_by_message_id(citation_links)
    for message in conversation.messages:
        lines.extend(
            render_compact_message(
                message,
                message_annotations.get(message.message_id or "", []),
                citation_links=citation_links_by_message.get(message.message_id or "", []),
                chat_url=conversation.chat_url,
                sandbox_links=(sandbox_links_by_message or {}).get(message.message_id or "", {}),
            )
        )
    lines.extend(
        render_alternate_branches_section(
            conversation,
            branches,
            message_annotations,
            compact=True,
            citation_links_by_message=citation_links_by_message,
            chat_url=conversation.chat_url,
            sandbox_links_by_message=sandbox_links_by_message,
        )
    )
    lines.extend(
        render_native_footnotes(
            citation_links,
            message_annotations,
            identity_lines=identity_lines,
        )
    )
    lines.extend(
        render_compact_evidence_register(
            assets=assets,
            message_source_rows=message_source_rows,
            tool_event_rows=tool_event_rows,
            technical_events=technical_events,
            runtime_artifacts=runtime_artifacts,
            historical_comparisons=historical_comparisons,
            canvas_context_count=canvas_context_count,
            citation_links=citation_links,
            skipped_technical=skipped_technical,
        )
    )
    lines.extend(["<!-- END GENERATED OPENAI CONVERSATION -->", ""])
    return "\n".join(lines)


def render_context_markdown(
    *,
    title: str,
    kind: str,
    context_id: str,
    evidence: str,
    status: str,
    conversation_count: int,
    linked_file_count: int,
    tags: list[str],
    basis: list[str] | None = None,
    extra_fields: dict[str, Any] | None = None,
    linked_conversations: list[str] | None = None,
    linked_files_by_role: dict[str, list[str]] | None = None,
) -> str:
    frontmatter = {
        "type": "openai_context",
        "kind": kind,
        "status": status,
        "title": title,
        "aliases": [],
        "context_id": context_id,
        "evidence": evidence,
        "name_evidence": "source_id",
        "basis": basis or [],
        "conversation_count": conversation_count,
        "linked_file_count": linked_file_count,
        "tags": tags,
    }
    if extra_fields:
        frontmatter.update(extra_fields)
    lines = render_frontmatter(frontmatter, keep_empty_lists={"aliases", "basis"})
    lines.extend(
        [
            "",
            f"# {title}",
            "",
            "## Manual Context",
            "",
            "### Description",
            "",
            "### System Instructions",
            "",
            "### Restart Notes",
            "",
            "<!-- BEGIN GENERATED OPENAI CONTEXT -->",
            "",
            "## Export Evidence",
            "",
            f"- Context ID: `{context_id}`",
            f"- Kind: `{kind}`",
            f"- Evidence: `{evidence}`",
            f"- Status: `{status}`",
            "",
            "## Linked Conversations",
            "",
        ]
    )
    if linked_conversations:
        lines.extend(linked_conversations)
    else:
        lines.append("No linked conversations recovered in this export scope.")
    lines.extend(["", "## Fichiers observés dans les conversations du contexte", ""])
    linked_files_by_role = linked_files_by_role or {}
    for heading, role in (
        ("Knowledge Files", "knowledge"),
        ("Attachments", "attachment"),
        ("Generated Files", "generated"),
        ("Unresolved References", "unresolved"),
    ):
        lines.extend([f"### {heading}", ""])
        role_lines = linked_files_by_role.get(role) or []
        if role_lines:
            lines.extend(role_lines)
        else:
            lines.append("None recovered.")
        lines.append("")
    lines.extend(
        [
            "## Evidence Pointers",
            "",
            "- `90_Evidence/context_links.jsonl`",
            "- `90_Evidence/file_manifest.jsonl`",
            "- `90_Evidence/asset_links.jsonl`",
            "",
            "<!-- END GENERATED OPENAI CONTEXT -->",
            "",
        ]
    )
    return "\n".join(lines)


def render_chat_files_section(assets: list[AssetRecord]) -> list[str]:
    lines = ["", "## Files", ""]
    exported = [asset for asset in unique_assets(assets) if asset.asset_status == "found"]
    referenced_only = [
        asset
        for asset in unique_assets([asset for asset in assets if asset.asset_status == "missing"])
        if is_referenced_source_without_payload(asset)
    ]
    unresolved_payloads = [
        asset
        for asset in unique_assets([asset for asset in assets if asset.asset_status == "missing"])
        if not is_referenced_source_without_payload(asset)
    ]
    collisions = unique_assets([asset for asset in assets if asset.asset_status == "collision"])
    if exported:
        lines.extend(["### Exported Files", ""])
        for asset in exported:
            lines.append(render_chat_file_link(asset))
    if referenced_only:
        lines.extend(["", "### Referenced Source Files", ""])
        for asset in referenced_only:
            label = asset.reconstructed_filename or asset.raw_file_id or "unknown source"
            lines.append(f"- `{label}` · payload not exported")
    if unresolved_payloads:
        lines.extend(["", "### Unresolved File Payloads", ""])
        for asset in unresolved_payloads:
            label = asset.reconstructed_filename or asset.raw_file_id or "unknown file"
            lines.append(f"- `{label}`")
    if collisions:
        lines.extend(["", "### Ambiguous Physical Payloads", ""])
        for asset in collisions:
            label = asset.reconstructed_filename or asset.raw_file_id or "unknown file"
            lines.append(f"- `{label}` · multiple physical candidates")
    if not exported and not referenced_only and not unresolved_payloads and not collisions:
        lines.append("No chat files recovered.")
    return lines


def render_runtime_artifacts_section(artifacts: list[RuntimeArtifactRecord]) -> list[str]:
    if not artifacts:
        return []
    lines = ["", "## Python Runtime Artifacts", ""]
    for artifact in sorted(artifacts, key=lambda row: row.physical_archive_path):
        confirmation = "confirmed by auxiliary chat.html" if artifact.relation_status == "confirmed_by_chat_html" else "conversation path only; execution node not confirmed"
        label = f"[[{artifact.copied_filename}]]" if artifact.copied_filename else f"`{artifact.filename}`"
        lines.extend(
            [
                f"%% openai_message_id: {artifact.execution_message_id} %%",
                f"> [!info] Python artifact · {confirmation}",
                f"> {label}",
                f"> Runtime path: `{artifact.runtime_path}`  ",
                f"> Physical proof: `{artifact.physical_archive_path}`",
                "",
            ]
        )
    lines.extend(["_Canonical rows: `90_Evidence/runtime_artifacts.jsonl`._", ""])
    return lines


def render_historical_export_evidence_section(
    comparisons: list[HistoricalConversationComparison],
    signals: list[HistoricalSignalRecord],
) -> list[str]:
    if not comparisons:
        return []
    by_kind: dict[str, int] = {}
    for signal in signals:
        by_kind[signal.signal_kind] = by_kind.get(signal.signal_kind, 0) + 1
    lines = ["", "## Historical Export Evidence", ""]
    for comparison in sorted(comparisons, key=lambda row: row.snapshot_id):
        lines.extend(
            [
                "> [!warning] Auxiliary historical source — not merged into this conversation",
                f"> Historical-only messages: {comparison.historical_only_message_count} · shared messages: {comparison.shared_message_count} · primary-only messages: {comparison.primary_only_message_count}  ",
                f"> Historical source: `{comparison.historical_conversations_archive_path}`  ",
                f"> Proof: `{comparison.historical_proof_path}`",
                "",
            ]
        )
    if by_kind:
        lines.append("- Observed historical signals: " + ", ".join(f"`{kind}`: {count}" for kind, count in sorted(by_kind.items())))
        lines.append("")
    lines.extend(
        [
            "_These rows preserve older source evidence only. They do not establish that a payload is present in the current export or that an asset should be resolved._",
            "_Canonical rows: `90_Evidence/historical_conversation_comparisons.jsonl`, `historical_messages.jsonl`, and `historical_signals.jsonl`._",
            "",
        ]
    )
    return lines


def render_technical_events_section(events: list[TechnicalEventRecord]) -> list[str]:
    if not events:
        return []
    by_source: dict[str, int] = {}
    by_family: dict[str, int] = {}
    unknown = 0
    for event in events:
        by_source[event.source_export] = by_source.get(event.source_export, 0) + 1
        by_family[event.event_family] = by_family.get(event.event_family, 0) + 1
        unknown += event.event_family == "unknown_recipient"
    lines = ["", "## Technical Event Evidence", ""]
    lines.extend(
        [
            "> [!info] Source-separated observations — no payload resolution",
            "> " + ", ".join(f"`{source}`: {count}" for source, count in sorted(by_source.items())),
            "",
            "- Families: " + ", ".join(f"`{family}`: {count}" for family, count in sorted(by_family.items())),
        ]
    )
    if unknown:
        lines.append(f"- Unknown tool recipients retained: {unknown}")
    lines.extend(
        [
            "",
            "_Technical events are observations from message sources. They do not create Generation, Textdoc, Gizmo, candidate-edge, or physical-payload relations._",
            "_Canonical rows: `90_Evidence/technical_events.jsonl`, `technical_event_comparisons.jsonl`, and `technical_event_unknowns.jsonl`._",
            "",
        ]
    )
    return lines


def render_sources_used_section(conversation: ConversationRecord, rows: list[dict[str, Any]]) -> list[str]:
    context_rows = [row for row in rows if row.get("source_family") == "context"]
    cited_files = [row for row in rows if row.get("source_kind") == "cited_file"]
    web_rows = [row for row in rows if row.get("source_family") == "web"]
    memory_rows = [row for row in context_rows if row.get("source_kind") == "user_memory"]
    instruction_rows = [row for row in context_rows if row.get("source_kind") == "user_instructions"]
    past_rows = [
        row
        for row in context_rows
        if row.get("source_kind") in {"past_conversation", "past_conversation_group"}
    ]

    lines = ["", "## Sources", ""]
    wrote = False
    if past_rows:
        wrote = True
        lines.extend(render_compact_source_group("Past Chats", past_rows))
    if memory_rows:
        wrote = True
        lines.extend(render_memory_source_group(conversation, memory_rows))
    if instruction_rows:
        wrote = True
        lines.extend(render_compact_source_group("Custom Instructions", instruction_rows))
    if cited_files:
        wrote = True
        lines.extend(render_compact_source_group("Referenced Files", cited_files))
    if web_rows:
        wrote = True
        lines.extend(render_compact_source_group("Web", web_rows))
    if not wrote:
        lines.append("No explicit assistant sources recovered.")
    lines.extend(["", "Detailed source rows are in `90_Evidence/message_sources.jsonl`."])
    return lines


def render_evidence_summary_section(
    conversation: ConversationRecord,
    assets: list[AssetRecord],
    source_rows: list[dict[str, Any]],
    tool_rows: list[dict[str, Any]],
) -> list[str]:
    unique_files = unique_asset_count(assets)
    resolved_files = unique_asset_count(assets, status="found")
    unresolved_files = unresolved_asset_count(assets)
    return [
        "",
        "## Evidence Summary",
        "",
        f"- Messages: {conversation.message_count}",
        f"- Files: {unique_files} unique · {resolved_files} resolved · {unresolved_files} unresolved · {len(assets)} references",
        f"- Sources: {len(source_rows)} evidence rows",
        f"- Tools & retrieval: {len(tool_rows)} evidence rows",
        "- File references are raw mentions; unique/resolved/unresolved count deduplicated files.",
        "- Source and tool/retrieval counts are JSONL evidence rows, not distinct files or distinct tools.",
        "",
    ]


def render_tools_retrieval_section(rows: list[dict[str, Any]]) -> list[str]:
    counts = tool_event_counts(rows)
    lines = ["", "## Tools & Retrieval", ""]
    lines.extend(
        [
            f"- Web search groups: {counts.get('web_search_group', 0)}",
            f"- Web references: {counts.get('web_reference', 0)}",
            f"- Tool messages: {counts.get('tool_message', 0)}",
            f"- Tool metadata rows: {counts.get('tool_metadata', 0)}",
            "",
            "_These rows come from exported retrieval/tool metadata; many rows can come from one assistant answer._",
            "",
        ]
    )
    if rows:
        lines.extend(["### Tool Evidence Preview", ""])
        for row in rows[:5]:
            label = row.get("title") or row.get("url") or row.get("tool_name") or row.get("event_kind") or "tool evidence"
            tool_name = row.get("tool_name") or row.get("event_kind") or "tool"
            suffix = f" · {row.get('url')}" if row.get("url") else ""
            lines.append(f"- {tool_name} · {label}{suffix}")
        lines.extend(["", "Full tool/retrieval evidence is in `90_Evidence/tool_events.jsonl`."])
    else:
        lines.append("No explicit tool or retrieval evidence recovered.")
    return lines


def render_alternate_branches_section(
    conversation: ConversationRecord,
    branches: list[BranchInfo],
    message_annotations: dict[str, list[MessageAnnotation]],
    *,
    compact: bool = False,
    citation_links_by_message: dict[str, list[CitationLinkRecord]] | None = None,
    chat_url: str | None = None,
    sandbox_links_by_message: dict[str, dict[str, str | None]] | None = None,
) -> list[str]:
    if not branches:
        return []
    message_by_node = {message.node_id: message for message in conversation.all_messages}
    children_by_node = {
        str(node.get("node_id") or ""): [str(child) for child in node.get("children") or []]
        for node in conversation.node_index
    }
    main_nodes = set(conversation.linear_node_ids)
    lines = ["", "## Alternate Branches", ""]
    for branch in branches:
        lines.extend(
            [
                f"### {branch_anchor(branch.node_id)}",
                "",
                f"- Origin node: `{branch.node_id}`",
                f"- Main child: `{branch.main_child_id or 'unknown'}`",
                f"- Alternate children: {', '.join(f'`{node_id}`' for node_id in branch.alternate_child_ids)}",
                "",
            ]
        )
        if compact:
            lines.append("The origin and its main continuation are already shown once in the primary transcript.")
        else:
            origin_message = message_by_node.get(branch.node_id)
            lines.extend(["From message:", ""])
            lines.extend(
                render_branch_origin_message(
                    origin_message,
                    message_annotations,
                    compact=False,
                    citation_links_by_message=citation_links_by_message,
                    chat_url=chat_url,
                    sandbox_links_by_message=sandbox_links_by_message,
                )
            )
        for child_id in branch.alternate_child_ids:
            lines.extend(["", f"Alternate continuation `{child_id}`:", ""])
            for message in alternate_branch_messages(child_id, message_by_node, children_by_node, main_nodes):
                annotations = message_annotations.get(message.message_id or "", [])
                lines.extend(
                    render_compact_message(
                        message,
                        annotations,
                        citation_links=(citation_links_by_message or {}).get(message.message_id or "", []),
                        chat_url=chat_url,
                        sandbox_links=(sandbox_links_by_message or {}).get(message.message_id or "", {}),
                    )
                    if compact
                    else render_readable_message(message, annotations)
                )
    return lines


def render_branch_origin_message(
    message: MessageRecord | None,
    message_annotations: dict[str, list[MessageAnnotation]],
    *,
    compact: bool = False,
    citation_links_by_message: dict[str, list[CitationLinkRecord]] | None = None,
    chat_url: str | None = None,
    sandbox_links_by_message: dict[str, dict[str, str | None]] | None = None,
) -> list[str]:
    if not message:
        return ["> _No parsed message record for branch origin._", ""]
    annotations = message_annotations.get(message.message_id or "", [])
    return (
        render_compact_message(
            message,
            annotations,
            citation_links=(citation_links_by_message or {}).get(message.message_id or "", []),
            chat_url=chat_url,
            sandbox_links=(sandbox_links_by_message or {}).get(message.message_id or "", {}),
        )
        if compact
        else render_readable_message(message, annotations)
    )


def first_non_empty_line(text: str) -> str | None:
    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned[:240]
    return None


def alternate_branch_messages(
    child_id: str,
    message_by_node: dict[str, MessageRecord],
    children_by_node: dict[str, list[str]],
    main_nodes: set[str],
) -> list[MessageRecord]:
    messages: list[MessageRecord] = []
    stack = [child_id]
    seen: set[str] = set()
    while stack:
        node_id = stack.pop(0)
        if node_id in seen or node_id in main_nodes:
            continue
        seen.add(node_id)
        message = message_by_node.get(node_id)
        if message:
            messages.append(message)
        stack.extend(children_by_node.get(node_id, []))
    return messages


def tool_event_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("event_kind") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def render_memory_source_group(conversation: ConversationRecord, rows: list[dict[str, Any]]) -> list[str]:
    lines = ["### Memory / Instructions", ""]
    if conversation.memory_scope:
        lines.append(f"- Memory scope: `{conversation.memory_scope}`")
    if not rows:
        lines.append("- No exported `user_memory` rows recovered for this conversation.")
        return lines
    for row in unique_source_rows(rows):
        label = row.get("title") or row.get("memory_id") or "Memory"
        lines.append(f"- `{label}`")
        if row.get("snippet"):
            lines.append(f"  - {row['snippet']}")
        if row.get("reason"):
            lines.append(f"  - Reason: {row['reason']}")
    return lines


def render_compact_source_group(title: str, rows: list[dict[str, Any]]) -> list[str]:
    lines = [f"### {title}", ""]
    for row in unique_source_rows(rows):
        lines.append(render_compact_source_row(row))
        if row.get("snippet"):
            lines.append(f"  - {row['snippet']}")
        if row.get("reason") and row.get("source_kind") in {"user_memory", "user_instructions"}:
            lines.append(f"  - Reason: {row['reason']}")
    return lines


def render_compact_source_row(row: dict[str, Any]) -> str:
    label = row.get("title") or row.get("file_id") or row.get("url") or row.get("source_kind") or "source"
    if row.get("cited_note"):
        target = row["cited_note"]
    elif row.get("url"):
        target = markdown_link(str(label), row.get("url")) or f"`{label}`"
    else:
        target = f"`{label}`"
    suffixes = []
    if row.get("source_kind") == "cited_file":
        suffixes.append("source file")
    if row.get("payload_status") == "not_exported":
        suffixes.append("payload not exported")
    if row.get("candidate_role"):
        suffixes.append(f"{row['candidate_role']} ({row.get('candidate_confidence') or 'unknown'})")
    return f"- {target}" + (f" · {' · '.join(suffixes)}" if suffixes else "")


def render_chat_file_link(asset: AssetRecord) -> str:
    label = asset.copied_filename or asset.reconstructed_filename or asset.raw_file_id or "unknown file"
    if asset.copied_filename and is_embeddable(asset):
        return f"![[{asset.copied_filename}|300]]"
    if asset.copied_filename:
        return f"- [[{asset.copied_filename}]]"
    return f"- `{label}`"


def readable_file_summary(assets: list[AssetRecord]) -> str:
    unique = unique_assets(assets)
    exported = [asset for asset in unique if asset.asset_status == "found"]
    referenced_only = [asset for asset in unique if asset.asset_status == "missing" and is_referenced_source_without_payload(asset)]
    unresolved = [asset for asset in unique if asset.asset_status == "missing" and not is_referenced_source_without_payload(asset)]
    collisions = [asset for asset in unique if asset.asset_status == "collision"]
    parts = [f"{len(unique)} visible", f"{len(exported)} exported"]
    if referenced_only:
        parts.append(f"{len(referenced_only)} referenced only")
    if unresolved:
        parts.append(f"{len(unresolved)} unresolved")
    if collisions:
        parts.append(f"{len(collisions)} ambiguous")
    return " · ".join(parts)


def readable_source_summary(conversation: ConversationRecord, rows: list[dict[str, Any]]) -> str:
    unique_rows = unique_source_rows(rows)
    counts = {
        "past": len([row for row in unique_rows if row.get("source_kind") in {"past_conversation", "past_conversation_group"}]),
        "memory": len([row for row in unique_rows if row.get("source_kind") == "user_memory"]),
        "instructions": len([row for row in unique_rows if row.get("source_kind") == "user_instructions"]),
        "files": len([row for row in unique_rows if row.get("source_kind") == "cited_file"]),
        "web": len([row for row in unique_rows if row.get("source_family") == "web"]),
    }
    parts = []
    if counts["past"]:
        parts.append(f"{counts['past']} past chat{'s' if counts['past'] != 1 else ''}")
    if conversation.memory_scope:
        parts.append("memory scope enabled")
    if counts["memory"]:
        parts.append(f"{counts['memory']} memory row{'s' if counts['memory'] != 1 else ''}")
    if counts["instructions"]:
        parts.append(f"{counts['instructions']} instruction row{'s' if counts['instructions'] != 1 else ''}")
    if counts["files"]:
        parts.append(f"{counts['files']} source file{'s' if counts['files'] != 1 else ''}")
    if counts["web"]:
        parts.append(f"{counts['web']} web source{'s' if counts['web'] != 1 else ''}")
    return " · ".join(parts) if parts else "none recovered"


def render_export_metadata_section(conversation: ConversationRecord) -> list[str]:
    return [
        "",
        "## Export Metadata",
        "",
        f"- Memory scope: `{conversation.memory_scope}`" if conversation.memory_scope else "- Memory scope: `unknown`",
        f"- Voice: `{conversation.voice}`" if conversation.voice else "- Voice: `unknown`",
        f"- Archived: `{str(conversation.is_archived).lower()}`" if conversation.is_archived is not None else "- Archived: `unknown`",
        f"- Starred: `{str(conversation.is_starred).lower()}`" if conversation.is_starred is not None else "- Starred: `unknown`",
        f"- Do not remember: `{str(conversation.is_do_not_remember).lower()}`"
        if conversation.is_do_not_remember is not None
        else "- Do not remember: `unknown`",
        "",
    ]


def readable_tool_summary(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "none explicit"
    seen: set[str] = set()
    labels: list[str] = []
    for row in rows:
        label = row.get("tool_name") or row.get("event_kind") or "tool"
        if label in seen:
            continue
        seen.add(str(label))
        labels.append(str(label))
    return ", ".join(labels) if labels else "none explicit"


def obsidian_datetime(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    return parsed.replace(tzinfo=None, second=0, microsecond=0).isoformat(timespec="minutes")


def render_context_evidence_section(rows: list[dict[str, Any]]) -> list[str]:
    unique_rows = unique_context_evidence_rows(rows)
    lines = ["", "## Context Evidence", ""]
    past_conversations = [row for row in unique_rows if row.get("context_kind") == "past_conversation"]
    other_rows = [row for row in unique_rows if row.get("context_kind") != "past_conversation"]
    if past_conversations:
        lines.extend(["### Past Conversations", ""])
        for row in past_conversations:
            title = row.get("cited_title") or row.get("cited_conversation_id") or "Untitled conversation"
            target = row.get("cited_note") or markdown_link(title, row.get("cited_chat_url")) or f"`{title}`"
            snippet = row.get("snippet")
            proof = row.get("proof_path")
            line = f"- {target}"
            if row.get("cited_conversation_id"):
                line += f" · `{row['cited_conversation_id']}`"
            lines.append(line)
            if snippet:
                lines.append(f"  - Snippet: {snippet}")
            if proof:
                lines.append(f"  - Proof: `{proof}`")
    for row in other_rows:
        title = row.get("cited_title") or row.get("context_kind") or "context"
        lines.append(f"- `{title}`")
    return lines


def render_message_sources_section(conversation: ConversationRecord, rows: list[dict[str, Any]]) -> list[str]:
    rows_by_message: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        message_id = str(row.get("message_id") or "unknown-message")
        rows_by_message.setdefault(message_id, []).append(row)
    message_by_id = {message.message_id: message for message in conversation.messages if message.message_id}
    lines = ["", "## Sources by Message", ""]
    for message in conversation.messages:
        if not message.message_id or message.message_id not in rows_by_message:
            continue
        source_rows = rows_by_message[message.message_id]
        title = f"{(message.author_role or 'unknown').title()} · {readable_timestamp(message.create_time)}"
        model = message.model_slug or message.default_model_slug
        if message.author_role == "assistant" and model:
            title += f" · Model: {model}"
        lines.extend([f"### {title}", ""])
        if message.author_role == "assistant" and conversation.memory_scope and not any(row.get("source_kind") == "user_memory" for row in source_rows):
            lines.append(f"- Memory scope: `{conversation.memory_scope}`; no exported `user_memory` rows for this message.")
        for section_title, kinds in (
            ("Memory", {"user_memory"}),
            ("Custom Instructions", {"user_instructions"}),
            ("Past Chats", {"past_conversation", "past_conversation_group"}),
            ("Files", {"cited_file", "uploaded_file", "inline_asset", "file_reference"}),
            ("Web", {"web_citation", "webpage", "webpage_extended", "grouped_webpages"}),
        ):
            selected = [row for row in source_rows if row.get("source_kind") in kinds]
            if selected:
                lines.extend(render_source_group(section_title, selected))
        remaining = [
            row
            for row in source_rows
            if row.get("source_kind")
            not in {
                "user_memory",
                "user_instructions",
                "past_conversation",
                "past_conversation_group",
                "cited_file",
                "uploaded_file",
                "inline_asset",
                "file_reference",
                "web_citation",
                "webpage",
                "webpage_extended",
                "grouped_webpages",
            }
        ]
        if remaining:
            lines.extend(render_source_group("Other Sources", remaining))
        lines.append("")
    if len(lines) == 3:
        return []
    return lines


def render_source_group(title: str, rows: list[dict[str, Any]]) -> list[str]:
    lines = [f"#### {title}", ""]
    for row in unique_source_rows(rows):
        label = row.get("title") or row.get("file_id") or row.get("url") or row.get("source_kind") or "source"
        if row.get("cited_note"):
            target = row["cited_note"]
        elif row.get("url"):
            target = markdown_link(str(label), row.get("url")) or f"`{label}`"
        else:
            target = f"`{label}`"
        suffixes = []
        if row.get("source_kind"):
            suffixes.append(str(row["source_kind"]))
        if row.get("payload_status"):
            suffixes.append(f"payload: {row['payload_status']}")
        if row.get("candidate_role"):
            suffixes.append(f"{row['candidate_role']} ({row.get('candidate_confidence') or 'unknown'})")
        line = f"- {target}"
        if suffixes:
            line += " · " + " · ".join(suffixes)
        lines.append(line)
        if row.get("snippet"):
            lines.append(f"  - Snippet: {row['snippet']}")
        if row.get("reason"):
            lines.append(f"  - Reason: {row['reason']}")
        if row.get("proof_path"):
            lines.append(f"  - Proof: `{row['proof_path']}`")
    return lines


def unique_source_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, Any, Any, Any]] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        if row.get("source_family") == "file":
            key = ("file", row.get("file_id"), None, row.get("title"))
        else:
            key = (row.get("source_kind"), row.get("file_id"), row.get("url"), row.get("memory_id") or row.get("title"))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def is_referenced_source_without_payload(asset: AssetRecord) -> bool:
    return ".metadata.citations[" in asset.proof_path or ".metadata.content_references[" in asset.proof_path


def unique_context_evidence_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, Any, Any]] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = (row.get("context_kind"), row.get("cited_conversation_id"), row.get("citation_uuid"))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def markdown_link(label: str, url: Any) -> str | None:
    if not isinstance(url, str) or not url:
        return None
    safe_label = label.replace("[", "\\[").replace("]", "\\]")
    safe_url = url.replace(")", "%29")
    return f"[{safe_label}]({safe_url})"


def render_frontmatter(data: dict[str, Any], keep_empty_lists: set[str] | None = None) -> list[str]:
    keep_empty_lists = keep_empty_lists or set()
    lines = ["---"]
    for key, value in data.items():
        if value in (None, ""):
            continue
        if value == [] and key not in keep_empty_lists:
            continue
        lines.extend(yaml_lines(key, value))
    lines.append("---")
    return lines


def yaml_lines(key: str, value: Any) -> list[str]:
    if isinstance(value, list):
        if not value:
            return [f"{key}: []"]
        lines = [f"{key}:"]
        for item in value:
            lines.append(f"  - {yaml_scalar(item)}")
        return lines
    if isinstance(value, bool):
        return [f"{key}: {'true' if value else 'false'}"]
    if isinstance(value, int):
        return [f"{key}: {value}"]
    if key == "month":
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return [f'{key}: "{escaped}"']
    return [f"{key}: {yaml_scalar(value)}"]


def yaml_value(value: Any) -> str:
    return yaml_scalar(value)


def yaml_scalar(value: Any) -> str:
    if value is None:
        return '""'
    text = str(value)
    if text == "":
        return '""'
    needs_quotes = (
        text.startswith("[[")
        or text.startswith("!")
        or ": " in text
        or text.startswith(("0", "-", "{", "[", "#", "&", "*"))
        or "\n" in text
    )
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"' if needs_quotes else escaped


def render_asset_section(title: str, assets: list[AssetRecord]) -> list[str]:
    lines = [f"### {title}", ""]
    for asset in assets:
        if asset.copied_filename and is_embeddable(asset):
            lines.append(f"![[{asset.copied_filename}|300]]")
        elif asset.copied_filename:
            lines.append(f"- [[{asset.copied_filename}]]")
        else:
            lines.append(f"- `{asset.reconstructed_filename or asset.raw_file_id or 'unknown asset'}`")
    return lines


def unique_assets(assets: list[AssetRecord]) -> list[AssetRecord]:
    seen: set[str] = set()
    result: list[AssetRecord] = []
    for asset in assets:
        key = asset.raw_file_id or asset.raw_dat_filename or asset.asset_ref_id
        if key in seen:
            continue
        seen.add(key)
        result.append(asset)
    return result


def unique_asset_count(assets: list[AssetRecord], *, status: str | None = None) -> int:
    selected = [asset for asset in assets if status is None or asset.asset_status == status]
    return len(unique_assets(selected))


def unresolved_asset_count(assets: list[AssetRecord]) -> int:
    return len(unique_assets([asset for asset in assets if is_unresolved_asset_status(asset.asset_status)]))


def is_embeddable(asset: AssetRecord) -> bool:
    ext = (asset.normalized_extension or Path(asset.copied_filename or "").suffix.lstrip(".")).lower()
    return ext in {"png", "jpg", "jpeg", "gif", "webp", "svg", "pdf"}


def render_readable_message(message: MessageRecord, annotations: list[MessageAnnotation] | None = None) -> list[str]:
    annotations = annotations or []
    if is_technical_empty_message(message) and not annotations:
        return []
    if is_technical_empty_message(message):
        lines: list[str] = []
        for index, annotation in enumerate(annotations):
            if index:
                lines.append("")
            lines.extend(render_message_annotation(annotation))
        lines.append("")
        return lines
    role = message.author_role or "unknown"
    title_parts = [role.title(), readable_timestamp(message.create_time)]
    model = message.model_slug or message.default_model_slug
    if role == "assistant" and model:
        title_parts.append(f"Model: {model}")
    # Keep the source UUID adjacent to its readable callout without exposing it
    # in Obsidian. This makes a local Markdown excerpt traceable back to
    # messages.jsonl and the source conversation mapping.
    lines = [f"%% openai_message_id: {message.message_id or 'missing'} %%", f"> [!{callout_type_for_role(role)}] {' · '.join(title_parts)}"]
    if message.text:
        body = message.text
    elif message.non_text_parts:
        body = f"_Non-text content only; preserved in `90_Evidence/messages.jsonl`: {len(message.non_text_parts)} part(s)._"
    else:
        body = "_Empty text message; raw record preserved in `90_Evidence/messages.jsonl`._"
    for body_line in body.splitlines():
        lines.append(f"> {body_line}" if body_line else ">")
    if message.text and message.non_text_parts:
        lines.extend([">", f"> _Non-text parts preserved in `90_Evidence/messages.jsonl`: {len(message.non_text_parts)}._"])
    if annotations:
        lines.append("")
    for index, annotation in enumerate(annotations):
        if index:
            lines.append("")
        lines.extend(render_message_annotation(annotation))
    lines.append("")
    return lines


def render_compact_message(
    message: MessageRecord,
    annotations: list[MessageAnnotation] | None = None,
    *,
    citation_links: list[CitationLinkRecord] | None = None,
    chat_url: str | None = None,
    sandbox_links: dict[str, str | None] | None = None,
) -> list[str]:
    """Render a message with all of its readable evidence nested beneath it."""
    annotations = annotations or []
    citation_links = citation_links or []
    visible_annotations = annotations
    if is_technical_empty_message(message) and not visible_annotations and not citation_links:
        return []
    role = message.author_role or "unknown"
    title_parts = compact_message_title_parts(role, message.create_time)
    model = message.model_slug or message.default_model_slug
    if role == "assistant" and model:
        title_parts.append(f"#model/{model}")
    lines = [
        f"> [!{callout_type_for_role(role)}] {' · '.join(title_parts)}",
        f"> <!-- openai_message_id: {message.message_id or 'missing'} -->",
        ">",
    ]
    if message.text:
        body = replace_compact_citation_markers(message.text, citation_links)
        body = rewrite_sandbox_references(body, sandbox_links or {}, chat_url)
    elif message.non_text_parts:
        body = f"_Non-text content only; preserved in `90_Evidence/messages.jsonl`: {len(message.non_text_parts)} part(s)._"
    else:
        body = "_Empty text message; raw record preserved in `90_Evidence/messages.jsonl`._"
    for body_line in body.splitlines():
        lines.append(f"> {body_line}" if body_line else ">")
    if message.text and message.non_text_parts:
        lines.extend([">", f"> _Non-text parts preserved in `90_Evidence/messages.jsonl`: {len(message.non_text_parts)}._"])

    grouped = (("file", "Attached files", "info", "`90_Evidence/asset_links.jsonl`", False),)
    grouped_annotations: set[int] = set()
    for kind, title, callout, evidence_path, collapsed in grouped:
        family = [annotation for annotation in visible_annotations if annotation.kind == kind]
        if not family:
            continue
        grouped_annotations.update(id(annotation) for annotation in family)
        lines.append(">")
        lines.extend(render_compact_annotation_bundle(title, callout, family, evidence_path=evidence_path, collapsed=collapsed))

    for kind, title, callout, evidence_path, collapsed in (
        ("tool", "Tools & retrieval", "example", "`90_Evidence/tool_events.jsonl`", True),
        ("source", "Sources & context", "cite", "`90_Evidence/message_sources.jsonl`", True),
    ):
        family = [annotation for annotation in visible_annotations if annotation.kind == kind]
        if not family:
            continue
        grouped_annotations.update(id(annotation) for annotation in family)
        lines.append(">")
        lines.extend(
            render_compact_annotation_bundle(
                title,
                callout,
                family,
                evidence_path=evidence_path,
                collapsed=collapsed,
            )
        )

    for annotation in visible_annotations:
        if id(annotation) in grouped_annotations:
            continue
        lines.append(">")
        if annotation.status == "canvas_textdoc_reference_found":
            rendered = render_compact_canvas_textdoc_reference(annotation, collapsed=False)
            lines.extend(append_annotation_footnote(rendered, annotation))
        else:
            rendered = render_compact_message_annotation(annotation)
            lines.extend(nest_message_evidence(append_annotation_footnote(rendered, annotation)))
    lines.append("")
    return lines


def render_compact_annotation_bundle(
    title: str,
    callout: str,
    annotations: list[MessageAnnotation],
    *,
    evidence_path: str,
    collapsed: bool,
) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for annotation in annotations:
        label = compact_annotation_label(annotation)
        key = annotation.presentation_identity if annotation.kind == "file" and annotation.presentation_identity else f"{annotation.status}:{label}"
        if key in seen:
            continue
        seen.add(key)
        marker = f"[^{compact_annotation_footnote_id(annotation)}]"
        labels.append(f"{label} • *details*{marker}")
    collapse_marker = ""
    lines = [f"> > [!{callout}]{collapse_marker} {title} · {len(labels)}"]
    for label in labels:
        lines.append(f"> > - {label}")
    lines.append(f"> > %% Evidence: {evidence_path} %%")
    return lines


def append_annotation_footnote(lines: list[str], annotation: MessageAnnotation) -> list[str]:
    """Attach one native footnote marker to a single annotation callout."""
    marker = f"*details*[^{compact_annotation_footnote_id(annotation)}]"
    result = list(lines)
    for index, line in enumerate(result):
        if re.match(r"^(?:> )+\[!", line):
            result[index] = f"{line} {marker}"
            break
    return result


def nest_message_evidence(lines: list[str]) -> list[str]:
    """Turn a normal callout into a child callout of the current message."""
    nested: list[str] = []
    for line in lines:
        nested.append(f"> {line}" if line.startswith(">") else f"> > {line}")
    return nested


def compact_annotation_label(annotation: MessageAnnotation) -> str:
    if annotation.kind == "file":
        if annotation.copied_pack_path:
            label = annotation.label.replace("|", "·").replace("]", "›")
            return f"[[{annotation.copied_pack_path}|{label}]]"
        if annotation.status == "exact_id_missing_unverified_payload_candidate":
            return f"`{annotation.label}` · citation identified; exact payload unresolved; same-name candidate available"
        suffix = "" if annotation.status.endswith("found") else f" · `{annotation.status}`"
        return f"`{annotation.label}`{suffix}"
    return annotation.label


def compact_message_title_parts(role: str, value: str | None) -> list[str]:
    """Render a stable date and time separately for the compact callout."""
    timestamp = readable_timestamp(value)
    if timestamp == "unknown":
        return [role.title(), timestamp]
    date, _, time = timestamp.partition(" ")
    return [role.title(), date, time] if time else [role.title(), date]


def compact_annotation_footnote_id(annotation: MessageAnnotation) -> str:
    """Stable display identifier; canonical provenance stays in JSONL."""
    if annotation.presentation_identity:
        source = "\x1f".join([annotation.kind, annotation.presentation_identity])
        return f"{annotation.kind}-{sha256(source.encode('utf-8')).hexdigest()[:12]}"
    source = "\x1f".join(
        [annotation.kind, annotation.status, annotation.label, annotation.detail or ""]
    )
    return f"{annotation.kind}-{sha256(source.encode('utf-8')).hexdigest()[:12]}"


def replace_compact_citation_markers(text: str, records: list[CitationLinkRecord]) -> str:
    """Replace exported citation glyphs with native footnote markers.

    The exact original marker remains canonical in citation_links.jsonl.
    """
    result = text
    grouped: dict[tuple[int, int, str], list[CitationLinkRecord]] = {}
    for record in records:
        grouped.setdefault((record.marker_start, record.marker_end, record.marker_original), []).append(record)
    for (marker_start, marker_end, marker_original), occurrences in sorted(
        grouped.items(),
        key=lambda item: item[0][0],
        reverse=True,
    ):
        if result[marker_start:marker_end] != marker_original:
            continue
        identifiers = list(
            dict.fromkeys(citation_source_footnote_id(record) for record in occurrences)
        )
        replacement = "".join(f"[^{identifier}]" for identifier in identifiers)
        result = f"{result[:marker_start]}{replacement}{result[marker_end:]}"
    return result


def citation_display_label(record: CitationLinkRecord) -> str:
    """Prefer a proven local filename, then an explicit source title."""
    if record.copied_pack_path:
        return Path(record.copied_pack_path).name
    return record.title or record.file_id or record.url or "source"


def render_native_footnotes(
    citation_links: list[CitationLinkRecord],
    message_annotations: dict[str, list[MessageAnnotation]],
    *,
    identity_lines: list[str] | None = None,
) -> list[str]:
    """Emit deterministic native footnotes without embedded file content."""
    citation_groups: dict[str, list[CitationLinkRecord]] = {}
    for record in citation_links:
        citation_groups.setdefault(citation_source_key(record), []).append(record)
    annotations = sorted(
        (
            annotation
            for rows in message_annotations.values()
            for annotation in rows
            if annotation.kind != "json"
        ),
        key=lambda annotation: compact_annotation_footnote_id(annotation),
    )
    if not citation_groups and not annotations and not identity_lines:
        return []
    lines = ["", "## References", ""]
    if identity_lines:
        lines.extend(
            [
                "> [!info] Identity and source properties",
                *identity_lines,
                "",
            ]
        )
    for source_key in sorted(citation_groups):
        occurrences = citation_groups[source_key]
        record = occurrences[0]
        identifier = citation_source_footnote_id(record)
        status = compact_citation_status_label(record)
        if record.url:
            subject = markdown_link(record.title or record.url, record.url) or f"`{record.url}`"
        elif record.copied_pack_path:
            subject = f"[[{record.copied_pack_path}|{Path(record.copied_pack_path).name}]]"
        else:
            subject = f"`{record.title or record.file_id or 'unresolved exported citation'}`"
        label = citation_display_label(record)
        lines.append(f"[^{identifier}]: **{label}** — {status}")
        lines.append(f"    - {'Fichier' if record.copied_pack_path else 'Source'} : {subject}")
        seen_line_ranges: set[str] = set()
        seen_snippets: set[str] = set()
        seen_proof_paths: set[str] = set()
        proof_paths: list[str] = []
        for occurrence in occurrences:
            if occurrence.line_start is not None:
                line_range = f"lines {occurrence.line_start}" if occurrence.line_end in (None, occurrence.line_start) else f"lines {occurrence.line_start}-{occurrence.line_end}"
                if line_range not in seen_line_ranges:
                    seen_line_ranges.add(line_range)
                    lines.append(f"    - Lignes : `{line_range.removeprefix('lines ')}`")
            if occurrence.snippet:
                snippet = compact_snippet(occurrence.snippet)
                if snippet and snippet not in seen_snippets:
                    seen_snippets.add(snippet)
                    lines.append(f"    - Snippet : {snippet}")
            if occurrence.proof_path and occurrence.proof_path not in seen_proof_paths:
                seen_proof_paths.add(occurrence.proof_path)
                proof_paths.append(occurrence.proof_path)
        lines.append("    - Evidence : `90_Evidence/citation_links.jsonl`")
        for proof_path in proof_paths:
            lines.append(f"    - proof_path : `{proof_path}`")
        lines.append("")
    emitted: set[str] = set()
    for annotation in annotations:
        identifier = compact_annotation_footnote_id(annotation)
        if identifier in emitted:
            continue
        emitted.add(identifier)
        lines.append(f"[^{identifier}]: **{annotation.label}** — {compact_annotation_status_label(annotation)}")
        if annotation.copied_pack_path:
            label = annotation.label.replace("|", "·").replace("]", "›")
            lines.append(f"    - Fichier : [[{annotation.copied_pack_path}|{label}]]")
        for detail in compact_footnote_detail_lines(annotation):
            lines.append(f"    - {detail}")
        seen_annotation_snippets: set[str] = set()
        for raw_snippet in annotation.snippets:
            snippet = compact_snippet(raw_snippet)
            if snippet and snippet not in seen_annotation_snippets:
                seen_annotation_snippets.add(snippet)
                lines.append(f"    - Snippet : {snippet}")
        if annotation.cited_note_path:
            note_path = Path(annotation.cited_note_path).with_suffix("").as_posix()
            note_label = annotation.label.replace("|", "·").replace("]", "›")
            lines.append(f"    - Note : [[{note_path}|{note_label}]]")
        if annotation.confidence and annotation.confidence != "explicit":
            lines.append(f"    - Confidence : `{annotation.confidence}`")
        evidence_path = compact_annotation_evidence_path(annotation.kind)
        if evidence_path:
            lines.append(f"    - Evidence : `{evidence_path}`")
        if annotation.proof_path:
            lines.append(f"    - proof_path : `{annotation.proof_path}`")
        lines.append("")
    return lines


def compact_citation_status_label(record: CitationLinkRecord) -> str:
    if record.file_id and not record.copied_pack_path:
        return "citation de fichier identifiée, payload physique non résolu"
    family = "citation de fichier" if record.copied_pack_path or record.file_id else "citation"
    status = "résolue" if record.resolution_status == "resolved" else record.resolution_status
    return f"{family} {status}"


def compact_annotation_status_label(annotation: MessageAnnotation) -> str:
    if annotation.kind == "file":
        if annotation.status == "exact_id_missing_unverified_payload_candidate":
            return "citation identifiée, payload exact non résolu"
        if annotation.status.endswith("found"):
            return "fichier trouvé"
        if "missing" in annotation.status:
            return "fichier manquant"
        return f"fichier ({annotation.status})"
    return f"{annotation.kind} ({annotation.status})"


def compact_footnote_detail_lines(annotation: MessageAnnotation) -> list[str]:
    """Keep concise proven metadata; never embed a payload in a footnote."""
    kept: list[str] = []
    for detail in (annotation.detail or "").splitlines():
        if not detail or detail.startswith("![["):
            continue
        if detail.startswith(("Copied path:", "Physical payload found:")):
            continue
        normalized = (
            detail.replace("Role:", "Rôle :", 1)
            .replace("Knowledge Store:", "Knowledge Store :", 1)
            .replace("Library file:", "Library file :", 1)
        )
        kept.append(normalized)
    return kept


def compact_snippet(value: str, *, limit: int = 280) -> str:
    """Normalize only whitespace and bound the Markdown projection."""
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    if limit <= 3:
        return "." * limit
    return f"{normalized[: limit - 3].rstrip()}..."


def compact_annotation_evidence_path(kind: str) -> str | None:
    return {
        "file": "90_Evidence/asset_links.jsonl",
        "source": "90_Evidence/message_sources.jsonl",
        "tool": "90_Evidence/tool_events.jsonl",
        "artifact": "90_Evidence/textdocs.jsonl",
        "branch": "90_Evidence/messages.jsonl",
    }.get(kind)


def citation_links_by_message_id(records: list[CitationLinkRecord]) -> dict[str, list[CitationLinkRecord]]:
    grouped: dict[str, list[CitationLinkRecord]] = {}
    for record in records:
        if record.message_id:
            grouped.setdefault(record.message_id, []).append(record)
    return grouped


def render_citation_notes(records: list[CitationLinkRecord]) -> list[str]:
    resolved = [record for record in records if record.resolution_status == "resolved"]
    if not resolved:
        return []
    lines = ["", "## Citation notes", "", "Each in-message citation keeps its original marker and links to its source note below.", ""]
    grouped: dict[str, list[CitationLinkRecord]] = {}
    for record in resolved:
        grouped.setdefault(citation_source_key(record), []).append(record)
    for source_key in sorted(grouped):
        occurrences = grouped[source_key]
        record = occurrences[0]
        if record.url:
            target = markdown_link(record.title or record.url, record.url) or f"`{record.url}`"
        elif record.copied_pack_path:
            target = f"[[{Path(record.copied_pack_path).name}]]"
        else:
            target = f"`{record.title or record.file_id or 'exported file citation'}`"
        lines.append(f"- {target}")
        lines.append(f"  ^{citation_source_footnote_id(record)}")
        for occurrence in occurrences:
            details = []
            if occurrence.line_start is not None:
                line_range = f"lines {occurrence.line_start}" if occurrence.line_end in (None, occurrence.line_start) else f"lines {occurrence.line_start}–{occurrence.line_end}"
                details.append(line_range)
            if occurrence.snippet:
                details.append(" ".join(occurrence.snippet.split())[:280])
            if occurrence.proof_path:
                details.append(f"proof: `{occurrence.proof_path}`")
            suffix = " · ".join(details)
            lines.append(f"  - {occurrence.marker_original}{' — ' + suffix if suffix else ''}")
    lines.append("")
    return lines


def render_compact_canvas_textdoc_reference(annotation: MessageAnnotation, *, collapsed: bool = False) -> list[str]:
    """Keep a Canvas reference at its original turn without implying a payload exists."""
    payload_exported = "payload exported elsewhere" in annotation.label.lower()
    state = "payload exported elsewhere" if payload_exported else "payload absent from export"
    lines = [f"> > [!abstract]{'-' if collapsed else ''} Canvas TextDoc reference · {state}"]
    for detail_line in (annotation.detail or "").splitlines():
        if detail_line.startswith("Textdoc ID:"):
            lines.append(f"> > {detail_line.replace('Textdoc ID:', 'ID:', 1)}")
        else:
            lines.append(f"> > {detail_line}")
    if annotation.proof_path:
        lines.append(f"> > Proof: `{annotation.proof_path}`")
    return lines


def render_compact_citation_bundle(records: list[CitationLinkRecord]) -> list[str]:
    resolved = [record for record in records if record.resolution_status == "resolved"]
    if not resolved:
        return []
    grouped: dict[str, list[CitationLinkRecord]] = {}
    for record in resolved:
        grouped.setdefault(citation_source_key(record), []).append(record)
    lines = [f"> > [!cite]- Citations · {len(grouped)} source{'s' if len(grouped) != 1 else ''}"]
    for source_key in sorted(grouped):
        record = grouped[source_key][0]
        label = record.title or record.url or record.file_id or "exported source"
        lines.append(f"> > - [[#^{citation_source_footnote_id(record)}|{label}]]")
    lines.append("> > Details: `90_Evidence/citation_links.jsonl`")
    return lines


def rewrite_sandbox_references(
    text: str,
    links: dict[str, str | None],
    chat_url: str | None,
) -> str:
    """Project explicit sandbox paths to a local payload or the source chat.

    The raw sandbox string and its resolution remain in the evidence JSONL. A
    conversation fallback is used only when this exact exported reference has
    no demonstrated materialized payload.
    """
    def replacement(raw_path: str, label: str | None = None) -> str:
        copied_path = links.get(raw_path)
        filename = Path(raw_path.rstrip(".,;:")).name or "download"
        display = label or f"📥 Télécharger `{filename}`"
        if copied_path:
            return f"[[{copied_path}|{display}]]"
        if chat_url:
            return f"👉 [{display}]({chat_url}) *(visit original conversation to download)*"
        return raw_path

    def markdown_link(match: re.Match[str]) -> str:
        return replacement(match.group("target"), match.group("label") or None)

    result = SANDBOX_MARKDOWN_LINK_RE.sub(markdown_link, text)
    return SANDBOX_REFERENCE_RE.sub(lambda match: replacement(match.group(0)), result)


def render_compact_evidence_register(
    *,
    assets: list[AssetRecord],
    message_source_rows: list[dict[str, Any]],
    tool_event_rows: list[dict[str, Any]],
    technical_events: list[TechnicalEventRecord],
    runtime_artifacts: list[RuntimeArtifactRecord],
    historical_comparisons: list[HistoricalConversationComparison],
    canvas_context_count: int,
    citation_links: list[CitationLinkRecord],
    skipped_technical: int,
) -> list[str]:
    lines = ["", "## Evidence register", ""]
    non_file_source_rows = [row for row in message_source_rows if row.get("source_family") != "file"]
    lines.append("- Machine-readable canon: `90_Evidence/`.")
    if skipped_technical:
        lines.append(
            f"- Technical empty assistant records omitted from Markdown: {skipped_technical}; "
            "full records remain in `90_Evidence/messages.jsonl`."
        )
    lines.append(
        f"- Files: {len(assets)} references · {unique_asset_count(assets)} distinct identities · "
        f"{unique_asset_count(assets, status='found')} resolved payloads · {unresolved_asset_count(assets)} unresolved or ambiguous references."
    )
    if non_file_source_rows:
        lines.append(f"- Sources and context: {len(non_file_source_rows)} raw row(s) in `90_Evidence/message_sources.jsonl`.")
    if tool_event_rows:
        lines.append(f"- Tools and retrievals: {len(tool_event_rows)} raw row(s) in `90_Evidence/tool_events.jsonl`.")
    if technical_events:
        lines.append(f"- Technical observations: {len(technical_events)} row(s) in `90_Evidence/technical_events.jsonl`.")
    if runtime_artifacts:
        lines.append(f"- Runtime artifacts: {len(runtime_artifacts)} row(s) in `90_Evidence/runtime_artifacts.jsonl`.")
    if historical_comparisons:
        lines.append(f"- Historical comparisons: {len(historical_comparisons)} row(s) in `90_Evidence/historical_conversation_comparisons.jsonl`.")
    if canvas_context_count:
        lines.append(
            f"- Canvas TextDoc references: {canvas_context_count} observed trace(s), each anchored to its original message; "
            "see `90_Evidence/textdocs.jsonl`."
        )
    if citation_links:
        resolved = sum(row.resolution_status == "resolved" for row in citation_links)
        lines.append(f"- Citation links: {resolved} resolved · {len(citation_links) - resolved} unresolved marker(s) in `90_Evidence/citation_links.jsonl`.")
    lines.append("")
    return lines


def is_technical_empty_message(message: MessageRecord) -> bool:
    return (
        not message.text
        and not message.non_text_parts
        and message.author_role == "assistant"
        and message.content_type in {"thoughts", "reasoning_recap"}
    )


def render_message_annotation(annotation: MessageAnnotation, *, collapsed: bool = True) -> list[str]:
    artifact_evidence_statuses = {"structural_textdoc_found", "canvas_textdoc_reference_found", "canmore_uri_reference"}
    callout = {
        "tool": "example",
        "source": "cite",
        "file": "warning" if annotation.status == "missing" or annotation.status.endswith("_missing") else "info",
        "artifact": "info" if annotation.status in artifact_evidence_statuses else "warning",
        "json": "info",
        "branch": "abstract",
    }.get(annotation.kind, "note")
    title = {
        "tool": "Tool / retrieval",
        "source": "Source evidence",
        "file": "File evidence",
        "artifact": "Export evidence" if annotation.status in artifact_evidence_statuses else "Export gap",
        "json": "JSON-only activity",
        "branch": "Branch",
    }.get(annotation.kind, "Evidence")
    lines = [f"> [!{callout}]{'-' if collapsed else ''} {title}"]
    lines.append(f"> {annotation.status}: {annotation.label}")
    if annotation.detail:
        for detail_line in annotation.detail.splitlines():
            lines.append(f"> {detail_line}" if detail_line else ">")
    if annotation.related_ids:
        ids = ", ".join(f"`{value}`" for value in annotation.related_ids)
        id_label = "File ID" if annotation.kind == "file" and len(annotation.related_ids) == 1 else "Related IDs"
        lines.append(f"> {id_label}: {ids}")
    if annotation.proof_path:
        lines.append(f"> Proof: `{annotation.proof_path}`")
    if annotation.confidence and annotation.confidence != "explicit":
        lines.append(f"> Confidence: `{annotation.confidence}`")
    return lines


def render_compact_message_annotation(annotation: MessageAnnotation) -> list[str]:
    """Keep message evidence terse; the native footnote carries full detail."""
    artifact_evidence_statuses = {
        "structural_textdoc_found",
        "canvas_textdoc_reference_found",
        "canmore_uri_reference",
    }
    callout = {
        "tool": "example",
        "source": "cite",
        "file": "warning"
        if annotation.status == "missing" or annotation.status.endswith("_missing")
        else "info",
        "artifact": "info" if annotation.status in artifact_evidence_statuses else "warning",
        "json": "info",
        "branch": "abstract",
    }.get(annotation.kind, "note")
    title = {
        "tool": "Tool / retrieval",
        "source": "Source evidence",
        "file": "File evidence",
        "artifact": "Export evidence" if annotation.status in artifact_evidence_statuses else "Export gap",
        "json": "JSON-only activity",
        "branch": "Branch",
    }.get(annotation.kind, "Evidence")
    collapse_marker = "-" if callout == "warning" else ""
    return [
        f"> [!{callout}]{collapse_marker} {title}",
        f"> {annotation.status}: {annotation.label}",
    ]


def technical_empty_message_count(
    messages: list[MessageRecord],
    annotations: dict[str, list[MessageAnnotation]] | None = None,
) -> int:
    annotations = annotations or {}
    return sum(
        1
        for message in messages
        if is_technical_empty_message(message) and not annotations.get(message.message_id or "")
    )


def callout_type_for_role(role: str) -> str:
    return {
        "user": "question",
        "assistant": "success",
        "system": "warning",
        "tool": "example",
    }.get(role, "note")


def readable_timestamp(value: str | None) -> str:
    if not value:
        return "unknown time"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    stamp = parsed.strftime("%Y-%m-%d %H:%M:%S")
    if parsed.tzinfo is None:
        return stamp
    offset = parsed.strftime("%z")
    if offset == "+0000":
        return f"{stamp} UTC"
    return f"{stamp} {offset[:3]}:{offset[3:]}"


def context_evidence(gpts: list[str], projects: list[str], knowledge: list[str], observed: list[str]) -> str:
    explicit = bool(gpts or projects or knowledge)
    observed_only = bool(observed)
    if explicit and observed_only:
        return "mixed"
    if explicit:
        return "explicit"
    if observed_only:
        return "observed"
    return "unknown"


def conditional_models_seen(default_model: str | None, models_seen: list[str]) -> list[str]:
    if not models_seen:
        return []
    if default_model and models_seen == [default_model]:
        return []
    return models_seen


def escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
