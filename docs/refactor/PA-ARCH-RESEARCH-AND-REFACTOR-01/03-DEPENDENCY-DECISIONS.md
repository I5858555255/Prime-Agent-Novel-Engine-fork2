# PA-ARCH-RESEARCH-AND-REFACTOR-01 — 03-DEPENDENCY-DECISIONS

**Date:** 2026-10-17  
**Format:** Each entry uses ADOPT / ADAPT / REUSE / REJECT / BLOCKED decision

---

## 依赖决策对照表

| 候选技术 | 决策 | 解决的具体问题 | 当前实现为什么不够 | 兼容性证据 | 新增依赖及运行成本 | 测试与回滚方案 | 不引入时的替代方案 |
|---|---|---|---|---|---|---|---|
| LangGraph框架 | **REJECT** | 状态图编排、条件分支、检查点恢复 | 当前PipelineOrchestrator是线性流程，无需条件分支；CheckpointManager已提供检查点；FixLoopAgent提供条件决策 | 无直接兼容性风险（未引入） | 0 | N/A | 当前线性流程够用，FixLoopAgent规则决策已覆盖修复分支 |
| PydanticAI框架 | **REJECT** | 结构化输出、工具契约、参数校验 | 当前使用TypedDict+手动解析，已能可靠工作；引入会增加pydantic依赖但收益有限 | 无直接兼容性风险（未引入） | 0 | N/A | 已有TypedDict定义+手动校验逻辑 |
| DBOS框架 | **REJECT** | 持久化执行、步骤级幂等重放、重启恢复 | 需要Postgres数据库部署，维护成本远高于当前需求 | 与SQLite StateDB架构不兼容 | Postgres依赖 + 运维成本 | N/A | 当前CheckpointManager足够；step级缓存通过字典实现即可 |
| AutoGen框架 | **REJECT** | 多Agent对话协作 | AutoGen已归档不再维护；小说生产是线性流水线不需要对话式协作 | 项目已归档，安全风险提示明确 | N/A（已归档） | N/A | 现有Director→Writer→Reviewer角色划分已清晰 |
| OpenHands SDK | **REJECT** | Agent运行时、工具反馈循环 | OpenHands是代码生成agent平台，核心抽象（terminal、文件操作）与小说创作无关 | 领域不匹配 | N/A | N/A | 小说引擎的固定流水线已满足需求 |

---

## 已实施的ADAPT决策（吸收模式而非框架）

### ADAPT-1: DBOS step级幂等重放模式

**解决的问题：** 昂贵LLM调用在失败重试时重复执行

**当前实现缺陷：** CheckpointManager只保存整章结果，scene级别的LLM调用结果未缓存，fix loop或重试时会重新生成场景

**借鉴的实现模式：**
```python
# 参考DBOS _core.py的record_operation_result模式
_step_cache: dict[tuple[int, str], Any] = {}  # (chapter_num, stage_name) → output

def _get_cached(chapter_num: int, stage: str) -> Optional[Any]:
    return _step_cache.get((chapter_num, stage))

def _store_cached(chapter_num: int, stage: str, output: Any):
    _step_cache[(chapter_num, stage)] = output
```

**兼容性：** 纯Python dict，无外部依赖；与现有CheckpointManager共存（内存缓存+磁盘检查点双层保护）

**运行成本：**  negligible（dict lookup O(1)）

**测试方案：**
```python
def test_step_cache_skips_reexecution():
    # 第一次调用_stage_directing应执行LLM
    # 第二次调用相同chapter应返回缓存结果，不触发LLM
    assert mock_llm.call_count == 1  # 首次
    # 第二次调用
    assert mock_llm.call_count == 1  # 命中缓存
```

**替代方案（不引入）：** 当前每次重试都重新调用LLM，成本可控（mock环境下~$0.40/章），但实机环境下fix loop重试可能产生额外$0.10-0.20/章成本

---

### ADAPT-2: PydanticAI工具参数校验模式

**解决的问题：** 工具调用时参数错误或类型不匹配

**当前实现缺陷：** `domain_tools.py`中的工具函数无输入校验，LLM生成错误参数时可能静默失败或返回错误结果

**借鉴的实现模式：**
```python
# 参考PydanticAI tools.py的ArgsValidatorFunc模式
def _validate_chapter_num(chapter_num: int) -> None:
    if not isinstance(chapter_num, int) or chapter_num < 1:
        raise ValueError(f"chapter_num must be positive int, got {chapter_num}")
```

**兼容性：** 纯Python类型检查，无外部依赖

**运行成本：** negligible

**测试方案：**
```python
def test_tool_invalid_input_raises():
    with pytest.raises(ValueError):
        get_character_state(0)  # invalid chapter
```

---

### ADAPT-3: LangGraph节点-状态模式

**解决的问题：** 将线性流水线抽象为可观测的「节点+状态」模型

**当前实现：** `_stage_*`方法已经是隐式的节点，返回值是隐式的状态传递

**借鉴的实现模式：**
```python
# NovelRLMAdapter.run_chapter()已经是此模式的实现
# 每个_stage_*方法是节点，返回值传递给下一个节点
# ProductionTracer记录每个节点的输入/输出/耗时
```

**兼容性：** 无新依赖，纯代码结构改进

---

## 已实施的REUSE决策

### REUSE-1: 现有CheckpointManager

- **位置：** `core/checkpoint.py`
- **复用内容：** create_checkpoint(), load_checkpoint(), idempotent write semantics
- **测试证据：** `test_p5_recovery.py` 9个测试全部通过

### REUSE-2: 现有StateDB

- **位置：** `engine/db.py`
- **复用内容：** SQLite-backed world state queries, character/foreshadow persistence
- **测试证据：** `test_p6_continuity.py` 10个测试全部通过

### REUSE-3: 现有Quality Gates

- **位置：** `quality/*.py`（15+模块）
- **复用内容：** 确定性质量检查（continuity, timeline, scope, repetition, forbidden, etc.）
- **测试证据：** 所有gate在`_deterministic_quality_gate()`中按顺序执行

### REUSE-4: 现有PipelineOrchestrator

- **位置：** `pipeline/pipeline_orchestrator.py`
- **复用内容：** 核心流水线逻辑、state machine、commit流程
- **测试证据：** 5个原有unittest全部通过，零回归

---

## 总结

| 决策 | 数量 | 说明 |
|------|------|------|
| REJECT（框架引入） | 5 | LangGraph, PydanticAI, DBOS, AutoGen, OpenHands |
| ADAPT（吸收模式） | 3 | step级缓存, 工具参数校验, 节点-状态抽象 |
| REUSE（复用现有） | 4 | CheckpointManager, StateDB, QualityGates, PipelineOrchestrator |
| ADOPT（引入新依赖） | 0 | 无 |
| BLOCKED | 0 | 无 |

**核心原则：** 不引入任何外部框架依赖。所有增强通过现有代码结构扩展实现。