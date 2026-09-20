# bili2rag 全命令参考

所有命令在本仓库（bili2rag）根目录下执行。通用约定：

- GPU 转写：`--asr-device cuda --asr-compute float16`（默认 cpu/int8）
- `--prune-output`：导出成功后删 `output/<bvid>/`，推荐始终带上（library 是唯一真源）
- `--download audio,subtitles,cover`：默认值；含 `video` 会显著变慢变大
- `--if-exists skip|overwrite|fail`：导出目录已存在时策略（默认 skip）

## run — 单视频全链

```
python -m bilibili_get run --url BVxxx [--url BVyyy ...] [--targets 文件]
  [--cookies cookie.txt] [--proxy http://127.0.0.1:7890]
  [--download audio,subtitles,cover] [--comment-pages 1]
  [--pbp] [--snapshots smart|auto|10,60,120] [--snapshot-k 5]
  [--no-asr] [--no-export] [--keep-audio]
  [--asr-device cuda] [--asr-compute float16] [--asr-model auto] [--asr-lang zh]
  [--resume] [--fail-fast] [--prune-output]
```

- `--url` 可重复；`--targets` 传列表文件
- `--resume`：按 `output/<bvid>/logs/pipeline_state.json` 跳过已成功阶段
- `--snapshots smart`：标题含"导图/思维导图"才截图；`auto` 按 PBP 峰值选点（缺峰值均匀取点）
- 单视频链是**保护项主链**（harvest→ASR→export），批量引擎也复用它

## grab-search — 关键词调研（一条命令：搜→筛→抓→转写）

```
python -m bilibili_get grab-search --keyword "..."
  [--search-limit 30] [--min-play 0] [--min-seconds 0] [--max-seconds 0]
  [--require-all 词,词] [--require-any 词,词]
  + 批量旗标（见下）
```

- `--require-all`：标题必须包含全部词；`--require-any`：至少包含其一（逗号分隔）
- 搜索接口**需要有效 SESSDATA**；失效时自动中止
- 产物：`discoveries/<ts>_search_<关键词>/targets.txt + report.json`
- 多关键词调研：跑多次 grab-search，或先多次 `python -m bilibili_search search --keyword X`（旧入口，产出 results.jsonl）再用 `scripts/topic_make_targets_from_discoveries.py` 合并精选

## grab-hot — 热门/排行榜发现头（OpenCLI 桥接，可选依赖）

```
python -m bilibili_get grab-hot [--source hot|ranking] [--discover-limit 50]
  [--require-all 词,词] [--require-any 词,词]
  + 批量旗标（见下）
```

- 依赖本机 OpenCLI（`npm i -g @jackwener/opencli` + 浏览器扩展在线）；未安装时退出码 2 并给安装提示，不影响其它命令
- 产物：`discoveries/<ts>_hot_<source>/targets.txt`，随后直接进引擎

## grab-uploader — 按 UP 增量

```
python -m bilibili_get grab-uploader --seed-bvid BVxxx | --mid 12345
  [--order pubdate] [--keyword ""] [--ps 30] [--page-sleep 0.5]
  [--discover-limit 50] [--since-days 0] [--new-limit 0]
  + 批量旗标
```

- `--seed-bvid` / `--mid` 二选一定位 UP
- `--discover-limit`：发现最近 N 条；`--new-limit`：本次最多处理多少条新增（0 不限）
- `--since-days 7`：只处理最近 N 天投稿，周期性增量刷新用
- space 发现**需要有效 SESSDATA**

## grab-targets — 按清单批量

```
python -m bilibili_get grab-targets --targets-file .\targets.txt [--name 运行名] + 批量旗标
```

- targets 文件任意格式，自动抽 BV 号；支持 `【标题】https://www.bilibili.com/video/BVxxx` 与 `BVxxx<TAB>标题` 两种行
- `--name` 用于 `discoveries/<ts>_collect_<name>/` 目录命名

## 批量旗标（grab-search / grab-uploader / grab-targets 共用）

```
--cookies cookie.txt   --proxy ...        --output-root output   --library-root library
--download audio,subtitles,cover          --comment-pages 1
--pbp                 --snapshots smart   --snapshot-k 5
--no-asr              --no-export
--asr-device cuda     --asr-compute float16  --asr-model auto  --asr-lang zh
--if-exists skip      --prune-output      --between-sleep 0.0   --fail-fast
--new-limit 0         --include-unavailable
--keep-audio          --fetch-workers 2   --no-defer-asr        --out-dir discoveries
```

引擎三段式（全部默认开启）：预过滤（done 谓词 + 批级索引）→ 并行 fetch（2 Worker、`--no-asr`、瞬时失败自动重试一轮）→ 延迟 ASR 扫描（单模型；CC 字幕零 GPU 直出）。

失败语义：
- 子进程崩溃 → salvage 导出已采集部分 → 无转写记 partial
- 明确不可得（-404/-403/-87008）→ 自动标 unavailable（终态）
- 其它 → 记 failures.jsonl + 报告 failed 账

## repair — 只修不抓

```
python -m bilibili_get repair --library-root library
  [--asr-device cuda --asr-compute float16] [--limit 0] [--dry-run]
  [--mark-unavailable BVxxx] [--out-dir discoveries]
```

- 有音频缺转写 → 就地转写 + 重写 manifest（sha256 保持可信）
- 缺音频/缺目录 → 生成 `targets_repair.txt` 并打印现成 grab-targets 命令
- `--mark-unavailable`：手动终态出口
- **重抓永远走 grab-targets，repair 自己不抓**

## search / uploader — 旧发现入口（只发现不抓）

```
python -m bilibili_get search --keyword X [--order click] [--limit 50] [...]
python -m bilibili_get uploader --seed-bvid BVxxx [--limit 50] [--order pubdate] [...]
```

加 `--pipeline` 可连带采集，但新代码一律优先 grab-search / grab-uploader。

## export — output 残留补导出

```
python -m bilibili_get export --all --output-root output --library-root library [--if-exists skip]
```

## scripts/ 资产工具

| 脚本 | 用途 |
|---|---|
| `scripts/doctor.py --library-root library [--write-targets]` | 三账体检：库内缺口 ∣ unavailable ∣ output 残留 |
| `scripts/backfill_comments.py --library-root library [--dry-run] [--deep N] [--force] [--bvid BVx]` | OpenCLI 官方接口评论回填（楼中楼 `--deep`）；EMPTY_RESULT=真零评论；非空 comments.txt 永不覆盖 |
| `scripts/export_txt_bundle.py --uploader X \| --topic X [--with-header] [--concat-all] [--parts-cap 32000]` | 转写导出全集 txt；`--parts-cap` 按视频边界切 32k 分卷 |
| `scripts/topic_collect.py --topic-name X --targets-file t.txt` | 主题收集（引擎抓取 + `library/_topics/X/*/pointer.json` 指针索引） |
| `scripts/topic_make_targets_from_discoveries.py --input discoveries/<run> [--input ...] --out-targets t.txt --profile <档位>` | 多次搜索结果合并去重精选（profiles: philosophy_netizen/philosophy_mixed/management/self_media/self_psych/systems/country_pe） |
| `scripts/build_uploader_catalog.py` + `scripts/make_library_nav.py` | 分类导航（`_by_category/` junction + `INDEX.md`） |
| `scripts/simplify_transcripts_inplace.py --library-root library` | 历史转写批量转简体 |
| `scripts/prune_output_exported.py --execute` | 清理已导出的 output 残留 |

## 输出位置

- 语料：`library/<UP>/<date>_<标题>_<BV>/`（audio、asr/、subtitles/、json/、comments.txt、manifest.json、bilibili.url、source.json、movie.nfo）
- 批报告与日志：`discoveries/<run_id>/`（report.json + logs/<BV>.log）
- txt 导出：`library/_exports/txt/{uploader,topic}/<名>/`；跨源合集惯例放 `library/_exports/txt/专题合集/`
- 账本：`library/_ledger/{failures,unavailable}.jsonl`
