"""The single batch orchestration engine (see docs/DESIGN.md §4).

Every bulk entrypoint (grab-uploader, grab-targets) shares run_batch():

    done()                       -> skip
    partial (audio, no transcript) -> skip + ledger   (rule E: batches never
                                   re-harvest what repair can fix in place)
    unavailable (unless included) -> skip
    otherwise                    -> subprocess `bilibili_get run` (process isolation)
        rc == 0                  -> verify done()
        rc != 0                  -> in-process salvage (export + prune);
                                     partial -> ledger; explicit 404/403 ->
                                     unavailable; else -> failure ledger

Decisions consult only the filesystem predicate + unavailable.jsonl.
failures.jsonl is append-only and never read back for decisions.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from bilibili_library.completion import (
    CLASS_NO_TRANSCRIPT_WITH_AUDIO,
    CLASS_OK,
    append_failure,
    classify,
    find_video_dir,
    is_done,
    list_unavailable,
    mark_unavailable,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Conservative patterns: only explicit not-found / permission / invisible errors
# may pin the permanent unavailable state. Transient failures must not.
# -87008 = 稿件不可见 (deleted / charging-exclusive / private); retry via
# --include-unavailable after refreshing cookies.
_UNAVAILABLE_PATTERNS = ("-404", "-403", "-87008", "404 Not Found", "啥都木有", "稿件不可见", "视频不存在")


@dataclass(frozen=True)
class BatchItem:
    bvid: str
    title: str = ""


@dataclass
class BatchConfig:
    run_dir: Path
    cookies: str = "cookie.txt"
    proxy: Optional[str] = None
    output_root: str = "output"
    library_root: str = "library"
    download: str = "audio,subtitles,cover"
    comment_pages: int = 1
    pbp: bool = False
    snapshots: str = "smart"
    snapshot_k: int = 5
    no_asr: bool = False
    no_export: bool = False
    asr_device: str = "cpu"
    asr_compute: str = "int8"
    asr_model: str = "auto"
    asr_lang: Optional[str] = None
    if_exists: str = "skip"
    prune_output: bool = False
    between_sleep: float = 0.0
    fail_fast: bool = False
    new_limit: int = 0
    include_unavailable: bool = False


@dataclass
class BatchReport:
    discovered: int = 0
    exported: List[str] = field(default_factory=list)
    skipped_done: List[str] = field(default_factory=list)
    skipped_partial: List[str] = field(default_factory=list)
    skipped_unavailable: List[str] = field(default_factory=list)
    failed: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "discovered": self.discovered,
            "exported": self.exported,
            "skipped_done": self.skipped_done,
            "skipped_partial": self.skipped_partial,
            "skipped_unavailable": self.skipped_unavailable,
            "failed": self.failed,
        }

    def exit_code(self) -> int:
        return 1 if self.failed else 0


def _print_utf8(line: str) -> None:
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))
    try:
        sys.stdout.buffer.flush()
    except Exception:
        pass


def build_run_cmd(cfg: BatchConfig, bvid: str, *, if_exists_override: Optional[str] = None) -> List[str]:
    if_exists = if_exists_override or cfg.if_exists
    cmd: List[str] = [
        sys.executable,
        "-m",
        "bilibili_get",
        "run",
        "--url",
        bvid,
        "--cookies",
        cfg.cookies,
        "--output-root",
        cfg.output_root,
        "--library-root",
        cfg.library_root,
        "--download",
        cfg.download,
        "--comment-pages",
        str(cfg.comment_pages),
        "--asr-device",
        cfg.asr_device,
        "--asr-compute",
        cfg.asr_compute,
        "--asr-model",
        cfg.asr_model,
        "--if-exists",
        if_exists,
    ]
    if cfg.pbp:
        cmd.append("--pbp")
    snapshots = str(cfg.snapshots or "none").strip()
    if snapshots.lower() != "none":
        cmd.extend(["--snapshots", snapshots, "--snapshot-k", str(int(cfg.snapshot_k))])
    if cfg.proxy:
        cmd.extend(["--proxy", cfg.proxy])
    if cfg.no_asr:
        cmd.append("--no-asr")
    if cfg.no_export:
        cmd.append("--no-export")
    if cfg.asr_lang:
        cmd.extend(["--asr-lang", cfg.asr_lang])
    if cfg.prune_output:
        cmd.append("--prune-output")
    return cmd


def _log_says_unavailable(log_path: Path) -> bool:
    try:
        text = log_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False
    return any(p in text[-20000:] for p in _UNAVAILABLE_PATTERNS)


def _salvage(cfg: BatchConfig, bvid: str) -> Optional[Path]:
    """In-process salvage: export output/<bvid> to library, prune if asked.

    Returns the library video dir on success, None when salvage is impossible
    (missing artifacts, export errors, ...). Never fakes data.
    """
    out_bvid_dir = (Path(cfg.output_root) / bvid).resolve()
    if cfg.no_export or not out_bvid_dir.exists():
        return None
    from bilibili_library.exporter import export_bvid

    try:
        ex = export_bvid(
            bvid,
            output_root=Path(cfg.output_root),
            library_root=Path(cfg.library_root),
            if_exists="overwrite",  # salvage never discards artifacts to an old dir
            dry_run=False,
        )
    except Exception:
        return None
    if cfg.prune_output:
        import shutil

        shutil.rmtree(out_bvid_dir, ignore_errors=True)
    return ex.dest_dir


def run_batch(items: List[BatchItem], cfg: BatchConfig) -> BatchReport:
    library_root = Path(cfg.library_root)
    logs_dir = cfg.run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    run_id = cfg.run_dir.name

    unavailable = list_unavailable(library_root)
    report = BatchReport(discovered=len(items))

    for item in items:
        bvid = item.bvid
        title = item.title

        vdir = find_video_dir(library_root, bvid)
        state = classify(vdir)
        if state == "ok":
            report.skipped_done.append(bvid)
            _print_utf8(f"[SKIP] {bvid} already done")
            continue
        if state == "no_transcript_with_audio":
            append_failure(
                library_root,
                bvid=bvid,
                stage="partial_asr",
                error="audio present, transcript missing (await repair)",
                title=title,
                run_id=run_id,
            )
            report.skipped_partial.append(bvid)
            _print_utf8(f"[SKIP] {bvid} partial (has audio, no transcript) -> repair")
            continue
        if (bvid in unavailable) and not cfg.include_unavailable:
            report.skipped_unavailable.append(bvid)
            _print_utf8(f"[SKIP] {bvid} unavailable")
            continue
        if cfg.new_limit and len(report.exported) >= int(cfg.new_limit):
            _print_utf8(f"[STOP] reached --new-limit={cfg.new_limit} (exported={len(report.exported)})")
            break

        log_path = logs_dir / f"{bvid}.log"
        _print_utf8(f"[RUN] {bvid} {title}".rstrip())
        # If an incomplete dir already exists, `skip` would silently discard
        # the fresh harvest (export skipped + output pruned) — force overwrite.
        if_exists_override = "overwrite" if vdir is not None else None
        cmd = build_run_cmd(cfg, bvid, if_exists_override=if_exists_override)
        try:
            with log_path.open("w", encoding="utf-8") as f:
                p = subprocess.run(cmd, cwd=str(_REPO_ROOT), stdout=f, stderr=subprocess.STDOUT)
        except Exception as e:
            rec = {"bvid": bvid, "stage": "subprocess_start", "error": str(e)}
            report.failed.append(rec)
            append_failure(library_root, bvid=bvid, stage="subprocess_start", error=str(e), title=title, run_id=run_id)
            _print_utf8(f"[FAIL] start {bvid}: {e}")
            if cfg.fail_fast:
                break
            continue

        salvaged: Optional[Path] = None
        if p.returncode != 0:
            # The subprocess may crash mid-pipeline while having already
            # exported (overwrite) or left a salvageable output dir. Both are
            # settled by the unified final-state classification below.
            salvaged = _salvage(cfg, bvid)

        final_dir = find_video_dir(library_root, bvid)
        state = classify(final_dir)
        if state == CLASS_OK:
            report.exported.append(bvid)
            tag = " (salvaged)" if salvaged is not None else ""
            _print_utf8(f"[OK] {bvid}{tag} -> {final_dir}")
        elif state == CLASS_NO_TRANSCRIPT_WITH_AUDIO:
            append_failure(
                library_root, bvid=bvid, stage="partial_asr",
                error=f"returncode={p.returncode}, no transcript after run", title=title, run_id=run_id,
            )
            report.skipped_partial.append(bvid)
            _print_utf8(f"[PARTIAL] {bvid} exported without transcript -> repair")
        else:
            # No audio at all: the stream is unavailable. Check the log even
            # on rc==0 — harvest logs yt-dlp -87008 warnings without failing.
            if _log_says_unavailable(log_path):
                mark_unavailable(library_root, bvid, reason=f"auto: no audio; returncode={p.returncode} (404/403/-87008)")
                unavailable[bvid] = {"bvid": bvid}
                append_failure(
                    library_root, bvid=bvid, stage="unavailable",
                    error=f"no audio; returncode={p.returncode}", title=title, run_id=run_id,
                )
                report.skipped_unavailable.append(bvid)
                _print_utf8(f"[UNAVAILABLE] {bvid} no audio; marked (will skip in future runs)")
                continue
            rec = {
                "bvid": bvid,
                "stage": "subprocess" if p.returncode != 0 else "no_audio_after_run",
                "returncode": p.returncode,
                "log": str(log_path),
            }
            report.failed.append(rec)
            append_failure(
                library_root, bvid=bvid, stage=rec["stage"],
                error=f"returncode={p.returncode} log={log_path}", title=title, run_id=run_id,
            )
            _print_utf8(f"[FAIL] {bvid} returncode={p.returncode} log={log_path}")
            if cfg.fail_fast:
                break
            continue
        _sleep(cfg)

    _write_report(cfg.run_dir, report)
    _print_utf8(
        f"[DONE] discovered={report.discovered} exported={len(report.exported)} "
        f"skipped_done={len(report.skipped_done)} partial={len(report.skipped_partial)} "
        f"unavailable={len(report.skipped_unavailable)} failed={len(report.failed)} dir={cfg.run_dir}"
    )
    return report


def _sleep(cfg: BatchConfig) -> None:
    if cfg.between_sleep and float(cfg.between_sleep) > 0:
        time.sleep(float(cfg.between_sleep))


def _write_report(run_dir: Path, report: BatchReport) -> None:
    import json

    (run_dir / "report.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def extract_bvids_from_lines(lines: List[str]) -> List[BatchItem]:
    """Parse any text (targets files, search results) into batch items.

    Recognized line shapes (bvid is always extracted by pattern):
      【title】https://www.bilibili.com/video/BVxxxx        (engine targets.txt)
      BVxxxx<TAB>title                                      (space listing)
      anything containing a BV id                           (best effort)
    """
    import re

    seen: set = set()
    items: List[BatchItem] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = re.search(r"(BV[0-9A-Za-z]+)", line)
        if not m:
            continue
        bvid = m.group(1)
        if bvid in seen:
            continue
        seen.add(bvid)
        title = ""
        tm = re.search(r"【(?P<title>[^】]{1,200})】", line)
        if tm:
            title = tm.group("title")
        elif "\t" in line:
            left, _, right = line.partition("\t")
            title = right.strip() if left.strip().startswith("BV") or "BV" not in right else left.strip()
        items.append(BatchItem(bvid=bvid, title=title))
    return items
