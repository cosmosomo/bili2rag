from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set


def _print_utf8(line: str) -> None:
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))
    # stdout may be redirected; flush so progress is observable.
    try:
        sys.stdout.buffer.flush()
    except Exception:
        pass


def _utc_now_compact() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _extract_bvids(lines: Iterable[str]) -> List[str]:
    out: List[str] = []
    for line in lines:
        for m in re.finditer(r"(BV[0-9A-Za-z]+)", line):
            out.append(m.group(1))
    # de-dupe preserving order
    seen: Set[str] = set()
    uniq: List[str] = []
    for b in out:
        if b in seen:
            continue
        seen.add(b)
        uniq.append(b)
    return uniq


def _resolve_under_repo(repo_dir: Path, p: str) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    return (repo_dir / path).resolve()


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _build_run_cmd(args: argparse.Namespace, bvid: str) -> List[str]:
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
        "--snapshots",
        args.snapshots,
        "--snapshot-k",
        str(args.snapshot_k),
        "--asr-device",
        args.asr_device,
        "--asr-compute",
        args.asr_compute,
        "--asr-model",
        args.asr_model,
        "--if-exists",
        args.if_exists,
        "--prune-output",
    ]
    if args.proxy:
        cmd.extend(["--proxy", args.proxy])
    if args.pbp:
        cmd.append("--pbp")
    if args.no_asr:
        cmd.append("--no-asr")
    if args.no_export:
        cmd.append("--no-export")
    if args.asr_lang:
        cmd.extend(["--asr-lang", args.asr_lang])
    return cmd


def main(argv: Optional[List[str]] = None) -> None:
    repo_dir = Path(__file__).resolve().parents[1]  # .../BILIBILI_GET
    if str(repo_dir) not in sys.path:
        sys.path.insert(0, str(repo_dir))

    parser = argparse.ArgumentParser(
        description=(
            "Collect a list of BV videos from a targets.txt (or arbitrary text file) into library/ "
            "by calling `bilibili_get run` per BV. This does NOT create library/_topics pointers."
        )
    )
    parser.add_argument("--name", default="", help="Optional run name, used in discoveries folder name.")
    parser.add_argument("--targets-file", required=True, help="Text file containing URLs/BV ids (any format).")

    parser.add_argument("--cookies", default="cookie.txt")
    parser.add_argument("--proxy", default=None)
    parser.add_argument("--output-root", default="output")
    parser.add_argument("--library-root", default="library")
    parser.add_argument("--download", default="audio,subtitles")
    parser.add_argument("--comment-pages", type=int, default=1)
    parser.add_argument("--pbp", action="store_true", help="抓取高能进度条（PBP）到 json/pbp.json")
    parser.add_argument("--snapshots", default="smart", help="抓取视频快照：none|smart|auto|秒列表（如 10,60,120）")
    parser.add_argument("--snapshot-k", type=int, default=5, help="--snapshots auto 时选取的数量（默认 5）")
    parser.add_argument("--no-asr", action="store_true")
    parser.add_argument("--no-export", action="store_true")
    parser.add_argument("--asr-device", default="cpu")
    parser.add_argument("--asr-compute", default="int8")
    parser.add_argument("--asr-model", default="auto")
    parser.add_argument("--asr-lang", default=None)
    parser.add_argument("--if-exists", choices=["fail", "skip", "overwrite"], default="skip")
    parser.add_argument("--between-sleep", type=float, default=0.0)
    parser.add_argument("--fail-fast", action="store_true", help="Stop on first failure.")
    args = parser.parse_args(argv)

    # Normalize path-like args to be repo-relative stable.
    args.targets_file = str(_resolve_under_repo(repo_dir, args.targets_file))
    args.output_root = str(_resolve_under_repo(repo_dir, args.output_root))
    args.library_root = str(_resolve_under_repo(repo_dir, args.library_root))
    args.cookies = str(_resolve_under_repo(repo_dir, args.cookies))

    targets_path = Path(args.targets_file)
    if not targets_path.exists():
        raise SystemExit(f"targets-file not found: {targets_path}")

    lines = targets_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    targets = _extract_bvids(lines)
    if not targets:
        raise SystemExit("No BV ids found in targets-file.")

    name = str(args.name).strip() or targets_path.stem
    run_dir = (repo_dir / "discoveries" / f"{_utc_now_compact()}_collect_{name}").resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        run_dir / "meta.json",
        {
            "name": name,
            "targets_count": len(targets),
            "targets_file": str(targets_path),
            "args": {
                "cookies": args.cookies,
                "proxy": args.proxy,
                "download": args.download,
                "comment_pages": args.comment_pages,
                "pbp": bool(args.pbp),
                "snapshots": args.snapshots,
                "snapshot_k": int(args.snapshot_k),
                "asr_device": args.asr_device,
                "asr_compute": args.asr_compute,
                "asr_model": args.asr_model,
                "asr_lang": args.asr_lang,
                "if_exists": args.if_exists,
                "no_asr": bool(args.no_asr),
                "no_export": bool(args.no_export),
            },
        },
    )
    (run_dir / "targets.txt").write_text("\n".join(targets) + "\n", encoding="utf-8")

    _print_utf8(f"[COLLECT] name={name} targets={len(targets)} dir={run_dir}")

    ok: List[str] = []
    failures: List[Dict[str, Any]] = []

    from bilibili_library.completion import (
        append_failure,
        classify,
        find_video_dir,
        mark_unavailable,
    )

    library_root = Path(args.library_root)
    run_id = run_dir.name

    _unavailable_patterns = ("-404", "-403", "404 Not Found", "啥都木有", "稿件不可见", "视频不存在")

    def _log_says_unavailable(path: Path) -> bool:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return False
        return any(p in text[-20000:] for p in _unavailable_patterns)

    for bvid in targets:
        if args.if_exists == "skip":
            vdir = find_video_dir(library_root, bvid)
            state = classify(vdir)
            if state == "ok":
                _print_utf8(f"[SKIP] {bvid} already done")
                continue
            if state == "no_transcript_with_audio":
                append_failure(library_root, bvid=bvid, stage="partial_asr", error="audio present, transcript missing (await repair)", run_id=run_id)
                _print_utf8(f"[SKIP] {bvid} partial (has audio, no transcript) -> repair")
                continue

        _print_utf8(f"[RUN] {bvid}")
        log_path = logs_dir / f"{bvid}.log"
        cmd = _build_run_cmd(args, bvid)
        try:
            with log_path.open("w", encoding="utf-8") as f:
                p = subprocess.run(cmd, cwd=str(repo_dir), stdout=f, stderr=subprocess.STDOUT)
        except Exception as e:
            failures.append({"bvid": bvid, "stage": "subprocess_start", "error": str(e), "cmd": cmd})
            append_failure(library_root, bvid=bvid, stage="subprocess_start", error=str(e), run_id=run_id)
            _print_utf8(f"[FAIL] start {bvid}: {e}")
            if args.fail_fast:
                raise SystemExit(1)
            continue

        if p.returncode != 0:
            failures.append({"bvid": bvid, "stage": "run", "returncode": p.returncode, "log": str(log_path)})
            if _log_says_unavailable(log_path):
                mark_unavailable(library_root, bvid, reason=f"auto: returncode={p.returncode} (404/403 pattern)")
                append_failure(library_root, bvid=bvid, stage="unavailable", error=f"returncode={p.returncode}", run_id=run_id)
            else:
                append_failure(library_root, bvid=bvid, stage="run", error=f"returncode={p.returncode} log={log_path}", run_id=run_id)
            _print_utf8(f"[FAIL] {bvid} returncode={p.returncode} log={log_path}")
            if args.fail_fast:
                raise SystemExit(p.returncode)
            continue

        ok.append(bvid)
        _append_jsonl(run_dir / "ok.jsonl", {"bvid": bvid, "log": str(log_path)})

        if args.between_sleep and float(args.between_sleep) > 0:
            time.sleep(float(args.between_sleep))

    _write_json(run_dir / "failures.json", {"failures": failures})
    _print_utf8(f"[DONE] ok={len(ok)} failures={len(failures)} dir={run_dir}")
    raise SystemExit(0 if not failures else 1)


if __name__ == "__main__":
    main()

