"""CLI tests for ``takt spec``.

Every test invokes the real CLI entry point in a subprocess (in a temporary
directory) so path handling, exit codes and stdout/stderr are exercised
end-to-end. This module also carries the ported cases of the former
``tests/test_spec_py.py`` (which tested the removed standalone ``spec.py``).
"""
from __future__ import annotations

import json
import os
import pty
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

_LAUNCH = (
    "import sys; sys.argv = ['takt'] + sys.argv[1:]; "
    "from agent_takt.cli import main; sys.exit(main())"
)


def _env() -> dict[str, str]:
    env = {**os.environ, "NO_COLOR": "1", "PYTHONPATH": str(SRC_ROOT)}
    env.pop("AGENT_TAKT_RUNNER", None)
    return env


def _run(args: list[str], cwd: str, stdin=subprocess.DEVNULL) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", _LAUNCH, *args],
        cwd=cwd, capture_output=True, text=True, env=_env(), stdin=stdin, timeout=60,
    )


def _spec(spec_id="spec-test1234", name="Test Spec", status="draft", body="# Test Spec\n", extra="",
          priority="null", tags="[]") -> str:
    return (
        f"---\nname: {name}\nid: {spec_id}\ndescription: null\ndependencies: null\n"
        f"priority: {priority}\ncomplexity: null\nstatus: {status}\ntags: {tags}\n"
        f"scope:\n  in: null\n  out: null\nfeature_root_id: null\n{extra}---\n{body}"
    )


def _spec_with_requirements(requirements: str, spec_id="spec-test1234") -> str:
    body = (
        "# Test Spec\n\n## Objective\n\n## Problems to Fix\n\n## Changes\n\n## Requirements\n\n"
        + requirements
        + "\n## Files to Modify\n\n| File | Change |\n|---|---|\n\n## Acceptance Criteria\n\n"
        "## Pending Decisions\n"
    )
    return _spec(spec_id=spec_id, body=body)


GOOD_ADDED = textwrap.dedent("""\
    ### ADDED: bead-lifecycle / Recovery bead on parse failure
    The scheduler SHALL create a recovery bead when output has no JSON.
    #### Scenario: Developer bead returns prose only
    - **WHEN** a developer bead's runner output contains no parseable JSON
    - **THEN** a bead `<id>-recovery` with `agent_type=recovery` is created
    """)


class SpecCliCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = self._td.name
        self.addCleanup(self._td.cleanup)

    # helpers -------------------------------------------------------------
    def mkdirs(self) -> None:
        for sub in ("drafts", "planned", "done"):
            os.makedirs(os.path.join(self.tmp, "specs", sub), exist_ok=True)

    def write(self, folder: str, filename: str, content: str) -> Path:
        p = Path(self.tmp, "specs", folder, filename)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def current(self, capability: str, content: str | None = None) -> Path:
        p = Path(self.tmp, "specs", "current", f"{capability}.md")
        if content is not None:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        return p

    def spec(self, *args: str, **kw) -> subprocess.CompletedProcess:
        return _run(["spec", *args], self.tmp, **kw)

    def snapshot(self) -> dict[str, bytes]:
        return {
            str(p.relative_to(self.tmp)): p.read_bytes()
            for p in Path(self.tmp).rglob("*") if p.is_file()
        }

    def pty_run(self, args: list[str], answer: str) -> subprocess.CompletedProcess:
        master, slave = pty.openpty()
        try:
            os.write(master, answer.encode())
            return subprocess.run(
                [sys.executable, "-c", _LAUNCH, "spec", *args],
                cwd=self.tmp, stdin=slave, capture_output=True, text=True, env=_env(), timeout=60,
            )
        finally:
            os.close(master)
            os.close(slave)


# ---------------------------------------------------------------------------
# init / create
# ---------------------------------------------------------------------------


class TestInitAndCreate(SpecCliCase):
    def test_init_creates_folders_and_template(self):
        r = self.spec("init")
        self.assertEqual(r.returncode, 0, r.stderr)
        for sub in ("drafts", "planned", "done"):
            self.assertTrue(Path(self.tmp, "specs", sub).is_dir())
        self.assertTrue(Path(self.tmp, "specs", "spec-template.md").is_file())

    def test_init_exits_1_when_specs_exists(self):
        os.makedirs(os.path.join(self.tmp, "specs"))
        r = self.spec("init")
        self.assertEqual(r.returncode, 1)
        self.assertIn("specs/ already exists", r.stderr)

    def test_commands_without_specs_dir_exit_1_and_suggest_init(self):
        for cmd in (["list"], ["create", "X"], ["show", "x"], ["validate"]):
            with self.subTest(cmd=cmd):
                r = self.spec(*cmd)
                self.assertEqual(r.returncode, 1)
                self.assertIn("takt spec init", r.stderr)

    def test_create_makes_file_prints_path_and_id(self):
        os.makedirs(os.path.join(self.tmp, "specs"))
        r = self.spec("create", "My Feature")
        self.assertEqual(r.returncode, 0, r.stderr)
        path = Path(self.tmp, "specs", "drafts", "my-feature.md")
        self.assertTrue(path.is_file())
        self.assertIn("specs/drafts/my-feature.md", r.stdout)
        self.assertRegex(r.stdout, r"ID: spec-[0-9a-f]{8}")
        self.assertIn("status: draft", path.read_text())

    def test_create_exits_1_when_file_exists(self):
        os.makedirs(os.path.join(self.tmp, "specs"))
        self.assertEqual(self.spec("create", "Dup").returncode, 0)
        r = self.spec("create", "Dup")
        self.assertEqual(r.returncode, 1)
        self.assertIn("already exists", r.stderr)

    def test_create_works_when_lifecycle_dirs_already_exist(self):
        self.mkdirs()
        self.assertEqual(self.spec("create", "Test Spec").returncode, 0)

    def _created(self, title: str) -> str:
        os.makedirs(os.path.join(self.tmp, "specs", "drafts"), exist_ok=True)
        r = self.spec("create", title)
        self.assertEqual(r.returncode, 0, r.stderr)
        (f,) = os.listdir(os.path.join(self.tmp, "specs", "drafts"))
        return Path(self.tmp, "specs", "drafts", f).read_text(encoding="utf-8")

    def test_create_body_contains_requirements_section(self):
        self.assertIn("## Requirements", self._created("My Feature"))

    def test_create_body_section_order(self):
        content = self._created("Ordered Spec")
        self.assertLess(content.index("## Changes"), content.index("## Requirements"))
        self.assertLess(content.index("## Requirements"), content.index("## Files to Modify"))

    def test_create_requirements_html_comment_present(self):
        content = self._created("Comment Spec")
        for kw in ("ADDED", "MODIFIED", "REMOVED", "SHALL", "MUST", "Scenario", "WHEN", "THEN", "AND"):
            self.assertIn(kw, content)

    def test_create_uses_project_template(self):
        os.makedirs(os.path.join(self.tmp, "specs"))
        Path(self.tmp, "specs", "spec-template.md").write_text("# {name}\n\nTEMPLATE\n")
        self.spec("create", "Custom")
        text = Path(self.tmp, "specs", "drafts", "custom.md").read_text()
        self.assertIn("TEMPLATE", text)
        self.assertIn("# Custom", text)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestList(SpecCliCase):
    def setUp(self):
        super().setUp()
        self.mkdirs()
        self.write("drafts", "a.md", _spec("spec-aaaaaaaa", "Alpha", priority="high", tags="\n- x\n- y"))
        self.write("planned", "b.md", _spec("spec-bbbbbbbb", "Beta", "planned", priority="low", tags="\n- x"))
        self.write("done", "c.md", _spec("spec-cccccccc", "Gamma", "done"))

    def test_list_all(self):
        r = self.spec("list")
        self.assertEqual(r.returncode, 0, r.stderr)
        for token in ("spec-aaaaaaaa", "Alpha", "spec-bbbbbbbb", "spec-cccccccc"):
            self.assertIn(token, r.stdout)

    def test_filters(self):
        self.assertNotIn("Beta", self.spec("list", "--status", "draft").stdout)
        self.assertIn("Beta", self.spec("list", "--status", "planned").stdout)
        self.assertIn("Alpha", self.spec("list", "--priority", "high").stdout)
        self.assertNotIn("Beta", self.spec("list", "--priority", "high").stdout)
        both = self.spec("list", "--tag", "x", "--tag", "y").stdout
        self.assertIn("Alpha", both)
        self.assertNotIn("Beta", both)
        self.assertEqual(self.spec("list", "--tag", "x", "--tag", "nope").stdout.strip(), "No specs found.")

    def test_json_shape(self):
        r = self.spec("list", "--json", "--status", "draft")
        rows = json.loads(r.stdout)
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            set(rows[0]), {"id", "name", "status", "priority", "tags", "path"}
        )
        self.assertEqual(rows[0]["id"], "spec-aaaaaaaa")
        self.assertEqual(rows[0]["tags"], ["x", "y"])
        self.assertEqual(rows[0]["path"], "specs/drafts/a.md")

    def test_current_dir_excluded(self):
        self.current("cap-one", "## Requirement: X\nSHALL\n")
        r = self.spec("list", "--json")
        self.assertEqual({row["id"] for row in json.loads(r.stdout)},
                         {"spec-aaaaaaaa", "spec-bbbbbbbb", "spec-cccccccc"})

    def test_legacy_spec_listed_as_legacy(self):
        self.write("drafts", "old.md", "# Old Spec\n\nNo frontmatter.\n")
        rows = json.loads(self.spec("list", "--json").stdout)
        legacy = [x for x in rows if x["status"] == "legacy"]
        self.assertEqual(len(legacy), 1)
        self.assertEqual(legacy[0]["name"], "Old Spec")

    def test_malformed_spec_listed_as_legacy_with_stderr_warning(self):
        self.write("drafts", "broken.md", "---\nkey: [oops\n---\nbody\n")
        r = self.spec("list", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("warning:", r.stderr)
        self.assertIn("broken.md", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertTrue(any(x["status"] == "legacy" for x in json.loads(r.stdout)))

    def test_name_inferred_from_first_heading_when_name_missing(self):
        self.write(
            "drafts", "noname.md",
            "---\nid: spec-dddddddd\nstatus: draft\n---\n\n# Inferred Heading Name\n\nbody\n",
        )
        rows = json.loads(self.spec("list", "--json").stdout)
        (row,) = [x for x in rows if x["id"] == "spec-dddddddd"]
        self.assertEqual(row["name"], "Inferred Heading Name")
        self.assertIn("Inferred Heading Name", self.spec("list").stdout)

    def test_wrapped_plain_scalar_has_no_warning(self):
        self.write(
            "drafts", "wrapped.md",
            "---\nname: Wrapped\nid: spec-wrap1234\n"
            "description: Rename the package, module, and CLI from old-name/old_mod\n"
            "  to new-name/new_mod\ndependencies: []\npriority: low\ncomplexity: medium\n"
            "status: draft\ntags:\n- a\nscope:\n  in: x\n  out: y\n---\n\n# Body\n",
        )
        r = self.spec("list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("warning", r.stderr)
        self.assertIn("Wrapped", r.stdout)
        shown = json.loads(self.spec("show", "wrap1234", "--json").stdout)
        self.assertEqual(
            shown["frontmatter"]["description"],
            "Rename the package, module, and CLI from old-name/old_mod to new-name/new_mod",
        )
        self.assertEqual(shown["frontmatter"]["scope"], {"in": "x", "out": "y"})


# ---------------------------------------------------------------------------
# show / resolution
# ---------------------------------------------------------------------------


class TestShow(SpecCliCase):
    def setUp(self):
        super().setUp()
        self.mkdirs()
        lines = "\n".join(f"Line {i}" for i in range(1, 35))
        self.write("drafts", "long-spec.md", _spec("spec-long0001", "Long Spec", body=lines + "\n"))

    def test_resolve_by_short_prefix(self):
        """Scenario: document-management / Spec resolution by ID, prefix or filename / Resolve a spec by short prefix"""
        self.write("drafts", "other.md", _spec("spec-1cdb5678", "Other"))
        r = self.spec("show", "1cdb")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("spec-1cdb5678", r.stdout)
        self.assertEqual(self.spec("show", "spec-1cdb").returncode, 0)

    def test_ambiguous_query_lists_every_id(self):
        """Scenario: document-management / Spec resolution by ID, prefix or filename / Ambiguous query"""
        self.write("drafts", "one.md", _spec("spec-1cdb1111", "One"))
        self.write("planned", "two.md", _spec("spec-1cdb2222", "Two", "planned"))
        r = self.spec("show", "1cdb")
        self.assertEqual(r.returncode, 1)
        self.assertIn("spec-1cdb1111", r.stderr)
        self.assertIn("spec-1cdb2222", r.stderr)
        self.assertEqual(r.stdout, "")

    def test_unknown_query(self):
        r = self.spec("show", "zzzzzz")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no spec matching", r.stderr)

    def test_filename_substring(self):
        self.assertEqual(self.spec("show", "long-spec").returncode, 0)

    def test_body_only_excludes_frontmatter(self):
        r = self.spec("show", "--body-only", "long-spec")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("---", r.stdout)
        self.assertNotIn("name: Long Spec", r.stdout)
        self.assertIn("Line 34", r.stdout)

    def test_default_truncates_at_20_lines(self):
        r = self.spec("show", "long-spec")
        self.assertIn("name: Long Spec", r.stdout)
        self.assertIn("Line 20", r.stdout)
        self.assertNotIn("Line 21", r.stdout)

    def test_full_shows_all_lines(self):
        self.assertIn("Line 34", self.spec("show", "--full", "long-spec").stdout)

    def test_stops_at_first_separator_line(self):
        self.write("drafts", "sep.md", _spec("spec-sep00001", "Sep", body="before\n---\nafter\n"))
        r = self.spec("show", "sep00001", "--full")
        self.assertIn("before", r.stdout)
        self.assertNotIn("after", r.stdout)

    def test_field_projection(self):
        self.assertEqual(self.spec("show", "long-spec", "--field", "name").stdout.strip(), "Long Spec")
        self.assertEqual(self.spec("show", "long-spec", "--field", "scope.in").stdout.strip(), "")
        bad = self.spec("show", "long-spec", "--field", "nope")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("field not found", bad.stderr)

    def test_json_output(self):
        data = json.loads(self.spec("show", "long-spec", "--json").stdout)
        self.assertEqual(data["id"], "spec-long0001")
        self.assertEqual(data["status"], "draft")
        self.assertIn("Line 34", data["body"])
        self.assertEqual(data["frontmatter"]["name"], "Long Spec")

    def test_body_only_on_legacy_spec_hits_legacy_warning(self):
        self.write("drafts", "legacy-spec.md", "# Legacy Spec\n\nNo frontmatter here.\n")
        r = self.spec("show", "--body-only", "legacy-spec")
        self.assertEqual(r.returncode, 0)
        self.assertIn("legacy", (r.stdout + r.stderr).lower())

    def test_legacy_field_exits_1(self):
        self.write("drafts", "legacy-spec.md", "# Legacy Spec\n")
        r = self.spec("show", "legacy-spec", "--field", "name")
        self.assertEqual(r.returncode, 1)
        self.assertIn("field not found", r.stderr)

    def test_legacy_json(self):
        self.write("drafts", "legacy-spec.md", "# Legacy Spec\n")
        data = json.loads(self.spec("show", "legacy-spec", "--json").stdout)
        self.assertEqual(data["status"], "legacy")


class TestNoTaktState(SpecCliCase):
    def test_spec_commands_do_not_create_takt_state(self):
        """Scenario: document-management / Spec management needs no standalone script / Spec commands do not create takt state"""
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".takt")))
        self.assertEqual(self.spec("init").returncode, 0)
        self.assertEqual(self.spec("create", "A Spec").returncode, 0)
        self.assertEqual(self.spec("list").returncode, 0)
        self.assertEqual(self.spec("validate").returncode, 0)
        self.assertEqual(self.spec("set", "priority", "high", "a-spec").returncode, 0)
        self.assertEqual(self.spec("set", "status", "planned", "a-spec").returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".takt")))

    def test_root_option_before_and_after_spec(self):
        os.makedirs(os.path.join(self.tmp, "proj", "specs"))
        elsewhere = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(elsewhere, ignore_errors=True))
        root = os.path.join(self.tmp, "proj")
        for argv in (["--root", root, "spec", "create", "One"], ["spec", "--root", root, "create", "Two"]):
            r = _run(argv, elsewhere)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(Path(root, "specs/drafts/one.md").is_file())
        self.assertTrue(Path(root, "specs/drafts/two.md").is_file())
        self.assertFalse(os.path.exists(os.path.join(elsewhere, "specs")))
        self.assertFalse(os.path.exists(os.path.join(root, ".takt")))


# ---------------------------------------------------------------------------
# set / migrate / remove
# ---------------------------------------------------------------------------


class TestSet(SpecCliCase):
    def setUp(self):
        super().setUp()
        self.mkdirs()
        self.path = self.write("drafts", "demo.md", _spec("spec-0123abcd", "Demo", body="# Demo\n\nBody --- text\n"))

    def body_of(self, p: Path) -> str:
        return p.read_text().split("---\n", 2)[2]

    def test_set_status_planned_moves_file_body_identical(self):
        """Scenario: document-management / Status changes keep folder and frontmatter in sync / Spec moves to planned"""
        body = self.body_of(self.path)
        r = self.spec("set", "status", "planned", "0123")
        self.assertEqual(r.returncode, 0, r.stderr)
        new = Path(self.tmp, "specs/planned/demo.md")
        self.assertIn("specs/planned/demo.md", r.stdout)
        self.assertFalse(self.path.exists())
        self.assertIn("status: planned", new.read_text())
        self.assertEqual(self.body_of(new), body)

    def test_set_status_cancelled_custom(self):
        """Scenario: document-management / Status changes keep folder and frontmatter in sync / Custom spec status"""
        body = self.body_of(self.path)
        r = self.spec("set", "status", "cancelled", "0123")
        self.assertEqual(r.returncode, 0, r.stderr)
        new = Path(self.tmp, "specs/cancelled/demo.md")
        self.assertTrue(new.is_file())
        self.assertIn("status: cancelled", new.read_text())
        self.assertEqual(self.body_of(new), body)
        self.assertEqual(json.loads(self.spec("list", "--json", "--status", "cancelled").stdout)[0]["id"],
                         "spec-0123abcd")

    def test_set_status_current_rejected_file_unchanged(self):
        """Scenario: document-management / Status changes keep folder and frontmatter in sync / Reserved status rejected"""
        before = self.path.read_bytes()
        r = self.spec("set", "status", "current", "0123")
        self.assertEqual(r.returncode, 1)
        self.assertIn("reserved", r.stderr)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(Path(self.tmp, "specs/current").exists())

    def test_set_status_invalid_custom_rejected(self):
        before = self.snapshot()
        r = self.spec("set", "status", "a b", "0123")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.snapshot(), before)

    def test_set_status_draft_planned_done_still_work(self):
        for status in ("planned", "done", "draft"):
            r = self.spec("set", "status", status, "0123")
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_set_description_round_trips(self):
        for value in ("Colon: here, and more", "preflight, tests", "true", "123", "a # b"):
            with self.subTest(value=value):
                r = self.spec("set", "description", value, "0123")
                self.assertEqual(r.returncode, 0, r.stderr)
                shown = self.spec("show", "0123", "--field", "description")
                self.assertEqual(shown.stdout.strip(), value)

    def test_description_with_colon_survives_other_edits(self):
        """Scenario: document-management / Frontmatter round-trip / Description with a colon"""
        value = "Gates: preflight, tests"
        self.assertEqual(self.spec("set", "description", value, "0123").returncode, 0)
        text = self.path.read_text()
        self.assertEqual(self.spec("set", "priority", "high", "0123").returncode, 0)
        self.assertEqual(self.spec("show", "0123", "--field", "description").stdout.strip(), value)
        listed = self.spec("list")
        self.assertEqual(listed.returncode, 0)
        self.assertEqual(listed.stderr, "")
        self.assertEqual(self.path.read_text(), text.replace("priority: null", "priority: high"))
        self.assertEqual(self.spec("validate").returncode, 0)

    def test_set_priority_validation(self):
        r = self.spec("set", "priority", "urgent", "0123")
        self.assertEqual(r.returncode, 1)
        self.assertIn("invalid priority", r.stderr)
        self.assertEqual(self.spec("set", "priority", "medium", "0123").returncode, 0)
        self.assertEqual(self.spec("show", "0123", "--field", "priority").stdout.strip(), "medium")

    def test_set_tags_splits_on_commas(self):
        self.assertEqual(self.spec("set", "tags", " a, b ,,c", "0123").returncode, 0)
        self.assertEqual(
            json.loads(self.spec("show", "0123", "--json").stdout)["frontmatter"]["tags"], ["a", "b", "c"]
        )

    def test_set_feature_root(self):
        self.assertEqual(self.spec("set", "feature-root", "B-12345678", "0123").returncode, 0)
        self.assertEqual(self.spec("show", "0123", "--field", "feature_root_id").stdout.strip(), "B-12345678")

    def test_set_single_field_leaves_other_lines_untouched(self):
        before = self.path.read_text()
        self.spec("set", "priority", "high", "0123")
        self.assertEqual(self.path.read_text(), before.replace("priority: null", "priority: high"))

    def test_set_priority_preserves_wrapped_description(self):
        joined = "Rename the package, module, and CLI from old-name/old_mod to new-name/new_mod"
        self.write(
            "drafts", "wrapped.md",
            "---\nname: Wrapped\nid: spec-wrap1234\n"
            "description: Rename the package, module, and CLI from old-name/old_mod\n"
            "  to new-name/new_mod\npriority: low\nstatus: draft\n---\n\n# Body\n",
        )
        r = self.spec("set", "priority", "high", "wrap1234")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = Path(self.tmp, "specs/drafts/wrapped.md").read_text()
        self.assertIn("priority: high", text)
        shown = self.spec("show", "wrap1234", "--field", "description")
        self.assertEqual(shown.stdout.strip(), joined)
        self.assertNotIn("warning", self.spec("list").stderr)

    def test_set_on_legacy_spec_exits_1(self):
        self.write("drafts", "old.md", "# Old\n")
        r = self.spec("set", "priority", "high", "old")
        self.assertEqual(r.returncode, 1)
        self.assertIn("legacy", r.stderr)

    def test_set_unknown_spec_exits_1(self):
        r = self.spec("set", "priority", "high", "zzzz")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no spec matching", r.stderr)

    def test_set_status_done_with_valid_deltas_merges(self):
        """Scenario: document-management / Requirements merge on spec completion / Valid deltas merged"""
        self.write("drafts", "req.md", _spec_with_requirements(GOOD_ADDED, "spec-req00001"))
        r = self.spec("set", "status", "done", "req00001")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("merged: ADDED bead-lifecycle / Recovery bead on parse failure", r.stdout)
        cap = self.current("bead-lifecycle").read_text()
        self.assertIn("# bead-lifecycle", cap)
        self.assertIn("## Requirement: Recovery bead on parse failure", cap)
        self.assertIn("<!-- source: spec-req00001 -->", cap)
        self.assertIn("### Scenario:", cap)
        self.assertNotIn("#### Scenario:", cap)
        self.assertTrue(Path(self.tmp, "specs/done/req.md").is_file())

    def test_set_status_done_with_invalid_delta_blocks(self):
        """Scenario: document-management / Requirements merge on spec completion / Invalid delta blocks completion"""
        bad = "### ADDED: cap-one / Bad\nNo normative word.\n#### Scenario: S\n- **WHEN** a\n- **THEN** b\n"
        p = self.write("drafts", "bad.md", _spec_with_requirements(bad, "spec-bad00001"))
        self.current("keep", "## Requirement: Keep\nSHALL keep\n")
        before = self.snapshot()
        r = self.spec("set", "status", "done", "bad00001")
        self.assertEqual(r.returncode, 1)
        self.assertIn("SHALL or MUST", r.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(p.exists())
        self.assertFalse(Path(self.tmp, "specs/done/bad.md").exists())

    def test_done_spec_validates_after_merge(self):
        """Scenario: document-management / Requirements merge on spec completion / Done spec validates after merge"""
        self.write("drafts", "req.md", _spec_with_requirements(GOOD_ADDED, "spec-req00001"))
        self.assertEqual(self.spec("set", "status", "done", "req00001").returncode, 0)
        # The ADDED requirement now exists in specs/current, yet the done spec is OK.
        r = self.spec("validate")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK: specs/done/req.md", r.stdout)
        self.assertEqual(self.spec("validate", "req00001").returncode, 0)

    def test_set_status_done_twice_merges_once(self):
        self.write("drafts", "req.md", _spec_with_requirements(GOOD_ADDED, "spec-req00001"))
        self.spec("set", "status", "done", "req00001")
        cap = self.current("bead-lifecycle")
        cap.write_text("sentinel\n" + cap.read_text())
        r = self.spec("set", "status", "done", "req00001")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("merged:", r.stdout)
        self.assertTrue(cap.read_text().startswith("sentinel\n"))

    def test_set_status_planned_or_draft_does_not_touch_current(self):
        self.write("drafts", "req.md", _spec_with_requirements(GOOD_ADDED, "spec-req00001"))
        self.spec("set", "status", "planned", "req00001")
        self.spec("set", "status", "draft", "req00001")
        self.assertFalse(Path(self.tmp, "specs/current").exists())

    def test_spec_with_no_requirements_moves_to_done(self):
        r = self.spec("set", "status", "done", "0123")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("merged:", r.stdout)
        self.assertFalse(Path(self.tmp, "specs/current").exists())

    # merge details --------------------------------------------------------

    def _merge(self, body: str, spec_id: str = "spec-m0000001", name: str = "m.md") -> subprocess.CompletedProcess:
        self.write("drafts", name, _spec_with_requirements(body, spec_id))
        return self.spec("set", "status", "done", spec_id)

    def test_added_appends_to_existing_capability_file(self):
        self.assertEqual(self._merge(GOOD_ADDED).returncode, 0)
        second = GOOD_ADDED.replace("Recovery bead on parse failure", "Second requirement")
        r = self._merge(second, "spec-m0000002", "m2.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        cap = self.current("bead-lifecycle").read_text()
        self.assertIn("## Requirement: Recovery bead on parse failure", cap)
        self.assertIn("## Requirement: Second requirement", cap)

    def test_modified_replaces_block_in_place(self):
        self._merge(GOOD_ADDED)
        mod = GOOD_ADDED.replace("### ADDED", "### MODIFIED").replace("SHALL create", "SHALL always create")
        r = self._merge(mod, "spec-m0000002", "m2.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        cap = self.current("bead-lifecycle").read_text()
        self.assertIn("SHALL always create", cap)
        self.assertEqual(cap.count("## Requirement: Recovery bead on parse failure"), 1)

    def test_removed_deletes_block_and_empty_file(self):
        self._merge(GOOD_ADDED)
        second = GOOD_ADDED.replace("Recovery bead on parse failure", "Second")
        self._merge(second, "spec-m0000002", "m2.md")
        rem1 = "### REMOVED: bead-lifecycle / Second\nReason: gone.\n"
        self.assertEqual(self._merge(rem1, "spec-m0000003", "m3.md").returncode, 0)
        cap = self.current("bead-lifecycle")
        self.assertNotIn("Second", cap.read_text())
        rem2 = "### REMOVED: bead-lifecycle / Recovery bead on parse failure\nReason: gone.\n"
        self.assertEqual(self._merge(rem2, "spec-m0000004", "m4.md").returncode, 0)
        self.assertFalse(cap.exists())


class TestMigrate(SpecCliCase):
    def setUp(self):
        super().setUp()
        self.mkdirs()

    def test_migrate_legacy_spec(self):
        p = self.write("planned", "old-spec.md", "# Old Heading\n\nSome body.\n")
        r = self.spec("migrate", "old-spec")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"ID: spec-[0-9a-f]{8}")
        self.assertIn("Status: planned", r.stdout)
        text = p.read_text()
        self.assertTrue(text.startswith("---\n"))
        self.assertTrue(text.endswith("# Old Heading\n\nSome body.\n"))
        data = json.loads(self.spec("show", "old-spec", "--json").stdout)
        self.assertEqual(data["status"], "planned")
        self.assertEqual(data["frontmatter"]["name"], "Old Heading")
        self.assertEqual(self.spec("validate").returncode, 0)

    def test_migrate_spec_that_already_has_frontmatter_exits_1(self):
        p = self.write("drafts", "new.md", _spec("spec-aaaaaaaa", "New"))
        before = p.read_bytes()
        r = self.spec("migrate", "aaaaaaaa")
        self.assertEqual(r.returncode, 1)
        self.assertIn("already has frontmatter", r.stderr)
        self.assertEqual(p.read_bytes(), before)

    def test_migrate_refuses_malformed_frontmatter(self):
        p = self.write("drafts", "broken.md", "---\nk: [oops\n---\nbody\n")
        before = p.read_bytes()
        r = self.spec("migrate", "broken")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(p.read_bytes(), before)


class TestRemove(SpecCliCase):
    def setUp(self):
        super().setUp()
        self.mkdirs()
        self.draft = self.write("drafts", "d.md", _spec("spec-dddddddd", "D"))
        self.planned = self.write("planned", "p.md", _spec("spec-pppppppp", "P", "planned"))

    def test_remove_draft_deleted(self):
        r = self.spec("remove", "dddddddd")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.draft.exists())

    def test_remove_non_draft_without_tty_and_force_exits_1_and_keeps_file(self):
        r = self.spec("remove", "pppppppp")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--force", r.stderr)
        self.assertTrue(self.planned.exists())

    def test_remove_with_force_deletes(self):
        self.assertEqual(self.spec("remove", "pppppppp", "--force").returncode, 0)
        self.assertFalse(self.planned.exists())

    def test_remove_tty_confirm_yes(self):
        r = self.pty_run(["remove", "pppppppp"], "y\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.planned.exists())

    def test_remove_tty_confirm_no(self):
        r = self.pty_run(["remove", "pppppppp"], "n\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Aborted", r.stdout)
        self.assertTrue(self.planned.exists())

    def test_remove_unknown_exits_1(self):
        self.assertEqual(self.spec("remove", "zzzz").returncode, 1)


# ---------------------------------------------------------------------------
# validate (ported from the former spec.py validate tests)
# ---------------------------------------------------------------------------


class TestValidate(SpecCliCase):
    def setUp(self):
        super().setUp()
        self.mkdirs()

    def validate_body(self, requirements: str, folder: str = "drafts", name: str = "t.md",
                      spec_id: str = "spec-test1234") -> subprocess.CompletedProcess:
        self.write(folder, name, _spec_with_requirements(requirements, spec_id))
        return self.spec("validate", spec_id)

    def assert_invalid(self, requirements: str, fragment: str | None = None):
        r = self.validate_body(requirements)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        if fragment:
            self.assertIn(fragment.lower(), r.stderr.lower())

    def test_no_arg_exits_0_with_nonstandard_id_warning(self):
        self.write("drafts", "w.md", _spec("s-a0f0124", "Warn"))
        r = self.spec("validate")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("warning:", r.stderr)
        self.assertIn("s-a0f0124", r.stderr)
        self.assertIn("OK:", r.stdout)

    def test_missing_name_exits_0_with_warning(self):
        p = self.write(
            "drafts", "noname.md",
            "---\nid: spec-aaaaaaaa\nstatus: draft\n---\n\n# Inferred Name\n",
        )
        r = self.spec("validate")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("warning:", r.stderr)
        self.assertIn("name", r.stderr)
        self.assertIn("OK:", r.stdout)
        listed = json.loads(self.spec("list", "--json").stdout)
        self.assertEqual(listed[0]["name"], "Inferred Name")
        self.assertNotIn("name:", p.read_text())

    def test_malformed_frontmatter_named_on_stderr_others_still_ok(self):
        self.write("drafts", "good.md", _spec("spec-aaaaaaaa", "Good"))
        self.write("drafts", "broken.md", "---\nkey: [oops\n---\nbody\n")
        r = self.spec("validate")
        self.assertEqual(r.returncode, 1)
        self.assertIn("broken.md", r.stderr)
        self.assertIn("OK: specs/drafts/good.md", r.stdout)
        self.assertNotIn("Traceback", r.stderr)

    def test_malformed_spec_frontmatter_scenario(self):
        """Scenario: document-management / Frontmatter round-trip / Malformed spec frontmatter"""
        self.write("drafts", "good.md", _spec("spec-aaaaaaaa", "Good"))
        broken = self.write("drafts", "broken.md", "---\nname: [unterminated\nid: spec-bbbbbbbb\n---\nbody\n")
        listed = self.spec("list", "--json")
        self.assertEqual(listed.returncode, 0)
        self.assertIn("broken.md", listed.stderr)
        self.assertEqual(
            {r["id"]: r["status"] for r in json.loads(listed.stdout)}["broken"], "legacy"
        )
        r = self.spec("validate")
        self.assertEqual(r.returncode, 1)
        self.assertIn(str(broken.relative_to(self.tmp)), r.stderr)
        self.assertIn("OK: specs/drafts/good.md", r.stdout)
        self.assertNotIn("Traceback", r.stderr + r.stdout)

    def test_file_path_argument_and_query_argument(self):
        p = self.write("drafts", "t.md", _spec_with_requirements(GOOD_ADDED, "spec-test1234"))
        self.assertEqual(self.spec("validate", str(p)).returncode, 0)
        self.assertEqual(self.spec("validate", "specs/drafts/t.md").returncode, 0)
        self.assertEqual(self.spec("validate", "test1234").returncode, 0)

    def test_ambiguous_or_unmatched_query_exits_1(self):
        self.write("drafts", "a.md", _spec("spec-1cdb1111", "A"))
        self.write("drafts", "b.md", _spec("spec-1cdb2222", "B"))
        amb = self.spec("validate", "1cdb")
        self.assertEqual(amb.returncode, 1)
        self.assertIn("spec-1cdb1111", amb.stderr)
        self.assertIn("spec-1cdb2222", amb.stderr)
        self.assertEqual(self.spec("validate", "zzzz").returncode, 1)

    def test_path_outside_specs_errors(self):
        outside = Path(self.tmp, "elsewhere.md")
        outside.write_text(_spec("spec-aaaaaaaa", "X"))
        r = self.spec("validate", str(outside))
        self.assertEqual(r.returncode, 1)
        self.assertIn("not a spec", r.stderr)

    def test_no_requirements_section_exits_0_and_writes_nothing(self):
        self.write("drafts", "t.md", _spec("spec-test1234", "T", body="# T\n\nNo reqs.\n"))
        before = self.snapshot()
        r = self.spec("validate", "test1234")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_well_formed_added_modified_removed(self):
        self.current("cap-one", "# cap-one\n\n## Requirement: Existing\nSHALL exist\n")
        body = (
            "### ADDED: cap-one / New thing\nThe system MUST do it.\n#### Scenario: S\n"
            "- **WHEN** a\n- **THEN** b\n\n"
            "### MODIFIED: cap-one / Existing\nThe system SHALL exist better.\n#### Scenario: S2\n"
            "- **WHEN** a\n- **THEN** b\n"
        )
        r = self.validate_body(body)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2 requirement deltas", r.stdout)
        rem = "### REMOVED: cap-one / Existing\nReason: obsolete.\n"
        self.assertEqual(self.validate_body(rem, name="u.md", spec_id="spec-test5678").returncode, 0)

    def test_capability_slug_with_numbers_and_empty_section(self):
        ok = GOOD_ADDED.replace("bead-lifecycle", "tui-2-panel")
        self.assertEqual(self.validate_body(ok).returncode, 0)
        self.assertEqual(
            self.validate_body("", name="e.md", spec_id="spec-test0002").returncode, 0
        )

    def test_section_at_end_of_file(self):
        text = _spec(body="# T\n\n## Requirements\n\n" + GOOD_ADDED)
        self.write("drafts", "end.md", text)
        self.assertEqual(self.spec("validate", "test1234").returncode, 0)

    def test_malformed_heading_exits_1(self):
        self.assert_invalid("### ADDED bead-lifecycle Missing separators\nSHALL\n")

    def test_non_kebab_case_capability_exits_1(self):
        self.assert_invalid(GOOD_ADDED.replace("bead-lifecycle", "Bead_Lifecycle"))

    def test_capability_with_spaces_exits_1(self):
        self.assert_invalid(GOOD_ADDED.replace("bead-lifecycle", "bead lifecycle"))

    def test_added_modified_missing_shall_must_exits_1(self):
        self.current("cap-one", "## Requirement: X\nSHALL\n")
        self.assert_invalid(
            "### ADDED: cap-one / Y\nNothing normative.\n#### Scenario: S\n- **WHEN** a\n- **THEN** b\n",
            "SHALL or MUST",
        )
        self.assert_invalid(
            "### MODIFIED: cap-one / X\nNothing normative.\n#### Scenario: S\n- **WHEN** a\n- **THEN** b\n",
            "SHALL or MUST",
        )

    def test_added_modified_no_scenario_exits_1(self):
        self.current("cap-one", "## Requirement: X\nSHALL\n")
        self.assert_invalid("### ADDED: cap-one / Y\nThe system SHALL.\n", "scenario")
        self.assert_invalid("### MODIFIED: cap-one / X\nThe system SHALL.\n", "scenario")

    def test_scenario_missing_when_or_then_exits_1(self):
        base = "### ADDED: cap-one / Y\nThe system SHALL.\n#### Scenario: S\n"
        self.assert_invalid(base + "- **THEN** b\n", "WHEN")
        self.assert_invalid(base + "- **WHEN** a\n", "THEN")

    def test_removed_without_reason_or_with_scenario_exits_1(self):
        self.current("cap-one", "## Requirement: X\nSHALL\n")
        self.assert_invalid("### REMOVED: cap-one / X\n", "reason")
        self.assert_invalid(
            "### REMOVED: cap-one / X\nReason: r\n#### Scenario: S\n- **WHEN** a\n- **THEN** b\n"
        )

    def test_duplicate_pairs_exit_1_case_insensitive(self):
        entry = "### ADDED: cap-one / Y\nThe system SHALL.\n#### Scenario: S\n- **WHEN** a\n- **THEN** b\n"
        self.assert_invalid(entry + "\n" + entry, "duplicate")
        self.assert_invalid(entry + "\n" + entry.replace("cap-one / Y", "CAP-ONE / y"), "duplicate")

    def test_consistency_errors(self):
        # ADDED for an existing requirement
        self.current("cap-one", "## Requirement: Y\nSHALL\n")
        self.assert_invalid(GOOD_ADDED.replace("bead-lifecycle / Recovery bead on parse failure", "cap-one / Y"),
                            "already exists")
        # MODIFIED / REMOVED for a missing requirement
        self.assert_invalid(
            "### MODIFIED: cap-one / Missing\nThe system SHALL.\n#### Scenario: S\n- **WHEN** a\n- **THEN** b\n",
            "not found",
        )
        self.assert_invalid("### REMOVED: cap-one / Missing\nReason: r\n", "not found")
        # MODIFIED / REMOVED when the capability file is absent
        self.assert_invalid(
            "### MODIFIED: nofile / X\nThe system SHALL.\n#### Scenario: S\n- **WHEN** a\n- **THEN** b\n",
            "capability file not found",
        )
        self.assert_invalid("### REMOVED: nofile / X\nReason: r\n", "capability file not found")

    def test_consistency_passes_when_requirement_exists(self):
        self.current("cap-one", "## Requirement: X\nSHALL\n")
        mod = "### MODIFIED: cap-one / X\nThe system SHALL.\n#### Scenario: S\n- **WHEN** a\n- **THEN** b\n"
        self.assertEqual(self.validate_body(mod).returncode, 0)
        self.assertEqual(
            self.validate_body("### REMOVED: cap-one / X\nReason: r\n", name="r.md", spec_id="spec-test0003").returncode, 0
        )

    def test_validate_never_writes_files(self):
        self.current("cap-one", "## Requirement: X\nSHALL\n")
        self.write("drafts", "t.md", _spec_with_requirements(GOOD_ADDED))
        before = self.snapshot()
        self.spec("validate")
        self.spec("validate", "test1234")
        self.assertEqual(self.snapshot(), before)

    def test_fenced_scenarios_not_requirements_structure(self):
        """Scenario: document-management / Single document core / Fenced scenarios are ignored consistently"""
        fenced_only = "```markdown\n## Requirements\n### ADDED: bad / thing\nno keyword\n```\n"
        self.write("drafts", "f.md", _spec(body="# T\n\n" + fenced_only))
        self.assertEqual(self.spec("validate", "test1234").returncode, 0)
        mixed = GOOD_ADDED + "\n```markdown\n#### Scenario: Fenced\n### ADDED: fake / Fake\n```\n"
        r = self.validate_body(mixed, name="m.md", spec_id="spec-test0009")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 requirement deltas", r.stdout)

    def test_current_dir_not_listed_or_validated_as_spec(self):
        self.current("cap-one", "## Requirement: X\nno frontmatter here\n")
        r = self.spec("validate")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("current", r.stdout)


# ---------------------------------------------------------------------------
# Packaging / skills mention
# ---------------------------------------------------------------------------


class TestNoSpecPy(unittest.TestCase):
    def test_package_contains_no_spec_py_script_copies(self):
        """Scenario: document-management / Spec management needs no standalone script / Package contains no spec.py"""
        skip = {"cli/commands/spec.py"}
        offenders = []
        for base in (REPO_ROOT / "src", REPO_ROOT / "templates", REPO_ROOT / ".claude",
                     REPO_ROOT / ".agents"):
            if not base.exists():
                continue
            for p in base.rglob("spec.py"):
                rel = p.relative_to(REPO_ROOT / "src" / "agent_takt").as_posix() \
                    if "src" in p.parts and "agent_takt" in p.parts else p.as_posix()
                if rel in skip:
                    continue
                offenders.append(str(p))
        self.assertEqual(offenders, [])

    def test_no_legacy_fm_helpers_in_src(self):
        hits = [
            str(p) for p in (REPO_ROOT / "src").rglob("*.py")
            if "_fm_load" in p.read_text(encoding="utf-8") or "_fm_dump" in p.read_text(encoding="utf-8")
        ]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
