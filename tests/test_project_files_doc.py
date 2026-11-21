"""Every bead field must be documented in the Beads section of docs/project-files.md."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.models import Bead, ExecutionRecord, Lease

PROJECT_FILES_DOC = REPO_ROOT / "docs" / "project-files.md"


def bead_section(doc: str, heading: str = "Beads") -> str:
    """Text between `## <heading>` and the next `## ` heading (subsections included)."""
    match = re.search(rf"^## {re.escape(heading)}[ \t]*$", doc, re.M)
    if match is None:
        raise AssertionError(f"heading '## {heading}' not found")
    rest = doc[match.end():]
    nxt = re.search(r"^## ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def missing_keys(section: str, keys) -> list[str]:
    """Keys that do not appear as an inline-code token (`key`) in `section`."""
    return sorted(k for k in keys if f"`{k}`" not in section)


def _populated_bead_dict() -> dict:
    bead = Bead(
        bead_id="B-12345678", title="t", agent_type="developer", description="d",
        priority="high", lease=Lease(owner="o", expires_at="2026-01-01T00:00:00+00:00"),
        labels=["x"], recovery_for="B-00000000",
    )
    bead.execution_history.append(ExecutionRecord(
        timestamp="2026-01-01T00:00:00+00:00", event="created", agent_type="scheduler", summary="s",
    ))
    return bead.to_dict()  # same serialisation RepositoryStorage uses


class ProjectFilesDocTests(unittest.TestCase):
    def test_all_current_bead_fields_documented(self) -> None:
        self.assertTrue(PROJECT_FILES_DOC.is_file(), "docs/project-files.md is missing")
        section = bead_section(PROJECT_FILES_DOC.read_text(encoding="utf-8"))
        keys = _populated_bead_dict().keys()
        self.assertGreater(len(keys), 20)
        missing = missing_keys(section, keys)
        self.assertEqual([], missing, f"bead keys not documented in docs/project-files.md: {missing}")

    def test_extra_bead_key_fails_and_is_named(self) -> None:
        section = bead_section(PROJECT_FILES_DOC.read_text(encoding="utf-8"))
        keys = [*_populated_bead_dict().keys(), "brand_new_field"]
        self.assertEqual(["brand_new_field"], missing_keys(section, keys))

    def test_key_only_outside_bead_section_does_not_count(self) -> None:
        doc = (
            "# Doc\n\n## Beads\n\n| `alpha` | x |\n\n### Sub\n\n`beta` here\n\n"
            "## Traces\n\n| `gamma` | only documented under traces |\n\n## Readers\n"
        )
        section = bead_section(doc)
        self.assertIn("`beta`", section)  # subsections belong to the bead section
        self.assertEqual(["gamma"], missing_keys(section, ["alpha", "beta", "gamma"]))

    def test_compatibility_and_readers_headings_exist(self) -> None:
        doc = PROJECT_FILES_DOC.read_text(encoding="utf-8")
        for heading in ("Compatibility rules", "Readers"):
            self.assertRegex(doc, rf"(?m)^## {heading}\s*$")


if __name__ == "__main__":
    unittest.main()
