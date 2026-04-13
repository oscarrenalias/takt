"""Tests for the `upgrade` and `asset` CLI subcommands.

Covers:
- Parser wiring: upgrade --dry-run, asset mark-owned/unmark-owned/list subcommands
- command_upgrade: dry-run produces output but no writes; each action type applied correctly
- command_upgrade: upgraded_at written to manifest on real run
- command_upgrade: config key merge inserts new bundled keys into user config
- command_upgrade: exit code 0 even when files are skipped (modified/user-owned)
- command_asset mark-owned: sets user_owned: true for matching bundled entries
- command_asset mark-owned: no-op for already-user-owned entries
- command_asset unmark-owned: sets user_owned: false for bundled entries
- command_asset unmark-owned: skips source: user entries
- command_asset list: shows all columns for populated manifest; "No assets tracked" for empty
- command_asset mark-owned/unmark-owned: no-match glob prints warning, exits 0
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.cli import build_parser, command_asset, command_upgrade
from agent_takt.console import ConsoleReporter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _console(stream: StringIO | None = None) -> ConsoleReporter:
    return ConsoleReporter(stream=stream or StringIO())


def _args_upgrade(root: str, dry_run: bool = False) -> Namespace:
    ns = Namespace()
    ns.root = root
    ns.dry_run = dry_run
    return ns


def _args_asset(root: str, subcommand: str, glob: str | None = None) -> Namespace:
    ns = Namespace()
    ns.root = root
    ns.asset_command = subcommand
    if glob is not None:
        ns.glob = glob
    return ns


def _write_manifest(root: Path, assets: dict) -> None:
    from importlib.metadata import version as _pkg_version
    from datetime import datetime, timezone

    manifest = {
        "takt_version": _pkg_version("agent-takt"),
        "installed_at": datetime.now(tz=timezone.utc).isoformat(),
        "assets": assets,
    }
    mp = root / ".takt" / "assets-manifest.json"
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _fake_bundled_config() -> MagicMock:
    """Return a mock packaged_default_config() that returns an empty YAML path."""
    mock = MagicMock()
    mock.return_value.read_text.return_value = ""
    return mock


# ---------------------------------------------------------------------------
# Parser wiring
# ---------------------------------------------------------------------------


class TestUpgradeParserWiring(unittest.TestCase):
    def test_upgrade_subcommand_registered(self):
        parser = build_parser()
        args = parser.parse_args(["upgrade"])
        self.assertEqual(args.command, "upgrade")

    def test_dry_run_default_false(self):
        parser = build_parser()
        args = parser.parse_args(["upgrade"])
        self.assertFalse(args.dry_run)

    def test_dry_run_flag_sets_true(self):
        parser = build_parser()
        args = parser.parse_args(["upgrade", "--dry-run"])
        self.assertTrue(args.dry_run)

    def test_asset_subcommand_registered(self):
        parser = build_parser()
        args = parser.parse_args(["asset", "list"])
        self.assertEqual(args.command, "asset")
        self.assertEqual(args.asset_command, "list")

    def test_asset_mark_owned_has_glob(self):
        parser = build_parser()
        args = parser.parse_args(["asset", "mark-owned", ".agents/skills/**"])
        self.assertEqual(args.glob, ".agents/skills/**")

    def test_asset_unmark_owned_has_glob(self):
        parser = build_parser()
        args = parser.parse_args(["asset", "unmark-owned", "templates/agents/*"])
        self.assertEqual(args.glob, "templates/agents/*")

    def test_asset_list_no_required_args(self):
        parser = build_parser()
        # Should not raise
        args = parser.parse_args(["asset", "list"])
        self.assertEqual(args.asset_command, "list")


# ---------------------------------------------------------------------------
# command_upgrade — dry-run mode
# ---------------------------------------------------------------------------


class TestCommandUpgradeDryRun(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_dry_run_no_manifest_written(self):
        """--dry-run with unchanged files does not rewrite the manifest."""
        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        content = "skill content"
        disk = self.root / rel
        disk.parent.mkdir(parents=True, exist_ok=True)
        disk.write_text(content)
        sha = _sha256(content)

        _write_manifest(self.root, {rel: {"sha256": sha, "source": "bundled", "user_owned": False}})
        mp = self.root / ".takt" / "assets-manifest.json"
        mtime_before = mp.stat().st_mtime

        from agent_takt.onboarding import AssetDecision
        decisions = [AssetDecision(
            rel_path=rel, action="unchanged",
            current_sha=sha, manifest_sha=sha, bundled_sha=sha, user_owned=False,
        )]

        stream = StringIO()
        console = _console(stream)
        with patch("agent_takt.onboarding.evaluate_upgrade_actions", return_value=decisions), \
             patch("agent_takt.onboarding._compute_bundled_catalog", return_value={rel: disk}), \
             patch("agent_takt._assets.packaged_default_config") as mock_pdc:
            mock_pdc.return_value.read_text.return_value = ""
            rc = command_upgrade(_args_upgrade(str(self.root), dry_run=True), console)

        mtime_after = mp.stat().st_mtime
        self.assertEqual(rc, 0)
        self.assertEqual(mtime_before, mtime_after, "Manifest was rewritten during dry-run")

    def test_dry_run_no_write_but_output_shown(self):
        """--dry-run produces action output but does not write files to disk."""
        rel = ".agents/skills/new-skill/SKILL.md"
        content = "new skill"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            bundled_path = Path(f.name)

        try:
            from agent_takt.onboarding import AssetDecision
            decision = AssetDecision(
                rel_path=rel, action="new",
                current_sha=None, manifest_sha=None,
                bundled_sha=_sha256(content), user_owned=False,
            )
            _write_manifest(self.root, {})
            stream = StringIO()
            console = _console(stream)
            with patch("agent_takt.onboarding.evaluate_upgrade_actions", return_value=[decision]), \
                 patch("agent_takt.onboarding._compute_bundled_catalog", return_value={rel: bundled_path}), \
                 patch("agent_takt._assets.packaged_default_config") as mock_pdc:
                mock_pdc.return_value.read_text.return_value = ""
                rc = command_upgrade(_args_upgrade(str(self.root), dry_run=True), console)
        finally:
            bundled_path.unlink(missing_ok=True)

        self.assertEqual(rc, 0)
        self.assertFalse((self.root / rel).is_file(), "Dry-run must not write files")
        self.assertIn("[new]", stream.getvalue())


# ---------------------------------------------------------------------------
# command_upgrade — action types applied correctly
# ---------------------------------------------------------------------------


class TestCommandUpgradeActions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_disk_file(self, rel: str, content: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def _run_upgrade(self, decisions, bundled_catalog=None, manifest_assets=None, dry_run=False):
        if manifest_assets is None:
            manifest_assets = {}
        _write_manifest(self.root, manifest_assets)
        stream = StringIO()
        console = _console(stream)

        with patch("agent_takt.onboarding.evaluate_upgrade_actions", return_value=decisions), \
             patch("agent_takt.onboarding._compute_bundled_catalog", return_value=bundled_catalog or {}), \
             patch("agent_takt._assets.packaged_default_config") as mock_pdc:
            mock_pdc.return_value.read_text.return_value = ""
            rc = command_upgrade(_args_upgrade(str(self.root), dry_run=dry_run), console)

        return rc, stream.getvalue()

    def test_new_action_installs_file(self):
        """action=new copies the bundled file to disk."""
        rel = ".agents/skills/new-skill/SKILL.md"
        content = "new skill content"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            bundled_path = Path(f.name)

        try:
            from agent_takt.onboarding import AssetDecision
            decision = AssetDecision(
                rel_path=rel, action="new",
                current_sha=None, manifest_sha=None,
                bundled_sha=_sha256(content), user_owned=False,
            )
            rc, output = self._run_upgrade([decision], bundled_catalog={rel: bundled_path})
        finally:
            bundled_path.unlink(missing_ok=True)

        self.assertEqual(rc, 0)
        self.assertTrue((self.root / rel).is_file())
        self.assertIn("[new]", output)

    def test_update_action_overwrites_file(self):
        """action=update overwrites the disk file with the bundled version."""
        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        old_content = "old skill"
        new_content = "new skill from bundle"
        disk = self._make_disk_file(rel, old_content)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(new_content)
            bundled_path = Path(f.name)

        try:
            from agent_takt.onboarding import AssetDecision
            decision = AssetDecision(
                rel_path=rel, action="update",
                current_sha=_sha256(old_content), manifest_sha=_sha256(old_content),
                bundled_sha=_sha256(new_content), user_owned=False,
            )
            rc, output = self._run_upgrade(
                [decision],
                bundled_catalog={rel: bundled_path},
                manifest_assets={rel: {"sha256": _sha256(old_content), "source": "bundled", "user_owned": False}},
            )
        finally:
            bundled_path.unlink(missing_ok=True)

        self.assertEqual(rc, 0)
        self.assertEqual(disk.read_text(), new_content)
        self.assertIn("[updated]", output)

    def test_restored_action_restores_file(self):
        """action=restored copies the bundled file when disk file is missing."""
        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        content = "bundled content to restore"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(content)
            bundled_path = Path(f.name)

        try:
            from agent_takt.onboarding import AssetDecision
            decision = AssetDecision(
                rel_path=rel, action="restored",
                current_sha=None, manifest_sha=_sha256(content),
                bundled_sha=_sha256(content), user_owned=False,
            )
            rc, output = self._run_upgrade(
                [decision],
                bundled_catalog={rel: bundled_path},
                manifest_assets={rel: {"sha256": _sha256(content), "source": "bundled", "user_owned": False}},
            )
        finally:
            bundled_path.unlink(missing_ok=True)

        self.assertEqual(rc, 0)
        self.assertTrue((self.root / rel).is_file())
        self.assertIn("[restored]", output)

    def test_disabled_action_renames_file_to_disabled(self):
        """action=disabled renames the disk file to .disabled."""
        rel = ".agents/skills/old/deprecated/SKILL.md"
        disk = self._make_disk_file(rel, "old content")
        sha = _sha256("old content")

        from agent_takt.onboarding import AssetDecision
        decision = AssetDecision(
            rel_path=rel, action="disabled",
            current_sha=sha, manifest_sha=sha,
            bundled_sha=None, user_owned=False,
        )
        rc, output = self._run_upgrade(
            [decision],
            manifest_assets={rel: {"sha256": sha, "source": "bundled", "user_owned": False}},
        )

        self.assertEqual(rc, 0)
        self.assertFalse(disk.is_file(), "Original file should be renamed away")
        self.assertTrue((disk.parent / (disk.name + ".disabled")).is_file())
        self.assertIn("[disabled", output)

    def test_skipped_modified_no_write(self):
        """action=skipped_modified does not overwrite the user-modified file."""
        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        user_content = "user modified"
        disk = self._make_disk_file(rel, user_content)

        from agent_takt.onboarding import AssetDecision
        decision = AssetDecision(
            rel_path=rel, action="skipped_modified",
            current_sha=_sha256(user_content),
            manifest_sha=_sha256("original install"),
            bundled_sha=_sha256("bundled"),
            user_owned=False,
        )
        rc, output = self._run_upgrade([decision])

        self.assertEqual(rc, 0)
        self.assertEqual(disk.read_text(), user_content)
        self.assertIn("locally modified", output)

    def test_skipped_user_owned_no_write(self):
        """action=skipped_user_owned does not overwrite user-owned file."""
        rel = "templates/agents/developer.md"
        content = "user template"
        disk = self._make_disk_file(rel, content)
        sha = _sha256(content)

        from agent_takt.onboarding import AssetDecision
        decision = AssetDecision(
            rel_path=rel, action="skipped_user_owned",
            current_sha=sha, manifest_sha=sha,
            bundled_sha=_sha256("bundled template"), user_owned=True,
        )
        rc, output = self._run_upgrade([decision])

        self.assertEqual(rc, 0)
        self.assertEqual(disk.read_text(), content)
        self.assertIn("user-owned", output)

    def test_exit_code_zero_when_all_skipped(self):
        """Exit code is 0 even when all files are skipped."""
        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        self._make_disk_file(rel, "user modified")

        from agent_takt.onboarding import AssetDecision
        decisions = [
            AssetDecision(
                rel_path=rel, action="skipped_modified",
                current_sha=_sha256("user modified"),
                manifest_sha=_sha256("original"),
                bundled_sha=_sha256("bundled"),
                user_owned=False,
            )
        ]
        rc, _ = self._run_upgrade(decisions)
        self.assertEqual(rc, 0)

    def test_upgraded_at_written_to_manifest(self):
        """After a real (non-dry-run) upgrade, upgraded_at is in the manifest."""
        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        content = "content"
        disk = self._make_disk_file(rel, content)
        sha = _sha256(content)

        _write_manifest(self.root, {rel: {"sha256": sha, "source": "bundled", "user_owned": False}})

        from agent_takt.onboarding import AssetDecision
        decision = AssetDecision(
            rel_path=rel, action="unchanged",
            current_sha=sha, manifest_sha=sha, bundled_sha=sha, user_owned=False,
        )

        stream = StringIO()
        console = _console(stream)
        with patch("agent_takt.onboarding.evaluate_upgrade_actions", return_value=[decision]), \
             patch("agent_takt.onboarding._compute_bundled_catalog", return_value={rel: disk}), \
             patch("agent_takt._assets.packaged_default_config") as mock_pdc:
            mock_pdc.return_value.read_text.return_value = ""
            rc = command_upgrade(_args_upgrade(str(self.root), dry_run=False), console)

        self.assertEqual(rc, 0)
        data = json.loads((self.root / ".takt" / "assets-manifest.json").read_text())
        self.assertIn("upgraded_at", data)
        self.assertNotEqual(data["upgraded_at"], "")


# ---------------------------------------------------------------------------
# command_upgrade — config key merge
# ---------------------------------------------------------------------------


class TestCommandUpgradeConfigMerge(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _write_manifest(self.root, {})

    def tearDown(self):
        self._tmp.cleanup()

    def _write_config(self, content: str) -> Path:
        p = self.root / ".takt" / "config.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_config_merge_inserts_new_keys(self):
        """Config merge adds new bundled keys that are missing from the user config."""
        self._write_config("existing_key: existing_value\n")

        stream = StringIO()
        console = _console(stream)

        user_cfg = {"existing_key": "existing_value"}
        bundled_cfg = {"existing_key": "bundled_value", "new_key": "new_value"}

        import yaml
        with patch("agent_takt.onboarding.evaluate_upgrade_actions", return_value=[]), \
             patch("agent_takt.onboarding._compute_bundled_catalog", return_value={}), \
             patch("agent_takt._assets.packaged_default_config") as mock_pdc, \
             patch("yaml.safe_load", side_effect=[user_cfg, bundled_cfg]):
            mock_pdc.return_value.read_text.return_value = "new_key: new_value\n"
            rc = command_upgrade(_args_upgrade(str(self.root), dry_run=False), console)

        output = stream.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("new_key", output)
        self.assertIn("[added]", output)

    def test_config_merge_no_op_when_user_has_all_keys(self):
        """Config merge is silent when user config already has all bundled keys."""
        self._write_config("existing_key: user_value\n")

        stream = StringIO()
        console = _console(stream)

        user_cfg = {"existing_key": "user_value"}
        bundled_cfg = {"existing_key": "bundled_value"}

        with patch("agent_takt.onboarding.evaluate_upgrade_actions", return_value=[]), \
             patch("agent_takt.onboarding._compute_bundled_catalog", return_value={}), \
             patch("agent_takt._assets.packaged_default_config") as mock_pdc, \
             patch("yaml.safe_load", side_effect=[user_cfg, bundled_cfg]):
            mock_pdc.return_value.read_text.return_value = "existing_key: bundled_value\n"
            rc = command_upgrade(_args_upgrade(str(self.root), dry_run=False), console)

        output = stream.getvalue()
        self.assertEqual(rc, 0)
        self.assertNotIn("[added]", output)


# ---------------------------------------------------------------------------
# command_asset — mark-owned / unmark-owned / list
# ---------------------------------------------------------------------------


class TestCommandAsset(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_manifest(self, assets: dict) -> None:
        _write_manifest(self.root, assets)

    def _run_asset(self, subcommand: str, glob: str | None = None) -> tuple[int, str]:
        stream = StringIO()
        console = _console(stream)
        rc = command_asset(_args_asset(str(self.root), subcommand, glob), console)
        return rc, stream.getvalue()

    # -- mark-owned -----------------------------------------------------------

    def test_mark_owned_sets_user_owned_true(self):
        """mark-owned with a matching glob sets user_owned: true in manifest."""
        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        self._write_manifest({rel: {"sha256": "abc", "source": "bundled", "user_owned": False}})

        rc, output = self._run_asset("mark-owned", ".agents/skills/**")

        self.assertEqual(rc, 0)
        data = json.loads((self.root / ".takt" / "assets-manifest.json").read_text())
        self.assertTrue(data["assets"][rel]["user_owned"])

    def test_mark_owned_user_added_already_user_owned(self):
        """mark-owned on an already user_owned entry is idempotent."""
        rel = ".agents/skills/custom/my-skill/SKILL.md"
        self._write_manifest({rel: {"sha256": "abc", "source": "user", "user_owned": True}})

        rc, output = self._run_asset("mark-owned", ".agents/skills/custom/**")

        self.assertEqual(rc, 0)
        data = json.loads((self.root / ".takt" / "assets-manifest.json").read_text())
        self.assertTrue(data["assets"][rel]["user_owned"])

    def test_mark_owned_no_match_warns_exit_zero(self):
        """mark-owned with a non-matching glob prints a warning and exits 0."""
        self._write_manifest({})

        rc, output = self._run_asset("mark-owned", ".agents/skills/nonexistent/**")

        self.assertEqual(rc, 0)
        self.assertIn("No manifest entries matched", output)

    # -- unmark-owned ---------------------------------------------------------

    def test_unmark_owned_bundled_entry_sets_false(self):
        """unmark-owned with a matching bundled-source entry sets user_owned: false."""
        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        self._write_manifest({rel: {"sha256": "abc", "source": "bundled", "user_owned": True}})

        rc, output = self._run_asset("unmark-owned", ".agents/skills/**")

        self.assertEqual(rc, 0)
        data = json.loads((self.root / ".takt" / "assets-manifest.json").read_text())
        self.assertFalse(data["assets"][rel]["user_owned"])

    def test_unmark_owned_user_source_skipped(self):
        """unmark-owned skips source: user entries and leaves user_owned: true."""
        rel = ".agents/skills/custom/my-skill/SKILL.md"
        self._write_manifest({rel: {"sha256": "abc", "source": "user", "user_owned": True}})

        rc, output = self._run_asset("unmark-owned", ".agents/skills/custom/**")

        self.assertEqual(rc, 0)
        data = json.loads((self.root / ".takt" / "assets-manifest.json").read_text())
        self.assertTrue(data["assets"][rel]["user_owned"])
        self.assertIn("skipped", output)

    def test_unmark_owned_no_match_warns_exit_zero(self):
        """unmark-owned with no matching glob prints a warning and exits 0."""
        self._write_manifest({})

        rc, output = self._run_asset("unmark-owned", "templates/agents/nonexistent.md")

        self.assertEqual(rc, 0)
        self.assertIn("No manifest entries matched", output)

    # -- list -----------------------------------------------------------------

    def test_list_populated_manifest_shows_columns(self):
        """list with a populated manifest prints PATH, STATUS, SOURCE, OWNED columns."""
        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        disk = self.root / rel
        disk.parent.mkdir(parents=True, exist_ok=True)
        disk.write_text("skill content")
        sha = _sha256("skill content")
        self._write_manifest({rel: {"sha256": sha, "source": "bundled", "user_owned": False}})

        from agent_takt.onboarding import AssetDecision
        decision = AssetDecision(
            rel_path=rel, action="unchanged",
            current_sha=sha, manifest_sha=sha, bundled_sha=sha, user_owned=False,
        )

        stream = StringIO()
        console = _console(stream)
        with patch("agent_takt.onboarding.evaluate_upgrade_actions", return_value=[decision]):
            rc = command_asset(_args_asset(str(self.root), "list"), console)

        output = stream.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("PATH", output)
        self.assertIn("STATUS", output)
        self.assertIn("SOURCE", output)
        self.assertIn("OWNED", output)
        self.assertIn(rel, output)

    def test_list_empty_manifest_shows_no_assets_message(self):
        """list on an empty manifest prints 'No assets tracked'."""
        self._write_manifest({})

        stream = StringIO()
        console = _console(stream)
        with patch("agent_takt.onboarding.evaluate_upgrade_actions", return_value=[]):
            rc = command_asset(_args_asset(str(self.root), "list"), console)

        output = stream.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("No assets tracked", output)


# ---------------------------------------------------------------------------
# command_upgrade — commit_scaffold call behavior
# ---------------------------------------------------------------------------


class TestCommandUpgradeCommitBehavior(unittest.TestCase):
    """Verify commit_scaffold is called on real runs and skipped on dry-run."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_disk_file(self, rel: str, content: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_commit_scaffold_called_when_update_occurs(self):
        """commit_scaffold is called when dry_run=False and at least one asset is updated."""
        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        old_content = "old skill"
        new_content = "updated skill from bundle"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(new_content)
            bundled_path = Path(f.name)

        try:
            self._make_disk_file(rel, old_content)
            _write_manifest(
                self.root,
                {rel: {"sha256": _sha256(old_content), "source": "bundled", "user_owned": False}},
            )

            from agent_takt.onboarding import AssetDecision
            decision = AssetDecision(
                rel_path=rel, action="update",
                current_sha=_sha256(old_content), manifest_sha=_sha256(old_content),
                bundled_sha=_sha256(new_content), user_owned=False,
            )

            stream = StringIO()
            console = _console(stream)

            with (
                patch("agent_takt.onboarding.evaluate_upgrade_actions", return_value=[decision]),
                patch("agent_takt.onboarding._compute_bundled_catalog", return_value={rel: bundled_path}),
                patch("agent_takt._assets.packaged_default_config") as mock_pdc,
                patch("agent_takt.cli.commands.init.commit_scaffold") as mock_commit,
            ):
                mock_pdc.return_value.read_text.return_value = ""
                rc = command_upgrade(_args_upgrade(str(self.root), dry_run=False), console)
        finally:
            bundled_path.unlink(missing_ok=True)

        self.assertEqual(rc, 0)
        mock_commit.assert_called_once()

    def test_dry_run_does_not_call_commit_scaffold(self):
        """commit_scaffold is NOT called when dry_run=True; the dry-run notice is emitted."""
        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        content = "skill content"
        self._make_disk_file(rel, content)
        sha = _sha256(content)
        _write_manifest(self.root, {rel: {"sha256": sha, "source": "bundled", "user_owned": False}})

        from agent_takt.onboarding import AssetDecision
        decision = AssetDecision(
            rel_path=rel, action="update",
            current_sha=sha, manifest_sha=sha,
            bundled_sha=_sha256("newer bundled content"), user_owned=False,
        )

        stream = StringIO()
        console = _console(stream)

        with (
            patch("agent_takt.onboarding.evaluate_upgrade_actions", return_value=[decision]),
            patch("agent_takt.onboarding._compute_bundled_catalog", return_value={rel: self.root / rel}),
            patch("agent_takt._assets.packaged_default_config") as mock_pdc,
            patch("agent_takt.cli.commands.init.commit_scaffold") as mock_commit,
        ):
            mock_pdc.return_value.read_text.return_value = ""
            rc = command_upgrade(_args_upgrade(str(self.root), dry_run=True), console)

        self.assertEqual(rc, 0)
        mock_commit.assert_not_called()
        self.assertIn("[dry-run] would commit upgraded assets", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
