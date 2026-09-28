from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from openai_export_obsidian.context_resource_usage import (
    ContextResourceUsageConstructor,
    emit_context_resource_usage_evidence,
)


GIZMO_ID = "g-usage-test"
PROFILE_ID = "gizmo-context-profile:usage-test"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


class ContextResourceUsageTests(unittest.TestCase):
    def build_evidence(self, evidence: Path) -> Path:
        evidence.mkdir(parents=True, exist_ok=True)
        profile = {
            "context_profile_id": PROFILE_ID,
            "context_type": "gizmo",
            "explicit_context_id": GIZMO_ID,
        }
        write_jsonl(evidence / "gizmo_context_profiles.jsonl", [profile])
        write_jsonl(evidence / "project_context_profiles.jsonl", [])
        write_jsonl(
            evidence / "context_profile_observations.jsonl",
            [
                {
                    "context_profile_observation_id": "context-profile-observation:conv-one",
                    "context_profile_id": PROFILE_ID,
                    "conversation_id": "conv-one",
                    "source_export": "primary_2026_json",
                    "observation_kind": "conversation_context_projection",
                    "proof_path": "conversations.jsonl[conversation_id=conv-one].gpts[0]",
                    "evidence_set_ids": [],
                    "status": "observed",
                },
                {
                    "context_profile_observation_id": "context-profile-observation:conv-two",
                    "context_profile_id": PROFILE_ID,
                    "conversation_id": "conv-two",
                    "source_export": "primary_2026_json",
                    "observation_kind": "conversation_context_projection",
                    "proof_path": "conversations.jsonl[conversation_id=conv-two].gpts[0]",
                    "evidence_set_ids": [],
                    "status": "observed",
                },
            ],
        )
        source_rows = [
            {
                "source_ref_id": "source-a-one",
                "conversation_id": "conv-one",
                "message_id": "message-one",
                "source_kind": "cited_file",
                "source": "my_files",
                "file_id": "file-a",
                "title": "alpha.md",
                "proof_path": "mapping.node-one.message.metadata.citations[0]",
            },
            {
                "source_ref_id": "source-a-two",
                "conversation_id": "conv-two",
                "message_id": "message-two",
                "source_kind": "cited_file",
                "source": "my_files",
                "file_id": "file-a",
                "title": "alpha.md",
                "proof_path": "mapping.node-two.message.metadata.citations[0]",
            },
            {
                "source_ref_id": "source-b-one",
                "conversation_id": "conv-one",
                "message_id": "message-one",
                "source_kind": "cited_file",
                "source": "my_files",
                "file_id": "file-b",
                "title": "beta.md",
                "proof_path": "mapping.node-one.message.metadata.citations[1]",
            },
        ]
        write_jsonl(evidence / "message_sources.jsonl", source_rows)
        memberships = []
        for source in source_rows:
            memberships.append(
                {
                    "context_file_membership_id": f"context-file-membership:{source['source_ref_id']}",
                    "context_profile_id": PROFILE_ID,
                    "source_record_id": source["source_ref_id"],
                    "source_export": "primary_2026_json",
                    "relation_type": "mentioned_or_cited_in_context_conversation",
                    "conversation_ids": [source["conversation_id"]],
                    "message_ids": [source["message_id"]],
                    "proof_paths": [source["proof_path"]],
                    "evidence_set_ids": [],
                    "confidence": "explicit",
                    "status": "observed",
                    "non_promotion_reason": "citation is observed use only",
                }
            )
        write_jsonl(evidence / "context_file_memberships.jsonl", memberships)
        confirmation = evidence / "owner-confirmations.jsonl"
        write_jsonl(
            confirmation,
            [
                {
                    "owner_confirmation_id": "owner-confirmation:alpha",
                    "explicit_context_id": GIZMO_ID,
                    "resource_name": "alpha.md",
                    "confirmation_scope": "knowledge_base",
                    "proof_path": "30_Contexts/GPTs/GPT - Usage Test.md:10-12",
                    "statement": "confirmed by owner",
                    "status": "owner_confirmed",
                },
                {
                    "owner_confirmation_id": "owner-confirmation:owner-only",
                    "explicit_context_id": GIZMO_ID,
                    "resource_name": "not-seen.md",
                    "confirmation_scope": "knowledge_base",
                    "proof_path": "30_Contexts/GPTs/GPT - Usage Test.md:10-12",
                    "status": "owner_confirmed",
                },
            ],
        )
        return confirmation

    def test_graph_distinguishes_owner_confirmation_from_observed_usage(self):
        with TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            confirmations = self.build_evidence(evidence)
            result = ContextResourceUsageConstructor().construct(evidence, owner_confirmations_path=confirmations)

            self.assertEqual(len(result.owner_claims), 2)
            self.assertFalse(any("physical" in row.edge_type for row in result.edges))
            metrics = {row.resource_name: row for row in result.metrics}
            self.assertEqual(metrics["alpha.md"].forensic_status, "confirmed_by_owner_and_export_corroborated")
            self.assertEqual(metrics["alpha.md"].distinct_conversation_count, 2)
            self.assertEqual(metrics["alpha.md"].relative_conversation_frequency, 1.0)
            self.assertEqual(metrics["beta.md"].forensic_status, "observed_context_resource_usage")
            self.assertEqual(metrics["beta.md"].distinct_message_count, 1)
            self.assertEqual(metrics["not-seen.md"].forensic_status, "confirmed_by_owner_not_seen_in_export")
            self.assertEqual(metrics["not-seen.md"].reference_occurrence_count, 0)
            self.assertEqual(len(result.cooccurrences), 1)
            pair = result.cooccurrences[0]
            self.assertEqual(pair.same_conversation_ids, ["conv-one"])
            self.assertEqual(pair.same_message_scopes, ["conv-one::message-one"])
            self.assertEqual(result.summary_dict()["contract"]["physical_resolution_called"], False)

    def test_emitted_evidence_is_deterministic(self):
        with TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            confirmations = self.build_evidence(evidence)
            first = ContextResourceUsageConstructor().construct(evidence, owner_confirmations_path=confirmations)
            emit_context_resource_usage_evidence(evidence, first)
            first_bytes = {path.name: path.read_bytes() for path in evidence.glob("context_resource_*.json*")}
            second = ContextResourceUsageConstructor().construct(evidence, owner_confirmations_path=confirmations)
            emit_context_resource_usage_evidence(evidence, second)
            second_bytes = {path.name: path.read_bytes() for path in evidence.glob("context_resource_*.json*")}
            self.assertEqual(first_bytes, second_bytes)

    def test_owner_confirmation_requires_proof_and_does_not_parse_markdown(self):
        with TemporaryDirectory() as temporary:
            evidence = Path(temporary)
            confirmations = self.build_evidence(evidence)
            write_jsonl(
                confirmations,
                [{"explicit_context_id": GIZMO_ID, "resource_name": "alpha.md"}],
            )
            with self.assertRaisesRegex(ValueError, "missing proof_path"):
                ContextResourceUsageConstructor().construct(evidence, owner_confirmations_path=confirmations)


if __name__ == "__main__":
    unittest.main()
