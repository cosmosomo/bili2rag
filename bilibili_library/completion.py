"""Completion semantics: the single source of truth for "is this video done?".

Design rules (see docs/DESIGN.md):
- done() is THE only completion predicate; every entrypoint (batch engine, doctor,
  repair) must consult it instead of ad-hoc "directory exists" checks.
- failures.jsonl is append-only, human-readable, and NEVER read for decisions.
- unavailable.jsonl is the ONLY persistent state file; it exists because
  "never-entered-library" deletions cannot be derived from the filesystem.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

LEDGER_DIRNAME = "_ledger"
FAILURES_FILENAME = "failures.jsonl"
UNAVAILABLE_FILENAME = "unavailable.jsonl"

_BV_SUFFIX_RE = re.compile(r"(BV[0-9A-Za-z]+)$")

AUDIO_NAMES = ("audio.mp3", "audio.m4a")
AUDIO_SUFFIXES = (".mp3", ".m4a", ".m4s", ".aac", ".wav", ".flac")

# Status of a video directory relative to the completion predicate.
CLASS_OK = "ok"
CLASS_NO_TRANSCRIPT_WITH_AUDIO = "no_transcript_with_audio"  # repairable in place
CLASS_NO_AUDIO = "no_audio"  # needs a re-harvest


def extract_bvid_from_dirname(name: str) -> Optional[str]:
    m = _BV_SUFFIX_RE.search(name.strip())
    return m.group(1) if m else None


def has_transcript(video_dir: Path) -> bool:
    if (video_dir / "asr" / "transcript.txt").exists():
        return True
    if (video_dir / "asr" / "transcript_all.txt").exists():
        return True
    pages_dir = video_dir / "pages"
    if pages_dir.is_dir():
        for pdir in pages_dir.iterdir():
            if not pdir.is_dir():
                continue
            if (pdir / "asr" / "transcript.txt").exists() or (pdir / "asr" / "transcript_all.txt").exists():
                return True
    return False


def has_audio(video_dir: Path) -> bool:
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
                if f.is_file() and f.suffix.lower() in AUDIO_SUFFIXES:
                    return True
    return False


def is_done(video_dir: Optional[Path]) -> bool:
    """done(d) := dir exists AND a transcript exists."""
    if video_dir is None or not video_dir.is_dir():
        return False
    return has_transcript(video_dir)


def classify(video_dir: Optional[Path]) -> str:
    if is_done(video_dir):
        return CLASS_OK
    if video_dir is not None and has_audio(video_dir):
        return CLASS_NO_TRANSCRIPT_WITH_AUDIO
    return CLASS_NO_AUDIO


def iter_video_dirs(library_root: Path) -> Iterator[Path]:
    """Iterate library/<uploader>/<video_dir>, skipping _-prefixed service dirs."""
    if not library_root.is_dir():
        return
    for up_dir in sorted(library_root.iterdir()):
        if not up_dir.is_dir() or up_dir.name.startswith("_"):
            continue
        for v_dir in sorted(up_dir.iterdir()):
            if v_dir.is_dir() and extract_bvid_from_dirname(v_dir.name):
                yield v_dir


def build_video_index(library_root: Path) -> Dict[str, Path]:
    """One-pass bvid -> video_dir index for batch operations.

    find_video_dir scans the whole library per call (O(items x library));
    batches should build this once and refresh entries as they export.
    """
    idx: Dict[str, Path] = {}
    for v_dir in iter_video_dirs(library_root):
        bvid = extract_bvid_from_dirname(v_dir.name)
        if bvid and bvid not in idx:
            idx[bvid] = v_dir
    return idx


def find_video_dir(library_root: Path, bvid: str, *, index: Optional[Dict[str, Path]] = None) -> Optional[Path]:
    """Locate the library video dir for a bvid (two-level scan, service dirs skipped).

    Pass `index` from build_video_index to avoid the O(library) scan.
    """
    if index is not None:
        return index.get(bvid)
    if not library_root.is_dir():
        return None
    for v_dir in iter_video_dirs(library_root):
        if v_dir.name.endswith(bvid):
            return v_dir
    return None


def is_done_bvid(library_root: Path, bvid: str) -> bool:
    return is_done(find_video_dir(library_root, bvid))


# ---------------------------------------------------------------- ledger files


def _ledger_path(library_root: Path, filename: str) -> Path:
    return library_root / LEDGER_DIRNAME / filename


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_failure(
    library_root: Path,
    *,
    bvid: str,
    stage: str,
    error: str = "",
    title: str = "",
    run_id: str = "",
) -> None:
    """Append a failure record. Pure log: no status, never read for decisions."""
    record: Dict[str, Any] = {
        "ts": _utc_now_iso(),
        "bvid": bvid,
        "stage": stage,
        "error": error,
    }
    if title:
        record["title"] = title
    if run_id:
        record["run_id"] = run_id
    path = _ledger_path(library_root, FAILURES_FILENAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_failures(library_root: Path, limit: int = 0) -> List[Dict[str, Any]]:
    path = _ledger_path(library_root, FAILURES_FILENAME)
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:] if limit and limit > 0 else rows


def list_unavailable(library_root: Path) -> Dict[str, Dict[str, Any]]:
    path = _ledger_path(library_root, UNAVAILABLE_FILENAME)
    if not path.exists():
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            bvid = rec.get("bvid")
            if isinstance(bvid, str) and bvid:
                out[bvid] = rec
    return out


def is_unavailable(library_root: Path, bvid: str) -> bool:
    return bvid in list_unavailable(library_root)


def mark_unavailable(library_root: Path, bvid: str, *, reason: str = "") -> None:
    path = _ledger_path(library_root, UNAVAILABLE_FILENAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = list_unavailable(library_root)
    if bvid in current:
        return
    current[bvid] = {
        "bvid": bvid,
        "reason": reason,
        "marked_at": _utc_now_iso(),
    }
    lines = [json.dumps(current[k], ensure_ascii=False) for k in sorted(current)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def clear_unavailable(library_root: Path, bvid: str) -> bool:
    path = _ledger_path(library_root, UNAVAILABLE_FILENAME)
    current = list_unavailable(library_root)
    if bvid not in current:
        return False
    del current[bvid]
    if current:
        lines = [json.dumps(current[k], ensure_ascii=False) for k in sorted(current)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        path.write_text("", encoding="utf-8")
    return True
