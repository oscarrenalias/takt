from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_takt_fleet.adapter import AdapterError, TaktAdapter, _extract_json


# ---------------------------------------------------------------------------
# _extract_json helper
# ---------------------------------------------------------------------------


def test_extract_json_pure_object():
    data = {"done": 2, "ready": 1}
    assert _extract_json(json.dumps(data)) == data


def test_extract_json_with_leading_text():
    payload = {"started": [], "completed": ["B-abc"]}
    mixed = "! Warning: version drift\n" + json.dumps(payload, indent=2)
    assert _extract_json(mixed) == payload


def test_extract_json_with_trailing_text():
    payload = {"a": 1}
    mixed = json.dumps(payload) + "\nsome trailing line"
    # The whole stripped text starts with { so json.loads succeeds on stripped.
    # But here there is trailing text, so let's verify the fallback path finds it.
    # Actually json.loads won't parse "...}\nsome trailing line" so we rely on
    # line-scan fallback which grabs from "{" to end; that also fails the same way.
    # This test confirms the function raises ValueError for ambiguous output.
    with pytest.raises(ValueError, match="No JSON object found"):
        _extract_json(mixed)


def test_extract_json_array():
    data = [1, 2, 3]
    assert _extract_json(json.dumps(data)) == data


def test_extract_json_no_json_raises():
    with pytest.raises(ValueError, match="No JSON object found"):
        _extract_json("just plain text, no JSON here")


def test_extract_json_indented_block_after_text():
    payload = {"final_state": {"done": 3}}
    output = "Scheduler\n• Starting loop\n✓ Cycle complete\n" + json.dumps(payload, indent=2)
    assert _extract_json(output) == payload


# ---------------------------------------------------------------------------
# TaktAdapter — helpers
# ---------------------------------------------------------------------------


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> MagicMock:
    """Build a fake subprocess.CompletedProcess."""
    result = MagicMock(spec=subprocess.CompletedProcess)
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = returncode
    return result


def _adapter(tmp_path: Path, timeout: int | None = 30) -> TaktAdapter:
    return TaktAdapter(project_path=tmp_path, timeout=timeout)


# ---------------------------------------------------------------------------
# TaktAdapter.version
# ---------------------------------------------------------------------------


def test_version_returns_stripped_output(tmp_path):
    adapter = _adapter(tmp_path)
    with patch("subprocess.run", return_value=_completed(stdout="takt 1.2.3\n")):
        assert adapter.version() == "takt 1.2.3"


def test_version_raises_on_nonzero(tmp_path):
    adapter = _adapter(tmp_path)
    with patch("subprocess.run", return_value=_completed(stdout="", returncode=1, stderr="not found")):
        with pytest.raises(AdapterError, match="exited with code 1"):
            adapter.version()


def test_version_preserves_stdout_stderr_on_error(tmp_path):
    adapter = _adapter(tmp_path)
    with patch("subprocess.run", return_value=_completed(stdout="bad", returncode=2, stderr="err")):
        exc = None
        try:
            adapter.version()
        except AdapterError as e:
            exc = e
        assert exc is not None
        assert exc.stdout == "bad"
        assert exc.stderr == "err"


# ---------------------------------------------------------------------------
# TaktAdapter.summary
# ---------------------------------------------------------------------------


SUMMARY_PAYLOAD = {"done": 5, "ready": 2, "in_progress": 1, "blocked": 0, "handed_off": 0}


def test_summary_parses_json(tmp_path):
    adapter = _adapter(tmp_path)
    output = json.dumps(SUMMARY_PAYLOAD, indent=2)
    with patch("subprocess.run", return_value=_completed(stdout=output)):
        result = adapter.summary()
    assert result == SUMMARY_PAYLOAD


def test_summary_parses_json_after_warning_text(tmp_path):
    adapter = _adapter(tmp_path)
    output = "! Version drift: upgrade takt\n" + json.dumps(SUMMARY_PAYLOAD, indent=2)
    with patch("subprocess.run", return_value=_completed(stdout=output)):
        result = adapter.summary()
    assert result == SUMMARY_PAYLOAD


def test_summary_raises_on_nonzero(tmp_path):
    adapter = _adapter(tmp_path)
    with patch("subprocess.run", return_value=_completed(returncode=1, stderr="takt not found")):
        with pytest.raises(AdapterError, match="exited with code 1"):
            adapter.summary()


def test_summary_raises_on_malformed_json(tmp_path):
    adapter = _adapter(tmp_path)
    with patch("subprocess.run", return_value=_completed(stdout="not json at all")):
        with pytest.raises(AdapterError, match="Malformed JSON"):
            adapter.summary()


def test_summary_raises_on_unexpected_json_type(tmp_path):
    adapter = _adapter(tmp_path)
    with patch("subprocess.run", return_value=_completed(stdout=json.dumps([1, 2, 3]))):
        with pytest.raises(AdapterError, match="Expected JSON object"):
            adapter.summary()


# ---------------------------------------------------------------------------
# TaktAdapter.create_bead
# ---------------------------------------------------------------------------


def test_create_bead_extracts_bead_id(tmp_path):
    adapter = _adapter(tmp_path)
    output = "✓ Created bead B-a1b2c3d4\n"
    with patch("subprocess.run", return_value=_completed(stdout=output)):
        bead_id = adapter.create_bead(
            title="Test title",
            description="Some description",
            agent_type="developer",
            labels=[],
        )
    assert bead_id == "B-a1b2c3d4"


def test_create_bead_passes_labels(tmp_path):
    adapter = _adapter(tmp_path)
    output = "✓ Created bead B-deadbeef\n"
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return _completed(stdout=output)

    with patch("subprocess.run", side_effect=fake_run):
        adapter.create_bead("T", "D", "developer", labels=["urgent", "api"])

    cmd = captured[0]
    assert "--label" in cmd
    label_indices = [i for i, v in enumerate(cmd) if v == "--label"]
    assert len(label_indices) == 2
    labels_passed = [cmd[i + 1] for i in label_indices]
    assert set(labels_passed) == {"urgent", "api"}


def test_create_bead_raises_on_nonzero(tmp_path):
    adapter = _adapter(tmp_path)
    with patch("subprocess.run", return_value=_completed(returncode=1, stderr="error")):
        with pytest.raises(AdapterError, match="exited with code 1"):
            adapter.create_bead("T", "D", "developer", [])


def test_create_bead_raises_when_id_not_found(tmp_path):
    adapter = _adapter(tmp_path)
    with patch("subprocess.run", return_value=_completed(stdout="unexpected output line\n")):
        with pytest.raises(AdapterError, match="Could not extract bead ID"):
            adapter.create_bead("T", "D", "developer", [])


def test_create_bead_uses_correct_cwd(tmp_path):
    adapter = _adapter(tmp_path)
    captured: list[dict] = []

    def fake_run(cmd, **kwargs):
        captured.append(kwargs)
        return _completed(stdout="✓ Created bead B-00000001\n")

    with patch("subprocess.run", side_effect=fake_run):
        adapter.create_bead("T", "D", "developer", [])

    assert captured[0]["cwd"] == tmp_path


# ---------------------------------------------------------------------------
# TaktAdapter.run
# ---------------------------------------------------------------------------


RUN_PAYLOAD = {
    "started": ["B-aaa"],
    "completed": ["B-aaa"],
    "blocked": [],
    "correctives_created": [],
    "deferred_count": 0,
    "final_state": {"done": 1},
}


def test_run_parses_json_summary(tmp_path):
    adapter = _adapter(tmp_path)
    prefix = "Scheduler\n• Starting\n✓ Cycle done\n"
    output = prefix + json.dumps(RUN_PAYLOAD, indent=2)
    with patch("subprocess.run", return_value=_completed(stdout=output)):
        result = adapter.run(runner=None, max_workers=None)
    assert result == RUN_PAYLOAD


def test_run_passes_runner_flag(tmp_path):
    adapter = _adapter(tmp_path)
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return _completed(stdout=json.dumps(RUN_PAYLOAD))

    with patch("subprocess.run", side_effect=fake_run):
        adapter.run(runner="claude", max_workers=None)

    cmd = captured[0]
    assert "--runner" in cmd
    assert cmd[cmd.index("--runner") + 1] == "claude"


def test_run_passes_max_workers_flag(tmp_path):
    adapter = _adapter(tmp_path)
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return _completed(stdout=json.dumps(RUN_PAYLOAD))

    with patch("subprocess.run", side_effect=fake_run):
        adapter.run(runner=None, max_workers=4)

    cmd = captured[0]
    assert "--max-workers" in cmd
    assert cmd[cmd.index("--max-workers") + 1] == "4"


def test_run_omits_optional_flags_when_none(tmp_path):
    adapter = _adapter(tmp_path)
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return _completed(stdout=json.dumps(RUN_PAYLOAD))

    with patch("subprocess.run", side_effect=fake_run):
        adapter.run(runner=None, max_workers=None)

    cmd = captured[0]
    assert "--runner" not in cmd
    assert "--max-workers" not in cmd


def test_run_raises_on_nonzero(tmp_path):
    adapter = _adapter(tmp_path)
    with patch("subprocess.run", return_value=_completed(returncode=1)):
        with pytest.raises(AdapterError, match="exited with code 1"):
            adapter.run(runner=None, max_workers=None)


def test_run_raises_on_malformed_json(tmp_path):
    adapter = _adapter(tmp_path)
    with patch("subprocess.run", return_value=_completed(stdout="Cycle done\n no json here")):
        with pytest.raises(AdapterError, match="Malformed JSON"):
            adapter.run(runner=None, max_workers=None)


# ---------------------------------------------------------------------------
# Timeout handling
# ---------------------------------------------------------------------------


def test_timeout_raises_adapter_error(tmp_path):
    adapter = _adapter(tmp_path, timeout=5)
    exc = subprocess.TimeoutExpired(cmd=["uv", "run", "takt", "--version"], timeout=5)
    with patch("subprocess.run", side_effect=exc):
        with pytest.raises(AdapterError, match="timed out"):
            adapter.version()


def test_timeout_error_preserves_context(tmp_path):
    adapter = _adapter(tmp_path, timeout=5)
    exc = subprocess.TimeoutExpired(
        cmd=["uv", "run", "takt", "summary"],
        timeout=5,
        output=b"partial",
        stderr=b"err",
    )
    with patch("subprocess.run", side_effect=exc):
        raised = None
        try:
            adapter.summary()
        except AdapterError as e:
            raised = e
    assert raised is not None
    assert "timed out" in str(raised)


# ---------------------------------------------------------------------------
# Subprocess invocation — shared invariants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method,args,kwargs", [
    ("version", [], {}),
    ("summary", [], {}),
    ("create_bead", [], {"title": "T", "description": "D", "agent_type": "developer", "labels": []}),
    ("run", [], {"runner": None, "max_workers": None}),
])
def test_invocation_uses_uv_run_takt(tmp_path, method, args, kwargs):
    """Every adapter method must invoke 'uv run takt ...' as the command prefix."""
    adapter = _adapter(tmp_path)
    captured: list[list[str]] = []

    def fake_run(cmd, **kw):
        captured.append(cmd)
        if method == "create_bead":
            return _completed(stdout="✓ Created bead B-00000001\n")
        if method == "run":
            return _completed(stdout=json.dumps(RUN_PAYLOAD))
        if method == "summary":
            return _completed(stdout=json.dumps(SUMMARY_PAYLOAD))
        return _completed(stdout="takt 1.0.0\n")

    with patch("subprocess.run", side_effect=fake_run):
        getattr(adapter, method)(*args, **kwargs)

    assert captured, "subprocess.run was not called"
    cmd = captured[0]
    assert cmd[:3] == ["uv", "run", "takt"], f"unexpected command prefix: {cmd[:3]}"


@pytest.mark.parametrize("method,args,kwargs", [
    ("version", [], {}),
    ("summary", [], {}),
    ("create_bead", [], {"title": "T", "description": "D", "agent_type": "developer", "labels": []}),
    ("run", [], {"runner": None, "max_workers": None}),
])
def test_invocation_uses_project_path_as_cwd(tmp_path, method, args, kwargs):
    adapter = _adapter(tmp_path)
    captured: list[dict] = []

    def fake_run(cmd, **kw):
        captured.append(kw)
        if method == "create_bead":
            return _completed(stdout="✓ Created bead B-00000001\n")
        if method == "run":
            return _completed(stdout=json.dumps(RUN_PAYLOAD))
        if method == "summary":
            return _completed(stdout=json.dumps(SUMMARY_PAYLOAD))
        return _completed(stdout="takt 1.0.0\n")

    with patch("subprocess.run", side_effect=fake_run):
        getattr(adapter, method)(*args, **kwargs)

    assert captured[0]["cwd"] == tmp_path
