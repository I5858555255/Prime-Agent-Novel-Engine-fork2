<p align="center">
  <a href="https://primeintellect.ai">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="assets/brand/prime-butterfly.svg">
      <img alt="Prime Intellect butterfly mark" src="assets/brand/prime-butterfly-black.svg" width="96">
    </picture>
  </a>
</p>

<h1 align="center">Prime Agent</h1>

<p align="center">
  <b>原生 RLM 终端代码与研究智能体 Harness（RLM-Native Terminal Coding & Research Harness）</b>
</p>

<p align="center">
  <a href="#-快速开始">快速开始</a> •
  <a href="#-项目架构">项目架构</a> •
  <a href="#-核心特性">核心特性</a> •
  <a href="#-交互模式与命令">交互模式与命令</a> •
  <a href="#-生态与扩展">生态与扩展</a> •
  <a href="#-开发者指南">开发者指南</a>
</p>

---

## 📖 项目简介

**Prime Agent** 是一个高性能、原生 RLM（Reinforcement Learning / Recursive Language Model）的终端终端代码与研究智能体 Harness。它以持久化 **IPython Kernel** 为单一强大工具，将 LLM 转化为具备代码执行、文件读写、数据分析和环境探索能力的全能 AI 智能体。

本项目采用 Monorepo 结构，涵盖从底层统一 LLM Provider API（支持 25+ 服务商）、有状态 Agent 运行时，到无闪烁终端 UI Primitives 及完整的 CLI 终端应用。

---

## ✨ 核心特性

- **🐍 原生 RLM IPython Kernel 工具**: 默认赋予模型强大的持久化 IPython 内核，模型可通过 Python 脚本直接操作文件系统、运行 bash 命令、解析数据与调试代码，无需零散繁琐的单一工具定义。
- **🌐 25+ LLM 服务商原生集成**: 涵盖 Anthropic (Claude)、OpenAI (GPT/Codex)、Google (Gemini/Vertex)、Amazon Bedrock、DeepSeek、GitHub Copilot、Mistral、Groq、xAI、OpenRouter、MiniMax、Kimi 等，支持自动模型发现与 Reasoning/Thinking 思考过程流式传输。
- **🌳 树状会话与分支管理 (`/tree`, `/fork`, `/clone`)**: 所有会话以 JSONL 格式自动持久化，支持在单文件内部进行树状分支切换、追溯、Fork 与克隆，永不丢失上下文发展路径。
- **🗜️ 智能上下文压减 (`/compact`)**: 支持主动与被动自动压缩长会话上下文，在超出模型上下文窗口时自动提取摘要并恢复执行。
- **🔌 原生 MCP (Model Context Protocol) 支持**: 通过 Python Skill 形式无缝接入 Linear、Notion 及自定义 MCP 服务，无需为 LLM 增加杂乱的工具定义。
- **🧩 丰富扩展机制 (Prompts, Skills, Extensions, Themes)**: 支持自定义 TypeScript 插件、Python 技能包、Markdown 提示词模板与主题，并内置强大的 `prime-agent package` 包管理器。
- **🖥️ 极致 TUI 交互体验**: 基于 `CSI 2026` 增量同步渲染技术，提供平滑无闪烁的终端界面，支持 IME（输入法）光标定位、富文本 Markdown 渲染、大文本粘贴标记与快捷键绑定。
- **🤖 自主模式 (Autonomous Mode)**: 支持 `--autonomous` 及 Command Gate 质量关卡门禁，实现无人值守的自动化迭代与验证。

---

## 🏗️ 项目架构

Prime Agent 采用 Monorepo（工作区）进行模块化开发，整体架构包含以下主要包：

```
prime-agent/
├── packages/
│   ├── coding-agent/    # Prime Agent CLI 主程序 (prime-agent)
│   ├── ai/              # 统一 LLM 工具包 (prime-agent-ai)
│   ├── agent/           # 有状态 Agent 运行时 (prime-agent-core)
│   └── tui/             # 终端 UI 组件库 (prime-agent-tui)
├── prime-agent-runtime/ # Python RLM 环境引导与内核依赖
├── scripts/             # 构建、打包与发布脚本
├── prime-agent.sh       # 本地开发与直接启动脚本
└── install.sh           # 独立一键安装脚本
```

### 包功能与 SDK 概览

| 包路径 | 模块名称 | 描述 |
| --- | --- | --- |
| `packages/coding-agent` | `prime-agent` | **终端智能体应用**：提供交互式命令行界面、命令处理、会话树管理、配置加载、更新检查与包管理。 |
| `packages/ai` | `prime-agent-ai` | **统一 LLM Toolkit**：抽象统一的 `stream()` / `complete()` 接口，自动模型发现、Token 与费用计算、思考流（Thinking/Reasoning）解析及跨 Provider 上下文平滑无缝 Hand-off。 |
| `packages/agent` | `prime-agent-core` | **Stateful Agent Runtime**：提供 Agent Loop 执行循环、并行/顺序工具执行控制、Steering（转向）与 Follow-up（追问）消息队列管理、工具 Preflight/Postprocessing 挂钩。 |
| `packages/tui` | `prime-agent-tui` | **Terminal UI Primitives**：实现 CSI 2026 原子同步渲染、差分更新（Differential Rendering）、Markdown 渲染、输入编辑器、Overlay 弹窗、IME 输入法定位与键绑定。 |

---

## 🚀 快速开始

### 1. 一键安装 (推荐)

使用官方安装脚本一键安装官方最新稳定版本：

```bash
curl -fsSL https://app.primeintellect.ai/prime-agent/install.sh | sh
```

安装最新 `beta` 测试版本：

```bash
curl -fsSL https://app.primeintellect.ai/prime-agent/install.sh | sh -s -- beta
```

### 2. 本地源码运行 (开发者)

克隆仓库并安装依赖：

```bash
git clone https://github.com/PrimeIntellect-ai/prime-agent.git
cd prime-agent
npm install
```

使用内置启动脚本即可直接启动开发版：

```bash
# 配置 API Key
export ANTHROPIC_API_KEY=sk-ant-...

# 启动 Prime Agent
./prime-agent.sh
```

### 3. 配置认证与模型选择

**方法 A：设置环境变量**
```bash
export ANTHROPIC_API_KEY=sk-ant-...
# 或
export OPENAI_API_KEY=sk-...
# 或
export PRIME_API_KEY=pr-...
```

**方法 B：交互式登录订阅**
在 Prime Agent 交互界面中输入：
```text
/login
```
选择对应 Provider（如 Anthropic Claude Pro/Max、OpenAI ChatGPT Plus/Pro、GitHub Copilot）进行 OAuth 登录。

首次启动时，Prime Agent 会自动构建并引导持久化 Python Kernel 虚拟环境 (`~/.prime/agent/kernel-venv`)。

---

## 💻 交互模式与快捷键

启动 `prime-agent` 后将进入交互式 TUI 界面：

### 常用斜杠命令 (Slash Commands)

| 命令 | 说明 |
| --- | --- |
| `/login` / `/logout` | 进行 OAuth 登录或注销服务提供商 |
| `/model` | 切换模型（支持搜索与快速选择，也可快捷键 `Ctrl+L`） |
| `/effort` | 设置模型推理/思考深度 (`off`, `minimal`, `low`, `medium`, `high`, `xhigh`) |
| `/tree` | 开启树状会话导航器，可视化跳转到历史任意节点并继续 |
| `/fork` | 从当前分支历史的指定消息处分叉创建新会话 |
| `/clone` | 复制当前分支至新会话文件 |
| `/compact [prompt]` | 手动压缩会话上下文，可附加自定义压缩指令 |
| `/usage` | 统计当前会话的 Token 消耗、费用与上下文占比 |
| `/btw <question>` | 展开边侧上下文临时提问，不污染主会话历史 |
| `/export [file]` | 将当前会话导出为 HTML 文件 |
| `/settings` | 打开系统设置（思考深度、主题、消息交付模式等） |
| `/reload` | 热加载快捷键、技能、提示词与扩展 |
| `/hotkeys` | 显示所有快捷键帮助列表 |

### 快捷键

- **`Ctrl+L`**: 打开模型选择面板
- **`Ctrl+P` / `Shift+Ctrl+P`**: 在已设定的作用域模型（Scoped Models）间前后循环切换
- **`Ctrl+O`**: 折叠 / 展开工具输出结果
- **`Ctrl+T`**: 折叠 / 展开思维链（Thinking Block）
- **`Shift+Enter` / `Alt+Enter`**: 编辑器换行
- **`Enter`**: 提交消息（Agent 运行中提交将作为 Steering 转向消息）
- **`Alt+Enter`**: 提交 Follow-up 追问消息（待 Agent 完成全部工作后递交）
- **`Escape`**: 清空输入框；连按两次可打开 `/tree` 会话树
- **`Ctrl+C`**: 打断当前 Agent 工作；空闲时按两次退出程序

---

## 🛠️ 配置与生态扩展

Prime Agent 提供了极高的自由度来适应不同项目与开发习惯：

### 1. 项目规则文件 (`AGENTS.md` / `CLAUDE.md`)
Prime Agent 启动时会自动自下而上查找并加载当前路径及父目录下的 `AGENTS.md`（或 `CLAUDE.md`）与 `~/.prime/agent/AGENTS.md`，用于定义项目规范、代码风格与常用指令。

### 2. 提示词模板 (Prompt Templates)
将常用 Markdown 提示词放置于 `~/.prime/agent/prompts/` 或 `.prime/agent/prompts/` 目录下，即可在终端中通过 `/templatename` 一键展开使用。

### 3. 技能 (Skills)
遵循 [Agent Skills 标准](https://agentskills.io)。将 `SKILL.md` 及可选的 Python 模块放置于 `~/.prime/agent/skills/` 中。Prime Agent 支持将 Python Skill 自动安装至 IPython 内核，模型可直接在内核中 `import` 调用。

### 4. 扩展插件 (Extensions)
使用 TypeScript 编写扩展，存放在 `~/.prime/agent/extensions/`。扩展可以自定义工具、注册命令、添加键盘快捷键、捕获事件或自定义 TUI 界面与 Overlay。

### 5. 包管理器 (Package Manager)
支持从 npm 或 Git 安装第三方 Extensions、Skills、Prompts 和 Themes：

```bash
prime-agent package install npm:@foo/prime-agent-tools
prime-agent package install git:github.com/user/repo
prime-agent package list
prime-agent package update
```

---

## 🤖 命令行参数与非交互模式

Prime Agent 支持命令行批处理及管道集成：

```bash
# 1. 打印模式（非交互运行并输出结果）
prime-agent -p "总结当前项目的架构设计"

# 2. 读取管道输入
cat README.md | prime-agent -p "总结这段文本"

# 3. 指定模型与思考等级
prime-agent --model anthropic/claude-3-7-sonnet --thinking high "解决此复杂算法问题"

# 4. JSON / RPC 模式（适用于程序集成与 SDK 通信）
prime-agent --mode json "列出文件"
prime-agent --mode rpc

# 5. 自主模式（带 Gate 门禁测试）
prime-agent --autonomous --autonomous-gate "npm test" "修复所有测试用例"
```

---

## 🔧 开发者指南

### 环境要求

- **Node.js**: `>= 20.6.0`
- **npm**: `>= 10.0.0`
- **Python**: `>= 3.10`（自动配置 `uv` 和 `ipykernel`）

### 构建与检查

在项目根目录下运行：

```bash
# 安装依赖
npm install

# 运行全项目代码检查与类型检查 (Biome + tsgo)
npm run check

# 构建所有 Packages
npm run build
```

### 运行单元测试

根据项目规范，运行测试请进入对应的 Package 根目录：

```bash
# 例如测试 packages/ai
cd packages/ai
npx vitest run

# 例如测试 packages/coding-agent
cd packages/coding-agent
npx vitest run
```

---

## 📄 开源协议与致谢

- 本项目基于 **[MIT License](LICENSE)** 许可协议开源。
- Prime Agent 最初 Hard-fork 自 Mario Zechner 开发的 [pi-mono](https://github.com/badlogic/pi-mono)，并在其优秀架构之上进行了独立的长期演进与独立发行，感谢原作者及开源社区的贡献。

---

<p align="center">
  由 <b>Prime Intellect</b> 团队开发与维护
</p>
