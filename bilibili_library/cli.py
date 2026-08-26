from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from .exporter import export_bvid, iter_bvid_dirs


def _print_utf8(line: str) -> None:
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Bilibili Library CLI (readable naming/export)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    export = sub.add_parser("export", help="将 output/<bvid>/ 导出为可读命名目录（不破坏原结构）")
    export.add_argument("--output-root", default="output", help="采集输出根目录（默认 output）")
    export.add_argument("--library-root", default="library", help="导出根目录（默认 library）")
    export.add_argument("--bvid", action="append", default=[], help="目标 bvid，可重复传入")
    export.add_argument("--all", action="store_true", help="导出 output-root 下全部 bvid 目录")
    export.add_argument("--if-exists", choices=["fail", "skip", "overwrite"], default="skip", help="目标目录已存在时策略")
    export.add_argument("--dry-run", action="store_true", help="只打印计划，不实际写入/复制")

    args = parser.parse_args(argv)

    if args.cmd == "export":
        output_root = Path(args.output_root)
        library_root = Path(args.library_root)

        bvids = list(args.bvid)
        if args.all:
            bvids.extend(iter_bvid_dirs(output_root))
        bvids = list(dict.fromkeys([b.strip() for b in bvids if b and b.strip()]))

        if not bvids:
            _print_utf8("请指定 --bvid BVxxxx 或使用 --all")
            sys.exit(2)

        ok = 0
        skipped = 0
        failed = 0
        for bvid in bvids:
            try:
                r = export_bvid(
                    bvid,
                    output_root=output_root,
                    library_root=library_root,
                    if_exists=args.if_exists,
                    dry_run=args.dry_run,
                )
                if not r.copied and args.if_exists == "skip" and r.dest_dir.exists():
                    skipped += 1
                    _print_utf8(f"[SKIP] {bvid} 已存在：{r.dest_dir}")
                else:
                    ok += 1
                    _print_utf8(f"[OK] {bvid} → {r.dest_dir}")
            except Exception as e:
                failed += 1
                _print_utf8(f"[FAIL] {bvid}: {e}")

        _print_utf8(f"Done. ok={ok} skip={skipped} fail={failed}")
        sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
