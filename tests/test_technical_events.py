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
from openai_export_obsidian.models import ArchiveMember
from openai_export_obsidian.technical_events import TechnicalEventExtractor, observe_message_technical_fields


CONVERSATION_ID = "conv-technical-fixture"


def primary_conversation() -> dict[str, object]:
    return {
        "id": CONVERSATION_ID,
        "gizmo_id": "g-fixture",
        "mapping": {
            "image-node": {
                "id": "image-node",
                "parent": None,
                "children": [],
                "message": {
                    "id": "image-node",
                    "author": {"role": "assistant"},
                    "recipient": "all",
                    "content": {
                        "content_type": "multimodal_text",
                        "parts": [{"asset_pointer": "file-service://file-image"}],
                    },
                    "metadata": {
                        "request_id": "request-primary",
                        "image_gen": {"from_client": {"operation": {"original_gen_id": "gen-original", "original_file_id": "file-original", "mask_file_id": "file-mask"}}},
                    },
                },
            }
        },
    }


def historical_conversation() -> dict[str, object]:
    raw = primary_conversation()
    mapping = raw["mapping"]
    assert isinstance(mapping, dict)
    image = mapping["image-node"]
    assert isinstance(image, dict)
    image_message = image["message"]
    assert isinstance(image_message, dict)
    image_message["recipient"] = "dalle.text2im"
    mapping["canmore-node"] = {
        "id": "canmore-node",
        "parent": "image-node",
        "children": ["unknown-node"],
        "message": {
            "id": "canmore-node",
            "author": {"role": "assistant"},
            "recipient": "canmore.create_textdoc",
            "content": {"content_type": "text", "text": "canmore://textdoc-fixture"},
            "metadata": {"canvas": {"textdoc_id": "textdoc-fixture"}},
        },
    }
    mapping["unknown-node"] = {
        "id": "unknown-node",
        "parent": "canmore-node",
        "children": [],
        "message": {
            "id": "unknown-node",
            "author": {"role": "assistant"},
            "recipient": "vendor.unclassified_tool",
            "content": {"content_type": "text", "text": "saved at /mnt/data/result.csv"},
            "metadata": {},
        },
    }
    return raw


class TechnicalEventTests(unittest.TestCase):
    def test_distinguishes_absent_null_empty_and_value_field_states(self):
        observed = observe_message_technical_fields(
            {
                "content": {"parts": [{"asset_pointer": "file-service://file-value"}]},
                "metadata": {
                    "dalle": {"from_client": {"operation": {"original_gen_id": None, "original_file_id": "", "mask_file_id": "file-mask"}}},
                },
            }
        )
        self.assertEqual(observed["field_presence"]["asset_pointer"], "value")
        self.assertEqual(observed["field_presence"]["original_gen_id"], "null")
        self.assertEqual(observed["field_presence"]["original_file_id"], "empty")
        self.assertEqual(observed["field_presence"]["mask_file_id"], "value")
        self.assertEqual(observed["field_presence"]["textdoc_reference"], "absent")

    def test_extracts_embedded_canmore_uri_without_promoting_the_full_message_or_type_metadata(self):
        source_text = "Document: [target](canmore://textdoc/abcdef0123456789abcdef0123456789)."
        observed = observe_message_technical_fields(
            {
                "content": {"parts": [source_text]},
                "metadata": {"canvas": {"textdoc_id": "code/json", "textdoc_type": "document"}},
            }
        )
        self.assertEqual(observed["textdoc_references"], ["canmore://textdoc/abcdef0123456789abcdef0123456789"])
        self.assertNotIn(source_text, observed["textdoc_references"])
        self.assertEqual(
            {row["value"] for row in observed["textdoc_non_identifier_values"]},
            {"code/json", "document"},
        )
        self.assertEqual(observed["embedded_canmore_source_texts"], [{"path": "message.content.parts[0]", "text": source_text}])

    def test_extracts_source_separated_events_unknowns_and_exact_key_comparisons(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.zip"
            nested = io.BytesIO()
            with zipfile.ZipFile(nested, "w", zipfile.ZIP_DEFLATED) as historical:
                historical.writestr("conversations.json", json.dumps([historical_conversation()]))
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as outer:
                outer.writestr("Conversations__history.zip", nested.getvalue())

            archive = ExportArchive(path)
            primary = parse_conversation(
                primary_conversation(),
                ArchiveMember(outer_zip="fixture", name="conversations-000.json", size=0),
                {},
            )
            result = TechnicalEventExtractor(archive, archive.inventory).extract(
                [primary], {CONVERSATION_ID: primary_conversation()}
            )

            self.assertTrue(any(row.source_export == "primary_2026_json" for row in result.records))
            self.assertTrue(any(row.source_export == "historical_embedded_json" for row in result.records))
            self.assertTrue(any(row.event_family == "dalle_call" and row.recipient == "dalle.text2im" for row in result.records))
            self.assertTrue(any(row.event_family == "canmore_call" and row.operation_observed == "canmore.create_textdoc" for row in result.records))
            unknown = [row for row in result.records if row.event_family == "unknown_recipient"]
            self.assertEqual(len(unknown), 1)
            self.assertEqual(unknown[0].recipient, "vendor.unclassified_tool")
            self.assertEqual(unknown[0].mnt_data_paths, ["/mnt/data/result.csv"])
            image_comparison = next(
                row for row in result.comparisons
                if row.node_id == "image-node" and row.event_family == "image_operation"
            )
            self.assertEqual(image_comparison.comparison_status, "contradiction")
            self.assertIn("recipient", image_comparison.contradictory_fields)
            self.assertEqual(image_comparison.comparison_basis, "exact conversation_id + node_id + message_id + event_family + operation_observed")
            self.assertTrue(all(row.observation_status == "observed_no_physical_resolution" for row in result.records))
            self.assertTrue(all("physical_archive_path" not in row.to_dict() for row in result.records))
            self.assertTrue(all(row.proof_path for row in result.records))
            self.assertTrue(all(row.proof_path.startswith(("mapping.", "conversation.")) for row in result.records if row.source_export != "chat_html_auxiliary"))
            streamed = TechnicalEventExtractor(archive, archive.inventory).extract_streaming([CONVERSATION_ID])
            self.assertEqual(
                [row.technical_event_id for row in streamed.records if row.source_export == "historical_embedded_json"],
                [row.technical_event_id for row in result.records if row.source_export == "historical_embedded_json"],
            )
