# Novel-Engine 生成控制使用说明

本目录提供一键批处理脚本，用于 **清理生成记录 / 开始全量生成 / 停止 / 断点续写 / 修改 API Key / 查看状态**。

```
scripts/control/
├── control.py      # 控制核心(Python，所有逻辑在此)
├── clean.bat       # 清理生成记录
├── start.bat       # 开始全量生成  [可选章节数]
├── stop.bat        # 优雅停止
├── resume.bat      # 从最后章节续写（单次）
├── auto.bat        # 无人值守：HALT 自动冷却重试，直到目标章数 [目标章数]
├── status.bat      # 查看状态与最近日志
├── setkey.bat      # 修改 API Key  [新key]
└── USAGE.md        # 本说明
```

> 路径全部相对本目录自动推导，可直接把整个 `novel-engine` 目录搬到别处使用。

---

## 0. 前提

- Python 3.12：`D:\Program Files\Python312\python.exe`（脚本已写死此路径）。
- 真实生成统一调用 **Agnes**（模型 `agnes-2.5-flash`，端点 `https://apihub.agnes-ai.com`），需有效的 `AGNES_API_KEY`（见 `src/novel_engine/.env`，supervisor 会自动加载）。不再使用 SiliconFlow。
- 生成入口为 `novel_engine.pipeline.production_runner`，脚本已封装好，无需手敲命令。

---

## 1. 快速开始

```bat
start.bat            :: 开始全量生成(读取配置里的 total_chapters，默认 3800 章)
status.bat           :: 看进度
stop.bat             :: 想停就停(已生成章节不丢)
resume.bat           :: 之后从断点继续
```

建议先用小批量试跑验证配置与 Key 是否生效：

```bat
start.bat 20         :: 只生成前 20 章，验证连通性与质量
```

---

## 2. 命令详解

### clean.bat — 清理生成记录
删除已生成的章节、运行时状态(checkpoint/state.db)、审计报告与日志，**不碰源码与配置**。
> 注意：若生成正在运行，clean 会拒绝执行（先 `stop`）。

### start.bat [章节数] — 开始全量生成
- 不传参数：读取 `config/runtime_config.json` 的 `pipeline.total_chapters`（默认 **3800**）。
- 传数字：`start.bat 50` 只生成前 50 章。
- 启动后**会重置运行时**（等同于先 clean 再生成），即旧章节会被覆盖。
- 进程以后台方式运行，pid 写入 `novel-engine/runtime_gen.pid`。

### stop.bat — 停止生成
向生成进程发送终止信号。因为每章生成完即落盘，所以**已完成的章节不会丢失**。
随后用 `resume.bat` 可继续。

### resume.bat — 断点续写
自动探测 `chapters/novel/chapter_*.txt` 的最大编号 N，然后从 N+1 继续生成。
- **不会重置运行时**，也不会重生成 1..N 中已完整的章节。
- 若某章生成到一半被中断（只有 novel 缺 synopsis/outline），resume 会把它当作未完成而重新生成，安全无重复。

### auto.bat [目标章数] — 无人值守自动续跑（推荐长跑使用）
基于 `novel_engine.pipeline.supervisor` 的看门狗，用于真正的无人值守长生成：
- 循环调用生产批，续跑**只承认通过 checkpoint 校验的章**：已正式出版的章跳过，未达出版线被隔离的章会重新生成，绝不会出现“正稿缺章却跳过去”。
- 某章中途 HALT（如评审分数低于出版线 88、瞬时 API 错误）时，按 **300s / 600s / 900s 递增冷却**后自动重跑该章。
- **同一章连续 HALT 超过 3 次**触发熔断，看门狗停止并保留 `runtime/HALT_REASON.json` 供人工检查，避免无限烧钱。
- 进程未写 HALT 却又无推进（静默崩溃/被杀）时，最多无进展重试 3 次后熔断。
- 不传参数则以 `runtime_config.json` 的 `total_chapters` 为目标。
- 用法：`auto.bat 1000`（跑到第 1000 章）；双击或在终端运行均可，关闭窗口即停止（已落盘章节不丢，之后可再 `auto.bat` 续跑）。

### status.bat — 查看状态
显示：是否运行中(pid)、已完成章节数、以及 `runtime/logs/production.log` 最近 20 行。
（脚本里加了 `pause`，方便双击查看；在终端里直接 `python control.py status` 也可。）

### setkey.bat <新key> — 修改 API Key
把当前 active_profile 对应密钥（默认 `AGNES_API_KEY`）写入 `src/novel_engine/.env`（若不存在则追加）。
下一次 `start` / `resume` 生效。详见 §4。

---

## 3. 是否支持续写？（重点）

**支持。** 机制如下：

1. 每章成功生成后，`production_runner` 会把 `chapter_N.txt` / `chapter_N.json`(outline) / `chapter_N.txt`(synopsis) 落盘，并更新 `runtime/checkpoint.json`。
2. `stop` 只是杀掉进程，**不删除任何已落盘章节**。
3. `resume` 以 `resume_checkpoint=N` 重新启动：
   - `resume_checkpoint > 0` 时，**跳过运行时重置**（不会清空已有章节）；
   - 对 `i ≤ N` 且三个文件都存在的章节直接跳过；
   - 从 `N+1` 开始继续生成。

所以正常工作流是：`start` →（中途 `stop`）→ `resume` → 直到完成。

---

## 4. 修改 API Key / 模型 / 地址 / 章节数

> **首选**：`select_llm.bat` 一键切换供应商/模型与密钥，完整说明见
> [LLM选择说明.md](./LLM选择说明.md)。LLM 的权威配置是
> `src/novel_engine/config/llm_providers.json` 的 `active_profile` 与 `profiles`。

### API Key
- **查看/切换供应商**：`select_llm.bat list` / `select_llm.bat switch <profile>`
- **写当前供应商的 Key**：`setkey.bat sk-你的新key`（写入 `.env` 中 active_profile 对应变量，默认 `AGNES_API_KEY`）
- **手动**：编辑 `src/novel_engine/.env`，改/加这一行：
  ```
  AGNES_API_KEY=sk-你的新key
  ```
> 真实密钥只放 `.env`，不要写进 `llm_providers.json`（那里只写环境变量名 `api_key_env`）。

### 模型与 API 地址
编辑 `src/novel_engine/config/llm_providers.json`：改 active profile 各 phase 的
`models` 与 profile 的 `base_url`（OpenAI 兼容端点）。新增 profile 后用
`select_llm.bat switch <name>` 切换。字段结构见 [LLM选择说明.md](./LLM选择说明.md)。
### 生成章节数
- 临时：`start.bat 100`
- 永久：改 `runtime_config.json` 的 `pipeline.total_chapters`。

### 其它可调质量参数（已按认证结果放宽）
`quality` 段：`publication_line=88`、`min_chapter_score=82`、`min_pacing=7.5`、`min_innovation=7.0`、`target_avg_score=88`。一般无需改动。

---

## 5. 监控与日志

- 主日志：`src/novel_engine/runtime/logs/production.log`（含每章 PASS/FAIL、分数、成本、ETA）。
- 进度汇总：`status.bat`；或直接 `tail` 该日志。
- 最终报告：`src/novel_engine/audit/production_report.json`（含均分/最低分/总成本/是否在预算内）。

---

## 6. 成本与耗时提示

- 真机实测（agnes-2.5-flash）单章约 6–9 分钟、约 **$0.01/章**（3 章 49 次调用 / 325k tokens / $0.0306）。
- 按 3800 章外推约 **$39 调用成本**，但墙钟时间是主要约束（数天连续运行）；建议用 `auto.bat` 无人值守续跑。
- 推荐流程：先 `start.bat 3` 或 `auto.bat 3` 试跑 → 看 `status` 与成稿确认质量 → 再 `auto.bat 1000`。

---

## 7. 故障排查

| 现象 | 处理 |
|---|---|
| `start` 提示“已在运行” | 先 `stop.bat`，再 `start` |
| 进程卡死/无日志 | `stop.bat` → `resume.bat` 续写 |
| 502/超时（API 抖动） | 引擎内置 both-empty 跨温度重试 + 章节级自动重试；仍失败章节会保留最优稿并标记，可 `resume` 重跑 |
| 改了 Key 不生效 | 确认 `select_llm current` 显示的 profile 与 `.env` 中变量名（默认 `AGNES_API_KEY`）一致，且 `start` 在改之后执行 |
| `runtime_gen.pid` 残留导致误判运行中 | 确认进程真死了后，手动删除 `novel-engine/runtime_gen.pid` 即可 |

---

## 8. 与 SLO 验证(mini_test_runner)的区别

- **本控制脚本** = 真正的长篇小说生产（`production_runner`），产出 `chapters/novel/chapter_*.txt`。
- 若只想跑 **质量/SLA 抽样验证**，用：
  ```
  cd src
  "D:\Program Files\Python312\python.exe" -m novel_engine.tests.mini_test_runner 10 --real
  ```
  结果在 `audit/mini_test_report.json`，再用 `python -m novel_engine.tests.slo_gate`（或直接 `aggregate_slo`）看 SLO 是否 PASS。
