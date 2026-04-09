"""Unit tests for skills/spec-management/spec.py.

Run with:
    python3 -m unittest discover -s skills/spec-management/tests/

Each test class uses a temporary directory as the working directory so that
the global SPECS_DIR / DRAFTS_DIR / PLANNED_DIR / DONE_DIR constants resolve
correctly without touching the real file system.
"""

import argparse
import io
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Path setup — allow importing spec.py from the parent skills directory.
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(__file__)
_SKILLS_DIR = os.path.abspath(os.path.join(_HERE, ".."))
if _SKILLS_DIR not in sys.path:
    sys.path.insert(0, _SKILLS_DIR)

import spec as spec_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(path: str, name: str = "My Spec", spec_id: str = "spec-aabbccdd",
               status: str = "draft", tags: list = None, priority: str = "medium",
               extra_body: str = "") -> None:
    """Write a valid spec file with frontmatter to *path*."""
    tags_str = str(tags or [])
    content = (
        f"---\n"
        f"name: {name}\n"
        f"id: {spec_id}\n"
        f"description:\n"
        f"dependencies:\n"
        f"priority: {priority}\n"
        f"complexity:\n"
        f"status: {status}\n"
        f"tags: {tags_str}\n"
        f"scope:\n"
        f"  in:\n"
        f"  out:\n"
        f"feature_root_id:\n"
        f"---\n"
        f"\n"
        f"# {name}\n"
        f"\n"
        f"## Objective\n"
        f"{extra_body}\n"
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _make_legacy_spec(path: str, heading: str = "Legacy Feature") -> None:
    """Write a legacy spec file (no frontmatter) to *path*."""
    content = f"# {heading}\n\nSome legacy content.\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


class _TempDirTest(unittest.TestCase):
    """Base class that runs each test inside a fresh temporary directory."""

    def setUp(self) -> None:
        self._orig_dir = os.getcwd()
        self._tmpdir = tempfile.mkdtemp()
        os.chdir(self._tmpdir)

    def tearDown(self) -> None:
        os.chdir(self._orig_dir)
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Convenience initialiser
    # ------------------------------------------------------------------

    def _init_specs(self) -> None:
        """Create the specs/ directory tree expected by most commands."""
        os.makedirs(os.path.join("specs", "drafts"))
        os.makedirs(os.path.join("specs", "planned"))
        os.makedirs(os.path.join("specs", "done"))


# ---------------------------------------------------------------------------
# _split_frontmatter
# ---------------------------------------------------------------------------


class TestSplitFrontmatter(unittest.TestCase):
    def test_valid_frontmatter(self):
        content = "---\nname: Foo\n---\nbody here\n"
        fm_str, body = spec_mod._split_frontmatter(content)
        self.assertEqual(fm_str, "name: Foo")
        self.assertIn("body here", body)

    def test_no_frontmatter_returns_none(self):
        content = "# Just a heading\n\nBody text.\n"
        fm_str, body = spec_mod._split_frontmatter(content)
        self.assertIsNone(fm_str)
        self.assertEqual(body, content)

    def test_unclosed_frontmatter_treated_as_legacy(self):
        content = "---\nname: Foo\nno closing marker\n"
        fm_str, body = spec_mod._split_frontmatter(content)
        self.assertIsNone(fm_str)

    def test_empty_frontmatter_block(self):
        content = "---\n---\nbody\n"
        fm_str, body = spec_mod._split_frontmatter(content)
        self.assertEqual(fm_str, "")
        self.assertIn("body", body)


# ---------------------------------------------------------------------------
# parse_frontmatter
# ---------------------------------------------------------------------------


class TestParseFrontmatter(_TempDirTest):
    def test_valid_spec(self):
        path = "test.md"
        _make_spec(path)
        fm, body = spec_mod.parse_frontmatter(path)
        self.assertIsNotNone(fm)
        self.assertEqual(fm["id"], "spec-aabbccdd")
        self.assertEqual(fm["status"], "draft")

    def test_legacy_spec_returns_none_frontmatter(self):
        path = "legacy.md"
        _make_legacy_spec(path)
        fm, body = spec_mod.parse_frontmatter(path)
        self.assertIsNone(fm)
        self.assertIn("Legacy Feature", body)

    def test_invalid_yaml_raises_frontmatter_error(self):
        path = "bad.md"
        with open(path, "w") as fh:
            fh.write("---\n: invalid: yaml: [\n---\nbody\n")
        with self.assertRaises(spec_mod.FrontmatterError):
            spec_mod.parse_frontmatter(path)


# ---------------------------------------------------------------------------
# is_legacy_spec
# ---------------------------------------------------------------------------


class TestIsLegacySpec(_TempDirTest):
    def test_modern_spec_not_legacy(self):
        path = "modern.md"
        _make_spec(path)
        self.assertFalse(spec_mod.is_legacy_spec(path))

    def test_legacy_spec_is_legacy(self):
        path = "legacy.md"
        _make_legacy_spec(path)
        self.assertTrue(spec_mod.is_legacy_spec(path))

    def test_unclosed_frontmatter_is_legacy(self):
        path = "unclosed.md"
        with open(path, "w") as fh:
            fh.write("---\nname: Foo\n")
        self.assertTrue(spec_mod.is_legacy_spec(path))


# ---------------------------------------------------------------------------
# infer_display_name
# ---------------------------------------------------------------------------


class TestInferDisplayName(_TempDirTest):
    def test_uses_name_from_frontmatter(self):
        path = "spec.md"
        _make_spec(path, name="My Spec Name")
        fm, _ = spec_mod.parse_frontmatter(path)
        result = spec_mod.infer_display_name(path, fm)
        self.assertEqual(result, "My Spec Name")

    def test_uses_heading_when_no_name(self):
        path = "spec.md"
        with open(path, "w") as fh:
            fh.write("---\nid: spec-x\n---\n# From The Heading\n")
        fm, _ = spec_mod.parse_frontmatter(path)
        result = spec_mod.infer_display_name(path, fm)
        self.assertEqual(result, "From The Heading")

    def test_falls_back_to_filename(self):
        path = "my-cool-spec.md"
        with open(path, "w") as fh:
            fh.write("No heading here.\n")
        result = spec_mod.infer_display_name(path, None)
        self.assertEqual(result, "My Cool Spec")


# ---------------------------------------------------------------------------
# _title_to_filename
# ---------------------------------------------------------------------------


class TestTitleToFilename(unittest.TestCase):
    def test_simple_title(self):
        self.assertEqual(spec_mod._title_to_filename("Hello World"), "hello-world.md")

    def test_strips_special_chars(self):
        self.assertEqual(spec_mod._title_to_filename("Foo & Bar!"), "foo-bar.md")

    def test_multiple_spaces_become_single_hyphen(self):
        self.assertEqual(spec_mod._title_to_filename("  A   B  "), "a-b.md")

    def test_consecutive_hyphens_collapsed(self):
        self.assertEqual(spec_mod._title_to_filename("A--B"), "a-b.md")


# ---------------------------------------------------------------------------
# write_frontmatter
# ---------------------------------------------------------------------------


class TestWriteFrontmatter(_TempDirTest):
    def test_rewrites_existing_frontmatter(self):
        path = "spec.md"
        _make_spec(path, name="Old Name")
        fm, _ = spec_mod.parse_frontmatter(path)
        fm["name"] = "New Name"
        spec_mod.write_frontmatter(path, fm)
        fm2, _ = spec_mod.parse_frontmatter(path)
        self.assertEqual(fm2["name"], "New Name")

    def test_prepends_frontmatter_to_legacy_file(self):
        path = "legacy.md"
        _make_legacy_spec(path)
        spec_mod.write_frontmatter(path, {"name": "Migrated", "id": "spec-1234"})
        fm, body = spec_mod.parse_frontmatter(path)
        self.assertIsNotNone(fm)
        self.assertEqual(fm["name"], "Migrated")
        self.assertIn("Legacy Feature", body)


# ---------------------------------------------------------------------------
# find_all_specs
# ---------------------------------------------------------------------------


class TestFindAllSpecs(_TempDirTest):
    def test_returns_sorted_paths_across_lifecycle_dirs(self):
        self._init_specs()
        _make_spec(os.path.join("specs", "drafts", "a.md"), spec_id="spec-0001")
        _make_spec(os.path.join("specs", "planned", "b.md"), spec_id="spec-0002")
        _make_spec(os.path.join("specs", "done", "c.md"), spec_id="spec-0003")
        paths = spec_mod.find_all_specs()
        basenames = [os.path.basename(p) for p in paths]
        self.assertIn("a.md", basenames)
        self.assertIn("b.md", basenames)
        self.assertIn("c.md", basenames)

    def test_ignores_non_md_files(self):
        self._init_specs()
        with open(os.path.join("specs", "drafts", "notes.txt"), "w") as fh:
            fh.write("not a spec")
        paths = spec_mod.find_all_specs()
        self.assertEqual(paths, [])

    def test_missing_dir_returns_empty(self):
        # Only drafts exists
        os.makedirs(os.path.join("specs", "drafts"))
        _make_spec(os.path.join("specs", "drafts", "x.md"))
        paths = spec_mod.find_all_specs()
        self.assertEqual(len(paths), 1)

    def test_discovers_arbitrary_status_dir(self):
        self._init_specs()
        os.makedirs(os.path.join("specs", "postponed"))
        _make_spec(os.path.join("specs", "postponed", "p.md"), spec_id="spec-0010")
        paths = spec_mod.find_all_specs()
        basenames = [os.path.basename(p) for p in paths]
        self.assertIn("p.md", basenames)

    def test_ignores_dotdirs(self):
        self._init_specs()
        os.makedirs(os.path.join("specs", ".hidden"))
        _make_spec(os.path.join("specs", ".hidden", "secret.md"), spec_id="spec-0011")
        paths = spec_mod.find_all_specs()
        basenames = [os.path.basename(p) for p in paths]
        self.assertNotIn("secret.md", basenames)


# ---------------------------------------------------------------------------
# resolve_spec
# ---------------------------------------------------------------------------


class TestResolveSpec(_TempDirTest):
    def setUp(self):
        super().setUp()
        self._init_specs()

    def test_exact_id_match(self):
        spec_path = os.path.join("specs", "drafts", "my-spec.md")
        _make_spec(spec_path, spec_id="spec-deadbeef")
        result = spec_mod.resolve_spec("spec-deadbeef")
        self.assertEqual(os.path.abspath(result), os.path.abspath(spec_path))

    def test_partial_filename_match(self):
        spec_path = os.path.join("specs", "drafts", "my-unique-spec.md")
        _make_spec(spec_path, spec_id="spec-11111111")
        result = spec_mod.resolve_spec("unique")
        self.assertEqual(os.path.abspath(result), os.path.abspath(spec_path))

    def test_no_match_exits_with_error(self):
        with self.assertRaises(SystemExit) as cm:
            with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
                spec_mod.resolve_spec("nonexistent-query")
        self.assertEqual(cm.exception.code, 1)

    def test_no_match_prints_error_message(self):
        with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
            with self.assertRaises(SystemExit):
                spec_mod.resolve_spec("no-such-spec-xyz")
            self.assertIn('no spec matching "no-such-spec-xyz"', mock_err.getvalue())

    def test_ambiguous_match_exits_with_error(self):
        _make_spec(os.path.join("specs", "drafts", "foo-alpha.md"), spec_id="spec-aaaa0001")
        _make_spec(os.path.join("specs", "drafts", "foo-beta.md"), spec_id="spec-aaaa0002")
        with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
            with self.assertRaises(SystemExit) as cm:
                spec_mod.resolve_spec("foo")
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("matches multiple specs", mock_err.getvalue())

    def test_exact_id_wins_over_ambiguous_filename(self):
        """Exact ID match should return immediately, bypassing filename matching."""
        _make_spec(os.path.join("specs", "drafts", "foo-one.md"), spec_id="spec-exact01")
        _make_spec(os.path.join("specs", "drafts", "foo-two.md"), spec_id="spec-exact02")
        # Query matches both filenames, but one exact ID
        result = spec_mod.resolve_spec("spec-exact01")
        self.assertIn("foo-one.md", result)


# ---------------------------------------------------------------------------
# cmd_init
# ---------------------------------------------------------------------------


class TestCmdInit(_TempDirTest):
    def _run_init(self):
        args = argparse.Namespace()
        spec_mod.cmd_init(args)

    def test_creates_specs_directory_structure(self):
        self._run_init()
        self.assertTrue(os.path.isdir("specs/drafts"))
        self.assertTrue(os.path.isdir("specs/planned"))
        self.assertTrue(os.path.isdir("specs/done"))

    def test_prints_success_message(self):
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            self._run_init()
            self.assertIn("Initialised specs/", mock_out.getvalue())

    def test_creates_spec_template_file(self):
        self._run_init()
        self.assertTrue(os.path.isfile(os.path.join("specs", "spec-template.md")))

    def test_spec_template_is_body_only(self):
        self._run_init()
        with open(os.path.join("specs", "spec-template.md"), encoding="utf-8") as fh:
            content = fh.read()
        self.assertNotIn("\n---\n", content)       # no frontmatter delimiters
        self.assertNotIn("{spec_id}", content)    # no frontmatter placeholders
        self.assertIn("{name}", content)          # title placeholder in body is ok

    def test_errors_if_specs_already_exists(self):
        self._run_init()
        with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
            with self.assertRaises(SystemExit) as cm:
                self._run_init()
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("already exists", mock_err.getvalue())


# ---------------------------------------------------------------------------
# cmd_create
# ---------------------------------------------------------------------------


class TestCmdCreate(_TempDirTest):
    def setUp(self):
        super().setUp()
        self._init_specs()

    def _run_create(self, title: str):
        args = argparse.Namespace(title=title)
        spec_mod.cmd_create(args)

    def test_creates_file_in_drafts(self):
        self._run_create("My New Feature")
        files = os.listdir("specs/drafts")
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].endswith(".md"))
        self.assertIn("my-new-feature", files[0])

    def test_file_has_valid_frontmatter(self):
        self._run_create("Test Spec")
        files = os.listdir("specs/drafts")
        path = os.path.join("specs", "drafts", files[0])
        fm, _ = spec_mod.parse_frontmatter(path)
        self.assertIsNotNone(fm)
        self.assertEqual(fm["name"], "Test Spec")
        self.assertEqual(fm["status"], "draft")
        self.assertTrue(fm["id"].startswith("spec-"))

    def test_prints_created_path_and_id(self):
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            self._run_create("Some Spec")
            output = mock_out.getvalue()
            self.assertIn("Created", output)
            self.assertIn("ID: spec-", output)

    def test_errors_if_file_already_exists(self):
        self._run_create("Duplicate Spec")
        with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
            with self.assertRaises(SystemExit) as cm:
                self._run_create("Duplicate Spec")
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("already exists", mock_err.getvalue())

    def test_errors_if_specs_dir_missing(self):
        import shutil
        shutil.rmtree("specs")
        with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
            with self.assertRaises(SystemExit) as cm:
                self._run_create("No Dir Spec")
            self.assertEqual(cm.exception.code, 1)

    def test_uses_custom_template_if_present(self):
        custom = "# {name}\n\nCustom body.\n"
        with open(os.path.join("specs", "spec-template.md"), "w", encoding="utf-8") as fh:
            fh.write(custom)
        self._run_create("Template Test")
        files = os.listdir("specs/drafts")
        path = os.path.join("specs", "drafts", files[0])
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("Custom body.", content)
        self.assertIn("Template Test", content)
        # Frontmatter must always be present regardless of template content
        fm, _ = spec_mod.parse_frontmatter(path)
        self.assertIsNotNone(fm)
        self.assertEqual(fm["status"], "draft")
        self.assertTrue(fm["id"].startswith("spec-"))

    def test_falls_back_to_builtin_template_when_file_absent(self):
        # _init_specs() does not create spec-template.md — fallback should apply
        self._run_create("Fallback Test")
        files = os.listdir("specs/drafts")
        path = os.path.join("specs", "drafts", files[0])
        fm, _ = spec_mod.parse_frontmatter(path)
        self.assertEqual(fm["name"], "Fallback Test")


# ---------------------------------------------------------------------------
# cmd_list
# ---------------------------------------------------------------------------


class TestCmdList(_TempDirTest):
    def setUp(self):
        super().setUp()
        self._init_specs()

    def _run_list(self, status=None, tag=None, priority=None):
        args = argparse.Namespace(status=status, tag=tag, priority=priority)
        spec_mod.cmd_list(args)

    def test_lists_specs_from_all_dirs(self):
        _make_spec(os.path.join("specs", "drafts", "a.md"), name="Alpha", spec_id="spec-0001", status="draft")
        _make_spec(os.path.join("specs", "planned", "b.md"), name="Beta", spec_id="spec-0002", status="planned")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            self._run_list()
            output = mock_out.getvalue()
            self.assertIn("Alpha", output)
            self.assertIn("Beta", output)

    def test_no_specs_prints_message(self):
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            self._run_list()
            self.assertIn("No specs found", mock_out.getvalue())

    def test_filter_by_status(self):
        _make_spec(os.path.join("specs", "drafts", "a.md"), name="Draft One", spec_id="spec-d001", status="draft")
        _make_spec(os.path.join("specs", "planned", "b.md"), name="Planned One", spec_id="spec-p001", status="planned")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            self._run_list(status="draft")
            output = mock_out.getvalue()
            self.assertIn("Draft One", output)
            self.assertNotIn("Planned One", output)

    def test_filter_by_tag(self):
        _make_spec(os.path.join("specs", "drafts", "a.md"), name="Tagged", spec_id="spec-t001", tags=["cli", "spec"])
        _make_spec(os.path.join("specs", "drafts", "b.md"), name="Not Tagged", spec_id="spec-t002", tags=[])
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            self._run_list(tag="cli")
            output = mock_out.getvalue()
            self.assertIn("Tagged", output)
            self.assertNotIn("Not Tagged", output)

    def test_filter_by_priority(self):
        _make_spec(os.path.join("specs", "drafts", "a.md"), name="High Pri", spec_id="spec-h001", priority="high")
        _make_spec(os.path.join("specs", "drafts", "b.md"), name="Low Pri", spec_id="spec-l001", priority="low")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            self._run_list(priority="high")
            output = mock_out.getvalue()
            self.assertIn("High Pri", output)
            self.assertNotIn("Low Pri", output)

    def test_legacy_spec_shown_with_legacy_status(self):
        _make_legacy_spec(os.path.join("specs", "drafts", "legacy.md"), heading="Old Feature")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            self._run_list()
            output = mock_out.getvalue()
            self.assertIn("legacy", output)

    def test_shows_column_headers(self):
        _make_spec(os.path.join("specs", "drafts", "x.md"), spec_id="spec-x001")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            self._run_list()
            output = mock_out.getvalue()
            self.assertIn("id", output)
            self.assertIn("status", output)
            self.assertIn("priority", output)
            self.assertIn("complexity", output)


# ---------------------------------------------------------------------------
# cmd_show
# ---------------------------------------------------------------------------


class TestCmdShow(_TempDirTest):
    def setUp(self):
        super().setUp()
        self._init_specs()

    def _run_show(self, spec_query: str, full: bool = False):
        args = argparse.Namespace(spec=spec_query, full=full)
        spec_mod.cmd_show(args)

    def test_shows_frontmatter_block(self):
        _make_spec(os.path.join("specs", "drafts", "my-spec.md"), name="Show Me", spec_id="spec-show01")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            self._run_show("spec-show01")
            output = mock_out.getvalue()
            self.assertIn("---", output)
            self.assertIn("Show Me", output)

    def test_legacy_spec_prints_warning(self):
        _make_legacy_spec(os.path.join("specs", "drafts", "legacy-show.md"), heading="Legacy Show")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            self._run_show("legacy-show")
            output = mock_out.getvalue()
            self.assertIn("legacy spec", output)

    def test_nonexistent_spec_exits_1(self):
        with patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as cm:
                self._run_show("does-not-exist")
            self.assertEqual(cm.exception.code, 1)


# ---------------------------------------------------------------------------
# cmd_set
# ---------------------------------------------------------------------------


class TestCmdSetStatus(_TempDirTest):
    def setUp(self):
        super().setUp()
        self._init_specs()

    def _make_draft(self, filename="my-spec.md", spec_id="spec-settest"):
        path = os.path.join("specs", "drafts", filename)
        _make_spec(path, spec_id=spec_id, status="draft")
        return path

    def test_set_status_planned_moves_file(self):
        self._make_draft(spec_id="spec-mv0001")
        args = argparse.Namespace(spec="spec-mv0001", field="status", value="planned")
        spec_mod.cmd_set(args)
        self.assertTrue(os.path.exists(os.path.join("specs", "planned", "my-spec.md")))
        self.assertFalse(os.path.exists(os.path.join("specs", "drafts", "my-spec.md")))

    def test_set_status_updates_frontmatter(self):
        self._make_draft(spec_id="spec-fm0001")
        args = argparse.Namespace(spec="spec-fm0001", field="status", value="planned")
        spec_mod.cmd_set(args)
        new_path = os.path.join("specs", "planned", "my-spec.md")
        fm, _ = spec_mod.parse_frontmatter(new_path)
        self.assertEqual(fm["status"], "planned")

    def test_set_status_done_moves_to_done_dir(self):
        self._make_draft(filename="done-spec.md", spec_id="spec-done01")
        args = argparse.Namespace(spec="spec-done01", field="status", value="done")
        spec_mod.cmd_set(args)
        self.assertTrue(os.path.exists(os.path.join("specs", "done", "done-spec.md")))

    def test_set_status_prints_new_path(self):
        self._make_draft(spec_id="spec-print01")
        args = argparse.Namespace(spec="spec-print01", field="status", value="planned")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            spec_mod.cmd_set(args)
            self.assertIn("planned", mock_out.getvalue())

    def test_set_status_arbitrary_creates_dir_and_moves_file(self):
        self._make_draft(filename="my-spec.md", spec_id="spec-arb01")
        args = argparse.Namespace(spec="spec-arb01", field="status", value="postponed")
        spec_mod.cmd_set(args)
        self.assertTrue(os.path.exists(os.path.join("specs", "postponed", "my-spec.md")))
        self.assertFalse(os.path.exists(os.path.join("specs", "drafts", "my-spec.md")))

    def test_set_status_arbitrary_updates_frontmatter(self):
        self._make_draft(filename="my-spec.md", spec_id="spec-arb02")
        args = argparse.Namespace(spec="spec-arb02", field="status", value="blocked")
        spec_mod.cmd_set(args)
        new_path = os.path.join("specs", "blocked", "my-spec.md")
        fm, _ = spec_mod.parse_frontmatter(new_path)
        self.assertEqual(fm["status"], "blocked")

    def test_set_status_valid_hyphenated(self):
        self._make_draft(filename="my-spec.md", spec_id="spec-arb03")
        args = argparse.Namespace(spec="spec-arb03", field="status", value="in-progress")
        spec_mod.cmd_set(args)
        self.assertTrue(os.path.exists(os.path.join("specs", "in-progress", "my-spec.md")))

    def test_set_status_invalid_value_exits(self):
        self._make_draft(spec_id="spec-inv01")
        for bad in ["../../evil", "bad/status", "has space", ""]:
            with self.subTest(bad=bad):
                args = argparse.Namespace(spec="spec-inv01", field="status", value=bad)
                with patch("sys.stderr", new_callable=io.StringIO):
                    with self.assertRaises(SystemExit) as cm:
                        spec_mod.cmd_set(args)
                    self.assertEqual(cm.exception.code, 1)


class TestCmdSetOtherFields(_TempDirTest):
    def setUp(self):
        super().setUp()
        self._init_specs()
        self._path = os.path.join("specs", "drafts", "field-spec.md")
        _make_spec(self._path, spec_id="spec-field01")

    def _run_set(self, field, value):
        args = argparse.Namespace(spec="spec-field01", field=field, value=value)
        spec_mod.cmd_set(args)

    def test_set_feature_root(self):
        self._run_set("feature-root", "B-abc12345")
        fm, _ = spec_mod.parse_frontmatter(self._path)
        self.assertEqual(fm["feature_root_id"], "B-abc12345")

    def test_set_tags(self):
        self._run_set("tags", "api,backend,cli")
        fm, _ = spec_mod.parse_frontmatter(self._path)
        self.assertEqual(fm["tags"], ["api", "backend", "cli"])

    def test_set_priority(self):
        self._run_set("priority", "high")
        fm, _ = spec_mod.parse_frontmatter(self._path)
        self.assertEqual(fm["priority"], "high")

    def test_set_description(self):
        self._run_set("description", "A short description.")
        fm, _ = spec_mod.parse_frontmatter(self._path)
        self.assertEqual(fm["description"], "A short description.")

    def test_unknown_field_exits_1(self):
        with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
            with self.assertRaises(SystemExit) as cm:
                self._run_set("unknown-field", "value")
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("unknown field", mock_err.getvalue())

    def test_set_on_legacy_spec_exits_with_migrate_hint(self):
        legacy_path = os.path.join("specs", "drafts", "legacy-set.md")
        _make_legacy_spec(legacy_path, heading="Legacy Set")
        args = argparse.Namespace(spec="legacy-set", field="priority", value="high")
        with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
            with self.assertRaises(SystemExit) as cm:
                spec_mod.cmd_set(args)
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("migrate", mock_err.getvalue())


# ---------------------------------------------------------------------------
# cmd_migrate
# ---------------------------------------------------------------------------


class TestCmdMigrate(_TempDirTest):
    def setUp(self):
        super().setUp()
        self._init_specs()

    def test_migrates_legacy_spec(self):
        legacy_path = os.path.join("specs", "drafts", "legacy-migrate.md")
        _make_legacy_spec(legacy_path, heading="Legacy Migrate")
        args = argparse.Namespace(spec="legacy-migrate")
        spec_mod.cmd_migrate(args)
        self.assertFalse(spec_mod.is_legacy_spec(legacy_path))

    def test_migrated_spec_has_correct_fields(self):
        legacy_path = os.path.join("specs", "drafts", "migrate-fields.md")
        _make_legacy_spec(legacy_path, heading="Migrate Fields")
        args = argparse.Namespace(spec="migrate-fields")
        spec_mod.cmd_migrate(args)
        fm, _ = spec_mod.parse_frontmatter(legacy_path)
        self.assertIsNotNone(fm)
        self.assertIn("id", fm)
        self.assertTrue(fm["id"].startswith("spec-"))
        self.assertEqual(fm["status"], "draft")  # in drafts dir

    def test_migrate_infers_name_from_heading(self):
        legacy_path = os.path.join("specs", "drafts", "named-legacy.md")
        _make_legacy_spec(legacy_path, heading="Inferred Name")
        args = argparse.Namespace(spec="named-legacy")
        spec_mod.cmd_migrate(args)
        fm, _ = spec_mod.parse_frontmatter(legacy_path)
        self.assertEqual(fm["name"], "Inferred Name")

    def test_migrate_prints_id(self):
        legacy_path = os.path.join("specs", "drafts", "printid-legacy.md")
        _make_legacy_spec(legacy_path)
        args = argparse.Namespace(spec="printid-legacy")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            spec_mod.cmd_migrate(args)
            output = mock_out.getvalue()
            self.assertIn("Migrated", output)
            self.assertIn("ID: spec-", output)

    def test_migrate_errors_if_already_has_frontmatter(self):
        modern_path = os.path.join("specs", "drafts", "modern-spec.md")
        _make_spec(modern_path, spec_id="spec-nomigrte")
        args = argparse.Namespace(spec="spec-nomigrte")
        with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
            with self.assertRaises(SystemExit) as cm:
                spec_mod.cmd_migrate(args)
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("already has frontmatter", mock_err.getvalue())

    def test_migrate_infers_status_from_directory(self):
        planned_path = os.path.join("specs", "planned", "in-planned.md")
        _make_legacy_spec(planned_path, heading="Planned Legacy")
        args = argparse.Namespace(spec="in-planned")
        spec_mod.cmd_migrate(args)
        fm, _ = spec_mod.parse_frontmatter(planned_path)
        self.assertEqual(fm["status"], "planned")


# ---------------------------------------------------------------------------
# cmd_remove
# ---------------------------------------------------------------------------


class TestCmdRemove(_TempDirTest):
    def setUp(self):
        super().setUp()
        self._init_specs()

    def _make_spec_in(self, folder, filename, spec_id, status):
        path = os.path.join("specs", folder, filename)
        _make_spec(path, spec_id=spec_id, status=status)
        return path

    def test_removes_draft_without_confirmation(self):
        path = self._make_spec_in("drafts", "to-remove.md", "spec-rm0001", "draft")
        args = argparse.Namespace(spec="spec-rm0001", force=False)
        spec_mod.cmd_remove(args)
        self.assertFalse(os.path.exists(path))

    def test_removes_planned_with_force(self):
        path = self._make_spec_in("planned", "to-remove.md", "spec-rm0002", "planned")
        args = argparse.Namespace(spec="spec-rm0002", force=True)
        spec_mod.cmd_remove(args)
        self.assertFalse(os.path.exists(path))

    def test_removes_done_with_force(self):
        path = self._make_spec_in("done", "to-remove.md", "spec-rm0003", "done")
        args = argparse.Namespace(spec="spec-rm0003", force=True)
        spec_mod.cmd_remove(args)
        self.assertFalse(os.path.exists(path))

    def test_planned_without_force_confirms_yes(self):
        path = self._make_spec_in("planned", "to-remove.md", "spec-rm0004", "planned")
        args = argparse.Namespace(spec="spec-rm0004", force=False)
        with patch("builtins.input", return_value="y"):
            spec_mod.cmd_remove(args)
        self.assertFalse(os.path.exists(path))

    def test_planned_without_force_confirms_no(self):
        path = self._make_spec_in("planned", "to-remove.md", "spec-rm0005", "planned")
        args = argparse.Namespace(spec="spec-rm0005", force=False)
        with patch("builtins.input", return_value="n"):
            spec_mod.cmd_remove(args)
        self.assertTrue(os.path.exists(path))

    def test_arbitrary_status_requires_confirmation(self):
        os.makedirs(os.path.join("specs", "postponed"))
        path = os.path.join("specs", "postponed", "to-remove.md")
        _make_spec(path, spec_id="spec-rm0006", status="postponed")
        args = argparse.Namespace(spec="spec-rm0006", force=False)
        with patch("builtins.input", return_value="n"):
            spec_mod.cmd_remove(args)
        self.assertTrue(os.path.exists(path))

    def test_prints_removed_path(self):
        self._make_spec_in("drafts", "to-remove.md", "spec-rm0007", "draft")
        args = argparse.Namespace(spec="spec-rm0007", force=False)
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            spec_mod.cmd_remove(args)
            self.assertIn("Removed", mock_out.getvalue())


# ---------------------------------------------------------------------------
# _status_from_path
# ---------------------------------------------------------------------------


class TestStatusFromPath(_TempDirTest):
    def setUp(self):
        super().setUp()
        self._init_specs()

    def test_draft_dir(self):
        path = os.path.join("specs", "drafts", "x.md")
        self.assertEqual(spec_mod._status_from_path(path), "draft")

    def test_planned_dir(self):
        path = os.path.join("specs", "planned", "x.md")
        self.assertEqual(spec_mod._status_from_path(path), "planned")

    def test_done_dir(self):
        path = os.path.join("specs", "done", "x.md")
        self.assertEqual(spec_mod._status_from_path(path), "done")

    def test_unknown_dir_falls_back_to_draft(self):
        path = os.path.join("specs", "x.md")
        self.assertEqual(spec_mod._status_from_path(path), "draft")

    def test_arbitrary_status_dir(self):
        os.makedirs(os.path.join("specs", "postponed"))
        path = os.path.join("specs", "postponed", "x.md")
        self.assertEqual(spec_mod._status_from_path(path), "postponed")

    def test_arbitrary_hyphenated_status_dir(self):
        os.makedirs(os.path.join("specs", "in-progress"))
        path = os.path.join("specs", "in-progress", "x.md")
        self.assertEqual(spec_mod._status_from_path(path), "in-progress")


# ---------------------------------------------------------------------------
# _normalize_tags
# ---------------------------------------------------------------------------


class TestNormalizeTags(unittest.TestCase):
    def test_list_passthrough(self):
        self.assertEqual(spec_mod._normalize_tags(["a", "b"]), ["a", "b"])

    def test_comma_string(self):
        self.assertEqual(spec_mod._normalize_tags("a, b, c"), ["a", "b", "c"])

    def test_none_returns_empty(self):
        self.assertEqual(spec_mod._normalize_tags(None), [])

    def test_empty_list_returns_empty(self):
        self.assertEqual(spec_mod._normalize_tags([]), [])


# ---------------------------------------------------------------------------
# Guard functions
# ---------------------------------------------------------------------------


class TestGuards(_TempDirTest):
    def test_require_specs_dir_exits_when_missing(self):
        with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
            with self.assertRaises(SystemExit) as cm:
                spec_mod._require_specs_dir()
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("spec init", mock_err.getvalue())

    def test_require_specs_dir_passes_when_present(self):
        os.makedirs("specs")
        spec_mod._require_specs_dir()  # should not raise

    def test_require_lifecycle_dirs_exits_when_missing_subdir(self):
        os.makedirs("specs")  # No subdirs
        with patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as cm:
                spec_mod._require_lifecycle_dirs()
            self.assertEqual(cm.exception.code, 1)

    def test_require_lifecycle_dirs_passes_when_complete(self):
        self._init_specs()
        spec_mod._require_lifecycle_dirs()  # should not raise


# ---------------------------------------------------------------------------
# build_parser smoke test
# ---------------------------------------------------------------------------


class TestBuildParser(unittest.TestCase):
    def test_returns_argument_parser(self):
        parser = spec_mod.build_parser()
        self.assertIsInstance(parser, argparse.ArgumentParser)

    def test_init_subcommand_recognised(self):
        parser = spec_mod.build_parser()
        args = parser.parse_args(["init"])
        self.assertEqual(args.subcommand, "init")
        self.assertEqual(args.func, spec_mod.cmd_init)

    def test_create_subcommand_recognised(self):
        parser = spec_mod.build_parser()
        args = parser.parse_args(["create", "My Title"])
        self.assertEqual(args.subcommand, "create")
        self.assertEqual(args.title, "My Title")

    def test_list_filters_parsed(self):
        parser = spec_mod.build_parser()
        args = parser.parse_args(["list", "--status", "draft", "--tag", "api", "--priority", "high"])
        self.assertEqual(args.status, "draft")
        self.assertEqual(args.tag, "api")
        self.assertEqual(args.priority, "high")

    def test_show_subcommand_recognised(self):
        parser = spec_mod.build_parser()
        args = parser.parse_args(["show", "spec-abc"])
        self.assertEqual(args.subcommand, "show")
        self.assertEqual(args.spec, "spec-abc")

    def test_set_status_subcommand(self):
        parser = spec_mod.build_parser()
        args = parser.parse_args(["set", "status", "planned", "spec-abc"])
        self.assertEqual(args.field, "status")
        self.assertEqual(args.value, "planned")
        self.assertEqual(args.spec, "spec-abc")

    def test_set_feature_root_subcommand(self):
        parser = spec_mod.build_parser()
        args = parser.parse_args(["set", "feature-root", "B-abc", "spec-abc"])
        self.assertEqual(args.field, "feature-root")
        self.assertEqual(args.value, "B-abc")

    def test_migrate_subcommand_recognised(self):
        parser = spec_mod.build_parser()
        args = parser.parse_args(["migrate", "spec-abc"])
        self.assertEqual(args.subcommand, "migrate")
        self.assertEqual(args.spec, "spec-abc")


# ---------------------------------------------------------------------------
# _status_to_dir
# ---------------------------------------------------------------------------


class TestStatusToDir(unittest.TestCase):
    def test_draft_uses_drafts_dir(self):
        self.assertEqual(spec_mod._status_to_dir("draft"), os.path.join("specs", "drafts"))

    def test_planned_uses_planned_dir(self):
        self.assertEqual(spec_mod._status_to_dir("planned"), os.path.join("specs", "planned"))

    def test_done_uses_done_dir(self):
        self.assertEqual(spec_mod._status_to_dir("done"), os.path.join("specs", "done"))

    def test_arbitrary_uses_specs_slash_status(self):
        self.assertEqual(spec_mod._status_to_dir("postponed"), os.path.join("specs", "postponed"))

    def test_arbitrary_hyphenated(self):
        self.assertEqual(spec_mod._status_to_dir("in-progress"), os.path.join("specs", "in-progress"))


if __name__ == "__main__":
    unittest.main()
