# PA-ARCH-RESEARCH-AND-REFACTOR-01 — 02-CURRENT-ARCHITECTURE

**Date:** 2026-10-17  
**Audit Scope:** `packages/coding-agent/skills/novel-engine/src/novel_engine/`  
**Trace Method:** Source code reading + call chain analysis + mock test execution

---

## 1. 完整调用链追踪

```
generate_chapter(chapter_num)                    [agent_api.py:98]
  │
  ├─ _KERNEL["rlm"] is not None?
  │   ├─ YES → NovelRLMAdapter.run_chapter(ch)   [adapters/rlm_adapter.py:86]
  │   │         ├─ _stage_world_sim(ch)           [pipeline_orchestrator.py:~1521]
  │   │   │               → StateDB queries (0 LLM calls, deterministic)
  │   │   ├─ _stage_directing(ch, ws)             [~line 1529]
  │   │   │               → ChapterDirector.generate_task_card() × 1-3 LLM calls
  │   │   │               └─ max_attempts=3 on parse failure
  │   │   ├─ _stage_synopsis(task_card)            [~line 1757]
  │   │   │               → SynopsisAgent.generate_synopsis() × 1 LLM call
  │   │   │               └─ cached in self._frozen_synopsis[chapter_num]
  │   │   ├─ _stage_write_rlm(ch, tc, syn, rlm, msg)  [rlm_adapter.py:204]
  │   │   │               → _group_independent_scenes() → groups of scenes
  │   │   │               → for each group:
  │   │   │                   ├─ rlm("sub-task") → handle
  │   │   │                   ├─ agent_message.send(spec, receiver="child")
  │   │   │                   ├─ agent_message.recv(receiver="parent") → [(bp, content), ...]
  │   │   │                   └─ fallback on exception: ThreadPoolExecutor per group
  │   │   ├─ _stage_review(ch, tc, syn, text, ws)  [~line 2965]
  │   │   │               → ReviewerAgent.review_chapter() × 1-4 LLM calls
  │   │   │               └─ conditional: re-review if score<65, triple-review if 81.5-85.5
  │   │   ├─ (FixLoopAgent decision point - NOT yet wired into RLM path)
  │   │   └─ commit_chapter(ch, text, syn, tc, ws)  [state_machine.commit_chapter]
  │   │               → evaluate_forbidden_gate() (deterministic)
  │   │               → run_stage6_foreshadow_check() (deterministic)
  │   │               → scrub_scaffold_latin() (deterministic)
  │   │               → _deterministic_quality_gate() (deterministic, 10+ gates)
  │   │               → CheckpointManager.create_checkpoint() (disk write)
  │   └─ NO → PipelineOrchestrator._run_with_retry(ch)  [pipeline_orchestrator.py:~4759]
  │             └─ generate_single_chapter(ch)    [~line 473]
  │                 └─ Same stages as above but all via ThreadPoolExecutor for scenes
  │
  └─ Returns: {"chapter": int, "success": bool, "score": float|None, "verdict": str}
```

### LLM调用计数（每章）

| 阶段 | 最小 | 最大 | 说明 |
|------|------|------|------|
| world_sim | 0 | 0 | 纯Python查询 |
| director | 1 | 3 | 失败时重试（最多3次） |
| synopsis | 1 | 1 | 缓存，重复使用 |
| scene write | M | M×2 | M=场景数(3-4)，CC30信号触发pro切换 |
| expansion | 0 | 2M | 短场景扩展（最多2轮） |
| polish | M | M | 每场景polish |
| review | 1 | 4 | base + re-review + triple-review |
| fix loop | 0 | ~12 | 每轮：review + patch |
| **总计** | **~4** | **~30+** | 平均约8-12次 |

---

## 2. Prime-Agent实际参与位置

### 2.1 当前参与程度

| 组件 | 参与程度 | 说明 |
|------|----------|------|
| `agent_api.set_kernel_handles()` | 定义但未自动调用 | 需外部显式设置 |
| `_KERNEL["rlm"]` | 定义但默认None | 无Prime-Agent注入时为None |
| `_KERNEL["agent_message"]` | 定义但默认None | 同上 |
| `generate_chapter_with_subagents()` | 存在但未接入主路径 | 独立入口，非`generate_chapter()`调用 |
| `NovelRLMAdapter` | **已接入** `generate_chapter()` | 当`_KERNEL["rlm"]`不为None时生效 |
| `FixLoopAgent.recommend_via_rlm()` | 定义但未在主路径调用 | 规则决策(`recommend`)可用，RLM决策未接入 |
| `domain_tools.*` (11个) | 定义但未注册到RLM | 需Prime-Agent kernel显式注册 |

### 2.2 Prime-Agent在小说生产中的实际角色

**答案：Prime-Agent是当前小说生产链路的「可选增强路径」，不是实际执行主体。**

- 当`_KERNEL["rlm"]`为None时（默认），生产完全走原有ThreadPoolExecutor路径
- 当注入kernel handles时，场景生成阶段切换为RLM子代理，但修复决策仍为规则驱动
- 领域工具（domain_tools.py）未主动暴露给Prime-Agent的tool registry

### 2.3 控制权边界

| 控制权 | 当前归属 | 数据 |
|--------|----------|------|
| 章节计划生成 | ChapterDirector(LLM) | task_card dict |
| 场景调度 | ThreadPoolExecutor（同步）/ RLM subagents（异步） | 取决于kernel |
| 场景内容生成 | WriterAgent(LLM) | scene_text |
| 质量评估 | ReviewerAgent(LLM) + 15+确定性门 | score + verdict |
| 修复决策 | FixLoopAgent（规则）/ RLM（未激活） | FixRecommendation |
| 提交决策 | 确定性门禁（通过/失败） | checkpoint hash |

---

## 3. 缺失环节

| # | 缺失环节 | 位置 | 影响 |
|---|----------|------|------|
| M1 | FixLoopAgent未集成到RLM路径 | `rlm_adapter.py:run_chapter()` 无fix loop调用 | RLM路径跳过agent决策修复 |
| M2 | 领域工具未注册到Prime-Agent | `tools/domain_tools.py` 无注册机制 | Agent无法调用工具 |
| M3 | 无step级结果缓存 | `_stage_*`方法无输出缓存 | 每次重试都重新调LLM |
| M4 | 无per-chapter LLM调用预算控制 | 无硬性上限 | 多轮fix loop可超预算 |
| M5 | `generate_chapter_with_subagents()` 与 `generate_chapter()` 两条路径未统一 | `agent_api.py:256` vs `agent_api.py:98` | 维护两套逻辑 |

---

## 4. 真实调用链验证（mock测试执行结果）

```
=== Mock测试执行 ===
RLM path result: {'chapter': 1, 'success': False, 'score': 78.2, 'verdict': 'fix', 'rlm_used': True}
Trace count: 14 entries (world_sim→directing→synopsis→write→review→commit)
RLM called: 1 time (dispatch)
Send/Recv: 1 each (subagent communication)

Fallback path (RLM raises RuntimeError):
Fallback result: success=False (degraded to sync)
Fallback trace: status="fallback" confirmed
```

**证据：** Prime-Agent确实在mock环境中完成了"提交任务→子代理执行→返回结果"的完整链路。但修复决策仍为规则驱动，未使用RLM推理。
