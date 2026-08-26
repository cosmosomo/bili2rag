from __future__ import annotations

import json
from pathlib import Path

from bilibili_library.exporter import export_bvid


def test_export_writes_nfo_and_preserves_cover_and_normalized_comments(tmp_path: Path) -> None:
    bvid = "BV1TEST12345"
    source_dir = tmp_path / "output" / bvid
    json_dir = source_dir / "json"
    json_dir.mkdir(parents=True)
    (json_dir / "metadata.json").write_text(
        json.dumps(
            {
                "id": bvid,
                "title": "测试视频",
                "description": "测试简介",
                "uploader": "测试UP",
                "upload_date": "20260101",
                "duration": 125,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (json_dir / "comments_normalized.json").write_text('{"source_pages": 0, "comments": []}', encoding="utf-8")
    (source_dir / "danmaku.json").write_text('{"schema_version": 1, "items": []}', encoding="utf-8")
    (source_dir / "cover.png").write_bytes(b"cover")

    result = export_bvid(bvid, output_root=tmp_path / "output", library_root=tmp_path / "library", if_exists="overwrite")

    nfo = (result.dest_dir / "movie.nfo").read_text(encoding="utf-8")
    assert "<title>测试视频</title>" in nfo
    assert "<uniqueid type=\"bilibili\" default=\"true\">BV1TEST12345</uniqueid>" in nfo
    assert (result.dest_dir / "cover.png").read_bytes() == b"cover"
    assert (result.dest_dir / "json" / "comments_normalized.json").exists()
    assert (result.dest_dir / "danmaku.json").exists()
