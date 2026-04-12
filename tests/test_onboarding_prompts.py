"""Tests for _select_from_list and STACKS in agent_takt.onboarding.prompts.

Covers:
- _select_from_list: valid in-range integer → correct 0-based return
- _select_from_list: empty input → default_index returned
- _select_from_list: non-integer input → error written, then valid input accepted
- _select_from_list: integer below 1 or above len(options) → error written, then valid input accepted
- _select_from_list: default_index at non-zero position
- STACKS: Python at index 0, Other at index -1 with empty test/build command strings
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.onboarding.prompts import STACKS, _select_from_list


class TestSelectFromList(unittest.TestCase):
    """Direct unit tests for _select_from_list."""

    OPTIONS = ["alpha", "beta", "gamma"]

    def _run(self, lines: list[str], default_index: int = 0) -> tuple[int, str]:
        """Run _select_from_list with injected streams; return (result, captured_output)."""
        inp = io.StringIO("\n".join(lines) + "\n")
        out = io.StringIO()
        result = _select_from_list(
            "Choose",
            self.OPTIONS,
            default_index=default_index,
            stream_in=inp,
            stream_out=out,
        )
        return result, out.getvalue()

    # ------------------------------------------------------------------
    # Case 1: valid in-range integer → correct 0-based return
    # ------------------------------------------------------------------

    def test_valid_first_choice_returns_zero_based_index(self):
        result, _ = self._run(["1"])
        self.assertEqual(0, result)

    def test_valid_second_choice_returns_one(self):
        result, _ = self._run(["2"])
        self.assertEqual(1, result)

    def test_valid_last_choice_returns_last_index(self):
        result, _ = self._run([str(len(self.OPTIONS))])
        self.assertEqual(len(self.OPTIONS) - 1, result)

    # ------------------------------------------------------------------
    # Case 2: empty input → default_index returned
    # ------------------------------------------------------------------

    def test_empty_input_returns_default_index_zero(self):
        result, _ = self._run([""])
        self.assertEqual(0, result)

    def test_empty_input_returns_non_zero_default_index(self):
        result, _ = self._run([""], default_index=2)
        self.assertEqual(2, result)

    # ------------------------------------------------------------------
    # Case 3: non-integer string → error written, then valid input accepted
    # ------------------------------------------------------------------

    def test_non_integer_then_valid_returns_correct_index(self):
        result, output = self._run(["notanumber", "2"])
        self.assertEqual(1, result)

    def test_non_integer_writes_error_message_to_stream(self):
        _, output = self._run(["abc", "1"])
        self.assertIn("'abc' is not a valid number", output)

    def test_non_integer_error_mentions_valid_range(self):
        _, output = self._run(["xyz", "1"])
        self.assertIn(f"1 and {len(self.OPTIONS)}", output)

    # ------------------------------------------------------------------
    # Case 4: out-of-range integer → error written, then valid input accepted
    # ------------------------------------------------------------------

    def test_zero_is_out_of_range_then_valid_accepted(self):
        result, output = self._run(["0", "1"])
        self.assertEqual(0, result)
        self.assertIn("out of range", output)

    def test_too_large_is_out_of_range_then_valid_accepted(self):
        result, output = self._run([str(len(self.OPTIONS) + 1), "1"])
        self.assertEqual(0, result)
        self.assertIn("out of range", output)

    def test_out_of_range_error_mentions_valid_range(self):
        _, output = self._run(["99", "1"])
        self.assertIn(f"1 and {len(self.OPTIONS)}", output)

    # ------------------------------------------------------------------
    # Case 5: default_index at non-zero position
    # ------------------------------------------------------------------

    def test_default_index_at_last_position_accepted_on_empty_input(self):
        last = len(self.OPTIONS) - 1
        result, _ = self._run([""], default_index=last)
        self.assertEqual(last, result)

    def test_default_index_shown_in_prompt(self):
        _, output = self._run(["1"], default_index=1)
        # Prompt should show 1-based default: default_index=1 → displays as [2]
        self.assertIn("[2]", output)


class TestStacksCatalogOtherEntry(unittest.TestCase):
    """Spot-check STACKS catalog: Python at [0], Other at [-1] with empty command strings."""

    def test_stacks_first_entry_is_python(self):
        lang, test_cmd, build_cmd = STACKS[0]
        self.assertEqual("Python", lang)

    def test_stacks_last_entry_name_is_other(self):
        lang, test_cmd, build_cmd = STACKS[-1]
        self.assertEqual("Other", lang)

    def test_stacks_other_entry_test_command_is_empty(self):
        _, test_cmd, _ = STACKS[-1]
        self.assertEqual("", test_cmd, "Other stack must have an empty test_command")

    def test_stacks_other_entry_build_check_command_is_empty(self):
        _, _, build_cmd = STACKS[-1]
        self.assertEqual("", build_cmd, "Other stack must have an empty build_check_command")


if __name__ == "__main__":
    unittest.main()
