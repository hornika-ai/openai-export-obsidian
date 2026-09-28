from __future__ import annotations

import io
import hashlib
import json
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from openai_export_obsidian.inventory import ExportInventory
from openai_export_obsidian.asset_resolution_migration import AssetResolutionMigrator
from openai_export_obsidian.models import AssetRecord
from openai_export_obsidian.physical_resolution import LibraryResolutionIndex, PhysicalResolver
from openai_export_obsidian.reference_extraction import ReferenceExtractor
from openai_export_obsidian.reference_extraction import ReferenceRecord
from openai_export_obsidian.runner import run_parse
from openai_export_obsidian.validation import validate_pack


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return buffer.getvalue()


def write_export(path: Path, entries: dict[str, bytes]) -> None:
    path.write_bytes(zip_bytes(entries))


def reference(
    *,
    raw_identifier: str,
    normalized_identifier: str | None = None,
    namespace: str | None = "file",
    reference_kind: str = "file_identifier",
    logical_name: str | None = None,
    declared_size: int | None = None,
) -> ReferenceRecord:
    return ReferenceRecord(
        reference_id="conversation:conv:node:reference-0001",
        scope="conversation",
        reference_kind=reference_kind,
        value_kind="identifier",
        conversation_id="conv",
        message_id="message",
        node_id="node",
        source_shard="conversations-000.json",
        proof_path="mapping.node.message.metadata",
        metadata_key="file_id",
        raw_identifier=raw_identifier,
        normalized_identifier=normalized_identifier or raw_identifier,
        namespace=namespace,
        logical_name=logical_name,
        declared_mime_type=None,
        declared_size=declared_size,
        inventory_source_archive="fixture.zip",
        inventory_status="inventoried",
    )


def asset(*, physical_archive_path: str | None, asset_status: str) -> AssetRecord:
    return AssetRecord(
        asset_ref_id="conv:message:0001",
        conversation_id="conv",
        message_id="message",
        source_shard="conversations-000.json",
        proof_path="mapping.node.message.metadata",
        raw_file_id="file-image",
        raw_dat_filename="file-image.dat",
        physical_archive_path=physical_archive_path,
        reconstructed_filename="file-image.dat",
        normalized_extension="dat",
        mime_type=None,
        size=None,
        width=None,
        height=None,
        source_origin_fields={},
        library_file_id=None,
        origination_message_id=None,
        origination_thread_id=None,
        asset_status=asset_status,
        provenance_status="explicit",
        origin_classification="user",
        origin_confidence="explicit",
    )


class PhysicalResolutionTests(unittest.TestCase):
    def test_materialization_uses_the_canonical_source_when_multiple_referenced_payloads_match(self):
        """Copy order must not choose a non-canonical member of a SHA group."""
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            payload = b"# Exact duplicate payload\n"

            def conversation(conversation_id: str, file_id: str, name: str) -> dict[str, object]:
                return {
                    "id": conversation_id,
                    "title": conversation_id,
                    "mapping": {
                        "node": {
                            "message": {
                                "id": f"message-{conversation_id}",
                                "author": {"role": "user", "metadata": {}},
                                "content": {"content_type": "text", "parts": ["document"]},
                                "metadata": {
                                    "attachments": [{"id": file_id, "name": name, "size": len(payload)}]
                                },
                            }
                        }
                    },
                }

            # The first conversation is processed first, but file-a.dat is the
            # lexical canonical representative of the exact-content group.
            write_export(
                export_path,
                {
                    "Conversations.zip": zip_bytes(
                        {
                            "conversations-000.json": json.dumps(
                                [
                                    conversation("conversation-z", "file-z", "Z.md"),
                                    conversation("conversation-a", "file-a", "A.md"),
                                ]
                            ).encode("utf-8"),
                            "file-z.dat": payload,
                            "file-a.dat": payload,
                        }
                    )
                },
            )

            run_parse(
                export_path,
                output_path,
                copy_assets=True,
                copy_unlinked_assets=True,
                emit_json=True,
                emit_markdown=False,
            )

            proof_dir = output_path / "90_Evidence"
            payload_hash = hashlib.sha256(payload).hexdigest()
            canonical_path = "Conversations.zip::file-a.dat"
            dispositions = [
                json.loads(line)
                for line in (proof_dir / "payload_dispositions.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            equivalent_dispositions = [row for row in dispositions if row["content_sha256"] == payload_hash]
            self.assertEqual({row["disposition_status"] for row in equivalent_dispositions}, {"referenced_payload"})
            self.assertEqual({row["canonical_archive_path"] for row in equivalent_dispositions}, {canonical_path})

            manifest = [
                json.loads(line)
                for line in (proof_dir / "file_manifest.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            equivalent_manifest = [row for row in manifest if row.get("content_sha256") == payload_hash]
            self.assertEqual(len(equivalent_manifest), 2)
            self.assertEqual(
                {row["materialized_from_archive_path"] for row in equivalent_manifest},
                {canonical_path},
            )
            self.assertEqual({row["copied_path"] for row in equivalent_manifest}, {equivalent_manifest[0]["copied_path"]})
            self.assertEqual(sum(row["copy_status"] == "copied" for row in equivalent_manifest), 1)

            copied_equivalents = [
                path
                for path in (output_path / "20_Files").glob("**/*")
                if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == payload_hash
            ]
            self.assertEqual(len(copied_equivalents), 1)

            summary = json.loads((proof_dir / "payload_materialization_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["physical_payloads_observed"], 2)
            self.assertEqual(summary["canonical_payloads_materialized"], 1)
            self.assertEqual(summary["physical_copies_not_materialized"], 1)
            self.assertEqual(summary["true_unlinked_payloads_materialized_in_unlinked"], 0)

    def test_parser_resolves_identical_candidates_and_excludes_personal_duplicate_from_unlinked(self):
        """Equivalent bytes are a resolvable collision, not an unlinked payload."""
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            payload = b"# Identical document\n"
            raw = {
                "id": "conv-equivalent",
                "title": "Equivalent payload",
                "mapping": {
                    "node": {
                        "message": {
                            "id": "message-equivalent",
                            "author": {"role": "user", "metadata": {}},
                            "content": {"content_type": "text", "parts": ["document"]},
                            "metadata": {
                                "attachments": [
                                    {
                                        "id": "file-equivalent",
                                        "name": "Equivalent.md",
                                        "size": len(payload),
                                    }
                                ]
                            },
                        }
                    }
                },
            }
            write_export(
                export_path,
                {
                    "Conversations.zip": zip_bytes(
                        {
                            "conversations-000.json": json.dumps([raw]).encode("utf-8"),
                            "file-equivalent.dat": payload,
                        }
                    ),
                    "Files.zip": zip_bytes({"personal/files/Equivalent.md": payload}),
                },
            )

            run_parse(
                export_path,
                output_path,
                copy_assets=True,
                copy_unlinked_assets=True,
                emit_json=True,
                emit_markdown=False,
            )

            proof_dir = output_path / "90_Evidence"
            resolution = json.loads((proof_dir / "physical_resolutions.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(resolution["resolution_status"], "resolved_equivalent_candidates")
            self.assertEqual(resolution["selected_archive_path"], "Conversations.zip::file-equivalent.dat")
            self.assertEqual({row["sha256"] for row in resolution["candidates"]}, {hashlib.sha256(payload).hexdigest()})

            asset_row = json.loads((proof_dir / "asset_links.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(asset_row["asset_status"], "found")
            self.assertEqual(asset_row["physical_archive_path"], "Conversations.zip::file-equivalent.dat")
            self.assertTrue((output_path / asset_row["copied_pack_path"]).exists())

            payload_hash = hashlib.sha256(payload).hexdigest()
            dispositions = [
                json.loads(line)
                for line in (proof_dir / "payload_dispositions.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            conversation = next(row for row in dispositions if row["physical_archive_path"] == "Conversations.zip::file-equivalent.dat")
            personal = next(row for row in dispositions if row["physical_archive_path"] == "Files.zip::personal/files/Equivalent.md")
            self.assertEqual(conversation["disposition_status"], "referenced_payload")
            self.assertEqual(personal["disposition_status"], "duplicate_of_referenced_payload")
            self.assertEqual(conversation["content_equivalence_group_id"], f"sha256:{payload_hash}")
            self.assertEqual(personal["content_equivalence_group_id"], f"sha256:{payload_hash}")
            self.assertEqual(
                set(personal["equivalent_archive_paths"]),
                {"Conversations.zip::file-equivalent.dat", "Files.zip::personal/files/Equivalent.md"},
            )

            unlinked = (proof_dir / "unlinked_assets.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("Files.zip::personal/files/Equivalent.md", unlinked)
            unlinked_dir = output_path / "20_Files" / "Unlinked"
            self.assertFalse(any(path.name == "Equivalent.md" for path in unlinked_dir.glob("**/*")) if unlinked_dir.exists() else False)

            manifest = [
                json.loads(line)
                for line in (proof_dir / "file_manifest.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            equivalent_manifest = [row for row in manifest if row.get("content_sha256") == payload_hash]
            self.assertEqual(len(equivalent_manifest), 1)
            self.assertEqual(equivalent_manifest[0]["archive_path"], "Conversations.zip::file-equivalent.dat")
            self.assertEqual(equivalent_manifest[0]["copied_path"], asset_row["copied_pack_path"])

            copied_equivalents = [
                path
                for path in (output_path / "20_Files").glob("**/*")
                if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == payload_hash
            ]
            self.assertEqual(copied_equivalents, [output_path / asset_row["copied_pack_path"]])

            inventory_paths = {
                row["archive_path"]
                for row in (
                    json.loads(line)
                    for line in (proof_dir / "inventory.jsonl").read_text(encoding="utf-8").splitlines()
                )
            }
            self.assertTrue({"Conversations.zip::file-equivalent.dat", "Files.zip::personal/files/Equivalent.md"} <= inventory_paths)

    def test_resolves_unique_file_identifier_and_retains_detected_inventory_facts(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            png = b"\x89PNG\r\n\x1a\nfixture"
            write_export(path, {"personal/files/file-image.dat": png})

            result = PhysicalResolver(ExportInventory.scan(str(path))).resolve(
                reference(raw_identifier="file-service://file-image", normalized_identifier="file-image", namespace="file-service")
            )

            self.assertEqual(result.resolution_status, "resolved_unique")
            self.assertEqual(result.selected_archive_path, "personal/files/file-image.dat")
            self.assertEqual(result.candidate_count, 1)
            candidate = result.candidates[0]
            self.assertIn("file_identifier_dat_basename", candidate.match_methods)
            self.assertEqual(candidate.detected_extension, "png")
            self.assertEqual(candidate.mime_type, "image/png")

    def test_keeps_zip_signature_office_payload_eligible_as_a_physical_candidate(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            docx_payload = zip_bytes({"[Content_Types].xml": b"<Types/>"})
            write_export(path, {"personal/files/brief.docx": docx_payload})

            result = PhysicalResolver(ExportInventory.scan(str(path))).resolve(
                reference(raw_identifier="file-brief", logical_name="brief.docx")
            )

            self.assertEqual(result.resolution_status, "resolved_unique")
            self.assertEqual(result.candidates[0].family, "archive_internal")
            self.assertEqual(result.selected_archive_path, "personal/files/brief.docx")

    def test_library_resolution_index_uses_direct_file_id_and_refuses_collision(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            row = {
                "file_id": "file-knowledge",
                "file_name": "knowledge.txt",
                "knowledge_store_id": "store-1",
            }
            write_export(path, {"library_files.json": json.dumps([row]).encode("utf-8"), "personal/files/knowledge.txt": b"knowledge"})
            inventory = ExportInventory.scan(str(path))
            references = ReferenceExtractor(inventory).extract_library_metadata({"file-knowledge": row})
            index = LibraryResolutionIndex(PhysicalResolver(inventory).resolve_all(references))

            self.assertEqual(index.selected_archive_path("file-knowledge"), "personal/files/knowledge.txt")

            write_export(
                path,
                {
                    "library_files.json": json.dumps([row]).encode("utf-8"),
                    "personal/files/knowledge.txt": b"knowledge",
                    "file-knowledge.dat": b"duplicate",
                },
            )
            inventory = ExportInventory.scan(str(path))
            references = ReferenceExtractor(inventory).extract_library_metadata({"file-knowledge": row})
            index = LibraryResolutionIndex(PhysicalResolver(inventory).resolve_all(references))

            self.assertIsNone(index.selected_archive_path("file-knowledge"))

    def test_reports_collision_without_selecting_a_basename_candidate(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            inner = zip_bytes(
                {
                    "personal/files/one/file-duplicate.dat": b"first",
                    "personal/files/two/file-duplicate.dat": b"second",
                }
            )
            write_export(path, {"Files.zip": inner})

            result = PhysicalResolver(ExportInventory.scan(str(path))).resolve(reference(raw_identifier="file-duplicate"))

            self.assertEqual(result.resolution_status, "collision")
            self.assertIsNone(result.selected_archive_path)
            self.assertEqual(result.candidate_count, 2)
            self.assertEqual(
                [candidate.archive_path for candidate in result.candidates],
                [
                    "Files.zip::personal/files/one/file-duplicate.dat",
                    "Files.zip::personal/files/two/file-duplicate.dat",
                ],
            )

    def test_distinguishes_absent_external_inline_and_non_physical_references(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            write_export(path, {"personal/files/file-present.dat": b"present"})
            resolver = PhysicalResolver(ExportInventory.scan(str(path)))

            self.assertEqual(resolver.resolve(reference(raw_identifier="file-missing")).resolution_status, "no_candidate_in_inventory")
            self.assertEqual(
                resolver.resolve(
                    reference(
                        raw_identifier="https://example.test/file",
                        namespace="external-url",
                        reference_kind="external_url",
                    )
                ).resolution_status,
                "external_non_exportable",
            )
            self.assertEqual(
                resolver.resolve(
                    reference(raw_identifier="data:image/png;base64,AAAA", namespace="data-uri", reference_kind="data_uri")
                ).resolution_status,
                "inline_payload_not_archive_member",
            )
            self.assertEqual(
                resolver.resolve(
                    reference(raw_identifier="gen-123", namespace=None, reference_kind="generation_identifier")
                ).resolution_status,
                "non_physical_reference",
            )

    def test_resolves_mnt_data_path_by_explicit_inventory_suffix_method(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            write_export(path, {"personal/files/session/mnt/data/result.png": b"image"})

            result = PhysicalResolver(ExportInventory.scan(str(path))).resolve(
                reference(
                    raw_identifier="sandbox:/mnt/data/result.png",
                    normalized_identifier="/mnt/data/result.png",
                    namespace="sandbox",
                    reference_kind="sandbox_path",
                )
            )

            self.assertEqual(result.resolution_status, "resolved_unique")
            self.assertIn("normalized_identifier_mnt_data_suffix", result.candidates[0].match_methods)

    def test_marks_absence_indeterminate_when_inventory_contains_nested_archive_errors(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            write_export(path, {"broken-inner.zip": b"not a zip"})

            result = PhysicalResolver(ExportInventory.scan(str(path))).resolve(reference(raw_identifier="file-missing"))

            self.assertEqual(result.resolution_status, "indeterminate_inventory_error")
            self.assertEqual(result.candidates, [])

    def test_migration_applies_only_a_unique_copyable_resolution_and_preserves_collision_fallback(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            write_export(path, {"personal/files/file-image.dat": b"payload"})
            resolution = PhysicalResolver(ExportInventory.scan(str(path))).resolve_all([reference(raw_identifier="file-image")])
            unique_asset = asset(physical_archive_path=None, asset_status="missing")

            unique = AssetResolutionMigrator(
                resolution,
                copyable_archive_paths={"personal/files/file-image.dat"},
            ).apply([unique_asset]).comparisons[0]

            self.assertEqual(unique.migration_status, "applied_unique_new")
            self.assertEqual(unique_asset.asset_status, "found")
            self.assertEqual(unique_asset.physical_archive_path, "personal/files/file-image.dat")

            duplicate = zip_bytes(
                {
                    "personal/files/one/file-image.dat": b"one",
                    "personal/files/two/file-image.dat": b"two",
                    "personal/files/display-name.txt": b"name hint",
                }
            )
            write_export(path, {"Files.zip": duplicate})
            collision = PhysicalResolver(ExportInventory.scan(str(path))).resolve_all(
                [
                    reference(raw_identifier="file-image"),
                    reference(raw_identifier="display-name.txt", normalized_identifier="display-name.txt"),
                ]
            )
            fallback_asset = asset(physical_archive_path="Files.zip::personal/files/one/file-image.dat", asset_status="found")
            fallback_asset.reconstructed_filename = "display-name.txt"
            fallback = AssetResolutionMigrator(
                collision,
                copyable_archive_paths={"Files.zip::personal/files/one/file-image.dat"},
            ).apply([fallback_asset]).comparisons[0]

            self.assertEqual(fallback.migration_status, "applied_collision")
            self.assertEqual(fallback_asset.asset_status, "collision")
            self.assertIsNone(fallback_asset.physical_archive_path)

    def test_parser_emits_resolution_evidence_and_validator_requires_one_row_per_reference(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            raw = {
                "id": "conv-resolution",
                "mapping": {
                    "node": {
                        "message": {
                            "id": "message-resolution",
                            "content": {
                                "content_type": "multimodal_text",
                                "parts": [{"content_type": "image_asset_pointer", "asset_pointer": "file-service://file-image"}],
                            },
                            "metadata": {},
                        }
                    }
                },
            }
            inner = zip_bytes({"conversations-000.json": json.dumps([raw]).encode("utf-8")})
            write_export(export_path, {"Conversations.zip": inner, "file-image.dat": b"payload"})

            run_parse(export_path, output_path, emit_json=True, emit_markdown=False)

            proof_dir = output_path / "90_Evidence"
            rows = [
                json.loads(line)
                for line in (proof_dir / "physical_resolutions.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["resolution_status"], "resolved_unique")
            self.assertTrue((proof_dir / "physical_resolution_summary.json").exists())
            comparisons = [
                json.loads(line)
                for line in (proof_dir / "asset_resolution_comparisons.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(comparisons), 1)
            self.assertEqual(comparisons[0]["migration_status"], "applied_unique_new")
            self.assertEqual(validate_pack(output_path)["errors"], [])

            rows.pop()
            (proof_dir / "physical_resolutions.jsonl").write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            errors = validate_pack(output_path)["errors"]
            self.assertTrue(any("do not exactly match references.jsonl" in error for error in errors), errors)

    def test_parser_marks_colliding_asset_without_retaining_legacy_selected_path(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            raw = {
                "id": "conv-collision",
                "mapping": {
                    "node": {
                        "message": {
                            "id": "message-collision",
                            "content": {
                                "content_type": "multimodal_text",
                                "parts": [{"content_type": "image_asset_pointer", "asset_pointer": "file-service://file-image"}],
                            },
                            "metadata": {},
                        }
                    }
                },
            }
            inner = zip_bytes({"conversations-000.json": json.dumps([raw]).encode("utf-8")})
            files = zip_bytes(
                {
                    "personal/files/one/file-image.dat": b"one",
                    "personal/files/two/file-image.dat": b"two",
                }
            )
            write_export(export_path, {"Conversations.zip": inner, "Files.zip": files})

            run_parse(export_path, output_path, emit_json=True, emit_markdown=False)

            proof_dir = output_path / "90_Evidence"
            asset_row = json.loads((proof_dir / "asset_links.jsonl").read_text(encoding="utf-8").splitlines()[0])
            comparison = json.loads(
                (proof_dir / "asset_resolution_comparisons.jsonl").read_text(encoding="utf-8").splitlines()[0]
            )
            self.assertEqual(asset_row["asset_status"], "collision")
            self.assertIsNone(asset_row["physical_archive_path"])
            self.assertEqual(comparison["migration_status"], "applied_collision")
            self.assertEqual(validate_pack(output_path)["errors"], [])

    def test_parser_copies_global_knowledge_file_only_after_unique_library_resolution(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            library = {
                "file_id": "file-knowledge",
                "file_name": "knowledge.txt",
                "knowledge_store_id": "store-1",
            }
            write_export(
                export_path,
                {
                    "Conversations.zip": zip_bytes({"conversations-000.json": b"[]"}),
                    "library_files.json": json.dumps([library]).encode("utf-8"),
                    "personal/files/knowledge.txt": b"knowledge",
                },
            )

            run_parse(export_path, output_path, copy_assets=True, emit_json=True, emit_markdown=False)

            manifest = [
                json.loads(line)
                for line in (output_path / "90_Evidence" / "file_manifest.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            knowledge = next(row for row in manifest if row.get("file_id") == "file-knowledge")
            self.assertEqual(knowledge["archive_path"], "personal/files/knowledge.txt")
            self.assertTrue((output_path / knowledge["copied_path"]).exists())


if __name__ == "__main__":
    unittest.main()
