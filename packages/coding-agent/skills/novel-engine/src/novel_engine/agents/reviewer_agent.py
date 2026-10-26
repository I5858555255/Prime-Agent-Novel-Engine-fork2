"""
审查评分 Agent：对生成的章节进行分级审查。
120分制：剧情一致性(25) / 人物一致性(20) / 伏笔执行(20) / 文风符合度(15) / 节奏控制(10) / 创新亮点(10) / 钩子强度(8) / 读者留存(7) / 爽点/悬念密度(5)
总计: 25+20+20+15+10+10+8+7+5 = 120 分，系统自动归一化到百分位（normalized_score）
"""

import json
import logging
import re
import math
import hashlib
import uuid
from pathlib import Path
from typing import Optional

from novel_engine.core.llm_client import LLMClient, call_llm
from novel_engine.core.execution import current_execution
from novel_engine.core.publication_transaction import atomic_write
from novel_engine.core.quality_policy import load_quality_policy
from novel_engine.quality.generation_constraints import generation_constraints
from novel_engine.pipeline.chapter_journal import load_authoritative_scenes
from novel_engine.quality.punctuation_health import detect_long_sentences, check_text_punctuation, LONG_SENTENCE_HARD

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


def build_scene_boundaries(text, scenes):
    """Locate unchanged scene words in the actual review text; never guess by chapter length."""
    offsets = [i for i, char in enumerate(text) if char.isalnum()]
    content = "".join(text[i] for i in offsets)
    result = []
    previous_end = 0
    for scene in scenes:
        words = "".join(c for c in scene.get("scene_text", "") if c.isalnum())
        if len(words) < 8 or content.count(words) != 1:
            return []
        start = content.index(words)
        end = start + len(words)
        if start < previous_end:
            return []
        lo, hi = offsets[start], offsets[end - 1] + 1
        result.append({"scene_id": scene["scene_id"], "start": lo, "end": hi,
                       "opening": text[lo:min(hi, lo + 60)], "ending": text[max(lo, hi - 60):hi]})
        previous_end = end
    return result


def review_context_fingerprint(root, task_card):
    bible = Path(root) / "bible"
    files = {name: (bible / name).read_text(encoding="utf-8") if (bible / name).exists() else None
             for name in ("author_intent.md", "character_bible.md", "style_bible.md")}
    payload = {"system": ReviewerAgent.SYSTEM_PROMPT, "bible": files,
               "constraints": generation_constraints(root, task_card)}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class ReviewResponseError(ValueError):
    """An incomplete scoring response must not become a low literary score."""



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
    raw = review.get("scores")
    if not isinstance(raw, dict):
        raise ReviewResponseError("scores must contain all nine dimensions")
    for name, maximum in DIM_MAX.items():
        if name not in raw or isinstance(raw[name], bool):
            raise ReviewResponseError(f"missing or invalid dimension: {name}")
        try:
            value = float(raw[name])
        except (TypeError, ValueError):
            raise ReviewResponseError(f"invalid numeric dimension: {name}") from None
        if not math.isfinite(value) or not 0 <= value <= maximum:
            raise ReviewResponseError(f"dimension outside finite range: {name}")
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
    review["normalized_score"] = round(total / max_total * 100, 1) if max_total else 0.0
    review["_computed_normalized"] = True
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
- 九个维度必须齐全，按各自满分独立给出有限数值；不得自报总分或归一化分数。
- 每项扣分必须有原文依据，issue 给出 evidence 原文短引文和依据的设定/任务条款。
- 同一事实不得先扣维度分再从总分重复扣除；每维最低0分。
- 设定中的长期履历与未来解锁内容不代表本章已经发生；以当前章边界、状态和允许例外为准。
- 标点事实以实测结果和逐字证据核对；短句分段本身不等于诗歌体或残句。
- 只返回实际评审对象，不要返回下面的Schema。""" + "\n" + json.dumps({
        "type": "object", "required": ["chapter_num", "scores", "issues"],
        "properties": {
            "chapter_num": {"type": "integer"},
            "scores": {"type": "object", "required": list(DIM_MAX),
                       "properties": {name: {"type": "number", "minimum": 0, "maximum": maximum}
                                      for name, maximum in DIM_MAX.items()}},
            "issues": {"type": "array", "items": {"type": "object", "properties": {
                "dimension": {"enum": list(DIM_MAX)}, "severity": {"enum": ["high", "medium", "low"]},
                "scene_ids": {"type": "array", "items": {"type": "integer"}},
                "description": {"type": "string"}, "evidence": {"type": "string"},
                "suggested_fix": {"type": "string"}}}},
            "praise": {"type": "string"}, "fix_scope": {"type": "string"}}}, ensure_ascii=False)

    def __init__(self, llm_client: Optional[LLMClient] = None, provider_config=None, project_root=None):
        self.llm = llm_client or LLMClient()
        self.provider_config = provider_config
        self.root = Path(project_root) if project_root is not None else Path(__file__).parent.parent

    def _build_setting_block(self, chapter_num: int) -> str:
        """设定对照块：当前卷 forbidden 列表 + character_bible 全文 + style_bible 全文。

        解决"reviewer 对照一份从未见过的文件打分"问题（诊断结论：review prompt
        只出现文件名，从未注入真实设定内容）。合计约 4KB，相对单章 20-30 分钟
        运行与 7500 字正文采样，token 增量可忽略。

        author_intent 按"## 第N阶段：xxx（第A-B章）"标题切分，取当前章所属
        阶段的 forbidden 列表（`- "..."` 列表项）。定位失败时 forbidden 置空，
        不阻断评审。
        """
        _bible_dir = self.root / "bible"
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
            collecting = False
            for _line in _cur_block.splitlines():
                _ls = _line.strip()
                if _ls == "forbidden:":
                    collecting = True
                    continue
                if collecting:
                    if _ls.startswith("- "):
                        _forbidden.append(_ls[2:].strip().strip('"'))
                    elif _ls and not _ls.startswith("#"):
                        collecting = False
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
        long_sentences = detect_long_sentences(novel_text)
        unpunctuated = check_text_punctuation(novel_text)
        punctuation_evidence = {
            "long_sentence_threshold_cn_chars": LONG_SENTENCE_HARD,
            "long_sentence_count": len(long_sentences),
            "unhealthy_paragraph_count": len(unpunctuated),
            "long_sentence_examples": long_sentences[:3],
            "unhealthy_paragraph_examples": unpunctuated[:2],
        }

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
        boundaries = build_scene_boundaries(novel_text, load_authoritative_scenes(self.root, chapter_num))

        prompt = f"""请审查第 {chapter_num} 章。

## 任务卡
{json.dumps(task_card, ensure_ascii=False, indent=2)}

## 已核验的场景边界（字符偏移从0开始；opening/ending用于定位，不能按全文长度猜场景号）
{json.dumps(boundaries, ensure_ascii=False)}
未能精确匹配时边界为空，此时无法确证场景编号的问题使用 scene_ids=[]，不得捏造场景号。

## 缩写
{synopsis.get('synopsis', '')}

## 正文（完整章节，未抽样删节）
{novel_text}

## 标点实测证据（旁白以句末为界、引号内对白豁免，与规则门口径一致）
{json.dumps(punctuation_evidence, ensure_ascii=False)}
请逐字核对原文再提出标点问题，不得把多个已有句号的句子拼成一个长句，或将有逗号/句号的段落称为整段无标点。
实测数据仅校准标点事实，不代表文风合格；用词、叙事语气与节奏仍独立评审。

## 世界状态
{json.dumps(world_state, ensure_ascii=False, indent=2)[:2000]}

{setting_block}

{generation_constraints(self.root, dict(task_card, chapter_num=chapter_num))}

## 当前阶段人物判断
以任务卡的 protagonist_agency_level 和本场限制作为当前能力状态，人物卡中的后期经历与成年性格不得视为当前已经实现。
婴儿/失能状态不应被要求展示成人算计或连贯论证；仍须检查超龄认知、无效重复及不符合本阶段的行为，并给出原文依据。

## 审查要求
1. 检查正文是否完成了任务卡中所有 scene_blueprints 的 goal
2. 检查伏笔动作是否执行
3. 检查是否有 forbidden 项被违反（对照上方"设定对照块·当前卷禁止事项"逐条核对，
   违反即标记为"设定违规"，按 severity/category 机制单独归类，不放宽为普通文风问题）
4. 检查人物行为是否符合 character_bible（对照上方人物设定全文）
5. 检查文风是否符合 style_bible（对照上方文风设定全文）{anchor_block}"""

        for attempt in range(2):
            if attempt:
                execution = current_execution(self.llm)
                if execution is not None:
                    execution.budget.review(vote=True)
            review = _coerce_review_object(call_llm(
                prompt=prompt, system_prompt=self.SYSTEM_PROMPT, client=self.llm,
                output_json=True, provider_config=self.provider_config, temperature=0))
            audit = {"chapter": chapter_num, "format_attempt": attempt,
                     "input_hash": hashlib.sha256((self.SYSTEM_PROMPT + prompt).encode()).hexdigest(),
                     "text_hash": hashlib.sha256(novel_text.encode()).hexdigest(),
                     "scene_boundaries": boundaries,
                     "raw_scores": dict(review["scores"]) if isinstance(review.get("scores"), dict) else review.get("scores"), "issues": review.get("issues", [])}
            try:
                review = _normalize_review(review)
            except ReviewResponseError as exc:
                audit["technical_error"] = str(exc)
                self._save_review_audit(audit)
                if attempt:
                    raise
                prompt += ("\n上次响应格式无效：" + str(exc)
                           + "。请补齐全部九维，数值须在各维有限范围内，不改变审查尺度，不自报总分。")
                continue
            audit["normalized_score"] = review["normalized_score"]
            self._save_review_audit(audit)
            logger.info("Review completed for chapter %s: score=%s", chapter_num, review["total_score"])
            return review

    def _save_review_audit(self, audit):
        directory = self.root / "audit" / "reviews"
        try:
            atomic_write(directory / f"chapter_{audit['chapter']}_{uuid.uuid4().hex}.json",
                         json.dumps(audit, ensure_ascii=False, indent=2))
        except OSError as exc:
            logger.warning("Review audit could not be saved: %s", type(exc).__name__)

    def grade_review(self, review: dict) -> str:
        policy = load_quality_policy(self.root)
        score = review.get("normalized_score", review.get("total_score", 0))
        if score >= policy["publication_line"]:
            return "pass"
        if score >= policy["fix_threshold"]:
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
