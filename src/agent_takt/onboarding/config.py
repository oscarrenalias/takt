"""Config-generation and template-substitution helpers for the onboarding package.

This module owns YAML config generation, config key merging, placeholder
substitution, and template installation with substitution.  Asset-loading
delegates to :mod:`agent_takt._assets`; prompt data structures come from
:mod:`.prompts`.
"""

from __future__ import annotations

import re
from pathlib import Path

from .._assets import packaged_default_config, packaged_templates_dir
from .prompts import InitAnswers


# ---------------------------------------------------------------------------
# Config key merging
# ---------------------------------------------------------------------------


def merge_config_keys(
    user_config: dict,
    bundled_config: dict,
    *,
    _prefix: str = "",
) -> tuple[dict, list[str]]:
    """Recursively merge *bundled_config* keys into *user_config*, skipping existing keys.

    Only keys that are present in *bundled_config* but absent from *user_config*
    are inserted.  Existing user keys and values are never removed or overwritten.
    When both sides have a mapping at the same key, the merge recurses into that
    mapping so that nested new keys are inserted without disturbing sibling keys.

    Args:
        user_config: The user's current config dict.  Mutated in-place and
            returned as the first element of the result tuple.
        bundled_config: The bundled default config dict used as the source of
            missing keys.
        _prefix: Internal — dotted path prefix used during recursion.  Callers
            should not set this argument.

    Returns:
        A tuple ``(merged_config, added_keys)`` where *merged_config* is the
        updated *user_config* (same object, mutated in-place) and *added_keys*
        is a list of dotted key paths that were inserted, e.g.
        ``["scheduler.max_corrective_attempts", "claude.timeout_seconds"]``.
        The list is empty when no keys were added.
    """
    added_keys: list[str] = []
    for key, bundled_value in bundled_config.items():
        dotted = f"{_prefix}{key}" if _prefix else key
        if key not in user_config:
            user_config[key] = bundled_value
            added_keys.append(dotted)
        elif isinstance(bundled_value, dict) and isinstance(user_config[key], dict):
            _, child_added = merge_config_keys(
                user_config[key],
                bundled_value,
                _prefix=f"{dotted}.",
            )
            added_keys.extend(child_added)
    return user_config, added_keys


# ---------------------------------------------------------------------------
# Config YAML generation
# ---------------------------------------------------------------------------


def generate_config_yaml(answers: InitAnswers) -> str:
    """Return a ``config.yaml`` string reflecting *answers*.

    Reads the bundled ``default_config.yaml`` — the single source of truth for
    config structure and defaults — and substitutes only the user-provided runner
    and test command.  All other settings (``allowed_tools_default``,
    ``allowed_tools_by_agent``, ``scheduler``, etc.) come directly from the
    bundled file and remain complete and correct.

    .. note::
        If :func:`config.default_config` in ``config.py`` is updated with new
        keys, the bundled ``_data/default_config.yaml`` must be kept in sync so
        that ``takt init`` generates a config that matches the runtime defaults.

    Args:
        answers: Collected answers from :func:`~.prompts.collect_init_answers`.

    Returns:
        A YAML string suitable for writing to ``.takt/config.yaml``.
    """
    text = packaged_default_config().read_text(encoding="utf-8")
    # Replace only the specific YAML key values to avoid corrupting other fields.
    text = re.sub(r'(default_runner:\s*)\S+', rf'\g<1>{answers.runner}', text)
    text = re.sub(r'(test_command:\s*).*', rf'\g<1>{answers.test_command}', text)
    # Inject commit_bead_state so new projects opt-in to the explicit default.
    text = re.sub(
        r'(  test_timeout_seconds:\s*\d+)',
        r'\g<1>\n  commit_bead_state: false',
        text,
    )
    return text


# ---------------------------------------------------------------------------
# Placeholder substitution
# ---------------------------------------------------------------------------


def substitute_template_placeholders(text: str, answers: InitAnswers) -> str:
    """Replace ``{{PLACEHOLDER}}`` tokens in *text* with values from *answers*.

    Substituted placeholders:

    * ``{{LANGUAGE}}`` → ``answers.language``
    * ``{{TEST_COMMAND}}`` → ``answers.test_command``
    * ``{{BUILD_CHECK_COMMAND}}`` → ``answers.build_check_command``

    Args:
        text: Template text containing placeholder tokens.
        answers: Collected answers from :func:`~.prompts.collect_init_answers`.

    Returns:
        The text with all recognised placeholders replaced.
    """
    text = text.replace("{{LANGUAGE}}", answers.language)
    text = text.replace("{{TEST_COMMAND}}", answers.test_command)
    text = text.replace("{{BUILD_CHECK_COMMAND}}", answers.build_check_command)
    return text


# ---------------------------------------------------------------------------
# Template installation with substitution
# ---------------------------------------------------------------------------


def install_templates_with_substitution(
    project_root: Path,
    answers: InitAnswers,
    *,
    overwrite: bool = False,
) -> list[Path]:
    """Copy bundled guardrail templates to *project_root* with placeholder substitution.

    Unlike :func:`~.assets.install_templates`, this variant reads each template
    as text, substitutes ``{{LANGUAGE}}``, ``{{TEST_COMMAND}}``, and
    ``{{BUILD_CHECK_COMMAND}}``, then writes the substituted content to the
    destination.

    Args:
        project_root: Root directory of the target project.
        answers: Collected answers used for placeholder substitution.
        overwrite: Overwrite existing template files when ``True``.

    Returns:
        List of destination paths that were written.
    """
    src = packaged_templates_dir()
    dest = project_root / "templates" / "agents"
    written: list[Path] = []
    for item in src.rglob("*"):
        if item.is_dir():
            continue
        relative = item.relative_to(src)
        dest_file = dest / relative
        if dest_file.exists() and not overwrite:
            continue
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        content = item.read_text(encoding="utf-8")
        content = substitute_template_placeholders(content, answers)
        dest_file.write_text(content, encoding="utf-8")
        written.append(dest_file)
    return written
