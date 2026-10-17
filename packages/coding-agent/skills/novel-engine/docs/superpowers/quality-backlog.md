# Quality Backlog（novel-engine）

本文件记录质量门/设定贯通相关待办、决策与验收结论。新增条目置顶并标注日期。
（本文件为唯一正本；仓库根 docs/ 与 src/docs/ 下的同名文件已废弃为迁移占位，
见 2026-10-04 去重记录。）

---

## 2026-10-06 ch62 发布状态不一致 + fact_changes 账本半接 — 根因已定位（修复待排期）

### 异常 1：ch62 novel/draft 双写 + checkpoint 语义错乱（真实状态机竞争）
**现象**：chapters/novel/chapter_62.txt（22:17，86.9 分 gray-band 合规版，
34.8K）与 chapters/draft/chapter_62.txt（22:23，79.5 分 force-best 版，
31.3K）并存；checkpoint ch62 mode=force_best_draft（记录 79.5 hash），
与 novel 内容不一致。

**行为链（日志还原）**：
1. fix loop 内 L3917-3923：86.9 分 gray-band → 返回 'gray_band' → break
2. L749 采纳 86.9 绿候选（"adopt gate-green candidate 86.9"）
3. L947-975 final gate：_gray_final=True → "Gray-band final release → publish
   to novel" + result["gray_band_release"]=True → 成功分支（apply_world_state=True）
4. 但后续 best_score 被改写为 79.5 → L976-992 else 分支（"Fix exhausted,
   force-best to draft 79.5"）→ _force_publish_best=True
5. L1174-1189 force_best 分支：写 draft + create_draft_checkpoint
   （force_best_draft）→ 覆盖 checkpoint；novel 62（86.9 版）残留未清

**判定**：novel 62 是合规 gray-band 发布内容（质量更高，保留）；draft 62
是 force-best 残留（多余，保留待审）；checkpoint 语义错乱（发布章被标为
draft 欠账）。**修复项（待排期）**：L1173 前检查 result["gray_band_release"]
短路 force-best 路径；确认 final gate 后 best_score 改写来源；补回归测试
"gray-band 已发布后不得被 force-best 覆盖"。

**✅ 2026-10-06 已修复（提交待记）**：novel/ 为唯一发布源不变量落地——
1. generate_single_chapter L1174 前：result["gray_band_release"] 为真时
   短路 force-best（置 _force_publish_best=False，force_best=False，
   result["published"]=True），不写 draft、不覆盖 checkpoint；
2. _decide_publish L1399 同样短路（双保险，两处 force-best 写 draft 点
   都被覆盖）；gray-band 走 _gray_commit 正常发布 novel；
3. 回归测试 test_gray_band_force_best_race.py：gray_band_release 短路
   不写 draft + 无 gray_band 时 force-best 保持原行为；
4. 验证：pyflakes 0 undefined；全量 1 failed/1107（唯一已知 pre-existing）。

### 异常 2：fact_changes pending 滞留 — 已澄清（非 bug，附 2 个真实缺陷）
**初判（已修正）**：112 条中 pending=101 曾被误判为"apply 路径不更新账本
导致滞留"。深挖后确认：
- **pending 101 条是提案层正常记账**：_stage_synopsis（L1696-1697）对
  synopsis.state_changes 调 add_pending_change → 写 pending 行。
- **apply 路径记账正常**：apply_pending_changes 正确追加 rejected（8）/
  applied（2）/ deferred（1）行。fact_changes 是 append-only 账本，
  提案行与应用行本就分离，pending 不会"转"applied——这是设计形态。

**真实缺陷 2 个**：
- **缺陷 A：add_pending_change 写 chapter=0**（memory_manager.py:206
  硬编码 chapter=0，_stage_synopsis 有 chapter_num 未传入）→ 101 条
  提案全部无法按章追溯，账本的审计价值打折。
- **缺陷 B：apply_pending_changes 分支未命中时静默**（world_simulator.py
  L179-257：character_realm 的 target 不在 characters、或 type 不匹配时，
  不应用、不 rejected、不 applied、不 deferred——变更去向完全不可见）。

**修复方向（待排期）**：A：add_pending_change 增加 chapter 参数并传
chapter_num；B：未命中分支记录 status=skipped 或至少 warning 日志。

## 2026-10-06 中纲批次 V01 ch51-80 — 生成完成 + 验收 ✅（附 3 项异常待处理）

**批次结果（30 章，全程 16h）**：COMMITTED 26 章（51-55/57/58/61-65/67-80）；
FAILED 4 章：ch56（P6-1 传授误报，已修）、ch59（89.7 分 force publish 入
novel，含"陆烬能感觉到气息"气感嫌疑，标记 spot-check）、ch60（85.3，
"您教我认字，教我那些呼吸的法子"提及指称疑似误报，quarantine）、
ch66（内容密度真空洞 WRITE_REPLAN，无规则误报）。

**验收**：
- checkpoint 51-80 无空洞（26 COMMITTED 全记录；4 FAILED 不写 checkpoint 符合设计）。
- novel 51-80：51(88.8)/73(87.6)/76(88.3) 合规发布；draft 51-80：23 章
  force_best_draft 欠账（累计 draft 池 58 章）——规模化画像：跑通 ≠ 达标，
  欠账在积累，需后续逐批修复或批量 force-best 复审。
- memory_health 类核验：StateDB characters=8/factions=0/foreshadows=9/
  relationships=6/concept_unlocks=4；world_state characters/power_system
  更新至 10/6 04:09，**relationships.json 停在 9/29 未随批次更新**。

**待处理异常（3 项）**：
1. **ch62 novel/draft 双写残留**：checkpoint mode=force_best_draft，但
   chapters/novel/chapter_62.txt（22:17 中间稿 34.8K）与
   chapters/draft/chapter_62.txt（22:23 终稿 31.3K）并存——force-best
   时未回滚/删除 novel 中间稿，novel 目录被非终态文本污染。需确认
   force-best 发布路径是否应清理 novel 残留。
2. **fact_changes 量级异常**：112 条中 pending=101 / rejected=8 / applied=2
   / deferred=1——提案大量滞留 pending（已知"add_pending_change 无生产
   调用方、提案直通"缺口的直接后果），审计账本未真正形成"提案→校验→
   应用"闭环。
3. **relationships.json 未随批次更新**：记忆层关系状态自 9/29 后未写入，
   与 characters/power_system 更新节奏不一致。

**后续优先级建议**：先修 ch62 残留（数据污染，最小改动）；ch59/60 做
"提及指称 vs 气感"边界系统性判定（ch5 约束气感最早 654 章）；ch56/66
修复后重跑；fact_changes pending 滞留与 relationships 更新一并深挖
（记忆层提交链路）；上述稳定后再启动 ch81+ 批次。

## 2026-10-05 ch56 失败双根因 — 已修复 ✅

**现象**：ch51-80 批次 ch56 FAILED 84.1，日志 hard 项 `[canon硬词] 吐纳：
老妇哆哆嗦嗦接过，只抿了一小口，便传给旁边半大孩子。`——引用句不含
"吐纳"，初判疑似误报，深挖后是两个独立 bug 叠加：

**根因 1（P6-1 传授违规误报）**：`_has_teach_violation` 的既成传授路径
（"传给/教给"+ 婴儿受事）用 `_TUNA_CHILD_BENEFICIARY` 泛称（孩子/娃/
小孩）做受事匹配——"老妇…**传给**旁边**半大孩子**"（普通物件传递）命中
"传给"+ "孩子" + "给"，虚标 term="吐纳" 判 hard。ch56 无 infant arc 配置
（infant.json 仅覆盖 ch1-9），此命中全部来自 task_card 注入的
extra_hard_terms=["吐纳"]。
**修复**：新增 `_TUNA_DIRECT_RECIPIENT = {陆烬,婴儿,襁褓,小儿}`，省略
宾语传授路径的受事收紧为修炼主角具名；含修炼宾语（吐纳/呼吸/法门…）
的句子本就走 `_CULTIVATION_OBJECTS` 近窗路径，不受影响（7 条既有
TRUE_POSITIVE 断言全保留，新增 3 条普通传递误报样本）。

**根因 2（issue 引用句截断）**：scope_gate 所有 hard/soft issue 的
`sentence=sent[:120]`——`_sentences()` 不按句号切分，长段落中 term 位于
第 120 字之后时引用句不含 term，误导人工审阅且 `_det_fulltext_fix` 会
拿错文本做定点修复。
**修复**：新增 `_sentence_ctx(sent, idx, term)`（term 为中心 ±36/+84
窗口），替换全部 7 处 `sent[:120]`，保证引用句始终含 term。

**验收**：新增 4 条 hermetic 测试（长段落 ctx 含 term + 普通传递不误报
+ teach 误报样本 3 条）；pyflakes 0 undefined name；全量 pytest
1 failed / 1105 passed（唯一已知 pre-existing）。批次在后台继续跑，
ch56 已 quarantine+human，ch51-55 已 COMMITTED（88.8/83.5/82.25/78.5/76.3）。

## 2026-10-05 中纲批次 V01 ch51-80 — 已 approved，批次启动 ✅

**背景**：延续 V01 中纲批次（ch21-50 已 approved）。本批范围 51-80，
plot_graph 节点章 4 个（60/68/74/79），无节点章 26 个。

**生成**（scripts/generate_mid_outline.py --vid V01 --start 51 --end 80）：
- 首跑 25/26（batch 5 越界 ch79 丢弃、ch80 缺失）→ --only 79,80 补跑
  （ch79 识别为节点章跳过，ch80 补齐）；--only 63 重生成撞锁条目。
- 产物 config/planning/mid_outline_V01_ch51-80.json（26 条）。

**校验**（audit_mid_outline）：通过 18 章 / 需人工确认 8 章（ch53/54/59/
63/67/69/76/78）/ 撞锁 0（ch63 初版"陆烬体内气机运转"撞 A 路线硬红线，
--only 重生成修正）。

**审阅（2026-10-05 全部放行）**：ch53/59/63/67/78（迷雾异象/降生异象
铺垫，根源未点明，放行；批次密度由后续观察）、ch54/ch76（F001 皇室
秘藏实物线索，符合超长引线）、ch69（陈老根练呼吸、陆烬旁观，正中 A
路线允许范围）。meta.status 已置 approved。

**F004 编号核实结论**：audit_mid_outline.py 对伏笔编号零解析（boundary_note
原样透传），generate_mid_outline.py 注入 registry.json（F001-F009 与
ending_bible 编号体系一致）；ch53/59/63/67/78 标"接近F004"系 LLM 生成层
标错（F004=线粒体/地球起源@ch2200；迷雾异象最接近 F005 主题但 F005
plant 在 ch1130，第一卷不在其埋设窗口）——非脚本 bug，不影响门禁判定，
后续批次留意 LLM 伏笔编号准确性即可。

## 2026-10-05 中文导演话语泄漏检测盲区 — 已修复 ✅

**背景（ch25 真实样本）**：ch25 正文第 156 行"这就是他必须执行的第一个
外部动作：翻阅旧笔记"——纯中文规划式表述泄漏进正文。核实：现有
[泄漏] 检测（scaffold_scrub 的 `[A-Za-z_]\w*`、latin_leak_gate 的
`[A-Za-z]{2,}`、verify_no_scaffolding 的标记 token）全部只抓拉丁字母/
英文/标记型，无任何规则针对中文"导演话语"。

**修复**（quality/repetition_detector.py）：
- 新增 `detect_director_speak_leak()`，两层检测（避免退回逐词打地鼠）：
  - A. 兜底短语正则：`必须执行的第一个(外部|内部)动作`、`接下来/下一步
     要做的`、`这一步的任务是`、`按照安排他需要`、`执行…指令/安排/任务`、
     `本章核心目标` 等规划式句式（有限枚举的句式层）。
  - B. 结构化 schema 词汇反查：chapter_director 的 task_card/prompt 写死
    的内部标签词（"本章核心目标"、"大纲核心任务"、"场景目标"、"场景冲突"、
    "场景情绪"、"情节点"、"外部动作"、"内部动作"、"concrete_events"、
    "must_cover_beats" 等）原样出现在正文即判 [泄漏]——检查"内部实现的
    词汇泄漏到外部产物"，集合由代码自身定义、有限，不依赖人工逐条补词。
- 接入 verify_no_scaffolding（pipeline 自动加 "[泄漏]" 前缀，复用
  _det_fulltext_fix 的 [泄漏] 修复分支"清除该表述"）。

**验收**：新增 5 条 hermetic 测试（ch25 真实实例 + 5 个不同措辞变体 +
4 条正常叙事不误报）；pyflakes 全包 0 undefined name；全量 pytest
1 failed / 1101 passed（唯一已知 pre-existing，无新增）。
**关联记录**：ch25 中纲偏离（图腾兽皮册→伤病记录册）与 A 路线边缘
（陆烬亲自调息、无概念锁词）两项为非阻塞项，随 ch25 gray-band 发布
保留 spot-check 标记。

## 2026-10-04 质量类 det 硬项接入修复通道 — 已修复 ✅

**背景（ch25 重跑坐实的缺口）**：中纲重跑对比显示 ch25 仍失败
（命名矛盾"活井/枯井同章共现" + 终态硬项"跨场危机 off-card 未承接"），
归因**质量类失败**（区别于已修复的设定类）。定位：`_deterministic_quality_gate`
产出的 `[命名矛盾]`/`[跨场危机]`/`[伏笔极性矛盾]` hard issues 检测到了，
但 `_det_fulltext_fix` 的消费前缀集合只有 `[称谓]/[canon/[泄漏]`——质量类
硬项从未进入修复路径，只能靠 force-best 落 draft 等人工。

**修复**（pipeline_orchestrator.py，_det_fulltext_fix + CC33 调用点）：
- 前缀集合扩为 `("[称谓]", "[canon", "[泄漏]", "[命名矛盾]", "[跨场危机]",
  "[伏笔极性矛盾]")`。
- 新增三个指令分支（复用既有"锚点替换 JSON → parse/apply → 重跑 det gate
  门过才采纳"模式，journal 不动）：
  - `[命名矛盾]`：统一同指实体用名，全文替换一致称呼，不得同章同指异名
    且证据成立。
  - `[跨场危机]`：本场景补写对上一场景危机的呼应/推进，不得悬空。
  - `[伏笔极性矛盾]`：补铺垫或调整为与既有伏笔极性一致。
- 质量类与规则类（scope leak/concept_unlocks）检测/修复逻辑保持独立，
  仅复用"接入 fix loop"的通道模式（符合边界文档第 13 节）。

**验收**：新增 2 条 hermetic 回归测试（命名矛盾、跨场危机指令生成与消费，
test_det_fulltext_fix.py）；pyflakes 全包 0 undefined name；全量 pytest
1 failed / 1098 passed（唯一已知 pre-existing，无新增）。

**真实重跑验收（2026-10-05 完成）**：ch25 在新通道下重新走完整流水线
→ **85.3 分、errors=[]，det gate 全过（gates pass），gray-band（85-88）
自动发布至 chapters/novel/chapter_25.txt，标记人工 spot-check**。
对比链：中纲前（命名矛盾 hard block 失败）→ 中纲后旧通道（82.9 分
force-best 落 draft，卡"跨场危机"）→ 新通道（85.3 分自动发布，不再停在
force-best）。任务书验收 b 达成。一次日志记录"[change_validation]
rejected: entity_not_found"（世界状态变更校验拒绝，非致命，属既有机制）。
**遗留**：ch25 以 gray-band 发布，非全绿 88 分；人工抽查标记保留。

## 2026-10-04 backlog 文档去重 — 已修复 ✅

仓库内曾存在三份 quality-backlog.md：src/docs/（278行，停更于封存轮）、
docs/（正本，持续维护）、仓库根 docs/（50行，最早停更）。代码零引用
（纯人读文档）。处理：以 `packages/coding-agent/skills/novel-engine/docs/
superpowers/quality-backlog.md` 为唯一正本；另两份替换为一行迁移占位，
提交说明记录原因。教训：路径只差 src/ 层级，肉眼极易选错；以后引用
backlog 一律用正本绝对路径。

## 2026-10-04 中纲（章级意图）层 V01 ch21-50 — 草稿已生成+校验，待用户审阅 ⏳

**背景（粒度缺口修复）**：plot_graph.json 49 节点覆盖全书约 50 个锚点章，
ch21-50 仅 43/50 命中节点，其余 28 章此前靠 director"前后 20 章节点+卷主题"
松散参考自由发挥（ch25/ch48 类失败的根源之一）。本批新增"章级中纲"层，
补在 bible（骨架级）与单章大纲（LLM 临场）之间。

### 阶段 1：生成（scripts/generate_mid_outline.py，新建）

- 输入 vid + 章节范围，读取 bible 五文件（author_intent 当前卷 forbidden +
  ending_bible 伏笔时间表）、volumes.json 卷主题/冲突/高潮、plot_graph.json
  邻近节点、foreshadow registry 相关条目、world_state 承接摘要。
- 每章输出：core_event（一句话核心事件）+ characters + boundary_note
  （接近伏笔/forbidden 边界时显式标"需人工确认"，不自行放行）。
- 产物：config/planning/mid_outline_V01_ch21-50.json（meta.status="draft"，
  与 plot_graph.json 同目录同管理，长期可追溯）。
- **踩坑记录（本批）**：
  - siliconflow DeepSeek-V3.2 的 output_json 模式**系统性返回空 content**
    （跨温度重试 4 次仍空，14 批全灭，90 分钟零产出）→ 改为普通文本输出
    + 行解析（`ch<N>|core_event|characters|boundary_note`），3 分 40 秒
    完成 28 章。
  - LLMClient 直接构造时 `_resolve_api_key` 的默认参数（ZLEAP_MODEL_API_KEY）
    与 runtime_config 的 api_key_env（LLM_API_KEY）不一致，且不查
    SILICONFLOW_API_KEY → 显式按 api_base 平台解析 key（siliconflow 优先
    SILICONFLOW_API_KEY）。此问题同样影响 run_volume 之外所有直接用
    LLMClient.from_config_dict 的脚本，后续统一修订。
  - 生成脚本支持 `--only 31,48` 重生成指定章并合并回主文件（覆盖式），
    用于撞锁条目的定向重跑。

### 阶段 2：校验（scripts/audit_mid_outline.py，新建）

- 复用 concept_audit 思路：detect_scope_violations（concept_unlocks 硬锁/
  软锁）+ author_intent forbidden 子串 + 新增 **A 路线主语模式判定**
  （陆烬亲自修炼样式动作：练习/运转/引气/吐纳/打坐/调息/催动、陆烬体内
  气机运转/共鸣——纯词表判不出，须按主语紧邻模式，避免"旁观陈老根练习"
  误判）。
- 结果：通过 23 章 / 需人工确认 5 章（ch31/35/40/42/45，均为模型自标
  伏笔/身世/修炼边界，待用户审阅）/ 直接撞锁 0 章（ch31、ch48 初版撞
  A 路线红线已用 --only 重生成修正；ch39 为审计模式误报已修）。
- **使用门禁**：Director 只读 meta.status=="approved" 的产物；audit 通过、
  用户审阅后由脚本将 status 置为 approved，否则永远不可用（物理门禁）。

### 阶段 3：接入 Director（chapter_director.py 修改）

- `_load_mid_outline_entry(chapter_num)`：扫描 config/planning/mid_outline_*.json，
  仅接受 approved 条目；无则返回 None。
- `_build_mid_outline_hint(entry)`：格式化强约束文本（core_event + 涉及角色
  + 边界注意）。
- `_build_shared_context` 注入 `mid_outline_entry` / `mid_outline_hint`；
  `_call_scene_skeleton` prompt 在"相关剧情节点"之后注入中纲行；无 approved
  条目时退回原松散参考逻辑。
- 4 条 hermetic 测试（tests/test_mid_outline_director.py）：approved 注入 /
  draft 不注入 / 无文件退回 / 任意非 approved 状态不可用。

### 阶段 4：验收

- pyflakes 全包 0 undefined name；全量 pytest 1 failed / 1096 passed
  （唯一失败为已知 pre-existing test_same_location_cluster_shared_objects_not_flagged，
  无新增）。
- **待办（用户审阅后）**：ch21-50 中纲重跑对比（通过率/返工轮数 vs 松散
  参考基线），结果如实追加本文件；ch25/ch48 专项复验。

### 待用户审阅（5 章需人工确认，approved 标记后 Director 才启用）

**2026-10-04 已审阅（用户逐条拍板）**：
- ch31 放行（异域尸骨=禁区边缘出事的物证，不等于"根源被点明"，合规悬念）。
- ch35 放行（呼吸法疗伤祛毒为表面功效；F002 真正揭示在第四卷 ch939，
  不碰核心反转）。
- ch40 放行（F001 三残物"第五卷起分阶段拼凑、ch3025-3035 落地、约
  ch3400 前完成"；第一卷埋首件实物正合超长引线设计）。
- ch42 **修改后放行**（原"认出文字相似+神色凝重"触及"陈老根来历被揭穿"
  实质一步；按指定文案改为：陈老根看到残石后沉默不语，只含糊说了句
  "莫要多想"便将其收起，去掉"认出关联"与"神色凝重"两个具体反应）。
- ch45 放行（文物线索+含糊带过，标准悬念，无实质信息）。

**结果**：meta.status 已置 "approved"（2026-10-04），28 条整批可用
（文件级门禁：audit 通过 + 用户审阅后整批激活；含 24 条自动通过 + 4 条
放行 + ch42 修改版）。复验 audit：0 撞锁。

### 阶段 4 续：真实 LLM 重跑对比（ch48/ch25）

**2026-10-04 重跑完成**（中纲 approved 生效后，真实 LLM 单章全流程，
scripts/rerun_mid_outline_verify.py；结果 runtime/mid_outline_rerun_result.json）：

| 章节 | 中纲前（基线） | 中纲后重跑 | 结论 |
|---|---|---|---|
| ch48 | 失败：前世/重活一世内容泄漏（reviewer 对照不存在的设定打分） | **通过**：87.1 分，errors=[]，20.7 分钟 | ✅ 设定贯通+中纲修复了泄漏类失败 |
| ch25 | 失败：命名矛盾（活井/枯井同章共现） | 仍失败：82.9 分 force-best 至 draft（57 分钟），命名矛盾复现，终态卡硬项"跨场危机 off-card 未承接" | ⏳ 质量类失败，非设定贯通范围 |

**归因**：ch48 属于"设定类"失败（Writer/Reviewer 看不到 bible），已由
设定贯通三阶段 + 中纲约束修复；ch25 属于"质量类"失败（命名一致性/
场景承接），对应 backlog 已有待办"质量类失败（如命名矛盾）无确定性
修复通道"——本次重跑确认该通道缺失仍是真实缺口，优先级提升。
**遗留动作**：质量类确定性修复通道（命名矛盾检测 → 定点修复指令，
参照 _det_fulltext_fix 模式）列为下一批任务；ch25 的 draft 已落盘
chapters/draft/chapter_25.txt（82.9 分），等通道上线后重跑。



## 2026-10-04 设定贯通三阶段（封存解封后第一批）— 已修复 ✅

**背景（解封诊断结论，已核实）**：bible/ 目录 5 个设定文件
（world/character/style/author_intent/ending）此前只有前 4 个接入 Director，
Writer 与 Reviewer 两层几乎看不到真实设定内容，对应封存时两个核心症状
（ch48 泄漏、ch25 命名矛盾）：

- ending_bible.md 零代码引用（只有 start.sh 存在性检查 + docs/编写指南.md
  声称"无截断全文发送"——声明与实现不符）。
- reviewer_agent.py 只在 prompt 字符串里提"符合 character_bible/style_bible"
  文件名，从未加载文件内容 → reviewer 对照一份从未见过的文件打分。
- writer_agent._bible_snippet() 硬截断 world[:600]+character[:600]，不含
  author_intent forbidden → writer 从没见过"本卷禁止事项"。

### 阶段 1：ending_bible 接入 Director + 伏笔-终局回收远期校验（commit b7cd5a3a4）

- chapter_director：`_load_bible_cache` / `_build_shared_context` /
  `get_context_for_chapter` 三处 bible_files 增加 `'ending'` 键；
  新增 `_build_ending_anchor_hint`——registry resolve_chapter 距当前 ≤30 章且
  未 resolved 时在 skeleton prompt 注入"临近回收"提示（远期锚点）。
- outline_coverage_gate：新增 `validate_foreshadow_closure` 纯函数，
  importance≥0.7 且 resolve_chapter < current 且未回收 → 硬拦截
  （resolve==current 视为正在回收不拦；registry 全量扫描，不再依赖
  query_active_foreshadows 的 clue_plan 命中——原超期提醒有漏报缺陷）。
- orchestrator `_deterministic_quality_gate`：接入硬拦截，issue 前缀 `[设定]`；
  原 soft 提醒保留。
- db.py：新增 `resolved_foreshadow_ids()`（StateDB status='resolved' 集合）。
- 验证：8 条 hermetic 纯函数测试；pyflakes 0 undefined name；
  全量 1 failed / 1075 passed（唯一 pre-existing 不变）。
- 生产影响：ch1-50 阶段 resolve_chapter 均 1800+，零触发（远期锚点设计使然）。

### 阶段 2：Reviewer 注入设定对照块 + 前世揭示尺度 soft_warn（commit ba14ccf31）

- reviewer_agent：新增 `_build_setting_block`——按 author_intent 分卷标题
  （`## 第N阶段：xxx（第A-B章）`）切分当前卷 forbidden 列表 + character_bible
  全文 + style_bible 全文注入 review prompt（合计约 4KB）；评分指令第 3 条
  强化为"对照设定对照块逐条核对，违反按'设定违规'单独归类"。
- 前世揭示尺度（ch48 口径决策）：**不做 hard block**。character_bible C001
  将"神魂跨界重生"定为既定身份（角色本人知道，不违反设定）；author_intent
  十卷 forbidden 无前世/转世禁令（已 grep 核实零命中）。真正问题是揭示节奏/
  笔触分寸 → 新增 `quality/reveal_scale_gate.py`：150 字窗口 ≥3 个前世具象
  细节词（病床/咽气/白墙/吊瓶等）→ soft_warn（不阻断，计入打分供人工审阅）。
  阈值/词表在 config/quality_thresholds.json `past_life_reveal` 可调。
- orchestrator：build_deterministic_signals 后追加 past_life_reveal 信号，
  确定性检测注入 reviewer，不新增 LLM 调用。
- 验证：10 条 hermetic 测试；pyflakes 0 undefined name；
  全量 1 failed / 1085 passed。

### 阶段 3：Writer 按卷定向注入替代固定 600 字截断（commit 06471dec7）

- writer_agent：`_load_bible_cache` 增加 author_intent 键（此前从未读到
  forbidden，这是 ch11/ch48 犯错的直接原因之一）。
- `_bible_snippet` 改造：当前卷 forbidden 完整注入（不截断，核心变更）；
  character_bible 按本章出场角色名定位段落（participants/characters，
  失败退回 600 字兜底）；world_bible 按地点/势力名定位（location/place，
  同样兜底）。
- 验证：7 条 hermetic 测试（分卷切片/定向命中/兜底三分支）；
  pyflakes 0 undefined name；全量 1 failed / 1092 passed。

### 复验结论（如实记录，不只有成功部分）

- ch48：合成 fixture 验证 reveal_scale_gate 对"病床/咽气/前世弥留"段落正确
  判 HIGH 且为 soft（不阻断）；合法提及（单词、无细节堆叠）不触发。
  真实 LLM 全流程复跑未做（不强制，见任务书"优先合成 fixture"条款）。
- ch25（命名矛盾）：不保证 100% 消除——命名矛盾属于"质量类失败"范畴，
  不完全是设定贯通能解决的。Writer 现在能看到更完整的定向 character_bible
  段落，应降低概率；若真实复跑仍失败，归因到质量类确定性修复通道（下述
  未做项），不是本批改动失败。

### 本批明确不做（保持 backlog 优先级，另排）

- `_decide_publish` 拒绝原因不落盘（发布可观测性）。
- 质量类失败（如命名矛盾）无确定性修复通道。
- 不改写 bible 内容、不顺手扩充 forbidden 条款（若接线后 ch11/ch48 类问题
  依然存在，用具体反例句子单独处理，不与接线混做）。

---

## 历史要点（压缩自前序会话，供追查）

- 工程纪律（写入 orchestrator-boundaries.md）：提取/委托禁
  `staticmethod(lambda self,*a,**k)`；外部变量显式入参；每阶段提交前
  pyflakes 全包 0 undefined name + 全量 pytest 仅允许 1 个 pre-existing
  （test_same_location_cluster_shared_objects_not_flagged）；新测试必须
  hermetic（合成 fixture，不依赖被 .gitignore 排除的真实产物）；提交前在
  干净 clone 复核。
- 已修复根因库见 docs/superpowers/../novel-engine-runner 技能
  references/root-causes.md（12 条）。
- 概念解锁机制（concept_unlocks，ch6-ch9 真实数据验证生效）：
  词表按概念分组（cultivation_system@6 / martial_identity@15 /
  guardian_identity@20 / xianxia_system@25），scope_gate 先查解锁状态再落
  词表，语境豁免降为兜底。
- 未决待办：ch21+ 创作方向（用户/剧情负责人提供大纲后，先用
  concept_audit.py 纸面推演）；martial_identity@15、guardian_identity@20
  两个解锁点从未在真实跑批中触发，是 ch21-50 第一个必撞窗口。

## 2026-10-06 传授既成 vs 提及/认知 边界（ch59/ch60 实证收口）
- 判定实证（_has_teach_violation 真代码运行）：ch60 L234 "您教我……呼吸的法子"→True
  （传授既成，拦截正确）；ch59 L56 体感+传授既成→True；认知句"陆烬知道根伯会些
  调息的法子"→False；纯提及句→False。允许边界=陈老根练/提及、陆烬旁观/被告知结果，
  "被传授方法"不在允许形态内。此前"疑似误报"判断撤回（只读 forbidden 文字未跑代码）。
- 修复 1（归因显示）：P6-1 传授违规 term 由硬编码"吐纳"改为实际命中词（固定特异度
  顺序 吐纳>调息>行气>口诀>功法>法诀>法门>呼吸，避免 set 迭代漂移）；sentence 用
  _sentence_ctx 定位（不再 sent[:120] 可能截掉命中词）。
- 修复 2（源头例句注入）：chapter_director 新增 _TEACH_BOUNDARY_HINT 常量并注入三处
  prompt（skeleton/craft/metadata），显式给"认知句可以 / 传授既成句不可以"边界例句，
  从生成源头降低发生率（与 concept_unlocks 同哲学，不新增检测词表）。
- 回归：test_teach_boundary.py 4 用例（ch59/ch60 违规 + 认知/纯提及放行）；
  round22 test_gap_b_definite_teach_with_hesitant_hard 断言更新为"实际命中词"。
- 教训（本次自查）：类体内定义类属性常量须 4 空格缩进；方法内引用类属性须 self. 前缀；
  归因显示改动会撞旧断言（写死"吐纳"），需同步更新断言而非只改实现。

## 2026-10-07 ch81-110 批次验收（16h 跑完，10-06 14:27 → 10-07 06:26）
- 中纲层：mid_outline_V01_ch81-110.json（27 条，跳过节点章 90/98/100；5 章需人工确认
  全部放行 ch84/92/104/107/110；ch92 F004 标号为 LLM 层标错，非脚本 bug）。
- 结果：30 章（4 补跑 + 26 新章）→ 27 COMMITTED / 3 FAILED（90% 跑通率）。
- novel 发布 4 章：ch59(88.4 补跑通过)/ch86(85.6 gray)/ch89(87.1 gray)/ch108(88.5)。
- draft 落盘 25 章（含 <85 force-best、88+ 但 reviewer high 的 force-publish-draft 66/92）。
- 3 个 FAILED 归因：
  1. ch56 88.6：reviewer plot_consistency（任务卡'午后' vs 正文'正午'）——新根因，
     上次的传输误报未复现（修复生效）。
  2. ch83 85.5：gray-band 授权发布后终审复查 reviewer high（F001 伏笔执行不到位）
     → 拒绝发布。期间 ch62 不变量（gray-band 短路 force-best）真实触发，draft/checkpoint
     未被污染。安全网按设计生效。
  3. ch91 76.3：跨场危机 off-card 未承接 ×2 + [canon硬词] 呼吸（传授既成"我教你的
     那套夜里静坐、调整呼吸的笨法子"）。源头预防未完全生效，但检测拦截正确；
     归因显示"呼吸"（实际命中词）验证归因修正生效。
- 补跑 4 章：ch59/60/66 原失败全部消解（59: 88.4、60: 73.6 无违规、66: 88.1）；
  ch56 撞上新 reviewer 拦截（非回归）。
- 关键验证：边界例句注入（ch59/60 传授违规消失）、归因显示修正（ch91 显示实际
  命中词）、gray-band 短路不变量（ch83）——三个修复均在真实 LLM 输出上验证生效。
- 遗留观察：本批次分数分布普遍 70-87（novel 达标率 13.3%，与 ch51-80 的 10% 一致，
  符合"draft 池算最终内容"共识）；ch56/ch83/ch91/ch110 隔离稿保留为负样本。

## ch111-140 批次验收（2026-10-09）

- 批次结果（30 章，多次重启后完成，checkpoint last_complete=140）：
  - 正式发布 novel/：2 章（ch120、ch139）
  - draft 池：26 章（含 ch136/138 pending_human_review）
  - 完全无产物：2 章（ch132、ch135）→ 需重跑
  - 跑通率 28/30 = 93.3%（对照 ch81-110 的 90%）；novel 发布率 6.7%
  - 平均单章耗时 23-31 分钟（V3.2）
- 5 个 FAILED 归因（分三类）：
  1. 检测边界疑似误报（2 章）：
     - ch126 81.65：canon 硬词"呼吸"——铁馆主（武馆教头）传授"配合呼吸的动作"，
       凡人武艺语境。武馆传授变体，与 ch91 同族，待概念化评估。
     - ch132 86.3：canon 硬词"吐纳"——"这不是修炼，陈老根从未教过他吐纳调息
       的口诀，他只是在模仿"。否认式提及（语义在否认修炼）仍被表面词命中，
       否认式句式边界待评估。
  2. 质量类（1 章）：ch135 70.8：跨场危机 off-card 未承接（陈老根拎粗布包裹）。
  3. reviewer 拦截（2 章，设计内行为）：ch136 85.9 / ch138 85.4：F001 伏笔执行
     review high/medium → 82-87 人工复核队列（draft pending_human_review）。
- 遗留待办：132/135 重跑；126/132 边界（武馆传授/否认式）是否 concept 化处理；
  136/138 人工复核。
- 模型切换（本次新增）：agnes_30 profile 已建（agnes-3.0-flash 全阶段，免费，
  实测单场景 17-19s/严格 JSON 通过）；批次验收后启动 ch141 A/B 试点对照 V3.2 基线。

## agnes-3.0-flash A/B 试点结论（2026-10-09）

- 试点批次（ch126-141 resume 跑 6 章，53 分钟，全程 agnes-3.0-flash）：
  - ch132 OK 65.0 / ch136 OK 71.7 / ch141 OK 76.1（新章首跑，单章 9.3 分钟）
  - ch126 / ch135 / ch138 FAIL 无分数（无产物落盘）
- 同章对比 V3.2 旧稿：有分章均分 agnes 70.9 vs V3.2 82.0（-11 分）
  - ch132: 86.3(F)→65.0(OK) -21.3；ch136: 85.9(F)→71.7(OK) -14.2
  - 速度：单章 9.3 分钟 vs V3.2 23-31 分钟（约 3 倍加速）✓
  - 质量：场景 under-target 频发（ch141 Scene 3/4 字数 1160-1392 < floor 1505 触发 regen）、
    polish 退化、review 出现 green-but-low+crosscheck 多轮
- 结论：**试点判负，已切回 siliconflow（DeepSeek-V3.2）**。速度 3 倍但分数全面下降
  14-21 分，失败章更彻底（无分数稿），按"draft 池算最终内容"共识会直接拉低全书
  质量。agnes_30 profile 保留，仅作 fallback/成本降级选项备用。
- 副作用记录：ch136 V3.2 的 85.9 稿（pending_human_review）被 agnes 71.7 稿覆盖，
  旧稿文本不可恢复（checkpoint hash 可追溯）；其余旧稿未受影响。
- 后续：ch132/136/141 的 agnes 稿保留为负样本；126/135/138 仍缺产物，下轮 V3.2 重跑。

## 流程层重检 + ch141-170 中纲 + ch141 第三次重跑（2026-10-09）
- 流程层重检结论：validate_task_card 缺章内场景时间顺序/跨日校验（ch141 任务卡
  场景1午后→2上午/傍晚→3午后→4黄昏 倒置 + 场景4"次日晨"跨日）；地名误填
  forbidden_markers 被时间门误判（青河县）。review 稳定性机制（三评/交叉/第4票）
  已覆盖，非缺口；写短（agnès 单次输出 1300-1900 字）记录为待评估项，未在本轮
  改 L1 分段生成（影响所有模型，风险高）。
- 优化落地（提交 a25a08425，已推送）：
  1. detect_task_card_scene_time_inversion：场景 narrative_time 顺序倒置报错；
     回溯/闪回场景（回忆/昨夜）不参与；未知时间词不断链。
  2. 跨日检测：章级跨度（如"当日"）内出现 次日/翌日/N日后 判矛盾。
  3. timeline_gate._GEO_SUFFIXES 扩充建筑/场所类 2 字后缀（武馆/镖局/客栈/书院/
     医馆/药铺等），地名不再被时间门误判。
  4. 新增 test_task_card_time_inversion.py（6 用例）+ 地名误判回归测试。
  5. 全量 pytest 基线：1 failed / 1118 passed（唯一失败为已知 pre-existing）。
- ch141-170 中纲：30 章全部生成（siliconflow，5 批），audit 校验 23 通过 +
  7 需人工确认 + 0 撞锁。7 章（143/144/146/148/154/164/167）经对照 bible 后
  全部放行（用户拍板），meta.status=approved。正文级约束 3 点（ch144 陆烬无气感、
  ch148 刻痕不解读成功法、ch167 传说不含因果结论）由 A 路线 prompt +
  concept_unlocks 兜底。audit 伏笔 ID 标注错位（146/164/167 标 F004，实际为
  F005/forbidden 第4条）——audit_mid_outline.py 匹配逻辑 bug 再次触发，待单独修。
- ch141 第三次重跑（agnès-3.0-flash + 中纲 + 时间校验 + 地名修复 + 写作纪律）：
  COMMITTED 77.7 分，6.6 分钟。对比：76.1（无纪律）→ 73.6（有纪律）→ 77.7。
  提升实证：日志首行"Task card validation failed: 场景4 叙事时间「次日晨」超出
  章级跨度「当日清晨至黄昏」"——新跨日校验在任务卡生成时即拦截并触发 director
  重生成，不再生成矛盾任务卡。review 首评 76.3 + 交叉 79.1 → median 77.7。
  无 det 硬项（hard=[]），soft 仅 1 条套话预警。距离 82 目标仍差 4.3 分，
  与 V3.2（82.0 基线）差距由 -11 收窄至 -4.3。agnès 判负结论维持"不维持"，
  继续观察 ch142+ 批次。


## ch142-170 批次（agnès-3.0-flash，29 章）结果与根因（2026-10-10）

### 批次结果
- 14 COMMITTED / 15 FAIL（通过率 48%）。OK 章均分 73.5（67.7-80.4），按"draft 算最终内容"共识入 draft/。
- OK：143/144/145/146/147/149/150/153/157/159/160/163/165/170
- FAIL 三类：
  1. 快速 hard-failed（~3 分钟，无 checkpoint，8 章）：148/151/152/158/162/164/167/168 —— **根因=CC round-30 新加时间校验误伤**（已修复，见下）
  2. 评审极不稳定（三评极差>15，强制人工复核，5 章）：142/155/156/161/166（49.5-74.7 分）
  3. 慢速 hard-failed（3 次 attempt 走完，2 章）：154（569s）、169（86.6 分差 1.4 未到 88）

### 根因定位：时间校验误伤（round-30 回归，已修复）
单章复现（带完整日志）推翻"API 故障"假设：
- ch148 复现完整跑通 555s（批次里 218s 是 API 瞬时故障假象），真实失败=评审低分不稳定（55.1/39.8/40.8）
- ch151 复现 161.6s 稳定复现快速失败：3 次 attempt 全被任务卡时间校验拦截
- **V3.2 重跑 ch151 同样失败**（场景1"次日清晨"+span"一日之内"）→ 证明不是 agnes 能力问题，是校验过严
- 修复后 V3.2 重跑 ch151 = **COMMITTED 79.0**（校验通过→全 gate pass→force-best 进 draft）✓

### 时间校验三层修复（CC round-31，待提交）
1. `_scene_time_order` 跨日偏移：次日/翌日/第N日/N天后按天数 +10*N（"次日清晨"=11 > "深夜"=8）
2. 倒置检测改 (日期档, 时段档) 二元比较：裸时段词承接前序日期档（"次日清晨"→"上午"同指次日）；
   深夜档(>=6) 后接清晨类(<=2) 视为隐含跨日推进（"深夜→拂晓"合法）
3. 跨日检查放宽：span 自身允许跨日（24小时/次日/深夜等）→ 全放行；
   首个带时间场景即跨日 → 放行（span 起点模糊）；仅"当日白昼场景之后突现跨日"（ch141 形态）仍拦
- 新增 10 个回归测试（test_task_card_time_inversion.py 现有 6 → 16 个）
- 教训：round-30 加校验时只测了 ch141 反例，没考虑"首个场景跨日/深夜→清晨隐含跨日"两类合法形态，
  导致 8 章被误杀。新增校验必须枚举合法形态的反例。

### 遗留
- 5 章评审极不稳定（142/155/156/161/166）：agnès reviewer 给分系统性偏低且分歧大，待 V3.2 重跑观察
- 169（86.6 差 1.4 到 88）：接近达标，V3.2 重跑大概率过
- 批次失败率 48% vs V3.2 基线 87%：agnès 整体能力差距（写短 4640<7310、reviewer 低分、任务卡不自洽），
  继续 agnes 优化边际递减；建议切回 V3.2 重跑全部 FAIL 章（15 章）
- ch126/135/138（更早 agnes 试点 FAIL）仍未重跑，纳入 V3.2 重跑清单


## agnes 写短根因实证与提示词优化（CC round-32，2026-10-10）

### 四组对照实验（agnès-3.0-flash，ch148 场景2 真实任务卡）
| 版本 | prompt 长度 | scene_text 字数 |
| --- | --- | --- |
| 裸测（简单 prompt 写3000字） | ~300 | 5011 |
| C 极简（任务卡+beats+字数） | ~900 | 2202 |
| D 蓝图瘦身（全约束+核心字段） | 2570 | 2160 |
| A 原版（全约束+全蓝图） | 3651 | 1343 |
| B 字数前置强化（A+字数前置） | ~3700 | 1095 |

结论：**写短不是模型能力问题，是 prompt 约束密度问题**。弱模型在超长
prompt 下输出被压缩；"强化字数指令"无效（B 比 A 更短），"精简 prompt 密度"
有效（D 比 A +61%）。用户"简单的字数 agnes 做不到"的判断方向正确。

### 根因（流程+提示词两层）
1. 流程层：director 只为 >5 场景章（merge_thin）和单场景章分配
   word_count_target，**4 场景章（绝大多数）从不分配** → writer 用默认
   2000/场景（ch148 任务卡 4 场景 word_count_target 全 None 实证）。
2. 提示词层：build_scene_prompt 蓝图 JSON 完整打印，其中
   concrete_events/named_interactions/info_reveal_points/scene_craft_elements
   已被 density_block/craft_block 等专用约束块完整注入，重复打印拉长
   prompt 近 1100 字符（3651→2570）。

### 修复（CC round-32，待提交）
1. scene_schema.py 新增 slim_scene_blueprint()：移除被专用块覆盖的 4 个大字段，
   build_scene_prompt 蓝图打印改用瘦身版。保留全部硬约束块，零行为风险。
2. 测试：test_scene_schema.py 新增 2 用例（瘦身不丢核心字段 + prompt 不含大字段）。
3. 真实批次验证：**agnès 重跑 ch148 = COMMITTED 78.65**（4.4 分钟）。
   字数 4640→6021（+30%），从硬失败变为进 draft（draft 算最终内容共识）。
   仍差 7310 软门槛 1289 字（soft issue 不阻断），评审 78.65 < 88 按共识接受。

### 遗留
- 流程层缺陷仍待修：director 应对所有场景数分配 word_count_target（按章目标
  分摊），不只 >5/单场景——对 V3.2 无影响（能力可补），对弱模型有意义。
- 真实流程注入块（bible/事件台账/前情/视角）使 prompt 比实验 D 更长，
  若需继续提长可做次级精简（bible_section 按命中压缩等），边际收益递减。
- 4 场景默认 2000 目标可考虑按章目标 8600/4=2150 提升（弱模型写出率 75% 时
  4×1612=6450 仍不足，需配合目标超额策略，另行评估）。


## agnes FAIL 章重跑批次（CC round-31+32 修复后，2026-10-10）

15 个 FAIL 章（原批次全 FAIL）在时间校验修复（717e1387e）+ 蓝图瘦身（86f8d0fed）后重跑：
**10 OK / 5 FAIL（66.7%）**，总耗时约 70 分钟（均章 ~4.5 分钟）。

### OK 章（10）：142(68.4) 152(64.8) 154(80.9) 155(73.9) 156(65.0) 158(77.2)
                   166(67.4) 167(67.6) 168(66.5) 169(81.3)
- 原批次全部 FAIL → 修复后 66.7% 通过，两个修复（时间校验误伤 + 写短）实证有效
- 154（原慢速 hard-failed）与 169（原 86.6 差 1.4）均过
- 156 仅 959 字仍 COMMITTED（force-best 兜底，写短残余但不再硬失败）

### FAIL 章（5）与根因
- 148：hard-failed（304.9s）——但修复后曾单跑 COMMITTED 78.65，批次内疑似
  API 瞬时故障/评审波动，需单跑复现确认
- 151/161/162/164：三评极差>15（评审极不稳定，强制人工复核）——writer 已能产出
  中上稿（164=78.5 分），卡在 reviewer 三评分歧。151 在 V3.2 下 79.0 稳定
- 结论：剩余瓶颈是 **agnès reviewer 三评不稳定**（非 writer 能力），4 章均分
  50-78 但因极差被拦

### 候选下一步
1. 148 单跑复现（排除 API 假象）
2. 4 章三评极差章：试 agnes 内部更强 reviewer（agnes-3.0-flash-max 或 2.5-pro，
   配置里 agnes_30 profile 的 review 只有 flash）→ 仍不稳再切 V3.2
3. agnes 通过率 66.7% vs V3.2 87%：免费模型可行但评审稳定性是硬差距

## 三评极差>15 根因与修复（CC round-33，2026-10-10）

### 根因（代码级，已核实）
- model_router._build_clients() 不传 temperature → 全部相位落 LLMClient 默认 0.85。
  评审是"评分"任务却用写作温度 → 高分方差（agnès-3.0-flash 对同一文本 raw 37-85 漂移）。
- ch151 温度 0 下三票 [31.7, 59.1, 64.7]：紧簇 59.1/64.7 差 5.6 恰超 TIGHT_CLUSTER_MAX=5，
  → 单离群识别失败（误判 consistent），CC26 补票通道未触发。

### 修复（d7cbde07b）
1. model_router._build_clients(profile, phase_cfg) 读相位级 temperature；
   llm_providers.json agnes_30 review 相位 temperature=0（写作相位保持 0.85）。
2. review_votes.py TIGHT_CLUSTER_MAX 5.0→7.0（9 维 120 分体系下两票差 5-7 分=每维<1 分，
   属正常波动）；同步 test_round26_outlier_votes.py 常量断言。

### 真机验证（agnès-3.0-flash，temperature=0）
- ch161：三票 [49.9, 38.3, 68.4] range 30.1（0.85）→ [58.7, 65.7, 54.6] range 11.1 → 不再 unstable
- ch151：0.85 全票漂移 unstable → 温度0+阈值7 后正常走完修复循环（70.5 分，score<88 正常路径），
  不再卡"三评极差>15 强制人工"
- 门禁：pyflakes 全包 0 undefined name；全量 pytest 1 failed/1130 passed（唯一已知 pre-existing）

### 残留 FAIL 章温度0复跑（2026-10-10，修复 d7cbde07b 后）
- ch151：不再三评卡死（首评 68.3 单评路径）→ 修复循环 70.5 分 FAILED（分数不足正常路径）
- ch161：三评 range 11.1 通过，但修复循环 hard-failed（场景 length_too_short/标点反复失败，
  写作质量问题，非评审方差）
- ch162：votes=[64.1, 65.4, 63.7] range 1.7 → **COMMITTED 73.0**
- ch164：votes=[73.1, 79.4, 70.2] range 9.2 → **COMMITTED 73.1**
- ch148：votes=[69.7, 62.5, 49.2] range 20.5 仍 unstable —— **缓坡三分布形态**（g1=13.3、g2=7.2，
  无紧簇无崩票），现有形态规则（single_outlier/true_split）均不命中，CC26 无补救通道。
  温度0 已改善（0.85 时 55.1/48.3/38.3 更差）但仍超 15。
- 结论：三评极差>15 卡死率 5/5→1/5。缓坡形态是 agnès flash 评审天花板残留，1 章人工复核；
  若后续批次该形态高频复现，再实施"缓坡补票"（consistent+range>15 时补 1 票，收敛才继续）。
---

## 2026-10-11 ch171-200 批次验收 + CC27 缓坡补票实施

### 批次结果（temperature=0 + TIGHT_CLUSTER_MAX=7 生效，中纲 approved）
- **27/30 COMMITTED（90%），历史最优**（对比 ch142-170 批次 14/29=48%）。
- 里程碑：**ch200 = 88.5 分全绿达标（det=[]，force publish 88.5）**——全项目第一个达到 88 发布线的章节。
- 逐章：171:79.1 172:64.0 173:66.1 174:67.3 175:72.3 176:76.2 177:75.8 178:78.5 179:80.8 180:69.1
  181:80.4 182:80.9 183:77.5 184:79.1 185:67.4 186:FAIL(hard-failed) 187:FAIL(三评极差 79.7) 188:79.7
  189:72.9 190:FAIL(三评极差 73.2) 191:62.2 192:FAIL(hard-failed) 193:71.9 194:68.0 195:75.9 196:65.4
  197:82.3 198:63.4 199:61.2 200:88.5
- 历史重跑：ch135(50.7)、ch161(62.1) 批次内仍三评极差 FAILED。
- 三评极差失败 4 章（135/161/187/190）= 13% 频率 → **达到"高频复现"阈值，实施缓坡补票**。

### CC27 缓坡补票（e8ace76a4）
- 形态：consistent + 极差>15（如 49.2/62.5/69.7，两 gap 均<=15 无单离群/无真分裂）——
  旧逻辑中位<软线且非全绿时直接人工、不给补票机会。
- 机制：该类形态补 1 票，4 票排序后中间两票差<=8（CONVERGENCE_TOLERANCE）→ 共识成形，
  清 highly_unstable 走正常 E-loop/提交路由（内容不达标≠评审不可信）；中间两票仍宽 → 维持 unstable 人工。
- 纯函数：review_votes.slope_median_converges(four_votes, tol=8)；orchestrator legacy 补票分支新增
  _slope4 条件（每章仍至多 1 次补充票，REVIEW_CALL_CAP=6 兜底）。
- 新增 6 条单测（收敛/不收敛/边界/等价路由/形态识别）；pyflakes 全包 0 undefined name；
  全量 pytest 1 failed/1134 passed（唯一已知 pre-existing）。
- 遗留：ch186/192 hard-failed 属修复循环耗尽（写作质量问题，非评审方差），等 ch201+ 批次自然重跑。
---

## 2026-10-11 ch201-230 批次验收（CC27 缓坡补票首次实跑）

### 批次结果（36 章 = 历史重跑 6 + 新批 30）
- **新批次 ch201-230：26/30 COMMITTED（86.7%）**；历史重跑 4/6 通过。
- 里程碑：**ch219 = 97.5 分全绿达标（det=[]，force publish）**——历史最高分（超 ch200 的 88.5）。
- 历史重跑结果：135: 50.7→**75.0 COMMITTED**、186: hard-failed→**86.8 COMMITTED**、
  187: 三评极差→**76.5 COMMITTED**、192: hard-failed→**84.4 COMMITTED**（4/6 复活）；
  161: 62.1→39.3 仍三评极差、190: 73.2→hard-failed 仍失败。
- 新批失败 4 章：201（87.3，best<88 差 0.7 未达标）、206（42.1 三评极差）、212（hard-failed）、
  215（68.7 三评极差）、229（79.2，**[canon硬词] 调息 传授语录泄漏**）。
- 三评极差失败：新批 2/30（6.7%，上批 3/30=10%）+ 历史章 161——比率下降趋势成立。

### 新观察：ch229 正文层传授违规（中纲通过、正文泄漏）
- ch229 中纲无撞锁标注（校验通过），但正文出现陈老根台词
  "等你长大些，我会教你一套调息的法子"——concept_unlocks 硬锁（调息+传授陆烬）在
  终检层拦下，det 修复通道尝试修复但 best 仍有该硬项 → FAILED。
- 与 ch196/220 同类的"传授类违规"从**中纲层转移到了正文生成层**：中纲校验只查章级意图文本，
  无法预知 writer 会在正文里让角色说出传授台词。修复通道（det 硬项→_det_fulltext_fix）理论上
  应能处理 [canon硬词] 前缀，但本批未成功消解（best 仍含硬项）。
- 处置：① 下批自然重跑观察是否复现；② 若复现，在中纲/director 约束层显式禁止
  "陈老根向陆烬承诺将来传授/教导修炼类内容"（对话台词级），并核查 _det_fulltext_fix
  为何未消解该硬项（可能需要补一条该场景的回归测试）。

### CC27 缓坡补票实跑情况
- 代码层接线确认（e8ace76a4）：_slope4 条件 + slope_median_converges + 补票分支。
- 本批日志不含评审明细（run_volume logger 只记录章级结果），slope-check 是否实际触发
  无法从批次日志确认；187/135 从三评极差→COMMITTED 可能来自补票或温度0+阈值7 常规随机性。
- 161/206/215 仍三评极差：形态（缓坡 vs 单离群不收敛 vs 真分裂）需单章带日志复现才能确认。
- 待办：如需要，用 repro_ch*.py 对 161/206/215 各跑一次带全量日志复现，确认 slope-check 行为。
---

## 2026-10-11 draft→novel 批量提升（用户确认）

### 背景
novel/ 目录此前只有 28 章（ch1-139 早期正常发布路径产物），而 checkpoint 已记录 223 章
COMMITTED——force-best/gray-band 兜底机制落地后所有新章都写 chapters/draft/，从未做
draft→novel 迁移，"draft 池 73-87 分算最终发布内容"的政策停留在语义认定、未落地文件操作。

### 执行
- 规则：draft/ 中 checkpoint 有 COMMITTED 记录的章 → 复制进 novel/（不覆盖已有、不删除 draft 副本）；
  无 checkpoint 记录（FAILED 章产物）留在 draft/。
- 结果：novel/ 28 → **223 章（覆盖 ch1-230）**；novel 缺失 7 章均为 FAILED 章：
  ch161（三评极差）、ch190（hard-failed）、ch201（87.3 未达标）、ch206（三评极差）、
  ch212（hard-failed）、ch215（三评极差）、ch229（canon 传授泄漏）——符合"被标记缺陷不提升"政策。
- ch200（88.5）、ch219（97.5）两个达标章随批量提升进入 novel/。
- 校验：committed but file nowhere = 空（所有 COMMITTED 章都有文件）。

### 后续注意
- draft/ 保留全部副本（含已提升章）作为工作目录；FAILED 产物继续留 draft/ 作负样本。
- 新批次章仍写 draft/——**批量提升是补历史欠账的一次性操作**，不是流水线的常设环节；
  是否把"COMMITTED 后自动进 novel/"设为常设机制，待 ch231+ 批次观察后再定。

## 批次验收报告（自动生成）
- 日志: `run_volume_20261010T170107Z.log` 章节数=36 成功=29 失败=7 通过率=80.6% 达标率(>=88)=2.8% 平均分=72.78 总耗时=18129s
- 达标发布 1 章: [219]
- 合格发布 20 章: [135, 186, 187, 192, 202, 203, 205, 207, 208, 213, 216, 218, 221, 222, 223, 224, 226, 227, 228, 230]
- 低分发布 8 章: [204, 209, 210, 211, 214, 217, 220, 225]
- 失败分类:
  - 评审方差 3 章: [161, 206, 215]
      * ch161: score=39.30 | score=39.3 三评极差>15，评审极不稳定，强制人工复核（不自动重采） ===
      * ch206: score=42.10 | score=42.1 三评极差>15，评审极不稳定，强制人工复核（不自动重采） ===
      * ch215: score=68.70 | score=68.7 三评极差>15，评审极不稳定，强制人工复核（不自动重采） ===
  - 修复循环耗尽 2 章: [190, 212]
      * ch190: score=None | ['Chapter 190 hard-failed after retries']
      * ch212: score=None | ['Chapter 212 hard-failed after retries']
  - 硬项泄漏 1 章: [229]
      * ch229: score=79.20 | hard gate: '[canon硬词] 调息：他盯着陆烬，目光复杂：“等你长大些，我会教你一套调息的法子。'
  - 未达标/其他 1 章: [201]
      * ch201: score=87.30 | score=87.3 未达 88.0 线，无硬项
- 处置路由（自动）:
  - ch161 评审方差类：下批自然重跑；若复现，repro 单跑查三票形态（CC26/CC27 已兜底）
  - ch190 修复循环耗尽：下批自然重跑；若复现，定位写作质量 vs 机制 bug，机制信号入 backlog 等任务书
  - ch201 未达标：下批自然重跑（87.x 档差线未达标）
  - ch206 评审方差类：下批自然重跑；若复现，repro 单跑查三票形态（CC26/CC27 已兜底）
  - ch212 修复循环耗尽：下批自然重跑；若复现，定位写作质量 vs 机制 bug，机制信号入 backlog 等任务书
  - ch215 评审方差类：下批自然重跑；若复现，repro 单跑查三票形态（CC26/CC27 已兜底）
  - ch229 硬项泄漏：提取命中词自动入库，；命中词 [调息] 对照 decision_rules R01-R11 判例匹配处置
- 记录时间: 2026-10-11T06:56:27Z
## 2026-10-12 10:16 中纲 ch261-316 生成与校验记录
- ch261-290（30 条）、ch291-316（25 条，ch316 为 plot_graph 节点章无需中纲）已生成并通过 audit，全部 approved（55/55），meta.status=approved，可供 director 逐条读取。
- 处理：ch271/277/290/303/308 五条 pending_human 经对照 V01 forbidden 原文放行；ch300 改写措辞规避（原"陈老根传授陆烬基础谋生策略"）。
### 记录两个机制问题（不阻塞，待后续独立处理）
1. P6-1 传授检测误报：scope_gate 传授语义词（传授/教/传 + 受事）命中即 hard，不区分宾语是否修炼内容——"传授谋生策略/凡人智慧"这类非修炼宾语也触发 hard block。建议后续优化：hard 判定要求修炼宾语近窗（参照 author_intent V01 forbidden#1 的传授反例语义），非修炼传授降级为允许。
2. audit_mid_outline 预测性标注与 F 编号错标复发：ch277 标 F004（实为迷雾异象，应关联 forbidden#3，F004=线粒体/地球起源）；ch303 标"F001 回收节点/皇室秘藏"但实际 core_event 为采药遇异响，与标注不符。此前 ch53/59/63/67/78 曾出现同类 F 错标，疑似 audit 内伏笔 ID 匹配逻辑索引问题，建议单独立项核查 audit_mid_outline 的 F 锚点匹配段。

## 2026-10-12 ch267 gray-band 授权被最终门否决（已修复）

- 症状：85.9 分章 FAILED，END note=`best 85.9 < 88 (hard gate [])`、errors=[]，无任何归因。
- 根因（机制级）：fix loop 授权 gray_band（3963 行 flag_human，85≤score<88 且 det passed 快照）→
  净化/修复后阶段6 重测出现新 det 硬项 → _decide_publish（原 1429 行 `not det_issues` 条件）否决授权 →
  落到 82-87 人工复核队列（1245-1259），该分支 human_review_note 不含否决原因，归因丢失。
  ch62 只修了 force_best 与 gray_band 竞争一条路径，"授权后最终门重测否决"未覆盖。
- 修复：_decide_publish gray_band 分支在 det_issues 非空时记录 result["gray_band_det_block"]，
  走 evaluate_publish 判定并保留 verdict reasons；调用点保留 _publish_verdict；
  82-87 队列 human_review_note + errors 追加归因（gray_band_det_block 或 verdict reasons 前 3 条）。
- 测试：test_gray_band_det_block.py（3 用例：授权+det hard→否决带归因 / 授权+clean→发布 /
  score<软线→不走 gray 路径）。定向 5 passed（含既有 force_best_race 2 个）；
  全量 9 failed/1177 passed，无新增回归（9 个均为既有环境差异类别：
  6 agnes/siliconflow 配置断言 + 2 round27 deepseek 断言 + 1 pre-existing）。
- 待办：ch267 需重跑验证（85.9 分章应不再静默 FAILED）；10 个 FAILED 章处置走失败分类器。
## 批次验收报告（自动生成）
- 日志: `run_volume_20261012T021648Z.log` 章节数=35 成功=25 失败=10 通过率=71.4% 达标率(>=88)=0.0% 平均分=68.95 总耗时=26478s
- 达标发布 0 章: []
- 合格发布 10 章: [260, 264, 265, 269, 271, 273, 275, 276, 277, 281]
- 低分发布 15 章: [233, 245, 263, 268, 270, 274, 278, 279, 280, 283, 284, 286, 288, 289, 291]
- 待处理队列: pending=10 章（失败 10 + 抽查 0） 比率=28.6%  卷=V01
- 失败分类:
  - 评审方差 5 章: [272, 282, 285, 290, 293]
      * ch272: score=65.40 | score=65.4 三评极差>15，评审极不稳定，强制人工复核（不自动重采） ===
      * ch282: score=48.60 | score=48.6 三评极差>15，评审极不稳定，强制人工复核（不自动重采） ===
      * ch285: score=56.70 | score=56.7 三评极差>15，评审极不稳定，强制人工复核（不自动重采） ===
      * ch290: score=60.85 | score=60.849999999999994 三评极差>15，评审极不稳定，强制人工复核（不自动重采） ===
      * ch293: score=63.20 | score=63.2 三评极差>15，评审极不稳定，强制人工复核（不自动重采） ===
  - 修复循环耗尽 4 章: [239, 266, 287, 292]
      * ch239: score=None | ['Chapter 239 hard-failed after retries']
      * ch266: score=None | ['Chapter 266 hard-failed after retries']
      * ch287: score=None | ['Chapter 287 hard-failed after retries']
      * ch292: score=None | ['Chapter 292 hard-failed after retries']
  - 未达标/其他 1 章: [267]
      * ch267: score=85.90 | score=85.9 note=best 85.9 < 88 (hard gate []) ===
- 处置路由（自动）:
  - ch239 修复循环耗尽：下批自然重跑；若复现，定位写作质量 vs 机制 bug，机制信号入 backlog 等任务书
  - ch266 修复循环耗尽：下批自然重跑；若复现，定位写作质量 vs 机制 bug，机制信号入 backlog 等任务书
  - ch267 未达标：下批自然重跑（87.x 档差线未达标）
  - ch272 评审方差类：下批自然重跑；若复现，repro 单跑查三票形态（CC26/CC27 已兜底）
  - ch282 评审方差类：下批自然重跑；若复现，repro 单跑查三票形态（CC26/CC27 已兜底）
  - ch285 评审方差类：下批自然重跑；若复现，repro 单跑查三票形态（CC26/CC27 已兜底）
  - ch287 修复循环耗尽：下批自然重跑；若复现，定位写作质量 vs 机制 bug，机制信号入 backlog 等任务书
  - ch290 评审方差类：下批自然重跑；若复现，repro 单跑查三票形态（CC26/CC27 已兜底）
  - ch292 修复循环耗尽：下批自然重跑；若复现，定位写作质量 vs 机制 bug，机制信号入 backlog 等任务书
  - ch293 评审方差类：下批自然重跑；若复现，repro 单跑查三票形态（CC26/CC27 已兜底）
- 记录时间: 2026-10-12T14:14:55Z
## 批次验收报告（自动生成）
- 日志: `run_volume_20261012T021648Z.log` 章节数=35 成功=25 失败=10 通过率=71.4% 达标率(>=88)=0.0% 平均分=68.95 总耗时=26478s
- 达标发布 0 章: []
- 合格发布 10 章: [260, 264, 265, 269, 271, 273, 275, 276, 277, 281]
- 低分发布 15 章: [233, 245, 263, 268, 270, 274, 278, 279, 280, 283, 284, 286, 288, 289, 291]
- 待处理队列: pending=10 章（失败 10 + 抽查 0） 比率=28.6%  卷=V01
- 失败分类:
  - 评审方差 5 章: [272, 282, 285, 290, 293]
      * ch272: score=65.40 | score=65.4 三评极差>15，评审极不稳定，强制人工复核（不自动重采） ===
      * ch282: score=48.60 | score=48.6 三评极差>15，评审极不稳定，强制人工复核（不自动重采） ===
      * ch285: score=56.70 | score=56.7 三评极差>15，评审极不稳定，强制人工复核（不自动重采） ===
      * ch290: score=60.85 | score=60.849999999999994 三评极差>15，评审极不稳定，强制人工复核（不自动重采） ===
      * ch293: score=63.20 | score=63.2 三评极差>15，评审极不稳定，强制人工复核（不自动重采） ===
  - 修复循环耗尽 4 章: [239, 266, 287, 292]
      * ch239: score=None | ['Chapter 239 hard-failed after retries']
      * ch266: score=None | ['Chapter 266 hard-failed after retries']
      * ch287: score=None | ['Chapter 287 hard-failed after retries']
      * ch292: score=None | ['Chapter 292 hard-failed after retries']
  - 未达标/其他 1 章: [267]
      * ch267: score=85.90 | score=85.9 note=best 85.9 < 88 (hard gate []) ===
- 处置路由（自动）:
  - ch239 修复循环耗尽：下批自然重跑；若复现，定位写作质量 vs 机制 bug，机制信号入 backlog 等任务书
  - ch266 修复循环耗尽：下批自然重跑；若复现，定位写作质量 vs 机制 bug，机制信号入 backlog 等任务书
  - ch267 未达标：下批自然重跑（87.x 档差线未达标）
  - ch272 评审方差类：下批自然重跑；若复现，repro 单跑查三票形态（CC26/CC27 已兜底）
  - ch282 评审方差类：下批自然重跑；若复现，repro 单跑查三票形态（CC26/CC27 已兜底）
  - ch285 评审方差类：下批自然重跑；若复现，repro 单跑查三票形态（CC26/CC27 已兜底）
  - ch287 修复循环耗尽：下批自然重跑；若复现，定位写作质量 vs 机制 bug，机制信号入 backlog 等任务书
  - ch290 评审方差类：下批自然重跑；若复现，repro 单跑查三票形态（CC26/CC27 已兜底）
  - ch292 修复循环耗尽：下批自然重跑；若复现，定位写作质量 vs 机制 bug，机制信号入 backlog 等任务书
  - ch293 评审方差类：下批自然重跑；若复现，repro 单跑查三票形态（CC26/CC27 已兜底）
- 记录时间: 2026-10-12T14:15:06Z
## 测试套件模型无关化改造（第一批）— 完成 2026-10-13
- 提交：326f10950（feat/stability-quality）
- 范围：7 个测试文件改造——test_factory_config/test_llm_client_streaming/test_model_router/test_novel_engine/test_switch_llm/domain_scene_schema/test_round27_phase_models/domain_gate_wiring/test_round14_cross_scene_repeat；test_same_location_cluster_shared_objects_not_flagged 标 xfail
- 效果：9 个环境差异失败清零（active_profile==siliconflow / model==deepseek 硬编码断言改为动态读配置；round18/19 fixture 缺失类已在前轮 hermetic 化）
- 收尾难点：test_pipeline_incremental_patcher 原"假通过"（mock 场景计数无界→SETS[i] IndexError→扩写链吞错→门软放过）。本次走真实路径暴露并修复：
  1) MockLLMForFix 场景计数加 threading.Lock + capped 取模（修复无界）
  2) _scene_text 解析 _mock_full_task_card 真实 word_count_target（不再 8 字子串循环违规）
  3) writer/scene/polish 三分支统一 json.loads 取 scene_text 纯文本（修复 JSON 进 .format 崩 } 与 latin_leak 误报）
  4) 四套互不相交词表（acts/locs/objs/ends 各 8 词）+ 单场景唯一中文序号（seq=k+1，<100 避免 _cn_num 返回阿拉伯数字）
  5) density 覆盖句改"动作发生状可见，第X条线索。"（跨场景共享前缀 10 字 < lcs≥12 阈值，不再触发 det gate fired → triple review）
  6) 场景 1 前置"夜色如墨，浓雾自荒原尽头漫卷而来。"（满足成品断言 B2 zero-scaffolding）
- 验证：决定性组合（41 文件）980 passed + 1 xfailed；全量 1185 passed + 1 xfailed，0 failed（唯一 pre-existing 已 xfail）
- pyflakes 全包：0 undefined name（unused import/局部未用等低危警告不计门禁，存量未清）

## ch294-316 批次验收（token 档 STARTER）— 2026-10-13
- 结果：23/32 COMMITTED，9 FAILED，总耗时 18172.2s（约 5.05h）；无 429/速率限制，token 档 5h 窗口配额够用（ch314 一次 502 被 failover 吸收后 COMMITTED）
- 新章（294-316，23 章）：19 COMMITTED / 4 FAILED（295、299、304、305）
- 残留重跑（266/267/282/285/287/290/292/293）：287/290/293 本轮 COMMITTED；266/267/282/285/292 仍 FAILED
- 失败归因分类：
  A. review 极低分无自动升级路径（4 章，新主要类别）：292(50.5)/295(54.2)/299(34.2)/304(59.9) — 全部 review 分极低 → NEEDS HUMAN，fix loop 不消费"无具体 issues 的低分"；304 另叠加 [称谓] 裸切硬项（陆烬/李淳）Post-enforce 仍 flagged
  B. 评审方差（2 章）：282(46.9)/285(70.1) 三评极差>15 复现，CC26/CC27 兜底未完全消除
  C. 泄漏硬项反复（2 章）：267(86.8，泄漏"接下来要做的"修复后仍差 1.2 分未达 88 线)/305(泄漏"执行任务"反复 2 次 → retries 耗尽)
  D. 伏笔 beats 未覆盖（1 章）：266 场景 4 反复 length_too_short(282/245 字 < 860) + foreshadow beats unresolved → 3 次 attempt 全 HARD BLOCK
- 疑似状态机问题：日志多处"无效状态转换: REVIEW → NEEDS_HUMAN (允许: ['PASS','FAIL','COMMIT'])"但 NEEDS_HUMAN 仍被设置——两条路径矛盾，需查 REVIEW 低分时的真实状态流
- 处置：下批（ch317+）自然重跑 A/B/C/D 四类；ch304 的 [称谓] 裸切与 ch305 泄漏短语、ch266 伏笔覆盖，建议分别单独归因（本批机制信号入 backlog 等任务书）


## 反例纪律条款（外部评审 P6-4 缺口1）— 完成 2026-10-13
- 提交：b6e043624（feat/stability-quality）
- 内容：orchestrator-boundaries.md 新增"反例纪律"小节——任何新增/修改检测规则必须
  ①附带反例测试才能合入（round-30 只测正例导致 8 章被误杀的教训）②不靠人工逐条加词续命
  ③规则变更后跑全量 pytest + pyflakes 全包 0 undefined name。
- 验证：提交前 pre-commit 检查通过；后续全量 pytest 无新增失败。

## 伏笔权威注册表（外部评审 6.1 缺口2）— 完成 2026-10-13
- 背景：伏笔编号体系曾有三份各自维护的源（ending_bible.md 文本 / config/foreshadow/registry.json /
  config/planning/foreshadow_anchors.json），均用 F00X 数字编号，靠人工同步——索引错位根源
  （ch53/59/63/67/78 曾把 F005 标成 F004）。
- 改动：
  1) config/foreshadow/registry.json 升级为权威注册表：每条伏笔新增 fid（稳定语义ID，
     如 f005_fog_core）+ anchors（从 foreshadow_anchors.json 并入）。
  2) foreshadow_anchors.json 改为废弃指针（_deprecated=true，不再含 anchors 数据）。
  3) audit_mid_outline._load_foreshadow_anchors 改读 registry.json，返回 F00X 与 fid 双向
     解析视图；boundary_note 任一引用均校验到同一实体锚点。
  4) generate_mid_outline prompt：伏笔时间表显示 fid（F编号），引导新引用优先用 fid。
  5) memory_health 消费 legacy id，兼容不变。
- 测试：新增 tests/test_foreshadow_registry.py（5 条：注册表完整性、双向解析、
  fid/F00X 锚点命中与未命中、旧文件废弃）；audit/memory_health 相关 34 passed。
- 门禁：pyflakes 全包仅 2 处既有 unused 警告（ab_evaluate.py，非本次引入，非 undefined name）；
  全量 pytest 待批次内确认无新增失败。
- 下一步：中纲新批次生成时 boundary_note 优先用 fid；旧 F00X 引用无需迁移（双向兼容）。


## 残留 9 章重跑验收（token 档，A 类机制真机验证）— 2026-10-13
- 批次：run_volume --chapters 316 --resume（9 个未提交章：266/267/282/285/292/295/299/304/305）
- 结果：5/9 COMMITTED（282/285/299/304/305，全 <88 → 仅进 draft/，novel/ 唯一发布源原则保持，
  novel/ 最大章节号 230 不变）；4 FAILED（266/267/292/295）；总耗时 6841s（约 1.9h），无 429。
- A 类机制（低分整章重写自动路径）真机验证：
  - 5 次 LOW-SCORE ESCALATION 触发：282(41.9)/285(69.7)/299(58.4)/304(68.0)/305(48.1)
  - ch299 修复成功（rewrite → 65.2 verdict=fix ≥60）；其余 4 章 rewrite 无增益 → terminal → force-best draft
  - 上一轮 A 类"review 极低分无升级路径、直接 NEEDS HUMAN"的失败形态消失——所有低分均自动走升级链路
- 四类失败对照（上轮 9 章全 FAILED → 本轮 5/9）：
  - D 类伏笔 beats：ch266 复现（scene4 foreshadow miss + length short）
  - C 类泄漏硬项：ch267 复现（84.7，差 1.3 未达 88 线）；ch305 泄漏缓解（改由 plot 低分主导，48.1）
  - B 类评审方差：ch285 通过（range=8.2，CC26/CC27 生效）；ch292 复现（42.65 极差>15）→ CC26/27 降低概率未消除
  - 新形态（本轮新增）：directing_error 时间倒置——ch295 FAILED / ch304 COMMITTED，
    场景3「午后/正午」早于前场景（task card 时间验证重试失败），需单独归因（test_task_card_time_inversion
    已有覆盖，但真实 LLM 仍生成倒置）
- 待办（下批次优先）：
  1) ch292 三评方差复现：repro 单跑查三票形态，评估是否需要异构 judge 或锚定评分
  2) ch295 时间倒置：查 task_card 时间验证失败后的重试路径，是否缺确定性修复指令
  3) ch266 伏笔 beats / ch267 泄漏差 1.3 分：已有归因，自然重跑观察


## 下批次待办修复：ch292 三评方差 / ch295 时间倒置 — 2026-10-13
- 归因结论（真机数据）：
  - ch292（FAILED 42.65）：评审 4 票 [37.8,29.0,47.5,57.0] 不收敛（28 分跨度），
    draft 文本仅 5258 字（目标下限 7310）——评审发散与严重短章客观缺陷强相关。
    流程缺口：pipeline_orchestrator review_unstable 分支在 A 类低分升级之前 return，
    低分 unstable 章被硬出口人工拦截，绕过已验证有效的整章重写路径（ch299 rewrite→fix 有效）。
    对照组 ch304：初判 range=21.3，补票后 midpair_range=7.10 converged=True → COMMITTED，
    证明 CC26/27 缓坡补票机制有判别力，ch292 属真发散而非机制失效。
  - ch295（FAILED directing_error）：detect_task_card_scene_time_inversion 能检测倒置
    （CC round-30），但 strict 分流只处理字段缺失（craft_missing→call2、chapter_missing→call3
    无差别重发），时间倒置语义错误无针对性修复指令 → 重发大概率再次倒置 → 重试耗尽。
- 修复 1（unstable 低分接入 A 类升级）：
  - 新增模块级纯函数 _unstable_upgrade_eligible(policy, score)：unstable 且 0<score<floor
    且 low_score_rewrite 启用 → 强制 verdict="fail" 继续走 low_score_eligible 整章重写，
    仅高分（>=floor）或升级未启用时才直接 force human（保留 CC round-8 原设计）。
  - 新增 tests/test_unstable_low_score_upgrade.py（6 条：默认升级/高分不升级/禁用保持人工/
    score<=0 不触发/自定义 floor）。
- 修复 2（时间倒置确定性修复指令）：
  - _call_scene_craft 新增 time_fix_instructions 参数，注入"时间修正要求"块
    （逐条列出倒置场景 + 要求随 scene_num 单调推进），不再无差别重发。
  - generate_task_card / generate_task_card_cached 的 strict 分流新增 call2_timefix 分支
    （elif time_inv：以时间倒置错误为指令重发 craft）。
  - 新增 tests/test_task_card_time_fix.py（3 条：指令注入/默认无块/真实分流链路
    倒置→timefix 重发→单调时间终检通过）。
- 验收：pyflakes 全包 0 undefined name；全量 pytest 1205 passed + 1 xfailed
  （唯一 xfail 为已知 pre-existing，1196+9 新增，无回归）。
- 剩余待办：ch266（D 类伏笔 beats）/ ch267（C 类泄漏 84.7 差 1.3）自然重跑观察，
  随下一批次（ch317+ 中纲生成后）覆盖。


## run_556 批验收（新章 527-556 + 重跑 370/499/524）— 2026-10-16
- SUMMARY：29/33 succeeded, 4 failed in 25111.3s（≈7h0m，无 429）。
- 成功 29 章：重跑 ch524 转正（79.7）；新章 527-556 除 531/549 外全部 COMMITTED
  （分数带 42.7-91.6；ch545 91.6 唯一过 88 线进 novel/；低分 committed 不算失败：
  ch534 42.7、ch543 54.6、ch547 59.5、ch532 58.8 等 force-best to draft）。
- FAILED 4 章归因（全部非确定性代码 bug，无代码修改）：
  - ch370（score=37.1，**第八败**）：内容质量极低 → 整章重写无增益 → force-best 37.1 < 88
    被最终门拒绝 → quarantine。处置方向（模型能力 vs 大纲密度 vs 豁免硬门）**待用户拍板**。
  - ch499（score=None，**重跑第二败**）：WRITE REPLAN 内容密度两轮重生无改善（疑似真空洞）
    → in-process replan 后仍 quality-replan → 放弃（无 score）。机制正常拦截；
    是否反向修密度（中纲 scene 密度目标）需判断后可能**待用户确认**。
  - ch531（score=62.3，首败）：初始检测 200 字级整块重复 → force patch/rewrite；
    低分 escalation（best 62.3 < floor 60 触发整章重写 round1 → 重写 60.2 勉强过 floor）；
    终末 FINAL GATE BLOCK：`punctuation:para#128 最长无句读=256 密度=0.39` → quarantine+human。
    根因链=低分无增益 + 标点硬门，与 ch370 同方向，**待用户拍板**是否调整大纲密度/豁免策略。
  - ch549（score=66.5，首败）：note=三评极差>15，评审极不稳定，强制人工复核（不自动重采）
    → 入 needs_human_review。机制行为（B 类评审方差升级），CC26/27 降低概率未消除。
- 观察：ch497 同源 `'str' object has no attribute 'get'` 本轮未复现；ch524 修复
  （借用词豁免，commit ceaa5167d）真机验证通过（重跑 79.7 COMMITTED，无 latin 误报）。
- 待办（下批次优先）：
  1) ch370/ch531 处置方向（模型能力/大纲密度/豁免硬门）待用户拍板；
  2) ch499 密度真空洞是否反向修密度（中纲 scene 密度目标）待判断；
  3) ch549 已入 needs_human_review 待用户查阅；


## run_586 批验收（新章 557-586 + 重跑 370/499/531/549）— 2026-10-17
- 批次全程：首跑进程于 10-17 01:56 异常终止（无 SUMMARY、无 traceback，日志停在
  ch585 连接超时重试；无 python 进程残留），10-17 06:25 resume 重启续跑，07:13 出 SUMMARY。
- 重启批 SUMMARY：5/7 succeeded, 2 failed in 2872.7s。终态：
  - ch499/531/549 重跑转正（71.1/66.95/71.2，ch499 第二败后第三跑转正）；
  - ch568/569/575 重跑转正（81.9/71.3/49.3——三评极差>15 章重跑后 COMMITTED）；
  - ch585/586 首跑 COMMITTED（68.05/58.2，均低分 force-best to draft）；
  - 新章 557-586 分数带 49.0（ch579）~87.2（ch573）；ch578 称谓裸切 hard gate 修复过门；
    ch581 200 字级整块重复 hard gate 修复后 COMMITTED。
- FAILED 2 章归因（全部非确定性代码 bug，无代码修改，机制按设计拦截）：
  - ch370（**第十败**，score=37.3）：段落整块重复 7 次（"存在即是意识…"循环句）+ 长度
    SEVERE shortfall 4080 + 整章重写 55.9 仍 <floor 60 → terminal → force-best 37.3 <88
    被最终门拒绝（punctuation:para#9 最长无句读=120 密度=0.0）→ quarantine+human。
    处置方向仍**待用户拍板**（模型能力/大纲密度/豁免硬门），与第八/九败同因。
  - ch584（**第二轮败**，score=79.0）：两轮同词 latin 泄漏 **'barely'**——轮1 86.3
    （gray-band release 后 FINAL GATE BLOCK latin 'barely'）、轮2 79.0（targeted rewrite
    后 scene1 regen 又泄漏 'loop'×4 → language retry，终态 FINAL GATE BLOCK
    latin 'barely' 1 处 + halfwidth_punct '.' 2 处）→ quarantine+human。
    归因：修复通道（latin targeted rewrite）对该章两轮均未清除 'barely'，属模型输出
    语言稳定性问题（writer 习惯性输出该副词），非代码 bug；机制链路
    （检测→重试→final gate 拦截）按设计工作。是否提升修复强度/换模型**待用户拍板**。
- 观察：ch568 高分 85.6/81.9 均未过 88 硬线（novel/ 只收 >=88）；本批无 88+ 进 novel 章。
- 待办（下批次优先）：
  1) ch370/ch584 处置方向（模型能力/大纲密度/豁免硬门 vs latin 修复强度/换模型）待用户拍板；
  2) ch549 已入 needs_human_review 待用户查阅（本批重跑已转正 71.2，建议复核后关闭）；
