"""Unit tests for agent_takt.adr module (AdrStore and helpers).

Covers: _allocate_id, _slugify, frontmatter round-trip, load_all, resolve_prefix,
list_adrs, new_adr, approve, reject, supersede, validate_all, format_adr_list_plain,
format_adr_field, ADR template parity, and ensure_adr_directories.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
TEMPLATES_ROOT = REPO_ROOT / "templates"
DATA_TEMPLATES_ROOT = REPO_ROOT / "src" / "agent_takt" / "_data" / "templates"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.adr import (
    ADR_APPROVED,
    ADR_DRAFT,
    ADR_REJECTED,
    ADR_SUPERSEDED,
    Adr,
    AdrStore,
    _allocate_id,
    _slugify,
    _parse_frontmatter,
    _render_frontmatter,
)
from agent_takt.cli.formatting import format_adr_field, format_adr_list_plain, format_bead_field


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=root, check=True, capture_output=True)


def _install_adr_template(root: Path) -> None:
    template_dir = root / "templates" / "adr"
    template_dir.mkdir(parents=True, exist_ok=True)
    src = TEMPLATES_ROOT / "adr" / "template.md"
    (template_dir / "template.md").write_bytes(src.read_bytes())


def _write_adr_file(root: Path, subdir: str, filename: str, adr: Adr, body: str = "") -> Path:
    d = root / "adr" / subdir
    d.mkdir(parents=True, exist_ok=True)
    path = d / filename
    AdrStore(root)._save_file(path, adr, body)
    return path


def _make_adr(
    adr_id: str = "ADR-a1b2c3d4",
    title: str = "Test ADR",
    status: str = "draft",
    created_at: str = "2026-01-01T00:00:00+00:00",
    **kwargs,
) -> Adr:
    return Adr(id=adr_id, title=title, status=status, created_at=created_at, authors=[], **kwargs)


def _valid_adr_body() -> str:
    """Return a body that passes all _check_body_sections() validation."""
    return (
        "## Summary\n\n"
        "We decided to use approach A for concrete reasons.\n\n"
        "## Context\n\n"
        "The system required a decision about real constraints.\n\n"
        "## Decision Drivers\n\n"
        "* Real driver: performance requirement\n\n"
        "## Considered Options\n\n"
        "### Option A — Real Option\n\n"
        "* Good: Meets goals\n"
        "* Bad: Higher cost\n\n"
        "### Option B — Alternative\n\n"
        "* Good: Lower cost\n"
        "* Bad: Does not meet goals\n\n"
        "## Decision\n\n"
        "We chose Option A because it meets performance requirements.\n\n"
        "## Consequences\n\n"
        "### Positive\n\n"
        "* Performance goals are met.\n\n"
        "### Negative\n\n"
        "* Higher initial implementation cost.\n"
    )


# ---------------------------------------------------------------------------
# _allocate_id
# ---------------------------------------------------------------------------


class TestAllocateId(unittest.TestCase):
    def test_format_is_adr_prefix_with_8_hex_chars(self):
        adr_id = _allocate_id()
        self.assertRegex(adr_id, r"^ADR-[0-9a-f]{8}$")

    def test_two_calls_produce_different_ids(self):
        self.assertNotEqual(_allocate_id(), _allocate_id())

    def test_hex_part_is_exactly_8_chars(self):
        hex_part = _allocate_id()[4:]  # strip "ADR-"
        self.assertEqual(len(hex_part), 8)

    def test_hex_chars_are_lowercase(self):
        hex_part = _allocate_id()[4:]
        self.assertEqual(hex_part, hex_part.lower())


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------


class TestSlugify(unittest.TestCase):
    def test_lowercases_input(self):
        self.assertEqual(_slugify("Hello World"), "hello-world")

    def test_colon_becomes_hyphen(self):
        self.assertEqual(_slugify("Title: Subtitle"), "title-subtitle")

    def test_slashes_become_hyphens(self):
        self.assertEqual(_slugify("path/to/thing"), "path-to-thing")

    def test_uppercase_becomes_lowercase(self):
        self.assertEqual(_slugify("USE GRPC"), "use-grpc")

    def test_special_chars_collapse_to_single_hyphen(self):
        self.assertEqual(_slugify("A!@#B"), "a-b")

    def test_leading_trailing_hyphens_stripped(self):
        self.assertEqual(_slugify("--trim--"), "trim")

    def test_empty_string_returns_untitled(self):
        self.assertEqual(_slugify(""), "untitled")

    def test_spaces_become_hyphens(self):
        result = _slugify("use gRPC for transport")
        self.assertIn("use-grpc-for-transport", result)


# ---------------------------------------------------------------------------
# Frontmatter round-trip
# ---------------------------------------------------------------------------


class TestFrontmatterRoundTrip(unittest.TestCase):
    def _round_trip(self, adr: Adr) -> Adr:
        fm_yaml = f"---\n{_render_frontmatter(adr.to_dict())}---\n\nbody\n"
        fm, _ = _parse_frontmatter(fm_yaml)
        return Adr.from_dict(fm)

    def test_basic_fields_survive_round_trip(self):
        adr = _make_adr()
        rt = self._round_trip(adr)
        self.assertEqual(rt.id, adr.id)
        self.assertEqual(rt.title, adr.title)
        self.assertEqual(rt.status, adr.status)

    def test_none_optional_fields_survive(self):
        adr = _make_adr(description=None, accepted_at=None, superseded_at=None, superseded_by=None)
        rt = self._round_trip(adr)
        self.assertIsNone(rt.description)
        self.assertIsNone(rt.accepted_at)
        self.assertIsNone(rt.superseded_at)
        self.assertIsNone(rt.superseded_by)

    def test_empty_list_fields_survive(self):
        adr = _make_adr(tags=[], supersedes=[], related_specs=[], related_beads=[])
        rt = self._round_trip(adr)
        self.assertEqual(rt.tags, [])
        self.assertEqual(rt.supersedes, [])
        self.assertEqual(rt.related_specs, [])
        self.assertEqual(rt.related_beads, [])

    def test_non_empty_list_fields_survive(self):
        adr = _make_adr(tags=["security", "api"], related_specs=["spec-abc"])
        rt = self._round_trip(adr)
        self.assertEqual(rt.tags, ["security", "api"])
        self.assertEqual(rt.related_specs, ["spec-abc"])

    def test_iso_timestamp_created_at_survives(self):
        ts = "2026-06-15T10:30:00+00:00"
        adr = _make_adr(created_at=ts)
        rt = self._round_trip(adr)
        self.assertIn("2026-06-15", rt.created_at)

    def test_pyyaml_datetime_object_coerced_to_str(self):
        """from_dict must handle datetime objects that PyYAML emits for ISO timestamps."""
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        data = {
            "id": "ADR-a1b2c3d4",
            "title": "Test",
            "status": "draft",
            "created_at": dt,
            "authors": [],
        }
        adr = Adr.from_dict(data)
        self.assertIsInstance(adr.created_at, str)
        self.assertIn("2026-01-01", adr.created_at)


# ---------------------------------------------------------------------------
# AdrStore.load_all
# ---------------------------------------------------------------------------


class TestLoadAll(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = AdrStore(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_returns_valid_adrs(self):
        _write_adr_file(self.root, "drafts", "adr-abc12345-test.md",
                        _make_adr("ADR-abc12345"))
        adrs = self.store.load_all()
        self.assertEqual(len(adrs), 1)
        self.assertEqual(adrs[0].id, "ADR-abc12345")

    def test_silently_skips_malformed_yaml(self):
        d = self.root / "adr" / "drafts"
        d.mkdir(parents=True, exist_ok=True)
        (d / "bad.md").write_text("---\n: [unclosed bracket\n---\nbody", encoding="utf-8")
        adrs = self.store.load_all()
        self.assertEqual(len(adrs), 0)

    def test_silently_skips_missing_required_fields(self):
        d = self.root / "adr" / "drafts"
        d.mkdir(parents=True, exist_ok=True)
        (d / "incomplete.md").write_text("---\nfoo: bar\n---\nbody", encoding="utf-8")
        adrs = self.store.load_all()
        self.assertEqual(len(adrs), 0)

    def test_loads_from_all_status_subdirs(self):
        _write_adr_file(self.root, "drafts", "adr-00000001-t.md", _make_adr("ADR-00000001", status="draft"))
        _write_adr_file(self.root, "approved", "adr-00000002-t.md", _make_adr("ADR-00000002", status="approved"))
        _write_adr_file(self.root, "rejected", "adr-00000003-t.md", _make_adr("ADR-00000003", status="rejected"))
        adrs = self.store.load_all()
        self.assertEqual(len(adrs), 3)


# ---------------------------------------------------------------------------
# AdrStore.resolve_prefix
# ---------------------------------------------------------------------------


class TestResolvePrefix(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = AdrStore(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, adr_id: str, status: str = "draft") -> None:
        subdir_map = {"draft": "drafts", "approved": "approved", "superseded": "superseded", "rejected": "rejected"}
        _write_adr_file(self.root, subdir_map[status], f"adr-{adr_id[4:]}-test.md",
                        _make_adr(adr_id, status=status))

    def test_full_id_resolves(self):
        self._write("ADR-a1b2c3d4")
        self.assertEqual(self.store.resolve_prefix("ADR-a1b2c3d4"), "ADR-a1b2c3d4")

    def test_bare_hex_prefix_resolves(self):
        self._write("ADR-a1b2c3d4")
        self.assertEqual(self.store.resolve_prefix("a1b2"), "ADR-a1b2c3d4")

    def test_adr_dash_prefix_resolves(self):
        self._write("ADR-a1b2c3d4")
        self.assertEqual(self.store.resolve_prefix("ADR-a1b2"), "ADR-a1b2c3d4")

    def test_zero_matches_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            self.store.resolve_prefix("xyz99999")
        self.assertIn("No ADR matches", str(ctx.exception))

    def test_multiple_matches_raises_value_error(self):
        self._write("ADR-a1b2c3d4")
        self._write("ADR-a1b2e5f6")
        with self.assertRaises(ValueError) as ctx:
            self.store.resolve_prefix("a1b2")
        self.assertIn("Ambiguous", str(ctx.exception))

    def test_prefix_matches_across_status_dirs(self):
        self._write("ADR-a1b2c3d4", status="approved")
        self.assertEqual(self.store.resolve_prefix("a1b2"), "ADR-a1b2c3d4")


# ---------------------------------------------------------------------------
# AdrStore.list_adrs
# ---------------------------------------------------------------------------


class TestListAdrs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = AdrStore(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, adr_id: str, status: str = "draft", tags: list[str] | None = None) -> None:
        subdir = {"draft": "drafts", "approved": "approved"}[status]
        _write_adr_file(self.root, subdir, f"adr-{adr_id[4:]}-test.md",
                        _make_adr(adr_id, status=status, tags=tags or []))

    def test_no_filters_returns_all(self):
        self._write("ADR-00000001", "draft")
        self._write("ADR-00000002", "approved")
        self.assertEqual(len(self.store.list_adrs()), 2)

    def test_status_or_semantics_returns_both(self):
        self._write("ADR-00000001", "draft")
        self._write("ADR-00000002", "approved")
        result = self.store.list_adrs(statuses=["draft", "approved"])
        ids = {a.id for a in result}
        self.assertIn("ADR-00000001", ids)
        self.assertIn("ADR-00000002", ids)

    def test_status_filter_excludes_non_matching(self):
        self._write("ADR-00000001", "draft")
        self._write("ADR-00000002", "approved")
        result = self.store.list_adrs(statuses=["draft"])
        ids = {a.id for a in result}
        self.assertIn("ADR-00000001", ids)
        self.assertNotIn("ADR-00000002", ids)

    def test_tag_and_semantics_requires_all_tags(self):
        self._write("ADR-00000001", "draft", tags=["security", "api"])
        self._write("ADR-00000002", "draft", tags=["api"])
        result = self.store.list_adrs(tags=["security", "api"])
        ids = {a.id for a in result}
        self.assertIn("ADR-00000001", ids)
        self.assertNotIn("ADR-00000002", ids)

    def test_tag_filter_one_tag(self):
        self._write("ADR-00000001", "draft", tags=["security"])
        self._write("ADR-00000002", "draft", tags=[])
        result = self.store.list_adrs(tags=["security"])
        ids = {a.id for a in result}
        self.assertIn("ADR-00000001", ids)
        self.assertNotIn("ADR-00000002", ids)

    def test_two_tag_filter_where_one_matches(self):
        self._write("ADR-00000001", "draft", tags=["security"])
        result = self.store.list_adrs(tags=["security", "api"])
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# AdrStore.new_adr
# ---------------------------------------------------------------------------


class TestNewAdr(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _init_git_repo(self.root)
        _install_adr_template(self.root)
        self.store = AdrStore(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_raises_file_not_found_when_template_absent(self):
        no_template_root = Path(self._tmp.name) / "no_tmpl"
        no_template_root.mkdir()
        store = AdrStore(no_template_root)
        with self.assertRaises(FileNotFoundError):
            store.new_adr("Test ADR")

    def test_creates_file_in_drafts_dir(self):
        self.store.new_adr("Test Decision")
        files = list((self.root / "adr" / "drafts").glob("*.md"))
        self.assertEqual(len(files), 1)

    def test_filename_format_adr_hex_slug(self):
        self.store.new_adr("Test Decision")
        files = list((self.root / "adr" / "drafts").glob("*.md"))
        self.assertRegex(files[0].name, r"^adr-[0-9a-f]{8}-.+\.md$")

    def test_slug_in_filename(self):
        self.store.new_adr("Use gRPC for Transport")
        files = list((self.root / "adr" / "drafts").glob("*.md"))
        self.assertIn("use-grpc-for-transport", files[0].name)

    def test_id_format_is_adr_8hex(self):
        adr = self.store.new_adr("Test Decision")
        self.assertRegex(adr.id, r"^ADR-[0-9a-f]{8}$")

    def test_status_is_draft(self):
        adr = self.store.new_adr("Test Decision")
        self.assertEqual(adr.status, ADR_DRAFT)

    def test_tags_stored(self):
        adr = self.store.new_adr("Test Decision", tags=["security", "api"])
        self.assertEqual(adr.tags, ["security", "api"])

    def test_description_stored(self):
        adr = self.store.new_adr("Test Decision", description="A brief summary")
        self.assertEqual(adr.description, "A brief summary")

    def test_related_spec_stored(self):
        adr = self.store.new_adr("Test Decision", related_specs=["spec-abc"])
        self.assertEqual(adr.related_specs, ["spec-abc"])

    def test_related_bead_stored(self):
        adr = self.store.new_adr("Test Decision", related_beads=["B-12345678"])
        self.assertEqual(adr.related_beads, ["B-12345678"])

    def test_created_at_is_string_not_datetime(self):
        """new_adr writes ISO timestamp inline; from_dict must coerce it back to str."""
        adr = self.store.new_adr("Test Decision")
        # Load the written file back from disk
        path = self.store.find_file_by_id(adr.id)
        text = path.read_text(encoding="utf-8")
        fm, _ = _parse_frontmatter(text)
        loaded = Adr.from_dict(fm)
        self.assertIsInstance(loaded.created_at, str)
        # Should look like a valid ISO date
        self.assertRegex(loaded.created_at[:10], r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# AdrStore.approve
# ---------------------------------------------------------------------------


class TestApprove(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _init_git_repo(self.root)
        self.store = AdrStore(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_draft(self, adr_id: str, body: str | None = None) -> None:
        _write_adr_file(self.root, "drafts", f"adr-{adr_id[4:]}-test.md",
                        _make_adr(adr_id, status="draft"),
                        body=body if body is not None else _valid_adr_body())

    def _write_approved(self, adr_id: str) -> None:
        _write_adr_file(self.root, "approved", f"adr-{adr_id[4:]}-test.md",
                        _make_adr(adr_id, status="approved", accepted_at="2026-01-02T00:00:00+00:00"),
                        body=_valid_adr_body())

    def test_happy_path_transitions_to_approved(self):
        self._write_draft("ADR-aaa00001")
        adr = self.store.approve("ADR-aaa00001")
        self.assertEqual(adr.status, ADR_APPROVED)

    def test_happy_path_sets_accepted_at(self):
        self._write_draft("ADR-aaa00001")
        adr = self.store.approve("ADR-aaa00001")
        self.assertIsNotNone(adr.accepted_at)

    def test_happy_path_moves_file_to_approved_dir(self):
        self._write_draft("ADR-aaa00001")
        self.store.approve("ADR-aaa00001")
        approved_files = list((self.root / "adr" / "approved").glob("*.md"))
        draft_files = list((self.root / "adr" / "drafts").glob("*.md"))
        self.assertEqual(len(approved_files), 1)
        self.assertEqual(len(draft_files), 0)

    def test_non_draft_raises_value_error(self):
        self._write_approved("ADR-aaa00001")
        with self.assertRaises(ValueError) as ctx:
            self.store.approve("ADR-aaa00001")
        self.assertIn("must be 'draft'", str(ctx.exception))

    def test_missing_required_section_raises_value_error(self):
        body_no_decision = (
            "## Summary\n\nReal content.\n\n"
            "## Context\n\nReal context.\n\n"
            "## Considered Options\n\n"
            "### Option A — Real\n\n* Good: works\n\n"
            "## Consequences\n\n### Positive\n\n* Good.\n\n### Negative\n\n* Bad.\n"
        )
        self._write_draft("ADR-aaa00001", body=body_no_decision)
        with self.assertRaises(ValueError) as ctx:
            self.store.approve("ADR-aaa00001")
        self.assertIn("## Decision", str(ctx.exception))

    def test_placeholder_only_summary_raises_value_error(self):
        body = (
            "## Summary\n\n"
            "> In the context of <use case>, facing <concern>, we decided for <option>.\n\n"
            "One sentence in the structured form above.\n\n"
            "## Context\n\nReal context.\n\n"
            "## Considered Options\n\n### Option A — Real\n\n* Good: works\n\n"
            "## Decision\n\nWe chose A.\n\n"
            "## Consequences\n\n### Positive\n\n* Good.\n\n### Negative\n\n* Bad.\n"
        )
        self._write_draft("ADR-aaa00001", body=body)
        with self.assertRaises(ValueError) as ctx:
            self.store.approve("ADR-aaa00001")
        self.assertIn("placeholder", str(ctx.exception).lower())

    def test_alexandrian_blockquote_without_angle_brackets_is_accepted(self):
        """Real Alexandrian-pattern blockquote (no angle-bracket tokens) must not be rejected."""
        body = (
            "## Summary\n\n"
            "> In the context of coordinating specialised AI workers against a shared codebase,"
            " facing the need for atomic units of work with structured handoffs,"
            " we decided to introduce the bead abstraction,"
            " to achieve predictable agent scope control,"
            " accepting the overhead of structured JSON handoffs.\n\n"
            "## Context\n\n"
            "The system required a decision about real constraints.\n\n"
            "## Considered Options\n\n"
            "### Option A — Real Option\n\n"
            "* Good: Meets goals\n"
            "* Bad: Higher cost\n\n"
            "## Decision\n\n"
            "We chose Option A because it meets performance requirements.\n\n"
            "## Consequences\n\n"
            "### Positive\n\n"
            "* Performance goals are met.\n\n"
            "### Negative\n\n"
            "* Higher initial implementation cost.\n"
        )
        self._write_draft("ADR-aaa00002", body=body)
        adr = self.store.approve("ADR-aaa00002")
        self.assertEqual(adr.status, ADR_APPROVED)

    def test_template_stub_with_angle_brackets_still_rejected(self):
        """Unmodified template stub containing angle-bracket tokens must still be detected as placeholder."""
        body = (
            "## Summary\n\n"
            "> In the context of <use case>, facing <concern>, we decided for <option>,"
            " to achieve <quality>, accepting <downside>.\n\n"
            "## Context\n\n"
            "The system required a decision about real constraints.\n\n"
            "## Considered Options\n\n"
            "### Option A — Real Option\n\n"
            "* Good: Meets goals\n\n"
            "## Decision\n\n"
            "We chose Option A.\n\n"
            "## Consequences\n\n"
            "### Positive\n\n"
            "* Good.\n\n"
            "### Negative\n\n"
            "* Bad.\n"
        )
        self._write_draft("ADR-aaa00003", body=body)
        with self.assertRaises(ValueError) as ctx:
            self.store.approve("ADR-aaa00003")
        self.assertIn("placeholder", str(ctx.exception).lower())

    def test_alexandrian_blockquote_with_single_angle_bracket_token_is_accepted(self):
        """Real blockquote Alexandrian Summary with one incidental <token> (e.g. a path) must not be flagged.

        Regression for B-ed9998c2: the previous fix (requiring >=1 angle-bracket token) introduced
        a new false-positive where a legitimate filename reference like templates/agents/<type>.md
        inside a blockquote Alexandrian Summary was incorrectly treated as placeholder content.
        The detector now requires >=3 distinct angle-bracket tokens to flag a line.
        """
        body = (
            "## Summary\n\n"
            "> In the context of running AI workers that mutate a shared codebase,"
            " facing the risk of agents drifting out of scope,"
            " we decided that every runnable agent type must have a mandatory guardrail"
            " template at `templates/agents/<type>.md`,"
            " to achieve enforced role boundaries and predictable per-agent behaviour,"
            " accepting that adding a new agent type is not a one-line change.\n\n"
            "## Context\n\n"
            "The system required a decision about real constraints.\n\n"
            "## Considered Options\n\n"
            "### Option A — Real Option\n\n"
            "* Good: Meets goals\n"
            "* Bad: Higher cost\n\n"
            "## Decision\n\n"
            "We chose Option A because it meets performance requirements.\n\n"
            "## Consequences\n\n"
            "### Positive\n\n"
            "* Performance goals are met.\n\n"
            "### Negative\n\n"
            "* Higher initial implementation cost.\n"
        )
        self._write_draft("ADR-aaa00004", body=body)
        adr = self.store.approve("ADR-aaa00004")
        self.assertEqual(adr.status, ADR_APPROVED)

    def test_missing_real_option_subsection_raises_value_error(self):
        body = (
            "## Summary\n\nReal summary.\n\n"
            "## Context\n\nReal context.\n\n"
            "## Considered Options\n\n"
            "### Option A — (name)\n\n* Good: …\n\n"
            "## Decision\n\nWe chose something.\n\n"
            "## Consequences\n\n### Positive\n\n* Good.\n\n### Negative\n\n* Bad.\n"
        )
        self._write_draft("ADR-aaa00001", body=body)
        with self.assertRaises(ValueError) as ctx:
            self.store.approve("ADR-aaa00001")
        self.assertIn("Considered Options", str(ctx.exception))

    def test_supersedes_happy_path_transitions_target_to_superseded(self):
        self._write_draft("ADR-aaa00001")
        self._write_approved("ADR-bbb00002")
        adr = self.store.approve("ADR-aaa00001", supersedes=["ADR-bbb00002"])
        self.assertEqual(adr.status, ADR_APPROVED)
        superseded_files = list((self.root / "adr" / "superseded").glob("*.md"))
        self.assertEqual(len(superseded_files), 1)

    def test_supersedes_sets_superseded_by_on_target(self):
        self._write_draft("ADR-aaa00001")
        self._write_approved("ADR-bbb00002")
        self.store.approve("ADR-aaa00001", supersedes=["ADR-bbb00002"])
        reloaded = self.store.find_by_id("ADR-bbb00002")
        self.assertEqual(reloaded.superseded_by, "ADR-aaa00001")

    def test_supersedes_non_approved_target_raises_no_files_moved(self):
        """Atomicity: main ADR stays draft if target is not approved."""
        self._write_draft("ADR-aaa00001")
        self._write_draft("ADR-ccc00003")  # also draft, not approved
        draft_count_before = len(list((self.root / "adr" / "drafts").glob("*.md")))
        with self.assertRaises(ValueError) as ctx:
            self.store.approve("ADR-aaa00001", supersedes=["ADR-ccc00003"])
        self.assertIn("must be 'approved'", str(ctx.exception))
        draft_count_after = len(list((self.root / "adr" / "drafts").glob("*.md")))
        self.assertEqual(draft_count_before, draft_count_after)
        approved = self.root / "adr" / "approved"
        approved_files = list(approved.glob("*.md")) if approved.exists() else []
        self.assertEqual(len(approved_files), 0)


# ---------------------------------------------------------------------------
# AdrStore.reject
# ---------------------------------------------------------------------------


class TestReject(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = AdrStore(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_draft(self, adr_id: str) -> None:
        _write_adr_file(self.root, "drafts", f"adr-{adr_id[4:]}-test.md",
                        _make_adr(adr_id, status="draft"))

    def _write_approved(self, adr_id: str) -> None:
        _write_adr_file(self.root, "approved", f"adr-{adr_id[4:]}-test.md",
                        _make_adr(adr_id, status="approved", accepted_at="2026-01-02T00:00:00+00:00"))

    def test_happy_path_transitions_to_rejected(self):
        self._write_draft("ADR-aaa00001")
        adr = self.store.reject("ADR-aaa00001")
        self.assertEqual(adr.status, ADR_REJECTED)

    def test_happy_path_moves_to_rejected_dir(self):
        self._write_draft("ADR-aaa00001")
        self.store.reject("ADR-aaa00001")
        self.assertEqual(len(list((self.root / "adr" / "rejected").glob("*.md"))), 1)
        self.assertEqual(len(list((self.root / "adr" / "drafts").glob("*.md"))), 0)

    def test_non_draft_raises_value_error(self):
        self._write_approved("ADR-aaa00001")
        with self.assertRaises(ValueError) as ctx:
            self.store.reject("ADR-aaa00001")
        self.assertIn("must be 'draft'", str(ctx.exception))


# ---------------------------------------------------------------------------
# AdrStore.supersede
# ---------------------------------------------------------------------------


class TestSupersede(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = AdrStore(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_approved(self, adr_id: str) -> None:
        _write_adr_file(self.root, "approved", f"adr-{adr_id[4:]}-test.md",
                        _make_adr(adr_id, status="approved", accepted_at="2026-01-02T00:00:00+00:00"))

    def _write_draft(self, adr_id: str) -> None:
        _write_adr_file(self.root, "drafts", f"adr-{adr_id[4:]}-test.md",
                        _make_adr(adr_id, status="draft"))

    def test_happy_path_transitions_to_superseded(self):
        self._write_approved("ADR-aaa00001")
        self._write_approved("ADR-bbb00002")
        adr = self.store.supersede("ADR-aaa00001", "ADR-bbb00002")
        self.assertEqual(adr.status, ADR_SUPERSEDED)
        self.assertEqual(adr.superseded_by, "ADR-bbb00002")

    def test_happy_path_sets_superseded_at(self):
        self._write_approved("ADR-aaa00001")
        self._write_approved("ADR-bbb00002")
        adr = self.store.supersede("ADR-aaa00001", "ADR-bbb00002")
        self.assertIsNotNone(adr.superseded_at)

    def test_happy_path_moves_to_superseded_dir(self):
        self._write_approved("ADR-aaa00001")
        self._write_approved("ADR-bbb00002")
        self.store.supersede("ADR-aaa00001", "ADR-bbb00002")
        self.assertEqual(len(list((self.root / "adr" / "superseded").glob("*.md"))), 1)
        # Original approved dir should no longer have the superseded ADR
        approved_ids = {
            AdrStore(self.root)._load_file(p).id
            for p in (self.root / "adr" / "approved").glob("*.md")
        }
        self.assertNotIn("ADR-aaa00001", approved_ids)

    def test_old_not_approved_raises_value_error(self):
        self._write_draft("ADR-aaa00001")
        self._write_approved("ADR-bbb00002")
        with self.assertRaises(ValueError) as ctx:
            self.store.supersede("ADR-aaa00001", "ADR-bbb00002")
        self.assertIn("must be 'approved'", str(ctx.exception))

    def test_new_not_approved_raises_value_error(self):
        self._write_approved("ADR-aaa00001")
        self._write_draft("ADR-bbb00002")
        with self.assertRaises(ValueError) as ctx:
            self.store.supersede("ADR-aaa00001", "ADR-bbb00002")
        self.assertIn("not approved", str(ctx.exception).lower())


# ---------------------------------------------------------------------------
# AdrStore.validate_all
# ---------------------------------------------------------------------------


class TestValidateAll(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = AdrStore(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, adr_id: str, status: str = "draft", body: str = "",
               extra_fm: dict | None = None) -> None:
        subdir_map = {"draft": "drafts", "approved": "approved",
                      "superseded": "superseded", "rejected": "rejected"}
        adr = _make_adr(adr_id, status=status)
        if extra_fm:
            for k, v in extra_fm.items():
                setattr(adr, k, v)
        _write_adr_file(self.root, subdir_map[status],
                        f"adr-{adr_id[4:]}-test.md", adr, body=body)

    def test_clean_pass_returns_empty_list(self):
        self._write("ADR-aaa00001")
        self.assertEqual(self.store.validate_all(), [])

    def test_dangling_superseded_by_reference(self):
        self._write("ADR-aaa00001", status="superseded",
                    extra_fm={"superseded_by": "ADR-nonexist"})
        errors = self.store.validate_all()
        msgs = [e.message for e in errors]
        self.assertTrue(any("dangling superseded_by" in m for m in msgs))
        ids = [e.adr_id for e in errors]
        self.assertIn("ADR-aaa00001", ids)

    def test_dangling_supersedes_entry(self):
        self._write("ADR-aaa00001", status="approved",
                    body=_valid_adr_body(),
                    extra_fm={"supersedes": ["ADR-nonexist"]})
        errors = self.store.validate_all()
        msgs = [e.message for e in errors]
        self.assertTrue(any("dangling supersedes" in m for m in msgs))

    def test_missing_required_frontmatter_field(self):
        d = self.root / "adr" / "drafts"
        d.mkdir(parents=True, exist_ok=True)
        (d / "bad.md").write_text(
            "---\nid: ADR-aaa00001\nstatus: draft\n---\nbody\n", encoding="utf-8"
        )
        errors = self.store.validate_all()
        msgs = [e.message for e in errors]
        self.assertTrue(any("missing required frontmatter field" in m for m in msgs))

    def test_invalid_id_format(self):
        d = self.root / "adr" / "drafts"
        d.mkdir(parents=True, exist_ok=True)
        (d / "bad-id.md").write_text(
            "---\nid: NOT-VALID-ID\ntitle: test\nstatus: draft\n"
            "created_at: '2026-01-01T00:00:00+00:00'\nauthors: []\n---\nbody\n",
            encoding="utf-8",
        )
        errors = self.store.validate_all()
        msgs = [e.message for e in errors]
        self.assertTrue(any("invalid ADR ID format" in m for m in msgs))

    def test_body_violation_on_approved_adr(self):
        d = self.root / "adr" / "approved"
        d.mkdir(parents=True, exist_ok=True)
        adr = _make_adr("ADR-aaa00001", status="approved", accepted_at="2026-01-01T00:00:00+00:00")
        path = d / "adr-aaa00001-test.md"
        # Body with no Decision section — should fail
        self.store._save_file(path, adr, "## Summary\n\nReal content.\n\n## Context\n\nSome context.\n")
        errors = self.store.validate_all()
        msgs = [e.message for e in errors]
        self.assertTrue(any("missing required section" in m for m in msgs))


# ---------------------------------------------------------------------------
# format_adr_list_plain
# ---------------------------------------------------------------------------


class TestFormatAdrListPlain(unittest.TestCase):
    def test_empty_list_returns_sentinel(self):
        self.assertEqual(format_adr_list_plain([]), "No ADRs found.")

    def test_column_headers_present(self):
        adr = _make_adr("ADR-a1b2c3d4", title="Test", status="draft")
        output = format_adr_list_plain([adr])
        for header in ("ID", "STATUS", "TITLE", "CREATED"):
            self.assertIn(header, output)

    def test_sort_order_by_created_at_then_id(self):
        adr1 = _make_adr("ADR-b0000001", created_at="2026-06-01T00:00:00+00:00")
        adr2 = _make_adr("ADR-a0000002", created_at="2026-01-01T00:00:00+00:00")
        output = format_adr_list_plain([adr1, adr2])
        lines = output.splitlines()
        # adr2 has earlier created_at and should appear before adr1 in data rows
        adr2_pos = next(i for i, l in enumerate(lines) if "ADR-a0000002" in l)
        adr1_pos = next(i for i, l in enumerate(lines) if "ADR-b0000001" in l)
        self.assertLess(adr2_pos, adr1_pos)

    def test_sort_by_id_on_tie(self):
        same_ts = "2026-01-01T00:00:00+00:00"
        adr_b = _make_adr("ADR-b0000002", created_at=same_ts)
        adr_a = _make_adr("ADR-a0000001", created_at=same_ts)
        output = format_adr_list_plain([adr_b, adr_a])
        lines = output.splitlines()
        a_pos = next(i for i, l in enumerate(lines) if "ADR-a0000001" in l)
        b_pos = next(i for i, l in enumerate(lines) if "ADR-b0000002" in l)
        self.assertLess(a_pos, b_pos)

    def test_date_truncation_to_10_chars(self):
        adr = _make_adr("ADR-a1b2c3d4", created_at="2026-06-15T10:30:00+00:00")
        output = format_adr_list_plain([adr])
        self.assertIn("2026-06-15", output)
        self.assertNotIn("10:30:00", output)


# ---------------------------------------------------------------------------
# format_adr_field
# ---------------------------------------------------------------------------


class TestFormatAdrField(unittest.TestCase):
    def _make(self, **kwargs) -> Adr:
        return Adr(
            id=str(kwargs.get("adr_id", "ADR-a1b2c3d4")),
            title=str(kwargs.get("title", "Test ADR")),
            status=str(kwargs.get("status", "draft")),
            created_at=str(kwargs.get("created_at", "2026-01-01T00:00:00+00:00")),
            authors=list(kwargs.get("authors", ["Test User"])),
            tags=list(kwargs.get("tags", ["security", "api"])),
            description=kwargs.get("description"),
        )

    def test_str_field_status(self):
        adr = self._make()
        self.assertEqual(format_adr_field(adr, "status"), "draft")

    def test_str_field_title(self):
        adr = self._make()
        self.assertEqual(format_adr_field(adr, "title"), "Test ADR")

    def test_list_field_tags_as_json(self):
        adr = self._make(tags=["security", "api"])
        result = format_adr_field(adr, "tags")
        parsed = json.loads(result)
        self.assertEqual(parsed, ["security", "api"])

    def test_list_with_index_authors_0(self):
        adr = self._make(authors=["Test User"])
        self.assertEqual(format_adr_field(adr, "authors[0]"), "Test User")

    def test_none_valued_optional_field_returns_empty(self):
        adr = self._make(description=None)
        self.assertEqual(format_adr_field(adr, "description"), "")

    def test_bool_lowercasing_via_format_bead_field(self):
        self.assertEqual(format_bead_field(True), "true")
        self.assertEqual(format_bead_field(False), "false")

    def test_list_dict_pretty_json_output(self):
        adr = self._make(tags=["a"])
        result = format_adr_field(adr, "tags")
        self.assertIn("\n", result)  # pretty-printed JSON has newlines

    def test_body_section_summary(self):
        adr = self._make()
        body = _valid_adr_body()
        result = format_adr_field(adr, "summary", body=body)
        self.assertIn("approach A", result)

    def test_body_section_context(self):
        adr = self._make()
        body = _valid_adr_body()
        result = format_adr_field(adr, "context", body=body)
        self.assertIn("real constraints", result)

    def test_body_section_decision(self):
        adr = self._make()
        body = _valid_adr_body()
        result = format_adr_field(adr, "decision", body=body)
        self.assertIn("Option A", result)

    def test_body_section_decision_drivers(self):
        adr = self._make()
        body = _valid_adr_body()
        result = format_adr_field(adr, "decision_drivers", body=body)
        self.assertIn("performance requirement", result)

    def test_body_section_considered_options(self):
        adr = self._make()
        body = _valid_adr_body()
        result = format_adr_field(adr, "considered_options", body=body)
        self.assertIn("Option A", result)

    def test_body_section_consequences(self):
        adr = self._make()
        body = _valid_adr_body()
        result = format_adr_field(adr, "consequences", body=body)
        self.assertIn("Positive", result)

    def test_body_section_consequences_positive(self):
        adr = self._make()
        body = _valid_adr_body()
        result = format_adr_field(adr, "consequences.positive", body=body)
        self.assertIn("Performance goals", result)

    def test_body_section_consequences_negative(self):
        adr = self._make()
        body = _valid_adr_body()
        result = format_adr_field(adr, "consequences.negative", body=body)
        self.assertIn("implementation cost", result)

    def test_case_insensitive_body_section_lookup(self):
        adr = self._make()
        body = _valid_adr_body()
        result_lower = format_adr_field(adr, "decision", body=body)
        result_upper = format_adr_field(adr, "DECISION", body=body)
        self.assertEqual(result_lower, result_upper)

    def test_unknown_path_raises_value_error_with_message(self):
        adr = self._make()
        with self.assertRaises(ValueError) as ctx:
            format_adr_field(adr, "nonexistent_field_xyz")
        self.assertEqual(str(ctx.exception), "field not found: nonexistent_field_xyz")

    def test_out_of_range_index_raises_value_error(self):
        adr = self._make(authors=["One User"])
        with self.assertRaises(ValueError) as ctx:
            format_adr_field(adr, "authors[5]")
        self.assertIn("field not found: authors[5]", str(ctx.exception))

    def test_empty_body_with_reserved_key_raises_value_error(self):
        adr = self._make()
        with self.assertRaises(ValueError) as ctx:
            format_adr_field(adr, "decision", body="")
        self.assertIn("field not found: decision", str(ctx.exception))

    def test_heading_present_but_empty_section_returns_empty_string(self):
        adr = self._make()
        body = (
            "## Summary\n\n"
            "## Context\n\nSome context.\n"
        )
        result = format_adr_field(adr, "summary", body=body)
        self.assertEqual(result, "")


# ---------------------------------------------------------------------------
# ADR template parity
# ---------------------------------------------------------------------------


class TestAdrTemplateParity(unittest.TestCase):
    """Verify templates/adr/template.md and its bundled copy are byte-identical
    and contain all required headings and {{placeholder}} variables."""

    SOURCE = TEMPLATES_ROOT / "adr" / "template.md"
    BUNDLED = DATA_TEMPLATES_ROOT / "adr" / "template.md"

    def test_both_files_exist(self):
        self.assertTrue(self.SOURCE.is_file(), f"Source template missing: {self.SOURCE}")
        self.assertTrue(self.BUNDLED.is_file(), f"Bundled template missing: {self.BUNDLED}")

    def test_files_are_byte_identical(self):
        self.assertEqual(
            self.SOURCE.read_bytes(),
            self.BUNDLED.read_bytes(),
            "templates/adr/template.md and _data/templates/adr/template.md differ",
        )

    def test_required_section_headings_present(self):
        content = self.SOURCE.read_text(encoding="utf-8")
        for heading in (
            "## Summary",
            "## Context",
            "## Decision Drivers",
            "## Considered Options",
            "### Option A",
            "### Option B",
            "## Decision",
            "## Consequences",
            "### Positive",
            "### Negative",
        ):
            self.assertIn(heading, content, f"Missing heading in ADR template: {heading!r}")

    def test_placeholder_variables_match_adr_fields(self):
        content = self.SOURCE.read_text(encoding="utf-8")
        for placeholder in (
            "{{id}}", "{{title}}", "{{created_at}}", "{{authors}}",
            "{{description}}", "{{tags}}", "{{related_specs}}",
            "{{related_beads}}", "{{supersedes}}",
        ):
            self.assertIn(placeholder, content,
                          f"Missing placeholder in ADR template: {placeholder!r}")


# ---------------------------------------------------------------------------
# ensure_adr_directories
# ---------------------------------------------------------------------------


class TestEnsureAdrDirectories(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _import(self):
        from agent_takt.onboarding.upgrade import ensure_adr_directories
        return ensure_adr_directories

    def test_fresh_dir_creates_all_4_subdirs_and_gitkeeps(self):
        ensure_adr_directories = self._import()
        created = ensure_adr_directories(self.root)
        self.assertEqual(len(created), 4)
        for subdir in ("drafts", "approved", "superseded", "rejected"):
            d = self.root / "adr" / subdir
            self.assertTrue(d.is_dir(), f"Missing adr/{subdir}/")
            self.assertTrue((d / ".gitkeep").is_file(), f"Missing adr/{subdir}/.gitkeep")

    def test_full_tree_present_returns_empty_list(self):
        ensure_adr_directories = self._import()
        ensure_adr_directories(self.root)  # first run creates everything
        created_again = ensure_adr_directories(self.root)
        self.assertEqual(created_again, [])

    def test_partial_tree_creates_missing_dirs(self):
        ensure_adr_directories = self._import()
        # Pre-create only drafts/
        (self.root / "adr" / "drafts").mkdir(parents=True)
        (self.root / "adr" / "drafts" / ".gitkeep").touch()
        created = ensure_adr_directories(self.root)
        # Should have created 3 missing dirs
        self.assertEqual(len(created), 3)
        for subdir in ("approved", "superseded", "rejected"):
            self.assertTrue((self.root / "adr" / subdir / ".gitkeep").is_file())


if __name__ == "__main__":
    unittest.main()
