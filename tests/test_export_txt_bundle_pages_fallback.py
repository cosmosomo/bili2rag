from __future__ import annotations

from pathlib import Path

from scripts.export_txt_bundle import VideoEntry, export_txt_bundle


def test_export_txt_bundle_pages_fallback(tmp_path: Path) -> None:
    exported = tmp_path / "UP" / "2026-01-01_title_BVxxxx"
    (exported / "pages" / "p01_0" / "asr").mkdir(parents=True)
    (exported / "pages" / "p02_0" / "asr").mkdir(parents=True)
    (exported / "pages" / "p01_0" / "asr" / "transcript.txt").write_text("第一页内容\n", encoding="utf-8")
    (exported / "pages" / "p02_0" / "asr" / "transcript.txt").write_text("第二页内容\n", encoding="utf-8")

    dest = tmp_path / "_exports"
    res = export_txt_bundle([VideoEntry(bvid="BVxxxx", exported_dir=exported)], dest_dir=dest, with_header=False, concat_all=True, if_missing="fail")
    assert res["missing"] == 0
    assert (dest / "ALL.txt").exists()
    txt = (dest / "ALL.txt").read_text(encoding="utf-8")
    assert "p01_0" in txt
    assert "第一页内容" in txt
    assert "p02_0" in txt
    assert "第二页内容" in txt
