from __future__ import annotations

import json
from pathlib import Path

from scripts.build_uploader_catalog import main


def _mk_video_dir(lib: Path, uploader: str, dirname: str) -> Path:
    p = lib / uploader / dirname
    p.mkdir(parents=True, exist_ok=True)
    return p


def test_build_uploader_catalog_smoke(tmp_path: Path) -> None:
    lib = tmp_path / "library"
    _mk_video_dir(lib, "UP_A", "2026-01-01_情绪管理：如何减少内耗_BV1AAAAAA")
    _mk_video_dir(lib, "UP_A", "2026-01-02_自我效能感：如何建立掌控感_BV1AAAAAB")
    _mk_video_dir(lib, "UP_B", "2026-01-03_通胀与利率：资产配置思路_BV1BBBBBB")

    out_json = tmp_path / "out.json"
    out_md = tmp_path / "out.md"
    main(
        [
            "--library-root",
            str(lib),
            "--out-json",
            str(out_json),
            "--out-md",
            str(out_md),
            "--min-videos-for-export",
            "2",
        ]
    )

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["totals"]["uploaders"] == 2
    assert payload["totals"]["videos"] == 3

    ups = {u["uploader"]: u for u in payload["uploaders"]}
    assert ups["UP_A"]["primary_category"] in ("psych_self", "other")
    assert ups["UP_A"]["export_recommended"] is True
    assert ups["UP_B"]["primary_category"] in ("money_invest", "macro_econ", "other")

