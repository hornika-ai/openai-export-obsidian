from __future__ import annotations

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
from openai_export_obsidian.runtime_artifacts import RuntimeArtifactLinker, parse_runtime_artifact_path


CONVERSATION_ID = "683e36ce-5950-8011-a3c2-e9d6e6cc1661"
EXECUTION_ID = "062e9ca4-d3c5-4253-ac1e-05231fd5eb66"


def chat_html(conversations: list[dict[str, object]]) -> str:
    return f"<html><script>var jsonData = {json.dumps(conversations)};</script></html>"


def auxiliary_conversation() -> dict[str, object]:
    return {
        "id": CONVERSATION_ID,
        "conversation_id": CONVERSATION_ID,
        "mapping": {
            EXECUTION_ID: {
                "id": EXECUTION_ID,
                "parent": "parent-node",
                "message": {
                    "id": EXECUTION_ID,
                    "author": {"role": "assistant"},
                    "recipient": "python",
                    "create_time": 1748914707.011631,
                    "content": {"content_type": "code", "text": "result.to_csv(...)"},
                },
            }
        },
    }


class RuntimeArtifactTests(unittest.TestCase):
    def test_parses_explicit_conversation_execution_runtime_path(self):
        parsed = parse_runtime_artifact_path(
            f"personal/files/{CONVERSATION_ID}/{EXECUTION_ID}/mnt/data/result.csv"
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.conversation_id, CONVERSATION_ID)
        self.assertEqual(parsed.execution_message_id, EXECUTION_ID)
        self.assertEqual(parsed.runtime_path, "/mnt/data/result.csv")

    def test_confirms_runtime_payload_against_chat_html_execution_node(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            payload_path = f"personal/files/{CONVERSATION_ID}/{EXECUTION_ID}/mnt/data/result.csv"
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("chat.html", chat_html([auxiliary_conversation()]))
                archive.writestr(payload_path, "column\nvalue\n")

            archive = ExportArchive(path)
            result = RuntimeArtifactLinker(archive, archive.inventory).link([CONVERSATION_ID])

            self.assertEqual(len(result.records), 1)
            record = result.records[0]
            self.assertEqual(record.relation_status, "confirmed_by_chat_html")
            self.assertEqual(record.execution_message_id, EXECUTION_ID)
            self.assertEqual(record.execution_author_role, "assistant")
            self.assertEqual(record.execution_recipient, "python")
            self.assertEqual(record.execution_content_type, "code")
            self.assertEqual(record.runtime_path, "/mnt/data/result.csv")
            self.assertEqual(result.scanned_chat_html_archive_paths, ["chat.html"])

    def test_keeps_conversation_scoped_runtime_payload_visible_without_html_confirmation(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            payload_path = f"personal/files/{CONVERSATION_ID}/{EXECUTION_ID}/mnt/data/result.csv"
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(payload_path, "column\nvalue\n")

            archive = ExportArchive(path)
            result = RuntimeArtifactLinker(archive, archive.inventory).link([CONVERSATION_ID])

            self.assertEqual(len(result.records), 1)
            self.assertEqual(result.records[0].relation_status, "conversation_scoped_unconfirmed")
            self.assertIsNone(result.records[0].chat_html_archive_path)
