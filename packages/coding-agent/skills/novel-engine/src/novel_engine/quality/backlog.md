# Quality Backlog - Novel Engine

## [Unreleased]

- 问题记录：ch9 scene3 设定泄漏经定点重生仍未消除

---

## Issue: WRITE_REPLAN ch9 scene3 泄漏重生成未消除

### 现象
```
Scope leak ch9 scene3: [('hard_leak', '吐纳')] -> targeted regen
Scope leak persists scene3 after regen [('hard_leak', '吐纳')] -> replan
WRITE REPLAN ch9: 设定泄漏/时间锚越界经定点重生仍未消除 scenes=[3]，需重排任务卡
Chapter 9 quality-replan requested (scenes=[3]) → skip in-process retries
```

### 根因分析

1. **连锁故障**：`scope_gate` 检测到 scene3 含有硬禁词 `吐纳`，触发定向重生。
2. **软重生失败**：定向重生调用 `generate_scene(..., prev=..., fix_directive=...)`，但 `WriterAgent.generate_scene()` 签名不接受 `prev` 关键字参数（实际参数名为 `previous_context`），导致 `TypeError`。
3. **文本未替换**：由于异常被 `except Exception` 捕获并仅打 warning 日志，scene3 的原文未被任何新文本替换。
4. **重复检测**：下一轮 scope_gate 重新扫描 chapter 全文，scene3 仍是原始含 `吐纳` 的文本，再次命中硬禁词。
5. **兜底耗尽**：场景级定点重生失败后，系统进入 `replan` 流程，触发 `ChapterQualityGapError`，章节 FAIL。

### 证据链

| 位置 | 证据 |
|------|------|
| `pipeline_orchestrator.py:856` | `prev=_prev,` 调用 site 1（atm repolish 路径） |
| `pipeline_orchestrator.py:3297` | `prev=...` 调用 site 2（soft regen 路径） |
| `pipeline_orchestrator.py:3399` | `prev=...` 调用 site 3（final gate 路径） |
| `writer_agent.py:383-396` | `generate_scene()` 签名中参数为 `previous_context`，非 `prev` |
| `run_volume_bg.log:27-29,46-48` | `ch6 soft regen scene2/3/4 failed: WriterAgent.generate_scene() got an unexpected keyword argument 'prev'` |

### 修复状态

- **问题 1 已修复**：将所有 `prev=` 改为 `previous_context=`（commit pending）。
- **问题 2 的间接影响**：修复问题 1 后，soft regen 路径将正常工作，ch9 scene3 的定点重生应能成功替换含 `吐纳` 的文本，不再需要 WRITE_REPLAN。

### 建议

1. 修复后重新跑 ch9，验证 scene3 的 scope leak 是否能被定点重生消除。
2. 若仍失败，需检查 `fix_directive` 构建逻辑是否正确传递了禁词替换指令。
3. 可考虑在 scope_gate 检测到硬禁词时，直接调用 `excise_temporal_violations` 确定性剔除禁词，作为再生的前置兜底。

---

*生成时间：2026-09-29*
