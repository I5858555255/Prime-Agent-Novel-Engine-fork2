# -*- coding: utf-8 -*-
"""CC28 批B 3c：同章同角色多称谓裸切检测（零 LLM）。

r40 ch0 真硬伤：canonical C001 转生本名“李淳”、重生后名“陆烬”，同章叙述里前半用
“李淳”、后半无任何解释直接切到“陆烬”，读者无法知道二者同一人。本门只在【叙述】中
检测同一 canonical 的多个称谓是否无“同指锚点”地裸切；对话中他人称呼（“陆烬！”）豁免，
出现“本名/转生后名/即/又名”等同指声明也豁免。

数据源（全部零 LLM、可离线）：
1) character_bible.md 标题里的 canonical 主名（### C001 陆烬 …）；
2) 本书硬编码转生别名组（可被 config 覆盖/扩展）；
3) config/planning/character_aliases.json（可选，{"groups": [[主名, 别名…], …]}）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_CJK = r"一-鿿"
_BIBLE_TITLE_RE = re.compile(rf"^###\s*C\d+\s*([{_CJK}]{{2,4}})")

# 本书硬事实：canonical 转生/化名关系。首名为主用名。
BUILTIN_ALIAS_GROUPS: list[list[str]] = [
    ["陆烬", "李淳"],
]

# 同指锚点关联词：两名在一句内共现且含任一，即视为作者已交代同一人（豁免裸切）。
_ANCHOR_HINT_RE = re.compile(
    r"本名|真名|又名|别名|化名|改名|取名|赐名|命名|唤作|叫做|叫作|自称|改称|"
    r"转世|转生|重生|借尸还魂|夺舍|便是|即是|乃是|原是|原来是|正是|此人便是|后世")

# 成对/成段中文对话引号（含直角引号），其内为人物台词，不参与“叙述裸切”统计。
_DIALOG_RE = re.compile(r"[“『「]([^”』」]*)[”』」]")
# 按句末标点切句，用于“同句共现 + 关联词”锚点判定。
_SENT_SPLIT_RE = re.compile(r"[。！？!?；;\n]")


def load_bible_primary_names(root) -> list[str]:
    """从 character_bible.md 抽取 canonical 主名（标题 ### Cnnn 名字）。"""
    names: list[str] = []
    for cand in [
        Path(root) / "bible" / "character_bible.md",
        Path(root) / "src" / "novel_engine" / "bible" / "character_bible.md",
    ]:
        if cand.exists():
            for line in cand.read_text(encoding="utf-8").splitlines():
                m = _BIBLE_TITLE_RE.match(line.strip())
                if m and m.group(1) not in names:
                    names.append(m.group(1))
            break
    return names


def load_alias_groups(root=None) -> list[list[str]]:
    """返回去重后的别名组（每组首名为主名）。bible 主名与内置/配置组合并。"""
    groups: list[list[str]] = [list(g) for g in BUILTIN_ALIAS_GROUPS]
    if root is not None:
        for nm in load_bible_primary_names(root):
            if not any(nm in g for g in groups):
                groups.append([nm])
        for cand in [
            Path(root) / "config" / "planning" / "character_aliases.json",
            Path(root) / "src" / "novel_engine" / "config" / "planning" / "character_aliases.json",
        ]:
            if cand.exists():
                try:
                    data = json.loads(cand.read_text(encoding="utf-8"))
                    for grp in data.get("groups", []) or []:
                        grp = [str(x).strip() for x in grp if str(x).strip()]
                        if len(grp) >= 2:
                            groups.append(grp)
                except (json.JSONDecodeError, OSError, TypeError):
                    pass
                break
    # 去重：同一主名只保留信息最全（最长）的一组
    merged: dict[str, list[str]] = {}
    for g in groups:
        primary = g[0]
        if primary not in merged or len(g) > len(merged[primary]):
            merged[primary] = g
    return [v for v in merged.values()]


def strip_dialogue(text: str) -> str:
    """移除对话引号内文本，得到纯叙述（含引号外提示语）。"""
    return _DIALOG_RE.sub(" ", text or "")


def has_identity_anchor(text: str, names: list[str]) -> bool:
    """全文是否存在把组内≥2个名字关联为同一人的锚点句。

    判据（满足其一）：同一句内出现≥2个组内名字，且该句含同指关联词；或两个不同名字
    物理相邻（间距 <= 10 字），覆盖“李淳，日后名陆烬”这类紧凑赐名式写法。
    """
    t = text or ""
    used = [n for n in names if n in t]
    if len(used) < 2:
        return False
    for sent in _SENT_SPLIT_RE.split(t):
        if sum(1 for n in used if n in sent) >= 2 and _ANCHOR_HINT_RE.search(sent):
            return True
    # 紧凑共现：任意两个不同名字在 10 字窗口内同现
    for i in range(len(used)):
        for j in range(i + 1, len(used)):
            a, b = used[i], used[j]
            for m in re.finditer(re.escape(a), t):
                s, e = max(0, m.start() - 10), min(len(t), m.end() + 10)
                if b in t[s:e]:
                    return True
    return False


def detect_bare_alias_switch(text: str, groups: list[list[str]] | None = None) -> dict:
    """检测同章叙述中的别名裸切。

    返回 {"issues": [{primary, used, dominant, switched_to}], "has_issue": bool}。
    仅当同一 canonical 有≥2个名字出现在【叙述】、且全文无同指锚点时才报。
    """
    groups = groups if groups is not None else load_alias_groups()
    narration = strip_dialogue(text)
    issues: list[dict] = []
    for grp in groups:
        if len(grp) < 2:
            continue
        counts = {n: narration.count(n) for n in grp}
        used = [n for n in grp if counts[n] > 0]
        if len(used) < 2:
            continue  # 叙述只用一个称谓（或仅出现在对话），不构成裸切
        if has_identity_anchor(text, grp):
            continue  # 作者已交代同一人
        dominant = max(used, key=lambda n: counts[n])
        switched = [n for n in used if n != dominant]
        issues.append({
            "primary": grp[0],
            "used": used,
            "counts": {n: counts[n] for n in used},
            "dominant": dominant,
            "switched_to": switched,
        })
    return {"has_issue": bool(issues), "issues": issues}
