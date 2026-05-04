from __future__ import annotations

import json
import subprocess
from pathlib import Path


class AdapterError(Exception):
    """Raised when a takt subprocess call fails or returns unexpected output."""

    def __init__(self, message: str, stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


def _extract_json(text: str) -> dict | list:
    """Extract the first JSON object or array from mixed text+JSON output."""
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    lines = stripped.splitlines()
    start = None
    for i, line in enumerate(lines):
        ls = line.lstrip()
        if ls.startswith("{") or ls.startswith("["):
            start = i
            break

    if start is not None:
        candidate = "\n".join(lines[start:])
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    raise ValueError(f"No JSON object found in output:\n{text}")


class TaktAdapter:
    """Subprocess boundary for invoking `uv run takt` in a project directory.

    All project-path subprocess interaction is centralised here.  Callers
    receive structured results or a descriptive `AdapterError` that preserves
    stdout/stderr for run-log recording.
    """

    def __init__(self, project_path: Path, timeout: int | None = None) -> None:
        self.project_path = project_path
        self.timeout = timeout

    def _invoke(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        cmd = ["uv", "run", "takt", *args]
        try:
            result = subprocess.run(
                cmd,
                cwd=self.project_path,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            raise AdapterError(
                f"takt command timed out after {self.timeout}s: {' '.join(args)}",
                stdout=stdout,
                stderr=stderr,
            ) from exc

        if result.returncode != 0:
            raise AdapterError(
                f"takt command exited with code {result.returncode}: {' '.join(args)}",
                stdout=result.stdout,
                stderr=result.stderr,
            )
        return result

    def _parse_json(self, stdout: str, stderr: str, context: str) -> dict:
        try:
            value = _extract_json(stdout)
        except ValueError as exc:
            raise AdapterError(
                f"Malformed JSON from takt {context}: {exc}",
                stdout=stdout,
                stderr=stderr,
            ) from exc
        if not isinstance(value, dict):
            raise AdapterError(
                f"Expected JSON object from takt {context}, got {type(value).__name__}",
                stdout=stdout,
                stderr=stderr,
            )
        return value

    def summary(self) -> dict:
        """Run `takt summary` and return the parsed JSON result."""
        result = self._invoke(["summary"])
        return self._parse_json(result.stdout, result.stderr, "summary")

    def create_bead(
        self,
        title: str,
        description: str,
        agent_type: str,
        labels: list[str],
    ) -> str:
        """Run `takt bead create` and return the created bead ID."""
        args = [
            "bead", "create",
            "--title", title,
            "--description", description,
            "--agent", agent_type,
        ]
        for label in labels:
            args.extend(["--label", label])

        result = self._invoke(args)

        for line in result.stdout.splitlines():
            if "Created bead" in line:
                parts = line.split()
                if parts:
                    return parts[-1]

        raise AdapterError(
            "Could not extract bead ID from takt bead create output",
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def run(self, runner: str | None, max_workers: int | None) -> dict:
        """Run `takt run` and return the parsed JSON summary block."""
        args: list[str] = []
        if runner is not None:
            args.extend(["--runner", runner])
        args.append("run")
        if max_workers is not None:
            args.extend(["--max-workers", str(max_workers)])

        result = self._invoke(args)
        return self._parse_json(result.stdout, result.stderr, "run")

    def version(self) -> str:
        """Run `takt --version` and return the version string."""
        result = self._invoke(["--version"])
        return result.stdout.strip()
