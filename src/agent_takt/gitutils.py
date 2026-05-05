from __future__ import annotations

import subprocess
from threading import Lock
from pathlib import Path


class GitError(RuntimeError):
    pass


_BEAD_STATE_PREFIX = ".takt/beads/"
_BEAD_STATE_PATHSPEC = _BEAD_STATE_PREFIX.rstrip("/")


_BEAD_STATE_GLOB = _BEAD_STATE_PREFIX + "**"


def _write_worktree_exclude(repo_root: Path, worktree_path: Path) -> None:
    """Write bead state glob patterns to the per-worktree git exclude file.

    The exclude file lives at repo_root/.git/worktrees/<worktree_name>/info/exclude.
    Note: .gitignore has higher precedence than info/exclude in git's rule ordering,
    so this does not fully suppress bead files that are explicitly un-ignored by
    !.takt/beads/** in .gitignore. The primary guard is _clean_untracked_bead_state
    called before merges.
    """
    worktree_name = worktree_path.name
    exclude_dir = repo_root / ".git" / "worktrees" / worktree_name / "info"
    exclude_dir.mkdir(parents=True, exist_ok=True)
    exclude_file = exclude_dir / "exclude"
    entries = [_BEAD_STATE_PREFIX, _BEAD_STATE_GLOB]
    if exclude_file.exists():
        lines = exclude_file.read_text().splitlines()
        missing = [e for e in entries if e not in lines]
        if missing:
            with exclude_file.open("a") as f:
                for e in missing:
                    f.write("\n" + e + "\n")
    else:
        exclude_file.write_text("\n".join(entries) + "\n")


class WorktreeManager:
    def __init__(self, root: Path, worktrees_dir: Path) -> None:
        self.root = root.resolve()
        self.worktrees_dir = worktrees_dir.resolve()
        self._lock = Lock()
        self._worktree_locks: dict[str, Lock] = {}

    def _run_git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.root,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise GitError(proc.stderr.strip() or proc.stdout.strip())
        return proc.stdout.strip()

    def ensure_repository(self) -> None:
        self._run_git("rev-parse", "--show-toplevel")

    def current_ref(self) -> str:
        return self._run_git("rev-parse", "HEAD")

    def branch_exists(self, branch_name: str) -> bool:
        proc = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
            cwd=self.root,
            check=False,
        )
        return proc.returncode == 0

    def worktree_path(self, feature_root_id: str) -> Path:
        """Get the filesystem path for a worktree given a feature root ID.

        Args:
            feature_root_id: The bead ID serving as the feature root (e.g., 'B-a7bc3f91').

        Returns:
            Path to the worktree directory (e.g., .takt/worktrees/B-a7bc3f91).
            Note: The path uses the feature_root_id directly, not lowercased.
        """
        return self.worktrees_dir / feature_root_id

    def _lock_for(self, feature_root_id: str) -> Lock:
        with self._lock:
            return self._worktree_locks.setdefault(feature_root_id, Lock())

    def _run_git_in(self, cwd: Path, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise GitError(proc.stderr.strip() or proc.stdout.strip())
        return proc.stdout.strip()

    def _save_and_remove_bead_files(self, worktree_path: Path) -> list[tuple[Path, bytes | None]]:
        """Save untracked .takt/beads/ files to memory and remove them from disk.

        Only files NOT in the git index are saved; tracked bead files are left alone
        to flow through git's normal merge-with-attributes path.

        Returns a list of (relative_path, content) tuples. content is None for empty files.
        """
        bead_dir = worktree_path / ".takt" / "beads"
        if not bead_dir.is_dir():
            return []
        ls_proc = subprocess.run(
            ["git", "ls-files", "--cached", "--", ".takt/beads/"],
            cwd=worktree_path,
            text=True,
            capture_output=True,
            check=False,
        )
        if ls_proc.returncode != 0:
            raise GitError(ls_proc.stderr.strip() or ls_proc.stdout.strip())
        tracked = {line.strip() for line in ls_proc.stdout.splitlines() if line.strip()}
        saved: list[tuple[Path, bytes | None]] = []
        for bead_file in sorted(bead_dir.rglob("*")):
            if not bead_file.is_file():
                continue
            rel_path = bead_file.relative_to(worktree_path)
            if str(rel_path) in tracked:
                continue
            raw = bead_file.read_bytes()
            saved.append((rel_path, raw if raw else None))
            bead_file.unlink()
        return saved

    def _restore_saved_bead_files(
        self, worktree_path: Path, saved: list[tuple[Path, bytes | None]]
    ) -> None:
        """Restore previously saved untracked bead files after a merge attempt."""
        for rel_path, content in saved:
            abs_path = worktree_path / rel_path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            if content is not None:
                abs_path.write_bytes(content)
            else:
                abs_path.touch()

    def _worktree_tracks_bead_state(self, worktree_path: Path) -> bool:
        proc = subprocess.run(
            ["git", "ls-files", "--cached", "--", _BEAD_STATE_PATHSPEC],
            cwd=worktree_path,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise GitError(proc.stderr.strip() or proc.stdout.strip())
        return bool(proc.stdout.strip())

    def _protect_worktree_bead_state(self, worktree_path: Path) -> None:
        _write_worktree_exclude(self.root, worktree_path)
        if not self._worktree_tracks_bead_state(worktree_path):
            return
        rm_proc = subprocess.run(
            ["git", "-C", str(worktree_path), "rm", "-r", "--cached", "--ignore-unmatch", _BEAD_STATE_PREFIX],
            text=True,
            capture_output=True,
            check=False,
        )
        if rm_proc.returncode != 0:
            raise GitError(rm_proc.stderr.strip() or rm_proc.stdout.strip())
        commit_proc = subprocess.run(
            [
                "git", "-C", str(worktree_path), "commit",
                "-m", "chore: untrack bead state from feature branch [skip ci]",
                "--allow-empty",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if commit_proc.returncode != 0:
            raise GitError(commit_proc.stderr.strip() or commit_proc.stdout.strip())

    def _conflicted_files_in(self, cwd: Path) -> list[str]:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise GitError(proc.stderr.strip() or proc.stdout.strip())
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]

    def _resolve_bead_state_conflicts(self, cwd: Path) -> bool:
        conflicted = self._conflicted_files_in(cwd)
        bead_conflicts = [path for path in conflicted if path.startswith(_BEAD_STATE_PREFIX)]
        if not bead_conflicts:
            return False
        non_bead_conflicts = [path for path in conflicted if not path.startswith(_BEAD_STATE_PREFIX)]
        if non_bead_conflicts:
            return False
        self._run_git_in(cwd, "checkout", "--ours", "--", *bead_conflicts)
        self._run_git_in(cwd, "add", "--", *bead_conflicts)
        remaining = self._conflicted_files_in(cwd)
        if remaining:
            raise GitError(
                "Bead-state auto-resolution did not fully stage merge conflicts: "
                + ", ".join(remaining)
            )
        self._run_git_in(cwd, "commit", "--no-edit")
        return True

    def _merge_with_bead_state_fallback(self, cwd: Path, *args: str) -> None:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode == 0:
            return
        if self._resolve_bead_state_conflicts(cwd):
            return
        raise GitError(proc.stderr.strip() or proc.stdout.strip())

    def ensure_worktree(self, feature_root_id: str, branch_name: str) -> Path:
        """Ensure a Git worktree exists for the given feature and branch.

        Creates a worktree at .takt/worktrees/{feature_root_id} if it doesn't exist.
        If the branch already exists in the repository, checks out that branch in the worktree.
        If the branch doesn't exist, creates a new branch from HEAD and checks it out.

        Args:
            feature_root_id: The bead ID serving as the feature root (e.g., 'B-a7bc3f91').
            branch_name: The Git branch name to use/create (e.g., 'feature/b-a7bc3f91').
                         Typically derived from feature_root_id via default_execution_branch_name().

        Returns:
            Path to the created or existing worktree directory.

        Raises:
            GitError: If any Git command fails.
        """
        with self._lock_for(feature_root_id):
            self.ensure_repository()
            self.worktrees_dir.mkdir(parents=True, exist_ok=True)
            target = self.worktree_path(feature_root_id)
            if target.exists():
                self._protect_worktree_bead_state(target)
                return target
            head_ref = self.current_ref()
            if self.branch_exists(branch_name):
                self._run_git("worktree", "add", str(target), branch_name)
            else:
                self._run_git("worktree", "add", "-b", branch_name, str(target), head_ref)
            self._protect_worktree_bead_state(target)
            return target

    def merge_branch(self, branch_name: str) -> None:
        self.ensure_repository()
        self._merge_with_bead_state_fallback(
            self.root,
            "merge",
            "--no-ff",
            "-s",
            "resolve",
            branch_name,
            "-m",
            f"Merge {branch_name}",
        )

    def commit_all(self, worktree_path: Path, message: str) -> str | None:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=worktree_path,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise GitError(proc.stderr.strip() or proc.stdout.strip())
        if not proc.stdout.strip():
            return None
        add_proc = subprocess.run(
            ["git", "add", "-A", "--", ".", ":(exclude).takt/beads/**"],
            cwd=worktree_path,
            text=True,
            capture_output=True,
            check=False,
        )
        if add_proc.returncode != 0:
            raise GitError(add_proc.stderr.strip() or add_proc.stdout.strip())
        diff_proc = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=worktree_path,
            text=True,
            capture_output=True,
            check=False,
        )
        if diff_proc.returncode == 0:
            return None
        commit_proc = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=worktree_path,
            text=True,
            capture_output=True,
            check=False,
        )
        if commit_proc.returncode != 0:
            raise GitError(commit_proc.stderr.strip() or commit_proc.stdout.strip())
        head_proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=worktree_path,
            text=True,
            capture_output=True,
            check=False,
        )
        if head_proc.returncode != 0:
            raise GitError(head_proc.stderr.strip() or head_proc.stdout.strip())
        return head_proc.stdout.strip()

    def _clean_untracked_bead_state(self, worktree_path: Path) -> None:
        """Remove untracked bead JSON files from the worktree.

        The .gitignore has !.takt/beads/** which un-ignores bead files project-wide.
        Since .gitignore takes precedence over info/exclude, the worktree's exclude
        file cannot suppress this. Bead files left as untracked after git rm --cached
        will cause 'would be overwritten by merge' errors when main has them tracked.
        Deleting them before the merge lets git proceed cleanly.
        """
        if not worktree_path.is_dir():
            return
        proc = subprocess.run(
            ["git", "ls-files", "--others", "--", _BEAD_STATE_PREFIX],
            cwd=worktree_path,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            return
        for rel_path in proc.stdout.splitlines():
            rel_path = rel_path.strip()
            if rel_path:
                target = worktree_path / rel_path
                if target.is_file() and target.suffix == ".json":
                    target.unlink(missing_ok=True)

    def merge_main_into_branch(self, worktree_path: Path, main_branch: str = "main") -> None:
        """Merge the main branch into the feature branch checked out in worktree_path.

        Untracked .takt/beads/ files (those not in the index) are saved and removed
        before the merge so git does not refuse with "would be overwritten by merge",
        then restored unconditionally via try/finally.  After a successful merge,
        _protect_worktree_bead_state untracks any newly indexed bead files brought in
        by the merge.

        Args:
            worktree_path: Path to the feature worktree.
            main_branch: Name of the main branch to merge from (default: 'main').

        Raises:
            GitError: If the merge fails (including conflict — caller should inspect
                      conflicted_files() and abort_merge() as needed).
        """
        saved = self._save_and_remove_bead_files(worktree_path)
        try:
            self._merge_with_bead_state_fallback(
                worktree_path,
                "merge",
                "--no-ff",
                main_branch,
                "-m",
                f"Merge {main_branch} into feature branch",
            )
        finally:
            self._restore_saved_bead_files(worktree_path, saved)
        self._protect_worktree_bead_state(worktree_path)

    def abort_merge(self, worktree_path: Path) -> None:
        """Abort an in-progress merge in the given worktree.

        Args:
            worktree_path: Path to the worktree where a merge is in progress.

        Raises:
            GitError: If there is no merge in progress or the abort fails.
        """
        proc = subprocess.run(
            ["git", "merge", "--abort"],
            cwd=worktree_path,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise GitError(proc.stderr.strip() or proc.stdout.strip())

    def conflicted_files(self, worktree_path: Path) -> list[str]:
        """Return the list of files with unresolved merge conflicts in the given worktree.

        Args:
            worktree_path: Path to the worktree to inspect.

        Returns:
            Sorted list of file paths that have unresolved conflicts (status 'UU', 'AA', 'DD',
            'AU', 'UA', 'DU', 'UD').

        Raises:
            GitError: If the git status command fails.
        """
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=worktree_path,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise GitError(proc.stderr.strip() or proc.stdout.strip())
        conflict_prefixes = {"UU", "AA", "DD", "AU", "UA", "DU", "UD"}
        conflicted: list[str] = []
        for line in proc.stdout.splitlines():
            if len(line) < 3:
                continue
            xy = line[:2]
            if xy in conflict_prefixes:
                path = line[3:]
                if " -> " in path:
                    path = path.split(" -> ", 1)[1]
                conflicted.append(path)
        return sorted(conflicted)

    def changed_files(self, worktree_path: Path) -> list[str]:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=worktree_path,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            raise GitError(proc.stderr.strip() or proc.stdout.strip())
        changed: list[str] = []
        for line in proc.stdout.splitlines():
            if not line:
                continue
            path = line[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            changed.append(path)
        return sorted(dict.fromkeys(changed))
