from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from openai_export_obsidian.logical_entities import LogicalEntityConstructor


def event(
    event_id: str,
    family: str,
    *,
    source: str = "primary_2026_json",
    conversation_id: str = "conv-entity",
    **fields: object,
) -> dict[str, object]:
    return {
        "technical_event_id": event_id,
        "event_family": family,
        "operation_observed": family,
        "source_export": source,
        "source_archive_path": f"{source}::conversations.json",
        "proof_path": f"mapping.node-{event_id}.message.metadata",
        "conversation_id": conversation_id,
        "node_id": f"node-{event_id}",
        "message_id": f"message-{event_id}",
        **fields,
    }


class LogicalEntityConstructionTests(unittest.TestCase):
    def test_groups_only_exact_explicit_identifiers_and_preserves_source_proof(self):
        result = LogicalEntityConstructor().construct(
            [
                event("technical-event:one", "image_operation", original_gen_ids=["gen-1"], original_file_ids=["file-1"]),
                event(
                    "technical-event:two",
                    "image_operation",
                    source="historical_embedded_json",
                    original_gen_ids=["gen-1"],
                    original_file_ids=["file-1"],
                ),
                event("technical-event:three", "asset_reference", asset_pointers=["file-1"]),
                event("technical-event:four", "textdoc_reference", textdoc_references=["canmore://textdoc-1"]),
                event("technical-event:five", "gizmo_context", gizmo_id="gizmo-1"),
                event("technical-event:six", "runtime_execution"),
            ]
        )

        self.assertEqual(len(result.records), 5)
        generation = next(row for row in result.records if row.entity_type == "original_generation_reference")
        self.assertEqual(generation.explicit_identifier, "gen-1")
        self.assertEqual(generation.source_presence_status, "present_in_primary_and_historical_by_exact_identifier")
        self.assertEqual(len(generation.source_event_ids), 2)
        self.assertFalse(generation.candidate_edges_created)

        file_entities = [row for row in result.records if row.explicit_identifier == "file-1"]
        self.assertEqual(len(file_entities), 2)
        self.assertEqual({row.entity_type for row in file_entities}, {"original_file_reference", "remote_asset_pointer"})
        self.assertTrue(all(row.logical_entity_id.startswith("logical-entity:") for row in result.records))
        self.assertTrue(all(row.proof_path for row in result.observations))
        self.assertEqual([row["technical_event_id"] for row in result.unmaterialized_events], ["technical-event:six"])

    def test_does_not_create_entities_from_recipient_or_non_explicit_field_values(self):
        result = LogicalEntityConstructor().construct(
            [
                event("technical-event:recipient", "dalle_call", recipient="dalle.text2im", gizmo_id="inherited-gizmo"),
                event("technical-event:canmore", "canmore_call", textdoc_references=["canmore://not-proven-by-family"]),
                event("technical-event:unknown", "unknown_recipient", mnt_data_paths=["/mnt/data/not-proven-by-family"]),
            ]
        )
        self.assertEqual(result.records, [])
        self.assertEqual(result.observations, [])
        self.assertEqual(
            {row["technical_event_id"] for row in result.unmaterialized_events},
            {"technical-event:recipient", "technical-event:canmore", "technical-event:unknown"},
        )

    def test_ids_are_deterministic_and_no_synthetic_generation_is_created(self):
        events = [event("technical-event:one", "image_operation", original_gen_ids=["gen-1"])]
        first = LogicalEntityConstructor().construct(events)
        second = LogicalEntityConstructor().construct(events)
        self.assertEqual([row.to_dict() for row in first.records], [row.to_dict() for row in second.records])
        self.assertEqual(first.summary_dict()["integrity"], {
            "logical_entity_id_collisions": 0,
            "logical_entity_observation_id_collisions": 0,
            "migration_id_collisions": 0,
        })

    def test_runtime_path_literals_are_conversation_scoped_and_qualified(self):
        result = LogicalEntityConstructor().construct(
            [
                event("technical-event:one", "runtime_path", conversation_id="conv-one", mnt_data_paths=["/mnt/data/result.csv"]),
                event("technical-event:two", "runtime_path", conversation_id="conv-two", mnt_data_paths=["/mnt/data/result.csv"]),
                event("technical-event:three", "runtime_path", conversation_id="conv-one", mnt_data_paths=["/mnt/data/frame_{i:02d}.png"]),
                event("technical-event:four", "runtime_path", conversation_id="conv-one", mnt_data_paths=["/mnt/data/broken...</td>"]),
            ]
        )
        paths = [row for row in result.records if row.entity_type == "runtime_path_literal"]
        self.assertEqual(len(paths), 4)
        result_paths = [row for row in paths if row.explicit_identifier == "/mnt/data/result.csv"]
        self.assertEqual(len(result_paths), 2)
        self.assertEqual({row.scope_value for row in result_paths}, {"conv-one", "conv-two"})
        self.assertEqual({row.entity_subtype for row in paths}, {"file_like", "template", "malformed_or_truncated"})

    def test_legacy_false_textdoc_is_preserved_in_migration_without_new_entity(self):
        event_id = "technical-event:nonidentifier"
        result = LogicalEntityConstructor().construct(
            [event(event_id, "textdoc_reference", textdoc_references=[], relevant_metadata={"textdoc_non_identifier_values": [{"value": "code/json"}]})],
            previous_entities=[
                {
                    "logical_entity_id": "logical-entity:legacy",
                    "entity_type": "textdoc",
                    "identifier_kind": "textdoc_reference",
                    "explicit_identifier": "code/json",
                    "source_event_ids": [event_id],
                }
            ],
        )
        self.assertEqual(result.records, [])
        self.assertEqual(result.unmaterialized_events[0]["reason"], "textdoc_non_identifier_metadata_only")
        self.assertEqual(len(result.migration_records), 1)
        migration = result.migration_records[0]
        self.assertEqual(migration.status, "deprecated_non_identifier_textdoc")
        self.assertIsNone(migration.new_logical_entity_id)

    def test_gizmo_project_namespace_and_asset_pointer_namespaces_stay_separate(self):
        result = LogicalEntityConstructor().construct(
            [
                event("technical-event:project", "gizmo_context", gizmo_id="g-p-project"),
                event("technical-event:gizmo", "gizmo_context", gizmo_id="g-ordinary"),
                event("technical-event:sediment", "asset_reference", asset_pointers=["sediment://same-suffix"]),
                event("technical-event:service", "asset_reference", asset_pointers=["file-service://same-suffix"]),
            ]
        )
        self.assertEqual(
            {row.entity_type for row in result.records},
            {"project_context_reference", "gizmo_context_reference", "remote_asset_pointer"},
        )
        pointers = [row for row in result.records if row.entity_type == "remote_asset_pointer"]
        self.assertEqual(len(pointers), 2)
        self.assertEqual({row.entity_subtype for row in pointers}, {"sediment", "file_service"})
