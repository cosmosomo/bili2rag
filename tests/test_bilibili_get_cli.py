from __future__ import annotations

import unittest

from bilibili_get.cli import build_parser


class TestBilibiliGetCli(unittest.TestCase):
    def test_parser_has_expected_commands(self) -> None:
        p = build_parser()
        # Just ensure parse doesn't throw for each subcommand.
        run_args = p.parse_args(["run", "--url", "BV15JqABoEvj", "--resume", "--no-asr", "--no-export"])
        self.assertTrue(run_args.resume)
        p.parse_args(["export", "--bvid", "BV15JqABoEvj"])
        p.parse_args(["search", "--keyword", "基础模型"])
        p.parse_args(["uploader", "--seed-bvid", "BV15JqABoEvj"])
        p.parse_args(["asr-uploader", "--seed-bvid", "BV15JqABoEvj", "--limit", "1", "--process-limit", "1"])


if __name__ == "__main__":
    unittest.main()
