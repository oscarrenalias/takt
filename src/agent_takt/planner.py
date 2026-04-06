from __future__ import annotations

from pathlib import Path

from .models import BEAD_DONE, BEAD_READY, PlanChild, PlanProposal
from .runner import AgentRunner
from .storage import RepositoryStorage

_VALID_AGENT_TYPES = frozenset(("planner", "developer", "tester", "documentation", "review"))


def _validate_plan_child_agent_types(node: PlanChild) -> None:
    if node.agent_type not in _VALID_AGENT_TYPES:
        raise ValueError(
            f"Invalid agent_type {node.agent_type!r} in bead {node.title!r}. "
            f"Valid types: {sorted(_VALID_AGENT_TYPES)}"
        )
    for child in node.children:
        _validate_plan_child_agent_types(child)


class PlanningService:
    def __init__(self, storage: RepositoryStorage, runner: AgentRunner) -> None:
        self.storage = storage
        self.runner = runner

    def propose(self, spec_path: Path) -> PlanProposal:
        return self.runner.propose_plan(spec_path.read_text(encoding="utf-8"))

    def write_plan(self, proposal: PlanProposal) -> list[str]:
        if proposal.feature is not None:
            _validate_plan_child_agent_types(proposal.feature)
        epic = self.storage.create_bead(
            title=proposal.epic_title,
            agent_type="planner",
            description=proposal.epic_description,
            status=BEAD_DONE,
            bead_type="epic",
            linked_docs=proposal.linked_docs,
        )
        created = [epic.bead_id]
        if proposal.feature is None:
            return created

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
            linked_docs=proposal.feature.linked_docs,
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
                linked_docs=node.linked_docs,
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
