# Novel-Engine：基于 Prime-Agent 底座的长篇小说简写生产与外部扩写引擎

<p align="center">
  <b>面向 3,800 章 / 1,600 万字网络小说的确定性状态简写（StateDB）+ 自包含外部 LLM 扩写包</b>
</p>

> **本仓库为 condensed-only**：原正篇多智能体正文流水线（agents/pipeline/quality/engine/adapters）
> 已移除。当前交付目标 = 3,800 章叙事简写（结构化事实层）+ 可独立使用的外部扩写包。

---

## 📖 项目简介

**Novel-Engine** 专门针对百万字级网络小说（玄幻、仙侠等）的长程一致性设计。
采用**确定性数据库（StateDB，SQLite 精确状态检索，无向量 RAG 漂移）+ 中纲章级意图注入
+ 逻辑验收（非文学评分）**架构，保证超长篇幅下故事逻辑、角色状态与世界观的高一致性。

生产流程：`规划（PLAN，含中纲 core_event 约束）→ 简写（DRAFT scenes/beats）→ 逻辑验收（REVIEW 8 维度）
→ 确定性 commit（hash 链 + timeline + facts）→ 导出自包含扩写包`。
扩写由项目外的人或 LLM 完成，扩写包不依赖本项目数据库、代码或 API。

---

## 🔥 核心特性与架构

### 1. StateDB：代码级精确状态检索（无向量 RAG 漂移）
* **拒绝概率性漂移**：放弃易产生语义偏移的传统 Vector RAG，采用 SQLite localized 精准查询。
* **状态与伏笔追踪**：管理角色属性、位置、物品、伏笔解开状态，commit 时做前值一致性校验。

### 2. 中纲章级意图注入（防结构模板化）
* `mid_outline_loader` 把人工审核过的 `mid_outline_*.json`（章级 core_event）注入 PLAN 提示词。
* PLAN 还带"章节类型系统"（冲突/揭示/行动/关系/日常），防止批次收敛到单一结构。

### 3. 确定性 commit 链
* 内容哈希 + 前章哈希 + timeline 单调校验，SQLite 事务内提交，可断点续跑、可 invalidate 回滚。

### 4. 自包含外部扩写包
* 每章导出：场景卡 + 全书相关设定 + 扩写/验收/修订提示词 + manifest，哈希可审计。
* 扩写 LLM 只需 `扩写提示词.md`，无需本项目依赖。

---

## 🛠️ 项目结构

本项目为 Monorepo 架构，Novel-Engine 技能包与底层 Prime-Agent 框架深度结合：

```
.
├── packages/
│   └── coding-agent/skills/novel-engine/    # 🌟 Novel-Engine 核心引擎与数据（condensed-only）
│       ├── SKILL.md                          # Skill 规范说明与 Agent API
│       ├── docs/                             # 简写生产与外部扩写等文档
│       └── src/novel_engine/                 # Novel-Engine Python 源码与数据根
│           ├── condensed/                    # 🌟 condensed 管线（runner/prompts/export/验收）
│           ├── core/                         # LLM 客户端、模型路由、执行预算
│           ├── scripts/                      # generate/audit_mid_outline
│           ├── bible/                        # 设定集（角色、世界观、势力、文风、意图、终局）
│           ├── config/planning/              # 卷纲、剧情图、中纲、决策规则
│           ├── config/foreshadow/            # 伏笔注册表
│           └── tests/                        # condensed 相关测试
├── packages/
│   ├── coding-agent/                         # Prime-Agent 编码智能体与 Skill 载体
│   ├── ai/                                   # 统一 LLM 客户端 API
│   ├── agent/                                # 核心 Agent 运行逻辑
│   └── tui/                                  # 终端交互界面
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
模型配置在 `src/novel_engine/config/llm_providers.json`（`active_profile` 决定当前供应商与模型；
当前为 `agnes_30_token`，Agnes-3.0-Flash token plan）。API 密钥写入 `src/novel_engine/.env`：
```env
AGNES_API_KEY=cpk-xxxx
AGNES_TOKEN_API_KEY=cpk-xxxx
```
或按需为对应 profile 的 `api_key_env` 设置同名环境变量。

---

## 🎮 运行方式与控制入口

condensed 模式统一入口为 `novel_engine.condensed`（`src` 目录在 PYTHONPATH 上）：

```bash
cd packages/coding-agent/skills/novel-engine

# 生成/续跑简写（自动从首个未提交章节继续；输出目录在源码外）
PYTHONPATH=src python -m novel_engine.condensed run --output /workspace/novel-condensed-3800 --end 3800

# 查看进度 / 校验连续链与扩写包
PYTHONPATH=src python -m novel_engine.condensed status --output /workspace/novel-condensed-3800
PYTHONPATH=src python -m novel_engine.condensed verify --output /workspace/novel-condensed-3800

# 导出扩写包 / 归档 / 合订
PYTHONPATH=src python -m novel_engine.condensed export --output /workspace/novel-condensed-3800
PYTHONPATH=src python -m novel_engine.condensed archive --output /workspace/novel-condensed-3800 --archive /workspace/expansion-packages.zip
```

放置 `输出目录/STOP` 可在下一章开始前停止；`invalidate` / `extend-budget` 等运维命令见
[简写生产与外部扩写](packages/coding-agent/skills/novel-engine/docs/简写生产与外部扩写.md)。

### Agent API（Python）
```python
import novel_engine

# 生成第 1 章简写（规划 → 撰写 → 逻辑验收 → commit → 导出扩写包）
result = await novel_engine.generate_condensed_chapter(1, output_root="/workspace/novel-condensed-3800")

# 查看生产状态
status = await novel_engine.condensed_status(output_root="/workspace/novel-condensed-3800")
```

中纲数据（章级意图约束）在 `src/novel_engine/config/planning/mid_outline_*.json`；
补生成/审计工具：`scripts/generate_mid_outline.py`、`scripts/audit_mid_outline.py`。

---

## 🧪 测试

```bash
cd packages/coding-agent/skills/novel-engine/src

# condensed 生产链路测试（含中纲注入、验收、commit、导出）
python -m pytest novel_engine/tests/test_condensed_production.py -v
```

生产前建议先小批量真实验证：`python -m novel_engine.condensed run --output ... --end 3`。

---

## 📄 文档指引

* **[简写生产与外部扩写](packages/coding-agent/skills/novel-engine/docs/简写生产与外部扩写.md)**：
  字数与预算、生成与质检流程、外部扩写包、恢复/审计/完成标准。
* **[中纲生成与审计](packages/coding-agent/skills/novel-engine/src/novel_engine/scripts/generate_mid_outline.py)**：
  `generate_mid_outline.py`（按 30 章批次生成中纲）与 `audit_mid_outline.py`（确定性规则审计）。

---

## 📜 许可证

本项目基于 [MIT License](LICENSE) 开源。
