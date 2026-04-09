from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.models import ExecutionRecord
from agent_takt.prompts import (
    BUILT_IN_AGENT_TYPES,
    build_worker_prompt,
    guardrail_template_path,
    load_guardrail_template,
    render_agent_output_requirements,
    render_context_snippets,
)
from agent_takt.storage import RepositoryStorage

RepositoryStorage._auto_commit = False

from helpers import OrchestratorTests as _OrchestratorBase  # noqa: E402


class PromptsTests(_OrchestratorBase):

    def _make_execution_record(self, index: int) -> ExecutionRecord:
        return ExecutionRecord(
            timestamp=f"2026-01-{index:02d}T00:00:00+00:00",
            event=f"event_{index}",
            agent_type="developer",
            summary=f"Summary {index}",
            details={"index": index},
        )

    def test_worker_prompt_includes_shared_feature_execution_context(self) -> None:
        bead = self.storage.create_bead(title="Implement", agent_type="developer", description="do work")
        prompt = build_worker_prompt(bead, [], self.root)
        self.assertIn('"feature_root_id"', prompt)
        self.assertIn('"execution_branch_name"', prompt)
        self.assertIn("shared feature worktree", prompt)
        self.assertIn("Agent guardrails:", prompt)
        self.assertIn(str(guardrail_template_path("developer", root=self.root)), prompt)
        self.assertIn("Primary responsibility: Implement only the assigned bead", prompt)

    def test_worker_prompt_loads_matching_guardrail_template_for_review(self) -> None:
        bead = self.storage.create_bead(title="Review", agent_type="review", description="inspect changes")
        bead.changed_files = ["src/agent_takt/scheduler.py"]
        prompt = build_worker_prompt(bead, [], self.root)
        self.assertIn(str(guardrail_template_path("review", root=self.root)), prompt)
        self.assertIn("Primary responsibility: Inspect code, tests, docs, and acceptance criteria", prompt)
        self.assertIn("return a blocked result with block_reason and next_agent", prompt)
        self.assertIn("always set `verdict` to `approved` or `needs_changes`", prompt)
        self.assertIn("Always set `findings_count`", prompt)
        self.assertIn("Set `requires_followup` explicitly", prompt)
        self.assertIn('"changed_files"', prompt)

    def test_worker_prompt_requires_structured_verdict_output_for_tester(self) -> None:
        bead = self.storage.create_bead(title="Tester", agent_type="tester", description="run checks")
        prompt = build_worker_prompt(bead, [], self.root)
        self.assertIn("always set `verdict` to `approved` or `needs_changes`", prompt)
        self.assertIn("Always set `findings_count`", prompt)
        self.assertIn("Set `requires_followup` explicitly", prompt)
        self.assertIn("include a concrete `block_reason`", prompt)

    def test_non_review_test_agents_get_baseline_structured_output_requirements(self) -> None:
        requirements = render_agent_output_requirements("developer")
        self.assertIn("always set `verdict` to `approved` or `needs_changes`", requirements)
        self.assertIn("Always set `findings_count`", requirements)
        self.assertIn("Set `requires_followup` explicitly", requirements)
        self.assertIn("Use `approved` when this bead is complete without follow-up", requirements)
        self.assertNotIn("For this agent type, set `findings_count` to the number of unresolved findings", requirements)

    def test_load_guardrail_template_returns_path_and_trimmed_contents_for_each_builtin_agent(self) -> None:
        for agent_type in BUILT_IN_AGENT_TYPES:
            with self.subTest(agent_type=agent_type):
                path, template_text = load_guardrail_template(agent_type, root=self.root)
                self.assertEqual(guardrail_template_path(agent_type, root=self.root), path)
                # Recovery uses "# Recovery Agent Guardrails"; others follow "# {Type} Guardrails"
                capitalized = agent_type.capitalize()
                self.assertTrue(
                    template_text.startswith(f"# {capitalized} Guardrails")
                    or template_text.startswith(f"# {capitalized} Agent Guardrails"),
                    f"Template for {agent_type!r} does not start with expected heading: {template_text[:60]!r}",
                )
                self.assertFalse(template_text.endswith("\n"))

    def test_review_and_tester_templates_require_structured_verdict_fields(self) -> None:
        for agent_type in ("review", "tester"):
            with self.subTest(agent_type=agent_type):
                _, template_text = load_guardrail_template(agent_type, root=self.root)
                self.assertIn("`verdict`, `findings_count`, and `requires_followup`", template_text)

    def test_worker_prompt_references_every_builtin_template_file(self) -> None:
        for agent_type in BUILT_IN_AGENT_TYPES:
            with self.subTest(agent_type=agent_type):
                bead = self.storage.create_bead(title=f"{agent_type} bead", agent_type=agent_type, description="scoped work")
                prompt = build_worker_prompt(bead, [], self.root)
                self.assertIn(f"Template: {guardrail_template_path(agent_type, root=self.root)}", prompt)

    def test_worker_prompt_uses_templates_from_provided_root(self) -> None:
        alt_root = self.root / "alt-root"
        for agent_type in BUILT_IN_AGENT_TYPES:
            template_path = alt_root / "templates" / "agents" / f"{agent_type}.md"
            template_path.parent.mkdir(parents=True, exist_ok=True)
            template_path.write_text(f"# {agent_type.capitalize()} Guardrails\n\nRoot marker: alt-root\n", encoding="utf-8")

        bead = self.storage.create_bead(title="Implement", agent_type="developer", description="do work")
        prompt = build_worker_prompt(bead, [], alt_root)
        self.assertIn(f"Template: {guardrail_template_path('developer', root=alt_root)}", prompt)
        self.assertIn("Root marker: alt-root", prompt)

    def test_linked_context_paths_falls_back_to_unique_basename_match(self) -> None:
        context_file = self.root / "simple-claims-plain-command.md"
        context_file.write_text("plain claims spec\n", encoding="utf-8")
        bead = self.storage.create_bead(
            title="Implement plain claims output",
            agent_type="developer",
            description="do work",
            linked_docs=["specs/simple-claims-plain-command.md"],
        )

        context_paths = self.storage.linked_context_paths(bead)

        self.assertIn(context_file.resolve(), [path.resolve() for path in context_paths])

    def test_linked_context_paths_skips_ambiguous_basename_matches(self) -> None:
        first = self.root / "docs" / "simple-claims-plain-command.md"
        second = self.root / "specs" / "simple-claims-plain-command.md"
        first.parent.mkdir(parents=True, exist_ok=True)
        second.parent.mkdir(parents=True, exist_ok=True)
        first.write_text("one\n", encoding="utf-8")
        second.write_text("two\n", encoding="utf-8")
        bead = self.storage.create_bead(
            title="Implement plain claims output",
            agent_type="developer",
            description="do work",
            linked_docs=["missing/simple-claims-plain-command.md"],
        )

        context_paths = self.storage.linked_context_paths(bead)

        resolved_context_paths = [path.resolve() for path in context_paths]
        self.assertNotIn(first.resolve(), resolved_context_paths)
        self.assertNotIn(second.resolve(), resolved_context_paths)

    def test_worker_prompt_raises_clear_error_when_guardrail_template_missing(self) -> None:
        template_path = guardrail_template_path("developer", root=self.root)
        original_text = template_path.read_text(encoding="utf-8")
        template_path.unlink()

        def restore_template() -> None:
            template_path.parent.mkdir(parents=True, exist_ok=True)
            template_path.write_text(original_text, encoding="utf-8")

        self.addCleanup(restore_template)

        bead = self.storage.create_bead(title="Implement", agent_type="developer", description="do work")
        with self.assertRaisesRegex(FileNotFoundError, "Missing guardrail template for built-in agent 'developer'"):
            build_worker_prompt(bead, [], self.root)

    def test_worker_prompt_includes_all_history_when_at_or_below_cap(self) -> None:
        bead = self.storage.create_bead(title="Implement", agent_type="developer", description="do work")
        for i in range(1, 6):
            bead.execution_history.append(self._make_execution_record(i))
        prompt = build_worker_prompt(bead, [], self.root)
        payload = json.loads(prompt.split("Assigned bead:\n")[1].split("\n\nAvailable repository context")[0])
        self.assertEqual(5, len(payload["execution_history"]))
        self.assertEqual("event_1", payload["execution_history"][0]["event"])
        self.assertEqual("event_5", payload["execution_history"][4]["event"])

    def test_worker_prompt_truncates_execution_history_to_last_five(self) -> None:
        bead = self.storage.create_bead(title="Implement", agent_type="developer", description="do work")
        for i in range(1, 9):
            bead.execution_history.append(self._make_execution_record(i))
        prompt = build_worker_prompt(bead, [], self.root)
        payload = json.loads(prompt.split("Assigned bead:\n")[1].split("\n\nAvailable repository context")[0])
        self.assertEqual(5, len(payload["execution_history"]))
        events = [e["event"] for e in payload["execution_history"]]
        self.assertEqual(["event_4", "event_5", "event_6", "event_7", "event_8"], events)

    def test_worker_prompt_omits_early_history_entries_when_truncated(self) -> None:
        bead = self.storage.create_bead(title="Implement", agent_type="developer", description="do work")
        for i in range(1, 9):
            bead.execution_history.append(self._make_execution_record(i))
        prompt = build_worker_prompt(bead, [], self.root)
        payload = json.loads(prompt.split("Assigned bead:\n")[1].split("\n\nAvailable repository context")[0])
        early_events = {e["event"] for e in payload["execution_history"]}
        for omitted in ["event_1", "event_2", "event_3"]:
            self.assertNotIn(omitted, early_events)

    def test_worker_prompt_single_history_entry_included_verbatim(self) -> None:
        bead = self.storage.create_bead(title="Implement", agent_type="developer", description="do work")
        # create_bead adds one "created" record; verify it is passed through unchanged
        self.assertEqual(1, len(bead.execution_history))
        prompt = build_worker_prompt(bead, [], self.root)
        payload = json.loads(prompt.split("Assigned bead:\n")[1].split("\n\nAvailable repository context")[0])
        self.assertEqual(1, len(payload["execution_history"]))
        self.assertEqual("created", payload["execution_history"][0]["event"])

    def test_render_agent_output_requirements_investigator_mentions_investigation_fields(self) -> None:
        requirements = render_agent_output_requirements("investigator")
        for field in ("findings", "recommendations", "risk_areas", "report_path"):
            self.assertIn(field, requirements)
        # The requirements should explicitly tell the agent NOT to set verdict
        self.assertIn("Do not include `verdict`", requirements)
        # The standard "always set `verdict`" instruction must not appear for investigator
        self.assertNotIn("always set `verdict`", requirements)

    def test_render_context_snippets_handles_paths_outside_worktree_root(self) -> None:
        repo_file = self.root / "specs" / "example.md"
        repo_file.parent.mkdir(parents=True, exist_ok=True)
        repo_file.write_text("spec\n", encoding="utf-8")
        worktree_root = self.root / ".takt" / "worktrees" / "B0002"
        worktree_root.mkdir(parents=True, exist_ok=True)
        rendered = render_context_snippets([repo_file], worktree_root)
        self.assertIn("example.md", rendered)


if __name__ == "__main__":
    unittest.main()
