# -*- coding: utf-8 -*-
"""CC round-10 P0-1：内容密度确定性门（零 LLM）。

导演任务卡每个场景新增三类“可写内容骨架”：
- concrete_events：谁做了什么可观察动作（不能只是环境描写）
- named_interactions：具名角色之间的实质互动
- info_reveal_points：明确的信息揭示/伏笔埋设点
低能动性主角（婴儿篇）另须 protagonist_interiority（转世意识的评判性内在观察），
该项仅软检测不硬阻断（纯规则难以稳定区分“评判”与“感官”）。

assembly 后、reviewer 前，对每个 scene_text 做关键词/字 bigram 共现覆盖率检测，
覆盖率 < coverage_threshold 判该场景“氛围堆砌、事件未演足”，交 orchestrator 定点重生。
缺失字段时从 beats/characters 确定性补全，避免 flash 偶发漏字段导致重生成死循环。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import scope_gate

_DEFAULT = {
    "coverage_threshold": 0.8,
    "event_bigram_ratio": 0.34,
    "event_min_bigrams": 2,
    "min_concrete_events": 2,
    "min_named_interactions": 1,
    "min_info_reveal_points": 1,
    "interiority_markers": [],
    "function_chars": "",
    "interaction_keywords": [],
}


def reset_config_cache() -> None:
    load_density_config.cache_clear()
    arc_for_chapter.cache_clear()


@lru_cache(maxsize=1)
def load_density_config(root: str | Path | None) -> dict:
    cfg = dict(_DEFAULT)
    if root is None:
        return cfg
    p = Path(root) / "config" / "density_requirements.json"
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


@lru_cache(maxsize=None)
def arc_for_chapter(chapter_num: int, root: str | Path | None) -> str | None:
    """复用 leak_terms 词表里的篇章区间作为唯一 arc→章节范围来源。"""
    if root is None:
        return None
    try:
        active = scope_gate._active_cfg(chapter_num, root)  # noqa: SLF001
        if active:
            return str(active.get("arc") or "").strip() or None
    except Exception:  # noqa: BLE001
        return None
    return None


def requirements_for(cfg: dict, arc: str | None) -> dict:
    req = dict(cfg)
    ov = (cfg.get("arc_overrides") or {}).get(arc or "")
    if isinstance(ov, dict):
        req.update(ov)
    return req


def _cjk_runs(text: str) -> list[str]:
    out: list[str] = []
    buf = []
    for ch in text:
        if "一" <= ch <= "鿿":
            buf.append(ch)
        else:
            if len(buf) >= 2:
                out.append("".join(buf))
            buf = []
    if len(buf) >= 2:
        out.append("".join(buf))
    return out


def _content_bigrams(text: str, func_chars: str) -> list[str]:
    """在 CJK 连续段上取 2-gram；两个字都是常见功能字时丢弃（保留含实义字的 gram）。"""
    grams: list[str] = []
    fset = set(func_chars or "")
    for run in _cjk_runs(text):
        for i in range(len(run) - 1):
            a, b = run[i], run[i + 1]
            if a in fset and b in fset:
                continue
            grams.append(a + b)
    return grams


def _cjk_name_tokens(strings) -> list[str]:
    names: list[str] = []
    for s in strings or []:
        s = str(s).strip()
        # 跳过 C001 之类 ID 与非中文/单字占位
        if len(s) >= 2 and all("一" <= c <= "鿿" for c in s):
            names.append(s)
    return names


def _as_text(item) -> str:
    if isinstance(item, dict):
        parts = []
        for key in ("event", "observable_action", "brief", "content", "interaction_type", "goal"):
            v = item.get(key)
            if v:
                parts.append(str(v))
        chars = item.get("characters")
        if isinstance(chars, list):
            parts.extend(str(c) for c in chars)
        return " ".join(parts)
    return str(item)


def normalize_scene_density(bp: dict, synthesize: bool = True) -> dict:
    """补齐缺失密度槽位；已存在的字段原样保留。

    synthesize=False 时只保留任务卡显式给出的字段（不从 beats/characters 派生），
    供覆盖率门区分“导演显式规划”与“老卡/模板卡无密度字段”——后者不硬判重规划。
    """
    events = bp.get("concrete_events")
    if not isinstance(events, list) or not events:
        events = (
            [{"event": str(b), "observable_action": str(b)} for b in (bp.get("beats") or []) if str(b).strip()]
            if synthesize else []
        )

    interactions = bp.get("named_interactions")
    if not isinstance(interactions, list) or not interactions:
        chars = bp.get("characters") or []
        if synthesize and len(chars) >= 2:
            interactions = [{
                "characters": [str(chars[0]), str(chars[1])],
                "interaction_type": "互动/对话/冲突",
                "brief": str(bp.get("goal", "") or ""),
            }]
        else:
            interactions = []

    reveals = bp.get("info_reveal_points")
    if not isinstance(reveals, list):
        reveals = []
    return {
        "concrete_events": events,
        "named_interactions": interactions,
        "info_reveal_points": reveals,
    }


def blueprint_has_explicit_density(bp: dict) -> bool:
    for key in ("concrete_events", "named_interactions", "info_reveal_points"):
        v = bp.get(key)
        if isinstance(v, list) and v:
            return True
    return False


def _item_covered(text_item: str, scene_text: str, cfg: dict) -> tuple[bool, int, int]:
    grams = _content_bigrams(text_item, cfg.get("function_chars", ""))
    if not grams:
        return (True, 0, 0)
    uniq = set(grams)
    present = sum(1 for g in uniq if g in scene_text)
    ratio = present / len(uniq)
    covered = present >= int(cfg.get("event_min_bigrams", 2)) and ratio >= float(cfg.get("event_bigram_ratio", 0.34))
    return (covered, present, len(uniq))


def evaluate_scene(bp: dict, scene_text: str, cfg: dict, synthesize: bool = True) -> dict:
    """返回单场景覆盖率结果。ratio 为已演足的内容骨架占比。

    synthesize=False 时只统计任务卡显式给出的密度项；无任何显式字段则 total=0、ratio=1.0
    （老卡/模板卡不被硬判）。
    """
    dens = normalize_scene_density(bp, synthesize=synthesize)
    items: list[tuple[str, str, list[str]]] = []  # (kind, text, required_names)
    for ev in dens["concrete_events"]:
        items.append(("event", _as_text(ev), []))
    for it in dens["named_interactions"]:
        names = _cjk_name_tokens(it.get("characters") if isinstance(it, dict) else [])
        items.append(("interaction", _as_text(it), names))
    for rv in dens["info_reveal_points"]:
        items.append(("reveal", _as_text(rv), []))

    covered: list[str] = []
    missing: list[str] = []
    for kind, text_item, names in items:
        ok, _, _ = _item_covered(text_item, scene_text, cfg)
        if ok and names:
            ok = all(n in scene_text for n in names)
        (covered if ok else missing).append(f"[{kind}] {text_item[:60]}")

    total = len(items)
    ratio = (len(covered) / total) if total else 1.0
    markers = cfg.get("interiority_markers") or []
    interiority = any(m in scene_text for m in markers)
    return {
        "ratio": ratio,
        "covered": covered,
        "missing": missing,
        "total": total,
        "covered_count": len(covered),
        "interiority": interiority,
    }


def density_directive(result: dict, protagonist_interiority: int = 0,
                    agency_level: str | None = None) -> str:
    miss = result.get("missing") or []
    lines = ["本场上一稿“氛围堆砌、规定的具体事件/互动/伏笔没有真正演出来”，必须把以下缺失内容补成可观察的正文："]
    for m in miss:
        lines.append(f"- {m}")
    lines.append(
        "要求：把每个事件演成“谁做了什么可观察动作/对话”，具名角色之间要有实质互动（对话、劝阻、冲突、配合），"
        "伏笔/信息点要落成具体的一句话或一个动作；严禁只写雾色/气氛/心理而不发生事件，严禁复述其它场景已演的情节。"
    )
    if protagonist_interiority and not result.get("interiority", False):
        try:
            from novel_engine.quality.pov_interiority_gate import is_restricted as _il22
        except Exception:
            _il22 = lambda a: False  # noqa: E731
        if _il22(agency_level):
            lines.append(
                "另可补1处低能动性主角的【碎片内心印象】：不超过50字的感官/生理/模糊情绪（如暖、冷、怕、安心）；"
                "严禁 恩情/誓约/命运/修为/前世/转世/此生不负 等抽象术语与成人论证式独白。"
            )
        else:
            lines.append(
                "另须补至少1处转世意识的【评判性内在观察】（如对眼前人/事做出判断、戒备、回忆对照，而非单纯感官），"
                "内在活动不得推进时间、不得出现修炼体系词汇。"
            )
    return "\n".join(lines)

# ── CC round-13 语义锚点存在性匹配（替代 bigram 字面复现）─────────────────────
import re as _re_sem

_pg = None
def _posseg():
    global _pg
    if _pg is None:
        import jieba.posseg as _p
        _pg = _p
    return _pg


def _cfg_list(cfg: dict, key: str) -> list[str]:
    v = (cfg or {}).get(key)
    return [str(x) for x in v if str(x).strip()] if isinstance(v, list) else []


@lru_cache(maxsize=None)
def _load_arc_synonyms(root: str, arc: str | None) -> tuple:
    fp = Path(root) / "config" / "synonym_map" / f"{arc}.json" if arc else None
    if fp is None or not fp.exists():
        return tuple()
    try:
        obj = json.loads(fp.read_text(encoding="utf-8"))
        return tuple((str(k), tuple(str(x) for x in vs))
                     for k, vs in (obj.get("synonyms", {}) or {}).items() if isinstance(vs, list))
    except Exception:
        return tuple()


def load_synonym_map(root: str, arc: str | None, cfg: dict | None = None) -> dict:
    """汇总 {词: [同义变体]}：arc 词表 + 通用同义 + 主角视角别名。

    婴儿等第一视角章节正文常不直呼主角名，主角名通过 protagonist_aliases 映射到
    “婴儿/孩子/他”等视角指代，避免“主体名未字面出现”造成系统性误判。
    """
    merged: dict = {}
    for k, vs in _load_arc_synonyms(root, arc):
        merged.setdefault(k, [])
        for x in vs:
            if x not in merged[k]:
                merged[k].append(x)
    for extra_key in ("generic_synonyms", "protagonist_aliases"):
        for k, vs in ((cfg or {}).get(extra_key, {}) or {}).items() if cfg else []:
            if isinstance(vs, list):
                merged.setdefault(str(k), [])
                for x in vs:
                    if str(x) not in merged[str(k)]:
                        merged[str(k)].append(str(x))
    return merged


def _term_present(term: str, text: str, syn: dict) -> bool:
    cands = [term] + syn.get(term, [])
    return any(c and c in text for c in cands)


_QUOTED_RE = _re_sem.compile(r"[‘’'\"“”「」『』]([^‘’'\"“”「」『』]{2,12})[‘’'\"“”「」『』]")
_META_RE = _re_sem.compile(
    r"(通过|描述|描写|描述周围|暗示|烘托|侧面|铺垫|伏笔|为了|用于|用来|显得|表现|表达|体现|"
    r"强化|揭示|揭露|制造|符合|设定|登场|不露脸|只留|观感|镜头|画面|信息|世界观|F\d+|"
    r"本场景|本章|主角出生环境|空气|中有|一种|的)")


def extract_anchor_terms(reveal: dict, limit: int = 3) -> list[str]:
    """从 info_reveal 项取可匹配意象：优先 anchor_terms；否则从引号专名 + 内容名词回退抽取。"""
    if not isinstance(reveal, dict):
        return []
    given = reveal.get("anchor_terms")
    if isinstance(given, list) and given:
        out = [str(x).strip() for x in given if str(x).strip()]
        if out:
            return out[:limit]
    text = str(reveal.get("content", "") or reveal)
    cands: list[str] = []
    for m in _QUOTED_RE.finditer(text):
        q = m.group(1).strip()
        if 2 <= len(q) <= 10:
            cands.append(q)
    # 去元措辞后按名词/动名词回退抽取
    cleaned = _META_RE.sub(" ", text)
    try:
        for w, flag in _posseg().cut(cleaned):
            w = w.strip()
            if len(w) >= 2 and flag in ("n", "nr", "ns", "nt", "nz", "vn", "an", "i"):
                cands.append(w)
    except Exception:
        pass
    # 保序去重
    seen, uniq = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq[:limit]


def extract_content_terms(desc: str, cfg: dict) -> tuple[list[str], list[str]]:
    """jieba 分词去功能/抽象/元措辞，返回 (对象词[名词/专名], 动作词[具体动词])，保序去重。"""
    meta = set(_cfg_list(cfg, "meta_stopwords"))
    abstract = set(_cfg_list(cfg, "abstract_verbs"))
    # “被遗弃/被放置”jieba 切成“遗弃/放置”，统一去被字归一进抽象词表
    abstract |= {w.lstrip("被") for w in abstract if w.startswith("被") and len(w) > 2}
    func = set(cfg.get("function_chars", "") or "")
    objects, actions = [], []

    def _keep(w: str) -> bool:
        if len(w) < 2:
            return False
        if w in meta or w in abstract:
            return False
        if all(ch in func for ch in w):
            return False
        if _re_sem.fullmatch(r"[\d\W_]+", w):
            return False
        # 介词粘连人名碎片（“向陆/朝他/把婴”等2字残片），非内容锚点
        if len(w) == 2 and w[0] in "向朝对往将把被让给随跟" and w not in meta:
            return False
        return True

    try:
        tagged = list(_posseg().cut(desc or ""))
    except Exception:
        tagged = []
    for w, flag in tagged:
        w = w.strip()
        if not _keep(w):
            continue
        if flag in ("n", "nr", "ns", "nt", "nz", "an", "i"):
            objects.append(w)
        elif flag in ("v", "vn") and w not in abstract:
            actions.append(w)

    def _dedup(seq):
        seen, out = set(), []
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    objects = _dedup(objects)
    actions = _dedup(actions)
    ocap = int(cfg.get("event_object_cap", 2) or 2)
    acap = int(cfg.get("event_action_cap", 2) or 2)
    # 对象词/可观察动作词均衡取样，避免主角名占满名额、把“啼哭/挣扎”等动作挤掉
    objs = objects[:ocap]
    acts = actions[:acap]
    merged = objs + acts
    maxcap = int(cfg.get("event_max_terms", 4) or 4)
    return merged[:maxcap], acts


def event_covered(ev: dict, text: str, cfg: dict, syn: dict) -> tuple[bool, dict]:
    desc = _as_text(ev)
    action = ""
    if isinstance(ev, dict):
        action = str(ev.get("observable_action", "") or "")
    terms, actions = extract_content_terms(desc + " " + action, cfg)
    hit_terms = [t for t in terms if _term_present(t, text, syn)]
    name_terms = [t for t in terms if t not in actions]
    hit_names = [t for t in name_terms if _term_present(t, text, syn)]
    hit_actions = [a for a in actions if _term_present(a, text, syn)]
    hit = len(hit_terms)
    total = len(terms)
    ratio = (hit / total) if total else 1.0
    k_min = int(cfg.get("event_hit_min", 2) or 2)
    r_min = float(cfg.get("event_hit_ratio", 0.5) or 0.5)
    ok = (total == 0) or (hit >= k_min) or (ratio >= r_min) or (
        bool(hit_names) and bool(hit_actions))
    return ok, {"terms": terms, "hit": hit_terms, "total": total, "ratio": round(ratio, 2)}


def _anchor_variants(a: str) -> list[str]:
    out = [a]
    for suf in ("感", "性", "气息", "边缘"):
        if a.endswith(suf) and len(a) - len(suf) >= 2:
            out.append(a[: -len(suf)])
    return out


def reveal_covered(rv: dict, text: str, syn: dict) -> tuple[bool, dict]:
    anchors = extract_anchor_terms(rv)
    if not anchors:
        return True, {"anchors": [], "hit": []}
    hit = []
    for a in anchors:
        if any(_term_present(v, text, syn) for v in _anchor_variants(a)):
            hit.append(a)
    return (len(hit) >= 1), {"anchors": anchors, "hit": hit}


def interaction_covered(it: dict, text: str, cfg: dict, syn: dict) -> tuple[bool, dict]:
    if not isinstance(it, dict):
        return True, {"reason": "not_dict"}
    clusters = cfg.get("interaction_clusters", {}) or {}
    itype = str(it.get("interaction_type", "") or "").strip()
    kws = list(clusters.get(itype, []))
    if not kws:
        kws = [itype] + _cfg_list(cfg, "interaction_keywords")
    action_hit = next((k for k in kws if _term_present(k, text, syn)), "")

    placeholders = set(_cfg_list(cfg, "placeholder_tokens"))
    raw_names = _cjk_name_tokens(it.get("characters"))
    # “两名村民/几个村人”等量词性前缀会让整串无法子串命中，剥离出核心身份词
    num_pref = _re_sem.compile(r"^(两名|几名|几个|数个|一群|那位|一个|一名|两位|几位|数名)")
    names = []
    for n in raw_names:
        if n in placeholders or len(n) < 2:
            continue
        c = num_pref.sub("", n).strip()
        names.append(c if len(c) >= 2 else n)
    party = next((n for n in names if _term_present(n, text, syn)), "")
    # 身份/角色指代在场（含自造配角），不依赖逐字姓名
    if not party:
        for g in ("村民", "村人", "人群", "众人", "村里人", "李嫂", "陈五", "老赵",
                  "陈老根", "陆烬", "老人", "老者", "孩子", "婴儿"):
            if g in text:
                party = g
                break
    if not party:
        m = _re_sem.search(r"[赵钱孙李周吴郑王陈刘杨黄张冯][嫂叔公伯婆翁汉五哥婶娘]", text)
        if m:
            party = m.group(0)
    if party and not action_hit:
        # 有人群/身份在场但无具体互动词：有引号对话即视为互动发生，强度交 reviewer
        has_dialogue = ('“' in text and '”' in text) or ('"' in text) or ('‘' in text and '’' in text)
        if has_dialogue:
            action_hit = "dialogue_fallback"
    ok = bool(action_hit) and bool(party)
    return ok, {"type": itype, "action_hit": action_hit, "party": party,
                "names": names, "keywords": kws}


def evaluate_action_chains(bp: dict, scene_text: str, syn: dict) -> list[dict]:
    """CC round-16 P0-3：beat 动作闭环检测（仅对显式标注 action_chain 的事件生效）。

    action_chain 是按序排列的关键动作节点（如 ["触近纹路","停驻","收回"]），每个节点
    复用实词命中（含同义簇）；链上所有节点都命中才算该 beat 闭环完整，缺任一节点则不完整。
    普通无 action_chain 的 beat 不受影响。
    """
    out: list[dict] = []
    events = bp.get("concrete_events") if isinstance(bp, dict) else None
    if not isinstance(events, list):
        return out
    for ev in events:
        if not isinstance(ev, dict):
            continue
        chain = ev.get("action_chain")
        if not isinstance(chain, list) or not chain:
            continue
        hit, missing = [], []
        for node in chain:
            n = str(node or "").strip()
            if not n:
                continue
            terms, _acts = extract_content_terms(n, {})
            if not terms:
                terms = [n]
            node_hit = any(_term_present(t, scene_text, syn) for t in terms) or _term_present(n, scene_text, syn)
            if not node_hit:
                # 整串被子串切词拆散（如“停驻”在正文“停驻片刻”里）→ jieba 分词后词级匹配
                try:
                    ntoks = {w for w in _posseg().cut(n) if len(w.word.strip()) >= 2}
                    stoks = {w.word for w in _posseg().cut(scene_text)}
                    node_hit = bool(ntoks & stoks)
                except Exception:
                    pass
            (hit if node_hit else missing).append(n)
        out.append({
            "label": str(ev.get("event", ""))[:40],
            "chain": [str(x).strip() for x in chain if str(x).strip()],
            "hit": hit,
            "missing": missing,
            "ok": len(missing) == 0,
        })
    return out


def evaluate_scene_semantic(bp: dict, scene_text: str, cfg: dict,
                            root: str | Path | None = None,
                            arc: str | None = None) -> dict:
    """CC round-13：语义锚点存在性覆盖率（逐项二元判定）。"""
    root = str(root) if root is not None else ""
    syn = load_synonym_map(root, arc, cfg) if root else {}
    dens = normalize_scene_density(bp, synthesize=False)
    rows: list[dict] = []
    covered, missing = [], []

    def _push(kind, label, ok, detail):
        rows.append({"kind": kind, "label": label, "ok": bool(ok), "detail": detail})
        (covered if ok else missing).append(f"[{kind}] {label[:50]}")

    for ev in dens["concrete_events"]:
        ok, d = event_covered(ev, scene_text, cfg, syn)
        _push("event", _as_text(ev), ok, d)
    for it in dens["named_interactions"]:
        ok, d = interaction_covered(it, scene_text, cfg, syn)
        _push("interaction", _as_text(it), ok, d)
    for rv in dens["info_reveal_points"]:
        ok, d = reveal_covered(rv, scene_text, syn)
        _push("reveal", _as_text(rv), ok, d)
    # CC round-16 P0-3：动作闭环（仅标注 action_chain 的 beat；单独成行，缺环可定点重生）
    for ch in evaluate_action_chains(bp, scene_text, syn):
        label = (f"{ch['label']}（动作闭环需完整演到：{'→'.join(ch['chain'])}；"
                 f"当前缺少：{'、'.join(ch['missing']) or '无'}）") if not ch["ok"] else ch["label"]
        _push("action_chain", label, ch["ok"],
              {"hit": ch["hit"], "missing": ch["missing"], "chain": ch["chain"]})

    total = len(rows)
    ratio = (len(covered) / total) if total else 1.0
    markers = cfg.get("interiority_markers") or []
    return {
        "ratio": ratio,
        "covered": covered,
        "missing": missing,
        "total": total,
        "covered_count": len(covered),
        "interiority": any(m in scene_text for m in markers),
        "rows": rows,
    }

