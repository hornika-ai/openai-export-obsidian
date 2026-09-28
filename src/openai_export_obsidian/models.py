from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


ASSET_STATUSES = frozenset({"found", "missing", "collision"})


def is_unresolved_asset_status(status: str) -> bool:
    return status != "found"


@dataclass(frozen=True)
class ArchiveMember:
    outer_zip: str
    name: str
    size: int
    nested_zip: str | None = None
    inner_name: str | None = None
    nested_chain: tuple[str, ...] = ()

    @property
    def archive_path(self) -> str:
        chain = self.nested_chain or ((self.nested_zip,) if self.nested_zip else ())
        if chain:
            return "::".join((*chain, self.inner_name or self.name))
        return self.name

    @property
    def basename(self) -> str:
        return self.name.replace("\\", "/").rstrip("/").split("/")[-1]


@dataclass
class MessageRecord:
    node_id: str
    message_id: str | None
    parent_id: str | None
    child_ids: list[str]
    author_role: str | None
    create_time: str | None
    update_time: str | None
    model_slug: str | None
    default_model_slug: str | None
    channel: str | None
    end_turn: bool | None
    status: str | None
    content_type: str | None
    text: str
    non_text_parts: list[Any] = field(default_factory=list)
    content_references: list[Any] = field(default_factory=list)
    context_citations: list[dict[str, Any]] = field(default_factory=list)
    citations: list[Any] = field(default_factory=list)
    safe_urls: list[str] = field(default_factory=list)
    code_blocks: list[dict[str, str]] = field(default_factory=list)
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConversationRecord:
    conversation_id: str
    title: str | None
    create_time: str | None
    update_time: str | None
    source_json_file: str
    source_json_shard: str
    source_archive_path: str
    chat_url: str | None
    conversation_template_id: str | None
    gizmo_type: str | None
    custom_gpt_id: str | None
    custom_gpt_name: str | None
    custom_gpt_url: str | None
    default_model_slug: str | None
    resolved_model_slugs: list[str]
    memory_scope: Any
    voice: str | None
    is_archived: bool | None
    is_starred: bool | None
    is_do_not_remember: bool | None
    project_id: str | None
    project_name: str | None
    shared_status: str
    shared_conversation: dict[str, Any] | None
    message_count: int
    linear_node_ids: list[str]
    linear_message_ids: list[str]
    messages: list[MessageRecord]
    all_messages: list[MessageRecord]
    node_index: list[dict[str, Any]]
    raw_metadata: dict[str, Any]
    parse_warnings: list[str] = field(default_factory=list)

    def index_dict(self, asset_count: int = 0, resolved_asset_count: int = 0, missing_asset_count: int = 0) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "title": self.title,
            "create_time": self.create_time,
            "update_time": self.update_time,
            "source_json_file": self.source_json_file,
            "source_json_shard": self.source_json_shard,
            "source_archive_path": self.source_archive_path,
            "chat_url": self.chat_url,
            "conversation_template_id": self.conversation_template_id,
            "gizmo_type": self.gizmo_type,
            "custom_gpt_id": self.custom_gpt_id,
            "custom_gpt_name": self.custom_gpt_name,
            "custom_gpt_url": self.custom_gpt_url,
            "default_model_slug": self.default_model_slug,
            "resolved_model_slugs": self.resolved_model_slugs,
            "memory_scope": self.memory_scope,
            "voice": self.voice,
            "is_archived": self.is_archived,
            "is_starred": self.is_starred,
            "is_do_not_remember": self.is_do_not_remember,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "shared_status": self.shared_status,
            "message_count": self.message_count,
            "asset_count": asset_count,
            "resolved_asset_count": resolved_asset_count,
            "missing_asset_count": missing_asset_count,
        }

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["messages"] = [message.to_dict() for message in self.messages]
        return data


@dataclass
class AssetRecord:
    asset_ref_id: str
    conversation_id: str
    message_id: str | None
    source_shard: str
    proof_path: str
    raw_file_id: str | None
    raw_dat_filename: str | None
    physical_archive_path: str | None
    reconstructed_filename: str | None
    normalized_extension: str | None
    mime_type: str | None
    size: int | None
    width: int | None
    height: int | None
    source_origin_fields: dict[str, Any]
    library_file_id: str | None
    origination_message_id: str | None
    origination_thread_id: str | None
    asset_status: str
    provenance_status: str
    origin_classification: str
    origin_confidence: str
    copied_relative_path: str | None = None
    copied_filename: str | None = None
    file_role: str = "attachment"
    copied_pack_path: str | None = None
    knowledge_store_id: str | None = None
    library_gizmo_id: str | None = None
    image_gen_generation_id: str | None = None
    raw_reference: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TextdocRecord:
    textdoc_ref_id: str
    conversation_id: str
    message_id: str | None
    node_id: str
    status: str
    textdoc_id: str | None
    title: str | None
    textdoc_type: str | None
    content_length: int | None
    content_sha256: str | None
    copied_pack_path: str | None
    proof_path: str
    source_shard: str
    canmore_uri: str | None = None
    copied_filename: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
