from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _print_utf8(line: str) -> None:
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))
    try:
        sys.stdout.buffer.flush()
    except Exception:
        pass


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _extract_bvid_from_dirname(name: str) -> Optional[str]:
    m = re.search(r"(BV[0-9A-Za-z]+)$", name)
    return m.group(1) if m else None


def _strip_date_prefix(dirname: str) -> str:
    # Pattern: YYYY-MM-DD_...
    return re.sub(r"^\\d{4}-\\d{2}-\\d{2}_", "", dirname)


def _strip_bvid_suffix(dirname: str) -> str:
    return re.sub(r"_BV[0-9A-Za-z]+$", "", dirname)


def _normalize_title_from_dir(dirname: str) -> str:
    # Keep human intent, remove technical suffixes.
    t = dirname
    t = _strip_date_prefix(t)
    t = _strip_bvid_suffix(t)
    return t.strip()


@dataclass(frozen=True)
class VideoDir:
    uploader: str
    exported_dir: Path
    dirname: str
    bvid: str
    title: str


@dataclass(frozen=True)
class Category:
    cid: str
    name: str
    desc: str
    keywords: Sequence[Tuple[str, float]]
    negative: Sequence[str]


def _default_categories() -> List[Category]:
    # <= 20, try to keep as orthogonal as possible (domain-based).
    return [
        Category(
            cid="psych_self",
            name="心理与自我成长",
            desc="心态/情绪/内耗/依恋/创伤/自我效能/边界感/生命力等",
            keywords=(
                ("心理", 1.2),
                ("心态", 1.2),
                ("情绪", 1.0),
                ("情绪管理", 1.4),
                ("内耗", 1.4),
                ("焦虑", 1.2),
                ("抑郁", 1.2),
                ("拖延", 1.0),
                ("自我效能", 1.6),
                ("效能感", 1.4),
                ("掌控感", 1.2),
                ("安全感", 1.0),
                ("自信", 1.0),
                ("自尊", 0.9),
                ("自洽", 1.0),
                ("自我接纳", 1.2),
                ("成长", 0.6),
                ("边界感", 1.2),
                ("依恋", 1.1),
                ("创伤", 1.1),
                ("疗愈", 0.8),
                ("觉醒", 0.7),
            ),
            negative=("塔罗", "占卜", "星座", "ASMR", "助眠", "白噪音"),
        ),
        Category(
            cid="money_invest",
            name="理财与投资",
            desc="个人理财/投资/资产配置/房价/基金/股市/债券/保险等",
            keywords=(
                ("理财", 1.3),
                ("投资", 1.2),
                ("资产配置", 1.6),
                ("基金", 1.1),
                ("股票", 1.1),
                ("股市", 1.1),
                ("债券", 1.1),
                ("黄金", 0.9),
                ("房价", 1.0),
                ("房产", 0.9),
                ("通胀", 1.0),
                ("通缩", 1.0),
                ("利率", 0.9),
                ("信用", 0.8),
                ("保险", 0.9),
                ("现金流", 0.8),
            ),
            negative=("游戏", "原神", "王者", "吃鸡"),
        ),
        Category(
            cid="macro_econ",
            name="宏观经济与产业",
            desc="宏观/财政货币/产业政策/增长/周期/城市与区域/人口经济等",
            keywords=(
                ("宏观", 1.2),
                ("经济", 0.8),
                ("财政", 1.1),
                ("货币", 1.0),
                ("央行", 1.0),
                ("美联储", 1.1),
                ("人民币", 1.0),
                ("增长", 0.9),
                ("周期", 1.0),
                ("产业", 0.9),
                ("产业政策", 1.2),
                ("制造业", 0.8),
                ("城市", 0.5),
                ("人口", 0.8),
                ("出生率", 1.0),
            ),
            negative=("娱乐", "综艺", "游戏"),
        ),
        Category(
            cid="politics_ir",
            name="政治制度与国际关系",
            desc="政体/制度/国家治理结构/国际关系与地缘等（偏结构性介绍）",
            keywords=(
                ("政体", 1.2),
                ("政治制度", 1.2),
                ("政治体制", 1.2),
                ("议会制", 1.2),
                ("总统制", 1.2),
                ("半总统制", 1.2),
                ("君主立宪", 1.2),
                ("联邦制", 1.1),
                ("选举制度", 1.1),
                ("宪法", 1.0),
                ("国际关系", 1.0),
                ("地缘", 0.8),
                ("外交", 0.8),
            ),
            negative=("突发", "快讯", "今日", "刚刚"),
        ),
        Category(
            cid="society_public",
            name="社会议题与公共讨论",
            desc="社会结构/教育/性别/婚恋/代际/舆论等公共议题",
            keywords=(
                ("社会", 0.9),
                ("婚恋", 1.0),
                ("两性", 0.9),
                ("教育", 0.9),
                ("家庭", 0.8),
                ("代际", 0.9),
                ("阶层", 1.1),
                ("阶级", 1.1),
                ("贫富差距", 1.1),
                ("公共", 0.7),
                ("舆论", 0.9),
            ),
            negative=("游戏", "影视", "综艺"),
        ),
        Category(
            cid="history",
            name="历史与文明",
            desc="中国史/世界史/人物史/制度史/文明史",
            keywords=(
                ("历史", 1.2),
                ("古代", 0.9),
                ("近代", 0.9),
                ("清朝", 0.9),
                ("民国", 0.9),
                ("战争", 0.8),
                ("帝国", 0.8),
                ("王朝", 0.8),
                ("人物", 0.6),
            ),
            negative=("游戏", "影视"),
        ),
        Category(
            cid="philosophy",
            name="哲学与思想",
            desc="哲学/思想史/宗教与信仰/经典解读等",
            keywords=(
                ("哲学", 1.4),
                ("思想", 1.0),
                ("论语", 1.1),
                ("庄子", 1.0),
                ("佛", 0.8),
                ("道", 0.8),
                ("宗教", 0.8),
                ("信仰", 0.8),
                ("意义", 0.7),
            ),
            negative=("游戏", "影视"),
        ),
        Category(
            cid="management_work",
            name="管理学与职场",
            desc="管理/领导力/项目管理/沟通/汇报/组织与绩效等",
            keywords=(
                ("管理", 1.0),
                ("管理学", 1.2),
                ("领导力", 1.2),
                ("组织", 0.9),
                ("绩效", 1.0),
                ("OKR", 1.0),
                ("KPI", 1.0),
                ("项目管理", 1.2),
                ("复盘", 0.8),
                ("沟通", 0.7),
                ("汇报", 0.7),
                ("会议", 0.6),
                ("职场", 0.9),
            ),
            negative=("游戏", "影视"),
        ),
        Category(
            cid="creator_ip",
            name="自媒体与个人IP/创业",
            desc="自媒体/内容创作/运营/变现/一人公司/个人IP/创业等",
            keywords=(
                ("自媒体", 1.3),
                ("新媒体", 1.1),
                ("个人IP", 1.4),
                ("IP", 0.4),
                ("一人公司", 1.2),
                ("运营", 0.9),
                ("起号", 1.0),
                ("选题", 0.9),
                ("文案", 0.8),
                ("剪辑", 0.7),
                ("变现", 1.0),
                ("带货", 0.9),
                ("创业", 0.8),
            ),
            negative=("游戏", "影视", "吃瓜", "八卦"),
        ),
        Category(
            cid="ai_cs",
            name="AI与计算机",
            desc="AI/大模型/RAG/编程/工程化/工具链等",
            keywords=(
                ("AI", 1.0),
                ("大模型", 1.2),
                ("LLM", 1.1),
                ("RAG", 1.2),
                ("编程", 0.9),
                ("代码", 0.8),
                ("开发", 0.8),
                ("开源", 0.8),
                ("本地化", 0.8),
                ("量化", 0.6),
                ("交易", 0.4),
            ),
            negative=("游戏", "影视"),
        ),
        Category(
            cid="systems_science",
            name="系统论/科学方法",
            desc="系统论/控制论/复杂系统/科学方法/认知框架等",
            keywords=(
                ("系统论", 1.4),
                ("控制论", 1.4),
                ("系统思维", 1.2),
                ("系统动力学", 1.2),
                ("复杂系统", 1.1),
                ("反馈", 0.8),
                ("模型", 0.6),
                ("方法论", 0.7),
            ),
            negative=("游戏", "影视", "考研", "真题"),
        ),
        Category(
            cid="health_med",
            name="医学与健康",
            desc="医学科普/疾病/治疗/家庭医生/心理咨询（偏临床）等",
            keywords=(
                ("医院", 1.0),
                ("医生", 1.0),
                ("诊所", 1.1),
                ("疾病", 1.1),
                ("药", 0.9),
                ("治疗", 1.0),
                ("健康", 0.9),
                ("医学", 1.1),
            ),
            negative=("游戏", "影视"),
        ),
        Category(
            cid="books_knowledge",
            name="读书解读与知识科普",
            desc="读书/解读/拆书/深读/知识讲解（非单一领域）",
            keywords=(
                ("读书", 1.2),
                ("解读", 1.0),
                ("深读", 1.2),
                ("拆书", 1.1),
                ("一口气", 0.4),
                ("万字", 0.6),
                ("书", 0.5),
            ),
            negative=("游戏", "影视"),
        ),
        Category(
            cid="entertainment",
            name="影视娱乐",
            desc="影视/综艺/娱乐八卦/剪辑混剪等",
            keywords=(
                ("电影", 1.2),
                ("电视剧", 1.2),
                ("综艺", 1.1),
                ("娱乐圈", 1.2),
                ("影评", 1.1),
                ("剪辑", 0.6),
            ),
            negative=(),
        ),
        Category(
            cid="games",
            name="游戏",
            desc="游戏内容、攻略、赛事、直播等",
            keywords=(("游戏", 1.2), ("原神", 1.2), ("王者", 1.2), ("吃鸡", 1.2), ("LOL", 1.0)),
            negative=(),
        ),
        Category(
            cid="lifestyle",
            name="生活方式",
            desc="Vlog/旅行/探店/美食/开箱/测评/日常等",
            keywords=(
                ("vlog", 1.0),
                ("VLOG", 1.0),
                ("旅行", 1.1),
                ("探店", 1.0),
                ("美食", 1.0),
                ("开箱", 1.0),
                ("测评", 1.0),
                ("日常", 0.8),
            ),
            negative=(),
        ),
        Category(
            cid="news",
            name="新闻快讯/时评",
            desc="突发/快讯/热点跟进/日更快评等",
            keywords=(("突发", 1.2), ("快讯", 1.2), ("刚刚", 1.0), ("今日", 1.0), ("最新", 1.0)),
            negative=(),
        ),
        Category(
            cid="other",
            name="未分类/杂项",
            desc="信号不足或跨域过多，暂归类到此（可后续手动修正）",
            keywords=(),
            negative=(),
        ),
    ]


def _score_title(title: str, cat: Category) -> float:
    t = title
    for bad in cat.negative:
        if bad and bad in t:
            return 0.0
    score = 0.0
    for kw, w in cat.keywords:
        if kw and kw in t:
            score += float(w)
    return score


def _assign_categories(titles: Sequence[str], categories: Sequence[Category]) -> Tuple[str, List[str], Dict[str, float]]:
    # Compute summed score per category (excluding "other" during ranking).
    scores: Dict[str, float] = {c.cid: 0.0 for c in categories}
    by_id = {c.cid: c for c in categories}
    for t in titles:
        for c in categories:
            if c.cid == "other":
                continue
            scores[c.cid] += _score_title(t, c)

    # Rank non-zero.
    ranked = [(cid, s) for cid, s in scores.items() if cid != "other" and s > 0]
    ranked.sort(key=lambda x: x[1], reverse=True)
    if not ranked:
        return ("other", [], scores)

    primary, s1 = ranked[0]
    secondary: List[str] = []
    for cid, s in ranked[1:]:
        # Keep a 2nd/3rd label only if it's meaningful.
        if s <= 0:
            continue
        if s >= max(2.0, 0.55 * s1):
            secondary.append(cid)
        if len(secondary) >= 2:
            break
    return (primary, secondary, scores)


def iter_video_dirs(library_root: Path) -> List[VideoDir]:
    if not library_root.exists() or not library_root.is_dir():
        raise FileNotFoundError(str(library_root))

    out: List[VideoDir] = []
    for uploader_dir in library_root.iterdir():
        if not uploader_dir.is_dir():
            continue
        if uploader_dir.name.startswith("_"):
            continue
        uploader = uploader_dir.name
        for video_dir in uploader_dir.iterdir():
            if not video_dir.is_dir():
                continue
            bvid = _extract_bvid_from_dirname(video_dir.name)
            if not bvid:
                continue
            title = _normalize_title_from_dir(video_dir.name)
            out.append(
                VideoDir(
                    uploader=uploader,
                    exported_dir=video_dir.resolve(),
                    dirname=video_dir.name,
                    bvid=bvid,
                    title=title,
                )
            )
    return out


def build_catalog(
    *,
    library_root: Path,
    categories: Sequence[Category],
    min_videos_for_uploader_export: int,
) -> Dict[str, Any]:
    videos = iter_video_dirs(library_root)

    by_uploader: Dict[str, List[VideoDir]] = {}
    for v in videos:
        by_uploader.setdefault(v.uploader, []).append(v)

    # stable by dirname (date prefix)
    for u, lst in by_uploader.items():
        lst.sort(key=lambda x: x.dirname)

    uploaders_out: List[Dict[str, Any]] = []
    for uploader, lst in sorted(by_uploader.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        titles = [v.title for v in lst]
        primary, secondary, scores = _assign_categories(titles, categories)
        examples = [v.title for v in lst[-3:]]  # newest 3 titles
        uploaders_out.append(
            {
                "uploader": uploader,
                "video_count": len(lst),
                "primary_category": primary,
                "secondary_categories": secondary,
                "category_scores": scores,
                "example_titles": examples,
                "export_recommended": bool(len(lst) >= int(min_videos_for_uploader_export)),
            }
        )

    # Summary stats
    singleton = sum(1 for u in uploaders_out if int(u["video_count"]) <= 1)
    multi = len(uploaders_out) - singleton
    return {
        "generated_at_utc": _utc_now_iso(),
        "library_root": str(library_root.resolve()),
        "totals": {
            "uploaders": len(uploaders_out),
            "videos": len(videos),
            "singleton_uploaders": singleton,
            "multi_video_uploaders": multi,
        },
        "categories": [{"cid": c.cid, "name": c.name, "desc": c.desc} for c in categories],
        "uploaders": uploaders_out,
    }


def render_markdown(catalog: Dict[str, Any], *, categories: Sequence[Category]) -> str:
    by_cid = {c.cid: c for c in categories}
    # Group uploaders by primary category
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for u in catalog.get("uploaders") or []:
        cid = str(u.get("primary_category") or "other")
        groups.setdefault(cid, []).append(u)
    for cid in groups:
        groups[cid].sort(key=lambda x: (-int(x.get("video_count") or 0), str(x.get("uploader") or "")))

    lines: List[str] = []
    totals = catalog.get("totals") or {}
    lines.append("# Uploader Catalog (by content)\n")
    lines.append(f"- generated_at_utc: `{catalog.get('generated_at_utc')}`")
    lines.append(f"- uploaders: `{totals.get('uploaders')}`  videos: `{totals.get('videos')}`")
    lines.append(f"- singleton_uploaders: `{totals.get('singleton_uploaders')}`  multi_video_uploaders: `{totals.get('multi_video_uploaders')}`")
    lines.append("")
    lines.append("说明：这是基于 `library/<UP主>/<日期_标题_BV...>/` 的“标题关键词”自动归类（可手动二次修正）。\n")

    # Ordered category list (keep stable, exclude other last)
    ordered = [c for c in categories if c.cid != "other"] + [by_cid["other"]]

    for c in ordered:
        items = groups.get(c.cid) or []
        lines.append(f"## {c.name} ({len(items)})")
        if c.desc:
            lines.append(f"- {c.desc}")
        lines.append("")
        if not items:
            lines.append("_（无）_\n")
            continue
        for u in items:
            name = u["uploader"]
            n = int(u["video_count"])
            examples = u.get("example_titles") or []
            ex = " | ".join(examples[:3])
            sec = u.get("secondary_categories") or []
            sec_txt = f"  secondary={','.join(sec)}" if sec else ""
            export_hint = "  export=建议" if u.get("export_recommended") else ""
            lines.append(f"- {name}  videos={n}{sec_txt}{export_hint}  e.g. {ex}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Build a content-based uploader catalog from library/ folder names.")
    parser.add_argument("--library-root", default="library", help="Library root (default: library)")
    parser.add_argument("--out-json", default="library/_catalog/uploader_catalog.json")
    parser.add_argument("--out-md", default="library/_catalog/uploader_catalog.md")
    parser.add_argument("--min-videos-for-export", type=int, default=10, help="Mark uploader export recommended if >= N videos")
    args = parser.parse_args(argv)

    library_root = Path(args.library_root).resolve()
    categories = _default_categories()

    cat = build_catalog(
        library_root=library_root,
        categories=categories,
        min_videos_for_uploader_export=int(args.min_videos_for_export),
    )
    _write_json(Path(args.out_json), cat)
    _write_text(Path(args.out_md), render_markdown(cat, categories=categories))
    _print_utf8(f"[OK] uploaders={cat['totals']['uploaders']} videos={cat['totals']['videos']} out_json={Path(args.out_json).resolve()} out_md={Path(args.out_md).resolve()}")


if __name__ == "__main__":
    main()

