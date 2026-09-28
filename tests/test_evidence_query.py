from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from openai_export_obsidian.evidence_query import CONVERSATION_QUERY_FIELDS, EvidenceQueryError, projected_conversations


ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_PACK = ROOT / "contracts/parser-pack/openai-obsidian-pack-v1.4/synthetic-example/pack"
CLI_ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}


class EvidenceConversationQueryTests(unittest.TestCase):
    def copy_pack(self, root: Path) -> Path:
        pack = root / "pack"
        shutil.copytree(SYNTHETIC_PACK, pack)
        return pack

    def test_closed_projection_is_sorted_private_free_and_deterministic(self) -> None:
        with TemporaryDirectory() as temporary:
            pack = self.copy_pack(Path(temporary))
            before = tree_hash(pack)
            command = [sys.executable, "-m", "openai_export_obsidian", "query", "conversations", "--pack", str(pack)]
            first = subprocess.run(command, env=CLI_ENV, check=False, text=True, capture_output=True)
            second = subprocess.run(command, env=CLI_ENV, check=False, text=True, capture_output=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(first.stdout, second.stdout)
            rows = [json.loads(line) for line in first.stdout.splitlines()]
            self.assertEqual([row["conversation_id"] for row in rows], sorted(row["conversation_id"] for row in rows))
            self.assertTrue(all(tuple(row) == tuple(sorted(CONVERSATION_QUERY_FIELDS)) for row in rows))
            self.assertTrue(all(set(row) == set(CONVERSATION_QUERY_FIELDS) for row in rows))
            forbidden = {"title", "chat_url", "url", "snippet", "raw_metadata", "text", "source_archive_path"}
            self.assertTrue(all(not forbidden.intersection(row) for row in rows))
            self.assertEqual(tree_hash(pack), before)

    def test_month_and_needs_review_filters(self) -> None:
        rows = projected_conversations(SYNTHETIC_PACK, months=["2000-01"], needs_review=True)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["needs_review"] for row in rows))
        self.assertEqual(projected_conversations(SYNTHETIC_PACK, months=["2001-01"]), [])
        with self.assertRaises(EvidenceQueryError):
            projected_conversations(SYNTHETIC_PACK, months=["2000-13"])

    def test_validation_failure_and_invalid_month_emit_no_stdout(self) -> None:
        with TemporaryDirectory() as temporary:
            pack = self.copy_pack(Path(temporary))
            conversations = pack / "90_Evidence/conversations.jsonl"
            conversations.write_text("{invalid\n", encoding="utf-8")
            for extra in (["--month", "2000-13"], []):
                completed = subprocess.run(
                    [sys.executable, "-m", "openai_export_obsidian", "query", "conversations", "--pack", str(pack), *extra],
                    env=CLI_ENV,
                    check=False,
                    text=True,
                    capture_output=True,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertEqual(completed.stdout, "")
                self.assertIn("ERROR:", completed.stderr)

    def test_not_emitted_locator_produces_null_note_paths(self) -> None:
        with TemporaryDirectory() as temporary:
            pack = self.copy_pack(Path(temporary))
            manifest_path = pack / "90_Evidence/pack_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["navigation"]["conversation_note_locator"] = {"state": "not_emitted", "relative_path": None, "record_count": 0}
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            (pack / "40_Views/conversation_note_locators.jsonl").unlink()
            rows = projected_conversations(pack)
            self.assertTrue(all(row["note_path"] is None for row in rows))

    def test_singular_query_still_operates(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "openai_export_obsidian", "query", "conversation", "--pack", str(SYNTHETIC_PACK), "contract-conversation-alpha"],
            env=CLI_ENV,
            check=False,
            text=True,
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Conversation", completed.stdout)


def tree_hash(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


if __name__ == "__main__":
    unittest.main()
