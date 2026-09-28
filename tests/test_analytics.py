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
from unittest.mock import patch

from openai_export_obsidian.analytics import ANALYTICS_SCHEMA, REVIEW_SCHEMA
from openai_export_obsidian.analytics.aggregates import build_dashboard, distribution, quantile
from openai_export_obsidian.analytics.inspection import inspect_analytics
from openai_export_obsidian.analytics.installation import AnalyticsInstallError, install
from openai_export_obsidian.analytics.paths import AnalyticsPathError, resolve_analytics_paths
from openai_export_obsidian.analytics.reader import read_pack_analytics
from openai_export_obsidian.analytics.refresh import refresh
from openai_export_obsidian.analytics.schema import validate_build_manifest, validate_dashboard, validate_review_state
from openai_export_obsidian.analytics.signatures import conversation_signature
from openai_export_obsidian.analytics.templates import template_bytes
from openai_export_obsidian.conversation_note_locator import CONVERSATION_NOTE_LOCATOR_RELATIVE_PATH
from openai_export_obsidian.validation import validate_pack


ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_PACK = ROOT / "contracts/parser-pack/openai-obsidian-pack-v1.4/synthetic-example/pack"
CLI_ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}


class AnalyticsTests(unittest.TestCase):
    def vault(self, temporary: str) -> tuple[Path, Path, Path]:
        vault = Path(temporary) / "vault"
        (vault / ".obsidian").mkdir(parents=True)
        pack = vault / "chatGPT"
        shutil.copytree(SYNTHETIC_PACK, pack)
        return vault, pack, vault / "Analytics/chatGPT"

    def test_paths_require_separation_and_common_vault(self) -> None:
        with TemporaryDirectory() as temporary:
            vault, pack, output = self.vault(temporary)
            paths = resolve_analytics_paths(pack, output)
            self.assertEqual(paths.pack_vault_path, "chatGPT")
            self.assertEqual(paths.output_vault_path, "Analytics/chatGPT")
            with self.assertRaises(AnalyticsPathError):
                resolve_analytics_paths(pack, pack)
            with self.assertRaises(AnalyticsPathError):
                resolve_analytics_paths(pack, pack / "Analytics")
            output_link = vault / "output-link"
            output_link.symlink_to(output)
            with self.assertRaises(AnalyticsPathError):
                resolve_analytics_paths(pack, output_link)
            output_link.unlink()
            output_file = vault / "output-file"
            output_file.write_text("not a directory", encoding="utf-8")
            with self.assertRaises(AnalyticsPathError):
                resolve_analytics_paths(pack, output_file)
            outside = Path(temporary) / "outside"
            with self.assertRaises(AnalyticsPathError):
                resolve_analytics_paths(pack, outside)

    def test_inspect_is_read_only_and_install_is_idempotent(self) -> None:
        with TemporaryDirectory() as temporary:
            _, pack, output = self.vault(temporary)
            paths = resolve_analytics_paths(pack, output)
            analytics = read_pack_analytics(paths)
            before = tree_hash(paths.vault)
            report = inspect_analytics(paths, analytics)
            self.assertEqual(report["files"]["Parsing Data Explorer.md"], "create")
            self.assertEqual(report["files"]["cache/dashboard.json"], "missing")
            self.assertEqual(tree_hash(paths.vault), before)
            installed = install(paths)
            self.assertEqual(installed["status"], "installed")
            state_bytes = (output / "state/review-state.json").read_bytes()
            second = install(paths)
            self.assertEqual(second["created"], [])
            self.assertEqual((output / "state/review-state.json").read_bytes(), state_bytes)

    def test_install_refuses_divergent_app_before_writing(self) -> None:
        with TemporaryDirectory() as temporary:
            _, pack, output = self.vault(temporary)
            paths = resolve_analytics_paths(pack, output)
            (output / "app").mkdir(parents=True)
            (output / "app/view.js").write_text("divergent", encoding="utf-8")
            with self.assertRaises(AnalyticsInstallError):
                install(paths)
            self.assertFalse((output / "Parsing Data Explorer.md").exists())
            self.assertFalse((output / "state/review-state.json").exists())

    def test_refresh_builds_closed_cache_and_preserves_owned_surfaces(self) -> None:
        with TemporaryDirectory() as temporary:
            _, pack, output = self.vault(temporary)
            paths = resolve_analytics_paths(pack, output)
            analytics = read_pack_analytics(paths)
            pack_before = tree_hash(pack)
            install(paths)
            app_before = tree_hash(output / "app")
            state_before = (output / "state/review-state.json").read_bytes()
            report = refresh(paths, analytics)
            self.assertEqual(report["status"], "refreshed")
            dashboard_bytes = (output / "cache/dashboard.json").read_bytes()
            dashboard = json.loads(dashboard_bytes)
            manifest = json.loads((output / "cache/build-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(validate_dashboard(dashboard), [])
            self.assertEqual(validate_build_manifest(manifest, dashboard_bytes), [])
            self.assertEqual(manifest["analytics_schema"], ANALYTICS_SCHEMA)
            self.assertEqual(tree_hash(pack), pack_before)
            self.assertEqual(tree_hash(output / "app"), app_before)
            self.assertEqual((output / "state/review-state.json").read_bytes(), state_before)
            first_cache = tree_hash(output / "cache")
            refresh(paths, read_pack_analytics(paths))
            self.assertEqual(tree_hash(output / "cache"), first_cache)
            inspected = inspect_analytics(paths, read_pack_analytics(paths))
            self.assertTrue(inspected["cache_fresh"])

    def test_refresh_restores_old_cache_when_swap_fails(self) -> None:
        with TemporaryDirectory() as temporary:
            _, pack, output = self.vault(temporary)
            paths = resolve_analytics_paths(pack, output)
            install(paths)
            refresh(paths, read_pack_analytics(paths))
            before = tree_hash(output / "cache")
            from openai_export_obsidian.analytics import refresh as refresh_module

            real_replace = refresh_module.os.replace
            def failing_replace(source, target):
                if Path(source).name.startswith(".cache-staging-") and Path(target).name == "cache":
                    raise OSError("injected swap failure")
                return real_replace(source, target)

            with patch.object(refresh_module.os, "replace", side_effect=failing_replace):
                with self.assertRaises(OSError):
                    refresh(paths, read_pack_analytics(paths))
            self.assertEqual(tree_hash(output / "cache"), before)

    def test_source_change_is_reported_stale(self) -> None:
        with TemporaryDirectory() as temporary:
            _, pack, output = self.vault(temporary)
            paths = resolve_analytics_paths(pack, output)
            install(paths)
            refresh(paths, read_pack_analytics(paths))
            manifest_path = pack / "90_Evidence/pack_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["generated_at"] = "2099-01-01T00:00:00Z"
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            inspected = inspect_analytics(paths, read_pack_analytics(paths))
            self.assertFalse(inspected["cache_fresh"])
            self.assertEqual(inspected["files"]["cache/dashboard.json"], "stale")

    def test_analytics_tolerates_renamed_note_without_reconstructing_navigation(self) -> None:
        with TemporaryDirectory() as temporary:
            _, pack, output = self.vault(temporary)
            locator_rows = [
                json.loads(line)
                for line in (pack / CONVERSATION_NOTE_LOCATOR_RELATIVE_PATH).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            original_relative = locator_rows[0]["relative_note_path"]
            original = pack / original_relative
            renamed = original.with_name("user-renamed-note.md")
            original.rename(renamed)

            strict_report = validate_pack(pack)
            self.assertTrue(strict_report["errors"])
            paths = resolve_analytics_paths(pack, output)
            analytics = read_pack_analytics(paths)
            affected = next(
                row for row in analytics.conversations
                if row["conversation_id"] == locator_rows[0]["conversation_id"]
            )
            self.assertIsNone(affected["note_path"])
            self.assertIn("navigation_target_missing:count=1", analytics.warnings)
            self.assertIn("navigation_note_set_drift", analytics.warnings)
            self.assertNotIn(renamed.relative_to(pack).as_posix(), json.dumps(analytics.conversations))
            inspected = inspect_analytics(paths, analytics)
            self.assertIn("navigation_target_missing:count=1", inspected["diagnostics"])

    def test_analytics_does_not_tolerate_locator_symlink(self) -> None:
        with TemporaryDirectory() as temporary:
            _, pack, output = self.vault(temporary)
            locator_rows = [
                json.loads(line)
                for line in (pack / CONVERSATION_NOTE_LOCATOR_RELATIVE_PATH).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            original = pack / locator_rows[0]["relative_note_path"]
            moved = original.with_name("moved-target.md")
            original.rename(moved)
            original.symlink_to(moved.name)
            paths = resolve_analytics_paths(pack, output)
            with self.assertRaisesRegex(ValueError, "pack validation failed"):
                read_pack_analytics(paths)

    def test_review_schema_preserves_orphans_and_signature_excludes_navigation(self) -> None:
        state = {"version": REVIEW_SCHEMA, "records": {"orphan": {"status": "reviewed", "status_updated_at": "2026-01-01T00:00:00Z", "source_signature": "a" * 64}}}
        self.assertEqual(validate_review_state(state), [])
        base = {field: None for field in ()}
        row = {
            "conversation_id": "c", "updated_at": "2026-01-01", "message_count": 1,
            "file_reference_count": 0, "unique_file_count": 0, "resolved_file_count": 0,
            "unresolved_file_count": 0, "source_evidence_count": 0, "tool_evidence_count": 0,
            "context_evidence": "unknown", "default_model": None, "models_seen": [], "gpts": [],
            "space_projects": [], "knowledge_stores": [], "is_archived": False, "note_path": "one.md", "title": "private",
        }
        changed_navigation = dict(row, note_path="two.md", title="other")
        self.assertEqual(conversation_signature(row), conversation_signature(changed_navigation))
        changed_counts = dict(row, message_count=2)
        self.assertNotEqual(conversation_signature(row), conversation_signature(changed_counts))

    def test_quantiles_and_template_safety(self) -> None:
        self.assertEqual(quantile([0, 10], 0.25), 2.5)
        self.assertEqual(distribution([0, 10])["p95"], 9.5)
        resources = template_bytes()
        self.assertEqual(set(resources), {"Parsing Data Explorer.md", "app/view.js", "app/view.css"})
        javascript = resources["app/view.js"].decode("utf-8")
        self.assertIn("app.vault.adapter.process", javascript)
        self.assertIn("crypto.subtle.digest", javascript)
        self.assertNotIn("https://", javascript)
        self.assertNotIn("http://", javascript)

    def test_analytics_cli_lifecycle(self) -> None:
        with TemporaryDirectory() as temporary:
            _, pack, output = self.vault(temporary)
            base = [sys.executable, "-m", "openai_export_obsidian", "analytics"]
            for operation in ("inspect", "install", "refresh", "inspect"):
                completed = subprocess.run(
                    [*base, operation, "--pack", str(pack), "--output", str(output)],
                    env=CLI_ENV,
                    check=False,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                report = json.loads(completed.stdout)
                self.assertIn("status", report)
            self.assertTrue(report["cache_fresh"])


def tree_hash(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


if __name__ == "__main__":
    unittest.main()
