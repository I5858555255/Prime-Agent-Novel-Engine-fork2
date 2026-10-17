# PANE novel-engine 项目现状与实际问题说明

- 日期：2026-10-12
- 分支：feat/stability-quality
- 进度：checkpoint ch1-291 已提交（force_best_draft 模式），批次 ch294-316 运行中（含 9 个 FAILED 章重跑）
- 模型：Agnes-3.0-Flash（Token Plan Starter 主流水线 + 免费档辅助），全 phase 同一模型

## 一、背景（已解决，供上下文理解）

- 流水线：director（章级大纲）→ scene craft（分场）→ writer（正文）→ reviewer（3 票评分）→ fix loop（patch/rewrite 迭代）→ det gate（确定性硬项）→ publish 决策（88 达标 / 85-87.9 gray-band 抽查 / <85 draft）
- 已建成机制：concept_unlocks（概念解锁状态替代关键词黑名单）、设定贯通（bible 5 文件接入 Director/Reviewer/Writer 三层）、中纲层（bible 与单章大纲之间的章级意图层）、中文导演话语泄漏两层检测（短语兜底 + schema 反查）、失败分类器（batch_audit 自动验收写 backlog）、hermetic 测试纪律、pyflakes + 全量 pytest 门禁
- 已修复的根因模式（12 类，见 novel-engine-runner 技能 references/root-causes.md）：staticmethod 绑定丢失、提取后局部变量未入参、except 吞 NameError、概念导入空转、测试间缓存污染、mojibake、概念锁未真实触发等

## 二、当前真实存在的问题（按严重度排序）

### 1. 评审方差（三评极差 > 15）未根治 —— 最严重
- 现象：同一章三次评审分数极差 > 15，触发"强制人工复核（不自动重采）"。批次 ch264-293 中 5/30 新章失败（272/282/285/290/293，16.7%），本批次 282/285 复现
- 已尝试：review phase temperature=0（agnes_30_token profile）——实测无效
- 影响：每批次稳定吃掉 10-17% 通过率，且失败章永远停在人工复核队列
- 疑点方向：reviewer prompt 采样一致性、三票之间评分标准漂移、LLM JSON 输出稳定性、dim_scores 归一化边界

### 2. 泄漏硬项"检测到但消解不掉"（写作纪律源头问题）
- 现象：ch267 两次 attempt 均命中 `[泄漏] 残留导演话语（规划式表述）: 接下来要做的`，fix loop 修复后重写文本再次出现同类表述 → det_hard 非空 → 否决 → FAILED（86.8 分）
- 同类：ch239 生成偏离中纲，出现任务卡明确禁止的"北地刺客公会/袖箭"超纲设定 + 章末钩子缺失 + 时间线冲突（reviewer 3 个 high，评分 60.6→58.35→53.6 循环下降）
- 根因：LLM 的导演腔/规划式叙述习惯在生成源头反复触发；检测是事后拦截，修复循环"删一处冒一处"（打地鼠）；长上下文 prompt 中禁止项被稀释，约束力不足
- 方向：把高频规划式表述写入 Writer 写作纪律 prompt（生成前避免），而非只靠检测后修复

### 3. fix loop 无法消解 plot 层问题
- 现象：reviewer 的 plot_consistency/foreshadow_execution 类 high 问题，patch 只能做局部文字修订，无法重构场景逻辑/剧情结构 → 评分循环下降或停滞
- 结果：要么 hard-failed（score=None 无产物，如 ch239 前三次、ch266/287/292），要么依赖 force-best 兜底提交低分 draft
- 本质：fix loop 的修复能力上限与 reviewer 的批评维度不匹配

### 4. force-best draft 堆积，novel/ 停更
- 现象：novel/ 只有 ch1-230；ch231+ 全部在 draft/（force_best_draft 模式），checkpoint 却标记 complete=True
- 现状共识：draft 73-88 分算最终内容（带具体缺陷的隔离章除外）——但发布源不一致（novel/ vs draft/）是长期隐患，一旦要"正式发布源"需要批量迁移
- 相关：gray-band 授权与最终门否决不一致问题已修复（ch267 归因，见 backlog），但"未授权即低分"路径（本批次 ch267 的泄漏硬项）是另一条未覆盖路径

### 5. 测试套件 9 个环境差异失败
- 现象：9 failed / 1177 passed，全部是配置断言：active_profile==siliconflow、model==deepseek-ai/DeepSeek-V3.2（deepseek 时代遗留，切换 agnes 后必然失败）+ 1 个已知 pre-existing（test_same_location_cluster_shared_objects_not_flagged）
- 影响：CI/全新 clone 永远无法全绿，环境差异与真实回归难以区分；建议把模型配置断言改为参数化（读 llm_providers.json 实际 active_profile 断言，而非硬编码 deepseek）

### 6. 已记录的低优先级问题（未修）
- audit_mid_outline 伏笔编号 F004/F005 错标（疑似伏笔 ID 匹配索引错位，需单独立项）
- P6-1 传授检测误报（需加"修炼宾语近窗"条件，未修）
- fact_changes 101 条 pending 已澄清为提案层正常记账（非缺陷），但 StateDB 与真实故事状态落差需持续监控
- 规则/词表膨胀风险：concept_unlocks 已取代关键词打地鼠，但新边界案例若持续出现仍需防膨胀纪律

## 三、架构层面待评估

- 单章端到端 8-12 分钟（最快 4 分钟），瓶颈在几十次串行 LLM 调用 + 逐级门禁 + 失败重试，不在模型速率（TPS 100-150 单请求已够）；提速杠杆 = 减少调用次数（合并 review 票、收紧 regen 触发、fix loop 更早止损）或场景级并行（有一致性风险，之前明确不做）
- 无人值守范围：仅 V01/V02（ch1-652）已条款化；V03-V10（约 2950 章，占 80%+）待冷启动，进入时大概率需要人工预判例
- 3600 章按中位 10 分钟/章纯生成 ≈ 25 天（不含失败重试与人工复核）

## 四、给评审者的开放问题

1. 评审方差根因最可能出在哪层（reviewer prompt / 评分聚合 / 模型采样 / 温度策略）？有无低成本验证方案？
2. 泄漏与生成偏离这类"源头纪律"问题，除加 prompt 约束外，有无机制层更稳的解法（如生成后一致性校验 + 确定性重写）？
3. fix loop 的修复能力是否应该引入"场景级重构"级别的修复路径（代价 vs 收益）？
4. force-best draft 作为事实发布源的长期策略是否可接受，还是需要"novel/ 唯一发布源"的强制迁移？
5. 测试套件的模型配置断言参数化改造是否有必要现在做（成本低，收益是 CI 全绿）？
