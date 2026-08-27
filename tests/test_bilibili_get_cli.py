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
        p.parse_args(["grab-uploader", "--seed-bvid", "BV15JqABoEvj", "--new-limit", "1"])
        p.parse_args(["grab-targets", "--targets-file", "x.txt", "--prune-output"])
        p.parse_args(["repair", "--library-root", "library", "--dry-run"])


if __name__ == "__main__":
    unittest.main()
