from __future__ import annotations

import logging
from pathlib import Path

from .doc_refs import normalize_linked_docs
from .documents.frontmatter import read_frontmatter_file
from .models import BEAD_DONE, BEAD_READY, PlanChild, PlanProposal
from .runner import AgentRunner
from .storage import RepositoryStorage

_VALID_AGENT_TYPES = frozenset(("planner", "developer", "tester", "documentation", "review"))

logger = logging.getLogger(__name__)


def _validate_plan_child_agent_types(node: PlanChild) -> None:
    if node.agent_type not in _VALID_AGENT_TYPES:
        raise ValueError(
            f"Invalid agent_type {node.agent_type!r} in bead {node.title!r}. "
            f"Valid types: {sorted(_VALID_AGENT_TYPES)}"
        )
    for child in node.children:
        _validate_plan_child_agent_types(child)


def spec_ref_from_path(spec_path: Path | str | None) -> tuple[str | None, str]:
    """Return (``spec:<id>`` or None, reason) for the spec at *spec_path*."""
    if not spec_path:
        return None, "no spec path"
    try:
        fm, _ = read_frontmatter_file(spec_path)
    except Exception as exc:  # unreadable or malformed
        return None, f"cannot read {spec_path}: {exc}"
    spec_id = str((fm or {}).get("id") or "").strip()
    if not spec_id:
        return None, f"no frontmatter id in {spec_path}"
    return f"spec:{spec_id}", ""


class PlanningService:
    def __init__(self, storage: RepositoryStorage, runner: AgentRunner) -> None:
        self.storage = storage
        self.runner = runner

    def propose(self, spec_path: Path) -> PlanProposal:
        try:
            fm, _ = read_frontmatter_file(spec_path)
            spec_id = str((fm or {}).get("id") or "").strip() or None
        except Exception:  # unreadable or malformed frontmatter
            spec_id = None
        return self.runner.propose_plan(
            spec_path.read_text(encoding="utf-8"), root=self.storage.root, spec_id=spec_id
        )

    def _docs(self, entries: list[str], spec_ref: str | None, spec_stem: str | None) -> list[str]:
        out = normalize_linked_docs(list(entries or []), self.storage.root)
        if spec_ref:
            out = [
                e for e in out
                if e == spec_ref
                or not (
                    spec_stem
                    and isinstance(e, str)
                    and Path(e).suffix.lower() == ".md"
                    and Path(e).stem == spec_stem
                )
            ]
            if spec_ref not in out:
                out.append(spec_ref)
        return out

    def persist_plan(
        self, proposal: PlanProposal, *, spec_ref: str | None = None, spec_stem: str | None = None
    ) -> list[str]:
        """Persist a PlanProposal to storage and return the list of created bead IDs.

        This helper contains all bead-creation logic so it can be shared by the
        ``--write`` flag (via :meth:`write_plan`) and future file-based promotion
        flows (e.g. ``--from-file``).
        """
        if proposal.feature is not None:
            _validate_plan_child_agent_types(proposal.feature)
        epic = self.storage.create_bead(
            title=proposal.epic_title,
            agent_type="planner",
            description=proposal.epic_description,
            status=BEAD_DONE,
            bead_type="epic",
            linked_docs=self._docs(proposal.linked_docs, spec_ref, spec_stem),
        )
        created = [epic.bead_id]

        if proposal.feature is not None:
            title_to_id = {"EPIC": epic.bead_id}
            pending_dependencies: list[tuple[str, list[str]]] = []
            feature = self.storage.create_bead(
                title=proposal.feature.title,
                agent_type=proposal.feature.agent_type,
                description=proposal.feature.description,
                status=BEAD_DONE,
                bead_type="feature",
                parent_id=epic.bead_id,
                feature_root_id=None,
                dependencies=[],
                acceptance_criteria=proposal.feature.acceptance_criteria,
                linked_docs=self._docs(proposal.feature.linked_docs, spec_ref, spec_stem),
                expected_files=proposal.feature.expected_files,
                expected_globs=proposal.feature.expected_globs,
            )
            title_to_id[proposal.feature.title] = feature.bead_id
            created.append(feature.bead_id)

            def create_tree(node: PlanChild, *, parent_id: str) -> None:
                bead = self.storage.create_bead(
                    title=node.title,
                    agent_type=node.agent_type,
                    description=node.description,
                    status=BEAD_READY,
                    parent_id=parent_id,
                    feature_root_id=None,
                    dependencies=[],
                    acceptance_criteria=node.acceptance_criteria,
                    linked_docs=self._docs(node.linked_docs, spec_ref, spec_stem),
                    expected_files=node.expected_files,
                    expected_globs=node.expected_globs,
                )
                title_to_id[node.title] = bead.bead_id
                created.append(bead.bead_id)
                pending_dependencies.append((bead.bead_id, list(node.dependencies)))
                for child in node.children:
                    create_tree(child, parent_id=bead.bead_id)

            for child in proposal.feature.children:
                create_tree(child, parent_id=feature.bead_id)

            for bead_id, dependencies in pending_dependencies:
                bead = self.storage.load_bead(bead_id)
                bead.dependencies = [
                    title_to_id.get(dep, dep) if dep not in {"", "none", "None"} else dep for dep in dependencies
                ]
                bead.dependencies = [dep for dep in bead.dependencies if dep]
                self.storage.save_bead(bead)

        return created

    def write_plan(self, proposal: PlanProposal, *, spec_path: Path | None = None) -> list[str]:
        spec_ref, _reason = spec_ref_from_path(spec_path)
        return self.persist_plan(
            proposal, spec_ref=spec_ref, spec_stem=Path(spec_path).stem if spec_path else None
        )
