from __future__ import annotations

import json
from pathlib import Path

import pytest

from bilibili_get.orchestrate import BatchConfig, BatchItem, run_batch
from bilibili_library.completion import read_failures


def _mk_done(tmp_path: Path, bvid: str) -> None:
    d = tmp_path / "library" / "UP" / f"2026-01-01_t_{bvid}"
    (d / "asr").mkdir(parents=True, exist_ok=True)
    (d / "asr" / "transcript.txt").write_text("x", encoding="utf-8")


def test_deferred_asr_sweep_single_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Deferred mode: fetch via --no-asr subprocess, then one sweep fixes partials."""
    calls = {"cmd": None}

    def fake_run(cmd, cwd=None, stdout=None, stderr=None):
        calls["cmd"] = cmd
        bvid = cmd[cmd.index("--url") + 1]
        # simulate lean fetch: subtitle copied, transcript absent
        d = tmp_path / "library" / "UP" / f"2026-01-01_t_{bvid}"
        (d / "subtitles").mkdir(parents=True, exist_ok=True)
        (d / "subtitles" / f"{bvid}.zh-CN.srt").write_text(
            "1\n00:00:01,000 --> 00:00:02,000\n你好世界\n", encoding="utf-8"
        )
        import types

        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr("bilibili_get.orchestrate.subprocess.run", fake_run)

    swept = {"dirs": [], "model_loads": 0}

    class FakeModel:
        pass

    def fake_transcribe(vdir, cfg, **kw):
        swept["dirs"].append(vdir.name)
        # emulate CC bypass writing transcript
        (vdir / "asr").mkdir(parents=True, exist_ok=True)
        (vdir / "asr" / "transcript.txt").write_text("你好世界", encoding="utf-8")
        return {"mode": "cc_subtitle"}

    def fake_manifest(vdir):
        swept.setdefault("manifests", []).append(vdir.name)

    monkeypatch.setattr("bilibili_asr.asr.transcribe_bvid_dir", fake_transcribe)
    monkeypatch.setattr("bilibili_library.exporter.rewrite_manifest", fake_manifest)

    cfg = BatchConfig(
        run_dir=tmp_path / "run",
        cookies=str(tmp_path / "cookie.txt"),
        output_root=str(tmp_path / "output"),
        library_root=str(tmp_path / "library"),
        defer_asr=True,
    )
    report = run_batch([BatchItem("BV1111111111", "t")], cfg)

    # subprocess command must have fetched WITHOUT inline asr
    assert "--no-asr" in calls["cmd"]
    # sweep ran exactly once per item and promoted it to done/exported
    assert swept["dirs"] and report.exported == ["BV1111111111"]
    assert not report.skipped_partial
    assert swept.get("manifests") == swept["dirs"]
    # report.json written with exported list
    data = json.loads((cfg.run_dir / "report.json").read_text(encoding="utf-8"))
    assert data["exported"] == ["BV1111111111"]


def test_lean_fetch_not_marked_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lean fetch (subtitle, no audio) must NOT fall into the unavailable trap."""

    def fake_run(cmd, cwd=None, stdout=None, stderr=None):
        bvid = cmd[cmd.index("--url") + 1]
        d = tmp_path / "library" / "UP" / f"2026-01-01_t_{bvid}"
        (d / "subtitles").mkdir(parents=True, exist_ok=True)
        (d / "subtitles" / f"{bvid}.zh-CN.srt").write_text(
            "1\n00:00:01,000 --> 00:00:02,000\n你好世界\n", encoding="utf-8"
        )
        import types

        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr("bilibili_get.orchestrate.subprocess.run", fake_run)

    def fake_transcribe(vdir, cfg, **kw):
        (vdir / "asr").mkdir(parents=True, exist_ok=True)
        (vdir / "asr" / "transcript.txt").write_text("你好世界", encoding="utf-8")
        return {"mode": "cc_subtitle"}

    monkeypatch.setattr("bilibili_asr.asr.transcribe_bvid_dir", fake_transcribe)
    monkeypatch.setattr("bilibili_library.exporter.rewrite_manifest", lambda v: None)

    cfg = BatchConfig(
        run_dir=tmp_path / "run2",
        cookies=str(tmp_path / "cookie.txt"),
        output_root=str(tmp_path / "output"),
        library_root=str(tmp_path / "library"),
        defer_asr=True,
    )
    report = run_batch([BatchItem("BV2222222222", "t")], cfg)
    assert report.skipped_unavailable == []
    assert report.failed == []
    assert report.exported == ["BV2222222222"]
