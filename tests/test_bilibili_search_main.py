from __future__ import annotations

import subprocess
import sys


def test_python_m_bilibili_search_help() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "bilibili_search", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert "Bilibili Search/Discover CLI" in (proc.stdout + proc.stderr)

