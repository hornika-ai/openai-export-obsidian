from __future__ import annotations

import hashlib
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

from openai_export_obsidian.archive import ExportArchive
from openai_export_obsidian.inventory import ExportInventory
from openai_export_obsidian.runner import run_parse


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return buffer.getvalue()


def write_outer_zip(path: Path, entries: dict[str, bytes]) -> None:
    path.write_bytes(zip_bytes(entries))


class InventoryTests(unittest.TestCase):
    def test_recursively_inventories_nested_zips_and_keeps_archive_paths_readable(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            deepest = zip_bytes({"conversations-000.json": b"[]"})
            middle = zip_bytes({"inner.zip": deepest, "personal/files/no-extension": b"hello"})
            write_outer_zip(path, {"Conversations.zip": middle})

            inventory = ExportInventory.scan(str(path))

            conversation = next(entry for entry in inventory.entries if entry.family == "conversation_shard")
            self.assertEqual(conversation.depth, 2)
            self.assertEqual(conversation.archive_chain, ("Conversations.zip", "inner.zip"))
            self.assertEqual(conversation.archive_path, "Conversations.zip::inner.zip::conversations-000.json")
            self.assertEqual(inventory.summary_dict()["counts"]["max_depth"], 2)

            archive = ExportArchive(path, inventory=inventory)
            member = next(member for member in archive.iter_members() if member.archive_path == conversation.archive_path)
            self.assertEqual(archive.read_text(member), "[]")

    def test_detects_dat_payload_by_signature_and_optional_hash(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            png = b"\x89PNG\r\n\x1a\n" + b"fixture"
            write_outer_zip(path, {"file-image.dat": png})

            inventory = ExportInventory.scan(str(path), forensic_hashes=True)
            entry = inventory.entries[0]

            self.assertEqual(entry.extension, "dat")
            self.assertEqual(entry.detected_extension, "png")
            self.assertEqual(entry.mime_type, "image/png")
            self.assertEqual(entry.signature, "png")
            self.assertEqual(entry.family, "file_payload")
            self.assertEqual(entry.sha256, hashlib.sha256(png).hexdigest())

    def test_inventories_files_without_extensions_and_preserves_detected_text_type(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            write_outer_zip(path, {"personal/files/session/mnt/data/result": b"plain exported text\n"})

            inventory = ExportInventory.scan(str(path))
            entry = inventory.entries[0]

            self.assertIsNone(entry.extension)
            self.assertEqual(entry.detected_extension, "txt")
            self.assertEqual(entry.mime_type, "text/plain")
            self.assertEqual(entry.family, "personal_file")
            self.assertEqual(entry.namespace, "mnt/data")
            self.assertTrue(entry.is_physical_payload)

    def test_reports_basename_collisions_without_resolving_them(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            write_outer_zip(
                path,
                {
                    "personal/files/one/report.dat": b"first",
                    "personal/files/two/report.dat": b"second",
                },
            )

            inventory = ExportInventory.scan(str(path))
            collisions = inventory.basename_collisions()

            self.assertEqual(list(collisions), ["report.dat"])
            self.assertEqual(
                [entry.archive_path for entry in collisions["report.dat"]],
                ["personal/files/one/report.dat", "personal/files/two/report.dat"],
            )
            self.assertEqual(inventory.summary_dict()["counts"]["basename_collision_groups"], 1)

    def test_classifies_export_manifest_and_keeps_non_parser_members_visible(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            manifest = json.dumps({"export_files": [], "logical_files": []}).encode("utf-8")
            write_outer_zip(
                path,
                {
                    "export_manifest.json": manifest,
                    "user.json": b'{"id":"user-1"}',
                    "user_settings.json": b"{}",
                    "message_feedback.json": b"[]",
                    "chat.html": b"<html><body>export</body></html>",
                    "Financial/Invoice Items.csv": b"id,amount\n1,10\n",
                },
            )

            inventory = ExportInventory.scan(str(path))
            by_path = {entry.member_path: entry for entry in inventory.entries}

            self.assertEqual(by_path["export_manifest.json"].family, "export_manifest")
            self.assertEqual(by_path["user.json"].family, "user_profile")
            self.assertEqual(by_path["user_settings.json"].family, "user_settings")
            self.assertEqual(by_path["message_feedback.json"].family, "message_feedback")
            self.assertEqual(by_path["chat.html"].family, "html_export")
            self.assertEqual(by_path["Financial/Invoice Items.csv"].family, "csv")
            self.assertEqual(len(inventory.entries), 6)

    def test_empty_archive_is_a_valid_empty_inventory(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.zip"
            write_outer_zip(path, {})

            inventory = ExportInventory.scan(str(path))

            self.assertEqual(inventory.source_status, "inventoried")
            self.assertEqual(inventory.entries, [])
            self.assertEqual(inventory.errors, [])
            self.assertEqual(inventory.summary_dict()["counts"]["files"], 0)

    def test_corrupt_archive_is_reported_without_raising(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.zip"
            path.write_bytes(b"not a zip")

            inventory = ExportInventory.scan(str(path))

            self.assertEqual(inventory.source_status, "corrupt_archive")
            self.assertEqual(inventory.entries, [])
            self.assertEqual(inventory.errors[0].code, "source_corrupt_archive")

    def test_parser_emits_canonical_inventory_evidence_without_new_cli_flags(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "export.zip"
            output_path = tmp_path / "out"
            inner = zip_bytes(
                {
                    "conversations-000.json": b"[]",
                    "export_manifest.json": b'{"export_files": [], "logical_files": []}',
                    "file-found.dat": b"payload",
                }
            )
            write_outer_zip(export_path, {"Conversations.zip": inner, "user.json": b"{}"})

            run_parse(export_path, output_path, emit_json=True, emit_markdown=False)

            proof_dir = output_path / "90_Evidence"
            rows = [json.loads(line) for line in (proof_dir / "inventory.jsonl").read_text(encoding="utf-8").splitlines()]
            summary = json.loads((proof_dir / "inventory_summary.json").read_text(encoding="utf-8"))
            self.assertTrue((proof_dir / "inventory_errors.jsonl").exists())
            self.assertIn("export_manifest", {row["family"] for row in rows})
            self.assertIn("user_profile", {row["family"] for row in rows})
            self.assertEqual(summary["source_status"], "inventoried")


if __name__ == "__main__":
    unittest.main()
