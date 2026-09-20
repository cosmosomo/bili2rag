# bili2rag

Turn Bilibili videos into a **local, RAG-ready corpus**: harvest audio / subtitles / danmaku / comments / metadata, run local ASR transcription (Simplified Chinese enforced), and archive everything into a readable, self-describing library.

```
Bilibili video ──▶ harvest ──▶ ASR ──▶ export ──▶ library/<UP>/<date>_<title>_<BV>/
                    │                      │
             audio/subs/comments      transcript.txt (.srt)
             danmaku/metadata         Simplified Chinese enforced
```

## Why

RAG over video content needs clean text, not clickstreams. bili2rag focuses on one job done well:

- **Single source of truth** — `library/` holds the final assets; `output/` is a prunable staging area (`--prune-output` deletes it after successful export).
- **Readable archiving** — directory names look like `2026-01-25_标题_BVxxxx`, so your file tree *is* your index.
- **Local-first ASR** — faster-whisper on CPU/CUDA; transcripts are converted to Simplified Chinese on write.
- **Risk-aware networking** — WBI signing, buvid bootstrap, space-API risk retries (`-352`), stale-cookie pre-flight check, yt-dlp 412 hints.

## Install

```powershell
git clone https://github.com/cosmosomo/bili2rag.git bili2rag
cd bili2rag
pip install -e .            # or: pip install -e ".[dev]" for tests
```

Requirements: Python ≥ 3.10 and ffmpeg for audio extraction — any one of:

- ffmpeg on `PATH` (winget/choco/apt), or
- drop `ffmpeg(.exe)` + `ffprobe(.exe)` into a `bin/` folder at the repo root (portable, git-ignored), or
- `pip install -e ".[ffmpeg]"` (pulls `imageio-ffmpeg`).

A GPU is optional (`--asr-device cuda --asr-compute float16`).

## Quick start

1. Export your bilibili.com cookies (any browser extension) and save as `cookie.txt` next to the repo root — see `cookie.txt.example` for the accepted formats. `SESSDATA` is required for search/uploader discovery.

2. Fetch a single video (harvest → ASR → export → prune):

```powershell
bili2rag run --url BV15JqABoEvj --cookies cookie.txt --prune-output
# GPU transcription:
bili2rag run --url BV15JqABoEvj --cookies cookie.txt --asr-device cuda --asr-compute float16 --prune-output
```

3. Bulk-fetch everything new from an uploader (one subprocess per video; done/partial/unavailable are skipped via the single completion predicate):

```powershell
python -m bilibili_get grab-uploader --seed-bvid BV1ZnzhB2EXe --new-limit 0 --cookies cookie.txt --asr-device cuda --asr-compute float16 --prune-output
# or from a targets file (topics use this too):
python -m bilibili_get grab-targets --targets-file path\to\targets.txt --prune-output
```

4. Health-check your library and close the recovery loop:

```powershell
python scripts/doctor.py                    # three-account view: gaps / unavailable / leftovers
python -m bilibili_get repair --library-root library --asr-device cuda --asr-compute float16
                                           # in-place ASR for partials (manifest rewritten);
                                           # prints a ready grab-targets command for refetches
```

Full walkthrough (topics, snapshots, PBP, catalogs, category navigation, FAQ): **[docs/USAGE.md](docs/USAGE.md)**. Architecture & design decisions: **[docs/DESIGN.md](docs/DESIGN.md)**.

## Fast by default (v0.2.1+)

- **CC-subtitle bypass** — if a zh subtitle was harvested, transcripts are materialized from it directly (better than whisper-base, zero GPU) and audio download is skipped (`--keep-audio` to disable).
- **Deferred ASR sweep** — batch fetching runs without inline ASR; one single-model sweep afterwards transcribes everything (model loads once per batch, not per video).
- **Parallel fetch** — 2 concurrent per-video subprocesses by default (`--fetch-workers`, keep ≤3 for risk-control friendliness).

## Optional OpenCLI bridge (v0.3, `feat/opencli-bridge`)

[OpenCLI](https://www.npmjs.com/package/@jackwener/opencli) is an **optional** subprocess dependency (like yt-dlp) that adds capabilities the pipeline lacks via its browser-session channel. Everything degrades gracefully when it is missing.

- `grab-hot [--source hot|ranking]` — hot/ranking **discovery head** feeding the same batch engine (`--require-any/--require-all` title filters, all batch flags apply).
- `cookie-refresh` — **cookie supply chain, no more extension-export dance**: merges the logged-in browser's cookies (via OpenCLI `document.cookie`), and when SESSDATA is missing/stale it renders a QR code (pure Python, no browser) and completes the ticket redemption hop itself — verified live end-to-end 2026-09-20 (scan → poll → crossDomain Set-Cookie → nav validation).
- `scripts/backfill_comments.py` — post-hoc **comments backfill** through the official API (incl. 楼中楼 via `--deep N`) for videos whose `comments.txt` is missing/empty; provenance in `json/comments_opencli.json`, manifest rewritten. Zero-comment videos are classified as `EMPTY_RESULT` (normal outcome), never faked.
- Library API: `bilibili_opencli.bridge` (`available()` / `chart()` / `comments()` / `summary()`) and `bilibili_opencli.cookies` (`qr_login_flow()` / `write_netscape()` / `validate_login()`).

## Cookie & access matrix

Not every feature needs a cookie. Check before troubleshooting:

| Capability | No cookie / stale cookie |
|---|---|
| `run` on public videos (audio / danmaku / metadata / cover) | ✅ works |
| ASR, `repair`, `doctor`, export/catalog/nav tools | ✅ works (pure local) |
| Comments snapshot | ✅ best-effort (first-page hot comments + total count) |
| `grab-uploader` (space discovery), keyword `search` | ❌ requires valid `SESSDATA`; stale cookie aborts early with a hint |
| Member-only / 充电专属 media streams | ❌ explicit failure even with valid cookie (never faked) |
| CC/AI subtitles & higher resolutions on some videos | ⚠️ partially requires login |

### Getting cookies (3 steps)

1. Log in to [bilibili.com](https://www.bilibili.com) in your browser (avatar visible top-right).
2. Use an extension such as **"Get cookies.txt"** and export **while on a bilibili.com tab** (exporting from another site's tab gives you the wrong cookies).
3. Save as `cookie.txt` in the repo root. It **must contain `SESSDATA=…`**; both header-style and Netscape formats are accepted (see `cookie.txt.example`).

### When a cookie goes stale

- Symptoms: space/search APIs fail with a misleading `-352` "risk control" error, while `run` keeps working but prints `[COOKIE WARN]`.
- Cause: `SESSDATA` expired, or invalidated by logging in again elsewhere (single-device policy).
- Fix: re-export and overwrite `cookie.txt`. Nothing else to clean up.
- ⚠️ `SESSDATA` is a login credential. `cookie.txt` is git-ignored; never share it.

## Pipeline / packages

| Package | Role |
|---|---|
| `bilibili_get` | CLI entry: `run` (URL→harvest→ASR→export), `grab-uploader` / `grab-targets` (single batch engine), `repair`, `export`, `search`, `uploader` |
| `bilibili_harvester` | yt-dlp based harvesting: metadata, audio/video/subtitles, danmaku, comments snapshot; signed playurl fallback |
| `bilibili_asr` | faster-whisper transcription, per-page aggregation, Simplified-Chinese normalization |
| `bilibili_search` | Search & uploader discovery: WBI signing, space listing with `-352` backoff, session/cookie handling |
| `bilibili_opencli` | Optional OpenCLI bridge: hot/ranking discovery, official-API comments (楼中楼), AI summaries |
| `bilibili_enrich` | Optional enrichment: PBP (high-energy bar), videoshot snapshots |
| `bilibili_library` | Export to readable dirs, NFO/manifest (sha256), naming, completion predicate, txt bundles |

Plus `scripts/`: topic collection (engine + pointer post-processing), doctor (library health), catalog & category navigation (`_by_category/` junctions + `INDEX.md`), txt bundle export.

## Bundled agent skill

`.agents/skills/bili2rag/` ships an **agent skill** (SKILL.md + command reference + workflow recipes + a cross-source merge script) so AI coding agents operating this repo get the full operating manual — task→command map, cookie pitfalls (`-352` = stale cookie, not risk control), long-batch background pattern, and the red lines from `docs/DESIGN.md`. Tools that discover `.agents/skills/` pick it up automatically; otherwise just read that SKILL.md.

## Library layout

```
library/
  <UP主>/
    2026-01-25_<title>_BVxxxx/
      audio.m4a|mp3          asr/transcript.txt|.srt|segments.json
      subtitles/…            snapshots/…        json/{metadata,comments,streams}.json
      comments.txt           manifest.json      bilibili.url      source.json
  _topics/<topic>/<video>/pointer.json          # pointer index (no duplicate assets)
  _exports/txt/{uploader,topic}/<name>/{*.txt,ALL.txt,index.json}
  _catalog/{uploader_catalog.json,uploader_catalog.md,INDEX.md}
  _by_category/<category>/<UP主>/               # junctions for topic browsing
```

## Testing

```powershell
pip install -e ".[dev]"
pytest
```

## Disclaimer

Use in accordance with Bilibili's Terms of Service and the laws of your jurisdiction. For lawful learning and research only; do not use for infringement or abuse. Comments fetching is best-effort and bounded by server-side anti-spam limits.

## License

MIT
