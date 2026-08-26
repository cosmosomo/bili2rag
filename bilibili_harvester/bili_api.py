from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

import requests

from .utils import requests_session, extract_cid_from_info


def parse_danmaku_xml(xml_text: str) -> Dict[str, Any]:
    """Convert Bilibili's XML danmaku payload into a schema suitable for RAG or analytics."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise ValueError("invalid danmaku XML") from exc

    items: List[Dict[str, Any]] = []
    for node in root.findall("d"):
        raw_position = node.get("p")
        if not raw_position:
            raise ValueError("danmaku item is missing p attribute")
        fields = raw_position.split(",")
        if len(fields) < 8:
            raise ValueError(f"danmaku item has invalid p attribute: {raw_position}")
        try:
            progress_seconds = float(fields[0])
            mode = int(fields[1])
            font_size = int(fields[2])
            color = int(fields[3])
            sent_at = int(fields[4])
            pool = int(fields[5])
        except ValueError as exc:
            raise ValueError(f"danmaku item has non-numeric p attribute: {raw_position}") from exc
        items.append(
            {
                "progress_seconds": progress_seconds,
                "mode": mode,
                "font_size": font_size,
                "color": color,
                "sent_at": sent_at,
                "pool": pool,
                "sender_hash": fields[6],
                "dmid": fields[7],
                "text": node.text or "",
            }
        )
    return {"schema_version": 1, "items": items}


def fetch_danmaku_xml(info: Dict[str, Any], cookie_header: Optional[str], proxy: Optional[str]) -> Optional[str]:
    """获取基础 XML 弹幕：https://comment.bilibili.com/<cid>.xml
    仅获取主 cid 的XML；若需要多P/历史，请自行扩展。
    """
    cid = extract_cid_from_info(info)
    if not cid:
        return None
    url = f"https://comment.bilibili.com/{cid}.xml"
    sess = requests_session(proxy=proxy, cookie_string=cookie_header)
    r = sess.get(url, timeout=15)
    if r.status_code == 200:
        return r.text
    return None


def fetch_comments_snapshot(bvid: str, cookie_header: Optional[str], proxy: Optional[str], pages: int = 1) -> Dict[str, Any]:
    """尽力获取评论（首屏/若干页）。接口与参数可能变化，失败时返回空结构。

    2025+ 风控说明：
    - 旧的 bvid 主路径（x/v2/reply?bvid=...）与 sort=0 无 Referer 的 oid 路径均会拿到空 replies；
    - x/v2/reply/main + 视频 Referer 目前可用（至少返回首屏热评 + all_count），全量受服务端 is_end 限制。
    """
    sess = requests_session(proxy=proxy, cookie_string=cookie_header)
    referer = f"https://www.bilibili.com/video/{bvid}"

    # Path A (preferred): resolve aid via view API, then reply/main with video Referer.
    try:
        v = sess.get(
            "https://api.bilibili.com/x/web-interface/view",
            params={"bvid": bvid},
            headers={"Referer": referer},
            timeout=15,
        )
        if v.status_code == 200 and v.json().get("code") == 0:
            aid = ((v.json().get("data") or {}).get("aid"))
            if aid:
                out: Dict[str, Any] = {"pages": [], "oid": aid, "api": "reply/main"}
                next_cursor = 0
                for _ in range(max(1, pages)):
                    r = sess.get(
                        "https://api.bilibili.com/x/v2/reply/main",
                        params={"type": 1, "oid": aid, "mode": 2, "next": next_cursor},
                        headers={"Referer": referer},
                        timeout=15,
                    )
                    if r.status_code != 200:
                        break
                    j = r.json()
                    out["pages"].append(j)
                    if j.get("code") != 0:
                        break
                    cursor = ((j.get("data") or {}).get("cursor")) or {}
                    nxt = cursor.get("next")
                    if cursor.get("is_end") or nxt is None or nxt == next_cursor:
                        break
                    next_cursor = nxt
                if _comments_have_replies(out):
                    return out
    except requests.RequestException:
        pass

    # Path B (legacy fallback): bvid params first; if empty, oid via view API.
    base = "https://api.bilibili.com/x/v2/reply"
    out = {"pages": []}
    ok = False
    for pn in range(1, pages + 1):
        params = {"bvid": bvid, "pn": pn}
        try:
            resp = sess.get(base, params=params, timeout=15)
            if resp.status_code == 200:
                j = resp.json()
                out["pages"].append(j)
                if j.get("code") == 0:
                    ok = True
            else:
                break
        except requests.RequestException:
            break
    if ok and _comments_have_replies(out):
        return out

    # fallback: resolve aid via view API, then use type=1&oid=aid
    try:
        v = sess.get("https://api.bilibili.com/x/web-interface/view", params={"bvid": bvid}, timeout=15)
        if v.status_code == 200:
            data = v.json().get("data") or {}
            aid = data.get("aid")
            if aid:
                out = {"pages": []}
                for pn in range(1, pages + 1):
                    params = {"type": 1, "oid": aid, "pn": pn, "sort": 0}
                    r2 = sess.get(base, params=params, timeout=15)
                    if r2.status_code == 200:
                        out["pages"].append(r2.json())
                    else:
                        break
    except requests.RequestException:
        pass
    return out


def _comments_have_replies(comments: Dict[str, Any]) -> bool:
    for page in comments.get("pages") or []:
        if not isinstance(page, dict):
            continue
        replies = ((page.get("data") or {}).get("replies") or [])
        if replies:
            return True
    return False
