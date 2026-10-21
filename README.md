# Novel-Engine：基于 Prime-Agent 底座的长篇小说 AI 自动生成与状态机引擎

<p align="center">
  <b>面向百万字长篇网络小说的多智能体流水线、确定性世界状态机（StateDB）与自愈撰写系统</b>
</p>

---

## 📖 项目简介

**Novel-Engine** 是一个专门针对百万字级网络小说（玄幻、都市、科幻、仙侠等）设计的高质量、自动化 AI 创作与状态管理引擎。针对传统 LLM 写作中常见的“幻觉、设定遗忘、战力崩塌、伏笔丢失”等难题，Novel-Engine 采用**确定性数据库（StateDB）+ 多智能体流水线（Multi-Agent Assembly Line）+ 可回滚会话树（Durable Session Tree）**架构，保证在超长篇幅下故事逻辑、角色状态与世界观的高一致性。

Novel-Engine 以 **Prime-Agent** 作为底层 Runtime 框架，深度集成 RLM（Recursive Language Model）Python 内核、多模型 API 路由管理及终端/Web 控制台，实现了从“大纲策划 → 场景仿真 → 章节分发 → 撰写修饰 → 质量审查 → 自动自愈（Self-Healing）”的全流程自动化生产。

---

## 🔥 核心特性与架构

### 1. StateDB：代码级精确状态检索（无向量 RAG 漂移）
* **拒绝概率性漂移**：放弃易产生语义偏移和模糊记忆的传统 Vector RAG，采用基于 SQLite 的 localized 代码级精准查询。
* **状态与伏笔追踪**：实时管理角色属性、修炼境界、物品清单、地理位置及伏笔解开状态（Foreshadow Trackers），100% 保证全书百余万字战力与人物关系无缝衔接。

### 2. Multi-Agent 创作流水线
小说生产由多个专业化子智能体协同协作完成：
* 🎬 **Chapter Director（章节导演）**：拆解大纲与卷纲，生成 Scene-Unit 任务卡与剧情蓝图。
* 🌍 **World Simulator（世界推演）**：预推演章节环境变化、阵营势力消长与角色动向。
* ✍️ **Writer Agent（撰写智能体）**：结合角色 Bible、场景蓝图进行多场景细化撰写。
* 🔍 **Reviewer Agent（审稿智能体）**：多维度审计章节质量（文风、节奏、设定符合度、战力一致性），给出客观评分与修正意见。
* 🚑 **Self-Healing Loop（自愈机制）**：当审稿分数低于质量阈值时，自动触发增量缝补（Incremental Patcher）或局部重写，直到达到 PASS 阈值。

### 3. Durable Session Tree（持久化分支会话树）
* 类似 Git 分支机制，支持剧情创意的多分支探索（Branch Compare）。
* 当遭遇战力失控或质量未达标时，支持**自动级联回滚**，恢复世界状态与文本快照，确保数据无损与安全。

### 4. Incremental Patcher（场景增量缝补）
* 针对有瑕疵的章节，优先进行场景级别的定向“裁缝与缝补”（Stitch-and-Patch），而非盲目全章重写，极大节省 API Cost 并保留优质文本片段。

---

## 🛠️ 项目结构

本项目为 Monorepo 架构，Novel-Engine 技能包与底层 Prime-Agent 框架深度结合：

```
.
├── packages/
│   └── coding-agent/skills/novel-engine/    # 🌟 Novel-Engine 核心引擎与数据
│       ├── SKILL.md                          # Skill 规范说明与 Agent API
│       ├── docs/                             # 使用方法指南、编写指南、效率优化方案
│       ├── scripts/control/                  # 自动化控制脚本 (control.py, select_llm.py)
│       └── src/novel_engine/                 # Novel-Engine Python 源码与数据根
│           ├── agents/                       # Chapter Director, Simulator, Writer, Reviewer
│           ├── engine/                       # StateDB (db.py), Session Tree, Patcher
│           ├── pipeline/                     # 生产运行器, Web Dashboard, 状态初始化
│           ├── bible/                        # 设定集 (角色、世界观、势力)
│           ├── planning/                     # 卷纲、大纲与剧情图谱
│           ├── foreshadow/                   # 伏笔注册表与追踪器
│           ├── chapters/                     # 生成的最终小说章节输出 (chapter_*.txt)
│           └── config/                       # 质量阈值、成本配置与 LLM Profile
├── packages/
│   ├── coding-agent/                         # Prime-Agent 编码智能体与 Skill 载体
│   ├── ai/                                   # 统一 LLM 客户端 API (Anthropic, OpenAI, DeepSeek 等)
│   ├── agent/                                # 核心 Agent 运行逻辑与提示词编排
│   └── tui/                                  # 终端交互界面 (Ink/React)
└── prime-agent-runtime/                      # RLM Python 内核与 Jupyter Kernel 通讯
```

---

## 🚀 快速开始

### 1. 环境准备
* **Node.js** >= 18.0.0
* **Python** >= 3.10
* **Poetry** 或 `uv`（Python 包与虚拟环境管理）

### 2. 安装项目依赖
在项目根目录运行：
```bash
# 安装 Node.js Monorepo 依赖与编译 packages
npm install
npm run build
```

为 Novel-Engine 安装 Python 依赖：
```bash
cd packages/coding-agent/skills/novel-engine
poetry install
```

### 3. 配置 LLM 模型与 API Key
在 Novel-Engine 目录下配置 API 密钥（推荐使用 DeepSeek / SiliconFlow / Qwen 等高性价比大模型）：

```bash
cd packages/coding-agent/skills/novel-engine

# 使用 LLM 交互选择脚本配置供应商与 API Key
python -m novel_engine.scripts.control.select_llm
```
或编辑 `src/novel_engine/.env` 写入 API 密钥：
```env
SILICONFLOW_API_KEY=sk-xxxx
DEEPSEEK_API_KEY=sk-xxxx
```

---

## 🎮 运行方式与控制入口

Novel-Engine 提供了多种灵活的运行方式：

### 1. 使用交互式控制脚本（推荐）
快捷管理后台、生成控制与状态查看：
```bash
# 启动控制主脚本
python -m novel_engine.scripts.control.control

# 常见控制命令：
# - auto [count] : 连续自动生成指定数量章节
# - resume       : 从断点恢复继续生成
# - status       : 查看当前生成进度、StateDB 状态与日志
# - clean        : 清理缓存与重置运行状态
```

### 2. 通过 Prime-Agent 命令行 / TUI 运行
通过 Prime-Agent 的 RLM Python Kernel 直接驱动 `novel_engine` Skill：

```bash
# 启动 Prime-Agent 并载入 novel-engine 技能
./prime-agent.sh --skill novel-engine
```

在持久化 IPython Kernel 中直接调用 API：
```python
# 1. 初始化世界状态 (可传入 reset=True 进行重置)
await novel_engine.init_state()

# 2. 生成第 1 章 (包含推演 → 撰写 → 审稿 → 自愈 → 提交全流程)
result = await novel_engine.generate_chapter(1)
print(result) # -> {"chapter": 1, "success": True, "score": 85}

# 3. 对已有章节进行独立审查
await novel_engine.review_chapter(1)

# 4. 评估多分支剧情，选择最高分版本提交
await novel_engine.branch_compare(1, variants=2)
```

### 3. Web 可视化仪表盘 (Web Dashboard)
可视化实时查看生成进度、角色状态、审稿分数曲线及成本明细：
```bash
cd packages/coding-agent/skills/novel-engine/src
python -m novel_engine.pipeline.web_dashboard
```
默认在浏览器访问 `http://localhost:8501` 即可进入控制台。

---

## 🧪 单元测试与仿真验证

在生产运行前，可以使用系统内置的仿真测试（Mock Mode）验证流水线与逻辑：

```bash
cd packages/coding-agent/skills/novel-engine/src

# 1. 运行核心库单元测试
poetry run python -m unittest novel_engine.tests.test_novel_engine -v

# 2. 运行 10 章节 Mock 编译测试 (无需消耗真实 API 额度)
poetry run python -m novel_engine.tests.mini_test_runner

# 3. 带真实 API 调用的迷你生产测试
poetry run python -m novel_engine.tests.mini_test_runner --real
```

---

## 📄 扩展与文档指引

* **[使用方法指南](packages/coding-agent/skills/novel-engine/docs/使用方法指南.md)**：更详细的控制台命令、参数配置与断点恢复说明。
* **[小说编写与设定指南](packages/coding-agent/skills/novel-engine/docs/编写指南.md)**：如何撰写 Bible（设定集）、卷纲（Outline）与大纲规划。
* **[效率与 Cost 优化方案](packages/coding-agent/skills/novel-engine/docs/效率优化方案.md)**：Token 消耗分析、模型分层路由与并发加速策略。

---

## 📜 许可证

本项目基于 [MIT License](LICENSE) 开源。
