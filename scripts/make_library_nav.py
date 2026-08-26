from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _print_utf8(line: str) -> None:
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))


def _make_junction(link: Path, target: Path) -> None:
    # Windows junction: no admin rights needed; links to directories only.
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        check=True,
        capture_output=True,
    )


def _remove_junction_dir(path: Path) -> None:
    # Only remove the link itself, never the target content.
    if path.is_symlink():
        path.unlink()
    elif path.is_junction():
        path.rmdir()
    elif path.is_dir():
        raise RuntimeError(f"refusing to recursively delete non-junction dir: {path}")
    elif path.exists():
        path.unlink()


def build_by_category(library_root: Path, catalog: Dict[str, Any]) -> Dict[str, int]:
    cat_by_cid = {c["cid"]: c["name"] for c in catalog.get("categories", [])}
    by_cat_root = library_root / "_by_category"

    # Clean rebuild: remove previous junctions only.
    if by_cat_root.exists():
        for cat_dir in by_cat_root.iterdir():
            if not cat_dir.is_dir():
                continue
            for link in cat_dir.iterdir():
                if link.is_dir() and not link.is_symlink() and not link.is_junction():
                    raise RuntimeError(f"unexpected non-junction dir under _by_category: {link}")
                _remove_junction_dir(link)
            cat_dir.rmdir()

    counts: Dict[str, int] = {}
    for u in catalog.get("uploaders", []):
        name = u.get("uploader")
        up_dir = library_root / str(name)
        if not name or not up_dir.is_dir():
            continue
        cid = u.get("primary_category") or "other"
        cat_name = cat_by_cid.get(cid, cid)
        cat_dir = by_cat_root / cat_name
        cat_dir.mkdir(parents=True, exist_ok=True)
        link = cat_dir / str(name)
        if link.exists():
            continue
        _make_junction(link, up_dir.resolve())
        counts[cat_name] = counts.get(cat_name, 0) + 1
    return counts


def render_index_md(catalog: Dict[str, Any], counts: Dict[str, int]) -> str:
    cat_by_cid = {c["cid"]: c for c in catalog.get("categories", [])}
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for u in catalog.get("uploaders", []):
        cid = u.get("primary_category") or "other"
        grouped.setdefault(cid, []).append(u)

    totals = catalog.get("totals") or {}
    lines: List[str] = []
    lines.append("# Library 索引（UP 主 → 主题分类）")
    lines.append("")
    lines.append(
        f"共 {totals.get('uploaders', '?')} 个 UP 主 / {totals.get('videos', '?')} 个视频。"
        "文件树可按分类浏览：`library/_by_category/<分类>/<UP主>/`（junction，不占空间）。"
    )
    lines.append("本文件由 `python scripts/make_library_nav.py` 生成，配合 `scripts/build_uploader_catalog.py` 使用。")
    lines.append("")

    def sort_key(u: Dict[str, Any]) -> Any:
        return -int(u.get("video_count") or 0)

    for c in catalog.get("categories", []):
        cid = c["cid"]
        items = sorted(grouped.get(cid, []), key=sort_key)
        if not items:
            continue
        lines.append(f"## {c['name']}（{len(items)} 个 UP 主）")
        if c.get("desc"):
            lines.append(f"- {c['desc']}")
        lines.append("")
        for u in items:
            n = int(u.get("video_count") or 0)
            sec = u.get("secondary_categories") or []
            sec_txt = f"（副：{','.join(sec)}）" if sec else ""
            export_hint = " ⭐导出推荐" if u.get("export_recommended") else ""
            lines.append(f"- **{u['uploader']}**  {n} 个视频{sec_txt}{export_hint}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="生成 library 导航：_by_category/ 分类 junction（文件树按主题浏览）+ _catalog/INDEX.md。"
    )
    parser.add_argument("--library-root", default="library")
    parser.add_argument("--catalog-json", default="library/_catalog/uploader_catalog.json")
    parser.add_argument("--out-index", default="library/_catalog/INDEX.md")
    args = parser.parse_args(argv)

    library_root = Path(args.library_root).resolve()
    catalog_path = Path(args.catalog_json)
    if not catalog_path.exists():
        _print_utf8(f"未找到 catalog：{catalog_path}，请先运行 python scripts/build_uploader_catalog.py")
        sys.exit(2)

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    counts = build_by_category(library_root, catalog)
    index_md = render_index_md(catalog, counts)
    out_index = Path(args.out_index)
    out_index.parent.mkdir(parents=True, exist_ok=True)
    out_index.write_text(index_md, encoding="utf-8")

    total_links = sum(counts.values())
    _print_utf8(f"[OK] categories={len(counts)} junctions={total_links} -> {library_root / '_by_category'}")
    _print_utf8(f"[OK] index -> {out_index}")


if __name__ == "__main__":
    main()
