"""Export a public, deterministic contract for the readable parser pack.

This module deliberately constructs its example from a tiny synthetic archive.  It
never accepts an export path and it never reads an existing pack: consumers can
inspect the emitted-record contract without granting this command access to user
content.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import tomllib
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .runner import SCHEMA_VERSION, run_parse
from .validation import validate_pack
from .ds_store_hygiene import ds_store_diagnostics


CONTRACT_VERSION = "1.0.2"
CONTRACT_ID = "openai-export-obsidian-parser-pack"
FIXTURE_GENERATED_AT = "2000-01-01T00:00:00Z"
SYNTHETIC_SOURCE_NAME = "synthetic-input.zip"
OPTIONAL_ROOT_NAVIGATION_FILES = ("00_Home.md",)
PACK_ROOT_DIRECTORIES = (
    "10_Conversations",
    "20_Files",
    "30_Contexts",
    "40_Views",
    "90_Evidence",
    "_logs",
    "logs",
)
CONTRACT_SOURCE_DEFINITIONS = [
    {"path": "pyproject.toml", "symbol": "project.version", "role": "parser_version"},
    {"path": "src/openai_export_obsidian/runner.py", "symbol": "SCHEMA_VERSION", "role": "pack_schema"},
    {"path": "src/openai_export_obsidian/runner.py", "symbol": "build_pack_manifest", "role": "pack manifest serializer"},
    {"path": "src/openai_export_obsidian/runner.py", "symbol": "emit_readable_outputs", "role": "readable pack writer"},
    {"path": "src/openai_export_obsidian/conversation_note_locator.py", "symbol": "write_conversation_note_locators", "role": "conversation note locator writer"},
    {"path": "src/openai_export_obsidian/runner.py", "symbol": "readable_conversation_row", "role": "conversation serializer"},
    {"path": "src/openai_export_obsidian/models.py", "symbol": "MessageRecord.to_dict", "role": "message serializer"},
    {"path": "src/openai_export_obsidian/models.py", "symbol": "AssetRecord.to_dict", "role": "asset serializer"},
    {"path": "src/openai_export_obsidian/validation.py", "symbol": "validate_pack", "role": "inter-file validator"},
]


def export_pack_contract(output: Path) -> Path:
    """Create *output* or verify it is already byte-identical.

    A non-identical existing directory is never overwritten.  Building in a
    temporary directory first also prevents partially written contract bundles.
    """
    output = Path(output)
    with tempfile.TemporaryDirectory(prefix="parser-pack-contract-") as temporary:
        candidate = Path(temporary) / "bundle"
        _write_bundle(candidate)
        _assert_no_absolute_paths(candidate)
        if output.exists():
            if _tree_hashes(output) == _tree_hashes(candidate):
                return output
            raise FileExistsError(f"refusing to overwrite different contract bundle: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(candidate, output)
    return output


def validate_contract_bundle(bundle: Path) -> list[str]:
    """Validate the generated public contract and return all detected errors."""
    bundle = Path(bundle)
    errors: list[str] = []
    try:
        manifest = _read_json(bundle / "contract-manifest.json")
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid contract-manifest.json: {exc}"]
    if manifest.get("pack_schema") != SCHEMA_VERSION:
        errors.append("contract pack_schema does not match runner.SCHEMA_VERSION")
    if manifest.get("contract_version") != CONTRACT_VERSION:
        errors.append("contract_version does not match exporter")
    if manifest.get("contract_bundle_version") != CONTRACT_VERSION:
        errors.append("contract_bundle_version does not match exporter")
    for relative, expected_hash in manifest.get("file_hashes", {}).items():
        path = bundle / relative
        if not path.exists():
            errors.append(f"manifest hash target missing: {relative}")
        elif _sha256(path) != expected_hash:
            errors.append(f"manifest hash mismatch: {relative}")

    synthetic = bundle / "synthetic-example" / "pack"
    if not synthetic.exists():
        return [*errors, "synthetic example pack missing"]
    errors.extend(f"synthetic pack layout: {error}" for error in validate_pack_layout(synthetic))
    report = validate_pack(synthetic)
    errors.extend(f"synthetic pack: {error}" for error in report["errors"])
    schemas = {
        "pack_manifest.json": _read_json(bundle / "pack-manifest.schema.json"),
        "conversations.jsonl": _read_json(bundle / "conversation-record.schema.json"),
        "messages.jsonl": _read_json(bundle / "message-record.schema.json"),
        "asset_links.jsonl": _read_json(bundle / "asset-link-record.schema.json"),
        "conversation_note_locators.jsonl": _read_json(bundle / "conversation-note-locator.schema.json"),
    }
    evidence = synthetic / "90_Evidence"
    for filename, schema in schemas.items():
        path = synthetic / "40_Views" / filename if filename == "conversation_note_locators.jsonl" else evidence / filename
        if not path.exists():
            errors.append(f"synthetic example missing {filename}")
            continue
        rows = [_read_json(path)] if filename.endswith(".json") else _read_jsonl(path)
        for index, row in enumerate(rows, start=1):
            errors.extend(f"{filename}:{index}: {error}" for error in validate_json_schema(row, schema))

    errors.extend(_cross_file_errors(evidence))
    try:
        _assert_no_absolute_paths(bundle)
    except ValueError as exc:
        errors.append(str(exc))
    return errors


def validate_json_schema(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Small dependency-free JSON Schema subset used by public contract tests."""
    if "const" in schema and value != schema["const"]:
        return [f"{path} must equal {schema['const']!r}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path} is not an allowed value"]
    allowed = schema.get("type")
    if allowed is not None:
        types = allowed if isinstance(allowed, list) else [allowed]
        if not any(_matches_type(value, item) for item in types):
            return [f"{path} must be type {allowed}"]
    errors: list[str] = []
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key} is required")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path}.{key} is not allowed")
        for key, child in properties.items():
            if key in value:
                errors.extend(validate_json_schema(value[key], child, f"{path}.{key}"))
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            errors.extend(validate_json_schema(item, schema["items"], f"{path}[{index}]"))
    return errors


def _write_bundle(bundle: Path) -> None:
    bundle.mkdir(parents=True)
    schemas = _schemas()
    for filename, content in schemas.items():
        _write_json(bundle / filename, content)
    _write_json(bundle / "pack-layout.json", _pack_layout())
    (bundle / "invariants.md").write_text(_invariants_markdown(), encoding="utf-8", newline="\n")
    (bundle / "field-provenance.md").write_text(_provenance_markdown(), encoding="utf-8", newline="\n")
    (bundle / "README.md").write_text(_readme_markdown(), encoding="utf-8", newline="\n")
    _write_synthetic_pack(bundle / "synthetic-example" / "pack")
    contract_files = sorted(path.relative_to(bundle).as_posix() for path in bundle.rglob("*") if path.is_file())
    file_hashes = {relative: _sha256(bundle / relative) for relative in contract_files}
    _write_json(
        bundle / "contract-manifest.json",
        {
            "contract_id": CONTRACT_ID,
            "contract_version": CONTRACT_VERSION,
            "contract_bundle_version": CONTRACT_VERSION,
            "parser_version": _package_version(),
            "pack_schema": SCHEMA_VERSION,
            "generated_from_source": True,
            "source_definitions": CONTRACT_SOURCE_DEFINITIONS,
            "required_files": _pack_layout()["required_files"],
            "optional_files": _pack_layout()["optional_files"],
            "schema_files": sorted(schemas),
            "invariants_file": "invariants.md",
            "synthetic_example_path": "synthetic-example/pack",
            "file_hashes": file_hashes,
            "hash_algorithm": "sha256",
            "hash_scope": "all contract files except this self-referential manifest",
        },
    )


def _schemas() -> dict[str, dict[str, Any]]:
    nullable_string = {"type": ["string", "null"]}
    any_json = {}
    return {
        "pack-manifest.schema.json": _object_schema(
            {
                "schema_version": {"const": SCHEMA_VERSION},
                "generated_at": {"type": "string"},
                "source_input": {"type": "string"},
                "markdown_profile": {"enum": ["readable", "readable_compact"]},
                "copy_assets": {"type": "boolean"},
                "copy_unlinked_assets": {"type": "boolean"},
                "emit_json": {"const": True},
                "emit_markdown": {"type": "boolean"},
                "emit_bases": {"type": "boolean"},
                "emit_dataview": {"type": "boolean"},
                "update_existing_pack": {"type": "boolean"},
                "navigation": _object_schema(
                    {
                        "conversation_note_locator": _object_schema(
                            {
                                "state": {"enum": ["emitted", "not_emitted"]},
                                "relative_path": nullable_string,
                                "record_count": {"type": "integer"},
                            }
                        ),
                    }
                ),
                "filters": _object_schema(
                    {
                        "conversation_ids": {"type": "array", "items": {"type": "string"}},
                        "months": {"type": "array", "items": {"type": "string"}},
                        "max_conversations": {"type": ["integer", "null"]},
                    }
                ),
                "counts": _object_schema(
                    {
                        "parsed_conversations": {"type": "integer"},
                        "emitted_conversations": {"type": "integer"},
                    }
                ),
            }
        ),
        "conversation-record.schema.json": _object_schema(
            {
                "type": {"const": "openai_conversation"}, "schema_version": {"const": SCHEMA_VERSION},
                "status": {"const": "parsed"}, "title": nullable_string, "reviewed": {"const": False},
                "created_at": nullable_string, "updated_at": nullable_string, "year": {"type": ["integer", "string"]},
                "month": {"type": "string"}, "month_key": {"type": "string"}, "conversation_id": {"type": "string"},
                "source_archive": nullable_string, "source_archive_path": {"type": "string"}, "source_shard": {"type": "string"},
                "chat_url": nullable_string, "message_count": {"type": "integer"}, "default_model": nullable_string,
                "models_seen": {"type": "array", "items": {"type": "string"}}, "memory_scope": any_json,
                "voice": nullable_string, "is_archived": {"type": ["boolean", "null"]},
                "is_starred": {"type": ["boolean", "null"]}, "is_do_not_remember": {"type": ["boolean", "null"]},
                "gpts": {"type": "array", "items": {"type": "string"}}, "projects": {"type": "array", "items": {"type": "string"}},
                "space_projects": {"type": "array", "items": {"type": "string"}}, "knowledge_stores": {"type": "array", "items": {"type": "string"}},
                "observed_contexts": {"type": "array", "items": {"type": "string"}},
                "context_evidence": {"enum": ["mixed", "explicit", "observed", "unknown"]},
                "file_reference_count": {"type": "integer"}, "unique_file_count": {"type": "integer"},
                "resolved_file_count": {"type": "integer"}, "unresolved_file_count": {"type": "integer"},
                "source_evidence_count": {"type": "integer"}, "tool_evidence_count": {"type": "integer"},
                "has_unresolved_files": {"type": "boolean"}, "has_warnings": {"type": "boolean"},
            }
        ),
        "message-record.schema.json": _object_schema(
            {
                "node_id": {"type": "string"}, "message_id": nullable_string, "parent_id": nullable_string,
                "child_ids": {"type": "array", "items": {"type": "string"}}, "author_role": nullable_string,
                "create_time": nullable_string, "update_time": nullable_string, "model_slug": nullable_string,
                "default_model_slug": nullable_string, "channel": nullable_string, "end_turn": {"type": ["boolean", "null"]},
                "status": nullable_string, "content_type": nullable_string, "text": {"type": "string"},
                "non_text_parts": {"type": "array", "items": any_json}, "content_references": {"type": "array", "items": any_json},
                "context_citations": {"type": "array", "items": {"type": "object"}}, "citations": {"type": "array", "items": any_json},
                "safe_urls": {"type": "array", "items": {"type": "string"}}, "code_blocks": {"type": "array", "items": {"type": "object"}},
                "raw_metadata": {"type": "object"}, "conversation_id": {"type": "string"},
            }
        ),
        "asset-link-record.schema.json": _object_schema(
            {
                "asset_ref_id": {"type": "string"}, "conversation_id": {"type": "string"}, "message_id": nullable_string,
                "source_shard": {"type": "string"}, "proof_path": {"type": "string"}, "raw_file_id": nullable_string,
                "raw_dat_filename": nullable_string, "physical_archive_path": nullable_string, "reconstructed_filename": nullable_string,
                "normalized_extension": nullable_string, "mime_type": nullable_string, "size": {"type": ["integer", "null"]},
                "width": {"type": ["integer", "null"]}, "height": {"type": ["integer", "null"]},
                "source_origin_fields": {"type": "object"}, "library_file_id": nullable_string, "origination_message_id": nullable_string,
                "origination_thread_id": nullable_string, "asset_status": {"enum": ["found", "missing", "collision"]},
                "provenance_status": {"type": "string"}, "origin_classification": {"type": "string"}, "origin_confidence": {"type": "string"},
                "copied_relative_path": nullable_string, "copied_filename": nullable_string, "file_role": {"type": "string"},
                "copied_pack_path": nullable_string, "knowledge_store_id": nullable_string, "library_gizmo_id": nullable_string,
                "image_gen_generation_id": nullable_string, "raw_reference": any_json,
            }
        ),
        "conversation-note-locator.schema.json": _object_schema(
            {
                "conversation_id": {"type": "string"},
                "relative_note_path": {"type": "string"},
            }
        ),
    }


def _object_schema(properties: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": list(properties),
        "properties": properties,
        "additionalProperties": False,
    }


def _pack_layout() -> dict[str, Any]:
    return {
        "pack_schema": SCHEMA_VERSION,
        "recommended_profile": "readable_compact",
        "required_files": [
            "90_Evidence/pack_manifest.json", "90_Evidence/parse_audit.json", "90_Evidence/conversations.jsonl",
            "90_Evidence/messages.jsonl", "90_Evidence/message_sources.jsonl", "90_Evidence/tool_events.jsonl",
            "90_Evidence/textdocs.jsonl", "90_Evidence/citation_links.jsonl", "90_Evidence/asset_links.jsonl",
            "90_Evidence/context_links.jsonl", "90_Evidence/file_manifest.jsonl", "90_Evidence/unlinked_assets.jsonl",
            "90_Evidence/inventory.jsonl", "90_Evidence/inventory_errors.jsonl", "90_Evidence/inventory_summary.json",
            "90_Evidence/references.jsonl", "90_Evidence/reference_summary.json", "90_Evidence/physical_resolutions.jsonl",
            "90_Evidence/physical_resolution_summary.json", "90_Evidence/payload_dispositions.jsonl",
            "90_Evidence/payload_disposition_summary.json", "90_Evidence/asset_resolution_comparisons.jsonl",
            "90_Evidence/asset_resolution_migration_summary.json", "90_Evidence/unverified_payload_candidates.jsonl",
            "90_Evidence/unverified_payload_candidate_summary.json", "90_Evidence/runtime_artifacts.jsonl",
            "90_Evidence/runtime_artifacts_summary.json", "90_Evidence/historical_export_snapshots.jsonl",
            "90_Evidence/historical_conversation_comparisons.jsonl", "90_Evidence/historical_messages.jsonl",
            "90_Evidence/historical_signals.jsonl", "90_Evidence/historical_export_summary.json", "90_Evidence/technical_events.jsonl",
            "90_Evidence/technical_event_comparisons.jsonl", "90_Evidence/technical_event_unknowns.jsonl",
            "90_Evidence/technical_event_summary.json", "90_Evidence/logical_entities.jsonl",
            "90_Evidence/logical_entity_observations.jsonl", "90_Evidence/logical_entity_unmaterialized_events.jsonl",
            "90_Evidence/logical_entity_taxonomy_migration.jsonl", "90_Evidence/logical_entity_summary.json",
            "90_Evidence/payload_materialization_summary.json", "90_Evidence/candidate_edges.jsonl",
            "90_Evidence/candidate_edge_unmatched.jsonl", "90_Evidence/candidate_edge_evidence.jsonl",
            "90_Evidence/candidate_edge_ambiguity_groups.jsonl", "90_Evidence/candidate_edge_evidence_sets.jsonl",
            "90_Evidence/candidate_edge_rejections.jsonl", "90_Evidence/candidate_edge_summary.json",
            "90_Evidence/gizmo_context_profiles.jsonl", "90_Evidence/project_context_profiles.jsonl",
            "90_Evidence/context_profile_observations.jsonl", "90_Evidence/context_instruction_candidates.jsonl",
            "90_Evidence/context_file_memberships.jsonl", "90_Evidence/context_profile_contradictions.jsonl",
            "90_Evidence/context_profile_summary.json", "90_Evidence/context_resource_nodes.jsonl",
            "90_Evidence/context_resource_edges.jsonl", "90_Evidence/context_resource_owner_claims.jsonl",
            "90_Evidence/context_resource_usage_metrics.jsonl", "90_Evidence/context_resource_cooccurrences.jsonl",
            "90_Evidence/context_resource_usage_summary.json",
        ],
        "optional_files": [
            *OPTIONAL_ROOT_NAVIGATION_FILES,
            "10_Conversations/**", "20_Files/**", "30_Contexts/**", "40_Views/conversation_note_locators.jsonl", "40_Views/**", "_logs/parse.log", "logs/parse.log",
        ],
        "navigation_artifacts": {
            "conversation_note_locator": {
                "relative_path": "40_Views/conversation_note_locators.jsonl",
                "state_when_markdown_enabled": "emitted",
                "state_when_markdown_disabled": "not_emitted",
                "evidence_fingerprint": "excluded",
            },
        },
        "optional_root_navigation_files": list(OPTIONAL_ROOT_NAVIGATION_FILES),
        "allowed_root_directories": list(PACK_ROOT_DIRECTORIES),
        "historical_variant": {"profile": "forensic", "contract_status": "outside this readable-pack contract"},
        "source_definitions": CONTRACT_SOURCE_DEFINITIONS,
    }


def _write_synthetic_pack(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="parser-contract-fixture-") as temporary:
        work = Path(temporary)
        _write_synthetic_export(work / SYNTHETIC_SOURCE_NAME)
        with _working_directory(work):
            run_parse(
                Path(SYNTHETIC_SOURCE_NAME),
                Path("pack"),
                emit_json=True,
                emit_markdown=True,
                markdown_profile="readable_compact",
                manifest_generated_at=FIXTURE_GENERATED_AT,
            )
        for log_dir in (work / "pack" / "_logs", work / "pack" / "logs"):
            if log_dir.exists():
                shutil.rmtree(log_dir)
        shutil.copytree(work / "pack", destination)


def _write_synthetic_export(path: Path) -> None:
    conversations = [
        _synthetic_conversation("contract-conversation-alpha", "Synthetic alpha", "alpha", include_attachment=True),
        _synthetic_conversation("contract-conversation-beta", "Synthetic beta", "beta"),
    ]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("conversations-000.json", json.dumps(conversations, ensure_ascii=False, sort_keys=True))
        archive.writestr("file-synthetic-alpha.dat", b"synthetic contract payload\n")


def _synthetic_conversation(
    conversation_id: str,
    title: str,
    token: str,
    *,
    include_attachment: bool = False,
) -> dict[str, Any]:
    user_node = f"{token}-node-user"
    assistant_node = f"{token}-node-assistant"
    user_metadata: dict[str, Any] = {}
    if include_attachment:
        user_metadata["attachments"] = [{"id": "file-synthetic-alpha", "name": "Synthetic attachment.txt", "mime_type": "text/plain"}]
    return {
        "id": conversation_id,
        "title": title,
        "create_time": 946684800.0,
        "update_time": 946684801.0,
        "current_node": assistant_node,
        "mapping": {
            user_node: {
                "id": user_node, "parent": None, "children": [assistant_node],
                "message": {"id": f"{token}-message-user", "author": {"role": "user"}, "create_time": 946684800.0,
                            "content": {"content_type": "text", "parts": [f"Synthetic user request for {token}."]}, "metadata": user_metadata, "status": "finished_successfully", "end_turn": False},
            },
            assistant_node: {
                "id": assistant_node, "parent": user_node, "children": [],
                "message": {"id": f"{token}-message-assistant", "author": {"role": "assistant"}, "create_time": 946684801.0,
                            "recipient": "python", "content": {"content_type": "text", "parts": [f"Synthetic assistant reply for {token}."]}, "metadata": {"model_slug": "synthetic-model"}, "status": "finished_successfully", "end_turn": True},
            },
        },
    }


def validate_pack_layout(pack: Path) -> list[str]:
    """Validate the closed root and evidence layout of this contract's pack.

    This is deliberately a contract-bundle check, not a relaxation or extension
    of the parser's general fail-soft validator.  It only reads metadata for
    ``20_Files`` and never opens or hashes copied payload bytes.
    """
    pack = Path(pack)
    if not pack.is_dir():
        return ["pack root is missing or not a directory"]
    layout = _pack_layout()
    allowed_files = set(layout["optional_root_navigation_files"])
    allowed_directories = set(layout["allowed_root_directories"])
    errors: list[str] = ds_store_diagnostics(pack)
    for entry in sorted(pack.iterdir(), key=lambda path: path.name):
        if entry.is_symlink():
            errors.append(f"root entry must not be a symlink: {entry.name}")
        elif entry.name in allowed_files:
            if not entry.is_file():
                errors.append(f"optional root navigation artifact must be a regular file: {entry.name}")
        elif entry.name in allowed_directories:
            if not entry.is_dir():
                errors.append(f"root layout entry must be a directory: {entry.name}")
        else:
            errors.append(f"unknown root entry: {entry.name}")

    for entry in sorted(pack.rglob("*"), key=lambda path: path.as_posix()):
        if entry.parent not in {pack, pack / "90_Evidence"} and entry.is_symlink():
            errors.append(f"pack entry must not be a symlink: {entry.relative_to(pack).as_posix()}")

    evidence = pack / "90_Evidence"
    if evidence.exists() and not evidence.is_dir():
        errors.append("90_Evidence must be a directory")
        return errors
    if not evidence.exists():
        return [*errors, "90_Evidence directory missing"]
    expected_evidence = {
        Path(relative).name
        for relative in layout["required_files"]
        if Path(relative).parent == Path("90_Evidence")
    }
    for name in sorted(expected_evidence):
        entry = evidence / name
        if entry.is_symlink() or not entry.is_file():
            errors.append(f"required 90_Evidence entry missing or not a regular file: {name}")
    for entry in sorted(evidence.iterdir(), key=lambda path: path.name):
        if entry.is_symlink():
            errors.append(f"90_Evidence entry must not be a symlink: {entry.name}")
        elif not entry.is_file():
            errors.append(f"unexpected non-file 90_Evidence entry: {entry.name}")
        elif entry.name not in expected_evidence:
            errors.append(f"unexpected 90_Evidence entry: {entry.name}")
    return errors


def evidence_fingerprint(pack: Path) -> str:
    """Hash only the required evidence files, never navigation or payloads."""
    pack = Path(pack)
    digest = hashlib.sha256()
    digest.update(b"openai-obsidian-pack-evidence-fingerprint-v1\0")
    for relative in sorted(_pack_layout()["required_files"]):
        path = pack / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def derived_record_hashes(pack: Path) -> dict[str, str]:
    """Return hashes for the consumer record envelopes, excluding projections."""
    pack = Path(pack)
    return {
        relative: _sha256(pack / relative)
        for relative in (
            "90_Evidence/pack_manifest.json",
            "90_Evidence/conversations.jsonl",
            "90_Evidence/messages.jsonl",
            "90_Evidence/asset_links.jsonl",
        )
    }


def _cross_file_errors(evidence: Path) -> list[str]:
    errors: list[str] = []
    conversations = _read_jsonl(evidence / "conversations.jsonl")
    messages = _read_jsonl(evidence / "messages.jsonl")
    manifest = _read_json(evidence / "pack_manifest.json")
    conversation_ids = [row.get("conversation_id") for row in conversations]
    message_keys = [
        (row.get("conversation_id"), row.get("message_id"))
        for row in messages
        if row.get("message_id") is not None
    ]
    node_keys = [(row.get("conversation_id"), row.get("node_id")) for row in messages]
    if len(conversation_ids) != len(set(conversation_ids)):
        errors.append("conversation IDs are not unique")
    if len(message_keys) != len(set(message_keys)):
        errors.append("message IDs are not unique within conversation scope")
    if len(node_keys) != len(set(node_keys)):
        errors.append("node IDs are not unique within conversation scope")
    known = set(conversation_ids)
    if any(row.get("conversation_id") not in known for row in messages):
        errors.append("message references an unknown conversation")
    known_message_keys = set(message_keys)
    for name in (
        "asset_links.jsonl",
        "message_sources.jsonl",
        "tool_events.jsonl",
        "textdocs.jsonl",
        "context_links.jsonl",
        "citation_links.jsonl",
    ):
        for index, row in enumerate(_read_jsonl(evidence / name), start=1):
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
    if manifest.get("counts", {}).get("emitted_conversations") != len(conversations):
        errors.append("manifest emitted_conversations does not match conversations.jsonl")
    for conversation in conversations:
        actual = sum(row.get("conversation_id") == conversation.get("conversation_id") for row in messages)
        if conversation.get("message_count") != actual:
            errors.append(f"message_count mismatch for {conversation.get('conversation_id')}")
    if conversation_ids != sorted(conversation_ids):
        errors.append("conversation order is not deterministic for equal timestamps")
    for conversation_id in conversation_ids:
        node_ids = [str(row.get("node_id")) for row in messages if row.get("conversation_id") == conversation_id]
        if node_ids != sorted(node_ids):
            errors.append(f"message order is not deterministic for {conversation_id}")
    return errors


def _invariants_markdown() -> str:
    return """# Readable pack invariants

This contract covers `parse --emit-json` with `readable` or `readable_compact`. The recommended consumer surface is `90_Evidence/`; Markdown and copied payloads are projections controlled by manifest flags.

## Identity and relations

- `conversations.jsonl.conversation_id` is the canonical conversation identity and must be unique.
- `messages.jsonl` contains every recovered mapping message, including non-linear branches. A non-null `message_id` is identified by `(conversation_id, message_id)` and must be unique within that conversation. `message_id` values may repeat across conversations.
- Every message node is identified by `(conversation_id, node_id)`, which must be unique within that conversation. `node_id` values may repeat across conversations and provide the fallback identity when `message_id` is null.
- Every message, asset, source, tool event, textdoc and context row must reference an emitted `conversation_id`. Rows carrying `message_id` must reference a known `(conversation_id, message_id)` pair.
- `ConversationRecord.message_count` is `len(all_messages)` and must equal the number of `messages.jsonl` rows for that conversation. `file_reference_count` equals the count of `asset_links.jsonl` rows for the conversation.

## Ordering and branches

- Conversations are sorted by `(create_time or "", conversation_id)` before filtering.
- Mapping nodes are parsed in sorted node-ID order. The readable transcript can follow `current_node`; JSONL message evidence remains all parsed mapping messages.
- If the main branch cannot be reconstructed, the parser keeps sorted messages and records a parse warning. It does not invent a branch.

## Timestamps, statuses and unknown values

- Numeric source timestamps are normalized by `utils.as_iso`; absent or unparseable values become null. Equality between conversation and message timestamps is allowed.
- The parser preserves a string role or content type rather than whitelisting it. An unfamiliar role/content type is valid evidence, not a rejected record.
- `raw_metadata`, `raw_reference`, non-text content and citation payloads intentionally admit arbitrary JSON: they preserve source evidence. Record-envelope fields are closed in the exported JSON Schemas so a new emitted field is a contract change.

## Files, hashes and validation

- Base readable evidence files are emitted by `emit_readable_outputs`; empty JSONL files are valid and still emitted.
- `00_Home.md` is an optional root-level Obsidian navigation artifact, emitted only with Markdown. It is not an evidence record and its bytes are excluded from the evidence fingerprint and consumer record hashes. Every other root entry remains closed by `pack-layout.json`.
- `40_Views/conversation_note_locators.jsonl` is a derived navigation relation emitted only with Markdown. Each closed record maps one canonical `conversation_id` to the exact POSIX-relative Markdown path emitted by the writer, and the validator checks that its target note's generated frontmatter has the same `conversation_id`. Locator paths are filesystem/navigation paths, not pre-rendered Wikilink syntax; consumers must escape or encode them for their own UI/Markdown syntax. The locator is excluded from the evidence fingerprint and consumer record hashes; `pack_manifest.json.navigation.conversation_note_locator` declares whether it is emitted.
- Files under `20_Files/`, views and Markdown are conditional on manifest flags. `forensic` is a historical layout variant and is excluded from this contract.
- Payload hashes in a real pack are SHA-256 of copied bytes where the relevant writer emits `content_sha256`; ordinary inventory hashes are opt-in. Contract `file_hashes` are SHA-256 of every bundle file other than `contract-manifest.json` (a self-hash would be impossible).
- `validate_pack` rejects missing base evidence, unknown conversation/message links, invalid asset/textdoc statuses, count mismatches, and missing copied payloads. It is intentionally fail-soft for malformed raw source records: such records can remain represented as warnings or empty evidence.

## Compatibility

A consumer must reject an unrecognized `pack_manifest.schema_version`, validate the envelope schemas in this bundle, then apply the inter-file rules above. It should treat unknown fields as incompatible with this contract, even though the current pack validator only explicitly rejects some obsolete fields.
"""


def _provenance_markdown() -> str:
    return """# Field provenance

| Record family | Authoritative source | Serializer | Validator / rule |
| --- | --- | --- | --- |
| Pack manifest | `runner.py:SCHEMA_VERSION`, `build_pack_manifest` | `emit_readable_outputs` -> `write_json` | `validate_pack` uses it as pack metadata; schema is closed here |
| Conversation evidence | `models.py:ConversationRecord`; `runner.py:readable_conversation_row` | `emit_readable_outputs` -> `conversations.jsonl` | `validate_pack` checks IDs, datetime type, context lists, message/file counts |
| Message evidence | `models.py:MessageRecord`; `conversations.py:parse_message` | `MessageRecord.to_dict` plus `conversation_id` in `emit_readable_outputs` | `validate_pack` checks conversation and non-null message references |
| Asset evidence | `models.py:AssetRecord`; `assets.py:build_asset_records` | `AssetRecord.to_dict` | `validate_pack` checks IDs, proof paths, status and copied files |
| Cross-file structure | `runner.py:emit_readable_outputs` | ordered JSONL writers | `validation.py:validate_pack` |
| Conversation note locator | `runner.py` exact note-emission path | `40_Views/conversation_note_locators.jsonl` | `validation.py:validate_conversation_note_locator` |

The internal `ConversationRecord.to_dict()` is not the exported conversation row: the public row is the separate `readable_conversation_row()` projection. `MessageRecord` and `AssetRecord` are exported through their dataclass serializers, with `conversation_id` added to message rows by the readable writer.
"""


def _readme_markdown() -> str:
    return f"""# Parser pack contract

This is a non-private, machine-readable contract for readable Parser packs. It contains no user export, personal record, source path, pseudonym map, or copied asset.

## Versions

- `parser_version` is the Python project version declared in `pyproject.toml`.
- `pack_schema` is `{SCHEMA_VERSION}`, the literal emitted by `runner.SCHEMA_VERSION`.
- `contract_version` / `contract_bundle_version` are `{CONTRACT_VERSION}`, the version of this independent description bundle.
- “V5” is a README/output-run reference label (for example an `output_v5` convention), not a pack-schema version. The source definitions inspected here do not serialize it into `pack_manifest.json`.

## Generate and verify

```bash
PYTHONPATH=src python3 -m openai_export_obsidian describe-pack-contract \\
  --output contracts/parser-pack/openai-obsidian-pack-v1.4
PYTHONPATH=src python3 -m unittest tests.test_pack_contract -v
```

The command takes no export or pack argument. It creates a fresh synthetic ZIP in a temporary directory, invokes the real readable serializer, removes its non-contract log, and refuses to overwrite a different bundle. Verify the `file_hashes` in `contract-manifest.json` with SHA-256; the manifest itself is deliberately excluded from its own hash list.

## External consumer

Read `contract-manifest.json`, reject a different `pack_schema`, validate the closed root layout, then validate `90_Evidence/pack_manifest.json` and stream the closed conversation/message/asset envelopes. If `pack_manifest.json.navigation.conversation_note_locator.state` is `emitted`, use the exact producer-owned relation in `40_Views/conversation_note_locators.jsonl`; do not derive note names or scan Markdown frontmatter. Locator paths are filesystem paths, not pre-rendered Wikilink syntax: escape or encode them only at the consuming UI layer. Conversation Pattern Forge's later repin task is responsible for its own safe Markdown rendering. `00_Home.md`, when present, is only navigation and must not feed evidence fingerprints or record hashes. Apply `invariants.md` for relations and counts. Other evidence files are named in `pack-layout.json`; their authoritative producers are recorded there and in `field-provenance.md`.

This bundle is documentation for a potential consumer only. It does not authorize access to a personal Parser pack.
"""


def _package_version() -> str:
    project = Path(__file__).resolve().parents[2] / "pyproject.toml"
    return str(tomllib.loads(project.read_text(encoding="utf-8"))["project"]["version"])


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [value for line in path.read_text(encoding="utf-8").splitlines() if line.strip() if isinstance(value := json.loads(line), dict)]


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }.get(expected, True)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_hashes(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): _sha256(path) for path in sorted(root.rglob("*")) if path.is_file()}


def _assert_no_absolute_paths(root: Path) -> None:
    pattern = re.compile(r"(?:^|[\"'\s])/(?:Users|private|var|tmp)(?:/|$)")
    for path in root.rglob("*"):
        if path.is_file() and pattern.search(path.read_text(encoding="utf-8", errors="replace")):
            raise ValueError(f"absolute path found in contract bundle: {path.relative_to(root)}")


@contextmanager
def _working_directory(path: Path) -> Iterator[None]:
    original = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(original)
