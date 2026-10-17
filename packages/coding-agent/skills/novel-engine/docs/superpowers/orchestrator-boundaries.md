# 流水线编排器 Gate 委托修复教训

## 背景

在将 gate 逻辑从 `pipeline_orchestrator.py` 提取到独立模块 `pipeline/gates.py` 的过程中，使用了错误的委托写法。

## 问题

以下写法引入了 16 处 bug：

```python
# 错误写法：staticmethod 关闭了 Python 的自动实例绑定
_run_continuity_gate = staticmethod(lambda self, *a, **k: _gates._run_continuity_gate(self, *a, **k))
```

**缺陷**：`staticmethod()` 包装后，方法失去自动绑定 `self` 的能力。所有调用点仍按普通实例方法方式调用：

```python
purified = self._run_continuity_gate(_bn, task_card, _scenes_list, purified)
```

期望 Python 把 `self`（orchestrator 实例）绑定到 lambda 的第一个参数，但 `staticmethod` 不做绑定——实际调用时第一个实参 `_bn` 顶替了 lambda 的 `self`，后续参数集体错位一位，最后一个参数（如 `assembled_text`）丢失。

**报错现象**：`_Gates._run_continuity_gate() missing 1 required positional argument: 'assembled_text'`

## 影响范围（16 处）

- 12 个 gate：`_run_continuity_gate` / `_run_scope_gate` / `_run_density_gate` / `_run_scene_progression_gate` / `_run_cross_scene_repeat_gate` / `_run_latin_leak_gate` / `_run_pov_interiority_gate` / `_run_constraint_compliance_gate` / `_run_punctuation_health_gate` / `_run_final_precommit_gate` / `_run_boundary_reprise_gate` / `_run_boundary_gate`
- `_apply_length_floor`
- 3 个标点修复：`_punct_only_repair` / `_repair_paragraphs_punct_parallel` / `_repair_paragraphs_punct_batched`

## 修复方案

改用普通方法定义，保留自动绑定：

```python
def _run_continuity_gate(self, *a, **k):
    return _gates._run_continuity_gate(self, *a, **k)
```

共 16 处全部替换，无残留 `staticmethod`。

## 教训

### 1. 提取方法/拆模块类改动，提交前必须跑全量测试套件

本次回归分布在 8+ 个测试文件（round13/14/15b/16/18/19/22 等），局部验证会完全漏掉。不能只跑新增或相关测试。

**正确做法**：
```bash
cd packages/coding-agent/skills/novel-engine && \
"D:/Program Files/Python312/python.exe" -m pytest src/novel_engine/tests/ -q
```

### 2. 委托到模块函数的类属性不要用 `staticmethod(lambda self, *a, **k: ...)` 写法

`staticmethod()` 会关闭 Python 的自动实例绑定，是隐藏陷阱。即使看起来"手动传 self"能工作，调用方仍可能按普通方法调用，导致参数错位。

**推荐写法**：
```python
def _delegate(self, *a, **k):
    return _module._function(self, *a, **k)
```

**不推荐**：
```python
_delegate = staticmethod(lambda self, *a, **k: _module._function(self, *a, **k))
_delegate = lambda self, *a, **k: _module._function(self, *a, **k)  # 依然有问题
```

### 3. 全量测试基线

修复前：28 failed / 848 passed（总数 876）
修复后：28 failed / 848 passed（数量不变，失败均为预存问题）

已知 pre-existing failures：
- `test_same_location_cluster_shared_objects_not_flagged`（cross_scene_repeat_gate 逻辑问题，与本次修复无关）
- 多个 FileNotFoundError（基础设施/配置缺失）
- round22 相关测试（scope gate 配置问题）

## 日期

2026-09-27
## P0 未定义名修复（拆分引入）

### 问题

2026-09-28 拆分 pipeline_orchestrator.py 后引入 12 处 undefined name，全部为 P0 阻塞：

| 文件 | 行 | 未定义名 | 修复 |
|---|---|---|---|
| pipeline_orchestrator.py | 667-668 | `current` / `current_draft` | 在 if 块前初始化镜像变量 |
| pipeline_orchestrator.py | 865 | `_len` | 笔误改为 `len` |
| pipeline_orchestrator.py | 890/892 | `violations` | 在 atm dedup 块前初始化为 `None` |
| pipeline_orchestrator.py | 1289 | `apply_world_state` | 加入 `_commit_chapter` 参数 |
| pipeline_orchestrator.py | 1307 | `cur_score` | 改为签名参数 `score` |
| agent_api.py | 103, 170 | `novel_chapter_path` | 补 import |
| gates.py | 1262, 1291 | `call_llm` | 补 import |
| gates.py | 1286, 1294 | `find_self_repetition` | 补 import |
| gates.py | 1458 | `_forbidden_violation_is_blocking` | 迁移到 quality_policy.py |
| llm_client.py | 681, 739, 867 | `record_call` | 补 from .call_metrics import |
| llm_client.py | 1027 | `ProviderConfig` | TYPE_CHECKING 守卫，避免循环导入 |

### 门禁

提交前必须通过以下检查：

```bash
cd packages/coding-agent/skills/novel-engine && \
python scripts/check_static.py && \
"D:/Program Files/Python312/python.exe" -m pytest src/novel_engine/tests -q
```

`check_static.py` 对 novel_engine 非 tests 目录运行 pyflakes，出现 `undefined name` 即退出码非 0。

### 防回归测试

新增 `tests/test_commit_path.py`，覆盖：
- T1：正常提交路径 → COMMITTED + event_ledger + end_state 各追加一条
- T2：含 state_changes 的 synopsis → apply_pending_changes 生效

## 反例纪律（2026-10-13，源自外部评审 P6-4）

任何新增/修改的检测规则（concept_unlocks 概念分组、泄漏检测模式、scope/传授判定、forbidden 条款、规则表 pattern）必须满足：

1. **附带反例测试才能合入**：提交时必须同时附至少一条"该规则不误杀合法内容"的反例测试（如概念解锁后同样表述放行、传授非修炼宾语不触发、合法形态不误杀）。教训：round-30 只测了 ch141 正例、未枚举"首个场景跨日/深夜→清晨隐含跨日"两类合法形态，导致 8 章被误杀（backlog 552 行）。
2. **不靠人工逐条加词续命**：发现新误报时，先判断能否归入已有概念分组/规则覆盖；只有明确不属于任何已知概念的才允许新增规则，并在 commit message 写清为什么不适用现有机制。
3. **验证范围**：规则变更后必须跑全量 pytest（novel_engine/tests，约 3 分钟），失败只允许已知 pre-existing（test_same_location_cluster_shared_objects_not_flagged）；pyflakes 全包 0 undefined name。
