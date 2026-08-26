from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import requests


@dataclass(frozen=True)
class OwnerInfo:
    mid: int
    name: str


def _as_int(v: Any) -> int:
    try:
        return int(v)
    except Exception:
        return 0


def fetch_owner_info_by_bvid(sess: requests.Session, bvid: str) -> OwnerInfo:
    """Resolve uploader (owner) info from a seed BV id via view API."""

    if not bvid or not bvid.startswith("BV"):
        raise ValueError("bvid must start with BV")
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    r = sess.get(url, timeout=20)
    r.raise_for_status()
    j: Dict[str, Any] = r.json()
    code = j.get("code")
    if code != 0:
        msg = j.get("message") or j.get("msg") or ""
        raise RuntimeError(f"view API error code={code} msg={msg}")
    data = j.get("data") or {}
    owner = data.get("owner") or {}
    mid = _as_int(owner.get("mid"))
    name = str(owner.get("name") or "").strip()
    if not mid:
        raise RuntimeError("view API response missing owner.mid")
    return OwnerInfo(mid=mid, name=name or str(mid))

