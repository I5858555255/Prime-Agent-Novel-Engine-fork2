"""
审查评分 Agent：对生成的章节进行分级审查。
120分制：剧情一致性(25) / 人物一致性(20) / 伏笔执行(20) / 文风符合度(15) / 节奏控制(10) / 创新亮点(10) / 钩子强度(8) / 读者留存(7) / 爽点/悬念密度(5)
总计: 25+20+20+15+10+10+8+7+5 = 120 分，系统自动归一化到百分位（normalized_score）
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

from novel_engine.core.llm_client import LLMClient, call_llm

logger = logging.getLogger(__name__)

# 各评分维度满分（合计 120 分）
# 原有：剧情(25) 人物(20) 伏笔(20) 文风(15) 节奏(10) 创新(10) = 100
# 新增：钩子强度(8) 读者留存(7) 爽点密度(5) = 20，合计 120
DIM_MAX = {
    "plot_consistency": 25,  # 缩减后比例保持不变占原有的 25/80
    "character_consistency": 20,
    "foreshadow_execution": 20,
    "style_match": 15,
    "pacing": 10,
    "innovation": 10,
    # 新增维度
    "hook_strength": 8,      # 章末钩子力度：是否留人、是否有悬念
    "reader_retention": 7,   # 读者留存意愿：读后是否想继续
    "cliffhensity": 5,       # 爽点/悬念密度：单位章节爽点/悬念数量
}


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_scene_ids(value) -> list:
    """Normalize a reviewer issue's scene_ids into a sorted-unique list of ints.

    Accepts ints, numeric strings ("场景3" / "3"), or lists. Empty list means a
    truly chapter-global issue (hook / overall retention) with no scene location.
    """
    if value is None:
        return []
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        raw = [value]
    elif isinstance(value, str):
        raw = re.findall(r"\d+", value)
    elif isinstance(value, list):
        raw = value
    else:
        raw = []
    out: list = []
    for x in raw:
        try:
            n = int(x)
        except (TypeError, ValueError):
            m = re.search(r"\d+", str(x))
            if not m:
                continue
            n = int(m.group())
        if n >= 1 and n not in out:
            out.append(n)
    return out


def _coerce_review_object(obj) -> dict:
    """flash 偶发经 json_repair 后顶层返回 list（如 [ {评审...} ] 或裸 issues 列表）。

    - dict：原样返回；
    - list：优先取其中"带 scores 字典"的 dict；否则取首个 dict；都没有返回 {}（交由
      is_valid_vote 按 raw=0 技术废票剔除，绝不让单票崩掉整个三评 try）。
    """
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, list):
        dicts = [x for x in obj if isinstance(x, dict)]
        for d in dicts:
            if isinstance(d.get("scores"), dict):
                return d
        if dicts:
            return dicts[0]
    return {}


def _normalize_review(review: dict) -> dict:
    """强制总分落在 0-100 刻度：将各维度夹取到定义上限后重新求和，
    并计算 normalized_score（百分位）。

    新增字段：
    - normalized_score: 原始总分 / 维度满分之和 * 100（百分位）
    - score_schema: "v2"
    - max_total: 维度满分之和（由 DIM_MAX 常量计算）
    - raw_total: 原始总分（夹取前）
    """
    raw = review.get("scores") or {}
    # 计算维度满分之和（禁止魔数）
    max_total = sum(DIM_MAX.values())
    # 计算原始总分（夹取前，用于 backward compat）
    raw_total = sum(_to_float(raw.get(name)) for name in DIM_MAX)
    # 夹取各维度到 [0, DIM_MAX[name]]
    norm = {name: max(0.0, min(DIM_MAX[name], _to_float(raw.get(name))))
            for name in DIM_MAX}
    total = sum(norm.values())
    review["scores"] = norm
    review["total_score"] = int(round(total))
    # 向后兼容：旧记录缺 normalized_score 时现场重算
    if "normalized_score" not in review:
        review["normalized_score"] = round(total / max_total * 100, 1) if max_total else 0.0
        review.setdefault("_computed_normalized", True)
    review["score_schema"] = "v2"
    review["max_total"] = max_total
    review["raw_total"] = int(round(raw_total))

    # 规整每条 issue 的 scene_ids 归因（供定点重生直接使用）
    if isinstance(review.get("issues"), list):
        for it in review["issues"]:
            if isinstance(it, dict):
                it["scene_ids"] = _coerce_scene_ids(it.get("scene_ids"))

    # --- 每维 evidence ---
    dim_evidence = {}
    for dim in DIM_MAX:
        ev = None
        for it in review.get("issues", []):
            if not isinstance(it, dict):
                continue
            if it.get("dimension") == dim:
                d = it.get("description", "")
                ev = d[:30] if d and len(d) >= 10 else None
                if ev:
                    break
        if not ev:
            kw_map = {
                "plot_consistency": "章节目标",
                "character_consistency": "角色行为",
                "foreshadow_execution": "伏笔执行",
                "style_match": "文风符合",
                "pacing": "节奏控制",
                "innovation": "创新亮点",
                "hook_strength": "章末钩子",
                "reader_retention": "读者留存",
                "cliffhensity": "爽点密度",
            }
            ev = kw_map.get(dim, "证据占位")
        dim_evidence[dim] = ev
    
    review["dim_scores"] = {
        dim: {"score": round(norm.get(dim, 0)), "evidence": dim_evidence.get(dim, "证据占位")}
        for dim in DIM_MAX
    }
    logger.info(
        f"Review normalized: raw_total={review['raw_total']}, "
        f"max_total={max_total}, normalized_score={review['normalized_score']}"
    )
    return review


class ReviewerAgent:
    """章节审查 Agent。"""

    SYSTEM_PROMPT = """你是一位严格的网络小说审查编辑。你的任务是对生成的章节进行评分和反馈。

评分维度（各维度独立满分，合计120分）：
1. 剧情一致性（25分）：是否严格遵循任务卡目标和 plot_graph 节点
2. 人物一致性（20分）：角色行为、语气、境界是否与设定一致
3. 伏笔执行（20分）：clue_plan 中的写作指令是否体现
4. 文风符合度（15分）：是否符合 style_bible.md 与中文标点断句规范（每句不超过约40字、分句用逗号、对话用引号，无整段无标点流水句）
5. 节奏控制（10分）：场景切换、情绪曲线是否符合任务卡
6. 创新亮点（10分）：是否有生动细节或意外转折
7. 钩子强度（8分）：章末是否有有效钩子（悬念/伏笔/抉择），是否能留住读者
8. 读者留存（7分）：读后是否想继续阅读下一章，情感粘性
9. 爽点/悬念密度（5分）：单位章节爽点/悬念数量是否符合预期，密度是否合理

硬性约束（违反任意一条直接扣5-10分）：
- 禁止出现英文词汇（如 steady, crossed, arms 等）
- 禁止场景顺序与任务卡不符
- 禁止章节内容截断（必须完整呈现所有场景）
- 禁止核心伏笔完全缺失
- 禁止人物台词风格与设定不符
- 禁止整段无标点的流水句（成百字中间没有逗号/句号）或明显超过约40字的超长句：每出现一处计入 style_match 扣分（每次扣2-5分），并在 scene_ids 标注问题场景、suggested_fix 指明断句重写

CC round-18 额外硬约束（婴儿卷 ch1-9，违反直接扣 5 分并记录）：
- 配角陈老根的背景/回忆/往事中严禁具体化体制身份：不得出现「军中/从军/军营/军医/军中医/医官/教头/将军/千总/百户/朝廷命官/当差官府」等词；白名单「习武/江湖/走南闯北/佩刀」允许保留。
- ch1-5 陈老根严禁主动施展修为：不得出现「气感/内视/行气探查/气机探查/吐纳/调息」；ch6 起仅放开「陈老根夜间独自吐纳、陆烬旁观窥见」一种受控形态；「内视他人脏腑」整个婴儿卷（ch1-9）始终禁止。
- 不得建议任何超出当前章大纲阶段的人物背景具体化或后续章才解锁的能力（上述词条一律禁止出现在 suggested_fix 中）。

分级标准（基于归一化百分位）：
- normalized_score >= 88：PASS
- normalized_score >= 60：局部修复（指出问题段落）
- normalized_score < 60：全量回退到导演环节

扣分归因（必须填写，便于定点重生）：
- 每条 issue 必须给出 scene_ids：把扣分归因到具体的一个或多个场景编号（与任务卡 scene_blueprints 的 scene_num 一致）。
- 凡是“某场景与相邻场景复述同一事件、信息/氛围/意象被反复渲染造成拖沓注水”“场景内动作原地踏步”，计入 pacing 或 style_match，并在 scene_ids 标注需要删改/重写的场景。
- 时间先后矛盾（如同一天内昼夜反复）计入 plot_consistency，scene_ids 标注矛盾场景。
- 错别字、英文残留、人物名错误等，scene_ids 标注问题出现的场景。
- 只有确实无法定位到具体场景的全局性问题（如章末钩子、整体留存）才把 scene_ids 留为空数组 []。
- suggested_fix 必须是可执行的重写指令（例：“删除与场景2重复的雾中围观描写，只保留推进情节的新信息”），不要泛泛而谈。

重要说明：
- 每个维度按各自满分独立打分，不要自报 total_score
- 系统会自动计算归一化分数（raw_total / max_total * 100）
- 示例仅为格式参考，各维分数之和不必等于 total_score

输出必须是 JSON：
{
  "chapter_num": 1,
  "scores": {
    "plot_consistency": 20,
    "character_consistency": 15,
    "foreshadow_execution": 18,
    "style_match": 12,
    "pacing": 8,
    "innovation": 7,
    "hook_strength": 6,
    "reader_retention": 5,
    "cliffhensity": 4
  },
  "verdict": "fix",
  "issues": [
    {
      "dimension": "pacing",
      "severity": "high|medium|low",
      "scene_ids": [3],
      "description": "场景3与场景2复述同一事件，雾中围观/火把/红眼被反复渲染，情节原地踏步",
      "suggested_fix": "删除与场景2重复的围观与氛围描写，只保留报官争议等新信息，把场景3推进到陈老根出场"
    }
  ],
  "praise": "值得保留的优点",
  "fix_scope": "若 verdict=fix，说明需要重新生成的段落范围"
}"""

    def __init__(self, llm_client: Optional[LLMClient] = None, provider_config=None):
        self.llm = llm_client or LLMClient()
        self.provider_config = provider_config

    def _build_setting_block(self, chapter_num: int) -> str:
        """设定对照块：当前卷 forbidden 列表 + character_bible 全文 + style_bible 全文。

        解决"reviewer 对照一份从未见过的文件打分"问题（诊断结论：review prompt
        只出现文件名，从未注入真实设定内容）。合计约 4KB，相对单章 20-30 分钟
        运行与 7500 字正文采样，token 增量可忽略。

        author_intent 按"## 第N阶段：xxx（第A-B章）"标题切分，取当前章所属
        阶段的 forbidden 列表（`- "..."` 列表项）。定位失败时 forbidden 置空，
        不阻断评审。
        """
        from pathlib import Path
        import re
        _bible_dir = Path(__file__).parent.parent / "bible"
        try:
            _ai_text = (_bible_dir / "author_intent.md").read_text(encoding="utf-8")
            _cb_text = (_bible_dir / "character_bible.md").read_text(encoding="utf-8")
            _sb_text = (_bible_dir / "style_bible.md").read_text(encoding="utf-8")
        except Exception as _sb_e:
            logger.warning(f"setting block bible read failed: {_sb_e}")
            return ""
        # 当前卷 forbidden
        _cur_block = None
        for _blk in re.split(r"^## ", _ai_text, flags=re.M):
            _m = re.match(
                r"第[一二三四五六七八九十]+阶段[：:].*?（.*?第?(\d+)-(\d+)章?）",
                _blk)
            if not _m:
                continue
            _lo, _hi = int(_m.group(1)), int(_m.group(2))
            if _lo <= chapter_num <= _hi:
                _cur_block = _blk
                break
        _forbidden: list[str] = []
        if _cur_block:
            for _line in _cur_block.splitlines():
                _ls = _line.strip()
                if _ls.startswith("-"):
                    _forbidden.append(_ls.lstrip("- ").strip().strip('"'))
        _fb_line = "；".join(_forbidden) if _forbidden else "（本阶段无显式 forbidden 条款）"
        return (
            "## 设定对照块（必读，以下为当前卷真实设定内容，评分必须以对照块为准）\n"
            f"【当前卷禁止事项】{_fb_line}\n\n"
            f"【人物设定 character_bible 全文】\n{_cb_text}\n\n"
            f"【文风设定 style_bible 全文】\n{_sb_text}"
        )

    def review_chapter(
        self,
        chapter_num: int,
        task_card: dict,
        synopsis: dict,
        novel_text: str,
        world_state: dict,
        deterministic_signals: dict | None = None,
    ) -> dict:
        """审查单章。

        CC round-24 P0-1(a)：可选注入 deterministic_signals（零 LLM 门产物的数据表），
        作为 plot/pacing/innovation/retention/cliffhanger 打分的客观参照校准；不改变
        评分维度与 120 分体系。为 None 时行为与历史完全一致。
        """
        # P2-B2: 采样覆盖全文（头/中/尾各 2500 字，避免 6000 截断毁结构）
        def _sample(text: str, head: int = 2500, mid: int = 2500, tail: int = 2500) -> str:
            if len(text) <= head + mid + tail:
                return text
            h = text[:head]
            m_start = max(0, len(text)//2 - mid//2)
            m = text[m_start:m_start+mid]
            t = text[-tail:]
            return f"{h}\n\n...[中部省略 {len(text)-head-mid-tail} 字]...\n\n{m}\n\n...[中部省略]...\n\n{t}"

        sampled = _sample(novel_text)

        # CC round-24 P0-1(a)：确定性锚点数据表（不是自然语言描述）。
        anchor_block = ""
        if isinstance(deterministic_signals, dict) and deterministic_signals:
            anchor_block = (
                "\n\n## 本章确定性检测结果（客观参照，请据此校准主观评分）\n"
                + json.dumps(deterministic_signals, ensure_ascii=False, indent=2)
                + "\n请在 plot_consistency/pacing/innovation/reader_retention/cliffhensity "
                  "评分时以上表为客观参照：若 beat_coverage_ratio 与 new_state_coverage 均高，"
                  "pacing/plot 不应给出与之矛盾的低分；若某场景 atmosphere_ratio 超过 0.55 或"
                  "相邻场共享度偏高，可作为 pacing/innovation/reader_retention 的扣分依据。"
                  "character_consistency/style_match/foreshadow_execution/hook_strength 仍按正文独立判断。"
            )
            # 设定贯通·揭示尺度：前世描写 soft_warn（ch48 口径，不做 hard block）
            _plr_sig = (deterministic_signals.get("past_life_reveal") or {})
            if _plr_sig.get("reveal_scale_high"):
                anchor_block += (
                    "\n注意：past_life_reveal 判定本章前世描写揭示尺度偏高"
                    f"（150字窗口内出现 {_plr_sig.get('max_window_hits')} 个前世具象细节词）。"
                    "这【不构成阻断】——前世/重生是角色既定身份（character_bible C001），"
                    "但请在 plot_consistency 与揭示节奏维度给出提示，并在 review issues 中"
                    "以 severity=low/medium、category=reveal_scale 记一条 soft note 供人工审阅。"
                )

        setting_block = self._build_setting_block(chapter_num)

        prompt = f"""请审查第 {chapter_num} 章。

## 任务卡
{json.dumps(task_card, ensure_ascii=False, indent=2)}

## 缩写
{synopsis.get('synopsis', '')}

## 正文（采样覆盖全文 头/中/尾）
{sampled}

## 世界状态
{json.dumps(world_state, ensure_ascii=False, indent=2)[:2000]}

{setting_block}

## 审查要求
1. 检查正文是否完成了任务卡中所有 scene_blueprints 的 goal
2. 检查伏笔动作是否执行
3. 检查是否有 forbidden 项被违反（对照上方"设定对照块·当前卷禁止事项"逐条核对，
   违反即标记为"设定违规"，按 severity/category 机制单独归类，不放宽为普通文风问题）
4. 检查人物行为是否符合 character_bible（对照上方人物设定全文）
5. 检查文风是否符合 style_bible（对照上方文风设定全文）{anchor_block}"""

        try:
            review = _coerce_review_object(call_llm(
                prompt=prompt,
                system_prompt=self.SYSTEM_PROMPT,
                client=self.llm,
                output_json=True,
                provider_config=self.provider_config,
            ))
            review = _normalize_review(review)
            logger.info(f"Review completed for chapter {chapter_num}: score={review.get('total_score')}")
            return review
        except Exception as e:
            logger.error(f"Review failed for chapter {chapter_num}: {e}")
            raise

    def grade_review(self, review: dict) -> str:
        import json
        from pathlib import Path
        from novel_engine.core.quality_policy import load_quality_policy
        try:
            cfg = json.loads((Path(__file__).parent.parent / "config" /
                              "runtime_config.json").read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        # 单一策略源：分级线读 quality_policy
        policy = load_quality_policy(Path(__file__).parent.parent)
        line = int(policy.get("publication_line",
                              cfg.get("quality", {}).get("publication_line", 88)))
        fix_t = int(policy.get("fix_threshold",
                               cfg.get("quality", {}).get("fix_threshold", 60)))
        # v2 优先使用 normalized_score（百分位），旧记录回退到 total_score
        score = review.get("normalized_score") if review.get("normalized_score") is not None else review.get("total_score", 0)
        if score >= line:
            return "pass"
        elif score >= fix_t:
            return "fix"
        return "fail"

    @staticmethod
    def _chinese_num_to_int(cn: str) -> int:
        """将中文数字（一~十、十一~十九、二十）转为整数。"""
        cn_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                  "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        if cn == "十":
            return 10
        if "十" in cn:
            head, _, tail = cn.partition("十")
            tens = cn_map.get(head, 1) * 10 if head else 10
            ones = cn_map.get(tail, 0)
            return tens + ones
        return cn_map.get(cn, 0)

    def _extract_segment(self, text: str, scope: str) -> str:
        """根据 fix_scope 从原文中提取问题段落，兼容多种场景标记格式。"""
        if not scope or not text:
            return text[:2000]

        scope_lower = scope.lower()
        # 尝试从 scope 中解析出场景编号，如 "场景2"、"scene 3"、"第2场景"
        target_nums = set()
        for m in re.finditer(r"(?:场景|scene)\s*(\d+)", scope_lower):
            target_nums.add(int(m.group(1)))

        # Format 1: 【场景N：地点】
        scene_pattern = r'【场景(\d+)：[^】]*】'
        scenes = list(re.finditer(scene_pattern, text))

        if scenes:
            target_scene_idx = None
            # 优先按场景编号匹配
            if target_nums:
                for i, scene_match in enumerate(scenes):
                    if int(scene_match.group(1)) in target_nums:
                        target_scene_idx = i
                        break
            # 其次按关键词匹配场景标题
            if target_scene_idx is None:
                for i, scene_match in enumerate(scenes):
                    if any(kw in scene_match.group().lower() for kw in scope_lower.split()):
                        target_scene_idx = i
                        break

            if target_scene_idx is not None:
                start = scenes[target_scene_idx].start()
                end = scenes[min(target_scene_idx + 1, len(scenes))].start() if target_scene_idx + 1 < len(scenes) else len(text)
                return text[start:end][:3000]

        # Format 2: ### 其一/其二...
        section_pattern = r'###\s*[一二三四五六七八九十]+'
        if re.search(section_pattern, text):
            sections = re.split(r'(?=###\s*[一二三四五六七八九十]+)', text)
            target_section_idx = None
            # 按场景编号映射到中文序号段
            if target_nums:
                for i, section in enumerate(sections):
                    match = re.match(r'###\s*([一二三四五六七八九十]+)', section.strip())
                    if match:
                        num = self._chinese_num_to_int(match.group(1))
                        if num in target_nums:
                            target_section_idx = i
                            break
            # 其次按关键词匹配段内容
            if target_section_idx is None:
                for i, section in enumerate(sections):
                    if any(kw in section.lower() for kw in scope_lower.split() if len(kw) > 2):
                        target_section_idx = i
                        break
            if target_section_idx is not None:
                return sections[target_section_idx][:3000]

        # Format 3: 按 ※ 分隔符分割
        parts = re.split(r'\n※\n', text)
        if len(parts) > 1:
            scope_lower = scope.lower()
            # 优先按场景编号匹配
            if target_nums:
                for i, part in enumerate(parts):
                    m = re.search(r'【场景(\d+)：', part)
                    if m and int(m.group(1)) in target_nums:
                        return part[:3000]
            for i, part in enumerate(parts):
                if any(kw in part.lower() for kw in scope_lower.split() if len(kw) > 2):
                    return part[:3000]
            # 如果找不到关键词，返回第一部分
            return parts[0][:3000]

        # Fallback: 按关键词定位
        scope_lower = scope.lower()
        for kw in scope_lower.split():
            if len(kw) > 2:
                idx = text.lower().find(kw)
                if idx >= 0:
                    start = max(0, idx - 500)
                    end = min(len(text), idx + 2000)
                    return text[start:end]

        return text[:2000]

    def generate_fix_prompt(self, review: dict, original_text: str) -> str:
        """生成局部修复 prompt（优化版：只发送问题段落，减少 token）。"""
        issues = review.get("issues", [])
        problem_text = self._extract_segment(original_text, review.get("fix_scope", ""))

        if not problem_text:
            problem_text = original_text[:3000]

        issue_desc = "\n".join([
            f"- [{issue.get('severity', 'medium')}] {issue.get('dimension', '')}: {issue.get('description', '')}"
            for issue in issues
        ])

        prompt = f"""请修复以下章节中的问题段落。

## 问题描述
{issue_desc}

## 问题段落
{problem_text}

## 原文全文（前3000字）
{original_text[:3000]}

## 修复要求
1. 仅修复问题段落，保持其余内容不变
2. 保持文风、人物名字和上下文剧情连贯
3. 修复后输出完整章节文本
4. 不添加任何解释或注释"""

        return prompt
