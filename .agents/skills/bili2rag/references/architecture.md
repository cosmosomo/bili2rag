# bili2rag 架构与保护清单（改代码前必读）

权威来源是仓库内 `docs/DESIGN.md`（v0.2），本文是操作摘要。改任何代码前先读完这两者。

## 四个持久概念（全部，没有第五个）

| 概念 | 载体 | 语义 |
|---|---|---|
| 视频条目 | `library/<UP>/<date>_<title>_<bvid>/` | 唯一真源，决策只看它 |
| 完成谓词 done() | `bilibili_library/completion.py` 纯函数 | 全项目唯一定义（目录存在 ∧ 转写存在） |
| 失败日志 | `library/_ledger/failures.jsonl` | **只写不读**，不参与决策 |
| unavailable | `library/_ledger/unavailable.jsonl` | 唯一持久终态；仅 -404/-403 等明确错误码触发 |

`pipeline_state.json` 是调试痕迹，run 继续写但没有决策读它。

## 分层

```
L1 命令层   run / grab-search / grab-uploader / grab-targets / repair / 资产工具
L2 引擎层   orchestrate.run_batch（唯一批量实现）；run 主链（保护项）；转写引擎
L3 语义层   done() / classify_partial / unavailable 终态
L4 数据层   library 真源 · _ledger（只写）· output（可弃）· 视图层（可重建）
```

规则：决策向下只看 L4 真源 + unavailable 文件；视图层（_topics/_catalog/_by_category/_exports）永不参与决策；箭头单向无环。

## 包职责

| 包 | 职责 |
|---|---|
| `bilibili_get` | CLI + orchestrate.run_batch 三段式引擎（预过滤→并行 fetch→延迟 ASR 扫描+自动重试） |
| `bilibili_harvester` | yt-dlp 采集：metadata/audio/subtitles/danmaku/comments；playurl 签名降级；lean 模式（CC 字幕跳过音频） |
| `bilibili_asr` | faster-whisper + CC 字幕旁路（find_cc_subtitle / write_asr_from_subtitle）+ 模型缓存 + BatchedInferencePipeline(cuda) + 简体强制 |
| `bilibili_search` | WBI 签名、space 列表（-352 退避）、关键词搜索、会话/cookie |
| `bilibili_enrich` | PBP 高能条、videoshot 快照 |
| `bilibili_library` | 导出可读目录、NFO/manifest(sha256)、命名、完成谓词、批级索引 build_video_index |

## 保护清单（重构时不可破坏）

1. **目录名即数据库**——不引入 DB/中心注册表/第二真源
2. **可弃中间态 + 进程隔离 + salvage**——不引入队列调度器；不用进程池复用替代每视频子进程
3. **降级链且从不造假**——失败显式记录，永不 mock
4. **run 主链不动**——收敛的是外围编排，不是核心流水线（harvest→ASR→export）

## 效率内因已根治清单（2026-09，commit c2d6ee0 / 109c9b5 / a760033）

| 根因 | 根治 |
|---|---|
| 每视频重载 whisper 模型 | 模型缓存（每 (model,device,compute) 一次）+ 延迟转写扫描（每批一次加载） |
| 有 CC 字幕仍下音频跑 GPU | CC 旁路直出转写 + lean 抓取（--keep-audio 可关） |
| 串行下载 | fetch-workers 2 并行子进程（≤3） |
| SSL 抖动要手动重跑 | 批尾自动重试一轮 |
| find_video_dir 每调用全库扫 O(items×library) | build_video_index 批级索引，导出后增量刷新 |
| 调研/合并靠手搓临时脚本 | grab-search 一条命令；export_txt_bundle --parts-cap |

外部不可根治项（保持免疫策略）：B站风控/CDN 抖动/评论截断/充电专属。已知挂起优化：aria2c 多连接下载（用户未拍板）。

## 扩展点（唯一）

新批量需求 = 新发现头（产出统一 targets 格式喂 grab-targets），仅此一个扩展点。无队列、无守护进程、无插件系统、无配置中心（CLI 参数即配置）。

## 测试

`python -m pytest`（55 用例，~2s）。改引擎/ASR 后必须全绿再提交。Windows 下注意：脚本写文件统一 `encoding="utf-8"`；控制台输出用项目的 `_print_utf8` / `sys.stdout.reconfigure`。
