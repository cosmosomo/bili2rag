from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class SnapshotsDecision:
    requested: str
    effective: str
    reason: str
    title: str


def _read_title_from_output_dir(bvid_dir: Path) -> str:
    # Prefer harvested metadata.json; it is written by harvester and contains title.
    p = (bvid_dir / "json" / "metadata.json").resolve()
    if p.exists():
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            obj = {}
        if isinstance(obj, dict):
            t = obj.get("title")
            if isinstance(t, str) and t.strip():
                return t.strip()

    # Fallback: some harvesters may only have core.json.
    p2 = (bvid_dir / "json" / "core.json").resolve()
    if p2.exists():
        try:
            obj2 = json.loads(p2.read_text(encoding="utf-8"))
        except Exception:
            obj2 = {}
        if isinstance(obj2, dict):
            t2 = obj2.get("title")
            if isinstance(t2, str) and t2.strip():
                return t2.strip()

    return ""


def resolve_snapshots_mode(requested: str, *, bvid_dir: Path) -> SnapshotsDecision:
    """Resolve snapshots mode.

    requested:
      - "none": never take snapshots
      - "auto": take snapshots (PBP peaks or uniform fallback)
      - seconds list like "10,60,120": take snapshots at explicit seconds
      - "smart": take snapshots only when title implies visual-heavy content
    """

    req = (requested or "").strip()
    req_l = req.lower()

    if req_l in ("", "none"):
        return SnapshotsDecision(requested=req or "none", effective="none", reason="explicit_none", title="")

    if req_l == "auto":
        return SnapshotsDecision(requested=req, effective="auto", reason="explicit_auto", title="")

    if req_l == "smart":
        title = _read_title_from_output_dir(bvid_dir)
        # Heuristic: "导图/思维导图" usually means the on-screen visual is essential.
        # Keep it minimal and deterministic; users can always force via --snapshots auto/seconds.
        if "导图" in title or "思维导图" in title:
            return SnapshotsDecision(requested=req, effective="auto", reason="title_match", title=title)
        return SnapshotsDecision(requested=req, effective="none", reason="title_no_match", title=title)

    # Treat anything else as explicit seconds list (validation happens later in enrich).
    return SnapshotsDecision(requested=req, effective=req, reason="explicit_seconds", title="")

