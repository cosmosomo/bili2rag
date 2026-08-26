from __future__ import annotations

import json
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from PIL import Image

from bilibili_search.session import build_web_session

from .pbp import PbpPeak, extract_pbp_peaks
from .videoshot import SnapshotMatch, VideoshotMeta, crop_snapshot_from_sheet, match_snapshot, parse_videoshot_meta


@dataclass(frozen=True)
class EnrichResult:
    bvid: str
    pbp_path: Optional[Path]
    videoshot_path: Optional[Path]
    snapshots_dir: Optional[Path]
    snapshots_index_path: Optional[Path]
    snapshots: List[SnapshotMatch]


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def _fetch_view(sess: requests.Session, bvid: str) -> Dict[str, Any]:
    r = sess.get("https://api.bilibili.com/x/web-interface/view", params={"bvid": bvid}, timeout=15)
    r.raise_for_status()
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError(f"view api error code={j.get('code')} msg={j.get('message')}")
    data = j.get("data") or {}
    if not isinstance(data, dict):
        raise RuntimeError("view api returned invalid data")
    return data


def fetch_pbp(sess: requests.Session, *, cid: str, aid: str, bvid: str) -> Dict[str, Any]:
    r = sess.get("https://bvc.bilivideo.com/pbp/data", params={"cid": cid, "aid": aid, "bvid": bvid}, timeout=15)
    r.raise_for_status()
    j = r.json()
    # PBP API doesn't have a "code" field in the documented response.
    if not isinstance(j, dict) or "events" not in j:
        raise RuntimeError("pbp api returned invalid payload")
    return j


def fetch_videoshot(sess: requests.Session, *, bvid: str, aid: str, cid: str) -> Dict[str, Any]:
    r = sess.get("https://api.bilibili.com/x/player/videoshot", params={"bvid": bvid, "aid": aid, "cid": cid}, timeout=15)
    r.raise_for_status()
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError(f"videoshot api error code={j.get('code')} msg={j.get('message')}")
    return j


def _download_image(sess: requests.Session, url: str) -> Image.Image:
    u = url.strip()
    if u.startswith("//"):
        u = "https:" + u
    r = sess.get(u, timeout=30)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content))


def enrich_bvid_dir(
    bvid_dir: Path,
    *,
    cookiefile: Optional[Path],
    proxy: Optional[str],
    pbp: bool,
    snapshots: str,
    snapshot_k: int,
) -> EnrichResult:
    """Enrich an already-harvested bvid directory.

    Writes (when requested):
      - output/<bvid>/json/pbp.json
      - output/<bvid>/json/videoshot.json
      - output/<bvid>/snapshots/*.jpg + snapshots/index.json

    This is intentionally separated from harvest to keep the low-entropy entry (`bilibili_get run`)
    extensible without coupling to any specific harvester implementation.
    """
    bvid_dir = bvid_dir.resolve()
    bvid = bvid_dir.name
    if not bvid.startswith("BV"):
        raise ValueError(f"invalid bvid dir: {bvid_dir}")

    if snapshot_k < 1:
        raise ValueError("snapshot_k must be >= 1")

    sess, _brief = build_web_session(cookiefile, proxy=proxy)

    view = _fetch_view(sess, bvid)
    aid = str(view.get("aid") or "").strip()
    cid = str(view.get("cid") or "").strip()
    if not aid or not cid:
        raise RuntimeError("missing aid/cid from view api")
    duration_sec = float(view.get("duration") or 0.0)

    json_dir = _ensure_dir(bvid_dir / "json")
    pbp_path: Optional[Path] = None
    videoshot_path: Optional[Path] = None
    snapshots_dir: Optional[Path] = None
    snapshots_index_path: Optional[Path] = None
    snapshot_matches: List[SnapshotMatch] = []

    # 1) PBP
    peaks: List[PbpPeak] = []
    pbp_payload: Optional[Dict[str, Any]] = None
    if pbp or snapshots.strip().lower() == "auto":
        pbp_payload = fetch_pbp(sess, cid=cid, aid=aid, bvid=bvid)
        pbp_path = json_dir / "pbp.json"
        _write_json(pbp_path, pbp_payload)
        peaks = extract_pbp_peaks(pbp_payload, top_k=int(snapshot_k))

    # 2) Videoshot + snapshots
    snapshots_mode = snapshots.strip().lower()
    if snapshots_mode != "none":
        shot_payload = fetch_videoshot(sess, bvid=bvid, aid=aid, cid=cid)
        videoshot_path = json_dir / "videoshot.json"
        _write_json(videoshot_path, shot_payload)

        meta: VideoshotMeta = parse_videoshot_meta(shot_payload)
        videoshot_index_strategy: str = "api_index"
        if not meta.image:
            raise RuntimeError("videoshot meta has no sprite sheet images")
        if not meta.index:
            # Some videos return empty data.index/indexs and only provide the sprite sheets.
            # We synthesize a uniform index mapping across duration so we can still generate
            # browseable snapshots.
            if duration_sec <= 0:
                videoshot_index_strategy = "missing_duration_cannot_synthesize"
            else:
                total_frames = int(meta.img_x_len) * int(meta.img_y_len) * len(meta.image)
                if total_frames <= 1:
                    videoshot_index_strategy = "insufficient_frames_cannot_synthesize"
                else:
                    step = duration_sec / float(total_frames - 1)
                    meta = VideoshotMeta(
                        index=[round(step * i, 6) for i in range(total_frames)],
                        image=meta.image,
                        img_x_len=meta.img_x_len,
                        img_y_len=meta.img_y_len,
                        img_x_size=meta.img_x_size,
                        img_y_size=meta.img_y_size,
                    )
                    videoshot_index_strategy = "synthetic_uniform_index"

        auto_strategy: Optional[str] = None
        if snapshots_mode == "auto":
            if peaks:
                auto_strategy = "pbp_peaks"
                times = [p.second for p in peaks]
            else:
                # PBP may have no usable peaks when danmaku is insufficient.
                # For usability, we fall back to uniform sampling across the video's duration
                # so "--snapshots auto" still produces browseable screenshots.
                auto_strategy = "uniform_fallback"
                if duration_sec <= 0:
                    times = []
                else:
                    k = int(snapshot_k)
                    step = duration_sec / float(k + 1)
                    times = [round(step * i, 3) for i in range(1, k + 1)]
        else:
            # comma-separated seconds: "10,60,120"
            parts = [x.strip() for x in snapshots.split(",") if x.strip()]
            if not parts:
                raise ValueError("snapshots must be 'none', 'auto', or a comma-separated list of seconds")
            times = [float(x) for x in parts]

        # de-dupe and keep a stable order (ascending seconds)
        times = sorted(list(dict.fromkeys([round(float(t), 3) for t in times])))

        snapshots_dir = _ensure_dir(bvid_dir / "snapshots")
        sheet_cache: Dict[int, Image.Image] = {}
        if times:
            for t in times:
                m = match_snapshot(meta, t)
                if m.sheet_index >= len(meta.image):
                    raise RuntimeError(f"snapshot sheet_index out of range: {m.sheet_index} >= {len(meta.image)}")

                if m.sheet_index not in sheet_cache:
                    sheet_cache[m.sheet_index] = _download_image(sess, meta.image[m.sheet_index])

                cropped = crop_snapshot_from_sheet(sheet_cache[m.sheet_index], m)
                fname = f"snapshot_{bvid}_{int(round(m.matched_second))}s.jpg"
                out_path = snapshots_dir / fname
                cropped.save(out_path, "JPEG")
                snapshot_matches.append(m)

        snapshots_index_path = snapshots_dir / "index.json"
        pbp_debug = pbp_payload.get("debug") if isinstance(pbp_payload, dict) else None
        _write_json(
            snapshots_index_path,
            {
                "bvid": bvid,
                "aid": aid,
                "cid": cid,
                "mode": snapshots_mode,
                "auto_strategy": auto_strategy,
                "pbp_debug": pbp_debug,
                "videoshot_index_strategy": videoshot_index_strategy,
                "requested": times,
                "matches": [m.__dict__ for m in snapshot_matches],
            },
        )

    return EnrichResult(
        bvid=bvid,
        pbp_path=pbp_path,
        videoshot_path=videoshot_path,
        snapshots_dir=snapshots_dir,
        snapshots_index_path=snapshots_index_path,
        snapshots=snapshot_matches,
    )
