from __future__ import annotations

from typing import Any

from .models import ConversationRecord, MessageRecord
from .utils import as_iso, basename, compact_raw, extract_code_blocks, extract_urls, first_present


def parse_conversation(raw: dict[str, Any], source_member, shared_by_conversation: dict[str, dict[str, Any]]) -> ConversationRecord:
    conversation_id = str(first_present(raw.get("id"), raw.get("conversation_id"), "unknown-conversation"))
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    mapping = raw.get("mapping") if isinstance(raw.get("mapping"), dict) else {}
    warnings: list[str] = []

    node_index: list[dict[str, Any]] = []
    message_by_node: dict[str, MessageRecord] = {}
    for node_id in sorted(mapping):
        node = mapping.get(node_id) or {}
        message = node.get("message") if isinstance(node, dict) else None
        node_index.append(build_node_index_entry(node_id, node, message))
        if isinstance(message, dict):
            message_by_node[node_id] = parse_message(node_id, node, message)

    linear_node_ids = reconstruct_linear_branch(mapping, raw.get("current_node"))
    linear_messages = [message_by_node[node_id] for node_id in linear_node_ids if node_id in message_by_node]
    if not linear_messages:
        linear_messages = [message_by_node[node_id] for node_id in sorted(message_by_node)]
        if message_by_node:
            warnings.append("main_branch_reconstruction_failed_used_sorted_messages")

    all_messages = [message_by_node[node_id] for node_id in sorted(message_by_node)]
    model_slugs = sorted({m.model_slug for m in all_messages if m.model_slug})
    default_model_slug = first_present(
        raw.get("default_model_slug"),
        metadata.get("default_model_slug"),
        next((m.default_model_slug for m in all_messages if m.default_model_slug), None),
    )
    conversation_template_id = first_present(
        raw.get("conversation_template_id"),
        metadata.get("conversation_template_id"),
        metadata.get("template_id"),
    )
    gizmo_type = first_present(raw.get("gizmo_type"), metadata.get("gizmo_type"))
    raw_gizmo_id = first_present(
        raw.get("gizmo_id"),
        metadata.get("gizmo_id"),
        metadata.get("custom_gpt_id"),
        next((m.raw_metadata.get("gizmo_id") for m in all_messages if m.raw_metadata.get("gizmo_id")), None),
    )
    is_chatgpt_project = (
        gizmo_type == "snorlax"
        or is_chatgpt_project_id(raw_gizmo_id)
        or (is_chatgpt_project_id(conversation_template_id) and not is_custom_gpt_id(raw_gizmo_id))
    )
    custom_gpt_id = None if is_chatgpt_project else raw_gizmo_id
    custom_gpt_url = f"https://chatgpt.com/g/{custom_gpt_id}" if isinstance(custom_gpt_id, str) and custom_gpt_id.startswith("g-") else None
    shared = shared_by_conversation.get(conversation_id)

    project_id = first_present(
        raw.get("project_id"),
        metadata.get("project_id"),
        metadata.get("workspace_id"),
        raw_gizmo_id if is_chatgpt_project_id(raw_gizmo_id) else None,
        conversation_template_id
        if is_chatgpt_project_id(conversation_template_id) and not is_custom_gpt_id(raw_gizmo_id)
        else None,
    )
    project_name = first_present(raw.get("project_name"), metadata.get("project_name"), metadata.get("workspace_name"))

    return ConversationRecord(
        conversation_id=conversation_id,
        title=raw.get("title"),
        create_time=as_iso(raw.get("create_time")),
        update_time=as_iso(raw.get("update_time")),
        source_json_file=source_member.archive_path,
        source_json_shard=basename(source_member.name),
        source_archive_path=source_member.archive_path,
        chat_url=f"https://chatgpt.com/c/{conversation_id}" if conversation_id != "unknown-conversation" else None,
        conversation_template_id=conversation_template_id,
        gizmo_type=gizmo_type,
        custom_gpt_id=custom_gpt_id,
        custom_gpt_name=first_present(raw.get("gizmo_name"), metadata.get("gizmo_name"), metadata.get("custom_gpt_name")),
        custom_gpt_url=custom_gpt_url,
        default_model_slug=default_model_slug,
        resolved_model_slugs=model_slugs,
        memory_scope=first_present(raw.get("memory_scope"), metadata.get("memory_scope")),
        voice=raw.get("voice") if isinstance(raw.get("voice"), str) else None,
        is_archived=raw.get("is_archived") if isinstance(raw.get("is_archived"), bool) else None,
        is_starred=raw.get("is_starred") if isinstance(raw.get("is_starred"), bool) else None,
        is_do_not_remember=raw.get("is_do_not_remember") if isinstance(raw.get("is_do_not_remember"), bool) else None,
        project_id=project_id,
        project_name=project_name,
        shared_status="explicit" if shared else "unknown",
        shared_conversation=shared,
        message_count=len(all_messages),
        linear_node_ids=linear_node_ids,
        linear_message_ids=[m.message_id for m in linear_messages if m.message_id],
        messages=linear_messages,
        all_messages=all_messages,
        node_index=node_index,
        raw_metadata=compact_raw(metadata),
        parse_warnings=warnings,
    )


def is_chatgpt_project_id(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("g-p-")


def is_custom_gpt_id(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("g-") and not value.startswith("g-p-")


def build_node_index_entry(node_id: str, node: dict[str, Any], message: Any) -> dict[str, Any]:
    entry = {
        "node_id": node_id,
        "parent": node.get("parent"),
        "children": node.get("children") or [],
        "has_message": isinstance(message, dict),
    }
    if isinstance(message, dict):
        entry.update(
            {
                "message_id": message.get("id"),
                "author_role": ((message.get("author") or {}).get("role") if isinstance(message.get("author"), dict) else None),
                "create_time": as_iso(message.get("create_time")),
                "status": message.get("status"),
                "content_type": (message.get("content") or {}).get("content_type") if isinstance(message.get("content"), dict) else None,
            }
        )
    return entry


def parse_message(node_id: str, node: dict[str, Any], message: dict[str, Any]) -> MessageRecord:
    author = message.get("author") if isinstance(message.get("author"), dict) else {}
    content = message.get("content") if isinstance(message.get("content"), dict) else {}
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    text, non_text = extract_text_and_non_text(content)
    urls = extract_urls(text)
    code_blocks = extract_code_blocks(text)
    raw_metadata = compact_raw(metadata)
    preserve_empty_metadata(raw_metadata, metadata, ("image_results",))
    return MessageRecord(
        node_id=node_id,
        message_id=message.get("id"),
        parent_id=node.get("parent") or metadata.get("parent_id"),
        child_ids=list(node.get("children") or []),
        author_role=author.get("role"),
        create_time=as_iso(message.get("create_time")),
        update_time=as_iso(message.get("update_time")),
        model_slug=metadata.get("model_slug"),
        default_model_slug=metadata.get("default_model_slug"),
        channel=message.get("channel"),
        end_turn=message.get("end_turn"),
        status=message.get("status"),
        content_type=content.get("content_type"),
        text=text,
        non_text_parts=non_text,
        content_references=list(metadata.get("content_references") or []),
        context_citations=extract_context_citations(metadata, node_id, message.get("id")),
        citations=list(metadata.get("citations") or []),
        safe_urls=urls,
        code_blocks=code_blocks,
        raw_metadata=raw_metadata,
    )


def preserve_empty_metadata(raw_metadata: dict[str, Any], metadata: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        if key in metadata and metadata.get(key) == []:
            raw_metadata[key] = []


def extract_context_citations(metadata: dict[str, Any], node_id: str, message_id: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(metadata.get("conversation_context_citation_metadata") or []):
        if not isinstance(item, dict):
            continue
        citation = item.get("citation") if isinstance(item.get("citation"), dict) else {}
        url = citation.get("url")
        cited_conversation_id = conversation_id_from_context_url(url)
        context_kind = citation.get("conversation_context_type") or citation.get("type") or "unknown"
        rows.append(
            {
                "source": "conversation_context_citation_metadata",
                "proof_path": f"mapping.{node_id}.message.metadata.conversation_context_citation_metadata[{index}]",
                "message_id": message_id,
                "citation_uuid": item.get("citation_uuid") or citation.get("citation_uuid"),
                "context_kind": context_kind,
                "attribution": citation.get("attribution"),
                "title": citation.get("conversation_title") or citation.get("title"),
                "cited_conversation_id": cited_conversation_id,
                "memory_id": citation.get("memory_id"),
                "raw_url": url,
                "chat_url": f"https://chatgpt.com/c/{cited_conversation_id}" if cited_conversation_id else None,
                "source_url": url if isinstance(url, str) else None,
                "snippet": citation.get("snippet"),
                "reason": citation.get("reason"),
                "category": citation.get("category"),
                "retrieval_origin": citation.get("retrieval_origin") or item.get("retrieval_origin"),
                "pub_date": as_iso(citation.get("pub_date")),
                "status": "explicit",
                "raw": compact_raw(item),
            }
        )
    return rows


def conversation_id_from_context_url(url: Any) -> str | None:
    if not isinstance(url, str):
        return None
    marker = "/c/"
    if marker not in url:
        return None
    tail = url.split(marker, 1)[1]
    return tail.split("?", 1)[0].split("#", 1)[0].strip("/") or None


def extract_text_and_non_text(content: dict[str, Any]) -> tuple[str, list[Any]]:
    parts = content.get("parts")
    text_parts: list[str] = []
    non_text: list[Any] = []
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
                else:
                    non_text.append(compact_raw(part))
            else:
                non_text.append(part)
    elif isinstance(parts, str):
        text_parts.append(parts)
    elif isinstance(content.get("text"), str):
        text_parts.append(content["text"])
    return "\n".join(part for part in text_parts if part).strip(), non_text


def reconstruct_linear_branch(mapping: dict[str, Any], current_node: str | None) -> list[str]:
    if current_node and current_node in mapping:
        branch: list[str] = []
        seen: set[str] = set()
        node_id = current_node
        while node_id and node_id in mapping and node_id not in seen:
            seen.add(node_id)
            branch.append(node_id)
            node = mapping.get(node_id) or {}
            node_id = node.get("parent")
        return list(reversed(branch))

    roots = [node_id for node_id, node in mapping.items() if not (node or {}).get("parent")]
    if not roots:
        return []
    branch = []
    node_id = sorted(roots)[0]
    seen = set()
    while node_id and node_id not in seen:
        seen.add(node_id)
        branch.append(node_id)
        children = (mapping.get(node_id) or {}).get("children") or []
        node_id = sorted(children)[0] if children else None
    return branch
