from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def _print_utf8(line: str) -> None:
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _extract_bvid_from_dirname(name: str) -> Optional[str]:
    m = re.search(r"(BV[0-9A-Za-z]+)$", name)
    return m.group(1) if m else None


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class VideoEntry:
    bvid: str
    exported_dir: Path  # library/<uploader>/<date_title_bvid>/

    @property
    def title_dirname(self) -> str:
        return self.exported_dir.name


def _iter_exported_dirs_under_uploader(uploader_dir: Path) -> List[VideoEntry]:
    if not uploader_dir.exists() or not uploader_dir.is_dir():
        raise FileNotFoundError(str(uploader_dir))

    out: List[VideoEntry] = []
    for p in uploader_dir.iterdir():
        if not p.is_dir():
            continue
        bvid = _extract_bvid_from_dirname(p.name)
        if not bvid:
            continue
        out.append(VideoEntry(bvid=bvid, exported_dir=p.resolve()))
    # Sort stable by directory name (date prefix makes it chronological)
    out.sort(key=lambda e: e.title_dirname)
    return out


def _iter_exported_dirs_from_topic(topic_dir: Path) -> List[VideoEntry]:
    if not topic_dir.exists() or not topic_dir.is_dir():
        raise FileNotFoundError(str(topic_dir))

    out: List[VideoEntry] = []
    for p in topic_dir.iterdir():
        if not p.is_dir():
            continue
        pointer = p / "pointer.json"
        if not pointer.exists():
            continue
        try:
            payload = json.loads(_read_text(pointer))
        except Exception as e:
            raise RuntimeError(f"Invalid pointer.json: {pointer}") from e
        bvid = str(payload.get("bvid") or "").strip()
        exported_dir = str(payload.get("exported_dir") or "").strip()
        if not bvid or not exported_dir:
            raise RuntimeError(f"pointer.json missing fields (bvid/exported_dir): {pointer}")
        out.append(VideoEntry(bvid=bvid, exported_dir=Path(exported_dir).resolve()))

    # Sort by exported dirname for readability
    out.sort(key=lambda e: e.title_dirname)
    return out


def _load_transcript(exported_dir: Path) -> Optional[Tuple[str, List[Path]]]:
    """Load transcript text from an exported video directory.

    Supported layouts:
    - asr/transcript.txt (single)
    - asr/transcript_all.txt (merged multi-page)
    - pages/<page>/asr/transcript.txt (multi-page, per-page only)
      In this case we concatenate pages in directory order.
    """

    # Root ASR outputs (preferred).
    p1 = exported_dir / "asr" / "transcript.txt"
    if p1.exists():
        return (_read_text(p1), [p1])
    p2 = exported_dir / "asr" / "transcript_all.txt"
    if p2.exists():
        return (_read_text(p2), [p2])

    # Fallback: per-page transcripts.
    pages_dir = exported_dir / "pages"
    if not pages_dir.exists() or not pages_dir.is_dir():
        return None

    page_dirs = [p for p in pages_dir.iterdir() if p.is_dir()]
    page_dirs.sort(key=lambda p: p.name)

    parts: List[str] = []
    sources: List[Path] = []
    for pd in page_dirs:
        pp = pd / "asr" / "transcript.txt"
        if pp.exists():
            parts.append(f"===== {pd.name} =====\n")
            parts.append(_read_text(pp).rstrip() + "\n\n")
            sources.append(pp)
            continue
        pp_all = pd / "asr" / "transcript_all.txt"
        if pp_all.exists():
            parts.append(f"===== {pd.name} =====\n")
            parts.append(_read_text(pp_all).rstrip() + "\n\n")
            sources.append(pp_all)

    if not sources:
        return None
    return ("".join(parts).rstrip() + "\n", sources)


def _basic_header(entry: VideoEntry) -> str:
    url = f"https://www.bilibili.com/video/{entry.bvid}"
    return "\n".join(
        [
            f"# bvid: {entry.bvid}",
            f"# url: {url}",
            f"# source_dir: {entry.exported_dir}",
            "",
        ]
    )


def _sanitize_filename_component(text: str, max_len: int = 120) -> str:
    # Keep this script standalone: re-implement a minimal Windows-safe sanitizer.
    # Prefer bilibili_library.naming.sanitize_component when available.
    try:
        from bilibili_library.naming import sanitize_component

        return sanitize_component(text, max_len=max_len)
    except Exception:
        invalid = re.compile(r'[<>:"/\\\\|?*\\x00-\\x1F\\x7F]')
        s = invalid.sub("_", (text or "").strip())
        s = re.sub(r"\\s+", " ", s).strip().rstrip(" .")
        if not s:
            s = "untitled"
        return s[:max_len].rstrip(" .") or "untitled"


def export_txt_bundle(
    entries: Sequence[VideoEntry],
    *,
    dest_dir: Path,
    with_header: bool,
    concat_all: bool,
    if_missing: str,
) -> Dict[str, Any]:
    """Export per-video transcript.txt into a single folder.

    - Writes one txt per video: <exported_dir_name>.txt
    - Optionally writes ALL.txt (concatenated).
    - Writes index.json with mapping.
    """

    dest_dir = dest_dir.resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)

    index: List[Dict[str, Any]] = []
    all_parts: List[str] = []
    ok = 0
    missing = 0

    for e in entries:
        loaded = _load_transcript(e.exported_dir)
        if loaded is None:
            missing += 1
            msg = f"[MISSING] {e.bvid} transcript not found under: {e.exported_dir}"
            if if_missing == "fail":
                raise FileNotFoundError(msg)
            _print_utf8(msg)
            continue

        (text, sources) = loaded
        if with_header:
            text = _basic_header(e) + text

        fname = _sanitize_filename_component(e.title_dirname, max_len=180) + ".txt"
        out_path = dest_dir / fname
        _write_text(out_path, text)
        ok += 1

        rec = {
            "bvid": e.bvid,
            "exported_dir": str(e.exported_dir),
            "transcript_sources": [str(p) for p in sources],
            "exported_dir_name": e.title_dirname,
            "exported_txt": str(out_path),
        }
        index.append(rec)

        if concat_all:
            all_parts.append(f"===== {e.title_dirname} =====\n")
            all_parts.append(text.rstrip() + "\n\n")

    if concat_all:
        _write_text(dest_dir / "ALL.txt", "".join(all_parts))

    _write_json(
        dest_dir / "index.json",
        {
            "generated_at_utc": _utc_now_iso(),
            "dest_dir": str(dest_dir),
            "ok": ok,
            "missing": missing,
            "items": index,
        },
    )

    return {"dest_dir": str(dest_dir), "ok": ok, "missing": missing}


def main(argv: Optional[List[str]] = None) -> None:
    repo_dir = Path(__file__).resolve().parents[1]  # .../BILIBILI_GET
    if str(repo_dir) not in sys.path:
        sys.path.insert(0, str(repo_dir))

    parser = argparse.ArgumentParser(description="Export transcripts into a single folder by uploader or by topic.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--uploader", default="", help="Uploader dir name under library/ (e.g. 硅谷101)")
    src.add_argument("--topic", default="", help="Topic name under library/_topics/ (e.g. 个人IP)")

    parser.add_argument("--library-root", default="library")
    parser.add_argument("--topic-root", default="library/_topics")
    parser.add_argument("--dest-root", default="library/_exports/txt")
    parser.add_argument("--with-header", action="store_true", help="Prepend a small header to each exported txt.")
    parser.add_argument("--concat-all", action="store_true", help="Also write ALL.txt (concatenated).")
    parser.add_argument("--if-missing", choices=["fail", "skip"], default="skip")
    args = parser.parse_args(argv)

    library_root = Path(args.library_root)
    dest_root = Path(args.dest_root)

    if args.uploader:
        uploader = str(args.uploader).strip()
        uploader_dir = (library_root / uploader).resolve()
        entries = _iter_exported_dirs_under_uploader(uploader_dir)
        dest_dir = (dest_root / "uploader" / _sanitize_filename_component(uploader, max_len=80)).resolve()
        rep = export_txt_bundle(
            entries,
            dest_dir=dest_dir,
            with_header=bool(args.with_header),
            concat_all=bool(args.concat_all),
            if_missing=str(args.if_missing),
        )
        _print_utf8(f"[OK] uploader={uploader} items={len(entries)} exported_ok={rep['ok']} missing={rep['missing']} dest={rep['dest_dir']}")
        raise SystemExit(0)

    topic = str(args.topic).strip()
    topic_dir = (Path(args.topic_root) / topic).resolve()
    entries = _iter_exported_dirs_from_topic(topic_dir)
    dest_dir = (dest_root / "topic" / _sanitize_filename_component(topic, max_len=80)).resolve()
    rep = export_txt_bundle(
        entries,
        dest_dir=dest_dir,
        with_header=bool(args.with_header),
        concat_all=bool(args.concat_all),
        if_missing=str(args.if_missing),
    )
    _print_utf8(f"[OK] topic={topic} items={len(entries)} exported_ok={rep['ok']} missing={rep['missing']} dest={rep['dest_dir']}")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
