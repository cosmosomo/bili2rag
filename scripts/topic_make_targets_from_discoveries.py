from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def _print_utf8(line: str) -> None:
    sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            s = raw.strip()
            if not s:
                continue
            items.append(json.loads(s))
    return items


@dataclass(frozen=True)
class Candidate:
    bvid: str
    title: str
    author: str
    mid: int
    play: int
    favorites: int
    review: int
    danmaku: int
    pubdate: int
    duration_seconds: int
    source: str


def _safe_int(x: Any) -> int:
    try:
        return int(x)
    except Exception:
        return 0


def _to_candidate(it: Dict[str, Any], source: str) -> Optional[Candidate]:
    bvid = str(it.get("bvid", "")).strip()
    if not bvid:
        return None
    title = str(it.get("title", "")).strip()
    author = str(it.get("author", "")).strip()
    return Candidate(
        bvid=bvid,
        title=title,
        author=author,
        mid=_safe_int(it.get("mid", 0)),
        play=_safe_int(it.get("play", 0)),
        favorites=_safe_int(it.get("favorites", 0)),
        review=_safe_int(it.get("review", 0)),
        danmaku=_safe_int(it.get("danmaku", 0)),
        pubdate=_safe_int(it.get("pubdate", 0)),
        duration_seconds=_safe_int(it.get("duration_seconds", it.get("length_seconds", 0))),
        source=source,
    )


def _normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _should_exclude(
    *,
    title: str,
    author: str,
    exclude_words: Sequence[str],
    exclude_re: Optional[re.Pattern[str]],
) -> Optional[str]:
    t = _normalize_text(title)
    a = _normalize_text(author)
    for w in exclude_words:
        ww = _normalize_text(w)
        if not ww:
            continue
        if ww in t or ww in a:
            return f"exclude_word:{w}"
    if exclude_re is not None and exclude_re.search(title):
        return "exclude_re"
    return None


def _score_philosophy_netizen_views(c: Candidate) -> Tuple[float, List[str]]:
    # Composite score: engagement + duration + "观点/人生意义/思辨/入门" boosts.
    boosts: List[str] = []
    t = c.title

    # Engagement (log scale to damp huge plays).
    score = 0.0
    score += 0.55 * math.log1p(max(0, c.play))
    score += 1.6 * math.log1p(max(0, c.favorites))
    score += 1.3 * math.log1p(max(0, c.review))
    score += 0.9 * math.log1p(max(0, c.danmaku))

    # Duration: prefer 3-60 minutes for "观点类" density.
    dur = max(0, c.duration_seconds)
    if dur >= 180:
        score += min(dur / 1800.0, 1.5)  # up to +1.5
        boosts.append("dur>=180")
    else:
        score -= 2.0
        boosts.append("dur<180_penalty")

    # Title intent boosts (netizen view style).
    kw_boosts = [
        ("普通人", 0.8),
        ("人生意义", 1.2),
        ("意义", 0.5),
        ("思辨", 0.8),
        ("思考", 0.6),
        ("哲学入门", 1.0),
        ("入门", 0.4),
        ("读书", 0.5),
        ("读后感", 0.7),
        ("笔记", 0.4),
        ("观点", 0.8),
        ("我的", 0.3),
        ("为什么", 0.3),
        ("如何", 0.3),
        ("你应该", 0.3),
        ("给你", 0.3),
        ("焦虑", 0.4),
        ("迷茫", 0.4),
        ("自我", 0.3),
        ("自由", 0.2),
        ("价值观", 0.5),
        ("世界观", 0.2),
        ("虚无主义", 1.0),
        ("存在主义", 1.0),
        ("斯多葛", 0.8),
        ("尼采", 0.7),
        ("康德", 0.5),
    ]
    for kw, w in kw_boosts:
        if kw in t:
            score += w
            boosts.append(f"kw:{kw}")

    # Penalties for obvious noise (entertainment/games/meme/clip).
    noise_re = re.compile(
        r"(说唱|翻唱|舞蹈|鬼畜|整活|搞笑|混剪|剪辑|影视|电影|电视剧|动漫|番剧|游戏|原神|王者|吃鸡|我的世界|Minecraft|LOL|英雄联盟|MMD|MAD|PV|MV|ASMR|开箱|测评|探店|美食|旅行|露营|健身)"
    )
    if noise_re.search(t):
        score -= 3.0
        boosts.append("noise_title_penalty")

    # Very short videos often low-signal for this topic.
    if 0 < dur < 120:
        score -= 1.5
        boosts.append("short<120_penalty")

    return score, boosts


def _score_management_views(c: Candidate) -> Tuple[float, List[str]]:
    # Composite score for management-topic videos: engagement + duration + management keyword boosts.
    boosts: List[str] = []
    t = c.title

    score = 0.0
    score += 0.55 * math.log1p(max(0, c.play))
    score += 1.4 * math.log1p(max(0, c.favorites))
    score += 1.2 * math.log1p(max(0, c.review))
    score += 0.85 * math.log1p(max(0, c.danmaku))

    dur = max(0, c.duration_seconds)
    # Prefer 5-90 minutes for "方法论/案例" density; too short tends to be tips/shorts.
    if dur >= 300:
        score += min(dur / 1800.0, 2.0)  # up to +2.0
        boosts.append("dur>=300")
    else:
        score -= 1.5
        boosts.append("dur<300_penalty")

    kw_boosts = [
        ("管理学", 1.6),
        ("管理", 0.6),
        ("组织管理", 1.4),
        ("组织", 0.7),
        ("组织行为学", 1.4),
        ("领导力", 1.4),
        ("领导", 0.6),
        ("团队管理", 1.3),
        ("团队", 0.7),
        ("绩效", 1.2),
        ("KPI", 1.2),
        ("OKR", 1.2),
        ("目标管理", 1.0),
        ("复盘", 0.8),
        ("SOP", 0.8),
        ("流程", 0.7),
        ("战略", 0.7),
        ("企业管理", 1.2),
        ("公司管理", 1.2),
        ("运营管理", 1.0),
        ("项目管理", 1.2),
        ("敏捷", 0.8),
        ("Scrum", 0.8),
        ("PMBOK", 0.8),
        ("沟通", 0.4),
        ("决策", 0.4),
        ("激励", 0.6),
        ("授权", 0.5),
        ("招聘", 0.4),
        ("面试", 0.3),
        ("汇报", 0.4),
        ("会议", 0.3),
        ("时间管理", 0.6),
    ]
    for kw, w in kw_boosts:
        if kw in t:
            score += w
            boosts.append(f"kw:{kw}")

    noise_re = re.compile(
        r"(说唱|翻唱|舞蹈|鬼畜|整活|搞笑|混剪|剪辑|影视|电影|电视剧|动漫|番剧|游戏|原神|王者|吃鸡|我的世界|Minecraft|LOL|英雄联盟|MMD|MAD|PV|MV|ASMR|开箱|测评|探店|美食|旅行|露营|健身)"
    )
    if noise_re.search(t):
        score -= 3.0
        boosts.append("noise_title_penalty")

    if 0 < dur < 180:
        score -= 1.0
        boosts.append("short<180_penalty")

    return score, boosts


def _score_self_media_teaching(c: Candidate) -> Tuple[float, List[str]]:
    # Composite score for self-media teaching videos: engagement + duration + teaching/ops keyword boosts.
    boosts: List[str] = []
    t = c.title

    score = 0.0
    score += 0.55 * math.log1p(max(0, c.play))
    score += 1.5 * math.log1p(max(0, c.favorites))
    score += 1.25 * math.log1p(max(0, c.review))
    score += 0.85 * math.log1p(max(0, c.danmaku))

    dur = max(0, c.duration_seconds)
    # Prefer 5-60 minutes for "教学/方法论" density.
    if dur >= 300:
        score += min(dur / 1800.0, 2.0)  # up to +2.0
        boosts.append("dur>=300")
    else:
        score -= 1.5
        boosts.append("dur<300_penalty")

    kw_boosts = [
        ("自媒体", 1.6),
        ("新媒体", 1.2),
        ("运营", 0.9),
        ("起号", 1.3),
        ("账号", 0.7),
        ("选题", 0.9),
        ("流量", 0.8),
        ("涨粉", 1.0),
        ("粉丝", 0.4),
        ("爆款", 0.8),
        ("标题", 0.6),
        ("文案", 0.9),
        ("脚本", 0.7),
        ("剪辑", 0.7),
        ("口播", 0.9),
        ("镜头", 0.3),
        ("表达", 0.3),
        ("定位", 0.6),
        ("内容", 0.4),
        ("内容创作", 1.0),
        ("变现", 1.2),
        ("商业化", 0.9),
        ("带货", 0.8),
        ("直播", 0.6),
        ("矩阵", 0.7),
        ("私域", 0.7),
        ("个人IP", 1.0),
        ("IP", 0.3),
        ("一人公司", 0.8),
        ("知识博主", 0.7),
        ("教程", 0.7),
        ("手把手", 0.8),
        ("实操", 0.6),
        ("案例", 0.5),
        ("拆解", 0.6),
        ("复盘", 0.6),
        # platforms
        ("B站", 0.5),
        ("哔哩", 0.5),
        ("小红书", 0.8),
        ("抖音", 0.8),
        ("快手", 0.6),
        ("视频号", 0.8),
        ("公众号", 0.7),
    ]
    for kw, w in kw_boosts:
        if kw in t:
            score += w
            boosts.append(f"kw:{kw}")

    noise_re = re.compile(
        r"(说唱|翻唱|舞蹈|鬼畜|整活|搞笑|混剪|影视|电影|电视剧|动漫|番剧|游戏|原神|王者|吃鸡|我的世界|Minecraft|LOL|英雄联盟|MMD|MAD|PV|MV|ASMR|开箱|测评|探店|美食|旅行|露营|健身)"
    )
    if noise_re.search(t):
        score -= 3.0
        boosts.append("noise_title_penalty")

    if 0 < dur < 180:
        score -= 1.0
        boosts.append("short<180_penalty")

    return score, boosts


def _score_self_psych_training(c: Candidate) -> Tuple[float, List[str]]:
    # Composite score for "self psychological training" videos: engagement + duration + mindset keyword boosts.
    boosts: List[str] = []
    t = c.title

    score = 0.0
    score += 0.55 * math.log1p(max(0, c.play))
    score += 1.55 * math.log1p(max(0, c.favorites))
    score += 1.15 * math.log1p(max(0, c.review))
    score += 0.75 * math.log1p(max(0, c.danmaku))

    dur = max(0, c.duration_seconds)
    # Prefer 4-60 minutes: enough density for "方法/心法", avoid shorts.
    if dur >= 240:
        score += min(dur / 1800.0, 2.0)  # up to +2.0
        boosts.append("dur>=240")
    else:
        score -= 2.0
        boosts.append("dur<240_penalty")

    kw_boosts = [
        ("心态", 0.8),
        ("心理", 0.8),
        ("心法", 0.9),
        ("自我效能", 1.4),
        ("效能感", 1.2),
        ("掌控感", 1.0),
        ("安全感", 0.8),
        ("自信", 0.8),
        ("自尊", 0.6),
        ("自洽", 0.7),
        ("内耗", 1.1),
        ("焦虑", 0.9),
        ("拖延", 0.9),
        ("行动力", 0.8),
        ("自律", 0.7),
        ("生命力", 1.1),
        ("觉醒", 0.8),
        ("强者", 0.6),
        ("韧性", 0.8),
        ("情绪", 0.6),
        ("情绪管理", 1.1),
        ("自我接纳", 1.0),
        ("自我成长", 0.8),
        ("边界感", 0.8),
        ("关系", 0.4),
        ("依恋", 0.7),
        ("创伤", 0.7),
        ("疗愈", 0.5),
    ]
    for kw, w in kw_boosts:
        if kw in t:
            score += w
            boosts.append(f"kw:{kw}")

    noise_re = re.compile(
        r"(说唱|翻唱|舞蹈|鬼畜|整活|搞笑|混剪|影视|电影|电视剧|动漫|番剧|游戏|原神|王者|吃鸡|我的世界|Minecraft|LOL|英雄联盟|ASMR|助眠|白噪音|冥想音乐|开箱|测评|探店|美食|旅行|露营|健身)"
    )
    if noise_re.search(t):
        score -= 3.0
        boosts.append("noise_title_penalty")

    return score, boosts


def _score_systems_theory(c: Candidate) -> Tuple[float, List[str]]:
    # Composite score for general systems theory / cybernetics / systems thinking.
    boosts: List[str] = []
    t = c.title

    score = 0.0
    score += 0.55 * math.log1p(max(0, c.play))
    score += 1.5 * math.log1p(max(0, c.favorites))
    score += 1.25 * math.log1p(max(0, c.review))
    score += 0.85 * math.log1p(max(0, c.danmaku))

    dur = max(0, c.duration_seconds)
    # Prefer 5-120 minutes for conceptual content; too short tends to be fragments.
    if dur >= 300:
        score += min(dur / 1800.0, 2.0)  # up to +2.0
        boosts.append("dur>=300")
    else:
        score -= 1.5
        boosts.append("dur<300_penalty")

    kw_boosts = [
        ("一般系统论", 2.0),
        ("系统论", 1.5),
        ("控制论", 1.5),
        ("Cybernetics", 1.3),
        ("cybernetics", 1.3),
        ("系统思维", 1.4),
        ("系统动力学", 1.4),
        ("复杂系统", 1.2),
        ("系统科学", 1.0),
        ("系统工程", 1.0),
        ("反馈", 0.6),
        ("反馈控制", 1.0),
        ("维纳", 1.2),
        ("Wiener", 1.0),
        ("贝塔朗菲", 1.2),
        ("Bertalanffy", 1.0),
        ("阿什比", 1.0),
        ("Ashby", 1.0),
        ("黑箱", 0.5),
        ("模型", 0.3),
        ("建模", 0.4),
        ("系统方法", 0.6),
        ("系统方法论", 0.8),
        ("闭环", 0.4),
        ("控制系统", 0.8),
        ("PID", 0.4),
        ("卡尔曼", 0.4),
        ("Kalman", 0.4),
        ("鲁棒", 0.3),
        ("最优控制", 0.4),
    ]
    for kw, w in kw_boosts:
        if kw in t:
            score += w
            boosts.append(f"kw:{kw}")

    noise_re = re.compile(
        r"(说唱|翻唱|舞蹈|鬼畜|整活|搞笑|混剪|影视|电影|电视剧|动漫|番剧|游戏|原神|王者|吃鸡|我的世界|Minecraft|LOL|英雄联盟|MMD|MAD|PV|MV|ASMR|开箱|测评|探店|美食|旅行|露营|健身)"
    )
    if noise_re.search(t):
        score -= 3.0
        boosts.append("noise_title_penalty")

    if 0 < dur < 180:
        score -= 1.0
        boosts.append("short<180_penalty")

    return score, boosts


def _score_country_political_economy(c: Candidate) -> Tuple[float, List[str]]:
    # Composite score for "country political/economic structure" explainers.
    boosts: List[str] = []
    t = c.title

    score = 0.0
    score += 0.55 * math.log1p(max(0, c.play))
    score += 1.5 * math.log1p(max(0, c.favorites))
    score += 1.25 * math.log1p(max(0, c.review))
    score += 0.85 * math.log1p(max(0, c.danmaku))

    dur = max(0, c.duration_seconds)
    # Prefer 8-120 minutes for institutional explanations.
    if dur >= 480:
        score += min(dur / 1800.0, 2.0)
        boosts.append("dur>=480")
    else:
        score -= 1.5
        boosts.append("dur<480_penalty")

    kw_boosts = [
        ("政体", 1.8),
        ("政治体制", 2.0),
        ("政治制度", 1.8),
        ("制度", 0.6),
        ("三权分立", 1.4),
        ("权力制衡", 1.2),
        ("代议制", 1.2),
        ("直接民主", 1.2),
        ("议会民主", 1.0),
        ("议会制", 1.6),
        ("总统制", 1.6),
        ("半总统制", 1.7),
        ("君主立宪", 1.7),
        ("联邦制", 1.4),
        ("单一制", 1.1),
        ("两院制", 1.0),
        ("选举制度", 1.4),
        ("政党", 0.9),
        ("政党制度", 1.2),
        ("宪法", 0.8),
        ("议会", 0.6),
        ("总统", 0.4),
        ("政府", 0.3),
        ("议员", 0.3),
        # economic structure / model
        ("经济体制", 1.8),
        ("经济制度", 1.4),
        ("社会市场经济", 1.6),
        ("混合经济", 1.3),
        ("计划经济", 1.3),
        ("市场经济", 0.9),
        ("福利国家", 1.4),
        ("社会保障", 1.0),
        ("税制", 1.0),
        ("财政", 0.7),
        ("央行", 0.7),
        ("货币政策", 0.7),
        ("产业政策", 0.9),
        ("发展型国家", 1.2),
        ("北欧模式", 1.2),
        ("东亚模式", 1.0),
        ("德国模式", 1.0),
    ]
    for kw, w in kw_boosts:
        if kw in t:
            score += w
            boosts.append(f"kw:{kw}")

    teaching_boosts = [
        ("一口气", 0.4),
        ("讲清楚", 0.5),
        ("讲透", 0.5),
        ("入门", 0.4),
        ("详解", 0.5),
        ("科普", 0.4),
        ("框架", 0.3),
        ("系统", 0.2),
    ]
    for kw, w in teaching_boosts:
        if kw in t:
            score += w
            boosts.append(f"teach:{kw}")

    noise_re = re.compile(
        r"(说唱|翻唱|舞蹈|鬼畜|整活|搞笑|混剪|影视|电影|电视剧|动漫|番剧|游戏|原神|王者|吃鸡|我的世界|Minecraft|LOL|英雄联盟|MMD|MAD|PV|MV|ASMR|开箱|测评|探店|美食|旅行|露营|健身)"
    )
    if noise_re.search(t):
        score -= 3.0
        boosts.append("noise_title_penalty")

    if 0 < dur < 180:
        score -= 1.0
        boosts.append("short<180_penalty")

    return score, boosts


def _load_candidates(inputs: Sequence[str]) -> List[Candidate]:
    all_items: List[Candidate] = []
    for raw in inputs:
        p = Path(raw)
        if p.is_dir():
            path = p / "results.jsonl"
        else:
            path = p
        if not path.exists():
            raise FileNotFoundError(str(path))
        source = str(path.parent.name)
        for it in _read_jsonl(path):
            c = _to_candidate(it, source=source)
            if c is None:
                continue
            all_items.append(c)
    return all_items


def _dedupe_by_bvid(items: Iterable[Candidate]) -> List[Candidate]:
    # Keep the "best" record if duplicates exist (higher favorites then play).
    best: Dict[str, Candidate] = {}
    for c in items:
        cur = best.get(c.bvid)
        if cur is None:
            best[c.bvid] = c
            continue
        if (c.favorites, c.review, c.play, c.danmaku) > (cur.favorites, cur.review, cur.play, cur.danmaku):
            best[c.bvid] = c
    return list(best.values())


def _write_targets_txt(path: Path, selected: Sequence[Candidate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for c in selected:
            f.write(f"【{c.title}】https://www.bilibili.com/video/{c.bvid}\n")


def _write_report_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a curated targets.txt from multiple bilibili_search discoveries (results.jsonl). "
            "This is intended for topic batch collection."
        )
    )
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        help="Discovery dir (containing results.jsonl) or a direct results.jsonl path. Repeatable.",
    )
    parser.add_argument("--out-targets", required=True, help="Output targets.txt path")
    parser.add_argument("--out-report", default="", help="Optional report.json output path")
    parser.add_argument("--limit", type=int, default=50, help="How many targets to select")
    parser.add_argument("--min-seconds", type=int, default=180, help="Minimum duration in seconds")
    parser.add_argument("--max-seconds", type=int, default=5400, help="Maximum duration in seconds (0=unlimited)")
    parser.add_argument("--max-per-author", type=int, default=2, help="Limit per author to avoid flooding")
    parser.add_argument(
        "--profile",
        choices=["philosophy_netizen", "philosophy_mixed", "management", "self_media", "self_psych", "systems", "country_pe"],
        default="philosophy_netizen",
        help=(
            "Curation profile. philosophy_netizen: prefer '网民观点/人生意义/思辨' and exclude obvious courseware/playlists/memes. "
            "philosophy_mixed: allow more lectures/courses. "
            "management: management/leadership/organization/project/okr/kpi oriented. "
            "self_media: self-media/new-media teaching & operations oriented. "
            "self_psych: self psychological training / mindset / self-efficacy oriented. "
            "systems: general systems theory / cybernetics / systems thinking oriented. "
            "country_pe: country political/economic structure explainers (政体/制度/模式)."
        ),
    )
    parser.add_argument("--exclude", action="append", default=[], help="Exclude word in title/author (repeatable)")
    parser.add_argument(
        "--exclude-re",
        default="",
        help="Optional regex to exclude by title (python regex). Example: '(游戏|鬼畜)'",
    )
    args = parser.parse_args(argv)

    if not args.input:
        _print_utf8("[FAIL] --input is required (repeatable). Provide discovery dirs or results.jsonl paths.")
        sys.exit(2)

    exclude_re = re.compile(args.exclude_re) if args.exclude_re else None

    # Built-in excludes for cmd.exe friendliness (no need to pass complex regex via shell).
    built_in_exclude_words: List[str] = []
    built_in_exclude_title_re: Optional[re.Pattern[str]] = None
    built_in_signal_words: Optional[List[str]] = None
    built_in_teaching_words: Optional[List[str]] = None
    if args.profile == "philosophy_netizen":
        built_in_exclude_words.extend(
            [
                # obvious memes / music playlists / low-signal clips
                "说唱",
                "cheems",
                "薯条",
                "码头",
                "眉飞色",
                "蕉忍",
                "循环歌单",
                "歌单",
                "纯享",
                "meme",
                "猫meme",
                "短片",
                "电影短片",
                "sleepy",
                "睡前消息",
                "婚礼",
                # entertainment / film
                "影视",
                "电影",
                "电视剧",
                "影评",
                "剧情",
                "让子弹飞",
                "瞬息全宇宙",
                # celebrity-based "哲学" tags
                "苏新皓",
                "爱死机",
                "爱、死亡和机器人",
                "脱口秀",
                "NovelAI",
                "AI绘画",
                "绘画",
                "萌化",
                "海军",
            ]
        )
        built_in_exclude_title_re = re.compile(
            r"(全\d+讲|全\d+集|合\d+讲|合\d+集|合集|课堂|课程|公开课|讲座|大学|复旦|人大|哈佛|字幕\|全|字幕版|文稿屏录|ppt版|纯享|循环歌单|meme|猫meme|电影短片|第\d+季|第\d+集)"
        )
        built_in_signal_words = [
            "哲学",
            "思辨",
            "人生意义",
            "意义",
            "虚无",
            "虚无主义",
            "存在",
            "存在主义",
            "斯多葛",
            "尼采",
            "康德",
            "叔本华",
            "萨特",
            "加缪",
            "海德格尔",
            "维特根斯坦",
            "齐泽克",
            "刘擎",
            "王德峰",
            "道家",
            "佛",
            "缘起",
            "心经",
            "金刚经",
            "自由",
            "价值观",
        ]
    elif args.profile == "management":
        built_in_exclude_words.extend(
            [
                "说唱",
                "翻唱",
                "舞蹈",
                "鬼畜",
                "整活",
                "搞笑",
                "电竞",
                "LPL",
                "KPL",
                "Kpop",
                "表情",
                "主播",
                "脱口秀",
                # exam/cram & education noise
                "期末",
                "考试",
                "复习",
                "不挂科",
                "带背",
                "考前",
                "真题",
                "考点",
                "背书",
                "公考",
                "公务员",
                "网校",
                "一建",
                "军队文职",
                "教学",
                "上课",
                "老师",
                "学生",
                "计算题",
                "组织管理题",
                "面试题",
                "面试100题",
                "羽球",
                "骂哭",
                "刘涛",
                "张瀚",
                "林心如",
                "钢铁雄心",
                "HOI4",
                "管泽元",
                "Doinb",
                "米勒",
                "WBG",
                "JDG",
                "冥府",
                "地府",
                "组织题",
                "计划组织题",
                "国测服",
                "顶号",
                "藏品",
                "全英雄",
                "影视",
                "电影",
                "电视剧",
                "动漫",
                "番剧",
                "游戏",
                "原神",
                "王者",
                "吃鸡",
                "我的世界",
                "Minecraft",
                "LOL",
                "英雄联盟",
                "ASMR",
            ]
        )
        built_in_exclude_title_re = re.compile(
            r"(电竞|LPL|KPL|Kpop|表情管理|游玩|体验|攻略|通关|抽卡|鸣潮|原神|仙剑|主播|八排|上厕所|内娱|庆余年|人民的名义|许半夏|范闲|庆帝|旧日神魔|钢铁雄心|HOI4|Doinb|WBG|JDG|\bvs\b|国测服|顶号|PTP|IRIG|10MHz|1pps|铷|管理系统|图书管理系统|SpringBoot|Spring Boot|Vue|uniapp|小程序|^第\d+章|^第\d+节|^第\d+讲|^第\d+课)"
        )
        built_in_signal_words = [
            "管理学",
            "领导力",
            "组织行为学",
            "团队管理",
            "绩效",
            "KPI",
            "OKR",
            "战略",
            "企业",
            "公司",
            "运营管理",
            "项目管理",
            "敏捷",
            "Scrum",
            "PMBOK",
            "决策",
            "激励",
            "授权",
            "时间管理",
            "供应链",
            "人力资源",
            "HR",
            "财务管理",
            "市场营销",
            "工商管理",
            "SOP",
            "流程",
            "复盘",
            "目标管理",
        ]
    elif args.profile == "self_media":
        built_in_exclude_words.extend(
            [
                # entertainment / noise
                "说唱",
                "翻唱",
                "舞蹈",
                "鬼畜",
                "整活",
                "搞笑",
                "影视",
                "电影",
                "电视剧",
                "动漫",
                "番剧",
                "游戏",
                "原神",
                "王者",
                "吃鸡",
                "我的世界",
                "Minecraft",
                "LOL",
                "英雄联盟",
                "ASMR",
                # gossip & drama (often unrelated to teaching)
                "吃瓜",
                "八卦",
                "娱乐圈",
                "塌房",
                "绯闻",
                # exam/cram
                "期末",
                "考试",
                "复习",
                "不挂科",
                "带背",
                "考前",
                "真题",
                "公考",
                "公务员",
                # lifestyle/story/noise for teaching-oriented set
                "vlog",
                "日常",
                "房车",
                "穷游",
                "改造",
                "圆梦",
                "啃老",
                "裸辞",
                "富二代",
                "离职",
                "赚了多少钱",
                "挣了多少钱",
                "赚多少钱",
                "挣多少钱",
                "爆火",
                "锤人",
            ]
        )
        built_in_exclude_title_re = re.compile(
            r"(吃瓜|娱乐圈|塌房|绯闻|明星|综艺|八卦|鬼畜|舞蹈|翻唱|说唱|vlog|旅行|探店|开箱|测评|游戏|原神|王者|吃鸡|房车|穷游|改造|圆梦|啃老|裸辞|富二代|离职|赚了多少钱|挣了多少钱|爆火|锤人|^第\d+集|^第\d+期)"
        )
        built_in_signal_words = [
            "自媒体",
            "新媒体",
            "内容创作",
            "运营",
            "起号",
            "账号",
            "选题",
            "流量",
            "涨粉",
            "爆款",
            "标题",
            "文案",
            "脚本",
            "剪辑",
            "口播",
            "定位",
            "变现",
            "商业化",
            "带货",
            "直播",
            "矩阵",
            "私域",
            "个人IP",
            "一人公司",
            "知识博主",
            # platforms
            "B站",
            "哔哩",
            "小红书",
            "抖音",
            "快手",
            "视频号",
            "公众号",
        ]
        # For "调研和教学类"，再加一层：必须像教程/方法论/实操，而不是纯经历/八卦。
        built_in_teaching_words = [
            "教程",
            "干货",
            "手把手",
            "实操",
            "实战",
            "全流程",
            "指南",
            "方法",
            "技巧",
            "拆解",
            "复盘",
            "底层逻辑",
            "运营",
            "起号",
            "选题",
            "标题",
            "文案",
            "脚本",
            "剪辑",
            "剪映",
            "PR",
            "Premiere",
            "AE",
            "After Effects",
            "OBS",
            "直播间",
            "变现",
            "商业化",
            "带货",
            "矩阵",
            "私域",
            "MCN",
            "侵权",
            "版权",
        ]
    elif args.profile == "self_psych":
        built_in_exclude_words.extend(
            [
                # entertainment / noise
                "说唱",
                "翻唱",
                "舞蹈",
                "鬼畜",
                "整活",
                "搞笑",
                "影视",
                "电影",
                "电视剧",
                "动漫",
                "番剧",
                "游戏",
                "原神",
                "王者",
                "吃鸡",
                "我的世界",
                "Minecraft",
                "LOL",
                "英雄联盟",
                "ASMR",
                "助眠",
                "白噪音",
                "冥想音乐",
                # fortune-telling / mysticism noise
                "星座",
                "塔罗",
                "占卜",
                "玄学",
                "能量",
                "吸引力法则",
                "开运",
                # exam/cram noise
                "考研",
                "高考",
                "真题",
                "刷题",
                "拿分",
                "课件",
                "教案",
                "背诵",
                "讲义",
            ]
        )
        built_in_exclude_title_re = re.compile(
            r"(vlog|旅行|探店|开箱|测评|游戏|原神|王者|吃鸡|ASMR|助眠|白噪音|冥想音乐|星座|塔罗|占卜|玄学|吸引力法则|开运|考研|高考|真题|刷题|拿分|课件|教案|背诵|讲义|^第\\d+集|^第\\d+期)"
        )
        # Require at least one psychological/mindset keyword in title.
        built_in_signal_words = [
            "心态",
            "心理",
            "心法",
            "自我效能",
            "效能感",
            "掌控感",
            "安全感",
            "自信",
            "自尊",
            "自洽",
            "内耗",
            "焦虑",
            "拖延",
            "行动力",
            "自律",
            "生命力",
            "觉醒",
            "强者",
            "韧性",
            "情绪",
            "情绪管理",
            "自我接纳",
            "自我成长",
            "边界感",
            "依恋",
            "创伤",
            "疗愈",
        ]
    elif args.profile == "systems":
        built_in_exclude_words.extend(
            [
                "说唱",
                "翻唱",
                "舞蹈",
                "鬼畜",
                "整活",
                "搞笑",
                # exam/cram noise
                "考研",
                "期末",
                "考试",
                "复习",
                "不挂科",
                "带背",
                "考点",
                "题",
                "申论",
                "行测",
                "范文",
                "模考",
                "复盘",
                "言语",
                "逻辑填空",
                "公务员",
                "面试",
                # games / entertainment
                "钢铁雄心",
                "HOI4",
                "五一大建",
                "冷战",
                # hardware / maker
                "ESP32",
                "单片机",
                "PLC",
                "遥控",
                "RC",
                # heavy tool-specific engineering (often not systems theory)
                "CarSim",
                "carsim",
                "Simulink",
                "simulink",
                "Matlab",
                "MATLAB",
                # extra game/maker/cad/fea noise
                "Stellaris",
                "群星",
                "DLC",
                "C4D",
                "CINEMA",
                "Cinema",
                "ANSYS",
                "有限元",
                "转子",
                "多旋翼",
                "飞行器",
                "无人机",
                "机电",
                "传动",
                "绑定",
                "减震",
                "魔方",
                "影视",
                "电影",
                "电视剧",
                "动漫",
                "番剧",
                "游戏",
                "原神",
                "王者",
                "吃鸡",
                "我的世界",
                "Minecraft",
                "LOL",
                "英雄联盟",
                "ASMR",
            ]
        )
        built_in_exclude_title_re = re.compile(
            r"(vlog|旅行|探店|开箱|测评|游戏|原神|王者|吃鸡|钢铁雄心|HOI4|模考|复盘|申论|行测|范文|考研|期末|不挂科|带背|考点|题|ESP32|单片机|PLC|CarSim|carsim|Simulink|simulink|MATLAB|Matlab|Stellaris|群星|DLC|C4D|CINEMA|Cinema|ANSYS|有限元|转子|多旋翼|飞行器|无人机|机电|传动|绑定|减震|魔方|^第\\d+集|^第\\d+期)"
        )
        built_in_signal_words = [
            "一般系统论",
            "系统论",
            "控制论",
            "系统思维",
            "系统动力学",
            "复杂系统",
            "系统科学",
            "系统工程",
            "反馈控制",
            "控制系统",
            "闭环",
            "维纳",
            "Wiener",
            "贝塔朗菲",
            "Bertalanffy",
            "阿什比",
            "Ashby",
            "Cybernetics",
            "cybernetics",
        ]
    elif args.profile == "country_pe":
        built_in_exclude_words.extend(
            [
                # entertainment / noise
                "说唱",
                "翻唱",
                "舞蹈",
                "鬼畜",
                "整活",
                "搞笑",
                "影视",
                "电影",
                "电视剧",
                "动漫",
                "番剧",
                "游戏",
                "原神",
                "王者",
                "吃鸡",
                "我的世界",
                "Minecraft",
                "LOL",
                "英雄联盟",
                "ASMR",
                # exam/cram noise
                "高中",
                "高考",
                "考研",
                "真题",
                "选必",
                "必修",
                "拿分",
                "考试",
                "选择题",
                "做题",
                "刷题",
                "题型",
                "考点",
                "带背",
                "背诵",
                "讲义",
                "笔记",
                "复习",
                "冲刺",
                "政体区分",
                "一政er",
                "考公",
                "行测",
                "申论",
                "课件",
                "教案",
                "九上",
                "九下",
                "九年级",
                "初三",
                "初中",
                "课程",
                "助眠",
                "提纲",
                "日报",
                "互动视频",
                "口诀",
                "公基",
                "速成",
                "依马峰",
                "维多利亚",
                "Victoria",
                "DLC",
                # too newsy / breaking
                "最新",
                "突发",
                "今天",
                "刚刚",
                "快讯",
            ]
        )
        built_in_exclude_title_re = re.compile(
            r"(vlog|旅行|探店|开箱|测评|游戏|原神|王者|吃鸡|高中|高考|考研|真题|选必|必修|拿分|考试|选择题|做题|刷题|题型|考点|带背|背诵|讲义|笔记|复习|冲刺|政体区分|考公|行测|申论|课件|教案|九上|九下|九年级|初三|初中|课程|助眠|提纲|日报|互动视频|口诀|公基|速成|维多利亚|Victoria|DLC|突发|快讯|最新|刚刚|^第\\d+集|^第\\d+期)"
        )
        # Require at least one structure keyword (avoid pure geopolitics/news).
        built_in_signal_words = [
            "政体",
            "政治体制",
            "政治制度",
            "三权分立",
            "权力制衡",
            "代议制",
            "直接民主",
            "议会民主",
            "议会制",
            "总统制",
            "半总统制",
            "君主立宪",
            "联邦制",
            "单一制",
            "两院制",
            "选举制度",
            "政党制度",
            "宪法",
            "经济体制",
            "社会市场经济",
            "混合经济",
            "福利国家",
            "税制",
            "社会保障",
            "产业政策",
            "发展型国家",
            "北欧模式",
        ]

    raw_items = _load_candidates(args.input)
    deduped = _dedupe_by_bvid(raw_items)

    excluded: List[Dict[str, Any]] = []
    filtered: List[Candidate] = []
    for c in deduped:
        if int(args.max_seconds) > 0 and c.duration_seconds and c.duration_seconds > int(args.max_seconds):
            excluded.append({"bvid": c.bvid, "title": c.title, "author": c.author, "reason": "max_seconds"})
            continue
        if c.duration_seconds and c.duration_seconds < int(args.min_seconds):
            excluded.append({"bvid": c.bvid, "title": c.title, "author": c.author, "reason": "min_seconds"})
            continue
        if built_in_exclude_title_re is not None and built_in_exclude_title_re.search(c.title):
            excluded.append({"bvid": c.bvid, "title": c.title, "author": c.author, "reason": "built_in_title_re"})
            continue
        if built_in_signal_words is not None:
            if not any(w in c.title for w in built_in_signal_words):
                excluded.append({"bvid": c.bvid, "title": c.title, "author": c.author, "reason": "no_signal_title"})
                continue
        if built_in_teaching_words is not None:
            if not any(w in c.title for w in built_in_teaching_words):
                excluded.append({"bvid": c.bvid, "title": c.title, "author": c.author, "reason": "no_teaching_title"})
                continue
        r = _should_exclude(
            title=c.title,
            author=c.author,
            exclude_words=tuple(list(args.exclude) + built_in_exclude_words),
            exclude_re=exclude_re,
        )
        if r:
            excluded.append({"bvid": c.bvid, "title": c.title, "author": c.author, "reason": r})
            continue
        filtered.append(c)

    scored: List[Tuple[float, Candidate, List[str]]] = []
    for c in filtered:
        if args.profile == "management":
            s, boosts = _score_management_views(c)
        elif args.profile == "self_media":
            s, boosts = _score_self_media_teaching(c)
        elif args.profile == "self_psych":
            s, boosts = _score_self_psych_training(c)
        elif args.profile == "systems":
            s, boosts = _score_systems_theory(c)
        elif args.profile == "country_pe":
            s, boosts = _score_country_political_economy(c)
        else:
            s, boosts = _score_philosophy_netizen_views(c)
        scored.append((s, c, boosts))
    scored.sort(key=lambda x: x[0], reverse=True)

    selected: List[Candidate] = []
    selected_meta: List[Dict[str, Any]] = []
    per_author: Dict[str, int] = {}
    for s, c, boosts in scored:
        if len(selected) >= int(args.limit):
            break
        k = c.author or str(c.mid or "")
        if per_author.get(k, 0) >= int(args.max_per_author):
            continue
        per_author[k] = per_author.get(k, 0) + 1
        selected.append(c)
        selected_meta.append(
            {
                "bvid": c.bvid,
                "title": c.title,
                "author": c.author,
                "mid": c.mid,
                "duration_seconds": c.duration_seconds,
                "play": c.play,
                "favorites": c.favorites,
                "review": c.review,
                "danmaku": c.danmaku,
                "pubdate": c.pubdate,
                "score": round(float(s), 4),
                "boosts": boosts,
                "source": c.source,
            }
        )

    out_targets = Path(args.out_targets)
    _write_targets_txt(out_targets, selected)

    report_path = Path(args.out_report) if args.out_report else None
    if report_path is not None:
        _write_report_json(
            report_path,
            {
                "inputs": [str(x) for x in args.input],
                "counts": {
                    "raw": len(raw_items),
                    "deduped": len(deduped),
                    "excluded": len(excluded),
                    "filtered": len(filtered),
                    "selected": len(selected),
                },
                "rules": {
                    "min_seconds": int(args.min_seconds),
                    "max_per_author": int(args.max_per_author),
                    "exclude_words": list(args.exclude),
                    "exclude_re": args.exclude_re,
                },
                "selected": selected_meta,
                "excluded_sample": excluded[:200],
            },
        )

    _print_utf8(f"[OK] targets: {out_targets}  selected={len(selected)}  filtered={len(filtered)}  deduped={len(deduped)}")
    if report_path is not None:
        _print_utf8(f"[OK] report: {report_path}")


if __name__ == "__main__":
    main()
