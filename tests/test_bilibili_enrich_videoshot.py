from __future__ import annotations

from PIL import Image

from bilibili_enrich.videoshot import SnapshotMatch, VideoshotMeta, crop_snapshot_from_sheet, match_snapshot


def _make_sheet(img_x_len: int, img_y_len: int, img_x_size: int, img_y_size: int) -> Image.Image:
    w = img_x_len * img_x_size
    h = img_y_len * img_y_size
    sheet = Image.new("RGB", (w, h), (0, 0, 0))
    # Fill each cell with a deterministic color derived from (row, col)
    for r in range(img_y_len):
        for c in range(img_x_len):
            color = ((r * 40) % 256, (c * 40) % 256, ((r + c) * 40) % 256)
            for y in range(r * img_y_size, (r + 1) * img_y_size):
                for x in range(c * img_x_size, (c + 1) * img_x_size):
                    sheet.putpixel((x, y), color)
    return sheet


def test_match_and_crop_snapshot_from_sheet() -> None:
    meta = VideoshotMeta(
        index=[0.0, 1.0, 2.0, 3.0],
        image=["https://example.invalid/sheet0.jpg"],
        img_x_len=2,
        img_y_len=2,
        img_x_size=8,
        img_y_size=8,
    )
    sheet = _make_sheet(meta.img_x_len, meta.img_y_len, meta.img_x_size, meta.img_y_size)

    # requested 2.2s should match closest index 2.0s (closest_idx=2)
    m: SnapshotMatch = match_snapshot(meta, 2.2)
    assert m.sheet_index == 0
    assert m.row == 1 and m.col == 0  # idx=2 -> (row=1,col=0) for 2x2 grid

    cropped = crop_snapshot_from_sheet(sheet, m)
    assert cropped.size == (meta.img_x_size, meta.img_y_size)
    # Pixel (0,0) in the crop should match the cell color at (row=1,col=0)
    assert cropped.getpixel((0, 0)) == ((1 * 40) % 256, (0 * 40) % 256, ((1 + 0) * 40) % 256)

