from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class PbpPeak:
    second: float
    value: float
    index: int


def extract_pbp_peaks(payload: Dict[str, Any], *, top_k: int) -> List[PbpPeak]:
    """Extract top-K PBP peaks as timepoints.

    PBP API returns:
      - step_sec: sampling interval (seconds)
      - events.default: list of vertex "heat" values along the timeline

    We convert the largest values into (second = index * step_sec).
    """
    if top_k < 1:
        raise ValueError("top_k must be >= 1")

    step_sec = payload.get("step_sec")
    if not isinstance(step_sec, (int, float)):
        raise ValueError("pbp payload missing/invalid step_sec")

    events = payload.get("events")
    if not isinstance(events, dict):
        raise ValueError("pbp payload missing/invalid events")

    # When danmaku is too few, API may return {"step_sec": 0, "events": {}}.
    if float(step_sec) <= 0:
        return []

    series = events.get("default")
    if not isinstance(series, list):
        return []

    pairs: List[tuple[float, int]] = []
    for idx, v in enumerate(series):
        if isinstance(v, (int, float)) and v > 0:
            pairs.append((float(v), idx))

    pairs.sort(key=lambda t: t[0], reverse=True)
    out: List[PbpPeak] = []
    for v, idx in pairs[:top_k]:
        out.append(PbpPeak(second=float(idx) * float(step_sec), value=v, index=idx))
    return out
