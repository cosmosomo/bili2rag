from __future__ import annotations

import unittest

from bilibili_harvester.bili_api import _comments_have_replies


class TestCommentsSnapshot(unittest.TestCase):
    def test_comments_have_replies_false_for_empty_bvid_response(self) -> None:
        comments = {
            "pages": [
                {
                    "code": 0,
                    "data": {
                        "replies": None,
                    },
                }
            ]
        }

        self.assertFalse(_comments_have_replies(comments))

    def test_comments_have_replies_true_when_any_page_has_replies(self) -> None:
        comments = {
            "pages": [
                {"code": 0, "data": {"replies": None}},
                {"code": 0, "data": {"replies": [{"rpid": 1}]}},
            ]
        }

        self.assertTrue(_comments_have_replies(comments))


if __name__ == "__main__":
    unittest.main()
