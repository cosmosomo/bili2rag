from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    repo_dir = Path(__file__).resolve().parents[1]  # .../BILIBILI_GET
    if str(repo_dir) not in sys.path:
        sys.path.insert(0, str(repo_dir))

    parser = argparse.ArgumentParser(
        description=(
            "Incrementally grab NEW uploads from an uploader (discover via space API, then harvest+ASR+export ONLY for not-yet-exported BV ids)."
        )
    )
    parser.add_argument("--seed-bvid", default="", help="Seed BV to resolve uploader mid (alternative to --mid)")
    parser.add_argument("--mid", type=int, default=0, help="Uploader mid (alternative to --seed-bvid)")
    parser.add_argument("--keyword", default="", help="Optional keyword filter (space API keyword)")
    parser.add_argument("--cookies", default="cookie.txt", help="Cookie file path (default: cookie.txt)")
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--discover-limit", type=int, default=50, help="How many most-recent uploads to discover (default: 50)")
    parser.add_argument("--new-limit", type=int, default=10, help="How many NEW (not-yet-exported) videos to process in this run (default: 10)")
    parser.add_argument("--prune-output", action="store_true", help="After export, delete output/<bvid>/ to save space.")

    # Pipeline config
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--library-root", default="library")
    parser.add_argument("--download", default="audio,subtitles")
    parser.add_argument("--comment-pages", type=int, default=1)
    parser.add_argument("--no-asr", action="store_true")
    parser.add_argument("--no-export", action="store_true")
    parser.add_argument("--asr-device", default="cpu")
    parser.add_argument("--asr-compute", default="int8")
    parser.add_argument("--asr-model", default="auto")
    parser.add_argument("--asr-lang", default=None)
    parser.add_argument("--if-exists", choices=["fail", "skip", "overwrite"], default="skip")
    args = parser.parse_args(argv)

    # Delegate to grab_uploader_series.py to ensure we truly skip already-exported BV ids
    # (skip happens BEFORE harvest/ASR), and to keep completeness reporting consistent.
    from scripts.grab_uploader_series import main as series_main

    series_argv: list[str] = [
        "--order",
        "pubdate",
        "--ps",
        "30",
        "--max-pages",
        "0",
        "--limit",
        str(max(int(args.discover_limit), 0)),
        "--page-sleep",
        "0.2",
        "--cookies",
        args.cookies,
        "--pipeline",
        "--process-limit",
        str(max(int(args.new_limit), 0)),
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
    if args.keyword:
        series_argv.extend(["--keyword", args.keyword])
    if args.proxy:
        series_argv.extend(["--proxy", args.proxy])
    if args.no_asr:
        series_argv.append("--no-asr")
    if args.no_export:
        series_argv.append("--no-export")
    if args.prune_output:
        series_argv.append("--prune-output")
    if args.asr_lang:
        series_argv.extend(["--asr-lang", args.asr_lang])

    if bool(args.seed_bvid) == bool(args.mid):
        raise SystemExit("请指定 --seed-bvid 或 --mid（二选一）")
    if args.seed_bvid:
        series_argv.extend(["--seed-bvid", args.seed_bvid])
    if args.mid:
        series_argv.extend(["--mid", str(args.mid)])

    # Ensure relative paths resolve under BILIBILI_GET.
    old_cwd = Path.cwd()
    try:
        import os

        os.chdir(repo_dir)
        series_main(series_argv)
    finally:
        try:
            import os

            os.chdir(old_cwd)
        except Exception as e:
            raise RuntimeError(f"Failed to restore cwd to {old_cwd}") from e


if __name__ == "__main__":
    main()
