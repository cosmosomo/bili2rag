# bili2rag 工作流配方

实战验证过的端到端流程。执行前先看 SKILL.md 的"任务→命令速查"和"长批量后台运行模式"。

## 配方一：专题调研（"帮我调研 XX 主题的视频语料"）

这是最重的场景（原型：Agent Harness 范式调研，72 集 / 35.5 万字符）。流程：

1. **发现+抓取**：多个关键词各跑一次 grab-search（每条命令独立完成 搜→筛→抓→转写）：

```powershell
python -m bilibili_get grab-search --keyword "deepseek harness" --require-any harness,Harness,DSH --min-play 1000 --min-seconds 120 --cookies cookie.txt --asr-device cuda --asr-compute float16 --prune-output --name kw_dsh
python -m bilibili_get grab-search --keyword "pi agent" ...
```

   - 标题过滤词宁缺勿滥：先用一次 `--search-limit 30` 不带过滤跑一遍看结果分布，再定 `--require-any`
   - 想要某个 UP 的全部相关视频：`grab-uploader --seed-bvid <他的任一视频BV> --discover-limit 0`
   - 用户点名的特定视频：直接 `run --url` 或并入 targets 文件用 `grab-targets`

2. **等待与监控**：>30 条用后台模式跑，`Get-Content discoveries\<name>_out.log -Tail 10` 查进度；引擎幂等，断了重跑即可

3. **体检收尾**：`python scripts\doctor.py --library-root library`；有 partial 就 `repair --asr-device cuda --asr-compute float16`

4. **合并**：
   - 单 UP / 单主题 → `python scripts\export_txt_bundle.py --uploader "UP名" --with-header --parts-cap 32000`
   - 跨 UP 多批 + 按子议题分桶 → 用本 skill 的 `scripts/merge_collection.py`（见下）
   - 产物放 `library/_exports/txt/专题合集/<主题>_partNN.txt`，每卷 ≤32k 可整体塞进上下文

5. **交付**：报告合集位置（卷数/集数/字符数/分桶分布），语料分析另起任务

## merge_collection.py 用法（跨源分桶合并）

```powershell
# 1) 写分桶配置 buckets.json（标题正则 → 子议题，顺序即卷内排序）
#    [{"name": "范式与概念", "patterns": ["harness engineering", "动画", "啥意思"]},
#     {"name": "工程深潜", "patterns": ["源码解析", "架构拆解", "Agent Loop", "Sandbox"]},
#     {"name": "DSH生态", "patterns": ["deepseek", "DSH"]}]

# 2) 合并多个批报告 + 某 UP 的相关目录
python .agents\skills\bili2rag\scripts\merge_collection.py --repo-root . --topic "AgentHarness范式" `
  --reports discoveries\<run1>\report.json discoveries\<run2>\report.json `
  --extra "AI林湛星:spec|harness|agent|架构" `
  --buckets buckets.json --default-bucket "DSH生态" --cap 32000
```

- `--extra "UP名:标题正则"`：把某 UP 目录名匹配正则的视频补进合集（可重复）
- 无 `--buckets` 时按日期单桶排序
- 幂等：重跑先删同名 `partNN.txt` 再写

## 配方二：UP 主周期增量刷新

```powershell
python -m bilibili_get grab-uploader --seed-bvid BVxxx --since-days 7 --new-limit 0 `
  --cookies cookie.txt --asr-device cuda --asr-compute float16 --prune-output
```

- `--since-days 7` 只看最近一周投稿；done 谓词自动跳过已抓的
- 多个 UP：各自跑一次，或把上次 discoveries 的 targets 并成清单走 grab-targets

## 配方三：修复闭环

```powershell
python scripts\doctor.py --library-root library --write-targets   # 体检+生成待修清单
python -m bilibili_get repair --library-root library --asr-device cuda --asr-compute float16
# repair 结束会打印：缺音频/缺目录条目的现成 grab-targets 命令（targets_repair.txt）
```

- partial（有音频缺转写）→ repair 就地转写，**不要重抓**
- 缺音频/缺目录 → 按打印的命令走 grab-targets 重抓
- 反复失败且日志见 -404/-403/-87008 → `repair --mark-unavailable BVxxx` 或让引擎自动标记，之后默认跳过；确认要重试时 `--include-unavailable`

## 配方四：故障排查速查

| 症状 | 真因 | 处理 |
|---|---|---|
| space/搜索报 `-352` 风控 | **cookie 失效**（误导性报错） | 用户重新导出覆盖 `cookie.txt`；别调退避参数 |
| `SSL: UNEXPECTED_EOF_WHILE_READING` | B站 CDN 抖动 | 引擎已自动重试一轮；仍失败的重跑同命令（幂等续传） |
| 下载 412 | yt-dlp 版本旧 | `python -m pip install -U yt-dlp` |
| `[COOKIE WARN]` 但 run 正常 | cookie 失效但公开视频不需要登录 | 继续；需要搜索时再换 |
| 批结束 partial > 0 | 延迟转写阶段个别失败 | `repair`；CC 字幕条目会零 GPU 直出 |
| 充电专属/会员内容失败 | 外部限制 | 不造假原则：显式失败，标记 unavailable |
| 评论只有首屏 | 服务端 is_end 截断 | 外部限制，接受 |

## 环境 & 规模事实（2026-09 时点）

- 库：40 UP / 358 条视频；GPU CUDA 可用；whisper 模型 auto（cuda→small / cpu→base）
- 转写速度参考：whisper-base float16 ≈ 15-25x 实时；CC 旁路条目零 GPU
- 每条视频子进程固定开销 ~3-5s（进程隔离保护项，已知不修）
- 已知挂起优化（用户未拍板）：aria2c 多连接下载（网络层 2-5x）
