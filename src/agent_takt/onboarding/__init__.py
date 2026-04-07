"""Onboarding package for the ``takt init`` and ``takt upgrade`` commands.

Public API is re-exported here so callers can import from ``agent_takt.onboarding``
without knowing the internal module layout.
"""

from .assets import (
    copy_asset_dir,
    copy_asset_file,
    install_agents_skills,
    install_claude_skills,
    install_default_config,
    install_templates,
    resolve_memory_seed,
)
from .config import (
    generate_config_yaml,
    install_templates_with_substitution,
    merge_config_keys,
    substitute_template_placeholders,
)
from .prompts import InitAnswers, _prompt, collect_init_answers
from .scaffold import (
    _CONVENTIONS_CONTENT,
    _GITIGNORE_ENTRIES,
    _KNOWN_ISSUES_CONTENT,
    _SPECS_HOWTO_CONTENT,
    _language_specific_known_issues,
    commit_scaffold,
    create_specs_howto,
    scaffold_project,
    seed_memory_files,
    update_gitignore,
)
from .upgrade import (
    _MANIFEST_FILENAME,
    _compute_bundled_catalog,
    _sha256_file,
    AssetActionType,
    AssetDecision,
    evaluate_upgrade_actions,
    read_assets_manifest,
    write_assets_manifest,
)

__all__ = [
    # assets
    "copy_asset_dir",
    "copy_asset_file",
    "install_agents_skills",
    "install_claude_skills",
    "install_default_config",
    "install_templates",
    "resolve_memory_seed",
    # config
    "generate_config_yaml",
    "install_templates_with_substitution",
    "merge_config_keys",
    "substitute_template_placeholders",
    # prompts
    "InitAnswers",
    "_prompt",
    "collect_init_answers",
    # scaffold
    "_CONVENTIONS_CONTENT",
    "_GITIGNORE_ENTRIES",
    "_KNOWN_ISSUES_CONTENT",
    "_SPECS_HOWTO_CONTENT",
    "_language_specific_known_issues",
    "commit_scaffold",
    "create_specs_howto",
    "scaffold_project",
    "seed_memory_files",
    "update_gitignore",
    # upgrade
    "_MANIFEST_FILENAME",
    "_compute_bundled_catalog",
    "_sha256_file",
    "AssetActionType",
    "AssetDecision",
    "evaluate_upgrade_actions",
    "read_assets_manifest",
    "write_assets_manifest",
]
