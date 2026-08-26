from __future__ import annotations

from scripts.collect_targets import _extract_bvids


def test_extract_bvids_dedupe_and_parse() -> None:
    lines = [
        "【标题】https://www.bilibili.com/video/BV1ABCDEF123",
        "BV1ABCDEF123",
        "noise BV9ZZZZZZZZZ and BV9ZZZZZZZZZ",
        "no bvid here",
    ]
    got = _extract_bvids(lines)
    assert got == ["BV1ABCDEF123", "BV9ZZZZZZZZZ"]

