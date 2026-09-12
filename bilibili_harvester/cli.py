from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import time
import sys
import platform
from datetime import datetime, timezone

import requests

from .cookies import ensure_buvid_cookie_header, read_cookie_file, write_netscape_cookie_file_from_header
from .utils import DEFAULT_HEADERS, ensure_dir, write_json, resolve_b23, extract_bvid
from .ytwrap import (
    _download_audio_via_playurl_api,
    _ffmpeg_location_dir,
    ytdlp_download_audio,
    ytdlp_download_cover,
    ytdlp_download_subtitles,
    ytdlp_download_video,
    ytdlp_info,
    ytdlp_list_entries,
)
from .bili_api import fetch_danmaku_xml, fetch_comments_snapshot, parse_danmaku_xml
from .struct import write_structured


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _print_utf8(line: str) -> None:
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))


def harvest_one(url: str, cookiefile: Optional[Path], out_root: Path, structured_root: Optional[Path], download_set: set[str], proxy: Optional[str], comment_pages: int, keep_audio: bool = False) -> Dict[str, Any]:
    start_ts = time.monotonic()
    start_at = _now_iso()
    original_url = url
    url = resolve_b23(url, proxy=proxy)
    bvid = extract_bvid(url) or "unknown"
    outdir = ensure_dir(out_root / bvid)
    json_dir = ensure_dir(outdir / "json")
    logs_dir = ensure_dir(outdir / "logs")
    run_log = logs_dir / "harvest_run.log"
    run_json = logs_dir / "harvest_run.json"

    def log(msg: str) -> None:
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
        with run_log.open("a", encoding="utf-8") as f:
            f.write(line)

    def write_danmaku_artifacts(xml: str, xml_path: Path) -> List[str]:
        xml_path.write_text(xml, encoding="utf-8")
        json_path = xml_path.with_suffix(".json")
        write_json(json_path, parse_danmaku_xml(xml))
        return [str(xml_path.relative_to(outdir)), str(json_path.relative_to(outdir))]

    cookie_header: Optional[str] = None
    cookiefile_str: Optional[str] = None
    if cookiefile and cookiefile.exists():
        ch, cf = read_cookie_file(str(cookiefile))
        cookie_header = ch
        cookiefile_str = cf
        if cookie_header:
            # Some Bilibili APIs (and yt-dlp extractor paths) may return 412 without buvid cookies.
            cookie_header = ensure_buvid_cookie_header(cookie_header, proxy=proxy)
            # Materialize a Netscape cookie file for yt-dlp to avoid passing cookies via raw headers
            # (yt-dlp emits deprecation warnings and may spam output otherwise).
            try:
                repo_root = Path(__file__).resolve().parents[1]  # .../BILIBILI_GET
                tmp_cookie = repo_root / ".tmp" / "cookie_bilibili.generated.txt"
                cookiefile_str = write_netscape_cookie_file_from_header(cookie_header, path=tmp_cookie)
            except Exception as e:
                raise RuntimeError(f"failed to generate netscape cookie file from header: {e}") from e
    steps: List[Dict[str, Any]] = []
    tools: Dict[str, Any] = {}
    outputs: Dict[str, Any] = {}

    log(f"Start harvest bvid={bvid} from url={original_url} resolved={url}")
    # tools info
    try:
        import yt_dlp  # type: ignore
        tools["yt_dlp"] = getattr(yt_dlp, "__version__", None)
    except Exception:
        tools["yt_dlp"] = None
    tools["python"] = sys.version.split()[0]
    tools["platform"] = platform.platform()
    loc = _ffmpeg_location_dir()
    tools["ffmpeg"] = {"bundled": bool(loc), "path": loc}

    # cookie brief
    cookie_mode = "none"
    if cookiefile_str:
        cookie_mode = "netscape"
    elif cookie_header:
        cookie_mode = "header"
    cookie_brief = {
        "mode": cookie_mode,
        "cookiefile": cookiefile_str,
        "has_SESSDATA": bool(cookie_header and "SESSDATA=" in cookie_header),
        "has_bili_jct": bool(cookie_header and "bili_jct=" in cookie_header),
        "has_buvid3": bool(cookie_header and ("buvid3=" in cookie_header or "Buvid3=" in cookie_header)),
        "has_buvid4": bool(cookie_header and ("buvid4=" in cookie_header or "Buvid4=" in cookie_header)),
    }

    def fetch_view_fallback_metadata(bvid: str) -> Dict[str, Any]:
        """Fallback when yt-dlp fails (often due to Bili API/risk-control/region behavior).

        We rely on official view API and normalize a minimal info dict compatible with our downstream struct/naming.
        """

        from datetime import datetime, timezone

        sess = requests.Session()
        sess.headers.update(DEFAULT_HEADERS)
        if cookie_header:
            sess.headers["Cookie"] = cookie_header
        if proxy:
            sess.proxies.update({"http": proxy, "https": proxy})
        # Ensure buvid cookies exist; some Bilibili endpoints return 412 otherwise.
        try:
            from bilibili_search.session import ensure_buvid_cookies as _ensure_buvid

            _ensure_buvid(sess)
        except Exception as e:
            raise RuntimeError(f"failed to ensure buvid cookies for view API: {e}") from e

        r = sess.get("https://api.bilibili.com/x/web-interface/view", params={"bvid": bvid}, timeout=20)
        r.raise_for_status()
        j = r.json()
        if j.get("code") != 0:
            raise RuntimeError(f"view API error code={j.get('code')} msg={j.get('message') or j.get('msg')}")
        data = j.get("data") or {}

        owner = data.get("owner") or {}
        pub_ts = data.get("pubdate")
        upload_date = None
        if isinstance(pub_ts, int) and pub_ts > 0:
            upload_date = datetime.fromtimestamp(pub_ts, tz=timezone.utc).strftime("%Y%m%d")

        pages = data.get("pages") or []
        entries = []
        if isinstance(pages, list):
            for p in pages:
                if not isinstance(p, dict):
                    continue
                entries.append(
                    {
                        "cid": p.get("cid"),
                        "title": p.get("part"),
                        "duration": p.get("duration"),
                    }
                )

        # Minimal normalized structure (still keeps the original payload for audit/debug).
        return {
            "id": bvid,
            "bvid": bvid,
            "aid": data.get("aid"),
            "title": data.get("title"),
            "description": data.get("desc"),
            "uploader": owner.get("name"),
            "uploader_id": owner.get("mid"),
            "owner": {"name": owner.get("name"), "mid": owner.get("mid")},
            "duration": data.get("duration"),
            "timestamp": pub_ts,
            "upload_date": upload_date,
            "stat": data.get("stat"),
            "entries": entries,
            "formats": [],  # unknown via this path
            "_source": {"kind": "bili_view_api", "data": data},
        }

    # 1) metadata via yt-dlp
    t0 = time.monotonic()
    try:
        info = ytdlp_info(url, cookiefile=cookiefile_str, proxy=proxy, cookie_header=cookie_header)
        steps.append({"name": "ytdlp_info", "ok": True, "duration": round(time.monotonic() - t0, 3)})
        log("Step ytdlp_info: OK")
    except Exception as e:
        steps.append({"name": "ytdlp_info", "ok": False, "error": str(e), "duration": round(time.monotonic() - t0, 3)})
        log(f"Step ytdlp_info: FAIL {e} (fallback to view API)")
        info = fetch_view_fallback_metadata(bvid)

    write_json(json_dir / "metadata.json", info)
    outputs["metadata"] = str((json_dir / "metadata.json").relative_to(outdir))

    if "cover" in download_set:
        t = time.monotonic()
        try:
            cover_path = ytdlp_download_cover(info, outdir, proxy=proxy, cookie_header=cookie_header)
            outputs["cover"] = str(cover_path.relative_to(outdir))
            steps.append({"name": "download_cover", "ok": True, "duration": round(time.monotonic() - t, 3)})
            log("Step download_cover: OK")
        except Exception as e:
            steps.append({"name": "download_cover", "ok": False, "error": str(e), "duration": round(time.monotonic() - t, 3)})
            log(f"Step download_cover: FAIL {e}")

    # 保存字幕轨道索引（若存在）
    subs_idx = {
        "subtitles": info.get("subtitles") or {},
        "automatic_captions": info.get("automatic_captions") or {},
    }
    write_json(json_dir / "subtitles_index.json", subs_idx)
    outputs["subtitles_index"] = str((json_dir / "subtitles_index.json").relative_to(outdir))

    # list pages and process per-page into pages/pXX_<cid>/
    pages_index: List[Dict[str, Any]] = []
    # 为了稳定“全P解析”，即便传入的是 ?p=XX，也改用 BV 根链接列举分P
    base_url_for_list = f"https://www.bilibili.com/video/{bvid}"
    try:
        _entries = ytdlp_list_entries(base_url_for_list, cookiefile=cookiefile_str, proxy=proxy, cookie_header=cookie_header)
    except Exception:
        _entries = []
    if _entries:
        pages_dir = ensure_dir(outdir / "pages")
        for i, ent in enumerate(_entries, start=1):
            page_url = ent.get("url") or url
            try:
                page_info = ytdlp_info(page_url, cookiefile=cookiefile_str, proxy=proxy, cookie_header=cookie_header)
            except Exception as e:
                log(f"page #{i} info FAIL: {e}")
                continue
            from .utils import extract_cid_from_info
            cid = extract_cid_from_info(page_info) or 0
            title = page_info.get("title") or page_info.get("alt_title")
            duration = page_info.get("duration")
            pdir = ensure_dir(pages_dir / f"p{i:02d}_{cid}")
            # save page info
            pjson_dir = ensure_dir(outdir / "json" / "pages")
            write_json(pjson_dir / f"info_p{i:02d}_{cid}.json", page_info)
            outputs.setdefault("pages_info", []).append(str((pjson_dir / f"info_p{i:02d}_{cid}.json").relative_to(outdir)))
            # subtitles/audio/video
            if "subtitles" in download_set:
                try:
                    ytdlp_download_subtitles(page_url, pdir, cookiefile=cookiefile_str, proxy=proxy, cookie_header=cookie_header)
                except Exception as e:
                    log(f"page #{i} subtitles FAIL: {e}")
            p_audio: Optional[Path] = None
            if "audio" in download_set:
                try:
                    p_audio = ytdlp_download_audio(page_url, pdir, cookiefile=cookiefile_str, proxy=proxy, cookie_header=cookie_header)
                except Exception as e:
                    log(f"page #{i} audio FAIL: {e}")
            p_video: Optional[Path] = None
            if "video" in download_set:
                try:
                    p_video = ytdlp_download_video(page_url, pdir, cookiefile=cookiefile_str, proxy=proxy, cookie_header=cookie_header)
                except Exception as e:
                    log(f"page #{i} video FAIL: {e}")
            # danmaku
            try:
                xml = fetch_danmaku_xml(page_info, cookie_header=cookie_header, proxy=proxy)
                if xml:
                    write_danmaku_artifacts(xml, pdir / "danmaku.xml")
            except Exception as e:
                log(f"page #{i} danmaku FAIL: {e}")
            pages_index.append({
                "index": i,
                "cid": cid,
                "title": title,
                "duration": duration,
                "dir": str(pdir.relative_to(outdir)),
                "audio": str(p_audio.relative_to(outdir)) if p_audio else None,
                "video": str(p_video.relative_to(outdir)) if p_video else None,
                "url": page_url,
            })
        write_json(json_dir / "pages_index.json", {"pages": pages_index})
        outputs["pages_index"] = str((json_dir / "pages_index.json").relative_to(outdir))
        # 避免重复在根目录再下载单页媒体
        download_set = set()

    # 2) subtitles: 下载所有可用字幕
    if (not pages_index) and ("subtitles" in download_set):
        t = time.monotonic()
        try:
            ytdlp_download_subtitles(url, outdir, cookiefile=cookiefile_str, proxy=proxy, cookie_header=cookie_header)
            steps.append({"name": "download_subtitles", "ok": True, "duration": round(time.monotonic() - t, 3)})
            log("Step download_subtitles: OK")
        except Exception as e:
            steps.append({"name": "download_subtitles", "ok": False, "error": str(e), "duration": round(time.monotonic() - t, 3)})
            log(f"Step download_subtitles: FAIL {e}")

    # 2.5) lean mode: zh CC subtitle already fetched -> skip audio download
    def _cc_skip_audio() -> bool:
        if keep_audio or "audio" not in download_set:
            return False
        try:
            from bilibili_asr.asr import find_cc_subtitle

            if find_cc_subtitle(outdir) is not None:
                download_set.discard("audio")
                log("Lean mode: zh CC subtitle present -> skip audio download")
                steps.append({"name": "lean_skip_audio", "ok": True, "detail": "cc subtitle present"})
                return True
        except Exception as e:
            log(f"Lean mode check FAIL (fallback to audio): {e}")
        return False

    _cc_skip_audio()

    # 3) audio
    audio_path: Optional[Path] = None
    if (not pages_index) and ("audio" in download_set):
        t = time.monotonic()
        try:
            audio_path = ytdlp_download_audio(url, outdir, cookiefile=cookiefile_str, proxy=proxy, cookie_header=cookie_header)
            steps.append({"name": "download_audio", "ok": True, "duration": round(time.monotonic() - t, 3)})
            log("Step download_audio: OK")
        except Exception as e:
            # If yt-dlp fails (often due to Bili API responses), fallback to signed playurl API.
            try:
                audio_path = _download_audio_via_playurl_api(bvid, outdir, cookie_header=cookie_header, proxy=proxy)
                steps.append(
                    {
                        "name": "download_audio_playurl_fallback",
                        "ok": True,
                        "error": str(e),
                        "duration": round(time.monotonic() - t, 3),
                        "path": str(audio_path.relative_to(outdir)),
                    }
                )
                log(f"Step download_audio: FAIL {e} (fallback playurl OK)")
            except Exception as e2:
                steps.append(
                    {"name": "download_audio", "ok": False, "error": f"{e}; fallback_playurl_error={e2}", "duration": round(time.monotonic() - t, 3)}
                )
                log(f"Step download_audio: FAIL {e}; fallback_playurl_error={e2}")
                combined = f"{e} {e2}"
                if "412" in combined or "Precondition Failed" in combined:
                    log(
                        "HINT: HTTP 412 = B站反爬拦截，yt-dlp 版本可能过旧；"
                        "请运行 python -m pip install -U yt-dlp 后重试"
                    )

    # 4) video
    video_path: Optional[Path] = None
    if (not pages_index) and ("video" in download_set):
        t = time.monotonic()
        try:
            video_path = ytdlp_download_video(url, outdir, cookiefile=cookiefile_str, proxy=proxy, cookie_header=cookie_header)
            steps.append({"name": "download_video", "ok": True, "duration": round(time.monotonic() - t, 3)})
            log("Step download_video: OK")
        except Exception as e:
            steps.append({"name": "download_video", "ok": False, "error": str(e), "duration": round(time.monotonic() - t, 3)})
            log(f"Step download_video: FAIL {e}")

    # 5) danmaku xml
    # 主cid弹幕
    if not pages_index:
        t = time.monotonic()
        danmaku_xml = fetch_danmaku_xml(info, cookie_header=cookie_header, proxy=proxy)
        if danmaku_xml:
            outputs.setdefault("danmaku", []).extend(write_danmaku_artifacts(danmaku_xml, outdir / "danmaku.xml"))
        steps.append({"name": "danmaku_main", "ok": bool(danmaku_xml), "duration": round(time.monotonic() - t, 3)})
        log(f"Step danmaku_main: {'OK' if danmaku_xml else 'EMPTY'}")
    # 所有分P弹幕（如果有entries）
    if isinstance(info.get("entries"), list):
        count = 0
        for e in info["entries"]:
            try:
                from .utils import extract_cid_from_info
                cid = extract_cid_from_info(e)
                if not cid:
                    continue
                xml = fetch_danmaku_xml(e, cookie_header=cookie_header, proxy=proxy)
                if xml:
                    outputs.setdefault("danmaku", []).extend(
                        write_danmaku_artifacts(xml, outdir / f"danmaku_{cid}.xml")
                    )
                    count += 1
            except Exception:
                log(f"Step danmaku_entry: FAIL cid={cid if 'cid' in locals() else None}")
        log(f"Step danmaku_entries: count={count}")

    # 6) comments snapshot
    t = time.monotonic()
    comments = fetch_comments_snapshot(bvid=bvid, cookie_header=cookie_header, proxy=proxy, pages=comment_pages)
    write_json(json_dir / "comments.json", comments)
    outputs["comments_json"] = str((json_dir / "comments.json").relative_to(outdir))
    steps.append({"name": "comments_snapshot", "ok": True, "duration": round(time.monotonic() - t, 3)})
    log("Step comments_snapshot: OK")

    # 7.5) write harvest logs before manifest
    end_ts = time.monotonic()
    end_at = _now_iso()
    run_record = {
        "bvid": bvid,
        "original_url": original_url,
        "resolved_url": url,
        "started_at": start_at,
        "ended_at": end_at,
        "duration_seconds": round(end_ts - start_ts, 3),
        "download": sorted(list(download_set)),
        "proxy": proxy,
        "cookie": cookie_brief,
        "tools": tools,
        "steps": steps,
        "outputs": outputs,
    }
    write_json(run_json, run_record)
    log("Harvest finished.")

    # 7) structured view
    if structured_root is not None:
        sdir = (structured_root / bvid)
        write_structured(info, sdir, comments)

    return {
        "bvid": bvid,
        "outdir": str(outdir),
        "audio": str(audio_path) if audio_path else None,
        "video": str(video_path) if video_path else None,
    }
