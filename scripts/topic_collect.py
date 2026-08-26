from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


def _print_utf8(line: str) -> None:
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))
    # When stdout is redirected (Start-Process -RedirectStandardOutput), Python may buffer.
    # Flush aggressively so progress is observable in real time.
    sys.stdout.buffer.flush()


def _resolve_under_repo(repo_dir: Path, p: str) -> Path:
    """
    Resolve a path argument in a stable way.

    topic_collect is often launched from different working directories (e.g. repo root).
    We interpret relative paths as relative to BILIBILI_GET repo_dir to avoid writing
    artifacts into unexpected locations.
    """
    path = Path(p)
    if path.is_absolute():
        return path
    return (repo_dir / path).resolve()


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


def _parse_exported_dir_from_log(log_text: str, bvid: str) -> Optional[Path]:
    # Example: [EXPORT OK] BVxxxx -> C:\...\library\...\...\_BVxxxx
    pat = re.compile(rf"^\[EXPORT OK\]\s+{re.escape(bvid)}\s+->\s+(?P<path>.+?)\s*$", re.MULTILINE)
    m = pat.search(log_text)
    if not m:
        return None
    p = m.group("path").strip().strip('"')
    try:
        return Path(p).resolve()
    except Exception:
        return Path(p)


def _find_exported_dir_by_glob(library_root: Path, bvid: str) -> Optional[Path]:
    # Fallback: search by folder suffix "_BVxxxx" across library/
    try:
        best: Optional[Tuple[float, Path]] = None
        for p in library_root.rglob(f"*{bvid}"):
            if not p.is_dir():
                continue
            try:
                mt = p.stat().st_mtime
            except Exception:
                mt = 0.0
            if best is None or mt > best[0]:
                best = (mt, p)
        return best[1] if best else None
    except Exception:
        return None


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


def _build_export_cmd(args: argparse.Namespace, bvid: str) -> List[str]:
    return [
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
    ]


def _build_prune_cmd(args: argparse.Namespace) -> List[str]:
    # Use the dedicated pruner to delete successfully exported output/<bvid>/ directories.
    return [
        sys.executable,
        str((Path(__file__).resolve().parent / "prune_output_exported.py")),
        "--output-root",
        args.output_root,
        "--library-root",
        args.library_root,
        "--execute",
    ]


def _topic_entry_dir(topic_root: Path, topic_name: str, exported_dir: Path) -> Path:
    # Mirror the exported folder name so directory listing is human-readable.
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
    repo_dir = Path(__file__).resolve().parents[1]  # .../BILIBILI_GET
    if str(repo_dir) not in sys.path:
        sys.path.insert(0, str(repo_dir))

    parser = argparse.ArgumentParser(
        description=(
            "Collect a list of BV videos into a topic folder under library/ without duplicating large assets: "
            "each topic entry is a folder named like the exported folder, containing a pointer.json."
        )
    )
    parser.add_argument("--topic-name", required=True, help="Topic folder name (under --topic-root)")
    parser.add_argument("--topic-root", default="library/_topics", help="Topic root (default: library/_topics)")

    parser.add_argument("--targets-file", default="", help="Text file containing URLs/BV ids (any format).")
    parser.add_argument("--bvid", action="append", default=[], help="BV id(s) (repeatable).")

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

    # Normalize all "root" / file paths under repo_dir to avoid cwd-dependent behavior.
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

    bvids = _extract_bvids(lines) + [b.strip() for b in (args.bvid or []) if b and b.strip()]
    # de-dupe preserving order again (in case file + args overlap)
    seen: Set[str] = set()
    targets: List[str] = []
    for b in bvids:
        if b in seen:
            continue
        seen.add(b)
        targets.append(b)

    if not targets:
        raise SystemExit("No targets provided. Use --targets-file or --bvid.")

    run_dir = (repo_dir / "discoveries" / f"{_utc_now_compact()}_topic_{args.topic_name}").resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        run_dir / "meta.json",
        {
            "topic_name": args.topic_name,
            "topic_root": str(topic_root),
            "targets_count": len(targets),
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
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    _print_utf8(f"[TOPIC] name={args.topic_name} targets={len(targets)} dir={run_dir}")

    existing_bvids = _load_existing_topic_bvids(topic_root, args.topic_name)
    if existing_bvids:
        _print_utf8(f"[TOPIC] resume: existing_pointers={len(existing_bvids)} (will skip)")

    ok: List[str] = []
    failures: List[Dict[str, Any]] = []

    for bvid in targets:
        if bvid in existing_bvids:
            _print_utf8(f"[SKIP] {bvid} already in topic pointers")
            continue
        _print_utf8(f"[RUN] {bvid}")
        log_path = logs_dir / f"{bvid}.log"
        cmd = _build_run_cmd(args, bvid)

        try:
            with log_path.open("w", encoding="utf-8") as f:
                p = subprocess.run(cmd, cwd=str(repo_dir), stdout=f, stderr=subprocess.STDOUT)
        except Exception as e:
            failures.append({"bvid": bvid, "stage": "subprocess_start", "error": str(e), "cmd": cmd})
            _print_utf8(f"[FAIL] start {bvid}: {e}")
            if args.fail_fast:
                raise SystemExit(1)
            continue

        log_text = ""
        try:
            log_text = log_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass

        # Even if the run process crashes, it may have produced output/<bvid> with ASR.
        # Try to salvage by exporting + pruning (no mocking, no dummy data).
        exported_dir = _parse_exported_dir_from_log(log_text, bvid)
        if p.returncode != 0 and exported_dir is None:
            out_bvid_dir = (Path(args.output_root) / bvid).resolve()
            if out_bvid_dir.exists():
                _print_utf8(f"[SALVAGE] {bvid} returncode={p.returncode}, but output dir exists; try export+prune")
                exp_cmd = _build_export_cmd(args, bvid)
                prn_cmd = _build_prune_cmd(args)
                try:
                    subprocess.check_call(exp_cmd, cwd=str(repo_dir))
                    subprocess.check_call(prn_cmd, cwd=str(repo_dir))
                except Exception as e:
                    failures.append(
                        {"bvid": bvid, "stage": "salvage_export", "returncode": p.returncode, "error": str(e), "log": str(log_path)}
                    )
                    _print_utf8(f"[FAIL] salvage {bvid}: {e}")
                    if args.fail_fast:
                        raise SystemExit(1)
                    continue
                # Re-parse after salvage
                try:
                    log_text2 = log_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    log_text2 = log_text
                exported_dir = _parse_exported_dir_from_log(log_text2, bvid) or _find_exported_dir_by_glob(library_root, bvid)

        if p.returncode != 0 and exported_dir is None:
            failures.append({"bvid": bvid, "stage": "run", "returncode": p.returncode, "log": str(log_path)})
            _print_utf8(f"[FAIL] {bvid} returncode={p.returncode} log={log_path}")
            if args.fail_fast:
                raise SystemExit(p.returncode)
            continue

        if exported_dir is None:
            exported_dir = _find_exported_dir_by_glob(library_root, bvid)

        if exported_dir is None or not exported_dir.exists():
            failures.append({"bvid": bvid, "stage": "locate_export", "error": "exported dir not found", "log": str(log_path)})
            _print_utf8(f"[FAIL] {bvid} exported dir not found (see log: {log_path})")
            if args.fail_fast:
                raise SystemExit(1)
            continue

        # Create topic pointer folder mirroring exported folder name.
        entry_dir = _topic_entry_dir(topic_root, args.topic_name, exported_dir)
        entry_dir.mkdir(parents=True, exist_ok=True)
        pointer = {
            "bvid": bvid,
            "bilibili_url": f"https://www.bilibili.com/video/{bvid}",
            "exported_dir": str(exported_dir),
            "exported_dir_name": exported_dir.name,
            "collected_at_utc": _utc_now_compact(),
            "log": str(log_path),
        }
        _write_json(entry_dir / "pointer.json", pointer)
        ok.append(bvid)
        _append_jsonl(run_dir / "index.jsonl", pointer)

        if args.between_sleep and float(args.between_sleep) > 0:
            time.sleep(float(args.between_sleep))

    _write_json(run_dir / "failures.json", {"failures": failures})
    _print_utf8(f"[DONE] ok={len(ok)} failures={len(failures)} dir={run_dir}")
    raise SystemExit(0 if not failures else 1)


if __name__ == "__main__":
    main()
