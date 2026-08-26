from __future__ import annotations

import unittest

from bilibili_harvester.cookies import ensure_buvid_cookie_header


class TestEnsureBuvidCookieHeader(unittest.TestCase):
    def test_returns_unchanged_if_buvid_present(self) -> None:
        header = "SESSDATA=abc; bili_jct=def; buvid3=AAA; buvid4=BBB"
        out = ensure_buvid_cookie_header(header, proxy=None)
        self.assertEqual(out, header)


if __name__ == "__main__":
    unittest.main()

