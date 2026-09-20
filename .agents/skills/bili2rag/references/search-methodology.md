# bili2rag 搜索与调研方法论

> 从多轮实战调研（代表性战役：Agent Harness 范式调研——多关键词矩阵 → 72 集 / 35 万字 / 13 卷分桶合集）沉淀的发现、筛选、收敛方法。

## 1. 发现头决策树：先选对入口

```
要什么？
├─ 某个主题的视频语料（最常见）   → grab-search（搜→筛→抓→转写一条命令）
├─ 某个 UP 的全部/新增            → grab-uploader（--since-days N 做周期增量）
├─ 热门/排行榜上的东西             → grab-hot --source hot|ranking（需 OpenCLI）
├─ 手里已有清单（用户点名/别处导出）→ grab-targets（targets 文件任意格式自动抽 BV）
└─ 主题指针索引（不复制大文件）     → scripts/topic_collect.py
```

混合场景直接串：`opencli bilibili hot` 看趋势 → 锁定 UP → `grab-uploader` 深挖。

## 2. 关键词矩阵：一个主题不是一次搜索

**单个关键词的召回永远不够。** 一个主题拆成五类词，各搜一轮（每轮 grab-search 一次，引擎幂等，重叠的自动跳过）：

| 词类 | 作用 | 示例（Agent Harness 调研实录） |
|---|---|---|
| 主题正名 | 召唤核心内容 | `Agent Harness`、`Harness Engineering` |
| 别名/缩写 | 抓黑话社区 | `DSH`、`DeepSeek Harness`、`OpenCode` |
| 对比词 | 召唤评测/横评（信息密度最高的一类） | `Pi vs Codex`、`三岔路` |
| 中英混合 | 中文社区用英文产品名很常见 | `Pi Agent 大道至简` |
| 人物/机构名 | 顺藤摸瓜找深度作者 | UP 名、实验室名 |

**实战规模参考**：完整专题 ≈ 8-12 组关键词、每组 `--search-limit 30`，去重后大约收敛到 40-80 集。

## 3. 过滤策略：两轮法，宁缺勿滥

**第一轮裸搜观察分布**（不加标题过滤），看结果的标题构成、播放量分布、噪音类型——然后才定过滤词：

```powershell
# 第一轮：裸搜 30 条看分布
python -m bilibili_get grab-search --keyword "deepseek harness" --search-limit 30 --no-asr ...
# （观察后）第二轮：带上过滤再正式抓
python -m bilibili_get grab-search --keyword "deepseek harness" --require-any "harness,DSH,Agent" --min-play 1000 --min-seconds 120 ...
```

- `--require-any`（至少含其一）比 `--require-all`（全部包含）常用得多——标题不会把所有词都说全
- `--min-play 1000` 起步挡纯噪音；`--min-seconds 120` 挡标题党切片（深度内容通常 ≥5 分钟）
- 搜索按相关度排序、高播放通常靠前——**前几条即可初筛**，不必翻页
- 过滤词宁少勿多：过滤过度丢的正文，比混进来的噪音更贵（噪音在合并阶段还能靠分桶隔离）

## 4. 价值预筛：三级取文阶梯，先判断值不值得

抓取+转写是有成本的（带宽/GPU/时间）。对候选视频**先便宜后贵**：

```
① 有字幕？ → 零成本全文（bili2rag CC 旁路 / opencli subtitle）
② 无字幕 → opencli bilibili summary → 零成本官方大纲（不是全文，但足以判断价值）
③ 值得 → 才付 ASR 的钱（批量场景交给引擎的延迟扫描）
```

辅助信号：`opencli bilibili video <BV>` 的统计 + `comments` 的高赞——播放/弹幕/评论比异常通常是优质信号。**先判断价值，再决定要不要付第三级的成本。**

## 5. 收敛：从多轮结果到一个合集

多轮搜索/多个发现头的产物，统一收敛成专题合集：

```powershell
# 跨批合并（去重靠 bvid + done 谓词，重复抓取自动跳过）
python <skill>\scripts\merge_collection.py --repo-root . --topic "主题名" `
  --reports discoveries\<run1>\report.json discoveries\<run2>\report.json `
  --extra "某UP名:spec|harness|agent" --bvid 用户点名的BV `
  --buckets buckets.json --default-bucket "残余桶" --cap 32000
```

**分桶设计三原则**：
- 桶名 = **读者视角的子议题**（"范式与概念"而不是"bucket1"）
- 桶顺序 = 叙事顺序（概念 → 深潜 → 生态 → 对比 → 争议）
- 桶内按日期排（同主题下新旧观点可见演化）；正则**宁窄勿宽**，命中不了的落 `--default-bucket` 兜底

**分卷**：`--cap 32000` 按视频边界切，每卷可整体塞进 32k 上下文直接问答。

## 6. 已知坑（搜索场景）

| 坑 | 对策 |
|---|---|
| 搜索/space 必须有效 SESSDATA，失效报误导性 -352 | `cookie-refresh` 一键供给（见 maintenance.md） |
| 同内容多 UP 转载（bvid 不同）→ 重复入库重复转写 | 目前无内容指纹，靠标题眼力；合并时 `merge_collection` 的去重只认 bvid——已知缺口 |
| 标题党切片混入 | `--min-seconds` + 桶内隔离；严重时第二轮加 `--require-any` |
| 单关键词召回假象（"搜不到"≠"不存在"） | 回到 §2 关键词矩阵，换词类再搜 |
| 搜索结果里 UP 质量参差 | 高价值 UP 直接 `grab-uploader` 收全量（比逐条筛省事） |

## 7. 一页速查：完整专题调研流

```
关键词矩阵（8-12 组）
  → 每组 grab-search（第一轮裸搜定过滤，第二轮正式抓；GPU 旗标 + --prune-output）
  → 后台跑（>30 条用 Start-Process 模式），doctor 收尾，有 partial 就 repair
  → 高价值 UP 补 grab-uploader 全量；用户点名的 BV 补 --bvid
  → merge_collection 分桶合并成 32k 分卷
  → 交付：卷数/集数/字符数/分桶分布 + 专题综述（另起任务）
```
