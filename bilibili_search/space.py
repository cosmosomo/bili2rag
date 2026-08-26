from __future__ import annotations

import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

from .session import ensure_buvid_cookies
from .wbi import WbiKeys, enc_wbi_params
from .search import parse_duration_to_seconds

# Anti-risk retries for space wbi/arc/search when hitting -352 (风控校验失败)
_RISK_RETRY = 6
_RISK_BACKOFF = 12.0


@dataclass(frozen=True)
class SpaceVideoItem:
    bvid: str
    title: str
    author: str
    mid: int
    created: int
    length_text: str
    length_seconds: Optional[int]
    play: int
    raw: Dict[str, Any]


def _as_int(v: Any) -> int:
    try:
        return int(v)
    except Exception:
        return 0


def _parse_vlist_item(obj: Dict[str, Any]) -> Optional[SpaceVideoItem]:
    bvid = obj.get("bvid")
    if not isinstance(bvid, str) or not bvid.startswith("BV"):
        return None
    title = str(obj.get("title") or "")
    author = str(obj.get("author") or "")
    length_text = str(obj.get("length") or "")
    return SpaceVideoItem(
        bvid=bvid,
        title=title.strip(),
        author=author.strip(),
        mid=_as_int(obj.get("mid")),
        created=_as_int(obj.get("created")),
        length_text=length_text,
        length_seconds=parse_duration_to_seconds(length_text),
        play=_as_int(obj.get("play")),
        raw=obj,
    )


def fetch_space_video_page(
    sess: requests.Session,
    keys: WbiKeys,
    *,
    mid: int,
    pn: int = 1,
    ps: int = 30,
    order: str = "pubdate",
    tid: int = 0,
    keyword: str = "",
) -> Tuple[List[SpaceVideoItem], Dict[str, Any]]:
    """Fetch one page of user's uploaded videos via x/space/wbi/arc/search."""

    ensure_buvid_cookies(sess)
    if mid <= 0:
        raise ValueError("mid must be > 0")
    if pn < 1:
        raise ValueError("pn must be >= 1")
    if ps < 1 or ps > 100:
        raise ValueError("ps must be between 1 and 100")

    params: Dict[str, Any] = {
        "mid": mid,
        "pn": pn,
        "ps": ps,
        "order": order,
        "tid": tid,
    }
    if keyword:
        params["keyword"] = keyword

    signed = enc_wbi_params(params, keys)
    url = "https://api.bilibili.com/x/space/wbi/arc/search"
    query = urllib.parse.urlencode(sorted(signed.items()), quote_via=urllib.parse.quote, safe="")
    j: Dict[str, Any] = {}
    for attempt in range(_RISK_RETRY):
        try:
            r = sess.get(f"{url}?{query}", timeout=25, headers={"Referer": f"https://space.bilibili.com/{mid}"})
            r.raise_for_status()
            j = r.json()
        except requests.RequestException:
            if attempt + 1 >= _RISK_RETRY:
                raise
            time.sleep(_RISK_BACKOFF * (attempt + 1))
            continue
        code = j.get("code")
        if code == 0:
            break
        if code == -352 and attempt + 1 < _RISK_RETRY:
            time.sleep(_RISK_BACKOFF * (attempt + 1))
            continue
        msg = j.get("message") or j.get("msg") or ""
        raise RuntimeError(f"space arc search API error code={code} msg={msg}")

    data = j.get("data") or {}
    vlist = (((data.get("list") or {}).get("vlist")) or [])
    if not isinstance(vlist, list):
        raise RuntimeError("space arc search response: data.list.vlist is not a list")

    items: List[SpaceVideoItem] = []
    for obj in vlist:
        if not isinstance(obj, dict):
            continue
        it = _parse_vlist_item(obj)
        if it is not None:
            items.append(it)
    return items, data


def utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def list_space_videos(
    sess: requests.Session,
    keys: WbiKeys,
    *,
    mid: int,
    order: str = "pubdate",
    tid: int = 0,
    keyword: str = "",
    limit: int = 0,
    max_pages: int = 0,
    page_sleep: float = 1.0,
    ps: int = 30,
) -> List[SpaceVideoItem]:
    out: List[SpaceVideoItem] = []
    pn = 1
    while True:
        items, meta = fetch_space_video_page(
            sess,
            keys,
            mid=mid,
            pn=pn,
            ps=ps,
            order=order,
            tid=tid,
            keyword=keyword,
        )
        if not items:
            return out
        for it in items:
            out.append(it)
            if limit and len(out) >= limit:
                return out

        pn += 1
        if max_pages and pn > max_pages:
            return out
        if page_sleep and page_sleep > 0:
            time.sleep(page_sleep)

