from __future__ import annotations

import unittest

from bilibili_asr.aggregate import _to_simplified


class TestSimplify(unittest.TestCase):
    def test_to_simplified_basic(self) -> None:
        self.assertEqual(_to_simplified("從牢A回城看美式民主"), "从牢A回城看美式民主")


if __name__ == "__main__":
    unittest.main()

