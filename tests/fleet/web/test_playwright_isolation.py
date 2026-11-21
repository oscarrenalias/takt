"""Regression: the ui tests must not leave Playwright's asyncio loop running."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

UI_TESTS = Path(__file__).with_name("test_ui.py")


def test_asyncio_run_works_after_ui_tests(tmp_path):
    probe = tmp_path / "test_zz_asyncio_probe.py"
    probe.write_text(textwrap.dedent("""
        import asyncio

        def test_asyncio_run_after_ui():
            async def f():
                return 1
            assert asyncio.run(f()) == 1
    """))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(UI_TESTS), str(probe), "-q", "-p", "no:cacheprovider",
         "-p", "no:randomly", "-k", "test_no_stray_placeholder or test_asyncio_run_after_ui"],
        capture_output=True, text=True, timeout=110, cwd=UI_TESTS.parents[3],
    )
    out = proc.stdout + proc.stderr
    assert "cannot be called from a running event loop" not in out, out
    assert proc.returncode == 0, out
