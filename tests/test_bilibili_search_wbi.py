from __future__ import annotations

import unittest

from bilibili_search.wbi import WbiKeys, enc_wbi_params


class TestWbiSigning(unittest.TestCase):
    def test_enc_wbi_params_matches_doc_example(self) -> None:
        # Deterministic example: fix keys + wts to validate signer output.
        keys = WbiKeys(
            img_key="7cd084941338484aae1ad9425b84077c",
            sub_key="4932caff0ff746eab6f01bf08b70ac45",
        )
        signed = enc_wbi_params(
            {"foo": "114", "bar": "514", "baz": 1919810},
            keys,
            now=1702204169,
        )
        self.assertEqual(signed["wts"], "1702204169")
        # md5("bar=514&baz=1919810&foo=114&wts=1702204169" + mixin_key)
        # mixin_key for these keys is: ea1db124af3c7062474693fa704f4ff8
        self.assertEqual(signed["w_rid"], "6149fdadf571698ca7e6a567265cd0ee")


if __name__ == "__main__":
    unittest.main()
