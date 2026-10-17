# PA-ARCH-RESEARCH-AND-REFACTOR-01 — P8: 回归测试与本地交付

**Date:** 2026-10-17  
**Status:** PASS

---

## 1. 测试覆盖清单

| 测试类别 | 测试文件 | 测试数 | 状态 |
|----------|----------|--------|------|
| RLM适配器 | test_rlm_adapter.py | 11 | ✅ PASS |
| 修复循环Agent | test_fix_loop_agent.py | 9 | ✅ PASS |
| 可观测性 | test_observability.py | 6 | ✅ PASS |
| 恢复机制 | test_p5_recovery.py | 15 | ✅ PASS |
| 连续性回归 | test_p6_continuity.py | 11 | ✅ PASS |
| 原有测试 | test_novel_engine.py | 5 | ✅ PASS |
| **总计** | | **57** | **全部通过** |

---

## 2. 新增测试详细

### 2.1 Prime-Agent真实任务执行

```python
test_run_chapter_without_rlm_degrades_cleanly
test_run_chapter_with_mock_rlm_calls_scene_unit
test_fallback_on_rlm_failure
```

### 2.2 工具注册和调用

```python
test_get_world_context_returns_dict
test_get_character_state_queries_db
test_validate_continuity_runs_gates
test_apply_targeted_repair_calls_writer
test_save_checkpoint_creates_file
```

### 2.3 工具结果回传

```python
test_rlm_dispatch_sends_and_receives
test_rlm_fallback_on_send_failure
test_rlm_fallback_on_recv_empty
```

### 2.4 结构化输出校验

```python
test_fix_diagnostic_builds_correctly
test_fix_recommendation_actions
test_trace_entry_has_required_fields
```

### 2.5 Agent决策变化

```python
test_recommend_pass_when_score_high
test_recommend_stop_when_no_improve
test_recommend_patch_on_high_issues
test_recommend_rewrite_on_low_score
```

### 2.6 审稿失败及定向修复

```python
test_review_failure_returns_error
test_fix_loop_agent_trace_records
```

### 2.7 质量门禁

```python
test_deterministic_gate_consistency
test_gate_issues_not_masked_as_pass
```

### 2.8 任务恢复

```python
test_checkpoint_is_atomic_write
test_double_commit_is_safe
test_resume_skips_done_chapters
test_partial_checkpoint_raises
test_load_last_complete
```

### 2.9 幂等性

```python
test_different_content_different_hash
test_checkpoint_has_complete_structure
```

### 2.10 模型超时和失败

```python
test_rlm_timeout_falls_back_to_sync
test_review_exception_handled
```

### 2.11 原有生产入口兼容性

```python
test_generate_chapter_without_kernel
test_orchestrator_runs_independently
```

### 2.12 正式稿保护

```python
test_forbidden_gate_blocks_violation
test_checkpoint_prevents_overwrite
```

---

## 3. 本地文档更新

### 3.1 环境与依赖

- Python >= 3.12（当前3.13.0）
- httpx, jieba, json-repair（必需）
- pytest, pytest-asyncio（测试）
- ipykernel（Prime-Agent集成）

### 3.2 安装和启动

```bash
# 安装
pip install -e "packages/coding-agent/skills/novel-engine[dev]"

# 环境变量
export AGNES_TOKEN_API_KEY="sk-xxx"

# 运行测试
cd packages/coding-agent/skills/novel-engine/src
python -m pytest tests/ -q
```

### 3.3 架构及模块边界

见`03-TARGET-ARCHITECTURE.md`架构图

### 3.4 Prime-Agent运行机制

1. `set_kernel_handles(rlm, agent_message)`注入kernel
2. `generate_chapter()`检测`_KERNEL["rlm"]`
3. 有RLM → `NovelRLMAdapter.run_chapter()`
4. 无RLM → `PipelineOrchestrator._run_with_retry()`

### 3.5 工具接口

| 工具 | 输入 | 输出 |
|------|------|------|
| `get_world_context` | chapter_num | dict(characters, factions, power_system) |
| `get_character_state` | char_name | dict(realm, location, relationships) |
| `get_timeline_context` | chapter_num | dict(anchors, elapsed) |
| `get_foreshadowing_state` | chapter_num | dict(active_clues, fulfilled) |
| `validate_continuity` | chapter_num, text | dict(issues, passed) |
| `apply_targeted_repair` | chapter_num, scene_num, directive | dict(new_text, score_delta) |
| `save_checkpoint` | chapter_num, content | dict(checkpoint_hash, timestamp) |

### 3.6 状态与检查点

- Checkpoint路径: `runtime/checkpoint.json`
- Trace路径: `runtime/traces/{run_id}.jsonl`
- 幂等性: 相同hash不重复写入

### 3.7 日志与性能指标

- 生产日志: `runtime/logs/production.log`
- Trace指标: `runtime/traces/{run_id}_metrics.json`
- 关键指标: LLM调用次数、耗时、token使用、失败原因

### 3.8 故障恢复

```bash
# 查看checkpoint
cat runtime/checkpoint.json | jq '.checkpoints[-5:]'

# 从指定章节恢复
python -m novel_engine.pipeline.production_runner --resume --start 50
```

### 3.9 测试命令

```bash
# 运行所有新测试
python -m pytest tests/test_rlm_adapter.py tests/test_fix_loop_agent.py \
                 tests/test_observability.py tests/test_p5_recovery.py \
                 tests/test_p6_continuity.py -v

# 运行原有测试
python -m unittest novel_engine.tests.test_novel_engine -v
```

### 3.10 回滚方式

```bash
# 回滚到重构前状态
git checkout 97c6cd4  # P0-P2提交前的最后一个commit

# 或删除新增文件
git rm packages/coding-agent/skills/novel-engine/src/novel_engine/adapters/rlm_adapter.py
git rm packages/coding-agent/skills/novel-engine/src/novel_engine/adapters/fix_loop_agent.py
git rm packages/coding-agent/skills/novel-engine/src/novel_engine/adapters/observability.py
git rm packages/coding-agent/skills/novel-engine/src/novel_engine/tools/domain_tools.py
```
