from __future__ import annotations

import json
import shutil
from hashlib import sha256
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

from .naming import (
    build_library_dirname,
    extract_title,
    extract_upload_date_yyyymmdd,
    extract_uploader,
    sanitize_component,
)


@dataclass(frozen=True)
class ExportResult:
    bvid: str
    source_dir: Path
    dest_dir: Path
    copied: List[str]
    missing: List[str]


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def _copy_tree(src_dir: Path, dest_dir: Path) -> int:
    """Copy a directory tree into dest_dir and return number of files copied.

    We only copy files (not symlinks), preserving relative paths.
    """
    if not src_dir.exists() or not src_dir.is_dir():
        return 0
    count = 0
    for src in src_dir.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(src_dir)
        _copy_file(src, dest_dir / rel)
        count += 1
    return count


def _write_manifest(root: Path) -> None:
    files: List[Dict[str, Any]] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        hasher = sha256()
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
        files.append(
            {
                "path": str(path.relative_to(root)),
                "size": path.stat().st_size,
                "sha256": hasher.hexdigest(),
            }
        )
    _write_text(root / "manifest.json", json.dumps({"files": files}, ensure_ascii=False, indent=2) + "\n")


def rewrite_manifest(root: Path) -> None:
    """Public entry for in-place repairs: recompute manifest.json after edits."""
    _write_manifest(root)


def _pick_root_audio(source_dir: Path) -> Optional[Path]:
    candidates: List[Path] = []
    for pat in ("*.mp3", "*.m4a", "*.m4s", "*.aac", "*.wav", "*.flac"):
        candidates.extend(source_dir.glob(pat))
    audio_dir = source_dir / "audio"
    if audio_dir.exists():
        for pat in ("*.mp3", "*.m4a", "*.m4s", "*.aac", "*.wav", "*.flac"):
            candidates.extend(audio_dir.glob(pat))
    if not candidates:
        return None
    candidates = [p for p in candidates if p.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_size if p.exists() else 0, reverse=True)
    return candidates[0]


def _pick_root_cover(source_dir: Path) -> Optional[Path]:
    candidates = [p for p in source_dir.glob("cover.*") if p.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.name.lower())
    return candidates[0]


def _build_movie_nfo(info: Dict[str, Any], bvid: str, *, cover_rel: Optional[str]) -> str:
    """Build a portable Kodi/Emby-style NFO from the harvested metadata."""
    title = extract_title(info)
    uploader = extract_uploader(info)
    description = str(info.get("description") or info.get("desc") or "").strip()
    upload_date = extract_upload_date_yyyymmdd(info)

    root = ElementTree.Element("movie")

    def add_text(tag: str, value: Optional[str]) -> None:
        if value is None:
            return
        text = str(value).strip()
        if not text:
            return
        node = ElementTree.SubElement(root, tag)
        node.text = text

    add_text("title", title)
    add_text("originaltitle", title)
    add_text("plot", description)
    add_text("outline", description)
    add_text("studio", uploader)
    add_text("url", f"https://www.bilibili.com/video/{bvid}")

    unique_id = ElementTree.SubElement(root, "uniqueid", {"type": "bilibili", "default": "true"})
    unique_id.text = bvid

    if upload_date:
        add_text("premiered", f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}")
        add_text("year", upload_date[:4])

    duration = info.get("duration")
    if isinstance(duration, (int, float)) and duration > 0:
        runtime_minutes = max(1, round(float(duration) / 60.0))
        add_text("runtime", str(runtime_minutes))

    if cover_rel:
        add_text("thumb", cover_rel)

    return ElementTree.tostring(root, encoding="unicode") + "\n"


def export_bvid(
    bvid: str,
    *,
    output_root: Path = Path("output"),
    library_root: Path = Path("library"),
    if_exists: str = "skip",
    dry_run: bool = False,
) -> ExportResult:
    if if_exists not in {"fail", "skip", "overwrite"}:
        raise ValueError("if_exists must be one of: fail|skip|overwrite")

    source_dir = (output_root / bvid).resolve()
    meta_path = source_dir / "json" / "metadata.json"
    info = _read_json(meta_path)

    uploader = extract_uploader(info)
    uploader_dir = sanitize_component(uploader, max_len=60)
    video_dirname = build_library_dirname(info, bvid, max_len=160)
    dest_dir = (library_root / uploader_dir / video_dirname).resolve()

    if dest_dir.exists():
        if if_exists == "fail":
            raise FileExistsError(str(dest_dir))
        if if_exists == "skip":
            return ExportResult(bvid=bvid, source_dir=source_dir, dest_dir=dest_dir, copied=[], missing=[])
        if if_exists == "overwrite":
            if dry_run:
                return ExportResult(bvid=bvid, source_dir=source_dir, dest_dir=dest_dir, copied=[], missing=[])
            shutil.rmtree(dest_dir)

    copied: List[str] = []
    missing: List[str] = []

    rel_paths: List[str] = [
        "json/metadata.json",
        "json/core.json",
        "json/streams.json",
        "json/subtitles_index.json",
        "json/comments.json",
        "json/comments_structured.json",
        "json/comments_normalized.json",
        "json/pages_index.json",
        "json/pbp.json",
        "json/videoshot.json",
        "comments.txt",
        "danmaku.xml",
        "danmaku.json",
        "manifest.json",
        "asr/segments.json",
        "asr/transcript.txt",
        "asr/transcript.srt",
        "asr/transcript_all.txt",
        "asr/transcript_all.srt",
        "logs/harvest_run.json",
        "logs/asr_run.json",
    ]

    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)

    for rel in rel_paths:
        src = source_dir / rel
        if not src.exists():
            missing.append(rel)
            continue
        dest = dest_dir / rel
        if dry_run:
            copied.append(rel)
            continue
        _copy_file(src, dest)
        copied.append(rel)

    # audio (root only; multi-P audios stay under pages/*)
    audio_path = _pick_root_audio(source_dir)
    if audio_path is not None:
        audio_dest_rel = f"audio{audio_path.suffix.lower()}"
        if dry_run:
            copied.append(audio_dest_rel)
        else:
            _copy_file(audio_path, dest_dir / audio_dest_rel)
            copied.append(audio_dest_rel)
    else:
        missing.append("audio(root)")

    cover_path = _pick_root_cover(source_dir)
    cover_dest_rel: Optional[str] = None
    if cover_path is not None:
        cover_dest_rel = f"cover{cover_path.suffix.lower()}"
        if dry_run:
            copied.append(cover_dest_rel)
        else:
            _copy_file(cover_path, dest_dir / cover_dest_rel)
            copied.append(cover_dest_rel)
    else:
        missing.append("cover(root)")

    # Preserve additional raw artifacts so users can delete output/ safely.
    # - subtitles/: yt-dlp downloaded subtitle files (incl danmaku xml)
    # - pages/: multi-P per-page assets (audio/subtitles/asr/danmaku)
    # - json/pages/: per-page info jsons (when present)
    extras: List[tuple[str, Path, Path]] = [
        ("subtitles", source_dir / "subtitles", dest_dir / "subtitles"),
        ("pages", source_dir / "pages", dest_dir / "pages"),
        ("json/pages", source_dir / "json" / "pages", dest_dir / "json" / "pages"),
        ("snapshots", source_dir / "snapshots", dest_dir / "snapshots"),
    ]
    for label, src_dir, dst_dir in extras:
        if not src_dir.exists():
            continue
        if dry_run:
            # Approximate "copied" marker without enumerating all files.
            copied.append(f"{label}/(tree)")
            continue
        n = _copy_tree(src_dir, dst_dir)
        if n:
            copied.append(f"{label}/(tree:{n})")

    # Copy extra danmaku files if present (danmaku_<cid>.xml/.json) at output root.
    for pattern in ("danmaku_*.xml", "danmaku_*.json"):
        for src in sorted(source_dir.glob(pattern)):
            if not src.is_file():
                continue
            rel = src.name
            if dry_run:
                copied.append(rel)
                continue
            _copy_file(src, dest_dir / rel)
            copied.append(rel)

    # bilibili link shortcut
    url = f"https://www.bilibili.com/video/{bvid}"
    shortcut = "[InternetShortcut]\n" f"URL={url}\n"
    if dry_run:
        copied.append("bilibili.url")
    else:
        _write_text(dest_dir / "bilibili.url", shortcut)
        copied.append("bilibili.url")

    if dry_run:
        copied.append("movie.nfo")
    else:
        _write_text(dest_dir / "movie.nfo", _build_movie_nfo(info, bvid, cover_rel=cover_dest_rel))
        copied.append("movie.nfo")

    # lightweight source index for this folder
    source_index = {
        "bvid": bvid,
        "url": url,
        "uploader": uploader,
        "source_dir": str(source_dir),
        "exported_at": datetime_utc_iso(),
        "files": copied,
        "missing": missing,
    }
    if dry_run:
        copied.append("source.json")
    else:
        _write_text(dest_dir / "source.json", json.dumps(source_index, ensure_ascii=False, indent=2) + "\n")
        copied.append("source.json")
        _write_manifest(dest_dir)

    return ExportResult(bvid=bvid, source_dir=source_dir, dest_dir=dest_dir, copied=copied, missing=missing)


def datetime_utc_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def iter_bvid_dirs(output_root: Path) -> List[str]:
    out = []
    if not output_root.exists():
        raise FileNotFoundError(str(output_root))
    for p in output_root.iterdir():
        if not p.is_dir():
            continue
        name = p.name
        if not name.startswith("BV"):
            continue
        if (p / "json" / "metadata.json").exists():
            out.append(name)
    out.sort()
    return out
