"""Regression tests: ADR template is packaged, installed by init, and installed/updated by upgrade."""
from __future__ import annotations

import fnmatch
import io
import json
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from argparse import Namespace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt._assets import packaged_adr_templates_dir, packaged_claude_skills_dir
from agent_takt.adr import AdrStore
from agent_takt.cli import command_upgrade
from agent_takt.console import ConsoleReporter
from agent_takt.onboarding import InitAnswers, read_assets_manifest, scaffold_project

DATA = SRC_ROOT / "agent_takt" / "_data"
ADR_SUBDIRS = ("drafts", "approved", "superseded", "rejected")


def _glob_matches(pattern: str, rel: str) -> bool:
    """setuptools-like glob: ``**`` spans directories, ``*`` does not."""
    regex = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            regex += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            regex += ".*"
            i += 2
        elif pattern[i] == "*":
            regex += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            regex += "[^/]"
            i += 1
        else:
            regex += re.escape(pattern[i])
            i += 1
    return re.fullmatch(regex, rel) is not None


def _answers() -> InitAnswers:
    return InitAnswers(runner="claude", max_workers=1, language="Python",
                       test_command="pytest", build_check_command="python -m py_compile")


def _scaffold(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    from unittest.mock import patch
    with patch("agent_takt.onboarding.scaffold.init_db"):
        scaffold_project(root, _answers(), console=ConsoleReporter(stream=io.StringIO()))


def _upgrade(root: Path, dry_run: bool = False) -> str:
    out = io.StringIO()
    rc = command_upgrade(Namespace(root=str(root), dry_run=dry_run), ConsoleReporter(stream=out))
    assert rc == 0
    return out.getvalue()


class TestPackaging(unittest.TestCase):
    def test_all_data_files_covered_by_package_data(self):
        cfg = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        patterns = cfg["tool"]["setuptools"]["package-data"]["agent_takt"]
        missing = []
        for f in DATA.rglob("*"):
            if not f.is_file() or "__pycache__" in f.parts:
                continue
            rel = f.relative_to(SRC_ROOT / "agent_takt").as_posix()
            if not any(_glob_matches(p, rel) for p in patterns):
                missing.append(rel)
        self.assertEqual(missing, [])

    def test_adr_template_via_accessor(self):
        self.assertTrue((packaged_adr_templates_dir() / "template.md").is_file())


class TestInit(unittest.TestCase):
    def test_init_installs_adr_template_and_adr_new_works(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _scaffold(root)
            tpl = root / "templates" / "adr" / "template.md"
            self.assertEqual(tpl.read_bytes(), (packaged_adr_templates_dir() / "template.md").read_bytes())
            for s in ADR_SUBDIRS:
                self.assertTrue((root / "adr" / s / ".gitkeep").is_file())
            self.assertIn("templates/adr/template.md", read_assets_manifest(root)["assets"])
            AdrStore(root).new_adr("Test")
            self.assertTrue(any(re.fullmatch(r"adr-[0-9a-f]{8}-test\.md", p.name)
                                for p in (root / "adr" / "drafts").iterdir()))


class TestUpgrade(unittest.TestCase):
    def _downstream(self, root: Path) -> None:
        _scaffold(root)
        import shutil
        shutil.rmtree(root / "templates" / "adr")
        shutil.rmtree(root / "adr")
        mp = root / ".takt" / "assets-manifest.json"
        m = json.loads(mp.read_text())
        m["assets"].pop("templates/adr/template.md")
        mp.write_text(json.dumps(m))

    def test_dry_run_then_real_upgrade(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self._downstream(root)
            out = _upgrade(root, dry_run=True)
            self.assertIn("templates/adr/template.md", out)
            for s in ADR_SUBDIRS:
                self.assertIn(f"adr/{s}/.gitkeep", out)
            self.assertFalse((root / "templates" / "adr").exists())
            self.assertFalse((root / "adr").exists())
            _upgrade(root)
            self.assertTrue((root / "templates" / "adr" / "template.md").is_file())
            for s in ADR_SUBDIRS:
                self.assertTrue((root / "adr" / s / ".gitkeep").is_file())
            AdrStore(root).new_adr("Test")

    def test_unmodified_old_template_updated(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _scaffold(root)
            tpl = root / "templates" / "adr" / "template.md"
            tpl.write_text("old template\n")
            mp = root / ".takt" / "assets-manifest.json"
            m = json.loads(mp.read_text())
            import hashlib
            m["assets"]["templates/adr/template.md"]["sha256"] = hashlib.sha256(b"old template\n").hexdigest()
            mp.write_text(json.dumps(m))
            out = _upgrade(root)
            self.assertIn("[updated]", out)
            self.assertEqual(tpl.read_bytes(), (packaged_adr_templates_dir() / "template.md").read_bytes())

    def test_modified_template_skipped(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _scaffold(root)
            tpl = root / "templates" / "adr" / "template.md"
            tpl.write_text("my custom template\n")
            out = _upgrade(root)
            self.assertIn("locally modified", out)
            self.assertIn("templates/adr/template.md", out)
            self.assertEqual(tpl.read_text(), "my custom template\n")

    def test_upgrade_installs_takt_skill(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _scaffold(root)
            skill = root / ".claude" / "skills" / "takt" / "SKILL.md"
            skill.unlink()
            mp = root / ".takt" / "assets-manifest.json"
            _upgrade(root)
            self.assertIn("Architecture Decision Records", skill.read_text())


class TestSkills(unittest.TestCase):
    def test_bundled_takt_skill_has_adr_section(self):
        text = (packaged_claude_skills_dir() / "takt" / "SKILL.md").read_text()
        self.assertIn("## Architecture Decision Records", text)
        self.assertIn("--related-spec", text)
        self.assertIn("uv run takt adr supersede OLD --by NEW", text)

    def test_reviewer_signoff_copies_identical_with_adr_hint(self):
        a = (REPO_ROOT / "templates/skills/role/reviewer-signoff/SKILL.md").read_text()
        b = (DATA / "templates/skills/role/reviewer-signoff/SKILL.md").read_text()
        self.assertEqual(a, b)
        self.assertIn("takt adr new", a)
        self.assertIn("non-blocking", a)


if __name__ == "__main__":
    unittest.main()
