from __future__ import annotations

import json
from pathlib import Path

from bilibili_library.completion import (
    CLASS_NO_AUDIO,
    CLASS_NO_TRANSCRIPT_WITH_AUDIO,
    CLASS_OK,
    append_failure,
    classify,
    clear_unavailable,
    extract_bvid_from_dirname,
    find_video_dir,
    is_done,
    is_unavailable,
    list_unavailable,
    mark_unavailable,
    read_failures,
)


def _mk_video(root: Path, name: str, *, transcript: bool = False, audio: bool = False) -> Path:
    d = root / "UP" / name
    d.mkdir(parents=True, exist_ok=True)
    if transcript:
        (d / "asr").mkdir(parents=True, exist_ok=True)
        (d / "asr" / "transcript.txt").write_text("文本", encoding="utf-8")
    if audio:
        (d / "audio.mp3").write_bytes(b"\x00" * 8)
    return d


def test_extract_bvid_from_dirname() -> None:
    assert extract_bvid_from_dirname("2026-01-25_标题_BV15JqABoEvj") == "BV15JqABoEvj"
    assert extract_bvid_from_dirname("no-bvid-here") is None


def test_is_done_single_page(tmp_path: Path) -> None:
    d = _mk_video(tmp_path, "a_BV1111111111")
    assert classify(d) == CLASS_NO_AUDIO
    assert not is_done(d)

    _mk_video(tmp_path, "a_BV1111111111", audio=True)
    d = find_video_dir(tmp_path, "BV1111111111")
    assert classify(d) == CLASS_NO_TRANSCRIPT_WITH_AUDIO
    assert not is_done(d)

    _mk_video(tmp_path, "a_BV1111111111", transcript=True, audio=True)
    d = find_video_dir(tmp_path, "BV1111111111")
    assert classify(d) == CLASS_OK
    assert is_done(d)


def test_is_done_multi_page(tmp_path: Path) -> None:
    d = tmp_path / "UP" / "multi_BV2222222222" / "pages" / "p01_123"
    d.mkdir(parents=True)
    (d / "asr").mkdir()
    (d / "asr" / "transcript.txt").write_text("x", encoding="utf-8")
    vdir = find_video_dir(tmp_path, "BV2222222222")
    assert vdir is not None
    assert is_done(vdir)


def test_find_video_dir_skips_service_dirs(tmp_path: Path) -> None:
    service = tmp_path / "_by_category" / "分类" / "UP_BV3333333333"
    service.mkdir(parents=True)
    assert find_video_dir(tmp_path, "BV3333333333") is None
    _mk_video(tmp_path, "x_BV3333333333", transcript=True)
    assert find_video_dir(tmp_path, "BV3333333333") is not None


def test_build_video_index_matches_find(tmp_path: Path) -> None:
    _mk_video(tmp_path, "a_BV7777777777", transcript=True, audio=True)
    _mk_video(tmp_path, "b_BV8888888888", audio=True)
    (tmp_path / "_by_category" / "cat").mkdir(parents=True)
    idx = __import__("bilibili_library.completion", fromlist=["build_video_index"]).build_video_index(tmp_path)
    assert set(idx) == {"BV7777777777", "BV8888888888"}
    for bvid, d in idx.items():
        assert d == find_video_dir(tmp_path, bvid)  # index == scan result
        assert find_video_dir(tmp_path, bvid, index=idx) is d  # index fast-path


def test_ledger_failure_roundtrip(tmp_path: Path) -> None:
    assert read_failures(tmp_path) == []
    append_failure(tmp_path, bvid="BV1", stage="subprocess", error="rc=1", title="t", run_id="r1")
    append_failure(tmp_path, bvid="BV2", stage="partial_asr", error="no transcript")
    rows = read_failures(tmp_path)
    assert [r["bvid"] for r in rows] == ["BV1", "BV2"]
    assert rows[0]["stage"] == "subprocess"
    assert "ts" in rows[0]
    assert read_failures(tmp_path, limit=1) == [rows[1]]
    # records carry no status field: pure log, never read for decisions
    assert all("status" not in r for r in rows)


def test_unavailable_lifecycle(tmp_path: Path) -> None:
    assert not is_unavailable(tmp_path, "BV1")
    mark_unavailable(tmp_path, "BV1", reason="auto: 404")
    assert is_unavailable(tmp_path, "BV1")
    listed = list_unavailable(tmp_path)
    assert "BV1" in listed and listed["BV1"]["reason"] == "auto: 404"
    # idempotent mark
    mark_unavailable(tmp_path, "BV1", reason="other")
    assert list_unavailable(tmp_path)["BV1"]["reason"] == "auto: 404"
    assert clear_unavailable(tmp_path, "BV1")
    assert not is_unavailable(tmp_path, "BV1")
    assert not clear_unavailable(tmp_path, "BV1")


def test_unavailable_file_is_valid_jsonl(tmp_path: Path) -> None:
    mark_unavailable(tmp_path, "BV1", reason="a")
    mark_unavailable(tmp_path, "BV2", reason="b")
    path = tmp_path / "_ledger" / "unavailable.jsonl"
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
    assert {r["bvid"] for r in lines} == {"BV1", "BV2"}
