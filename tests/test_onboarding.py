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
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.onboarding import (
    InitAnswers,
    _language_specific_known_issues,
    collect_init_answers,
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
        self.assertIn("--max-workers 3", yaml_text)

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


if __name__ == "__main__":
    unittest.main()
