from __future__ import annotations

import subprocess
from threading import Lock
from pathlib import Path


class GitError(RuntimeError):
    pass


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
        return self.worktrees_dir / feature_root_id

    def _lock_for(self, feature_root_id: str) -> Lock:
        with self._lock:
            return self._worktree_locks.setdefault(feature_root_id, Lock())

    def ensure_worktree(self, feature_root_id: str, branch_name: str) -> Path:
        with self._lock_for(feature_root_id):
            self.ensure_repository()
            self.worktrees_dir.mkdir(parents=True, exist_ok=True)
            target = self.worktree_path(feature_root_id)
            if target.exists():
                return target
            head_ref = self.current_ref()
            if self.branch_exists(branch_name):
                self._run_git("worktree", "add", str(target), branch_name)
            else:
                self._run_git("worktree", "add", "-b", branch_name, str(target), head_ref)
            return target

    def merge_branch(self, branch_name: str) -> None:
        self.ensure_repository()
        self._run_git("merge", "--no-ff", branch_name, "-m", f"Merge {branch_name}")

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
            ["git", "add", "-A"],
            cwd=worktree_path,
            text=True,
            capture_output=True,
            check=False,
        )
        if add_proc.returncode != 0:
            raise GitError(add_proc.stderr.strip() or add_proc.stdout.strip())
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
