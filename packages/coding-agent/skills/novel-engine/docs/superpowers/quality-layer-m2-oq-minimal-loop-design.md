# M2 OQ 最小闭环（离线可跑） — 设计

* **作者:** lead
* **基线:** M2 OQ-gate 地基（judge_calibration / retention_ingest / drift 的 no-op 基础层已落地，PR #31）
* **目标:** 把 OQ1/OQ2/OQ4 三个外部输入用**最小可用**的本地替代品喂进去，让"爆款可控"闭环**离线就能跑通**（不依赖真实 retention 平台、不依赖 ~200 章人工标注、不依赖外部报警端点）。

## 设计原则
1. **不破坏 no-op 不变量**：三个地基模块在没有外部输入时仍是显式 no-op（不臆造参考/feed/通道）。本设计是"喂真实（虽是最小）输入"，不是"让模块假装工作"。
2. **纯本地、无网络、无 LLM**：gold set 是人工写死的 JSON；retention feed 是本地 JSON 文件；alarm channel 是本地 log 文件。全部可离线复现。
3. **可测**：每个新模块合成 fixture + tmp_path + 真实 Store，提交前全量门禁。
4. **可替换**：本地最小输入是"占位"，真实 OQ1/OQ2/OQ4 落地后直接换数据源，模块接口不变。

## 三个新件（都在 `condensed/` 下，纯加法）

### 1. `gold_set.py`（OQ2 最小 gold set）
```
load_gold_set(path) -> list[gold_record]   # gold_record = {chapter, scores{爽点,钩子,压抑}, rubric}
```
- 读一个本地 JSON 文件（~20 章人工标注 + 统一 rubric 字符串）。
- 文件不存在 / 空 → 返回 `[]`（显式 no-op，不臆造参考）。
- 校验：每章 scores 三维 int 0-5；坏记录剔除（复用 judge_calibration._valid_scores 的纪律）。
- 这是 OQ2 "~200 章 gold set" 的**最小离线替身**：20 章够跑通 `score_agreement`（min_n 可临时设 15-20）。

### 2. `retention_feed.py`（OQ1 最小 retention feed）
```
load_retention_feed(path) -> list[feed_record]
# feed_record = {chapter, read_through:float0-1, reread:float0-1, abandon_rate:float0-1, source, captured_at}
```
- 读一个本地 JSON 文件（每章三率 + source/captured_at）。
- 文件不存在 / 空 → 返回 `[]`（显式 no-op，不臆造留存）。
- 校验：float 0..1（拒 bool/int）、chapter int（拒 bool）；坏记录剔除。
- 这是 OQ1 "读者分析导出" 的**最小离线替身**。

### 3. `alarm_log.py`（OQ4 最小报警通道）
```
class AlarmLog:
    def __init__(self, path)   # 本地 log 文件（JSONL）
    def emit(self, drift_window, telemetry_row_id) -> None
```
- 把 drift 的 suspect 事件（drift_window + telemetry row id）append 到本地 JSONL 文件。
- 这是 OQ4 "报警到哪" 的**最小离线替身**：log 文件。真实 OQ4（metrics/endpoint）落地后，把 `emit` 换掉即可，drift 模块不动。

### 4. `viral_loop.py`（离线闭环驱动，纯函数，不写库除非传入 db）
```
def run_viral_loop(*,
                   gold: list, verdicts: list, feed: list,
                   retention_db=None, alarm: AlarmLog|None = None,
                   window=10, min_n=30) -> dict:
    """离线爆款可控闭环：
    1. 若 retention_db 且 feed 非空 → ingest_retention(db, feed) 写 retention 表
    2. score_agreement(gold, verdicts, min_n) 算一致性
    3. rolling_agreement(gold, verdicts, window, min_n) 算漂移窗口
    4. 若窗口 suspect 且 alarm → record_drift_suspect(db, window) + alarm.emit(window, row_id)
    5. 返回 {agreement, drift_window, ingested_retention:int, suspect_emitted:bool, ready:bool}
    无 gold → agreement ready=False（no-op）；无 feed → ingested_retention=0（no-op）；
    无 suspect → 不 emit。全部显式，不臆造。"""
```
- 这是把三个地基模块**串成一条线**的驱动器。它不发明新指标，只做编排 + 把 alarm 钩子接上。

## 数据文件（本地 fixture，进 skill 的 `fixtures/` 或 `tests/fixtures/`）
- `gold_set_20.json`：20 章人工标注（scores + rubric）
- `retention_feed_20.json`：20 章三率（read_through/reread/abandon）
- 这两个是"可跑通闭环"的最小数据集，真实 OQ 落地后替换。

## 明确不做（留给真实 OQ / 后续 M2）
- 真实 retention 平台对接（OQ1 答了才换数据源）
- ~200 章完整 gold set 的人工标注流程（OQ2 答了才扩量；本设计 20 章够跑通）
- 外部报警端点 / metrics 系统（OQ4 答了才换 channel；本设计 log 文件够离线）
- LLM climax 分类（更晚的 M2 层）
- 全局剧情走向的显式控制面（"第 X 章必须爽点爆发"）—— 这是"可控爆款"的另一半，本设计只闭环"质量度量 + 漂移报警"，不闭环"剧情走向控制"

## 构建顺序（TDD 红-绿-重构）
1. `gold_set.py` + `gold_set_20.json` → 检查点：load 出 20 条 well-formed；缺文件 → []
2. `retention_feed.py` + `retention_feed_20.json` → 检查点：load 出 20 条；坏记录剔除；缺文件 → []
3. `alarm_log.py` → 检查点：emit 写 JSONL；无 suspect 不写
4. `viral_loop.py` → 检查点：串起 ingest + score + rolling + record + emit；各 no-op 路径正确

全量门禁 = condensed 基线 3 既有失败 + 新增 OQ-闭环测试全绿。
