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
git clone <this repo> bili2rag
cd bili2rag
pip install -e .            # or: pip install -e ".[dev]" for tests
```

Requirements: Python ≥ 3.10, [ffmpeg] on PATH (audio extraction). A GPU is optional (`--asr-device cuda --asr-compute float16`).

## Quick start

1. Export your bilibili.com cookies (any browser extension) and save as `cookie.txt` next to the repo root — see `cookie.txt.example` for the accepted formats. `SESSDATA` is required for search/uploader discovery.

2. Fetch a single video (harvest → ASR → export → prune):

```powershell
bili2rag run --url BV15JqABoEvj --cookies cookie.txt --prune-output
# GPU transcription:
bili2rag run --url BV15JqABoEvj --cookies cookie.txt --asr-device cuda --asr-compute float16 --prune-output
```

3. Bulk-fetch everything new from an uploader (one subprocess per video, auto-skips what's already exported):

```powershell
python scripts/grab_uploader_new_isolated.py --seed-bvid BV1ZnzhB2EXe --new-limit 0 --cookies cookie.txt --asr-device cuda --asr-compute float16 --prune-output
```

4. Health-check your library and export "all transcripts" bundles:

```powershell
python scripts/doctor.py                    # find missing transcripts/audio/empty comments
python scripts/export_txt_bundle.py --uploader "某UP主" --with-header --concat-all
```

Full walkthrough (topics, snapshots, PBP, catalogs, category navigation, FAQ): **[docs/USAGE.md](docs/USAGE.md)**.

## Pipeline / packages

| Package | Role |
|---|---|
| `bilibili_get` | Low-entropy CLI entry: `run` (URL→harvest→ASR→export), `export`, `search`, `uploader`, `asr-uploader` |
| `bilibili_harvester` | yt-dlp based harvesting: metadata, audio/video/subtitles, danmaku, comments snapshot; signed playurl fallback |
| `bilibili_asr` | faster-whisper transcription, per-page aggregation, Simplified-Chinese normalization |
| `bilibili_search` | Search & uploader discovery: WBI signing, space listing with `-352` backoff, session/cookie handling |
| `bilibili_enrich` | Optional enrichment: PBP (high-energy bar), videoshot snapshots |
| `bilibili_library` | Export to readable dirs, NFO/manifest (sha256), naming, txt bundles |

Plus `scripts/`: bulk grabbers, topic collection, doctor (library health), catalog & category navigation (`_by_category/` junctions + `INDEX.md`).

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

[MIT](LICENSE)

[ffmpeg]: https://ffmpeg.org/download.html
