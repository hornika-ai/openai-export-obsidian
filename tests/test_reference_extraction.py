from __future__ import annotations

import io
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
from openai_export_obsidian.models import ArchiveMember
from openai_export_obsidian.conversations import parse_conversation
from openai_export_obsidian.reference_extraction import ReferenceExtractor
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


def raw_conversation() -> dict:
    return {
        "id": "conv-reference",
        "mapping": {
            "user-node": {
                "parent": None,
                "children": [],
                "message": {
                    "id": "msg-reference",
                    "author": {"role": "user"},
                    "content": {
                        "content_type": "multimodal_text",
                        "parts": [
                            {
                                "content_type": "image_asset_pointer",
                                "asset_pointer": "file-service://file-image",
                            },
                            {
                                "content_type": "real_time_user_audio_video_asset_pointer",
                                "audio_asset_pointer": {
                                    "content_type": "audio_asset_pointer",
                                    "asset_pointer": "sediment://file-audio",
                                },
                                "video_container_asset_pointer": {
                                    "content_type": "video_asset_pointer",
                                    "asset_pointer": "sediment://file-video",
                                },
                                "frames_asset_pointers": [
                                    {"asset_pointer": "sediment://file-frame"},
                                ],
                            },
                            "Saved to sandbox:/mnt/data/result.png and /mnt/data/second.png. "
                            "See https://example.com/source and data:image/png;base64,AAAA. "
                            "The original upload is file-inline.dat.",
                        ],
                    },
                    "metadata": {
                        "attachments": [
                            {
                                "id": "file-attachment",
                                "name": "Uploaded.pdf",
                                "mime_type": "application/pdf",
                                "size": 42,
                            }
                        ],
                        "dalle": {
                            "from_client": {
                                "operation": {
                                    "original_file_id": "file-original",
                                    "mask_file_id": "file-mask",
                                    "original_gen_id": "gen-original",
                                }
                            }
                        },
                        "image_gen_generation_id": "s_generated",
                        "canmore_uri": "canmore://textdoc/canmore-123",
                    },
                },
            }
        },
    }


class ReferenceExtractionTests(unittest.TestCase):
    def test_normalizes_message_and_metadata_references_without_physical_resolution(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            write_export(path, {"file-image.dat": b"payload"})
            inventory = ExportInventory.scan(str(path))
            source = ArchiveMember(str(path), "conversations-000.json", 0)
            raw = raw_conversation()
            conversation = parse_conversation(raw, source, {})

            records = ReferenceExtractor(inventory).extract_conversation(conversation, raw)
            by_kind = {record.reference_kind: record for record in records}

            self.assertEqual(by_kind["attachment_file_identifier"].raw_identifier, "file-attachment")
            self.assertEqual(by_kind["image_asset_pointer"].raw_identifier, "file-service://file-image")
            self.assertEqual(by_kind["image_asset_pointer"].normalized_identifier, "file-image")
            self.assertEqual(by_kind["image_asset_pointer"].namespace, "file-service")
            self.assertEqual(by_kind["realtime_audio_asset_pointer"].raw_identifier, "sediment://file-audio")
            self.assertEqual(by_kind["realtime_video_asset_pointer"].raw_identifier, "sediment://file-video")
            self.assertEqual(by_kind["realtime_frame_asset_pointer"].raw_identifier, "sediment://file-frame")
            self.assertEqual(by_kind["original_file_identifier"].raw_identifier, "file-original")
            self.assertEqual(by_kind["mask_file_identifier"].raw_identifier, "file-mask")
            self.assertEqual(by_kind["generation_identifier"].raw_identifier, "gen-original")
            self.assertEqual(by_kind["image_generation_identifier"].raw_identifier, "s_generated")
            self.assertIn("sandbox:/mnt/data/result.png", {record.raw_identifier for record in records})
            self.assertIn("/mnt/data/second.png", {record.raw_identifier for record in records})
            self.assertIn("https://example.com/source", {record.raw_identifier for record in records})
            self.assertIn("data:image/png;base64,AAAA", {record.raw_identifier for record in records})
            self.assertIn("canmore://textdoc/canmore-123", {record.raw_identifier for record in records})
            inline = next(record for record in records if record.raw_identifier == "file-inline.dat")
            self.assertEqual(inline.reference_kind, "file_identifier")
            self.assertEqual(inline.normalized_identifier, "file-inline")
            self.assertEqual(inline.namespace, "file")
            self.assertNotIn("file-service", {record.raw_identifier for record in records})
            self.assertNotIn("file-image", {record.raw_identifier for record in records})

            for record in records:
                payload = record.to_dict()
                self.assertNotIn("physical_archive_path", payload)
                self.assertNotIn("asset_status", payload)
                self.assertEqual(payload["inventory_source_archive"], str(path))
                self.assertEqual(payload["inventory_status"], "inventoried")

    def test_extracts_library_file_and_generation_identifiers_as_metadata_references(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            write_export(path, {"library_files.json": b"[]"})
            inventory = ExportInventory.scan(str(path))
            extractor = ReferenceExtractor(inventory)

            records = extractor.extract_library_metadata(
                {
                    "file-library": {
                        "file_id": "file-library",
                        "file_name": "Generated.png",
                        "mime_type": "image/png",
                        "origination_thread_id": "conv-library",
                        "image_gen_generation_id": "s_library_generated",
                    }
                }
            )

            self.assertEqual({record.scope for record in records}, {"library_metadata"})
            self.assertIn("file-library", {record.raw_identifier for record in records})
            self.assertIn("s_library_generated", {record.raw_identifier for record in records})
            self.assertEqual({record.conversation_id for record in records}, {"conv-library"})

    def test_library_conversation_fallbacks_preserve_source_relation(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            write_export(path, {"library_files.json": b"[]"})
            records = ReferenceExtractor(ExportInventory.scan(str(path))).extract_library_metadata(
                {
                    "file-initiating": {
                        "file_id": "file-initiating",
                        "initiating_conversation_id": {"id": "conv-initiating"},
                    }
                },
                selected_conversation_ids={"conv-initiating"},
            )
            self.assertTrue(records)
            self.assertEqual({record.conversation_id for record in records}, {"conv-initiating"})

    def test_parser_emits_reference_evidence_and_validator_enforces_extraction_boundary(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            inner = zip_bytes({"conversations-000.json": json.dumps([raw_conversation()]).encode("utf-8")})
            write_export(export_path, {"Conversations.zip": inner})

            run_parse(export_path, output_path, emit_json=True, emit_markdown=False)

            proof_dir = output_path / "90_Evidence"
            rows = [json.loads(line) for line in (proof_dir / "references.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertTrue(rows)
            self.assertTrue((proof_dir / "reference_summary.json").exists())
            self.assertEqual(validate_pack(output_path)["errors"], [])

            rows[0]["physical_archive_path"] = "must-not-appear.dat"
            (proof_dir / "references.jsonl").write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            errors = validate_pack(output_path)["errors"]
            self.assertTrue(any("must not contain physical resolution fields" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
