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
    install_skill_templates,
    install_templates,
)
from .config import (
    generate_config_yaml,
    install_templates_with_substitution,
    merge_config_keys,
    substitute_template_placeholders,
)
from .prompts import InitAnswers, STACKS, _prompt, collect_init_answers
from .scaffold import (
    _GITIGNORE_ENTRIES,
    _SPECS_HOWTO_CONTENT,
    commit_scaffold,
    create_specs_howto,
    scaffold_project,
    update_gitignore,
)
from .upgrade import (
    _MANIFEST_FILENAME,
    _compute_bundled_catalog,
    _sha256_file,
    AssetActionType,
    AssetDecision,
    ensure_adr_directories,
    evaluate_upgrade_actions,
    read_assets_manifest,
    write_assets_manifest,
)
from .version import (
    VERSION_FILE,
    check_version_drift,
    read_version_file,
    write_version_file,
)

__all__ = [
    # assets
    "copy_asset_dir",
    "copy_asset_file",
    "install_agents_skills",
    "install_claude_skills",
    "install_default_config",
    "install_skill_templates",
    "install_templates",
    # config
    "generate_config_yaml",
    "install_templates_with_substitution",
    "merge_config_keys",
    "substitute_template_placeholders",
    # prompts
    "InitAnswers",
    "STACKS",
    "_prompt",
    "collect_init_answers",
    # scaffold
    "_GITIGNORE_ENTRIES",
    "_SPECS_HOWTO_CONTENT",
    "commit_scaffold",
    "create_specs_howto",
    "scaffold_project",
    "update_gitignore",
    # upgrade
    "_MANIFEST_FILENAME",
    "_compute_bundled_catalog",
    "_sha256_file",
    "AssetActionType",
    "AssetDecision",
    "ensure_adr_directories",
    "evaluate_upgrade_actions",
    "read_assets_manifest",
    "write_assets_manifest",
    # version
    "VERSION_FILE",
    "check_version_drift",
    "read_version_file",
    "write_version_file",
]
