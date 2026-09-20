# bili2rag 修复手册与发布流程（skill 自身的维护指南）

> 本文回答三件事：**出了 bug 怎么修**、**修完怎么验证**、**验证完怎么上传**。
> 案例全部来自实战（2026-07 ~ 2026-09 的真实故障），不是假设性建议。

## 1. Bug 分类学：先分类，再动手

| 类别 | 特征 | 修复入口 |
|---|---|---|
| **数据层**（库里的缺口） | doctor 报 missing/partial/unavailable | §2 就地修复，**不改代码** |
| **集成层**（外部依赖行为变化） | opencli/yt-dlp/B站接口 报错或输出变形 | §3 最小复现 → 适配 → 记录形状 |
| **代码层**（我们自己的 bug） | 语法/逻辑错误、测试红 | §4 修复协议 |

判断口诀：**先跑 `doctor`**——它把数据层问题清单化；doctor 全绿再怀疑集成/代码层。

## 2. 数据层修复（不改代码）

```powershell
python scripts\doctor.py --library-root library --write-targets   # 体检+清单
python -m bilibili_get repair --library-root library --asr-device cuda --asr-compute float16
```

- **partial**（有音频缺转写）→ repair 就地转写（CC 字幕条目零 GPU 直出）
- **缺音频/缺目录** → repair 会打印现成 grab-targets 命令（targets_repair.txt）
- **unavailable 反复失败** → 确认真的不可得（-404/-403/-87008）后接受终态；误标的用 `--include-unavailable` 重试
- **空评论** → `python scripts\backfill_comments.py --library-root library --dry-run` 先看计划；注意 EMPTY_RESULT = 真零评论，不是故障
- **cookie 失效（-352 / nav -101）** → `python -m bilibili_get cookie-refresh`（日常合并浏览器 cookie；失效时自动弹二维码，扫一次即全套）

## 3. 集成层：外部依赖的已知陷阱（实战账本）

修复任何集成 bug 前，先查这张表——大概率已经踩过：

| 症状 | 根因 | 修复（已内置/方法） |
|---|---|---|
| space/搜索报 `-352` 风控 | **cookie 失效**（误导性报错） | cookie-refresh；别调退避参数 |
| `SSL: UNEXPECTED_EOF_WHILE_READING` | B站 CDN 抖动 | 引擎已自动重试一轮；仍失败重跑同命令（幂等续传） |
| 下载 412 | yt-dlp 版本旧 | `python -m pip install -U yt-dlp` |
| opencli 退出码 66 + `EMPTY_RESULT` | 渠道真无数据（如零评论） | 归类为 empty 而非 failed——**无数据 ≠ 故障** |
| Python `subprocess` 找不到 opencli/yt-dlp（WinError 2） | npm/pip 的 `.cmd`/`.bat` shim，`CreateProcess` 不解析裸名 | 用 `shutil.which()` 解析完整路径（bridge 已内置） |
| `opencli browser` 报 `unknown option '-f'` | `-f json` 是适配器层旗标，browser 通道不认 | 桥接层按通道参数化（`fmt_json`） |
| `browser eval` 输出解析 JSON 失败 | eval 返回**原生纯文本**（非 JSON 编码） | 走 `_run_text`，不强制 json.loads |
| 写二维码 PNG 报 `EINVAL` | 固定文件名被图片查看器锁住 | 每次运行用唯一文件名 |
| 登录页"等一下自动跳转"、捕获不到扫码 | 浏览器已登录→登录页无感跳转，无 poll 可捕获 | **别走浏览器捕获**；纯 Python QR 流程（generate→poll→ticket 兑换） |
| poll 成功但响应里没有 SESSDATA | B站 2026-09 改版：`data.url` 只剩 `ticket`，cookie 改由 crossDomain 跳转 Set-Cookie 下发 | 自己请求 crossDomain URL 兑换 Set-Cookie（cookies.py `_redeem_cross_domain`） |
| Chrome cookie 库复制/读取失败 | Chrome 运行时独占锁（DPAPI 密钥能解但库打不开） | 死路，别再试；走 QR 流程 |

**B站改版应对方法论**：外部接口的输出形状会漂移。发现"成功但解析不到"时：
1. 写最小复现脚本（临时 .py，跑完删），把**完整响应**打到 stdout（截断 ≥400 字符，160 会把关键信息掐掉——ticket 改版那次就是被截断坑的）
2. 对比代码里的形状注释（bridge.py 文件头记录了各命令的实测形状与日期）
3. 适配 + 把新形状写回形状注释——这是形状账本，下次漂移先查它

## 4. 代码层修复协议

```
复现（最小化）→ 定位 → 修复 → 验证 → 提交
```

验证三关，**全部通过才能提交**：

1. `python -m py_compile <改动的文件>` —— 语法关（编辑工具偶尔会吃掉换行导致两行粘连，编译立刻暴露）
2. `python -m pytest` —— 全绿关（不许带着红测试提交；修 bug 必须配回归测试，把触发该 bug 的输入固化成用例）
3. **小样本实测** —— mock 测试全绿 ≠ 真实世界没问题（EMPTY_RESULT 语义、ticket 兑换都是实测才暴露的）；用真实库/真浏览器各打一发

## 5. 上传协议（修复/增强完成后的双仓同步）

skill 有三个位置，关系固定：

```
活体（改这里）: 本机 skill 加载目录（含本机路径与实测细节的完整版）
私有全量备份:   维护者的私有快照仓                  ← 原样快照，零丢失
开源脱敏版:     本仓库 → .agents/skills/bili2rag/   ← 任何人克隆即用
```

**步骤**（改完活体后）：

1. **同步到开源仓拷贝**（本仓库 `.agents/skills/bili2rag/`），执行脱敏规则：
   - 绝对路径（`E:\...`、`C:\Users\...`）→ 改成"本仓库/仓库根"等相对表述
   - 个人库规模数字（UP 数/视频数/N 条空评论）→ "部分/大部分"或删除
   - 具体 BV 号战报 → 机制性描述（保留"发生了什么类型的事"，去掉"具体哪条"）
   - 维护者对话来源标记 → 中性表述
   - 账号/用户名 → 一律不出现
2. **公开仓提交**：
   ```powershell
   git add .agents
   git commit -m "docs(skill): <改动摘要>"; git push
   ```
3. **私有仓快照**（维护者私有流程）：克隆私有快照仓 → 用活体覆盖 `bili2rag/` → 核对 README 索引行 → commit + push
4. **验证**：从 GitHub raw 抓开源版原文，grep 本地信息模式（绝对路径/规模数字/BV 号）应**零命中**

## 6. 预防性原则（写进习惯）

- 修好的每个 bug 都要有回归测试（本仓 82+ 测试就是这么攒出来的）
- 外部输出形状记录在代码注释里（形状账本），漂移时先查账本再重新探测
- 修复类改动一次一个主题一个 commit，信息里写清"症状→根因→修复"三段
- 涉及凭证的文件（cookie.txt 及其 .bak）确认在 .gitignore 内再 push
