---
name: docs-edit
description: Documentation editing capability for assigned doc updates.
---

# docs-edit

Use this skill for the mechanics of editing documentation safely, concisely, and in a way that stays faithful to the current repository state.

## Edit Preparation

Before changing documentation:

1. Read the exact section you plan to update, not just the heading.
2. Check the nearby code, template, or config that the text describes.
3. Separate required factual updates from optional cleanup so the patch stays bead-scoped.

## Editing Rules

- Preserve factual accuracy over polish. If a sentence is slightly awkward but correct, do not broaden the patch just to rewrite it.
- Keep examples executable-looking and repository-realistic. Match the command forms and file paths already used in the project.
- Reuse existing terminology for agent types, bead fields, config keys, and directory names.
- Update surrounding context when needed to avoid leaving contradictions, but avoid broad copy edits outside the affected topic.
- Do not add promises about validation, guarantees, or workflow steps that the repository does not actually enforce.

## Safe Documentation Strategy

- Prefer small, targeted diffs that make one behavior easier to understand.
- When a table, bullet list, or example embeds the changed behavior, update every affected occurrence in the touched scope.
- Keep backend-specific instructions explicit when Codex and Claude Code differ.
- Maintain documentation-only changes. Do not mix runtime edits into a docs patch.

## Self-Check Before Handoff

Review the patch against these questions:

- Does every changed line reflect something supported by code, templates, or linked docs?
- Did the edit remove or avoid contradictions in the touched section?
- Are command snippets, file paths, and field names spelled exactly as they exist in the repository?
- Is any unresolved gap clearly called out for the next agent instead of being implied away?

## Output Discipline

Record the docs you actually edited and describe the documentation effect concretely. If correct documentation depends on code or test changes that are not present yet, stop and hand off instead of drafting speculative text.
