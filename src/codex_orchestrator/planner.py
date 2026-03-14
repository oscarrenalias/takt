from __future__ import annotations

from pathlib import Path

from .models import BEAD_DONE, BEAD_READY, PlanProposal
from .runner import AgentRunner
from .storage import RepositoryStorage


class PlanningService:
    def __init__(self, storage: RepositoryStorage, runner: AgentRunner) -> None:
        self.storage = storage
        self.runner = runner

    def propose(self, spec_path: Path) -> PlanProposal:
        return self.runner.propose_plan(spec_path.read_text(encoding="utf-8"))

    def write_plan(self, proposal: PlanProposal) -> list[str]:
        epic = self.storage.create_bead(
            title=proposal.epic_title,
            agent_type="planner",
            description=proposal.epic_description,
            status=BEAD_DONE,
            bead_type="epic",
            linked_docs=proposal.linked_docs,
        )
        created = [epic.bead_id]
        title_to_id = {"EPIC": epic.bead_id}
        pending = list(proposal.children)

        for child in pending:
            bead = self.storage.create_bead(
                title=child.title,
                agent_type=child.agent_type,
                description=child.description,
                status=BEAD_READY,
                parent_id=epic.bead_id,
                feature_root_id=None,
                dependencies=[],
                acceptance_criteria=child.acceptance_criteria,
                linked_docs=child.linked_docs,
                expected_files=child.expected_files,
                expected_globs=child.expected_globs,
            )
            title_to_id[child.title] = bead.bead_id
            created.append(bead.bead_id)

        for bead_id, child in zip(created[1:], proposal.children, strict=True):
            bead = self.storage.load_bead(bead_id)
            bead.dependencies = [
                title_to_id.get(dep, dep) if dep not in {"", "none", "None"} else dep for dep in child.dependencies
            ]
            bead.dependencies = [dep for dep in bead.dependencies if dep]
            self.storage.save_bead(bead)
        return created
