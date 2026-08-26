from __future__ import annotations

import time
import urllib.parse
from dataclasses import dataclass
from functools import reduce
from hashlib import md5
from typing import Any, Dict, Tuple


MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52
]


@dataclass(frozen=True)
class WbiKeys:
    img_key: str
    sub_key: str


def _get_mixin_key(raw_wbi_key: str) -> str:
    return reduce(lambda s, i: s + raw_wbi_key[i], MIXIN_KEY_ENC_TAB, "")[:32]


def _filter_value(v: Any) -> str:
    s = str(v)
    # filter "!'()*" chars
    return "".join(ch for ch in s if ch not in "!'()*")


def enc_wbi_params(params: Dict[str, Any], keys: WbiKeys, *, now: int | None = None) -> Dict[str, str]:
    """Return signed query parameters for WBI protected endpoints."""
    if not keys.img_key or not keys.sub_key:
        raise ValueError("invalid WBI keys")

    curr_time = int(now if now is not None else round(time.time()))
    mixin_key = _get_mixin_key(keys.img_key + keys.sub_key)

    p: Dict[str, str] = {str(k): _filter_value(v) for k, v in params.items()}
    p["wts"] = str(curr_time)

    # sort and url-encode
    items = sorted(p.items(), key=lambda kv: kv[0])
    query = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
        for (k, v) in items
    )
    w_rid = md5((query + mixin_key).encode("utf-8")).hexdigest()
    p["w_rid"] = w_rid
    return p


def extract_wbi_keys_from_nav_json(nav: Dict[str, Any]) -> WbiKeys:
    data = nav.get("data") or {}
    wbi_img = data.get("wbi_img") or {}
    img_url = wbi_img.get("img_url")
    sub_url = wbi_img.get("sub_url")
    if not isinstance(img_url, str) or not isinstance(sub_url, str):
        raise KeyError("nav.data.wbi_img.img_url/sub_url missing")
    img_key = img_url.rsplit("/", 1)[-1].split(".", 1)[0]
    sub_key = sub_url.rsplit("/", 1)[-1].split(".", 1)[0]
    if not img_key or not sub_key:
        raise ValueError("invalid img_key/sub_key extracted from nav")
    return WbiKeys(img_key=img_key, sub_key=sub_key)

