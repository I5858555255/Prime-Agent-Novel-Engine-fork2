# 质量层 M2 OQ 门批次 — 设计（judge_calibration / retention 摄取 / 漂移+报警 的可推进部分）

* **作者:** juliandbarnard
* **基线:** v0 + M2 早批次（main `d699ca7` + PR #31 待合）；`retention` DDL 已随 v0 落地。
* **范围:** M2 路线图里**阻塞在 OQ1（retention feed）/ OQ2（gold set）**的三项。这些项的「核心」（真实校准 / 真实留存摄取 / 真实漂移报警）**依赖外部输入**，本轮**不做**（OQ 未答前是空壳，强做是 M2-roadmap 明确反对的「盲建」）。
* **本轮做:** 把「OQ 落地前就能确定性、可测、无网络」的**基础层**先立起来，让 OQ1/OQ2 一落地即插即用：
  1. `judge_calibration.py` — 校准协议的数据契约 + 确定性评分函数（给一份 gold set 输入，算 judge–human 一致性）；**没有 gold set 时是 no-op**（不臆造参考）。
  2. `retention_ingest.py` — 留存摄取的数据契约 + 确定性 ingest（读一个 feed 结构 → 写 `retention` 表）；**没有 feed 时是 no-op**。
  3. `drift.py` — 漂移指标的确定性计算（judge–human 一致性窗口的 rolling 指标 + 阈值 suspect 事件）；**没有校准样本时是 no-op**。
  4. OQ 门文档：把「每项需要 OQ 提供什么、落地前是什么 no-op、落地后接哪」写死。

**纪律:** 纯加法、无网络、无 LLM（校准的「评分」是确定性一致性度量，不是再跑 critic）；全部合成 fixture 可测；提交前全量门禁。

---

## 1. 外部门（OQ1/OQ2/OQ4）—— 写死「落地前 / 落地后」

| 门 | 提供什么 | 落地前（本轮） | 落地后（接入点） |
|---|---|---|---|
| **OQ1** retention feed | 每章 read-through / reread / abandon-rate（交付/读者分析导出） | `retention` 表已有（v0）；`retention_ingest` 是确定性 ingest，**无 feed → no-op（不写、不臆造）** | feed 结构定义好了 → `retention_ingest.ingest(db, feed_records)` 直接写 `retention` 表；`ledger_head`/`migration` 记一次 `retention_ingest_v1` |
| **OQ2** gold set + rubric | ~200 章人工评分（每维 0–5 + 标注）+ 打分口径 | `judge_calibration` 的确定性一致性函数**没有参考集 → no-op**（不臆造参考） | gold set 结构定义好了 → `judge_calibration.score_agreement(gold, verdicts)` 算一致性；喂给 `drift` 触发 suspect |
| **OQ4** 报警通道 | 漂移指标报警到哪（log / metrics 文件 / 外部端点） | `drift` 只**产出指标 + suspect 记录**（哈希链叶），**不选通道**（OQ4 未定） | 通道定了 → `drift` 的 suspect 事件路由到该通道（本轮只留 `emit` 钩子） |

**关键不变量:** 本轮**不**让任何 OQ-gated 项在没有外部输入时「假装」工作（不臆造留存、不臆造人工参考、不臆造报警通道）。所有 no-op 都是**显式**的（返回空 + 一条可观测的「未就绪」信号），绝不静默。

---

## 2. `judge_calibration.py`（确定性一致性度量）

### 数据契约
```
gold_record = {chapter:int, scores:{爽点:int0-5,钩子:int,压抑:int}, rubric: str}
verdict_record = {chapter:int, scores:{...}, prompt_ver:str, judge_model:str, knob_set_hash:str}
agreement_result = {
  n:int,                         # 参与评分的章数（gold ∩ verdict）
  per_dim_mae: {爽点:float, 钩子:float, 压抑:float},   # 每维 |judge−human| 均值
  per_dim_corr: {爽点:float, 钩子:float, 压抑:float},   # 每维 Pearson（n<2 时为 None）
  overall_agreement: float,      # 3 维 per_dim_mae 的均值（0..5）→ 归一 1 − mae/5
  prompt_ver:str|None, judge_model:str|None,
  ready:bool,                   # 参考集是否齐备（n>=min_n）
}
```

### API
```
def score_agreement(gold: list[dict], verdicts: list[dict], min_n:int=30) -> dict:
    """确定性 judge–human 一致性。
    - 只对 gold ∩ verdicts（同 chapter）评分；缺参考的章不参与。
    - per_dim_mae = 该维 |judge − human| 的均值；overall_agreement = 1 − (三维 mae 均值)/5。
    - n < min_n → ready=False（参考不足，**不**报「校准良好」）。
    - 无 gold（空）→ ready=False + n=0（**显式 no-op，不臆造参考**）。
    - 纯确定性（无 LLM、无网络、无随机）。"""
```
**no-op 语义:** `score_agreement([], verdicts)` / `score_agreement(gold, [])` → `ready=False, n=0`（可观测的「参考未就绪」），**不**抛错、**不**臆造。

## 3. `retention_ingest.py`（确定性摄取）

### 数据契约
```
feed_record = {chapter:int, read_through:float0-1, reread:float0-1,
               abandon_rate:float0-1, source:str, captured_at:TEXT}
```
### API
```
def ingest_retention(db, feed_records: list[dict], source:str) -> int:
    """把 feed 记录写进 v0 已建的 `retention` 表（UPSERT on chapter）。
    - 纯确定性：只校验字段类型/范围（float 0..1），写库，返回写入行数。
    - **空 feed_records → 返回 0（显式 no-op：无 feed 就不写、不臆造）**。
    - enable_quality 门控（retention 表在门控块里；无表 → RuntimeError）。"""
```
**no-op 语义:** 空 feed → 0 行（可观测），不创建假数据。

## 4. `drift.py`（确定性漂移指标 + suspect 事件）

### 数据契约
```
drift_window = {prompt_ver:str, judge_model:str, knob_set_hash:str,
                window_chapters:list[int], agreement:float,
                suspect:bool, suspect_reason:str|None}
```
### API
```
def rolling_agreement(gold, verdicts, window:int=10) -> dict:
    """最近 `window` 章的 judge–human 一致性（复用 judge_calibration.score_agreement）。
    无参考（gold 空）→ suspect=False + reason='no_reference'（**显式 no-op，不臆造**）。"""
def record_drift_suspect(db, drift_window: dict) -> str:
    """一致性跌破阈值（agreement < 0.5 或 n < min_n）→ 在 `telemetry` 写一条
    kind='drift_suspect' 的哈希链 suspect 事件（绑 prompt_ver/knob_set_hash），返回 telemetry 行 id。
    - 只**记录** suspect；报警通道（OQ4）由后续路由（本轮留 `emit` 钩子，默认 log-only）。
    - 无参考 → 不写（no_reference 不是「漂移」，是「未就绪」）。"""
```
**no-op 语义:** 无参考集 → 不算漂移、不写 suspect（避免把「未校准」误报成「漂移」）。

---

## 5. 构建顺序 + 检查点（TDD 红-绿-重构）

1. `judge_calibration.py`（纯函数，无依赖）→ 检查点：`score_agreement` 合成 gold+verdicts → mae/corr/ready 正确；空 gold → ready=False 显式 no-op。
2. `retention_ingest.py`（依赖 v0 `retention` DDL）→ 检查点：合成 feed 写库 + 行数；空 feed → 0 行 no-op；enable_quality 门控。
3. `drift.py`（依赖 judge_calibration）→ 检查点：rolling_agreement 复用一致性；无参考 → no_reference no-op；record_drift_suspect 写 telemetry 叶（哈希链）+ 未参考不写。

全在 `feat/quality-layer-m2-early`（M2 早批次同 PR，作为一个「M2 OQ-门基础层」commit）或独立 commit。全量门禁 = condensed 基线 3 既有失败 + 新增 OQ-gate 测试全绿。

## 6. 明确不做（留给 OQ 落地后）
- 真实 retention feed 的抓取/对接（OQ1 答了才有源）。
- 真实 gold set 的人工标注流程（OQ2 答了才有参考）。
- 报警通道路由（OQ4 答了才选 log/metrics/endpoint）。
- LLM climax 分类（更晚的 M2，旋钮规则引擎之上的下一层，本轮 `knob_rules` 保持纯 JSON）。
