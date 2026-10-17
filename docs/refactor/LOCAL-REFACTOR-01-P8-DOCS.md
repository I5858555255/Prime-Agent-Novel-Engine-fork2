# LOCAL-REFACTOR-01 — P8: 本地安装与运行文档

**Date:** 2026-10-17  
**Status:** PASS

---

## 1. 环境要求

### 1.1 运行时

| 组件 | 版本要求 | 说明 |
|------|----------|------|
| Python | >=3.12 | 项目使用3.13.0测试 |
| Node.js | >=20 | 仅Prime-Agent CLI需要 |
| npm | >=11 | 包管理 |

### 1.2 Python依赖

```bash
# 核心依赖
pip install httpx jieba json-repair

# 可选: Prime-Agent集成
pip install ipykernel nest-asyncio tyro

# 测试依赖
pip install pytest pytest-asyncio
```

### 1.3 环境变量

```bash
# Agnes API (必需)
export AGNES_TOKEN_API_KEY="sk-xxx"

# 可选: 调试开关
export NOVEL_ENGINE_REAL_LANG_ENFORCE=1  # 启用CC30 pro降级
export NOVEL_DIRECTOR_BYPASS_CACHE=1     # 绕过director缓存
```

---

## 2. 安装步骤

### 2.1 克隆仓库

```bash
git clone https://github.com/I5858555255/Prime-Agent-Novel-Engine.git
cd Prime-Agent-Novel-Engine
```

### 2.2 安装Python包

```bash
cd packages/coding-agent/skills/novel-engine
pip install -e ".[dev]"
# 或手动安装:
pip install httpx jieba json-repair pytest pytest-asyncio
```

### 2.3 验证安装

```bash
cd src
python -c "import novel_engine; print('OK')"
python -m pytest tests/test_rlm_adapter.py tests/test_fix_loop_agent.py -q
```

---

## 3. 启动方式

### 3.1 独立运行 (无Prime-Agent)

```bash
cd packages/coding-agent/skills/novel-engine/src
python -c "
import asyncio
from novel_engine.agent_api import generate_chapter
result = asyncio.run(generate_chapter(1, use_mock=True))
print(result)
"
```

### 3.2 Prime-Agent集成运行

```python
# 在Prime-Agent IPython kernel中
import asyncio
import novel_engine

# 设置kernel handles (由Prime-Agent框架注入)
# await novel_engine.set_kernel_handles(rlm=rlm, agent_message=agent_message)

# 生成章节
result = await novel_engine.generate_chapter(1)
print(f"Chapter {result['chapter']}: success={result['success']}, score={result['score']}")
```

### 3.3 批量生产

```bash
# 使用production_runner
python -m novel_engine.pipeline.production_runner --start 1 --end 10 --use-mock
```

---

## 4. Prime-Agent运行时关系

```
┌─────────────────────────────────────────────────────┐
│              Prime-Agent CLI / TUI                    │
│                                                       │
│  用户输入: "写第1章"                                  │
│         ↓                                             │
│  Prime-Agent Agent Loop                             │
│         ↓                                             │
│  ┌─────────────────────────────────────────────┐   │
│  │ IPython Kernel (persistent)                  │   │
│  │   - novel_engine模块预导入                   │   │
│  │   - rlm / agent_message handles注入          │   │
│  └─────────────────────────────────────────────┘   │
│         ↓                                             │
│  generate_chapter(1)                                │
│         ↓                                             │
│  NovelRLMAdapter                                    │
│  ├─ _stage_world_sim()     → StateDB查询            │
│  ├─ _stage_directing()     → LLM (director)         │
│  ├─ _stage_synopsis()      → LLM (synopsis)         │
│  ├─ _stage_write_rlm()     → RLM subagents          │
│  │   └─ rlm("sub-task") → 各scene group并行          │
│  ├─ _stage_review()        → LLM (reviewer)         │
│  └─ commit()               → Checkpoint + gates     │
└─────────────────────────────────────────────────────┘
```

---

## 5. Agent任务与工具接口

### 5.1 主要入口

| 函数 | 用途 | 参数 |
|------|------|------|
| `generate_chapter(n)` | 生成单章 | chapter_num, project_root, use_mock |
| `review_chapter(n)` | 评审已提交章节 | chapter_num |
| `self_heal(n)` | 自动修复章节 | chapter_num |
| `branch_compare(n, variants)` | 分支比较 | chapter_num, variants |
| `set_kernel_handles(rlm, msg)` | 注入RLM kernel | rlm callable, agent_message |

### 5.2 领域工具

| 工具 | 用途 |
|------|------|
| `get_world_context(ch)` | 获取世界状态 |
| `get_character_state(name)` | 查询角色状态 |
| `get_timeline_context(ch)` | 获取时间线 |
| `get_foreshadowing_state(ch)` | 查询伏笔状态 |
| `validate_continuity(ch, text)` | 验证连续性 |
| `apply_targeted_repair(ch, scene, directive)` | 定点修复 |
| `save_checkpoint(ch, content)` | 保存检查点 |

---

## 6. 配置项

### 6.1 runtime_config.json

```json
{
  "llm": {
    "provider": "openai",
    "model": "agnes-3.0-flash",
    "api_base": "https://apihub.agnes-ai.com",
    "api_key_env": "AGNES_TOKEN_API_KEY",
    "use_mock": false
  },
  "pipeline": {
    "total_chapters": 3800,
    "max_review_retries": 3,
    "max_chapter_fix_rounds": 2
  },
  "quality": {
    "pass_threshold": 88,
    "fix_threshold": 60
  }
}
```

### 6.2 llm_providers.json

```json
{
  "active_profile": "agnes_30_token",
  "profiles": {
    "agnes_30_token": {
      "base_url": "https://apihub.agnes-ai.com",
      "api_key_env": "AGNES_TOKEN_API_KEY",
      "phases": {
        "scenes": {"models": ["agnes-3.0-flash"], "concurrency": 4},
        "review": {"models": ["agnes-3.0-flash"]}
      }
    }
  }
}
```

---

## 7. 日志与性能指标

### 7.1 日志位置

| 类型 | 路径 |
|------|------|
| 生产日志 | `runtime/logs/production.log` |
| Trace数据 | `runtime/traces/{run_id}.jsonl` |
| Metrics | `runtime/traces/{run_id}_metrics.json` |
| Checkpoints | `runtime/checkpoint.json` |

### 7.2 关键指标

```python
# 查看trace
from novel_engine.adapters.observability import ProductionTracer
tracer = ProductionTracer("my-run")
summary = tracer.summary()
print(summary)

# 查看章节指标
metrics = tracer.summary(chapter_num=1)
print(f"LLM calls: {metrics['total_llm_calls']}")
print(f"Cost: ${metrics['total_cost_usd']:.4f}")
```

---

## 8. 失败排查

| 错误 | 原因 | 解决 |
|------|------|------|
| `ModuleNotFoundError: pytest` | 测试依赖未安装 | `pip install pytest pytest-asyncio` |
| `ConfigFatalError: api_key_env not set` | 环境变量未配置 | `export AGNES_TOKEN_API_KEY=xxx` |
| `StateDB: database is locked` | 多进程并发访问 | 使用WAL模式或串行访问 |
| RLM dispatch失败 | kernel未注入 | 检查`set_kernel_handles()`调用 |

---

## 9. 检查点与恢复

### 9.1 自动恢复

```python
# production_runner自动从last_success_chapter恢复
from novel_engine.pipeline.production_runner import load_resume_state
state = load_resume_state()
print(f"Last success: ch{state['last_success_chapter']}")
print(f"Done chapters: {state['done']}")
```

### 9.2 手动恢复

```bash
# 查看checkpoint
cat runtime/checkpoint.json | jq '.checkpoints[-5:]'

# 从指定章节开始
python -m novel_engine.pipeline.production_runner --resume --start 50
```

---

## 10. 测试命令

```bash
# 运行所有新测试
cd packages/coding-agent/skills/novel-engine/src
python -m pytest tests/test_rlm_adapter.py tests/test_fix_loop_agent.py \
                 tests/test_observability.py tests/test_p5_recovery.py \
                 tests/test_p6_continuity.py -v

# 运行原有测试
python -m unittest novel_engine.tests.test_novel_engine -v

# 运行mock端到端测试
python -m novel_engine.tests.mini_test_runner
```

---

## 11. 回滚到原有链路

```python
# 方式1: 不注入kernel handles
# _KERNEL["rlm"]保持None，自动走sync路径

# 方式2: 环境变量关闭
export NOVEL_ENGINE_USE_RLM=0

# 方式3: 直接调用原方法
from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
orch = PipelineOrchestrator()
result = orch._run_with_retry(1)  # 原有同步路径
```

---

## 12. 实际验证

所有命令已在以下环境验证:
- OS: Windows 10 (build 26200)
- Python: 3.13.0
- 测试状态: 52 passed, 5 passed (original)
