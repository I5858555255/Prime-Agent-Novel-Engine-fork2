# Prime-Agent Novel-Engine

基于 [Prime-Agent](https://github.com/PrimeIntellect-ai/prime-agent) 底座的长篇小说 AI 生产引擎。仓库是一个 Monorepo：上层是 TypeScript 写的 Prime-Agent 终端智能体，Novel-Engine 以 **Python Skill** 的形式放在其中，也可以脱离 Prime-Agent 单独运行。

> 本文档依据仓库当前代码重写。文中标注"已验证"的命令，已在干净环境里实际运行过。

---

## 一、这个项目实际是什么

| 部分 | 语言 | 位置 | 作用 |
|---|---|---|---|
| Prime-Agent | TypeScript | `packages/{ai,agent,coding-agent,tui}` | 终端编码智能体（上游 Prime Intellect，源自 pi-mono 的硬分叉），内置 TUI、多模型 API、Skill 机制 |
| RLM 运行时 | Python | `prime-agent-runtime/` | 常驻 IPython Kernel 的垫片，Skill 在其中被预加载并 `await` 调用 |
| **Novel-Engine** | **Python** | `packages/coding-agent/skills/novel-engine/` | 小说生产引擎，**本项目的核心** |

仓库自带一部具体小说的设定库：《吸氧证道：阴阳逆碳，我以人道定仙天》，规划 **3,800 章**、约 **1,600 万汉字**（`src/novel_engine/bible/`、`config/planning/`）。它不是"任意题材通用引擎"，换书需要替换这些设定文件。

### 两种生产模式

| | **简写模式（condensed，默认）** | 正文模式（prose，旧流水线） |
|---|---|---|
| 产出 | 每章 900～1,400 字的事件链简写、场景卡，以及可在项目外使用的**扩写提示词包** | 直接生成完整章节正文 |
| 正文谁写 | 项目外的人或其他 LLM 按提示词扩写（目标 4,000～5,000 汉字/章） | 本项目内的 Writer 智能体 |
| 质检 | 代码校验 + 独立 LLM 逻辑审查（设定、因果、知识边界、连续性、伏笔） | 文学评分（发布线 88）+ 大量确定性门控 |
| 数据 | 输出目录里的 SQLite（`manuscript.sqlite3`） | `src/novel_engine/runtime/state.db` 等 |
| 入口 | `scripts/condensed.py` / `python -m novel_engine.condensed` | `production_runner`、控制脚本、Web 面板、Agent API |

两种模式数据互相独立，不能混用。SKILL.md 约定：**除非明确要求写正文，一律走简写模式**。

---

## 二、仓库结构

```
.
├── packages/
│   ├── ai/                  # 统一 LLM 客户端（含 MCP、各家 provider）
│   ├── agent/               # Agent 运行循环
│   ├── tui/                 # 终端 UI
│   └── coding-agent/
│       ├── src/             # CLI 与交互模式（cli.ts）
│       └── skills/
│           ├── novel-engine/    # ← 本项目核心
│           └── ...              # 其他内置 Skill（goal、websearch、notion 等）
├── prime-agent-runtime/     # Python：RLM kernel 垫片（rlm/harness.py、skill.py、mcp_base.py）
├── prime-agent.sh           # 从源码启动 Prime-Agent
├── install.sh               # Prime-Agent 安装脚本
├── AGENTS.md                # 对 Prime-Agent 本体（TS 部分）的开发规范
├── OPTIMIZATION_LOG.md      # 正文模式早期的问题与修复记录
└── docs/                    # 简写 vs 旧 prose 对照记录、生成与质检故障复盘
```

### novel-engine 目录

```
packages/coding-agent/skills/novel-engine/
├── SKILL.md                 # Skill 说明（Agent 读取）
├── pyproject.toml           # Poetry；Python ^3.12
├── scripts/
│   ├── condensed.py         # 简写模式跨平台入口
│   ├── check_static.py
│   ├── start.sh             # 正文模式启动脚本（Linux/macOS）
│   └── control/             # 正文模式控制脚本，仅 Windows（见"已知限制"）
├── docs/                    # 使用指南、编写指南、简写生产说明等
└── src/novel_engine/        # Python 包，同时也是数据根
    ├── condensed/           # 简写模式（runner、contracts、store、export、accept_check、expand_run…）
    ├── agents/              # chapter_director、world_simulator、writer_agent、reviewer_agent、literary_pass 等
    ├── engine/              # db.py（StateDB）、session.py（会话树）、patcher.py（增量缝补）
    ├── pipeline/            # pipeline_orchestrator、production_runner、supervisor、gates、web_dashboard…
    ├── quality/             # 约 50 个确定性质量门（重复、泄漏、时间线、命名一致性、标点…）
    ├── core/                # llm_client、model_router、checkpoint、state_machine、memory_manager…
    ├── adapters/            # RLM / 观测适配
    ├── bible/               # 设定集：世界观、人物、文风、作者意图、结局、决策规则
    ├── config/              # llm_providers.json、质量阈值、中纲（mid_outline）、里程碑、伏笔、模拟规则
    ├── agent_api.py         # 供 Prime-Agent Kernel 调用的异步 API
    ├── scripts/             # 审计、中纲生成等辅助脚本
    ├── tests/               # 测试
    ├── timeline/、tools/
    └── （运行时生成，已 gitignore）chapters/、runtime/、audit/、memory/、cache/
```

---

## 三、环境要求

| 用途 | 要求 |
|---|---|
| Novel-Engine（Python） | **Python ≥ 3.12**；依赖 `httpx`、`jieba`、`json-repair`；开发依赖 `pytest`、`pytest-asyncio` |
| Prime-Agent（可选） | Node.js **≥ 22.8.0**（根 `package.json` 的 `engines`）；npm ≥ 11.10 才会执行 7 天依赖冷却 |
| RLM 运行时（可选） | Python ≥ 3.10；`ipykernel`、`nest-asyncio`、`tyro` |

> 只想跑简写模式或测试，**不需要 Node.js**。

---

## 四、快速开始：简写模式

### 1. 安装依赖

```bash
cd packages/coding-agent/skills/novel-engine
poetry install
# 或者不用 Poetry：
pip install httpx jieba json-repair
```

### 2. 配置模型与密钥

模型由 `src/novel_engine/config/llm_providers.json` 决定，当前 `active_profile` 为 `agnes_30_token`（Agnes 的 `agnes-3.0-flash`，所有阶段同一模型）。各 profile 需要的环境变量：

| profile | 环境变量 |
|---|---|
| `agnes_30_token`（当前默认） | `AGNES_TOKEN_API_KEY` |
| `agnes`、`agnes_30` | `AGNES_API_KEY` |
| `siliconflow`（DeepSeek-V3.2） | `LLM_API_KEY` |

最稳妥的做法是直接在 shell 里导出：

```bash
export AGNES_TOKEN_API_KEY=sk-xxxx
```

也可以写 `.env`，但**不同入口读取的位置不一致**（见"已知限制"）。简写模式读取的是 **`src/.env`**。密钥不要写进 `llm_providers.json`，该文件只放环境变量名。

切换供应商：把 `active_profile` 改成想用的 profile；新增供应商就复制一个 profile 并填写 `base_url`、`api_key_env`、`phases`。

### 3. 运行

在 `novel-engine` 目录下执行，输出目录必须与源码目录分开：

```bash
# 先小批量验证连通性
python scripts/condensed.py run --output /path/to/my-manuscript --end 3

# 全书（同一输出目录重跑会自动续接，已提交章节不会重复请求模型）
python scripts/condensed.py run --output /path/to/my-manuscript --end 3800

# 查看进度（已验证：空目录下返回 committed_chapters=0、complete=false）
python scripts/condensed.py status --output /path/to/my-manuscript

python scripts/condensed.py verify  --output /path/to/my-manuscript   # 校验哈希链与全部文件
python scripts/condensed.py compile --output /path/to/my-manuscript   # 生成合订本与章节索引
```

等价写法：`PYTHONPATH=src python -m novel_engine.condensed <命令> ...`；安装后也可使用 `novel-condensed`。

在输出目录里放一个名为 `STOP` 的文件，会在**下一章开始前**暂停；删除后再运行即可继续。命令失败会返回非零退出码。

### 4. 输出目录

```
my-manuscript/
├── manuscript.sqlite3        # 正式提交依据（章节、评审、状态变化、用量、预算）
├── expansion/chapter_0001/   # 每章扩写包：简写、场景卡、设定、上下文、文风指南、
│                             #   扩写/验收/修订提示词、哈希清单、count_han.py
├── manuscript/               # 全部章节简写.md（仅含已提交章节）、章节索引.csv
├── status.json               # 实际完成数、请求数、token；未完成时 complete=false
├── audit/、drafts/           # 质检、修复、原始响应、失败诊断
├── canon.json、project.json  # 设定快照与项目标识
└── production.log
```

打开某章的 `扩写提示词.md` 即可交给外部 LLM 使用，不依赖本项目的代码、数据库或 API。

### 5. 打包扩写包

```bash
# 全部已提交章节
python scripts/condensed.py archive --output /path/to/my-manuscript --archive /path/to/expansion.zip

# 按范围打包，设定/文风/里程碑在 ZIP 内只存一份
python scripts/condensed.py archive --output /path/to/my-manuscript --compact \
    --from-chapter 1 --end 100 --archive /path/to/expansion-0001-0100.zip
```

### 6. 其他子命令

| 命令 | 作用 |
|---|---|
| `export` | 重建已提交章节的扩写包（不重写章节） |
| `invalidate --from-chapter N --reason ...` | 发现已提交简写有缺陷时，归档原数据并撤销该章及后续依赖 |
| `extend-budget --operation chapter_N --extra-calls N --extra-seconds N --reason ...` | 显式追加某章的请求/时间额度（每项最多额外 16 次、1800 秒） |
| `accept-check --from-chapter N --prose 正文.txt` | 对外部扩写成稿做程序验收（汉字数 4,000～5,000 等） |
| `dual-check --from-chapter N --prose 正文.txt` | 程序检查 + LLM 双重验收 |
| `expand --from-chapter N [--prose 初稿] [--max-rounds 5]` | 调用 `polish` 阶段模型做迭代扩写 |
| `mark-verified --from-chapter N [--llm-report 文件]` | 依据验收报告写入扩写验证状态 |

---

## 五、简写模式的生成与质检流程

1. 用 Bible 和专用主线里程碑（`config/condensed_milestones.json`，49 个节点）规划临近十章。
2. 检查规划编号、覆盖、预算、重复事件和主线节点；规划与当章简写合并做独立逻辑审查。
3. 依据已提交前章、相关人物状态和近三十章伏笔活动生成简写与场景卡（每章 2～6 个场景）。
4. 代码检查事件引用、覆盖、场景预算、时间顺序、状态前值与证据。
5. 独立 LLM 审查设定、因果、知识、连续性、覆盖、伏笔、扩写充分性和章节边界；审查需逐事件、逐里程碑给出证据。
6. 明确问题自动修复，默认最多两轮；必要时仅重规划当前章一次。失败保留诊断，**不推进依赖章节**。
7. 在一个 SQLite 事务内提交章节、评审和状态变化，再导出扩写包。

几条硬约束：

- **预算**：每项规划或章节操作默认共享 **16 次真实请求、900 秒活动时间**；重试同样计费，重启不重置已耗额度。
- **字数**：全书扩写预算精确分配为每章 4,210 或 4,211 汉字，合计 16,000,000；汉字计数只含 CJK 汉字，不含标点、数字、英文。
- **完成标准**：只有 3,800 章全部提交、扩写包全部生成、哈希链与文件校验通过，`status.json` 才会写 `complete=true`。外部成稿始终标 `external_expansion_verified=false`，不会与简写完成混淆。
- 未知费用不会被当作零；没有价格配置时不虚报货币成本。

详见 [`docs/简写生产与外部扩写.md`](packages/coding-agent/skills/novel-engine/docs/简写生产与外部扩写.md)。

---

## 六、正文模式（旧流水线，仍保留）

仅在明确需要由本项目直接写正文时使用。

### 核心组件

- **多智能体流水线**：World Simulator（世界推演）→ Chapter Director（章节导演，生成任务卡/场景蓝图）→ Writer（分场景撰写）→ Reviewer（多票评分）→ 修复循环 → 确定性门控 → 发布决策。
- **StateDB**（`engine/db.py`）：SQLite 精确状态库，含 `characters`、`factions`、`foreshadows`、`clue_plans`、`relationships`、`concept_unlocks` 等表；另提供轻量 `hybrid_search`（token 重叠）补充精确匹配的盲区。没有向量数据库。
- **SessionTree**（`engine/session.py`）：类 Git 的提交节点树，支持 `fork_branch`、`rollback_to_node`、`prune_snapshots`。
- **IncrementalPatcher**（`engine/patcher.py`）：按场景号提取并替换文本（`extract_scene`、`apply_scene_patch`、`apply_regex_patch`），避免整章重写。
- **质量门**（`quality/`）：重复、导演话语泄漏、拉丁字符泄漏、命名一致性、时间线、伏笔、标点、场景推进等确定性检查。发布决策：≥88 达标；85～87.9 进灰度带；<85 作为草稿。

### 运行入口

```bash
cd packages/coding-agent/skills/novel-engine/src

# 全量生产（不加 --real 使用 Mock LLM）
python -m novel_engine.pipeline.production_runner --real 3000 1

# Web 面板，默认 http://localhost:8080
python -m novel_engine.pipeline.web_dashboard --host 127.0.0.1 --port 8080
```

`scripts/start.sh` 对以上命令做了封装（`dashboard`、`status`、`test <n> [--real]`、`production <n> [--real]`、`resume <n>`、`stop`）。`scripts/control/` 下的 `.bat` 与 `control.py` 面向 Windows，其中 `auto` 使用 `pipeline.supervisor` 看门狗：HALT 后按 300/600/900 秒冷却重试，同一章连续 HALT 超过 3 次熔断。

### Agent API（在 Prime-Agent 的 IPython Kernel 中）

```python
await novel_engine.init_state(reset=False)
await novel_engine.generate_chapter(1)          # 正文模式
await novel_engine.review_chapter(1)
await novel_engine.self_heal(1)
await novel_engine.branch_compare(1, variants=2)
await novel_engine.commit(1)

# 简写模式
await novel_engine.generate_condensed_chapter(1, output_root="/path/to/my-manuscript")
await novel_engine.condensed_status(output_root="/path/to/my-manuscript")

# 子智能体并发（需先注入 Kernel 句柄）
await novel_engine.set_kernel_handles(rlm=rlm, agent_message=agent_message)
await novel_engine.generate_chapter_with_subagents(1)
```

`generate_chapter`、`self_heal`、`branch_compare` 支持 `use_mock=True`，可无 API 冒烟。

---

## 七、通过 Prime-Agent 使用（可选）

```bash
# 仓库根目录
npm install
npm run build

./prime-agent.sh --skill novel-engine
```

`--skill` 参数在 `packages/coding-agent/src/cli/args.ts` 中确认存在。这一节的完整流程本次**未实际运行**。AGENTS.md 里要求开发 Prime-Agent 本体时不要擅自跑 `npm run build` / `npm test`，那是针对贡献者的规则，使用者需要先构建。

---

## 八、测试

```bash
cd packages/coding-agent/skills/novel-engine/src

# 简写模式测试（已验证：55 passed）
python -m pytest novel_engine/tests/test_condensed_production.py -q

# 核心库单元测试（已验证：5 tests OK）
python -m unittest novel_engine.tests.test_novel_engine

# 全量测试
python -m pytest novel_engine/tests/ -q

# 正文模式 Mock 小批量；加 --real 才会消耗真实 API
python -m novel_engine.tests.mini_test_runner
```

> `mini_test_runner` 不支持 `--help`，未知参数会直接报错。`docs/runbook.md` 中"应为 16 passed"的说法已过期，测试数量远大于此。

---

## 九、已知限制与文档差异

以下是读代码时发现的实际问题，使用前请留意：

1. **`.env` 位置不统一**：`condensed` 和 `core/llm_client.py` 读取 `src/.env`；`select_llm.py` 与 `supervisor.py` 使用 `src/novel_engine/.env`；`agent_api.py` 两处都找。混用入口时，**优先用 shell 环境变量**，环境变量不会被 `.env` 覆盖。
2. **`scripts/control/control.py` 仅限 Windows**：使用 `tasklist`，并把 Python 路径写死为 `D:\Program Files\Python312\python.exe`。`select_llm.py` 是跨平台的，但应直接运行 `python scripts/control/select_llm.py <current|list|switch|set-key|use>`，不存在 `novel_engine.scripts.control` 这个模块。
3. **以下文档含过期内容**，以本文和代码为准：
   - 根目录旧 README：Python ≥ 3.10、Node ≥ 18、`SILICONFLOW_API_KEY`/`DEEPSEEK_API_KEY`、Web 面板端口 8501、`python -m novel_engine.scripts.control.control` 等均不正确。
   - `novel-engine/SKILL.md` 的目录树：列出的 `generate.bat`、`start_dashboard.bat`、`gui_console.py`、`engine/subagent.py`、`simulation/`、`planning/` 等路径并不存在（规划与模拟配置实际在 `config/planning/`、`config/simulation/`）。
   - `docs/使用方法指南.md`：引用不存在的 `runtime_config.json` 配置流程与本机 `D:/` 路径。
   - `docs/runbook.md`：指向 SiliconFlow + `Qwen3.5-4B`，与当前 `llm_providers.json` 不符。
   - `scripts/start.sh` 提示"Python 3.8+"，实际需要 3.12+。
4. **正文模式的已知质量问题**（见 `docs/superpowers/project-status-issues-20261012.md`）：三评极差大于 15 触发人工复核、导演腔泄漏"检测到但消解不掉"、修复循环难以解决剧情层问题。这些是旧流水线的原因之一，也是简写模式独立出来的背景。
5. 简写模式不保证外部模型扩写无误，也不保证固定的成本节省比例；应通过真实连续生成和代表章节的试扩写分别验证。

---

## 十、文档索引

| 文档 | 内容 |
|---|---|
| [`docs/简写生产与外部扩写.md`](packages/coding-agent/skills/novel-engine/docs/简写生产与外部扩写.md) | 简写模式的完整规则、预算、恢复与完成标准 |
| [`docs/编写指南.md`](packages/coding-agent/skills/novel-engine/docs/编写指南.md) | 如何撰写 Bible、卷纲与大纲 |
| [`docs/效率优化方案.md`](packages/coding-agent/skills/novel-engine/docs/效率优化方案.md)、[`docs/流水线性能优化与基准.md`](packages/coding-agent/skills/novel-engine/docs/流水线性能优化与基准.md) | 耗时与成本分析 |
| [`docs/superpowers/`](packages/coding-agent/skills/novel-engine/docs/superpowers) | 质量待办、阶段性项目状态、编排边界 |
| [`OPTIMIZATION_LOG.md`](OPTIMIZATION_LOG.md) | 正文模式早期优化轮次记录 |
| [`packages/coding-agent/README.md`](packages/coding-agent/README.md) | Prime-Agent CLI 使用手册 |

---

## 十一、许可证

[MIT License](LICENSE)。Prime-Agent 部分版权归 Mario Zechner 与 Prime Intellect 所有。
