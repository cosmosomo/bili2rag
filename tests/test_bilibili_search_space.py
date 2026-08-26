from __future__ import annotations

import unittest

from bilibili_search.space import _parse_vlist_item


class TestSpaceParsing(unittest.TestCase):
    def test_parse_vlist_item(self) -> None:
        obj = {
            "bvid": "BV1Hgr8BpE59",
            "title": "t",
            "author": "u",
            "mid": 123,
            "created": 1700000000,
            "length": "01:02",
            "play": 456,
        }
        it = _parse_vlist_item(obj)
        self.assertIsNotNone(it)
        assert it is not None
        self.assertEqual(it.bvid, "BV1Hgr8BpE59")
        self.assertEqual(it.length_seconds, 62)

    def test_parse_vlist_item_invalid(self) -> None:
        self.assertIsNone(_parse_vlist_item({"bvid": "av123"}))


if __name__ == "__main__":
    unittest.main()

