from __future__ import annotations

import os
import re
import subprocess
import urllib.parse
from shutil import which
from pathlib import Path
from typing import Any, Dict, Optional, List

import yt_dlp
from pathlib import Path
import requests


_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _direct_download(url: str, out_path: Path, *, headers: Dict[str, str], proxy: Optional[str]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".part")
    if tmp.exists():
        tmp.unlink()

    def try_requests() -> None:
        sess = requests.Session()
        sess.headers.update(headers)
        if proxy:
            sess.proxies.update({"http": proxy, "https": proxy})

        with sess.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
        tmp.replace(out_path)

    def try_curl() -> None:
        curl = which("curl.exe") or which("curl")
        if not curl:
            raise RuntimeError("curl not found")

        args = [curl, "-L", "--fail", "--show-error", "-o", str(tmp)]
        for k, v in headers.items():
            args.extend(["-H", f"{k}: {v}"])
        if proxy:
            args.extend(["--proxy", proxy])
        args.append(url)

        proc = subprocess.run(args, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            stdout = (proc.stdout or "").strip()
            detail = stderr or stdout or f"curl exit={proc.returncode}"
            raise RuntimeError(detail)
        tmp.replace(out_path)

    try:
        try_requests()
    except Exception:
        # On Windows, Python/OpenSSL may hit SSL EOF to some CDN nodes; curl(schannel) is often more stable.
        try:
            try_curl()
        except Exception:
            raise


def ytdlp_download_cover(
    info: Dict[str, Any],
    outdir: Path,
    *,
    proxy: Optional[str] = None,
    cookie_header: Optional[str] = None,
) -> Path:
    """Download the metadata thumbnail as a stable cover asset beside harvested media."""
    thumbnail = info.get("thumbnail")
    if not isinstance(thumbnail, str) or not thumbnail.startswith(("http://", "https://", "//")):
        raise RuntimeError("metadata does not contain a downloadable thumbnail")
    if thumbnail.startswith("//"):
        thumbnail = "https:" + thumbnail

    suffix = Path(urllib.parse.urlparse(thumbnail).path).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"
    headers = {"Referer": "https://www.bilibili.com", "User-Agent": _UA}
    if cookie_header:
        headers["Cookie"] = cookie_header

    out_path = outdir / f"cover{suffix}"
    _direct_download(thumbnail, out_path, headers=headers, proxy=proxy)
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(f"cover download produced no file: {out_path}")
    return out_path


def _pick_audio_formats_best_first(info: Dict[str, Any]) -> List[Dict[str, Any]]:
    fmts = info.get("formats") or []
    if not isinstance(fmts, list):
        return []
    cands: List[Dict[str, Any]] = []
    for f in fmts:
        if not isinstance(f, dict):
            continue
        if f.get("vcodec") != "none":
            continue
        ac = f.get("acodec")
        if not ac or ac == "none":
            continue
        u = f.get("url")
        if not isinstance(u, str) or not u.startswith("http"):
            continue
        cands.append(f)
    if not cands:
        return []

    def score(f: Dict[str, Any]) -> tuple[int, float]:
        ext = str(f.get("ext") or "").lower()
        abr = f.get("abr")
        tbr = f.get("tbr")
        bitrate = 0.0
        if isinstance(abr, (int, float)):
            bitrate = float(abr)
        elif isinstance(tbr, (int, float)):
            bitrate = float(tbr)
        ext_rank = {"m4a": 0, "mp4": 1, "m4s": 2}.get(ext, 9)
        return (ext_rank, -bitrate)

    cands.sort(key=score)
    return cands


def _extract_bvid_from_text(text: str) -> Optional[str]:
    m = re.search(r"BV[0-9A-Za-z]+", text or "")
    return m.group(0) if m else None


def _fetch_view_first_cid(bvid: str, *, cookie_header: Optional[str], proxy: Optional[str]) -> int:
    sess = requests.Session()
    sess.headers.update({"User-Agent": _UA, "Referer": "https://www.bilibili.com"})
    if cookie_header:
        sess.headers["Cookie"] = cookie_header
    if proxy:
        sess.proxies.update({"http": proxy, "https": proxy})

    r = sess.get("https://api.bilibili.com/x/web-interface/view", params={"bvid": bvid}, timeout=15)
    r.raise_for_status()
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError(f"view API error code={j.get('code')} msg={j.get('message')}")
    data = j.get("data") or {}
    pages = data.get("pages") or []
    if isinstance(pages, list) and pages:
        cid = pages[0].get("cid")
        if isinstance(cid, int) and cid > 0:
            return cid
        if isinstance(cid, str) and cid.isdigit():
            return int(cid)
    raise RuntimeError("view API missing pages[0].cid")


def _fetch_wbi_keys(*, cookie_header: Optional[str], proxy: Optional[str]) -> "WbiKeys":
    # Reuse WBI implementation from bilibili_search to avoid diverging algorithms.
    from bilibili_search.wbi import extract_wbi_keys_from_nav_json, WbiKeys

    sess = requests.Session()
    sess.headers.update({"User-Agent": _UA, "Referer": "https://www.bilibili.com/"})
    if cookie_header:
        sess.headers["Cookie"] = cookie_header
    if proxy:
        sess.proxies.update({"http": proxy, "https": proxy})

    r = sess.get("https://api.bilibili.com/x/web-interface/nav", timeout=15)
    r.raise_for_status()
    return extract_wbi_keys_from_nav_json(r.json())


def _fetch_playurl_dash_audio_urls(
    bvid: str,
    cid: int,
    *,
    cookie_header: Optional[str],
    proxy: Optional[str],
) -> List[str]:
    from bilibili_search.wbi import enc_wbi_params

    keys = _fetch_wbi_keys(cookie_header=cookie_header, proxy=proxy)
    params: Dict[str, Any] = {
        "bvid": bvid,
        "cid": cid,
        "fnval": 16,  # DASH
        "fnver": 0,
        "otype": "json",
    }
    signed = enc_wbi_params(params, keys)
    query = urllib.parse.urlencode(sorted(signed.items()), quote_via=urllib.parse.quote, safe="")

    sess = requests.Session()
    sess.headers.update({"User-Agent": _UA, "Referer": f"https://www.bilibili.com/video/{bvid}"})
    if cookie_header:
        sess.headers["Cookie"] = cookie_header
    if proxy:
        sess.proxies.update({"http": proxy, "https": proxy})

    url = f"https://api.bilibili.com/x/player/wbi/playurl?{query}"
    r = sess.get(url, timeout=20)
    r.raise_for_status()
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError(f"playurl API error code={j.get('code')} msg={j.get('message')}")
    data = j.get("data") or {}
    dash = data.get("dash") or {}
    audios = dash.get("audio") or []
    if not isinstance(audios, list) or not audios:
        raise RuntimeError("playurl API: dash.audio empty")

    # Choose best quality by id (30280 > 30232 > 30216 ...)
    def audio_id(a: Dict[str, Any]) -> int:
        v = a.get("id") or a.get("audio_id")
        try:
            return int(v)
        except Exception:
            return 0

    audios = [a for a in audios if isinstance(a, dict)]
    audios.sort(key=audio_id, reverse=True)
    best = audios[0]

    base = best.get("base_url") or best.get("baseUrl")
    backups = best.get("backup_url") or best.get("backupUrl") or []
    if isinstance(backups, str):
        backups = [backups]
    if not isinstance(backups, list):
        backups = []

    urls: List[str] = []
    if isinstance(base, str) and base.startswith("http"):
        urls.append(base)
    for u in backups:
        if isinstance(u, str) and u.startswith("http"):
            urls.append(u)
    # de-dup while preserving order
    seen = set()
    out = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _download_audio_via_playurl_api(
    bvid: str,
    outdir: Path,
    *,
    cookie_header: Optional[str],
    proxy: Optional[str],
) -> Path:
    cid = _fetch_view_first_cid(bvid, cookie_header=cookie_header, proxy=proxy)
    urls = _fetch_playurl_dash_audio_urls(bvid, cid, cookie_header=cookie_header, proxy=proxy)
    headers = {"Referer": f"https://www.bilibili.com/video/{bvid}", "User-Agent": _UA}
    if cookie_header:
        headers["Cookie"] = cookie_header
    out_path = outdir / f"{bvid}.m4a"
    last_error: Optional[Exception] = None
    for u in urls:
        try:
            _direct_download(u, out_path, headers=headers, proxy=proxy)
            return out_path
        except Exception as e:
            last_error = e
            continue
    raise RuntimeError(f"playurl 音频直链下载失败：{last_error}") from last_error


def _ffmpeg_location_dir() -> Optional[str]:
    """Locate ffmpeg for yt-dlp postprocessing.

    Search order (first hit wins):
    1. ffmpeg on system PATH (shutil.which)
    2. <repo>/bin/            — drop ffmpeg(.exe)+ffprobe(.exe) here for a portable setup
    3. imageio-ffmpeg package — pip-installed ffmpeg (no ffprobe, still covers extraction)
    4. legacy sibling BILIBILI_GET bundle (local dev compatibility)
    """
    import shutil

    exe = shutil.which("ffmpeg")
    if exe:
        return str(Path(exe).parent)

    repo_bin = Path(__file__).resolve().parents[1] / "bin"
    if (repo_bin / "ffmpeg.exe").exists() or (repo_bin / "ffmpeg").exists():
        return str(repo_bin)

    try:
        import imageio_ffmpeg  # type: ignore

        return str(Path(imageio_ffmpeg.get_ffmpeg_exe()).parent)
    except Exception:
        pass

    return None


def ytdlp_info(url: str, cookiefile: Optional[str] = None, proxy: Optional[str] = None, cookie_header: Optional[str] = None) -> Dict[str, Any]:
    ydl_opts: Dict[str, Any] = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
        # Avoid hanging forever on flaky CDN nodes / TLS EOF issues.
        "socket_timeout": 20,
        "retries": 2,
        "extractor_retries": 2,
        "fragment_retries": 2,
        "http_headers": {"Referer": "https://www.bilibili.com"},
    }
    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile
    if cookie_header and not cookiefile:
        ydl_opts.setdefault("http_headers", {}).update({"Cookie": cookie_header})
    if proxy:
        ydl_opts["proxy"] = proxy
    loc = _ffmpeg_location_dir()
    if loc:
        ydl_opts["ffmpeg_location"] = loc
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def ytdlp_list_entries(url: str, cookiefile: Optional[str] = None, proxy: Optional[str] = None, cookie_header: Optional[str] = None) -> List[Dict[str, Any]]:
    """以扁平模式列出播放列表/多P的条目，返回 entries（每项包含 url/title/playlist_index 等）。"""
    ydl_opts: Dict[str, Any] = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": False,
        "extract_flat": "in_playlist",
        "socket_timeout": 20,
        "retries": 2,
        "extractor_retries": 2,
        "fragment_retries": 2,
        "http_headers": {"Referer": "https://www.bilibili.com"},
    }
    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile
    if cookie_header and not cookiefile:
        ydl_opts.setdefault("http_headers", {}).update({"Cookie": cookie_header})
    if proxy:
        ydl_opts["proxy"] = proxy
    loc = _ffmpeg_location_dir()
    if loc:
        ydl_opts["ffmpeg_location"] = loc
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    entries = info.get("entries") if isinstance(info, dict) else None
    return entries or []


def ytdlp_download_audio(url: str, outdir: Path, cookiefile: Optional[str] = None, proxy: Optional[str] = None, kbps: str = "64", cookie_header: Optional[str] = None) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(outdir / "%(id)s.%(ext)s")
    ydl_opts: Dict[str, Any] = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": outtmpl,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": kbps}
        ],
        "noplaylist": True,
        # Reduce verbosity for batch runs; errors still surface via exceptions/logs.
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        # Avoid hanging forever on flaky CDN nodes / TLS EOF issues.
        "socket_timeout": 20,
        "retries": 2,
        "extractor_retries": 2,
        "fragment_retries": 2,
        "http_headers": {"Referer": "https://www.bilibili.com"},
    }
    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile
    if cookie_header and not cookiefile:
        ydl_opts.setdefault("http_headers", {}).update({"Cookie": cookie_header})
    if proxy:
        ydl_opts["proxy"] = proxy
    loc = _ffmpeg_location_dir()
    if loc:
        ydl_opts["ffmpeg_location"] = loc
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            vid = info.get("id")
            return outdir / f"{vid}.mp3"
    except Exception as e:
        # Fallback: on some networks, yt-dlp may fail with SSL EOF during media download.
        # We retry by fetching metadata and downloading the best audio format URL directly.
        try:
            info = ytdlp_info(url, cookiefile=cookiefile, proxy=proxy, cookie_header=cookie_header)
        except Exception as e2:
            raise RuntimeError(f"音频下载失败且元信息重试失败：{e}; {e2}") from e2

        fmts = _pick_audio_formats_best_first(info)
        if not fmts:
            raise RuntimeError(f"音频下载失败，且无法从 formats 里找到音频直链：{e}") from e

        vid = info.get("id") or "audio"
        headers = {"Referer": "https://www.bilibili.com", "User-Agent": _UA}
        if cookie_header:
            headers["Cookie"] = cookie_header

        last_error: Optional[Exception] = None
        for fmt in fmts:
            file_url = fmt.get("url")
            if not isinstance(file_url, str):
                continue
            # Some format URLs may contain fragments; direct download only supports plain URLs.
            if re.search(r"\\bfragment\\b", file_url, flags=re.IGNORECASE):
                continue

            ext = str(fmt.get("ext") or "m4a").lower()
            extra_headers = fmt.get("http_headers")
            hdrs = dict(headers)
            if isinstance(extra_headers, dict):
                hdrs.update({str(k): str(v) for k, v in extra_headers.items()})

            out_path = outdir / f"{vid}.{ext}"
            try:
                _direct_download(file_url, out_path, headers=hdrs, proxy=proxy)
                return out_path
            except Exception as e3:
                last_error = e3
                continue

        # Final fallback: use playurl API to fetch DASH audio urls with backup_url list.
        bvid = None
        if isinstance(info.get("id"), str) and info.get("id").startswith("BV"):
            bvid = info.get("id")
        if not bvid:
            bvid = _extract_bvid_from_text(url)
        if bvid:
            try:
                return _download_audio_via_playurl_api(bvid, outdir, cookie_header=cookie_header, proxy=proxy)
            except Exception as e4:
                last_error = e4

        raise RuntimeError(f"音频下载失败，直链下载也失败：{last_error}") from last_error


def ytdlp_download_video(url: str, outdir: Path, cookiefile: Optional[str] = None, proxy: Optional[str] = None, cookie_header: Optional[str] = None) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(outdir / "%(id)s.%(ext)s")
    ydl_opts: Dict[str, Any] = {
        "format": "bv*[ext=mp4]/bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 20,
        "retries": 2,
        "extractor_retries": 2,
        "fragment_retries": 2,
        "http_headers": {"Referer": "https://www.bilibili.com"},
    }
    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile
    if cookie_header and not cookiefile:
        ydl_opts.setdefault("http_headers", {}).update({"Cookie": cookie_header})
    if proxy:
        ydl_opts["proxy"] = proxy
    loc = _ffmpeg_location_dir()
    if loc:
        ydl_opts["ffmpeg_location"] = loc
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        vid = info.get("id")
        return outdir / f"{vid}.mp4"


def ytdlp_download_subtitles(url: str, outdir: Path, cookiefile: Optional[str] = None, proxy: Optional[str] = None, cookie_header: Optional[str] = None) -> List[Path]:
    """下载可用字幕（含自动生成）为 .srt；返回生成的文件路径列表。"""
    from typing import List, Any, Dict
    outdir.mkdir(parents=True, exist_ok=True)
    # 让字幕落在 subtitles 目录
    subdir = outdir / "subtitles"
    subdir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(subdir / "%(id)s.%(ext)s")
    ydl_opts: Dict[str, Any] = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitlesformat": "srt",
        "subtitleslangs": ["all"],
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 20,
        "retries": 2,
        "extractor_retries": 2,
        "fragment_retries": 2,
        "http_headers": {"Referer": "https://www.bilibili.com"},
    }
    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile
    if cookie_header and not cookiefile:
        ydl_opts.setdefault("http_headers", {}).update({"Cookie": cookie_header})
    if proxy:
        ydl_opts["proxy"] = proxy
    loc = _ffmpeg_location_dir()
    if loc:
        ydl_opts["ffmpeg_location"] = loc
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(url, download=True)
    # 收集 srt
    srt_files = list(subdir.glob("*.srt"))
    return srt_files
