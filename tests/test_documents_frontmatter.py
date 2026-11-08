"""Tests for agent_takt.documents.frontmatter (shared ADR / spec frontmatter I/O)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.documents.frontmatter import (
    ADR_STYLE,
    SPEC_STYLE,
    FrontmatterError,
    read_frontmatter,
    read_frontmatter_file,
    render_document,
    split_frontmatter,
    update_frontmatter,
    update_frontmatter_file,
    write_document,
)

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "documents"

SPEC_TEXT = (
    "---\n"
    "# a comment\n"
    "name: Demo\n"
    "id: spec-0123abcd\n"
    "description: null\n"
    "priority: low   # trailing\n"
    "status: draft\n"
    "tags:\n"
    "  - a\n"
    "  - b\n"
    "scope:\n"
    "  in: x\n"
    "  out: y\n"
    "---\n"
    "\n# Body\n\n---\n\nmore\n"
)


class TestReading(unittest.TestCase):
    def test_no_frontmatter_returns_none_and_text(self):
        fm, body = read_frontmatter("# just a heading\n")
        self.assertIsNone(fm)
        self.assertEqual(body, "# just a heading\n")

    def test_unclosed_frontmatter_is_not_frontmatter(self):
        fm, body = read_frontmatter("---\nid: x\nno close\n")
        self.assertIsNone(fm)

    def test_body_returned_raw(self):
        fm, body = read_frontmatter("---\nid: x\n---\n\nbody\n")
        self.assertEqual(fm, {"id": "x"})
        self.assertEqual(body, "\nbody\n")

    def test_body_with_dashes_untouched(self):
        fm, body = read_frontmatter(SPEC_TEXT)
        self.assertEqual(fm["id"], "spec-0123abcd")
        self.assertEqual(body, "\n# Body\n\n---\n\nmore\n")

    def test_crlf_input(self):
        text = "---\r\nid: x\r\nname: Y\r\n---\r\nbody\r\n"
        fm, body = read_frontmatter(text)
        self.assertEqual(fm, {"id": "x", "name": "Y"})
        self.assertEqual(body, "body\r\n")

    def test_malformed_yaml_raises_with_path(self):
        with self.assertRaises(FrontmatterError) as ctx:
            read_frontmatter("---\nkey: [unclosed\n---\nbody\n", path="specs/x.md")
        self.assertEqual(ctx.exception.path, "specs/x.md")
        self.assertIn("specs/x.md", str(ctx.exception))

    def test_non_mapping_frontmatter_raises_with_path(self):
        with self.assertRaises(FrontmatterError) as ctx:
            read_frontmatter("---\n- a\n- b\n---\nbody\n", path="p.md")
        self.assertEqual(ctx.exception.path, "p.md")
        self.assertIn("not a YAML mapping", ctx.exception.message)

    def test_empty_frontmatter_is_empty_mapping(self):
        fm, _ = read_frontmatter("---\n---\nbody\n")
        self.assertEqual(fm, {})

    def test_read_file_sets_path_on_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.md"
            p.write_text("---\na: [\n---\n", encoding="utf-8")
            with self.assertRaises(FrontmatterError) as ctx:
                read_frontmatter_file(p)
            self.assertEqual(ctx.exception.path, str(p))

    def test_read_file_missing_raises_frontmatter_error(self):
        with self.assertRaises(FrontmatterError):
            read_frontmatter_file("/nonexistent/zzz.md")

    def test_split_frontmatter(self):
        raw, body = split_frontmatter("---\na: 1\n---\nrest")
        self.assertEqual(raw, "a: 1\n")
        self.assertEqual(body, "rest")


class TestAdrStyle(unittest.TestCase):
    def test_render_layout_matches_legacy_adr(self):
        data = {"id": "ADR-1", "title": "T", "status": "draft", "tags": ["a"], "x": None}
        expected_fm = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
        self.assertEqual(
            render_document(data, "body\n", ADR_STYLE), f"---\n{expected_fm}---\n\nbody\n"
        )

    def test_update_redumps_in_order_and_strips_leading_newlines(self):
        text = "---\nid: ADR-1\nstatus: draft\n---\n\n\nbody\n"
        out = update_frontmatter(text, {"status": "approved", "accepted_at": "now"}, ADR_STYLE)
        fm, body = read_frontmatter(out)
        self.assertEqual(list(fm), ["id", "status", "accepted_at"])
        self.assertEqual(body, "\nbody\n")
        self.assertTrue(out.startswith("---\nid: ADR-1\nstatus: approved\naccepted_at: now\n---\n\nbody"))

    def test_none_renders_as_null(self):
        out = render_document({"a": None}, "", ADR_STYLE)
        self.assertIn("a: null\n", out)


class TestSpecStyleUpdate(unittest.TestCase):
    def _changed_lines(self, before: str, after: str) -> tuple[list[str], list[str]]:
        b, a = before.splitlines(), after.splitlines()
        return [l for l in b if l not in a], [l for l in a if l not in b]

    def test_scalar_update_leaves_everything_else_identical(self):
        out = update_frontmatter(SPEC_TEXT, {"priority": "high"}, SPEC_STYLE)
        self.assertEqual(out, SPEC_TEXT.replace("priority: low   # trailing\n", "priority: high\n"))

    def test_block_list_update(self):
        out = update_frontmatter(SPEC_TEXT, {"tags": ["x", "y"]}, SPEC_STYLE)
        self.assertEqual(out, SPEC_TEXT.replace("tags:\n  - a\n  - b\n", "tags:\n- x\n- y\n"))

    def test_nested_mapping_update(self):
        out = update_frontmatter(SPEC_TEXT, {"scope": {"in": "p", "out": "q"}}, SPEC_STYLE)
        removed, added = self._changed_lines(SPEC_TEXT, out)
        self.assertEqual(sorted(removed), ["  in: x", "  out: y"])
        self.assertEqual(sorted(added), ["  in: p", "  out: q"])
        self.assertIn("# a comment\n", out)
        self.assertTrue(out.endswith("\n# Body\n\n---\n\nmore\n"))
        self.assertEqual(read_frontmatter(out)[0]["scope"], {"in": "p", "out": "q"})

    def test_comments_and_body_byte_identical(self):
        out = update_frontmatter(SPEC_TEXT, {"description": "hello"}, SPEC_STYLE)
        self.assertIn("# a comment\n", out)
        self.assertTrue(out.endswith("---\n\n# Body\n\n---\n\nmore\n"))

    def test_new_key_appended(self):
        out = update_frontmatter(SPEC_TEXT, {"feature_root_id": "B-1"}, SPEC_STYLE)
        self.assertIn("  out: y\nfeature_root_id: B-1\n---\n", out)

    def test_unchanged_value_is_noop(self):
        self.assertEqual(update_frontmatter(SPEC_TEXT, {"status": "draft"}, SPEC_STYLE), SPEC_TEXT)

    def test_crlf_preserved(self):
        text = "---\r\nid: x\r\nstatus: draft\r\n---\r\nbody\r\n"
        out = update_frontmatter(text, {"status": "planned"}, SPEC_STYLE)
        self.assertEqual(out, "---\r\nid: x\r\nstatus: planned\r\n---\r\nbody\r\n")

    def test_none_renders_null(self):
        out = update_frontmatter(SPEC_TEXT, {"priority": None}, SPEC_STYLE)
        self.assertIn("priority: null\n", out)
        self.assertIsNone(read_frontmatter(out)[0]["priority"])

    def test_tricky_strings_round_trip(self):
        for value in ["preflight, tests", "Gates: x", "true", "123", "", "- dash", "a # b",
                      "[x]", "it's \"quoted\"", "null", "#hash", "ünïcode"]:
            with self.subTest(value=value):
                out = update_frontmatter(SPEC_TEXT, {"description": value}, SPEC_STYLE)
                fm, body = read_frontmatter(out)
                self.assertEqual(fm["description"], value)
                self.assertIsInstance(fm["description"], str)
                self.assertEqual(fm["name"], "Demo")
                self.assertTrue(out.endswith("\n# Body\n\n---\n\nmore\n"))

    def test_long_scalar_not_wrapped(self):
        value = "word " * 100
        out = update_frontmatter(SPEC_TEXT, {"description": value.strip()}, SPEC_STYLE)
        line = [l for l in out.splitlines() if l.startswith("description:")]
        self.assertEqual(len(line), 1)
        self.assertEqual(read_frontmatter(out)[0]["description"], value.strip())

    def test_wrapped_plain_scalar_replaced_wholesale(self):
        text = "---\nid: x\ndescription: first\n  second\npriority: low\n---\nb\n"
        self.assertEqual(read_frontmatter(text)[0]["description"], "first second")
        out = update_frontmatter(text, {"description": "new"}, SPEC_STYLE)
        self.assertEqual(out, "---\nid: x\ndescription: new\npriority: low\n---\nb\n")

    def test_malformed_yaml_raises_with_path(self):
        with self.assertRaises(FrontmatterError) as ctx:
            update_frontmatter("---\na: [\n---\nb\n", {"a": 1}, SPEC_STYLE, path="z.md")
        self.assertEqual(ctx.exception.path, "z.md")

    def test_render_spec_style(self):
        out = render_document({"a": 1, "b": [1, 2], "c": {"d": None}}, "body\n", SPEC_STYLE)
        self.assertTrue(out.startswith("---\n") and out.endswith("---\nbody\n"))
        self.assertEqual(read_frontmatter(out)[0], {"a": 1, "b": [1, 2], "c": {"d": None}})


class TestGoldenSpecEdits(unittest.TestCase):
    """Golden files: a single-field spec edit changes only that field's line(s)."""

    def setUp(self):
        self.src = (FIXTURES / "spec" / "golden-spec.md").read_text(encoding="utf-8")

    def _check(self, name: str, updates: dict) -> None:
        expected = (FIXTURES / "spec" / "expected" / f"{name}.md").read_text(encoding="utf-8")
        self.assertEqual(update_frontmatter(self.src, updates, SPEC_STYLE), expected)

    def test_golden_priority(self):
        self._check("priority", {"priority": "high"})

    def test_golden_tags(self):
        self._check("tags", {"tags": ["x", "y"]})

    def test_golden_description_with_colon(self):
        self._check("description", {"description": "Colon: here, and more"})

    def test_golden_feature_root(self):
        self._check("feature-root", {"feature_root_id": "B-12345678"})

    def test_golden_nested_scope(self):
        self._check("scope-out", {"scope": {"in": "everything", "out": "something else"}})


class TestFileHelpers(unittest.TestCase):
    def test_write_and_update_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "d.md"
            write_document(p, {"id": "x", "status": "draft"}, "body\n", SPEC_STYLE)
            update_frontmatter_file(p, {"status": "done"}, SPEC_STYLE)
            fm, body = read_frontmatter_file(p)
            self.assertEqual(fm, {"id": "x", "status": "done"})
            self.assertEqual(body, "body\n")


if __name__ == "__main__":
    unittest.main()
