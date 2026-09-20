"""Post-hoc comments backfill via the OpenCLI bridge (optional dependency).

bili2rag's harvester captures only the first-page hot comments (server-side
is_end truncation). OpenCLI's `comments` uses the official API and can read
楼中楼 via --parent. This script walks the library and, for videos whose
comments.txt is missing or empty, fetches comments through OpenCLI and
writes:

  json/comments_opencli.json   raw items + provenance (source/fetched_at)
  comments.txt                 plain-text snapshot (作者: 文本, 楼中楼缩进)
                               — never overwrites a non-empty file unless --force

The main harvest chain is untouched (post-hoc enrichment only); manifest is
rewritten after each video so sha256 stays trustworthy. Never fakes data:
failures are recorded and the run continues.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bilibili_library.completion import build_video_index  # noqa: E402
from bilibili_library.exporter import rewrite_manifest  # noqa: E402
from bilibili_opencli import bridge  # noqa: E402


def _comments_txt(items: List[dict], deep: Dict[str, List[dict]]) -> str:
    lines: List[str] = []
    for it in items:
        author = str(it.get("author") or "匿名").strip()
        text = str(it.get("text") or "").strip().replace("\n", " ")
        lines.append(f"{author}: {text}")
        for sub in deep.get(str(it.get("rpid")), []):
            s_author = str(sub.get("author") or "匿名").strip()
            s_text = str(sub.get("text") or "").strip().replace("\n", " ")
            lines.append(f"  {s_author}: {s_text}")
    return "\n".join(lines) + ("\n" if lines else "")


def _needs_backfill(vdir: Path) -> bool:
    c = vdir / "comments.txt"
    return (not c.exists()) or (c.stat().st_size == 0)


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="用 OpenCLI 官方评论接口回填库内评论缺口（后置增强，不改主链）")
    ap.add_argument("--library-root", default="library")
    ap.add_argument("--bvid", action="append", default=[], help="只处理指定 bvid（可重复）")
    ap.add_argument("--force", action="store_true", help="comments.txt 非空也覆盖（默认只补缺失/空文件）")
    ap.add_argument("--deep", type=int, default=0, help="对前 N 条主评抓楼中楼（默认 0=不抓，N>0 时逐条 --parent）")
    ap.add_argument("--limit", type=int, default=0, help="本次最多处理多少条（0=不限制）")
    ap.add_argument("--dry-run", action="store_true", help="只列出将要回填的视频，不调用 OpenCLI")
    args = ap.parse_args(argv)

    if not bridge.available():
        print("[OPENCLI] 未检测到 opencli。安装：npm install -g @jackwener/opencli 并保持浏览器扩展在线。")
        return 2

    idx = build_video_index(Path(args.library_root))
    if args.bvid:
        pairs: List[Tuple[str, Path]] = []
        for b in args.bvid:
            d = idx.get(b)
            if d is None:
                print(f"[SKIP] {b} 不在库中")
            else:
                pairs.append((b, d))
    else:
        pairs = sorted(idx.items(), key=lambda kv: str(kv[1]))

    todo = [(b, d) for b, d in pairs if args.force or _needs_backfill(d)]
    if args.limit and int(args.limit) > 0:
        todo = todo[: int(args.limit)]

    print(f"[PLAN] 库={len(idx)} 待回填={len(todo)} force={bool(args.force)} deep={int(args.deep)}")
    if args.dry_run:
        for b, d in todo:
            print(f"  [DRY] {b}  {d.name}")
        return 0

    ok = empty = failed = 0
    for bvid, vdir in todo:
        try:
            items = bridge.comments(bvid)
        except bridge.BridgeError as e:
            if e.adapter_code == "EMPTY_RESULT":
                # The adapter exits 66 with EMPTY_RESULT when a channel has no
                # data — for comments that means the video genuinely has zero
                # comments (verified against the raw API count on 2026-09-20).
                print(f"  [EMPTY] {bvid} 无评论（EMPTY_RESULT）")
                empty += 1
            else:
                print(f"  [FAIL] {bvid}: {e}")
                failed += 1
            continue
        if not items:
            print(f"  [EMPTY] {bvid} 无评论")
            empty += 1
            continue

        deep: Dict[str, List[dict]] = {}
        if args.deep and int(args.deep) > 0:
            for it in items[: int(args.deep)]:
                rpid = str(it.get("rpid") or "")
                if rpid and int(it.get("replies") or 0) > 0:
                    try:
                        deep[rpid] = bridge.comments(bvid, parent=rpid)
                    except bridge.BridgeError as e:
                        print(f"  [WARN] {bvid} 楼中楼 {rpid} 失败: {e}")

        (vdir / "json").mkdir(parents=True, exist_ok=True)
        (vdir / "json" / "comments_opencli.json").write_text(
            json.dumps(
                {"source": "opencli:bilibili.comments", "fetched_at": bridge.fetch_timestamp(), "bvid": bvid,
                 "items": items, "deep": deep},
                ensure_ascii=False, indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        txt_path = vdir / "comments.txt"
        if args.force or _needs_backfill(vdir):
            txt_path.write_text(_comments_txt(items, deep), encoding="utf-8")
        try:
            rewrite_manifest(vdir)
        except Exception as e:  # manifest rewrite must not abort the walk
            print(f"  [WARN] {bvid} manifest 重写失败: {e}")
        ok += 1
        print(f"  [OK] {bvid} 主评={len(items)} 楼中楼={sum(len(v) for v in deep.values())} -> {vdir.name}")

    print(f"[DONE] ok={ok} empty={empty} failed={failed} total={len(todo)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
