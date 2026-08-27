from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bilibili_library.completion import (  # noqa: E402
    extract_bvid_from_dirname,
    has_audio,
    has_transcript,
)

SKIP_PREFIXES = ("_", ".")


def _print_utf8(line: str) -> None:
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))


def _extract_bvid(dirname: str) -> Optional[str]:
    return extract_bvid_from_dirname(dirname)


def _has_asr(video_dir: Path) -> bool:
    return has_transcript(video_dir)


def _has_audio(video_dir: Path) -> bool:
    return has_audio(video_dir)


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
        help="为缺转写/缺音频的视频生成 targets 文件（BV + 目录名标题），可用 grab-targets 重抓",
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

    from bilibili_library.completion import list_unavailable

    unavailable = list_unavailable(library_root)
    report["unavailable"] = sorted(unavailable)

    # Unavailable videos are a terminal account of their own; keep them out of
    # the "missing" noise (their library dirs may exist from salvage exports).
    unavail_set = set(unavailable)
    for key in ("missing_asr", "missing_audio", "empty_comments"):
        report[key] = [item for item in report[key] if item.get("bvid") not in unavail_set]

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.out_dir) / f"{ts}_doctor"
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    _print_utf8(
        f"[SCAN] uploaders={report['uploaders']} videos={report['videos']} "
        f"missing_asr={len(report['missing_asr'])} missing_audio={len(report['missing_audio'])} "
        f"empty_comments={len(report['empty_comments'])} unavailable={len(unavailable)} "
        f"output_leftovers={len(leftovers)}"
    )
    _print_utf8(f"[REPORT] {report_path}")

    for item in report["missing_asr"][:20]:
        _print_utf8(f"[MISSING ASR] {item['uploader']} / {item['dir']}")
    for item in report["missing_audio"][:20]:
        _print_utf8(f"[MISSING AUDIO] {item['uploader']} / {item['dir']}")
    for item in report["empty_comments"][:10]:
        _print_utf8(f"[EMPTY COMMENTS] {item['uploader']} / {item['dir']}")
    for bvid in report["unavailable"][:10]:
        reason = (unavailable.get(bvid) or {}).get("reason", "")
        _print_utf8(f"[UNAVAILABLE] {bvid} {reason}")

    # Repair routing: in-place ASR vs engine refetch (single executor).
    partial_n = sum(
        1 for item in report["missing_asr"]
        if item["bvid"] not in {i["bvid"] for i in report["missing_audio"]}
    )
    if partial_n or report["missing_audio"]:
        _print_utf8(
            f"[FIX] 就地补转写 {partial_n} 条：python -m bilibili_get repair --library-root \"{library_root}\""
        )
        if report["missing_audio"]:
            _print_utf8(
                f"[FIX] 重抓缺音频 {len(report['missing_audio'])} 条：python -m bilibili_get repair "
                f"--library-root \"{library_root}\"（会生成 targets_repair.txt 并给出 grab-targets 命令）"
            )
    if unavailable:
        _print_utf8("[FIX] 重试终态：python -m bilibili_get grab-* --include-unavailable")

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


if __name__ == "__main__":
    main()
