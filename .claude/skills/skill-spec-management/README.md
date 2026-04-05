# skill-spec-management

An opinionated spec management skill for spec-driven development workflows. It provides a CLI tool (`spec.py`) and a Claude skill that enforce a structured lifecycle for feature specs — from initial draft through planning to completion.

Specs are Markdown files with YAML frontmatter. They live in a three-stage folder hierarchy (`drafts/` → `planned/` → `done/`) and are the single source of truth for what is being built, why, and when it's done. It also possible to use custom status values, e.g., "blocked", "postponed", the CLI will deal with that just fine but the skill enforces the default workflow.

## CLI Reference

Run `spec.py` from the project root (where `specs/` lives):

```bash
python3 spec.py <subcommand> [args]
```

| Subcommand | Description |
|---|---|
| `init` | Create the `specs/drafts/`, `specs/planned/`, `specs/done/` folder structure |
| `create <title>` | Create a new spec in `specs/drafts/` with a generated ID and standard template |
| `list` | List all specs with ID, status, priority, complexity, and name |
| `list --status <s>` | Filter by status (`draft`, `planned`, `done`) |
| `list --tag <tag>` | Filter by tag |
| `list --priority <p>` | Filter by priority (`high`, `medium`, `low`) |
| `show <spec>` | Print frontmatter and first 20 lines of body |
| `show --full <spec>` | Print frontmatter and complete body |
| `set status <value> <spec>` | Transition status and move file to the matching folder |
| `set priority <value> <spec>` | Set `priority` field (`high`, `medium`, `low`) |
| `set tags <tag1,tag2> <spec>` | Replace the `tags` list |
| `set description <text> <spec>` | Set the `description` field |
| `set feature-root <id> <spec>` | Link spec to a feature root bead ID |
| `migrate <spec>` | Add frontmatter to a legacy spec that has none |

`<spec>` can be a full spec ID (e.g. `spec-a3f19c2b`) or a partial filename substring.

`spec.py` has no external dependencies — it runs with the Python standard library only.

## Onboarding a New Project

### 1. Install the skill

Via APM or by cloning — see [Installation](#installation) below.

### 2. Initialise the specs folder

Run this once from the project root:

```bash
python3 spec.py init
```

This creates:

```
specs/
├── drafts/
├── planned/
└── done/
```

### 3. Create your first spec

```bash
python3 spec.py create "My First Feature"
# Created specs/drafts/my-first-feature.md
# ID: spec-a3f19c2b
```

Open the generated file and fill in the sections: **Objective**, **Problems to Fix**, **Changes**, **Files to Modify**, **Acceptance Criteria**, and **Pending Decisions**.

### 4. Manage the lifecycle

```bash
# List all specs
python3 spec.py list

# Move a spec forward when ready
python3 spec.py set status planned spec-a3f19c2b
python3 spec.py set status done    spec-a3f19c2b
```

### Migrating existing specs

If you have existing Markdown specs without frontmatter, migrate them in place:

```bash
python3 spec.py migrate my-old-spec
```

This adds a generated ID, infers the name from the first heading or filename, and sets the status from the file's current folder.

## Installation

### Via APM

```bash
apm install oscarrenalias/skill-spec-management
```

APM will copy the skill into every detected target directory (`.github/`, `.claude/`, `.cursor/`, etc.).

### As a Native Claude Skill

Copy `SKILL.md` and `spec.py` into your project's skills folder:

```bash
mkdir -p .claude/skills/spec-management
cp SKILL.md spec.py .claude/skills/spec-management/
```

Or clone the repo and reference it directly:

```bash
git clone https://github.com/oscarrenalias/skill-spec-management .claude/skills/spec-management
```

Claude will pick up the skill automatically on the next session.
