from __future__ import annotations

import json
from pathlib import Path

from bilibili_library.exporter import export_bvid


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False) + "\n", encoding="utf-8")


def test_export_includes_subtitles_and_pages_tree(tmp_path: Path) -> None:
    output_root = tmp_path / "output"
    library_root = tmp_path / "library"
    bvid = "BV1TEST12345"

    src = output_root / bvid
    _write_json(
        src / "json" / "metadata.json",
        {
            "id": bvid,
            "bvid": bvid,
            "title": "测试视频",
            "uploader": "测试UP",
            "upload_date": "20260101",
            "duration": 1,
        },
    )
    _write_json(src / "json" / "core.json", {})
    _write_json(src / "json" / "streams.json", {})
    _write_json(src / "json" / "subtitles_index.json", {})
    _write_json(src / "json" / "comments.json", {})
    _write_json(src / "json" / "comments_structured.json", {})

    (src / "subtitles").mkdir(parents=True, exist_ok=True)
    (src / "subtitles" / "danmaku.xml").write_text("<i/>", encoding="utf-8")

    (src / "pages" / "p01_123").mkdir(parents=True, exist_ok=True)
    (src / "pages" / "p01_123" / "audio.m4a").write_bytes(b"fake")

    (src / "json" / "pages").mkdir(parents=True, exist_ok=True)
    _write_json(src / "json" / "pages" / "info_p01_123.json", {"cid": 123})
    _write_json(src / "json" / "pages_index.json", {"pages": [{"cid": 123}]})

    r = export_bvid(bvid, output_root=output_root, library_root=library_root, if_exists="overwrite", dry_run=False)

    # Tree copies
    assert (r.dest_dir / "subtitles" / "danmaku.xml").exists()
    assert (r.dest_dir / "pages" / "p01_123" / "audio.m4a").exists()
    assert (r.dest_dir / "json" / "pages" / "info_p01_123.json").exists()
    assert (r.dest_dir / "json" / "pages_index.json").exists()

