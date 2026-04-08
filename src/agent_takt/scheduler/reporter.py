from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..models import Bead


class SchedulerReporter(Protocol):
    """Observer interface for scheduler lifecycle events.

    Implementors receive callbacks as beads move through the scheduler cycle.
    The CLI reporter (``CliSchedulerReporter``) logs events to the terminal;
    the TUI reporter (``TuiSchedulerReporter``) posts them to the log panel.
    Both implementations must satisfy this protocol.

    All methods are called from the scheduler's worker threads and must be
    thread-safe.
    """

    def lease_expired(self, bead_id: str) -> None: ...

    def bead_started(self, bead: Bead) -> None: ...

    def worktree_ready(self, bead: Bead, branch_name: str, worktree_path: Path) -> None: ...

    def bead_completed(self, bead: Bead, summary: str, created: list[Bead]) -> None: ...

    def bead_deferred(self, bead: Bead, reason: str) -> None:
        """Called when a bead is skipped or requeued during a scheduler cycle.

        ``reason`` is a human-readable string describing why the bead was not
        dispatched this pass.  Common reasons include:

        - ``"file-scope conflict with in-progress <bead_id>"`` — overlapping
          ``expected_files`` / ``expected_globs`` between this bead and one
          that is already running.
        - ``"worktree in use by in-progress <bead_id> (no file scope defined)"``
          — a mutating bead shares the same feature-tree worktree and neither
          bead has a declared file scope.
        - ``"dependency not done: <dep_id>, ..."`` — the bead is READY but one
          or more of its listed dependencies have not reached DONE status yet.
        - ``"Requeued blocked bead after transient failure"`` — a transiently
          blocked bead has been reset to READY for the next attempt.
        - ``"Created corrective bead <corrective_id> ..."`` — a corrective
          retry bead was spawned; the original bead is standing by.
        - ``"Created recovery bead <recovery_id> ..."`` — a recovery bead was
          auto-created after a no-structured-output failure.

        Implementations may suppress or format this event at their discretion.
        The CLI reporter only emits this event when ``--verbose`` is set;
        the TUI reporter always appends it to the log panel.

        Each bead is reported at most once per ``run_once()`` call regardless
        of how many fill-loop iterations inspect it.
        """
        ...

    def bead_blocked(self, bead: Bead, summary: str) -> None: ...

    def bead_failed(self, bead: Bead, summary: str) -> None: ...
