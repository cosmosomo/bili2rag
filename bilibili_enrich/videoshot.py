from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from PIL import Image


@dataclass(frozen=True)
class VideoshotMeta:
    index: List[float]
    image: List[str]
    img_x_len: int
    img_y_len: int
    img_x_size: int
    img_y_size: int


@dataclass(frozen=True)
class SnapshotMatch:
    requested_second: float
    matched_second: float
    sheet_index: int
    row: int
    col: int
    left: int
    top: int
    right: int
    bottom: int


def parse_videoshot_meta(payload: Dict[str, Any]) -> VideoshotMeta:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("videoshot payload missing data")

    index = data.get("index")
    image = data.get("image")
    if not isinstance(index, list) or not isinstance(image, list):
        raise ValueError("videoshot payload missing index/image")

    def _int_field(name: str, default: int) -> int:
        v = data.get(name, default)
        if not isinstance(v, int) or v <= 0:
            raise ValueError(f"videoshot payload invalid {name}")
        return v

    return VideoshotMeta(
        index=[float(x) for x in index],
        image=[str(x) for x in image],
        img_x_len=_int_field("img_x_len", 10),
        img_y_len=_int_field("img_y_len", 10),
        img_x_size=_int_field("img_x_size", 160),
        img_y_size=_int_field("img_y_size", 90),
    )


def find_closest_index(index_list: List[float], second: float) -> Tuple[int, float]:
    if not index_list:
        raise ValueError("empty index_list")
    best_i = 0
    best_diff = float("inf")
    for i, t in enumerate(index_list):
        d = abs(float(t) - float(second))
        if d < best_diff:
            best_diff = d
            best_i = i
    return best_i, float(index_list[best_i])


def match_snapshot(meta: VideoshotMeta, requested_second: float) -> SnapshotMatch:
    closest_idx, matched_second = find_closest_index(meta.index, requested_second)

    per_sheet = int(meta.img_x_len) * int(meta.img_y_len)
    if per_sheet <= 0:
        raise ValueError("invalid sprite sheet grid")

    sheet_index = int(closest_idx) // per_sheet
    pos_in_sheet = int(closest_idx) % per_sheet
    row = pos_in_sheet // int(meta.img_x_len)
    col = pos_in_sheet % int(meta.img_x_len)

    left = col * int(meta.img_x_size)
    top = row * int(meta.img_y_size)
    right = left + int(meta.img_x_size)
    bottom = top + int(meta.img_y_size)

    return SnapshotMatch(
        requested_second=float(requested_second),
        matched_second=float(matched_second),
        sheet_index=sheet_index,
        row=row,
        col=col,
        left=left,
        top=top,
        right=right,
        bottom=bottom,
    )


def crop_snapshot_from_sheet(sheet_img: Image.Image, match: SnapshotMatch) -> Image.Image:
    # PIL crop is non-destructive and returns a new image
    return sheet_img.crop((match.left, match.top, match.right, match.bottom))

