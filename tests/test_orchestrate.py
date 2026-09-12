from __future__ import annotations

from pathlib import Path

import pytest

from bilibili_get.orchestrate import BatchConfig, BatchItem, extract_bvids_from_lines, run_batch
from bilibili_library.completion import (
    is_unavailable,
    list_unavailable,
    mark_unavailable,
    read_failures,
)


def _cfg(tmp_path: Path, **kw) -> BatchConfig:
    run_dir = tmp_path / "run"
    kw.setdefault("defer_asr", False)  # legacy inline semantics for these tests
    return BatchConfig(
        run_dir=run_dir,
        cookies=str(tmp_path / "cookie.txt"),
        output_root=str(tmp_path / "output"),
        library_root=str(tmp_path / "library"),
        **kw,
    )


def _mk_done(tmp_path: Path, bvid: str) -> None:
    d = tmp_path / "library" / "UP" / f"2026-01-01_t_{bvid}"
    (d / "asr").mkdir(parents=True, exist_ok=True)
    (d / "asr" / "transcript.txt").write_text("x", encoding="utf-8")


def _mk_partial(tmp_path: Path, bvid: str) -> None:
    d = tmp_path / "library" / "UP" / f"2026-01-01_t_{bvid}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "audio.mp3").write_bytes(b"\x00")


class FakeRun:
    def __init__(self, returncode: int = 0, log_text: str = ""):
        self.returncode = returncode
        self.log_text = log_text

    def __call__(self, cmd, cwd=None, stdout=None, stderr=None):
        if stdout is not None and self.log_text:
            stdout.write(self.log_text)
        return self


def test_extract_bvids_from_lines_variants() -> None:
    lines = [
        "【标题甲】https://www.bilibili.com/video/BV1111111111",
        "BV2222222222\t标题乙",
        "random line with BV3333333333 inside",
        "dupe BV3333333333",
        "",
    ]
    items = extract_bvids_from_lines(lines)
    assert [i.bvid for i in items] == ["BV1111111111", "BV2222222222", "BV3333333333"]
    assert items[0].title == "标题甲"
    assert items[1].title == "标题乙"
    assert items[2].title == ""


def test_run_batch_skips_done_partial_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"n": 0}

    def fake_run(cmd, cwd=None, stdout=None, stderr=None):
        called["n"] += 1
        return FakeRun()

    monkeypatch.setattr("bilibili_get.orchestrate.subprocess.run", fake_run)

    _mk_done(tmp_path, "BV1111111111")
    _mk_partial(tmp_path, "BV2222222222")
    lib = tmp_path / "library"
    mark_unavailable(lib, "BV3333333333", reason="test")

    cfg = _cfg(tmp_path)
    report = run_batch(
        [
            BatchItem("BV1111111111", "done"),
            BatchItem("BV2222222222", "partial"),
            BatchItem("BV3333333333", "gone"),
        ],
        cfg,
    )
    assert called["n"] == 0  # nothing required a subprocess
    assert report.skipped_done == ["BV1111111111"]
    assert report.skipped_partial == ["BV2222222222"]
    assert report.skipped_unavailable == ["BV3333333333"]
    assert report.exported == [] and report.failed == []
    assert report.exit_code() == 0
    # partial was recorded in the ledger
    assert any(r["bvid"] == "BV2222222222" and r["stage"] == "partial_asr" for r in read_failures(lib))


def test_run_batch_success_and_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"BV1111111111": FakeRun(0), "BV4444444444": FakeRun(1, log_text="ERROR: view API error code=-404")}

    def fake_run(cmd, cwd=None, stdout=None, stderr=None):
        bvid = cmd[cmd.index("--url") + 1]
        fake = calls[bvid]
        if stdout is not None and fake.log_text:
            stdout.write(fake.log_text)
        if bvid == "BV1111111111":
            _mk_done(tmp_path, bvid)  # simulate the pipeline exporting successfully
        return fake

    monkeypatch.setattr("bilibili_get.orchestrate.subprocess.run", fake_run)

    cfg = _cfg(tmp_path)
    report = run_batch([BatchItem("BV1111111111"), BatchItem("BV4444444444")], cfg)
    assert report.exported == ["BV1111111111"]
    assert "BV4444444444" in report.skipped_unavailable  # -404 pinned as unavailable
    assert report.failed == []
    assert is_unavailable(tmp_path / "library", "BV4444444444")
    assert report.exit_code() == 0
    # report.json written
    import json

    data = json.loads((cfg.run_dir / "report.json").read_text(encoding="utf-8"))
    assert data["discovered"] == 2


def test_run_batch_transient_failure_not_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd, cwd=None, stdout=None, stderr=None):
        stdout.write("[HARVEST FAIL] SSL: UNEXPECTED_EOF_WHILE_READING")
        return FakeRun(1)

    monkeypatch.setattr("bilibili_get.orchestrate.subprocess.run", fake_run)
    cfg = _cfg(tmp_path)
    report = run_batch([BatchItem("BV5555555555")], cfg)
    assert report.failed and report.failed[0]["bvid"] == "BV5555555555"
    assert list_unavailable(tmp_path / "library") == {}  # transient error must not pin state
    assert report.exit_code() == 1
