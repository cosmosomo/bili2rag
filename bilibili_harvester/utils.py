from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def ensure_dir(p: str | Path) -> Path:
    path = Path(p)
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, obj: Any):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_b23(url: str, proxy: Optional[str] = None) -> str:
    if "b23.tv" not in url:
        return url
    sess = requests.Session()
    if proxy:
        sess.proxies.update({"http": proxy, "https": proxy})
    resp = sess.head(url, allow_redirects=True, timeout=10)
    return resp.url


def extract_bvid(url: str) -> Optional[str]:
    m = re.search(r"BV[0-9A-Za-z]+", url)
    return m.group(0) if m else None


_URL_RE = re.compile(r"https?://\S+")
_BVID_RE = re.compile(r"BV[0-9A-Za-z]+")


def extract_targets(lines: Iterable[str]) -> List[str]:
    """Extract bilibili target URLs from arbitrary lines.

    Supported formats:
    - full URL anywhere in the line (including b23.tv short links)
    - bare BV id anywhere in the line (converted to https://www.bilibili.com/video/<bvid>)
    """

    out: List[str] = []
    seen: set[str] = set()
    for raw in lines:
        line = (raw or "").strip()
        if not line or line.startswith("#"):
            continue

        m = _URL_RE.search(line)
        if m:
            target = m.group(0)
        else:
            m2 = _BVID_RE.search(line)
            if not m2:
                continue
            target = f"https://www.bilibili.com/video/{m2.group(0)}"

        if target in seen:
            continue
        seen.add(target)
        out.append(target)
    return out


def extract_cid_from_info(info: Dict[str, Any]) -> Optional[int]:
    # yt-dlp info dict: try prefer current chapter/first entry
    if "cid" in info:
        try:
            return int(info["cid"])  # type: ignore[arg-type]
        except Exception:
            pass
    # try from entries (playlist of pages)
    if "entries" in info and isinstance(info["entries"], Iterable):
        for e in info["entries"]:
            if isinstance(e, dict) and "cid" in e:
                try:
                    return int(e["cid"])  # type: ignore[arg-type]
                except Exception:
                    continue
    return None


def requests_session(proxy: Optional[str] = None, headers: Optional[Dict[str, str]] = None, cookie_string: Optional[str] = None) -> requests.Session:
    sess = requests.Session()
    sess.headers.update(DEFAULT_HEADERS)
    if headers:
        sess.headers.update(headers)
    if proxy:
        sess.proxies.update({"http": proxy, "https": proxy})
    if cookie_string:
        sess.headers["Cookie"] = cookie_string
    return sess
