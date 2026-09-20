"""Merge multi-source transcripts into one bucketed, 32k-capped collection.

Generalizes the merge scripts that were hand-rolled three times (蒸馏合集 /
哭猫情绪屋 / AgentHarness范式): several grab-* run reports + extra uploader
dirs (regex-filtered) + ad-hoc bvids -> bucket by title regex -> date sort
inside each bucket -> parts capped at --cap chars, split at video boundaries
only (LLM-context friendly).

Run from the bili2rag repo root (or pass --repo-root). Output goes to
library/_exports/txt/专题合集/<topic>_partNN.txt by convention.

Example:
    python merge_collection.py --repo-root . --topic "AgentHarness范式" ^
        --reports discoveries/20260912_072809_collect_harness_research/report.json ^
        --extra "AI林湛星:spec|harness|agent|架构" --bvid BV1Rt8A61Ev4 ^
        --buckets buckets.json --default-bucket "DSH生态" --cap 32000

buckets.json shape (patterns are title regexes, bucket order = part order):
    [{"name": "范式与概念", "patterns": ["harness engineering", "动画", "啥意思"]},
     {"name": "工程深潜", "patterns": ["源码解析", "架构拆解", "Agent Loop"]}]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def find_transcript(vdir: Path) -> Path | None:
    for cand in ("asr/transcript.txt", "asr/transcript_all.txt"):
        p = vdir / cand
        if p.exists():
            return p
    pages = vdir / "pages"
    if pages.is_dir():
        for pd in sorted(pages.iterdir()):
            for cand in ("asr/transcript.txt", "asr/transcript_all.txt"):
                p = pd / cand
                if p.exists():
                    return p
    return None


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", default=".", help="bili2rag 仓库根（默认当前目录）")
    ap.add_argument("--topic", required=True, help="合集名（决定输出文件名 <topic>_partNN.txt）")
    ap.add_argument("--reports", nargs="*", default=[], help="discoveries/<run>/report.json 路径，可多个")
    ap.add_argument("--extra", nargs="*", default=[], help='补充UP目录，形如 "UP名:标题正则"（对 library/UP名/ 下目录名过滤），可多个')
    ap.add_argument("--bvid", nargs="*", default=[], help="零散补充的 bvid（用户点名的视频），可多个")
    ap.add_argument("--buckets", default="", help="分桶配置 JSON 文件：[{name, patterns:[...]}]；不给则按日期单桶")
    ap.add_argument("--default-bucket", default="合集", help="未命中任何桶的残差桶名")
    ap.add_argument("--cap", type=int, default=32000, help="每卷字符上限（默认 32000）")
    ap.add_argument("--out-dir", default="library/_exports/txt/专题合集", help="输出目录")
    args = ap.parse_args(argv)

    repo = Path(args.repo_root).resolve()
    sys.path.insert(0, str(repo))
    from bilibili_library.completion import find_video_dir, is_done  # noqa: E402

    buckets: list[tuple[str, list[str]]] = []
    if args.buckets:
        raw = json.loads(Path(args.buckets).read_text(encoding="utf-8"))
        buckets = [(b["name"], list(b["patterns"])) for b in raw]

    def bucket_of(title: str) -> str:
        for name, pats in buckets:
            for p in pats:
                if re.search(p, title, re.I):
                    return name
        return args.default_bucket

    # ---- collect bvids: reports first (exported + skipped_done), dedup keep order
    bvids: list[str] = []
    for rp in args.reports:
        data = json.loads((repo / rp).read_text(encoding="utf-8"))
        bvids += data.get("exported", []) + data.get("skipped_done", [])
    bvids += list(args.bvid or [])
    seen: set[str] = set()
    bvids = [b for b in bvids if not (b in seen or seen.add(b))]

    entries = []
    missing = []

    def add_entry(vdir: Path, bvid: str) -> None:
        if not is_done(vdir):
            missing.append(bvid)
            return
        ts = find_transcript(vdir)
        if ts is None:
            missing.append(bvid)
            return
        m = re.match(r"^(\d{4}-\d{2}-\d{2})_(.*)$", vdir.name)
        entries.append({
            "date": m.group(1) if m else "",
            "title": m.group(2).rsplit("_" + bvid, 1)[0] if m else vdir.name,
            "bvid": bvid,
            "text": ts.read_text(encoding="utf-8").strip(),
        })

    for bvid in bvids:
        vdir = find_video_dir(repo / "library", bvid)
        if vdir is None:
            missing.append(bvid)
            continue
        add_entry(vdir, bvid)

    for spec in args.extra or []:
        up_name, _, pat = spec.partition(":")
        if not pat:
            print(f"[SKIP] --extra 格式应为 'UP名:正则'，收到: {spec!r}")
            continue
        up_dir = repo / "library" / up_name
        if not up_dir.is_dir():
            print(f"[SKIP] UP 目录不存在: {up_name}")
            continue
        for d in sorted(up_dir.iterdir()):
            if not d.is_dir() or "_BV" not in d.name:
                continue
            bvid = "BV" + d.name.rsplit("_BV", 1)[-1]
            if bvid in seen or not re.search(pat, d.name):
                continue
            seen.add(bvid)
            add_entry(d, bvid)

    if not entries:
        print(f"[ABORT] 没有可用条目（missing={len(missing)}）。检查 --reports/--extra 路径与库状态。")
        return 1

    # ---- bucket, then date
    order = {n: i for i, (n, _) in enumerate(buckets)}
    entries.sort(key=lambda e: (order.get(bucket_of(e["title"]), 99), e["date"]))

    blocks, stats, cur_bucket = [], {}, None
    for e in entries:
        b = bucket_of(e["title"])
        if buckets and b != cur_bucket:
            blocks.append(f"\n########## 子专题：{b} ##########\n")
            cur_bucket = b
        stats[b] = stats.get(b, 0) + 1
        head = f"===== {args.topic}_{e['title']} =====\n# bvid: {e['bvid']}\n# date: {e['date']}\n"
        blocks.append(head + e["text"] + "\n")

    # ---- split into capped parts at video boundaries
    parts, cur, cur_len = [], [], 0
    for b in blocks:
        if cur and cur_len + len(b) > args.cap:
            parts.append(cur)
            cur, cur_len = [], 0
        cur.append(b)
        cur_len += len(b)
    if cur:
        parts.append(cur)

    out_dir = (repo / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"{args.topic}*.txt"):
        old.unlink()
    w = len(str(len(parts)))
    for i, p in enumerate(parts, 1):
        out = out_dir / f"{args.topic}_part{str(i).zfill(w)}.txt"
        out.write_text("\n".join(x for x in p if x.strip()).lstrip() + "\n", encoding="utf-8")
        print(f"  {out.name}  {sum(len(x) for x in p)} 字符")
    print(f"[{args.topic}] {len(entries)} 集 -> {len(parts)} 卷 (共 {sum(len(e['text']) for e in entries)} 字符)")
    print("buckets:", stats)
    if missing:
        print(f"[WARN] {len(missing)} 条不可用（未 done 或缺转写，需 doctor/repair）: {', '.join(missing[:10])}{' ...' if len(missing) > 10 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
