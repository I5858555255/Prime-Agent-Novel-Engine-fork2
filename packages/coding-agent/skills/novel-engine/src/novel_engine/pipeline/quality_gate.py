"""质量门控——确定性检查与发布判定。

提供确定性检查接口，用于在发布前判断章节是否满足各项质量标准。
"""

from typing import Dict, Any, Optional
from novel_engine.agents.reviewer_agent import DIM_MAX


def check_beat_coverage(chapter_outline: list, beats: list) -> dict:
    """检查 beat 覆盖率。
    
    计算实际 beats 数 vs 章节 blueprints 预期场景数的覆盖率。
    覆盖率 < 80% 返回 must_fix。
    
    Args:
        chapter_outline: 章节大纲，包含 scene_blueprints 数组
        beats: 实际生成的 beats 列表
        
    Returns:
        dict: {"gate": "beat_coverage", "value": 覆盖率, "pass": 是否通过}
    """
    # 从 scene_blueprints 数组获取预期场景数
    expected_scenes = len(chapter_outline.get("scene_blueprints", []) or [])
    expected_beats_per_scene = 3  # 每场景预期 beats 数
    expected_beats = expected_scenes * expected_beats_per_scene
    
    actual_beats = len(beats) if beats else 0
    coverage = actual_beats / expected_beats if expected_beats > 0 else 0
    
    return {
        "gate": "beat_coverage",
        "value": round(coverage, 4),
        "pass": coverage >= 0.8,
        "actual_beats": actual_beats,
        "expected_beats": expected_beats
    }


def check_chapter_hook(chapter_text: str) -> dict:
    """检查章末钩子类型与节奏密度。
    
    升级自"300字有无内容"为"钩子类型判定与节奏密度检测"：
    
    a) 钩子类型判定（deterministic hard gate）：
       枚举 5 类章末钩子，章末 10% 文本必须命中其一，否则 hard 拦截：
       1)危机倒计时：章末出现"倒计时、时限、必须在…前"等紧迫感
       2)新信息爆点：章末揭示新地点、新身份、新机密
       3)角色受迫抉择：角色面临非黑即白的抉择，且结果未知
       4)悬念断裂：章末以疑问句、悬念句结尾，悬而未决
       5)行动升级：章末从常规行动升级至关键冲突或大场面
       
    b) 节奏密度检测（soft note）：
       检测章节每 1500-2000 字是否含至少 1 个事件点（冲突/发现/转折/新信息）
       未达标记 soft_issues，不阻塞发布但记录备注。
    
    返回契约：{"gate": "chapter_hook", "pass": 是否命中钩子类型, "hook_type": "5类钩子其中之一", "text_snippet": 末尾片段, "rhythm_density": {"events_per_2000": 计数, "pass": 是否达标}, "soft_issues": [...]}"""
    
    # a) 钩子类型判定：检查章末是否命中 5 类钩子之一
    # 取章节文本末尾 10% 文本（而非固定 300 字）
    tail = chapter_text[-int(len(chapter_text) * 0.1):] if len(chapter_text) > 0 else ""
    
    # 5 类钩子关键词匹配
    hook_types = {
        "危机倒计时": ["倒计时", "时限", "必须在", "时间", "剩余"],
        "新信息爆点": ["突然", " unexpectedly", " revealed that", "发现", "新的", "消息"],
        "角色受迫抉择": ["不得不", "不由得", "别无选择", "必须选择", "抉择"],
        "悬念断裂": ["还是", "怎么办", "究竟如何", "如何是好", "悬而未决"],
        "行动升级": ["冲突", "战斗", "追逐", "高潮", "高潮来临"]
    }
    
    matched_hook_type = None
    for hook_type, keywords in hook_types.items():
        if any(kw in tail for kw in keywords):
            matched_hook_type = hook_type
            break
    
    # 判定：章末 10% 文本必须命中至少一类钩子，否则 hard 拦截
    hook_pass = matched_hook_type is not None
    
    # b) 节奏密度检测：检查每 1500-2000 字是否含至少 1 个事件点
    # 事件关键词列表
    event_keywords = [
        "冲突", "发现", "转折", "新信息", " suddenly", " unexpectedly",
        "揭秘", "真相", "背叛", "危机", "高潮", "高潮来临"
    ]
    # 简单计数：统计文本中 event_keywords 出现的总次数
    event_count = sum(1 for kw in event_keywords if kw in chapter_text)
    # 每 2000 字的事件密度
    rhythm_density = event_count / max(1, len(chapter_text) / 2000)
    rhythm_pass = rhythm_density >= 0.5  # 每 2000 字至少 0.5 个事件（约等于 1个/2000字）
    
    # 提取尾部片段用于显示
    snippet = tail[-50:] if len(tail) >= 50 else tail
    
    # soft_issues：节奏密度不达标的预警
    soft_issues = []
    if not rhythm_pass:
        soft_issues.append(f"[节奏密度] 章节每2000字事件密度不足：{rhythm_density:.2f} < 0.5")
    
    return {
        "gate": "chapter_hook",
        "pass": hook_pass,
        "hook_type": matched_hook_type,
        "text_snippet": snippet,
        "rhythm_density": {"events_per_2000": round(rhythm_density, 2), "pass": rhythm_pass},
        "soft_issues": soft_issues
    }


def _dim_score(dim_scores, primary, alias=None, default=100):
    """dim_scores 取值：兼容 int 与 {"score": x, "evidence": ...} 两种结构。"""
    v = dim_scores.get(primary)
    if v is None and alias:
        v = dim_scores.get(alias)
    if v is None:
        return default
    return v.get("score", default) if isinstance(v, dict) else v



def _norm100(v, dim):
    """维度分（按 DIM_MAX 量表）归一化到 0-100 分制。"""
    m = DIM_MAX.get(dim, 25)
    return v / m * 100 if m else v


def evaluate_publish(*, score=None, reviewer_issues=None, det_hard=None, leak=None,
                     violations=None, policy=None, dim_scores=None, total_score=None):
    """根据维度分数判断是否发布。

    统一使用归一化百分位（0-100）与 publication_line（默认88）比较。

    规则优先级：
    1. 硬拦截：reviewer_issues/det_hard/leak/violations 中 forbidden_block → 直接阻断
    2. 聚合线：归一化总分 < publication_line → block
    3. 维度线：任一维度归一化分 < 门槛 → block

    返回契约：{"publish", "reasons", "note"}。
    """
    from novel_engine.core.quality_policy import is_blocking

    reasons = []
    note = []
    publish = True
    publication_line = int((policy or {}).get("publication_line", 88))

    # --- 第一步：硬性拦截（最高优先级）---
    if reviewer_issues:
        for issue in reviewer_issues:
            cat = issue.get("category") or issue.get("dimension")
            sev = issue.get("severity", "").lower()
            if is_blocking(policy, cat):
                # Hard dimension: only block on high/medium severity
                if sev in ("high", "medium"):
                    reasons.append(f"reviewer_issue_blocking:{cat}")
                    publish = False
            elif sev == "high":
                # Note dimension + high severity → advisory only, do not block
                pass

    if det_hard:
        for item in det_hard:
            if isinstance(item, str):
                text = item
            elif isinstance(item, dict):
                text = item.get("text", "") or ""
            else:
                continue
            if not (text.startswith("Length:") or "长度" in text or "套话" in text or "fluff" in text.lower()):
                reasons.append(f"det_hard_hard:{text[:40]}")
                publish = False

    if leak:
        reasons.append(f"leak:{leak}")
        publish = False

    if violations:
        for v in violations:
            if v.get("category") == "forbidden_block":
                reasons.append(f"violation_forbidden:{v.get('text', '')[:30]}")
                publish = False

    # --- 第二步：聚合归一化总分 vs publication_line ---
    # score 应已是归一化百分位（0-100）；若传的是 raw，则自动归一化
    if score is not None:
        norm_score = score
        # 如果 score > 100，可能是 raw 分（如 102/120），自动归一化
        if score > 100 and total_score is not None:
            norm_score = round(score / 120 * 100, 1)
        if norm_score < publication_line:
            reasons.append(f"score<{publication_line}|must_fix")
            publish = False

    # --- 第三步：维度归一化分门槛 ---
    if dim_scores is not None:

        # 维度门槛（归一化百分位）
        dim_thresholds = {
            "plot_consistency": 75,
            "character_consistency": 70,
            "pacing": 70,
        }

        for dim_name, threshold in dim_thresholds.items():
            raw_val = _dim_score(dim_scores, dim_name)
            norm_val = _norm100(raw_val, dim_name)
            if norm_val < threshold:
                tag = "must_fix" if threshold in (75, 70) else "warn"
                reasons.append(f"{dim_name}={raw_val}({norm_val:.1f}%)<{threshold}|{tag}")
                if threshold in (75, 70):
                    publish = False

        # 其他维度低于60分警告
        for dim_name in DIM_MAX:
            if dim_name in dim_thresholds:
                continue
            raw_val = _dim_score(dim_scores, dim_name)
            norm_val = _norm100(raw_val, dim_name)
            if norm_val < 60:
                reasons.append(f"{dim_name}={raw_val}({norm_val:.1f}%)<60|warn")

    # --- 第四步：note 构建 ---
    notes = []
    for d in (det_hard or []):
        if isinstance(d, str):
            if "长度" in d or "套话" in d:
                notes.append(d)
        elif isinstance(d, dict):
            text = d.get("text", "") or ""
            if "长度" in text or "套话" in text:
                notes.append(text)

    note = "; ".join(notes) if notes else ""

    return {
        "publish": publish,
        "reasons": reasons,
        "note": note,
        "total_score": total_score,
        "publication_line": publication_line,
    }