"""Unit tests for agent_takt.spec_requirements (scenario parsing and prefix matching)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.spec_requirements import (
    criterion_matches_prefix,
    normalize_delimiters,
    parse_requirement_scenarios,
    reserved_delimiter_names,
    scenario_prefix,
)


SPEC = """# Spec

## Objective

text

## Requirements

### ADDED: auth / Login

#### Scenario: Valid login
- **WHEN** user logs in
- **THEN** session created

#### Scenario: Bad password
- **WHEN** wrong password
- **THEN** rejected

### MODIFIED: auth / Logout

#### Scenario: Logout clears session
- **WHEN** logout
- **THEN** cleared

### REMOVED: auth / Legacy

#### Scenario: Old behaviour
- **WHEN** x
- **THEN** y

## Acceptance Criteria

#### Scenario: Outside requirements
"""


class ParseRequirementScenariosTests(unittest.TestCase):
    def test_added_and_modified_returned_as_tuples(self) -> None:
        self.assertEqual(
            [
                ("auth", "Login", "Valid login"),
                ("auth", "Login", "Bad password"),
                ("auth", "Logout", "Logout clears session"),
            ],
            parse_requirement_scenarios(SPEC),
        )

    def test_removed_entries_ignored(self) -> None:
        names = [s for _, _, s in parse_requirement_scenarios(SPEC)]
        self.assertNotIn("Old behaviour", names)

    def test_no_requirements_section_returns_empty(self) -> None:
        self.assertEqual([], parse_requirement_scenarios("# Spec\n\n## Objective\n\nx\n"))

    def test_parsing_stops_at_next_h2_heading(self) -> None:
        names = [s for _, _, s in parse_requirement_scenarios(SPEC)]
        self.assertNotIn("Outside requirements", names)

    def test_scenarios_in_fenced_blocks_ignored(self) -> None:
        text = (
            "## Requirements\n\n### ADDED: cap / Req\n\n"
            "```\n#### Scenario: Fenced\n```\n\n"
            "~~~\n#### Scenario: Tilde fenced\n~~~\n\n"
            "#### Scenario: Real\n"
        )
        self.assertEqual([("cap", "Req", "Real")], parse_requirement_scenarios(text))

    def test_requirements_heading_inside_fence_ignored(self) -> None:
        text = "```\n## Requirements\n### ADDED: c / R\n#### Scenario: S\n```\n"
        self.assertEqual([], parse_requirement_scenarios(text))

    def test_empty_scenario_names_skipped(self) -> None:
        text = "## Requirements\n\n### ADDED: c / R\n\n#### Scenario:\n#### Scenario:   \n#### Scenario: Named\n"
        self.assertEqual([("c", "R", "Named")], parse_requirement_scenarios(text))

    def test_scenario_before_any_requirement_heading_ignored(self) -> None:
        text = "## Requirements\n\n#### Scenario: Orphan\n\n### ADDED: c / R\n\n#### Scenario: Kept\n"
        self.assertEqual([("c", "R", "Kept")], parse_requirement_scenarios(text))

    def test_scenario_after_removed_heading_not_attributed_to_previous_requirement(self) -> None:
        text = (
            "## Requirements\n\n### ADDED: c / A\n\n#### Scenario: One\n\n"
            "### REMOVED: c / B\n\n#### Scenario: Two\n"
        )
        self.assertEqual([("c", "A", "One")], parse_requirement_scenarios(text))


class ReservedDelimiterTests(unittest.TestCase):
    def test_hyphen_in_scenario_name_reported(self) -> None:
        scenarios = [("a", "b", "Login - admin")]
        self.assertEqual(["Scenario: a / b / Login - admin"], reserved_delimiter_names(scenarios))

    def test_reserved_names_in_capability_or_requirement_reported(self) -> None:
        scenarios = [("a - x", "b", "ok"), ("a", "b -- y", "ok"), ("a", "b", "fine")]
        self.assertEqual(
            ["Scenario: a - x / b / ok", "Scenario: a / b -- y / ok"],
            reserved_delimiter_names(scenarios),
        )

    def test_em_dash_with_spaces_reported(self) -> None:
        self.assertEqual(1, len(reserved_delimiter_names([("a", "b", "x — y")])))

    def test_plain_hyphenated_words_not_reported(self) -> None:
        self.assertEqual([], reserved_delimiter_names([("a-b", "re-try", "log-in")]))


class PrefixMatchingTests(unittest.TestCase):
    """Plan-time check uses criterion_matches_prefix directly (see misc._check_scenario_coverage)."""

    PREFIX = "Scenario: a / b / Login"

    def test_scenario_prefix_format(self) -> None:
        self.assertEqual(self.PREFIX, scenario_prefix("a", "b", "Login"))

    def test_exact_match(self) -> None:
        self.assertTrue(criterion_matches_prefix("Scenario: a / b / Login", self.PREFIX))

    def test_match_with_em_dash_suffix(self) -> None:
        self.assertTrue(criterion_matches_prefix("Scenario: a / b / Login — WHEN x THEN y", self.PREFIX))

    def test_longer_scenario_name_does_not_match(self) -> None:
        self.assertFalse(
            criterion_matches_prefix("Scenario: a / b / Login fails — WHEN x THEN y", self.PREFIX)
        )

    def test_hyphen_delimiter_normalised(self) -> None:
        self.assertTrue(criterion_matches_prefix("Scenario: a / b / Login - WHEN x THEN y", self.PREFIX))

    def test_double_hyphen_delimiter_normalised(self) -> None:
        self.assertTrue(criterion_matches_prefix("Scenario: a / b / Login -- WHEN x", self.PREFIX))

    def test_surrounding_whitespace_ignored(self) -> None:
        self.assertTrue(criterion_matches_prefix("  Scenario: a / b / Login — WHEN x  ", self.PREFIX))

    def test_different_capability_does_not_match(self) -> None:
        self.assertFalse(criterion_matches_prefix("Scenario: z / b / Login — WHEN x", self.PREFIX))

    def test_prefix_without_delimiter_boundary_does_not_match(self) -> None:
        self.assertFalse(criterion_matches_prefix("Scenario: a / b / Login—WHEN x", self.PREFIX))

    def test_normalize_delimiters(self) -> None:
        self.assertEqual("a — b — c", normalize_delimiters("a - b -- c"))


if __name__ == "__main__":
    unittest.main()
