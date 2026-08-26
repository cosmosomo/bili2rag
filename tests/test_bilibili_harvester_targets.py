from __future__ import annotations

import unittest

from bilibili_harvester.utils import extract_targets


class TestHarvesterTargets(unittest.TestCase):
    def test_extract_targets_supports_url_and_bvid(self) -> None:
        lines = [
            "【标题】https://www.bilibili.com/video/BV1Hgr8BpE59?p=1",
            "BV1S7rYB5ERi",
            "  https://b23.tv/abcdEFG  ",
        ]
        out = extract_targets(lines)
        self.assertEqual(
            out,
            [
                "https://www.bilibili.com/video/BV1Hgr8BpE59?p=1",
                "https://www.bilibili.com/video/BV1S7rYB5ERi",
                "https://b23.tv/abcdEFG",
            ],
        )

    def test_extract_targets_skips_comments_and_dedupes(self) -> None:
        lines = [
            "",
            "# comment",
            "https://www.bilibili.com/video/BV1Hgr8BpE59",
            "BV1Hgr8BpE59",
        ]
        out = extract_targets(lines)
        self.assertEqual(out, ["https://www.bilibili.com/video/BV1Hgr8BpE59"])


if __name__ == "__main__":
    unittest.main()

