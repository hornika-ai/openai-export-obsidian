from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

from .models import AssetRecord, ConversationRecord, MessageRecord
from .payload_candidates import UnverifiedPayloadCandidateRecord


@dataclass(frozen=True)
class BranchInfo:
    node_id: str
    message_id: str | None
    main_child_id: str | None
    alternate_child_ids: list[str]


@dataclass(frozen=True)
class MessageAnnotation:
    kind: str
    status: str
    label: str
    proof_path: str | None = None
    detail: str | None = None
    confidence: str = "explicit"
    related_ids: list[str] = field(default_factory=list)
    presentation_identity: str | None = None
    copied_pack_path: str | None = None
    canonical_archive_path: str | None = None
    snippets: list[str] = field(default_factory=list)
    cited_note_path: str | None = None


GENERATION_PATTERNS = [
    re.compile(r"\bça a généré\b", re.IGNORECASE),
    re.compile(r"\bg[ée]n[ée]r[ée]\b", re.IGNORECASE),
    re.compile(r"\bgenerated\b", re.IGNORECASE),
    re.compile(r"\bcreated (?:an? )?(?:image|file|audio|video)\b", re.IGNORECASE),
    re.compile(r"\btriptyque\b", re.IGNORECASE),
]

DOCUMENT_PATTERNS = [
    re.compile(r"\bfichier\b.*\best ouvert\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bc['’]est fait\b.*\bfichier\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bj['’]ai\s+(?:créé|cree|enregistré|enregistre|ouvert)\b.*\b(?:fichier|document|canevas|textdoc)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\b(?:document|canevas|textdoc)\b.*\b(?:créé|cree|enregistré|enregistre|ouvert)\b", re.IGNORECASE | re.DOTALL),
]

DOCUMENT_NAME_RE = re.compile(r"`([^`]+\.(?:md|txt|json|csv|ssv|yaml|yml|html|xml))`", re.IGNORECASE)


def branch_infos(conversation: ConversationRecord) -> list[BranchInfo]:
    main_nodes = set(conversation.linear_node_ids)
    main_edges = {
        parent: child
        for parent, child in zip(conversation.linear_node_ids, conversation.linear_node_ids[1:])
    }
    message_by_node = {message.node_id: message for message in conversation.all_messages}
    result: list[BranchInfo] = []
    for node in conversation.node_index:
        node_id = str(node.get("node_id") or "")
        if node_id not in main_nodes:
            continue
        children = [str(child) for child in node.get("children") or []]
        if len(children) < 2:
            continue
        main_child = main_edges.get(node_id)
        alternate_children = [child for child in children if child != main_child]
        if not alternate_children:
            continue
        message = message_by_node.get(node_id)
        result.append(
            BranchInfo(
                node_id=node_id,
                message_id=message.message_id if message else node.get("message_id"),
                main_child_id=main_child,
                alternate_child_ids=alternate_children,
            )
        )
    return result


def build_message_annotations(
    conversation: ConversationRecord,
    assets: list[AssetRecord],
    source_rows: list[dict[str, Any]],
    tool_rows: list[dict[str, Any]],
    textdoc_rows: list[dict[str, Any]] | None = None,
    asset_presentation_by_archive_path: dict[str, dict[str, str | None]] | None = None,
    unverified_payload_candidates_by_asset_ref_id: dict[str, list[UnverifiedPayloadCandidateRecord]] | None = None,
) -> dict[str, list[MessageAnnotation]]:
    annotations: dict[str, list[MessageAnnotation]] = {}
    textdoc_rows = textdoc_rows or []
    asset_presentation_by_archive_path = asset_presentation_by_archive_path or {}
    unverified_payload_candidates_by_asset_ref_id = unverified_payload_candidates_by_asset_ref_id or {}

    def add(message_id: str | None, annotation: MessageAnnotation) -> None:
        if not message_id:
            return
        annotations.setdefault(message_id, []).append(annotation)

    for row in source_rows:
        if row.get("source_family") == "file":
            continue
        add(
            row.get("message_id"),
            MessageAnnotation(
                kind="source",
                status=str(row.get("source_kind") or row.get("source_family") or "source"),
                label=_source_label(row),
                proof_path=row.get("proof_path"),
                detail=_source_detail(row),
                confidence=str(row.get("candidate_confidence") or row.get("evidence") or "explicit"),
                snippets=[str(row["snippet"])] if row.get("snippet") else [],
                cited_note_path=(
                    str(row.get("cited_note_path") or row.get("cited_note_candidate_path"))
                    if row.get("cited_note_path") or row.get("cited_note_candidate_path")
                    else None
                ),
            ),
        )

    grouped_tool_rows: dict[str, list[dict[str, Any]]] = {}
    for row in tool_rows:
        message_id = row.get("message_id")
        if not message_id:
            continue
        grouped_tool_rows.setdefault(str(message_id), []).append(row)
    for message_id, rows in grouped_tool_rows.items():
        if len(rows) > 5:
            add(message_id, _tool_group_annotation(rows))
            continue
        for row in rows:
            add(
                message_id,
                MessageAnnotation(
                    kind="tool",
                    status=str(row.get("event_kind") or "tool"),
                    label=_tool_label(row),
                    proof_path=row.get("proof_path"),
                    detail=row.get("url"),
                    snippets=[str(row["snippet"])] if row.get("snippet") else [],
                ),
            )

    messages_with_textdoc_rows: set[str] = set()
    exported_textdoc_ids = {
        str(row["textdoc_id"])
        for row in textdoc_rows
        if row.get("status") == "exported_payload" and row.get("textdoc_id")
    }
    for row in textdoc_rows:
        message_id = row.get("message_id")
        if message_id:
            messages_with_textdoc_rows.add(str(message_id))
        add(message_id, _textdoc_row_annotation(conversation, row, exported_textdoc_ids))

    grouped_assets: dict[tuple[str | None, str], list[AssetRecord]] = {}
    for asset in assets:
        presentation = asset_presentation_by_archive_path.get(asset.physical_archive_path or "", {})
        identity = file_presentation_identity(asset, presentation)
        grouped_assets.setdefault((asset.message_id, identity), []).append(asset)
    for (message_id, identity), grouped in grouped_assets.items():
        asset = max(grouped, key=asset_presentation_priority)
        presentation = asset_presentation_by_archive_path.get(asset.physical_archive_path or "", {})
        payload_candidates = unverified_payload_candidates_by_asset_ref_id.get(asset.asset_ref_id, [])
        add(
            message_id,
            MessageAnnotation(
                kind="file",
                status=_asset_annotation_status(asset, payload_candidates),
                label=asset.reconstructed_filename or asset.raw_file_id or "unknown file",
                proof_path=asset.proof_path,
                detail=_asset_detail(asset, payload_candidates),
                confidence=asset.origin_confidence,
                related_ids=sorted({row.raw_file_id for row in grouped if row.raw_file_id}),
                presentation_identity=identity,
                copied_pack_path=asset.copied_pack_path or asset.copied_relative_path,
                canonical_archive_path=presentation.get("canonical_archive_path"),
            ),
        )

    for message in conversation.messages:
        picture_annotation = picture_v2_annotation(conversation, message)
        if picture_annotation:
            add(message.message_id, picture_annotation)

        if (message.message_id or "") not in messages_with_textdoc_rows:
            for annotation in structural_textdoc_annotations(conversation, message):
                add(message.message_id, annotation)

        annotation = generated_artifact_gap_annotation(conversation, message)
        if not annotation:
            continue
        related_assets = [
            asset
            for asset in assets
            if asset.message_id == message.message_id and asset.asset_status == "found"
        ]
        if related_assets or has_textdoc_payload(message):
            continue
        add(message.message_id, annotation)

    return annotations


def file_presentation_identity(asset: AssetRecord, presentation: dict[str, str | None]) -> str:
    """Stable identity for a visible file, independent of its rendered label."""
    if asset.raw_file_id:
        return f"raw_file_id:{asset.raw_file_id}"
    if presentation.get("content_equivalence_group_id"):
        return f"content_equivalence_group:{presentation['content_equivalence_group_id']}"
    if presentation.get("canonical_archive_path"):
        return f"canonical_archive_path:{presentation['canonical_archive_path']}"
    return f"reference:{asset.message_id or 'unknown'}:{asset.proof_path}:{asset.asset_ref_id}"


def asset_presentation_priority(asset: AssetRecord) -> tuple[int, int, str]:
    """Choose the most useful evidence row when one logical file has repeats."""
    status_rank = {"found": 3, "collision": 2, "missing": 1}.get(asset.asset_status, 0)
    copied_rank = 1 if asset.copied_pack_path or asset.copied_relative_path else 0
    return status_rank, copied_rank, asset.asset_ref_id


def add_branch_annotations(
    annotations: dict[str, list[MessageAnnotation]],
    branches: list[BranchInfo],
) -> dict[str, list[MessageAnnotation]]:
    for branch in branches:
        if not branch.message_id:
            continue
        continuation_count = len(branch.alternate_child_ids) + (1 if branch.main_child_id else 0)
        alternate_links = [
            f"→ Alternate branch: [[#{branch_anchor(branch.node_id)}]]"
            for _child_id in branch.alternate_child_ids
        ]
        annotations.setdefault(branch.message_id, []).append(
            MessageAnnotation(
                kind="branch",
                status="branch_point",
                label=f"{continuation_count} continuation{'s' if continuation_count != 1 else ''}",
                detail="\n".join(["→ Main path continues below", *alternate_links]),
                proof_path=f"mapping.{branch.node_id}.children",
            )
        )
    return annotations


def branch_anchor(node_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", node_id).strip("-")
    return f"branch-{safe or 'unknown-node'}"


def json_only_annotations(message: MessageRecord) -> list[MessageAnnotation]:
    metadata = message.raw_metadata if isinstance(message.raw_metadata, dict) else {}
    annotations: list[MessageAnnotation] = []
    if message.content_type in {"thoughts", "reasoning_recap"}:
        annotations.append(
            MessageAnnotation(
                kind="json",
                status=message.content_type,
                label="JSON-only assistant activity record",
                proof_path=f"mapping.{message.node_id}.message.content",
                detail="Record is preserved in JSON evidence and has no normal visible transcript text.",
            )
        )
    thoughts = metadata.get("thoughts")
    if isinstance(thoughts, list) and thoughts:
        annotations.append(
            MessageAnnotation(
                kind="json",
                status="thoughts",
                label=f"{len(thoughts)} hidden reasoning/thought row(s)",
                proof_path=f"mapping.{message.node_id}.message.metadata.thoughts",
                detail="Reasoning metadata is present in the export; content remains canonical in JSONL/raw JSON.",
            )
        )
    if metadata.get("reasoning_recap"):
        annotations.append(
            MessageAnnotation(
                kind="json",
                status="reasoning_recap",
                label="Reasoning recap metadata present",
                proof_path=f"mapping.{message.node_id}.message.metadata.reasoning_recap",
                detail="Recap metadata is present in the export; content remains canonical in JSONL/raw JSON.",
            )
        )
    return annotations


def _asset_detail(
    asset: AssetRecord,
    payload_candidates: list[UnverifiedPayloadCandidateRecord] | None = None,
) -> str | None:
    label = asset.reconstructed_filename or asset.raw_file_id or "unknown file"
    details: list[str] = []
    if asset.file_role in {"knowledge", "library"}:
        details.append(f"Role: `{asset.file_role}`")
    if asset.knowledge_store_id:
        details.append(f"Knowledge Store: `{asset.knowledge_store_id}`")
    if asset.library_file_id:
        details.append(f"Library file: `{asset.library_file_id}`")
    if asset.image_gen_generation_id:
        details.append(f"Image generation ID: `{asset.image_gen_generation_id}`")
    if asset.origination_message_id and asset.message_id != asset.origination_message_id:
        details.append(f"Declared origin message (not exported): `{asset.origination_message_id}`")
    if asset.asset_status == "missing":
        details.append(f"Referenced file ID has no physical payload in export: `{asset.raw_file_id or label}`")
        if payload_candidates:
            details.append("Related payload candidate(s) elsewhere in export; identity, content, and version are not demonstrated.")
            for candidate in payload_candidates:
                details.append(f"Candidate OpenAI file ID: `{candidate.candidate_file_id}`")
                details.append(f"Candidate physical payload: `{candidate.candidate_physical_archive_path}`")
                details.append("Name match: URL-decoded logical basename equality.")
                if candidate.reference_declared_size is not None:
                    details.append(f"Referenced declared size: `{candidate.reference_declared_size}` bytes")
                if candidate.candidate_size is not None:
                    details.append(f"Candidate payload size: `{candidate.candidate_size}` bytes")
                if candidate.candidate_payload_disposition_status:
                    details.append(f"Candidate disposition: `{candidate.candidate_payload_disposition_status}`")
                if candidate.candidate_count_for_name > 1:
                    details.append(f"Ambiguity: `{candidate.candidate_count_for_name}` same-name payload candidate(s).")
            details.append("Details: `90_Evidence/unverified_payload_candidates.jsonl`")
        else:
            details.append(f"Physical payload missing from export for this reference: {label}")
    elif asset.asset_status == "collision":
        details.append(f"Physical payload ambiguous: multiple candidates for {label}")
    elif asset.physical_archive_path:
        details.append(f"Physical payload found: `{asset.physical_archive_path}`")
    if asset.copied_pack_path or asset.copied_relative_path:
        details.append(f"Copied path: `{asset.copied_pack_path or asset.copied_relative_path}`")
    if asset.copied_filename and asset.asset_status == "found":
        details.append(_asset_embed(asset))
    return "\n".join(details) if details else None


def _asset_embed(asset: AssetRecord) -> str:
    filename = asset.copied_filename or asset.reconstructed_filename or ""
    extension = (asset.normalized_extension or Path(filename).suffix.lstrip(".")).lower()
    if extension in {"png", "jpg", "jpeg", "webp", "gif", "avif", "bmp", "svg"}:
        return f"![[{filename}|300]]"
    return f"![[{filename}]]"


def _asset_annotation_status(
    asset: AssetRecord,
    payload_candidates: list[UnverifiedPayloadCandidateRecord] | None = None,
) -> str:
    if payload_candidates:
        return "exact_id_missing_unverified_payload_candidate"
    suffix = "found" if asset.asset_status == "found" else "collision" if asset.asset_status == "collision" else "missing"
    if asset.image_gen_generation_id:
        return f"knowledge_generated_image_{suffix}" if asset.knowledge_store_id else f"library_generated_image_{suffix}"
    if asset.knowledge_store_id:
        return f"knowledge_file_{suffix}"
    if asset.library_file_id:
        return f"library_file_{suffix}"
    return asset.asset_status


def _source_label(row: dict[str, Any]) -> str:
    return str(row.get("title") or row.get("file_id") or row.get("url") or row.get("source_kind") or "source")


def _source_detail(row: dict[str, Any]) -> str | None:
    parts = []
    if row.get("payload_status") == "not_exported" or row.get("asset_status") == "missing":
        parts.append("payload not exported")
    if row.get("payload_status") == "ambiguous" or row.get("asset_status") == "collision":
        parts.append("physical payload ambiguous")
    if row.get("candidate_role"):
        parts.append(str(row["candidate_role"]))
    if row.get("url"):
        parts.append(str(row["url"]))
    return " · ".join(parts) if parts else None


def _tool_label(row: dict[str, Any]) -> str:
    return str(row.get("title") or row.get("tool_name") or row.get("event_kind") or "tool evidence")


def _tool_group_annotation(rows: list[dict[str, Any]]) -> MessageAnnotation:
    counts: dict[str, int] = {}
    for row in rows:
        event_kind = str(row.get("event_kind") or "tool")
        counts[event_kind] = counts.get(event_kind, 0) + 1
    detail_lines = [f"{kind}: `{count}`" for kind, count in sorted(counts.items())]
    preview_rows = [row for row in rows if row.get("url")][:3]
    for row in preview_rows:
        label = row.get("title") or row.get("tool_name") or row.get("event_kind") or "tool evidence"
        detail_lines.append(f"- {label}: {row['url']}")
    detail_lines.append("Full rows: `90_Evidence/tool_events.jsonl`")
    return MessageAnnotation(
        kind="tool",
        status="tool_retrieval_summary",
        label=f"{len(rows)} tool/retrieval evidence rows",
        proof_path=rows[0].get("proof_path"),
        detail="\n".join(detail_lines),
        snippets=list(
            dict.fromkeys(str(row["snippet"]) for row in rows if row.get("snippet"))
        ),
    )


def _textdoc_row_annotation(
    conversation: ConversationRecord,
    row: dict[str, Any],
    exported_textdoc_ids: set[str],
) -> MessageAnnotation:
    status = str(row.get("status") or "textdoc")
    mapped_status = {
        "exported_payload": "structural_textdoc_found",
        "canvas_reference_only": "canvas_textdoc_reference_found",
        "canmore_uri_reference": "canmore_uri_reference",
    }.get(status, status)
    label = {
        "exported_payload": "Text document payload present in export",
        "canmore_uri_reference": "Canmore textdoc URI present in export",
    }.get(status, "Textdoc evidence present in export")
    textdoc_id = str(row["textdoc_id"]) if row.get("textdoc_id") else None
    if status == "canvas_reference_only":
        label = (
            "Canvas TextDoc reference; payload exported elsewhere in this conversation"
            if textdoc_id and textdoc_id in exported_textdoc_ids
            else "Canvas TextDoc reference; payload absent from export"
        )
    detail_parts = []
    title = row.get("title")
    if title and str(title) != textdoc_id:
        detail_parts.append(f"Title: `{title}`")
    if textdoc_id:
        detail_parts.append(f"Textdoc ID: `{textdoc_id}`")
    if row.get("content_length") is not None:
        detail_parts.append(f"Content length: `{row['content_length']}` characters")
    if row.get("copied_pack_path"):
        label_name = row.get("copied_filename") or str(row["copied_pack_path"]).replace("\\", "/").split("/")[-1]
        detail_parts.append(f"File: [[{label_name}]]")
    if row.get("canmore_uri"):
        detail_parts.append(f"URI: `{row['canmore_uri']}`")
    if status == "canvas_reference_only":
        if textdoc_id and textdoc_id in exported_textdoc_ids:
            detail_parts.append("A Canvas/TextDoc payload for this ID is exported elsewhere in this conversation.")
        else:
            detail_parts.extend(
                [
                    "No Canvas/TextDoc payload or file for this ID is included in the export.",
                    _original_conversation_check(conversation),
                ]
            )
    return MessageAnnotation(
        kind="artifact",
        status=mapped_status,
        label=label,
        proof_path=row.get("proof_path"),
        detail="\n".join(detail_parts) if detail_parts else None,
    )


def picture_v2_annotation(conversation: ConversationRecord, message: MessageRecord) -> MessageAnnotation | None:
    if message.author_role != "user":
        return None
    metadata = message.raw_metadata if isinstance(message.raw_metadata, dict) else {}
    system_hints = metadata.get("system_hints")
    if not isinstance(system_hints, list) or "picture_v2" not in system_hints:
        return None
    detail_lines = [
        "Image generation mode is explicit in `system_hints`, but no generated image payload is present in this exported message.",
        f"Check original conversation: https://chatgpt.com/c/{conversation.conversation_id}",
    ]
    if "image_results" in metadata:
        detail_lines.insert(1, f"Exported `image_results`: `{metadata.get('image_results')}`")
    if metadata.get("image_send_uuid"):
        detail_lines.append(f"Image send UUID: `{metadata['image_send_uuid']}`")
    if metadata.get("image_prompt_id"):
        detail_lines.append(f"Image prompt ID: `{metadata['image_prompt_id']}`")
    dalle = metadata.get("dalle")
    operation = dalle.get("from_client", {}).get("operation") if isinstance(dalle, dict) else None
    if isinstance(operation, dict):
        if operation.get("type"):
            detail_lines.append(f"DALL-E operation: `{operation['type']}`")
        for key in ("original_file_id", "mask_file_id", "original_gen_id"):
            if operation.get(key):
                detail_lines.append(f"{key}: `{operation[key]}`")
    return MessageAnnotation(
        kind="artifact",
        status="image_generation_missing_from_export",
        label="Image generation turn detected; generated image not included in export",
        proof_path=f"mapping.{message.node_id}.message.metadata.system_hints",
        detail="\n".join(detail_lines),
        confidence="explicit_system_hint",
    )


def generated_artifact_gap_annotation(
    conversation: ConversationRecord,
    message: MessageRecord,
) -> MessageAnnotation | None:
    if message.author_role != "assistant":
        return None
    metadata = message.raw_metadata if isinstance(message.raw_metadata, dict) else {}
    text = message.text or ""
    document_name = _mentioned_document_name(text)
    if any(pattern.search(text) for pattern in DOCUMENT_PATTERNS):
        detail_lines = []
        if document_name:
            detail_lines.append(f"Mentioned document: `{document_name}`")
        detail_lines.extend(
            [
                "Assistant text claims that a TextDoc, document, or file was opened or generated.",
                "No Canvas/TextDoc payload, tool event, or exported file was found for this message.",
                _original_conversation_check(conversation),
            ]
        )
        return MessageAnnotation(
            kind="artifact",
            status="assistant_claim_without_payload",
            label="Text document or file mentioned by assistant, but not exported",
            proof_path=f"mapping.{message.node_id}.message.content.parts",
            detail="\n".join(detail_lines),
            confidence="textual_claim_only",
        )
    if "image_results" in metadata and metadata.get("image_results") in ([], None):
        return _generic_artifact_gap(conversation, message)
    if any(pattern.search(text) for pattern in GENERATION_PATTERNS):
        return _generic_artifact_gap(conversation, message)
    return None


def _generic_artifact_gap(conversation: ConversationRecord, message: MessageRecord) -> MessageAnnotation:
    return MessageAnnotation(
        kind="artifact",
        status="mentioned_not_exported",
        label="Generated artifact mentioned by assistant, but not exported",
        proof_path=f"mapping.{message.node_id}.message.content.parts",
        detail=(
            "Assistant text or metadata indicates a generated output, but no found asset row is attached to this message.\n"
            + _original_conversation_check(conversation)
        ),
        confidence="heuristic",
    )


def _original_conversation_check(conversation: ConversationRecord) -> str:
    if conversation.chat_url:
        return f"[Check original conversation]({conversation.chat_url}) for the Canvas document or download."
    return "The original ChatGPT conversation URL is unavailable in this export."


def _mentioned_document_name(text: str) -> str | None:
    match = DOCUMENT_NAME_RE.search(text or "")
    return match.group(1) if match else None


def has_textdoc_payload(message: MessageRecord) -> bool:
    metadata = message.raw_metadata if isinstance(message.raw_metadata, dict) else {}
    canvas = metadata.get("canvas")
    if not isinstance(canvas, dict):
        return False
    textdocs = canvas.get("user_created_textdocs")
    return isinstance(textdocs, list) and any(
        isinstance(textdoc, dict) and isinstance(textdoc.get("content"), str)
        for textdoc in textdocs
    )


def structural_textdoc_annotations(
    conversation: ConversationRecord,
    message: MessageRecord,
) -> list[MessageAnnotation]:
    metadata = message.raw_metadata if isinstance(message.raw_metadata, dict) else {}
    canvas = metadata.get("canvas")
    if isinstance(canvas, dict):
        textdocs = canvas.get("user_created_textdocs")
        if isinstance(textdocs, list) and textdocs:
            annotations = []
            for index, textdoc in enumerate(textdocs):
                if not isinstance(textdoc, dict):
                    continue
                textdoc_id = textdoc.get("textdoc_id") or textdoc.get("id")
                title = textdoc.get("title") or textdoc.get("name") or textdoc_id or "text document"
                content = textdoc.get("content")
                detail_parts = [f"Title: `{title}`"]
                if textdoc_id:
                    detail_parts.append(f"Textdoc ID: `{textdoc_id}`")
                if isinstance(content, str):
                    detail_parts.append(f"Content length: `{len(content)}` characters")
                annotations.append(
                    MessageAnnotation(
                        kind="artifact",
                        status="structural_textdoc_found",
                        label="Text document payload present in export",
                        proof_path=f"mapping.{message.node_id}.message.metadata.canvas.user_created_textdocs[{index}]",
                        detail="\n".join(detail_parts),
                    )
                )
            return annotations
    open_view = metadata.get("open_in_canvas_view")
    if isinstance(open_view, dict) and open_view.get("type") == "canvas_textdoc":
        textdoc_id = open_view.get("id")
        detail_parts = [f"Textdoc ID: `{textdoc_id}`"] if textdoc_id else ["Canvas TextDoc view is referenced."]
        detail_parts.extend(
            [
                "No Canvas/TextDoc payload or file for this ID is included in the export.",
                _original_conversation_check(conversation),
            ]
        )
        return [
            MessageAnnotation(
                kind="artifact",
                status="canvas_textdoc_reference_found",
                label="Canvas TextDoc reference; payload absent from export",
                proof_path=f"mapping.{message.node_id}.message.metadata.open_in_canvas_view",
                detail="\n".join(detail_parts),
            )
        ]
    return []
