"""Tests for scaffold orchestration in agent_takt.onboarding.

Covers:
- collect_init_answers (via injected streams)
- update_gitignore
- create_specs_howto
- scaffold_project (high-level entry point + load_config integration)
- commit_scaffold

Asset installation and copy helper tests live in test_onboarding_assets.py.
Config generation, key merge, and template substitution tests live in test_onboarding_config.py.
Upgrade evaluation, AssetDecision, and manifest read/write tests live in test_onboarding_upgrade.py.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.console import ConsoleReporter
from agent_takt.onboarding import (
    InitAnswers,
    collect_init_answers,
    commit_scaffold,
    create_specs_howto,
    scaffold_project,
    update_gitignore,
)


def _make_answers(**kwargs) -> InitAnswers:
    defaults = dict(
        runner="claude",
        max_workers=2,
        language="Python",
        test_command="pytest",
        build_check_command="python -m py_compile",
    )
    defaults.update(kwargs)
    return InitAnswers(**defaults)


# ---------------------------------------------------------------------------
# collect_init_answers
# ---------------------------------------------------------------------------


class TestCollectInitAnswers(unittest.TestCase):
    def _run(self, lines: list[str]) -> InitAnswers:
        inp = io.StringIO("\n".join(lines) + "\n")
        out = io.StringIO()
        return collect_init_answers(stream_in=inp, stream_out=out)

    def test_accepts_defaults(self):
        # All empty → all defaults
        answers = self._run(["", "", "", "", ""])
        self.assertEqual(answers.runner, "claude")
        self.assertEqual(answers.max_workers, 1)
        self.assertEqual(answers.language, "Python")
        self.assertEqual(answers.test_command, "pytest")
        self.assertEqual(answers.build_check_command, "python -m py_compile")

    def test_custom_values(self):
        # Runner=2 (codex), workers=4, stack=3 (TypeScript), accept defaults for test/build
        answers = self._run(["2", "4", "3", "", ""])
        self.assertEqual(answers.runner, "codex")
        self.assertEqual(answers.max_workers, 4)
        self.assertEqual(answers.language, "TypeScript")
        self.assertEqual(answers.test_command, "npm test")
        self.assertEqual(answers.build_check_command, "tsc --noEmit")

    def test_other_stack_free_text(self):
        # Stack=7 (Other) → free-text prompts for language, test, build
        answers = self._run(["1", "2", "7", "TypeScript/Node.js", "npm test", "tsc --noEmit"])
        self.assertEqual(answers.runner, "claude")
        self.assertEqual(answers.max_workers, 2)
        self.assertEqual(answers.language, "TypeScript/Node.js")
        self.assertEqual(answers.test_command, "npm test")
        self.assertEqual(answers.build_check_command, "tsc --noEmit")

    def test_predefined_stack_allows_command_override(self):
        # Stack=1 (Python), override both commands
        answers = self._run(["1", "1", "1", "uv run pytest", "uv run python -m py_compile"])
        self.assertEqual(answers.language, "Python")
        self.assertEqual(answers.test_command, "uv run pytest")
        self.assertEqual(answers.build_check_command, "uv run python -m py_compile")

    def test_invalid_runner_then_valid(self):
        # "notanumber" is rejected by _select_from_list, "1" selects claude
        answers = self._run(["notanumber", "1", "", "", "", ""])
        self.assertEqual(answers.runner, "claude")

    def test_invalid_max_workers_then_valid(self):
        answers = self._run(["", "abc", "0", "2", "", "", ""])
        self.assertEqual(answers.max_workers, 2)

    def test_negative_max_workers_rejected(self):
        answers = self._run(["", "-1", "3", "", "", ""])
        self.assertEqual(answers.max_workers, 3)


# ---------------------------------------------------------------------------
# update_gitignore
# ---------------------------------------------------------------------------


class TestUpdateGitignore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_creates_gitignore_when_missing(self):
        result = update_gitignore(self.root)
        self.assertTrue(result)
        gitignore = self.root / ".gitignore"
        self.assertTrue(gitignore.is_file())
        content = gitignore.read_text()
        self.assertIn(".takt/worktrees/", content)

    def test_appends_to_existing_gitignore(self):
        gitignore = self.root / ".gitignore"
        gitignore.write_text("node_modules/\n")
        result = update_gitignore(self.root)
        self.assertTrue(result)
        content = gitignore.read_text()
        self.assertIn("node_modules/", content)
        self.assertIn(".takt/worktrees/", content)

    def test_idempotent_when_entries_present(self):
        update_gitignore(self.root)
        result = update_gitignore(self.root)
        self.assertFalse(result)

    def test_all_entries_added(self):
        update_gitignore(self.root)
        content = (self.root / ".gitignore").read_text()
        for entry in [
            ".takt/worktrees/",
            ".takt/telemetry/",
            ".takt/logs/",
            ".takt/agent-runs/",
        ]:
            self.assertIn(entry, content)


# ---------------------------------------------------------------------------
# create_specs_howto
# ---------------------------------------------------------------------------


class TestCreateSpecsHowto(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_creates_howto(self):
        result = create_specs_howto(self.root)
        self.assertIsNotNone(result)
        self.assertEqual(result, self.root / "specs" / "HOWTO.md")
        self.assertIn("spec", result.read_text().lower())

    def test_skips_existing_without_overwrite(self):
        dest = self.root / "specs" / "HOWTO.md"
        dest.parent.mkdir(parents=True)
        dest.write_text("original")
        result = create_specs_howto(self.root, overwrite=False)
        self.assertIsNone(result)
        self.assertEqual(dest.read_text(), "original")

    def test_overwrites_when_flag_set(self):
        dest = self.root / "specs" / "HOWTO.md"
        dest.parent.mkdir(parents=True)
        dest.write_text("original")
        result = create_specs_howto(self.root, overwrite=True)
        self.assertIsNotNone(result)
        self.assertNotEqual(dest.read_text(), "original")


# ---------------------------------------------------------------------------
# scaffold_project (integration-level)
# ---------------------------------------------------------------------------


class TestScaffoldProject(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_fake_dirs(self):
        """Create minimal fake packaged asset dirs so scaffold doesn't need real package data."""
        fake_templates = self.root / "_fake_templates"
        fake_templates.mkdir()
        (fake_templates / "developer.md").write_text("lang={{LANGUAGE}}")

        fake_agents = self.root / "_fake_agents_skills"
        fake_agents.mkdir()
        (fake_agents / "skill.md").write_text("skill")

        fake_claude = self.root / "_fake_claude_skills"
        fake_claude.mkdir()
        (fake_claude / "skill.md").write_text("skill")

        fake_config = self.root / "_fake_config.yaml"
        fake_config.write_text("fake: true")

        return fake_templates, fake_agents, fake_claude, fake_config

    def test_scaffold_creates_required_structure(self):
        fake_templates, fake_agents, fake_claude, fake_config = self._make_fake_dirs()
        answers = _make_answers()
        out = io.StringIO()

        with (
            patch("agent_takt.onboarding.config.packaged_templates_dir", return_value=fake_templates),
            patch("agent_takt.onboarding.assets.packaged_agents_skills_dir", return_value=fake_agents),
            patch("agent_takt.onboarding.assets.packaged_claude_skills_dir", return_value=fake_claude),
            patch("agent_takt.onboarding.config.packaged_default_config", return_value=fake_config),
        ):
            scaffold_project(self.root, answers, stream_out=out)

        # .takt subdirs
        for subdir in ("beads", "logs", "worktrees", "telemetry", "agent-runs"):
            self.assertTrue((self.root / ".takt" / subdir).is_dir(), subdir)

        # config.yaml
        self.assertTrue((self.root / ".takt" / "config.yaml").is_file())

        # templates installed with substitution
        self.assertTrue((self.root / "templates" / "agents" / "developer.md").is_file())

        # skill catalogs
        self.assertTrue((self.root / ".agents" / "skills" / "skill.md").is_file())
        self.assertTrue((self.root / ".claude" / "skills" / "skill.md").is_file())

        # .gitignore
        self.assertTrue((self.root / ".gitignore").is_file())

        # specs structure
        self.assertTrue((self.root / "specs" / "HOWTO.md").is_file())
        self.assertTrue((self.root / "specs" / "done").is_dir())
        self.assertTrue((self.root / "specs" / "drafts").is_dir())

    def test_scaffold_output_mentions_done(self):
        fake_templates, fake_agents, fake_claude, fake_config = self._make_fake_dirs()
        answers = _make_answers()
        out = io.StringIO()
        with (
            patch("agent_takt.onboarding.config.packaged_templates_dir", return_value=fake_templates),
            patch("agent_takt.onboarding.assets.packaged_agents_skills_dir", return_value=fake_agents),
            patch("agent_takt.onboarding.assets.packaged_claude_skills_dir", return_value=fake_claude),
            patch("agent_takt.onboarding.config.packaged_default_config", return_value=fake_config),
        ):
            scaffold_project(self.root, answers, stream_out=out)
        output = out.getvalue()
        self.assertIn("Done", output)


# ---------------------------------------------------------------------------
# scaffold_project + load_config integration
# ---------------------------------------------------------------------------


class TestScaffoldProjectLoadConfigIntegration(unittest.TestCase):
    """Verify that config.yaml written by scaffold_project() is parseable by load_config()."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_scaffold_config_parseable_by_load_config(self):
        """scaffold_project writes a valid config.yaml that load_config() can parse without error."""
        from agent_takt.config import load_config

        answers = _make_answers(runner="claude", test_command="uv run pytest")
        out = io.StringIO()
        scaffold_project(self.root, answers, stream_out=out)

        config = load_config(self.root)
        self.assertEqual(config.default_runner, "claude")
        self.assertEqual(config.common.test_command, "uv run pytest")

    def test_scaffold_codex_runner_parsed_correctly(self):
        """default_runner=codex is round-tripped correctly through scaffold + load_config."""
        from agent_takt.config import load_config

        answers = _make_answers(runner="codex", test_command="go test ./...")
        out = io.StringIO()
        scaffold_project(self.root, answers, stream_out=out)

        config = load_config(self.root)
        self.assertEqual(config.default_runner, "codex")
        self.assertEqual(config.common.test_command, "go test ./...")

    def test_scaffold_config_has_allowed_tools(self):
        """Generated config.yaml preserves allowed_tools_default and allowed_tools_by_agent."""
        answers = _make_answers(runner="claude")
        out = io.StringIO()
        scaffold_project(self.root, answers, stream_out=out)

        config_text = (self.root / ".takt" / "config.yaml").read_text(encoding="utf-8")
        self.assertIn("allowed_tools_default:", config_text)
        self.assertIn("- Edit", config_text)
        self.assertIn("- Write", config_text)
        self.assertIn("- Read", config_text)
        self.assertIn("- Bash", config_text)
        self.assertIn("- Glob", config_text)
        self.assertIn("- Grep", config_text)
        self.assertIn("allowed_tools_by_agent:", config_text)
        self.assertIn("- Agent", config_text)
        self.assertIn("- TaskCreate", config_text)


# ---------------------------------------------------------------------------
# commit_scaffold
# ---------------------------------------------------------------------------


class TestCommitScaffold(unittest.TestCase):
    """Tests for commit_scaffold(): git staging, committing, idempotency, and edge cases."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # Initialise a real git repo so subprocess git commands work.
        subprocess.run(["git", "init", str(self.root)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(self.root), "config", "user.email", "test@example.com"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "config", "user.name", "Test User"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "config", "commit.gpgsign", "false"],
            check=True, capture_output=True,
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _make_console(self) -> tuple["ConsoleReporter", io.StringIO]:
        out = io.StringIO()
        return ConsoleReporter(stream=out), out

    def _setup_scaffold_files(self) -> None:
        """Write the minimal set of files that commit_scaffold stages."""
        (self.root / "templates" / "agents").mkdir(parents=True, exist_ok=True)
        (self.root / "templates" / "agents" / "developer.md").write_text("dev")
        (self.root / ".agents" / "skills").mkdir(parents=True, exist_ok=True)
        (self.root / ".agents" / "skills" / "skill.md").write_text("skill")
        (self.root / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
        (self.root / ".claude" / "skills" / "skill.md").write_text("skill")
        (self.root / ".claude" / "agents").mkdir(parents=True, exist_ok=True)
        (self.root / ".claude" / "agents" / "spec-reviewer.md").write_text("agent")
        (self.root / "specs").mkdir(parents=True, exist_ok=True)
        (self.root / "specs" / "HOWTO.md").write_text("howto")
        (self.root / ".takt").mkdir(parents=True, exist_ok=True)
        (self.root / ".takt" / "config.yaml").write_text("fake: true")
        (self.root / ".takt" / "assets-manifest.json").write_text('{"takt_version":"0.0.0","installed_at":"","assets":{}}')
        (self.root / ".gitignore").write_text("node_modules/\n")

    def test_happy_path_creates_commit(self):
        """Fresh git init + scaffold files → commit_scaffold creates a git commit."""
        self._setup_scaffold_files()
        console, _ = self._make_console()
        commit_scaffold(self.root, console)
        result = subprocess.run(
            ["git", "-C", str(self.root), "log", "--oneline"],
            capture_output=True, text=True, check=True,
        )
        self.assertIn("chore: takt init scaffold", result.stdout)

    def test_happy_path_commit_includes_gitkeep(self):
        """commit_scaffold creates .takt/beads/.gitkeep and includes it in the commit."""
        self._setup_scaffold_files()
        console, _ = self._make_console()
        commit_scaffold(self.root, console)
        result = subprocess.run(
            ["git", "-C", str(self.root), "show", "--stat", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        self.assertIn(".takt/beads/.gitkeep", result.stdout)

    def test_happy_path_commit_includes_templates(self):
        """The scaffold commit includes templates/ files."""
        self._setup_scaffold_files()
        console, _ = self._make_console()
        commit_scaffold(self.root, console)
        result = subprocess.run(
            ["git", "-C", str(self.root), "show", "--stat", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        self.assertIn("templates/", result.stdout)

    def test_idempotent_second_call_warns_not_raises(self):
        """Second commit_scaffold with no new changes warns and does not raise."""
        self._setup_scaffold_files()
        console, _ = self._make_console()
        commit_scaffold(self.root, console)
        # Nothing has changed; second call should warn, not raise.
        console2, out2 = self._make_console()
        commit_scaffold(self.root, console2)
        output = out2.getvalue()
        self.assertIn("git commit skipped", output)

    def test_idempotent_second_call_exits_zero(self):
        """commit_scaffold returns normally (no exception) on second call with nothing to commit."""
        self._setup_scaffold_files()
        console, _ = self._make_console()
        commit_scaffold(self.root, console)
        # Should not raise on second call.
        console2, _ = self._make_console()
        try:
            commit_scaffold(self.root, console2)
        except Exception as exc:  # pragma: no cover
            self.fail(f"commit_scaffold raised unexpectedly on idempotent call: {exc}")

    def test_non_git_directory_warns_not_raises(self):
        """commit_scaffold in a non-git directory warns and returns without raising."""
        non_git = self.root / "not_a_repo"
        non_git.mkdir()
        console, out = self._make_console()
        commit_scaffold(non_git, console)
        output = out.getvalue()
        self.assertIn("git add failed", output)

    def test_gitignore_does_not_contain_beads_entry(self):
        """update_gitignore must NOT add .takt/beads/ — beads must be tracked by git."""
        update_gitignore(self.root)
        content = (self.root / ".gitignore").read_text()
        self.assertNotIn(".takt/beads/", content)

    def test_worktree_contains_scaffold_files(self):
        """After the scaffold commit, a git worktree add sees the committed scaffold files."""
        self._setup_scaffold_files()
        console, _ = self._make_console()
        commit_scaffold(self.root, console)

        wt_path = Path(self._tmp.name + "_wt")
        try:
            add_result = subprocess.run(
                ["git", "-C", str(self.root), "worktree", "add", str(wt_path), "HEAD"],
                capture_output=True, text=True, check=False,
            )
            if add_result.returncode != 0:
                self.skipTest(f"git worktree add unavailable: {add_result.stderr.strip()}")
            self.assertTrue((wt_path / ".takt" / "beads" / ".gitkeep").is_file(),
                            ".takt/beads/.gitkeep missing in worktree")
            self.assertTrue((wt_path / "specs" / "HOWTO.md").is_file(),
                            "specs/HOWTO.md missing in worktree")
            self.assertTrue((wt_path / ".takt" / "config.yaml").is_file(),
                            ".takt/config.yaml missing in worktree")
            self.assertTrue((wt_path / "templates").is_dir(),
                            "templates/ missing in worktree")
        finally:
            subprocess.run(
                ["git", "-C", str(self.root), "worktree", "remove", "--force", str(wt_path)],
                capture_output=True, check=False,
            )
            if wt_path.exists():
                shutil.rmtree(wt_path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Scaffold memory bootstrap tests
# ---------------------------------------------------------------------------


class TestScaffoldProjectMemoryBootstrap(unittest.TestCase):
    """Verify scaffold_project() calls init_db() for memory bootstrap (idempotent)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _scaffold(self, overwrite: bool = False) -> io.StringIO:
        """Run scaffold_project with minimal patches; mock _download_model to skip network."""
        answers = _make_answers()
        out = io.StringIO()
        with patch("agent_takt.memory._download_model", return_value=None):
            scaffold_project(self.root, answers, overwrite=overwrite, stream_out=out)
        return out

    def test_scaffold_creates_memory_db(self):
        """scaffold_project must create .takt/memory/memory.db."""
        self._scaffold()
        memory_db = self.root / ".takt" / "memory" / "memory.db"
        self.assertTrue(memory_db.exists(), ".takt/memory/memory.db was not created by scaffold_project")

    def test_scaffold_memory_db_is_valid_sqlite(self):
        """The created memory.db must be a valid SQLite database with WAL mode."""
        import sqlite3
        self._scaffold()
        db = self.root / ".takt" / "memory" / "memory.db"
        conn = sqlite3.connect(str(db))
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual("wal", mode)
        finally:
            conn.close()

    def test_scaffold_second_run_does_not_error(self):
        """Running scaffold_project twice (re-init) must not raise (init_db is idempotent)."""
        self._scaffold()
        try:
            self._scaffold(overwrite=True)
        except Exception as exc:
            self.fail(f"Second scaffold_project raised unexpectedly: {exc}")

    def test_scaffold_second_run_preserves_memory_db(self):
        """Memory DB must still exist after a second scaffold_project run."""
        self._scaffold()
        self._scaffold(overwrite=True)
        memory_db = self.root / ".takt" / "memory" / "memory.db"
        self.assertTrue(memory_db.exists())

    def test_init_db_called_on_scaffold(self):
        """scaffold_project must call init_db with the correct path."""
        answers = _make_answers()
        out = io.StringIO()
        with patch("agent_takt.onboarding.scaffold.init_db") as mock_init, \
             patch("agent_takt.memory._download_model", return_value=None):
            scaffold_project(self.root, answers, stream_out=out)
        expected_path = self.root / ".takt" / "memory" / "memory.db"
        mock_init.assert_called_once_with(expected_path, model_cache_dir=None)


if __name__ == "__main__":
    unittest.main()
