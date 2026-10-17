# LOCAL-REFACTOR-01 — P1：四类参考项目源码级研究

**Date:** 2026-10-17  
**Evidence Level:** DBOS=直接源码（完整）；LangGraph/PydanticAI/OpenHands/AutoGen=GitHub API + README（受限）

---

## 1. OpenHands Software Agent SDK

**仓库：** https://github.com/OpenHands/OpenHands  
**分支：** main（最新）  
**源码访问：** 受限（API返回空，README可访问）

### 关键结构（据 README）
- 定位：「self-hosted developer control center for coding agents」
- 支持多种 agent backend（Claude Code、Codex、Gemini CLI 等），通过 ACP 协议统一接入
- Agent Canvas 模式：自动化任务编排 + 持续运行

### 与小说引擎的对应关系

| OpenHands 能力 | 小说引擎现状 | 差异 |
|---|---|---|
| 多 backend 统一管理 | 只有单一 LLM 调用链（agnes-3.0-flash） | 无对比价值 |
| 持续运行、自动化流程 | PipelineOrchestrator 是同步批处理 | **架构理念可借鉴** |
| ACP 协议 | 无标准协议 | 不适用 |

### 决策：`REJECT`
**理由：**
- OpenHands 是代码生成 agent 平台，核心抽象（代码执行环境、文件操作、terminal）与小说创作无关
- 其「多 backend 统一」理念对单一 LLM 场景无意义
- 引入会引入不必要复杂性

---

## 2. LangGraph

**仓库：** https://github.com/langchain-ai/langgraph  
**分支：** main  
**源码访问：** 受限（clone 超时，部分 API 可用）

### 关键结构（据 README 与 API 响应）
- 「low-level orchestration framework for building stateful, long-running agents」
- 核心抽象：`StateGraph`（节点 + 边 + 条件分支）
- 持久化：`MemorySaver` / `BaseCheckpointSaver`（检查点 + 回滚）
- Human-in-the-loop：`interrupt()` 机制
- 并发子图：`Subgraph` 支持

### 与小说引擎的对应关系

| LangGraph 能力 | 小说引擎现状 | 评估 |
|---|---|---|
| 状态图编排（有向图 + 条件边） | PipelineOrchestrator 是硬编码顺序调用 | **概念相同但实现简陋** |
| 检查点保存/恢复 | CheckpointManager 已存在（checkpoint.py） | **已有等效实现** |
| Human-in-the-loop 中断 | 无 | **潜在增量** |
| 并发子图 | ThreadPoolExecutor 简单并发 | **有限替代** |

### 决策：`REJECT（ADAPT 理念）`
**理由：**
- 引入 LangGraph 需要全新 state 类型定义 + graph 构建，改造成本 > 收益
- 当前 PipelineOrchestrator 的流程是线性的（世界模拟→指导→大纲→写作→评审→提交），不需要条件分支
- 检查点能力已由 `CheckpointManager` 提供
- Human-in-the-loop 是潜在增量，但当前需求不紧迫
- **应借鉴的理念**：将 `_stage_*` 方法视为「节点」、将返回值视为「状态」——但无需引入框架本身

---

## 3. PydanticAI

**仓库：** https://github.com/pydantic/pydantic-ai  
**版本：** 最新 main  
**源码访问：** 部分（tools.py 可读，其他受限）

### 关键结构（据 tools.py）
- **Tool 定义**：`@agent.tool` 装饰器，自动从函数签名生成 JSON Schema
- **RunContext**：依赖注入，工具函数可接收运行时上下文
- **ArgsValidatorFunc**：参数预校验钩子
- **ToolPrepareFunc**：动态准备工具定义
- **ToolSelectorFunc**：自定义工具选择逻辑
- **OutputSchema**：结构化输出验证（Pydantic model）
- **ToolResult / ToolOutput**：标准化工具返回

### 与小说引擎的对应关系

| PydanticAI 能力 | 小说引擎现状 | 评估 |
|---|---|---|
| Pydantic 结构化输出 | `typing.TypedDict` + 手动解析 | **部分等价** |
| 自动 JSON Schema 生成 | 手动定义 schema | **有差距** |
| 参数校验 | 无（LLM 自由输入） | **可借鉴** |
| 依赖注入 (RunContext) | `_KERNEL` 全局单例 | **概念类似** |

### 决策：`ADAPT`
**理由：**
- 小说引擎已有 TypedDict 定义，引入 Pydantic 验证会增加依赖但价值有限
- **可借鉴点**：工具函数的参数校验逻辑（在 `domain_tools.py` 中添加基础输入验证）
- 不引入 PydanticAI 框架本身（避免依赖膨胀）

---

## 4. DBOS Transact-Py

**仓库：** https://github.com/dbos-inc/dbos-transact-py  
**版本：** v0.5.x（commit 61426cb）  
**源码访问：** ✅ 完整（本地 clone 可用，11,225 行 Python）

### 核心架构洞察

#### 4.1 Workflow 作为顶层原子
```python
@workflow
def my_workflow(arg1, arg2):
    result = my_step(arg1)        # step 调用
    child_wf = workflow(my_other)(result)  # 子 workflow
    ...
```
- `@workflow` 装饰器包装函数，自动创建 workflow UUID
- 每次调用都有唯一 `workflow_uuid` + `function_id`
- 恢复时按相同 UUID 重放，**跳过已记录步骤的输出**

#### 4.2 Step 级幂等与检查点
```python
class StepStatus:
    step_id: int
    current_attempt: Optional[int]
    max_attempts: Optional[int]
```
- 每个 step 在执行前记录开始时间 `started_at_epoch_ms`
- 执行成功后写入 `operation_outputs` 表（output + serialization）
- 执行失败写入 `error` 字段（序列化异常）
- **replay 逻辑**：查询 `operation_outputs`，若已有记录则直接返回缓存结果，否则重跑

#### 4.3 状态机
```python
class WorkflowStatusString(Enum):
    PENDING = "PENDING"
    ENQUEUED = "ENQUEUED"
    DELAYED = "DELAYED"
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"
    MAX_RECOVERY_ATTEMPTS_EXCEEDED = "MAX_RECOVERY_ATTEMPTS_EXCEEDED"
```
- 持久化到 `workflow_status` 表
- 恢复线程扫描 `PENDING/ENQUEUED` 状态，触发重执行
- `rewind_workflow(start_step)` 可回滚到指定步骤重新执行

#### 4.4 串行化模型
- `DBOSPortableJSON`：通用 JSON 序列化（支持 datetime/date/Decimal）
- `DefaultSerializer`：pickle + base64（Python 专属）
- 序列化失败时不记录 checkpoint（让 replay 重新执行而非卡住）

#### 4.5 Stream 检查点
- `record_stream_read(offset, value)` — 流读取位置持久化
- 支持 timeout 和 cancel

### 与小说引擎的对应关系

| DBOS 能力 | 小说引擎现状 | 差距 |
|---|---|---|
| Workflow UUID + 状态机 | `CheckpointManager`（轻量） | CheckpointManager 功能类似但更简单 |
| Step 级幂等重放 | 无（整章重跑） | **关键缺口** |
| 操作结果缓存 | 无 | 每次重试都重新调 LLM |
| 持久化队列 | 无 | 无排队/优先级 |
| 子 workflow | `generate_scene_unit()` | 子过程但无 UUID 追踪 |

### 决策：`ADAPT（吸收核心模式）`
**理由：**
- DBOS 的核心价值不是数据库层，而是**操作级幂等重放**模式
- 小说引擎已有的 `CheckpointManager` 只做了「保存检查点」，没有「按步骤记录输出避免重复 LLM 调用」
- **具体可借鉴**：
  1. 为每个 `_stage_*` 方法添加「结果缓存」逻辑（按 chapter_num + stage_name 记录输出 hash）
  2. 恢复时先查缓存，命中则跳过，未命中才执行
  3. 这比 DBOS 的完整方案轻量得多，但解决了「昂贵 LLM 调用重复执行」的核心问题

---

## 5. AutoGen

**仓库：** https://github.com/microsoft/autogen  
**状态：** ⚠️ 已归档（archived），不再维护  
**源码访问：** 受限

### 关键结构（据 README）
- 多 Agent 协作框架（GroupChat、GroupChatManager）
- Agent 角色：AssistantAgent、UserProxyAgent
- 消息协议：`ChatResult`、`Conversation`
- 终止条件：手动指定、LLM 判断、轮次限制

### 与小说引擎的对应关系

| AutoGen 能力 | 小说引擎现状 | 评估 |
|---|---|---|
| 多 Agent 角色分工 | DirectorAgent + WriterAgent + ReviewerAgent（已有） | **已实现** |
| 对话式协作 | 无（PipelineOrchestrator 是单向流水线） | 不适用（创作不需要对话） |
| 终止条件 | 无自动终止（固定流程） | 当前够用 |

### 决策：`REJECT`
**理由：**
- AutoGen 已归档，不再维护，引入有风险
- 小说生产是**线性流水线**而非**对话式协作**，多 Agent 对话模式不适用
- 现有 Director → Writer → Reviewer 角色划分已经清晰

---

## 6. 综合对照表

| 研究能力 | 参考项目 | 当前是否已有 | 当前缺陷证据 | 推荐处理方式 |
|---|---|---|---|---|
| Agent 执行与工具反馈 | OpenHands | ✅ 部分（固定流程） | 无自主规划能力 | REJECT（领域不匹配） |
| 条件任务编排（状态图） | LangGraph | ⚠️ 线性流程，无分支 | PipelineOrchestrator 硬编码顺序 | REJECT（当前线性流程够用） |
| 检查点与恢复 | DBOS | ⚠️ CheckpointManager 基础版 | 无 step 级幂等，整章重跑 | ADAPT（吸收 step 级缓存模式） |
| 结构化输出与校验 | PydanticAI | ⚠️ TypedDict + 手动解析 | 无自动参数校验 | ADAPT（添加输入校验） |
| 多 Agent 协作 | AutoGen | ✅ Director+Writer+Reviewer | 不适用对话模式 | REJECT（已归档 + 不适用） |
| 运行轨迹追踪 | DBOS/OHAD | ✅ ProductionTracer | 仅日志级别，无 step 关联 | 已有，可扩展 |

---

## 7. 实际可落地的改进点

1. **Step 级结果缓存**（来自 DBOS 启发）：为 `_stage_directing()`、`_stage_synopsis()`、`_stage_write()`、`_stage_review()` 添加结果缓存，key 为 `(chapter_num, stage_name, input_hash)`，命中则跳过执行
2. **工具输入校验**（来自 PydanticAI 启发）：在 `domain_tools.py` 中添加参数校验，失败时返回明确错误而非让 LLM 猜测
3. **Workflow 状态细化**（来自 DBOS 启发）：将 `checkpoint.py` 中的状态从简单的「成功/失败」扩展为 `PENDING → IN_PROGRESS → SUCCESS/ERROR`

---

*研究证据等级说明：DBOS 源码完整阅读（~11K 行）；LangGraph/PydanticAI/OpenHands 主要依赖 README 和 API 响应，部分源码受限。结论基于可获取证据。*
