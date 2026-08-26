from __future__ import annotations

import pytest

from bilibili_harvester.bili_api import parse_danmaku_xml


def test_parse_danmaku_xml_preserves_timeline_and_content() -> None:
    parsed = parse_danmaku_xml('<i><d p="12.5,1,25,16777215,1710000000,0,abc,42">hello</d></i>')

    assert parsed == {
        "schema_version": 1,
        "items": [
            {
                "progress_seconds": 12.5,
                "mode": 1,
                "font_size": 25,
                "color": 16777215,
                "sent_at": 1710000000,
                "pool": 0,
                "sender_hash": "abc",
                "dmid": "42",
                "text": "hello",
            }
        ],
    }


def test_parse_danmaku_xml_rejects_incomplete_position_metadata() -> None:
    with pytest.raises(ValueError, match="invalid p attribute"):
        parse_danmaku_xml('<i><d p="12.5,1">hello</d></i>')
