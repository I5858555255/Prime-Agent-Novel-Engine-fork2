# PA-ARCH-RESEARCH-AND-REFACTOR-01 — 00-BASELINE

**Date:** 2026-10-17  
**Task ID:** PA-ARCH-RESEARCH-AND-REFACTOR-01  
**Project root:** `D:/projectAGI/Prime-Agent-Novel-Engine-feat-stability-quality`  
**Novel engine:** `packages/coding-agent/skills/novel-engine/src/novel_engine/`

---

## 1. Repository State

| Item | Value |
|------|-------|
| Git branch | master |
| Total commits | 16 |
| Last commit | e07f411 (docs: add P1 open-source research report) |
| Work area status | Clean — 全部已提交 |
| Python | 3.13.0 |
| Node.js | v24.14.1 |
| npm | 11.11.0 |
| pytest | 已安装（本次会话补充） |

---

## 2. Directory & Module Structure

```
novel_engine/
├── agent_api.py             # Prime-Agent IPython kernel入口，_KERNEL单例
├── adapters/                 # ★本次新增
│   ├── __init__.py
│   ├── rlm_adapter.py        # NovelRLMAdapter — RLM路由+ProductionTracer集成
│   ├── fix_loop_agent.py     # FixLoopAgent — 修复决策路由
│   └── observability.py      # ProductionTracer — trace持久化
├── agents/
│   ├── chapter_director.py   # 任务卡生成（LLM×1-3）
│   ├── writer_agent.py       # 场景生成（ThreadPool×4 workers）
│   ├── reviewer_agent.py     # 审稿（LLM×1-4）
│   ├── world_simulator.py
│   └── scene_schema.py
├── core/
│   ├── llm_client.py         # Mock+HTTPX客户端
│   ├── model_router.py       # 阶段级provider路由
│   ├── llm_failover.py       # 主/备切换控制
│   ├── state_machine.py      # ChapterPhase FSM
│   ├── checkpoint.py         # Hash-backed保存/恢复
│   └── quality_policy.py     # 阈值加载
├── engine/
│   ├── db.py                 # StateDB（SQLite代码查询）
│   ├── session.py            # Durable session tree
│   └── patcher.py            # 增量场景修复
├── pipeline/
│   ├── production_runner.py  # 批量生产运行器（920 LOC）
│   ├── pipeline_orchestrator.py  # 核心流水线（5219 LOC）
│   └── chapter_status.py     # COMMITTED/HALTED追踪
├── quality/                  # 15+质量门模块
│   ├── scene_model_router.py    # CC30降级→pro切换
│   ├── scene_failover_controller.py
│   ├── continuity_gate.py
│   ├── timeline_gate.py
│   ├── scope_gate.py
│   ├── repetition_detector.py
│   ├── forbidden_scanner.py
│   ├── chinese_gate.py
│   ├── latin_leak_gate.py
│   └── ...（其余见SOURCE-AUDIT.md）
├── tools/                    # ★本次新增
│   ├── __init__.py
│   └── domain_tools.py         # 11个领域工具封装
└── tests/
    ├── test_rlm_adapter.py     # 11 tests（新增）
    ├── test_fix_loop_agent.py  # 9 tests（新增）
    ├── test_observability.py   # 6 tests（新增）
    ├── test_p5_recovery.py     # 9 tests（新增）
    ├── test_p6_continuity.py   # 10 tests（新增）
    └── test_novel_engine.py    # 原有5 tests
```

---

## 3. Protected Data & Config

| 路径 | 类型 | 状态 |
|------|------|------|
| `bible/*.md` | 世界观/人物/风格圣经 | ✅ 存在，内容完整 |
| `config/planning/*.json` | 第1-616章大纲 | ✅ 存在 |
| `config/quality_thresholds.json` | 质量门禁阈值 | ✅ 存在 |
| `config/llm_providers.json` | 多profile provider配置 | ✅ 存在 |
| `config/runtime_config.json` | 主运行时配置 | ✅ 存在 |
| `timeline/timeline_anchors.json` | 时间锚点 | ✅ 存在 |
| `runtime/checkpoint.json` | 检查点 | ✅ 空（无历史运行） |
| `chapters/*.txt` | 正式章节 | ✅ 空 |
| `runtime/traces/` | Trace数据 | ✅ 空（本次新建） |

**回滚方式：** git reset --hard HEAD^ 或 checkout 到任意commit。所有新文件均在独立commit中，可逐文件删除。

---

## 4. Test Baseline

```
pytest:  52 passed (新测试)
unittest: 5 passed (原有)
预先失败: 4 (domain_rescue_remediation，与本次重构无关)
```

**无法确认事项：**
- 真实API端到端性能数据：UNKNOWN（需实机测试获取）
- 多章节并发压力测试：UNKNOWN（需批量运行验证）

---

## 5. Current Architecture Capabilities (Already Implemented)

| 能力 | 位置 | 状态 |
|------|------|------|
| 章节生产流水线 | `PipelineOrchestrator` | ✅ 运行正常 |
| 质量门禁（15+） | `quality/` | ✅ 确定性运行 |
| 检查点保存/恢复 | `core/checkpoint.py` | ✅ 通过P5测试 |
| 角色/伏笔持久化 | `engine/db.py` (StateDB) | ✅ 通过P6测试 |
| SessionTree分支回滚 | `engine/session.py` | ✅ 通过P6测试 |
| Prime-Agent RLM路由 | `adapters/rlm_adapter.py` | ✅ Mock测试通过 |
| 修复决策Agent | `adapters/fix_loop_agent.py` | ✅ Mock测试通过 |
| 运行级trace | `adapters/observability.py` | ✅ Mock测试通过 |
| 领域工具（11个） | `tools/domain_tools.py` | ✅ 定义完整 |

---

## 6. 未解决事项（后续阶段）

| 项目 | 优先级 | 阻塞条件 |
|------|--------|----------|
| FixLoopAgent集成到RLM路径review→commit之间 | P0 | 需要review→commit中间插入点 |
| Step级结果缓存（DBOS启发） | P1 | 需要确定缓存key策略 |
| 实机端到端测试（ch1-ch10） | P1 | 需要授权 |
| 多章节滑动窗口性能测试 | P2 | — |
| Web dashboard trace可视化 | P3 | — |
