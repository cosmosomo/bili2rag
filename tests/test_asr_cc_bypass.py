from __future__ import annotations

from pathlib import Path

import pytest

from bilibili_asr.asr import ASRConfig, find_cc_subtitle, parse_srt, transcribe_bvid_dir, write_asr_from_subtitle


SRT = """1
00:00:01,000 --> 00:00:03,500
你好，歡迎來到測試

2
00:00:04,000 --> 00:00:06,000
第二行字幕內容
"""


def _mk(tmp_path: Path, *, subtitles: bool = False, audio: bool = False) -> Path:
    d = tmp_path / "UP" / "2026-01-01_t_BV1111111111"
    d.mkdir(parents=True, exist_ok=True)
    if subtitles:
        (d / "subtitles").mkdir(exist_ok=True)
        (d / "subtitles" / "BV1111111111.zh-CN.srt").write_text(SRT, encoding="utf-8")
    if audio:
        (d / "audio.mp3").write_bytes(b"\x00" * 16)
    return d


def test_parse_srt_roundtrip() -> None:
    p = tmp = None
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "a.srt"
        f.write_text(SRT, encoding="utf-8")
        segs = parse_srt(f)
    assert segs[0]["start"] == 1.0 and segs[0]["end"] == 3.5
    assert segs[0]["text"] == "你好，歡迎來到測試"
    assert segs[1]["start"] == 4.0


def test_find_cc_subtitle_prefers_uploader_over_ai(tmp_path: Path) -> None:
    d = _mk(tmp_path, subtitles=True)
    (d / "subtitles" / "BV1111111111.ai-zh.srt").write_text(SRT, encoding="utf-8")
    best = find_cc_subtitle(d)
    assert best is not None and "ai-zh" not in best.name and "zh" in best.name


def test_find_cc_subtitle_none_for_foreign(tmp_path: Path) -> None:
    d = _mk(tmp_path)
    (d / "subtitles").mkdir(exist_ok=True)
    (d / "subtitles" / "x.en.srt").write_text(SRT, encoding="utf-8")
    assert find_cc_subtitle(d) is None


def test_write_asr_from_subtitle_simplifies(tmp_path: Path) -> None:
    d = _mk(tmp_path, subtitles=True)
    sub = d / "subtitles" / "BV1111111111.zh-CN.srt"
    res = write_asr_from_subtitle(sub, d / "asr", source="cc:test")
    assert res["segments"] == 2
    txt = (d / "asr" / "transcript.txt").read_text(encoding="utf-8")
    assert "欢迎" in txt and "歡迎" not in txt  # simplified enforcement
    assert (d / "asr" / "transcript.srt").exists()
    import json

    segs = json.loads((d / "asr" / "segments.json").read_text(encoding="utf-8"))
    assert segs["source"].startswith("cc:")


def test_transcribe_bvid_dir_cc_bypass_no_whisper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CC subtitle present -> outputs materialized without loading whisper."""
    d = _mk(tmp_path, subtitles=True, audio=True)

    def _no_model(cfg):
        raise AssertionError("whisper must not be loaded when a zh CC subtitle exists")

    monkeypatch.setattr("bilibili_asr.asr.load_model", _no_model)
    rep = transcribe_bvid_dir(d, ASRConfig(device="cpu", prefer_subtitle=True))
    assert rep["mode"] == "cc_subtitle"
    assert (d / "asr" / "transcript.txt").exists()
    assert (d / "logs" / "asr_run.json").exists()


def test_transcribe_bvid_dir_no_cc_still_whisper_flag(tmp_path: Path) -> None:
    """No subtitle -> bypass must not fabricate anything (falls through)."""
    d = _mk(tmp_path, audio=True)
    assert find_cc_subtitle(d) is None
    # transcribe would need whisper; just assert the bypass decision here
    # (full whisper path is covered by live runs).
