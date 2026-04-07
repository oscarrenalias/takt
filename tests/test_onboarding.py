"""Tests for src/agent_takt/onboarding.py.

Covers:
- copy_asset_file / copy_asset_dir (low-level helpers)
- install_templates, install_agents_skills, install_claude_skills, install_default_config
- resolve_memory_seed
- collect_init_answers (via injected streams)
- generate_config_yaml
- substitute_template_placeholders
- install_templates_with_substitution
- seed_memory_files
- update_gitignore
- create_specs_howto
- scaffold_project (high-level entry point)
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    copy_asset_dir,
    copy_asset_file,
    create_specs_howto,
    generate_config_yaml,
    install_agents_skills,
    install_claude_skills,
    install_default_config,
    install_templates,
    install_templates_with_substitution,
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
# copy_asset_file
# ---------------------------------------------------------------------------


class TestCopyAssetFile(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_copies_file(self):
        src = self.tmp / "src.txt"
        src.write_text("hello")
        dest = self.tmp / "sub" / "dest.txt"
        copy_asset_file(src, dest)
        self.assertEqual(dest.read_text(), "hello")

    def test_skips_existing_without_overwrite(self):
        src = self.tmp / "src.txt"
        src.write_text("new")
        dest = self.tmp / "dest.txt"
        dest.write_text("old")
        copy_asset_file(src, dest, overwrite=False)
        self.assertEqual(dest.read_text(), "old")

    def test_overwrites_when_flag_set(self):
        src = self.tmp / "src.txt"
        src.write_text("new")
        dest = self.tmp / "dest.txt"
        dest.write_text("old")
        copy_asset_file(src, dest, overwrite=True)
        self.assertEqual(dest.read_text(), "new")

    def test_raises_if_src_missing(self):
        with self.assertRaises(FileNotFoundError):
            copy_asset_file(self.tmp / "nonexistent.txt", self.tmp / "dest.txt")

    def test_creates_parent_dirs(self):
        src = self.tmp / "src.txt"
        src.write_text("x")
        dest = self.tmp / "a" / "b" / "c" / "dest.txt"
        copy_asset_file(src, dest)
        self.assertTrue(dest.is_file())


# ---------------------------------------------------------------------------
# copy_asset_dir
# ---------------------------------------------------------------------------


class TestCopyAssetDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_src(self) -> Path:
        src = self.tmp / "src_dir"
        (src / "sub").mkdir(parents=True)
        (src / "a.txt").write_text("a")
        (src / "sub" / "b.txt").write_text("b")
        return src

    def test_copies_recursively(self):
        src = self._make_src()
        dest = self.tmp / "dest_dir"
        copy_asset_dir(src, dest)
        self.assertEqual((dest / "a.txt").read_text(), "a")
        self.assertEqual((dest / "sub" / "b.txt").read_text(), "b")

    def test_skips_existing_without_overwrite(self):
        src = self._make_src()
        dest = self.tmp / "dest_dir"
        dest.mkdir()
        (dest / "a.txt").write_text("old")
        copy_asset_dir(src, dest, overwrite=False)
        self.assertEqual((dest / "a.txt").read_text(), "old")

    def test_overwrites_when_flag_set(self):
        src = self._make_src()
        dest = self.tmp / "dest_dir"
        dest.mkdir()
        (dest / "a.txt").write_text("old")
        copy_asset_dir(src, dest, overwrite=True)
        self.assertEqual((dest / "a.txt").read_text(), "a")

    def test_raises_if_src_missing(self):
        with self.assertRaises(FileNotFoundError):
            copy_asset_dir(self.tmp / "no_dir", self.tmp / "dest")

    def test_raises_if_src_is_file_not_dir(self):
        f = self.tmp / "file.txt"
        f.write_text("x")
        with self.assertRaises(FileNotFoundError):
            copy_asset_dir(f, self.tmp / "dest")


# ---------------------------------------------------------------------------
# install_templates
# ---------------------------------------------------------------------------


class TestInstallTemplates(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_fake_templates_dir(self) -> Path:
        d = self.root / "_fake_templates"
        d.mkdir()
        (d / "developer.md").write_text("dev template")
        (d / "tester.md").write_text("tester template")
        return d

    def test_installs_templates(self):
        fake = self._make_fake_templates_dir()
        with patch("agent_takt.onboarding.packaged_templates_dir", return_value=fake):
            written = install_templates(self.root)
        dest = self.root / "templates" / "agents"
        self.assertIn(dest / "developer.md", written)
        self.assertIn(dest / "tester.md", written)
        self.assertEqual((dest / "developer.md").read_text(), "dev template")

    def test_skips_existing_without_overwrite(self):
        fake = self._make_fake_templates_dir()
        dest = self.root / "templates" / "agents"
        dest.mkdir(parents=True)
        (dest / "developer.md").write_text("original")
        with patch("agent_takt.onboarding.packaged_templates_dir", return_value=fake):
            written = install_templates(self.root, overwrite=False)
        self.assertNotIn(dest / "developer.md", written)
        self.assertEqual((dest / "developer.md").read_text(), "original")

    def test_overwrites_when_flag_set(self):
        fake = self._make_fake_templates_dir()
        dest = self.root / "templates" / "agents"
        dest.mkdir(parents=True)
        (dest / "developer.md").write_text("original")
        with patch("agent_takt.onboarding.packaged_templates_dir", return_value=fake):
            written = install_templates(self.root, overwrite=True)
        self.assertIn(dest / "developer.md", written)
        self.assertEqual((dest / "developer.md").read_text(), "dev template")


# ---------------------------------------------------------------------------
# install_default_config
# ---------------------------------------------------------------------------


class TestInstallDefaultConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_writes_config(self):
        fake_src = self.root / "_fake_config.yaml"
        fake_src.write_text("fake: config")
        with patch("agent_takt.onboarding.packaged_default_config", return_value=fake_src):
            dest = install_default_config(self.root)
        self.assertEqual(dest, self.root / ".takt" / "config.yaml")
        self.assertEqual(dest.read_text(), "fake: config")

    def test_skips_existing_without_overwrite(self):
        fake_src = self.root / "_fake_config.yaml"
        fake_src.write_text("new")
        config_path = self.root / ".takt" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("old")
        with patch("agent_takt.onboarding.packaged_default_config", return_value=fake_src):
            install_default_config(self.root, overwrite=False)
        self.assertEqual(config_path.read_text(), "old")


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
# install_templates_with_substitution
# ---------------------------------------------------------------------------


class TestInstallTemplatesWithSubstitution(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_fake_templates_dir(self) -> Path:
        d = self.root / "_fake_templates"
        d.mkdir()
        (d / "developer.md").write_text("lang={{LANGUAGE}} test={{TEST_COMMAND}}")
        return d

    def test_substitutes_placeholders(self):
        fake = self._make_fake_templates_dir()
        answers = _make_answers(language="Go", test_command="go test ./...")
        with patch("agent_takt.onboarding.packaged_templates_dir", return_value=fake):
            written = install_templates_with_substitution(self.root, answers)
        dest = self.root / "templates" / "agents" / "developer.md"
        self.assertIn(dest, written)
        self.assertEqual(dest.read_text(), "lang=Go test=go test ./...")

    def test_skips_existing_without_overwrite(self):
        fake = self._make_fake_templates_dir()
        dest = self.root / "templates" / "agents"
        dest.mkdir(parents=True)
        (dest / "developer.md").write_text("original")
        answers = _make_answers()
        with patch("agent_takt.onboarding.packaged_templates_dir", return_value=fake):
            written = install_templates_with_substitution(self.root, answers, overwrite=False)
        self.assertEqual(written, [])
        self.assertEqual((dest / "developer.md").read_text(), "original")


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
# install_agents_skills — spec-management skill bundling
# ---------------------------------------------------------------------------


class TestInstallAgentsSkillsSpecManagement(unittest.TestCase):
    """Verify that the spec-management task skill is bundled and installed correctly."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_spec_management_files_installed(self):
        """install_agents_skills() copies SKILL.md, spec.py, and agents/openai.yaml."""
        install_agents_skills(self.root)
        skill_dir = self.root / ".agents" / "skills" / "task" / "spec-management"
        self.assertTrue((skill_dir / "SKILL.md").is_file(), "SKILL.md not installed")
        self.assertTrue((skill_dir / "spec.py").is_file(), "spec.py not installed")
        self.assertTrue((skill_dir / "agents" / "openai.yaml").is_file(), "agents/openai.yaml not installed")

    def test_spec_py_is_executable(self):
        """spec.py installed by install_agents_skills() retains executable bit."""
        install_agents_skills(self.root)
        spec_py = self.root / ".agents" / "skills" / "task" / "spec-management" / "spec.py"
        self.assertTrue(spec_py.is_file())
        self.assertTrue(os.access(spec_py, os.X_OK), "spec.py is not executable")

    def test_spec_py_init_subcommand(self):
        """spec.py init runs without error and creates specs/ structure."""
        import subprocess
        install_agents_skills(self.root)
        spec_py = self.root / ".agents" / "skills" / "task" / "spec-management" / "spec.py"
        result = subprocess.run(
            [sys.executable, str(spec_py), "init"],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"spec.py init failed: {result.stderr}")
        self.assertTrue((self.root / "specs").is_dir(), "specs/ directory not created by init")

    def test_spec_py_list_subcommand(self):
        """spec.py list runs without error (returns empty list on fresh init)."""
        import subprocess
        install_agents_skills(self.root)
        spec_py = self.root / ".agents" / "skills" / "task" / "spec-management" / "spec.py"
        # initialise first so specs/ dir exists
        subprocess.run([sys.executable, str(spec_py), "init"], cwd=str(self.root), capture_output=True)
        result = subprocess.run(
            [sys.executable, str(spec_py), "list"],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"spec.py list failed: {result.stderr}")

    def test_spec_py_create_subcommand(self):
        """spec.py create produces a spec file with expected frontmatter."""
        import subprocess
        install_agents_skills(self.root)
        spec_py = self.root / ".agents" / "skills" / "task" / "spec-management" / "spec.py"
        subprocess.run([sys.executable, str(spec_py), "init"], cwd=str(self.root), capture_output=True)
        result = subprocess.run(
            [sys.executable, str(spec_py), "create", "Test spec"],
            cwd=str(self.root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"spec.py create failed: {result.stderr}")
        # A spec file should exist in specs/drafts/
        drafts = list((self.root / "specs" / "drafts").glob("*.md"))
        self.assertTrue(len(drafts) >= 1, "No spec file created in specs/drafts/")
        content = drafts[0].read_text()
        self.assertIn("name:", content)


# ---------------------------------------------------------------------------
# scaffold_project — spec-management skill end-to-end
# ---------------------------------------------------------------------------

class TestScaffoldProjectSpecManagementSkill(unittest.TestCase):
    """Verify scaffold_project installs the spec-management skill using real package data."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _fake_templates(self) -> Path:
        d = self.root / "_fake_templates"
        d.mkdir()
        (d / "developer.md").write_text("lang={{LANGUAGE}}")
        return d

    def _fake_config(self) -> Path:
        f = self.root / "_fake_config.yaml"
        f.write_text("fake: true")
        return f

    def _run_scaffold(self, runner: str) -> None:
        answers = _make_answers(runner=runner)
        out = io.StringIO()
        fake_templates = self._fake_templates()
        fake_config = self._fake_config()
        with (
            patch("agent_takt.onboarding.packaged_templates_dir", return_value=fake_templates),
            patch("agent_takt.onboarding.packaged_default_config", return_value=fake_config),
        ):
            scaffold_project(self.root, answers, stream_out=out)

    def test_scaffold_claude_runner_installs_spec_management(self):
        """scaffold_project with claude runner installs task/spec-management into .agents/skills."""
        self._run_scaffold("claude")
        skill_dir = self.root / ".agents" / "skills" / "task" / "spec-management"
        self.assertTrue((skill_dir / "SKILL.md").is_file())
        self.assertTrue((skill_dir / "spec.py").is_file())
        self.assertTrue((skill_dir / "agents" / "openai.yaml").is_file())

    def test_scaffold_codex_runner_installs_spec_management(self):
        """scaffold_project with codex runner installs task/spec-management into .agents/skills."""
        self._run_scaffold("codex")
        skill_dir = self.root / ".agents" / "skills" / "task" / "spec-management"
        self.assertTrue((skill_dir / "SKILL.md").is_file())
        self.assertTrue((skill_dir / "spec.py").is_file())
        self.assertTrue((skill_dir / "agents" / "openai.yaml").is_file())


# ---------------------------------------------------------------------------
# install_claude_skills — takt operator skill
# ---------------------------------------------------------------------------


class TestInstallClaudeSkillsTaktSkill(unittest.TestCase):
    """Verify that the takt operator skill is bundled and installed correctly."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_takt_skill_file_installed(self):
        """install_claude_skills() copies takt/SKILL.md to .claude/skills/takt/SKILL.md."""
        install_claude_skills(self.root)
        skill_path = self.root / ".claude" / "skills" / "takt" / "SKILL.md"
        self.assertTrue(skill_path.is_file(), "takt/SKILL.md not installed by install_claude_skills()")

    def test_takt_skill_has_valid_yaml_frontmatter(self):
        """The installed takt/SKILL.md has parseable YAML frontmatter."""
        install_claude_skills(self.root)
        skill_path = self.root / ".claude" / "skills" / "takt" / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        self.assertTrue(content.startswith("---"), "SKILL.md does not start with YAML frontmatter delimiter")
        end_idx = content.index("---", 3)
        frontmatter_text = content[3:end_idx].strip()
        import yaml
        parsed = yaml.safe_load(frontmatter_text)
        self.assertIsInstance(parsed, dict, "Frontmatter did not parse as a dict")
        self.assertIn("name", parsed, "Frontmatter missing 'name' field")
        self.assertIn("description", parsed, "Frontmatter missing 'description' field")

    def test_takt_skill_name_is_takt(self):
        """The takt skill's frontmatter name field is 'takt'."""
        install_claude_skills(self.root)
        skill_path = self.root / ".claude" / "skills" / "takt" / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        end_idx = content.index("---", 3)
        import yaml
        parsed = yaml.safe_load(content[3:end_idx].strip())
        self.assertEqual("takt", parsed["name"])

    def test_takt_skill_content_mentions_bead(self):
        """The installed takt/SKILL.md references bead concepts."""
        install_claude_skills(self.root)
        skill_path = self.root / ".claude" / "skills" / "takt" / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        self.assertIn("bead", content.lower(), "SKILL.md does not mention beads")


# ---------------------------------------------------------------------------
# scaffold_project — takt operator skill end-to-end
# ---------------------------------------------------------------------------


class TestScaffoldProjectTaktSkill(unittest.TestCase):
    """Verify scaffold_project installs the takt operator skill using real package data."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _fake_templates(self) -> Path:
        d = self.root / "_fake_templates"
        d.mkdir()
        (d / "developer.md").write_text("lang={{LANGUAGE}}")
        return d

    def _fake_config(self) -> Path:
        f = self.root / "_fake_config.yaml"
        f.write_text("fake: true")
        return f

    def test_scaffold_installs_takt_skill(self):
        """scaffold_project() installs takt/SKILL.md into .claude/skills/takt/."""
        answers = _make_answers(runner="claude")
        out = io.StringIO()
        fake_templates = self._fake_templates()
        fake_config = self._fake_config()
        with (
            patch("agent_takt.onboarding.packaged_templates_dir", return_value=fake_templates),
            patch("agent_takt.onboarding.packaged_default_config", return_value=fake_config),
        ):
            scaffold_project(self.root, answers, stream_out=out)
        skill_path = self.root / ".claude" / "skills" / "takt" / "SKILL.md"
        self.assertTrue(skill_path.is_file(), "takt/SKILL.md not created by scaffold_project()")


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
# write_assets_manifest / read_assets_manifest
# ---------------------------------------------------------------------------


class TestWriteAssetsManifest(unittest.TestCase):
    """Tests for write_assets_manifest() schema and field correctness."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_file(self, rel: str, content: str = "hello") -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_json_structure_keys(self):
        """Manifest contains takt_version, installed_at, and assets at the top level."""
        from agent_takt.onboarding import write_assets_manifest

        f = self._make_file(".agents/skills/core/base-orchestrator/SKILL.md")
        manifest_path = write_assets_manifest(self.root, [f])
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn("takt_version", data)
        self.assertIn("installed_at", data)
        self.assertIn("assets", data)
        self.assertIsInstance(data["assets"], dict)

    def test_sha256_matches_file_contents(self):
        """SHA-256 recorded in the manifest matches the actual file content."""
        import hashlib
        from agent_takt.onboarding import write_assets_manifest

        content = "some content for hashing"
        f = self._make_file(".agents/skills/core/base-orchestrator/SKILL.md", content)
        manifest_path = write_assets_manifest(self.root, [f])
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_sha = hashlib.sha256(content.encode()).hexdigest()
        recorded_sha = data["assets"][".agents/skills/core/base-orchestrator/SKILL.md"]["sha256"]
        self.assertEqual(expected_sha, recorded_sha)

    def test_templates_marked_user_owned(self):
        """templates/agents/ files are recorded with user_owned: true."""
        from agent_takt.onboarding import write_assets_manifest

        f = self._make_file("templates/agents/developer.md", "template content")
        manifest_path = write_assets_manifest(self.root, [f])
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = data["assets"]["templates/agents/developer.md"]
        self.assertTrue(entry["user_owned"])
        self.assertEqual("bundled", entry["source"])

    def test_agents_skills_not_user_owned(self):
        """.agents/skills/ files are recorded with user_owned: false."""
        from agent_takt.onboarding import write_assets_manifest

        f = self._make_file(".agents/skills/core/base-orchestrator/SKILL.md")
        manifest_path = write_assets_manifest(self.root, [f])
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = data["assets"][".agents/skills/core/base-orchestrator/SKILL.md"]
        self.assertFalse(entry["user_owned"])

    def test_excludes_docs_memory_files(self):
        """Files under docs/memory/ are excluded from the manifest even if passed in."""
        from agent_takt.onboarding import write_assets_manifest

        skill = self._make_file(".agents/skills/core/base-orchestrator/SKILL.md")
        mem = self._make_file("docs/memory/conventions.md")
        manifest_path = write_assets_manifest(self.root, [skill, mem])
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn(".agents/skills/core/base-orchestrator/SKILL.md", data["assets"])
        self.assertNotIn("docs/memory/conventions.md", data["assets"])

    def test_manifest_path_resolves_correctly(self):
        """_MANIFEST_FILENAME resolves to .takt/assets-manifest.json relative to project root."""
        from agent_takt.onboarding import _MANIFEST_FILENAME, write_assets_manifest

        f = self._make_file(".agents/skills/core/base-orchestrator/SKILL.md")
        manifest_path = write_assets_manifest(self.root, [f])
        self.assertEqual(manifest_path, self.root / ".takt" / "assets-manifest.json")
        self.assertEqual(_MANIFEST_FILENAME, ".takt/assets-manifest.json")

    def test_config_yaml_tracked(self):
        """.takt/config.yaml is included when passed in installed_files."""
        from agent_takt.onboarding import write_assets_manifest

        cfg = self._make_file(".takt/config.yaml", "fake: true")
        manifest_path = write_assets_manifest(self.root, [cfg])
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn(".takt/config.yaml", data["assets"])


class TestReadAssetsManifest(unittest.TestCase):
    """Tests for read_assets_manifest() error handling and round-trip."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_absent_file_returns_empty_structure(self):
        """When .takt/assets-manifest.json does not exist, return empty manifest."""
        from agent_takt.onboarding import read_assets_manifest

        result = read_assets_manifest(self.root)
        self.assertEqual(result, {"takt_version": "", "installed_at": "", "assets": {}})

    def test_malformed_json_returns_empty_structure(self):
        """When the manifest contains invalid JSON, return empty manifest without raising."""
        from agent_takt.onboarding import read_assets_manifest

        manifest_path = self.root / ".takt" / "assets-manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text("{not valid json!!", encoding="utf-8")
        result = read_assets_manifest(self.root)
        self.assertEqual(result, {"takt_version": "", "installed_at": "", "assets": {}})

    def test_round_trip_write_then_read(self):
        """write_assets_manifest followed by read_assets_manifest returns equivalent data."""
        from agent_takt.onboarding import read_assets_manifest, write_assets_manifest

        f = self.root / ".agents" / "skills" / "core" / "base-orchestrator" / "SKILL.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("skill content")
        write_assets_manifest(self.root, [f])
        result = read_assets_manifest(self.root)
        self.assertIn(".agents/skills/core/base-orchestrator/SKILL.md", result["assets"])
        self.assertNotEqual("", result["takt_version"])
        self.assertNotEqual("", result["installed_at"])


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


# ---------------------------------------------------------------------------
# evaluate_upgrade_actions — all 8 action types
# ---------------------------------------------------------------------------


class TestEvaluateUpgradeActions(unittest.TestCase):
    """Tests for evaluate_upgrade_actions() covering all 8 AssetDecision action types."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_file(self, rel: str, content: str = "content") -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def _sha256(self, content: str) -> str:
        import hashlib
        return hashlib.sha256(content.encode()).hexdigest()

    def _bundled_entry(self, rel: str, sha: str, *, user_owned: bool = False) -> dict:
        return {"sha256": sha, "source": "bundled", "user_owned": user_owned}

    def test_empty_manifest_all_new(self):
        """With an empty manifest, all bundled entries should be action=new."""
        from agent_takt.onboarding import evaluate_upgrade_actions

        manifest = {"takt_version": "", "installed_at": "", "assets": {}}
        decisions = evaluate_upgrade_actions(self.root, manifest)
        actions = {d.action for d in decisions}
        # There may be user_added (disk files) but no bundled entries are in the manifest
        # so all bundled files must be 'new'
        bundled_decisions = [d for d in decisions if d.action != "user_added"]
        self.assertTrue(all(d.action == "new" for d in bundled_decisions),
                        f"Expected all bundled to be 'new', got: {[d.action for d in bundled_decisions]}")

    def test_unchanged_action(self):
        """disk sha == manifest sha == bundled sha → action=unchanged."""
        from agent_takt.onboarding import evaluate_upgrade_actions

        content = "unchanged content"
        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        disk_file = self._make_file(rel, content)
        sha = self._sha256(content)

        with patch("agent_takt.onboarding._compute_bundled_catalog",
                   return_value={rel: disk_file}):
            manifest = {"takt_version": "", "installed_at": "", "assets": {
                rel: self._bundled_entry(rel, sha)
            }}
            decisions = evaluate_upgrade_actions(self.root, manifest)

        d = next((x for x in decisions if x.rel_path == rel), None)
        self.assertIsNotNone(d)
        self.assertEqual(d.action, "unchanged")

    def test_update_action(self):
        """disk sha == manifest sha but bundled sha differs → action=update."""
        from agent_takt.onboarding import evaluate_upgrade_actions
        import tempfile

        disk_content = "old content"
        bundled_content = "new bundled content"
        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        disk_file = self._make_file(rel, disk_content)
        disk_sha = self._sha256(disk_content)

        # Create a separate temp file representing the bundled version
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as bf:
            bf.write(bundled_content)
            bundled_path = Path(bf.name)

        try:
            with patch("agent_takt.onboarding._compute_bundled_catalog",
                       return_value={rel: bundled_path}):
                manifest = {"takt_version": "", "installed_at": "", "assets": {
                    rel: self._bundled_entry(rel, disk_sha)
                }}
                decisions = evaluate_upgrade_actions(self.root, manifest)
        finally:
            bundled_path.unlink(missing_ok=True)

        d = next((x for x in decisions if x.rel_path == rel), None)
        self.assertIsNotNone(d)
        self.assertEqual(d.action, "update")

    def test_skipped_modified_action(self):
        """disk sha != manifest sha → action=skipped_modified."""
        from agent_takt.onboarding import evaluate_upgrade_actions

        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        disk_file = self._make_file(rel, "user modified content")
        original_sha = self._sha256("original content from install")

        with patch("agent_takt.onboarding._compute_bundled_catalog",
                   return_value={rel: disk_file}):
            manifest = {"takt_version": "", "installed_at": "", "assets": {
                rel: self._bundled_entry(rel, original_sha)
            }}
            decisions = evaluate_upgrade_actions(self.root, manifest)

        d = next((x for x in decisions if x.rel_path == rel), None)
        self.assertIsNotNone(d)
        self.assertEqual(d.action, "skipped_modified")

    def test_skipped_user_owned_action(self):
        """user_owned: true in manifest → action=skipped_user_owned."""
        from agent_takt.onboarding import evaluate_upgrade_actions

        content = "template content"
        rel = "templates/agents/developer.md"
        disk_file = self._make_file(rel, content)
        sha = self._sha256(content)

        with patch("agent_takt.onboarding._compute_bundled_catalog",
                   return_value={rel: disk_file}):
            manifest = {"takt_version": "", "installed_at": "", "assets": {
                rel: {"sha256": sha, "source": "bundled", "user_owned": True}
            }}
            decisions = evaluate_upgrade_actions(self.root, manifest)

        d = next((x for x in decisions if x.rel_path == rel), None)
        self.assertIsNotNone(d)
        self.assertEqual(d.action, "skipped_user_owned")

    def test_restored_action(self):
        """In manifest + bundle but missing from disk → action=restored."""
        from agent_takt.onboarding import evaluate_upgrade_actions
        import tempfile

        content = "bundled content"
        rel = ".agents/skills/core/base-orchestrator/SKILL.md"
        # Do NOT create the disk file — it's missing
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as bf:
            bf.write(content)
            bundled_path = Path(bf.name)

        try:
            with patch("agent_takt.onboarding._compute_bundled_catalog",
                       return_value={rel: bundled_path}):
                manifest = {"takt_version": "", "installed_at": "", "assets": {
                    rel: self._bundled_entry(rel, self._sha256(content))
                }}
                decisions = evaluate_upgrade_actions(self.root, manifest)
        finally:
            bundled_path.unlink(missing_ok=True)

        d = next((x for x in decisions if x.rel_path == rel), None)
        self.assertIsNotNone(d)
        self.assertEqual(d.action, "restored")

    def test_disabled_action(self):
        """In manifest, NOT in bundle → action=disabled."""
        from agent_takt.onboarding import evaluate_upgrade_actions

        rel = ".agents/skills/old/deprecated/SKILL.md"
        disk_file = self._make_file(rel, "old skill")
        sha = self._sha256("old skill")

        with patch("agent_takt.onboarding._compute_bundled_catalog",
                   return_value={}):  # empty bundle — no bundled files
            manifest = {"takt_version": "", "installed_at": "", "assets": {
                rel: self._bundled_entry(rel, sha)
            }}
            decisions = evaluate_upgrade_actions(self.root, manifest)

        d = next((x for x in decisions if x.rel_path == rel), None)
        self.assertIsNotNone(d)
        self.assertEqual(d.action, "disabled")

    def test_user_added_action(self):
        """On disk under bundled prefix, not in manifest or bundle → action=user_added."""
        from agent_takt.onboarding import evaluate_upgrade_actions

        rel = ".agents/skills/custom/my-skill/SKILL.md"
        self._make_file(rel, "my custom skill")

        with patch("agent_takt.onboarding._compute_bundled_catalog",
                   return_value={}):
            manifest = {"takt_version": "", "installed_at": "", "assets": {}}
            decisions = evaluate_upgrade_actions(self.root, manifest)

        d = next((x for x in decisions if x.rel_path == rel), None)
        self.assertIsNotNone(d)
        self.assertEqual(d.action, "user_added")
        self.assertTrue(d.user_owned)


# ---------------------------------------------------------------------------
# scaffold_project — manifest creation and rerun behavior
# ---------------------------------------------------------------------------


class TestScaffoldProjectManifest(unittest.TestCase):
    """Verify scaffold_project creates the assets manifest on first run."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_fake_dirs(self):
        fake_templates = self.root / "_fake_templates"
        fake_templates.mkdir(exist_ok=True)
        (fake_templates / "developer.md").write_text("lang={{LANGUAGE}}")

        fake_agents = self.root / "_fake_agents_skills"
        fake_agents.mkdir(exist_ok=True)
        (fake_agents / "core" / "base-orchestrator").mkdir(parents=True, exist_ok=True)
        (fake_agents / "core" / "base-orchestrator" / "SKILL.md").write_text("skill")

        fake_claude = self.root / "_fake_claude_skills"
        fake_claude.mkdir(exist_ok=True)
        (fake_claude / "skill.md").write_text("skill")

        fake_config = self.root / "_fake_config.yaml"
        fake_config.write_text("fake: true")
        return fake_templates, fake_agents, fake_claude, fake_config

    def _run_scaffold(self):
        fake_templates, fake_agents, fake_claude, fake_config = self._make_fake_dirs()
        answers = _make_answers()
        out = io.StringIO()
        with (
            patch("agent_takt.onboarding.packaged_templates_dir", return_value=fake_templates),
            patch("agent_takt.onboarding.packaged_agents_skills_dir", return_value=fake_agents),
            patch("agent_takt.onboarding.packaged_claude_skills_dir", return_value=fake_claude),
            patch("agent_takt.onboarding.packaged_default_config", return_value=fake_config),
            patch("agent_takt.onboarding.commit_scaffold"),
        ):
            scaffold_project(self.root, answers, stream_out=out)
        return out.getvalue()

    def test_fresh_init_creates_manifest(self):
        """After scaffold_project, .takt/assets-manifest.json exists with non-empty assets."""
        self._run_scaffold()
        manifest_path = self.root / ".takt" / "assets-manifest.json"
        self.assertTrue(manifest_path.is_file())
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertGreater(len(data["assets"]), 0)

    def test_fresh_init_takt_version_populated(self):
        """The manifest written at init has a non-empty takt_version."""
        self._run_scaffold()
        data = json.loads((self.root / ".takt" / "assets-manifest.json").read_text())
        self.assertNotEqual(data["takt_version"], "")

    def test_fresh_init_templates_user_owned(self):
        """Guardrail templates are marked user_owned: true in the init manifest."""
        self._run_scaffold()
        data = json.loads((self.root / ".takt" / "assets-manifest.json").read_text())
        template_entries = {k: v for k, v in data["assets"].items() if k.startswith("templates/agents/")}
        self.assertGreater(len(template_entries), 0)
        for k, v in template_entries.items():
            self.assertTrue(v["user_owned"], f"Expected user_owned=true for {k}")

    def test_second_run_prints_upgrade_notice(self):
        """Second scaffold_project run prints a notice to run 'takt upgrade'."""
        self._run_scaffold()
        # Second run
        out = self._run_scaffold()
        self.assertIn("takt upgrade", out)

    def test_install_agents_skills_second_run_returns_empty(self):
        """install_agents_skills() on a dir where skills exist returns empty list.

        Uses a fake bundled catalog so the second call installs from the same
        source and sees all files already present.
        """
        fake_templates, fake_agents, fake_claude, fake_config = self._make_fake_dirs()
        with patch("agent_takt.onboarding.packaged_agents_skills_dir", return_value=fake_agents):
            first = install_agents_skills(self.root, overwrite=False)
            second = install_agents_skills(self.root, overwrite=False)
        self.assertGreater(len(first), 0)
        self.assertEqual(second, [])


if __name__ == "__main__":
    unittest.main()
