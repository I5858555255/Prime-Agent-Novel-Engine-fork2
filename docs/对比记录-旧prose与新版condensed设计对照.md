# 对比记录：旧 prose 模式 vs 新版 condensed 简写模式

> 记录时间：2026-11-03，Asia/Shanghai。
> 目的：防止后续遗忘两套流程之间的关系。后续开发以新版 condensed 为目标；旧版 prose 仅在显式要求时使用。

---

## 1. 两套流程定位

| 维度 | 旧版 prose（仍在代码中） | 新版 condensed（默认，3800 章项目） |
|---|---|---|
| 入口 | `python -m novel_engine.pipeline.production_runner` / `await novel_engine.generate_chapter(n)` | `PYTHONPATH=src python -m novel_engine.condensed run --output <root> --end 3800` / `await novel_engine.generate_condensed_chapter(n, output_root=...)` |
| 交付物 | 完整正文（约 7500–10000 字/章，`chapters/novel/`） | 结构化简写（900–1400 字事件链+场景卡）+ 自包含外部扩写包（4000–5000 字正文由外部 LLM 完成） |
| 评审 | 120 分制 9 维文学评分 + 净化/长度门控 | 逻辑与信息覆盖核对（事件引用、因果、知识边界、时间顺序、状态前值、证据绑定），不用散文评分 |
| 存储 | 运行时 `state.db` + checkpoint + 场景日志 | 独立 `manuscript.sqlite3`（WAL，哈希链，原子提交） |
| 完成语义 | 章节文件落盘 | SQLite 提交 + 扩写包导出校验；外部成稿另标 `external_expansion_verified`，不与简写完成混淆 |
| 代码位置 | `pipeline/`、`quality/`、`agents/`、`core/`（旧状态机） | `condensed/`（runner/store/contracts/export/prompts/render/timeline/context/api） |

两套数据与完成状态**完全分离**（文档 `简写生产与外部扩写.md` §29：condensed 不调用原正文模式的文学总分、正文润色或 10000 字补足门）。

---

## 2. 旧版有参考价值的设计（condensed 已吸收）

- **StateDB 确定性查询**思想保留：condensed `store` 建 `characters` 表记录首次/最近出现章节，按首次登场过滤生成上下文（规避旧版"全员当当前演员"故障）。
- **请求预算**思想保留：旧 `core/execution.py` 的 `AttemptBudget`（max_llm_calls/max_wall_seconds）被 condensed 的 `DurableBudget`（继承 `AttemptBudget` + 持久化 `Store.allowance`）复用。
- **原子写/发布事务**：`core/publication_transaction.atomic_write` 被 condensed store 直接引用。
- **模型路由**：condensed `runner` 引用旧 `core/model_router.ModelRouter`。
- **模型只给相对权重、程序精确分配字数**：源于旧版 R12"目标中途漂移"教训（冻结批次级目标 7500 常量），升级为 condensed 的 `chapter_budget()` 精确预算。
- **原始响应留档审计**：旧版质量门控迭代经验（评分/修复历史落盘）在 condensed 中保留"原始模型响应 + usage + 候选稿"审计目录。

## 3. 旧版被记录的弯路 → condensed 如何规避（核心对照表）

来源：`OPTIMIZATION_LOG.md`（R1–R13 + Q1–Q16 共识 + SDD 11 任务）、`docs/生成与质检故障复盘.md`。

| 旧版弯路 | 记录出处 | condensed 的规避 |
|---|---|---|
| R1–R13 打地鼠：每轮局部修复制造下一轮问题 | OPTIMIZATION_LOG §1、§7 | condensed 是独立子包+独立 SQLite，从事件链确定性生成，不套旧"正文→净化→长度门"脆弱链 |
| R2 移动字数目标（5000→3500→3000 追不上） | OPTIMIZATION_LOG R2 | `ProductionConfig.chapter_budget()` 批次冻结 4210/4211 字精确预算；模型只给场景相对权重 |
| R7–R11 成品污染（【场景】/（章末钩子）/节拍点 泄漏） | OPTIMIZATION_LOG R7–R11 | 交付物是 JSON 事件链+场景卡，无正文拼接环节；扩写由外部完成 |
| R10 章末钩子可见拼接 | OPTIMIZATION_LOG R10 | 简写无 hook 拼接点 |
| 审稿缺失维度默认零、信任模型总分 | 故障复盘表第 1 行 | `validate_review` 强制九项完整+范围合法，程序按权重重算；模型不判通过/失败 |
| 修订缺冻结蓝图，错误建议诱导新增设定 | 故障复盘表第 6 行 | 修订携带冻结场景/事件/伏笔；扩写提示词明令不得新增因果性设定 |
| 角色库全员当当前演员（陈老根被排到第 1 章） | 故障复盘表第 5 行 | `store.characters` 首次/最近出现索引 + 按章过滤；`generic_role()` 拦截村民/守卫等未具名群体 |
| 模型自报字数 | 故障复盘（Q3 共识） | 全程程序计数 `count_han.py`/`han_count()`，提示词明令自报无效 |
| 无界重试 | 故障复盘 | 默认 16 请求+900 秒/操作；`extend-budget` 有上限(+16/+1800)且留记录；耗尽保留诊断、不推进后章 |
| 三票取最高（91.7/85.8/77.5） | 故障复盘（2026-10-26 试验） | §95：相同合同下已被有效判失败的稿件不得靠再评审取得通过 |
| 时间线关键词误拦"成年壮汉" | 故障复盘表末行 | 第三版合同：模型只给相对时段，程序算绝对日程（岁=365、月=30 内部估算） |
| 断点续跑串状态 | 故障复盘（2 章诊断） | SQLite 事务 + 哈希链；续跑从首个未提交章继续，`invalidate` 显式归档撤销 |

## 4. 已知缺口（condensed 有意留白，非遗漏）

旧版 `quality/`（47 文件）的确定性门控（degeneration/punctuation_health/timeline_gate/continuity_gate/scope_gate/temporal_continuity）是**正文**防线；condensed 简写不产正文，故不走这些门控。
风险集中在**外部扩写环节**：外部 LLM 扩成 4000–5000 字正文时，当前只有软性防线（扩写提示词约束 + 验收提示词 + `count_han.py` 字数 + 事实变更清单可选回流），无程序化确定性验收。若要求 3800 章全部成稿且程序化验收，可考虑移植旧版门控为"扩写后验收器"。这是设计决策点，不是 bug。

---

## 5. 待办与决议（2026-11-03 更新）

1. ~~核实 bible 版本~~ **已核实：bible 为最新版，无需更新**。6 个文件均为合法 UTF-8；git 历史仅 `b89de78` 一次改动；`condensed_milestones.json` policy 声明"decision_rules 与 author_intent 优先于旧图，保留 49 节点并修正乱码/倒置日期，旧文件不改"。内容与 milestones 无矛盾。
2. **真实生成测试**：先跑 3 章小批验证（`condensed run --end 3`），用 Agnes 3.0 Flash 真实 API；后台自动续跑 + 定期检查点；STOP 文件可随时叫停。
3. **质检**：简写侧已有证据合同（validate_plan/validate_review/validate_timeline + 哈希链 + 事务提交），**不需要新增**；扩写侧（外部 LLM 4000–5000 字正文）是缺口，后续如要程序化验收 3800 章成稿，再移植旧版 `quality/` 门控为"扩写后验收器"。
4. **自动推进节奏**：3 章验证通过 → 10 章小批 → 视结果决定全量 3800 章节奏。每批结束 agent 报告 status（提交数/预算余量/失败诊断），不转人工队列。
