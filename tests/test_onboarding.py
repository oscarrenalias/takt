"""Tests for src/agent_takt/onboarding.py.

Covers:
- resolve_memory_seed
- collect_init_answers (via injected streams)
- generate_config_yaml
- substitute_template_placeholders
- seed_memory_files
- update_gitignore
- create_specs_howto
- scaffold_project (high-level entry point)
- commit_scaffold
- merge_config_keys

Asset installation and copy helper tests live in test_onboarding_assets.py.
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
    _language_specific_known_issues,
    collect_init_answers,
    commit_scaffold,
    create_specs_howto,
    generate_config_yaml,
    resolve_memory_seed,
    scaffold_project,
    seed_memory_files,
    substitute_template_placeholders,
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
# resolve_memory_seed
# ---------------------------------------------------------------------------


class TestResolveMemorySeed(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fake_dir = Path(self._tmp.name)
        (self.fake_dir / "conventions.md").write_text("# Conventions")

    def tearDown(self):
        self._tmp.cleanup()

    def test_returns_path_for_existing_seed(self):
        with patch("agent_takt.onboarding.packaged_docs_memory_dir", return_value=self.fake_dir):
            p = resolve_memory_seed("conventions.md")
        self.assertEqual(p.name, "conventions.md")
        self.assertTrue(p.is_file())

    def test_raises_for_missing_seed(self):
        with patch("agent_takt.onboarding.packaged_docs_memory_dir", return_value=self.fake_dir):
            with self.assertRaises(FileNotFoundError):
                resolve_memory_seed("nonexistent.md")


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
        answers = self._run(["codex", "4", "TypeScript/Node.js", "npm test", "tsc --noEmit"])
        self.assertEqual(answers.runner, "codex")
        self.assertEqual(answers.max_workers, 4)
        self.assertEqual(answers.language, "TypeScript/Node.js")
        self.assertEqual(answers.test_command, "npm test")
        self.assertEqual(answers.build_check_command, "tsc --noEmit")

    def test_invalid_runner_then_valid(self):
        answers = self._run(["invalid", "claude", "", "", "", ""])
        self.assertEqual(answers.runner, "claude")

    def test_invalid_max_workers_then_valid(self):
        answers = self._run(["", "abc", "0", "2", "", "", ""])
        self.assertEqual(answers.max_workers, 2)

    def test_negative_max_workers_rejected(self):
        answers = self._run(["", "-1", "3", "", "", ""])
        self.assertEqual(answers.max_workers, 3)


# ---------------------------------------------------------------------------
# generate_config_yaml
# ---------------------------------------------------------------------------


class TestGenerateConfigYaml(unittest.TestCase):
    def test_contains_runner(self):
        answers = _make_answers(runner="codex", test_command="go test ./...", max_workers=3)
        yaml_text = generate_config_yaml(answers)
        self.assertIn("default_runner: codex", yaml_text)
        self.assertIn("test_command: go test ./...", yaml_text)
        # max_workers is a CLI flag, not a config key — it must not appear in the YAML
        self.assertNotIn("max_workers", yaml_text)

    def test_runner_substituted_claude(self):
        yaml_text = generate_config_yaml(_make_answers(runner="claude"))
        self.assertIn("default_runner: claude", yaml_text)

    def test_runner_substituted_codex(self):
        yaml_text = generate_config_yaml(_make_answers(runner="codex"))
        self.assertIn("default_runner: codex", yaml_text)

    def test_test_command_substituted(self):
        yaml_text = generate_config_yaml(_make_answers(test_command="npm test"))
        self.assertIn("test_command: npm test", yaml_text)

    def test_contains_allowed_tools_default(self):
        yaml_text = generate_config_yaml(_make_answers())
        self.assertIn("allowed_tools_default:", yaml_text)
        for tool in ("Edit", "Write", "Read", "Bash", "Glob", "Grep"):
            self.assertIn(f"- {tool}", yaml_text)

    def test_contains_allowed_tools_by_agent(self):
        yaml_text = generate_config_yaml(_make_answers())
        self.assertIn("allowed_tools_by_agent:", yaml_text)
        self.assertIn("developer:", yaml_text)
        self.assertIn("- Agent", yaml_text)
        self.assertIn("- TaskCreate", yaml_text)

    def test_contains_claude_block(self):
        yaml_text = generate_config_yaml(_make_answers())
        self.assertIn("claude:", yaml_text)
        self.assertIn("binary: claude", yaml_text)

    def test_contains_codex_block(self):
        yaml_text = generate_config_yaml(_make_answers())
        self.assertIn("codex:", yaml_text)
        self.assertIn("binary: codex", yaml_text)

    def test_contains_model_default(self):
        yaml_text = generate_config_yaml(_make_answers())
        self.assertIn("model_default: claude-sonnet-4-6", yaml_text)

    def test_contains_timeout_seconds(self):
        yaml_text = generate_config_yaml(_make_answers())
        self.assertIn("timeout_seconds: 900", yaml_text)

    def test_contains_skills_dirs(self):
        yaml_text = generate_config_yaml(_make_answers())
        self.assertIn("skills_dir: .agents", yaml_text)
        self.assertIn("skills_dir: .claude", yaml_text)

    def test_scheduler_preserved(self):
        yaml_text = generate_config_yaml(_make_answers())
        self.assertIn("scheduler:", yaml_text)
        self.assertIn("transient_block_patterns:", yaml_text)
        self.assertIn("lease_timeout_minutes:", yaml_text)

    def test_model_by_agent_preserved(self):
        yaml_text = generate_config_yaml(_make_answers())
        self.assertIn("model_by_agent:", yaml_text)
        self.assertIn("developer: claude-sonnet-4-6", yaml_text)


# ---------------------------------------------------------------------------
# substitute_template_placeholders
# ---------------------------------------------------------------------------


class TestSubstituteTemplatePlaceholders(unittest.TestCase):
    def test_replaces_all_placeholders(self):
        answers = _make_answers(language="Go", test_command="go test ./...", build_check_command="go build ./...")
        text = "lang={{LANGUAGE}} test={{TEST_COMMAND}} build={{BUILD_CHECK_COMMAND}}"
        result = substitute_template_placeholders(text, answers)
        self.assertEqual(result, "lang=Go test=go test ./... build=go build ./...")

    def test_no_placeholders_unchanged(self):
        answers = _make_answers()
        text = "nothing to replace here"
        self.assertEqual(substitute_template_placeholders(text, answers), text)

    def test_multiple_occurrences_replaced(self):
        answers = _make_answers(language="Python")
        text = "{{LANGUAGE}} and {{LANGUAGE}}"
        result = substitute_template_placeholders(text, answers)
        self.assertEqual(result, "Python and Python")


# ---------------------------------------------------------------------------
# seed_memory_files
# ---------------------------------------------------------------------------


class TestSeedMemoryFiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_creates_known_issues_and_conventions(self):
        answers = _make_answers()
        written = seed_memory_files(self.root, answers)
        names = {p.name for p in written}
        self.assertIn("known-issues.md", names)
        self.assertIn("conventions.md", names)

    def test_skips_existing_without_overwrite(self):
        answers = _make_answers()
        memory_dir = self.root / "docs" / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "conventions.md").write_text("old")
        written = seed_memory_files(self.root, answers, overwrite=False)
        self.assertNotIn(memory_dir / "conventions.md", written)
        self.assertEqual((memory_dir / "conventions.md").read_text(), "old")

    def test_overwrites_when_flag_set(self):
        answers = _make_answers()
        memory_dir = self.root / "docs" / "memory"
        memory_dir.mkdir(parents=True)
        (memory_dir / "conventions.md").write_text("old")
        written = seed_memory_files(self.root, answers, overwrite=True)
        self.assertIn(memory_dir / "conventions.md", written)
        self.assertNotEqual((memory_dir / "conventions.md").read_text(), "old")

    def test_typescript_specific_content(self):
        answers = _make_answers(language="TypeScript/Node.js")
        written = seed_memory_files(self.root, answers)
        ki_path = self.root / "docs" / "memory" / "known-issues.md"
        content = ki_path.read_text()
        self.assertIn("TypeScript", content)

    def test_go_specific_content(self):
        answers = _make_answers(language="Go")
        written = seed_memory_files(self.root, answers)
        ki_path = self.root / "docs" / "memory" / "known-issues.md"
        content = ki_path.read_text()
        self.assertIn("Go", content)

    def test_python_no_language_specific_section(self):
        answers = _make_answers(language="Python")
        content = _language_specific_known_issues("Python")
        self.assertEqual(content, "")


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
            patch("agent_takt.onboarding.packaged_templates_dir", return_value=fake_templates),
            patch("agent_takt.onboarding.packaged_agents_skills_dir", return_value=fake_agents),
            patch("agent_takt.onboarding.packaged_claude_skills_dir", return_value=fake_claude),
            patch("agent_takt.onboarding.packaged_default_config", return_value=fake_config),
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

        # memory
        self.assertTrue((self.root / "docs" / "memory" / "known-issues.md").is_file())
        self.assertTrue((self.root / "docs" / "memory" / "conventions.md").is_file())

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
            patch("agent_takt.onboarding.packaged_templates_dir", return_value=fake_templates),
            patch("agent_takt.onboarding.packaged_agents_skills_dir", return_value=fake_agents),
            patch("agent_takt.onboarding.packaged_claude_skills_dir", return_value=fake_claude),
            patch("agent_takt.onboarding.packaged_default_config", return_value=fake_config),
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
        (self.root / "docs" / "memory").mkdir(parents=True, exist_ok=True)
        (self.root / "docs" / "memory" / "conventions.md").write_text("conventions")
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
# merge_config_keys
# ---------------------------------------------------------------------------


class TestMergeConfigKeys(unittest.TestCase):
    """Tests for merge_config_keys() — recursive dict merge that preserves user values."""

    def _merge(self, user: dict, bundled: dict):
        from agent_takt.onboarding import merge_config_keys
        return merge_config_keys(user, bundled)

    def test_top_level_missing_key_inserted(self):
        """A missing top-level key from bundled is inserted into user config."""
        user = {"a": 1}
        bundled = {"a": 99, "b": 2}
        merged, added = self._merge(user, bundled)
        self.assertEqual(merged["b"], 2)
        self.assertIn("b", added)

    def test_existing_top_level_key_not_overwritten(self):
        """An existing top-level user key is never overwritten."""
        user = {"a": 1}
        bundled = {"a": 99}
        merged, added = self._merge(user, bundled)
        self.assertEqual(merged["a"], 1)
        self.assertEqual(added, [])

    def test_recursive_nested_missing_key_added(self):
        """A missing key inside a nested dict is inserted with its full dotted path."""
        user = {"scheduler": {"lease_timeout_minutes": 30}}
        bundled = {"scheduler": {"lease_timeout_minutes": 60, "max_retries": 5}}
        merged, added = self._merge(user, bundled)
        self.assertEqual(merged["scheduler"]["max_retries"], 5)
        self.assertIn("scheduler.max_retries", added)

    def test_recursive_nested_existing_key_not_overwritten(self):
        """An existing nested user key is preserved, not overwritten by bundled value."""
        user = {"scheduler": {"lease_timeout_minutes": 30}}
        bundled = {"scheduler": {"lease_timeout_minutes": 60}}
        merged, added = self._merge(user, bundled)
        self.assertEqual(merged["scheduler"]["lease_timeout_minutes"], 30)
        self.assertEqual(added, [])

    def test_non_dict_values_user_wins(self):
        """When both sides have a non-dict value at the same key, user value wins."""
        user = {"timeout": 100}
        bundled = {"timeout": 900}
        merged, added = self._merge(user, bundled)
        self.assertEqual(merged["timeout"], 100)
        self.assertEqual(added, [])

    def test_empty_user_config_entire_bundled_inserted(self):
        """When user config is empty, every bundled top-level key is inserted.

        Nested dicts are inserted as a whole because they are absent from
        user_config — the function only recurses when *both* sides have a
        dict at the same key. When the user key is absent, the entire bundled
        subtree is stored and only the top-level dotted key is recorded.
        """
        user: dict = {}
        bundled = {"a": 1, "b": {"c": 2}}
        merged, added = self._merge(user, bundled)
        self.assertEqual(merged["a"], 1)
        self.assertEqual(merged["b"]["c"], 2)
        self.assertIn("a", added)
        # "b" is recorded (the key that was absent in user_config), not "b.c"
        self.assertIn("b", added)

    def test_empty_bundled_config_no_keys_added(self):
        """When bundled config is empty, no keys are added and the returned list is empty."""
        user = {"x": 1}
        bundled: dict = {}
        merged, added = self._merge(user, bundled)
        self.assertEqual(merged, {"x": 1})
        self.assertEqual(added, [])

    def test_deeply_nested_merge(self):
        """Merge inserts a missing leaf key at 3+ levels of nesting."""
        user = {"a": {"b": {"c": 1}}}
        bundled = {"a": {"b": {"c": 99, "d": 2}}}
        merged, added = self._merge(user, bundled)
        self.assertEqual(merged["a"]["b"]["c"], 1)  # preserved
        self.assertEqual(merged["a"]["b"]["d"], 2)   # inserted
        self.assertIn("a.b.d", added)


if __name__ == "__main__":
    unittest.main()
