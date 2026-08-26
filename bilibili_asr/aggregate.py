from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Optional


_TIME_RE = re.compile(r"^(\d\d):(\d\d):(\d\d),(\d\d\d)\s+-->\s+(\d\d):(\d\d):(\d\d),(\d\d\d)$")


def _to_simplified(text: str) -> str:
    try:
        from hanziconv import HanziConv  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError("缺少依赖：hanziconv（用于繁体→简体转换）") from e
    return HanziConv.toSimplified(text)


def _parse_ts(ts: str) -> float:
    m = _TIME_RE.match(ts)
    if not m:
        raise ValueError(f"Invalid SRT time line: {ts}")
    h, m1, s, ms, h2, m2, s2, ms2 = map(int, m.groups())
    start = h * 3600 + m1 * 60 + s + ms / 1000.0
    end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000.0
    return start, end


def _fmt_ts(sec: float) -> str:
    if sec < 0:
        sec = 0
    ms = int(round(sec * 1000))
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    ms2 = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms2:03d}"


def _read_srt(p: Path) -> List[Tuple[float, float, List[str]]]:
    blocks: List[Tuple[float, float, List[str]]] = []
    if not p.exists():
        return blocks
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    i = 0
    n = len(lines)
    while i < n:
        # skip index line(s)
        # typical block: idx, time, 1..k text lines, blank
        # find time line
        while i < n and not _TIME_RE.match(lines[i]):
            i += 1
        if i >= n:
            break
        # time line
        tline = lines[i]
        i += 1
        try:
            start, end = _parse_ts(tline)
        except Exception:
            continue
        # text lines until blank
        text_lines: List[str] = []
        while i < n and lines[i].strip() != "":
            text_lines.append(lines[i])
            i += 1
        # skip blank
        while i < n and lines[i].strip() == "":
            i += 1
        blocks.append((start, end, text_lines))
    return blocks


def _write_srt(blocks: List[Tuple[float, float, List[str]]], out_path: Path) -> None:
    out_lines: List[str] = []
    for idx, (st, et, txt) in enumerate(blocks, start=1):
        out_lines.append(str(idx))
        out_lines.append(f"{_fmt_ts(st)} --> {_fmt_ts(et)}")
        if txt:
            # Normalize to simplified Chinese (deterministic; safe for non-CJK text).
            out_lines.extend([_to_simplified(t) for t in txt])
        out_lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out_lines).strip() + "\n", encoding="utf-8")


def _read_duration_from_segments(seg_json: Path) -> Optional[float]:
    try:
        j = json.loads(seg_json.read_text(encoding="utf-8"))
        d = j.get("duration")
        if isinstance(d, (int, float)):
            return float(d)
    except Exception:
        pass
    return None


def aggregate_bvid(bvid_dir: Path) -> Tuple[Path, Path]:
    """Aggregate all pages' transcripts (SRT/TXT) into two files under <bvid>/asr/.

    Returns: (srt_path, txt_path)
    """
    pages_root = bvid_dir / "pages"
    asr_dir_root = bvid_dir / "asr"
    asr_dir_root.mkdir(parents=True, exist_ok=True)
    out_srt = asr_dir_root / "transcript_all.srt"
    out_txt = asr_dir_root / "transcript_all.txt"

    # discover pages in order: by json/pages_index.json, fallback to glob
    pages_index = []
    pi = bvid_dir / "json" / "pages_index.json"
    if pi.exists():
        try:
            j = json.loads(pi.read_text(encoding="utf-8"))
            pages_index = j.get("pages") or []
        except Exception:
            pages_index = []

    if not pages_index:
        # fallback: sort by directory name
        pages = sorted(pages_root.glob("p*/"))
        for idx, pd in enumerate(pages, start=1):
            pages_index.append({"index": idx, "dir": str(pd.relative_to(bvid_dir))})

    # iterate pages and merge
    merged_blocks: List[Tuple[float, float, List[str]]] = []
    merged_txt: List[str] = []
    offset = 0.0
    for p in pages_index:
        rel = p.get("dir")
        if not rel:
            # derive from index
            i = p.get("index") or 0
            # try to match pXX_* pattern
            candidates = sorted(pages_root.glob(f"p{i:02d}_*/"))
            if not candidates:
                continue
            page_dir = candidates[0]
        else:
            page_dir = bvid_dir / rel

        srt_path = page_dir / "asr" / "transcript.srt"
        txt_path = page_dir / "asr" / "transcript.txt"
        seg_path = page_dir / "asr" / "segments.json"
        # read duration for offset
        dur = _read_duration_from_segments(seg_path)

        # merge txt
        if txt_path.exists():
            merged_txt.append(f"[Page {p.get('index')}] {page_dir.name}")
            merged_txt.append(_to_simplified(txt_path.read_text(encoding="utf-8", errors="ignore").strip()))
            merged_txt.append("")

        # merge srt with offset
        blocks = _read_srt(srt_path)
        if blocks:
            for (st, et, lines) in blocks:
                merged_blocks.append((st + offset, et + offset, lines))
        # increase offset by duration if available, else by last end-start
        if dur is not None:
            offset += dur
        else:
            if blocks:
                last_end = blocks[-1][1]
                offset += last_end

    # write outputs
    _write_srt(merged_blocks, out_srt)
    out_txt.write_text("\n".join(merged_txt).strip() + "\n", encoding="utf-8")
    return out_srt, out_txt
