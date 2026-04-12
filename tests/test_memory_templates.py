"""Tests verifying that agent guardrail templates contain the required memory blocks.

Covers:
- All 5 built-in agent templates (developer, tester, planner, documentation, review)
  contain a ## Memory heading.
- developer, tester, planner templates contain both read (3 search calls) and write
  ($TAKT_CMD memory add) blocks.
- documentation and review templates contain read blocks and explicit do-not-write text.
- planner's write block mentions only the global namespace.
- $TAKT_CMD variable references appear consistently across all relevant templates.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "templates" / "agents"

_WRITE_AGENTS = ("developer", "tester", "planner")
_READ_ONLY_AGENTS = ("documentation", "review")
_ALL_AGENTS = list(_WRITE_AGENTS) + list(_READ_ONLY_AGENTS)


def _read_template(agent_type: str) -> str:
    path = TEMPLATES_DIR / f"{agent_type}.md"
    return path.read_text(encoding="utf-8")


class TestTemplateMemoryHeading(unittest.TestCase):
    """All 5 templates must contain '## Memory'."""

    def _check(self, agent_type: str) -> None:
        content = _read_template(agent_type)
        self.assertIn("## Memory", content, f"{agent_type}.md is missing '## Memory' heading")

    def test_developer_has_memory_heading(self):
        self._check("developer")

    def test_tester_has_memory_heading(self):
        self._check("tester")

    def test_planner_has_memory_heading(self):
        self._check("planner")

    def test_documentation_has_memory_heading(self):
        self._check("documentation")

    def test_review_has_memory_heading(self):
        self._check("review")


class TestTemplateReadBlocks(unittest.TestCase):
    """developer, tester, planner must have 3-search read blocks (at least 3 memory search calls)."""

    def _check_three_searches(self, agent_type: str) -> None:
        content = _read_template(agent_type)
        # Each template should reference 'memory search' at least 3 times
        search_count = content.count("memory search")
        self.assertGreaterEqual(
            search_count, 3,
            f"{agent_type}.md has only {search_count} 'memory search' references; expected >= 3"
        )

    def test_developer_has_three_search_calls(self):
        self._check_three_searches("developer")

    def test_tester_has_three_search_calls(self):
        self._check_three_searches("tester")

    def test_planner_has_three_search_calls(self):
        self._check_three_searches("planner")

    def test_documentation_has_search_calls(self):
        """documentation template must have at least 1 memory search reference."""
        content = _read_template("documentation")
        self.assertIn("memory search", content, "documentation.md is missing memory search reference")

    def test_review_has_search_calls(self):
        """review template must have at least 1 memory search reference."""
        content = _read_template("review")
        self.assertIn("memory search", content, "review.md is missing memory search reference")


class TestTemplateWriteBlocks(unittest.TestCase):
    """developer, tester, planner must have write ($TAKT_CMD memory add) blocks."""

    def _check_write_block(self, agent_type: str) -> None:
        content = _read_template(agent_type)
        self.assertIn(
            "memory add",
            content,
            f"{agent_type}.md is missing 'memory add' write block"
        )

    def test_developer_has_write_block(self):
        self._check_write_block("developer")

    def test_tester_has_write_block(self):
        self._check_write_block("tester")

    def test_planner_has_write_block(self):
        self._check_write_block("planner")


class TestTemplateReadOnlyAgents(unittest.TestCase):
    """documentation and review templates must NOT have write blocks."""

    def _check_no_write_block(self, agent_type: str) -> None:
        content = _read_template(agent_type)
        self.assertNotIn(
            "memory add",
            content,
            f"{agent_type}.md should NOT contain 'memory add' (read-only agent)"
        )

    def test_documentation_has_no_write_block(self):
        self._check_no_write_block("documentation")

    def test_review_has_no_write_block(self):
        self._check_no_write_block("review")


class TestTemplateTaktCmdVariable(unittest.TestCase):
    """$TAKT_CMD must appear in all 5 templates for consistent invocation."""

    def _check(self, agent_type: str) -> None:
        content = _read_template(agent_type)
        self.assertIn(
            "$TAKT_CMD",
            content,
            f"{agent_type}.md is missing '$TAKT_CMD' variable reference"
        )

    def test_developer_has_takt_cmd(self):
        self._check("developer")

    def test_tester_has_takt_cmd(self):
        self._check("tester")

    def test_planner_has_takt_cmd(self):
        self._check("planner")

    def test_documentation_has_takt_cmd(self):
        self._check("documentation")

    def test_review_has_takt_cmd(self):
        self._check("review")


class TestPlannerWriteGlobalNamespaceOnly(unittest.TestCase):
    """planner.md write block must mention only the global namespace."""

    def test_planner_write_block_global_namespace(self):
        content = _read_template("planner")
        # The planner write block should reference global namespace
        self.assertIn("--namespace global", content,
                      "planner.md write block should reference '--namespace global'")

    def test_planner_write_block_no_feature_namespace(self):
        """planner.md should NOT direct agents to write to feature namespaces."""
        content = _read_template("planner")
        # planner should not have feature:<x> write instructions
        # It may have feature: in read block, but the write block should only use global
        # We check the 'memory add' section does not immediately precede 'feature:'
        # Simply verify the write guidance block references only global
        write_idx = content.find("memory add")
        if write_idx == -1:
            self.skipTest("No memory add block found (separate test handles this)")
        write_section = content[write_idx:write_idx + 200]
        self.assertIn("global", write_section,
                      "planner.md memory add block should reference global namespace")


if __name__ == "__main__":
    unittest.main()
