"""CLI integration tests for `takt adr` subcommands.

Covers: new, list, show (with --field), approve, approve --supersedes,
reject, supersede --by, validate; prefix resolution, datetime coercion,
error exits, and JSON output.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import types
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
TEMPLATES_ROOT = REPO_ROOT / "templates"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.adr import Adr, AdrStore
from agent_takt.cli.commands.adr import command_adr
from agent_takt.console import ConsoleReporter


# ---------------------------------------------------------------------------
# Base class and helpers
# ---------------------------------------------------------------------------


def _valid_adr_body() -> str:
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


class AdrCliTestBase(unittest.TestCase):
    """Base class: temp git repo with ADR template + lightweight storage stub."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

        # Init git repo
        subprocess.run(["git", "init"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.root, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=self.root, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "commit.gpgsign", "false"],
            cwd=self.root, check=True, capture_output=True,
        )

        # Install ADR template
        tmpl_dir = self.root / "templates" / "adr"
        tmpl_dir.mkdir(parents=True, exist_ok=True)
        src = TEMPLATES_ROOT / "adr" / "template.md"
        (tmpl_dir / "template.md").write_bytes(src.read_bytes())

        # Lightweight storage stub — command_adr only needs storage.root
        self.storage = types.SimpleNamespace(root=self.root)
        self.store = AdrStore(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _console(self) -> tuple[ConsoleReporter, io.StringIO]:
        stream = io.StringIO()
        return ConsoleReporter(stream=stream), stream

    def _run(self, args: Namespace) -> tuple[int, str]:
        console, stream = self._console()
        rc = command_adr(args, self.storage, console)  # type: ignore[arg-type]
        return rc, stream.getvalue()

    def _write_adr(self, adr_id: str, status: str = "draft", body: str = "") -> Path:
        subdir = {"draft": "drafts", "approved": "approved",
                  "superseded": "superseded", "rejected": "rejected"}[status]
        d = self.root / "adr" / subdir
        d.mkdir(parents=True, exist_ok=True)
        adr = Adr(
            id=adr_id,
            title=f"Test {adr_id}",
            status=status,
            created_at="2026-01-01T00:00:00+00:00",
            authors=["Test User"],
            accepted_at="2026-01-02T00:00:00+00:00" if status == "approved" else None,
        )
        path = d / f"adr-{adr_id[4:]}-test.md"
        self.store._save_file(path, adr, body)
        return path


# ---------------------------------------------------------------------------
# adr new
# ---------------------------------------------------------------------------


class TestCliAdrNew(AdrCliTestBase):
    def _new_args(self, title: str = "Test Decision", **kwargs) -> Namespace:
        defaults = dict(
            adr_command="new",
            title=title,
            description=None,
            tag=[],
            related_spec=[],
            related_bead=[],
            supersedes=[],
        )
        defaults.update(kwargs)
        return Namespace(**defaults)

    def test_basic_new_exits_0(self):
        rc, _output = self._run(self._new_args())
        self.assertEqual(rc, 0)

    def test_basic_new_creates_file_in_drafts(self):
        self._run(self._new_args())
        self.assertEqual(len(list((self.root / "adr" / "drafts").glob("*.md"))), 1)

    def test_basic_new_output_contains_id(self):
        _rc, output = self._run(self._new_args())
        self.assertIn("ADR-", output)

    def test_with_description(self):
        rc, _ = self._run(self._new_args(description="A brief description"))
        self.assertEqual(rc, 0)
        adr = self.store.find_by_id(self.store.load_all()[0].id)
        self.assertEqual(adr.description, "A brief description")

    def test_with_tags(self):
        rc, _ = self._run(self._new_args(tag=["security", "api"]))
        self.assertEqual(rc, 0)
        adr = self.store.load_all()[0]
        self.assertIn("security", adr.tags)
        self.assertIn("api", adr.tags)

    def test_with_related_spec(self):
        rc, _ = self._run(self._new_args(related_spec=["spec-abc123"]))
        self.assertEqual(rc, 0)
        adr = self.store.load_all()[0]
        self.assertIn("spec-abc123", adr.related_specs)

    def test_with_related_bead(self):
        rc, _ = self._run(self._new_args(related_bead=["B-12345678"]))
        self.assertEqual(rc, 0)
        adr = self.store.load_all()[0]
        self.assertIn("B-12345678", adr.related_beads)

    def test_with_supersedes(self):
        # Create an existing ADR to supersede
        self._write_adr("ADR-eeee0001")
        rc, _output = self._run(self._new_args(supersedes=["ADR-eeee0001"]))
        self.assertEqual(rc, 0)
        # The new ADR should reference the superseded one
        adrs = self.store.load_all()
        new_adr = next(a for a in adrs if "ADR-eeee0001" not in a.id)
        self.assertIn("ADR-eeee0001", new_adr.supersedes)

    def test_supersedes_invalid_ref_exits_1(self):
        rc, output = self._run(self._new_args(supersedes=["ADR-nonexist"]))
        self.assertEqual(rc, 1)
        self.assertIn("not found", output.lower())

    def test_missing_template_exits_1(self):
        (self.root / "templates" / "adr" / "template.md").unlink()
        rc, _output = self._run(self._new_args())
        self.assertEqual(rc, 1)


# ---------------------------------------------------------------------------
# adr list
# ---------------------------------------------------------------------------


class TestCliAdrList(AdrCliTestBase):
    def _list_args(self, **kwargs) -> Namespace:
        defaults = dict(
            adr_command="list",
            status_filter=[],
            tag_filter=[],
            plain=False,
            output_json=False,
        )
        defaults.update(kwargs)
        return Namespace(**defaults)

    def test_empty_list_outputs_sentinel(self):
        rc, output = self._run(self._list_args())
        self.assertEqual(rc, 0)
        self.assertIn("No ADRs found", output)

    def test_list_shows_existing_adrs(self):
        self._write_adr("ADR-a1b2c3d4")
        rc, output = self._run(self._list_args())
        self.assertEqual(rc, 0)
        self.assertIn("ADR-a1b2c3d4", output)

    def test_status_or_semantics(self):
        self._write_adr("ADR-d1111111", status="draft")
        self._write_adr("ADR-a2222222", status="approved")
        rc, output = self._run(self._list_args(status_filter=["draft", "approved"]))
        self.assertEqual(rc, 0)
        self.assertIn("ADR-d1111111", output)
        self.assertIn("ADR-a2222222", output)

    def test_status_filter_excludes_non_matching(self):
        self._write_adr("ADR-d1111111", status="draft")
        self._write_adr("ADR-a2222222", status="approved")
        rc, output = self._run(self._list_args(status_filter=["draft"]))
        self.assertIn("ADR-d1111111", output)
        self.assertNotIn("ADR-a2222222", output)

    def test_tag_and_semantics(self):
        d = self.root / "adr" / "drafts"
        d.mkdir(parents=True, exist_ok=True)
        adr1 = Adr(id="ADR-t1111111", title="T1", status="draft",
                   created_at="2026-01-01T00:00:00+00:00", authors=[], tags=["security", "api"])
        adr2 = Adr(id="ADR-t2222222", title="T2", status="draft",
                   created_at="2026-01-01T00:00:00+00:00", authors=[], tags=["api"])
        self.store._save_file(d / "adr-t1111111-t1.md", adr1, "")
        self.store._save_file(d / "adr-t2222222-t2.md", adr2, "")
        rc, output = self._run(self._list_args(tag_filter=["security", "api"]))
        self.assertIn("ADR-t1111111", output)
        self.assertNotIn("ADR-t2222222", output)

    def test_plain_flag_outputs_table(self):
        self._write_adr("ADR-a1b2c3d4")
        rc, output = self._run(self._list_args(plain=True))
        self.assertEqual(rc, 0)
        self.assertIn("ID", output)

    def test_json_flag_outputs_json_array(self):
        self._write_adr("ADR-a1b2c3d4")
        rc, output = self._run(self._list_args(output_json=True))
        self.assertEqual(rc, 0)
        parsed = json.loads(output.strip())
        self.assertIsInstance(parsed, list)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["id"], "ADR-a1b2c3d4")


# ---------------------------------------------------------------------------
# adr show
# ---------------------------------------------------------------------------


class TestCliAdrShow(AdrCliTestBase):
    def _show_args(self, adr_id: str, field: str | None = None) -> Namespace:
        return Namespace(adr_command="show", adr_id=adr_id, field=field)

    def test_show_with_full_id_exits_0(self):
        self._write_adr("ADR-a1b2c3d4", body=_valid_adr_body())
        rc, output = self._run(self._show_args("ADR-a1b2c3d4"))
        self.assertEqual(rc, 0)

    def test_show_with_full_id_outputs_json(self):
        self._write_adr("ADR-a1b2c3d4", body=_valid_adr_body())
        rc, output = self._run(self._show_args("ADR-a1b2c3d4"))
        parsed = json.loads(output.strip())
        self.assertEqual(parsed["id"], "ADR-a1b2c3d4")

    def test_show_with_bare_hex_prefix(self):
        self._write_adr("ADR-a1b2c3d4", body=_valid_adr_body())
        rc, output = self._run(self._show_args("a1b2"))
        self.assertEqual(rc, 0)
        parsed = json.loads(output.strip())
        self.assertEqual(parsed["id"], "ADR-a1b2c3d4")

    def test_show_with_adr_dash_prefix(self):
        self._write_adr("ADR-a1b2c3d4", body=_valid_adr_body())
        rc, output = self._run(self._show_args("ADR-a1b2"))
        self.assertEqual(rc, 0)

    def test_field_frontmatter_status(self):
        self._write_adr("ADR-a1b2c3d4", body=_valid_adr_body())
        rc, output = self._run(self._show_args("ADR-a1b2c3d4", field="status"))
        self.assertEqual(rc, 0)
        self.assertIn("draft", output)

    def test_field_frontmatter_authors_index(self):
        self._write_adr("ADR-a1b2c3d4", body=_valid_adr_body())
        rc, output = self._run(self._show_args("ADR-a1b2c3d4", field="authors[0]"))
        self.assertEqual(rc, 0)
        self.assertIn("Test User", output)

    def test_field_body_section_decision(self):
        self._write_adr("ADR-a1b2c3d4", body=_valid_adr_body())
        rc, output = self._run(self._show_args("ADR-a1b2c3d4", field="decision"))
        self.assertEqual(rc, 0)
        self.assertIn("Option A", output)

    def test_field_body_section_summary(self):
        self._write_adr("ADR-a1b2c3d4", body=_valid_adr_body())
        rc, output = self._run(self._show_args("ADR-a1b2c3d4", field="summary"))
        self.assertEqual(rc, 0)
        self.assertIn("approach A", output)

    def test_field_body_section_context(self):
        self._write_adr("ADR-a1b2c3d4", body=_valid_adr_body())
        rc, output = self._run(self._show_args("ADR-a1b2c3d4", field="context"))
        self.assertEqual(rc, 0)
        self.assertIn("constraints", output)

    def test_field_body_section_consequences_positive(self):
        self._write_adr("ADR-a1b2c3d4", body=_valid_adr_body())
        rc, output = self._run(self._show_args("ADR-a1b2c3d4", field="consequences.positive"))
        self.assertEqual(rc, 0)
        self.assertIn("Performance goals", output)

    def test_field_body_section_consequences_negative(self):
        self._write_adr("ADR-a1b2c3d4", body=_valid_adr_body())
        rc, output = self._run(self._show_args("ADR-a1b2c3d4", field="consequences.negative"))
        self.assertEqual(rc, 0)
        self.assertIn("implementation cost", output)

    def test_missing_field_exits_1(self):
        self._write_adr("ADR-a1b2c3d4", body=_valid_adr_body())
        rc, _ = self._run(self._show_args("ADR-a1b2c3d4", field="nonexistent_field_xyz"))
        self.assertEqual(rc, 1)

    def test_ambiguous_prefix_exits_1(self):
        self._write_adr("ADR-a1b2c3d4")
        self._write_adr("ADR-a1b2e5f6")
        rc, output = self._run(self._show_args("a1b2"))
        self.assertEqual(rc, 1)
        self.assertIn("Ambiguous", output)

    def test_no_match_prefix_exits_1(self):
        rc, output = self._run(self._show_args("xyz99999"))
        self.assertEqual(rc, 1)

    def test_created_at_is_iso_string_in_json_output(self):
        """Datetime coercion fix: created_at must be a string in the JSON output."""
        # Use new_adr so PyYAML writes the timestamp inline (no quotes)
        args = Namespace(
            adr_command="new",
            title="Datetime Test ADR",
            description=None,
            tag=[],
            related_spec=[],
            related_bead=[],
            supersedes=[],
        )
        rc, _ = self._run(args)
        self.assertEqual(rc, 0)
        all_adrs = self.store.load_all()
        adr_id = all_adrs[0].id
        rc2, output2 = self._run(self._show_args(adr_id))
        self.assertEqual(rc2, 0)
        parsed = json.loads(output2.strip())
        created_at = parsed["created_at"]
        self.assertIsInstance(created_at, str)
        import re
        self.assertRegex(created_at[:10], r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# adr approve
# ---------------------------------------------------------------------------


class TestCliAdrApprove(AdrCliTestBase):
    def _approve_args(self, adr_id: str, supersedes: list[str] | None = None) -> Namespace:
        return Namespace(adr_command="approve", adr_id=adr_id, supersedes=supersedes or [])

    @patch("agent_takt.cli.commands.adr._git_commit_adr_lifecycle")
    def test_happy_path_exits_0(self, mock_commit):
        self._write_adr("ADR-a1b2c3d4", body=_valid_adr_body())
        rc, output = self._run(self._approve_args("ADR-a1b2c3d4"))
        self.assertEqual(rc, 0)

    @patch("agent_takt.cli.commands.adr._git_commit_adr_lifecycle")
    def test_happy_path_transitions_to_approved(self, mock_commit):
        self._write_adr("ADR-a1b2c3d4", body=_valid_adr_body())
        self._run(self._approve_args("ADR-a1b2c3d4"))
        approved_files = list((self.root / "adr" / "approved").glob("*.md"))
        self.assertEqual(len(approved_files), 1)

    @patch("agent_takt.cli.commands.adr._git_commit_adr_lifecycle")
    def test_happy_path_output_contains_accepted_at(self, mock_commit):
        self._write_adr("ADR-a1b2c3d4", body=_valid_adr_body())
        rc, output = self._run(self._approve_args("ADR-a1b2c3d4"))
        self.assertIn("accepted_at", output)

    @patch("agent_takt.cli.commands.adr._git_commit_adr_lifecycle")
    def test_approve_with_supersedes_transitions_both(self, mock_commit):
        self._write_adr("ADR-a1b2c3d4", body=_valid_adr_body())
        self._write_adr("ADR-b5b5b5b5", status="approved", body=_valid_adr_body())
        rc, output = self._run(self._approve_args("ADR-a1b2c3d4", supersedes=["ADR-b5b5b5b5"]))
        self.assertEqual(rc, 0)
        self.assertIn("superseded", output.lower())
        superseded_files = list((self.root / "adr" / "superseded").glob("*.md"))
        self.assertEqual(len(superseded_files), 1)

    @patch("agent_takt.cli.commands.adr._git_commit_adr_lifecycle")
    def test_approve_with_supersedes_output_contains_superseded_by(self, mock_commit):
        self._write_adr("ADR-a1b2c3d4", body=_valid_adr_body())
        self._write_adr("ADR-b5b5b5b5", status="approved", body=_valid_adr_body())
        rc, output = self._run(self._approve_args("ADR-a1b2c3d4", supersedes=["ADR-b5b5b5b5"]))
        self.assertIn("superseded_by", output)

    def test_non_draft_adr_exits_1(self):
        self._write_adr("ADR-a1b2c3d4", status="approved", body=_valid_adr_body())
        rc, output = self._run(self._approve_args("ADR-a1b2c3d4"))
        self.assertEqual(rc, 1)

    def test_body_section_incomplete_exits_1(self):
        # Draft with no Decision section
        body = (
            "## Summary\n\nReal summary.\n\n"
            "## Context\n\nReal context.\n\n"
            "## Considered Options\n\n### Option A — Real\n\n* Good: works\n\n"
            "## Consequences\n\n### Positive\n\n* Good.\n\n### Negative\n\n* Bad.\n"
        )
        self._write_adr("ADR-a1b2c3d4", body=body)
        rc, _ = self._run(self._approve_args("ADR-a1b2c3d4"))
        self.assertEqual(rc, 1)

    def test_supersedes_non_approved_target_exits_1(self):
        self._write_adr("ADR-a1b2c3d4", body=_valid_adr_body())
        self._write_adr("ADR-c3c3c3c3", status="draft")  # draft, not approved
        rc, output = self._run(self._approve_args("ADR-a1b2c3d4", supersedes=["ADR-c3c3c3c3"]))
        self.assertEqual(rc, 1)

    def test_approve_with_prefix_resolution(self):
        self._write_adr("ADR-a1b2c3d4", body=_valid_adr_body())
        with patch("agent_takt.cli.commands.adr._git_commit_adr_lifecycle"):
            rc, _ = self._run(self._approve_args("a1b2"))
        self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# adr reject
# ---------------------------------------------------------------------------


class TestCliAdrReject(AdrCliTestBase):
    def _reject_args(self, adr_id: str) -> Namespace:
        return Namespace(adr_command="reject", adr_id=adr_id)

    @patch("agent_takt.cli.commands.adr._git_commit_adr_lifecycle")
    def test_happy_path_exits_0(self, mock_commit):
        self._write_adr("ADR-a1b2c3d4")
        rc, _ = self._run(self._reject_args("ADR-a1b2c3d4"))
        self.assertEqual(rc, 0)

    @patch("agent_takt.cli.commands.adr._git_commit_adr_lifecycle")
    def test_happy_path_moves_to_rejected_dir(self, mock_commit):
        self._write_adr("ADR-a1b2c3d4")
        self._run(self._reject_args("ADR-a1b2c3d4"))
        self.assertEqual(len(list((self.root / "adr" / "rejected").glob("*.md"))), 1)
        self.assertEqual(len(list((self.root / "adr" / "drafts").glob("*.md"))), 0)

    @patch("agent_takt.cli.commands.adr._git_commit_adr_lifecycle")
    def test_happy_path_output_contains_transition(self, mock_commit):
        self._write_adr("ADR-a1b2c3d4")
        rc, output = self._run(self._reject_args("ADR-a1b2c3d4"))
        self.assertIn("rejected", output.lower())

    def test_non_draft_exits_1(self):
        self._write_adr("ADR-a1b2c3d4", status="approved", body=_valid_adr_body())
        rc, _ = self._run(self._reject_args("ADR-a1b2c3d4"))
        self.assertEqual(rc, 1)

    @patch("agent_takt.cli.commands.adr._git_commit_adr_lifecycle")
    def test_prefix_resolution(self, mock_commit):
        self._write_adr("ADR-a1b2c3d4")
        rc, _ = self._run(self._reject_args("a1b2"))
        self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# adr supersede
# ---------------------------------------------------------------------------


class TestCliAdrSupersede(AdrCliTestBase):
    def _supersede_args(self, adr_id: str, by_adr_id: str) -> Namespace:
        return Namespace(adr_command="supersede", adr_id=adr_id, by_adr_id=by_adr_id)

    @patch("agent_takt.cli.commands.adr._git_commit_adr_lifecycle")
    def test_happy_path_exits_0(self, mock_commit):
        self._write_adr("ADR-a1b2c3d4", status="approved", body=_valid_adr_body())
        self._write_adr("ADR-b5b5b5b5", status="approved", body=_valid_adr_body())
        rc, _ = self._run(self._supersede_args("ADR-a1b2c3d4", "ADR-b5b5b5b5"))
        self.assertEqual(rc, 0)

    @patch("agent_takt.cli.commands.adr._git_commit_adr_lifecycle")
    def test_happy_path_moves_to_superseded_dir(self, mock_commit):
        self._write_adr("ADR-a1b2c3d4", status="approved", body=_valid_adr_body())
        self._write_adr("ADR-b5b5b5b5", status="approved", body=_valid_adr_body())
        self._run(self._supersede_args("ADR-a1b2c3d4", "ADR-b5b5b5b5"))
        self.assertEqual(len(list((self.root / "adr" / "superseded").glob("*.md"))), 1)

    @patch("agent_takt.cli.commands.adr._git_commit_adr_lifecycle")
    def test_happy_path_output_contains_superseded_at(self, mock_commit):
        self._write_adr("ADR-a1b2c3d4", status="approved", body=_valid_adr_body())
        self._write_adr("ADR-b5b5b5b5", status="approved", body=_valid_adr_body())
        rc, output = self._run(self._supersede_args("ADR-a1b2c3d4", "ADR-b5b5b5b5"))
        self.assertIn("superseded_at", output)

    def test_non_approved_old_exits_1(self):
        self._write_adr("ADR-a1b2c3d4", status="draft")
        self._write_adr("ADR-b5b5b5b5", status="approved", body=_valid_adr_body())
        rc, _ = self._run(self._supersede_args("ADR-a1b2c3d4", "ADR-b5b5b5b5"))
        self.assertEqual(rc, 1)

    def test_non_approved_new_exits_1(self):
        self._write_adr("ADR-a1b2c3d4", status="approved", body=_valid_adr_body())
        self._write_adr("ADR-b5b5b5b5", status="draft")
        rc, _ = self._run(self._supersede_args("ADR-a1b2c3d4", "ADR-b5b5b5b5"))
        self.assertEqual(rc, 1)

    @patch("agent_takt.cli.commands.adr._git_commit_adr_lifecycle")
    def test_prefix_resolution_in_both_args(self, mock_commit):
        self._write_adr("ADR-a1b2c3d4", status="approved", body=_valid_adr_body())
        self._write_adr("ADR-b5b5b5b5", status="approved", body=_valid_adr_body())
        rc, _ = self._run(self._supersede_args("a1b2", "b5b5"))
        self.assertEqual(rc, 0)


# ---------------------------------------------------------------------------
# adr validate
# ---------------------------------------------------------------------------


class TestCliAdrValidate(AdrCliTestBase):
    def _validate_args(self) -> Namespace:
        return Namespace(adr_command="validate")

    def test_clean_pass_exits_0(self):
        self._write_adr("ADR-a1b2c3d4")
        rc, output = self._run(self._validate_args())
        self.assertEqual(rc, 0)

    def test_clean_pass_outputs_all_adrs_valid(self):
        self._write_adr("ADR-a1b2c3d4")
        rc, output = self._run(self._validate_args())
        self.assertIn("All ADRs valid", output)

    def test_errors_exits_nonzero(self):
        # Write an ADR with dangling superseded_by
        d = self.root / "adr" / "superseded"
        d.mkdir(parents=True, exist_ok=True)
        from agent_takt.adr import _render_frontmatter
        adr = Adr(
            id="ADR-a1b2c3d4",
            title="Test",
            status="superseded",
            created_at="2026-01-01T00:00:00+00:00",
            authors=[],
            superseded_by="ADR-nonexistent",
        )
        self.store._save_file(d / "adr-a1b2c3d4-test.md", adr, "")
        rc, output = self._run(self._validate_args())
        self.assertNotEqual(rc, 0)

    def test_errors_output_one_line_per_error(self):
        # Write two ADRs with missing required fields
        d = self.root / "adr" / "drafts"
        d.mkdir(parents=True, exist_ok=True)
        (d / "bad1.md").write_text(
            "---\nid: ADR-aaaa0001\nstatus: draft\n---\nbody\n", encoding="utf-8"
        )
        (d / "bad2.md").write_text(
            "---\nid: ADR-bbbb0002\nstatus: draft\n---\nbody\n", encoding="utf-8"
        )
        rc, output = self._run(self._validate_args())
        self.assertNotEqual(rc, 0)
        # Each error appears on its own line
        lines = [l for l in output.splitlines() if "missing required frontmatter" in l]
        self.assertGreaterEqual(len(lines), 2)

    def test_no_adrs_exits_0(self):
        rc, output = self._run(self._validate_args())
        self.assertEqual(rc, 0)
        self.assertIn("All ADRs valid", output)


if __name__ == "__main__":
    unittest.main()
