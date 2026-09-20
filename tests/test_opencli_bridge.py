from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bilibili_opencli import bridge


def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "_AVAILABLE_CACHE", {})


def _fake_proc(stdout: bytes, returncode: int = 0, stderr: bytes = b"") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ---------- availability ----------

def test_available_false_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset(monkeypatch)

    def boom(cmd, **kw):
        raise FileNotFoundError("opencli")

    monkeypatch.setattr(bridge.subprocess, "run", boom)
    assert bridge.available() is False


def test_available_true_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset(monkeypatch)
    calls = {"n": 0}

    def ok(cmd, **kw):
        calls["n"] += 1
        return _fake_proc(b"1.8.7")

    monkeypatch.setattr(bridge.subprocess, "run", ok)
    assert bridge.available() is True
    assert bridge.available() is True
    assert calls["n"] == 1  # cached


# ---------- chart (hot / ranking) ----------

def test_chart_hot_keeps_bvid_field(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset(monkeypatch)
    data = [{"rank": 1, "title": "T1", "author": "A", "play": 9, "danmaku": 2,
             "bvid": "BV1111111111", "url": "https://www.bilibili.com/video/BV1111111111"}]
    monkeypatch.setattr(bridge, "_run", lambda args, **kw: data)
    out = bridge.chart("hot", 10)
    assert out[0]["bvid"] == "BV1111111111" and out[0]["title"] == "T1"


def test_chart_ranking_extracts_bvid_from_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset(monkeypatch)
    data = [  # ranking shape: no bvid key, url only
        {"rank": 1, "title": "R1", "author": "A", "score": 100, "url": "https://www.bilibili.com/video/BV2222222222"},
        {"rank": 2, "title": "", "author": "B", "score": 1, "url": "https://www.bilibili.com/video/BV3333333333"},
    ]
    monkeypatch.setattr(bridge, "_run", lambda args, **kw: data)
    out = bridge.chart("ranking", 10)
    assert [i["bvid"] for i in out] == ["BV2222222222"]  # empty-title item dropped


def test_chart_limit_and_bad_source(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset(monkeypatch)
    monkeypatch.setattr(bridge, "_run", lambda args, **kw: [
        {"rank": i, "title": f"t{i}", "bvid": f"BV{i:010d}", "url": "x"} for i in range(5)
    ])
    assert len(bridge.chart("hot", 3)) == 3
    with pytest.raises(ValueError):
        bridge.chart("nope")


def test_run_raises_bridge_error_on_bad_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset(monkeypatch)
    monkeypatch.setattr(bridge.subprocess, "run", lambda cmd, **kw: _fake_proc(b"", returncode=1, stderr=b"boom"))
    with pytest.raises(bridge.BridgeError):
        bridge._run(["bilibili", "hot"])


def test_run_raises_bridge_error_on_non_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset(monkeypatch)
    monkeypatch.setattr(bridge.subprocess, "run", lambda cmd, **kw: _fake_proc("不是json".encode("utf-8")))
    with pytest.raises(bridge.BridgeError):
        bridge._run(["bilibili", "hot"])


def test_comments_and_summary_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset(monkeypatch)
    seen: list = []
    data = {"comments": [{"rank": 1, "rpid": "42", "author": "u", "text": "hi", "likes": 1, "replies": 2, "time": "t"}],
            "summary": [{"time": "00:01", "content": "# 大纲"}]}

    def fake_run(args, **kw):
        seen.append(args)
        return data[args[1]]

    monkeypatch.setattr(bridge, "_run", fake_run)
    cs = bridge.comments("BV1", parent="42")
    assert cs[0]["rpid"] == "42" and seen[-1][:2] == ["bilibili", "comments"]
    sm = bridge.summary("BV1")
    assert sm[0]["content"].startswith("#") and seen[-1][:2] == ["bilibili", "summary"]


# ---------- grab-hot discovery head ----------

def _hot_args(tmp_path: Path, **kw) -> SimpleNamespace:
    """Parse real CLI args so the namespace matches _batch_config_from_args."""
    from bilibili_get import cli

    argv = ["grab-hot", "--out-dir", str(tmp_path)]
    for k, v in kw.items():
        argv += [f"--{k.replace('_', '-')}", str(v)]
    return cli.build_parser().parse_args(argv)


def test_grab_hot_feeds_engine_and_writes_targets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bilibili_get import cli

    _reset(monkeypatch)
    monkeypatch.setattr(bridge, "available", lambda: True)
    monkeypatch.setattr(bridge, "chart", lambda src, lim: [
        {"rank": 1, "title": "iPhone 深度", "author": "a", "bvid": "BV1111111111", "url": "u"},
        {"rank": 2, "title": "随便什么", "author": "b", "bvid": "BV2222222222", "url": "u"},
    ])
    captured: dict = {}

    def fake_run_batch(items, cfg):
        captured["items"] = [(i.bvid, i.title) for i in items]
        captured["run_dir"] = cfg.run_dir
        return SimpleNamespace(exit_code=lambda: 0)

    monkeypatch.setattr("bilibili_get.orchestrate.run_batch", fake_run_batch)
    rc = cli._grab_hot_cmd(_hot_args(tmp_path, require_any="iphone"))
    assert rc == 0
    assert captured["items"] == [("BV1111111111", "iPhone 深度")]
    targets = (captured["run_dir"] / "targets.txt").read_text(encoding="utf-8")
    assert "BV1111111111" in targets and "BV2222222222" not in targets


def test_grab_hot_aborts_without_opencli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bilibili_get import cli

    _reset(monkeypatch)
    monkeypatch.setattr(bridge, "available", lambda: False)
    assert cli._grab_hot_cmd(_hot_args(tmp_path)) == 2


# ---------- backfill_comments ----------

def _mk_video(tmp_path: Path, bvid: str, *, comments_txt: str = "") -> Path:
    d = tmp_path / "library" / "UP" / f"2026-01-01_t_{bvid}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "asr").mkdir(exist_ok=True)
    (d / "asr" / "transcript.txt").write_text("x", encoding="utf-8")
    if comments_txt is not None:
        (d / "comments.txt").write_text(comments_txt, encoding="utf-8")
    return d


def test_backfill_writes_missing_comments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import backfill_comments as bc

    _reset(monkeypatch)
    monkeypatch.setattr(bridge, "available", lambda: True)
    monkeypatch.setattr(bridge, "fetch_timestamp", lambda: "T")
    d = _mk_video(tmp_path, "BV1111111111", comments_txt="")  # empty -> needs backfill
    _mk_video(tmp_path, "BV9999999999", comments_txt="已有内容\n")  # non-empty -> untouched

    monkeypatch.setattr(bridge, "comments", lambda bvid, parent=None, limit=0: [
        {"rank": 1, "rpid": "7", "author": "甲", "text": "主评", "likes": 3, "replies": 1, "time": "t"},
    ])
    manifests: list = []
    monkeypatch.setattr(bc, "rewrite_manifest", lambda vdir: manifests.append(vdir.name))

    rc = bc.main(["--library-root", str(tmp_path / "library"), "--deep", "1"])
    assert rc == 0
    txt = (d / "comments.txt").read_text(encoding="utf-8")
    assert "甲: 主评" in txt
    raw = json.loads((d / "json" / "comments_opencli.json").read_text(encoding="utf-8"))
    assert raw["source"] == "opencli:bilibili.comments" and raw["bvid"] == "BV1111111111"
    assert manifests == [d.name]  # only the backfilled video


def test_backfill_never_overwrites_non_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import backfill_comments as bc

    _reset(monkeypatch)
    monkeypatch.setattr(bridge, "available", lambda: True)
    d = _mk_video(tmp_path, "BV1111111111", comments_txt="原快照\n")
    monkeypatch.setattr(bridge, "comments", lambda bvid, parent=None, limit=0: [])
    rc = bc.main(["--library-root", str(tmp_path / "library")])
    assert rc == 0
    assert (d / "comments.txt").read_text(encoding="utf-8") == "原快照\n"  # untouched


def test_backfill_empty_result_is_empty_not_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import backfill_comments as bc

    _reset(monkeypatch)
    monkeypatch.setattr(bridge, "available", lambda: True)
    d = _mk_video(tmp_path, "BV1111111111", comments_txt="")

    def no_comments(bvid, parent=None, limit=0):
        raise bridge.BridgeError("opencli 退出码 66: code: EMPTY_RESULT", exit_code=66, adapter_code="EMPTY_RESULT")

    monkeypatch.setattr(bridge, "comments", no_comments)
    monkeypatch.setattr(bc, "rewrite_manifest", lambda vdir: None)
    rc = bc.main(["--library-root", str(tmp_path / "library")])
    assert rc == 0  # zero comments is a normal outcome, not a failure
    assert not (d / "json" / "comments_opencli.json").exists()  # nothing fabricated


def test_bridge_error_carries_adapter_code(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset(monkeypatch)
    monkeypatch.setattr(bridge.subprocess, "run", lambda cmd, **kw: _fake_proc(
        b"", returncode=66, stderr=b"ok: false\nerror:\n  code: EMPTY_RESULT\n  message: no data"
    ))
    with pytest.raises(bridge.BridgeError) as ei:
        bridge._run(["bilibili", "comments", "BV1"])
    assert ei.value.exit_code == 66 and ei.value.adapter_code == "EMPTY_RESULT"
