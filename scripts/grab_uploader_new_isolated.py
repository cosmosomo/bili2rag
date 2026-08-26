from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set


def _print_utf8(line: str) -> None:
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))


def _utc_now_compact() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _extract_bvid_from_dirname(name: str) -> Optional[str]:
    import re

    m = re.search(r"(BV[0-9A-Za-z]+)$", name)
    return m.group(1) if m else None


def _discover_exported_bvids(library_root: Path, uploader_dir: str) -> Set[str]:
    out: Set[str] = set()
    root = library_root / uploader_dir
    if not root.exists():
        return out
    for p in root.iterdir():
        if not p.is_dir():
            continue
        bvid = _extract_bvid_from_dirname(p.name)
        if bvid:
            out.add(bvid)
    return out


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_targets(path: Path, items: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(f"【{it.get('title','')}】https://www.bilibili.com/video/{it.get('bvid')}\n")


def _build_bilibili_get_run_cmd(args: argparse.Namespace, bvid: str) -> List[str]:
    cmd: List[str] = [
        sys.executable,
        "-m",
        "bilibili_get",
        "run",
        "--url",
        bvid,
        "--cookies",
        args.cookies,
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
    if getattr(args, "pbp", False):
        cmd.append("--pbp")
    snapshots = str(getattr(args, "snapshots", "none") or "none").strip()
    if snapshots.lower() != "none":
        cmd.extend(["--snapshots", snapshots, "--snapshot-k", str(int(getattr(args, "snapshot_k", 5)))])
    if args.proxy:
        cmd.extend(["--proxy", args.proxy])
    if args.no_asr:
        cmd.append("--no-asr")
    if args.no_export:
        cmd.append("--no-export")
    if args.asr_lang:
        cmd.extend(["--asr-lang", args.asr_lang])
    if args.prune_output:
        cmd.append("--prune-output")
    return cmd


def _build_bilibili_get_export_cmd(args: argparse.Namespace, bvid: str) -> List[str]:
    cmd: List[str] = [
        sys.executable,
        "-m",
        "bilibili_get",
        "export",
        "--bvid",
        bvid,
        "--output-root",
        args.output_root,
        "--library-root",
        args.library_root,
        "--if-exists",
        args.if_exists,
    ]
    return cmd


def _build_prune_output_exported_cmd(args: argparse.Namespace) -> List[str]:
    return [
        sys.executable,
        str((Path(__file__).resolve().parent / "prune_output_exported.py")),
        "--output-root",
        args.output_root,
        "--library-root",
        args.library_root,
        "--execute",
    ]


# Conservative patterns: only explicit not-found / permission errors may mark a
# video unavailable. Transient network failures must NOT pin a permanent state.
_UNAVAILABLE_PATTERNS = ("-404", "-403", "404 Not Found", "啥都木有", "稿件不可见", "视频不存在")


def _log_says_unavailable(log_path: Path) -> bool:
    try:
        text = log_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    tail = text[-20000:]
    return any(p in tail for p in _UNAVAILABLE_PATTERNS)


def main(argv: Optional[List[str]] = None) -> None:
    repo_dir = Path(__file__).resolve().parents[1]  # .../BILIBILI_GET
    if str(repo_dir) not in sys.path:
        sys.path.insert(0, str(repo_dir))

    from bilibili_library.completion import (
        append_failure,
        classify,
        find_video_dir,
        is_done,
        mark_unavailable,
    )

    parser = argparse.ArgumentParser(
        description=(
            "Incrementally grab NEW uploads from an uploader, but run bilibili_get per-video in a subprocess "
            "to avoid long-running batch instability (each video is isolated in its own process)."
        )
    )
    parser.add_argument("--seed-bvid", default="", help="Seed BV to resolve uploader mid (alternative to --mid)")
    parser.add_argument("--mid", type=int, default=0, help="Uploader mid (alternative to --seed-bvid)")
    parser.add_argument("--keyword", default="", help="Optional keyword filter (space API keyword)")
    parser.add_argument("--cookies", default="cookie.txt", help="Cookie file path (default: cookie.txt)")
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--order", default="pubdate", help="pubdate|click|stow (default: pubdate)")
    parser.add_argument("--tid", type=int, default=0)
    parser.add_argument("--ps", type=int, default=30)
    parser.add_argument("--page-sleep", type=float, default=0.2)
    parser.add_argument(
        "--discover-limit",
        type=int,
        default=50,
        help="How many most-recent uploads to discover (default: 50)",
    )
    parser.add_argument(
        "--new-limit",
        type=int,
        default=10,
        help="How many NEW (not-yet-exported) videos to process in this run (default: 10)",
    )
    parser.add_argument("--fail-fast", action="store_true", help="Stop on first per-video pipeline failure.")
    parser.add_argument("--between-sleep", type=float, default=0.0, help="Sleep seconds between videos (default: 0)")

    # Pipeline config
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--library-root", default="library")
    parser.add_argument("--download", default="audio,subtitles")
    parser.add_argument("--comment-pages", type=int, default=1)
    parser.add_argument("--pbp", action="store_true", help="Fetch PBP (high-energy bar) into json/pbp.json")
    parser.add_argument(
        "--snapshots",
        default="none",
        help="Snapshots: none|auto|comma-separated seconds (e.g. 10,60,120). auto uses PBP peaks.",
    )
    parser.add_argument("--snapshot-k", type=int, default=5, help="When --snapshots auto, pick top K peaks (default: 5)")
    parser.add_argument("--no-asr", action="store_true")
    parser.add_argument("--no-export", action="store_true")
    parser.add_argument("--asr-device", default="cpu")
    parser.add_argument("--asr-compute", default="int8")
    parser.add_argument("--asr-model", default="auto")
    parser.add_argument("--asr-lang", default=None)
    parser.add_argument("--if-exists", choices=["fail", "skip", "overwrite"], default="skip")
    parser.add_argument("--prune-output", action="store_true", help="After export, delete output/<bvid>/ to save space.")
    parser.add_argument("--out-dir", default="discoveries", help="Discovery/progress output directory (default: discoveries)")
    args = parser.parse_args(argv)

    if bool(args.seed_bvid) == bool(args.mid):
        raise SystemExit("请指定 --seed-bvid 或 --mid（二选一）")

    from bilibili_library.naming import sanitize_component
    from bilibili_search.seed import fetch_owner_info_by_bvid
    from bilibili_search.session import build_web_session
    from bilibili_search.space import list_space_videos
    from bilibili_search.wbi import extract_wbi_keys_from_nav_json

    cookiefile = Path(args.cookies)

    # Pre-check login: space wbi/arc/search returns misleading -352 when SESSDATA is stale.
    if cookiefile.exists():
        from bilibili_harvester.cookies import check_login_state, read_cookie_file, STALE_COOKIE_HINT

        header, _ = read_cookie_file(str(cookiefile))
        if check_login_state(header, proxy=args.proxy) is False:
            raise SystemExit(f"[COOKIE STALE] {STALE_COOKIE_HINT}")

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

    items = list_space_videos(
        sess,
        keys,
        mid=owner_mid,
        order=args.order,
        tid=args.tid,
        keyword=args.keyword,
        limit=max(int(args.discover_limit), 0),
        max_pages=0,
        page_sleep=max(float(args.page_sleep), 0.0),
        ps=max(int(args.ps), 1),
    )

    safe_owner = sanitize_component(owner_name or str(owner_mid), max_len=60)
    safe_kw = sanitize_component(args.keyword, max_len=60) if args.keyword else "all"
    run_dir = Path(args.out_dir) / f"{_utc_now_compact()}_up_{owner_mid}_{safe_owner}_kw_{safe_kw}_isolated"
    run_dir.mkdir(parents=True, exist_ok=True)

    compact_items: List[Dict[str, Any]] = []
    for it in items:
        compact_items.append(
            {
                "bvid": it.bvid,
                "title": it.title,
                "author": it.author,
                "mid": it.mid,
                "created": it.created,
                "length": it.length_text,
                "length_seconds": it.length_seconds,
                "play": it.play,
            }
        )

    _write_jsonl(run_dir / "results.jsonl", compact_items)
    _write_targets(run_dir / "targets.txt", compact_items)
    (run_dir / "meta.json").write_text(
        json.dumps(
            {
                "uploader": {"mid": owner_mid, "name": owner_name},
                "keyword": args.keyword,
                "order": args.order,
                "tid": args.tid,
                "page_size": args.ps,
                "returned_items": len(compact_items),
                "cookie_mode": getattr(cookie_brief, "mode", None),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _print_utf8(f"[DISCOVER] up={owner_name} mid={owner_mid} returned={len(compact_items)} dir={run_dir}")

    uploader_dir = sanitize_component(owner_name or str(owner_mid), max_len=60)
    library_root = Path(args.library_root)
    already_exported = _discover_exported_bvids(library_root, uploader_dir)
    run_id = run_dir.name

    processed: List[str] = []
    failures: List[Dict[str, Any]] = []
    started_at = time.time()

    def write_progress(stage: str, current_bvid: Optional[str] = None) -> None:
        payload = {
            "uploader": {"mid": owner_mid, "name": owner_name},
            "discoveries_dir": str(run_dir),
            "stage": stage,
            "current_bvid": current_bvid,
            "discovered": len(compact_items),
            "already_exported_at_start": len(already_exported),
            "processed_ok": processed,
            "failures": failures,
            "new_limit": args.new_limit,
            "elapsed_seconds": round(time.time() - started_at, 3),
        }
        (run_dir / "progress.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    write_progress("start")

    logs_dir = run_dir / "pipeline_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    for it in compact_items:
        bvid = str(it["bvid"])
        title = str(it.get("title") or "")

        if args.if_exists == "skip":
            vdir = find_video_dir(library_root, bvid)
            state = classify(vdir)
            if state == "ok":
                _print_utf8(f"[SKIP] {bvid} already done")
                continue
            if state == "no_transcript_with_audio":
                # Rule E: batches never re-harvest what repair can fix in place.
                append_failure(
                    library_root, bvid=bvid, stage="partial_asr", error="audio present, transcript missing (await repair)", title=title, run_id=run_id
                )
                _print_utf8(f"[SKIP] {bvid} partial (has audio, no transcript) -> repair")
                continue
        if args.new_limit and len(processed) >= int(args.new_limit):
            _print_utf8(f"[STOP] reached --new-limit={args.new_limit} (processed_ok={len(processed)})")
            break

        cmd = _build_bilibili_get_run_cmd(args, bvid)
        log_path = logs_dir / f"{bvid}.log"
        write_progress("run_subprocess", current_bvid=bvid)
        _print_utf8(f"[RUN] {bvid} {title}")

        try:
            with log_path.open("w", encoding="utf-8") as f:
                p = subprocess.run(cmd, cwd=str(repo_dir), stdout=f, stderr=subprocess.STDOUT)
        except Exception as e:
            failures.append({"bvid": bvid, "stage": "subprocess_start", "error": str(e), "cmd": cmd})
            append_failure(library_root, bvid=bvid, stage="subprocess_start", error=str(e), title=title, run_id=run_id)
            _print_utf8(f"[FAIL] start {bvid}: {e}")
            write_progress("subprocess_start_fail", current_bvid=bvid)
            if args.fail_fast:
                raise SystemExit(1)
            continue

        if p.returncode != 0:
            # Sometimes the per-video process may crash (common on flaky networks),
            # but it might have already produced output/<bvid>/ (or even exported to library/).
            # We try to salvage by exporting + pruning if possible, then treat it as processed.
            exported_now = _discover_exported_bvids(library_root, uploader_dir)
            out_bvid_dir = (Path(args.output_root) / bvid).resolve()
            if bvid in exported_now:
                if classify(find_video_dir(library_root, bvid)) != "ok":
                    append_failure(
                        library_root, bvid=bvid, stage="partial_asr",
                        error=f"returncode={p.returncode}, salvaged without transcript", title=title, run_id=run_id,
                    )
                _print_utf8(f"[SALVAGE] {bvid} returncode={p.returncode} but already exported; continue")
                processed.append(bvid)
                write_progress("salvage_already_exported_ok", current_bvid=bvid)
                continue

            if (not args.no_export) and out_bvid_dir.exists():
                _print_utf8(f"[SALVAGE] {bvid} returncode={p.returncode}, output dir exists; try export+prune")
                try:
                    subprocess.check_call(_build_bilibili_get_export_cmd(args, bvid), cwd=str(repo_dir))
                    if args.prune_output:
                        subprocess.check_call(_build_prune_output_exported_cmd(args), cwd=str(repo_dir))
                except Exception as e:
                    failures.append(
                        {
                            "bvid": bvid,
                            "stage": "salvage_export",
                            "returncode": p.returncode,
                            "error": str(e),
                            "log": str(log_path),
                        }
                    )
                    append_failure(library_root, bvid=bvid, stage="salvage_export", error=str(e), title=title, run_id=run_id)
                    _print_utf8(f"[FAIL] salvage {bvid}: {e}")
                    write_progress("salvage_fail", current_bvid=bvid)
                    if args.fail_fast:
                        raise SystemExit(1)
                    continue

                exported_now2 = _discover_exported_bvids(library_root, uploader_dir)
                if bvid in exported_now2:
                    if classify(find_video_dir(library_root, bvid)) != "ok":
                        append_failure(
                            library_root, bvid=bvid, stage="partial_asr",
                            error=f"returncode={p.returncode}, salvaged without transcript", title=title, run_id=run_id,
                        )
                    processed.append(bvid)
                    write_progress("salvage_export_ok", current_bvid=bvid)
                    continue

            failures.append({"bvid": bvid, "stage": "subprocess", "returncode": p.returncode, "log": str(log_path)})
            if _log_says_unavailable(log_path):
                mark_unavailable(library_root, bvid, reason=f"auto: returncode={p.returncode} (404/403 pattern)")
                append_failure(library_root, bvid=bvid, stage="unavailable", error=f"returncode={p.returncode}", title=title, run_id=run_id)
                _print_utf8(f"[UNAVAILABLE] {bvid} marked (404/403); will skip in future runs")
            else:
                append_failure(library_root, bvid=bvid, stage="subprocess", error=f"returncode={p.returncode} log={log_path}", title=title, run_id=run_id)
            _print_utf8(f"[FAIL] {bvid} returncode={p.returncode} log={log_path}")
            write_progress("subprocess_fail", current_bvid=bvid)
            if args.fail_fast:
                raise SystemExit(p.returncode)
            continue

        processed.append(bvid)
        write_progress("subprocess_ok", current_bvid=bvid)
        if args.between_sleep and float(args.between_sleep) > 0:
            time.sleep(float(args.between_sleep))

    (run_dir / "pipeline_failures.json").write_text(
        json.dumps({"failures": failures}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    exported_now = _discover_exported_bvids(library_root, uploader_dir)
    discovered = set([str(it["bvid"]) for it in compact_items])
    missing = sorted([b for b in discovered if b not in exported_now])
    (run_dir / "missing_bvids.txt").write_text("\n".join(missing) + ("\n" if missing else ""), encoding="utf-8")

    _print_utf8(
        f"[DONE] discovered={len(discovered)} exported_now={len(discovered) - len(missing)} missing={len(missing)} failures={len(failures)} report_dir={run_dir}"
    )
    write_progress("done")

    # If we pruned everything and output root is empty, remove it.
    if args.prune_output:
        out_root_dir = Path(args.output_root)
        try:
            if out_root_dir.exists() and not any(out_root_dir.iterdir()):
                out_root_dir.rmdir()
        except Exception:
            pass

    raise SystemExit(0 if not failures else 1)


if __name__ == "__main__":
    main()
