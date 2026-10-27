"""Tests for spec.py validate and set-status-done (merge-on-done) behaviour.

All tests invoke the canonical spec.py as a subprocess in a temporary directory
so the script's CWD-relative path logic is exercised end-to-end.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_SPEC_PY = REPO_ROOT / "src" / "agent_takt" / "_data" / "claude_skills" / "skill-spec-management" / "spec.py"
AGENTS_SPEC_PY = REPO_ROOT / "src" / "agent_takt" / "_data" / "agents_skills" / "task" / "spec-management" / "spec.py"


def _run(args: list[str], cwd: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run spec.py with the given args in cwd."""
    cmd = [sys.executable, str(CANONICAL_SPEC_PY)] + args
    merged_env = {**os.environ, "NO_COLOR": "1"}
    if env:
        merged_env.update(env)
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=merged_env)


def _make_spec_dir(tmp: str) -> None:
    """Create specs/{drafts,planned,done}/ under tmp."""
    for sub in ("drafts", "planned", "done"):
        os.makedirs(os.path.join(tmp, "specs", sub), exist_ok=True)


def _write_spec(tmp: str, filename: str, content: str) -> str:
    """Write content to specs/drafts/<filename> and return the path."""
    path = os.path.join(tmp, "specs", "drafts", filename)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def _write_current_capability(tmp: str, capability: str, content: str) -> str:
    """Write a capability file to specs/current/<capability>.md."""
    current_dir = os.path.join(tmp, "specs", "current")
    os.makedirs(current_dir, exist_ok=True)
    path = os.path.join(current_dir, f"{capability}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def _make_spec_content(requirements_section: str) -> str:
    """Build a full spec file with the given Requirements section body."""
    return (
        "---\n"
        "name: Test Spec\n"
        "id: spec-test1234\n"
        "description: null\n"
        "dependencies: null\n"
        "priority: null\n"
        "complexity: null\n"
        "status: draft\n"
        "tags: []\n"
        "scope:\n"
        "  in: null\n"
        "  out: null\n"
        "feature_root_id: null\n"
        "---\n"
        "# Test Spec\n"
        "\n"
        "## Objective\n"
        "\n"
        "## Problems to Fix\n"
        "\n"
        "## Changes\n"
        "\n"
        "## Requirements\n"
        "\n"
    ) + requirements_section + (
        "\n"
        "## Files to Modify\n"
        "\n"
        "| File | Change |\n"
        "|---|---|\n"
        "\n"
        "## Acceptance Criteria\n"
        "\n"
        "## Pending Decisions\n"
    )


# ---------------------------------------------------------------------------
# Parity test
# ---------------------------------------------------------------------------


class TestSpecPyParity(unittest.TestCase):
    def test_canonical_and_agents_skills_are_byte_identical(self):
        """Both packaged spec.py copies must be byte-identical."""
        canonical = CANONICAL_SPEC_PY.read_bytes()
        agents = AGENTS_SPEC_PY.read_bytes()
        self.assertEqual(
            canonical,
            agents,
            "spec.py copies have diverged — run the sync bead to reconcile",
        )


# ---------------------------------------------------------------------------
# validate: absent Requirements section
# ---------------------------------------------------------------------------


class TestValidateNoRequirementsSection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _make_spec_dir(self.tmp)
        content = textwrap.dedent("""\
            ---
            name: Simple Spec
            id: spec-aaaa0001
            description: null
            dependencies: null
            priority: null
            complexity: null
            status: draft
            tags: []
            scope:
              in: null
              out: null
            feature_root_id: null
            ---
            # Simple Spec

            ## Objective

            ## Acceptance Criteria
            """)
        self.spec_path = _write_spec(self.tmp, "simple-spec.md", content)

    def test_exits_0_no_requirements_section(self):
        r = _run(["validate", "simple-spec"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no Requirements section", r.stdout)

    def test_no_files_written(self):
        current_dir = os.path.join(self.tmp, "specs", "current")
        _run(["validate", "simple-spec"], self.tmp)
        self.assertFalse(os.path.exists(current_dir))


# ---------------------------------------------------------------------------
# validate: well-formed entries
# ---------------------------------------------------------------------------


class TestValidateWellFormedEntries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _make_spec_dir(self.tmp)

    def _write_and_validate(self, requirements_body: str) -> subprocess.CompletedProcess:
        content = _make_spec_content(requirements_body)
        _write_spec(self.tmp, "my-spec.md", content)
        return _run(["validate", "my-spec"], self.tmp)

    def test_well_formed_added_exits_0(self):
        body = textwrap.dedent("""\
            ### ADDED: bead-lifecycle / Recovery bead creation
            The scheduler SHALL create a recovery bead automatically.
            #### Scenario: Developer bead returns no JSON
            - **WHEN** a developer bead produces no parseable JSON
            - **THEN** a recovery bead is created
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 requirement delta", r.stdout)

    def test_well_formed_modified_exits_0(self):
        _write_current_capability(
            self.tmp,
            "bead-lifecycle",
            "# bead-lifecycle\n\n## Requirement: Recovery bead creation\n<!-- source: spec-old -->\nThe scheduler SHALL do things.\n",
        )
        body = textwrap.dedent("""\
            ### MODIFIED: bead-lifecycle / Recovery bead creation
            The scheduler MUST update the recovery mechanism.
            #### Scenario: Developer bead times out
            - **WHEN** a developer bead exceeds its timeout
            - **THEN** a recovery bead is created automatically
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 requirement delta", r.stdout)

    def test_well_formed_removed_exits_0(self):
        _write_current_capability(
            self.tmp,
            "bead-lifecycle",
            "# bead-lifecycle\n\n## Requirement: Old requirement\n<!-- source: spec-old -->\nSome text.\n",
        )
        body = textwrap.dedent("""\
            ### REMOVED: bead-lifecycle / Old requirement
            Reason: superseded by automatic recovery.
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_multiple_well_formed_entries_exit_0(self):
        _write_current_capability(
            self.tmp,
            "bead-lifecycle",
            "# bead-lifecycle\n\n## Requirement: Existing req\n<!-- source: spec-old -->\nText.\n",
        )
        body = textwrap.dedent("""\
            ### ADDED: bead-lifecycle / New feature
            The system SHALL support new feature.
            #### Scenario: Feature is triggered
            - **WHEN** user triggers feature
            - **THEN** feature activates
            ### REMOVED: bead-lifecycle / Existing req
            Reason: replaced by new feature.
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2 requirement deltas", r.stdout)

    def test_capability_slug_with_numbers_accepted(self):
        body = textwrap.dedent("""\
            ### ADDED: ui2-panel / New panel feature
            The UI2 panel SHALL display beads.
            #### Scenario: Bead list visible
            - **WHEN** user opens ui2 panel
            - **THEN** bead list is shown
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_empty_requirements_section_exits_0(self):
        # Section present but contains only whitespace/comment
        body = textwrap.dedent("""\
            <!-- no entries -->
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("0 requirement deltas", r.stdout)

    def test_section_at_end_of_file(self):
        content = textwrap.dedent("""\
            ---
            name: End Section Spec
            id: spec-endtest1
            description: null
            dependencies: null
            priority: null
            complexity: null
            status: draft
            tags: []
            scope:
              in: null
              out: null
            feature_root_id: null
            ---
            # End Section Spec

            ## Requirements

            ### ADDED: my-capability / Some requirement
            The system SHALL do something.
            #### Scenario: Trigger happens
            - **WHEN** trigger happens
            - **THEN** something occurs
            """)
        _write_spec(self.tmp, "end-section.md", content)
        r = _run(["validate", "end-section"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 requirement delta", r.stdout)


# ---------------------------------------------------------------------------
# validate: error cases
# ---------------------------------------------------------------------------


class TestValidateErrors(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _make_spec_dir(self.tmp)

    def _write_and_validate(self, requirements_body: str) -> subprocess.CompletedProcess:
        content = _make_spec_content(requirements_body)
        _write_spec(self.tmp, "my-spec.md", content)
        return _run(["validate", "my-spec"], self.tmp)

    def test_malformed_heading_exits_1(self):
        body = "### BAD HEADING WITHOUT COLON\nSome text.\n"
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 1)
        self.assertIn("malformed heading", r.stderr)

    def test_non_kebab_case_capability_exits_1(self):
        body = textwrap.dedent("""\
            ### ADDED: BadCapability / Some req
            The system SHALL do something.
            #### Scenario: X
            - **WHEN** x
            - **THEN** y
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 1)
        self.assertIn("invalid capability slug", r.stderr)

    def test_capability_with_spaces_exits_1(self):
        body = textwrap.dedent("""\
            ### ADDED: bad capability / Some req
            The system SHALL do something.
            #### Scenario: X
            - **WHEN** x
            - **THEN** y
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 1)
        self.assertIn("invalid capability slug", r.stderr)

    def test_added_missing_shall_must_exits_1(self):
        body = textwrap.dedent("""\
            ### ADDED: bead-lifecycle / Some feature
            The system should do something here.
            #### Scenario: X
            - **WHEN** x happens
            - **THEN** y occurs
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 1)
        self.assertIn("SHALL or MUST", r.stderr)

    def test_modified_missing_shall_must_exits_1(self):
        _write_current_capability(
            self.tmp,
            "bead-lifecycle",
            "# bead-lifecycle\n\n## Requirement: Some feature\n<!-- source: old -->\nText.\n",
        )
        body = textwrap.dedent("""\
            ### MODIFIED: bead-lifecycle / Some feature
            The system will do something here.
            #### Scenario: X
            - **WHEN** x happens
            - **THEN** y occurs
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 1)
        self.assertIn("SHALL or MUST", r.stderr)

    def test_added_no_scenario_exits_1(self):
        body = textwrap.dedent("""\
            ### ADDED: bead-lifecycle / Some feature
            The system SHALL do something. No scenario follows.
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Scenario", r.stderr)

    def test_modified_no_scenario_exits_1(self):
        _write_current_capability(
            self.tmp,
            "bead-lifecycle",
            "# bead-lifecycle\n\n## Requirement: Some feature\n<!-- source: old -->\nText.\n",
        )
        body = textwrap.dedent("""\
            ### MODIFIED: bead-lifecycle / Some feature
            The system SHALL now do something. No scenario.
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Scenario", r.stderr)

    def test_scenario_missing_when_exits_1(self):
        body = textwrap.dedent("""\
            ### ADDED: bead-lifecycle / Some feature
            The system SHALL do something.
            #### Scenario: X
            - **THEN** something happens
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 1)
        self.assertIn("WHEN", r.stderr)

    def test_scenario_missing_then_exits_1(self):
        body = textwrap.dedent("""\
            ### ADDED: bead-lifecycle / Some feature
            The system SHALL do something.
            #### Scenario: X
            - **WHEN** something triggers
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 1)
        self.assertIn("THEN", r.stderr)

    def test_removed_without_reason_exits_1(self):
        _write_current_capability(
            self.tmp,
            "bead-lifecycle",
            "# bead-lifecycle\n\n## Requirement: Old req\n<!-- source: old -->\nText.\n",
        )
        body = textwrap.dedent("""\
            ### REMOVED: bead-lifecycle / Old req
            No reason line here.
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Reason", r.stderr)

    def test_removed_with_scenario_exits_1(self):
        _write_current_capability(
            self.tmp,
            "bead-lifecycle",
            "# bead-lifecycle\n\n## Requirement: Old req\n<!-- source: old -->\nText.\n",
        )
        body = textwrap.dedent("""\
            ### REMOVED: bead-lifecycle / Old req
            Reason: superseded.
            #### Scenario: Should not exist
            - **WHEN** x
            - **THEN** y
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 1)
        self.assertIn("scenarios", r.stderr)

    def test_duplicate_capability_req_pair_exits_1(self):
        body = textwrap.dedent("""\
            ### ADDED: bead-lifecycle / Duplicate req
            The system SHALL do something.
            #### Scenario: X
            - **WHEN** x
            - **THEN** y
            ### ADDED: bead-lifecycle / Duplicate req
            The system SHALL do something again.
            #### Scenario: Y
            - **WHEN** y
            - **THEN** z
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 1)
        self.assertIn("duplicate", r.stderr)

    def test_duplicate_detection_is_case_insensitive(self):
        body = textwrap.dedent("""\
            ### ADDED: bead-lifecycle / Bead-Lifecycle Req
            The system SHALL do something.
            #### Scenario: X
            - **WHEN** x
            - **THEN** y
            ### ADDED: bead-lifecycle / bead-lifecycle req
            The system MUST do something again.
            #### Scenario: Y
            - **WHEN** y
            - **THEN** z
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 1)
        self.assertIn("duplicate", r.stderr)

    def test_added_for_existing_requirement_exits_1(self):
        _write_current_capability(
            self.tmp,
            "bead-lifecycle",
            "# bead-lifecycle\n\n## Requirement: Existing req\n<!-- source: old -->\nText.\n",
        )
        body = textwrap.dedent("""\
            ### ADDED: bead-lifecycle / Existing req
            The system SHALL add it again.
            #### Scenario: X
            - **WHEN** x
            - **THEN** y
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 1)
        self.assertIn("already exists", r.stderr)

    def test_modified_for_missing_requirement_exits_1(self):
        _write_current_capability(
            self.tmp,
            "bead-lifecycle",
            "# bead-lifecycle\n\n## Requirement: Existing req\n<!-- source: old -->\nText.\n",
        )
        body = textwrap.dedent("""\
            ### MODIFIED: bead-lifecycle / Nonexistent req
            The system SHALL update it.
            #### Scenario: X
            - **WHEN** x
            - **THEN** y
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not found", r.stderr)

    def test_removed_for_missing_requirement_exits_1(self):
        _write_current_capability(
            self.tmp,
            "bead-lifecycle",
            "# bead-lifecycle\n\n## Requirement: Existing req\n<!-- source: old -->\nText.\n",
        )
        body = textwrap.dedent("""\
            ### REMOVED: bead-lifecycle / Nonexistent req
            Reason: does not exist.
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not found", r.stderr)

    def test_modified_when_capability_file_absent_exits_1(self):
        body = textwrap.dedent("""\
            ### MODIFIED: missing-cap / Some req
            The system SHALL update it.
            #### Scenario: X
            - **WHEN** x
            - **THEN** y
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 1)
        self.assertIn("capability file not found", r.stderr)

    def test_removed_when_capability_file_absent_exits_1(self):
        body = textwrap.dedent("""\
            ### REMOVED: missing-cap / Some req
            Reason: removing something missing.
            """)
        r = self._write_and_validate(body)
        self.assertEqual(r.returncode, 1)
        self.assertIn("capability file not found", r.stderr)

    def test_validate_never_writes_files(self):
        """validate must be read-only — no files created in specs/current/."""
        body = textwrap.dedent("""\
            ### ADDED: new-cap / Some requirement
            The system SHALL do something.
            #### Scenario: X
            - **WHEN** x
            - **THEN** y
            """)
        content = _make_spec_content(body)
        _write_spec(self.tmp, "my-spec.md", content)
        current_dir = os.path.join(self.tmp, "specs", "current")
        before = set(os.listdir(current_dir)) if os.path.exists(current_dir) else set()
        _run(["validate", "my-spec"], self.tmp)
        after = set(os.listdir(current_dir)) if os.path.exists(current_dir) else set()
        self.assertEqual(before, after, "validate must not write files")


# ---------------------------------------------------------------------------
# validate: consistency checks against specs/current/
# ---------------------------------------------------------------------------


class TestValidateCurrentConsistency(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _make_spec_dir(self.tmp)

    def test_added_passes_when_current_absent(self):
        body = textwrap.dedent("""\
            ### ADDED: brand-new-cap / Fresh requirement
            The system SHALL add this.
            #### Scenario: X
            - **WHEN** trigger
            - **THEN** outcome
            """)
        content = _make_spec_content(body)
        _write_spec(self.tmp, "my-spec.md", content)
        r = _run(["validate", "my-spec"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_modified_fails_when_current_absent(self):
        body = textwrap.dedent("""\
            ### MODIFIED: absent-cap / Some req
            The system SHALL update it.
            #### Scenario: X
            - **WHEN** x
            - **THEN** y
            """)
        content = _make_spec_content(body)
        _write_spec(self.tmp, "my-spec.md", content)
        r = _run(["validate", "my-spec"], self.tmp)
        self.assertEqual(r.returncode, 1)
        self.assertIn("capability file not found", r.stderr)

    def test_removed_fails_when_current_absent(self):
        body = textwrap.dedent("""\
            ### REMOVED: absent-cap / Some req
            Reason: no longer needed.
            """)
        content = _make_spec_content(body)
        _write_spec(self.tmp, "my-spec.md", content)
        r = _run(["validate", "my-spec"], self.tmp)
        self.assertEqual(r.returncode, 1)
        self.assertIn("capability file not found", r.stderr)

    def test_modified_passes_when_requirement_exists(self):
        _write_current_capability(
            self.tmp,
            "existing-cap",
            "# existing-cap\n\n## Requirement: Known req\n<!-- source: spec-old -->\nThe system SHALL do X.\n",
        )
        body = textwrap.dedent("""\
            ### MODIFIED: existing-cap / Known req
            The system MUST now do X better.
            #### Scenario: Improvement
            - **WHEN** user triggers
            - **THEN** improved result
            """)
        content = _make_spec_content(body)
        _write_spec(self.tmp, "my-spec.md", content)
        r = _run(["validate", "my-spec"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_removed_passes_when_requirement_exists(self):
        _write_current_capability(
            self.tmp,
            "existing-cap",
            "# existing-cap\n\n## Requirement: Old req\n<!-- source: spec-old -->\nText.\n",
        )
        body = textwrap.dedent("""\
            ### REMOVED: existing-cap / Old req
            Reason: no longer needed.
            """)
        content = _make_spec_content(body)
        _write_spec(self.tmp, "my-spec.md", content)
        r = _run(["validate", "my-spec"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)


# ---------------------------------------------------------------------------
# set status done: merge-on-done behaviour
# ---------------------------------------------------------------------------


class TestSetStatusDoneMerge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _make_spec_dir(self.tmp)

    def _make_spec(self, filename: str, spec_id: str, requirements_body: str) -> str:
        content = (
            "---\n"
            f"name: Test Spec\n"
            f"id: {spec_id}\n"
            "description: null\n"
            "dependencies: null\n"
            "priority: null\n"
            "complexity: null\n"
            "status: draft\n"
            "tags: []\n"
            "scope:\n"
            "  in: null\n"
            "  out: null\n"
            "feature_root_id: null\n"
            "---\n"
            "# Test Spec\n"
            "\n"
            "## Objective\n"
            "\n"
            "## Requirements\n"
            "\n"
        ) + requirements_body + (
            "\n"
            "## Acceptance Criteria\n"
        )
        path = _write_spec(self.tmp, filename, content)
        return path

    def test_added_creates_capability_file_with_correct_structure(self):
        self._make_spec(
            "my-spec.md",
            "spec-test0001",
            textwrap.dedent("""\
                ### ADDED: bead-lifecycle / Recovery bead on parse failure
                The scheduler SHALL create a recovery bead when output has no JSON.
                #### Scenario: Developer bead returns prose only
                - **WHEN** a developer bead's runner output contains no parseable JSON
                - **THEN** a bead `<id>-recovery` with `agent_type=recovery` is created
                """),
        )
        r = _run(["set", "status", "done", "my-spec"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("merged:", r.stdout)

        cap_path = os.path.join(self.tmp, "specs", "current", "bead-lifecycle.md")
        self.assertTrue(os.path.exists(cap_path), "capability file should be created")
        cap_content = Path(cap_path).read_text(encoding="utf-8")
        self.assertIn("# bead-lifecycle", cap_content)
        self.assertIn("## Requirement: Recovery bead on parse failure", cap_content)
        self.assertIn("<!-- source: spec-test0001 -->", cap_content)
        # Scenario headings in capability file use ### not ####
        self.assertIn("### Scenario:", cap_content)
        self.assertNotIn("#### Scenario:", cap_content)

    def test_added_appends_to_existing_capability_file(self):
        _write_current_capability(
            self.tmp,
            "bead-lifecycle",
            "# bead-lifecycle\n\n## Requirement: Existing req\n<!-- source: spec-old -->\nThe system SHALL do X.\n### Scenario: Trigger\n- **WHEN** trigger\n- **THEN** outcome\n",
        )
        self._make_spec(
            "my-spec.md",
            "spec-test0002",
            textwrap.dedent("""\
                ### ADDED: bead-lifecycle / New requirement
                The scheduler MUST add new functionality.
                #### Scenario: New trigger
                - **WHEN** new trigger fires
                - **THEN** new outcome occurs
                """),
        )
        r = _run(["set", "status", "done", "my-spec"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)

        cap_content = Path(os.path.join(self.tmp, "specs", "current", "bead-lifecycle.md")).read_text(encoding="utf-8")
        self.assertIn("## Requirement: Existing req", cap_content)
        self.assertIn("## Requirement: New requirement", cap_content)

    def test_modified_replaces_block_in_place(self):
        _write_current_capability(
            self.tmp,
            "bead-lifecycle",
            "# bead-lifecycle\n\n## Requirement: Req alpha\n<!-- source: spec-old -->\nThe system SHALL do alpha.\n### Scenario: Alpha trigger\n- **WHEN** alpha\n- **THEN** result\n\n## Requirement: Req beta\n<!-- source: spec-old -->\nThe system SHALL do beta.\n",
        )
        self._make_spec(
            "my-spec.md",
            "spec-test0003",
            textwrap.dedent("""\
                ### MODIFIED: bead-lifecycle / Req alpha
                The system MUST do alpha better.
                #### Scenario: Updated alpha trigger
                - **WHEN** alpha fires
                - **THEN** better result
                """),
        )
        r = _run(["set", "status", "done", "my-spec"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)

        cap_content = Path(os.path.join(self.tmp, "specs", "current", "bead-lifecycle.md")).read_text(encoding="utf-8")
        self.assertIn("## Requirement: Req alpha", cap_content)
        self.assertIn("## Requirement: Req beta", cap_content)
        self.assertIn("MUST do alpha better", cap_content)
        self.assertNotIn("SHALL do alpha.", cap_content)
        self.assertIn("<!-- source: spec-test0003 -->", cap_content)

    def test_removed_deletes_block(self):
        _write_current_capability(
            self.tmp,
            "bead-lifecycle",
            "# bead-lifecycle\n\n## Requirement: To remove\n<!-- source: old -->\nThe system SHALL do something.\n\n## Requirement: To keep\n<!-- source: old -->\nThe system SHALL keep this.\n",
        )
        self._make_spec(
            "my-spec.md",
            "spec-test0004",
            textwrap.dedent("""\
                ### REMOVED: bead-lifecycle / To remove
                Reason: no longer needed.
                """),
        )
        r = _run(["set", "status", "done", "my-spec"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)

        cap_content = Path(os.path.join(self.tmp, "specs", "current", "bead-lifecycle.md")).read_text(encoding="utf-8")
        self.assertNotIn("To remove", cap_content)
        self.assertIn("To keep", cap_content)

    def test_removed_last_requirement_deletes_file(self):
        _write_current_capability(
            self.tmp,
            "bead-lifecycle",
            "# bead-lifecycle\n\n## Requirement: Only req\n<!-- source: old -->\nThe system SHALL do this.\n",
        )
        self._make_spec(
            "my-spec.md",
            "spec-test0005",
            textwrap.dedent("""\
                ### REMOVED: bead-lifecycle / Only req
                Reason: no longer relevant.
                """),
        )
        r = _run(["set", "status", "done", "my-spec"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)

        cap_path = os.path.join(self.tmp, "specs", "current", "bead-lifecycle.md")
        self.assertFalse(os.path.exists(cap_path), "empty capability file should be deleted")

    def test_invalid_spec_exits_1_and_leaves_current_unchanged(self):
        _write_current_capability(
            self.tmp,
            "bead-lifecycle",
            "# bead-lifecycle\n\n## Requirement: Existing\n<!-- source: old -->\nText.\n",
        )
        before_content = Path(os.path.join(self.tmp, "specs", "current", "bead-lifecycle.md")).read_text(encoding="utf-8")

        # ADDED for a requirement that already exists → validation error
        self._make_spec(
            "bad-spec.md",
            "spec-test0006",
            textwrap.dedent("""\
                ### ADDED: bead-lifecycle / Existing
                The system SHALL add this again.
                #### Scenario: X
                - **WHEN** x
                - **THEN** y
                """),
        )
        r = _run(["set", "status", "done", "bad-spec"], self.tmp)
        self.assertEqual(r.returncode, 1)

        after_content = Path(os.path.join(self.tmp, "specs", "current", "bead-lifecycle.md")).read_text(encoding="utf-8")
        self.assertEqual(before_content, after_content, "specs/current/ must be unchanged on validation failure")

    def test_set_status_done_twice_merges_once(self):
        self._make_spec(
            "idempotent-spec.md",
            "spec-test0007",
            textwrap.dedent("""\
                ### ADDED: once-cap / Unique requirement
                The system SHALL do this once.
                #### Scenario: First trigger
                - **WHEN** trigger fires
                - **THEN** result occurs
                """),
        )
        r1 = _run(["set", "status", "done", "idempotent-spec"], self.tmp)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertIn("merged:", r1.stdout)

        # Second call: spec is already done; merge should not re-apply
        r2 = _run(["set", "status", "done", "idempotent-spec"], self.tmp)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertNotIn("merged:", r2.stdout)

    def test_set_status_planned_does_not_touch_current(self):
        self._make_spec(
            "planned-spec.md",
            "spec-test0008",
            textwrap.dedent("""\
                ### ADDED: planned-cap / Some req
                The system SHALL do something.
                #### Scenario: X
                - **WHEN** x
                - **THEN** y
                """),
        )
        current_dir = os.path.join(self.tmp, "specs", "current")
        r = _run(["set", "status", "planned", "planned-spec"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(current_dir, "planned-cap.md")))

    def test_set_status_draft_does_not_touch_current(self):
        # Create spec in planned state first
        content = textwrap.dedent("""\
            ---
            name: Back to Draft
            id: spec-test0009
            description: null
            dependencies: null
            priority: null
            complexity: null
            status: planned
            tags: []
            scope:
              in: null
              out: null
            feature_root_id: null
            ---
            # Back to Draft

            ## Requirements

            ### ADDED: draft-cap / Some req
            The system SHALL do something.
            #### Scenario: X
            - **WHEN** x
            - **THEN** y

            ## Acceptance Criteria
            """)
        path = os.path.join(self.tmp, "specs", "planned", "back-to-draft.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        current_dir = os.path.join(self.tmp, "specs", "current")
        r = _run(["set", "status", "draft", "back-to-draft"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(current_dir, "draft-cap.md")))

    def test_spec_with_no_requirements_section_moves_to_done(self):
        content = textwrap.dedent("""\
            ---
            name: Simple Done Spec
            id: spec-test0010
            description: null
            dependencies: null
            priority: null
            complexity: null
            status: draft
            tags: []
            scope:
              in: null
              out: null
            feature_root_id: null
            ---
            # Simple Done Spec

            ## Objective
            Simple.

            ## Acceptance Criteria
            """)
        _write_spec(self.tmp, "simple-done.md", content)
        r = _run(["set", "status", "done", "simple-done"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("merged:", r.stdout)
        done_path = os.path.join(self.tmp, "specs", "done", "simple-done.md")
        self.assertTrue(os.path.exists(done_path))


# ---------------------------------------------------------------------------
# specs/current/ exclusion from find_all_specs
# ---------------------------------------------------------------------------


class TestCurrentDirExclusion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _make_spec_dir(self.tmp)
        # Create a normal draft spec
        content = textwrap.dedent("""\
            ---
            name: Normal Spec
            id: spec-norm0001
            description: null
            dependencies: null
            priority: null
            complexity: null
            status: draft
            tags: []
            scope:
              in: null
              out: null
            feature_root_id: null
            ---
            # Normal Spec
            """)
        _write_spec(self.tmp, "normal-spec.md", content)
        # Also create a capability file in specs/current/
        _write_current_capability(
            self.tmp,
            "some-capability",
            "# some-capability\n\n## Requirement: Cap req\n<!-- source: spec-x -->\nText.\n",
        )

    def test_spec_list_excludes_current_dir(self):
        r = _run(["list"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("some-capability", r.stdout)
        self.assertIn("spec-norm0001", r.stdout)


# ---------------------------------------------------------------------------
# set status current: reserved name error
# ---------------------------------------------------------------------------


class TestSetStatusCurrentReserved(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _make_spec_dir(self.tmp)
        content = textwrap.dedent("""\
            ---
            name: Reserved Test Spec
            id: spec-res00001
            description: null
            dependencies: null
            priority: null
            complexity: null
            status: draft
            tags: []
            scope:
              in: null
              out: null
            feature_root_id: null
            ---
            # Reserved Test Spec
            """)
        _write_spec(self.tmp, "reserved-test.md", content)

    def test_set_status_current_exits_1(self):
        r = _run(["set", "status", "current", "reserved-test"], self.tmp)
        self.assertEqual(r.returncode, 1)
        self.assertIn("reserved", r.stderr)

    def test_set_status_current_does_not_modify_file(self):
        spec_path = os.path.join(self.tmp, "specs", "drafts", "reserved-test.md")
        before = Path(spec_path).read_bytes()
        _run(["set", "status", "current", "reserved-test"], self.tmp)
        after = Path(spec_path).read_bytes()
        self.assertEqual(before, after, "file must not be modified on reserved-name error")

    def test_set_status_draft_still_works(self):
        # Move to planned first, then back to draft
        _run(["set", "status", "planned", "reserved-test"], self.tmp)
        r = _run(["set", "status", "draft", "reserved-test"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_set_status_planned_still_works(self):
        r = _run(["set", "status", "planned", "reserved-test"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_set_status_done_still_works(self):
        r = _run(["set", "status", "done", "reserved-test"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)


# ---------------------------------------------------------------------------
# create: idempotent makedirs and body template
# ---------------------------------------------------------------------------


class TestCreateCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # Create specs/ but leave subdirs absent — create will make them
        os.makedirs(os.path.join(self.tmp, "specs"))

    def test_create_works_when_lifecycle_dirs_already_exist(self):
        # Create dirs first so makedirs must be idempotent
        for sub in ("drafts", "planned", "done"):
            os.makedirs(os.path.join(self.tmp, "specs", sub), exist_ok=True)
        r = _run(["create", "Test Spec"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_create_body_contains_requirements_section(self):
        os.makedirs(os.path.join(self.tmp, "specs", "drafts"), exist_ok=True)
        r = _run(["create", "My Feature"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        drafts_dir = os.path.join(self.tmp, "specs", "drafts")
        files = os.listdir(drafts_dir)
        self.assertEqual(len(files), 1)
        content = Path(drafts_dir, files[0]).read_text(encoding="utf-8")
        self.assertIn("## Requirements", content)

    def test_create_body_section_order(self):
        """Requirements must appear between Changes and Files to Modify."""
        os.makedirs(os.path.join(self.tmp, "specs", "drafts"), exist_ok=True)
        r = _run(["create", "Ordered Spec"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        drafts_dir = os.path.join(self.tmp, "specs", "drafts")
        files = os.listdir(drafts_dir)
        content = Path(drafts_dir, files[0]).read_text(encoding="utf-8")
        idx_changes = content.index("## Changes")
        idx_reqs = content.index("## Requirements")
        idx_files = content.index("## Files to Modify")
        self.assertLess(idx_changes, idx_reqs)
        self.assertLess(idx_reqs, idx_files)

    def test_create_requirements_html_comment_present(self):
        """The HTML comment block with format guidance must be in the template."""
        os.makedirs(os.path.join(self.tmp, "specs", "drafts"), exist_ok=True)
        r = _run(["create", "Comment Spec"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        drafts_dir = os.path.join(self.tmp, "specs", "drafts")
        files = os.listdir(drafts_dir)
        content = Path(drafts_dir, files[0]).read_text(encoding="utf-8")
        for keyword in ("ADDED", "MODIFIED", "REMOVED", "SHALL", "MUST", "Scenario", "WHEN", "THEN", "AND"):
            self.assertIn(keyword, content, f"keyword {keyword!r} missing from template comment")


# ---------------------------------------------------------------------------
# show: --body-only and --full flags
# ---------------------------------------------------------------------------


class TestShowFlags(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _make_spec_dir(self.tmp)
        lines = ["Line " + str(i) for i in range(1, 35)]
        body_text = "\n".join(lines)
        content = (
            "---\n"
            "name: Long Spec\n"
            "id: spec-long0001\n"
            "description: null\n"
            "dependencies: null\n"
            "priority: null\n"
            "complexity: null\n"
            "status: draft\n"
            "tags: []\n"
            "scope:\n"
            "  in: null\n"
            "  out: null\n"
            "feature_root_id: null\n"
            "---\n"
        ) + body_text + "\n"
        _write_spec(self.tmp, "long-spec.md", content)

    def test_body_only_excludes_frontmatter_delimiters(self):
        r = _run(["show", "--body-only", "long-spec"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("---", r.stdout)
        self.assertNotIn("name: Long Spec", r.stdout)

    def test_body_only_shows_full_body(self):
        r = _run(["show", "--body-only", "long-spec"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        # Body has 34 lines; should all be present
        self.assertIn("Line 34", r.stdout)

    def test_default_truncates_at_20_lines(self):
        r = _run(["show", "long-spec"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Line 1", r.stdout)
        self.assertNotIn("Line 34", r.stdout)

    def test_full_shows_all_lines(self):
        r = _run(["show", "--full", "long-spec"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Line 34", r.stdout)

    def test_body_only_on_legacy_spec_hits_legacy_warning(self):
        legacy_content = "# Legacy Spec\n\nNo frontmatter here.\n"
        _write_spec(self.tmp, "legacy-spec.md", legacy_content)
        r = _run(["show", "--body-only", "legacy-spec"], self.tmp)
        # Legacy spec → warning path; body-only doesn't apply cleanly
        # spec.py checks fm is None before checking body_only flag
        self.assertEqual(r.returncode, 0)
        self.assertIn("legacy", r.stdout.lower() + r.stderr.lower())


# ---------------------------------------------------------------------------
# Fenced code blocks are never Requirements structure
# ---------------------------------------------------------------------------


class TestFencedCodeBlocks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _make_spec_dir(self.tmp)

    def _spec(self, body: str) -> str:
        return (
            "---\nname: Fence Spec\nid: spec-fence001\ndescription: null\n"
            "dependencies: null\npriority: null\ncomplexity: null\nstatus: draft\n"
            "tags: []\nscope:\n  in: null\n  out: null\nfeature_root_id: null\n---\n"
            "# Fence Spec\n\n" + body
        )

    def test_requirements_only_inside_fence_is_no_section(self):
        for fence in ("```", "~~~markdown"):
            closer = fence[:3]
            _write_spec(
                self.tmp,
                "fenced.md",
                self._spec(
                    "## Changes\n\nExample:\n\n" + fence + "\n## Requirements\n\n"
                    "### ADDED: bead-lifecycle / x\n### 2. `spec.py validate`\n"
                    "### MODIFIED: missing-cap / y\n" + closer + "\n\n## Acceptance Criteria\n"
                ),
            )
            r = _run(["validate", "fenced"], self.tmp)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("malformed", r.stderr)
            r = _run(["set", "status", "done", "fenced"], self.tmp)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(os.path.exists(os.path.join(self.tmp, "specs", "current")))
            for d in ("done",):
                os.remove(os.path.join(self.tmp, "specs", d, "fenced.md"))

    def test_real_section_followed_by_fenced_example_parses_only_real_entries(self):
        _write_spec(
            self.tmp,
            "real.md",
            self._spec(
                "## Requirements\n\n"
                "### ADDED: bead-lifecycle / Real req\n"
                "The scheduler SHALL do the real thing.\n"
                "#### Scenario: Real\n- **WHEN** a\n- **THEN** b\n"
                "```\n## Something\n### ADDED: x / y\n#### Scenario: fake\n```\n\n"
                "## Acceptance Criteria\n"
            ),
        )
        r = _run(["validate", "real"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = _run(["set", "status", "done", "real"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        cap = Path(self.tmp, "specs", "current", "bead-lifecycle.md").read_text(encoding="utf-8")
        self.assertIn("## Requirement: Real req", cap)
        self.assertIn("#### Scenario: fake", cap)  # fenced body left unchanged
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "specs", "current", "x.md")))


class TestWrappedPlainScalarFrontmatter(unittest.TestCase):
    CONTENT = (
        "---\n"
        "name: Wrapped\n"
        "id: spec-wrap1234\n"
        "description: Rename the package, module, and CLI from old-name/old_mod\n"
        "  to new-name/new_mod\n"
        "dependencies: []\n"
        "priority: low\n"
        "complexity: medium\n"
        "status: drafts\n"
        "tags:\n"
        "- a\n"
        "scope:\n"
        "  in: x\n"
        "  out: y\n"
        "---\n\n# Body\n"
    )
    JOINED = "Rename the package, module, and CLI from old-name/old_mod to new-name/new_mod"

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = self._td.name
        _make_spec_dir(self.tmp)
        self.path = _write_spec(self.tmp, "wrapped.md", self.CONTENT)

    def tearDown(self):
        self._td.cleanup()

    def test_parses_to_joined_value(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("spec_under_test", CANONICAL_SPEC_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fm, _ = mod.parse_frontmatter(self.path)
        self.assertEqual(fm["description"], self.JOINED)
        self.assertEqual(fm["tags"], ["a"])
        self.assertEqual(fm["scope"], {"in": "x", "out": "y"})
        self.assertEqual(fm["priority"], "low")

    def test_list_has_no_warning(self):
        r = _run(["list"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("could not parse frontmatter", r.stderr + r.stdout)
        self.assertIn("Wrapped", r.stdout)

    def test_set_priority_preserves_description(self):
        r = _run(["set", "priority", "high", "wrapped"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        text = Path(self.path).read_text(encoding="utf-8")
        self.assertIn(self.JOINED, text)
        self.assertIn("priority: high", text)
        r = _run(["list"], self.tmp)
        self.assertNotIn("could not parse frontmatter", r.stderr)


if __name__ == "__main__":
    unittest.main()
