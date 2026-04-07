"""Tests for config generation, key merge, and template substitution in agent_takt.onboarding.

Covers:
- generate_config_yaml
- substitute_template_placeholders
- merge_config_keys
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.onboarding import (
    InitAnswers,
    generate_config_yaml,
    merge_config_keys,
    substitute_template_placeholders,
)


def _make_answers(**kwargs) -> InitAnswers:
    defaults = dict(
        runner="claude",
        max_workers=2,
        language="Python",
        test_command="pytest",
        build_check_command="python -m py_compile",
    )
    defaults.update(kwargs)
    return InitAnswers(**defaults)


# ---------------------------------------------------------------------------
# generate_config_yaml
# ---------------------------------------------------------------------------


class TestGenerateConfigYaml(unittest.TestCase):
    def test_contains_runner(self):
        answers = _make_answers(runner="codex", test_command="go test ./...", max_workers=3)
        yaml_text = generate_config_yaml(answers)
        self.assertIn("default_runner: codex", yaml_text)
        self.assertIn("test_command: go test ./...", yaml_text)
        # max_workers is a CLI flag, not a config key — it must not appear in the YAML
        self.assertNotIn("max_workers", yaml_text)

    def test_runner_substituted_claude(self):
        yaml_text = generate_config_yaml(_make_answers(runner="claude"))
        self.assertIn("default_runner: claude", yaml_text)

    def test_runner_substituted_codex(self):
        yaml_text = generate_config_yaml(_make_answers(runner="codex"))
        self.assertIn("default_runner: codex", yaml_text)

    def test_test_command_substituted(self):
        yaml_text = generate_config_yaml(_make_answers(test_command="npm test"))
        self.assertIn("test_command: npm test", yaml_text)

    def test_contains_allowed_tools_default(self):
        yaml_text = generate_config_yaml(_make_answers())
        self.assertIn("allowed_tools_default:", yaml_text)
        for tool in ("Edit", "Write", "Read", "Bash", "Glob", "Grep"):
            self.assertIn(f"- {tool}", yaml_text)

    def test_contains_allowed_tools_by_agent(self):
        yaml_text = generate_config_yaml(_make_answers())
        self.assertIn("allowed_tools_by_agent:", yaml_text)
        self.assertIn("developer:", yaml_text)
        self.assertIn("- Agent", yaml_text)
        self.assertIn("- TaskCreate", yaml_text)

    def test_contains_claude_block(self):
        yaml_text = generate_config_yaml(_make_answers())
        self.assertIn("claude:", yaml_text)
        self.assertIn("binary: claude", yaml_text)

    def test_contains_codex_block(self):
        yaml_text = generate_config_yaml(_make_answers())
        self.assertIn("codex:", yaml_text)
        self.assertIn("binary: codex", yaml_text)

    def test_contains_model_default(self):
        yaml_text = generate_config_yaml(_make_answers())
        self.assertIn("model_default: claude-sonnet-4-6", yaml_text)

    def test_contains_timeout_seconds(self):
        yaml_text = generate_config_yaml(_make_answers())
        self.assertIn("timeout_seconds: 900", yaml_text)

    def test_contains_skills_dirs(self):
        yaml_text = generate_config_yaml(_make_answers())
        self.assertIn("skills_dir: .agents", yaml_text)
        self.assertIn("skills_dir: .claude", yaml_text)

    def test_scheduler_preserved(self):
        yaml_text = generate_config_yaml(_make_answers())
        self.assertIn("scheduler:", yaml_text)
        self.assertIn("transient_block_patterns:", yaml_text)
        self.assertIn("lease_timeout_minutes:", yaml_text)

    def test_model_by_agent_preserved(self):
        yaml_text = generate_config_yaml(_make_answers())
        self.assertIn("model_by_agent:", yaml_text)
        self.assertIn("developer: claude-sonnet-4-6", yaml_text)


# ---------------------------------------------------------------------------
# substitute_template_placeholders
# ---------------------------------------------------------------------------


class TestSubstituteTemplatePlaceholders(unittest.TestCase):
    def test_replaces_all_placeholders(self):
        answers = _make_answers(language="Go", test_command="go test ./...", build_check_command="go build ./...")
        text = "lang={{LANGUAGE}} test={{TEST_COMMAND}} build={{BUILD_CHECK_COMMAND}}"
        result = substitute_template_placeholders(text, answers)
        self.assertEqual(result, "lang=Go test=go test ./... build=go build ./...")

    def test_no_placeholders_unchanged(self):
        answers = _make_answers()
        text = "nothing to replace here"
        self.assertEqual(substitute_template_placeholders(text, answers), text)

    def test_multiple_occurrences_replaced(self):
        answers = _make_answers(language="Python")
        text = "{{LANGUAGE}} and {{LANGUAGE}}"
        result = substitute_template_placeholders(text, answers)
        self.assertEqual(result, "Python and Python")


# ---------------------------------------------------------------------------
# merge_config_keys
# ---------------------------------------------------------------------------


class TestMergeConfigKeys(unittest.TestCase):
    """Tests for merge_config_keys() — recursive dict merge that preserves user values."""

    def _merge(self, user: dict, bundled: dict):
        return merge_config_keys(user, bundled)

    def test_top_level_missing_key_inserted(self):
        """A missing top-level key from bundled is inserted into user config."""
        user = {"a": 1}
        bundled = {"a": 99, "b": 2}
        merged, added = self._merge(user, bundled)
        self.assertEqual(merged["b"], 2)
        self.assertIn("b", added)

    def test_existing_top_level_key_not_overwritten(self):
        """An existing top-level user key is never overwritten."""
        user = {"a": 1}
        bundled = {"a": 99}
        merged, added = self._merge(user, bundled)
        self.assertEqual(merged["a"], 1)
        self.assertEqual(added, [])

    def test_recursive_nested_missing_key_added(self):
        """A missing key inside a nested dict is inserted with its full dotted path."""
        user = {"scheduler": {"lease_timeout_minutes": 30}}
        bundled = {"scheduler": {"lease_timeout_minutes": 60, "max_retries": 5}}
        merged, added = self._merge(user, bundled)
        self.assertEqual(merged["scheduler"]["max_retries"], 5)
        self.assertIn("scheduler.max_retries", added)

    def test_recursive_nested_existing_key_not_overwritten(self):
        """An existing nested user key is preserved, not overwritten by bundled value."""
        user = {"scheduler": {"lease_timeout_minutes": 30}}
        bundled = {"scheduler": {"lease_timeout_minutes": 60}}
        merged, added = self._merge(user, bundled)
        self.assertEqual(merged["scheduler"]["lease_timeout_minutes"], 30)
        self.assertEqual(added, [])

    def test_non_dict_values_user_wins(self):
        """When both sides have a non-dict value at the same key, user value wins."""
        user = {"timeout": 100}
        bundled = {"timeout": 900}
        merged, added = self._merge(user, bundled)
        self.assertEqual(merged["timeout"], 100)
        self.assertEqual(added, [])

    def test_empty_user_config_entire_bundled_inserted(self):
        """When user config is empty, every bundled top-level key is inserted.

        Nested dicts are inserted as a whole because they are absent from
        user_config — the function only recurses when *both* sides have a
        dict at the same key. When the user key is absent, the entire bundled
        subtree is stored and only the top-level dotted key is recorded.
        """
        user: dict = {}
        bundled = {"a": 1, "b": {"c": 2}}
        merged, added = self._merge(user, bundled)
        self.assertEqual(merged["a"], 1)
        self.assertEqual(merged["b"]["c"], 2)
        self.assertIn("a", added)
        # "b" is recorded (the key that was absent in user_config), not "b.c"
        self.assertIn("b", added)

    def test_empty_bundled_config_no_keys_added(self):
        """When bundled config is empty, no keys are added and the returned list is empty."""
        user = {"x": 1}
        bundled: dict = {}
        merged, added = self._merge(user, bundled)
        self.assertEqual(merged, {"x": 1})
        self.assertEqual(added, [])

    def test_deeply_nested_merge(self):
        """Merge inserts a missing leaf key at 3+ levels of nesting."""
        user = {"a": {"b": {"c": 1}}}
        bundled = {"a": {"b": {"c": 99, "d": 2}}}
        merged, added = self._merge(user, bundled)
        self.assertEqual(merged["a"]["b"]["c"], 1)  # preserved
        self.assertEqual(merged["a"]["b"]["d"], 2)   # inserted
        self.assertIn("a.b.d", added)


if __name__ == "__main__":
    unittest.main()
