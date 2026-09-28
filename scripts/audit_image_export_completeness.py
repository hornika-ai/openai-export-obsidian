#!/usr/bin/env python3
"""Audit image-generation evidence across a nested OpenAI export ZIP.

Read-only on the source archive. Writes CSV/JSON/Markdown evidence reports.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
from collections import Counter, defaultdict
from pathlib import Path
import zipfile


IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif", "avif", "heic", "bmp", "tif", "tiff", "svg"}


def scalar_id(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        candidate = value.get("id")
        return candidate if isinstance(candidate, str) else None
    return None


def walk(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            yield child_path, key, child
            yield from walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            yield child_path, None, child
            yield from walk(child, child_path)


def conversation_evidence(conversation, shard, archive_name):
    cid = conversation.get("id") or conversation.get("conversation_id")
    mapping = conversation.get("mapping") if isinstance(conversation.get("mapping"), dict) else {}
    picture_messages = []
    image_pointer_rows = []
    generation_ids = set()
    tool_message_ids = []
    orphan_metadata_parents = []

    for node_id, node in mapping.items():
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        message_id = message.get("id") or node_id
        author = message.get("author") if isinstance(message.get("author"), dict) else {}
        role = author.get("role")
        metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        hints = metadata.get("system_hints")
        if isinstance(hints, list) and "picture_v2" in hints:
            picture_messages.append(message_id)
        if role == "tool":
            tool_message_ids.append(message_id)
        metadata_parent = metadata.get("parent_id")
        if isinstance(metadata_parent, str) and metadata_parent not in mapping and metadata_parent != "client-created-root":
            orphan_metadata_parents.append(f"{message_id}:{metadata_parent}")
        for proof_path, key, value in walk(message):
            if key == "content_type" and value == "image_asset_pointer":
                container = None
                # Find the nearest image part by a second small traversal.
                for part in (message.get("content") or {}).get("parts", []) if isinstance(message.get("content"), dict) else []:
                    if isinstance(part, dict) and part.get("content_type") == "image_asset_pointer":
                        container = part
                        break
                image_pointer_rows.append({
                    "message_id": message_id,
                    "author_role": role,
                    "proof_path": f"mapping.{node_id}.message.{proof_path}",
                    "asset_pointer": container.get("asset_pointer") if container else None,
                })
            elif key in {"gen_id", "image_gen_generation_id"} and isinstance(value, str):
                generation_ids.add(value)

    return {
        "conversation_id": cid,
        "title": conversation.get("title") or "",
        "create_time": conversation.get("create_time"),
        "update_time": conversation.get("update_time"),
        "source_archive": archive_name,
        "source_shard": shard,
        "picture_v2_message_count": len(picture_messages),
        "picture_v2_message_ids": picture_messages,
        "conversation_image_pointer_count": len(image_pointer_rows),
        "conversation_image_pointers": image_pointer_rows,
        "assistant_image_pointer_count": sum(1 for item in image_pointer_rows if item["author_role"] == "assistant"),
        "user_image_pointer_count": sum(1 for item in image_pointer_rows if item["author_role"] == "user"),
        "conversation_generation_ids": sorted(generation_ids),
        "tool_message_count": len(tool_message_ids),
        "tool_message_ids": tool_message_ids,
        "orphan_metadata_parent_count": len(orphan_metadata_parents),
        "orphan_metadata_parents": orphan_metadata_parents,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    conversations = {}
    conversation_record_count = 0
    library_rows = []
    physical_members = defaultdict(list)
    source_inventory = []

    with zipfile.ZipFile(args.input) as outer:
        nested_names = sorted(name for name in outer.namelist() if name.lower().endswith(".zip"))
        for nested_name in nested_names:
            with zipfile.ZipFile(io.BytesIO(outer.read(nested_name))) as inner:
                members = inner.namelist()
                source_inventory.append({"archive": nested_name, "member_count": len(members)})
                for member in members:
                    basename = Path(member).name
                    physical_members[basename].append(f"{nested_name}::{member}")
                if "library_files.json" in members:
                    loaded = json.loads(inner.read("library_files.json"))
                    rows = loaded if isinstance(loaded, list) else []
                    for row in rows:
                        if isinstance(row, dict):
                            copy = dict(row)
                            copy["_source_archive"] = nested_name
                            copy["_source_json"] = "library_files.json"
                            library_rows.append(copy)
                for member in sorted(m for m in members if Path(m).name.startswith("conversations-") and m.endswith(".json")):
                    loaded = json.loads(inner.read(member))
                    if not isinstance(loaded, list):
                        continue
                    for conversation in loaded:
                        if not isinstance(conversation, dict):
                            continue
                        conversation_record_count += 1
                        evidence = conversation_evidence(conversation, Path(member).name, nested_name)
                        if evidence["conversation_id"]:
                            conversations[evidence["conversation_id"]] = evidence

    library_image_rows = []
    by_thread = defaultdict(list)
    for row in library_rows:
        extension = str(row.get("file_extension") or "").lower().lstrip(".")
        category = row.get("library_file_category")
        mime = str(row.get("mime_type") or "")
        if category != "image" and extension not in IMAGE_EXTENSIONS and not mime.startswith("image/"):
            continue
        file_id = row.get("file_id")
        dat_name = f"{file_id}.dat" if isinstance(file_id, str) else None
        candidates = physical_members.get(dat_name, []) if dat_name else []
        item = {
            "file_name": row.get("file_name"),
            "file_id": file_id,
            "library_file_id": scalar_id(row.get("id")),
            "knowledge_store_id": scalar_id(row.get("knowledge_store_id")),
            "image_gen_generation_id": row.get("image_gen_generation_id"),
            "origination_thread_id": row.get("origination_thread_id"),
            "origination_message_id": row.get("origination_message_id"),
            "created_at": row.get("created_at"),
            "file_size_bytes": row.get("file_size_bytes"),
            "mime_type": row.get("mime_type"),
            "library_artifact_type": row.get("library_artifact_type"),
            "version_actor": (row.get("version_provenance") or {}).get("actor") if isinstance(row.get("version_provenance"), dict) else None,
            "physical_payload_found": bool(candidates),
            "physical_payload_paths": candidates,
            "source_archive": row.get("_source_archive"),
            "source_json": row.get("_source_json"),
        }
        library_image_rows.append(item)
        if item["origination_thread_id"]:
            by_thread[item["origination_thread_id"]].append(item)

    conversation_rows = []
    for cid, row in sorted(conversations.items()):
        images = by_thread.get(cid, [])
        generated = [x for x in images if x["image_gen_generation_id"]]
        payloads = [x for x in images if x["physical_payload_found"]]
        if row["picture_v2_message_count"] or row["conversation_image_pointer_count"] or images:
            if row["assistant_image_pointer_count"]:
                status = "assistant_output_pointer_present"
            elif generated and payloads:
                status = "knowledge_generated_payload_present"
            elif generated:
                status = "knowledge_generated_payload_missing"
            elif images and payloads:
                status = "knowledge_image_payload_present_origin_unknown"
            elif row["picture_v2_message_count"]:
                status = "picture_v2_without_linked_output"
            else:
                status = "image_evidence_other"
            conversation_rows.append({
                **row,
                "linked_library_image_count": len(images),
                "linked_generated_library_image_count": len(generated),
                "linked_physical_library_image_count": len(payloads),
                "linked_library_file_ids": [x["file_id"] for x in images],
                "linked_image_generation_ids": [x["image_gen_generation_id"] for x in generated],
                "audit_status": status,
            })

    def write_csv(path, rows, fields):
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for key, value in row.items()})

    conversation_fields = [
        "conversation_id", "title", "create_time", "source_shard", "picture_v2_message_count",
        "picture_v2_message_ids", "conversation_image_pointer_count", "conversation_image_pointers",
        "assistant_image_pointer_count", "user_image_pointer_count",
        "conversation_generation_ids", "tool_message_count", "orphan_metadata_parent_count",
        "orphan_metadata_parents", "linked_library_image_count", "linked_generated_library_image_count",
        "linked_physical_library_image_count", "linked_library_file_ids", "linked_image_generation_ids", "audit_status",
    ]
    image_fields = [
        "file_name", "file_id", "library_file_id", "knowledge_store_id", "image_gen_generation_id",
        "origination_thread_id", "origination_message_id", "created_at", "file_size_bytes", "mime_type",
        "library_artifact_type", "version_actor", "physical_payload_found", "physical_payload_paths",
        "source_archive", "source_json",
    ]
    write_csv(args.output / "conversation_image_audit.csv", conversation_rows, conversation_fields)
    write_csv(args.output / "library_image_inventory.csv", library_image_rows, image_fields)

    status_counts = Counter(row["audit_status"] for row in conversation_rows)
    picture_conversations = [row for row in conversation_rows if row["picture_v2_message_count"]]
    picture_with_generated_library = [row for row in picture_conversations if row["linked_generated_library_image_count"]]
    picture_without_any_output = [
        row for row in picture_conversations
        if not row["assistant_image_pointer_count"] and not row["linked_generated_library_image_count"]
    ]
    generated_library = [row for row in library_image_rows if row["image_gen_generation_id"]]
    generated_library_found = [row for row in generated_library if row["physical_payload_found"]]
    library_images_found = [row for row in library_image_rows if row["physical_payload_found"]]
    assistant_pointer_conversations = [row for row in conversation_rows if row["assistant_image_pointer_count"]]
    user_pointer_conversations = [row for row in conversation_rows if row["user_image_pointer_count"]]
    summary = {
        "source_zip": str(args.input),
        "total_conversation_records": conversation_record_count,
        "unique_conversation_ids": len(conversations),
        "total_library_files": len(library_rows),
        "total_library_images": len(library_image_rows),
        "library_images_with_payload": len(library_images_found),
        "library_images_with_generation_id": len(generated_library),
        "generated_library_images_with_payload": len(generated_library_found),
        "conversations_with_assistant_image_pointer": len(assistant_pointer_conversations),
        "conversations_with_user_image_pointer": len(user_pointer_conversations),
        "picture_v2_conversations": len(picture_conversations),
        "picture_v2_with_generated_library_image": len(picture_with_generated_library),
        "picture_v2_without_conversation_pointer_or_generated_library_image": len(picture_without_any_output),
        "conversation_status_counts": dict(sorted(status_counts.items())),
        "source_inventory": source_inventory,
    }
    (args.output / "image_export_audit_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    report = [
        "# Audit global des images dans l'export OpenAI",
        "",
        f"Source : `{args.input}`",
        "",
        "## Totaux",
        "",
        f"- Enregistrements de conversations : **{summary['total_conversation_records']}**",
        f"- Identifiants de conversation uniques : **{summary['unique_conversation_ids']}**",
        f"- Conversations avec `picture_v2` : **{summary['picture_v2_conversations']}**",
        f"- Fichiers dans `library_files.json` : **{summary['total_library_files']}**",
        f"- Images de bibliothèque : **{summary['total_library_images']}**",
        f"- Images de bibliothèque avec payload `.dat` trouvé : **{summary['library_images_with_payload']}**",
        f"- Images avec `image_gen_generation_id` : **{summary['library_images_with_generation_id']}**",
        f"- Images générées de bibliothèque avec payload `.dat` trouvé : **{summary['generated_library_images_with_payload']}**",
        f"- Conversations avec un `image_asset_pointer` assistant : **{summary['conversations_with_assistant_image_pointer']}**",
        f"- Conversations avec un `image_asset_pointer` utilisateur : **{summary['conversations_with_user_image_pointer']}**",
        f"- Conversations `picture_v2` reliées à une image générée de bibliothèque : **{summary['picture_v2_with_generated_library_image']}**",
        f"- Conversations `picture_v2` sans pointeur conversationnel ni image générée de bibliothèque reliée : **{summary['picture_v2_without_conversation_pointer_or_generated_library_image']}**",
        "",
        "## Statuts conversationnels",
        "",
    ]
    report.extend(f"- `{key}` : **{value}**" for key, value in sorted(status_counts.items()))
    report.extend([
        "",
        "## Règle de lecture",
        "",
        "`picture_v2` est un indice de capacité/contexte, pas une preuve de résultat. `image_gen_generation_id` est une preuve explicite de génération pour une entrée de bibliothèque. `physical_payload_found` prouve que le `.dat` correspondant est présent dans un ZIP interne.",
        "",
        "Voir `conversation_image_audit.csv` et `library_image_inventory.csv` pour les lignes complètes.",
        "",
    ])
    (args.output / "image_export_audit_report.md").write_text("\n".join(report), encoding="utf-8")


if __name__ == "__main__":
    main()
