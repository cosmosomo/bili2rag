from __future__ import annotations

from bilibili_get.orchestrate import extract_bvids_from_lines


def test_extract_bvids_dedupe_and_parse() -> None:
    lines = [
        "【标题】https://www.bilibili.com/video/BV1ABCDEF123",
        "BV1ABCDEF123",
        "noise BV9ZZZZZZZZZ and BV9ZZZZZZZZZ",
        "no bvid here",
    ]
    got = [it.bvid for it in extract_bvids_from_lines(lines)]
    assert got == ["BV1ABCDEF123", "BV9ZZZZZZZZZ"]
