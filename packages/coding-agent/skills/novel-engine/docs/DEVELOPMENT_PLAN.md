# Novel-Engine 千万字级优化开发计划

## 背景
- 当前进度: 203章完成 / 3000章目标, ~83万字符
- 目标: 支撑10M+字符级别的稳定生产
- 瓶颈分析: 每章平均~4000 tokens输出, 3000章 ≈ 12M tokens; 当前pipeling无并行、上下文无界增长

---

## Task T1: 拆分 Director Context 为 Static + Dynamic 两层
**文件**: `agents/chapter_director.py`, `pipeline/pipeline_orchestrator.py`
**影响**: 每章 director prompt 从 ~50K tokens 降至 ~5K tokens
**风险**: 低 — 仅改变 context 传入方式, LLM调用接口不变

## Task T2: 并行化独立场景生成
**文件**: `agents/writer_agent.py`
**影响**: 章节生成 wall-clock 时间减少 40-60%
**前置**: T5 (call_log 线程安全)
**风险**: 中 — 需保证场景顺序合并正确性

## Task T3: 限制近期章节记忆窗口至30章
**文件**: `core/memory_manager.py`
**影响**: 每章上下文减少 ~25K tokens (80→30章)
**风险**: 低

## Task T4: Lazy DB Reload (mtime 比较)
**文件**: `pipeline/pipeline_orchestrator.py`
**影响**: 每章减少 3次全量 JSON→SQLite 重载
**风险**: 低

## Task T5: Call Log 线程安全改造
**文件**: `core/llm_client.py`
**影响**: 支持 T2 并行化的前提
**风险**: 低

## Task T6: 滑动窗口审查裁剪至最近3个窗口
**文件**: `core/memory_manager.py`
**影响**: audit 文件不再无限增长
**风险**: 低

## Task T7: 确定性模板章节快捷路径
**文件**: `pipeline/pipeline_orchestrator.py`, `agents/chapter_director.py`
**影响**: 无剧情变化的章节跳过 LLM 调用
**风险**: 中 — 需验证模板匹配逻辑不破坏剧情连贯性

## Task T8: 重构 generate_single_chapter 为阶段方法
**文件**: `pipeline/pipeline_orchestrator.py`
**影响**: 代码可维护性, 无行为变化
**风险**: 极低

## Task T9: 修复 production_runner 重复计算 avg_score
**文件**: `pipeline/production_runner.py`
**影响**: 消除冗余计算
**风险**: 无

## Task T10: 修复硬编码默认模型名
**文件**: `core/llm_client.py`
**影响**: 确保默认配置与实际使用一致
**风险**: 极低
