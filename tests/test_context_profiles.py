from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from openai_export_obsidian.context_profiles import (
    ContextProfileConstructor,
    emit_context_profile_evidence,
    emit_context_profile_obsidian_projection,
)


GIZMO_ID = "g-test-gizmo"
PROJECT_ID = "g-p-test-project"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def entity(identifier: str, entity_type: str, entity_id: str) -> dict:
    return {"logical_entity_id": entity_id, "entity_type": entity_type, "explicit_identifier": identifier}


def observation(entity_id: str, entity_type: str, identifier: str, observation_id: str, source: str, conversation: str, message: str) -> dict:
    return {
        "logical_entity_observation_id": observation_id,
        "logical_entity_id": entity_id,
        "entity_type": entity_type,
        "explicit_identifier": identifier,
        "source_export": source,
        "source_archive_path": f"{source}::conversations.json",
        "proof_path": f"mapping.node-{message}.message.metadata.gizmo_id",
        "conversation_id": conversation,
        "message_id": message,
        "node_id": f"node-{message}",
    }


class ContextProfileTests(unittest.TestCase):
    def build_pack(self, evidence: Path) -> None:
        write_jsonl(
            evidence / "conversations.jsonl",
            [
                {
                    "conversation_id": "conv-gizmo",
                    "source_archive_path": "primary::conversations-001.json",
                    "gpts": [f"[[GPT - {GIZMO_ID}]]"],
                    "space_projects": [],
                },
                {
                    "conversation_id": "conv-project",
                    "source_archive_path": "primary::conversations-002.json",
                    "gpts": [],
                    "space_projects": [f"[[Project - {PROJECT_ID}]]"],
                },
            ],
        )
        write_jsonl(
            evidence / "logical_entities.jsonl",
            [
                entity(GIZMO_ID, "gizmo_context_reference", "logical-entity:gizmo"),
                entity(PROJECT_ID, "project_context_reference", "logical-entity:project"),
            ],
        )
        write_jsonl(
            evidence / "logical_entity_observations.jsonl",
            [
                observation("logical-entity:gizmo", "gizmo_context_reference", GIZMO_ID, "obs-gizmo-primary", "primary_2026_json", "conv-gizmo", "msg-gizmo-primary"),
                observation("logical-entity:gizmo", "gizmo_context_reference", GIZMO_ID, "obs-gizmo-historical", "historical_embedded_json", "conv-gizmo", "msg-gizmo-historical"),
                observation("logical-entity:project", "project_context_reference", PROJECT_ID, "obs-project-primary", "primary_2026_json", "conv-project", "msg-project-primary"),
            ],
        )
        write_jsonl(
            evidence / "technical_events.jsonl",
            [
                {
                    "technical_event_id": "event-gizmo-primary",
                    "gizmo_id": GIZMO_ID,
                    "source_export": "primary_2026_json",
                    "source_archive_path": "primary::conversations-001.json",
                    "proof_path": "mapping.node-gizmo.message.metadata",
                    "conversation_id": "conv-gizmo",
                    "message_id": "msg-gizmo-primary",
                    "node_id": "node-gizmo",
                    "model_slug": "gpt-5",
                    "default_model_slug": "gpt-5",
                    "recipient": "python",
                    "relevant_metadata": {
                        "gizmo_instructions": "Only use explicit evidence.",
                        "gizmo_name": "Evidence Helper",
                    },
                },
                {
                    "technical_event_id": "event-gizmo-historical",
                    "gizmo_id": GIZMO_ID,
                    "source_export": "historical_embedded_json",
                    "source_archive_path": "history::conversations.json",
                    "proof_path": "mapping.node-historical.message.metadata",
                    "conversation_id": "conv-gizmo",
                    "message_id": "msg-gizmo-historical",
                    "node_id": "node-historical",
                    "model_slug": "gpt-4o",
                    "default_model_slug": "gpt-4o",
                    "recipient": "canmore.create_textdoc",
                    "relevant_metadata": {},
                },
            ],
        )
        write_jsonl(
            evidence / "candidate_edges.jsonl",
            [
                {
                    "candidate_edge_id": "candidate-edge:gizmo",
                    "edge_type": "gizmo_context_matches_conversation_gpt",
                    "source_entity_id": "logical-entity:gizmo",
                    "target_id": "gpt:conv-gizmo",
                    "source_evidence_set_id": "candidate-evidence-set:gizmo-source",
                    "target_evidence_set_id": "candidate-evidence-set:gizmo-target",
                    "confidence": "high",
                },
                {
                    "candidate_edge_id": "candidate-edge:project",
                    "edge_type": "project_context_matches_space_project",
                    "source_entity_id": "logical-entity:project",
                    "target_id": "space_project:conv-project",
                    "source_evidence_set_id": "candidate-evidence-set:project-source",
                    "target_evidence_set_id": "candidate-evidence-set:project-target",
                    "confidence": "high",
                },
            ],
        )
        write_jsonl(
            evidence / "candidate_edge_evidence_sets.jsonl",
            [
                {"evidence_set_id": value}
                for value in (
                    "candidate-evidence-set:gizmo-source",
                    "candidate-evidence-set:gizmo-target",
                    "candidate-evidence-set:project-source",
                    "candidate-evidence-set:project-target",
                )
            ],
        )
        write_jsonl(
            evidence / "messages.jsonl",
            [
                {
                    "conversation_id": "conv-gizmo",
                    "message_id": "msg-gizmo-metadata",
                    "node_id": "node-gizmo-metadata",
                    "raw_metadata": {"instructions": "This is not yet scoped to the GPT."},
                }
            ],
        )
        write_jsonl(
            evidence / "message_sources.jsonl",
            [
                {
                    "source_ref_id": "source-upload",
                    "conversation_id": "conv-gizmo",
                    "message_id": "msg-upload",
                    "proof_path": "mapping.node-upload.message.metadata.attachments[0]",
                    "source_kind": "uploaded_file",
                    "file_id": "file-upload",
                    "title": "upload.pdf",
                    "payload_status": "exported",
                },
                {
                    "source_ref_id": "source-cited",
                    "conversation_id": "conv-gizmo",
                    "message_id": "msg-cited",
                    "proof_path": "mapping.node-cited.message.metadata.citations[0]",
                    "source_kind": "cited_file",
                    "file_id": "file-cited",
                    "title": "citation.pdf",
                    "payload_status": "exported",
                },
                {
                    "source_ref_id": "source-candidate",
                    "conversation_id": "conv-gizmo",
                    "message_id": "msg-candidate",
                    "proof_path": "mapping.node-candidate.message.metadata.citations[0]",
                    "source_kind": "cited_file",
                    "file_id": "file-candidate",
                    "title": "possible-knowledge.pdf",
                    "payload_status": "exported",
                    "candidate_role": "gpt_knowledge_reference",
                    "candidate_confidence": "heuristic",
                },
                {
                    "source_ref_id": "source-missing",
                    "conversation_id": "conv-gizmo",
                    "message_id": "msg-missing",
                    "proof_path": "mapping.node-missing.message.metadata.attachments[0]",
                    "source_kind": "uploaded_file",
                    "file_id": "file-missing",
                    "title": "missing.pdf",
                    "payload_status": "not_exported",
                },
            ],
        )
        write_jsonl(
            evidence / "textdocs.jsonl",
            [
                {
                    "textdoc_ref_id": "textdoc-project-instructions",
                    "conversation_id": "conv-project",
                    "message_id": "msg-project-textdoc",
                    "node_id": "node-project-textdoc",
                    "proof_path": "mapping.node-project.message.metadata.canvas.user_created_textdocs[0]",
                    "title": "Project Instructions",
                }
            ],
        )
        write_jsonl(evidence / "context_links.jsonl", [])
        for name in ("historical_messages.jsonl", "references.jsonl", "physical_resolutions.jsonl", "runtime_artifacts.jsonl"):
            write_jsonl(evidence / name, [])

    def test_profiles_preserve_source_separation_and_non_promoted_memberships(self):
        with TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            self.build_pack(evidence)
            result = ContextProfileConstructor().construct(evidence)

            self.assertEqual(len(result.gizmo_profiles), 1)
            self.assertEqual(len(result.project_profiles), 1)
            gizmo = result.gizmo_profiles[0]
            self.assertEqual(gizmo.explicit_context_id, GIZMO_ID)
            self.assertEqual(gizmo.source_presence_status, "present_in_primary_and_historical")
            self.assertEqual(gizmo.explicit_names_or_titles, ["Evidence Helper"])
            self.assertEqual(gizmo.observed_models, ["gpt-4o", "gpt-5"])
            self.assertIn("python", gizmo.observed_tools)
            self.assertTrue(gizmo.explicit_instruction_ids)
            self.assertTrue(gizmo.candidate_instruction_ids)
            self.assertTrue(any(row.status == "candidate_not_promoted" for row in result.observations))

            relations = {row.relation_type for row in result.memberships}
            self.assertEqual(
                relations,
                {
                    "observed_in_context_conversation",
                    "mentioned_or_cited_in_context_conversation",
                    "candidate_context_membership",
                    "unresolved",
                },
            )
            self.assertFalse(any(row.relation_type.startswith("explicit_context_") for row in result.memberships))
            candidate = next(row for row in result.memberships if row.relation_type == "candidate_context_membership")
            self.assertEqual(candidate.status, "candidate_not_promoted")
            self.assertTrue(candidate.non_promotion_reason)
            self.assertEqual(result.summary_dict()["contract"], {
                "raw_zip_reparsed": False,
                "candidate_edges_promoted_automatically": False,
                "physical_resolution_called": False,
                "historical_messages_merged": False,
                "context_file_membership_inferred_from_conversation_only": False,
            })

    def test_emitted_jsonl_is_deterministic(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.build_pack(root)
            first = ContextProfileConstructor().construct(root)
            emit_context_profile_evidence(root, first)
            first_bytes = {path.name: path.read_bytes() for path in root.glob("context_*.json*")} | {
                path.name: path.read_bytes() for path in root.glob("*context_profiles.jsonl")
            }
            second = ContextProfileConstructor().construct(root)
            emit_context_profile_evidence(root, second)
            second_bytes = {path.name: path.read_bytes() for path in root.glob("context_*.json*")} | {
                path.name: path.read_bytes() for path in root.glob("*context_profiles.jsonl")
            }
            self.assertEqual(first_bytes, second_bytes)

    def test_optional_obsidian_projection_does_not_replace_existing_hubs(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.build_pack(root)
            result = ContextProfileConstructor().construct(root)
            (root / "30_Contexts" / "GPTs").mkdir(parents=True)
            existing_hub = root / "30_Contexts" / "GPTs" / f"GPT - {GIZMO_ID}.md"
            existing_hub.write_text("manual hub must remain", encoding="utf-8")
            self.assertEqual(emit_context_profile_obsidian_projection(root, result), 2)
            self.assertEqual(existing_hub.read_text(encoding="utf-8"), "manual hub must remain")
            projection = next((root / "30_Contexts" / "Forensic Profiles" / "GPTs").glob("*.md"))
            text = projection.read_text(encoding="utf-8")
            self.assertIn("## Instructions explicitement observées", text)
            self.assertIn("## Instructions candidates", text)
            self.assertIn("## Références de fichiers par relation", text)
            self.assertIn("## Conversations associées", text)


if __name__ == "__main__":
    unittest.main()
