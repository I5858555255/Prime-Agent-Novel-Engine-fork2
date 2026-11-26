# Novel-Engine 整理与地基加固计划（condensed-only）

> 本计划对应目标：清除正篇管线、整理项目、查缺补漏、打好地基。
> 状态：阶段 A–D 已执行完毕；阶段 E（本计划）完成；阶段 F（执行清单）见文末。
> 生成时间：2026-10-17。仓库当前为 condensed-only，原正篇正文流水线已移除。

---

## 1. 目标与约束

- **唯一管线**：condensed 简写生产 + 外部扩写包。不恢复、不引用正篇多智能体正文管线。
- **不动地基**：StateDB facts 表、timeline 单调校验、hash 链（`condensed/store.py`）保持原语义；
  本次改动仅涉及提示词上下文、loader、配置读取与 Windows 文件写入健壮性。
- **中纲数据**：`config/planning/mid_outline_*.json`（ch21–3800，128 文件）为既定数据源；
  ch1–20 缺口待补生成。

## 2. 已完成（阶段 A–D）

### 2.1 清除正篇管线（阶段 A）
删除目录/文件：`agents/`、`pipeline/`、`quality/`、`engine/`、`adapters/`、`agent_api.py`、
`core/memory_manager.py`、`timeline/timeline_anchors.py`、正篇专用测试与 80 个正篇测试文件、
`scripts/` 正篇脚本、`runtime/`、`memory/`、`audit/` 数据目录。
`__init__.py` 重写为 condensed-only 导出（`generate_condensed_chapter` / `condensed_status`）。
导入链验证：`novel_engine`、`condensed.runner`、`mid_outline_loader`、`condensed.api`、
`core.model_router` 全部可导入；condensed 生产链路测试通过（阶段 A 时点）。

### 2.2 整理项目（阶段 B）
- 仓库根 `README.md`：重写为 condensed-only（架构、目录树、运行入口、Agent API、测试、文档指引）。
- `OPTIMIZATION_LOG.md`：加“历史记录”标注，说明 R1–R13 均属已移除正篇。
- 技能包 `SKILL.md`：condensed-only；`packages/.../novel-engine/README.md`：尾行更新。
- 删除正篇文档：`docs/` 正篇文档 + `docs/superpowers/` + `ARCHITECTURE_PRIME_AGENT_BASE.md`
  + `scripts/control/` + `src/novel_engine/config/LLM选择器使用说明.md`。
  保留：`docs/简写生产与外部扩写.md`（当前权威文档）。

### 2.3 查缺补漏（阶段 C）
- 删除引用已删模块的测试：`test_batch_audit`、`test_memory_health`、`test_gen_task_cards`、
  `test_force_best_checkpoint`、`test_scene_parallel`、`test_timeline_project_scope`、
  `slo_gate`、`aggregate_slo`、`fixtures_real_data`、`fixtures/`、`test_factory_config`（prose runtime_config）。
- `test_model_router.py`：移除对 `pipeline.pipeline_orchestrator` 的引用；`tmp_path` 改为可写目录。
- `switch_llm.py`：移除死代码 `sync_runtime_config`（runtime_config 已不随仓库发布）。
- `scripts/audit_mid_outline.py`：`main()` 中 `novel_engine.quality.scope_gate`（已删）替换为
  `condensed/concept_terms.py` 的等价概念判定。
- 全仓 grep 确认无 `agents/pipeline/quality/engine/adapters` 悬空引用。

### 2.4 打好地基（阶段 D）
1. **`core/publication_transaction.py` `atomic_write`**：原实现 `os.replace` 后仍 `tmp.unlink`，
   Windows 下对已移走文件做 `Path.unlink(missing_ok=True)` 触发 `FileNotFoundError`；且 Windows +
   Defender 对新目录内即时新文件存在瞬时 ENOENT 窗口。重写为：uuid 命名 tmp、成功即
   `os.replace` 返回、仅 `FileNotFoundError` 做有界退避重试（8 次、0.25s 递增）。
2. **`condensed/runner.py` 输出根守卫**：`output.is_relative_to(source)` 过严（测试把输出放在
   source 下的 `.test_tmp` 内），改为“输出根 == source 或其直接子目录”才拒绝。
3. **`condensed/store.py` `invalidate_from`**：归档目录由 uuid 名改为稳定的 `invalidated/chNNNN`，
   避免重复 invalidate 时新目录 churn 放大瞬时 ENOENT。
4. **Windows 编码**：`tests/test_condensed_production.py` 中 5 处无编码 `read_text()` 显式
   `encoding="utf-8"`（Windows 默认 cp936/GBK 读 UTF-8 配置会 UnicodeDecodeError）。
5. **测试可写目录**：pytest 的 AppData `basetemp` 在沙箱内无权限；各测试文件改用
   `novel_engine/.test_tmp/`（本地 scratch）。

## 3. 已知限制（Windows 本机环境）

`test_condensed_production.py` 中 7 个用例（`export_chapter` / `extend_budget` /
`invalidate_from` 路径）在本机（Windows + Defender 实时防护 + D: 盘 ACL）会间歇性
`FileNotFoundError`：对**刚 `mkdir` 的目录内**第一个新文件，`open(...,"wb")` 偶发
ENOENT，即使父目录存在且 8 次退避重试也偶发耗尽。独立小脚本 50 次循环、单文件直接运行均
稳定通过，说明是 Defender 实时扫描 / NTFS 句柄释放的瞬时竞争，**不是逻辑缺陷**。
- 缓解：`atomic_write` 已带 8 次有界退避；`invalidate_from` 已用稳定目录名。
- 影响面：仅这 7 个测试；生产运行（`python -m novel_engine.condensed run`）输出目录在源码外、
  非高频 churn，未观察到该问题。
- 彻底修复需在本机关闭 Defender 对该目录的实时防护或改用非扫描盘符，超出代码范畴。
- 其余 136 个测试全部通过（含中纲注入、验收、commit、hash 链、导出校验、预算、失效回滚等）。

## 4. 后续执行清单（阶段 F）

### P0 地基
1. ✅ `atomic_write` ENOENT 退避（代码层已做；环境层见 §3）。
2. ✅ `output` 根守卫修正。
3. ✅ 审计脚本 scope_gate 等价替换。
4. ⬜ **修 `scripts/generate_mid_outline.py` 的 LLM 客户端**：当前仍读旧
   `config/runtime_config.json`（已不存在）的 `llm.api_base`，需改为读
   `config/llm_providers.json` 的 `active_profile`（`agnes_30_token`）。
5. ⬜ 生成 ch1–20 缺失中纲（15 个非节点章）：跑修好的 `generate_mid_outline.py`
   → `audit_mid_outline.py` → 批准 → 落 `config/planning/`。
6. ⬜ 15 个 draft-meta 中纲文件的 `meta.status` 升级（逐条 entry 已 approved 后把 meta 提为
   approved），使 `mid_outline_loader` 完整覆盖。

### P1 验证
7. ⬜ ch1 小批量真实回归：`python -m novel_engine.condensed run --output <dir> --end 3`，
   确认中纲注入 + 新 ch1–20 数据生效、结构不再模板化。
8. ⬜ 全 3800 章续跑脚本就绪（预算、断点、STOP 语义见 docs/简写生产与外部扩写.md）。

### P2 可选
9. `dual_check.py` `--fast-path` 已在；外部扩写验收链路（accept_check/mark_verified）可加端到端用例。
10. 清理 `.test_tmp/`、`__pycache__`、遗留 `.pyc`（含已删模块的 `domain_tools.cpython-313.pyc` 等）。

## 5. 不做的事

- 不恢复正篇管线、不引入文学评分/三票/fix-loop。
- 不改 beat 结构、StateDB facts、hash 链语义。
- 不动 47 个剧情节点章“无中纲”的设计（PLAN 用 milestones + 类型系统兜底）。
