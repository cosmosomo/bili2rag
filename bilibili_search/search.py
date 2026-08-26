from __future__ import annotations

import json
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from .session import ensure_buvid_cookies
from .wbi import WbiKeys, enc_wbi_params, extract_wbi_keys_from_nav_json


_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    return _TAG_RE.sub("", text or "").strip()


def parse_duration_to_seconds(s: str) -> Optional[int]:
    if not isinstance(s, str) or not s.strip():
        return None
    parts = s.strip().split(":")
    try:
        nums = [int(p) for p in parts]
    except Exception:
        return None
    if len(nums) == 2:
        m, sec = nums
        return m * 60 + sec
    if len(nums) == 3:
        h, m, sec = nums
        return h * 3600 + m * 60 + sec
    return None


@dataclass(frozen=True)
class VideoSearchItem:
    bvid: str
    aid: int
    title: str
    title_plain: str
    author: str
    mid: int
    play: int
    favorites: int
    review: int
    danmaku: int
    pubdate: int
    duration_text: str
    duration_seconds: Optional[int]
    raw: Dict[str, Any]


def _as_int(v: Any) -> int:
    try:
        return int(v)
    except Exception:
        return 0


def fetch_wbi_keys(sess: requests.Session) -> WbiKeys:
    r = sess.get("https://api.bilibili.com/x/web-interface/nav", timeout=15)
    r.raise_for_status()
    j = r.json()
    return extract_wbi_keys_from_nav_json(j)


def search_video_page(
    sess: requests.Session,
    keys: WbiKeys,
    *,
    keyword: str,
    order: str = "click",
    duration: int = 0,
    tids: int = 0,
    page: int = 1,
) -> Tuple[List[VideoSearchItem], Dict[str, Any]]:
    ensure_buvid_cookies(sess)

    if not keyword or not keyword.strip():
        raise ValueError("keyword is required")
    if page < 1:
        raise ValueError("page must be >= 1")

    params: Dict[str, Any] = {
        "search_type": "video",
        "keyword": keyword,
        "order": order,
        "duration": duration,
        "tids": tids,
        "page": page,
    }
    signed = enc_wbi_params(params, keys)
    url = "https://api.bilibili.com/x/web-interface/wbi/search/type"
    # Important: WBI signing requires spaces encoded as %20 (encodeURIComponent behavior),
    # while requests' default params encoding uses application/x-www-form-urlencoded (+).
    query = urllib.parse.urlencode(sorted(signed.items()), quote_via=urllib.parse.quote, safe="")
    r = sess.get(f"{url}?{query}", timeout=20)
    r.raise_for_status()
    j = r.json()

    code = j.get("code")
    if code != 0:
        msg = j.get("message") or j.get("msg") or ""
        raise RuntimeError(f"search API error code={code} msg={msg}")

    data = j.get("data") or {}
    results = data.get("result") or []
    if not isinstance(results, list):
        raise RuntimeError("search API response: data.result is not a list")

    items: List[VideoSearchItem] = []
    for obj in results:
        if not isinstance(obj, dict):
            continue
        bvid = obj.get("bvid")
        if not isinstance(bvid, str) or not bvid.startswith("BV"):
            continue
        title = obj.get("title") or ""
        author = obj.get("author") or ""
        duration_text = obj.get("duration") or ""
        items.append(
            VideoSearchItem(
                bvid=bvid,
                aid=_as_int(obj.get("aid") or obj.get("id")),
                title=str(title),
                title_plain=strip_html(str(title)),
                author=str(author),
                mid=_as_int(obj.get("mid")),
                play=_as_int(obj.get("play")),
                favorites=_as_int(obj.get("favorites")),
                review=_as_int(obj.get("review")),
                danmaku=_as_int(obj.get("video_review")),
                pubdate=_as_int(obj.get("pubdate")),
                duration_text=str(duration_text),
                duration_seconds=parse_duration_to_seconds(str(duration_text)),
                raw=obj,
            )
        )
    return items, data


@dataclass(frozen=True)
class VideoFilter:
    min_play: int = 0
    min_favorites: int = 0
    min_review: int = 0
    min_danmaku: int = 0
    min_duration_seconds: int = 0
    max_duration_seconds: int = 0
    require_mid: int = 0
    exclude_text: Tuple[str, ...] = ()


def passes_filter(item: VideoSearchItem, flt: VideoFilter) -> bool:
    if flt.require_mid and item.mid != flt.require_mid:
        return False
    if item.play < flt.min_play:
        return False
    if item.favorites < flt.min_favorites:
        return False
    if item.review < flt.min_review:
        return False
    if item.danmaku < flt.min_danmaku:
        return False
    if flt.min_duration_seconds:
        if item.duration_seconds is None or item.duration_seconds < flt.min_duration_seconds:
            return False
    if flt.max_duration_seconds:
        if item.duration_seconds is None or item.duration_seconds > flt.max_duration_seconds:
            return False
    if flt.exclude_text:
        hay = f"{item.title_plain} {item.author}".lower()
        for w in flt.exclude_text:
            if w.lower() in hay:
                return False
    return True


def utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_targets_txt(path: Path, items: Iterable[VideoSearchItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(f"【{it.title_plain}】https://www.bilibili.com/video/{it.bvid}\n")


def search_videos(
    sess: requests.Session,
    *,
    keyword: str,
    order: str,
    duration: int,
    tids: int,
    limit: int,
    max_pages: int,
    page_sleep: float,
    flt: VideoFilter,
) -> List[VideoSearchItem]:
    keys = fetch_wbi_keys(sess)
    out: List[VideoSearchItem] = []
    page = 1
    while True:
        items, meta = search_video_page(
            sess,
            keys,
            keyword=keyword,
            order=order,
            duration=duration,
            tids=tids,
            page=page,
        )
        for it in items:
            if passes_filter(it, flt):
                out.append(it)
                if limit and len(out) >= limit:
                    return out
        page += 1
        if max_pages and page > max_pages:
            return out
        if not items:
            return out
        if page_sleep and page_sleep > 0:
            time.sleep(page_sleep)
