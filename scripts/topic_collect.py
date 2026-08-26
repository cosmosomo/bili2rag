from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bilibili_get.orchestrate import BatchConfig, BatchItem, extract_bvids_from_lines, run_batch  # noqa: E402
from bilibili_library.completion import find_video_dir  # noqa: E402


def _print_utf8(line: str) -> None:
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))
    try:
        sys.stdout.buffer.flush()
    except Exception:
        pass


def _utc_now_compact() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _resolve_under_repo(repo_dir: Path, p: str) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    return (repo_dir / path).resolve()


def _topic_entry_dir(topic_root: Path, topic_name: str, exported_dir: Path) -> Path:
    return (topic_root / topic_name / exported_dir.name).resolve()


def _load_existing_topic_bvids(topic_root: Path, topic_name: str) -> Set[str]:
    existing: Set[str] = set()
    base = (topic_root / topic_name)
    if not base.exists():
        return existing
    for d in base.iterdir():
        if not d.is_dir():
            continue
        p = d / "pointer.json"
        if not p.exists():
            continue
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        b = j.get("bvid")
        if isinstance(b, str) and b:
            existing.add(b.strip())
    return existing


def main(argv: Optional[List[str]] = None) -> None:
    repo_dir = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser(
        description=(
            "Collect BV videos into a topic folder under library/ without duplicating assets: "
            "harvest via the shared batch engine, then create pointer.json entries per video."
        )
    )
    parser.add_argument("--topic-name", required=True)
    parser.add_argument("--topic-root", default="library/_topics")
    parser.add_argument("--targets-file", default="", help="Text file containing URLs/BV ids (any format).")
    parser.add_argument("--bvid", action="append", default=[], help="BV id(s) (repeatable).")

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
    parser.add_argument("--between-sleep", type=float, default=0.0)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--include-unavailable", action="store_true")
    parser.add_argument("--out-dir", default="discoveries")
    args = parser.parse_args(argv)

    args.topic_root = str(_resolve_under_repo(repo_dir, args.topic_root))
    args.library_root = str(_resolve_under_repo(repo_dir, args.library_root))
    args.output_root = str(_resolve_under_repo(repo_dir, args.output_root))
    args.cookies = str(_resolve_under_repo(repo_dir, args.cookies))
    if args.targets_file:
        args.targets_file = str(_resolve_under_repo(repo_dir, args.targets_file))

    topic_root = Path(args.topic_root)
    library_root = Path(args.library_root)

    lines: List[str] = []
    if args.targets_file:
        p = Path(args.targets_file)
        if not p.exists():
            raise SystemExit(f"targets-file not found: {p}")
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()

    file_items = extract_bvids_from_lines(lines)
    extra = [b.strip() for b in (args.bvid or []) if b and b.strip()]
    seen: Set[str] = set()
    items: List[BatchItem] = []
    for it in file_items + [BatchItem(bvid=b) for b in extra]:
        if it.bvid in seen:
            continue
        seen.add(it.bvid)
        items.append(it)
    if not items:
        raise SystemExit("No targets provided. Use --targets-file or --bvid.")

    run_dir = (repo_dir / args.out_dir / f"{_utc_now_compact()}_topic_{args.topic_name}").resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "targets.txt").write_text("\n".join(it.bvid for it in items) + "\n", encoding="utf-8")

    _print_utf8(f"[TOPIC] name={args.topic_name} targets={len(items)} dir={run_dir}")
    existing_bvids = _load_existing_topic_bvids(topic_root, args.topic_name)
    if existing_bvids:
        _print_utf8(f"[TOPIC] resume: existing_pointers={len(existing_bvids)}")

    cfg = BatchConfig(
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
        prune_output=True,  # topic collection never keeps output staging dirs
        between_sleep=float(args.between_sleep),
        fail_fast=bool(args.fail_fast),
        include_unavailable=bool(args.include_unavailable),
    )
    report = run_batch(items, cfg)

    # Pointer post-processing: one pointer per target that exists in library/.
    pointers_created = 0
    with (run_dir / "index.jsonl").open("a", encoding="utf-8") as index_f:
        for it in items:
            if it.bvid in existing_bvids:
                continue
            vdir = find_video_dir(library_root, it.bvid)
            if vdir is None:
                continue
            entry_dir = _topic_entry_dir(topic_root, args.topic_name, vdir)
            entry_dir.mkdir(parents=True, exist_ok=True)
            pointer: Dict[str, Any] = {
                "bvid": it.bvid,
                "bilibili_url": f"https://www.bilibili.com/video/{it.bvid}",
                "exported_dir": str(vdir),
                "exported_dir_name": vdir.name,
                "collected_at_utc": _utc_now_compact(),
            }
            (entry_dir / "pointer.json").write_text(
                json.dumps(pointer, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            index_f.write(json.dumps(pointer, ensure_ascii=False) + "\n")
            pointers_created += 1

    _print_utf8(
        f"[DONE] exported={len(report.exported)} pointers_created={pointers_created} "
        f"failed={len(report.failed)} dir={run_dir}"
    )
    raise SystemExit(report.exit_code())


if __name__ == "__main__":
    main()
