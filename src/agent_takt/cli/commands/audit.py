from __future__ import annotations

import argparse
import json
from pathlib import Path

from ...audit import build_feature_audit, render_feature_audit_markdown
from ...console import ConsoleReporter
from ...storage import RepositoryStorage


def command_audit(args: argparse.Namespace, storage: RepositoryStorage, console: ConsoleReporter) -> int:
    """Print or write the feature audit report. Read-only: no bead writes, no events."""
    try:
        bead = storage.load_bead(storage.resolve_bead_id(args.bead_id))
    except (ValueError, FileNotFoundError) as exc:
        console.error(str(exc))
        return 1
    feature_root_id = storage.feature_root_id_for(bead) or bead.bead_id
    audit = build_feature_audit(storage, feature_root_id)
    if args.json:
        text = json.dumps(audit, indent=2)
    else:
        text = render_feature_audit_markdown(audit)
    if not text.endswith("\n"):
        text += "\n"
    if args.output:
        try:
            Path(args.output).write_text(text, encoding="utf-8")
        except OSError as exc:
            console.error(f"cannot write {args.output}: {exc}")
            return 1
        return 0
    print(text, end="")
    return 0
