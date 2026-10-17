# -*- coding: utf-8 -*-
"""CC round-23 P0-1：场景差异化推进 + 氛围占比确定性门（零 LLM）。

针对真机 r25 ch0“文笔成熟但 4 场景同质、反复渲染雾/寒/磷光、事件与信息增量不足，
被 pacing/innovation/retention 压到 83 分”的根因，在 assembly 后、reviewer 前新增
两类纯规则检测：

1) 新状态/新实体覆盖（no_new_state）：导演任务卡每场景在既有 concrete_events 之外
   再声明 scene_progression_contract.new_state_or_entity（本场首次出现/改变的具体
   实体或状态）与 irreversible_change（一个不可逆动作/关系变化）。复用 density_gate
   的语义实词命中（含同义簇），至少 1 项在正文落地才算本场“真的向前走了一步”。
   - 仅对显式给出 scene_progression_contract 的卡硬判；老卡/模板卡软跳过（与密度门
     blueprint_has_explicit_density 的安全模式一致），避免对旧规划误杀。

2) 氛围饱和（atmosphere_saturated）：单场“纯氛围/感官/心理句”占比 > 阈值（默认0.55）
   判该场靠渲染而非推进；相邻两场共享氛围词占其内容词比例 > adjacent 阈值（默认0.25）
   判相邻场同质。纯氛围句=含环境/身体氛围词、但无对话、无具体动作动词、无具名实体事件。

处置（交 orchestrator）：违规场景定点重生 1-2 次，负例指令要求“插入一个此前未出现的
具体状态变化/动作、把氛围句压到阈值以下”；重生有改善但未完全达标走 soft-pass 交
reviewer，绝不因单一氛围比例硬整章重排（婴儿开篇氛围本就偏高，避免误杀好稿）。

所有阈值与词表数据驱动：config/scene_progression.json，可在不改代码的情况下校准。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import density_gate

# 新状态描述里要剔除的泛词/元措辞（防“状态/首次/呈现”这类无信息词被当成锚点）
_PROG_META_DEFAULT = [
    "状态", "首次", "完整", "完全", "呈现", "显现", "出现", "可见", "可见化", "范围",
    "结束", "阶段", "视角", "景象", "确认", "进入", "完成", "进行", "发生", "形成",
    "产生", "相关", "对应", "当前", "此时", "开始", "正式", "暂时", "彻底", "已经",
    "正在", "一种", "一个", "某个", "方面", "方式", "情况", "过程", "悬置", "变化",
    "事件", "形象", "具象", "完成压缩",
]

_DEFAULT_CFG = {
    # 单场：纯氛围句占比超过此值 -> 氛围饱和
    "scene_atmosphere_ratio": 0.55,
    # 相邻两场：共享氛围词 / 两场内容词总数 超过此值 -> 相邻同质
    # CC round-24 P0-4：0.25 -> 0.35。雾/迷雾禁区固定环境天然共享意象；该项已降级为“仅提示、
    # 不触发重生”，放宽阈值避免给 reviewer 过度警示，但 >0.35 仍能抓到真正过度的重复。
    "adjacent_atmosphere_ratio": 0.35,
    # 新状态/新实体：命中项最少个数（语义实词命中，1 项即算本场有推进）
    "new_state_min_hits": 1,
    # 新状态描述里要剔除的泛词/元措辞（防“状态/首次/呈现”当锚点）
    "prog_meta_stopwords": list(_PROG_META_DEFAULT),
    # 跨写法同义组：组内任一成员在正文出现，即视为该锚点落地
    "new_state_synonyms": [
        ["发光点", "发光", "光点", "光团", "光球", "亮点"],
        ["坠落", "落下", "坠下", "坠入", "沉下", "沉落", "沉坠", "跌落"],
        ["降生", "出生", "诞生", "落地", "坠地"],
        ["闭合", "合拢", "愈合", "关闭", "封合"],
        ["枯黄", "发黄", "蔫黄", "焦黄", "枯萎"],
        ["窗户", "窗棂", "窗纸", "窗口", "格窗"],
        ["影子", "人影", "身影", "黑影"],
    ],
    # 少于这个有效句数的场景不做单场氛围比例硬判（太短，统计无意义）
    "min_sentences_for_ratio": 6,
    # 环境/氛围/身体感官词（默认种子，可被 config 全量覆盖/扩展）
    "atmosphere_terms": [
        "雾", "雾气", "雾丝", "雾墙", "雾霭", "浓雾", "迷雾", "寒", "寒意", "寒冷",
        "冷", "冷意", "冰凉", "凉", "凉意", "暖", "暖意", "光", "光影", "磷火", "幽光",
        "幽蓝", "残影", "影", "暗影", "夜", "夜色", "黑夜", "暗", "昏暗", "灰", "灰白",
        "气息", "腥", "腥气", "湿", "湿气", "黏稠", "风", "夜风", "露", "夜露", "霜",
        "呼吸", "喘息", "颤", "颤抖", "颤栗", "抖", "哆嗦", "痉挛", "凝视", "目光",
        "注视", "寂静", "死寂", "沉寂", "静谧", "嗡鸣", "轰鸣", "震颤", "震荡", "压力",
        "窒息", "沉寂", "微光", "寒光", "漩涡", "涡旋", "翻涌", "涌动", "笼罩", "包裹",
    ],
    # 具体动作/事件动词：句中出现即不算“纯氛围句”
    "concrete_action_verbs": [
        "抱", "抱起", "抱走", "带回", "接过", "放下", "走", "迈步", "冲", "逃", "跑",
        "停", "站住", "跪", "蹲", "坐", "躺", "站", "来", "去", "进", "退", "推", "拉",
        "攥", "抓", "握住", "松开", "抬手", "举", "递", "接", "砍", "刺", "挡", "躲",
        "回头", "转身", "俯身", "弯腰", "点头", "摇头", "睁眼", "闭眼", "张嘴", "开口",
        "说", "问", "答", "喊", "叫", "吼", "哭", "啼", "笑", "骂", "劝", "阻", "拦",
        "赐", "取名", "命名", "决定", "答应", "拒绝", "塞", "系", "放置", "留下", "带走",
        "点燃", "举起", "擎着", "靠近", "逼近", "退开", "跟上", "带路", "敲", "推门",
    ],
}

_SENT_SPLIT = __import__("re").compile(r"(?<=[。！？!?…])")
_QUOTE_CHARS = "“”\"'‘’「」『』"
_HAS_CJK = __import__("re").compile(r"[\u4e00-\u9fffA-Za-z0-9]")

try:
    import jieba.posseg as _pseg
except Exception:  # pragma: no cover - jieba 由项目主依赖保证
    _pseg = None


@lru_cache(maxsize=1)
def load_progression_config(root: str | Path | None) -> dict:
    cfg = {k: (list(v) if isinstance(v, list) else v) for k, v in _DEFAULT_CFG.items()}
    if root is None:
        return cfg
    p = Path(root) / "config" / "scene_progression.json"
    if p.exists():
        try:
            user = json.loads(p.read_text(encoding="utf-8"))
            for key in ("atmosphere_terms", "concrete_action_verbs"):
                # 词表：用户给 extra_* 则追加，给同名 list 则全量替换
                extra = user.get(f"extra_{key}")
                if isinstance(extra, list):
                    cfg[key] = list(dict.fromkeys([*cfg[key], *map(str, extra)]))
                if isinstance(user.get(key), list):
                    cfg[key] = list(dict.fromkeys(map(str, user[key])))
            for key in ("scene_atmosphere_ratio", "adjacent_atmosphere_ratio",
                        "new_state_min_hits", "min_sentences_for_ratio"):
                if key in user:
                    cfg[key] = user[key]
            if isinstance(user.get("prog_meta_stopwords"), list):
                cfg["prog_meta_stopwords"] = list(dict.fromkeys(map(str, user["prog_meta_stopwords"])))
            _extra_meta = user.get("extra_prog_meta_stopwords")
            if isinstance(_extra_meta, list):
                cfg["prog_meta_stopwords"] = list(dict.fromkeys(
                    [*map(str, cfg["prog_meta_stopwords"]), *map(str, _extra_meta)]))
            if isinstance(user.get("new_state_synonyms"), list):
                cfg["new_state_synonyms"] = user["new_state_synonyms"]
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def split_sentences(text: str) -> list[str]:
    out = []
    for chunk in _SENT_SPLIT.split(text or ""):
        s = chunk.strip()
        # 过滤纯标点/孤立引号残片（如句末引号 ”），避免污染句数统计
        if s and _HAS_CJK.search(s):
            out.append(s)
    return out


def _has_dialogue(sent: str) -> bool:
    return any(q in sent for q in "“”\"‘’「」『』")


def _term_hits(text: str, terms) -> list[str]:
    return [t for t in terms if t and t in text]


def _has_concrete_action(sent: str, cfg: dict) -> bool:
    verbs = cfg.get("concrete_action_verbs", [])
    multi = [v for v in verbs if len(v) >= 2]
    single = {v for v in verbs if len(v) == 1}
    if _term_hits(sent, multi):
        return True
    if not single or _pseg is None:
        return False
    # 单字动词仅在被 jieba 判为动词 token 时计数，避免“压下来”的“来”、
    # “刺骨”的“刺”这类趋向补语/构词成分误伤纯氛围句。
    for w, f in _pseg.cut(sent):
        if w in single and f.startswith("v"):
            return True
    return False


def is_pure_atmosphere_sentence(sent: str, cfg: dict) -> bool:
    """纯氛围/感官/心理句：含氛围词，但无对话、无具体动作动词。"""
    if not sent:
        return False
    if _has_dialogue(sent):
        return False
    if _has_concrete_action(sent, cfg):
        return False
    return bool(_term_hits(sent, cfg.get("atmosphere_terms", [])))


def scene_atmosphere_ratio(text: str, cfg: dict) -> dict:
    sents = split_sentences(text)
    total = len(sents)
    atmos = sum(1 for s in sents if is_pure_atmosphere_sentence(s, cfg))
    ratio = (atmos / total) if total else 0.0
    return {
        "total_sentences": total,
        "atmosphere_sentences": atmos,
        "ratio": round(ratio, 3),
        "saturated": (total >= int(cfg.get("min_sentences_for_ratio", 6))
                      and ratio > float(cfg.get("scene_atmosphere_ratio", 0.55))),
    }


def _content_word_set(text: str, cfg: dict) -> set[str]:
    """本场出现的氛围内容词（用于相邻场共享度）。"""
    return set(_term_hits(text or "", cfg.get("atmosphere_terms", [])))


def adjacent_atmosphere_overlap(text_a: str, text_b: str, cfg: dict) -> dict:
    wa, wb = _content_word_set(text_a, cfg), _content_word_set(text_b, cfg)
    shared = wa & wb
    union = wa | wb
    ratio = (len(shared) / len(union)) if union else 0.0
    return {
        "shared": sorted(shared),
        "ratio": round(ratio, 3),
        "saturated": ratio > float(cfg.get("adjacent_atmosphere_ratio", 0.25)),
    }


def progression_contract(bp: dict) -> dict:
    """安全读取单场 scene_progression_contract；非 dict / 空 返回空契约。"""
    c = (bp or {}).get("scene_progression_contract")
    if not isinstance(c, dict):
        return {"new_state_or_entity": [], "irreversible_change": ""}
    nse = c.get("new_state_or_entity")
    if not isinstance(nse, list):
        nse = []
    nse = [str(x).strip() for x in nse if str(x).strip()]
    irr = str(c.get("irreversible_change", "") or "").strip()
    return {"new_state_or_entity": nse, "irreversible_change": irr}


def blueprint_has_progression_contract(bp: dict) -> bool:
    c = progression_contract(bp)
    return bool(c["new_state_or_entity"]) or bool(c["irreversible_change"])


def _clean_state_list(v, cap: int = 4) -> list[str]:
    """new_state_or_entity 只保留非空中文字符串，去空白/去重保序，限项。

    容忍模型把单项误写成一个裸字符串（包成单元素列表）。
    """
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return []
    out: list[str] = []
    for x in v:
        s = str(x).strip() if not isinstance(x, dict) else ""
        # dict 项容忍：取常见描述键拼起来
        if isinstance(x, dict):
            parts = [str(x.get(k, "")).strip() for k in ("state", "entity", "change", "desc", "content")]
            s = "：".join(p for p in parts if p)
        if not s:
            continue
        if s in out:
            continue
        out.append(s)
    return out[:cap]


def sanitize_scene_progression(bp: dict) -> dict:
    """清洗单个蓝图的 scene_progression_contract：非 dict 删除；字段归一成合法结构。

    模型偶发把契约写成自由文本字符串或把 new_state_or_entity 写成单个字符串，
    门对脏结构虽安全跳过，但那样该约束就不会被机器硬判——这里在源头规整。
    清洗后为空契约则保留空 dict（与“老卡无契约”等价，门软跳过 no_new_state）。
    """
    if not isinstance(bp, dict):
        return bp
    c = bp.get("scene_progression_contract")
    if not isinstance(c, dict):
        # 字符串/列表等非法类型：无法可靠解析，直接删除避免下游脏判
        if c is not None:
            bp.pop("scene_progression_contract", None)
        return bp
    nse = _clean_state_list(c.get("new_state_or_entity"))
    irr = c.get("irreversible_change", "")
    irr = str(irr).strip() if isinstance(irr, str) else ""
    bp["scene_progression_contract"] = {
        "new_state_or_entity": nse,
        "irreversible_change": irr[:120],
    }
    return bp


def sanitize_task_card_progression(task_card: dict) -> dict:
    """统一后处理点：清洗所有场景蓝图的推进契约。原地修改并返回 task_card。"""
    if not isinstance(task_card, dict):
        return task_card
    for bp in task_card.get("scene_blueprints", []) or []:
        if isinstance(bp, dict):
            sanitize_scene_progression(bp)
    return task_card


def _new_state_covered(desc: str, scene_text: str, cfg: dict, syn: dict) -> tuple[bool, dict]:
    """单个新状态/新实体项是否在正文落地（专用实义锚点抽取 + 配置同义组 + 密度门同义簇）。

    用 extract_progression_terms 而非通用密度抽取：后者只留 n/vn/部分v，会把
    “发光点/坠落/降生/枯黄/窗户”这类被 jieba 切碎或标成 a 的关键实义词丢掉，
    导致“正文其实演了、却判 no_new_state”的误报与白烧重生。
    """
    terms = extract_progression_terms(desc, cfg)
    if not terms:
        # 抽不出实词时退化为整串关键词匹配（长度短的状态描述）
        terms = [desc]
    groups = cfg.get("new_state_synonyms", []) or []
    hit = [t for t in terms if _prog_term_present(t, scene_text, groups, syn)]
    need = int(cfg.get("new_state_min_hits", 1))
    ok = len(hit) >= need
    return ok, {"desc": desc[:60], "terms": terms, "hit": hit}


# ── 新状态实义锚点抽取（Fix A：降低 no_new_state 同义/分词误报）──────────────
_PROG_KEEP_PREFIX = ("n", "v", "a", "i")


def _merge_single_run(run: list[str]) -> list[str]:
    """把连续的单字实义语素合并成词（坠落→坠+落，降生→降+生），并与单字一并保留。"""
    out = []
    if len(run) >= 2:
        out.append("".join(run))
    return out


def extract_progression_terms(desc: str, cfg: dict) -> list[str]:
    """从“新状态/新实体”描述抽可匹配实义锚点：n*/v/a/i 的≥2字词 + 相邻单字实义合并词。

    剔除 prog_meta_stopwords 泛词（状态/首次/呈现/出现/范围…）与标点；保序去重。
    """
    meta = set(cfg.get("prog_meta_stopwords", _PROG_META_DEFAULT) or [])
    raw_terms: list[str] = []
    single_run: list[str] = []

    def _flush():
        nonlocal single_run
        if len(single_run) >= 2:
            raw_terms.append("".join(single_run))
        single_run = []

    try:
        tagged = list(_pseg.cut(desc or "")) if _pseg else []
    except Exception:
        tagged = []
    for w, flag in tagged:
        w = (w or "").strip()
        if not w or w in meta:
            _flush()
            continue
        keep_pos = flag.startswith(_PROG_KEEP_PREFIX)
        if len(w) >= 2:
            _flush()
            if keep_pos and any("\u4e00" <= ch <= "\u9fff" for ch in w):
                raw_terms.append(w)
        elif len(w) == 1 and "\u4e00" <= w <= "\u9fff" and keep_pos:
            single_run.append(w)
        else:
            _flush()
    _flush()

    seen, out = set(), []
    for t in raw_terms:
        t = t.strip()
        if len(t) < 2 or t in meta or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out[:10]


def _prog_term_present(term: str, text: str, groups: list, syn: dict) -> bool:
    if term and term in text:
        return True
    # 配置化同义组（如 发光点/光点、坠落/落下、降生/出生落地）
    for g in groups or []:
        if term in g:
            if any(v and v != term and v in text for v in g):
                return True
    # 复用密度门 arc/通用同义簇
    try:
        return bool(density_gate._term_present(term, text, syn))  # noqa: SLF001
    except Exception:
        return False



def evaluate_scene_progression(bp: dict, scene_text: str, cfg: dict,
                               root: str | Path | None = None,
                               arc: str | None = None) -> dict:
    """单场推进评估：新状态覆盖 + 氛围占比。"""
    root = str(root) if root is not None else ""
    syn = density_gate.load_synonym_map(root, arc, {}) if root else {}
    contract = progression_contract(bp)
    missing_states: list[dict] = []
    if contract["new_state_or_entity"]:
        for desc in contract["new_state_or_entity"]:
            ok, detail = _new_state_covered(desc, scene_text or "", cfg, syn)
            if not ok:
                missing_states.append(detail)
    atmos = scene_atmosphere_ratio(scene_text or "", cfg)
    return {
        "scene_id": int((bp or {}).get("scene_num", 0) or 0),
        "explicit_contract": blueprint_has_progression_contract(bp),
        "missing_new_state": missing_states,
        "atmosphere": atmos,
        # 仅在显式契约下把“新状态缺失”作为硬违规；氛围饱和独立成立
        "no_new_state": bool(missing_states),
        "atmosphere_saturated": bool(atmos["saturated"]),
    }


def check_chapter_progression(task_card: dict, scenes: list, cfg: dict,
                              root: str | Path | None = None,
                              arc: str | None = None) -> dict:
    """整章检测。返回每场景违规 + 相邻场同质对。

    scenes: 含 scene_id/scene_text 的对象或 dict。
    """
    bps = (task_card or {}).get("scene_blueprints", []) or []
    bp_by_id = {int(b.get("scene_num", 0) or 0): b for b in bps if isinstance(b, dict)}

    def _sid_text(sc):
        if isinstance(sc, dict):
            return int(sc.get("scene_id", 0) or 0), str(sc.get("scene_text", "") or "")
        return int(getattr(sc, "scene_id", 0) or 0), str(getattr(sc, "scene_text", "") or "")

    ordered = sorted((_sid_text(s) for s in scenes), key=lambda x: x[0])
    per_scene: dict[int, dict] = {}
    scene_violations: list[dict] = []
    for sid, text in ordered:
        bp = bp_by_id.get(sid, {})
        ev = evaluate_scene_progression(bp, text, cfg, root=root, arc=arc)
        per_scene[sid] = ev
        vtypes = []
        if ev["explicit_contract"] and ev["no_new_state"]:
            vtypes.append("no_new_state")
        if ev["atmosphere_saturated"]:
            vtypes.append("atmosphere_saturated")
        if vtypes:
            scene_violations.append({"scene_id": sid, "types": vtypes,
                                     "detail": {"missing_new_state": ev["missing_new_state"],
                                                "atmosphere_ratio": ev["atmosphere"]["ratio"]}})

    # 相邻场氛围同质（按场景顺序的相邻对，而非全部组合）
    adjacent: list[dict] = []
    for (s1, t1), (s2, t2) in zip(ordered, ordered[1:]):
        ov = adjacent_atmosphere_overlap(t1, t2, cfg)
        if ov["saturated"]:
            later = max(s1, s2)
            adjacent.append({"scenes": [s1, s2], "later_scene": later,
                             "shared": ov["shared"], "ratio": ov["ratio"]})
    return {"scene_violations": scene_violations, "adjacent": adjacent,
            "per_scene": per_scene}


# ── 定点重生指令 ─────────────────────────────────────────────────────────
def new_state_directive(violation: dict) -> str:
    missing = [d.get("desc", "") for d in (violation or {}).get("detail", {}).get(
        "missing_new_state", [])]
    lines = ["本场上一稿没有把“必须发生的新变化”演出来，只是在渲染气氛。必须【新增】"
             "此前在本章未出现过的具体推进（状态变化 / 新实体登场 / 一个被做出的决定或动作），"
             "从以下要素里至少落地一项，写成可观察的动作或一句具体的话："]
    lines.extend(f"- {m}" for m in missing if m)
    lines.append("严禁只改写措辞或继续铺陈雾色/寒气/心理；必须让故事真正向前走一步，"
                 "但不得改变任务卡既定剧情走向，也不得写成复杂成人动作（低能动性主角可用"
                 "“体温变化/被移动/听到新声音/某人做出决定”这类事件满足）。")
    return "\n".join(lines)


def atmosphere_ratio_directive(ratio: float, threshold: float) -> str:
    return (
        f"本场上一稿环境/感官/心理描写句占比约{ratio:.0%}，超过{threshold:.0%}上限，"
        "氛围堆砌、情节推进稀薄。请删减重复的雾/寒/光/被注视等渲染，把其中一部分改写为"
        "【具体动作、对话或新信息】：人物做了什么、说了什么、出现了什么此前没有的东西或变化。"
        f"改写后纯氛围描写句占比必须降到{threshold:.0%}以下，同时至少补 1 处具体推进；"
        "总字数基本保持，不要靠再堆环境描写凑字数。"
    )


def adjacent_motif_directive(adj: dict) -> str:
    shared = "、".join(adj.get("shared", [])[:12])
    return (
        f"本场景与相邻场景反复使用同一组意象（{shared}），读感同质。请保留必要承接，"
        "但改用本场景独有的具体细节与动作来推进，不要再重复渲染上述氛围词；"
        "本场必须有区别于上一场的新事件、新信息或关系变化。"
    )
