"""Subprocess bridge to the OpenCLI bilibili adapter.

Every function here talks to `opencli bilibili <cmd> ... -f json` and
returns plain Python data. Output shapes were recorded live (opencli
1.8.7, 2026-09-20):

  hot       -> [{"rank", "title", "author", "play", "danmaku", "bvid", "url"}]
  ranking   -> [{"rank", "title", "author", "score", "url"}]   (no bvid — extracted from url)
  comments  -> [{"rank", "rpid", "author", "text", "likes", "replies", "time"}]
  summary   -> [{"time", "content"}]

Rules:
- OpenCLI is optional: check available() first and degrade gracefully.
- stdout is UTF-8 JSON regardless of the Windows console codepage; decode
  bytes explicitly.
- Failures raise BridgeError with the trimmed stderr — never fake data.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

_DEFAULT_TIMEOUT = 120.0  # browser-session roundtrips can be slow
_BVID_RE = re.compile(r"(BV[0-9A-Za-z]+)")

_AVAILABLE_CACHE: Dict[str, Any] = {}


class BridgeError(RuntimeError):
    """OpenCLI call failed (missing binary, adapter error, bad output).

    adapter_code carries the adapter's own error code when parsed (e.g.
    EMPTY_RESULT — opencli exits 66 when a channel has no data, which for
    `comments` means the video genuinely has zero comments).
    """

    def __init__(self, msg: str, *, exit_code: Optional[int] = None, adapter_code: Optional[str] = None):
        super().__init__(msg)
        self.exit_code = exit_code
        self.adapter_code = adapter_code


_ADAPTER_CODE_RE = re.compile(r"code:\s*([A-Z_]+)")


def _exe() -> Optional[str]:
    """Resolve the opencli executable (npm ships opencli.cmd on Windows;
    CreateProcess does not resolve .cmd shims from a bare name)."""
    return shutil.which("opencli")


def _run(args: List[str], *, timeout: float = _DEFAULT_TIMEOUT, fmt_json: bool = False) -> Any:
    """Run one opencli command. `fmt_json` appends the adapter-level `-f json`
    flag — the browser channel does NOT accept it (and already outputs JSON)."""
    exe = _exe()
    if exe is None:
        raise BridgeError(
            "opencli 不在 PATH（安装：npm install -g @jackwener/opencli，且需浏览器扩展在线）"
        )
    cmd = [exe, *args]
    if fmt_json:
        cmd += ["-f", "json"]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except FileNotFoundError as e:
        raise BridgeError(
            "opencli 不在 PATH（安装：npm install -g @jackwener/opencli，且需浏览器扩展在线）"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise BridgeError(f"opencli 超时（>{timeout}s）: {' '.join(args)}") from e
    out = p.stdout.decode("utf-8", errors="replace").strip()
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", errors="replace").strip()
        m = _ADAPTER_CODE_RE.search(err) or _ADAPTER_CODE_RE.search(out)
        raise BridgeError(
            f"opencli 退出码 {p.returncode}: {err[:300] or out[:300]}",
            exit_code=p.returncode,
            adapter_code=m.group(1) if m else None,
        )
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        raise BridgeError(f"opencli 输出不是 JSON: {out[:200]}") from e


def available(*, refresh: bool = False) -> bool:
    """True if the opencli binary answers. Result is cached per process."""
    key = "ok"
    if not refresh and key in _AVAILABLE_CACHE:
        return bool(_AVAILABLE_CACHE[key])
    exe = _exe()
    if exe is None:
        _AVAILABLE_CACHE[key] = False
        return False
    try:
        p = subprocess.run([exe, "--version"], capture_output=True, timeout=30)
        ok = p.returncode == 0
    except Exception:
        ok = False
    _AVAILABLE_CACHE[key] = ok
    return ok


def _extract_bvid(item: Dict[str, Any]) -> Optional[str]:
    bvid = item.get("bvid")
    if isinstance(bvid, str) and bvid.startswith("BV"):
        return bvid
    m = _BVID_RE.search(str(item.get("url") or ""))
    return m.group(1) if m else None


def _normalize_list(data: Any, *, what: str) -> List[Dict[str, Any]]:
    if not isinstance(data, list):
        raise BridgeError(f"opencli {what} 返回了非列表输出: {str(data)[:120]}")
    return [x for x in data if isinstance(x, dict)]


def chart(source: str = "hot", limit: int = 50) -> List[Dict[str, Any]]:
    """hot / ranking discovery. Returns items with a guaranteed `bvid` key."""
    if source not in ("hot", "ranking"):
        raise ValueError(f"source 必须是 hot|ranking，收到 {source!r}")
    items = _normalize_list(_run(["bilibili", source], fmt_json=True), what=source)
    out: List[Dict[str, Any]] = []
    for it in items[: max(0, int(limit))]:
        bvid = _extract_bvid(it)
        if not bvid:
            continue
        rec = {"rank": it.get("rank"), "title": str(it.get("title") or "").strip(), "author": it.get("author"),
               "bvid": bvid, "url": it.get("url")}
        for k in ("play", "danmaku", "score"):
            if k in it:
                rec[k] = it.get(k)
        if rec["title"]:
            out.append(rec)
    return out


def comments(bvid: str, *, parent: Optional[str] = None, limit: int = 0) -> List[Dict[str, Any]]:
    """Main-level comments (or one thread's 楼中楼 via parent rpid), official API."""
    args = ["bilibili", "comments", bvid]
    if parent:
        args += ["--parent", str(parent)]
    items = _normalize_list(_run(args, fmt_json=True), what="comments")
    return items[: int(limit)] if limit and int(limit) > 0 else items


def summary(bvid: str) -> List[Dict[str, Any]]:
    """Official AI summary: outline entries with timestamps (大纲，不是全文)."""
    return _normalize_list(_run(["bilibili", "summary", bvid], fmt_json=True), what="summary")


# ---------- browser channel (generic, for the cookie supply chain) ----------

def _run_text(args: List[str], *, timeout: float = _DEFAULT_TIMEOUT) -> str:
    """Run one opencli command and return raw stdout (browser `eval` emits
    plain text, not JSON)."""
    exe = _exe()
    if exe is None:
        raise BridgeError(
            "opencli 不在 PATH（安装：npm install -g @jackwener/opencli，且需浏览器扩展在线）"
        )
    try:
        p = subprocess.run([exe, *args], capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise BridgeError(f"opencli 超时（>{timeout}s）: {' '.join(args)}") from e
    out = p.stdout.decode("utf-8", errors="replace").strip()
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", errors="replace").strip()
        raise BridgeError(f"opencli 退出码 {p.returncode}: {err[:300] or out[:300]}")
    return out


def browser_open(session: str, url: str, *, foreground: bool = False) -> Any:
    return _run(["browser", session, "open", url, "--window", "foreground" if foreground else "background"])



def browser_eval(session: str, js: str) -> str:
    """Evaluate JS in the page context; returns raw text (quotes stripped).
    NOTE: HttpOnly cookies (SESSDATA) are invisible to document.cookie —
    see cookies.qr_login_cookies."""
    return _run_text(["browser", session, "eval", js]).strip('"')


def browser_network(session: str, since: str = "30s", detail: Optional[str] = None) -> Any:
    """Captured network entries (or one full body via detail=<key>)."""
    args = ["browser", session, "network", "--since", since]
    if detail:
        args += ["--detail", detail]
    return _run(args)


def browser_close(session: str) -> None:
    try:
        _run(["browser", session, "close"])
    except BridgeError:
        pass  # closing a dead session is fine


def fetch_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
