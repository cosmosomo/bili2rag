from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, List


def _to_simplified(text: str) -> str:
    try:
        from hanziconv import HanziConv  # type: ignore
    except Exception as e:
        raise RuntimeError("缺少依赖：hanziconv（用于繁体→简体转换）") from e
    return HanziConv.toSimplified(text)


def _iter_targets(roots: Iterable[Path]) -> List[Path]:
    pats = [
        "**/asr/transcript*.txt",
        "**/asr/transcript*.srt",
        "**/asr/segments.json",
    ]
    out: List[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for pat in pats:
            out.extend([p for p in root.glob(pat) if p.is_file()])
    # de-dup and keep stable order
    seen = set()
    uniq: List[Path] = []
    for p in out:
        rp = str(p.resolve())
        if rp in seen:
            continue
        seen.add(rp)
        uniq.append(p)
    return uniq


def _simplify_text_file_inplace(path: Path, *, dry_run: bool) -> bool:
    before = path.read_text(encoding="utf-8", errors="ignore")
    after = _to_simplified(before)
    if after == before:
        return False
    if not dry_run:
        path.write_text(after, encoding="utf-8")
    return True


def _simplify_segments_json_inplace(path: Path, *, dry_run: bool) -> bool:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    j = json.loads(raw)
    segs = j.get("segments")
    if not isinstance(segs, list):
        return False
    changed = False
    for s in segs:
        if not isinstance(s, dict):
            continue
        t = s.get("text")
        if not isinstance(t, str) or not t:
            continue
        nt = _to_simplified(t)
        if nt != t:
            s["text"] = nt
            changed = True
    if not changed:
        return False
    if not dry_run:
        path.write_text(json.dumps(j, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Convert ASR outputs from Traditional Chinese to Simplified Chinese in-place.")
    p.add_argument("--root", action="append", default=[], help="Root dir to scan (repeatable). Default: output + library")
    p.add_argument("--bvid", default="", help="Optional BV filter (only process paths containing this substring)")
    p.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    return p


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    roots = [Path(r) for r in (args.root or [])]
    if not roots:
        roots = [Path("output"), Path("library")]

    bvid_filter = (args.bvid or "").strip()

    targets = _iter_targets(roots)
    if bvid_filter:
        targets = [p for p in targets if bvid_filter in str(p)]

    total = 0
    changed = 0
    failed = 0

    for path in targets:
        total += 1
        try:
            if path.name == "segments.json":
                did = _simplify_segments_json_inplace(path, dry_run=args.dry_run)
            else:
                did = _simplify_text_file_inplace(path, dry_run=args.dry_run)
            if did:
                changed += 1
        except Exception as e:
            failed += 1
            sys.stderr.write(f"[FAIL] {path}: {e}\n")

    sys.stdout.write(
        f"done total={total} changed={changed} failed={failed} dry_run={bool(args.dry_run)} roots={[str(r) for r in roots]} bvid={bvid_filter!r}\n"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

