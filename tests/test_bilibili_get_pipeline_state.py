from __future__ import annotations

from pathlib import Path

from bilibili_get.pipeline_state import load_or_create_state, set_stage, stage_succeeded


def test_pipeline_state_persists_stage_transitions_atomically(tmp_path: Path) -> None:
    bvid_dir = tmp_path / "BV1TEST12345"
    state = load_or_create_state(bvid_dir, bvid="BV1TEST12345", source_url="https://www.bilibili.com/video/BV1TEST12345")

    set_stage(bvid_dir, state, "harvest", "running")
    set_stage(bvid_dir, state, "harvest", "succeeded", detail={"outdir": str(bvid_dir)})

    reloaded = load_or_create_state(bvid_dir, bvid="BV1TEST12345", source_url="ignored-on-reload")
    assert stage_succeeded(reloaded, "harvest")
    assert reloaded["stages"]["harvest"]["detail"]["outdir"] == str(bvid_dir)
    assert not (bvid_dir / "logs" / "pipeline_state.json.tmp").exists()
