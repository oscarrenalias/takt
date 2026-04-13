from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from ...console import ConsoleReporter
from ...onboarding.scaffold import commit_scaffold


def command_init(args: argparse.Namespace, console: ConsoleReporter) -> int:
    from ...onboarding import InitAnswers, collect_init_answers, scaffold_project

    root = Path(args.root or ".").resolve()

    if not (root / ".git").exists():
        console.error(f"{root} is not a git repository. Run `git init` first.")
        return 1

    console.section("=== takt init ===")

    if getattr(args, "non_interactive", False):
        from ...onboarding import STACKS
        _lang, _test_cmd, _build_cmd = STACKS[0]
        answers = InitAnswers(
            runner="claude",
            max_workers=1,
            language=_lang,
            test_command=_test_cmd,
            build_check_command=_build_cmd,
        )
    else:
        answers = collect_init_answers()

    _RUNNER_INSTALL_HINTS: dict[str, str] = {
        "claude": "npm install -g @anthropic-ai/claude-code",
        "codex": "npm install -g @openai/codex",
    }
    binary = answers.runner
    if shutil.which(binary) is None:
        hint = _RUNNER_INSTALL_HINTS.get(binary, f"install the '{binary}' CLI tool")
        console.error(
            f"Runner binary '{binary}' not found in PATH.\n"
            f"Install it with: {hint}\n"
            f"Then re-run `takt init`."
        )
        return 1

    scaffold_project(root, answers, overwrite=getattr(args, "overwrite", False), console=console)
    return 0


def command_upgrade(args: argparse.Namespace, console: ConsoleReporter) -> int:
    """Upgrade takt-managed assets to the current bundled version.

    Reads ``.takt/assets-manifest.json``, compares each tracked file against
    the bundled catalog, and applies the upgrade decision table.  When
    ``--dry-run`` is set, the full plan is printed but no files are written.

    Decision table:

    * ``user_owned`` → skip, print ``[skipped — user-owned]``
    * new in bundle, absent from manifest → install, print ``[new]``
    * unmodified + bundle has newer version → overwrite, print ``[updated]``
    * unmodified + bundle matches disk → skip silently (``[up-to-date]`` in dry-run)
    * disk differs from manifest → skip, print ``[skipped — locally modified]``
    * missing from disk, still in bundle → restore, print ``[restored]``
    * in manifest, removed from bundle → rename to ``.disabled``, print
      ``[disabled — removed from bundle]``
    * on disk under bundled prefix, not in manifest or bundle → record in
      manifest as ``source: user``, ``user_owned: true``, print ``[tracked]``
    """
    import fnmatch

    from ...onboarding import (
        AssetDecision,
        _compute_bundled_catalog,
        _sha256_file,
        evaluate_upgrade_actions,
        read_assets_manifest,
        write_assets_manifest,
    )
    from ...console import BOLD, CYAN, DIM, GREEN, RED, RESET, YELLOW

    root = Path(args.root or ".").resolve()
    dry_run: bool = getattr(args, "dry_run", False)

    if dry_run:
        console.section("=== takt upgrade --dry-run ===")
    else:
        console.section("=== takt upgrade ===")

    manifest = read_assets_manifest(root)
    decisions = evaluate_upgrade_actions(root, manifest)

    # Tallies for the end summary.
    counts: dict[str, int] = {
        "updated": 0,
        "new": 0,
        "restored": 0,
        "skipped_modified": 0,
        "skipped_user_owned": 0,
        "disabled": 0,
        "tracked": 0,
        "unchanged": 0,
    }
    modified_paths: list[str] = []

    # Work on a copy of the manifest assets so we can mutate it.
    updated_assets: dict[str, dict] = dict(manifest.get("assets", {}))

    bundled_catalog = _compute_bundled_catalog()

    for decision in sorted(decisions, key=lambda d: d.rel_path):
        rp = decision.rel_path
        action = decision.action

        if action == "new":
            counts["new"] += 1
            bundled_abs = bundled_catalog[rp]
            new_sha = decision.bundled_sha or _sha256_file(bundled_abs)
            if dry_run:
                console.emit(f"  {console._c(GREEN)}[new]{console._c(RESET)}        {rp}")
            else:
                dest = root / rp
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(bundled_abs), str(dest))
                updated_assets[rp] = {"sha256": new_sha, "source": "bundled", "user_owned": False}
                console.emit(f"  {console._c(GREEN)}[new]{console._c(RESET)}        {rp}")

        elif action == "update":
            counts["updated"] += 1
            bundled_abs = bundled_catalog[rp]
            new_sha = decision.bundled_sha or _sha256_file(bundled_abs)
            if dry_run:
                console.emit(f"  {console._c(CYAN)}[updated]{console._c(RESET)}    {rp}")
            else:
                dest = root / rp
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(bundled_abs), str(dest))
                updated_assets[rp]["sha256"] = new_sha
                console.emit(f"  {console._c(CYAN)}[updated]{console._c(RESET)}    {rp}")

        elif action == "unchanged":
            counts["unchanged"] += 1
            if dry_run:
                console.emit(f"  {console._c(DIM)}[up-to-date]{console._c(RESET)}  {rp}")
            # In non-dry-run mode, unchanged files are silently skipped.

        elif action == "restored":
            counts["restored"] += 1
            bundled_abs = bundled_catalog[rp]
            new_sha = decision.bundled_sha or _sha256_file(bundled_abs)
            if dry_run:
                console.emit(f"  {console._c(YELLOW)}[restored]{console._c(RESET)}   {rp}")
            else:
                dest = root / rp
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(bundled_abs), str(dest))
                updated_assets[rp]["sha256"] = new_sha
                console.emit(f"  {console._c(YELLOW)}[restored]{console._c(RESET)}   {rp}")

        elif action == "skipped_user_owned":
            counts["skipped_user_owned"] += 1
            console.emit(f"  {console._c(DIM)}[skipped — user-owned]{console._c(RESET)}  {rp}")

        elif action == "skipped_modified":
            counts["skipped_modified"] += 1
            modified_paths.append(rp)
            console.emit(f"  {console._c(YELLOW)}[skipped — locally modified]{console._c(RESET)}  {rp}")

        elif action == "disabled":
            counts["disabled"] += 1
            disk_file = root / rp
            disabled_path = disk_file.parent / (disk_file.name + ".disabled")
            if dry_run:
                console.emit(
                    f"  {console._c(DIM)}[disabled — removed from bundle]{console._c(RESET)}  {rp}"
                    f" → {disabled_path.name}"
                )
            else:
                if disk_file.is_file():
                    disk_file.rename(disabled_path)
                # Remove the original key; the .disabled file is user territory.
                updated_assets.pop(rp, None)
                console.emit(
                    f"  {console._c(DIM)}[disabled — removed from bundle]{console._c(RESET)}  {rp}"
                    f" → {disabled_path.name}"
                )

        elif action == "user_added":
            counts["tracked"] += 1
            if dry_run:
                console.emit(f"  {console._c(GREEN)}[tracked — user-owned]{console._c(RESET)}  {rp}")
            else:
                updated_assets[rp] = {
                    "sha256": decision.current_sha or "",
                    "source": "user",
                    "user_owned": True,
                }
                console.emit(f"  {console._c(GREEN)}[tracked — user-owned]{console._c(RESET)}  {rp}")

    # Write updated manifest unless this is a dry run.
    if not dry_run:
        import json as _json
        from importlib.metadata import version as _pkg_version
        from datetime import datetime, timezone

        new_manifest = {
            "takt_version": _pkg_version("agent-takt"),
            "installed_at": manifest.get("installed_at", datetime.now(tz=timezone.utc).isoformat()),
            "upgraded_at": datetime.now(tz=timezone.utc).isoformat(),
            "assets": updated_assets,
        }
        manifest_path = root / ".takt" / "assets-manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(_json.dumps(new_manifest, indent=2), encoding="utf-8")

    # Config key merge — insert any new keys from the bundled default config
    # into the user's .takt/config.yaml without overwriting existing values.
    import yaml as _yaml

    from ...onboarding import merge_config_keys
    from ..._assets import packaged_default_config

    config_path = root / ".takt" / "config.yaml"
    added_config_keys: list[str] = []
    if config_path.is_file():
        user_cfg = _yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        bundled_cfg = _yaml.safe_load(packaged_default_config().read_text(encoding="utf-8")) or {}
        merged_cfg, added_config_keys = merge_config_keys(user_cfg, bundled_cfg)
        if added_config_keys:
            console.emit("")
            console.emit(f"{console._c(BOLD)}Config additions:{console._c(RESET)}")
            for key in added_config_keys:
                console.emit(f"  {console._c(GREEN)}[added]{console._c(RESET)}  {key}")
            if not dry_run:
                config_path.write_text(_yaml.dump(merged_cfg, default_flow_style=False), encoding="utf-8")

    # Print summary.
    console.emit("")
    prefix = "[dry-run] " if dry_run else ""
    console.emit(
        f"{console._c(BOLD)}{prefix}Summary:{console._c(RESET)}"
        f"  updated={counts['updated']}"
        f"  new={counts['new']}"
        f"  restored={counts['restored']}"
        f"  disabled={counts['disabled']}"
        f"  tracked={counts['tracked']}"
        f"  skipped(modified)={counts['skipped_modified']}"
        f"  skipped(user-owned)={counts['skipped_user_owned']}"
        f"  config-keys-added={len(added_config_keys)}"
    )

    if modified_paths:
        console.emit("")
        console.emit(
            f"{console._c(YELLOW)}Files skipped due to local modifications"
            f" — review manually:{console._c(RESET)}"
        )
        for path in modified_paths:
            console.emit(f"  {path}")

    if dry_run:
        console.emit(f"  {console._c(DIM)}[dry-run] would commit upgraded assets{console._c(RESET)}")
    else:
        commit_scaffold(root, console)

    return 0
