from __future__ import annotations

import unittest

from bilibili_library.naming import build_library_dirname, sanitize_component


class TestNaming(unittest.TestCase):
    def test_sanitize_preserves_unicode_and_digits(self) -> None:
        self.assertEqual(sanitize_component("硅谷101"), "硅谷101")

    def test_sanitize_replaces_invalid_chars(self) -> None:
        self.assertEqual(sanitize_component('a:b'), "a_b")
        self.assertEqual(sanitize_component("a/b"), "a_b")
        self.assertEqual(sanitize_component("a\\b"), "a_b")
        self.assertEqual(sanitize_component("a?b*"), "a_b_")

    def test_sanitize_trims_trailing_dot_and_space(self) -> None:
        self.assertEqual(sanitize_component("abc. "), "abc")

    def test_sanitize_reserved_names(self) -> None:
        self.assertEqual(sanitize_component("CON"), "CON_")
        self.assertEqual(sanitize_component("lpt1"), "lpt1_")

    def test_build_library_dirname_truncates_title(self) -> None:
        info = {"title": "A" * 400, "upload_date": "20260112"}
        bvid = "BV1Hgr8BpE59"
        out = build_library_dirname(info, bvid, max_len=160)
        self.assertTrue(out.endswith(f"_{bvid}"))
        self.assertLessEqual(len(out), 160)


if __name__ == "__main__":
    unittest.main()

