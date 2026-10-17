# 运行手册（Runbook）：小说生成引擎稳定性与质量双 SLO

本手册用于指导在 `feat/stability-quality` 分支上运行、监控与收尾小说生成引擎，确保系统满足 **稳定性 SLO** 与 **质量 SLO（双 SLO）**。

---

## 1. 启动约束（Global Constraints）

引擎通过 **SiliconFlow API**（模型 `Qwen/Qwen3.5-4B`）调用大模型，必须遵守以下约束：

- **API 基地址（base）**：`https://api.siliconflow.cn`
- **模型（model）**：`Qwen/Qwen3.5-4B`
- 调用路径统一经 `runtime_config.json` 中的 `provider` 字段配置，禁止在代码中硬编码其他 provider。

### `runtime_config.json` 关键字段

| 字段 | 说明 / 取值 |
|------|-------------|
| `provider` | 模型供应商标识，指向 SiliconFlow API（`https://api.siliconflow.cn`，模型 `Qwen/Qwen3.5-4B`） |
| `quality.publication_line` | 发布线阈值，**固定为 `88`**（低于该分的章视为不达标） |
| `quality.target_avg_score` | 目标平均质量分，**固定为 `88`** |
| `pipeline.max_review_retries` | 单章最大评审/重试次数，决定自动修复的轮询上限 |
| `quality.fix_threshold` | 触发自动重写的质量下限，**固定为 `60`**（低于该分强制重生成） |

> 修改上述质量门阈值后，必须重新跑回归门与 `aggregate_slo.py` 复核双 SLO（见第 5 节）。

---

## 2. 单轮生成

使用迷你测试运行器生成单轮章节，推荐使用真实生成（非 mock）：

```bash
python -m novel_engine.tests.mini_test_runner 20 --real
```

> 参数 `20` 表示本轮生成 20 章；`--real` 表示走真实 API（SiliconFlow / Qwen，请确认网络与配额可用）。
> 也可使用项目既有的启动模板/入口脚本，只要产出 `novel_engine/audit/mini_test_report.json` 即可。

每轮结束后，立即观察双 SLO 判定：

```bash
python -c "import json; from novel_engine.tests.slo_gate import evaluate_slo; r=json.load(open('novel_engine/audit/mini_test_report.json')); print(evaluate_slo(r))"
```

关注输出中的两个布尔值：

- `meets_stability`：稳定性是否达标（零硬失败、失败率低于阈值）。
- `meets_quality`：质量是否达标（min/avg 分数、pacing、innovation 等维度满足要求）。

---

## 3. 阶段 1 出口（200 章 SLO 判定）

当累计生成约 200 章后进入阶段 1 出口检查，要求**连续 10 轮（r21..r30）**同时满足：

- 零硬失败（无 exception / 无 catastrophic 失败）；
- 失败率 `< 1%`；
- 质量维度满足：
  - `min_score >= 82`（即 >= `publication_line`）；
  - `avg_score >= 88`（即 >= `target_avg_score`）；
  - `pacing >= 9`；
  - `innovation >= 8.5`。

若任一条件不满足：

- **回到 Task 1**，调整 `retry_temperatures`（重试温度序列）与 `cache_bust_suffix`（缓存击穿后缀），再重新跑轮次直至达标。

复核双 SLO 聚合报告（需已存在多轮 `mini_test_report_*.json` 或在单轮报告上运行）：

```bash
python -m novel_engine.tests.aggregate_slo
```

---

## 4. 人工终校队列

每轮/每阶段产出 `novel_engine/audit/needs_human_review.json`，其结构包含 `queue` 列表，记录自动流程判定为不达标或需人工把关的章节。

处理流程：

1. 检查 `needs_human_review.json` 的 `queue`；
2. 对队列中每一章，人工润色或触发重生成（`_regenerate_chapter`）；
3. 处理完清空或更新对应条目，避免重复进入队列。

---

## 5. 回归门

每次改动（含质量门阈值调整）后，必须保证测试全绿：

```bash
python -m pytest novel_engine/tests/ -q
```

要求：`16 passed`。

改动质量门（如 `fix_threshold`、`publication_line`、`target_avg_score`）后，额外运行聚合 SLO 复核：

```bash
python -m novel_engine.tests.aggregate_slo
```

确认 `verdict == "PASS"`（即 `meets_stability` 与 `meets_quality` 均为真）。

---

## 6. 已知局限

- **专项 agent 尚未接入生成管线**：pacing / innovation / style 三个专项 agent 已创建，但截至本手册撰写时**尚未接线到主生成管线**（Ruling 10）。其效果需待真实跑量验证后再行接入。当前质量评分仍以既有管线为主。
- **`_regenerate_chapter` 温度轮询机制**：重生成并非强制指定目标温度，而是通过 provider 内部的 `retry_temperatures` 温度轮询**间接换温**实现。若需更精确的温度控制，需后续改造 provider 接口。

---

## 快速命令汇总

```bash
# 单轮真实生成 20 章
python -m novel_engine.tests.mini_test_runner 20 --real

# 观察单轮双 SLO
python -c "import json; from novel_engine.tests.slo_gate import evaluate_slo; r=json.load(open('novel_engine/audit/mini_test_report.json')); print(evaluate_slo(r))"

# 聚合多轮双 SLO 报告
python -m novel_engine.tests.aggregate_slo

# 回归门
python -m pytest novel_engine/tests/ -q
```
