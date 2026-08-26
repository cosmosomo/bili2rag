from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


def _print_utf8(line: str) -> None:
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))


# Allow running this script from anywhere while keeping imports relative to the BILIBILI_GET repo dir.
_REPO_DIR = Path(__file__).resolve().parents[1]
if str(_REPO_DIR) not in sys.path:
    sys.path.insert(0, str(_REPO_DIR))


def _utc_now_compact() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _extract_bvid_from_dirname(name: str) -> Optional[str]:
    import re

    m = re.search(r"(BV[0-9A-Za-z]+)$", name)
    return m.group(1) if m else None


def _discover_exported_bvids(library_root: Path, uploader_dir: str) -> Set[str]:
    out: Set[str] = set()
    root = (library_root / uploader_dir)
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


def _download_set(s: str) -> Set[str]:
    return set([x.strip() for x in (s or "").split(",") if x.strip() and x.strip() != "none"])


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Discover an uploader's uploads via space API (WBI signed), optionally harvest+ASR+export, and report completeness."
    )
    parser.add_argument("--seed-bvid", default="", help="Seed BV to resolve uploader mid (alternative to --mid)")
    parser.add_argument("--mid", type=int, default=0, help="Uploader mid (alternative to --seed-bvid)")
    parser.add_argument("--keyword", default="", help="Filter uploader uploads by keyword (space API keyword)")
    parser.add_argument("--cookies", default="cookie.txt", help="Cookie file path (default: cookie.txt)")
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--order", default="pubdate", help="pubdate|click|stow")
    parser.add_argument("--tid", type=int, default=0)
    parser.add_argument("--ps", type=int, default=30)
    parser.add_argument("--max-pages", type=int, default=0, help="0=unlimited")
    parser.add_argument("--limit", type=int, default=0, help="0=unlimited")
    parser.add_argument("--page-sleep", type=float, default=1.0)

    parser.add_argument("--pipeline", action="store_true", help="Run harvest+ASR+export for discovered BV ids")
    parser.add_argument(
        "--process-limit",
        type=int,
        default=0,
        help="Limit how many NEW (not-yet-exported) videos to process in this run (0=all discovered).",
    )
    parser.add_argument("--no-asr", action="store_true", help="Skip ASR (download/export only; much faster).")
    parser.add_argument("--no-export", action="store_true", help="Skip export (keep artifacts under output/ only).")
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--library-root", default="library")
    parser.add_argument("--download", default="audio,subtitles")
    parser.add_argument("--comment-pages", type=int, default=1)
    parser.add_argument("--asr-device", default="cpu")
    parser.add_argument("--asr-compute", default="int8")
    parser.add_argument("--asr-model", default="auto")
    parser.add_argument("--asr-lang", default=None)
    parser.add_argument("--if-exists", choices=["fail", "skip", "overwrite"], default="skip")
    parser.add_argument("--prune-output", action="store_true", help="After a successful export, delete output/<bvid>/ to save space.")

    args = parser.parse_args(argv)

    if bool(args.seed_bvid) == bool(args.mid):
        _print_utf8("请指定 --seed-bvid 或 --mid（二选一）")
        sys.exit(2)

    from bilibili_search.session import build_web_session
    from bilibili_search.seed import fetch_owner_info_by_bvid
    from bilibili_search.wbi import extract_wbi_keys_from_nav_json
    from bilibili_search.space import fetch_space_video_page
    from bilibili_library.naming import sanitize_component

    cookiefile = Path(args.cookies)
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

    safe_kw = sanitize_component(args.keyword, max_len=60) if args.keyword else "all"

    # page 1 to read total count
    items, meta = fetch_space_video_page(
        sess,
        keys,
        mid=owner_mid,
        pn=1,
        ps=args.ps,
        order=args.order,
        tid=args.tid,
        keyword=args.keyword,
    )

    # When user passes --mid directly, resolve a stable uploader folder name from the space API result,
    # otherwise "already exported" detection will look under a numeric directory and miss existing exports.
    if (not args.seed_bvid) and items and owner_name == str(owner_mid):
        try:
            author = getattr(items[0], "author", None)
            if author:
                owner_name = str(author)
        except Exception:
            # Non-fatal: keep numeric owner_name
            pass

    safe_owner = sanitize_component(owner_name or str(owner_mid), max_len=60)
    run_dir = Path("discoveries") / f"{_utc_now_compact()}_up_{owner_mid}_{safe_owner}_kw_{safe_kw}"
    total = int(((meta.get("page") or {}).get("count")) or 0)
    all_items = list(items)
    pn = 2
    while True:
        if args.limit and len(all_items) >= args.limit:
            all_items = all_items[: args.limit]
            break
        if args.max_pages and pn > args.max_pages:
            break
        if not items:
            break
        if args.page_sleep and args.page_sleep > 0:
            time.sleep(args.page_sleep)
        items, meta = fetch_space_video_page(
            sess,
            keys,
            mid=owner_mid,
            pn=pn,
            ps=args.ps,
            order=args.order,
            tid=args.tid,
            keyword=args.keyword,
        )
        if not items:
            break
        all_items.extend(items)
        pn += 1

    # de-duplicate by bvid preserving order
    seen: Set[str] = set()
    compact_items: List[Dict[str, Any]] = []
    for it in all_items:
        if it.bvid in seen:
            continue
        seen.add(it.bvid)
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
                "total_count_from_api": total,
                "returned_items": len(compact_items),
                "cookie_mode": getattr(cookie_brief, "mode", None),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _print_utf8(
        f"[DISCOVER] up={owner_name} mid={owner_mid} keyword={args.keyword!r} total_from_api={total} returned={len(compact_items)} dir={run_dir}"
    )

    if not args.pipeline:
        sys.exit(0)

    from bilibili_harvester.cli import harvest_one
    from bilibili_harvester.utils import ensure_dir
    from bilibili_asr.asr import ASRConfig, transcribe_bvid_dir
    from bilibili_library.exporter import export_bvid

    out_root = ensure_dir(args.output_root)
    library_root = Path(args.library_root)
    cfg = ASRConfig(model=args.asr_model, device=args.asr_device, compute_type=args.asr_compute, language=args.asr_lang)
    dl = _download_set(args.download)

    uploader_dir = sanitize_component(owner_name or str(owner_mid), max_len=60)
    already_exported = _discover_exported_bvids(library_root, uploader_dir)
    failures: List[Dict[str, Any]] = []
    processed: List[str] = []
    started_at = time.time()

    def write_progress(stage: str, current_bvid: Optional[str] = None) -> None:
        """Write a resumable progress snapshot for long-running batches."""

        payload = {
            "uploader": {"mid": owner_mid, "name": owner_name},
            "discoveries_dir": str(run_dir),
            "stage": stage,
            "current_bvid": current_bvid,
            "discovered": len(compact_items),
            "already_exported_at_start": len(already_exported),
            "processed_ok": processed,
            "failures": failures,
            "process_limit": args.process_limit,
            "elapsed_seconds": round(time.time() - started_at, 3),
        }
        (run_dir / "progress.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    write_progress("start")

    for it in compact_items:
        bvid = str(it["bvid"])
        title = str(it.get("title") or "")
        url = f"https://www.bilibili.com/video/{bvid}"

        if args.if_exists == "skip" and bvid in already_exported:
            _print_utf8(f"[SKIP] {bvid} already exported")
            continue
        if args.process_limit and len(processed) >= args.process_limit:
            _print_utf8(f"[STOP] reached --process-limit={args.process_limit} (processed_ok={len(processed)})")
            break

        try:
            _print_utf8(f"[HARVEST] {bvid} {title}")
            write_progress("harvest", current_bvid=bvid)
            harvest_one(
                url,
                cookiefile=cookiefile if cookiefile.exists() else None,
                out_root=out_root,
                structured_root=out_root,
                download_set=dl,
                proxy=args.proxy,
                comment_pages=args.comment_pages,
            )
        except BaseException as e:
            if isinstance(e, KeyboardInterrupt):
                raise
            failures.append({"bvid": bvid, "stage": "harvest", "error": str(e)})
            _print_utf8(f"[FAIL] harvest {bvid}: {e}")
            write_progress("harvest_fail", current_bvid=bvid)
            continue

        if not args.no_asr:
            try:
                bvid_dir = (Path(args.output_root) / bvid).resolve()
                _print_utf8(f"[ASR] {bvid}")
                write_progress("asr", current_bvid=bvid)
                transcribe_bvid_dir(bvid_dir, cfg, merge_pages=True, fail_fast=False)
            except BaseException as e:
                if isinstance(e, KeyboardInterrupt):
                    raise
                failures.append({"bvid": bvid, "stage": "asr", "error": str(e)})
                _print_utf8(f"[FAIL] asr {bvid}: {e}")
                write_progress("asr_fail", current_bvid=bvid)
                continue

        if not args.no_export:
            try:
                _print_utf8(f"[EXPORT] {bvid}")
                write_progress("export", current_bvid=bvid)
                export_bvid(
                    bvid,
                    output_root=Path(args.output_root),
                    library_root=library_root,
                    if_exists=args.if_exists,
                    dry_run=False,
                )
                processed.append(bvid)
                write_progress("export_ok", current_bvid=bvid)
                if args.prune_output:
                    try:
                        shutil.rmtree((Path(args.output_root) / bvid).resolve())
                        _print_utf8(f"[PRUNE] {bvid} OK")
                    except BaseException as e:
                        if isinstance(e, KeyboardInterrupt):
                            raise
                        failures.append({"bvid": bvid, "stage": "prune_output", "error": str(e)})
                        _print_utf8(f"[FAIL] prune_output {bvid}: {e}")
                        write_progress("prune_fail", current_bvid=bvid)
            except BaseException as e:
                if isinstance(e, KeyboardInterrupt):
                    raise
                failures.append({"bvid": bvid, "stage": "export", "error": str(e)})
                _print_utf8(f"[FAIL] export {bvid}: {e}")
                write_progress("export_fail", current_bvid=bvid)
                continue
        else:
            # Consider it processed (for batching) once harvested if export is disabled.
            processed.append(bvid)
            write_progress("harvest_ok", current_bvid=bvid)

    (run_dir / "pipeline_failures.json").write_text(json.dumps({"failures": failures}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Completeness check: for the discovered set, how many are exported now?
    exported_now = _discover_exported_bvids(library_root, uploader_dir)
    discovered = set([str(it["bvid"]) for it in compact_items])
    missing = sorted([b for b in discovered if b not in exported_now])
    (run_dir / "missing_bvids.txt").write_text("\n".join(missing) + ("\n" if missing else ""), encoding="utf-8")

    _print_utf8(
        f"[DONE] discovered={len(discovered)} exported_now={len(discovered) - len(missing)} missing={len(missing)} failures={len(failures)} report_dir={run_dir}"
    )
    write_progress("done")

    # If we pruned everything we processed and output root is empty, remove it.
    if args.prune_output:
        out_root_dir = Path(args.output_root)
        try:
            if out_root_dir.exists() and not any(out_root_dir.iterdir()):
                out_root_dir.rmdir()
        except Exception:
            pass

    # Heuristic "is it complete?"
    # If API returned <= what it claims total count (with our limit/max_pages applied), and missing==0, we treat as complete for this query.
    scope_limited = bool(args.limit) or bool(args.max_pages)
    if scope_limited:
        _print_utf8("[NOTE] scope was limited by --limit/--max-pages; cannot claim full coverage.")
    else:
        if total and len(discovered) < total:
            _print_utf8(f"[WARN] API says total_count={total} but we only discovered={len(discovered)} (may be risk/blocked or API filtering).")
        elif missing:
            _print_utf8("[WARN] some discovered BV ids are not exported; see missing_bvids.txt")
        else:
            _print_utf8("[OK] All discovered BV ids are exported. For this keyword/tid query, coverage looks complete.")


if __name__ == "__main__":
    main()
