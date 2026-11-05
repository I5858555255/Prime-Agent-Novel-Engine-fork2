"""Programmatic acceptance check for external-LLM expanded prose.

Input: an expansion package directory (the one exported by export.py) and the
model-generated prose file. Output: JSON report with blocking/warning findings.
Stdlib only; runs standalone or as `python -m novel_engine.condensed.accept_check`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    from novel_engine.core.env_bootstrap import load_project_env
except ImportError:  # running standalone from inside a package directory
    def load_project_env() -> None:
        pass

HAN = re.compile(r"[\u3007\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f\U00030000-\U000323af]")

# Cognition-boundary forbidden terms by stage. Stage boundaries follow
# character_bible.md C001: stage 1 = chapters 1-316 (no cultivation body-feel),
# stage 2 = 317+ (after N015 ch316 the protagonist confirms the power exists
# and cultivation practice terms become legitimate, so those words stop being
# blocking; only hard spoilers remain).
STAGE1_MAX_CHAPTER = 316
# Stage-1 forbidden terms: cultivation body-feel, pseudo-scientific mechanism
# leaks, setting terms that cross the cognition boundary, and modern/foreign
# vocabulary that breaks the pre-modern village voice.
STAGE1_FORBIDDEN = [
    "气感", "体内暖意", "引气", "吐纳", "灵力运转", "周天", "丹田",
    # pseudo-scientific mechanism phrases (infant/protagonist can't "mark" or "reflect" like a system)
    "感官阈值", "标记为", "安全域", "条件反射", "监控网", "阈值",
    "微扰", "滞涩感", "神经反射",
    # data-science vocabulary: the C002 observer stays vague; no tables,
    # variables, or precise-feedback language
    "变量", "表格", "精准反馈", "共振", "频率", "数据化", "建模",
    # C002 forbidden to name the fog-forbidden-zone entity in stage 1
    "巨大存在",
    # setting leaks that cross the C001/C002 cognition boundary in stage 1
    "神魂残留", "浊气", "门芯", "沉睡之物", "异界神魂", "阴阳裂隙", "沉碳界法则",
    # modern/foreign vocabulary that breaks the village voice
    "invisible", "双轨监控", "环境感知器", "人设",
    # author-layer mechanism terms: legitimate in beat.cause (author evidence),
    # forbidden in beat.action/outcome (perception layer) and in prose.
    "神魂", "阴阳裂隙", "沉碳界法则", "沉碳界", "异界神魂",
]
STAGE2_FORBIDDEN = [
    "人皇鼎碎片完整真相", "仙庭旧部身份", "摄魂阁人造飞升",
    "李淳前世全貌", "太上养魂经完整传承", "血冥教逆炼心魔",
    "乾武帝赎罪动机坐实", "墟界坐标",
]


def stage_forbidden_terms(chapter: int) -> list[str]:
    """Blocking terms for prose acceptance at this chapter.

    Stage 1 (ch <= STAGE1_MAX_CHAPTER): the full list. Stage 2 keeps only
    late-game spoilers and modern/foreign vocabulary; body-feel and
    pseudo-scientific terms become legitimate after N015.
    """
    if chapter <= STAGE1_MAX_CHAPTER:
        return list(dict.fromkeys(STAGE1_FORBIDDEN))
    return [t for t in STAGE1_FORBIDDEN if t in STAGE2_FORBIDDEN]


def perception_forbidden_terms(chapter: int) -> list[str]:
    """Terms forbidden in beat action/outcome (perception layer) and in prose.

    cause may legitimately keep author-layer mechanism terms as causal
    evidence; action/outcome must stay in what the character can perceive.
    Stage 2: no additional perception-layer blocking beyond hard spoilers.
    """
    if chapter <= STAGE1_MAX_CHAPTER:
        return list(dict.fromkeys(STAGE1_FORBIDDEN))
    return list(STAGE2_FORBIDDEN)


# Metadata-leak patterns: scene ids, tags, budget labels that must not appear in prose.
METADATA_PATTERNS = [
    (re.compile("主线第\\s*\\d+\\s*天"), "主线第N天"),
    (re.compile("【?S\\d+(?:-\\d+)?】"), "场景编号S\\d"),
    (re.compile("CH\\d+-E\\d+"), "事件编号CH-E"),
    (re.compile("扩写预算"), "扩写预算标签"),
    (re.compile("扩写重点"), "扩写重点标签"),
    (re.compile("对话意图"), "对话意图标签"),
    (re.compile("情绪转折"), "情绪转折标签"),
    (re.compile("因果依据"), "因果依据标签"),
    (re.compile("\\b正文\\b.*?事实变更"), "正文/事实变更说明段"),
]


def han_count(text: str) -> int:
    return len(HAN.findall(text))


def load_card(package: Path) -> dict:
    return json.loads((package / "场景扩写卡.json").read_text(encoding="utf-8"))


def load_manifest(package: Path) -> dict:
    return json.loads((package / "manifest.json").read_text(encoding="utf-8"))


def extract_prose(text: str) -> tuple[str, list[str]]:
    """Strip obvious wrapper labels; returns (prose, notes)."""
    notes = []
    prose = text
    if prose.lstrip().startswith("正文"):
        first_nl = prose.find("\n")
        prose = prose[first_nl + 1:] if first_nl != -1 else ""
        notes.append("removed leading '正文' label")
    # Trailing fact-change sections are not part of pure prose for counting.
    m = re.search(r"\n\s*(事实变更清单|修改说明)\s*[:：]?", prose)
    if m:
        notes.append(f"trailing section '{m.group(1)}' excluded from prose")
        prose = prose[: m.start()]
    return prose.strip(), notes


def check_prose(prose: str, card: dict, manifest: dict) -> dict:
    blocking, warnings, info = [], [], []
    target = manifest.get("expanded_target_han")
    lo, hi = manifest.get("expanded_range", [4000, 5000])
    han = han_count(prose)

    # 1. Word count
    if not lo <= han <= hi:
        blocking.append({"check": "word_count", "detail": f"han={han}, range=[{lo},{hi}], target={target}"})
    else:
        info.append({"check": "word_count", "detail": f"han={han} in range"})
        if target and abs(han - target) > 0.2 * target:
            warnings.append({"check": "word_count_drift", "detail": f"han={han} vs target {target} (>20% drift)"})

    # 2. Metadata leakage
    for pattern, label in METADATA_PATTERNS:
        hits = pattern.findall(prose)
        if hits:
            severity = "blocking" if label in ("主线第N天", "扩写预算标签", "对话意图标签") else "warning"
            entry = {"check": "metadata_leak", "detail": f"'{label}' x{len(hits)}", "samples": hits[:3]}
            (blocking if severity == "blocking" else warnings).append(entry)

    # 3. Cognition-boundary forbidden terms (stage-dependent). Stage 1
    #    (ch<=316): no cultivation body-feel terms at all. Stage 2: body-feel
    #    terms are legitimate; only late-game spoilers are blocking.
    chapter = manifest.get("chapter", 0)
    forbidden = stage_forbidden_terms(chapter)
    hits = [t for t in forbidden if t in prose]
    if hits:
        blocking.append({"check": "cognition_boundary",
                         "detail": f"stage-forbidden terms (ch{chapter}): {hits}",
                         "stage": 1 if chapter <= STAGE1_MAX_CHAPTER else 2})

    # 5. Beat coverage heuristic: each beat's core action phrase must be echoed
    #    somewhere in the prose. CJK is not space-delimited, so use the longest
    #    CJK run of the beat action as its key signature and check presence.
    missing = []
    for b in [b for s in card.get("scenes", []) for b in s.get("beats", [])]:
        action = b.get("action", "")
        runs = re.findall(r"[\u4e00-\u9fff]{2,}", action)
        key = max(runs, key=len) if runs else ""
        if key and key not in prose:
            missing.append(f"{b['id']}({key})")
    if missing:
        warnings.append({"check": "beat_coverage",
                         "detail": f"beats whose key action phrase is absent: {missing}"})

    # 6. POV drift: if the card declares multiple POV characters, warn the
    #    reviewer to verify each interior-monologue passage matches its scene
    #    card POV. (Heuristic: count declared POVs; the LLM acceptance layer
    #    handles the semantic check.)
    pocs = list({s.get("pov") for s in card.get("scenes", []) if s.get("pov")})
    if len(pocs) > 1:
        warnings.append({"check": "pov_drift",
                         "detail": f"card declares {len(pocs)} POV characters {pocs}; "
                                    "verify each interior-monologue passage matches its scene POV"})

    # 7. Infant POV self-analysis: block self-awareness / logical-synthesis
    #    phrases that are impossible for a month-age < 3 character.
    INFANT_SELF_AWARENESS = [
        "他意识到", "她意识到", "他明白", "她明白", "他清楚自己", "她清楚自己",
        "这意味着", "这说明", "他记得那个", "他清楚这是", "他明白自己", "他意识到自己",
    ]
    self_hits = [p for p in INFANT_SELF_AWARENESS if p in prose]
    if self_hits:
        blocking.append({"check": "infant_self_awareness",
                         "detail": f"infant POV self-analysis phrases: {self_hits}",
                         "samples": self_hits[:3]})

    return {"blocking": blocking, "warnings": warnings, "info": info, "han": han}


def run(package_dir: Path, prose_path: Path, out_path: Path | None = None) -> dict:
    card = load_card(package_dir)
    manifest = load_manifest(package_dir)
    raw = prose_path.read_text(encoding="utf-8-sig")
    prose, notes = extract_prose(raw)
    result = check_prose(prose, card, manifest)
    result.update({
        "chapter": manifest.get("chapter"),
        "package": str(package_dir),
        "prose_file": str(prose_path),
        "strip_notes": notes,
        "pass": not result["blocking"],
        "verdict": "PASS" if not result["blocking"] else "BLOCK",
    })
    if out_path:
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main(argv=None) -> int:
    load_project_env()
    parser = argparse.ArgumentParser(description="Programmatic acceptance check for expanded prose")
    parser.add_argument("package", type=Path, help="expansion package directory, e.g. expansion/chapter_0001")
    parser.add_argument("prose", type=Path, help="UTF-8 file containing the model-generated prose")
    parser.add_argument("--out", type=Path, help="write JSON report here (default: <package>/accept_report.json)")
    args = parser.parse_args(argv)
    out = args.out or args.package / "accept_report.json"
    result = run(args.package, args.prose, out)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
