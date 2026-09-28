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

from openai_export_obsidian.archive import ExportArchive
from openai_export_obsidian.conversations import parse_conversation
from openai_export_obsidian.embedded_historical_exports import EmbeddedHistoricalExportIngestor
from openai_export_obsidian.models import ArchiveMember


CONVERSATION_ID = "conv-history-fixture"


def primary_raw() -> dict[str, object]:
    return {
        "id": CONVERSATION_ID,
        "title": "Primary conversation",
        "current_node": "primary-user",
        "mapping": {
            "primary-user": {
                "id": "primary-user",
                "parent": None,
                "children": [],
                "message": {
                    "id": "primary-user",
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": ["hello"]},
                },
            }
        },
    }


def historical_raw() -> dict[str, object]:
    raw = primary_raw()
    mapping = raw["mapping"]
    assert isinstance(mapping, dict)
    mapping["historic-python"] = {
        "id": "historic-python",
        "parent": "primary-user",
        "children": [],
        "message": {
            "id": "historic-python",
            "author": {"role": "assistant"},
            "recipient": "python",
            "content": {
                "content_type": "code",
                "text": "result.to_csv('/mnt/data/report.csv')",
                "parts": [{"asset_pointer": "file-service://file-old"}],
            },
            "metadata": {
                "image_gen_generation_id": "gen-old",
                "dalle": {"prompt": "historical image"},
                "canmore_id": "textdoc-old",
            },
        },
    }
    return raw


class EmbeddedHistoricalExportTests(unittest.TestCase):
    def test_indexes_nested_monolithic_export_without_merging_it_into_primary(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            nested_bytes = io.BytesIO()
            with zipfile.ZipFile(nested_bytes, "w", zipfile.ZIP_DEFLATED) as historical:
                historical.writestr("conversations.json", json.dumps([historical_raw()]))
                historical.writestr("chat.html", "<html>historical</html>")
                historical.writestr("user.json", "{}")
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as outer:
                outer.writestr("User Online Activity/Conversations__fixture.zip", nested_bytes.getvalue())
                outer.writestr("User Online Activity/Files__fixture.zip", nested_bytes.getvalue())

            archive = ExportArchive(path)
            self.assertEqual(
                [entry.family for entry in archive.inventory.entries if entry.basename == "conversations.json"],
                ["conversation_monolith", "conversation_monolith"],
            )
            primary = parse_conversation(
                primary_raw(),
                ArchiveMember(outer_zip="fixture", name="conversations-000.json", size=0),
                {},
            )
            result = EmbeddedHistoricalExportIngestor(archive, archive.inventory).ingest([primary])

            self.assertEqual(len(result.snapshots), 2)
            self.assertEqual(sum(row.duplicate_of_snapshot_id is not None for row in result.snapshots), 1)
            self.assertEqual(result.snapshots[0].content_sha256, result.snapshots[1].content_sha256)
            self.assertIsNotNone(result.snapshots[0].content_sha256)
            self.assertEqual(len(result.comparisons), 1)
            comparison = result.comparisons[0]
            self.assertEqual(comparison.historical_only_message_count, 1)
            self.assertEqual(comparison.shared_message_count, 1)
            self.assertEqual(len(result.messages), 2)
            historic = next(row for row in result.messages if row.node_id == "historic-python")
            self.assertEqual(historic.message_status, "historical_only")
            kinds = {row.signal_kind for row in result.signals}
            self.assertTrue({"python_execution", "mnt_data_path", "asset_pointer", "image_gen", "dalle", "canmore"} <= kinds)
            self.assertTrue(all("conversations.json" in row.historical_conversations_archive_path for row in result.signals))
