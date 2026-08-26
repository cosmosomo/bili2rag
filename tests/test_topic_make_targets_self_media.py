from __future__ import annotations

import json
from pathlib import Path

from scripts.topic_make_targets_from_discoveries import main


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_topic_make_targets_self_media_profile(tmp_path: Path) -> None:
    disc = tmp_path / "discoveries" / "run1"
    results = disc / "results.jsonl"
    _write_jsonl(
        results,
        [
            {
                "bvid": "BVsm1",
                "title": "自媒体起号：从0到1讲清选题、标题、脚本与剪辑（实操）",
                "author": "运营UP",
                "mid": 1,
                "play": 10000,
                "favorites": 300,
                "review": 50,
                "danmaku": 120,
                "pubdate": 1700000000,
                "duration_seconds": 900,
            },
            {
                "bvid": "BVstory1",
                "title": "作为富二代，我为什么要做自媒体？",
                "author": "故事UP",
                "mid": 4,
                "play": 20000,
                "favorites": 500,
                "review": 80,
                "danmaku": 200,
                "pubdate": 1700000003,
                "duration_seconds": 900,
            },
            {
                "bvid": "BVnoise1",
                "title": "原神自媒体：抽卡整活合集",
                "author": "游戏UP",
                "mid": 2,
                "play": 999999,
                "favorites": 99999,
                "review": 9999,
                "danmaku": 9999,
                "pubdate": 1700000001,
                "duration_seconds": 900,
            },
            {
                "bvid": "BVirrelevant1",
                "title": "旅行vlog：城市散步",
                "author": "生活UP",
                "mid": 3,
                "play": 100000,
                "favorites": 1000,
                "review": 100,
                "danmaku": 100,
                "pubdate": 1700000002,
                "duration_seconds": 900,
            },
        ],
    )

    out_targets = tmp_path / "targets.txt"
    out_report = tmp_path / "report.json"
    main(
        [
            "--profile",
            "self_media",
            "--input",
            str(disc),
            "--out-targets",
            str(out_targets),
            "--out-report",
            str(out_report),
            "--limit",
            "50",
            "--min-seconds",
            "180",
            "--max-seconds",
            "5400",
        ]
    )

    txt = out_targets.read_text(encoding="utf-8")
    assert "BVsm1" in txt
    assert "BVstory1" not in txt
    assert "BVnoise1" not in txt
    assert "BVirrelevant1" not in txt
