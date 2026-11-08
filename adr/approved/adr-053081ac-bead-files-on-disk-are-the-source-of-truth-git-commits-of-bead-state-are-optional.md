---
id: ADR-053081ac
title: Bead files on disk are the source of truth; git commits of bead state are optional
status: approved
created_at: '2026-11-08T15:47:38.497906+00:00'
authors:
- Renalias, Oscar
description: Bead JSON on disk is authoritative; per-save [bead] commits are controlled
  by common.commit_bead_state.
accepted_at: '2026-11-08T15:52:48.927092+00:00'
superseded_at: null
superseded_by: null
supersedes: []
tags:
- storage
- git
- config
related_specs:
- spec-7fd6960b
- spec-b518b2b6
related_beads: []
review_after: null
---

# Bead files on disk are the source of truth; git commits of bead state are optional

## Summary

> In the context of persisting bead state as JSON under `.takt/beads/`, facing the conflict between wanting bead state in git and a `git log` that per-save commits had flooded (81% of all commits), we decided that the atomically-written bead file on disk is the source of truth and that committing it to git is optional, controlled by `common.commit_bead_state` (`true` when the key is absent, `false` in configs written by `takt init`), to achieve a readable history without losing auditability, accepting that git history is no longer a complete bead audit trail once the flag is off.

## Context

Bead state was first committed only when something forced it, such as a manual commit or a merge failing on dirty state. `specs/done/auto-commit-bead-state-on-every-save.md` fixed the merge preflight failures ("your local changes would be overwritten") and made bead history visible in git. It did this by calling `_git_commit_bead()` from `RepositoryStorage._write_bead()`, the single place every bead write goes through. The commit was best-effort from the start: bead writes must never fail because of git.

That produced a `[bead] B-xxx: <status>` commit for every status change and every appended `execution_history` event. `specs/done/add-commitbeadstate-config-toggle.md` measured 5,598 of 6,901 commits (81%) in this repo as `[bead]` noise. Real `[takt]` worker commits were hard to find. The same spec noted that the audit trail was already duplicated in two places: each bead's `execution_history` and the project-wide `.takt/logs/events.jsonl` written by `RepositoryStorage.record_event()`.

## Considered Options

### Option A — Disk is authoritative; per-save commits behind a config flag (chosen)

* Good: `_write_bead()` always writes a temp file and `replace()`s it atomically, whether or not a commit follows, so the stored state is always the same.
* Good: New projects get a quiet `git log`, and existing repos see no change until an operator edits their config.
* Good: `execution_history` and `events.jsonl` keep a full audit trail without git.
* Bad: Two supported modes to test and document.
* Bad: With the flag off, bead state is only as durable as the working directory unless the operator backs it up.

### Option B — Always commit bead state on every save

* Good: Every transition is recoverable from git, and the main checkout is never dirty with bead JSON.
* Bad: Most of the history becomes bookkeeping commits.
* Bad: Duplicates `execution_history` and `events.jsonl` without adding an operator workflow they don't already support.

### Option C — Never commit bead state; remove the code path

* Good: Simplest.
* Bad: Changes behaviour for existing repos that depend on bead state in git (self-hosting history, merge preflight on a clean tree).
* Bad: No opt-out for teams who want bead state versioned.

### Option D — Automatic migration (untrack and rewrite history) on upgrade

* Good: Existing repos would get the quiet behaviour too.
* Bad: Rewriting history and editing `.gitignore` without asking is destructive. The spec rejected a `takt migrate-beads` command and left migration as an opt-in manual recipe.

## Decision

**The bead JSON file written by `RepositoryStorage._write_bead()` is the source of truth for bead state. Git commits of bead state are an optional mirror.**

* `CommonConfig.commit_bead_state` (`config.py`) defaults to `True` in `default_config()` and when the key is missing from `.takt/config.yaml`. That keeps existing repos unchanged, and `takt upgrade` does not add the key.
* `takt init` writes `commit_bead_state: false` into the `common:` block (`onboarding/config.py`) and adds `.takt/beads/` to `.gitignore` (`update_gitignore(include_bead_state=True)`).
* `make_services()` passes the flag into `RepositoryStorage(root, commit_bead_state=...)`. When it is `False`, `_git_commit_bead()` and `_git_commit_bead_deletion()` return before running any git command. The flag can only suppress commits; it never forces one when the class-level `_auto_commit` test guard is off.
* Commit failures are never fatal. A failed commit appends a `git_commit_failed` execution record and writes the bead file directly.
* The audit trail is `execution_history` plus `.takt/logs/events.jsonl`. Agents and operators read bead state through the CLI, not from git history.

## Consequences

### Positive

* New projects have a `git log` with only real work in it.
* Bead persistence does not depend on git working.
* Existing repos decide for themselves when to switch, using the documented recipe (flag off, ignore `.takt/beads/`, `git rm -r --cached`).

### Negative

* With the flag off, `git log` no longer shows bead lifecycle history, and operators must use `takt bead history` or `events.jsonl`.
* The quiet-mode recipe puts `.takt/beads/` in `.gitignore`, which exposed a latent staging bug in `commit_all()` that later had to be fixed. Code that stages files must handle both modes.
* Removing old `[bead]` commits from an existing repo's history needs `git filter-repo` or a rebase, which takt does not do.
