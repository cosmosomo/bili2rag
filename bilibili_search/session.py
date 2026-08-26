from __future__ import annotations

import http.cookies
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests

from bilibili_harvester.cookies import detect_cookie_format, load_netscape_cookie_file
from bilibili_harvester.utils import DEFAULT_HEADERS


@dataclass(frozen=True)
class CookieBrief:
    mode: str  # none|netscape|header
    has_sessdata: bool
    has_bili_jct: bool
    has_buvid3: bool
    has_buvid4: bool


def _parse_cookie_header_to_dict(header: str) -> Dict[str, str]:
    c = http.cookies.SimpleCookie()
    c.load(header)
    out: Dict[str, str] = {}
    for k, morsel in c.items():
        out[k] = morsel.value
    return out


def _brief_from_cookie_dict(d: Dict[str, str], *, mode: str) -> CookieBrief:
    return CookieBrief(
        mode=mode,
        has_sessdata=("SESSDATA" in d),
        has_bili_jct=("bili_jct" in d),
        has_buvid3=("buvid3" in d),
        has_buvid4=("buvid4" in d),
    )


def build_web_session(cookiefile: Optional[Path], *, proxy: Optional[str]) -> Tuple[requests.Session, CookieBrief]:
    sess = requests.Session()
    sess.headers.update(DEFAULT_HEADERS)
    if proxy:
        sess.proxies.update({"http": proxy, "https": proxy})

    if not cookiefile:
        return sess, CookieBrief(mode="none", has_sessdata=False, has_bili_jct=False, has_buvid3=False, has_buvid4=False)

    if not cookiefile.exists():
        raise FileNotFoundError(str(cookiefile))

    text = cookiefile.read_text(encoding="utf-8")
    kind = detect_cookie_format(text)
    if kind == "netscape":
        jar = load_netscape_cookie_file(str(cookiefile))
        d: Dict[str, str] = {}
        for c in jar:
            if "bilibili.com" not in c.domain:
                continue
            d[c.name] = c.value
        for k, v in d.items():
            sess.cookies.set(k, v, domain=".bilibili.com", path="/")
        return sess, _brief_from_cookie_dict(d, mode="netscape")

    if kind == "header":
        header_line = text.strip().splitlines()[0].strip()
        d = _parse_cookie_header_to_dict(header_line)
        for k, v in d.items():
            sess.cookies.set(k, v, domain=".bilibili.com", path="/")
        return sess, _brief_from_cookie_dict(d, mode="header")

    raise ValueError(f"unknown cookie file format: {cookiefile}")


def ensure_buvid_cookies(sess: requests.Session) -> None:
    """Ensure buvid3/buvid4 exist in session cookies (required by some web APIs like search)."""
    if sess.cookies.get("buvid3") and sess.cookies.get("buvid4"):
        return

    # Prefer finger/spi API (returns buvid3/buvid4) to avoid HTML changes.
    r = sess.get("https://api.bilibili.com/x/frontend/finger/spi", timeout=15)
    r.raise_for_status()
    j = r.json()
    data = j.get("data") or {}
    b3 = data.get("b_3")
    b4 = data.get("b_4")
    if not isinstance(b3, str) or not b3.strip():
        raise RuntimeError("failed to obtain buvid3 from finger/spi")
    if not isinstance(b4, str) or not b4.strip():
        raise RuntimeError("failed to obtain buvid4 from finger/spi")
    sess.cookies.set("buvid3", b3.strip(), domain=".bilibili.com", path="/")
    sess.cookies.set("buvid4", b4.strip(), domain=".bilibili.com", path="/")

