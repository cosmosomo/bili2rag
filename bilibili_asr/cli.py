from __future__ import annotations

import argparse
import sys
import json
from pathlib import Path
from typing import Optional

from .asr import ASRConfig, transcribe_bvid_dir


def resolve_bvid_dir(bvid: str, output_root: Path = Path("output")) -> Path:
    return (output_root / bvid).resolve()


def _print_utf8(line: str) -> None:
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Bilibili ASR CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="对单个 bvid 的音频执行转写")
    run.add_argument("--bvid", required=True, help="目标 bvid（用于定位 output/<bvid>/ 下音频）")
    run.add_argument("--output-root", default="output", help="输出根目录（默认 output）")
    run.add_argument("--audio", default=None, help="可选，显式指定音频文件路径")
    run.add_argument("--lang", default=None, help="可选，语言代码（如 zh、en），默认自动检测")
    run.add_argument("--model", default="auto", help="auto|模型名(base/small/...)|本地模型目录路径")
    run.add_argument("--device", default="cpu", help="cpu|cuda（默认 cpu）")
    run.add_argument("--compute", default="int8", help="int8|float16|float32|auto（默认 int8）")

    merge = sub.add_parser("merge", help="合并分P的转写为全集合并文件")
    merge.add_argument("--bvid", required=True, help="目标 bvid")
    merge.add_argument("--output-root", default="output", help="输出根目录（默认 output）")

    args = parser.parse_args(argv)

    if args.cmd == "run":
        bvid_dir = resolve_bvid_dir(args.bvid, Path(args.output_root))
        if not bvid_dir.exists():
            _print_utf8(f"未找到输出目录：{bvid_dir}")
            sys.exit(2)

        cfg = ASRConfig(model=args.model, device=args.device, compute_type=args.compute, language=args.lang)
        audio = Path(args.audio) if args.audio else None

        try:
            report = transcribe_bvid_dir(bvid_dir, cfg, audio=audio, merge_pages=True, fail_fast=False)
        except Exception as e:
            _print_utf8(f"[FAIL] ASR {args.bvid}: {e}")
            sys.exit(1)

        runs = report.get("runs") or []
        failed = [r for r in runs if not r.get("ok")]
        _print_utf8(f"[OK] ASR {args.bvid}: mode={report.get('mode')} runs={len(runs)} fail={len(failed)} merged={report.get('merged')}")
        sys.exit(0 if not failed else 1)
    elif args.cmd == "merge":
        from .aggregate import aggregate_bvid
        bvid_dir = resolve_bvid_dir(args.bvid, Path(args.output_root))
        if not bvid_dir.exists():
            _print_utf8(f"未找到输出目录：{bvid_dir}")
            sys.exit(2)
        srt_path, txt_path = aggregate_bvid(bvid_dir)
        _print_utf8(f"合并完成：\n  SRT: {srt_path}\n  TXT: {txt_path}")


if __name__ == "__main__":
    main()
