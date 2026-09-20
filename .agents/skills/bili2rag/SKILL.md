---
name: bili2rag
description: Operate the bili2rag pipeline (this repo) to turn Bilibili videos into a local RAG-ready corpus. Use whenever the user mentions B站/bilibili 视频的抓取、下载、转写、字幕、弹幕、评论，要调研一个专题并收集视频语料，把转写合并成合集/32k 分卷，检查或修复语料库（doctor/repair），或要求修改/优化 bili2rag 系统本身 —— 即使他们没有点名这个工具。
---

# bili2rag — B站视频 → RAG 语料流水线

## 项目位置与环境

- 仓库：本 skill 所在的 bili2rag 仓库，**所有命令在仓库根目录下执行**（本 skill 位于 `<repo>/.agents/skills/bili2rag/`）
- Python ≥3.10（已 `pip install -e .`）、ffmpeg 已就绪、CUDA GPU 可用
- PowerShell 会话先设 UTF-8 输出（中文日志不乱码）：

```powershell
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
```

- GPU 转写旗标：`--asr-device cuda --asr-compute float16`（不传则 cpu/int8；cuda 下智能选型自动用 small 模型）
- 调用形式：`python -m bilibili_get <子命令>`（与 `bili2rag <子命令>` 等价）

## 系统已内置的加速——不要手动重造

v0.2.2 起这些全是默认行为，直接享受，不要绕开、不要写旁路脚本：

| 能力 | 默认行为 | 关闭方式 |
|---|---|---|
| CC 字幕旁路 | 有中文字幕 → 零 GPU 直出转写，抓取时**跳过音频下载** | `--keep-audio` 保留音频 |
| 延迟转写 | 抓取阶段不带 ASR，批尾单模型集中转写（每批只加载一次模型） | `--no-defer-asr` |
| 并行抓取 | 2 个子进程并行下载（对站点温和，勿超 3） | `--fetch-workers 1` |
| 瞬时失败自愈 | SSL 抖动类失败批尾自动重试一轮 | — |
| 模型缓存 | 同批同配置只加载一次 whisper | — |

历史教训（用户点破过的盲区）：曾出现库里 312/358 条视频的中文字幕一直躺在磁盘上，而每条都白跑了一次 whisper GPU。**先看有没有字幕，再想 ASR**；同理，合并、调研这些重复工作流都已做成命令，不要再手搓临时脚本。

## 任务 → 命令速查

| 用户想要 | 命令 |
|---|---|
| 抓单条视频（采集→转写→归档） | `python -m bilibili_get run --url BVxxx --cookies cookie.txt --asr-device cuda --asr-compute float16 --prune-output` |
| 抓某 UP 的新投稿 | `python -m bilibili_get grab-uploader --seed-bvid BVxxx --cookies cookie.txt --asr-device cuda --asr-compute float16 --prune-output`；周期增量加 `--since-days 7` |
| 关键词调研一个专题（一条命令） | `python -m bilibili_get grab-search --keyword "关键词" --require-any 词1,词2 --min-play 1000 --min-seconds 120 ...批量旗标` |
| 热门/排行榜批量（需 OpenCLI） | `python -m bilibili_get grab-hot --source hot|ranking --discover-limit 30 --require-any "词" ...批量旗标` |
| 按清单批量 | `python -m bilibili_get grab-targets --targets-file .\targets.txt --name 运行名 --cookies cookie.txt --asr-device cuda --asr-compute float16 --prune-output` |
| 转写合并成 txt 全集/32k 分卷 | 单 UP 或单主题：`python scripts\export_txt_bundle.py --uploader "UP名" --with-header --parts-cap 32000`；跨 UP 多批分桶合集：用本 skill 的 `scripts/merge_collection.py` |
| 库体检 | `python scripts\doctor.py --library-root library`（加 `--write-targets` 生成待修清单） |
| 修 partial（有音频缺转写） | `python -m bilibili_get repair --library-root library --asr-device cuda --asr-compute float16` |
| 重抓缺音频/失败条目 | repair 会输出 `targets_repair.txt` + 现成 grab-targets 命令；unavailable 重试加 `--include-unavailable` |
| 评论回填（楼中楼，需 OpenCLI） | `python scripts\backfill_comments.py --library-root library --dry-run` 先看计划，`--deep 3` 抓楼中楼 |
| cookie 供给（免手动导出） | `python -m bilibili_get cookie-refresh`：合并浏览器 cookie；SESSDATA 缺失/失效时自动弹二维码（扫码一次即全套） |
| 主题指针索引（不复制大文件） | `python scripts\topic_collect.py --topic-name "主题" --targets-file .\t.txt ...` |
| 分类导航浏览 | `python scripts\build_uploader_catalog.py --library-root library` + `python scripts\make_library_nav.py` |

完整旗标、默认值、参数语义：读 `references/commands.md`。端到端场景（专题调研全流程、增量刷新、修复闭环、故障排查）：读 `references/recipes.md`。

## Cookie

- 位置：仓库根 `cookie.txt`（Netscape 或单行 Header 格式，必须含 `SESSDATA`）
- **首选供给方式**：`python -m bilibili_get cookie-refresh`（需 OpenCLI）——Tier1 零接触合并浏览器 24 项 cookie；SESSDATA（HttpOnly，浏览器读不到）缺失或失效时自动走纯 Python 二维码流程：图片弹窗 → 用户扫码 → poll 成功 → crossDomain ticket 兑换 Set-Cookie → 全套写入 + nav 验证（2026-09-20 实测通过）
- **失效症状：space/搜索接口报误导性的 `-352` "风控"错误**。工具启动会自动预检：`run` 打 `[COOKIE WARN]` 继续，搜索/发现类命令直接中止
- 处理顺序：先跑 cookie-refresh（绝大多数情况一条命令解决）；OpenCLI 不可用时才用浏览器扩展手动导出
- 不需要 cookie 的：公开视频 run、ASR、导出、doctor、repair

## 长批量后台运行模式

预计 >10 分钟或 >30 条的批量用后台模式（前台 90s 超时会打断等待）：

```powershell
$p = Start-Process -FilePath python -ArgumentList @('-u','-m','bilibili_get','grab-targets','--targets-file','.tmp_targets.txt','--name','mybatch','--cookies','cookie.txt','--asr-device','cuda','--asr-compute','float16','--prune-output') -WorkingDirectory (Get-Location).Path -RedirectStandardOutput "discoveries\mybatch_out.log" -RedirectStandardError "discoveries\mybatch_err.log" -NoNewWindow -PassThru
Write-Output "PID=$($p.Id)"          # 记下 PID
Get-Content "discoveries\mybatch_out.log" -Tail 10   # 随时查进度
```

引擎幂等：中断后重跑同命令自动跳过已完成条目（done 谓词），所以"杀掉重来"永远安全。

## 完成语义（判断"做完"的唯一标准）

- **done** = `library/<UP>/<date>_<标题>_<BV>/` 存在 **且** `asr/transcript.txt` 存在；有音频缺转写 = partial（等 repair，批量不重抓）
- **unavailable** = 终态（-404/-403/-87008 等），记录在 `library/_ledger/unavailable.jsonl`，默认永久跳过
- `failures.jsonl` 只写不读，不参与任何决策
- 批报告：`discoveries/<run_id>/report.json`（discovered/exported/skipped_done/skipped_partial/unavailable/failed/asr_fixed 账目）

## 红线（改代码、写脚本前必读）

1. **library/ 是唯一真源**——不引入 DB/注册表/第二真源；所有决策只看目录谓词
2. **永不造假**——失败显式记录，绝不用 mock/伪造数据兜底（充电专属抓不到就是抓不到）
3. **repair 只修不抓**——重抓一律走 grab-targets（抓取执行者唯一）
4. **进程隔离不可破坏**——每视频独立子进程是防 ASR 崩溃连坐的保护项，勿用进程池复用替代
5. **不手搓重复工作流**——合并用 export_txt_bundle / merge_collection，调研用 grab-search；同一件事要手写第三遍之前，先把它做进系统
6. 修改系统代码前，读 `references/architecture.md` 与仓库内 `docs/DESIGN.md`

## 本 skill 的随附文件

- `references/commands.md` — 全命令 + 旗标参考
- `references/recipes.md` — 工作流配方与故障排查
- `references/architecture.md` — 架构分层、包职责、保护清单（改代码时读）
- `scripts/merge_collection.py` — 跨 UP 多批语料的分桶 32k 合集合并
