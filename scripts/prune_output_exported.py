from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List


def _print_utf8(line: str) -> None:
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))


def main(argv: List[str] | None = None) -> None:
    repo_dir = Path(__file__).resolve().parents[1]  # .../BILIBILI_GET
    if str(repo_dir) not in sys.path:
        sys.path.insert(0, str(repo_dir))

    parser = argparse.ArgumentParser(
        description=(
            "Prune output/<bvid>/ directories that have already been exported into library/. "
            "This helps reclaim space after confirming library is the single source of truth."
        )
    )
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--library-root", default="library")
    parser.add_argument("--dry-run", action="store_true", help="Only report what would be deleted.")
    parser.add_argument("--execute", action="store_true", help="Actually delete directories.")
    parser.add_argument("--keep-last-n", type=int, default=0, help="Keep newest N output dirs even if exported (0=keep none).")
    args = parser.parse_args(argv)

    if args.dry_run == args.execute:
        raise SystemExit("Please specify exactly one of: --dry-run | --execute")

    output_root = Path(args.output_root).resolve()
    library_root = Path(args.library_root).resolve()
    if not output_root.exists():
        _print_utf8(f"[SKIP] output root does not exist: {output_root}")
        return

    from bilibili_library.exporter import export_bvid, iter_bvid_dirs

    bvids = iter_bvid_dirs(output_root)
    # keep newest N output dirs
    keep_set = set()
    if args.keep_last_n and args.keep_last_n > 0:
        dirs = []
        for bvid in bvids:
            p = output_root / bvid
            try:
                dirs.append((p.stat().st_mtime, bvid))
            except Exception:
                continue
        dirs.sort(reverse=True)
        keep_set = set([bvid for _, bvid in dirs[: args.keep_last_n]])

    deleted: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for bvid in bvids:
        if bvid in keep_set:
            skipped.append({"bvid": bvid, "reason": f"kept_by_keep_last_n={args.keep_last_n}"})
            continue

        # Determine expected dest dir from metadata, then decide if it exists.
        try:
            r = export_bvid(bvid, output_root=output_root, library_root=library_root, if_exists="skip", dry_run=True)
        except Exception as e:
            skipped.append({"bvid": bvid, "reason": f"export_bvid_dry_run_failed: {e}"})
            continue

        if not r.dest_dir.exists():
            skipped.append({"bvid": bvid, "reason": "not_exported_in_library", "dest_dir": str(r.dest_dir)})
            continue

        src_dir = output_root / bvid
        if args.dry_run:
            _print_utf8(f"[WOULD DELETE] {src_dir}")
            deleted.append({"bvid": bvid, "src_dir": str(src_dir), "dest_dir": str(r.dest_dir)})
            continue

        shutil.rmtree(src_dir)
        _print_utf8(f"[DELETED] {src_dir}")
        deleted.append({"bvid": bvid, "src_dir": str(src_dir), "dest_dir": str(r.dest_dir)})

    report = {
        "output_root": str(output_root),
        "library_root": str(library_root),
        "mode": "dry-run" if args.dry_run else "execute",
        "deleted": deleted,
        "skipped": skipped,
        "deleted_count": len(deleted),
        "skipped_count": len(skipped),
    }
    Path("discoveries").mkdir(parents=True, exist_ok=True)
    out = Path("discoveries") / "prune_output_exported_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _print_utf8(f"[REPORT] {out} deleted={len(deleted)} skipped={len(skipped)}")


if __name__ == "__main__":
    main()
