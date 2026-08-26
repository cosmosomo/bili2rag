from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


STATE_FILENAME = "pipeline_state.json"
STAGES = ("harvest", "asr", "export")
_STATUSES = {"pending", "running", "succeeded", "failed", "skipped"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(bvid_dir: Path) -> Path:
    return bvid_dir / "logs" / STATE_FILENAME


def _empty_stages() -> Dict[str, Dict[str, Any]]:
    return {stage: {"status": "pending"} for stage in STAGES}


def _validate_state(state: Dict[str, Any], *, bvid: str) -> None:
    if state.get("bvid") != bvid:
        raise ValueError(f"pipeline state bvid mismatch: expected={bvid} actual={state.get('bvid')}")

    stages = state.get("stages")
    if not isinstance(stages, dict):
        raise ValueError("pipeline state is missing stages")

    for stage in STAGES:
        record = stages.get(stage)
        if not isinstance(record, dict):
            raise ValueError(f"pipeline state is missing stage record: {stage}")
        if record.get("status") not in _STATUSES:
            raise ValueError(f"pipeline state has invalid status for {stage}: {record.get('status')}")


def load_or_create_state(bvid_dir: Path, *, bvid: str, source_url: str) -> Dict[str, Any]:
    """Load a pipeline state file or create its initial, explicit stage records."""
    path = _state_path(bvid_dir)
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid pipeline state JSON: {path}") from exc
        if not isinstance(state, dict):
            raise ValueError(f"pipeline state must be an object: {path}")
        _validate_state(state, bvid=bvid)
        return state

    now = _utc_now_iso()
    state = {
        "schema_version": 1,
        "bvid": bvid,
        "source_url": source_url,
        "created_at": now,
        "updated_at": now,
        "stages": _empty_stages(),
    }
    write_state(bvid_dir, state)
    return state


def write_state(bvid_dir: Path, state: Dict[str, Any]) -> None:
    """Atomically persist the state so an interrupted process never leaves partial JSON."""
    bvid = state.get("bvid")
    if not isinstance(bvid, str) or not bvid:
        raise ValueError("pipeline state bvid must be a non-empty str")
    _validate_state(state, bvid=bvid)

    path = _state_path(bvid_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _utc_now_iso()
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def set_stage(
    bvid_dir: Path,
    state: Dict[str, Any],
    stage: str,
    status: str,
    *,
    detail: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    if stage not in STAGES:
        raise ValueError(f"unknown pipeline stage: {stage}")
    if status not in _STATUSES:
        raise ValueError(f"invalid pipeline stage status: {status}")
    if status == "failed" and not error:
        raise ValueError("failed pipeline stage requires an error message")
    if status != "failed" and error is not None:
        raise ValueError("only failed pipeline stages may have an error message")

    record: Dict[str, Any] = {"status": status, "updated_at": _utc_now_iso()}
    if detail is not None:
        record["detail"] = detail
    if error is not None:
        record["error"] = error
    state["stages"][stage] = record
    write_state(bvid_dir, state)


def stage_succeeded(state: Dict[str, Any], stage: str) -> bool:
    if stage not in STAGES:
        raise ValueError(f"unknown pipeline stage: {stage}")
    return state["stages"][stage].get("status") == "succeeded"


def stage_records(state: Dict[str, Any]) -> Iterable[tuple[str, Dict[str, Any]]]:
    """Expose stage records in execution order for inspection tools."""
    for stage in STAGES:
        yield stage, state["stages"][stage]
