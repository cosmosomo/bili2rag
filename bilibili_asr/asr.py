from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


BN_LOCAL_MODEL_ROOT = Path(__file__).resolve().parents[1] / "BiliNote_win_v1.1.1" / "models" / "whisper"

# OpenMP runtime duplication workaround on Windows (onnxruntime/ctranslate2)
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

# Best-effort: add CUDA/cuDNN DLL search paths if available
def _maybe_add_cuda_search_paths():
    try:
        from os import add_dll_directory  # type: ignore[attr-defined]
        _add = add_dll_directory  # only on Windows
    except Exception:
        return
    # CUDA via CUDA_PATH or common install path
    candidates = []
    cuda_path = os.environ.get("CUDA_PATH")
    if cuda_path:
        candidates.append(Path(cuda_path) / "bin")
    candidates.append(Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin"))
    candidates.append(Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.7\bin"))
    # cuDNN via pip package
    try:
        import importlib
        spec = importlib.util.find_spec("nvidia.cudnn")
        if spec and spec.origin:
            cudnn_bin = Path(spec.origin).resolve().parent / "bin"
            candidates.append(cudnn_bin)
    except Exception:
        pass
    # cuDNN via default program files
    candidates.append(Path(r"C:\Program Files\NVIDIA\CUDNN"))
    # Add if exists
    for d in candidates:
        try:
            if d and d.exists():
                _add(str(d))
        except Exception:
            pass

_maybe_add_cuda_search_paths()


@dataclass
class ASRConfig:
    model: Optional[str] = None  # "auto" | HF name | local dir
    device: str = "cpu"  # "cpu" | "cuda"
    compute_type: str = "int8"  # int8|float16|float32|auto
    language: Optional[str] = None  # e.g. "zh"


def find_local_model_dir() -> Optional[Path]:
    """Locate bundled faster-whisper snapshot if present."""
    try:
        snapshot_root = BN_LOCAL_MODEL_ROOT / "models--Systran--faster-whisper-base" / "snapshots"
        if snapshot_root.exists():
            for p in snapshot_root.iterdir():
                if not p.is_dir():
                    continue
                if (p / "model.bin").exists() and (p / "config.json").exists():
                    return p
    except Exception:
        pass
    return None


def load_model(cfg: ASRConfig):
    try:
        from faster_whisper import WhisperModel
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "未安装 faster-whisper，请先安装：python -m pip install --user faster-whisper"
        ) from e

    model_ref: str | Path
    if cfg.model in (None, "auto"):
        local_dir = find_local_model_dir()
        model_ref = local_dir if local_dir else "base"
    else:
        model_ref = cfg.model

    # compute type heuristic
    compute_type = cfg.compute_type or "int8"
    device = cfg.device or "cpu"

    return WhisperModel(str(model_ref), device=device, compute_type=compute_type), str(model_ref)


def pick_audio_file(bvid_dir: Path, explicit: Optional[Path] = None) -> Path:
    if explicit:
        return explicit
    cands = list(bvid_dir.glob("*.mp3")) + list(bvid_dir.glob("*.m4a")) + list(
        (bvid_dir / "audio").glob("*.mp3") if (bvid_dir / "audio").exists() else []
    )
    if not cands:
        raise FileNotFoundError(f"未在 {bvid_dir} 找到音频文件（*.mp3|*.m4a）")
    # choose largest
    cands.sort(key=lambda p: p.stat().st_size if p.exists() else 0, reverse=True)
    return cands[0]


def _iter_page_dirs(bvid_dir: Path) -> List[Path]:
    pages_root = bvid_dir / "pages"
    if not pages_root.exists():
        return []
    page_dirs = [p for p in pages_root.iterdir() if p.is_dir()]
    page_dirs.sort(key=lambda p: p.name)
    return page_dirs


def _copy_page_log_to_root(page_dir: Path, root_bvid_dir: Path) -> List[str]:
    copied: List[str] = []
    root_logs_dir = root_bvid_dir / "logs"
    root_logs_dir.mkdir(parents=True, exist_ok=True)

    for name in ("asr_run.json", "asr_run.log"):
        src = page_dir / "logs" / name
        if not src.exists():
            continue
        stem, dot, ext = name.partition(".")
        dst_name = f"{stem}_{page_dir.name}{dot}{ext}" if dot else f"{stem}_{page_dir.name}"
        dst = root_logs_dir / dst_name
        shutil.copy2(src, dst)
        copied.append(str(dst))
    return copied


def transcribe_bvid_dir(
    bvid_dir: Path,
    cfg: ASRConfig,
    *,
    audio: Optional[Path] = None,
    merge_pages: bool = True,
    fail_fast: bool = True,
) -> Dict[str, Any]:
    """Transcribe a harvested bvid directory.

    - If pages exist under <bvid>/pages/*/, transcribe per-page audio into each page's asr/ dir.
      Then (optionally) merge pages into <bvid>/asr/transcript_all.(txt|srt).
    - Otherwise, transcribe a single audio under <bvid>/ into <bvid>/asr/.
    """

    bvid_dir = bvid_dir.resolve()
    if not bvid_dir.exists():
        raise FileNotFoundError(str(bvid_dir))

    page_dirs = _iter_page_dirs(bvid_dir)
    runs: List[Dict[str, Any]] = []

    if page_dirs:
        ok_any = False
        for pd in page_dirs:
            try:
                ap = pick_audio_file(pd)
            except Exception as e:
                runs.append({"page": pd.name, "ok": False, "skipped": True, "error": str(e)})
                if fail_fast:
                    raise
                continue

            asr_dir = pd / "asr"
            try:
                result = transcribe_to_dir(ap, asr_dir, cfg)
                runs.append({"page": pd.name, "ok": True, **result})
                ok_any = True
            except Exception as e:
                runs.append({"page": pd.name, "ok": False, "error": str(e)})
                if fail_fast:
                    raise
            # always try to surface per-page logs in root logs/
            try:
                _copy_page_log_to_root(pd, bvid_dir)
            except Exception as e:
                runs.append({"page": pd.name, "ok": False, "log_copy_error": str(e)})
                if fail_fast:
                    raise

        if not ok_any:
            raise FileNotFoundError(f"未在 {bvid_dir / 'pages'} 找到任何可转写的音频文件")

        merged: Dict[str, Any] = {}
        if merge_pages:
            from .aggregate import aggregate_bvid

            srt_path, txt_path = aggregate_bvid(bvid_dir)
            merged = {"srt": str(srt_path), "txt": str(txt_path)}
        # write summary index under root logs/
        logs_dir = bvid_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "asr_runs_index.json").write_text(
            json.dumps({"runs": runs, "merged": merged}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {"mode": "pages", "bvid_dir": str(bvid_dir), "runs": runs, "merged": merged}

    # single-page
    audio_path = pick_audio_file(bvid_dir, explicit=audio)
    asr_dir = bvid_dir / "asr"
    result = transcribe_to_dir(audio_path, asr_dir, cfg)
    return {"mode": "single", "bvid_dir": str(bvid_dir), "runs": [{"ok": True, **result}], "merged": {}}


def segments_to_srt(segments: Iterable[Dict[str, Any]]) -> str:
    def fmt_ts(t: float) -> str:
        ms = int(round(t * 1000))
        h = ms // 3600000
        m = (ms % 3600000) // 60000
        s = (ms % 60000) // 1000
        ms2 = ms % 1000
        return f"{h:02d}:{m:02d}:{s:02d},{ms2:03d}"

    lines: List[str] = []
    for i, seg in enumerate(segments, start=1):
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", 0.0))
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        lines.append(str(i))
        lines.append(f"{fmt_ts(start)} --> {fmt_ts(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def transcribe_to_dir(audio_path: Path, out_dir: Path, cfg: ASRConfig) -> Dict[str, Any]:
    import time
    from datetime import datetime, timezone

    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    out_dir.mkdir(parents=True, exist_ok=True)
    bvid_dir = out_dir.parent
    logs_dir = (bvid_dir / "logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    run_log = logs_dir / "asr_run.log"
    run_json = logs_dir / "asr_run.json"

    def log(msg: str) -> None:
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
        with run_log.open("a", encoding="utf-8") as f:
            f.write(line)

    start_ts = time.monotonic()
    start_at = now_iso()
    (model, model_ref) = load_model(cfg)
    log(f"ASR start audio={audio_path.name} model={model_ref} device={cfg.device} compute={cfg.compute_type} lang={cfg.language}")

    # Perform transcription
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=cfg.language,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )

    segs: List[Dict[str, Any]] = []
    for seg in segments_iter:
        segs.append(
            {
                "start": float(seg.start) if seg.start is not None else 0.0,
                "end": float(seg.end) if seg.end is not None else 0.0,
                "text": seg.text.strip(),
                "avg_logprob": getattr(seg, "avg_logprob", None),
                "no_speech_prob": getattr(seg, "no_speech_prob", None),
                "temperature": getattr(seg, "temperature", None),
                "compression_ratio": getattr(seg, "compression_ratio", None),
            }
        )

    def to_simplified(text: str) -> str:
        # Always normalize CN output to simplified Chinese for downstream indexing.
        # This is deterministic and leaves non-CJK text unchanged.
        try:
            from hanziconv import HanziConv  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError("缺少依赖：hanziconv（用于繁体→简体转换）") from e
        return HanziConv.toSimplified(text)

    # Normalize segment text to simplified Chinese in-place.
    for s in segs:
        t = s.get("text")
        if isinstance(t, str) and t:
            s["text"] = to_simplified(t)

    # Write outputs
    (out_dir / "segments.json").write_text(
        json.dumps(
            {
                "language": getattr(info, "language", None),
                "duration": getattr(info, "duration", None),
                "segments": segs,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # transcript.txt
    full_text = " ".join(s["text"].strip() for s in segs if s.get("text"))
    (out_dir / "transcript.txt").write_text(full_text.strip() + "\n", encoding="utf-8")

    # transcript.srt
    (out_dir / "transcript.srt").write_text(segments_to_srt(segs), encoding="utf-8")

    end_ts = time.monotonic()
    end_at = now_iso()
    runtime = round(end_ts - start_ts, 3)
    result = {"segments": len(segs), "language": getattr(info, "language", None), "duration": getattr(info, "duration", None)}

    # Run log json
    run_record = {
        "audio": str(audio_path),
        "model": {"configured": cfg.model or "auto", "resolved": model_ref, "device": cfg.device, "compute_type": cfg.compute_type},
        "language": result["language"],
        "segments": result["segments"],
        "media_duration": result["duration"],
        "started_at": start_at,
        "ended_at": end_at,
        "runtime_seconds": runtime,
        "outputs": {
            "segments_json": str((out_dir / "segments.json").resolve()),
            "transcript_txt": str((out_dir / "transcript.txt").resolve()),
            "transcript_srt": str((out_dir / "transcript.srt").resolve()),
        },
    }
    (run_json).write_text(json.dumps(run_record, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"ASR done segments={result['segments']} lang={result['language']} duration={result['duration']}s runtime={runtime}s")

    return result
