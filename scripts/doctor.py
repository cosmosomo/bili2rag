from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})$")

AUDIO_NAMES = ("audio.mp3", "audio.m4a")
SKIP_PREFIXES = ("_", ".")


def _print_utf8(line: str) -> None:
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))


def _extract_bvid(dirname: str) -> Optional[str]:
    m = _BV_RE.search(dirname.strip())
    return m.group(1) if m else None


def _has_asr(video_dir: Path) -> bool:
    if (video_dir / "asr" / "transcript.txt").exists():
        return True
    if (video_dir / "asr" / "transcript_all.txt").exists():
        return True
    pages_dir = video_dir / "pages"
    if pages_dir.is_dir():
        for pdir in pages_dir.iterdir():
            if pdir.is_dir() and (
                (pdir / "asr" / "transcript.txt").exists() or (pdir / "asr" / "transcript_all.txt").exists()
            ):
                return True
    return False


def _has_audio(video_dir: Path) -> bool:
    for name in AUDIO_NAMES:
        if (video_dir / name).exists():
            return True
    pages_dir = video_dir / "pages"
    if pages_dir.is_dir():
        for pdir in pages_dir.iterdir():
            if not pdir.is_dir():
                continue
            for name in AUDIO_NAMES:
                if (pdir / name).exists():
                    return True
            for f in pdir.iterdir():
                if f.is_file() and f.suffix.lower() in (".mp3", ".m4a"):
                    return True
    return False


def _comments_state(video_dir: Path) -> str:
    f = video_dir / "comments.txt"
    if not f.exists():
        return "missing"
    if f.stat().st_size == 0:
        return "empty"
    return "ok"


def scan_library(library_root: Path) -> Dict[str, Any]:
    uploaders = 0
    videos = 0
    missing_asr: List[Dict[str, str]] = []
    missing_audio: List[Dict[str, str]] = []
    empty_comments: List[Dict[str, str]] = []

    for up_dir in sorted(library_root.iterdir()):
        if not up_dir.is_dir() or up_dir.name.startswith(SKIP_PREFIXES):
            continue
        uploaders += 1
        for v_dir in sorted(up_dir.iterdir()):
            if not v_dir.is_dir():
                continue
            bvid = _extract_bvid(v_dir.name)
            if not bvid:
                continue
            videos += 1
            item = {"uploader": up_dir.name, "bvid": bvid, "dir": v_dir.name}
            if not _has_asr(v_dir):
                missing_asr.append(item)
            if not _has_audio(v_dir):
                missing_audio.append(item)
            if _comments_state(v_dir) != "ok":
                empty_comments.append(item)

    return {
        "library_root": str(library_root),
        "uploaders": uploaders,
        "videos": videos,
        "missing_asr": missing_asr,
        "missing_audio": missing_audio,
        "empty_comments": empty_comments,
    }


def scan_output_leftovers(output_root: Path) -> List[str]:
    leftovers: List[str] = []
    if not output_root.is_dir():
        return leftovers
    for d in sorted(output_root.iterdir()):
        if d.is_dir() and (d / "json" / "metadata.json").exists():
            leftovers.append(d.name)
    return leftovers


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="体检 library/：找出缺转写/缺音频/空评论的视频目录，以及 output/ 未导出残留。"
    )
    parser.add_argument("--library-root", default="library")
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--out-dir", default="discoveries", help="报告输出目录（默认 discoveries/）")
    parser.add_argument(
        "--write-targets",
        action="store_true",
        help="为缺转写/缺音频的视频生成 targets 文件（BV + 目录名标题），可用 collect_targets.py 重抓",
    )
    args = parser.parse_args(argv)

    library_root = Path(args.library_root).resolve()
    if not library_root.is_dir():
        _print_utf8(f"未找到 library 目录：{library_root}")
        sys.exit(2)

    report = scan_library(library_root)
    leftovers = scan_output_leftovers(Path(args.output_root).resolve())
    report["output_leftovers"] = leftovers
    report["generated_at_utc"] = datetime.now(timezone.utc).isoformat()

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.out_dir) / f"{ts}_doctor"
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    _print_utf8(
        f"[SCAN] uploaders={report['uploaders']} videos={report['videos']} "
        f"missing_asr={len(report['missing_asr'])} missing_audio={len(report['missing_audio'])} "
        f"empty_comments={len(report['empty_comments'])} output_leftovers={len(leftovers)}"
    )
    _print_utf8(f"[REPORT] {report_path}")

    for item in report["missing_asr"][:20]:
        _print_utf8(f"[MISSING ASR] {item['uploader']} / {item['dir']}")
    for item in report["missing_audio"][:20]:
        _print_utf8(f"[MISSING AUDIO] {item['uploader']} / {item['dir']}")
    for item in report["empty_comments"][:10]:
        _print_utf8(f"[EMPTY COMMENTS] {item['uploader']} / {item['dir']}")

    if args.write_targets:
        repair = {}
        for item in report["missing_audio"]:
            repair[item["bvid"]] = item["dir"]
        for item in report["missing_asr"]:
            repair.setdefault(item["bvid"], item["dir"])
        targets_path = run_dir / "targets_repair.txt"
        lines = [f"{bvid}\t{dirname}" for bvid, dirname in sorted(repair.items())]
        targets_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        _print_utf8(f"[TARGETS] {len(repair)} 条待修复 -> {targets_path}")
        _print_utf8("修复方式（缺音频需重抓，缺转写可用 asr-uploader 或 collect_targets.py --if-exists overwrite）")


if __name__ == "__main__":
    main()
