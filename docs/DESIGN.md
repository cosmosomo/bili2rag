# bili2rag 设计规格（v0.2）

本文档记录项目的结构决策与理由，供实施对照与未来维护参考。改代码前先读"保护清单"。

## 1. 定位与边界

**单机命令行流水线：把 B 站视频变成文件系统里可被 RAG 直接消费的语料资产。**

| 是 | 不是 |
|---|---|
| 单人本地工具，文件系统是唯一持久层 | 不是服务/多用户系统，无守护进程 |
| 三种粒度触发：单视频 / 按UP增量 / 按清单 | 不是通用爬虫框架 |
| 承认并降级外部限制（评论截断/删除视频/充电专属） | 不绕过风控、不造假数据 |

## 2. 四个持久概念（全部，没有第五个）

| 概念 | 定义 | 载体 | 语义 |
|---|---|---|---|
| 视频条目 | 一个 bvid 的资产目录 | `library/<UP>/<date>_<title>_<bvid>/` | 唯一真源，决策只看它 |
| 完成谓词 `done()` | 目录存在 ∧ 转写存在 | `bilibili_library/completion.py` 纯函数 | 全项目唯一定义 |
| 失败日志 | 纯追加的人读记录 | `library/_ledger/failures.jsonl` | **只写，不参与任何决策** |
| unavailable | 明确不可得（已删除/充电专属）的终态 | `library/_ledger/unavailable.jsonl` | 唯一持久状态；仅 -404/-403 等明确错误码触发 |

`pipeline_state.json`（logs/ 下）是调试痕迹：run 继续写，但**没有决策读它**。

## 3. 分层架构

```
L1 命令层（6 个）  run / grab-uploader / grab-targets / repair / doctor / 资产工具
L2 引擎层          orchestrate.run_batch（唯一批量实现）；主链（run 内部，不动）；
                  转写引擎（接受任意含 audio 的目录）
L3 语义层          done() 谓词 / classify_partial / unavailable 终态
L4 数据层          library 真源（决策依据）· _ledger（只写）· output（可弃）· 视图层（可重建）
```

规则：所有决策向下只看 L4 真源 + unavailable 文件；账本只写不读；视图层（_topics/_catalog/_by_category/_exports）永不参与决策。箭头单向，无环。

## 4. 核心流程

- **单视频**：`URL → harvest(yt-dlp→view→playurl 三级降级) → ASR(简体强制) → export → prune`（保护项，不改）
- **批量**（引擎，一条规则走全程）：
  - `done()` → skip
  - partial（有音频缺转写）→ skip + 记账，**批量永不重抓 repair 能就地修好的东西**
  - unavailable 且未显式 `--include-unavailable` → skip
  - 其余 → 子进程跑主链；崩溃→salvage+记 partial；失败→记 failure；-404/-403→记 unavailable
  - 结束写运行报告（discovered/exported/skipped/failed）
- **修复**：repair 只做就地 ASR + manifest 重写 + unavailable 标记 + 输出 targets_repair.txt；**重抓一律走 grab-targets**（抓取执行者唯一）
- **体检**：doctor 三账视图 = 库内缺口（谓词现算）+ unavailable + output 残留

## 5. 设计决策（含代价）

| 决策 | 理由 | 代价 |
|---|---|---|
| 目录谓词取代 state.json 作决策 | 目录是跨入口可见的共同事实 | state 双写但只信一个 |
| 账本 jsonl 而非 DB | 延续"文件系统即数据库" | 无并发保护（单机可接受） |
| repair 显式命令而非自动 | 修复花 GPU/带宽，人决定时机 | 多记一个命令（doctor 给出现成命令行） |
| salvage 保留但记入账本 | 元数据保底价值不变；partial 从静默成功变可见 | 多一次账本写 |
| unavailable 仅明确错误码触发 | 防瞬时网络错误被永久钉死 | 错误码漂移需人工校准，`--include-unavailable` 兜底 |
| 批量不重抓 partial | 有音频只缺转写时，就地修复成本远低于重抓 | partial 需等 repair（报告里可见） |
| CC 字幕旁路（v0.2.1） | UP 主字幕质量 ≥ whisper-base 且零 GPU；312/358 库内视频本就带字幕 | CC 缺失/质量差时仍走 whisper（自动回退） |
| 延迟转写扫描（v0.2.1） | 模型每批加载一次而非每视频一次 | 抓取与转写两阶段，中间态可见为 partial |
| 并行抓取 2 Worker（v0.2.1） | 下载是墙钟主瓶颈；进程隔离不变 | 站点侧压力略增（默认 2，温和） |
| OpenCLI 桥接为可选子进程依赖（v0.3） | hot/ranking 发现头、官方评论（楼中楼）是流水线缺口；yt-dlp 同款模式，缺失即优雅降级 | 外部 npm 依赖；浏览器会话需在线；EMPTY_RESULT 语义需在桥接层翻译 |

## 6. 保护清单（重构时不可破坏）

1. **目录名即数据库**——不引入 DB/中心注册表/第二真源
2. **可弃中间态 + 进程隔离 + salvage**——不引入队列调度器
3. **降级链且从不造假**——失败显式记录，永不 mock
4. **run 主链不动**——收敛的是外围编排，不是核心流水线

## 7. 已知外部限制与降级

| 限制 | 行为 |
|---|---|
| 评论服务端 `is_end` 截断 | 只取首屏热评 + all_count，显式不造假 |
| 充电专属/会员内容 | 显式失败（元信息可能可得） |
| space/搜索接口需登录 | 启动 nav 预检，失效即中止并提示换 cookie |
| yt-dlp 412 反爬 | 提示升级 yt-dlp |

## 8. 不做什么

无队列、无守护进程、无并发框架、无插件系统、无配置中心（CLI 参数即配置）、无 B站以外目标。新批量需求 = 新发现头（产出统一 targets 格式），仅此一个扩展点。
