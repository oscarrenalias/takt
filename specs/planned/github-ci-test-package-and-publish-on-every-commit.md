---
name: 'GitHub CI: test, package, and publish on every commit'
id: spec-dd4b17af
description: GitHub Actions workflow that runs tests, builds, and publishes a release
  on every push to main
dependencies: null
priority: medium
complexity: low
status: planned
tags: []
scope:
  in: null
  out: null
feature_root_id: null
---
# GitHub CI: Test, Package, and Publish on Every Commit

## Objective

Every push to `main` should automatically run the test suite, build the Python package, and publish a versioned GitHub release with the built artifacts. This allows installing the latest orchestrator via a stable release URL without manual build steps. The patch version is bumped automatically on every successful build using `uv version --bump patch`; major and minor bumps are done manually by the developer before pushing.

---

## Problems to Fix

1. **No CI** — there is no `.github/workflows/` directory. Tests only run locally or via `orchestrator merge`.
2. **No automated releases** — publishing requires manual `uv build` + `gh release create` steps.
3. **No running version number** — `pyproject.toml` has a static `0.1.0` that is never updated automatically.

---

## Changes

### 1. Version scheme

The CI workflow bumps the patch version on every successful build using `uv version --bump patch`, which increments the patch component in `pyproject.toml` and writes it back. The bumped version is committed to `main` with `[skip ci]` in the commit message to prevent re-triggering the workflow.

```bash
uv version --bump patch          # e.g. 0.1.3 → 0.1.4, writes pyproject.toml
VERSION=$(uv version --short)    # e.g. "0.1.4"
git commit -am "chore: bump version to $VERSION [skip ci]"
git push
```

To bump major or minor, the developer runs `uv version --bump minor` or `uv version --bump major` locally and pushes. The CI will continue bumping patch from the new base on subsequent builds.

### 2. Workflow: `.github/workflows/ci.yml`

Triggered on every push to `main`. Three jobs run in order:

#### Job 1: `test`

```yaml
- Check out repo
- Set up Python 3.12
- Install uv
- Install dependencies: uv sync --all-extras
- Run tests: uv run pytest tests/ -n auto -q
```

Fails the pipeline if any test fails.

#### Job 2: `build`

Depends on `test`. Computes the version and builds the package:

```yaml
- Check out repo
- Set up Python 3.12 + uv
- Install dependencies: uv sync
- Configure git identity for the version bump commit
- Bump patch version: uv version --bump patch
- Read bumped version: VERSION=$(uv version --short)
- Commit and push: git commit -am "chore: bump version to $VERSION [skip ci]" && git push
- Run: uv build
- Upload dist/ as a build artifact named `dist` with VERSION as an output
```

The patched `pyproject.toml` is not committed — modified only in the runner's working copy.

#### Job 3: `publish`

Depends on `build`. Creates a GitHub release and uploads the built artifacts:

```yaml
- Download `dist` artifact
- Check out repo
- Install uv
- Read version: VERSION=$(uv version --short)
- Create git tag: git tag v$VERSION && git push origin v$VERSION
- Create release: gh release create v$VERSION dist/* --title "v$VERSION" --notes "Automated release from commit ${{ github.sha }}"
```

Uses the built-in `GITHUB_TOKEN` — no manually configured secrets required. The `publish` job sets `permissions: contents: write` explicitly; other jobs have no elevated permissions.

---

## Files to Modify

| File | Change |
|---|---|
| `.github/workflows/ci.yml` | New file — CI workflow |

No changes to `src/` or tests. `pyproject.toml` is updated by the CI via `uv version --bump patch` and committed back to main on each build.

---

## Testing Notes

No automated tests are required for this spec. The deliverable is a single YAML workflow file with no production code changes. Validation happens when the workflow runs on the first push to main after merging. The review agent should verify the workflow YAML is structurally correct and matches the spec.

---

## Acceptance Criteria

- Pushing a commit to `main` triggers the workflow
- `test` job runs `uv run pytest tests/ -n auto -q` in parallel and fails the pipeline on test failure
- `build` job bumps the patch version in `pyproject.toml` via `uv version --bump patch`, commits it back to main with `[skip ci]`, then builds the package
- Built artifacts are named `codex_agent_orchestration-<version>-*.whl` and `.tar.gz` reflecting the bumped version
- `publish` job creates a git tag `v<version>` and a GitHub release with both artifacts attached
- A second push creates a new release with patch+1 — no version collision
- Running `uv version --bump minor` locally and pushing causes subsequent CI builds to bump patch from the new minor base
- Workflow requires no manually configured secrets beyond the built-in `GITHUB_TOKEN`

---

## Pending Decisions

### 1. ~~Workflow permissions~~
~~`gh release create` requires the workflow to have `contents: write` permission. This should be set explicitly at the job or workflow level.~~ **Resolved: set `permissions: contents: write` on the `publish` job only.**

### 2. ~~Skip publish on test failure~~
~~If `test` fails, `build` and `publish` should not run.~~ **Resolved: handled automatically by the `needs:` dependency chain — no extra configuration needed.**
