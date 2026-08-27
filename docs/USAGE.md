# bili2rag 使用手册（低熵入口）

> 安装后 bili2rag ... 与 python -m bilibili_get ... 两种调用等价；本文沿用模块形式。

目标：把 B 站视频变成可用于 RAG 的本地数据资产（音频/字幕/弹幕/评论快照/元信息/ASR 转写），并以“看目录名就知道是什么视频”的方式归档到 `library/`。

## 0. 最重要的约定（请先读）

1) **单一真源：`library/`**
- `output/` 是采集中间目录（可删）。推荐始终加 `--prune-output`，导出成功后自动删除 `output/<bvid>/`。

2) **简体中文强制**
- ASR 输出 `asr/transcript.txt`、`asr/transcript.srt` 会在写盘前强制转为简体中文，并覆盖原输出（不保留繁体/混杂版本）。

3) **Windows UTF-8**
- PowerShell 里建议先执行：

```powershell
[Console]::InputEncoding  = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
chcp 65001 > $null
```

## 1. 目录结构（你最需要关心的）

```
library/
  <UP主名>/
    2026-01-25_<标题>_BVxxxx/
      audio.m4a | audio.mp3              # 音频（导出后统一命名）
      asr/
        transcript.txt                   # 转写文本（简体）
        transcript.srt                   # 转写字幕（简体）
        segments.json                    # 分段信息
      subtitles/                         # B站侧字幕/AI字幕/弹幕xml（若可用）
      snapshots/                         # 视频快照（可选：--snapshots）
        snapshot_<bvid>_<ts>s.jpg
        index.json
      json/                              # metadata/comments/danmaku/streams等结构化数据
      logs/                              # harvest/asr/pipeline 运行日志与状态
      comments.txt                       # 评论纯文本快照
      cover.jpg | cover.png | cover.webp # 原始封面（若可用）
      danmaku.json                       # 结构化弹幕（若可用）
      movie.nfo                          # Emby/Kodi 兼容元数据
      manifest.json                      # 文件清单（含 sha256）
      bilibili.url                       # 原始链接
      source.json                        # 关键元信息摘要

library/_topics/
  <主题名>/
    <2026-01-.._标题_BV...>/             # 主题条目目录（不重复存大文件）
      pointer.json                       # 指向真实的 library/<UP主>/... 目录

library/_exports/txt/
  uploader/<UP主名>/                     # 同一UP主的“全集 txt”导出目录
    *.txt
    ALL.txt
    index.json
  topic/<主题名>/                        # 同一主题的“全集 txt”导出目录
    *.txt
    ALL.txt
    index.json
```

## 2. Cookie 准备（决定可访问内容）

> 功能 × Cookie 依赖矩阵、获取步骤、失效症状速查：见 README「Cookie & access matrix」节（单一事实源，此处不重复）。

要点：
- 保存为项目根目录 `cookie.txt`，支持 Netscape / 单行 Header 两种格式（详见 `cookie.txt.example`）
- 公开视频、ASR、导出、doctor **不需要** cookie；space 发现/搜索**必须要有效** `SESSDATA`
- cookie 失效时 space 类接口报误导性的 `-352`；工具启动会自动预检并提示

## 3. 低熵入口：单视频一键跑通（最常用）

在 `` 下执行：

```powershell
python -m bilibili_get run --url BV15JqABoEvj --cookies cookie.txt --prune-output
```

GPU 转写（推荐）：

```powershell
python -m bilibili_get run --url BV15JqABoEvj --cookies cookie.txt --asr-device cuda --asr-compute float16 --prune-output
```

常用参数：
- `--download audio,subtitles,cover`：默认保存音频、字幕和封面；视频体积大不建议默认抓视频流。
- `--comment-pages 1`：评论快照页数（越大越慢）。
- `--pbp`：抓取高能进度条（保存到 `json/pbp.json`）。
- `--snapshots smart`：默认。标题含“导图/思维导图”时自动截取快照（保存到 `snapshots/`，并写 `snapshots/index.json`）。
- `--snapshots auto`：根据 PBP 峰值自动截取快照（缺峰值会均匀取点）。
- `--snapshots 10,60,120`：按指定秒数截取快照。
- `--snapshot-k 5`：`--snapshots auto` 时选取的峰值数量（默认 5）。
- `--if-exists skip|overwrite|fail`：导出目录已存在时策略（默认 skip）。
- `--resume`：跳过状态成功且产物仍存在的采集、转写和导出阶段；状态保存在 `output/<bvid>/logs/pipeline_state.json`。
- `--proxy http://127.0.0.1:7890`：网络不稳可用代理。

## 4. 批量：统一编排引擎（grab-uploader / grab-targets）

所有批量走同一个引擎（`run_batch`）：每视频独立子进程抗崩溃；完成判断只有一条规则——
`library 里目录存在且转写存在 = done`。跳过三态：`done` / `partial（有音频缺转写，等 repair）` /
`unavailable（终态：已删除/充电专属，可用 --include-unavailable 显式重试）`。

### 4.1 按 UP 主增量抓新（space 发现头）

```powershell
python -m bilibili_get grab-uploader `
  --seed-bvid BV1ZnzhB2EXe `
  --discover-limit 50 --new-limit 0 `
  --cookies .\cookie.txt `
  --asr-device cuda --asr-compute float16 `
  --prune-output
```

- `--seed-bvid` / `--mid`：二选一定位 UP 主；`--discover-limit`：发现最近 N 条。
- `--new-limit`：本次最多处理多少条新增（0=不限）；`--between-sleep`：防抖间隔。
- 报告：`discoveries/<run_id>/report.json`（exported/skipped/failed 五账）+ 每视频日志。

### 4.2 按清单批量（文件发现头；主题收集也用它）

```powershell
python -m bilibili_get grab-targets --targets-file .\targets.txt --prune-output --asr-device cuda --asr-compute float16
```

- targets 文件任意格式（会自动抽 BV 号，支持 `【标题】URL` 与 `BV<TAB>标题` 两种行）。

### 4.3 失败语义

- 子进程崩溃 → 引擎 salvage（导出+清理已采集部分）→ 无转写则记 `partial`（等 repair）。
- 明确不可得（-404/-403/-87008）→ 自动标 `unavailable`（`library/_ledger/unavailable.jsonl`）。
- 其它失败 → 记入 `library/_ledger/failures.jsonl`（纯日志）+ 报告 failed 账。

## 5. 主题收集（把不同 UP 的视频归到一个主题目录）

适用场景：你想把“个人IP”这类主题下的多个视频集中浏览，但又不想复制一份音频/转写占空间。

### 5.1 准备 targets 文件（任意格式都行，脚本会抽 BV 号）

例：`.tmp/topic_targets/personal_ip.txt`

#### 5.1.1 自动生成精选 targets（从 discoveries 合并→去重→过滤）

当你已经用 `python -m bilibili_search search ...` 产出了多个 `discoveries/<run_id>/results.jsonl`，可以用下面脚本把它们合并成一个“精选 50” targets：

```powershell
python .\scripts\topic_make_targets_from_discoveries.py `
  --input .\discoveries\20260129_232609_哲学 `
  --input .\discoveries\20260129_232612_普通人 哲学 `
  --input .\discoveries\20260129_232615_哲学 人生意义 `
  --out-targets .\.tmp\topic_targets\哲学_网民见解_精选50.txt `
  --out-report .\.tmp\topic_targets\哲学_网民见解_精选50.report.json `
  --profile philosophy_netizen `
  --limit 50 --min-seconds 180 --max-seconds 5400 --max-per-author 2
```

说明：
- `--input`：可重复；传 `discoveries/<run_id>/` 目录或直接传 `results.jsonl` 文件路径都行。
- `--out-report`：会记录筛选规则与每条选中视频的打分/来源，便于复核“有没有混入噪音”。
- `--profile`：内置筛选档位（会影响“信号词/噪音词/打分逻辑”）：
  - `philosophy_netizen`：哲学网民见解向（默认示例）
  - `philosophy_mixed`：哲学混合向（允许更多课程/讲座）
  - `management`：管理学/组织管理/项目管理/绩效 OKR/KPI 向（已做噪音过滤）
  - `self_media`：自媒体/新媒体“教学 + 运营”向（起号/选题/文案/剪辑/变现/平台策略）
  - `self_psych`：自我心理训练/心态建设/效能感/内耗焦虑/情绪管理 等“心法”向（尽量避开助眠/占卜/刷题）
  - `systems`：一般系统论/控制论/系统思维/系统动力学/复杂系统 向
  - `country_pe`：各国政体/政治制度/经济体制/福利国家等“结构性介绍”向（尽量避开纯新闻快讯）

### 5.2 运行主题收集（引擎抓取/转写/导出，然后生成 pointer）

```powershell
python .\scripts\topic_collect.py `
  --topic-name "个人IP" `
  --targets-file .\.tmp\topic_targets\personal_ip.txt `
  --cookies .\cookie.txt `
  --pbp --snapshots auto --snapshot-k 5 `
  --asr-device cuda --asr-compute float16 `
  --between-sleep 1.0
```

产物：
- 真实数据仍在：`library/<UP主>/<date_title_bvid>/`
- 主题索引在：`library/_topics/个人IP/<date_title_bvid>/pointer.json`
- topic_collect 内部调用统一编排引擎（无私有循环），仅保留 pointer 后处理。

### 5.3 无 topic 的清单批量（就是 grab-targets）

```powershell
python -m bilibili_get grab-targets `
  --targets-file .\discoveries\<run_id>\targets.txt `
  --name "MHYYYY_近20" `
  --cookies .\cookie.txt `
  --download audio,subtitles,cover `
  --snapshots smart `
  --asr-lang zh `
  --prune-output
```

产物：
- 真实数据落盘：`library/<UP主>/<date_title_bvid>/`
- 报告与每视频日志：`discoveries/<timestamp>_collect_<name>/`

## 6. 导出“全集 txt”（按 UP 主或按主题）

你提出的需求“按主题或者 UP 主，把完整 txt 导出到同一个文件夹”就是这个脚本：
- `scripts/export_txt_bundle.py`

### 6.1 按 UP 主导出

```powershell
python .\scripts\export_txt_bundle.py --uploader "硅谷101" --with-header --concat-all
```

输出到：
- `library/_exports/txt/uploader/硅谷101/`

### 6.2 按主题导出

```powershell
python .\scripts\export_txt_bundle.py --topic "个人IP" --with-header --concat-all
```

输出到：
- `library/_exports/txt/topic/个人IP/`

文件说明：
- 每条视频一个 `*.txt`（文件名即导出目录名）
- `ALL.txt`：把全部拼接在一起
- `index.json`：映射（bvid → 原目录 → 导出 txt）

如果某条视频缺 `asr/transcript.txt`（单视频）或 `asr/transcript_all.txt`（多 P 合并）：
- 默认 `--if-missing skip` 会跳过并打印 `[MISSING] ...`
- 也可以 `--if-missing fail` 直接失败

补充：少数多 P 视频可能只有 `pages/<pXX_0>/asr/transcript.txt`（每 P 各一份，未生成合并稿）。
`export_txt_bundle.py` 会自动按 `pages/` 目录顺序把各页拼接导出（带 `===== pXX_0 =====` 分隔）。

## 7. 空间清理（确保只有 library/）

推荐始终使用 `--prune-output`。

如果你历史上残留了 `output/`，可以：

```powershell
python -m bilibili_get export --all --output-root .\output --library-root .\library --if-exists skip
python .\scripts\prune_output_exported.py --output-root .\output --library-root .\library --execute
```

## 7.5 体检与修复闭环（doctor + repair）

**doctor 三账视图**（库内缺口 ∣ unavailable 终态 ∣ output 残留）：

```powershell
python .\scripts\doctor.py --library-root .\library
python .\scripts\doctor.py --write-targets   # 同时生成待修复 targets 文件
```

**repair**（只修不抓——重抓永远走引擎）：

```powershell
python -m bilibili_get repair --library-root .\library --dry-run   # 预览
python -m bilibili_get repair --library-root .\library --asr-device cuda --asr-compute float16
# 有音频缺转写 -> 就地转写 + 重写 manifest（sha256 保持可信）
# 缺音频/缺目录 -> 生成 targets_repair.txt 并打印现成 grab-targets 命令
python -m bilibili_get repair --mark-unavailable BVxxxx --library-root .\library   # 手动终态出口
```

unavailable（已删除/充电专属/互动视频等）重试：`grab-* --include-unavailable`。

## 7.6 library 导航（分类浏览 + 索引）

library/ 下 UP 主动辄数百个，平铺难找。两步生成分类导航：

```powershell
python .\scripts\build_uploader_catalog.py --library-root .\library   # 主题归类（数据源）
python .\scripts\make_library_nav.py                                  # 生成 _by_category/ junction + INDEX.md
```

产物：
- `library/_by_category/<分类>/<UP主>/`：junction 链接（不占空间），VSCode/资源管理器可按主题层级浏览
- `library/_catalog/INDEX.md`：分类 → UP 主 → 视频数 的可搜索索引

## 7.7 可靠性提示（内置）

- **cookie 失效预检**：run/批量启动时自动调 nav 检查登录态；失效会打 `[COOKIE WARN]`（space 相关命令直接中止，避免误导性的 -352）
- **412 反爬**：音频下载 412 时提示更新 yt-dlp（`python -m pip install -U yt-dlp`）
- **output 残留汇总**：run 结束若有"已采集未导出"的目录，打 `[LEFTOVER]` 提示用 export --all 补救
- **失败账本**：`library/_ledger/failures.jsonl`（纯日志）与 `unavailable.jsonl`（唯一持久终态）；决策只看目录谓词，不看日志

## 8. 简体修复（对历史转写批量覆盖）

如果你库里存在历史转写（繁体/混杂），可直接原地覆盖为简体：

```powershell
python .\scripts\simplify_transcripts_inplace.py --library-root .\library
```

## 9. 常见问题（FAQ）

1) 出现 `SSL: UNEXPECTED_EOF_WHILE_READING`
- B站/CDN 网络抖动常见，重试即可；批量用 `grab-uploader`/`grab-targets`（每视频独立子进程 + done 谓词自动跳过，重跑同命令即续传）。

2) `--snapshots auto` 没有弹幕 / PBP peaks 为空怎么办？
- 有些视频弹幕太少，PBP 接口会返回空数据（例如 debug 里提示 `not enough dm`）。
- 当前实现会自动改用“按时长均匀取点”的策略生成快照（`snapshots/index.json` 里会标注 `auto_strategy`）。
- 如果你想自己控制截图位置：用手动秒数 `--snapshots 10,60,120`。

3) 有些视频 `videoshot` 返回了图片但没有 index（会导致无法按时间匹配）怎么办？
- 当前实现会在缺 index 时合成一个“均匀时间轴 index”来匹配截图（`snapshots/index.json` 里会标注 `videoshot_index_strategy=synthetic_uniform_index`）。

4) 充电专属能不能抓全？
- 可能拿不到音频/视频流（会失败）。脚本不会用 mock/假数据兜底，会显式报错/失败记录。

5) 为什么 `library/_topics` 里没有音频/转写？
- 主题目录是“指针索引”，避免重复存大文件；真实数据在 `pointer.json` 里的 `exported_dir`。

## 10. 免责声明

请在遵守 B 站服务条款与所在地法律法规的前提下使用；仅用于合规学习与研究，禁止用于任何侵权或违规用途。
