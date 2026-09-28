from __future__ import annotations

import json
import io
import os
import shutil
import stat
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from openai_export_obsidian.ds_store_hygiene import (
    DS_STORE_DIAGNOSTIC,
    QUARANTINE_CONFIRMATION,
    DsStoreHygieneError,
    quarantine_ds_store,
    remove_residual_ds_store_from_new_output,
    scan_ds_store_entries,
)
from openai_export_obsidian.pack_contract import (
    _synthetic_conversation,
    export_pack_contract,
    validate_pack_layout,
)
from openai_export_obsidian.runner import run_parse
from openai_export_obsidian.validation import validate_pack
from openai_export_obsidian.cli import main as cli_main


class DsStoreHygieneTests(unittest.TestCase):
    def test_new_output_sweep_removes_only_regular_exact_ds_store(self) -> None:
        with TemporaryDirectory() as tmp:
            pack = Path(tmp) / "pack"
            (pack / "10_Conversations" / "nested" / "deep").mkdir(parents=True)
            (pack / "20_Files").mkdir()
            (pack / ".DS_Store").write_bytes(b"root")
            (pack / "10_Conversations" / ".DS_Store").write_bytes(b"nested")
            (pack / "10_Conversations" / "nested" / "deep" / ".DS_Store").write_bytes(b"deep")
            retained = pack / "20_Files" / ".DS_Store.backup"
            retained.write_bytes(b"not finder metadata")

            self.assertEqual(remove_residual_ds_store_from_new_output(pack), 3)
            self.assertEqual(scan_ds_store_entries(pack).categories(), {
                "regular_files": 0,
                "directories": 0,
                "symlinks": 0,
                "special_entries": 0,
            })
            self.assertEqual(retained.read_bytes(), b"not finder metadata")

    def test_non_regular_ds_store_never_gets_ignored_or_removed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory_pack = root / "directory"
            (directory_pack / ".DS_Store").mkdir(parents=True)
            with self.assertRaises(DsStoreHygieneError):
                remove_residual_ds_store_from_new_output(directory_pack)

            symlink_pack = root / "symlink"
            symlink_pack.mkdir()
            (symlink_pack / ".DS_Store").symlink_to("missing-target")
            scan = scan_ds_store_entries(symlink_pack)
            self.assertEqual(scan.categories()["symlinks"], 1)
            with self.assertRaises(DsStoreHygieneError):
                remove_residual_ds_store_from_new_output(symlink_pack)

            if hasattr(os, "mkfifo"):
                special_pack = root / "special"
                special_pack.mkdir()
                os.mkfifo(special_pack / ".DS_Store")
                self.assertEqual(scan_ds_store_entries(special_pack).categories()["special_entries"], 1)
                with self.assertRaises(DsStoreHygieneError):
                    remove_residual_ds_store_from_new_output(special_pack)

    def test_validation_reports_exact_regular_contamination_and_closed_layout_stays_strict(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = export_pack_contract(root / "bundle")
            original = bundle / "synthetic-example" / "pack"
            candidate = root / "candidate"
            shutil.copytree(original, candidate)
            (candidate / ".DS_Store").write_bytes(b"finder")
            errors = validate_pack(candidate)["errors"]
            self.assertIn(f"{DS_STORE_DIAGNOSTIC}:regular_files=1", errors)
            self.assertIn("unknown root entry: .DS_Store", errors)

            (candidate / ".DS_Store").unlink()
            (candidate / "10_Conversations" / ".DS_Store").mkdir()
            layout_errors = validate_pack_layout(candidate)
            self.assertIn(f"{DS_STORE_DIAGNOSTIC}:directories=1", layout_errors)

            (candidate / "10_Conversations" / ".DS_Store").rmdir()
            (candidate / ".DS_Store.backup").write_bytes(b"unexpected")
            (candidate / ".hidden").write_bytes(b"unexpected")
            layout_errors = validate_pack_layout(candidate)
            self.assertIn("unknown root entry: .DS_Store.backup", layout_errors)
            self.assertIn("unknown root entry: .hidden", layout_errors)

    def test_archive_payload_named_ds_store_is_not_materialized(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_path = root / "synthetic-input.zip"
            pack = root / "pack"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "conversations-000.json",
                    json.dumps([_synthetic_conversation("synthetic-ds-store", "Synthetic", "ds")], sort_keys=True),
                )
                archive.writestr("personal/files/.DS_Store", b"finder source bytes")
            run_parse(
                archive_path,
                pack,
                copy_assets=True,
                copy_unlinked_assets=True,
                emit_json=True,
                emit_markdown=True,
                manifest_generated_at="2000-01-01T00:00:00Z",
            )
            self.assertFalse(any(path.name == ".DS_Store" for path in pack.rglob("*")))

    def test_controlled_quarantine_is_metadata_only_sanitized_and_preserves_official_bytes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "pack"
            quarantine = root / "quarantine"
            official = pack / "90_Evidence" / "official.json"
            nested = pack / "10_Conversations" / "nested"
            nested.mkdir(parents=True)
            official.parent.mkdir(parents=True, exist_ok=True)
            official.write_bytes(b'{"official":true}\n')
            (pack / ".DS_Store").write_bytes(b"root finder")
            (nested / ".DS_Store").write_bytes(b"nested finder")
            set_private_modes(pack)
            (pack / ".DS_Store").chmod(0o644)
            (nested / ".DS_Store").chmod(0o644)
            before = tree_bytes_excluding_ds_store(pack)
            target_identities = {
                path.name + str(index): (path.stat().st_dev, path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns)
                for index, path in enumerate(sorted(pack.rglob(".DS_Store")), start=1)
            }

            dry = quarantine_ds_store(pack, quarantine, confirmation=None, dry_run=True)
            self.assertEqual(dry["status"], "dry_run")
            self.assertTrue(dry["non_target_entries_private"])
            self.assertEqual(dry["target_files_with_group_other_bits"], 2)
            self.assertEqual(dry["official_regular_file_identity_comparison"], "not_performed")
            self.assertFalse(quarantine.exists())
            self.assertEqual(tree_bytes_excluding_ds_store(pack), before)
            with self.assertRaises(DsStoreHygieneError):
                quarantine_ds_store(pack, quarantine, confirmation=None)
            self.assertFalse(quarantine.exists())

            result = quarantine_ds_store(pack, quarantine, confirmation=QUARANTINE_CONFIRMATION)
            self.assertEqual(result["status"], "quarantined")
            self.assertEqual(result["moved_regular_files"], 2)
            self.assertTrue(result["official_regular_file_identity_unchanged"])
            self.assertTrue(result["moved_target_identity_preserved"])
            self.assertEqual(tree_bytes_excluding_ds_store(pack), before)
            self.assertEqual(scan_ds_store_entries(pack).categories()["regular_files"], 0)
            self.assertEqual(
                [path.name for path in sorted(quarantine.iterdir())],
                ["0001.DS_Store", "0002.DS_Store"],
            )
            self.assertEqual(
                sorted(path.read_bytes() for path in quarantine.iterdir()),
                [b"nested finder", b"root finder"],
            )
            quarantined_identities = {
                path.name + str(index): (path.stat().st_dev, path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns)
                for index, path in enumerate(sorted(quarantine.iterdir()), start=1)
            }
            self.assertEqual(sorted(target_identities.values()), sorted(quarantined_identities.values()))
            self.assertEqual(stat.S_IMODE(quarantine.stat().st_mode), 0o700)
            self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in quarantine.iterdir()))
            self.assertNotIn("10_Conversations", json.dumps(result, sort_keys=True))

    def test_non_target_file_or_directory_permissions_block_before_mutation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, make_unsafe in (
                ("file", lambda pack: (pack / "official.json").chmod(0o644)),
                ("directory", lambda pack: (pack / "nested").chmod(0o755)),
            ):
                with self.subTest(name=name):
                    pack = root / name / "pack"
                    quarantine = root / name / "quarantine"
                    (pack / "nested").mkdir(parents=True)
                    (pack / "official.json").write_bytes(b"official")
                    target = pack / ".DS_Store"
                    target.write_bytes(b"finder")
                    set_private_modes(pack)
                    target.chmod(0o644)
                    make_unsafe(pack)
                    with self.assertRaises(DsStoreHygieneError):
                        quarantine_ds_store(pack, quarantine, confirmation=QUARANTINE_CONFIRMATION)
                    self.assertTrue(target.exists())
                    self.assertFalse(quarantine.exists())

    def test_official_replacement_between_snapshots_fails_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "pack"
            quarantine = root / "quarantine"
            pack.mkdir()
            official = pack / "official.json"
            official.write_bytes(b"original")
            target = pack / ".DS_Store"
            target.write_bytes(b"finder")
            set_private_modes(pack)
            target.chmod(0o644)

            def replace_official() -> None:
                official.unlink()
                official.write_bytes(b"replacement")
                official.chmod(0o600)

            with self.assertRaises(DsStoreHygieneError):
                quarantine_ds_store(
                    pack,
                    quarantine,
                    confirmation=QUARANTINE_CONFIRMATION,
                    _before_mutation_hook=replace_official,
                )
            self.assertTrue(target.exists())
            self.assertFalse(quarantine.exists())

    def test_target_replacement_between_snapshots_stops_before_quarantine(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "pack"
            quarantine = root / "quarantine"
            pack.mkdir()
            official = pack / "official.json"
            official.write_bytes(b"official")
            target = pack / ".DS_Store"
            target.write_bytes(b"finder-original")
            set_private_modes(pack)
            target.chmod(0o644)
            official_before = official.read_bytes()

            def replace_target() -> None:
                replacement = pack / "replacement"
                replacement.write_bytes(b"finder-replacement")
                replacement.chmod(0o644)
                os.replace(replacement, target)

            with self.assertRaisesRegex(DsStoreHygieneError, r"targets changed before quarantine"):
                quarantine_ds_store(
                    pack,
                    quarantine,
                    confirmation=QUARANTINE_CONFIRMATION,
                    _before_mutation_hook=replace_target,
                )
            self.assertTrue(target.is_file())
            self.assertFalse(quarantine.exists())
            self.assertEqual(official.read_bytes(), official_before)

    def test_quarantine_rejects_unsafe_destination_and_nonregular_target_before_mutation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "pack"
            pack.mkdir()
            target = pack / ".DS_Store"
            target.write_bytes(b"finder")
            set_private_modes(pack)
            with self.assertRaises(DsStoreHygieneError):
                quarantine_ds_store(pack, pack / "quarantine", confirmation=QUARANTINE_CONFIRMATION)
            self.assertTrue(target.exists())

            git_parent = root / "git-parent"
            git_parent.mkdir()
            (git_parent / ".git").mkdir()
            git_pack = git_parent / "pack"
            git_pack.mkdir()
            (git_pack / ".DS_Store").write_bytes(b"finder")
            set_private_modes(git_pack)
            with self.assertRaises(DsStoreHygieneError):
                quarantine_ds_store(git_pack, root / "external-quarantine", confirmation=QUARANTINE_CONFIRMATION)
            self.assertTrue((git_pack / ".DS_Store").is_file())

            target.unlink()
            target.mkdir()
            with self.assertRaises(DsStoreHygieneError):
                quarantine_ds_store(pack, root / "quarantine", confirmation=QUARANTINE_CONFIRMATION)
            self.assertTrue(target.is_dir())

    def test_cli_dry_run_returns_sanitized_machine_readable_result(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "pack"
            pack.mkdir()
            (pack / ".DS_Store").write_bytes(b"finder")
            set_private_modes(pack)
            (pack / ".DS_Store").chmod(0o644)
            output = io.StringIO()
            with redirect_stdout(output):
                status = cli_main(
                    [
                        "quarantine-ds-store",
                        "--pack",
                        str(pack),
                        "--quarantine",
                        str(root / "quarantine"),
                        "--dry-run",
                    ]
                )
            self.assertEqual(status, 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["status"], "dry_run")
            self.assertEqual(report["target_files_with_group_other_bits"], 1)
            self.assertEqual(report["official_regular_file_identity_comparison"], "not_performed")
            self.assertNotIn(str(pack), output.getvalue())
            self.assertTrue((pack / ".DS_Store").is_file())

    def test_symlinked_pack_or_quarantine_ancestor_is_rejected_before_mutation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_root = root / "real"
            real_root.mkdir()
            pack = real_root / "pack"
            pack.mkdir()
            (pack / ".DS_Store").write_bytes(b"finder")
            set_private_modes(pack)
            (pack / ".DS_Store").chmod(0o644)

            pack_alias = root / "pack-alias"
            pack_alias.symlink_to(real_root, target_is_directory=True)
            with self.assertRaises(DsStoreHygieneError):
                quarantine_ds_store(pack_alias / "pack", root / "quarantine", confirmation=QUARANTINE_CONFIRMATION)
            self.assertTrue((pack / ".DS_Store").exists())
            self.assertFalse((root / "quarantine").exists())

            quarantine_real = root / "quarantine-real"
            quarantine_real.mkdir()
            quarantine_alias = root / "quarantine-alias"
            quarantine_alias.symlink_to(quarantine_real, target_is_directory=True)
            with self.assertRaises(DsStoreHygieneError):
                quarantine_ds_store(pack, quarantine_alias / "new", confirmation=QUARANTINE_CONFIRMATION)
            self.assertTrue((pack / ".DS_Store").exists())
            self.assertFalse((quarantine_real / "new").exists())


def set_private_modes(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(0o700 if path.is_dir() else 0o600)
    root.chmod(0o700)


def tree_bytes_excluding_ds_store(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != ".DS_Store"
    }
