from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from bilibili_asr.asr import ASRConfig, transcribe_bvid_dir
from bilibili_harvester.cli import harvest_one
from bilibili_harvester.utils import ensure_dir
from bilibili_library.exporter import export_bvid
from bilibili_library.naming import sanitize_component

from .search import VideoFilter, search_videos, utc_now_compact, write_jsonl, write_targets_txt
from .session import build_web_session
from .seed import fetch_owner_info_by_bvid
from .space import list_space_videos, fetch_space_video_page
from .wbi import extract_wbi_keys_from_nav_json


def _print_utf8(line: str) -> None:
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Bilibili Search/Discover CLI (WBI signed)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    search = sub.add_parser("search", help="搜索视频并导出 targets.txt / results.jsonl")
    search.add_argument("--keyword", required=True, help="搜索关键词")
    search.add_argument("--order", default="click", help="totalrank|click|pubdate|dm|stow|scores")
    search.add_argument("--duration", type=int, default=0, help="时长筛选：0/1/2/3/4（见 docs）")
    search.add_argument("--tids", type=int, default=0, help="分区 tid（0=全部）")
    search.add_argument("--limit", type=int, default=50, help="最多返回条目数（0=不限制）")
    search.add_argument("--max-pages", type=int, default=5, help="最多翻页数（0=不限制）")
    search.add_argument("--page-sleep", type=float, default=1.0, help="翻页间隔秒")
    search.add_argument("--cookies", default="cookie.txt", help="Cookie 文件路径（可选）")
    search.add_argument("--proxy", default=None, help="HTTP(S) 代理，如 http://127.0.0.1:7890")
    search.add_argument("--out-dir", default="discoveries", help="输出目录（默认 discoveries/）")
    search.add_argument("--min-play", type=int, default=0)
    search.add_argument("--min-favorites", type=int, default=0)
    search.add_argument("--min-review", type=int, default=0)
    search.add_argument("--min-danmaku", type=int, default=0)
    search.add_argument("--min-seconds", type=int, default=0, help="最小时长（秒）")
    search.add_argument("--max-seconds", type=int, default=0, help="最大时长（秒）")
    search.add_argument("--require-mid", type=int, default=0, help="只保留指定 UP(mid) 的视频（0=不限制）")
    search.add_argument("--exclude", action="append", default=[], help="排除包含该词的标题/作者（可重复）")

    pipe = sub.add_parser("pipeline", help="搜索→采集→转写→导出可读命名目录（默认 fail-fast）")
    pipe.add_argument("--keyword", required=True, help="搜索关键词")
    pipe.add_argument("--order", default="click")
    pipe.add_argument("--duration", type=int, default=0)
    pipe.add_argument("--tids", type=int, default=0)
    pipe.add_argument("--limit", type=int, default=10)
    pipe.add_argument("--max-pages", type=int, default=5)
    pipe.add_argument("--page-sleep", type=float, default=1.0)
    pipe.add_argument("--cookies", default="cookie.txt")
    pipe.add_argument("--proxy", default=None)
    pipe.add_argument("--out-dir", default="discoveries")
    pipe.add_argument("--output-root", default="output")
    pipe.add_argument("--library-root", default="library")
    pipe.add_argument("--download", default="audio,subtitles,cover", help="采集下载项：video,audio,subtitles,cover,none")
    pipe.add_argument("--comment-pages", type=int, default=1)
    pipe.add_argument("--asr-device", default="cpu", help="cpu|cuda")
    pipe.add_argument("--asr-compute", default="int8", help="int8|float16|float32|auto")
    pipe.add_argument("--asr-model", default="auto")
    pipe.add_argument("--asr-lang", default=None)
    pipe.add_argument("--if-exists", choices=["fail", "skip", "overwrite"], default="skip")
    pipe.add_argument("--min-play", type=int, default=0)
    pipe.add_argument("--min-favorites", type=int, default=0)
    pipe.add_argument("--min-review", type=int, default=0)
    pipe.add_argument("--min-danmaku", type=int, default=0)
    pipe.add_argument("--min-seconds", type=int, default=0)
    pipe.add_argument("--max-seconds", type=int, default=0)
    pipe.add_argument("--require-mid", type=int, default=0)
    pipe.add_argument("--exclude", action="append", default=[])

    up = sub.add_parser("uploader", help="列举某 UP（mid）的投稿视频并导出 targets/results（可选 pipeline）")
    up.add_argument("--mid", type=int, default=0, help="UP 主 mid（与 --seed-bvid 二选一）")
    up.add_argument("--seed-bvid", default="", help="从种子视频 BV 号推导出 UP 主 mid（与 --mid 二选一）")
    up.add_argument("--order", default="pubdate", help="pubdate|click|stow")
    up.add_argument("--tid", type=int, default=0, help="分区 tid（0=不限制）")
    up.add_argument("--keyword", default="", help="可选：仅筛选包含该关键词的投稿（UP 空间接口自带）")
    up.add_argument("--limit", type=int, default=50, help="最多返回条目数（0=不限制）")
    up.add_argument("--max-pages", type=int, default=10, help="最多翻页数（0=不限制）")
    up.add_argument("--page-sleep", type=float, default=1.0, help="翻页间隔秒")
    up.add_argument("--ps", type=int, default=30, help="每页项数（默认 30）")
    up.add_argument("--cookies", default="cookie.txt", help="Cookie 文件路径（可选）")
    up.add_argument("--proxy", default=None, help="HTTP(S) 代理，如 http://127.0.0.1:7890")
    up.add_argument("--out-dir", default="discoveries", help="输出目录（默认 discoveries/）")
    up.add_argument("--pipeline", action="store_true", help="启用：采集→ASR→导出（同 pipeline 命令）")
    up.add_argument("--output-root", default="output")
    up.add_argument("--library-root", default="library")
    up.add_argument("--download", default="audio,subtitles,cover", help="采集下载项：video,audio,subtitles,cover,none")
    up.add_argument("--comment-pages", type=int, default=1)
    up.add_argument("--asr-device", default="cpu")
    up.add_argument("--asr-compute", default="int8")
    up.add_argument("--asr-model", default="auto")
    up.add_argument("--asr-lang", default=None)
    up.add_argument("--if-exists", choices=["fail", "skip", "overwrite"], default="skip")

    args = parser.parse_args(argv)

    cookie_path = Path(args.cookies)
    cookiefile = cookie_path if cookie_path.exists() else None
    sess, cookie_brief = build_web_session(cookiefile, proxy=getattr(args, "proxy", None))

    # uploader list mode (do it early to avoid coupling with search-only args)
    if args.cmd == "uploader":
        if bool(args.mid) == bool(args.seed_bvid):
            _print_utf8("请指定 --mid 或 --seed-bvid（二选一）")
            sys.exit(2)

        owner = None
        mid = int(args.mid or 0)
        if args.seed_bvid:
            try:
                owner = fetch_owner_info_by_bvid(sess, args.seed_bvid.strip())
                mid = owner.mid
            except Exception as e:
                _print_utf8(f"[FAIL] seed-bvid resolve mid: {e}")
                sys.exit(1)

        # Fetch wbi keys via nav (same as search flow)
        nav = sess.get("https://api.bilibili.com/x/web-interface/nav", timeout=15)
        nav.raise_for_status()
        keys = extract_wbi_keys_from_nav_json(nav.json())

        uploader_name = owner.name if owner is not None else str(mid)
        safe_name = sanitize_component(uploader_name, max_len=60)
        run_id = f"{utc_now_compact()}_up_{mid}_{safe_name}".strip("_")
        out_dir = Path(args.out_dir) / run_id

        items = list_space_videos(
            sess,
            keys,
            mid=mid,
            order=args.order,
            tid=args.tid,
            keyword=args.keyword,
            limit=args.limit,
            max_pages=args.max_pages,
            page_sleep=args.page_sleep,
            ps=args.ps,
        )

        results_path = out_dir / "results.jsonl"
        targets_path = out_dir / "targets.txt"
        write_jsonl(
            results_path,
            [
                {
                    "bvid": it.bvid,
                    "title": it.title,
                    "author": it.author,
                    "mid": it.mid,
                    "play": it.play,
                    "created": it.created,
                    "length": it.length_text,
                    "length_seconds": it.length_seconds,
                }
                for it in items
            ],
        )
        # reuse targets writer shape: 【title】url
        out_dir.mkdir(parents=True, exist_ok=True)
        with targets_path.open("w", encoding="utf-8") as f:
            for it in items:
                f.write(f"【{it.title}】https://www.bilibili.com/video/{it.bvid}\n")
        _print_utf8(f"Uploader saved: {results_path} / {targets_path}  items={len(items)}  mid={mid}  cookie_mode={cookie_brief.mode}")

        if not args.pipeline:
            sys.exit(0)

        out_root = ensure_dir(args.output_root)
        download_set = set([x.strip() for x in args.download.split(",") if x.strip() and x.strip() != "none"])
        cfg = ASRConfig(model=args.asr_model, device=args.asr_device, compute_type=args.asr_compute, language=args.asr_lang)
        for it in items:
            url = f"https://www.bilibili.com/video/{it.bvid}"
            _print_utf8(f"[HARVEST] {it.bvid} {it.title}")
            harvest_one(
                url,
                cookiefile=cookiefile,
                out_root=out_root,
                structured_root=out_root,
                download_set=download_set,
                proxy=args.proxy,
                comment_pages=args.comment_pages,
            )
            bvid_dir = (Path(args.output_root) / it.bvid).resolve()
            _print_utf8(f"[ASR] {it.bvid}")
            transcribe_bvid_dir(bvid_dir, cfg, merge_pages=True, fail_fast=True)
            _print_utf8(f"[EXPORT] {it.bvid}")
            export_bvid(
                it.bvid,
                output_root=Path(args.output_root),
                library_root=Path(args.library_root),
                if_exists=args.if_exists,
                dry_run=False,
            )

        sys.exit(0)

    flt = VideoFilter(
        min_play=args.min_play,
        min_favorites=args.min_favorites,
        min_review=args.min_review,
        min_danmaku=args.min_danmaku,
        min_duration_seconds=args.min_seconds,
        max_duration_seconds=args.max_seconds,
        require_mid=args.require_mid,
        exclude_text=tuple(args.exclude or ()),
    )

    safe_kw = sanitize_component(args.keyword, max_len=80)
    run_id = f"{utc_now_compact()}_{safe_kw}".strip()
    out_dir = Path(args.out_dir) / run_id

    items = search_videos(
        sess,
        keyword=args.keyword,
        order=args.order,
        duration=args.duration,
        tids=args.tids,
        limit=args.limit,
        max_pages=args.max_pages,
        page_sleep=args.page_sleep,
        flt=flt,
    )

    results_path = out_dir / "results.jsonl"
    targets_path = out_dir / "targets.txt"
    write_jsonl(
        results_path,
        [
            {
                "bvid": it.bvid,
                "title": it.title_plain,
                "author": it.author,
                "mid": it.mid,
                "play": it.play,
                "favorites": it.favorites,
                "review": it.review,
                "danmaku": it.danmaku,
                "pubdate": it.pubdate,
                "duration": it.duration_text,
                "duration_seconds": it.duration_seconds,
            }
            for it in items
        ],
    )
    write_targets_txt(targets_path, items)
    _print_utf8(f"Search saved: {results_path} / {targets_path}  items={len(items)}  cookie_mode={cookie_brief.mode}")

    if args.cmd == "search":
        return

    # pipeline
    out_root = ensure_dir(args.output_root)
    download_set = set([x.strip() for x in args.download.split(",") if x.strip() and x.strip() != "none"])
    cfg = ASRConfig(model=args.asr_model, device=args.asr_device, compute_type=args.asr_compute, language=args.asr_lang)

    for it in items:
        url = f"https://www.bilibili.com/video/{it.bvid}"
        _print_utf8(f"[HARVEST] {it.bvid} {it.title_plain}")
        harvest_one(
            url,
            cookiefile=cookiefile,
            out_root=out_root,
            structured_root=out_root,
            download_set=download_set,
            proxy=args.proxy,
            comment_pages=args.comment_pages,
        )

        bvid_dir = (Path(args.output_root) / it.bvid).resolve()
        _print_utf8(f"[ASR] {it.bvid}")
        transcribe_bvid_dir(bvid_dir, cfg, merge_pages=True, fail_fast=True)

        _print_utf8(f"[EXPORT] {it.bvid}")
        export_bvid(
            it.bvid,
            output_root=Path(args.output_root),
            library_root=Path(args.library_root),
            if_exists=args.if_exists,
            dry_run=False,
        )


if __name__ == "__main__":
    main()
