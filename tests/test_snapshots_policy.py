from __future__ import annotations

import json
from pathlib import Path

from bilibili_get.snapshots_policy import resolve_snapshots_mode


def test_resolve_snapshots_mode_smart_matches_daotu(tmp_path: Path) -> None:
    bvid_dir = tmp_path / "BVxxxx"
    (bvid_dir / "json").mkdir(parents=True)
    (bvid_dir / "json" / "metadata.json").write_text(
        json.dumps({"title": "价值观解析导图系统解析价值观"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    d = resolve_snapshots_mode("smart", bvid_dir=bvid_dir)
    assert d.effective == "auto"
    assert d.reason == "title_match"


def test_resolve_snapshots_mode_smart_no_match(tmp_path: Path) -> None:
    bvid_dir = tmp_path / "BVxxxx"
    (bvid_dir / "json").mkdir(parents=True)
    (bvid_dir / "json" / "metadata.json").write_text(json.dumps({"title": "普通视频"}, ensure_ascii=False) + "\n", encoding="utf-8")
    d = resolve_snapshots_mode("smart", bvid_dir=bvid_dir)
    assert d.effective == "none"
    assert d.reason == "title_no_match"
