# Prime-Agent-Novel-Engine 优化日志（问题与修复记录）

> 生成时间：2026-09-05 02:00，SDD 现状刷新：2026-09-05  
> 分支：`fix/architecture-patch-wiring`（基于 `feat/stability-quality`，保持本地未推送）  
> 关联产物：`archify-novel-engine-architecture.html` / `runtime/logs/production.log` / `audit/per_chapter_reviews.json` / `docs/superpowers/plans/2026-09-05-quality-foundation.md`

---

## 1. 总览

| 轮次 | 触发 | 核心问题 | 关键修复 | 产物 |
|---|---|---|---|---|
| R1 | 人审 P0 致命 | 成品残留 `【场景】/※/（章末钩子）`、质量门形同虚设（60 分即发布）、无重复/截断检测 | 成品净化 `purify_novel_for_publish`、提交加闸 `≥88+无high/block+确定性门控`、确定性检测器 | `repetition_detector.py` |
| R2 | 10 章 0 落盘死循环 | 移动字数目标（5000→3500→3000）致永远追不上 | 冻结单章 `task_card`（`_frozen_*`）、统一区间 `0.65-1.35`、强制后重跑门控 | `pipeline_orchestrator: _frozen` |
| R3 | 进程被 opencode 重启杀死 | `bash` 子进程挂在侧车进程树 | WMI `Win32_Process.Create` 脱离至 `WmiPrvSE→services.exe` | `launch_10.bat` + WMI |
| R4-5 | POLISH 20min 空转 + 修复振荡 | 90s 一刀切误杀整章 polish、patch 找不到 marker、长度门 1 字符误杀 | 超时分级 `POLISH 300s / Scene 90s`、`ReadTimeout` 快速切 `Qwen27B`、分场景批处理→单次全文、长度容差 `+2%` | `llm_client`/`model_router`/`writer` |
| R6 | `violations` 未初始化崩溃 + 场景缺失 | `if apply_world_state` 内赋值块外引用 `UnboundLocalError`、writer 漏写 1 场景 | 前置 `violations=[]`、WRITE 后场景数校验补写 | `pipeline_orchestrator` |
| R7 | 成品污染换形态（节拍点/场景小结/字数） | 清洗为固定黑名单、发布前无终检 | 段落级判伪（逐段分类剥离指令段）、发布前 `verify_no_scaffolding` 硬阻断、4 例回归 `test_purify_regression.py` | `repetition_detector` |
| R8-9 | 括号指令 `（章末钩子：陆烬（婴儿）` 截断残留 + 93 分因 1 字符超标被弃 | `（章末钩子` 截断半行未剥离、长度门 `9991>9990` 1 字符误杀、泄漏只拦不修 | 括号指令整行移除（含截断）、泄漏先修再弃（`purify` 后复检）、长度容差 `+max(50,2%)`、标题 `^# 第N章` 校准、目标全局常量 7500 | `repetition_detector`/`pipeline` |
| R10 | 章末钩子可见拼接 `*（章末钩子：...）*` 为唯一泄漏源 | `writer:365` 直接拼接 hook 标记 | 删除拼接点，hook 改为末场景写作指令自然收束；写盘与检测同源 `purified` | `writer_agent` |
| R11 | 截断制造 high → 90+ 被拒，耗尽即跳过 | `post-enforce` 按字符硬截断砍掉场景/hook → 评审判 high → 90+ 整体弃 → 重试翻倍 | 按场景等比裁剪保所有场景与 hook、`best≥88` 硬阻断时强制发布 `best`（附 note）、耗尽后落 `best` 而非跳过 | `pipeline: _enforce` + 终态发布 |
| R12 | 单章 27min（目标 15min） | 目标中途 4900→7500 双目标、polish 4 次串行 7min、净化误删场景标题致 mismatch | 目标批次级冻结 7500 常量（`_global_target`）、polish 单次全文 300s、writer 3 场景并发、净化保留 `【场景】` 边界 | `pipeline`/`writer` |
| R13 | Round12 遗留收尾 | 同上，部分已落地，本轮收口为 R12 的后续验证批次 | 同 R12，已以 `01:50 WMI 59328→python 58016` 重跑 10 章脱离验证 | - |

---

## 2. 架构级根因与修复映射（对应用户 5 点疑虑）

| 用户疑虑 | 验真 | 修复落点 |
|---|---|---|
| LLM Providers 孤立 | Director/Writer/Reviewer 确经 `ModelRouter(scene/polish/review)` 汇聚到 SiliconFlow，WorldSim 纯本地 | 架构图 `Orchestrator→LLM` 重標註为 `ModelRouter 复用`，避免孤立误读 |
| Patcher 孤立 | `engine/patcher.py` 仅被 `tests` 引用，主流程 `fix` 分支未调用 | `pipeline: _patch_weak_scenes` 接活（解析 `fix_scope` 场景号→`writer.generate_scene`→`IncrementalPatcher.apply_scene_patch` 热插拔，`mock` 模式跳过） |
| Bible 仅到 Orchestrator | Director 注入 `world/character/style` 全文，Writer 仅靠 `task_card` 透传 | `writer: _bible_snippet()` 1200 字显式注入每场景 prompt，`chapter_director` 混合召回 `hybrid_search` 补充隐含关系 |
| StateDB 精确匹配盲区 | `characters/foreshadows/relationships` 精确 `=` + `keyword` 精确交集 | `engine/db.py: hybrid_search()`（token 重叠 BM25 轻量）+ `memory_manager: hybrid_retrieve()` + `director` 调试日志 |
| 节奏/长度门 | `_enforce 0.85-1.15` 与 `detect 0.65-1.35` 矛盾、每章 LLM 生成目标漂移 | 统一 `0.65-1.35+容差`，`writer` 场景目标 `1000→1800`，`pipeline` 全局常量 7500，`_enforce` 按场景等比保 hook |

---

## 3. 当前管线（10 章批次）

```
PLANNING → WORLD_SIM → DIRECTING → SYNOPSIS → WRITE_SCENE (3-4 场景并发) 
  → POLISH (单次全文 300s, 90s/场景 降级) → 净化(purify, 标题校正) → 强制(_enforce 0.65-1.35+容差, 保场景/hook)
  → 终稿评审( head/mid/tail 采样, 6 维 25/20/20/15/10/10, pass≥88/fix≥60) → 确定性门控(硬:重复/截断/净化/长度; 软:套话/时间线)
  → 发布门控(仅 hard+high/block 阻断, 泄漏先修再弃) → COMMIT(写 chapters/novel, 附 hook) / Draft(未达标)
  ↳ fix 循环: 场景级 patch 优先 → 全章重写(保持或缩短) → 每轮重跑门控 → 3 轮不超 best 早停 → 耗尽后强制发布 best
```

- **模型路由：** `scenes: DeepSeek-V3.2 → Qwen27B → Ling-mini`, `polish: DeepSeek-V3.2 → Qwen27B (300s)`, `review: agnes-2.5-flash`
- **阈值收敛：** `quality_thresholds.json pass 85→88`, `runtime_config pass 85→88`, `pipeline` 统一 `publication_line 88`
- **并发：** `max_parallel_chapters 2→1`（状态隔离前）

---

## 4. 关键文件清单

| 路径 | 职责 | 本次改动 |
|---|---|---|
| `agents/writer_agent.py` | 场景并发/原子落盘/显式 bible 注入/perspective | 顺序→并发、bible 1200 字注入、婴儿期视角开关、单次 polish、partial 原子落盘 |
| `agents/chapter_director.py` | 任务卡生成、混合召回 | word_count 1000→1800/2000、hybrid_search 调试、_global_target 冻结 |
| `pipeline/pipeline_orchestrator.py` | 状态机、门控、提交、修复循环 | 冻结 task_card/synopsis、统一区间、软硬分离、泄漏终检、强制发布 best、截断保场景/hook、场景数校验补写 |
| `engine/db.py` | StateDB 精确检索 | `hybrid_search()` + WAL checkpoint 健壮化 |
| `core/memory_manager.py` | 记忆/beat 历史 | `beat_history.json` + `get_recent_beats()` + `hybrid_retrieve()` |
| `core/llm_client.py` | LLM 调用与重试 | `ReadTimeout` 快速失败切 fallback、鉴权 401 不重试、`timeout` 覆盖、`_get_client(timeout)` |
| `core/model_router.py` | 多模型路由 | `polish` 重试收敛 2 次、断连/401/超时快速切下一模型、Qwen27B 兜底、`use_mock` 兼容 |
| `quality/repetition_detector.py` | 净化与确定性检测 | 段落级判伪、括号指令截断半行移除、泄漏终检、长度容差、4 例回归 |
| `config/runtime_config.json` | 运行时阈值与路由 | 温度 0.85→0.7、scenes 主 DeepSeek、polish 双模型、max_parallel 1 |
| `config/quality_thresholds.json` | 评分分级 | pass 85→88 |
| `tests/test_purify_regression.py` | 回归 | ch2 节拍点/ch3 场景小结/ch4 字数/ch7 场景指令 4 例 |

---

## 5. 日志与验证

- **分支：** `fix/architecture-patch-wiring`（`git log --oneline -10` 10+ 次 fix 提交，见上表）
- **架构图：** `archify-novel-engine-architecture.html`（spec 84b9b8… / artifact c35fd…，9/9 showcase pass）
- **运行时志：** `runtime/logs/production.log`（FileHandler）+ `production_10_real_v2.log`（WMI 重定向，`Invoke-WmiMethod 59352→WmiPrvSE` 脱离验证：`WmiPrvSE 2084 → services.exe`）
- **审计：** `audit/per_chapter_reviews.json`（每章 6 维分数/issues）、`audit/defects.json`（forbidden）、`chapters/novel/chapter_*.txt`（已净化成品，`grep -r "节拍点\|场景小结\|当前字数\|【\|（章末钩子" chapters/novel` 应 0 命中）
- **回归：** `pytest tests/test_purify_regression.py -q` 5 passed；`npm run check` 0 告警

### 最新批次（v12/v13 脱离重跑 10 章，当前 01:50 起）

- **启动：** `WMI 59352 → python 58016`，`Production: 10 chapters, start=1, resume=0`，`chapters/novel 0 / draft 0` 全量重置
- **耗时预算：** 目标单章 ≤15min、批次 ≤3h（场景并发 2-3min + 单次 polish 300s + 1 次评审）
- **验收：** `grep` 零泄漏、跨章同质 beat ≤1 次、每章结尾有叙事钩子、标题统一 `# 第N章`、评分 89+ 直接落盘（耗尽亦发布 best 附 note）

---

## 6. 复跑命令

```bash
# WMI 真正脱离（不受 opencode 重启影响）
Invoke-WmiMethod -Class Win32_Process -Name Create -ArgumentList 'cmd.exe /c "D:\AI\Prime-Agent-Novel-Engine\Prime-Agent-Novel-Engine\launch_10.bat"'
# 或按引擎规范：先 reset 再重跑
python -m novel_engine.pipeline.production_runner 10 --real  # 在 src/novel_engine 下，需 PYTHONPATH=src 且 ZLEAP/AGNES 已设
```

> 本文档由 `opencode/muse-spark` 基于 `chapter_review_round*.md` 人审意见与 `fix/architecture-patch-wiring` 代码落点自动生成。

---

## 7. Q1–Q16 grill 共识 → SDD 结构重构（2026-09-05，当前现状）

**背景：** R1–R13 被判定为打地鼠（每轮修复制造下一轮问题）。经 grill-me 三轮 frontier（Q1–Q6 → Q7–Q12 → Q13–Q16）锁死共识：冻结局部补丁（崩溃/数据损坏类热修例外），按 A→C→B→D 顺序做结构修复。开发计划见 `docs/superpowers/plans/2026-09-05-quality-foundation.md`，11 个任务全部 complete（子代理执行＋任务评审＋fix loop＋终审 With fixes→fix wave→复检 clean，共 20 个提交）。

### 7.1 锁死共识（Q1–Q16，不重议）

- **Q1 冻结**：单个 case 只记 `docs/superpowers/quality-backlog.md`，不打补丁。
- **Q2/Q7 唯一源 6 键＋8 级表**：`chapter_target_chars`、`min_ratio`、`max_ratio`、`tolerance_chars`、`publication_line`、`severity_map`（`leak_scaffolding/truncation/verbatim_duplication/forbidden_block/scene_missing=hard`；`length_deviation/beat_repetition_thematic/hook_missing=note`）。scene 目标一律派生（余数归末场景）。
- **Q3/Q8 结构化 Writer**：严格 JSON `{"scene_id","scene_text","hook","beats"}`，`scene_text` 纯叙事，`hook` 整句 raw append，`word_count` 实测不自报。
- **Q4 统一仲裁 (c)**：leak/截断硬否决（含 93 分）；长度只记 note＋自动收束；逐字重复 hard、主题重复 note。
- **Q5 草稿分流＋监控**：耗尽 best 只进 `draft/`（附 note）；`forced_draft_rate` 记录 3 批次观察期后再定 15% 是否 enforcing。
- **Q6/Q11 Runner 拥有持久化**：`chapter_{n}_partial.jsonl`（行级 JSON 校验，坏行＝该场景未完成）；状态机只做内存流转。
- **Q9 严格度跟实际 provider**：DeepSeek-V3.2 严格，Qwen27B/Ling-mini 宽松（围栏/regex 兜底）。
- **Q13 beats 试点**：30 场景人工核对（权重看漏报率），fallback（关键词重叠事后抽取）已预定。
- **Q14 `leak_scaffolding` 保留**：降级为 POLISH 后兜底防线；`repetition_detector`＋4 例回归在 B 落地前不得删除。

### 7.2 SDD 落地（11 任务，全部 complete）

| 任务 | 内容 | 评审结论 |
|---|---|---|
| 0 | 冻结台账 `docs/superpowers/quality-backlog.md` | clean（评审误报字节问题已字节验证驳回） |
| A1 | `core/quality_policy.py`（6 键＋8 级表＋派生＋查表） | clean |
| A2 | 全部数值读取方迁移（一处不变行为） | clean（2 parked：根路径注释差异、spy 覆盖面） |
| A3 | severity 字面量＋场景目标迁移 | clean（reviewer-high 非阻塞系共识语义，已锁定） |
| C1 | `pipeline/quality_gate.py` 唯一仲裁＋1 轮 fix（日志与裁决对齐） | clean |
| C2 | `forced_draft_rate` 记录＋2 轮 fix（接线＋success/published 谓词） | clean |
| B1 | `agents/scene_schema.py`＋provider 戳记 | clean |
| B2 | Writer 发 JSON、管线组装＋1 轮 fix（`agent_api` 同源＋废弃断言更新） | clean（`test_novel_engine.py:212` 红灯系预存 mock-vs-硬门矛盾，已记 backlog） |
| B3 | beats fallback＋试点协议＋1 轮 fix（降序修正） | clean |
| D1 | `pipeline/chapter_journal.py`＋1 轮 fix（真路径测试＋复用 helper） | clean |
| D2 | resume 归 runner＋1 轮 fix（reset 门＋残留文件＋Task-11 零调用验证） | clean；Task-11 废弃已记 backlog |

### 7.3 验证基线（2026-09-05）

- 新测试 **39/39 通过**（`test_quality_policy`＋`test_quality_gate`＋`test_scene_schema`＋`test_chapter_journal`＋`test_purify_regression`）。
- 全套件：20 通过＋4 项预存失败（`test_pipeline_incremental_patcher`＋3×`test_autonomy`，stash 验证与本分支无关）。
- 工作树干净；生产进程 0 残留；旧代码章节残留（novel 8＋draft 18）已在新一轮验证跑前清空（见 §7.4）。
- 待人项：beats 30 场景试点；`forced_draft_rate` 攒 3 批次；密钥吊销（GitHub PAT/SiliconFlow/Agnes）；推送 GitHub（保持本地指令下未推）。

### 7.4 新一轮 10 章验证跑（新代码，WMI 脱离）

- 批次启动前 `reset_runtime_state` 全量清空（含 `state.db`、`resume_state.json`、`last_success_chapter.txt`、`chapters/draft` 旧残留）。
- 启动：`Invoke-WmiMethod Win32_Process.Create 'cmd.exe /c launch_10.bat'` → 父链 `WmiPrvSE→services.exe`，与 opencode 进程树脱离。
- 判定：`score≥88` 且无 hard 否决进 `novel/`；耗尽 best 进 `draft/`（附 note）；落盘打印耗时＋attempt 数。
