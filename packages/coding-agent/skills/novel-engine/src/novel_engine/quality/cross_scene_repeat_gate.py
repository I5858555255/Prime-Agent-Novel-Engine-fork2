# -*- coding: utf-8 -*-
"""CC round-14 P0-1：跨场景重复确定性门（assembly 后、reviewer 前，零 LLM）。

两层检测：
- 句级：去标点/功能字归一后，跨场景做 4 字 n-gram Jaccard，>=sentence_similarity 判近逐字重复，
  确定性删除“后出现”的整句、保留首现场景；删后低于 length_min 交有界扩写（被删意象作负例）。
- 意象级：每场景抽“特定意象名词”（排除通用环境词/人名/地名/代词），跨场景共享 >=shared_images
  判素材搬运，交定点重生后出现场景，负例 prompt 列出已用意象。

任务卡可声明 intentional_recurring_motifs（刻意伏笔回响），白名单跳过；单个意象重合不触发，
为首尾合理呼应留空间。
"""
from __future__ import annotations

import json
import re
from itertools import combinations
from pathlib import Path

_DEFAULT_CFG = {
    "sentence_similarity": 0.6,
    "sentence_ngram": 4,
    "sentence_min_norm_len": 8,
    "shared_images": 2,
    "generic_env_terms": [],
    "generic_noun_suffixes": [],
}
_SENT_SPLIT = re.compile(r"(?<=[。！？!?…])")
_PUNCT = re.compile(r"[\s，。！？!?…、；：“”‘’\"'‘’（）()《》〈〉,\.\-—~·:;0-9a-zA-Z]")


def load_repeat_config(root: str | Path) -> dict:
    cfg = dict(_DEFAULT_CFG)
    fp = Path(root) / "config" / "cross_scene_repeat.json"
    if fp.exists():
        try:
            cfg.update(json.loads(fp.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def split_sentences(text: str) -> list[str]:
    out = []
    for chunk in _SENT_SPLIT.split(text or ""):
        s = chunk.strip()
        if s:
            out.append(s)
    return out


def _normalize_sentence(sent: str, function_chars: str = "") -> str:
    s = _PUNCT.sub("", sent)
    if function_chars:
        s = "".join(ch for ch in s if ch not in set(function_chars))
    return s


def _ngrams(s: str, n: int) -> set[str]:
    return {s[i:i + n] for i in range(len(s) - n + 1)} if len(s) >= n else ({s} if s else set())


def ngram_jaccard(a: str, b: str, n: int = 4) -> float:
    A, B = _ngrams(a, n), _ngrams(b, n)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def longest_common_substring(a: str, b: str) -> int:
    """两串最长公共连续子串长度（DP，按较短串滚动）。"""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for ca in a:
        cur = [0] * (len(b) + 1)
        for j, cb in enumerate(b, 1):
            if ca == cb:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def recurring_motif_whitelist(task_card: dict) -> set[str]:
    out = set()
    for key in ("intentional_recurring_motifs", "recurring_motifs"):
        v = (task_card or {}).get(key)
        if isinstance(v, list):
            out.update(str(x).strip() for x in v if str(x).strip())
    # 各场景蓝图内声明的 motif 一并收集
    for bp in (task_card or {}).get("scene_blueprints", []) or []:
        if isinstance(bp, dict):
            v = bp.get("intentional_recurring_motifs")
            if isinstance(v, list):
                out.update(str(x).strip() for x in v if str(x).strip())
    return out


def detect_sentence_repeat(scenes: list, cfg: dict, function_chars: str = "",
                           whitelist: set[str] | None = None) -> list[dict]:
    """scenes: 含 scene_id / scene_text 的对象或 dict 列表。返回跨场景近逐字句对。"""
    n = int(cfg.get("sentence_ngram", 4))
    thr = float(cfg.get("sentence_similarity", 0.6))
    sub_len = int(cfg.get("shared_substring_len", 12))
    minlen = int(cfg.get("sentence_min_norm_len", 8))
    whitelist = whitelist or set()
    pool = []  # (sid, raw, norm)
    for sc in scenes:
        sid, txt = _sid_text(sc)
        for raw in split_sentences(txt):
            norm = _normalize_sentence(raw, function_chars)
            if len(norm) >= minlen and not any(m in raw for m in whitelist):
                pool.append((sid, raw, norm))
    violations = []
    for i in range(len(pool)):
        sid1, raw1, n1 = pool[i]
        for j in range(i + 1, len(pool)):
            sid2, raw2, n2 = pool[j]
            if sid1 == sid2:
                continue
            sim = ngram_jaccard(n1, n2, n)
            lcs = longest_common_substring(n1, n2)
            if sim + 1e-9 >= thr or lcs >= sub_len:
                violations.append({
                    "scenes": [sid1, sid2], "sentences": [raw1, raw2],
                    "similarity": round(sim, 3), "lcs": lcs, "kind": "sentence",
                })
    return violations


def extract_specific_images(text: str, cfg: dict, exclude_terms: set[str] | None = None) -> set[str]:
    """具体物品/意象名词（2-4字），排除通用环境词、人名地名、时间、量词化泛指。"""
    stop = set(cfg.get("generic_env_terms", []) or [])
    stop |= set(cfg.get("body_part_terms", []) or [])
    stop |= set(cfg.get("setting_object_terms", []) or [])
    stop |= set(cfg.get("setting_role_terms", []) or [])
    stop |= set(cfg.get("abstract_noun_terms", []) or [])
    suffixes = tuple(cfg.get("generic_noun_suffixes", []) or [])
    exclude = set(exclude_terms or set())
    images = set()
    try:
        from .density_gate import _posseg
        tagged = _posseg().cut(text or "")
    except Exception:
        return images
    for w, flag in tagged:
        w = w.strip()
        if len(w) < 2 or len(w) > 4:
            continue
        if flag not in ("n", "nz", "i"):  # 仅普通/具体名词，排除 nr人名 ns地名 nt机构 t时间
            continue
        if w in stop or w in exclude:
            continue
        if suffixes and w.endswith(suffixes):
            continue
        images.add(w)
    return images


def _card_exclude_terms(task_card: dict) -> set[str]:
    """人名/地名/主角等不应算‘搬运意象’的专名。"""
    ex: set[str] = set()
    for key in ("character_names", "characters_all"):
        v = (task_card or {}).get(key)
        if isinstance(v, list):
            ex.update(str(x) for x in v)
    for bp in (task_card or {}).get("scene_blueprints", []) or []:
        if not isinstance(bp, dict):
            continue
        loc = str(bp.get("location", "") or "")
        if loc:
            ex.add(loc)
        for it in bp.get("named_interactions", []) or []:
            if isinstance(it, dict):
                for c in it.get("characters", []) or []:
                    ex.add(str(c))
    return ex


def _canonical_location_group(loc: str, keywords: list[str], abstract_markers: list[str]) -> str | None:
    """把 location 归一到稳定地点簇：命中配置关键词即用该词；抽象过渡场景返回 None（不参与意象门）。"""
    loc = (loc or "").strip()
    if any(m in loc for m in abstract_markers):
        return None
    for kw in keywords:
        if kw and kw in loc:
            return kw
    for sep in ["·", "→", "（", "("]:
        if sep in loc:
            loc = loc.split(sep)[0].strip()
    return loc or None


def _scene_location_groups(task_card: dict, cfg: dict | None = None) -> dict:
    """scene_id -> 地点簇(可能 None=抽象过渡场，跳过意象门)。按关键词归并“雾隐村外·…”等同簇变体。"""
    keywords = (cfg or {}).get("location_group_keywords", []) or []
    abstract_markers = (cfg or {}).get("abstract_location_markers", []) or []
    groups: dict[int, str] = {}
    for bp in (task_card or {}).get("scene_blueprints", []) or []:
        if not isinstance(bp, dict):
            continue
        sid = bp.get("scene_id", bp.get("scene_number"))
        try:
            sid = int(sid)
        except (TypeError, ValueError):
            continue
        groups[sid] = _canonical_location_group(
            str(bp.get("location", "") or ""), keywords, abstract_markers)
    return groups


def detect_image_reuse(scenes: list, cfg: dict, task_card: dict | None = None,
                       whitelist: set[str] | None = None) -> list[dict]:
    need = int(cfg.get("shared_images", 2))
    whitelist = whitelist or set()
    exclude = _card_exclude_terms(task_card or {}) | whitelist
    per = {}
    for sc in scenes:
        sid, txt = _sid_text(sc)
        imgs = extract_specific_images(txt, cfg, exclude_terms=exclude)
        imgs = {x for x in imgs if x not in whitelist}
        per[sid] = imgs
    groups = _scene_location_groups(task_card or {}, cfg)
    violations = []
    for sid1, sid2 in combinations(sorted(per), 2):
        # 抽象过渡场（无实体地点）不参与意象门；同一地点簇内共享道具/环境具象词属合理。
        g1, g2 = groups.get(sid1), groups.get(sid2)
        if g1 is None or g2 is None:
            continue
        if g1 == g2:
            continue
        shared = per[sid1] & per[sid2]
        # 白名单子串（如 motif 是“灼烧”，正文“灼烧感”也跳过）
        shared = {x for x in shared if not any(m and m in x for m in whitelist)}
        if len(shared) >= need:
            violations.append({"scenes": [sid1, sid2], "shared_images": sorted(shared), "kind": "image"})
    return violations


def _sid_text(sc) -> tuple[int, str]:
    if isinstance(sc, dict):
        return int(sc.get("scene_id", 0) or 0), str(sc.get("scene_text", "") or "")
    return int(getattr(sc, "scene_id", 0) or 0), str(getattr(sc, "scene_text", "") or "")


def repeated_sentence_set(violations: list[dict]) -> set[str]:
    """需要删除的‘后出现’整句（保留首现）。"""
    return {v["sentences"][1] for v in violations if v.get("kind") == "sentence"}


def excise_repeated_sentences(text: str, drop: set[str]) -> str:
    """从单场景文本中删除命中的重复整句，按原段落重拼，不留残句。"""
    if not drop:
        return text
    kept_paras = []
    for para in (text or "").split("\n"):
        kept = [s for s in split_sentences(para) if s.strip() not in drop]
        np = "".join(kept)
        if np.strip():
            kept_paras.append(np)
    return "\n".join(kept_paras)


def image_reuse_directive(violations: list[dict], scene_sid: int) -> str:
    lines = ["本场景与其它场景重复使用了以下具体意象，疑似搬运已有素材，必须改用本场景独有的细节："]
    for v in violations:
        if v.get("kind") == "image" and scene_sid in v["scenes"]:
            lines.append("- 已在别处使用、本场景禁用的意象：" + "、".join(v["shared_images"]))
    lines.append("要求：只使用本场景 location/在场角色/narrative_time 独有的物品与环境细节，"
                 "换一组全新的具体意象来演同一件事；严禁复述或改写上面这些意象。")
    return "\n".join(lines)
