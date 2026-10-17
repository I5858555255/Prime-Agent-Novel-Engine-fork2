"""Gate delegation layer: thin wrappers around quality_gate modules.

This module holds the gate orchestration logic (scene regen, hard-block
accumulation, journal append, text reassembly) extracted from
pipeline_orchestrator.py. The orchestrator calls these functions directly,
passing self as the first argument where stateful operations are needed
(_regen_scene_for_id, _flag_for_human, etc.).
"""
from __future__ import annotations
from typing import Optional, Callable, Tuple, Any

import logging
import re
from pathlib import Path

from novel_engine.core.errors import SceneUnrecoverableError, ChapterResampleRequiredError, ChapterQualityGapError
from novel_engine.core.quality_policy import load_quality_policy, is_blocking, _forbidden_violation_is_blocking
from novel_engine.agents.scene_schema import SceneOutput
from novel_engine.pipeline.chapter_journal import append_scene, snapshot_journal
from novel_engine.pipeline.event_ledger import latest_end_state
from novel_engine.pipeline.boundary_guard import (
    stage1_score, scene_overlap_scores,
    build_stage2_prompt, parse_stage2_verdict,
)
from novel_engine.quality.continuity_gate import (
    normalize_sequence, build_specs, detect_inversions, continuity_fix_directive,
    extract_prior_acquired_entities,
)
from novel_engine.quality.scope_gate import (
    detect_scope_violations, scope_fix_directive, _extract_card_hard_terms,
    excise_dawn_overrun,
)
from novel_engine.quality.temporal_continuity import excise_from_text as excise_temporal_violations
from novel_engine.quality.repetition_detector import purify_novel_for_publish
from novel_engine.core.checkpoint import novel_chapter_path
from novel_engine.quality import (
    density_gate, scene_progression_gate, cross_scene_repeat_gate,
    latin_leak_gate, pov_interiority_gate, constraint_compliance_gate,
    punctuation_health, boundary_reprise_gate, final_precommit_gate,
)

from novel_engine.core.llm_client import call_llm
from novel_engine.quality.degeneration import find_self_repetition
logger = logging.getLogger(__name__)


def _forbidden_violations(orchestrator, novel, review_text=""):
    """Scan novel text for forbidden violations.

    Thin wrapper around ForbiddenScanner that delegates to the gates module.
    """
    text = getattr(novel, "content", None)
    if text is None:
        text = novel if isinstance(novel, str) else str(novel)
    from novel_engine.quality.repetition_detector import purify_novel_for_publish
    text = purify_novel_for_publish(text)
    out = []
    out.extend(orchestrator.forbidden.scan(text))
    if review_text:
        out.extend(orchestrator.forbidden.scan_review(review_text))
    return out


def _run_continuity_gate(
    self, chapter_num: int, task_card: dict, scenes: list, assembled_text: str
) -> str:
    """CC round-8 P0-2b：assembly 后章内时间/因果一致性门（零 LLM 规则判定）。

    检测某场景是否提前演出了更晚场景才成立的实体状态；命中则对该场景做一次 E
    定点重生（负例带 do_not_depict_before），重生后仍倒置则抛 ChapterResampleRequiredError
    交导演绕过缓存重排任务卡。全部消除则按重生后的场景重新组装并净化返回。
    """
    bps = task_card.get("scene_blueprints", []) or []
    order, _ = normalize_sequence(bps)
    # CC round-8：导演自报关键词实测不可靠（环境词共现误报），默认只启用内置种子规则；
    # 可经 pipeline.use_director_depict_keywords=true 显式打开导演关键词硬判通道。
    _use_director_kw = bool(
        (self.config.get("pipeline", {}) or {}).get("use_director_depict_keywords", False))
    bp_by_id: dict[int, dict] = {}
    for bp in bps:
        sid = int(bp.get("scene_num", 0) or 0)
        b = dict(bp)
        b["_seq"] = order.get(sid, sid)
        bp_by_id[sid] = b

    specs = build_specs(scenes, bp_by_id)
    _prev_es = latest_end_state(self.root, chapter_num)
    _prior_acquired = extract_prior_acquired_entities(_prev_es)
    inversions = detect_inversions(specs, use_director_keywords=_use_director_kw,
                                   prior_acquired_entities=_prior_acquired)
    if not inversions:
        return assembled_text
    self._chapter_gate_fired = True

    spec_by_id = {sp["scene_id"]: sp for sp in specs}
    still_bad: list[int] = []
    for inv in inversions:
        sid = int(inv["scene_id"])
        directive = continuity_fix_directive(inv, spec_by_id.get(sid))
        logger.warning(
            f"Continuity inversion ch{chapter_num} scene{sid} ({inv.get('source')}): "
            f"{inv.get('state')} matched={inv.get('matched')} -> targeted regen")
        cur_scene = next((s for s in scenes if int(getattr(s, "scene_id", 0) or 0) == sid), None)
        try:
            new_scene = self._regen_scene_for_id(task_card, sid, scenes, fix_directive=directive)
        except Exception as e:
            logger.warning(f"Continuity regen failed scene{sid}: {e} -> fallback to excision")
            new_scene = None
        rescue_text = (new_scene.scene_text if new_scene is not None
                       else (getattr(cur_scene, "scene_text", "") if cur_scene is not None else ""))
        probe = dict(spec_by_id.get(sid, {"scene_id": sid}))
        probe["text"] = rescue_text or ""
        others = [sp for sp in specs if sp["scene_id"] != sid]
        recheck = detect_inversions([probe] + others, use_director_keywords=_use_director_kw,
                                    prior_acquired_entities=_prior_acquired)
        accepted = None
        if not any(int(r["scene_id"]) == sid for r in recheck):
            accepted = new_scene if new_scene is not None else cur_scene
        else:
            # CC20 选项C-A：重生(或其失败)后仍倒置 -> 确定性时间越界切除自救一次，同份文本复检
            exc_text, n_exc = excise_temporal_violations(rescue_text or "", task_card.get("timeline_anchor"))
            if n_exc:
                probe2 = dict(spec_by_id.get(sid, {"scene_id": sid}))
                probe2["text"] = exc_text
                rc2 = detect_inversions([probe2] + others, use_director_keywords=_use_director_kw,
                                        prior_acquired_entities=_prior_acquired)
                if not any(int(r["scene_id"]) == sid for r in rc2):
                    if new_scene is not None:
                        new_scene.scene_text = exc_text
                        accepted = new_scene
                    else:
                        accepted = SceneOutput(sid, exc_text,
                                               getattr(cur_scene, "hook", "") if cur_scene else "",
                                               list(getattr(cur_scene, "beats", []) or []) if cur_scene else [])
                    logger.info(f"Continuity temporal excision rescued ch{chapter_num} scene{sid} ({n_exc} hit)")
        if accepted is not None:
            for idx, s in enumerate(scenes):
                if int(getattr(s, "scene_id", 0) or 0) == sid:
                    scenes[idx] = accepted
            if new_scene is not None or accepted is not cur_scene:
                try:
                    append_scene(self.root, chapter_num,
                                 {"scene_id": sid, "scene_text": accepted.scene_text,
                                  "hook": getattr(accepted, "hook", ""),
                                  "beats": list(getattr(accepted, "beats", []) or [])})
                except Exception as je:
                    logger.warning(f"Continuity journal append failed scene{sid}: {je}")
            continue
        logger.warning(f"Continuity inversion persists scene{sid} after regen+excision -> gap-continue")
        still_bad.append(sid)

    self.writer.last_scenes = list(scenes)
    if still_bad:
        # CC20 选项C-B：重生+确定性切除仍倒置 -> gap-continue（隔离+人验+游标继续，不整批 HALT）
        raise ChapterQualityGapError(
            f"章内时序/因果倒置经定点重生与时间越界切除后仍未消除 scenes={sorted(set(still_bad))}，本章留空待补",
            chapter=chapter_num, scene_ids=sorted(set(still_bad)),
            replan="continuity_gap", gap_kind="continuity",
            violations=[{"scene_id": s, "kind": "continuity_inversion"} for s in sorted(set(still_bad))],
        )
    cur = "\n\n".join((getattr(s, "scene_text", "") or "") for s in scenes)
    logger.info(f"Continuity gate resolved ch{chapter_num}; reassembled {len(cur)} chars")
    return purify_novel_for_publish(cur, chapter_num=chapter_num)

def _run_scope_gate(self, chapter_num: int, task_card: dict, scenes: list, assembled_text: str) -> str:
    """CC round-9 P0-1：assembly 后设定泄漏/时间锚越界/越权增场确定性硬门（零 LLM 判定）。

    命中场景做一次 E 定点重生（fp/pp=0.4，负例点名违禁词/越界时间标记），复检仍命中则
    判 scope_block 交导演绕过缓存重排任务卡。软预警只记录不阻断。
    """
    bps = task_card.get("scene_blueprints", []) or []
    # 越权新增场景：实际场景数 > 任务卡场景数 → 纯计数硬阻断（CC Q1(c)，零歧义）
    if bps and len(scenes) > len(bps):
        raise ChapterResampleRequiredError(
            f"实际场景数 {len(scenes)} 超过任务卡规划 {len(bps)}，疑似越权新增场景，需重新规划",
            chapter=chapter_num,
            scene_ids=[int(getattr(x, "scene_id", 0) or 0) for x in scenes],
            replan="scope_block",
        )
    anchor_ = task_card.get("timeline_anchor")
    try:
        extra = _extract_card_hard_terms(task_card)
    except Exception:
        extra = []
    still_bad: list[int] = []
    fired = False
    changed = False
    for s in list(scenes):
        sid = int(getattr(s, "scene_id", 0) or 0)
        res = detect_scope_violations(s.scene_text or "", chapter_num, anchor_, self.root, extra)
        hard = res.get("hard", [])
        for w in res.get("soft", []):
            logger.info(f"Scope soft-warn ch{chapter_num} scene{sid}: {w.get('kind')} {w.get('term')}")
        if not hard:
            continue
        fired = True
        dawn_hits = [v for v in hard if v.get("kind") == "dawn_overrun"]
        leak_hits = [v for v in hard if v.get("kind") != "dawn_overrun"]

        cur_scene = s
        if leak_hits:
            # 体系/世界观泄漏必须 LLM 定点重生；dawn 时间锚词不进重生 prompt，交确定性切除
            directive = scope_fix_directive(leak_hits)
            # CC round-24 P0-2：scope 修复是“高优先级硬约束先修”，但负例 prompt 不能只要求
            # 删词——r27 ch1 实测删词时把 density/推进锚点一起删光，修复后该场 coverage=0
            # 被 density 门判空送入死路。这里把本场 scene_progression_contract 作为【正向保留
            # 约束】带入同一 prompt（不在下游放松 density 标准，而是让上游修复一次做对）。
            try:
                from novel_engine.quality import scene_progression_gate as _spg24b
                _bp24 = next((b for b in bps
                              if int(b.get("scene_num", 0) or 0) == sid), {}) or {}
                _c24 = _spg24b.progression_contract(_bp24)
                _nse24 = _c24.get("new_state_or_entity") or []
                _irr24 = _c24.get("irreversible_change") or ""
                if _nse24 or _irr24:
                    directive += (
                        "\n【重要·修复时必须保留推进要素】删除/替换禁用词的同时，"
                        "严禁把本场具体事件一并删掉，正文仍必须明确演出：\n"
                        + "\n".join(f"- 必须落地的新状态/新实体：{x}" for x in _nse24)
                        + (f"\n- 必须发生的不可逆动作/关系变化：{_irr24}" if _irr24 else "")
                    )
            except Exception:
                pass
            logger.warning(
                f"Scope leak ch{chapter_num} scene{sid}: "
                f"{[(v.get('kind'), v.get('term')) for v in leak_hits]} -> targeted regen")
            try:
                cur_scene = self._regen_scene_for_id(
                    task_card, sid, scenes, fix_directive=directive,
                    frequency_penalty=0.4, presence_penalty=0.4)
            except Exception as e:
                logger.warning(f"Scope regen failed scene{sid}: {e} -> replan")
                still_bad.append(sid)
                continue
        elif dawn_hits:
            logger.info(
                f"Scope dawn_overrun ch{chapter_num} scene{sid}: "
                f"{[v.get('term') for v in dawn_hits]} -> deterministic sentence excision")

        # dawn_overrun 确定性整句切除（复用引号/将来时豁免），不调用 LLM
        base_text = cur_scene.scene_text or ""
        excised, removed = excise_dawn_overrun(base_text, chapter_num, self.root, anchor_)
        if removed:
            cur_scene.scene_text = excised
            logger.info(f"Scope dawn excised ch{chapter_num} scene{sid}: {len(removed)} sentence(s)")

        # 重生后仍含禁词时，先确定性字符串替换兜底（LLM 改不动时不直接判重排）
        _cur_text = cur_scene.scene_text or ""
        _deterministically_replaced = False
        _recheck = detect_scope_violations(_cur_text, chapter_num, anchor_, self.root, extra)
        _leak_left = [v for v in _recheck.get("hard", []) if v.get("kind") != "dawn_overrun"]
        dawn_left = [v for v in _recheck.get("hard", []) if v.get("kind") == "dawn_overrun"]
        if _leak_left:
            for _v in _leak_left:
                _term = _v.get("term", "")
                _orig_sent = _v.get("sentence", "")
                if _term and _orig_sent and _orig_sent in _cur_text:
                    _escaped = re.escape(_orig_sent)
                    # 替换目标不能是 hard_block 中的词（如婴儿的调息也禁），用无害替代词
                    _repls = {
                        "吐纳": "调整呼吸",
                        "调息": "调整呼吸",
                        "气感": "体内异样",
                        "内视": "内观",
                        "行气探查": "感知气息",
                        "气机探查": "感知气息",
                    }
                    _replacement = _repls.get(_term, _term + "（已删除）")
                    _new_text = re.sub(_escaped, _replacement, _cur_text)
                    if _new_text != _cur_text:
                        cur_scene.scene_text = _new_text
                        _cur_text = _new_text
                        _deterministically_replaced = True
                        logger.info(
                            f"Scope deterministic replace ch{chapter_num} scene{sid}: "
                            f"'{_term}' -> '{_replacement}' in {_v.get('sentence','')[:60]}")
            if _deterministically_replaced:
                _recheck = detect_scope_violations(_cur_text, chapter_num, anchor_, self.root, extra)
                _leak_left = [v for v in _recheck.get("hard", []) if v.get("kind") != "dawn_overrun"]
                dawn_left = [v for v in _recheck.get("hard", []) if v.get("kind") == "dawn_overrun"]
        if _leak_left:
            logger.warning(
                f"Scope leak persists scene{sid} after regen "
                f"{[(v.get('kind'), v.get('term')) for v in _leak_left]} -> replan")
            still_bad.append(sid)
            continue
        if dawn_left:
            # 理论上切除后不应残留；兜底判重排，避免漏网
            logger.warning(
                f"Scope dawn still present scene{sid} after excision "
                f"{[v.get('term') for v in dawn_left]} -> replan")
            still_bad.append(sid)
            continue
        if cur_scene is not s or removed:
            changed = True
        for idx, sc in enumerate(scenes):
            if int(getattr(sc, "scene_id", 0) or 0) == sid:
                scenes[idx] = cur_scene
        try:
            append_scene(self.root, chapter_num,
                         {"scene_id": sid, "scene_text": cur_scene.scene_text,
                          "hook": getattr(cur_scene, "hook", None),
                          "beats": list(getattr(cur_scene, "beats", []) or [])})
        except Exception as je:
            logger.warning(f"Scope journal append failed scene{sid}: {je}")
    if fired:
        self._chapter_gate_fired = True
    self.writer.last_scenes = list(scenes)
    if still_bad:
        raise ChapterResampleRequiredError(
            f"设定泄漏/时间锚越界经定点重生仍未消除 scenes={sorted(set(still_bad))}，需重排任务卡",
            chapter=chapter_num, scene_ids=sorted(set(still_bad)),
            replan="scope_block",
        )
    if not changed:
        return assembled_text
    cur = "\n\n".join((getattr(x, "scene_text", "") or "") for x in scenes)
    logger.info(f"Scope gate resolved ch{chapter_num}; reassembled {len(cur)} chars")
    return purify_novel_for_publish(cur, chapter_num=chapter_num)

def _run_density_gate(self, chapter_num: int, task_card: dict, scenes: list, assembled_text: str) -> str:
    """CC round-10 P0-1：assembly 后内容密度覆盖率确定性门（零 LLM）。

    每场景 concrete_events / named_interactions / info_reveal_points 的关键词覆盖率
    低于阈值，判“氛围堆砌、事件未演足”，定点重生一次并复检；仍不足判 density_block
    交导演绕过缓存重排任务卡。protagonist_interiority 仅软提示不阻断。
    """
    from novel_engine.quality import density_gate
    bps = task_card.get("scene_blueprints", []) or []
    if not bps:
        return assembled_text
    cfg = density_gate.load_density_config(self.root)
    arc = density_gate.arc_for_chapter(chapter_num, self.root)
    req = density_gate.requirements_for(cfg, arc)
    threshold = float(req.get("coverage_threshold", 0.8))
    interior_n = int(req.get("protagonist_interiority", 0) or 0)
    still_bad: list[int] = []
    fired = False

    def _sem(sid, text):
        bpx = next((b for b in bps if int(b.get("scene_num", 0) or 0) == sid), None)
        return density_gate.evaluate_scene_semantic(
            bpx, text or "", req, root=self.root, arc=arc)

    def _commit_scene(sid, ns):
        for k, sc in enumerate(scenes):
            if int(getattr(sc, "scene_id", 0) or 0) == sid:
                scenes[k] = ns
        try:
            append_scene(self.root, chapter_num,
                         {"scene_id": sid, "scene_text": ns.scene_text,
                          "hook": getattr(ns, "hook", None),
                          "beats": list(getattr(ns, "beats", []) or [])})
        except Exception as je:
            logger.warning(f"Density journal append failed scene{sid}: {je}")

    for s in list(scenes):
        sid = int(getattr(s, "scene_id", 0) or 0)
        bp = next((b for b in bps if int(b.get("scene_num", 0) or 0) == sid), None)
        if bp is None:
            continue
        # 仅对导演显式给密度字段的卡硬判；老卡/模板卡软跳过
        if not density_gate.blueprint_has_explicit_density(bp):
            logger.info(f"Density gate skip ch{chapter_num} scene{sid}: no explicit density fields")
            continue
        base = _sem(sid, s.scene_text or "")
        if base["ratio"] + 1e-9 >= threshold:
            if interior_n and not base.get("interiority"):
                logger.info(f"Density interiority soft-miss ch{chapter_num} scene{sid} (不阻断)")
            continue
        fired = True
        directive = density_gate.density_directive(
            base, interior_n,
            agency_level=task_card.get("protagonist_agency_level"))
        logger.warning(
            f"Density gap ch{chapter_num} scene{sid}: coverage={base['ratio']:.2f} "
            f"({base['covered_count']}/{base['total']}) missing={len(base['missing'])} -> targeted regen")
        prev_ratio = base["ratio"]
        cur_scene = s
        accepted = None  # 有提升但未达阈的最佳稿
        for rnd in (1, 2):
            try:
                ns = self._regen_scene_for_id(
                    task_card, sid, scenes, fix_directive=directive,
                    frequency_penalty=0.3, presence_penalty=0.3)
            except Exception as e:
                logger.warning(f"Density regen failed scene{sid} r{rnd}: {e} -> replan")
                still_bad.append(sid)
                break
            rr = _sem(sid, ns.scene_text or "")
            improved = rr["ratio"] > prev_ratio + 1e-9
            logger.warning(
                f"Density r{rnd} scene{sid} coverage={rr['ratio']:.2f} "
                f"({rr['covered_count']}/{rr['total']}) improved={improved}")
            if rr["ratio"] + 1e-9 >= threshold:
                cur_scene = ns
                accepted = "pass"
                break
            if improved:
                # 趋势向上：保留最佳稿，继续/最终软放行交 reviewer
                cur_scene = ns
                prev_ratio = rr["ratio"]
                accepted = "soft"
                continue
            # 持平/下降：再无增长趋势
            if rnd == 1:
                continue
            if accepted is None:
                still_bad.append(sid)
        if sid in still_bad:
            continue
        if accepted is not None and cur_scene is not s:
            _commit_scene(sid, cur_scene)
            if accepted == "soft":
                logger.warning(
                    f"Density soft-pass ch{chapter_num} scene{sid}: coverage={prev_ratio:.2f} "
                    f"虽<{threshold:.2f} 但两轮有提升，放行交 reviewer 兜底（不重排）")
    if fired:
        self._chapter_gate_fired = True
    self.writer.last_scenes = list(scenes)
    if still_bad:
        raise ChapterResampleRequiredError(
            f"内容密度两轮重生无改善（疑似真空洞） scenes={sorted(set(still_bad))}，需重排任务卡",
            chapter=chapter_num, scene_ids=sorted(set(still_bad)),
            replan="density_block",
        )
    if not fired:
        return assembled_text
    cur = "\n\n".join((getattr(x, "scene_text", "") or "") for x in scenes)
    logger.info(f"Density gate resolved ch{chapter_num}; reassembled {len(cur)} chars")
    return purify_novel_for_publish(cur, chapter_num=chapter_num)

def _run_scene_progression_gate(self, chapter_num, task_card, scenes, assembled_text):
    """CC round-23 P0-1：场景差异化推进 + 氛围占比门（零 LLM 判定，受限 LLM 定点重生）。

    - no_new_state：显式 scene_progression_contract 的场景没在正文落地任何新状态/新实体；
    - atmosphere_saturated：单场纯氛围句占比超阈值；
    - 相邻场氛围意象共享度超阈值 -> 只重生后现场。
    每场景至多重生 2 次，复检测同一组确定性谓词；有改善但未清零走 soft 交 reviewer/E-loop，
    本门【绝不单独】抛整章重排或 HALT（婴儿开篇氛围天然偏高，优先保稳定与低误伤）。
    每场景的推进/氛围指标存 self._scene_progression_metrics 供 reviewer 锚点与 E-loop 使用。
    """
    from novel_engine.quality import scene_progression_gate as spg
    from novel_engine.quality import density_gate as _dg23
    if not scenes:
        return assembled_text
    if (self.config.get("llm") or {}).get("use_mock"):
        return assembled_text
    cfg = spg.load_progression_config(self.root)
    arc = _dg23.arc_for_chapter(chapter_num, self.root)

    def _sid_of(x):
        return int(getattr(x, "scene_id", 0) or 0) if not isinstance(x, dict) else int(x.get("scene_id", 0) or 0)

    def _text_of(x):
        return getattr(x, "scene_text", "") or "" if not isinstance(x, dict) else str(x.get("scene_text", "") or "")

    def _latest_list():
        latest = {}
        for sc in scenes:
            latest[_sid_of(sc)] = sc
        return [latest[k] for k in sorted(latest)]

    def _bp(sid):
        return next((b for b in (task_card.get("scene_blueprints", []) or [])
                     if int(b.get("scene_num", 0) or 0) == sid), {}) or {}

    check0 = spg.check_chapter_progression(task_card, _latest_list(), cfg, root=self.root, arc=arc)
    self._scene_progression_metrics = {
        sid: {"no_new_state": ev["no_new_state"] and ev["explicit_contract"],
              "missing_new_state": [d.get("desc", "") for d in ev["missing_new_state"]],
              "atmosphere_ratio": ev["atmosphere"]["ratio"]}
        for sid, ev in (check0.get("per_scene") or {}).items()
    }
    scene_v = {v["scene_id"]: v for v in check0.get("scene_violations", [])}
    adj_later = {}
    for a in check0.get("adjacent", []) or []:
        adj_later.setdefault(a["later_scene"], []).append(a)
    # CC round-24 P0-4：仅 no_new_state / 单场氛围超标 触发 LLM 重生；相邻场氛围饱和在
    # 本书“雾/迷雾禁区”固定环境下属天然基线，实测重生无法收敛，降级为只记录供 reviewer
    # 锚点/E-loop，不再为它烧 LLM（最大提速点之一）。
    targets = sorted(set(scene_v))
    _adj_only24 = sorted(set(adj_later) - set(scene_v))
    if _adj_only24:
        logger.info(
            f"Progression ch{chapter_num} adjacent-only saturation scenes={_adj_only24} "
            f"-> reviewer/E-loop hint only, no LLM regen (fixed mist environment)")
    if not targets:
        return assembled_text
    self._chapter_gate_fired = True
    changed = False

    def _directive(sid):
        parts = []
        v = scene_v.get(sid)
        if v and "no_new_state" in v.get("types", []):
            parts.append(spg.new_state_directive(v))
        if v and "atmosphere_saturated" in v.get("types", []):
            parts.append(spg.atmosphere_ratio_directive(
                float(v["detail"]["atmosphere_ratio"]),
                float(cfg.get("scene_atmosphere_ratio", 0.55))))
        for a in adj_later.get(sid, []):
            parts.append(spg.adjacent_motif_directive(a))
        return "\n\n".join(parts)

    def _still_bad(sid, text):
        ev = spg.evaluate_scene_progression(_bp(sid), text, cfg, root=self.root, arc=arc)
        no_state = ev["explicit_contract"] and ev["no_new_state"]
        sat = ev["atmosphere_saturated"]
        # 相邻复检：该场作为后现场仍与前一在场饱和
        latest = {_sid_of(x): _text_of(x) for x in _latest_list()}
        adj_bad = False
        sids_sorted = sorted(latest)
        for a, b in zip(sids_sorted, sids_sorted[1:]):
            if b == sid:
                adj_bad = spg.adjacent_atmosphere_overlap(latest[a], latest[b], cfg)["saturated"]
        return no_state, sat, adj_bad

    for sid in targets:
        cur = next((x for x in scenes if _sid_of(x) == sid), None)
        if cur is None:
            continue
        directive = _directive(sid)
        prev_text = _text_of(cur)
        accepted_scene = None
        # CC round-24 P0-4：重生轮次 2 -> 1（no_new_state/氛围超标 1 轮通常足够；仍不行
        # 说明有更深层问题，交 reviewer/E-loop，不在本门继续烧轮次）。
        for rnd in (1,):
            try:
                ns = self._regen_scene_for_id(
                    task_card, sid, scenes, fix_directive=directive,
                    frequency_penalty=0.3, presence_penalty=0.3)
            except Exception as e:
                logger.warning(f"Progression regen failed scene{sid} r{rnd}: {e}")
                break
            ntext = getattr(ns, "scene_text", "") or ""
            no_state, sat, adj_bad = _still_bad(sid, ntext)
            logger.warning(
                f"Progression r{rnd} ch{chapter_num} scene{sid}: "
                f"no_new_state={no_state} atmosphere_sat={sat} adjacent_sat={adj_bad}")
            # 验收只看可修复两项；adjacent 残留属固定环境基线，不阻断接受
            if not no_state and not sat:
                accepted_scene = ns
                break
            # 有改善（任一项消除或文本确有变化）继续/最终软保留
            if len(ntext) >= int(0.8 * max(1, len(prev_text))):
                accepted_scene = ns
                prev_text = ntext
                for idx, x in enumerate(scenes):
                    if _sid_of(x) == sid:
                        scenes[idx] = ns
        if accepted_scene is not None:
            for idx, x in enumerate(scenes):
                if _sid_of(x) == sid:
                    scenes[idx] = accepted_scene
            changed = True
            try:
                append_scene(self.root, chapter_num,
                             {"scene_id": sid, "scene_text": getattr(accepted_scene, "scene_text", ""),
                              "hook": getattr(accepted_scene, "hook", None),
                              "beats": list(getattr(accepted_scene, "beats", []) or [])})
            except Exception as je:
                logger.warning(f"Progression journal append failed scene{sid}: {je}")
            # 重生后仍残留 -> soft 交 reviewer/E-loop（不重排、不 HALT）
            ntext = getattr(accepted_scene, "scene_text", "") or ""
            no_state, sat, adj_bad = _still_bad(sid, ntext)
            if no_state or sat or adj_bad:
                logger.warning(
                    f"Progression soft-pass ch{chapter_num} scene{sid}: residual "
                    f"no_new_state={no_state} atmosphere_sat={sat} adjacent_sat={adj_bad} -> reviewer/E-loop")
    # 更新指标快照（重生后）
    try:
        chk2 = spg.check_chapter_progression(task_card, _latest_list(), cfg, root=self.root, arc=arc)
        self._scene_progression_metrics = {
            sid: {"no_new_state": ev["no_new_state"] and ev["explicit_contract"],
                  "missing_new_state": [d.get("desc", "") for d in ev["missing_new_state"]],
                  "atmosphere_ratio": ev["atmosphere"]["ratio"]}
            for sid, ev in (chk2.get("per_scene") or {}).items()
        }
    except Exception:
        pass
    if not changed:
        return assembled_text
    self.writer.last_scenes = list(scenes)
    cur = "\n\n".join((getattr(x, "scene_text", "") or "") for x in _latest_list())
    logger.info(f"Progression gate resolved ch{chapter_num}; {len(cur)} chars")
    return purify_novel_for_publish(cur, chapter_num=chapter_num)

def _run_cross_scene_repeat_gate(self, chapter_num: int, task_card: dict,
                                 scenes: list, assembled_text: str) -> str:
    """CC round-14 P0-1：跨场景近逐字句确定性切除 + 意象搬运定点重生。"""
    from novel_engine.quality import cross_scene_repeat_gate as crg
    if not scenes:
        return assembled_text
    cfg = crg.load_repeat_config(self.root)
    try:
        fc = load_quality_policy(self.root)
        func_chars = ""
    except Exception:
        func_chars = ""
    try:
        dcfg = __import__(
            "novel_engine.quality.density_gate", fromlist=["load_density_config"]
        ).load_density_config(self.root)
        func_chars = dcfg.get("function_chars", "") or ""
    except Exception:
        pass
    wl = crg.recurring_motif_whitelist(task_card)

    # 每场景取最新对象（重生会替换 list 中同 id）
    def _latest():
        latest = {}
        for sc in scenes:
            latest[int(getattr(sc, "scene_id", 0) or 0)] = sc
        return [latest[k] for k in sorted(latest)]

    changed = False
    # ① 句级近逐字：确定性切除“后现”整句，保留首现
    sv = crg.detect_sentence_repeat(_latest(), cfg, function_chars=func_chars, whitelist=wl)
    if sv:
        drop = crg.repeated_sentence_set(sv)
        fired_sids = sorted({v["scenes"][1] for v in sv})
        logger.warning(
            f"Cross-scene repeat ch{chapter_num}: excise {len(drop)} near-verbatim "
            f"sentence(s) in scenes {fired_sids}")
        for sc in _latest():
            sid = int(getattr(sc, "scene_id", 0) or 0)
            if sid not in fired_sids:
                continue
            new_text = crg.excise_repeated_sentences(getattr(sc, "scene_text", "") or "", drop)
            if new_text != (getattr(sc, "scene_text", "") or ""):
                sc.scene_text = new_text
                changed = True
        self._chapter_gate_fired = True

    # ② 意象级搬运：对后现场景定点重生1次（保留首现场景），残留交 reviewer，不硬 HALT
    iv = crg.detect_image_reuse(_latest(), cfg, task_card, whitelist=wl)
    if iv:
        later_sids = sorted({max(v["scenes"]) for v in iv})
        logger.warning(
            f"Cross-scene image reuse ch{chapter_num}: regen later scenes {later_sids} "
            f"({[v['shared_images'] for v in iv]})")
        for sid in later_sids:
            directive = crg.image_reuse_directive(iv, sid)
            try:
                ns = self._regen_scene_for_id(
                    task_card, sid, scenes, fix_directive=directive,
                    frequency_penalty=0.4, presence_penalty=0.4)
                for idx, sc in enumerate(scenes):
                    if int(getattr(sc, "scene_id", 0) or 0) == sid:
                        scenes[idx] = ns
                changed = True
            except Exception as e:
                logger.warning(f"Cross-scene image regen failed scene{sid}: {e}")
        self._chapter_gate_fired = True
        # 重生后复检，仍有显著意象搬运则记 soft（交 reviewer pacing 兜底，不整章重排）
        try:
            iv2 = crg.detect_image_reuse(_latest(), cfg, task_card, whitelist=wl)
            if iv2:
                logger.warning(
                    f"Cross-scene image reuse persists ch{chapter_num} after regen "
                    f"{[v['shared_images'] for v in iv2]} -> defer to reviewer")
        except Exception:
            pass

    if not changed:
        return assembled_text
    self.writer.last_scenes = list(scenes)
    cur = "\n\n".join((getattr(x, "scene_text", "") or "") for x in scenes)
    logger.info(f"Cross-scene repeat gate resolved ch{chapter_num}; {len(cur)} chars")
    return purify_novel_for_publish(cur, chapter_num=chapter_num)

def _punct_only_repair(self, paragraph: str) -> str | None:
    """CC round-16 P0-1：定点 LLM“仅插标点”修复一段流水句。

    严格只允许插入标点；用去标点逐字比对校验未越界改写，不合规或仍不健康返回 None
    （调用方改走整段重生）。
    """
    from novel_engine.quality import punctuation_health as ph
    prompt = (
        "下面这段中文小说叙事缺少必要的标点断句。请【仅在合适位置插入逗号、句号、顿号、"
        "分号等标点】，使其符合正常中文叙事节奏。\n"
        "严禁增删、替换、改写任何文字，严禁调整语序，严禁增减任何词语，只允许插入标点。\n"
        "直接输出加好标点后的段落本身，不要任何解释、不要引号包裹、不要markdown代码块。\n\n"
        f"原文：{paragraph}"
    )
    try:
        resp = self.polish_router.chat_completion(
            [{"role": "user", "content": prompt}], temperature=0.2, max_tokens=2000)
        fixed = (resp or {}).get("content", "") or ""
    except Exception as e:
        logger.warning(f"punct-only repair call failed: {e}")
        return None
    fixed = fixed.strip()
    for fence in ("```",):
        if fixed.startswith(fence):
            fixed = fixed.split("\n", 1)[1] if "\n" in fixed else fixed
            if fixed.endswith(fence):
                fixed = fixed.rsplit(fence, 1)[0]
            fixed = fixed.strip()
    if fixed.startswith(("“", '"', "‘")) and fixed.endswith(("”", '"', "’")):
        fixed = fixed[1:-1].strip()
    if not fixed:
        return None
    if not ph.is_punctuation_only_fix(paragraph, fixed):
        logger.warning("punct-only repair changed wording -> reject, fall back to scene regen")
        return None
    if ph.check_paragraph_punctuation(fixed)["is_unhealthy"]:
        return None
    return fixed

def _run_latin_leak_gate(self, chapter_num: int, task_card: dict,
                         scenes: list, assembled_text: str) -> str:
    """CC round-18 P0-1：正文拉丁字母（英文残片）泄漏门。

    纯中文古风零拉丁词。命中场景定点重生 1 次并复检；仍有残留则记确定性硬问题
    （提交门据此拦截、force-best 隔离），不依赖 reviewer 偶然发现。
    """
    from novel_engine.quality import latin_leak_gate as llg
    if not scenes:
        return assembled_text
    if (self.config.get("llm") or {}).get("use_mock"):
        return assembled_text
    leaks = llg.latin_leaks_by_scene(
        [{"scene_id": getattr(s, "scene_id", 0), "scene_text": getattr(s, "scene_text", "") or ""}
         for s in scenes])
    if not leaks:
        return assembled_text
    logger.warning(f"Latin leak ch{chapter_num}: scenes {[x['scene_id'] for x in leaks]} "
                   f"tokens={[x['leaked_tokens'] for x in leaks]} -> targeted rewrite")
    changed = False
    for info in leaks:
        sid = info["scene_id"]
        # 区分"零星残片"与"整段英文跑飞"：后者用更强烈的指令 + 真实英文句样例
        # （ch267/ch325：模型整段输出英文，重生指令必须点明是语言跑飞而非个别词）。
        sent = llg.detect_english_sentences(
            next((getattr(x, "scene_text", "") or "" for x in scenes
                  if int(getattr(x, "scene_id", 0) or 0) == sid), ""))
        if sent["has_english_sentence"]:
            directive = ("本场景整段输出了英文句子（示例：" + (sent["sample"] or "") + "）。"
                         "这是模型语言跑飞，本场景必须整体重写为纯中文：正文 100% 中文，"
                         "禁止任何英文字母、英文单词、英文句子与拉丁字符，专有名词一律用中文"
                         "或约定译名；不得改变剧情与情节点。")
            neg_ex = [(sent["sample"] or "The purple fog pressed against the outside of his body"),
                      "（正文整段英文，严禁——必须整体重写为中文）"]
        else:
            directive = ("正文出现了英文字母/英文单词，这在纯中文古风里是严重错误。重写本场景时"
                         "严禁出现任何英文字母、英文单词或拉丁字符，正文必须全部为中文，"
                         "专有名词也一律用中文或约定译名；不得改变剧情与情节点。")
            neg_ex = ["he decision 不对外透露（正文混入英文残片，严禁）"]
        try:
            ns = self._regen_scene_for_id(
                task_card, sid, scenes,
                negative_examples=neg_ex, fix_directive=directive,
                frequency_penalty=0.3, presence_penalty=0.3)
            for idx, x in enumerate(scenes):
                if int(getattr(x, "scene_id", 0) or 0) == sid:
                    scenes[idx] = ns
            changed = True
        except Exception as e:
            logger.warning(f"Latin leak regen failed scene{sid}: {e}")
        # 复检
        latest = next((x for x in scenes if int(getattr(x, "scene_id", 0) or 0) == sid), None)
        still = llg.detect_latin_leak(getattr(latest, "scene_text", "") or "")
        still_sent = llg.detect_english_sentences(getattr(latest, "scene_text", "") or "")
        if still["has_leak"]:
            detail = (f"场景{sid} 重生后仍含拉丁字符 {still['leaked_tokens']}"
                      if not still_sent["has_english_sentence"]
                      else f"场景{sid} 重生后仍含整段英文（样例={still_sent['sample']}）")
            hard = f"[非中文] {detail}"
            self._last_deterministic_issues = getattr(self, "_last_deterministic_issues", []) + [hard]
            logger.error(f"Latin leak persists ch{chapter_num} scene{sid} {still['leaked_tokens']} -> hard block")
            self._flag_for_human(chapter_num, getattr(self, "_cur_review_score", 0), hard)
        self._chapter_gate_fired = True
    if not changed:
        return assembled_text
    self.writer.last_scenes = list(scenes)
    cur = "\n\n".join((getattr(x, "scene_text", "") or "") for x in scenes)
    logger.info(f"Latin leak gate resolved ch{chapter_num}; {len(cur)} chars")
    return purify_novel_for_publish(cur, chapter_num=chapter_num)

def _run_pov_interiority_gate(self, chapter_num, task_card, scenes, assembled_text):
    """CC round-22 P0-1：低能动性主角成人化内心独白门。

    零 LLM 检测：超长/抽象独白片段先确定性截断；截断无法清除抽象禁词的场景再
    定点重生 1 次，重生后仍残留则记确定性硬问题交提交门隔离。仅在 director 标注
    的低能动性状态(infant_low/injured/imprisoned)下生效。
    """
    from novel_engine.quality import pov_interiority_gate as pig
    if not scenes:
        return assembled_text
    if (self.config.get("llm") or {}).get("use_mock"):
        return assembled_text
    agency = str(task_card.get("protagonist_agency_level", "") or "").strip().lower()
    if not pig.is_restricted(agency):
        return assembled_text

    changed = False
    for sc in list(scenes):
        sid = int(getattr(sc, "scene_id", 0) or 0)
        text = getattr(sc, "scene_text", "") or ""
        if not text:
            continue
        new_text, regen_terms, did_cut = pig.plan_and_excise(text, agency)
        if did_cut and new_text != text:
            sc.scene_text = new_text
            changed = True
            logger.warning(f"POV interiority ch{chapter_num} scene{sid}: deterministic excise of adult monologue")
        if regen_terms:
            logger.warning(
                f"POV interiority ch{chapter_num} scene{sid}: abstract terms {regen_terms} -> targeted rewrite")
            directive = (
                "本场景主角此刻为婴儿/重伤/被囚等低能动性状态，不具备成人思考能力。重写时内心活动只允许"
                "不超过50字的碎片化感官印象/生理反应/模糊情绪（暖、冷、怕、安心）；严禁出现 "
                + "、".join(regen_terms)
                + " 等抽象概念或前世身份术语，严禁连贯成人论证式独白；不得改变剧情与情节点。"
            )
            try:
                ns = self._regen_scene_for_id(
                    task_card, sid, scenes,
                    negative_examples=["他心想这份恩情此生必报、命运既有安排……（低能动性主角的成人抽象独白，严禁）"],
                    fix_directive=directive, frequency_penalty=0.3, presence_penalty=0.3)
                ntext = getattr(ns, "scene_text", "") or ""
                ntext2, nterms, cut2 = pig.plan_and_excise(ntext, agency)
                if cut2:
                    ntext = ntext2
                ns.scene_text = ntext
                for idx, x in enumerate(scenes):
                    if int(getattr(x, "scene_id", 0) or 0) == sid:
                        scenes[idx] = ns
                if nterms:
                    hard = f"[POV独白] 场景{sid} 重生后仍含抽象内心独白术语 {nterms}"
                    self._last_deterministic_issues = getattr(self, "_last_deterministic_issues", []) + [hard]
                    self._flag_for_human(chapter_num, getattr(self, "_cur_review_score", 0), hard)
                    logger.error(f"POV interiority persists ch{chapter_num} scene{sid} {nterms} -> hard block")
                changed = True
            except Exception as e:
                logger.warning(f"POV interiority regen failed scene{sid}: {e}")
            self._chapter_gate_fired = True
    if not changed:
        return assembled_text
    self.writer.last_scenes = list(scenes)
    cur = "\n\n".join((getattr(x, "scene_text", "") or "") for x in scenes)
    logger.info(f"POV interiority gate resolved ch{chapter_num}; {len(cur)} chars")
    return purify_novel_for_publish(cur, chapter_num=chapter_num)

def _run_constraint_compliance_gate(self, chapter_num, task_card, scenes, assembled_text):
    """CC round-22 P0-2：任务卡硬约束词面化履约门。

    只对显式带 scene_constraints 的场景硬判；action_forbidden_until /
    identity_concealment 两类。命中->对该场景定点重生1次（负例引用违反的约束原文），
    重生后仍违反则记确定性硬问题交提交门隔离。
    """
    from novel_engine.quality import constraint_compliance_gate as ccg
    if not scenes:
        return assembled_text
    if (self.config.get("llm") or {}).get("use_mock"):
        return assembled_text
    bps = task_card.get("scene_blueprints", []) or []
    bp_by_id = {int(b.get("scene_num", 0) or 0): b for b in bps}
    changed = False
    for sc in list(scenes):
        sid = int(getattr(sc, "scene_id", 0) or 0)
        bp = bp_by_id.get(sid)
        if bp is None:
            continue
        cons = ccg.constraints_for_blueprint(bp)
        if not cons:
            continue
        text = getattr(sc, "scene_text", "") or ""
        violations = ccg.check_constraint_compliance(text, cons)
        if not violations:
            continue
        logger.warning(
            f"Constraint noncompliance ch{chapter_num} scene{sid}: "
            f"{[v['detail'] for v in violations]} -> targeted rewrite")
        neg = [ccg.violation_negative_example(v) for v in violations][:3]
        directive = (
            "上一版正文违反了本场景任务卡的显式硬约束，必须逐条改正后重写整个场景：\n"
            + "\n".join("- " + ccg.violation_negative_example(v) for v in violations)
            + "\n改正后仍须演足本场景全部 beats，不得改变剧情走向，只删除/改写违规内容。"
        )
        try:
            ns = self._regen_scene_for_id(
                task_card, sid, scenes, negative_examples=neg,
                fix_directive=directive, frequency_penalty=0.3, presence_penalty=0.3)
            for idx, x in enumerate(scenes):
                if int(getattr(x, "scene_id", 0) or 0) == sid:
                    scenes[idx] = ns
            if ccg.check_constraint_compliance(getattr(ns, "scene_text", "") or "", cons):
                hard = f"[硬约束] 场景{sid} 重生后仍违反任务卡约束 {[v['detail'] for v in violations]}"
                self._last_deterministic_issues = getattr(self, "_last_deterministic_issues", []) + [hard]
                self._flag_for_human(chapter_num, getattr(self, "_cur_review_score", 0), hard)
                logger.error(f"Constraint noncompliance persists ch{chapter_num} scene{sid} -> hard block")
            changed = True
        except Exception as e:
            logger.warning(f"Constraint regen failed scene{sid}: {e}")
        self._chapter_gate_fired = True
    if not changed:
        return assembled_text
    self.writer.last_scenes = list(scenes)
    cur = "\n\n".join((getattr(x, "scene_text", "") or "") for x in scenes)
    logger.info(f"Constraint compliance gate resolved ch{chapter_num}; {len(cur)} chars")
    return purify_novel_for_publish(cur, chapter_num=chapter_num)

def _run_punctuation_health_gate(self, chapter_num: int, task_card: dict,
                                 scenes: list, assembled_text: str) -> str:
    """CC round-16 P0-1：成稿标点健康门（兜底；扩写/polish 已应在源头少产出流水句）。

    命中段先“仅插标点”修复并做去标点逐字合规校验；2 次仍不合规则对所属场景定点重生。
    残留转 soft（交 reviewer/人验），不因纯标点问题硬 HALT 整章。
    """
    from novel_engine.quality import punctuation_health as ph
    if not scenes:
        return assembled_text
    if (self.config.get("llm") or {}).get("use_mock"):
        return assembled_text
    # 仅在真实 LLM 客户端上启用（离线 mock 文本无中文标点、且修复调用会返回 None）
    _providers = getattr(self.polish_router, "providers", {}) or {}
    _client = next(iter(_providers.values()), None) if _providers else None
    if _client is None:
        return assembled_text
    _cn = type(_client).__name__.lower()
    if any(k in _cn for k in ("mock", "fake", "stub", "dummy", "blocked", "empty", "both", "long", "test")):
        return assembled_text

    def _is_cjk(s: str) -> bool:
        if not s:
            return False
        cjk = sum(1 for ch in s if "一" <= ch <= "鿿")
        return cjk / max(len(s), 1) >= 0.4

    changed = False
    residual_sids: list[int] = []
    for sc in list(scenes):
        sid = int(getattr(sc, "scene_id", 0) or 0)
        text = getattr(sc, "scene_text", "") or ""
        if not _is_cjk(text):
            continue
        # CC round-12 R2b：句末口径长句拆分（独立于流水段检测），无条件对每个中文场景执行
        ls_persisted = False
        try:
            ls_text, ls_stats = ph.split_long_sentences(text)
            if ls_text != text:
                text = ls_text
                changed = True
                ls_persisted = True
                logger.info(f"Punctuation gate ch{chapter_num} scene{sid}: "
                            f"long-sentence split resolved {ls_stats.get('split_count', 0)}/"
                            f"{ls_stats.get('long_sentences_detected', 0)}")
        except Exception as _e:
            logger.warning(f"Long sentence split error ch{chapter_num} scene{sid}: {_e}")
        bad = ph.check_text_punctuation(text)
        if ls_persisted:
            # 即使 bad 为空（仅有长句拆分、无流水段），也须持久化回写 scene_text
            sc.scene_text = text.strip()
            self._chapter_gate_fired = True
        if not bad:
            continue
        changed = True
        # CC28 3a：优先零 LLM 确定性断句（只在安全边界增标点/拆段、绝不改字）。
        # 若所有流水段都被零 LLM 修好则直接采用、不耗 LLM；只要还有残留就整体
        # 落回下面原有的“LLM 定点修复→整场重生”路径（不使用部分结果，避免
        # 拆段后 para_index 与旧 bad 列表错位）。
        try:
            _ztext, _zstats = ph.repair_text_punctuation(text)
        except Exception as _e:
            _ztext, _zstats = text, {"residual_unhealthy": len(bad)}
            logger.warning(f"Punctuation deterministic repair error ch{chapter_num} scene{sid}: {_e}")
        if _ztext != text and _zstats.get("residual_unhealthy", 1) == 0:
            sc.scene_text = _ztext.strip()
            self._chapter_gate_fired = True
            logger.warning(f"Punctuation gate ch{chapter_num} scene{sid}: zero-LLM deterministic split "
                           f"resolved ({_zstats.get('changed_paragraphs', 0)} paras, "
                           f"{_zstats.get('inserts', 0)} pauses)")
            continue
        # 按段精确替换仅插标点修复；同段最多 2 次
        paras = text.split("\n")
        sid_resolved = True
        for info in bad:
            pi = info["para_index"]
            if pi >= len(paras):
                continue
            orig = paras[pi].strip()
            fixed = None
            for _ in range(2):
                fixed = self._punct_only_repair(orig)
                if fixed is not None:
                    break
            if fixed is not None:
                paras[pi] = fixed
                logger.warning(f"Punctuation gate ch{chapter_num} scene{sid}: "
                               f"repaired run-on para (run={info['max_unpunctuated_run']})")
            else:
                sid_resolved = False
        new_text = "\n".join(paras).strip()
        if ph.check_text_punctuation(new_text):
            sid_resolved = False
        if sid_resolved:
            sc.scene_text = new_text
        else:
            # 仅插标点失败/残留 → 对该场景定点重生（强约束正常标点），再复检
            logger.warning(f"Punctuation gate ch{chapter_num} scene{sid}: "
                           f"punct-only failed -> targeted scene rewrite")
            directive = (
                "上一版部分段落是整段无句读的流水句（成百字中间没有逗号/句号）。重写本场景时必须："
                "每个完整句不超过约40字；每2-4个分句之间用逗号分隔；正常使用中文句号、逗号，"
                "对话用引号；严禁输出无标点的长串文字。不得改变剧情与情节点，只改正文断句与表达。"
            )
            try:
                ns = self._regen_scene_for_id(
                    task_card, sid, scenes, fix_directive=directive,
                    frequency_penalty=0.3, presence_penalty=0.3)
                for idx, x in enumerate(scenes):
                    if int(getattr(x, "scene_id", 0) or 0) == sid:
                        scenes[idx] = ns
                if ph.check_text_punctuation(getattr(ns, "scene_text", "") or ""):
                    residual_sids.append(sid)
                    self._flag_for_human(
                        chapter_num, getattr(self, "_cur_review_score", 0),
                        f"punctuation run-on persists scene{sid} after repair+regen")
            except Exception as e:
                logger.warning(f"Punctuation scene regen failed scene{sid}: {e}")
                residual_sids.append(sid)
        self._chapter_gate_fired = True
    if residual_sids:
        self._last_deterministic_soft = getattr(self, "_last_deterministic_soft", []) + [
            f"[标点-软] 场景{sorted(set(residual_sids))} 仍有流水句，已转人验"]
    if not changed:
        return assembled_text
    self.writer.last_scenes = list(scenes)
    cur = "\n\n".join((getattr(x, "scene_text", "") or "") for x in scenes)
    logger.info(f"Punctuation health gate resolved ch{chapter_num}; {len(cur)} chars")
    return purify_novel_for_publish(cur, chapter_num=chapter_num)

def _repair_paragraphs_punct_parallel(self, paragraphs, para_indexes, max_workers=4):
    """CC round-22 提速：对多个流水句自然段并发执行"仅插标点"修复。

    纯并发化，判定与串行版完全一致：每段至多尝试 2 次，仅保留
    ①去标点逐字相同(is_punctuation_only_fix) 且 ②复检健康 的结果。
    返回 {para_index: fixed_text}；不合规/仍流水的段不在结果里。
    复用 polish_router（本就按并发设计），不新增门或质量判定。
    """
    from concurrent.futures import ThreadPoolExecutor
    from novel_engine.quality import punctuation_health as _ph

    jobs = []
    for pi in para_indexes:
        if isinstance(pi, int) and 0 <= pi < len(paragraphs):
            orig = paragraphs[pi].strip()
            if orig:
                jobs.append((pi, orig))
    if not jobs:
        return {}
    workers = max(1, min(int(max_workers or 1), len(jobs)))

    def _one(job):
        pi, orig = job
        for _ in range(2):
            try:
                cand = self._punct_only_repair(orig)
            except Exception:
                cand = None
            if (cand and _ph.is_punctuation_only_fix(orig, cand)
                    and not _ph.check_text_punctuation(cand)):
                return pi, cand
        return pi, None

    fixed_map = {}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="punctfix") as _ex:
        for pi, fixed in _ex.map(_one, jobs):
            if fixed is not None:
                fixed_map[pi] = fixed
    return fixed_map

def _repair_paragraphs_punct_batched(self, paragraphs, para_indexes):
    """CC round-22 Q4：把多个流水句自然段打包成“1 次”LLM 调用统一仅插标点。

    编号块协议输入/输出；逐段做去标点逐字合规校验(is_punctuation_only_fix)与
    单段健康复检，只有合规且健康的段进结果，其余退回由调用方并发兜底。
    整次调用失败或解析不到任何段 -> 返回 {}（调用方回退并发版）。
    """
    import re as _re
    from novel_engine.quality import punctuation_health as _ph

    jobs = []
    seen = set()
    for pi in para_indexes:
        if isinstance(pi, int) and 0 <= pi < len(paragraphs) and pi not in seen:
            orig = paragraphs[pi].strip()
            if orig:
                jobs.append((pi, orig))
                seen.add(pi)
    if not jobs:
        return {}

    def _blk(i, body):
        return f"<<<P{i}>>>\n{body}"

    user_tail = "\n".join(_blk(i, orig) for i, (_, orig) in enumerate(jobs))
    prompt = (
        "下面有多段中文小说叙事，各自缺少必要标点断句。请对每一段【仅在合适位置插入逗号、"
        "句号、顿号、分号、问号、叹号、引号】，使其符合正常中文叙事节奏。\n"
        "严禁增删/替换/改写任何文字，严禁调整语序，严禁增减词语，只允许插入标点。\n"
        "必须严格沿用下面的编号块格式逐段原样返回，编号顺序一致，不要任何解释、"
        "不要markdown代码块、不要引号包裹整段：\n"
        + "\n".join(f"<<<P{i}>>>" for i in range(len(jobs)))
        + "\n\n原文如下：\n" + user_tail
    )
    cap = min(8000, 200 + sum(len(o) for _, o in jobs) + 60 * len(jobs))
    try:
        resp = self.polish_router.chat_completion(
            [{"role": "user", "content": prompt}], temperature=0.2, max_tokens=cap)
        out = (resp or {}).get("content", "") or ""
    except Exception as e:
        logger.warning(f"batched punct-only call failed -> fall back to parallel: {e}")
        return {}
    out = out.strip()
    if out.startswith("```"):
        out = out.split("\n", 1)[1] if "\n" in out else out
        if out.rstrip().endswith("```"):
            out = out.rstrip()[:-3]
        out = out.strip()

    # 解析编号块
    marks = list(_re.finditer(r"<<<\s*P\s*(\d+)\s*>>>", out))
    if not marks:
        logger.warning("batched punct-only returned no parseable blocks -> fall back to parallel")
        return {}
    parsed = {}
    for k, m in enumerate(marks):
        end = marks[k + 1].start() if k + 1 < len(marks) else len(out)
        idx = int(m.group(1))
        body = out[m.end():end].strip()
        parsed[idx] = body

    fixed_map = {}
    for i, (pi, orig) in enumerate(jobs):
        cand = parsed.get(i)
        if not cand:
            continue
        if cand.startswith(("“", '"', "‘")) and cand.endswith(("”", '"', "’")):
            cand = cand[1:-1].strip()
        if (cand and _ph.is_punctuation_only_fix(orig, cand)
                and not _ph.check_paragraph_punctuation(cand)["is_unhealthy"]):
            fixed_map[pi] = cand
    return fixed_map

def _run_final_precommit_gate(self, chapter_num, task_card, final_text):
    """CC round-19 Q1：提交前对即将落盘的同一份文本做统一 fail-closed 终检。

    零成本谓词（latin/标点/脚手架头/半角标点/scope硬词）在此同一份字节上重跑。
    - latin/scope 漏到此处=上游门失效（流程异常），终检层不再重生，直接隔离转人工；
    - 标点/脚手架/半角属格式类，允许确定性或受限LLM修复1次，并在同一份文本复检；
      复检仍不过则硬阻断隔离转人工。
    mock 模式整体跳过（mock 文本无中文标点，避免误阻断离线流程）。
    """
    from novel_engine.quality import final_precommit_gate as fg
    from novel_engine.quality import punctuation_health as _ph19

    if (self.config.get("llm") or {}).get("use_mock"):
        return {"text": final_text, "hard_blocked": False, "violations": [], "repaired": False}

    def _scope_terms(txt):
        try:
            v = detect_scope_violations(txt, chapter_num, task_card.get("timeline_anchor"), self.root)
            return [x.get("term") for x in (v.get("hard") or []) if x.get("term")]
        except Exception:
            return []

    def _violations(txt):
        vs = list(fg.evaluate_final_text(txt)["violations"])
        terms = _scope_terms(txt)
        if terms:
            vs.append({"kind": "scope_hard_leak", "detail": f"scope硬词 {terms[:12]}"})
        return vs

    violations0 = _violations(final_text)
    if not violations0:
        return {"text": final_text, "hard_blocked": False, "violations": [], "repaired": False}

    immediate = [v for v in violations0 if v["kind"] in ("latin_leak", "scope_hard_leak")]
    repairable = {v["kind"] for v in violations0 if v["kind"] in fg.REPAIRABLE_KINDS}
    if immediate:
        return {"text": final_text, "hard_blocked": True, "violations": violations0, "repaired": False}

    # 格式类：确定性修复（剥指令头 / 半角转全角）+ 受限 LLM 仅插标点，一次后复检同一文本
    repaired = final_text
    if "scaffolding_header" in repairable:
        repaired = fg.strip_instruction_headers(repaired)
    if "halfwidth_punct" in repairable:
        repaired, _ = fg.normalize_halfwidth_punctuation(repaired)
    if "punctuation" in repairable:
        # 修复E（ch370 归因）：先零 LLM 确定性断句（每45字硬边界插句号，超长无句读段
        # 也能修），残余段再走 LLM 仅插标点——旧逻辑直接上 LLM，213/248 字符超长
        # 流水段连"仅插标点"都校验不过，最终 9/13 修完剩 4 段硬阻断。
        try:
            _z19, _zs19 = _ph19.repair_text_punctuation(repaired)
            if _zs19.get("residual_unhealthy", 1) == 0:
                repaired = _z19
                logger.info(f"Final gate ch{chapter_num}: zero-LLM deterministic "
                            f"punct split resolved all run-on paras "
                            f"({_zs19.get('changed_paragraphs', 0)} paras, "
                            f"{_zs19.get('inserts', 0)} pauses)")
            elif _z19 != repaired:
                repaired = _z19
                logger.info(f"Final gate ch{chapter_num}: zero-LLM punct split resolved "
                            f"partially, {_zs19.get('residual_unhealthy')} paras remain "
                            f"-> LLM punct repair fallback")
        except Exception as _ze19:
            logger.warning(f"Final gate zero-LLM punct split error ch{chapter_num}: {_ze19}")
        paras = repaired.split("\n")
        _bad_infos = _ph19.check_text_punctuation(repaired) or []
        _targets = [info.get("para_index") for info in _bad_infos]
        try:
            _mw = int(getattr(self.polish_router, "concurrency", 4) or 4)
        except Exception:
            _mw = 4
        _fixed_map = self._repair_paragraphs_punct_batched(paras, _targets)
        _rest = [pi for pi in _targets if pi not in _fixed_map]
        if _rest:
            _fix2 = self._repair_paragraphs_punct_parallel(paras, _rest, max_workers=_mw)
            _fixed_map.update(_fix2)
        for pi in sorted(_fixed_map):
            paras[pi] = _fixed_map[pi]
        repaired = "\n".join(paras).strip()
        if _fixed_map:
            logger.warning(
                f"Final gate punct repair ch{chapter_num}: {len(_fixed_map)}/{len(_targets)} "
                f"run-on paras fixed (1 batched call + {len(_rest)} parallel fallback, workers<={_mw})")

    after = _violations(repaired)
    if not after:
        return {"text": repaired, "hard_blocked": False, "violations": [], "repaired": True}
    return {"text": repaired, "hard_blocked": True, "violations": after, "repaired": True}

def _run_boundary_reprise_gate(self, chapter_num: int, task_card: dict,
                               scenes: list, assembled_text: str) -> str:
    """CC round-16 P0-2（经17轮校准）：相邻场景边界“近逐字/高字面重合”重演门。

    只抓表层可验证的边界复述（实词 Jaccard>=阈值且无推进词）；同义改写式语义重演
    不在此处理，交 reviewer pacing + E-loop。命中：确定性删后场首窗口重演段，过短则
    带负例定点重生。
    """
    from novel_engine.quality import boundary_reprise_gate as brg
    if not scenes or len(scenes) < 2:
        return assembled_text
    if (self.config.get("llm") or {}).get("use_mock"):
        return assembled_text
    cfg = brg.load_boundary_config(self.root)
    latest = {}
    for sc in scenes:
        latest[int(getattr(sc, "scene_id", 0) or 0)] = sc
    ordered = [latest[k] for k in sorted(latest)]
    hits = brg.detect_chapter_boundary_reprises(
        [{   "scene_id": getattr(s, "scene_id", 0), "scene_text": getattr(s, "scene_text", "") or ""}
         for s in ordered], cfg, task_card)
    if not hits:
        return assembled_text
    changed = False
    fired = sorted({h["next_scene"] for h in hits})
    logger.warning(f"Boundary reprise ch{chapter_num}: near-verbatim head reprise at scenes {fired} "
                   f"(ratios={[h['overlap_ratio'] for h in hits]})")
    for h in hits:
        sid = h["next_scene"]
        target = next((s for s in scenes if int(getattr(s, "scene_id", 0) or 0) == sid), None)
        if target is None:
            continue
        old = getattr(target, "scene_text", "") or ""
        new = brg.excise_head_reprise(old, cfg)
        if new != old and len(new) >= int((task_card.get("scene_target_chars") or 2000) * 0.4):
            target.scene_text = new
            changed = True
        else:
            # 删除后过短（或窗口即整段）→ 带负例定点重生
            directive = ("禁止在本场景开头复述上一场结尾已经演过的事件（尤其是刚发生的争执/冲突）。"
                         "至多承接一句转折（如‘都少说两句’/新角色介入），随即直接演出新的动作与情节，"
                         "不要重新铺陈已发生的争吵。")
            try:
                ns = self._regen_scene_for_id(
                    task_card, sid, scenes, negative_examples=[
                        "争吵声像沸水一般翻涌着。那妇人声音尖利……（上一场已演过这场争执，勿复述）"],
                    fix_directive=directive, frequency_penalty=0.4, presence_penalty=0.4)
                for idx, x in enumerate(scenes):
                    if int(getattr(x, "scene_id", 0) or 0) == sid:
                        scenes[idx] = ns
                changed = True
            except Exception as e:
                logger.warning(f"Boundary reprise regen failed scene{sid}: {e}")
        self._chapter_gate_fired = True
    if not changed:
        return assembled_text
    self.writer.last_scenes = list(scenes)
    cur = "\n\n".join((getattr(x, "scene_text", "") or "") for x in scenes)
    logger.info(f"Boundary reprise gate resolved ch{chapter_num}; {len(cur)} chars")
    return purify_novel_for_publish(cur, chapter_num=chapter_num)

def _apply_length_floor(self, chapter_num: int, task_card: dict, scenes: list, assembled_text: str) -> str:
    """CC round-9 P0-3：过全部确定性门后整章仍 < 软下限(默认6800) → 对最短的 1-2 个场景
    定向补写（感官/环境/内心，不新增事件）。单场景增量 ≤ 现字40%；补写文本必须过自相似
    检测（命中则收窄为仅环境/感官重试1次，仍注水则放弃保留原字）且不得重新触发 scope 硬门。
    最多 2 次补写调用；软下限是软的，不足也接受现状进入 reviewer。
    """
    try:
        _wp = load_quality_policy(self.root)
        target_total = int(_wp["chapter_target_chars"])
        target_min = int(target_total * _wp["min_ratio"])
    except Exception as e:
        logger.warning(f"Length floor policy load failed: {e}")
        return assembled_text
    cur_total = sum(len(getattr(x, "scene_text", "") or "") for x in scenes)
    if cur_total >= target_min or not scenes:
        return assembled_text
    deficit0 = target_min - cur_total
    order = sorted(range(len(scenes)),
                   key=lambda i: len(scenes[i].scene_text or ""))[:2]
    from concurrent.futures import ThreadPoolExecutor as _TPE2, as_completed as _asc2

    def _topup(idx):
        sc = scenes[idx]
        base = len(sc.scene_text or "")
        inc = min(deficit0, int(base * 0.4))
        if inc <= 120:
            return idx, ""
        prompt = (
            "在不改变已有情节走向、事件顺序和结尾的前提下，为本场景补写约"
            f"{inc}字。只允许：1) 把此前一笔带过的动作补成具体感官细节；"
            "2) 补一段不引入新事件的环境描写深化氛围；3) 若含对话，适度展开人物内心活动或微表情。"
            "严禁复述已写情节、新增任务卡之外的事件、重复已用比喻或意象、引入任何修炼/体系化名词。"
            "只返回补写并自然衔接后的完整场景正文，不要解释。\n\n" + (sc.scene_text or ""))
        try:
            out = call_llm(prompt, system_prompt="你是资深小说编辑，只在不改变情节的前提下补足细节",
                           output_json=False, client=self.refine_router)
        except Exception:
            out = ""
        return idx, (out or "")

    conc = int(getattr(self.refine_router, "concurrency", 2) or 2)
    results: dict[int, str] = {}
    with _TPE2(max_workers=max(1, min(conc, 2)), thread_name_prefix="floor") as _ex2:
        for _f in _asc2([_ex2.submit(_topup, i) for i in order]):
            try:
                idx, out = _f.result()
                results[idx] = out
            except Exception as e:
                logger.warning(f"Length floor topup failed: {e}")
    changed = False
    for idx, out in results.items():
        if not out:
            continue
        sc = scenes[idx]
        cand = str(out).strip()
        if len(cand) <= len(sc.scene_text or ""):
            continue
        try:
            if find_self_repetition(cand)[0]:
                base2 = len(sc.scene_text or "")
                p2 = ("仅用环境与感官细节（光影、雾气、声音、触感、气味）把下面场景自然补足到约"
                      f"{base2 + min(deficit0, int(base2 * 0.4))}字，不新增事件、不新增台词、"
                      "不引入设定名词、不重复已有比喻，只返回完整正文：\n\n" + (sc.scene_text or ""))
                cand2 = str(call_llm(
                    p2, system_prompt="你是资深小说编辑", output_json=False,
                    client=self.refine_router) or "").strip()
                if not cand2 or find_self_repetition(cand2)[0] or len(cand2) <= base2:
                    logger.info(f"Length floor scene{getattr(sc,'scene_id',0)} repeated after retry; keep original")
                    continue
                cand = cand2
        except Exception:
            continue
        try:
            v = detect_scope_violations(cand, chapter_num, task_card.get("timeline_anchor"), self.root)
            if v.get("hard"):
                logger.info(
                    f"Length floor scene{getattr(sc,'scene_id',0)} reintroduced scope hard "
                    f"{[x.get('term') for x in v.get('hard')]}; keep original")
                continue
        except Exception:
            pass
        sc.scene_text = cand
        changed = True
    if not changed:
        return assembled_text
    self.writer.last_scenes = list(scenes)
    cur = "\n\n".join((getattr(x, "scene_text", "") or "") for x in scenes)
    logger.info(f"Length floor ch{chapter_num}: {cur_total} -> {len(cur)} (soft min {target_min})")
    return purify_novel_for_publish(cur, chapter_num=chapter_num)

def _run_boundary_gate(self, chapter_num: int, task_card: dict, scenes: list, assembled_text: str) -> str:
    """CC round-7 P0-3: post-assembly cross-chapter replay gate.

    Stage1 (zero LLM) screens current vs the previous committed chapter; a
    candidate gets one Stage2 LLM four-criterion judgement; a confirmed replay is
    repaired by regenerating the single most-overlapping scene with the previous
    chapter's end_state as a precise negative. Stage2 errors fail OPEN (a legit
    callback must not be killed by infra). If the similarity survives the bounded
    repair it becomes a deterministic hard block so the chapter is isolated, never
    silently published.
    """
    self._boundary_hard = []
    if not scenes or int(chapter_num or 0) <= 1:
        return assembled_text
    prev_path = novel_chapter_path(self.root, chapter_num - 1)
    if not prev_path.exists():
        return assembled_text
    try:
        prev_text = prev_path.read_text(encoding="utf-8")
    except OSError:
        return assembled_text

    def _assemble() -> str:
        return "\n\n".join((getattr(s, "scene_text", "") or "") for s in scenes)

    cur = _assemble()
    if not cur.strip():
        return assembled_text
    st = stage1_score(cur, prev_text)
    if not st["candidate"]:
        logger.info(f"Boundary Stage1 pass for ch{chapter_num} ({st})")
        return assembled_text
    logger.warning(f"Boundary Stage1 candidate for ch{chapter_num} {st} → Stage2 LLM")

    es = latest_end_state(self.root, chapter_num) or {}
    prompt = build_stage2_prompt(es, prev_text, cur)
    try:
        raw = self.outline_router.chat_completion(
            [{"role": "user", "content": prompt}], temperature=0.0, max_tokens=400)
        content = raw.get("content", "") if isinstance(raw, dict) else str(raw)
        verdict = parse_stage2_verdict(content)
    except Exception as e:  # fail-open: never let judge infra kill a callback chapter
        logger.warning(f"Boundary Stage2 failed-open ({e}); treat as no replay")
        return assembled_text
    if not verdict.get("ok") or not verdict.get("replay"):
        logger.info(f"Boundary Stage2 judged no replay for ch{chapter_num}: {verdict.get('reason', '')}")
        return assembled_text

    scores = scene_overlap_scores(
        {getattr(s, "scene_id", i + 1): (getattr(s, "scene_text", "") or "")
         for i, s in enumerate(scenes)}, prev_text)
    if not scores:
        return assembled_text
    sid = max(scores, key=lambda k: scores[k])
    completed = "、".join(es.get("completed_actions", []) or [])
    position = str(es.get("narrative_position", "") or "")
    pending = "、".join(es.get("pending_actions", []) or []) or "紧接其后的新情节"
    directive = (
        f"上一章已完整演完：{completed}；上一章停在：{position}。"
        "本场景却把这些动作的发生经过重新搬演了一遍（跨章重演）。"
        "本次严禁倒回重演其发生过程，只允许用一两句概括其结果作为承接；"
        f"场景必须直接从“{pending}”或更靠后的新时空节点开场，全部笔墨用于推进新事件。"
    )
    logger.warning(f"Boundary Stage2 confirmed replay for ch{chapter_num}; regen scene {sid}")
    try:
        new_scene = self._regen_scene_for_id(
            task_card, sid, scenes, fix_directive=directive,
            frequency_penalty=0.4, presence_penalty=0.4)
    except Exception as e:
        logger.warning(f"Boundary scene regen failed ({e}); raise hard block")
        self._boundary_hard = [f"[跨章重演] Stage2 判定重演但定点重生失败: {e}"]
        return assembled_text
    for idx, s in enumerate(scenes):
        if getattr(s, "scene_id", None) == sid:
            scenes[idx] = new_scene
    try:
        append_scene(self.root, chapter_num,
                     {"scene_id": sid, "scene_text": new_scene.scene_text,
                      "hook": new_scene.hook, "beats": list(new_scene.beats or [])})
    except Exception as je:
        logger.warning(f"Boundary journal append failed for scene {sid}: {je}")
    self.writer.last_scenes = list(scenes)

    cur2 = _assemble()
    st2 = stage1_score(cur2, prev_text)
    purified2 = purify_novel_for_publish(cur2, chapter_num=chapter_num)
    if st2["candidate"]:
        self._boundary_hard = [
            f"[跨章重演] 定点重生场景{sid}后仍与上一章高度重合 {st2}"]
        logger.warning(f"Boundary still candidate after scene {sid} regen {st2} → hard block")
    else:
        logger.info(f"Boundary resolved after scene {sid} regen {st2}")
    return purified2


from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class ForbiddenGateResult:
    """Result of evaluating the forbidden gate."""
    apply_world_state: bool
    success: bool
    violations: list = field(default_factory=list)
    flag_human: bool = False
    flag_reason: str = ""


def evaluate_forbidden_gate(
    chapter_num: int,
    current_novel: str,
    review: dict,
    policy: dict,
    apply_world_state: bool,
    forbidden_violations_fn: Callable,
) -> ForbiddenGateResult:
    """Evaluate the forbidden gate and return structured result.
    
    This is a pure function that does NOT mutate self directly.
    Callers must handle side effects (defects.add, flag_for_human, result updates).
    """
    violations: list = []
    if not apply_world_state:
        return ForbiddenGateResult(
            apply_world_state=True,
            success=True,
            violations=[],
        )
    
    violations = forbidden_violations_fn(current_novel, review)
    if not violations:
        return ForbiddenGateResult(
            apply_world_state=True,
            success=True,
            violations=[],
        )
    
    # Log non-blocking hits
    blocking = [v for v in violations 
                if _forbidden_violation_is_blocking(policy, v)]
    
    if blocking:
        return ForbiddenGateResult(
            apply_world_state=False,
            success=False,
            violations=violations,
            flag_human=True,
            flag_reason="forbidden violation",
        )
    else:
        # Non-blocking: just record, don't fail
        return ForbiddenGateResult(
            apply_world_state=True,
            success=True,
            violations=violations,
        )




# ============================================================================
# Fix Loop: Early Stop Condition (Phase 3.1)
# ============================================================================

def check_fix_loop_early_stop(
    iteration: int,
    no_improve: int,
    best_score: float,
) -> bool:
    """Check if fix loop should stop early due to no improvement.
    
    Returns True if should break, False if should continue.
    """
    if no_improve >= 2 and iteration >= 2:
        logger.warning(f"Fix loop no improve for {no_improve} rounds (best {best_score}), early stop")
        return True
    return False





# ============================================================================
# Fix Loop: Best Candidate Tracking (Phase 3.1)
# ============================================================================

@dataclass
class BestCandidateState:
    """Tracks the best candidate across fix loop iterations."""
    best_score: float
    best_novel: str
    best_draft: str
    best_journal_snapshot: list
    best_green: Optional[Tuple[float, str, str, list]] = None
    no_improve: int = 0


def track_best_candidate(
    state: BestCandidateState,
    score: float,
    current_novel: str,
    current_draft: str,
    deterministic_passed: bool,
    has_high_issue: bool,
    snapshot_fn: Callable[[], list],
) -> BestCandidateState:
    """Update best candidate tracking after each fix loop iteration."""
    if score > state.best_score:
        new_green = None
        if deterministic_passed and not has_high_issue:
            new_green = (score, current_novel, current_draft, snapshot_fn())
        return BestCandidateState(
            best_score=score,
            best_novel=current_novel,
            best_draft=current_draft,
            best_journal_snapshot=snapshot_fn(),
            best_green=new_green,
            no_improve=0,
        )
    else:
        new_green = state.best_green
        if deterministic_passed and not has_high_issue:
            if new_green is None or score > new_green[0]:
                new_green = (score, current_novel, current_draft, snapshot_fn())
        return BestCandidateState(
            best_score=state.best_score,
            best_novel=state.best_novel,
            best_draft=state.best_draft,
            best_journal_snapshot=state.best_journal_snapshot,
            best_green=new_green,
            no_improve=state.no_improve + 1,
        )
