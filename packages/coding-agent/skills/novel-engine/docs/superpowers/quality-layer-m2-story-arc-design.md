# M2 剧情走向控制面（story_arc） — 设计

* **作者:** lead
* **基线:** M2 OQ 最小闭环设计（同目录 `quality-layer-m2-oq-minimal-loop-design.md`）
* **目标:** 把"全局剧情走向可控"做成**确定性、可测、无 LLM**的控制面——人工写死"第 N 章必须爽点爆发 / 压抑积累到第 M 章必须释放 / 钩子密度曲线"等约束，引擎在 DRAFT/REVIEW 阶段**校验**是否满足，不满足给出可执行反馈。

## 设计原则（与质量闭环一致）
1. **纯加法、无 LLM、无网络**：约束是人工写死的 JSON（"第 N 章爽点≥4"这类），校验是确定性比对。
2. **可测**：合成 fixture + tmp_path + 真实 Store；提交前全量门禁。
3. **不破坏 v0/M2 不变量**：story_arc 是**校验层**，不改 `quality_judge` 的 verdict 语义、不改 `knob_rules` 的 per-stage 旋钮选择——它只在 commit 前检查"当前章的 verdict + knob_set 是否满足人工约束"。
4. **显式 no-op**：没有 arc 约束文件 → 不校验（返回 `{enforced: false, violations: []}`），不静默放行。

## 数据契约
```
arc_constraint = {
  # 三类约束（可任意组合）：
  climax_at: list[int],                    # 这些章必须"爽点爆发"：verdict.爽点 >= threshold
  suppression_release: {chapter:int, min_压抑:int, release_by_chapter:int},
                                           # 压抑积累到第 X 章 ≥ min_压抑，且在第 Y 章前必须释放（爽点≥4 或钩子≥4）
  hook_density: {min_hooks_per_chapter:int, window:int},
                                           # 每 window 章内钩子≥N 的章占比 ≥ ratio
}
```

## API（`condensed/story_arc.py`）
```
def load_arc(path) -> arc_constraint | None:
    """读本地 JSON 约束文件；缺文件/空 → None（显式 no-op）。"""

def check_arc(chapter:int, verdict:dict, knob_set_hash:str,
              arc:arc_constraint, history: list[verdict]) -> dict:
    """对当前章做确定性校验。history = 之前章的 verdict（用于 hook_density / suppression_release 的窗口判断）。
    返回：{enforced:bool, violations:list[str], satisfied:list[str]}
    - 无 arc（None）→ {enforced: False, violations: [], satisfied: ["no_arc"]}（no-op，不静默放行）
    - 每条违反约束生成一条人话反馈（"第 N 章爽点=X < 阈值 4，需要加强"），供 DRAFT/REVIEW 阶段回灌扩写。
    - 纯确定性：同输入同输出。"""
```

## 与 viral_loop 的整合
`viral_loop.run_viral_loop` 加一个可选 `arc: arc_constraint | None` 参数：
```
if arc is not None:
    arc_result = check_arc(chapter, latest_verdict, knob_set_hash, arc, history)
    result["arc"] = arc_result
    # 违反约束的章 → 在结果里标记（不阻塞 commit，但给出可执行反馈；是否阻塞由调用方定）
```

## 明确不做
- **不**改 verdict 语义（judge 还是 0-5 bounded，story_arc 只读 verdict 不写）
- **不**做 LLM 自动调剧情（那是更晚的 M3）
- **不**做"自动修复"（只校验 + 反馈；修复靠回灌 DRAFT/REVIEW 阶段，由现有扩写循环处理）
- 约束是**人工写死**的（JSON），不是 LLM 生成的（那会引入非确定性）

## 构建顺序（TDD 红-绿-重构，接在质量闭环之后）
1. `story_arc.py` + `arc_constraints.json` fixture → 检查点：load 约束；check_arc 三类约束各覆盖；无 arc → no-op；违反 → 人话反馈
2. `viral_loop.py` 加 `arc` 参数 → 检查点：传 arc 时结果含 `arc` 子结构；不传时 `arc` 缺省

全量门禁 = condensed 基线 3 既有失败 + 质量闭环测试 + story_arc 测试全绿。
