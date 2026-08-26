from __future__ import annotations

import http.cookiejar as cookiejar
import http.cookies
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests

from .utils import DEFAULT_HEADERS


def load_netscape_cookie_file(path: str) -> cookiejar.MozillaCookieJar:
    jar = cookiejar.MozillaCookieJar()
    jar.load(path, ignore_discard=True, ignore_expires=True)
    return jar


def cookie_header_from_jar(jar: cookiejar.CookieJar, domain_filter: Optional[str] = None) -> str:
    pairs = []
    for c in jar:
        if domain_filter and domain_filter not in c.domain:
            continue
        pairs.append(f"{c.name}={c.value}")
    return "; ".join(pairs)


def cookie_dict(jar: cookiejar.CookieJar, domain_filter: Optional[str] = None) -> Dict[str, str]:
    d: Dict[str, str] = {}
    for c in jar:
        if domain_filter and domain_filter not in c.domain:
            continue
        d[c.name] = c.value
    return d


def detect_cookie_format(text: str) -> str:
    """Return 'netscape' or 'header' or 'unknown' based on file content."""
    stripped = text.lstrip('\ufeff').strip()
    if not stripped:
        return 'unknown'
    first = stripped.splitlines()[0].strip()
    if first.startswith('# Netscape HTTP Cookie File'):
        return 'netscape'
    # very naive check: header-like 'name=value; name2=value2'
    if '=' in first and ';' in stripped:
        return 'header'
    return 'unknown'


def parse_header_cookie(text: str) -> str:
    # normalize whitespace, keep semicolons
    line = text.strip().splitlines()[0].strip()
    # remove trailing semicolon
    if line.endswith(';'):
        line = line[:-1]
    return line


def check_login_state(cookie_header: Optional[str], *, proxy: Optional[str] = None, timeout: int = 12) -> Optional[bool]:
    """Check login state via nav API (cheap, no risk-control).

    Returns:
        True  — logged in (cookie valid)
        False — cookie stale/expired (nav says not logged in)
        None  — undeterminable (empty cookie / network error); do not warn
    """
    if not cookie_header:
        return None
    sess = requests.Session()
    sess.headers.update(DEFAULT_HEADERS)
    sess.headers["Cookie"] = cookie_header
    if proxy:
        sess.proxies.update({"http": proxy, "https": proxy})
    try:
        r = sess.get("https://api.bilibili.com/x/web-interface/nav", timeout=timeout)
        j = r.json()
    except Exception:
        return None
    if j.get("code") == 0:
        return bool((j.get("data") or {}).get("isLogin"))
    if j.get("code") == -101:
        return False
    return None


STALE_COOKIE_HINT = (
    "cookie 已失效（SESSDATA 过期）：请用浏览器重新登录 bilibili.com 后导出 Cookie 覆盖 cookie.txt。"
    "失效期间：公开视频仍可抓取，但 space 发现/搜索等登录接口会返回 -352 风控错误（极具误导性）。"
)


def read_cookie_file(path: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Read cookie file and return (cookie_header, cookiefile_path).
    - If Netscape format: returns (header_built_from_jar, path)
    - If header format (single line 'name=value; ...'): returns (header, None)
    - If unknown: (None, None)
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
    except Exception:
        return None, None

    kind = detect_cookie_format(text)
    if kind == 'netscape':
        try:
            jar = load_netscape_cookie_file(path)
            return cookie_header_from_jar(jar, domain_filter='bilibili.com'), path
        except Exception:
            return None, None
    if kind == 'header':
        return parse_header_cookie(text), None
    return None, None


def _cookie_header_to_dict(header: str) -> Dict[str, str]:
    c = http.cookies.SimpleCookie()
    c.load(header)
    out: Dict[str, str] = {}
    for k, morsel in c.items():
        out[k] = morsel.value
    return out


def _dict_to_cookie_header(d: Dict[str, str]) -> str:
    # Keep it simple and deterministic: alphabetical by key.
    parts = [f"{k}={v}" for k, v in sorted(d.items(), key=lambda kv: kv[0].lower())]
    return "; ".join(parts)


def _parse_cookie_header_pairs(header: str) -> Dict[str, str]:
    """Parse a Cookie header into key/value pairs.

    We intentionally keep parsing simple and robust for typical browser-exported cookie headers.
    """
    out: Dict[str, str] = {}
    for part in (header or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        out[k] = v
    return out


def write_netscape_cookie_file_from_header(
    cookie_header: str,
    *,
    path: Path,
    domain: str = ".bilibili.com",
    include_subdomains: bool = True,
    cookie_path: str = "/",
    secure: bool = False,
) -> str:
    """Materialize a Netscape cookie file from a Cookie header.

    This avoids passing cookies via raw HTTP headers to tools like yt-dlp (which emits deprecation warnings).
    """
    header = (cookie_header or "").strip()
    if not header:
        raise ValueError("cookie_header is empty")

    pairs = _parse_cookie_header_pairs(header)
    if not pairs:
        raise ValueError("cookie_header parsed to empty pairs")

    path.parent.mkdir(parents=True, exist_ok=True)
    # Netscape format:
    # domain \t includeSubdomains \t path \t secure \t expiration \t name \t value
    # Use expiration=0 (session) since we don't know real expiry timestamps.
    flag = "TRUE" if include_subdomains else "FALSE"
    sec = "TRUE" if secure else "FALSE"
    exp = "0"
    lines = ["# Netscape HTTP Cookie File", "# This file is generated from cookie header for bilibili.com", ""]
    for name, value in sorted(pairs.items(), key=lambda kv: kv[0].lower()):
        lines.append(f"{domain}\t{flag}\t{cookie_path}\t{sec}\t{exp}\t{name}\t{value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def ensure_buvid_cookie_header(cookie_header: str, *, proxy: Optional[str]) -> str:
    """Ensure buvid3/buvid4 are present in a cookie header (some Bilibili APIs return 412 without them).

    Fail-fast:
    - If cookie_header is empty, raise.
    - If finger/spi does not return buvid values, raise.
    """

    header = (cookie_header or "").strip()
    if not header:
        raise ValueError("cookie_header is empty")

    d = _cookie_header_to_dict(header)
    if d.get("buvid3") and d.get("buvid4"):
        return header

    sess = requests.Session()
    sess.headers.update(DEFAULT_HEADERS)
    if proxy:
        sess.proxies.update({"http": proxy, "https": proxy})

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

    d.setdefault("buvid3", b3.strip())
    d.setdefault("buvid4", b4.strip())
    return _dict_to_cookie_header(d)
