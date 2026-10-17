"""audit_mid_outline: 中纲草稿前置校验（生成后、交付 Director 前，必须执行）。

对 config/planning/mid_outline_*.json 的每条章级意图做自动判定：
  0) 条款判定化覆盖审计（bible 无人值守加固）：当前卷 forbidden 未全部条款化
     → 批次全部挂起 pending_human，防无人值守漏判（V03-V10 未条款化会卡关）；
  1) 判定规则库（config/planning/decision_rules.json，bible/decision_rules.md 的
     条款化实现）逐条匹配——rule_applies 卷范围过滤后，
     forbidden 类 → needs_regenerate；allowed/hint 类 → approved；
     R11 密度类 → 批次级密度统计后收紧；
  2) 未命中规则 → A 路线硬红线 + concept_unlocks 判定（scope_gate）；
  3) boundary_note 自带"需人工确认"→ pending_human；
  4) F 编号锚点校验：boundary_note 的 F00X 与中纲文本锚点不匹配 → pending_human
     （foreshadow_anchors.json，防伏笔编号标错）；
  5) author_intent 当前卷 forbidden（子串匹配，剥离条款内正例段）；
  6) 新角色检测：未登记专名（character_registry.json）→ pending_human；
  7) 默认通过。概念解锁规划状态（unlock_schedule）输出提醒。

判定结果写回每条 entry.status（approved / pending_human / needs_regenerate），
供 chapter_director 逐条门禁读取（meta.status=draft 时逐条 approved 才可用，
实现"挂起单章不阻塞批次"）。

Usage (run from src/):
    python -m novel_engine.scripts.audit_mid_outline <path_to_mid_outline.json> [--no-write]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

_SRC_ROOT = str(Path(__file__).resolve().parents[2])
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

ROOT = Path(__file__).resolve().parents[1]

_DENSITY_WINDOW = 30  # R11 密度窗口：同一伏笔 30 章内暗示上限
_DENSITY_MAX = 3

# A 路线硬红线模式（第一阶段 ch1-316：陆烬禁修炼样式动作；
# 呼吸法仅陈老根练/提及，陆烬最多旁观或被告知结果）。
# 主语判定用"紧邻"而非宽间隔，避免把"陆烬旁观陈老根练习"误判为陆烬修炼。
_A_ROUTE_PATTERNS = [
    (r"陆烬(亲自|独自|也跟着|也|在山林间|在屋里|晚间|夜晚|白天|偷偷|默默)?(练习|修炼|运转|引气|吐纳|打坐|调息|催动)",
     "陆烬亲自出现修炼样式动作"),
    (r"陆烬.{0,4}(照着|学着|模仿|跟着).{0,10}(练|练习|呼吸法|吐纳)",
     "陆烬模仿修炼样式动作"),
    (r"陆烬.{0,12}体内.{0,20}(运转|流转|共鸣|气感|气流|暖意|灵气|气机)", "陆烬体内出现气机运转/共鸣"),
    (r"(练|修炼|运转|引气|吐纳|打坐|调息)的.{0,8}(是|主角|他)", "动作主语指回陆烬"),
]

_STATUS_ORDER = {"needs_regenerate": 0, "pending_human": 1, "approved": 2}

# 否定语境豁免：forbidden 规则命中"解读/用途/秘密"类词时，若其紧邻前文是否定式
# （无法解读/未能参透等），视为"未坐实解读"而非"完成解读"——避免 R14 等规则把
# "无法解读，暂且封存"误判为用途完全解读（ch559/ch573 实证）。
_NEGATED_DISCLOSURE_RE = re.compile(
    r"(?:无法|不能|难以|未能|尚未|不曾|未解|未明|不知|看不|参不透).{0,5}"
    r"(?:解读|全貌|真正用途|用途|秘密|是钥匙|是密码|为阵法)"
)


def _is_negated_disclosure(snippet: str) -> bool:
    """snippet 中是否出现"否定词 + 解读/用途类词"的否定语境。"""
    return bool(_NEGATED_DISCLOSURE_RE.search(snippet or ""))


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[error] failed to parse {path}: {exc}")
        return {}


def _load_decision_rules() -> list[dict]:
    p = ROOT / "config" / "planning" / "decision_rules.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("rules", [])
    except Exception:
        return []


def decide_entry(
    event: str, chars: list, note: str, chapter: int,
    rules: list[dict], joined: str | None = None, vol_id: str = "",
) -> tuple[str, str, str | None]:
    """对单条章级意图做规则判定。

    返回 (status, reason, rule_id|None)。
    status: approved / pending_human / needs_regenerate。
    rule_applies 卷范围判定：rule 不在当前卷/章节窗口内则跳过（防止旧卷规则误伤新卷）。
    注意：R11 密度类在此不判定（返回特殊标记），由批次级统计处理。
    """
    if joined is None:
        joined = f"{event} {', '.join(str(c) for c in chars)}"
    for r in rules:
        applies = r.get("rule_applies")
        if applies:
            if "vols" in applies and vol_id and vol_id not in applies["vols"]:
                continue
            if "chapter_range" in applies:
                lo, hi = applies["chapter_range"]
                if not (lo <= chapter <= hi):
                    continue
        for pat in r.get("patterns", []):
            try:
                m = re.search(pat, joined)
            except re.error:
                continue
            if not m:
                continue
            scale = r.get("scale", "forbidden")
            rid = r.get("id", "")
            if scale == "forbidden" and _is_negated_disclosure(m.group(0)):
                # 否定语境（无法解读/未能参透等）未坐实 → 跳过该 pattern，继续查其余规则
                continue
            if scale == "forbidden":
                return "needs_regenerate", f"规则{rid}: {r.get('name', '')}", rid
            if r.get("action") == "density_count":
                return "__density__", f"规则{rid}: {r.get('name', '')}", rid
            # allowed / hint → approved（hint 类附规则 note 供后续追踪）
            return "approved", f"规则{rid}: {r.get('name', '')}", rid
    return "", "", None


def _clause_lines(blk: str) -> list[str]:
    """取块内 forbidden 条款行（排除 chapter_range/theme 等配置行）。"""
    out = []
    for ln in blk.splitlines():
        s = ln.strip()
        if not s.startswith("-"):
            continue
        body = s.lstrip("- ").strip()
        if body.startswith("chapter_range"):
            continue
        out.append(body.strip('"'))
    return out


def _forbidden_scan_text(clause: str) -> str:
    """条款只保留禁止语义部分：截断"正例（允许）"之后的内容，
    避免条款正文里的正例例句被子串匹配误判为禁止。"""
    for marker in ("正例（允许）", "正例（允许）:", "。正例"):
        idx = clause.find(marker)
        if idx != -1:
            return clause[:idx]
    return clause


def _current_volume_forbidden(author_intent: str, vid: str, chapter_num: int) -> list[str]:
    if not author_intent:
        return []
    for blk in re.split(r"^## ", author_intent, flags=re.M):
        m = re.match(r"第[一二三四五六七八九十]+阶段[：:].*?（.*?第?(\d+)-(\d+)章?）", blk)
        if not m:
            continue
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo <= chapter_num <= hi:
            return _clause_lines(blk)
    return []


_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _current_volume_id(author_intent: str, chapter_num: int) -> str:
    """按章节号定位卷 id（V01/V02/…），供 rule_applies 卷范围判定。"""
    if not author_intent:
        return ""
    for blk in re.split(r"^## ", author_intent, flags=re.M):
        m = re.match(r"第([一二三四五六七八九十]+)阶段[：:].*?（.*?第?(\d+)-(\d+)章?）", blk)
        if not m:
            continue
        lo, hi = int(m.group(2)), int(m.group(3))
        if lo <= chapter_num <= hi:
            return f"V{_CN_NUM.get(m.group(1), 0):02d}"
    return ""


def _parse_intent_clauses(author_intent: str) -> dict[str, list[str]]:
    """解析 author_intent 各卷 forbidden 条款 → {vid: [clause,...]}（列表序=条款序号）。"""
    vols: dict[str, list[str]] = {}
    for blk in re.split(r"^## ", author_intent, flags=re.M):
        m = re.match(r"第([一二三四五六七八九十]+)阶段[：:].*?（.*?第?(\d+)-(\d+)章?）", blk)
        if not m:
            continue
        vid = f"V{_CN_NUM.get(m.group(1), 0):02d}"
        vols[vid] = _clause_lines(blk)
    return vols


def _coverage_audit(author_intent: str, rules: list[dict], vid: str) -> tuple[list[str], bool]:
    """条款判定化覆盖审计：该卷 forbidden N/M/K；未覆盖条款 → 批次挂起不静默放行。"""
    vols = _parse_intent_clauses(author_intent)
    if vid not in vols or not vols[vid]:
        return [], False
    clauses = vols[vid]
    covers = {c for r in rules for c in r.get("covers_clauses", [])}
    uncovered = [(i + 1, c) for i, c in enumerate(clauses)
                 if f"{vid}#{i + 1}" not in covers]
    msgs = [f"[覆盖审计] {vid}: forbidden {len(clauses)} 条，已判定化 "
            f"{len(clauses) - len(uncovered)} 条，未覆盖 {len(uncovered)} 条"]
    for i, c in uncovered:
        msgs.append(f"  - {vid}#{i}: {c[:70]}  — 未条款化，需补判例后重审")
    return msgs, bool(uncovered)


def _load_foreshadow_anchors() -> dict:
    """伏笔锚点视图：从权威注册表 config/foreshadow/registry.json 读取。

    返回 {引用标识: [锚点词,...]}——legacy F 编号（F001）与稳定语义 fid
    （f001_royal_heirlooms）都作为键，boundary_note 里任一引用均可解析到
    同一实体的锚点（双向解析，防编号错位）。旧文件 foreshadow_anchors.json
    已并入注册表并废弃（见 registry.json _comment）。
    """
    p = ROOT / "config" / "foreshadow" / "registry.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, list[str]] = {}
    for fs in data.get("foreshadows", []):
        if not isinstance(fs, dict):
            continue
        anchors = fs.get("anchors") or []
        if fs.get("id"):
            out[fs["id"]] = anchors
        if fs.get("fid"):
            out[fs["fid"]] = anchors
    return out


def _check_f_anchor(note: str, joined: str, anchors: dict) -> str | None:
    """boundary_note 含伏笔引用（F00X 或 fid）→ 校验该实体锚点词是否命中
    中纲文本；不匹配返回原因。F00X 与 fid 双向解析到同一实体（权威注册表）。"""
    if not anchors:
        return None
    for fid, words in anchors.items():
        if fid in note:
            if any(w in joined for w in words):
                return None
            return f"{fid} 锚点未命中（{'/'.join(words[:4])}…），疑似伏笔编号标错"
    return None


_IGNORE_ROLE_SUFFIX = ("叔", "伯", "娘", "婶", "掌柜", "村长", "里正",
                       "兵", "役", "官", "爷", "婆", "汉", "子", "人", "氏", "哥", "姐")
_IGNORE_ROLE_TAIL = ("村", "镇", "县", "城", "山", "店", "馆", "铺", "坊")


def _load_character_registry() -> tuple[set[str], set[str]]:
    p = ROOT / "config" / "planning" / "character_registry.json"
    if not p.exists():
        return set(), set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return (set(data.get("characters", [])), set(data.get("character_ids", [])))
    except Exception:
        return set(), set()


def _unregistered_proper_names(chars: list, registry: set[str], registry_ids: set[str]) -> list[str]:
    """中纲 characters 中未登记且疑似专名（非通用称谓/地名结尾）的角色。

    已登记的 C 编号（C001-C009）豁免；未知 C 编号（如 C010）与未登记专名报出。
    """
    out: list[str] = []
    for c in chars or []:
        s = str(c).strip()
        if not s or s in registry or s in registry_ids:
            continue
        if len(s) < 2 or len(s) > 5:
            continue
        if any(s.endswith(t) for t in _IGNORE_ROLE_SUFFIX):
            continue
        if any(s.endswith(t) for t in _IGNORE_ROLE_TAIL):
            continue
        out.append(s)
    return out


def _load_unlock_schedule() -> list[dict]:
    p = ROOT / "config" / "leak_terms" / "concept_unlocks.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("unlock_schedule", [])
    except Exception:
        return []


def _schedule_status(schedule: list[dict], vid: str) -> str | None:
    for s in schedule:
        if s.get("vol") == vid:
            return s.get("status", "unknown")
    return None  # 未规划


def _atomic_write_json(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix=".audit_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(path))
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit mid-level outline draft.")
    parser.add_argument("path", help="Path to mid_outline JSON (or 'auto' to scan config/planning/)")
    parser.add_argument("--no-write", action="store_true", help="只报告不写回 entry.status")
    args = parser.parse_args(argv)

    if args.path.lower() == "auto":
        candidates = sorted((ROOT / "config" / "planning").glob("mid_outline_*.json"))
        if not candidates:
            print("[error] no mid_outline_*.json found under config/planning/")
            return 2
        path = candidates[0]
    else:
        path = Path(args.path)
    data = _load_json(path)
    if not data:
        return 2

    entries = data.get("entries", {})
    meta = data.get("meta", {})
    if meta.get("status") != "draft":
        print(f"[info] meta.status={meta.get('status')} (expected 'draft' for audit)")

    rules = _load_decision_rules()
    if not rules:
        print("[warn] decision_rules.json 未加载，规则表判定跳过（仅 concept/forbidden/A 路线）")

    # 全局初始化（scope_gate 内部缓存）
    from novel_engine.quality.scope_gate import (
        detect_scope_violations,
        reset_concept_cache,
    )
    reset_concept_cache()

    author_intent = ""
    p = ROOT / "bible" / "author_intent.md"
    if p.exists():
        author_intent = p.read_text(encoding="utf-8")

    vid = str(meta.get("vid") or "")
    anchors = _load_foreshadow_anchors()
    registry, registry_ids = _load_character_registry()
    schedule = _load_unlock_schedule()

    # 覆盖审计（修复1）：当前卷 forbidden 未条款化 → 批次挂起
    cov_msgs, uncovered = _coverage_audit(author_intent, rules, vid)
    for m in cov_msgs:
        print(m)
    if uncovered:
        print(f"[卡关] {vid} 存在未条款化 forbidden 条款，批次全部挂起待 bible 完善")

    # 概念解锁规划检查（修复4）
    sched_status = _schedule_status(schedule, vid)
    if sched_status is None:
        print(f"[概念规划] {vid} 不在 concept_unlocks.unlock_schedule 中，需补卷级概念规划")
    elif sched_status != "covered":
        print(f"[概念规划] {vid} unlock_schedule.status={sched_status}（planned=已规划待验证/covered=已覆盖）")

    passed: list[int] = []
    human_confirm: list[tuple[int, str]] = []
    hard_blocked: list[tuple[int, str]] = []
    empty: list[int] = []
    density_chapters: list[int] = []
    status_map: dict[int, str] = {}

    for ch_str, entry in sorted(entries.items()):
        chapter = int(ch_str)
        if uncovered:
            status_map[chapter] = "pending_human"
            human_confirm.append((chapter, f"覆盖审计卡关：{vid} 有未条款化 forbidden，批次挂起"))
            continue
        event = str(entry.get("core_event", "") or "").strip()
        chars = entry.get("characters") or []
        note = entry.get("boundary_note") or ""
        joined = f"{event} {', '.join(str(c) for c in chars)}"

        # 1) 空条目（生成失败的占位）
        if not event or event == "需人工确认" or "本批生成失败" in event:
            empty.append(chapter)
            status_map[chapter] = "needs_regenerate"
            human_confirm.append((chapter, "中纲条目为空/本批生成失败，需重跑"))
            continue

        # 2) 判定规则库（bible 条款化，最高优先；rule_applies 卷范围生效）
        status, why, rid = decide_entry(event, chars, note, chapter, rules, joined, vid)
        if status == "__density__":
            density_chapters.append(chapter)
            status_map[chapter] = "approved"
            passed.append(chapter)
            continue
        if status:
            status_map[chapter] = status
            if status == "approved":
                passed.append(chapter)
            else:
                hard_blocked.append((chapter, why))
            continue

        # 3) A 路线硬红线
        a_route_hit = None
        for pat, why in _A_ROUTE_PATTERNS:
            if re.search(pat, joined):
                a_route_hit = why
                break
        if a_route_hit:
            status_map[chapter] = "needs_regenerate"
            hard_blocked.append((chapter, f"撞 A 路线硬红线: {a_route_hit}"))
            continue

        # 4) 自带 boundary_note 标注
        if "需人工确认" in str(note):
            status_map[chapter] = "pending_human"
            human_confirm.append((chapter, str(note)))
            continue

        # 4.5) F 编号锚点校验（修复2）：boundary_note 的 F00X 与中纲文本锚点匹配
        if anchors and note:
            f_issue = _check_f_anchor(str(note), joined, anchors)
            if f_issue:
                status_map[chapter] = "pending_human"
                human_confirm.append((chapter, f"伏笔编号校验: {f_issue}"))
                continue

        # 5) concept_unlocks 判定
        result = detect_scope_violations(joined, chapter, {}, ROOT)
        if result["hard"]:
            terms = "; ".join(f"{h['term']}({h['sentence'][:30]})" for h in result["hard"][:5])
            status_map[chapter] = "needs_regenerate"
            hard_blocked.append((chapter, f"撞 concept 硬锁: {terms}"))
            continue
        if result["soft"]:
            terms = "; ".join(f"{h['term']}({h['sentence'][:30]})" for h in result["soft"][:5])
            status_map[chapter] = "pending_human"
            human_confirm.append((chapter, f"soft 命中: {terms}"))
            continue

        # 6) forbidden 条款命中（子串匹配，剥离条款内正例段）
        forbidden = _current_volume_forbidden(author_intent, meta.get("vid", ""), chapter)
        hits = [f for f in forbidden if f and _forbidden_scan_text(f) in joined]
        if hits:
            status_map[chapter] = "needs_regenerate"
            hard_blocked.append((chapter, f"撞 author_intent forbidden: {hits[:3]}"))
            continue

        # 6.5) 新角色检测（修复5）：未登记专名（非通用称谓/地名结尾）→ pending_human
        new_names = _unregistered_proper_names(chars, registry, registry_ids)
        if new_names:
            status_map[chapter] = "pending_human"
            human_confirm.append((chapter, f"新角色未登记: {'、'.join(new_names)}"))
            continue

        # 7) 默认通过
        status_map[chapter] = "approved"
        passed.append(chapter)

    # R11 密度批次级统计：同一伏笔 30 章窗口内暗示 >3 次 → 收紧为 pending_human
    density_flagged: list[int] = []
    if density_chapters:
        for ch in sorted(density_chapters):
            window = [c for c in density_chapters if ch - _DENSITY_WINDOW < c <= ch]
            if len(window) > _DENSITY_MAX:
                density_flagged.append(ch)
                status_map[ch] = "pending_human"
        if density_flagged:
            # 从 passed 移除
            passed = [c for c in passed if c not in set(density_flagged)]
            human_confirm.append(
                (density_flagged[0], f"迷雾/禁区氛围密度超限（30 章内暗示 >{_DENSITY_MAX} 次）: {sorted(density_flagged)}")
            )

    # 写回 entry.status（原子写）
    if not args.no_write:
        for ch_str, entry in entries.items():
            st = status_map.get(int(ch_str))
            if st is not None:
                entry["status"] = st
        if "audit" not in meta:
            meta["audit"] = {}
        meta["audit"]["last_run"] = True
        _atomic_write_json(path, data)
        print(f"[write] 已写回 entry.status 到 {path.name}")

    print(f"=== 中纲草稿校验报告: {path.name} ===")
    print(f"条目总数: {len(entries)}")
    print(f"a) 校验通过(approved): {len(passed)} 章 {sorted(passed)}")
    print(f"b) 需人工确认(pending_human): {len(human_confirm)} 章")
    for ch, why in human_confirm:
        print(f"   - ch{ch}: {why}")
    print(f"c) 直接撞锁/撞forbidden(needs_regenerate): {len(hard_blocked)} 章")
    for ch, why in hard_blocked:
        print(f"   - ch{ch}: {why}")
    if empty:
        print(f"[注意] 其中 {len(empty)} 章为空条目: {sorted(empty)}")
    if density_flagged:
        print(f"[密度] 迷雾氛围密度收紧: {sorted(density_flagged)}")

    # 退出码：有 needs_regenerate → 1（需重生成）；空条目 → 2；否则 0
    if hard_blocked:
        return 1
    if empty:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
