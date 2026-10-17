# -*- coding: utf-8 -*-
"""CC round-25 P0-2：整章一次性、聚焦 hook/style/innovation 三维的定向文学性重写。

背景：plot/character/foreshadow/pacing/retention 等维度可做"场景级定点归因"，继续由
E-loop 的逐场定点重生处理；但 hook（场末钩子张力）/ style（文风与整章基调一致）/
innovation（去套路化表达）是横跨场景的文字层面问题，逐场补事件既不对症还会扰动情节。
本模块用**一次**整章 LLM 调用，只针对 reviewer 点名的这三维具体 issue 输出少量"锚点替换"
编辑，确定性地把替换缝进对应场景，绝不整章重排、绝不改情节走向。

铁律（写进 prompt）：
- 只改文字表达：场末钩子张力 / 文风统一 / 替换套路化措辞；
- 严禁改情节走向、增删已定事件、改变场景推进契约、改变信息揭示与时间线；
- 只输出需改段落并标注场景位置（锚点必须是原文中唯一的连续字串）。

调用方（orchestrator）在缝入后必须重跑全部确定性门并重新评审一次，门不过则整体弃用。
本模块自身零 LLM（除显式 build→parse 外），parse/apply 纯函数、离线可测。
"""
from __future__ import annotations

import difflib
import json
import re

LITERARY_DIMS = ("hook", "style", "innovation")
_DIM_ALIASES = {
    "hook": "hook", "hook_strength": "hook", "cliff_hook": "hook",
    # cliffhensity（章末悬崖感）本质也是"场末收束张力"，与 hook 同源、同由 replace_tail 处理；
    # 不应再走 CC23 的"补事件"（r30 实测对'结尾平淡'补事件反而降分）。
    "cliffhensity": "hook", "cliffhanger": "hook",
    "style": "style", "style_match": "style", "style_consistency": "style", "文风": "style",
    "innovation": "innovation", "novelty": "innovation",
}
_DIM_LABEL = {
    "hook": "场末钩子张力（结尾悬念/张力不足、收束平淡）",
    "style": "文风与整章基调一致性（措辞风格不统一/不贴冷峻基调）",
    "innovation": "去套路化（陈词滥调、模板化比喻、似曾相识的表达）",
}

SYSTEM_PROMPT = (
    "你是资深中文网文文学编辑，只做文字层面的精修，不碰情节。你针对审稿人点名的"
    "场末钩子、文风一致性、套路化表达三类问题，输出最小化的锚点替换编辑。"
    "严禁改变任何情节走向、事件、人物决定、信息揭示顺序或时间线；只改写措辞与收束方式。"
)

_LATIN_RE = re.compile(r"[A-Za-z]")
_MARKER_RE = re.compile(r"[【】※]|```")
# 替换稿允许的句末收束（含引号闭合的对白/心理）
_END_OK = "。！？…」”’"


def _canon_dim(d: str) -> str:
    return _DIM_ALIASES.get(str(d or "").strip().lower(), "")


def select_literary_issues(review: dict) -> list[dict]:
    """从 reviewer 结果中抽出 hook/style/innovation 三维、且带场景归因的具体点名 issue。"""
    out: list[dict] = []
    for iss in (review.get("issues") or []):
        if not isinstance(iss, dict):
            continue
        dim = _canon_dim(iss.get("dimension", ""))
        if dim not in LITERARY_DIMS:
            continue
        desc = str(iss.get("description", "") or "").strip()
        fix = str(iss.get("suggested_fix", "") or "").strip()
        if not desc and not fix:
            continue
        sids = []
        for x in (iss.get("scene_ids") or []):
            try:
                n = int(x)
            except (TypeError, ValueError):
                continue
            if n >= 1 and n not in sids:
                sids.append(n)
        out.append({"dimension": dim, "scene_ids": sids,
                    "description": desc[:200], "suggested_fix": fix[:200]})
    return out


def literary_weak(review: dict, raw_ratio: float = 0.85) -> bool:
    """三维是否需要文学性重写：raw 分低于该维上限的 raw_ratio，或有点名 issue。

    reviewer scores 键可能是全名（hook_strength/style_match）或 review_hybrid 归一后的
    规范名（hook/style），两套都兼容。
    """
    if select_literary_issues(review):
        return True
    canon_max = {"hook": 8, "style": 15, "innovation": 10}
    score_alias = {"hook": ("hook", "hook_strength"),
                   "style": ("style", "style_match"),
                   "innovation": ("innovation",)}
    scores = review.get("scores") or {}
    for dim in LITERARY_DIMS:
        val = None
        for k in score_alias[dim]:
            if k in scores:
                try:
                    val = float(scores[k])
                    break
                except (TypeError, ValueError):
                    continue
        if val is not None and val < canon_max[dim] * raw_ratio:
            return True
    return False


def strip_literary_from_review(review: dict) -> dict:
    """返回去掉 hook/style/innovation issue 的浅拷贝，供 E-loop 只处理结构性维度。"""
    if not isinstance(review, dict):
        return review
    kept = [iss for iss in (review.get("issues") or [])
            if _canon_dim((iss or {}).get("dimension", "")) not in LITERARY_DIMS]
    cp = dict(review)
    cp["issues"] = kept
    return cp


def build_literary_prompt(scene_texts: list[tuple[int, str]], issues: list[dict],
                          chapter_num: int) -> str:
    """组装一次性整章文学性重写 prompt（要求严格 JSON 锚点替换）。"""
    scenes_block = "\n\n".join(
        f"===== 场景 {sid} =====\n{txt}" for sid, txt in scene_texts if (txt or "").strip())
    issue_lines = []
    for it in issues:
        sids = ("、".join(f"场景{n}" for n in it["scene_ids"]) if it["scene_ids"] else "全章")
        issue_lines.append(
            f"- [{_DIM_LABEL.get(it['dimension'], it['dimension'])}｜{sids}] {it['description']}"
            + (f"；改进方向：{it['suggested_fix']}" if it["suggested_fix"] else ""))
    # 无场景归因时，允许依据低分维度在"场末收束/明显套路句"处自寻锚点，但每维至多 2 处。
    issues_block = "\n".join(issue_lines) or (
        "- 审稿显示本章场末钩子偏平、部分措辞套路化、文风不够统一；请只在最明显的场末收束句"
        "与陈词套话处做最小替换，全场合计不超过 4 处。")
    return f"""请对第 {chapter_num} 章做一次【只改文字、不改情节】的精修，仅处理三类问题：
1. 场末钩子张力（让场景最后一两句更有悬念/张力，但不新增事件）；
2. 文风与全章冷峻基调统一（替换出戏/风格不一的措辞）；
3. 去套路化（替换陈词滥调、模板化比喻、似曾相识的句式）。

审稿人点名的具体问题（必须逐条对症，不许自由发挥、不许扩大改动）：
{issues_block}

待修正文（按场景区分，锚点必须从这些原文里逐字摘取）：
{scenes_block}

严格要求：
- 对症逐条改：凡审稿点名、或确实平淡的场末收束句与明显套话都给编辑，全章通常 2-6 处；只改最有把握的，不凑数；
- 严禁改变情节走向、增删事件、改人物决定/关系、改信息揭示与时间线、改场景边界；
- replace_tail（场末钩子张力）【不需要 anchor】：系统会自动定位并替换该场景最后一整句；你只给 scene_id 和 replacement——一句更有悬念/张力、但情节不增不减的新场末句；
- replace_span（句中去套路/统一文风）必须给 anchor 与 anchor_end，二者都要从上面原文中【逐字、连续、唯一】摘取（含原标点），系统替换二者之间（含）；
- replacement 必须是可直接成稿的中文，长度与被替换句/段相近（±60%），不得出现英文、【】、※、代码围栏或任何说明。

只输出严格 JSON（不要解释、不要 markdown）：
{{"edits":[
  {{"scene_id":3,"op":"replace_tail","replacement":"新的场末成稿（更有钩子张力，情节不变；系统自动替换最后一整句，无需 anchor）"}},
  {{"scene_id":2,"op":"replace_span","anchor":"套路句开头连续8-16字（逐字摘自原文）","anchor_end":"套路句结尾连续4-10字（逐字摘自原文）","replacement":"去套路后的成稿原句"}}
]}}"""


def _parse_json(raw) -> dict | list | None:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, str):
        return None
    t = raw.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    try:
        from json_repair import repair_json
        return json.loads(repair_json(t))
    except Exception:
        return None


def parse_literary_edits(raw) -> list[dict]:
    """容错解析 LLM 输出为 edit 列表（非法项丢弃）。"""
    data = _parse_json(raw)
    if data is None:
        return []
    if isinstance(data, dict):
        edits = data.get("edits") or data.get("replacements") or []
    else:
        edits = data
    if not isinstance(edits, list):
        return []
    out: list[dict] = []
    for e in edits:
        if not isinstance(e, dict):
            continue
        try:
            sid = int(e.get("scene_id"))
        except (TypeError, ValueError):
            continue
        op = str(e.get("op", "replace_span")).strip()
        if op not in ("replace_tail", "replace_span"):
            op = "replace_span"
        anchor = str(e.get("anchor", "") or "").strip()
        anchor_end = str(e.get("anchor_end", "") or "").strip()
        repl = str(e.get("replacement", "") or "").strip()
        # replace_tail 免锚点（系统自动定位末句）；replace_span 必须有 anchor + replacement。
        # anchor_end 可选（CC26 硬化：flash 常不给/给错结尾锚点，缺省时仅替换 anchor 短语本身）。
        if sid < 1 or not repl:
            continue
        if op == "replace_span" and not anchor:
            continue
        out.append({"scene_id": sid, "op": op, "anchor": anchor,
                    "anchor_end": anchor_end, "replacement": repl})
    return out[:8]


def _replacement_ok(repl: str) -> bool:
    if len(repl) < 8:
        return False
    if _LATIN_RE.search(repl) or _MARKER_RE.search(repl):
        return False
    if repl[-1] not in _END_OK:
        return False
    return True


def _last_sentence_start(text: str) -> int:
    """确定性定位"最后一整句"的起点（replace_tail 免锚点时使用）。

    取倒数第二个句末标点之后为最后一句起点；末句过短则再多并一句；跳过引号闭合。
    全文只有一句时返回 0（由后续长度护栏决定是否采用）。
    """
    poss = [m.start() for m in re.finditer(r"[。！？…!?；]", text or "")]
    if not poss:
        return 0

    def _clean(st: int) -> int:
        while st < len(text) and text[st] in "」”’\"\n　 \t":
            st += 1
        return st

    if len(poss) >= 2:
        st = _clean(poss[-2] + 1)
    else:
        st = 0
    if len(text) - st < 8 and len(poss) >= 3:
        st = _clean(poss[-3] + 1)
    return st


def _locate_unique_span(hay: str, needle: str) -> tuple[int, int] | None:
    """在原文中唯一定位 needle：先精确匹配，失败则忽略空白差异（映射回原索引）。"""
    if not needle:
        return None
    if hay.count(needle) == 1:
        i = hay.index(needle)
        return i, i + len(needle)
    idxs, norm = [], []
    for i, ch in enumerate(hay):
        if not ch.isspace():
            norm.append(ch)
            idxs.append(i)
    nn = re.sub(r"\s+", "", needle)
    joined = "".join(norm)
    if nn and joined.count(nn) == 1:
        j = joined.index(nn)
        return idxs[j], idxs[j + len(nn) - 1] + 1
    return None


def _lcs_unique(hay: str, needle: str, min_len: int = 6) -> tuple[int, int] | None:
    """最后兜底：needle 与原文的【最长公共连续子串】，且该子串在原文中唯一。

    用于 flash 锚点夹带少量改写/标点漂移但核心短语逐字保留的情况；若模型是整句
    意译（无足够长的逐字公共段），返回 None 安全跳过，绝不靠相似度替换到错误位置。
    """
    if not hay or not needle:
        return None
    blocks = difflib.SequenceMatcher(None, hay, needle, autojunk=False).get_matching_blocks()
    cand = [b for b in blocks if b.size >= min_len]
    cand.sort(key=lambda b: b.size, reverse=True)
    for b in cand:
        frag = hay[b.a:b.a + b.size]
        if frag and hay.count(frag) == 1:
            return b.a, b.a + b.size
    return None


def _locate_span_lenient(hay: str, needle: str) -> tuple[int, int] | None:
    """精确/忽略空白 → 最长唯一公共子串，逐级放宽。"""
    return _locate_unique_span(hay, needle) or _lcs_unique(hay, needle)


# ============================================================================
# CC28 #2：CC25 文学 pass 的编辑预算 / 去 no-op / 相邻合并 / 语义护栏（零 LLM）
# r40 ch0 出现“applied 8 edits”且多为 55->57、32->32 的几十字微替换，把文本
# 越改越碎并伴随分数反降。这里在确定性缝合层统一收口。
# ============================================================================
MAX_EFFECTIVE_EDITS = 3        # 单轮有效编辑数上限
ADJACENT_MERGE_CHARS = 30      # 同场相邻 span 间隔小于该值视为碎改，只保留一条
MAX_ABS_TOTAL_CHANGE = 300     # 整章净改动绝对字数上限（与 12% 比例同时生效）
MIN_EFFECTIVE_EDIT_DIST = 2    # 归一化后编辑距离小于该值视为 no-op

_EDIT_STRIP_CHARS = " 　\t\r\n，。！？!?…、；：“”‘’\"'（）()《》〈〉,.—~·;:-_"


def _norm_edit_text(s: str) -> str:
    """归一化编辑文本：去标点/空白，用于判断两处是否实质相同。"""
    return "".join(ch for ch in (s or "") if ch not in _EDIT_STRIP_CHARS)


def _edit_distance(a: str, b: str) -> int:
    """字符级 Levenshtein（编辑文本通常很短，DP 成本可忽略）。"""
    if a == b:
        return 0
    m, n = len(a), len(b)
    if m == 0:
        return n
    if n == 0:
        return m
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        ai = a[i - 1]
        for j in range(1, n + 1):
            cost = 0 if ai == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[n]


def is_noop_edit(removed: str, replacement: str,
                 min_dist: int = MIN_EFFECTIVE_EDIT_DIST) -> bool:
    """True 表示该编辑没有实质改动（如 32->32、仅改一两个标点/近义字）。"""
    nr, nq = _norm_edit_text(removed), _norm_edit_text(replacement)
    if nr == nq:
        return True
    if abs(len(nr) - len(nq)) < min_dist and _edit_distance(nr, nq) < min_dist:
        return True
    return False


def violates_protected_terms(removed: str, replacement: str,
                             protected_terms) -> bool:
    """检测语义漂移：受保护词（canonical 人名/关键锚点）在替换片段中被删或新增。"""
    if not protected_terms:
        return False
    for term in protected_terms:
        term = (term or "").strip()
        if term and ((term in (removed or "")) != (term in (replacement or ""))):
            return True
    return False


def normalize_literary_edits(edits: list[dict],
                             max_edits: int = MAX_EFFECTIVE_EDITS,
                             adjacent_merge_chars: int = ADJACENT_MERGE_CHARS) -> tuple[list[dict], list[str]]:
    """按编辑预算与“相邻碎改”规则裁剪 LLM 给出的原始 edit 列表（与文本无关的预筛）。

    列表内相邻（不区分场）的微改只保留首条；超过单轮预算的丢弃。返回 (保留edits, 说明)。
    真正基于定位坐标的同场相邻判定在 apply 阶段再做一次（那里有准确字符位置）。
    """
    kept: list[dict] = []
    notes: list[str] = []
    for e in edits or []:
        if len(kept) >= max_edits:
            notes.append("literary edit budget exhausted; drop remaining edits")
            break
        kept.append(e)
    return kept, notes


def apply_literary_edits(texts: dict[int, str], edits: list[dict],
                         scene_change_cap: float = 0.25,
                         total_change_cap: float = 0.12,
                         max_edits: int = MAX_EFFECTIVE_EDITS,
                         adjacent_merge_chars: int = ADJACENT_MERGE_CHARS,
                         max_abs_total_change: int = MAX_ABS_TOTAL_CHANGE,
                         protected_terms=None) -> tuple[dict[int, str], int, list[str]]:
    """把锚点替换确定性地缝进各场景（不改任何其它字）。

    返回 (新文本dict, 成功应用条数, 说明)。任一条定位失败/越界/替换不合法/no-op/
    触碰保护词/相邻碎改/超编辑预算都跳过该条；总改动超 total_change_cap 或绝对字数
    上限、或 0 条成功时整体回滚原文本、applied=0。
    """
    cur: dict[int, str] = {k: v for k, v in texts.items()}
    total_before = sum(len(v) for v in cur.values()) or 1
    applied = 0
    notes: list[str] = []
    last_end_by_sid: dict[int, int] = {}  # 每场上一条已接受 span 的终点（当前文本坐标）
    for e in edits:
        if applied >= max_edits:
            notes.append("literary effective-edit budget reached "
                         f"({max_edits}); skip remaining")
            break
        sid = e["scene_id"]
        text = cur.get(sid)
        if text is None:
            notes.append(f"scene {sid} missing; skip")
            continue
        anchor, repl = e["anchor"], e["replacement"]
        if e["op"] == "replace_tail":
            # 有唯一锚点用锚点；否则确定性替换最后一整句（不依赖模型逐字摘锚点）。
            if anchor and text.count(anchor) == 1:
                start = text.index(anchor)
            else:
                start = _last_sentence_start(text)
            removed = text[start:]
            new = text[:start] + repl
            end_pos = len(text)
        else:
            loc_s = _locate_span_lenient(text, anchor)
            if loc_s is None:
                notes.append(f"scene {sid} span anchor not uniquely located; skip")
                continue
            anchor_end = (e.get("anchor_end") or "").strip()
            loc_e = _locate_span_lenient(text, anchor_end) if anchor_end else None
            start, end_pos = loc_s[0], loc_s[1]
            if loc_e is not None and loc_e[1] > start:
                end_pos = loc_e[1]
            if end_pos <= start:
                notes.append(f"scene {sid} span order invalid; skip")
                continue
            # 同场相邻碎改：与上一条已接受 span 间隔过小则跳过（合并为保留首条）
            prev_end = last_end_by_sid.get(sid)
            if prev_end is not None and 0 <= start - prev_end < adjacent_merge_chars:
                notes.append(f"scene {sid} adjacent span within {adjacent_merge_chars} chars; "
                             "merge/skip to avoid micro-churn")
                continue
            removed = text[start:end_pos]
            new = text[:start] + repl + text[end_pos:]
        if len(removed) < 4 or not _replacement_ok(repl):
            notes.append(f"scene {sid} replacement invalid; skip")
            continue
        # CC28：去 no-op（归一化后实质相同 / 编辑距离过小）
        if is_noop_edit(removed, repl):
            notes.append(f"scene {sid} no-op edit "
                         f"({len(removed)}->{len(repl)}); skip")
            continue
        # CC28：语义护栏，禁止增删 canonical 人名/关键锚点
        if violates_protected_terms(removed, repl, protected_terms):
            notes.append(f"scene {sid} edit touches protected term; semantic guard reject")
            continue
        # 单场景净变化与替换比例护栏。
        # replace_tail：短促有力的钩子句天然比被替换的铺垫尾句短，下限放到 0.18（绝对≥8字、
        # 必须完整成句已由 _replacement_ok 保证），整章长度由编排层字数门+全部门复检兜底。
        lo, hi = (0.4, 1.6) if e["op"] == "replace_span" else (0.18, 2.0)
        if not (lo * len(removed) <= len(repl) <= hi * len(removed)):
            notes.append(f"scene {sid} replacement length out of band "
                         f"({len(removed)}->{len(repl)}); skip")
            continue
        scene_delta_ratio = abs(len(new) - len(text)) / max(1, len(text))
        if scene_delta_ratio > scene_change_cap:
            notes.append(f"scene {sid} scene-level change {scene_delta_ratio:.2f} > cap; skip")
            continue
        cur[sid] = new
        # 记录“替换后文本”中该片段的新终点，供下一条在同一（已更新）文本上判相邻
        last_end_by_sid[sid] = start + len(repl)
        applied += 1
        notes.append(f"scene {sid} {e['op']} applied ({len(removed)}->{len(repl)})")
    total_after = sum(len(v) for v in cur.values())
    total_ratio = abs(total_after - total_before) / total_before
    if applied == 0 or total_ratio > total_change_cap or abs(total_after - total_before) > max_abs_total_change:
        return dict(texts), 0, notes + ["change cap / absolute-length cap violated or no edits; rollback"]
    return cur, applied, notes
