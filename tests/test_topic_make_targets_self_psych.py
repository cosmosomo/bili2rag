from __future__ import annotations

import json
from pathlib import Path

from scripts.topic_make_targets_from_discoveries import main


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_topic_make_targets_self_psych_profile(tmp_path: Path) -> None:
    disc = tmp_path / "discoveries" / "run1"
    results = disc / "results.jsonl"
    _write_jsonl(
        results,
        [
            {
                "bvid": "BVok1",
                "title": "如何培养我能办成「任何事的感觉」：自我效能感与掌控感",
                "author": "心理心法UP",
                "mid": 1,
                "play": 100000,
                "favorites": 5000,
                "review": 300,
                "danmaku": 800,
                "pubdate": 1700000000,
                "duration_seconds": 900,
            },
            {
                # has signal word "心理" but should be excluded as ASMR/助眠
                "bvid": "BVasmr1",
                "title": "心理助眠ASMR：白噪音陪你入睡",
                "author": "助眠UP",
                "mid": 2,
                "play": 999999,
                "favorites": 99999,
                "review": 9999,
                "danmaku": 9999,
                "pubdate": 1700000001,
                "duration_seconds": 1200,
            },
            {
                # has signal word "安全感" but should be excluded as fortune-telling
                "bvid": "BVtarot1",
                "title": "塔罗占卜：你该如何建立安全感？",
                "author": "塔罗UP",
                "mid": 3,
                "play": 500000,
                "favorites": 20000,
                "review": 2000,
                "danmaku": 2000,
                "pubdate": 1700000002,
                "duration_seconds": 900,
            },
            {
                # has signal word but should be excluded as exam/cram
                "bvid": "BVexam1",
                "title": "考研心态：稳住别慌，三天冲刺拿分",
                "author": "考研UP",
                "mid": 4,
                "play": 500000,
                "favorites": 10000,
                "review": 500,
                "danmaku": 500,
                "pubdate": 1700000003,
                "duration_seconds": 900,
            },
            {
                "bvid": "BVirrelevant1",
                "title": "旅行vlog：城市散步",
                "author": "生活UP",
                "mid": 5,
                "play": 100000,
                "favorites": 1000,
                "review": 100,
                "danmaku": 100,
                "pubdate": 1700000004,
                "duration_seconds": 900,
            },
        ],
    )

    out_targets = tmp_path / "targets.txt"
    out_report = tmp_path / "report.json"
    main(
        [
            "--profile",
            "self_psych",
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
    assert "BVok1" in txt
    assert "BVasmr1" not in txt
    assert "BVtarot1" not in txt
    assert "BVexam1" not in txt
    assert "BVirrelevant1" not in txt

