from __future__ import annotations

import json
from pathlib import Path

from scripts.topic_make_targets_from_discoveries import main


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_topic_make_targets_country_pe_profile(tmp_path: Path) -> None:
    disc = tmp_path / "discoveries" / "run1"
    results = disc / "results.jsonl"
    _write_jsonl(
        results,
        [
            {
                "bvid": "BVpe1",
                "title": "一口气讲清楚：议会制、总统制、半总统制到底区别在哪？",
                "author": "制度UP",
                "mid": 1,
                "play": 10000,
                "favorites": 300,
                "review": 50,
                "danmaku": 120,
                "pubdate": 1700000000,
                "duration_seconds": 900,
            },
            {
                "bvid": "BVexam1",
                "title": "政体区分选择题技巧：一分钟拿分",
                "author": "刷题UP",
                "mid": 4,
                "play": 500000,
                "favorites": 10000,
                "review": 500,
                "danmaku": 500,
                "pubdate": 1700000003,
                "duration_seconds": 900,
            },
            {
                "bvid": "BVnews1",
                "title": "突发！某国最新局势解读",
                "author": "新闻UP",
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
            "country_pe",
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
            "7200",
        ]
    )

    txt = out_targets.read_text(encoding="utf-8")
    assert "BVpe1" in txt
    assert "BVexam1" not in txt
    assert "BVnews1" not in txt
    assert "BVirrelevant1" not in txt
