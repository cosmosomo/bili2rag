from __future__ import annotations

from bilibili_harvester.struct import normalize_comments


def test_normalize_comments_preserves_nested_reply_relationships() -> None:
    raw = {
        "pages": [
            {
                "data": {
                    "replies": [
                        {
                            "rpid": 10,
                            "member": {"mid": "1", "uname": "root"},
                            "content": {"message": "first"},
                            "ctime": 100,
                            "like": 5,
                            "rcount": 1,
                            "replies": [
                                {
                                    "rpid": 11,
                                    "member": {"mid": 2, "uname": "child"},
                                    "content": {"message": "second"},
                                    "ctime": 101,
                                    "like": 1,
                                }
                            ],
                        }
                    ]
                }
            }
        ]
    }

    normalized = normalize_comments(raw)

    assert normalized["source_pages"] == 1
    assert normalized["comments"] == [
        {
            "rpid": 10,
            "root_rpid": 10,
            "parent_rpid": None,
            "depth": 0,
            "page": 1,
            "author": {"mid": 1, "name": "root"},
            "message": "first",
            "ctime": 100,
            "like": 5,
            "reply_count": 1,
        },
        {
            "rpid": 11,
            "root_rpid": 10,
            "parent_rpid": 10,
            "depth": 1,
            "page": 1,
            "author": {"mid": 2, "name": "child"},
            "message": "second",
            "ctime": 101,
            "like": 1,
            "reply_count": None,
        },
    ]
