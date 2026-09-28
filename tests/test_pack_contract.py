from __future__ import annotations

import json
import shutil
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from openai_export_obsidian.pack_contract import (
    _cross_file_errors,
    _synthetic_conversation,
    _write_synthetic_export,
    derived_record_hashes,
    evidence_fingerprint,
    export_pack_contract,
    validate_contract_bundle,
    validate_json_schema,
    validate_pack_layout,
)
from openai_export_obsidian.runner import SCHEMA_VERSION, build_conversation_note_refs, run_parse
from openai_export_obsidian.validation import generated_note_conversation_id, validate_pack
from openai_export_obsidian.markdown import yaml_scalar


class PackContractTests(unittest.TestCase):
    def generate(self, root: Path) -> Path:
        bundle = root / "contract"
        self.assertEqual(export_pack_contract(bundle), bundle)
        self.assertEqual(validate_contract_bundle(bundle), [])
        return bundle

    def test_public_export_is_deterministic_and_contains_no_personal_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self.generate(root / "one")
            second = self.generate(root / "two")
            self.assertEqual(tree_bytes(first), tree_bytes(second))
            manifest = json.loads((first / "contract-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["pack_schema"], SCHEMA_VERSION)
            self.assertEqual(manifest["synthetic_example_path"], "synthetic-example/pack")
            for relative, digest in manifest["file_hashes"].items():
                self.assertEqual(sha256(first / relative), digest)
            text = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in first.rglob("*") if path.is_file())
            self.assertNotIn("OpenAI-export.zip", text)
            self.assertNotIn("/Users/", text)
            self.assertNotIn("/private/", text)

    def test_synthetic_pack_is_emitted_by_real_parser_and_validates(self) -> None:
        with TemporaryDirectory() as tmp:
            bundle = self.generate(Path(tmp))
            synthetic_pack = bundle / "synthetic-example" / "pack"
            report = validate_pack(synthetic_pack)
            self.assertEqual(report["errors"], [])
            self.assertEqual(report["counts"]["conversations"], 2)
            self.assertEqual(report["counts"]["messages"], 4)

    def test_markdown_navigation_artifact_is_optional_and_valid(self) -> None:
        with TemporaryDirectory() as tmp:
            bundle = self.generate(Path(tmp))
            synthetic_pack = bundle / "synthetic-example" / "pack"
            self.assertTrue((synthetic_pack / "00_Home.md").is_file())
            self.assertEqual(validate_pack_layout(synthetic_pack), [])
            (synthetic_pack / "00_Home.md").unlink()
            self.assertEqual(validate_pack_layout(synthetic_pack), [])

    def test_navigation_bytes_do_not_change_evidence_or_record_hashes(self) -> None:
        with TemporaryDirectory() as tmp:
            bundle = self.generate(Path(tmp))
            synthetic_pack = bundle / "synthetic-example" / "pack"
            before_fingerprint = evidence_fingerprint(synthetic_pack)
            before_records = derived_record_hashes(synthetic_pack)
            (synthetic_pack / "00_Home.md").write_text("# Replacement navigation only\n", encoding="utf-8")
            self.assertEqual(evidence_fingerprint(synthetic_pack), before_fingerprint)
            self.assertEqual(derived_record_hashes(synthetic_pack), before_records)

    def test_conversation_note_locator_is_writer_owned_complete_and_evidence_neutral(self) -> None:
        with TemporaryDirectory() as tmp:
            bundle = self.generate(Path(tmp))
            pack = bundle / "synthetic-example" / "pack"
            evidence = pack / "90_Evidence"
            locator_path = pack / "40_Views" / "conversation_note_locators.jsonl"
            rows = read_jsonl(locator_path)
            manifest = json.loads((evidence / "pack_manifest.json").read_text(encoding="utf-8"))
            schema = json.loads((bundle / "conversation-note-locator.schema.json").read_text(encoding="utf-8"))

            self.assertEqual(
                manifest["navigation"]["conversation_note_locator"],
                {
                    "state": "emitted",
                    "relative_path": "40_Views/conversation_note_locators.jsonl",
                    "record_count": 2,
                },
            )
            self.assertEqual([row["conversation_id"] for row in rows], ["contract-conversation-alpha", "contract-conversation-beta"])
            self.assertEqual(
                {row["relative_note_path"] for row in rows},
                {
                    "10_Conversations/2000/01/2000-01-01 - Synthetic alpha.md",
                    "10_Conversations/2000/01/2000-01-01 - Synthetic beta.md",
                },
            )
            self.assertEqual(
                {row["relative_note_path"] for row in rows},
                {
                    path.relative_to(pack).as_posix()
                    for path in (pack / "10_Conversations").glob("**/*.md")
                    if path.is_file() and not path.is_symlink()
                },
            )
            for row in rows:
                self.assertEqual(validate_json_schema(row, schema), [])
                self.assertTrue((pack / row["relative_note_path"]).is_file())

            before_fingerprint = evidence_fingerprint(pack)
            before_records = derived_record_hashes(pack)
            changed = [dict(row) for row in rows]
            changed[0]["relative_note_path"] = "10_Conversations/2000/01/locator-only-mutation.md"
            write_jsonl(locator_path, changed)
            self.assertEqual(evidence_fingerprint(pack), before_fingerprint)
            self.assertEqual(derived_record_hashes(pack), before_records)

    def test_conversation_note_locator_rejects_invalid_relations_and_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = self.generate(root) / "synthetic-example" / "pack"

            def mutate_rows(pack: Path, mutate) -> None:
                path = pack / "40_Views" / "conversation_note_locators.jsonl"
                rows = [dict(row) for row in read_jsonl(path)]
                mutate(rows)
                write_jsonl(path, rows)

            def first_target(pack: Path) -> Path:
                relative = read_jsonl(pack / "40_Views" / "conversation_note_locators.jsonl")[0]["relative_note_path"]
                return pack / relative

            def mutate_frontmatter(pack: Path, mutate) -> None:
                target = first_target(pack)
                target.write_text(mutate(target.read_text(encoding="utf-8")), encoding="utf-8")

            def swap_paths(pack: Path) -> None:
                def swap(rows) -> None:
                    first, second = rows[0]["relative_note_path"], rows[1]["relative_note_path"]
                    rows[0] = dict(rows[0], relative_note_path=second)
                    rows[1] = dict(rows[1], relative_note_path=first)
                mutate_rows(pack, swap)

            cases = (
                ("duplicate-id", lambda pack: mutate_rows(pack, lambda rows: rows.__setitem__(1, dict(rows[1], conversation_id=rows[0]["conversation_id"]))), "conversation_id values are not unique"),
                ("duplicate-path", lambda pack: mutate_rows(pack, lambda rows: rows.__setitem__(1, dict(rows[1], relative_note_path=rows[0]["relative_note_path"]))), "relative_note_path values are not unique"),
                ("unknown-conversation", lambda pack: mutate_rows(pack, lambda rows: rows.__setitem__(0, dict(rows[0], conversation_id="unknown-conversation"))), "references unknown conversation_id"),
                ("orphan-locator", lambda pack: mutate_rows(pack, lambda rows: rows.__setitem__(0, dict(rows[0], relative_note_path="10_Conversations/2000/01/orphan.md"))), "target missing or not a regular file"),
                ("missing-locator", lambda pack: (pack / "40_Views" / "conversation_note_locators.jsonl").unlink(), "locator missing or not a regular file"),
                ("missing-target", lambda pack: (pack / read_jsonl(pack / "40_Views" / "conversation_note_locators.jsonl")[0]["relative_note_path"]).unlink(), "target missing or not a regular file"),
                ("directory-target", lambda pack: ((pack / read_jsonl(pack / "40_Views" / "conversation_note_locators.jsonl")[0]["relative_note_path"]).unlink(), (pack / read_jsonl(pack / "40_Views" / "conversation_note_locators.jsonl")[0]["relative_note_path"]).mkdir()), "target missing or not a regular file"),
                ("symlink-target", lambda pack: ((pack / read_jsonl(pack / "40_Views" / "conversation_note_locators.jsonl")[0]["relative_note_path"]).unlink(), (pack / read_jsonl(pack / "40_Views" / "conversation_note_locators.jsonl")[0]["relative_note_path"]).symlink_to("../../../../90_Evidence/pack_manifest.json")), "target missing or not a regular file"),
                ("absolute", lambda pack: mutate_rows(pack, lambda rows: rows.__setitem__(0, dict(rows[0], relative_note_path="/tmp/note.md"))), "must not be absolute"),
                ("traversal", lambda pack: mutate_rows(pack, lambda rows: rows.__setitem__(0, dict(rows[0], relative_note_path="10_Conversations/../note.md"))), "contains an invalid path segment"),
                ("backslash", lambda pack: mutate_rows(pack, lambda rows: rows.__setitem__(0, dict(rows[0], relative_note_path="10_Conversations\\note.md"))), "must use POSIX separators"),
                ("control", lambda pack: mutate_rows(pack, lambda rows: rows.__setitem__(0, dict(rows[0], relative_note_path="10_Conversations/\u0001note.md"))), "contains a control character"),
                ("unknown-field", lambda pack: mutate_rows(pack, lambda rows: rows.__setitem__(0, dict(rows[0], extra="rejected"))), "has unknown or missing fields"),
                ("swapped-pairs", swap_paths, "target pairwise binding does not match locator conversation_id"),
                ("frontmatter-mismatch", lambda pack: mutate_frontmatter(pack, lambda text: text.replace("conversation_id: contract-conversation-alpha", "conversation_id: contract-conversation-beta", 1)), "target pairwise binding does not match locator conversation_id"),
                ("frontmatter-absent", lambda pack: mutate_frontmatter(pack, lambda text: text.replace("---\n", "", 1)), "target frontmatter is absent"),
                ("frontmatter-unterminated", lambda pack: mutate_frontmatter(pack, lambda text: text.replace("\n---\n", "\n--- not-closing\n", 1)), "target frontmatter is unterminated"),
                ("frontmatter-missing-id", lambda pack: mutate_frontmatter(pack, lambda text: text.replace("conversation_id: contract-conversation-alpha\n", "", 1)), "target frontmatter conversation_id is absent"),
                ("frontmatter-duplicate-id", lambda pack: mutate_frontmatter(pack, lambda text: text.replace("conversation_id: contract-conversation-alpha\n", "conversation_id: contract-conversation-alpha\nconversation_id: contract-conversation-alpha\n", 1)), "target frontmatter conversation_id is duplicate or ambiguous"),
                ("frontmatter-malformed-id", lambda pack: mutate_frontmatter(pack, lambda text: text.replace("conversation_id: contract-conversation-alpha", "conversation_id: malformed value", 1)), "target frontmatter conversation_id is malformed"),
            )
            for name, mutate, expected_error in cases:
                with self.subTest(name=name):
                    candidate = root / name
                    shutil.copytree(original, candidate, symlinks=True)
                    mutate(candidate)
                    self.assertTrue(
                        any(expected_error in error for error in validate_pack(candidate)["errors"]),
                        validate_pack(candidate)["errors"],
                    )

    def test_locator_preserves_historical_special_note_names_and_collisions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            export = root / "synthetic-input.zip"
            pack = root / "pack"
            title = "C# [draft] | branch^note"
            conversations = [
                _synthetic_conversation("locator-special-alpha", title, "alpha"),
                _synthetic_conversation("locator-special-beta", title, "beta"),
            ]
            with zipfile.ZipFile(export, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("conversations-000.json", json.dumps(conversations, ensure_ascii=False, sort_keys=True))
            expected = build_conversation_note_refs(
                [
                    SimpleNamespace(conversation_id="locator-special-alpha", title=title, create_time=946684800.0),
                    SimpleNamespace(conversation_id="locator-special-beta", title=title, create_time=946684800.0),
                ]
            )
            run_parse(export, pack, emit_json=True, emit_markdown=True, manifest_generated_at="2000-01-01T00:00:00Z")
            rows = read_jsonl(pack / "40_Views" / "conversation_note_locators.jsonl")
            self.assertEqual(
                {row["conversation_id"]: row["relative_note_path"] for row in rows},
                {conversation_id: ref["path"] for conversation_id, ref in expected.items()},
            )
            self.assertEqual(
                [row["relative_note_path"] for row in rows],
                [
                    "10_Conversations/2000/01/2000-01-01 - C# [draft] | branch^note.md",
                    "10_Conversations/2000/01/2000-01-01 - C# [draft] | branch^note (2).md",
                ],
            )
            self.assertTrue(all((pack / row["relative_note_path"]).is_file() for row in rows))
            self.assertEqual(validate_pack(pack)["errors"], [])

    def test_locator_decodes_renderer_quoted_conversation_ids(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            export = root / "synthetic-input.zip"
            pack = root / "pack"
            conversation_id = '0synthetic-escaped-\\path"quote'
            with zipfile.ZipFile(export, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "conversations-000.json",
                    json.dumps([_synthetic_conversation(conversation_id, "Synthetic quoted identifier", "quoted")], sort_keys=True),
                )
            run_parse(export, pack, emit_json=True, emit_markdown=True, manifest_generated_at="2000-01-01T00:00:00Z")

            locator = read_jsonl(pack / "40_Views" / "conversation_note_locators.jsonl")[0]
            note = pack / locator["relative_note_path"]
            frontmatter_line = next(line for line in note.read_text(encoding="utf-8").splitlines() if line.startswith("conversation_id:"))
            self.assertEqual(frontmatter_line, f"conversation_id: {yaml_scalar(conversation_id)}")
            self.assertTrue(frontmatter_line.startswith('conversation_id: "0'))
            self.assertEqual(locator["conversation_id"], conversation_id)
            self.assertEqual(generated_note_conversation_id(note), (conversation_id, None))
            self.assertEqual(validate_pack(pack)["errors"], [])

            note.write_text(
                note.read_text(encoding="utf-8").replace(
                    yaml_scalar(conversation_id),
                    yaml_scalar('0synthetic-different-id'),
                    1,
                ),
                encoding="utf-8",
            )
            self.assertTrue(any("pairwise binding does not match" in error for error in validate_pack(pack)["errors"]))

            for malformed in ('"0synthetic-unterminated', '"0synthetic" trailing', '"0synthetic\\q"'):
                note.write_text(
                    note.read_text(encoding="utf-8").replace(
                        yaml_scalar('0synthetic-different-id'),
                        malformed,
                        1,
                    ),
                    encoding="utf-8",
                )
                self.assertEqual(generated_note_conversation_id(note), (None, "frontmatter conversation_id is malformed"))
                note.write_text(
                    note.read_text(encoding="utf-8").replace(malformed, yaml_scalar('0synthetic-different-id'), 1),
                    encoding="utf-8",
                )

    def test_markdown_disabled_pack_declares_locator_unavailable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            export = root / "synthetic-input.zip"
            pack = root / "pack"
            _write_synthetic_export(export)
            run_parse(
                export,
                pack,
                emit_json=True,
                emit_markdown=False,
                manifest_generated_at="2000-01-01T00:00:00Z",
            )
            manifest = json.loads((pack / "90_Evidence" / "pack_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(
                manifest["navigation"]["conversation_note_locator"],
                {"state": "not_emitted", "relative_path": None, "record_count": 0},
            )
            self.assertFalse((pack / "40_Views" / "conversation_note_locators.jsonl").exists())
            self.assertEqual(validate_pack(pack)["errors"], [])

    def test_synthetic_markdown_hard_breaks_are_exactly_the_declared_writer_output(self) -> None:
        expected_hard_breaks = {
            "synthetic-example/pack/10_Conversations/2000/01/2000-01-01 - Synthetic alpha.md": [
                (25, "> Created: 2000-01-01 00:00:00 UTC  "),
                (26, "> Last updated: 2000-01-01 00:00:01 UTC  "),
            ],
            "synthetic-example/pack/10_Conversations/2000/01/2000-01-01 - Synthetic beta.md": [
                (25, "> Created: 2000-01-01 00:00:00 UTC  "),
                (26, "> Last updated: 2000-01-01 00:00:01 UTC  "),
            ],
        }
        with TemporaryDirectory() as tmp:
            bundle = self.generate(Path(tmp))
            manifest = json.loads((bundle / "contract-manifest.json").read_text(encoding="utf-8"))
            declared_notes = sorted(
                relative
                for relative in manifest["file_hashes"]
                if relative.startswith("synthetic-example/pack/10_Conversations/") and relative.endswith(".md")
            )
            self.assertEqual(declared_notes, sorted(expected_hard_breaks))
            for relative in declared_notes:
                observed_hard_breaks: list[tuple[int, str]] = []
                for line_number, line in enumerate((bundle / relative).read_text(encoding="utf-8").splitlines(), start=1):
                    without_trailing = line.rstrip(" \t")
                    trailing = line[len(without_trailing):]
                    if trailing:
                        self.assertEqual(trailing, "  ", f"unexpected trailing whitespace in {relative}:{line_number}")
                        self.assertTrue(without_trailing, f"blank line with trailing whitespace in {relative}:{line_number}")
                        observed_hard_breaks.append((line_number, line))
                self.assertEqual([line for _, line in observed_hard_breaks], [line for _, line in expected_hard_breaks[relative]])

    def test_closed_layout_rejects_unknown_root_entries_directories_symlinks_and_evidence_entries(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self.generate(root)
            original_pack = bundle / "synthetic-example" / "pack"
            cases = (
                ("unknown-root", lambda pack: (pack / "00_Home-copy.md").write_text("unexpected\n", encoding="utf-8"), "unknown root entry: 00_Home-copy.md"),
                ("home-directory", lambda pack: ((pack / "00_Home.md").unlink(), (pack / "00_Home.md").mkdir()), "optional root navigation artifact must be a regular file: 00_Home.md"),
                ("home-symlink", lambda pack: ((pack / "00_Home.md").unlink(), (pack / "00_Home.md").symlink_to("90_Evidence/pack_manifest.json")), "root entry must not be a symlink: 00_Home.md"),
                ("unexpected-evidence", lambda pack: (pack / "90_Evidence" / "unexpected.json").write_text("{}\n", encoding="utf-8"), "unexpected 90_Evidence entry: unexpected.json"),
            )
            for name, mutate, expected_error in cases:
                with self.subTest(name=name):
                    candidate = root / name
                    shutil.copytree(original_pack, candidate)
                    mutate(candidate)
                    self.assertIn(expected_error, validate_pack_layout(candidate))

    def test_exported_records_match_closed_schemas_and_serializer_shapes(self) -> None:
        with TemporaryDirectory() as tmp:
            bundle = self.generate(Path(tmp))
            evidence = bundle / "synthetic-example" / "pack" / "90_Evidence"
            for filename, schema_name in (
                ("pack_manifest.json", "pack-manifest.schema.json"),
                ("conversations.jsonl", "conversation-record.schema.json"),
                ("messages.jsonl", "message-record.schema.json"),
                ("asset_links.jsonl", "asset-link-record.schema.json"),
            ):
                schema = json.loads((bundle / schema_name).read_text(encoding="utf-8"))
                rows = [json.loads((evidence / filename).read_text(encoding="utf-8"))] if filename.endswith(".json") else read_jsonl(evidence / filename)
                for row in rows:
                    self.assertEqual(validate_json_schema(row, schema), [])
                    self.assertEqual(set(row), set(schema["properties"]))

    def test_schema_rejects_missing_or_new_envelope_fields_but_preserves_unknown_roles(self) -> None:
        with TemporaryDirectory() as tmp:
            bundle = self.generate(Path(tmp))
            evidence = bundle / "synthetic-example" / "pack" / "90_Evidence"
            schema = json.loads((bundle / "message-record.schema.json").read_text(encoding="utf-8"))
            row = read_jsonl(evidence / "messages.jsonl")[0]
            missing = dict(row)
            missing.pop("conversation_id")
            self.assertTrue(validate_json_schema(missing, schema))
            changed = dict(row, undocumented_field=True)
            self.assertTrue(validate_json_schema(changed, schema))
            unusual = dict(row, author_role="future-role", content_type="future-content")
            self.assertEqual(validate_json_schema(unusual, schema), [])

    def test_cross_file_rules_reject_orphans_duplicates_and_count_drift(self) -> None:
        with TemporaryDirectory() as tmp:
            bundle = self.generate(Path(tmp))
            evidence = bundle / "synthetic-example" / "pack" / "90_Evidence"
            messages_path = evidence / "messages.jsonl"
            rows = read_jsonl(messages_path)
            orphan = dict(rows[0], conversation_id="absent-conversation", message_id="orphan-message")
            extra = dict(rows[0], message_id="extra-message")
            messages_path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in [*rows, orphan, extra]) + "\n", encoding="utf-8")
            errors = _cross_file_errors(evidence)
            self.assertTrue(any("unknown conversation" in error for error in errors))
            self.assertTrue(any("message_count mismatch" in error for error in errors))
            rows[1]["message_id"] = rows[0]["message_id"]
            write_jsonl(messages_path, rows)
            self.assertTrue(
                any(
                    "message IDs are not unique within conversation scope" in error
                    for error in _cross_file_errors(evidence)
                )
            )

    def test_message_identity_is_scoped_by_conversation_with_node_fallback(self) -> None:
        with TemporaryDirectory() as tmp:
            bundle = self.generate(Path(tmp))
            pack = bundle / "synthetic-example" / "pack"
            evidence = pack / "90_Evidence"
            messages = read_jsonl(evidence / "messages.jsonl")
            alpha = next(
                row
                for row in messages
                if row["conversation_id"] == "contract-conversation-alpha" and row["author_role"] == "assistant"
            )
            beta = next(
                row
                for row in messages
                if row["conversation_id"] == "contract-conversation-beta" and row["author_role"] == "assistant"
            )
            old_message_id = beta["message_id"]
            old_node_id = beta["node_id"]

            for path in sorted(evidence.glob("*.jsonl")):
                rows = read_jsonl(path)
                changed = False
                for row in rows:
                    if row.get("conversation_id") != "contract-conversation-beta":
                        continue
                    if row.get("message_id") == old_message_id:
                        row["message_id"] = alpha["message_id"]
                        changed = True
                    if row.get("node_id") == old_node_id:
                        row["node_id"] = alpha["node_id"]
                        changed = True
                if changed:
                    write_jsonl(path, rows)

            self.assertEqual(_cross_file_errors(evidence), [])
            self.assertEqual(validate_pack(pack)["errors"], [])

            messages = read_jsonl(evidence / "messages.jsonl")
            alpha_rows = [row for row in messages if row["conversation_id"] == "contract-conversation-alpha"]
            alpha_rows[1]["message_id"] = alpha_rows[0]["message_id"]
            write_jsonl(evidence / "messages.jsonl", [
                *alpha_rows,
                *[row for row in messages if row["conversation_id"] != "contract-conversation-alpha"],
            ])
            self.assertTrue(
                any(
                    "message IDs are not unique within conversation scope" in error
                    for error in _cross_file_errors(evidence)
                )
            )
            self.assertTrue(
                any(
                    "message IDs are not unique within conversation scope" in error
                    for error in validate_pack(pack)["errors"]
                )
            )

    def test_null_message_identity_requires_unique_conversation_scoped_node(self) -> None:
        with TemporaryDirectory() as tmp:
            bundle = self.generate(Path(tmp))
            pack = bundle / "synthetic-example" / "pack"
            evidence = pack / "90_Evidence"
            messages = read_jsonl(evidence / "messages.jsonl")
            alpha_rows = [row for row in messages if row["conversation_id"] == "contract-conversation-alpha"]
            alpha_rows[0]["message_id"] = None
            alpha_rows[1]["message_id"] = None
            alpha_rows[1]["node_id"] = alpha_rows[0]["node_id"]
            write_jsonl(evidence / "messages.jsonl", [
                *alpha_rows,
                *[row for row in messages if row["conversation_id"] != "contract-conversation-alpha"],
            ])

            self.assertTrue(
                any("node IDs are not unique within conversation scope" in error for error in _cross_file_errors(evidence))
            )
            self.assertTrue(
                any("node IDs are not unique within conversation scope" in error for error in validate_pack(pack)["errors"])
            )

    def test_dependent_rows_resolve_message_ids_within_their_conversation(self) -> None:
        with TemporaryDirectory() as tmp:
            bundle = self.generate(Path(tmp))
            pack = bundle / "synthetic-example" / "pack"
            evidence = pack / "90_Evidence"
            for name in (
                "asset_links.jsonl",
                "message_sources.jsonl",
                "tool_events.jsonl",
                "textdocs.jsonl",
                "context_links.jsonl",
                "citation_links.jsonl",
            ):
                path = evidence / name
                rows = read_jsonl(path)
                row = dict(rows[0]) if rows else {}
                row["conversation_id"] = "contract-conversation-beta"
                row["message_id"] = "alpha-message-user"
                rows.append(row)
                write_jsonl(path, rows)

            contract_errors = _cross_file_errors(evidence)
            validation_errors = validate_pack(pack)["errors"]
            for name in (
                "asset_links.jsonl",
                "message_sources.jsonl",
                "tool_events.jsonl",
                "textdocs.jsonl",
                "context_links.jsonl",
                "citation_links.jsonl",
            ):
                expected = (
                    f"{name}:{len(read_jsonl(evidence / name))} "
                    "references unknown conversation-scoped message identity"
                )
                self.assertIn(expected, contract_errors)
                self.assertIn(expected, validation_errors)

    def test_existing_different_bundle_is_never_overwritten(self) -> None:
        with TemporaryDirectory() as tmp:
            bundle = self.generate(Path(tmp))
            (bundle / "README.md").write_text("different\n", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                export_pack_contract(bundle)


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}
