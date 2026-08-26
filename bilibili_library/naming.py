from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional


_INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1F\x7F]')
_WHITESPACE_RE = re.compile(r"\s+")
_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_component(text: str, *, replacement: str = "_", max_len: int = 80) -> str:
    if max_len < 1:
        raise ValueError("max_len must be >= 1")
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if not isinstance(replacement, str) or replacement == "":
        raise ValueError("replacement must be a non-empty str")

    s = text.strip()
    s = _INVALID_CHARS_RE.sub(replacement, s)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    s = s.rstrip(" .")

    if not s:
        raise ValueError("empty filename component after sanitization")

    if s.upper() in _RESERVED_NAMES:
        s = f"{s}_"

    if len(s) > max_len:
        s = s[:max_len].rstrip(" .")
        if not s:
            raise ValueError("filename component is empty after truncation")
        if s.upper() in _RESERVED_NAMES:
            s = f"{s}_"

    return s


def extract_upload_date_yyyymmdd(info: Dict[str, Any]) -> Optional[str]:
    ud = info.get("upload_date")
    if isinstance(ud, str) and len(ud) == 8 and ud.isdigit():
        return ud
    ts = info.get("timestamp")
    if isinstance(ts, (int, float)) and ts > 0:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y%m%d")
    rts = info.get("release_timestamp")
    if isinstance(rts, (int, float)) and rts > 0:
        return datetime.fromtimestamp(float(rts), tz=timezone.utc).strftime("%Y%m%d")
    return None


def format_date_yyyy_mm_dd(yyyymmdd: str) -> str:
    if len(yyyymmdd) != 8 or not yyyymmdd.isdigit():
        raise ValueError("invalid yyyymmdd")
    return f"{yyyymmdd[0:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def extract_title(info: Dict[str, Any]) -> str:
    title = info.get("title") or info.get("fulltitle") or info.get("alt_title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    raise KeyError("metadata missing title")


def extract_uploader(info: Dict[str, Any]) -> str:
    uploader = info.get("uploader")
    if isinstance(uploader, str) and uploader.strip():
        return uploader.strip()
    owner = info.get("owner")
    if isinstance(owner, dict):
        name = owner.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        mid = owner.get("mid")
        if isinstance(mid, int) and mid > 0:
            return f"uid{mid}"
    uid = info.get("uploader_id")
    if isinstance(uid, (int, str)) and str(uid).strip():
        return f"uid{uid}"
    raise KeyError("metadata missing uploader")


def build_library_dirname(info: Dict[str, Any], bvid: str, *, max_len: int = 160) -> str:
    if not bvid or not isinstance(bvid, str):
        raise ValueError("bvid must be a non-empty str")
    bvid = bvid.strip()
    if not bvid.startswith("BV"):
        raise ValueError(f"invalid bvid: {bvid}")

    title = extract_title(info)
    date8 = extract_upload_date_yyyymmdd(info)
    date_part = format_date_yyyy_mm_dd(date8) if date8 else None

    fixed = f"{bvid}"
    if date_part:
        fixed = f"{date_part}_{bvid}"
        remaining = max_len - (len(date_part) + 1 + len(bvid) + 1)
    else:
        remaining = max_len - (len(bvid) + 1)

    if remaining < 8:
        remaining = 8

    safe_title = sanitize_component(title, max_len=remaining)

    if date_part:
        return f"{date_part}_{safe_title}_{bvid}"
    return f"{safe_title}_{bvid}"
