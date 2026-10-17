# PA-ARCH-RESEARCH-AND-REFACTOR-01 — 01-REFERENCE-RESEARCH

**Date:** 2026-10-17  
**Evidence Level:** DBOS=直接源码（完整，11,225行Python）；其余4项目=GitHub API + README（受限，明确标记）

---

## 1. OpenHands — Agent运行时

| 项目 | 内容 |
|------|------|
| 仓库 | https://github.com/OpenHands/OpenHands |
| 分支 | main（最新） |
| 源码访问 | **受限** — clone超时，API返回空，仅README可访问 |
| 证据等级 | ⚠️ README级，无法验证源码细节 |

### 关键发现（据README）
- 定位：「self-hosted developer control center for coding agents」
- 架构：多backend统一（Claude Code、Codex、Gemini CLI等），ACP协议
- Agent Canvas：自动化任务编排 + 持续运行

### 与小说引擎的对应关系

| OpenHands 能力 | 小说引擎现状 | 差距 |
|---|---|---|
| 多 backend 统一接入 | 单一LLM调用链（agnes-3.0-flash） | 无对比价值 |
| 持续运行自动化流程 | PipelineOrchestrator同步批处理 | 架构理念可借鉴 |
| ACP协议/agent通信 | 无标准协议 | 不适用 |

### 核心问题回答：如何让Prime-Agent持续执行"规划→工具→观察→调整"？

OpenHands的模式是**事件驱动循环**：agent接收任务→调用工具→观察结果→决定下一步。当前小说引擎的模式是**固定步骤序列**：director→synopsis→write→review→commit，无自主决策回路。

**可借鉴点：** 工具结果触发后续决策的逻辑（如审稿后是否修复、修复后是否重审）。当前`FixLoopAgent`已部分实现此模式。

**不适合直接采用的部分：** 代码执行环境、文件操作、terminal交互——与小说创作领域不匹配。

**决策：`REJECT`（引入）** / `ADAPT`（吸收事件驱动模式到`FixLoopAgent`）

---

## 2. LangGraph — 任务编排与状态机

| 项目 | 内容 |
|------|------|
| 仓库 | https://github.com/langchain-ai/langgraph |
| 分支 | main |
| 源码访问 | **受限** — clone超时，API可读目录列表，部分文件404 |
| 证据等级 | ⚠️ README + 目录结构 |

### 关键发现（据README/AGENTS.md）
- 「low-level orchestration framework for building stateful, long-running agents」
- 核心抽象：`StateGraph`（节点+边+条件分支）
- 持久化：`MemorySaver` / `BaseCheckpointSaver`
- Human-in-the-loop：`interrupt()`机制
- 并发子图：`Subgraph`支持

### 必须回答的问题

**Q1: 当前小说生产链路是否已具备类似状态图能力？**
部分具备。`state_machine.py`定义了`ChapterPhase`枚举和转换规则，`PipelineOrchestrator.generate_single_chapter()`按固定顺序调用`_stage_*`方法。但这是硬编码顺序，没有条件边或动态路由。

**Q2: 现有编排器能否通过有限修改支持条件分支与循环修复？**
可以。`_run_fix_loop_iteration()`已经是循环结构的雏形（max 2 iter），`_force_triple24`/`_green24`标志已控制评审轮数。只需将决策从配置阈值移到Agent可调用的逻辑。

**Q3: 引入LangGraph是否有实际收益？**
当前流水线是**线性**的（6个确定阶段），不需要条件分支。收益主要体现在：
- Human-in-the-loop支持（当前无）
- 可视化编排（当前无）
但这些需求不紧迫。

**Q4: 如果不引入，如何在现有架构内实现必要能力？**
已通过`NovelRLMAdapter`封装现有管线为「伪图」结构：每个`_stage_*`视为节点，返回值视为状态。`FixLoopAgent`提供条件分支决策。无需引入框架本身。

**决策：`REJECT`（引入框架）/ `ADAPT`（吸收节点-状态模式）**

---

## 3. PydanticAI — 结构化Agent与工具契约

| 项目 | 内容 |
|------|------|
| 仓库 | https://github.com/pydantic/pydantic-ai |
| 版本 | 最新main |
| 源码访问 | **部分** — tools.py可读（~80行头部），result.py部分可读 |
| 证据等级 | ⚠️ 部分源码 + README |

### 关键发现（据tools.py + result.py）
- **Tool定义**：`@agent.tool`装饰器自动从函数签名生成JSON Schema
- **RunContext**：依赖注入，工具函数可接收运行时上下文
- **ArgsValidatorFunc**：参数预校验钩子（失败时让模型重新生成参数）
- **ToolPrepareFunc**：动态准备工具定义
- **OutputSchema**：Pydantic model结构化输出验证
- **ToolResult/ToolOutput**：标准化工具返回

### 优先研究的校验数据结构

| 候选结构 | 小说引擎现状 | PydanticAI启发 |
|---|---|---|
| 章节任务计划 | `task_card: dict`（TypedDict） | 可加参数校验 |
| 场景任务规格 | `scene_blueprint: dict` | 可加类型约束 |
| 任务依赖关系 | `_group_independent_scenes()`静态分组 | 可引入显式依赖图 |
| 人物状态变更 | `world_state: dict`（StateDB查询） | 可加变更追踪 |
| 连续性问题 | `issues: list[dict]` | 可定义结构化schema |
| 审稿结果 | `score: float, verdict: str, issues: list` | 已有，可加校验 |
| 定向修复计划 | `FixRecommendation`（本次新建） | 可加运行时校验 |
| 最终质量门禁结果 | `_deterministic_quality_gate()`结果 | 已有 |

### 决策：程序校验 vs Agent判断 vs 质量规则

| 内容 | 校验方式 | 理由 |
|------|----------|------|
| 章节任务计划必填字段 | **程序校验**（Pydantic/TypedDict） | 格式错误应立即报错 |
| 场景任务规格完整性 | **程序校验** | 缺失beats会导致生成问题 |
| 任务依赖关系合法性 | **程序校验** | 循环依赖会死锁 |
| 人物状态变更合理性 | **Agent判断** | 需要语义理解 |
| 连续性问题检测 | **程序校验**（确定性gate） | 已有`continuity_gate.py` |
| 审稿结果可信度 | **Agent判断**（多评审员聚合） | 单一模型评分不可靠 |
| 定向修复计划可行性 | **Agent判断** | 需要上下文理解 |
| 最终质量门禁通过/失败 | **程序校验**（确定性gate） | 必须是客观标准 |

**决策：`ADAPT`** — 不引入PydanticAI框架，但借鉴其工具契约模式，在`domain_tools.py`中添加参数校验。

---

## 4. DBOS Transact-Py — 持久化执行与任务恢复

| 项目 | 内容 |
|------|------|
| 仓库 | https://github.com/dbos-inc/dbos-transact-py |
| 版本 | v0.5.x（commit 61426cb，2024-10） |
| 源码访问 | ✅ **完整** — 本地clone可用，11,225行Python |
| 证据等级 | ✅ 直接源码 |

### 关键架构洞察

#### 4.1 Workflow作为顶层原子
```python
@workflow
def my_workflow(arg1, arg2):
    result = my_step(arg1)           # step 调用
    child_wf = workflow(my_other)(result)  # 子workflow
```
- `@workflow`装饰器自动创建`workflow_uuid`
- 每次调用有唯一`workflow_uuid` + `function_id`
- 恢复时按相同UUID重放，**跳过已记录步骤的输出**

#### 4.2 Step级幂等检查点
```python
class StepStatus:
    step_id: int
    current_attempt: Optional[int]
    max_attempts: Optional[int]
```
- 每个step执行前记录`started_at_epoch_ms`
- 成功后写入`operation_outputs`表（output + serialization）
- 失败写入`error`字段（序列化异常）
- **replay逻辑**：查询`operation_outputs`，已有记录则直接返回缓存结果

#### 4.3 Workflow状态机
```python
class WorkflowStatusString(Enum):
    PENDING → ENQUEUED → DELAYED → SUCCESS / ERROR
           → CANCELLED / MAX_RECOVERY_ATTEMPTS_EXCEEDED
```
- 持久化到`workflow_status`表
- 恢复线程扫描`PENDING/ENQUEUED`状态触发重执行
- `rewind_workflow(start_step)`可回滚到指定步骤

#### 4.4 序列化模型
- `DBOSPortableJSON`：通用JSON序列化（支持datetime/date/Decimal）
- `DefaultSerializer`：pickle+base64（Python专属）
- 序列化失败时**不记录checkpoint**（让replay重新执行而非卡住）

### 长任务恢复关键问题

| 场景 | DBOS方案 | 小说引擎现状 | 差距 |
|------|----------|-------------|------|
| 场景完成但后续中断 | 保存step checkpoint | 仅保存整章checkpoint | **关键缺口** |
| 审稿完成但提交失败 | replay从最后成功step开始 | 整章重跑 | 高成本浪费 |
| 模型调用完成但结果未保存 | operation_outputs缓存 | 无缓存，重试必重调LLM | **关键缺口** |
| 重复提交 | workflow_id去重 | 仅hash去重整章 | 粒度不够 |
| 恢复执行重复写入正式稿 | 状态机控制 | checkpoint幂等保护 | 已处理 |

### 决策：`ADAPT`（吸收核心模式）

DBOS的价值不在于数据库层（需要Postgres），而在于**操作级幂等重放**模式。具体可借鉴：
1. 为每个`_stage_*`方法添加「结果缓存」逻辑（key: `chapter_num + stage_name`）
2. 恢复时先查缓存，命中则跳过，未命中才执行
3. 这比DBOS完整方案轻量得多，但解决「昂贵LLM调用重复执行」的核心问题

---

## 5. AutoGen — 多Agent协作

| 项目 | 内容 |
|------|------|
| 仓库 | https://github.com/microsoft/autogen |
| 状态 | ⚠️ **已归档（archived）**，不再维护 |
| 源码访问 | 受限 |
| 证据等级 | ⚠️ README级 |

### 关键发现（据README）
- 多Agent协作框架（GroupChat、GroupChatManager）
- Agent角色：AssistantAgent、UserProxyAgent
- 消息协议：`ChatResult`、`Conversation`
- 终止条件：手动指定、LLM判断、轮次限制

### 与小说引擎的对应关系

| AutoGen 能力 | 小说引擎现状 | 评估 |
|---|---|---|
| 多Agent角色分工 | Director+Writer+Reviewer（已有） | ✅ 已实现 |
| 对话式协作 | PipelineOrchestrator单向流水线 | 不适用（创作不需要对话） |
| 终止条件 | 无自动终止（固定流程） | 当前够用 |

### 核心问题回答：需要多个独立Agent还是一个Agent通过工具完成任务？

**一个Prime-Agent通过不同工具、任务角色和结构化上下文即可完成。** 理由：
1. 小说生产是**线性流水线**而非**对话式博弈**
2. Director/Writer/Reviewer的角色已清晰划分，不需要动态协商
3. AutoGen已归档，引入维护风险

**决策：`REJECT`**

---

## 6. 综合对照表

| 研究能力 | 参考项目 | 当前是否已有 | 当前缺陷证据 | 推荐处理方式 |
|---|---|---|---|---|
| Agent执行与工具反馈 | OpenHands | ⚠️ 部分（固定流程） | 无自主规划能力，仅单次调用 | REJECT（领域不匹配）/ ADAPT（事件驱动模式） |
| 条件任务编排（状态图） | LangGraph | ⚠️ 线性流程，无条件分支 | PipelineOrchestrator硬编码顺序 | REJECT框架 / ADAPT节点-状态理念 |
| 检查点与恢复 | DBOS | ⚠️ CheckpointManager基础版 | 无step级幂等，整章重跑成本高 | ADAPT（吸收step级缓存模式） |
| 结构化任务与结果校验 | PydanticAI | ⚠️ TypedDict+手动解析 | 无自动参数校验 | ADAPT（工具输入校验） |
| 多Agent协作 | AutoGen | ✅ Director+Writer+Reviewer | 不适用对话模式 | REJECT（已归档+不适用） |
| 运行轨迹追踪 | DBOS/OpenHands | ✅ ProductionTracer | 仅日志级，无step级关联 | 已有，可扩展为step级 |

---

## 7. 证据限制说明

| 项目 | 证据等级 | 原因 |
|------|----------|------|
| DBOS | ✅ 源码级 | 成功clone，完整阅读11,225行Python |
| PydanticAI | ⚠️ 部分源码 | tools.py/result.py可读，其他受限 |
| LangGraph | ⚠️ README级 | clone超时，API部分可用 |
| OpenHands | ⚠️ README级 | clone超时，API返回空 |
| AutoGen | ⚠️ README级 | 已归档，API受限 |

所有结论基于可获取证据，无编造内容。
