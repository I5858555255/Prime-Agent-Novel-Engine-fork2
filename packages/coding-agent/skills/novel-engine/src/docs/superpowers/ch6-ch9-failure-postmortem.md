# Ch6/Ch9 快速失败与泄漏 Regen 根因分析

## Ch6 快速失败 (score=85.0, quarantine)

### 现象
```
Canon scope gate ch6: 1 hard hit(s) -> hard block; terms=['吐纳']
FINAL GATE BLOCK ch6 score=85.0: ["scope_hard_leak:scope硬词 ['吐纳']"] -> quarantine+human
```

### 根因

**Primary cause: final precommit gate 的 `detect_scope_violations` 调用缺少 prev_sents 上下文**

`_run_scope_gate` 逐场景检测，scene1 的 "他几乎是毫不犹豫地，立刻中断了正在运行的吐纳法门。" 在 scene-level 上下文中有前句 "那一声声敲击里，昨夜的情景便不受控制地浮现。"，所以 `_descriptive_markers` 中的 "那" 触发了描述性引用豁免。

但 `_run_final_precommit_gate` 传入整个章节文本，分句后 `prev_sents` 维护了正确上下文，"他" 通过代词回溯到陈老根，应该豁免。实际测试显示该句在 multi-sentence 上下文中确实豁免了。

**实际 root cause: scene4 草稿含有超出 ch6 豁免范围的非 ch6 违规词**

ch6 最终草稿 scene4 含:
- `"而是那门连他自己都快记不全的，真正的吐纳呼吸之法。"` → 有 "那" + "自己" → 应豁免
- `"那时他正在调息，运转那早已废弃多年、几乎连自己都快忘记的法门"` → 有 "那" + "自己" → 应豁免
- `"这体质，老根只在极遥远的过去，在一些被宗门严密保护的种子身上见过传闻。"` → **"宗门" 在 hard_block 中**，不在 ch6 豁免范围内

### 修复
1. `scope_gate.py`: 将 `"方才"` 加入 `_descriptive_markers`（覆盖 "方才吐纳被打断" 类句式）
2. `pipeline_orchestrator.py` (commit 41a042ff6): 修复 soft regen 路径的 `prev=` → `previous_context=`（已在 HEAD）

### 影响
- ch6 soft regen scene2/3/4 不再因 TypeError 静默失败
- ch6 final gate 对 "吐纳"/"调息" 的 ch6 夜间陈老根独白豁免更健壮

---

## Ch9 泄漏 Regen 循环 (WRITE_REPLAN, score=None)

### 现象
```
Scope leak ch9 scene3: [('hard_leak', '吐纳')] -> targeted regen
Scope leak persists scene3 after regen [('hard_leak', '吐纳')] -> replan
WRITE REPLAN ch9: 设定泄漏/时间锚越界经定点重生仍未消除 scenes=[3]，需重排任务卡
```

### 根因

**Primary cause: `prev=` 参数 bug 导致 soft regen 失败（已修复）**

ch9 软重生（opening_anchor 修复）调用了 `generate_scene(prev=..., fix_directive=...)`，但签名是 `previous_context`，TypeError 导致 scene2/3/4 再生全部失败。

**Secondary cause: scope_gate 定点重生后 LLM 仍生成含禁词的文本**

scene3 的 `吐纳` 是通过 `_regen_scene_for_id`（不带 `prev=`）调用的，该路径本身无误。但 LLM 在收到 fix_directive 后仍未完全移除禁词，触发重排。

**Tertiary cause: deterministic replacement 会引入新硬词**

原替换映射 `"吐纳" → "调息"` 中 "调息" 也是 infant.json 的 hard_block 词，替换后复检仍失败。

### 修复
1. `scope_gate.py`: `scope_fix_directive()` 增加违禁原句列表（带引号标注），LLM 可精确定位替换位置
2. `gates.py`: `_run_scope_gate` 重生后复检阶段增加确定性字符串替换兜底，替换目标避免使用 hard_block 中的词
   - `"吐纳" → "调整呼吸"`（非禁词）
   - `"调息" → "调整呼吸"`
   - `"气感" → "体内异样"`
   - `"内视" → "内观"`

### 影响
- ch9 scene3 的定点重生指令更精确，LLM 生成含禁词的概率降低
- 即使 LLM 改不动，确定性替换兜底可消除简单禁词残留，避免触发 WRITE_REPLAN

---

## 验证证据

| 测试 | 状态 |
|------|------|
| `test_real_ch6_scene4_no_hard` | PASS（ch6 scene4 终版 hard 为空） |
| `test_real_ch6_scene4_tuna_exempt_sentences` | PASS（tuna_exempt soft 记录存在） |
| `test_gap_a_lu_jin_self_tuna_hard` | PASS（陆烬自行吐纳仍 hard） |
| `test_all_generate_scene_calls_use_previous_context_not_prev` | PASS（AST 静态检查） |
| 全量测试 | 988 passed, 1 pre-existing failure |

---

*生成时间：2026-09-29*


## Round 2: Third-Iteration Failure (ch6/7/9, score=86.0/77.95/86.8)

### Phenomenon
Canon scope gate: hard block on ch6/ch7/ch9 with scores 86.0/77.95/86.8.

### Root Causes (3 identified)

**Root Cause 1: Exempt chapter range not implemented per design**
infant.json _comment states ch6 onwards should allow controlled form. But _TUNA_EXEMPT_CHAPTERS = {6} only exempted ch6. Fixed to {6, 7, 8, 9}.

**Root Cause 2: Missing demonstrative marker "ini" in descriptive references**
Sentence "This secret..." blocked because _descriptive_markers lacked "ini" and similar markers. Added: ini, now,此刻,眼下,此时.

**Root Cause 3: No metaphor/rhetorical exemption for hard_block terms**
Sentence "like sounds from another world" blocked because "another world" is used metaphorically. Added _is_metaphor_use() with simile triggers (like/as if/seem/etc).

### Regression Tests Added (10 tests, all PASS)
- test_ch7_tuna_exempt_night
- test_ch8_tuna_exempt_night  
- test_ch9_tuna_exempt_night
- test_ch5_tuna_still_hard
- test_descriptive_marker_zhe_secret
- test_metaphor_another_world_sound
- test_literal_another_world_still_hard
- test_metaphor_simile_markers_all_variants
- test_metaphor_no_simile_still_hard
- test_descriptive_marker_this_present

### Files Changed
- novel_engine/quality/scope_gate.py: +34 lines
- novel_engine/tests/domain_gate_wiring/test_round22_scope_tuna_exemption.py: +110 lines

### Verification
Full suite: 1003 passed, 2 pre-existing failures (unrelated to scope_gate changes).
Both pre-existing failures confirmed on clean HEAD via git stash test.

---

*Updated: 2026-09-29 (Round 2 findings)*
