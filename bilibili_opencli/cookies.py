"""Cookie supply chain: browser → cookie.txt, no extension export dance.

Why this exists: bili2rag needs `cookie.txt` (Netscape) with a valid
SESSDATA for search/space discovery, and collecting it by hand (browser
extension export) is the most annoying step of setup.

Reality of the browser channel (verified live, 2026-09-20):
- `document.cookie` yields 24 bilibili cookies INCLUDING bili_jct and
  DedeUserID — everything except SESSDATA, which is HttpOnly.
- Chrome's cookie DB is exclusively locked while Chrome runs, so direct
  DPAPI decryption is not viable day-to-day.
- The ONLY automatic SESSDATA source is the QR-login poll response body
  (`/x/passport-login/web/qrcode/poll`): on success it carries the full
  cookie set in `data.cookie_info.cookies` and in `data.url` query params
  (crossDomain redirect). Network capture exposes bodies — hence:

  Tier 1 (zero-touch)  browser document.cookie + SESSDATA carried over
                       from the existing cookie.txt → merge → validate.
                       Covers the routine refreshes (buvid/ticket/...).
  Tier 2 (scan once)   open the login page, capture the poll, extract
                       SESSDATA (+everything). Covers first-time setup
                       and SESSDATA rotation.
  Tier 3 (fallback)    tell the user to export once with a cookie
                       extension (rare, last resort).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import bridge


def parse_cookie_header(raw: str) -> Dict[str, str]:
    """'k1=v1; k2=v2' -> dict (values keep their URL encoding)."""
    out: Dict[str, str] = {}
    for pair in raw.split(";"):
        if "=" in pair:
            k, _, v = pair.strip().partition("=")
            if k:
                out[k.strip()] = v.strip()
    return out


def browser_cookies(session: str = "bili2rag_cookie") -> Dict[str, str]:
    """Tier 1: non-HttpOnly cookies of the logged-in browser session."""
    bridge.browser_open(session, "https://www.bilibili.com")
    try:
        raw = bridge.browser_eval(session, "document.cookie")
    finally:
        bridge.browser_close(session)
    return parse_cookie_header(raw)


def extract_login_cookies(poll_body: Any) -> Dict[str, str]:
    """Extract the full cookie set (incl. SESSDATA) from a successful
    QR-poll response body. Two documented shapes are handled:

      data.cookie_info.cookies: [{"name","value"},...]
      data.url: "...crossDomain?DedeUserID=..&SESSDATA=..&bili_jct=.."
    """
    out: Dict[str, str] = {}
    data = (poll_body or {}).get("data") if isinstance(poll_body, dict) else None
    if not isinstance(data, dict):
        return out
    cookie_info = data.get("cookie_info")
    if isinstance(cookie_info, dict):
        for c in cookie_info.get("cookies") or []:
            if isinstance(c, dict) and c.get("name") and c.get("value") is not None:
                out[str(c["name"])] = str(c["value"])
    if "SESSDATA" not in out:
        url = data.get("url")
        if isinstance(url, str) and "SESSDATA=" in url:
            # Raw (still URL-encoded) values — the Netscape file keeps the
            # encoded form, matching extension-exported cookie.txt files.
            # Whitelist: the crossDomain URL also carries non-cookie params
            # like gourl/Expires.
            wanted = {"SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid"}
            for pair in url.partition("?")[2].split("&"):
                k, _, v = pair.partition("=")
                if k in wanted and v:
                    out.setdefault(k, v)
    return out


def _passport_get(path: str, params: Dict[str, str], cookie_header: Optional[str]):
    import requests

    r = requests.get(
        f"https://passport.bilibili.com/x/passport-login/web/{path}",
        params=params,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com/",
            **({"Cookie": cookie_header} if cookie_header else {}),
        },
        timeout=15,
    )
    return r.json()


def _render_qr_png(content_url: str, log) -> Optional[Path]:
    """Render the login QR as a PNG and pop it open in the default viewer.

    Returns the PNG path, or None when the `qrcode` lib is missing (the
    caller keeps polling regardless — the user may install it next time).
    """
    try:
        import qrcode  # lazy: optional dependency of an optional flow
    except ImportError:
        log("[QR] 渲染二维码需要 qrcode 库：python -m pip install --user qrcode")
        return None
    import os
    import tempfile

    # Unique name per run: a previously opened viewer (os.startfile) can hold
    # the old file locked and make overwriting it fail with EINVAL.
    png = Path(tempfile.gettempdir()) / f"bili2rag_qr_login_{int(time.time() * 1000) % 10**9}.png"
    qrcode.make(content_url).save(str(png))
    try:
        os.startfile(str(png))  # Windows: pop the QR in the default viewer
    except Exception:
        pass
    return png


def _redeem_cross_domain(url: str, cookie_header: Optional[str]) -> Dict[str, str]:
    """Complete the crossDomain hop the web login page would do in a browser:
    the ticket URL responds with Set-Cookie carrying the fresh session
    (SESSDATA etc.). Verified shape (2026-09-20): the poll's data.url carries
    a `ticket` param — cookies are NOT in the URL anymore."""
    import requests

    r = requests.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com/",
            **({"Cookie": cookie_header} if cookie_header else {}),
        },
        timeout=15,
        allow_redirects=False,
    )
    jar = {c.name: c.value for c in r.cookies}
    if "SESSDATA" not in jar:  # belt and braces: parse raw Set-Cookie too
        for v in r.raw.headers.getlist("Set-Cookie") if hasattr(r.raw, "headers") else []:
            head, _, _rest = v.partition(";")
            k, _, val = head.strip().partition("=")
            if k and val:
                jar.setdefault(k, val)
    return jar


def qr_login_flow(cookie_header: Optional[str] = None, *, timeout: float = 300.0, log=print) -> Dict[str, str]:
    """Tier 2 (pure Python, no browser): render the QR login code, poll the
    passport endpoint, and return the full fresh cookie set (incl. SESSDATA).

    Works even when the browser is already logged in. Poll codes: 86101 未扫码,
    86090 已扫待确认, 86038 二维码过期(自动换新), 0 成功. On success the cookies
    come from data.cookie_info / the crossDomain ticket hop (Set-Cookie)."""
    deadline = time.monotonic() + timeout
    regenerations = 0
    while time.monotonic() < deadline and regenerations <= 2:
        gen = _passport_get("qrcode/generate", {}, cookie_header)
        data = gen.get("data") or {}
        key = data.get("qrcode_key")
        content_url = data.get("url")
        if not key or not content_url:
            log(f"[QR] generate 失败: {str(gen)[:160]}")
            return {}
        png = _render_qr_png(content_url, log)
        if png is not None:
            log(f"[QR] 二维码已打开：{png}（用B站App扫码并确认；二维码约3分钟有效，过期自动换新）")
        key_deadline = min(deadline, time.monotonic() + 170.0)
        while time.monotonic() < key_deadline:
            time.sleep(2.0)
            j = _passport_get("qrcode/poll", {"qrcode_key": key}, cookie_header)
            d = j.get("data") or {}
            # top-level code and data.code mirror each other; resolve one status
            status = d.get("code", j.get("code"))
            if status == 0:
                cookies = extract_login_cookies(j)
                hop_url = (j.get("data") or {}).get("url")
                if "SESSDATA" not in cookies and isinstance(hop_url, str) and hop_url.startswith("http"):
                    try:
                        cookies.update(_redeem_cross_domain(hop_url, cookie_header))
                    except Exception as e:
                        log(f"[QR] crossDomain 兑换失败: {e}")
                if cookies:
                    return cookies
                log(f"[QR] 登录成功但未取得 cookie: {str(j)[:400]}")
                return {}
            if status == 86038:  # QR expired -> regenerate
                regenerations += 1
                log("[QR] 二维码已过期，自动换新……")
                break
        else:
            return {}  # overall deadline hit
    return {}


def merge_cookies(base: Dict[str, str], override: Dict[str, str]) -> Dict[str, str]:
    """Fresh browser/QR cookies win; anything else carries over from base."""
    return {**base, **{k: v for k, v in override.items() if v}}


def write_netscape(path: Path, cookies: Dict[str, str], *, backup: bool = True) -> Optional[Path]:
    """Write Netscape cookie.txt (UTF-8, no BOM). Backs up an existing file
    to <name>.bak-<ts>. Far-future expiry: yt-dlp filters expired cookies,
    and real staleness is decided by the nav check, not file dates."""
    bak: Optional[Path] = None
    if path.exists() and backup:
        bak = path.with_name(path.name + ".bak-" + time.strftime("%Y%m%d_%H%M%S"))
        bak.write_bytes(path.read_bytes())
    expires = int((datetime.now(timezone.utc) + timedelta(days=180)).timestamp())
    lines = [
        "# Netscape HTTP Cookie File",
        "# http://curl.haxx.se/rfc/cookie_spec.html",
        "# Generated by bili2rag cookie-refresh (browser/QR supply chain).",
        "",
    ]
    secure_names = {"SESSDATA", "bili_jct", "sid", "DedeUserID", "DedeUserID__ckMd5"}
    for name in sorted(cookies):
        val = cookies[name]
        if not val or any(ch in val for ch in "\t\r\n"):
            continue  # Netscape is TSV; a broken value would corrupt the file
        secure = "TRUE" if name in secure_names else "FALSE"
        lines.append(f".bilibili.com\tTRUE\t/\t{secure}\t{expires}\t{name}\t{val}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return bak


def validate_login(cookie_path: Path, proxy: Optional[str] = None) -> Optional[bool]:
    """True/False by the nav API; None when the check itself failed."""
    try:
        from bilibili_harvester.cookies import check_login_state, read_cookie_file

        header, _ = read_cookie_file(str(cookie_path))
        return check_login_state(header, proxy=proxy)
    except Exception:
        return None


def read_existing(path: Path) -> Dict[str, str]:
    """Best-effort parse of an existing Netscape/header cookie file."""
    if not path.exists():
        return {}
    out: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) == 7:  # Netscape row
            out[parts[5]] = parts[6]
        elif "=" in line:  # header-style k=v; ...
            out.update(parse_cookie_header(line))
    return out
