from __future__ import annotations

from bilibili_enrich.pbp import extract_pbp_peaks


def test_extract_pbp_peaks_topk() -> None:
    payload = {
        "step_sec": 3,
        "events": {
            # idx: 0.. -> second = idx * 3
            "default": [0, 10, 20, 15, 5, 100, 90],
        },
    }
    peaks = extract_pbp_peaks(payload, top_k=3)
    assert [p.value for p in peaks] == [100.0, 90.0, 20.0]
    assert [p.second for p in peaks] == [5 * 3, 6 * 3, 2 * 3]

