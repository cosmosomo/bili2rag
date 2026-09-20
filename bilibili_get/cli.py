from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from bilibili_asr.asr import ASRConfig, transcribe_bvid_dir
from bilibili_harvester.cli import harvest_one
from bilibili_harvester.utils import ensure_dir, extract_bvid, extract_targets
from bilibili_library.exporter import export_bvid, iter_bvid_dirs

from .pipeline_state import load_or_create_state, set_stage, stage_succeeded


def _print_utf8(line: str) -> None:
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _add_batch_pipeline_args(parser: argparse.ArgumentParser) -> None:
    """Pipeline flags shared by grab-uploader / grab-targets (fed to the engine)."""
    parser.add_argument("--cookies", default="cookie.txt")
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--library-root", default="library")
    parser.add_argument("--download", default="audio,subtitles,cover")
    parser.add_argument("--comment-pages", type=int, default=1)
    parser.add_argument("--pbp", action="store_true", help="抓取高能进度条（PBP）到 json/pbp.json")
    parser.add_argument("--snapshots", default="smart", help="none|smart|auto|秒列表（如 10,60,120）")
    parser.add_argument("--snapshot-k", type=int, default=5)
    parser.add_argument("--no-asr", action="store_true")
    parser.add_argument("--no-export", action="store_true")
    parser.add_argument("--asr-device", default="cpu")
    parser.add_argument("--asr-compute", default="int8")
    parser.add_argument("--asr-model", default="auto")
    parser.add_argument("--asr-lang", default=None)
    parser.add_argument("--if-exists", choices=["fail", "skip", "overwrite"], default="skip")
    parser.add_argument("--prune-output", action="store_true", help="导出成功后删除 output/<bvid>/")
    parser.add_argument("--between-sleep", type=float, default=0.0)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--new-limit", type=int, default=0, help="本次最多处理多少条新增（0=不限制）")
    parser.add_argument("--include-unavailable", action="store_true", help="重试已标记 unavailable 的视频")
    parser.add_argument(
        "--keep-audio", action="store_true",
        help="禁用瘦身模式：即使已有中文字幕也照常下载音频（默认有 CC 字幕时跳过音频）",
    )
    parser.add_argument(
        "--fetch-workers", type=int, default=2,
        help="并行抓取子进程数（默认 2；对站点温和，勿超 3）",
    )
    parser.add_argument(
        "--no-defer-asr", action="store_true",
        help="关闭延迟转写（默认：抓取阶段不带 ASR，结束后单模型集中转写，速度更快）",
    )
    parser.add_argument("--out-dir", default="discoveries")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bilibili low-entropy entrypoint: run (URL→harvest→ASR→export), search, uploader, export."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="URL/BV → 采集（音频/字幕/评论/弹幕）→ ASR → 导出可读命名目录")
    run.add_argument("--url", action="append", default=[], help="URL/BV（可重复），例如 BV15JqABoEvj")
    run.add_argument("--targets", default=None, help="目标列表文件路径（每行 URL/BV，行内可带标题）")
    run.add_argument("--cookies", default="cookie.txt", help="Cookie 文件路径（默认 cookie.txt）")
    run.add_argument("--proxy", default=None, help="HTTP(S) 代理，如 http://127.0.0.1:7890")
    run.add_argument("--output-root", default="output", help="采集输出根目录（默认 output）")
    run.add_argument("--library-root", default="library", help="导出根目录（默认 library）")
    run.add_argument("--download", default="audio,subtitles,cover", help="采集下载项：video,audio,subtitles,cover,none")
    run.add_argument("--comment-pages", type=int, default=1, help="评论抓取页数（默认 1）")
    run.add_argument("--pbp", action="store_true", help="抓取高能进度条（PBP）并保存到 json/pbp.json")
    run.add_argument(
        "--snapshots",
        default="smart",
        help=(
            "抓取视频快照：none|smart|auto|秒列表（如 10,60,120）。"
            "smart 会在标题含“导图/思维导图”时自动开启截图；auto 会根据 PBP 峰值自动选点（缺峰值会均匀取点）。"
        ),
    )
    run.add_argument("--snapshot-k", type=int, default=5, help="--snapshots auto 时选取的峰值数量（默认 5）")
    run.add_argument("--asr", action="store_true", help="启用 ASR（默认启用；配合 --no-asr 可关闭）")
    run.add_argument("--no-asr", action="store_true", help="关闭 ASR（仅采集+可选导出）")
    run.add_argument("--export", action="store_true", help="启用导出（默认启用；配合 --no-export 可关闭）")
    run.add_argument("--no-export", action="store_true", help="关闭导出（仅采集+可选 ASR）")
    run.add_argument("--keep-audio", action="store_true", help="禁用瘦身模式（有中文字幕仍下载音频）")
    run.add_argument("--if-exists", choices=["fail", "skip", "overwrite"], default="skip", help="导出目录已存在时策略")
    run.add_argument("--prune-output", action="store_true", help="导出成功后删除 output/<bvid>/，节省空间（以 library/ 为唯一真源）")
    run.add_argument("--asr-device", default="cpu", help="cpu|cuda（默认 cpu）")
    run.add_argument("--asr-compute", default="int8", help="int8|float16|float32|auto（默认 int8）")
    run.add_argument("--asr-model", default="auto", help="auto|模型名(base/small/...)|本地模型目录路径")
    run.add_argument("--asr-lang", default=None, help="可选：语言代码（如 zh、en）")
    run.add_argument("--fail-fast", action="store_true", help="遇到失败立即停止（默认继续处理并最后返回非零码）")
    run.add_argument("--resume", action="store_true", help="跳过状态成功且产物仍存在的 harvest、ASR、export 阶段")

    export = sub.add_parser("export", help="将 output/<bvid>/ 导出为可读命名目录（library/）")
    export.add_argument("--output-root", default="output")
    export.add_argument("--library-root", default="library")
    export.add_argument("--bvid", action="append", default=[], help="目标 bvid，可重复传入")
    export.add_argument("--all", action="store_true", help="导出 output-root 下全部 bvid 目录")
    export.add_argument("--if-exists", choices=["fail", "skip", "overwrite"], default="skip")

    search = sub.add_parser("search", help="关键词搜索并导出 targets/results（或 pipeline）")
    search.add_argument("--keyword", required=True)
    search.add_argument("--order", default="click")
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("--cookies", default="cookie.txt")
    search.add_argument("--proxy", default=None)
    search.add_argument("--out-dir", default="discoveries")
    search.add_argument("--pipeline", action="store_true", help="启用：采集→ASR→导出")
    search.add_argument("--output-root", default="output")
    search.add_argument("--library-root", default="library")
    search.add_argument("--download", default="audio,subtitles,cover")
    search.add_argument("--comment-pages", type=int, default=1)
    search.add_argument("--asr-device", default="cpu")
    search.add_argument("--asr-compute", default="int8")
    search.add_argument("--asr-model", default="auto")
    search.add_argument("--asr-lang", default=None)
    search.add_argument("--if-exists", choices=["fail", "skip", "overwrite"], default="skip")

    up = sub.add_parser("uploader", help="列出某 UP 的投稿视频并导出 targets/results（或 pipeline）")
    up.add_argument("--seed-bvid", default="", help="从种子 BV 推导 mid（与 --mid 二选一）")
    up.add_argument("--mid", type=int, default=0, help="UP 主 mid（与 --seed-bvid 二选一）")
    up.add_argument("--limit", type=int, default=50)
    up.add_argument("--order", default="pubdate")
    up.add_argument("--keyword", default="")
    up.add_argument("--cookies", default="cookie.txt")
    up.add_argument("--proxy", default=None)
    up.add_argument("--out-dir", default="discoveries")
    up.add_argument("--pipeline", action="store_true", help="启用：采集→ASR→导出")
    up.add_argument("--output-root", default="output")
    up.add_argument("--library-root", default="library")
    up.add_argument("--download", default="audio,subtitles,cover")
    up.add_argument("--comment-pages", type=int, default=1)
    up.add_argument("--asr-device", default="cpu")
    up.add_argument("--asr-compute", default="int8")
    up.add_argument("--asr-model", default="auto")
    up.add_argument("--asr-lang", default=None)
    up.add_argument("--if-exists", choices=["fail", "skip", "overwrite"], default="skip")

    grab_s = sub.add_parser(
        "grab-search",
        help="关键词搜索 → 引擎批量抓取（系统性调研一条命令：搜→筛→抓→转写）",
    )
    grab_s.add_argument("--keyword", required=True)
    grab_s.add_argument("--search-limit", type=int, default=30, help="每个关键词最多取多少条结果")
    grab_s.add_argument("--min-play", type=int, default=0, help="播放数下限过滤")
    grab_s.add_argument("--min-seconds", type=int, default=0, help="时长下限（秒）")
    grab_s.add_argument("--max-seconds", type=int, default=0, help="时长上限（秒，0=不限）")
    grab_s.add_argument("--require-all", default="", help="标题必须包含的词（逗号分隔，任一命中即可的用 --require-any）")
    grab_s.add_argument("--require-any", default="", help="标题至少包含其一的词（逗号分隔）")
    _add_batch_pipeline_args(grab_s)

    grab_hot = sub.add_parser(
        "grab-hot",
        help="热门/排行榜发现头（OpenCLI 桥接，可选依赖）→ 引擎批量抓取",
    )
    grab_hot.add_argument("--source", choices=["hot", "ranking"], default="hot", help="发现源（默认 hot）")
    grab_hot.add_argument("--discover-limit", type=int, default=50, help="取榜单前 N 条（0=不限制）")
    grab_hot.add_argument("--require-all", default="", help="标题必须包含的词（逗号分隔）")
    grab_hot.add_argument("--require-any", default="", help="标题至少包含其一的词（逗号分隔）")
    _add_batch_pipeline_args(grab_hot)

    grab_up = sub.add_parser(
        "grab-uploader",
        help="增量抓取某 UP 主新投稿（space 发现 → 引擎逐条子进程采集）",
    )
    grab_up.add_argument("--seed-bvid", default="", help="从种子 BV 推导 mid（与 --mid 二选一）")
    grab_up.add_argument("--mid", type=int, default=0, help="UP 主 mid（与 --seed-bvid 二选一）")
    grab_up.add_argument("--order", default="pubdate")
    grab_up.add_argument("--tid", type=int, default=0)
    grab_up.add_argument("--keyword", default="")
    grab_up.add_argument("--ps", type=int, default=30)
    grab_up.add_argument("--page-sleep", type=float, default=0.5)
    grab_up.add_argument("--discover-limit", type=int, default=50, help="发现最近 N 条投稿（0=不限制）")
    grab_up.add_argument(
        "--since-days", type=int, default=0,
        help="只处理最近 N 天内的投稿（0=不限；周期性增量刷新用）",
    )
    _add_batch_pipeline_args(grab_up)

    grab_t = sub.add_parser(
        "grab-targets",
        help="按清单批量抓取（targets 文件 → 引擎逐条子进程采集；主题收集用 --name 命名）",
    )
    grab_t.add_argument("--targets-file", required=True, help="包含 URL/BV 的文本文件（任意格式）")
    grab_t.add_argument("--name", default="", help="运行名（用于 discoveries 目录命名）")
    _add_batch_pipeline_args(grab_t)

    ck = sub.add_parser(
        "cookie-refresh",
        help="从已登录浏览器刷新 cookie.txt（OpenCLI 桥接：document.cookie 合并；缺 SESSDATA 时自动弹扫码页补全）",
    )
    ck.add_argument("--cookies", default="cookie.txt")
    ck.add_argument("--qr", action="store_true", help="直接走扫码流程（首次设置或 SESSDATA 已失效时）")
    ck.add_argument("--no-backup", action="store_true", help="不备份旧 cookie.txt")
    ck.add_argument("--proxy", default=None)

    repair = sub.add_parser(
        "repair",
        help="修复库内缺口：就地补转写（有音频缺转写的目录）并重写 manifest；"
             "缺音频/缺目录的输出 targets_repair.txt 供 grab-targets 重抓（repair 自己不抓取）",
    )
    repair.add_argument("--library-root", default="library")
    repair.add_argument("--output-root", default="output", help="仅用于生成重抓命令的提示")
    repair.add_argument("--asr-device", default="cpu")
    repair.add_argument("--asr-compute", default="int8")
    repair.add_argument("--asr-model", default="auto")
    repair.add_argument("--asr-lang", default=None)
    repair.add_argument("--limit", type=int, default=0, help="本次最多就地转写多少条（0=不限制）")
    repair.add_argument("--dry-run", action="store_true", help="只列出将要修复的目录，不执行")
    repair.add_argument("--mark-unavailable", action="append", default=[], help="手动标记终态不可得（可重复）")
    repair.add_argument("--out-dir", default="discoveries", help="targets_repair.txt 输出目录")

    return parser


def _harvest_artifacts_exist(bvid_dir: Path) -> bool:
    return (bvid_dir / "json" / "metadata.json").exists() and (bvid_dir / "logs" / "harvest_run.json").exists()


def _asr_artifacts_exist(bvid_dir: Path) -> bool:
    return any(
        path.exists()
        for path in (
            bvid_dir / "asr" / "transcript.txt",
            bvid_dir / "asr" / "transcript_all.txt",
        )
    )


def _export_artifacts_exist(state: Dict[str, Any]) -> bool:
    detail = state.get("stages", {}).get("export", {}).get("detail", {})
    if not isinstance(detail, dict):
        return False
    dest_dir = detail.get("dest_dir")
    return isinstance(dest_dir, str) and Path(dest_dir).is_dir()


def _warn_if_cookie_stale(cookiefile: Optional[Path], proxy: Optional[str]) -> bool:
    """Return True if cookie is confirmed stale (and print a warning)."""
    if cookiefile is None or not cookiefile.exists():
        return False
    try:
        from bilibili_harvester.cookies import check_login_state, read_cookie_file, STALE_COOKIE_HINT

        header, _ = read_cookie_file(str(cookiefile))
        state = check_login_state(header, proxy=proxy)
    except Exception:
        return False
    if state is False:
        _print_utf8(f"[COOKIE WARN] {STALE_COOKIE_HINT}")
        return True
    return False


def _run_cmd(args: argparse.Namespace) -> int:
    cookie_path = Path(args.cookies)
    cookiefile = cookie_path if cookie_path.exists() else None
    out_root = ensure_dir(args.output_root)
    _warn_if_cookie_stale(cookiefile, args.proxy)

    urls: List[str] = []
    if args.url:
        urls.extend(extract_targets(args.url))
    if args.targets:
        targets_path = Path(args.targets)
        if not targets_path.exists():
            _print_utf8(f"未找到 targets 文件：{targets_path}")
            return 2
        urls.extend(extract_targets(targets_path.read_text(encoding="utf-8", errors="ignore").splitlines()))
    if not urls:
        _print_utf8("请通过 --url 或 --targets 提供目标")
        return 2

    urls = list(dict.fromkeys(urls))
    download_set = set([x.strip() for x in args.download.split(",") if x.strip() and x.strip() != "none"])

    do_asr = (not args.no_asr)  # default on
    if args.asr:
        do_asr = True
    do_export = (not args.no_export)  # default on
    if args.export:
        do_export = True

    cfg = ASRConfig(model=args.asr_model, device=args.asr_device, compute_type=args.asr_compute, language=args.asr_lang)

    failures = 0
    for u in urls:
        bvid = extract_bvid(u)
        bvid_dir = (out_root / bvid).resolve() if bvid else None
        state: Optional[Dict[str, Any]] = None
        if bvid and bvid_dir is not None:
            state = load_or_create_state(bvid_dir, bvid=bvid, source_url=u)

        try:
            if (
                args.resume
                and state is not None
                and bvid_dir is not None
                and stage_succeeded(state, "harvest")
                and _harvest_artifacts_exist(bvid_dir)
            ):
                r = {"bvid": bvid, "outdir": str(bvid_dir)}
                _print_utf8(f"[HARVEST RESUME] {bvid} -> {bvid_dir}")
            else:
                if state is not None and bvid_dir is not None:
                    set_stage(bvid_dir, state, "harvest", "running")
                    set_stage(bvid_dir, state, "asr", "pending")
                    set_stage(bvid_dir, state, "export", "pending")
                r = harvest_one(
                    u,
                    cookiefile=cookiefile,
                    out_root=out_root,
                    structured_root=out_root,
                    download_set=download_set,
                    proxy=args.proxy,
                    comment_pages=args.comment_pages,
                    keep_audio=bool(getattr(args, "keep_audio", False)),
                )
                bvid = str(r.get("bvid") or "unknown")
                bvid_dir = Path(str(r.get("outdir") or (out_root / bvid))).resolve()
                if state is None:
                    state = load_or_create_state(bvid_dir, bvid=bvid, source_url=u)
                set_stage(bvid_dir, state, "harvest", "succeeded", detail={"outdir": str(bvid_dir)})
                _print_utf8(f"[HARVEST OK] {bvid} -> {bvid_dir}")
        except Exception as e:
            if state is not None and bvid_dir is not None:
                set_stage(bvid_dir, state, "harvest", "failed", error=str(e))
            failures += 1
            _print_utf8(f"[HARVEST FAIL] {u} reason={e}")
            if args.fail_fast:
                return 1
            continue

        if bvid_dir is None or state is None:
            raise RuntimeError(f"pipeline state was not initialized for {u}")

        # Optional enrichment (PBP / videoshot snapshots)
        try:
            from .snapshots_policy import resolve_snapshots_mode

            decision = resolve_snapshots_mode(str(args.snapshots or "none"), bvid_dir=bvid_dir)
            snapshots_mode = decision.effective.strip().lower()
            do_enrich = bool(args.pbp) or snapshots_mode != "none"
            if do_enrich:
                from bilibili_enrich.enrich import enrich_bvid_dir

                cookie_path = Path(args.cookies)
                cookiefile2 = cookie_path if cookie_path.exists() else None
                rep = enrich_bvid_dir(
                    bvid_dir,
                    cookiefile=cookiefile2,
                    proxy=args.proxy,
                    pbp=bool(args.pbp),
                    snapshots=str(snapshots_mode),
                    snapshot_k=int(args.snapshot_k),
                )
                _print_utf8(
                    f"[ENRICH OK] {bvid} pbp={bool(rep.pbp_path)} videoshot={bool(rep.videoshot_path)} snapshots={len(rep.snapshots)}"
                )
        except Exception as e:
            failures += 1
            _print_utf8(f"[ENRICH FAIL] {bvid} reason={e}")
            if args.fail_fast:
                return 1

        if do_asr:
            try:
                if args.resume and stage_succeeded(state, "asr") and _asr_artifacts_exist(bvid_dir):
                    _print_utf8(f"[ASR RESUME] {bvid} -> existing transcript")
                else:
                    set_stage(bvid_dir, state, "asr", "running")
                    rep = transcribe_bvid_dir(bvid_dir, cfg, merge_pages=True, fail_fast=False)
                    set_stage(
                        bvid_dir,
                        state,
                        "asr",
                        "succeeded",
                        detail={"mode": rep.get("mode"), "merged": rep.get("merged")},
                    )
                    _print_utf8(f"[ASR OK] {bvid} mode={rep.get('mode')} merged={rep.get('merged')}")
            except Exception as e:
                set_stage(bvid_dir, state, "asr", "failed", error=str(e))
                failures += 1
                _print_utf8(f"[ASR FAIL] {bvid} reason={e}")
                msg = str(e)
                if "音频" in msg or "audio" in msg.lower():
                    _print_utf8(
                        f"[HINT] {bvid} 音频缺失常见原因：412 反爬（运行 python -m pip install -U yt-dlp 后重试）/"
                        f"充电专属或已删除视频/网络抖动。采集产物已保留在 {bvid_dir}"
                    )
                if args.fail_fast:
                    return 1
                continue
        else:
            set_stage(bvid_dir, state, "asr", "skipped", detail={"reason": "--no-asr"})

        if do_export:
            try:
                if args.resume and stage_succeeded(state, "export") and _export_artifacts_exist(state):
                    _print_utf8(f"[EXPORT RESUME] {bvid} -> existing library directory")
                else:
                    set_stage(bvid_dir, state, "export", "running")
                    ex = export_bvid(
                        bvid,
                        output_root=Path(args.output_root),
                        library_root=Path(args.library_root),
                        if_exists=args.if_exists,
                        dry_run=False,
                    )
                    set_stage(bvid_dir, state, "export", "succeeded", detail={"dest_dir": str(ex.dest_dir)})
                    _print_utf8(f"[EXPORT OK] {bvid} -> {ex.dest_dir}")
                if args.prune_output:
                    try:
                        shutil.rmtree(bvid_dir)
                        _print_utf8(f"[PRUNE OK] removed {bvid_dir}")
                    except Exception as e:
                        failures += 1
                        _print_utf8(f"[PRUNE FAIL] {bvid} reason={e}")
                        if args.fail_fast:
                            return 1
            except Exception as e:
                set_stage(bvid_dir, state, "export", "failed", error=str(e))
                failures += 1
                _print_utf8(f"[EXPORT FAIL] {bvid} reason={e}")
                if args.fail_fast:
                    return 1
        else:
            set_stage(bvid_dir, state, "export", "skipped", detail={"reason": "--no-export"})

    _print_utf8(f"Done. items={len(urls)} fail={failures}")

    # Summarize output leftovers (harvested but not exported/pruned) so they are never silently lost.
    try:
        leftovers = [d.name for d in out_root.iterdir() if d.is_dir() and (d / "json" / "metadata.json").exists()]
        if leftovers:
            _print_utf8(
                f"[LEFTOVER] {len(leftovers)} 个 output/ 目录已采集但未导出：{', '.join(sorted(leftovers)[:10])}"
                "… 可用 python -m bilibili_get export --all 补导出"
            )
    except Exception:
        pass
    return 0 if failures == 0 else 1


def _export_cmd(args: argparse.Namespace) -> int:
    output_root = Path(args.output_root)
    library_root = Path(args.library_root)
    bvids = list(args.bvid or [])
    if args.all:
        bvids.extend(iter_bvid_dirs(output_root))
    bvids = list(dict.fromkeys([b.strip() for b in bvids if b and b.strip()]))
    if not bvids:
        _print_utf8("请指定 --bvid BVxxxx 或使用 --all")
        return 2
    failed = 0
    for bvid in bvids:
        try:
            r = export_bvid(bvid, output_root=output_root, library_root=library_root, if_exists=args.if_exists, dry_run=False)
            _print_utf8(f"[OK] {bvid} -> {r.dest_dir}")
        except Exception as e:
            failed += 1
            _print_utf8(f"[FAIL] {bvid}: {e}")
    return 0 if failed == 0 else 1


def _repair_cmd(args: argparse.Namespace) -> int:
    """In-place repairs only (amendment D): transcribe+manifest; never fetch.

    Refetch candidates are written to targets_repair.txt with a ready-to-run
    grab-targets command line — fetching has exactly one executor.
    """
    from bilibili_asr.asr import ASRConfig, transcribe_bvid_dir
    from bilibili_library.completion import (
        CLASS_NO_AUDIO,
        CLASS_NO_TRANSCRIPT_WITH_AUDIO,
        classify,
        extract_bvid_from_dirname,
        iter_video_dirs,
        list_unavailable,
        mark_unavailable,
    )
    from bilibili_library.exporter import rewrite_manifest

    library_root = Path(args.library_root).resolve()
    if not library_root.is_dir():
        _print_utf8(f"未找到 library 目录：{library_root}")
        return 2

    # 0) manual unavailable marking (escape hatch)
    for bvid in args.mark_unavailable:
        mark_unavailable(library_root, bvid, reason="manual")
        _print_utf8(f"[UNAVAILABLE] {bvid} marked (manual)")

    unavailable = list_unavailable(library_root)
    partials: List[Dict[str, str]] = []
    refetch: List[Dict[str, str]] = []
    for v_dir in iter_video_dirs(library_root):
        bvid = extract_bvid_from_dirname(v_dir.name) or v_dir.name
        if bvid in unavailable:
            continue  # terminal account; retry goes through grab-* --include-unavailable
        state = classify(v_dir)
        if state == CLASS_NO_TRANSCRIPT_WITH_AUDIO:
            partials.append({"bvid": bvid, "dir": str(v_dir)})
        elif state == CLASS_NO_AUDIO:
            refetch.append({"bvid": bvid, "dir": str(v_dir)})

    _print_utf8(
        f"[SCAN] partial={len(partials)} refetch={len(refetch)} unavailable={len(unavailable)}"
    )

    # 1) in-place transcription for partials (the cheap fix)
    done_now = 0
    failures = 0
    if partials:
        cfg = ASRConfig(model=args.asr_model, device=args.asr_device, compute_type=args.asr_compute, language=args.asr_lang)
        work = partials if not args.limit else partials[: int(args.limit)]
        for item in work:
            v_dir = Path(item["dir"])
            if args.dry_run:
                _print_utf8(f"[DRY] transcribe in place: {v_dir.name}")
                continue
            _print_utf8(f"[REPAIR-ASR] {item['bvid']} {v_dir.name}")
            try:
                transcribe_bvid_dir(v_dir, cfg, merge_pages=True, fail_fast=False)
                rewrite_manifest(v_dir)  # amendment B: manifest must not lie
                done_now += 1
                _print_utf8(f"[REPAIR-ASR OK] {item['bvid']} (manifest rewritten)")
            except Exception as e:
                failures += 1
                _print_utf8(f"[REPAIR-ASR FAIL] {item['bvid']} reason={e}")
                if args.fail_fast:
                    break

    # 2) refetch candidates -> targets file + ready command (fetching stays in the engine)
    if refetch:
        ts = _utc_now_compact()
        run_dir = Path(args.out_dir) / f"{ts}_repair"
        run_dir.mkdir(parents=True, exist_ok=True)
        targets_path = run_dir / "targets_repair.txt"
        lines = [f"【{Path(it['dir']).name}】https://www.bilibili.com/video/{it['bvid']}" for it in refetch]
        targets_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        _print_utf8(f"[REFETCH] {len(refetch)} 条缺音频/缺目录 -> {targets_path}")
        _print_utf8(
            "重抓命令（抓取只有一个执行者=引擎）：\n"
            f"  python -m bilibili_get grab-targets --targets-file \"{targets_path}\" "
            f"--cookies cookie.txt --asr-device {args.asr_device} --asr-compute {args.asr_compute} --prune-output"
        )

    if unavailable:
        sample = ", ".join(list(unavailable)[:8])
        _print_utf8(f"[UNAVAILABLE] {len(unavailable)} 条终态（示例 {sample}…）；重试请用 grab-* --include-unavailable")

    _print_utf8(f"Done. repaired={done_now} refetch_candidates={len(refetch)} failures={failures}")
    return 1 if failures else 0


def _delegate_to_bilibili_search(argv: List[str]) -> int:
    from bilibili_search.cli import main as search_main

    try:
        search_main(argv)
        return 0
    except SystemExit as e:
        code = int(getattr(e, "code", 0) or 0)
        return code


def _batch_config_from_args(args: argparse.Namespace, run_dir: Path) -> "BatchConfig":
    from .orchestrate import BatchConfig

    return BatchConfig(
        run_dir=run_dir,
        cookies=args.cookies,
        proxy=args.proxy,
        output_root=args.output_root,
        library_root=args.library_root,
        download=args.download,
        comment_pages=int(args.comment_pages),
        pbp=bool(args.pbp),
        snapshots=str(args.snapshots),
        snapshot_k=int(args.snapshot_k),
        no_asr=bool(args.no_asr),
        no_export=bool(args.no_export),
        asr_device=args.asr_device,
        asr_compute=args.asr_compute,
        asr_model=args.asr_model,
        asr_lang=args.asr_lang,
        if_exists=args.if_exists,
        prune_output=bool(args.prune_output),
        between_sleep=float(args.between_sleep),
        fail_fast=bool(args.fail_fast),
        new_limit=int(args.new_limit),
        include_unavailable=bool(args.include_unavailable),
        keep_audio=bool(getattr(args, "keep_audio", False)),
        fetch_workers=int(getattr(args, "fetch_workers", 2) or 1),
        defer_asr=not bool(getattr(args, "no_defer_asr", False)),
    )


def _grab_search_cmd(args: argparse.Namespace) -> int:
    """Keyword research as one command: search head -> batch engine."""
    from bilibili_search.search import VideoFilter, search_videos
    from bilibili_search.session import build_web_session

    from .orchestrate import BatchItem, run_batch

    cookiefile = Path(args.cookies)
    if cookiefile.exists() and _warn_if_cookie_stale(cookiefile, args.proxy):
        _print_utf8("搜索接口依赖登录态，已中止。请先更新 cookie.txt 再重试。")
        return 2
    sess, _ = build_web_session(cookiefile if cookiefile.exists() else None, proxy=args.proxy)

    flt = VideoFilter(
        min_play=int(args.min_play),
        min_duration_seconds=int(args.min_seconds),
        max_duration_seconds=int(args.max_seconds) or 0,
    )
    items_found = search_videos(
        sess,
        keyword=args.keyword,
        order="totalrank",
        duration=0,
        tids=0,
        limit=int(args.search_limit),
        max_pages=0,
        page_sleep=1.5,
        flt=flt,
    )

    require_all = [t.strip().lower() for t in args.require_all.split(",") if t.strip()]
    require_any = [t.strip().lower() for t in args.require_any.split(",") if t.strip()]
    items: List[BatchItem] = []
    for it in items_found:
        title = (it.title_plain or it.title or "").strip()
        low = title.lower()
        if require_all and not all(t in low for t in require_all):
            continue
        if require_any and not any(t in low for t in require_any):
            continue
        items.append(BatchItem(bvid=it.bvid, title=title))

    import re as _re

    safe_kw = _re.sub(r"[^\w\u4e00-\u9fff]+", "_", args.keyword)[:40]
    run_dir = Path(args.out_dir) / f"{_utc_now_compact()}_search_{safe_kw}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "targets.txt").write_text(
        "\n".join(f"【{i.title}】https://www.bilibili.com/video/{i.bvid}" for i in items) + ("\n" if items else ""),
        encoding="utf-8",
    )
    _print_utf8(f"[SEARCH] keyword={args.keyword!r} hits={len(items_found)} kept={len(items)} dir={run_dir}")

    report = run_batch(items, _batch_config_from_args(args, run_dir))
    return report.exit_code()


def _grab_hot_cmd(args: argparse.Namespace) -> int:
    """Hot/ranking discovery head (OpenCLI bridge, optional) -> batch engine."""
    from bilibili_opencli import bridge

    from .orchestrate import BatchItem, run_batch

    if not bridge.available():
        _print_utf8(
            "[OPENCLI] 未检测到 opencli（热门/排行榜发现依赖它）。"
            "安装：npm install -g @jackwener/opencli 并保持浏览器扩展在线；"
            "或改用 grab-search / grab-uploader / grab-targets。"
        )
        return 2
    try:
        chart = bridge.chart(str(args.source), int(args.discover_limit))
    except bridge.BridgeError as e:
        _print_utf8(f"[OPENCLI] 榜单获取失败：{e}")
        return 2

    require_all = [t.strip().lower() for t in args.require_all.split(",") if t.strip()]
    require_any = [t.strip().lower() for t in args.require_any.split(",") if t.strip()]
    items: List[BatchItem] = []
    for it in chart:
        title = str(it.get("title") or "").strip()
        low = title.lower()
        if require_all and not all(t in low for t in require_all):
            continue
        if require_any and not any(t in low for t in require_any):
            continue
        items.append(BatchItem(bvid=it["bvid"], title=title))

    run_dir = Path(args.out_dir) / f"{_utc_now_compact()}_hot_{args.source}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "targets.txt").write_text(
        "\n".join(f"【{i.title}】https://www.bilibili.com/video/{i.bvid}" for i in items) + ("\n" if items else ""),
        encoding="utf-8",
    )
    _print_utf8(f"[HOT] source={args.source} hits={len(chart)} kept={len(items)} dir={run_dir}")

    report = run_batch(items, _batch_config_from_args(args, run_dir))
    return report.exit_code()


def _cookie_refresh_cmd(args: argparse.Namespace) -> int:
    """cookie.txt supply chain: browser cookies + (auto) QR login for SESSDATA."""
    from bilibili_opencli import bridge
    from bilibili_opencli.cookies import (
        browser_cookies,
        merge_cookies,
        qr_login_flow,
        read_existing,
        validate_login,
        write_netscape,
    )

    if not bridge.available():
        _print_utf8(
            "[OPENCLI] 未检测到 opencli（cookie-refresh 依赖它读取浏览器）。"
            "安装：npm install -g @jackwener/opencli 并保持浏览器扩展在线；"
            "或沿用浏览器扩展手动导出 cookie.txt。"
        )
        return 2

    cookie_path = Path(args.cookies)
    existing = read_existing(cookie_path)
    _print_utf8(f"[COOKIE] 现有文件: {cookie_path}（{len(existing)} 项）")

    # Tier 1: live browser cookies (non-HttpOnly) merged over the existing file
    try:
        fresh = browser_cookies()
    except bridge.BridgeError as e:
        _print_utf8(f"[COOKIE] 浏览器 cookie 获取失败：{e}")
        fresh = {}
    _print_utf8(f"[COOKIE] 浏览器会话: {len(fresh)} 项（SESSDATA 为 HttpOnly，document.cookie 不可见）")
    merged = merge_cookies(existing, fresh)

    need_qr = bool(args.qr) or "SESSDATA" not in merged
    if not need_qr:
        bak = write_netscape(cookie_path, merged, backup=not args.no_backup)
        ok = validate_login(cookie_path, args.proxy)
        if ok:
            _print_utf8(f"[OK] cookie.txt 已刷新并验证登录有效（{len(merged)} 项，SESSDATA 沿用现有值）")
            if bak:
                _print_utf8(f"[BACKUP] 旧文件 -> {bak.name}")
            return 0
        if ok is None:
            # The nav check itself failed (network etc.) — inconclusive, not
            # stale. Never trigger the QR flow on an inconclusive check.
            _print_utf8("[WARN] 登录校验未能完成（网络原因？）——文件已写好，稍后可用 doctor 或重跑验证")
            if bak:
                _print_utf8(f"[BACKUP] 旧文件 -> {bak.name}")
            return 0
        _print_utf8("[WARN] 合并后登录校验未通过——现有 SESSDATA 可能已失效，转入扫码流程")

    # Tier 2: pure-Python QR login (no browser) — the successful poll
    # response body is the only automatic SESSDATA source.
    _print_utf8("[QR] 正在生成登录二维码（窗口会自动弹出）……")
    header = "; ".join(f"{k}={v}" for k, v in merged.items())
    try:
        qr = qr_login_flow(header or None, log=lambda m: _print_utf8(m))
    except Exception as e:
        _print_utf8(f"[FAIL] 扫码流程异常：{e}")
        return 1
    if not qr:
        _print_utf8(
            "[FAIL] 未捕获到扫码登录（超时或未扫）。兜底：用浏览器扩展（Get cookies.txt）导出覆盖 cookie.txt，"
            "然后重跑本命令做日常刷新。"
        )
        return 1
    merged = merge_cookies(merged, qr)
    bak = write_netscape(cookie_path, merged, backup=not args.no_backup)
    ok = validate_login(cookie_path, args.proxy)
    if ok:
        _print_utf8(f"[OK] cookie.txt 已通过扫码刷新并验证登录有效（{len(merged)} 项，含新 SESSDATA）")
        if bak:
            _print_utf8(f"[BACKUP] 旧文件 -> {bak.name}")
        return 0
    _print_utf8("[WARN] 已写入但登录校验未通过；请重跑或检查网络后重试")
    return 1


def _grab_uploader_cmd(args: argparse.Namespace) -> int:
    if bool(args.seed_bvid) == bool(args.mid):
        _print_utf8("请指定 --seed-bvid 或 --mid（二选一）")
        return 2

    from bilibili_library.naming import sanitize_component
    from bilibili_search.seed import fetch_owner_info_by_bvid
    from bilibili_search.session import build_web_session
    from bilibili_search.space import list_space_videos
    from bilibili_search.wbi import extract_wbi_keys_from_nav_json

    from .orchestrate import BatchItem, run_batch

    cookiefile = Path(args.cookies)
    if cookiefile.exists() and _warn_if_cookie_stale(cookiefile, args.proxy):
        _print_utf8("space 发现接口依赖登录态，已中止。请先更新 cookie.txt 再重试。")
        return 2
    sess, cookie_brief = build_web_session(cookiefile if cookiefile.exists() else None, proxy=args.proxy)

    owner_mid = int(args.mid or 0)
    owner_name = str(owner_mid)
    if args.seed_bvid:
        owner = fetch_owner_info_by_bvid(sess, args.seed_bvid.strip())
        owner_mid = owner.mid
        owner_name = owner.name

    nav = sess.get("https://api.bilibili.com/x/web-interface/nav", timeout=15)
    nav.raise_for_status()
    keys = extract_wbi_keys_from_nav_json(nav.json())

    items_found = list_space_videos(
        sess,
        keys,
        mid=owner_mid,
        order=args.order,
        tid=int(args.tid),
        keyword=args.keyword,
        limit=max(int(args.discover_limit), 0),
        max_pages=0,
        page_sleep=max(float(args.page_sleep), 0.0),
        ps=max(int(args.ps), 1),
    )
    found = items_found
    if args.since_days and int(args.since_days) > 0:
        import time as _time

        cutoff = _time.time() - int(args.since_days) * 86400
        found = [it for it in items_found if (it.created or 0) >= cutoff]
        _print_utf8(f"[SINCE] --since-days={args.since_days}: {len(items_found)} -> {len(found)}")
    items = [BatchItem(bvid=it.bvid, title=it.title) for it in found]

    safe_owner = sanitize_component(owner_name or str(owner_mid), max_len=60)
    safe_kw = sanitize_component(args.keyword, max_len=60) if args.keyword else "all"
    run_dir = Path(args.out_dir) / f"{_utc_now_compact()}_up_{owner_mid}_{safe_owner}_kw_{safe_kw}"
    run_dir.mkdir(parents=True, exist_ok=True)

    import json as _json

    (run_dir / "targets.txt").write_text(
        "\n".join(f"【{it.title}】https://www.bilibili.com/video/{it.bvid}" for it in items) + ("\n" if items else ""),
        encoding="utf-8",
    )
    (run_dir / "meta.json").write_text(
        _json.dumps(
            {
                "uploader": {"mid": owner_mid, "name": owner_name},
                "keyword": args.keyword,
                "order": args.order,
                "returned_items": len(items),
                "cookie_mode": getattr(cookie_brief, "mode", None),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _print_utf8(f"[DISCOVER] up={owner_name} mid={owner_mid} returned={len(items)} dir={run_dir}")

    report = run_batch(items, _batch_config_from_args(args, run_dir))
    return report.exit_code()


def _grab_targets_cmd(args: argparse.Namespace) -> int:
    from .orchestrate import extract_bvids_from_lines, run_batch

    targets_path = Path(args.targets_file)
    if not targets_path.exists():
        _print_utf8(f"未找到 targets 文件：{targets_path}")
        return 2
    lines = targets_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    items = extract_bvids_from_lines(lines)
    if not items:
        _print_utf8("targets 文件中未解析到任何 BV 号")
        return 2

    name = str(args.name).strip() or targets_path.stem
    run_dir = Path(args.out_dir) / f"{_utc_now_compact()}_collect_{name}"
    run_dir.mkdir(parents=True, exist_ok=True)

    import json as _json

    (run_dir / "meta.json").write_text(
        _json.dumps(
            {"name": name, "targets_count": len(items), "targets_file": str(targets_path)},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _print_utf8(f"[COLLECT] name={name} targets={len(items)} dir={run_dir}")

    report = run_batch(items, _batch_config_from_args(args, run_dir))
    return report.exit_code()


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "run":
        sys.exit(_run_cmd(args))
    if args.cmd == "export":
        sys.exit(_export_cmd(args))
    if args.cmd == "grab-uploader":
        sys.exit(_grab_uploader_cmd(args))
    if args.cmd == "grab-targets":
        sys.exit(_grab_targets_cmd(args))
    if args.cmd == "grab-search":
        sys.exit(_grab_search_cmd(args))
    if args.cmd == "grab-hot":
        sys.exit(_grab_hot_cmd(args))
    if args.cmd == "cookie-refresh":
        sys.exit(_cookie_refresh_cmd(args))
    if args.cmd == "repair":
        sys.exit(_repair_cmd(args))
    if args.cmd == "search":
        # Delegate to existing CLI to keep behavior consistent.
        sub_argv: List[str] = ["pipeline" if args.pipeline else "search"]
        sub_argv.extend(["--keyword", args.keyword, "--order", args.order, "--limit", str(args.limit), "--cookies", args.cookies])
        if args.proxy:
            sub_argv.extend(["--proxy", args.proxy])
        sub_argv.extend(["--out-dir", args.out_dir])
        if args.pipeline:
            sub_argv.extend(
                [
                    "--output-root",
                    args.output_root,
                    "--library-root",
                    args.library_root,
                    "--download",
                    args.download,
                    "--comment-pages",
                    str(args.comment_pages),
                    "--asr-device",
                    args.asr_device,
                    "--asr-compute",
                    args.asr_compute,
                    "--asr-model",
                    args.asr_model,
                    "--if-exists",
                    args.if_exists,
                ]
            )
            if args.asr_lang:
                sub_argv.extend(["--asr-lang", args.asr_lang])
        sys.exit(_delegate_to_bilibili_search(sub_argv))
    if args.cmd == "uploader":
        sub_argv = ["uploader"]
        if args.seed_bvid:
            sub_argv.extend(["--seed-bvid", args.seed_bvid])
        if args.mid:
            sub_argv.extend(["--mid", str(args.mid)])
        sub_argv.extend(["--order", args.order, "--limit", str(args.limit), "--cookies", args.cookies, "--out-dir", args.out_dir])
        if args.keyword:
            sub_argv.extend(["--keyword", args.keyword])
        if args.proxy:
            sub_argv.extend(["--proxy", args.proxy])
        if args.pipeline:
            sub_argv.append("--pipeline")
            sub_argv.extend(
                [
                    "--output-root",
                    args.output_root,
                    "--library-root",
                    args.library_root,
                    "--download",
                    args.download,
                    "--comment-pages",
                    str(args.comment_pages),
                    "--asr-device",
                    args.asr_device,
                    "--asr-compute",
                    args.asr_compute,
                    "--asr-model",
                    args.asr_model,
                    "--if-exists",
                    args.if_exists,
                ]
            )
            if args.asr_lang:
                sub_argv.extend(["--asr-lang", args.asr_lang])
        sys.exit(_delegate_to_bilibili_search(sub_argv))

    raise RuntimeError(f"unknown cmd: {args.cmd}")


if __name__ == "__main__":
    main()
