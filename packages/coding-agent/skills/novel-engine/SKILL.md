---
name: novel-engine
description: Generate evidence-checked condensed novel chapters and portable external-LLM expansion packages for the 3,800-chapter project. Use when asked to continue the novel, produce condensed chapters, export expansion prompts, or run continuous generation. The prose pipeline has been removed; this skill is condensed-only. Project canon lives under src/novel_engine/.
---

# Novel-Engine Skill（Condensed-Only）

本技能包现在只提供 **condensed（简写生产 + 外部扩写）** 管线。原正篇管线
（agents / pipeline / quality / engine / adapters）及其测试、脚本已移除。

## Condensed 简写生产

面向 3,800 章 / 1,600 万汉字项目。产出：
- 叙事简写（结构化事实层，StateDB 确定性追踪）
- 自包含外部 LLM 扩写包（场景卡 + 扩写/验收/修订提示词 + manifest）

简写模式使用逻辑与信息覆盖审查（而非正篇的文学评分或 10,000 字门）。
导出的扩写提示词**不算**已扩写/已发布的正文章节。

### 运行

在本技能包目录（依赖已安装）：

```bash
PYTHONPATH=src python -m novel_engine.condensed run --output /workspace/novel-condensed-3800 --end 3800
PYTHONPATH=src python -m novel_engine.condensed status --output /workspace/novel-condensed-3800
PYTHONPATH=src python -m novel_engine.condensed verify --output /workspace/novel-condensed-3800
```

Agent API：`await novel_engine.generate_condensed_chapter(1, output_root="...")`。
API 密钥走 `config/llm_providers.json` 的 active profile + 注入的环境变量；不要把密钥写进导出包。

## 架构

1. **StateDB（代码级精确状态检索）**：SQLite 确定性查询角色/境界/位置/伏笔状态，绕过概率性 RAG 漂移，保证百万字级一致性。
2. **确定性 commit 链**：内容哈希 + 前章哈希 + timeline 校验，SQLite 事务内提交。
3. **逻辑验收**：REVIEW 8 维度（canon/causality/knowledge/continuity/coverage/foreshadow/expandability/boundaries），不评分。
4. **中纲注入（P0-1）**：`mid_outline_loader` 把人工审核过的章级意图（mid_outline_*.json）注入 PLAN 提示词，防止结构模板化。

## 目录结构

```
packages/coding-agent/skills/novel-engine/
├── SKILL.md
├── README.md
├── docs/简写生产与外部扩写.md     # condensed 模式权威说明
└── src/novel_engine/
    ├── __init__.py               # condensed-only 包入口
    ├── condensed/                # 🌟 condensed 管线核心
    │   ├── runner.py             # 生产运行器（PLAN/DRAFT/REVIEW/commit）
    │   ├── prompts.py            # PLAN/DRAFT/REVIEW/REPAIR 提示词
    │   ├── mid_outline_loader.py # 中纲注入转换层（P0-1）
    │   ├── export.py             # 扩写包导出 + EXPANSION/CHECK/REVISE 提示词
    │   ├── accept_check.py       # 程序验收（字数/禁词/元数据）
    │   ├── dual_check.py         # 程序+LLM 双验收（含快判通道）
    │   ├── store.py              # SQLite commit 链
    │   ├── timeline.py           # 确定性日程计算
    │   └── contracts.py          # 结构校验
    ├── core/                     # 核心支撑（LLM 客户端/模型路由/执行预算）
    ├── scripts/
    │   ├── generate_mid_outline.py   # 中纲生成
    │   └── audit_mid_outline.py      # 中纲校验
    ├── bible/                    # 设定五件套
    ├── config/                   # planning/llm_providers/leak_terms
    └── tests/                    # condensed 相关测试
```

## 测试

```bash
cd src
python -m pytest novel_engine/tests/test_condensed_production.py -q
```

## 注意

- condensed 不依赖正篇的 agents/pipeline/quality/engine。
- `core/model_router.py` 的 `generation_constraints` 已做惰性导入：quality 模块缺失时自动降级为无 guardrail，不影响路由/故障转移。
