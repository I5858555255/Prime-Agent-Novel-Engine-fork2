"""Staged expansion loop: expand -> count -> continue-writing until in range.

Continues from an existing draft (or calls the full expansion prompt when no
draft is given) until the cleaned prose is inside the manifest's expanded range.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

import httpx

from novel_engine.core.env_bootstrap import load_project_env

from .accept_check import check_prose, extract_prose, han_count, load_card, load_manifest, INFANT_SELF_AWARENESS
from .contracts import dumps
from .synonym_repair import deterministic_repair
from . import observability, quality_store
from .providers import load_provider_full
from .quality_judge import judge_prose
from .volume_knobs import knob_set_hash

CONTINUE_TEMPLATE = """你是本小说的正文续写者。以下是已完成部分和剩余任务。

已完成正文（不得改写，只能续写）：
{existing}

任务：续写至全章共 {target} 汉字（当前 {current} 字，还差约 {remaining} 字）。
- 续写紧接已完成正文最后一句，保持人称、视角、文风一致。
- 遵守全部资料规则（认知边界、对话意图、场景间连续性、伏笔尺度）。
- 只输出续写的新增正文段落，不要重复已完成部分，不要添加任何标签或说明。
- 续写结束后，落在章节简写的终点状态。
{forbidden_terms}
{materials}
"""

REPAIR_TEMPLATE = """你是本小说的正文修订者。以下正文因命中认知边界禁词/越界短语而被验收阻断。
请做【最小修订】：只把列出的违规词/短语替换为中性、合月龄、合认知边界的等价表达，保留情节、因果、人物声线、字数规模与章末状态不变。
不得新增设定、不得改写其他未违规段落、不得改变场景顺序。只输出修订后的完整正文，不加任何标签或说明。

【必须替换的违规词/短语】
{violations}
{forbidden_terms}
【被阻断的正文】
{prose}
"""

REPAIR_EXPAND_TEMPLATE = """你是本小说的正文修订兼扩写者。以下正文因命中认知边界禁词/越界短语被验收阻断，且字数未达目标。
请做【最小修订 + 补足字数】：
1. 把列出的违规词/短语替换为中性、合月龄、合认知边界的等价表达；
2. 在保留情节、因果、人物声线、场景顺序与章末状态的前提下，补足正文至全章约 {target} 汉字（当前 {current} 字，还差约 {remaining} 字）；
3. 只输出修订并补足后的完整正文，不加任何标签或说明。
{forbidden_terms}
【必须替换的违规词/短语】
{violations}
【被阻断的正文】
{prose}
"""

SAT_REPAIR_TEMPLATE = """你是本小说的正文爽点强化修订者。以下正文经质检判定爽点低于目标（当前爽点判分偏低），
读者情绪峰值不足、压抑未被兑现。请做【爽点强化修订】：
1. 把本章设计的爽点（payoff：打脸/反转/能力升级/解压抑）在前1/3篇幅内引爆——让读者看到压抑被反将一军、实力或信息差打脸、获得认可或资源；
2. 负面情节（羞辱/轻视/规则刁难）必须在同一场景或紧邻场景给出反转，不得悬置；
3. 用短句+动作+结果直接呈现情绪峰值，不靠内心独白堆砌；
4. 保留情节、因果、人物声线、场景顺序、字数规模（约 {target} 汉字）与章末钩子不变，只强化爽点节奏。
爽点目标下限：{floor_satisfaction}/5。
{payoff_hint}
{forbidden_terms}
【需爽点强化的正文】
{prose}
"""


def _payoff_hint(card: dict) -> str:
    """Extract the chapter's designed payoff beats as a re-expand hint.

    A card carrying a payoff beat (L1) names it so the L3 re-expand can ignite
    that exact payoff. No payoff beats -> a generic hint. Deterministic.
    """
    lines = []
    for scene in card.get("scenes", []) if isinstance(card, dict) else []:
        if not isinstance(scene, dict):
            continue
        for beat in scene.get("beats", []):
            if not isinstance(beat, dict):
                continue
            payoff = beat.get("payoff")
            if isinstance(payoff, dict) and (payoff.get("type") or payoff.get("trigger")):
                lines.append(f"- 场景{scene.get('id')} beat {beat.get('id')}: "
                             f"爽点={payoff.get('type')} 引爆='{payoff.get('trigger')}' "
                             f"释放来源={payoff.get('release_of','')} 强度={payoff.get('intensity','?')}/5")
    if not lines:
        return "（本章未显式设计 payoff beat；按打脸/反转/升级/解压抑自定一处在前1/3引爆）"
    return "本章设计的爽点（必须引爆）：\n" + "\n".join(lines)


def _collect_violations(report: dict) -> list[str]:
    """Gather the offending terms/phrases from a blocking report, for a repair prompt."""
    found: list[str] = []
    for block in report.get("blocking", []):
        detail = block.get("detail", "")
        # "stage-forbidden terms (chN): ['频率']" and "infant POV self-analysis phrases: ['他意识到']"
        import re
        m = re.search(r"\[([^\]]*)\]", detail)
        if m:
            items = [x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()]
            found.extend(items)
    # De-dup, keep order
    out, seen = [], set()
    for v in found:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _forbidden_terms_block(chapter: int) -> str:
    from .accept_check import stage_forbidden_terms, INFANT_SELF_AWARENESS
    terms = list(stage_forbidden_terms(chapter))
    self_aware = list(INFANT_SELF_AWARENESS)
    if not terms and not self_aware:
        return ""
    lines = [
        "\n【认知边界禁词（本章正文不得出现，违反即验收阻断）】",
    ]
    if terms:
        lines.append("未解锁概念词：" + "、".join(terms))
    if self_aware:
        lines.append("婴儿视角自我分析短语（月龄<3 的陆烬章节禁止）：" + "、".join(self_aware))
    lines.append("如需描述相关现象，改用中性观察语言（如呼吸节奏、气息起伏、生理反应），"
                 "不得引用修炼机制名词。正文中不得出现“主线第N天”等元数据标签。\n")
    return "\n".join(lines)


def _llm(base_url: str, api_key: str, model: str, messages: list[dict],
         max_tokens: int, temperature: float,
         reasoning_effort: str | None = None) -> tuple[str, dict]:
    """One polish call. Returns (content, usage) where usage carries
    total_tokens/prompt_tokens/completion_tokens + usage_estimated (True when
    the provider returned no `usage` and we estimated: CJK≈1 token/char,
    ASCII≈4 chars/token, mirroring llm_client._estimate_usage)."""
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    body: dict = {"model": model, "messages": messages,
                  "max_tokens": max_tokens, "temperature": temperature}
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort
    with httpx.Client(timeout=httpx.Timeout(connect=15, read=300, write=30, pool=30)) as c:
        r = c.post(url, headers={"Authorization": f"Bearer {api_key}"}, json=body)
        r.raise_for_status()
        payload = r.json()
    content = payload["choices"][0]["message"]["content"]
    usage = payload.get("usage") or {}
    if not usage.get("total_tokens"):
        prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
        prompt_est = observability._estimate_tokens("x" * prompt_chars)
        completion_est = observability._estimate_tokens(content)
        usage = {"total_tokens": int(prompt_est + completion_est),
                 "prompt_tokens": int(prompt_est), "completion_tokens": int(completion_est),
                 "usage_estimated": True}
    else:
        usage = dict(usage)
        usage.setdefault("prompt_tokens", 0)
        usage.setdefault("completion_tokens", usage.get("total_tokens", 0))
        usage["usage_estimated"] = False
    return content, usage


def expand_until_range(package: Path, base_url: str, api_key: str, model: str,
                       initial_draft: Path | None = None, max_rounds: int = 5,
                       target: int | None = None, out_dir: Path | None = None,
                       temperature: float | None = None,
                       reasoning_effort: str | None = None,
                       trace_id: str | None = None, spec: dict | None = None,
                       store=None) -> dict:
    card = load_card(package)
    manifest = load_manifest(package)
    lo, hi = manifest.get("expanded_range", [4000, 5000])
    target = target or manifest.get("expanded_target_han") or 4210

    if trace_id is None:
        trace_id = quality_store.new_trace_id()
    run_id_counter = {"n": 0}
    total_expansion_tokens = 0

    def _call_llm(messages, max_tokens, temperature, reasoning_effort=None):
        nonlocal total_expansion_tokens
        content, usage = _llm(base_url, api_key, model, messages,
                              max_tokens, temperature, reasoning_effort=reasoning_effort)
        run_id = f"{trace_id}-{run_id_counter['n']}"
        run_id_counter["n"] += 1
        total_expansion_tokens += int(usage.get("total_tokens", 0))
        if store is not None:
            observability.record_expansion_call(store.db, trace_id, run_id,
                                                usage.get("total_tokens", 0), model,
                                                prompt_ver=None,
                                                usage_estimated=usage.get("usage_estimated", False))
        return content

    prompt_text = (package / "扩写提示词.md").read_text(encoding="utf-8")
    cut = prompt_text.find("## 全书设定.md")
    materials = prompt_text[cut:] if cut != -1 else ""

    if initial_draft:
        raw = initial_draft.read_text(encoding="utf-8-sig")
    else:
        raw = _call_llm([{"role": "system",
                          "content": "你是专业小说正文扩写者。严格遵守所有资料规则。只输出纯正文，不加任何标签或说明。"
                                     + _forbidden_terms_block(manifest.get("chapter", 0))},
                         {"role": "user", "content": prompt_text}],
                       max_tokens=10000, temperature=0.85)

    rounds: list[dict] = []
    prose = raw
    repair_rounds_used = 0
    for i in range(max_rounds + 1):
        prose, notes = extract_prose(prose)
        han = han_count(prose)
        # Intermediate rounds: compliance-only (spec=None) — the knob-set judge
        # verdict is applied to the FINAL check after the loop.
        report = check_prose(prose, card, manifest, spec=None)
        verdict = "PASS" if not report["blocking"] else "BLOCK"
        violations = _collect_violations(report)
        rounds.append({"round": i, "han": han, "verdict": verdict,
                       "blocking": report["blocking"], "warnings": report["warnings"],
                       "strip_notes": notes, "violations": violations})
        if verdict == "PASS":
            break
        # Gap 2: a block caused purely by forbidden terms / infant self-awareness
        # phrases is reword-able. Spend a targeted repair round (reword only the
        # violations) instead of stopping at the first block.
        if violations and repair_rounds_used < max(1, max_rounds // 2):
            repair_rounds_used += 1
            if han < lo:
                fix_prompt = REPAIR_EXPAND_TEMPLATE.format(
                    target=target, current=han, remaining=max(0, min(hi, target) - han),
                    violations="、".join(violations),
                    forbidden_terms=_forbidden_terms_block(manifest.get("chapter", 0)),
                    prose=prose)
            else:
                fix_prompt = REPAIR_TEMPLATE.format(
                    violations="、".join(violations),
                    forbidden_terms=_forbidden_terms_block(manifest.get("chapter", 0)),
                    prose=prose)
            pre_han = han_count(prose)
            fixed = _call_llm([{"role": "system", "content": "你是小说正文修订者。只输出修订后的完整正文，不加标签或说明。"},
                               {"role": "user", "content": fix_prompt}],
                              max_tokens=10000, temperature=0.3)
            fixed_clean, _ = extract_prose(fixed)
            # Non-destructive repair: adopt only if it preserves most of the
            # prose (guards against a whole-chapter collapse to near-zero that
            # strands the chapter under-budget). Otherwise keep the original
            # prose and fall through to continuation.
            if han_count(fixed_clean) >= max(0.5 * pre_han, pre_han - 600):
                prose = fixed_clean
            else:
                print(f"[expand ch{manifest['chapter']}] repair dropped {pre_han} -> "
                      f"{han_count(fixed_clean)} han; kept original prose", flush=True)
            continue
        # Target-aware continuation: only append when still under target. Once the
        # prose is at/over target (or already in-window), stop appending -- the
        # old `han > hi` check overshot because a +4000-token append jumped
        # from under-lo to over-hi.
        if han >= lo:
            break  # in-window or over; trim handled after the loop
        if i == max_rounds:
            break
        remaining = min(hi, target) - han
        cont_prompt = CONTINUE_TEMPLATE.format(existing=prose, target=target,
                                               current=han, remaining=remaining,
                                               forbidden_terms=_forbidden_terms_block(manifest.get("chapter", 0)),
                                               materials=materials)
        continuation = _call_llm([{"role": "system", "content": "你是小说正文续写者。只输出新增正文，不重复已有内容。"},
                                   {"role": "user", "content": cont_prompt}],
                                  max_tokens=4000, temperature=0.85)
        prose = prose + "\n\n" + continuation

    # Post-loop trim: if a repair/continuation left the prose over `hi`, drop
    # trailing paragraphs down to `lo..hi` so word_count clears.
    final_prose, _ = extract_prose(prose)
    final_han = han_count(final_prose)
    if final_han > hi:
        para = final_prose.strip().split("\n\n")
        trimmed = final_prose
        while han_count(trimmed) > hi and len(para) > 1:
            para = para[:-1]
            trimmed = "\n\n".join(para)
            if han_count(trimmed) < lo:
                break
        final_prose = trimmed
        if han_count(final_prose) != final_han:
            print(f"[expand ch{manifest['chapter']}] trimmed {final_han} -> "
                  f"{han_count(final_prose)} han to fit range", flush=True)
    # Deterministic residual repair: replace any remaining stage-forbidden terms /
    # infant self-awareness phrases with their neutral synonyms so the program
    # checker passes without an LLM round or a waiver.
    repaired, replaced_tokens, missed_tokens = deterministic_repair(final_prose, manifest.get("chapter", 0))
    if replaced_tokens:
        print(f"[expand ch{manifest['chapter']}] deterministic repair replaced: {replaced_tokens}",
              flush=True)
    if missed_tokens:
        print(f"[expand ch{manifest['chapter']}] deterministic repair MISSED (will fall back to waiver): {missed_tokens}",
              flush=True)
    final_prose = repaired
    out = out_dir or package
    final_path = out / f"expanded_draft_ch{manifest['chapter']:04d}.md"
    final_path.write_text(final_prose, encoding="utf-8")
    summary = {
        "chapter": manifest["chapter"],
        "package": str(package),
        "target": target,
        "range": [lo, hi],
        "rounds": rounds,
        "repair_rounds_used": repair_rounds_used,
        "deterministic_repair": replaced_tokens,
        "deterministic_missed": missed_tokens,
        "final_han": han_count(final_prose),
        "final_draft": str(final_path),
        "trace_id": trace_id,
        "total_expansion_tokens": total_expansion_tokens,
    }
    # Reflect the trimmed deliverable in the pass flag (a trim can clear a
    # word_count block that the last round still showed). spec + the judge
    # verdict apply to the FINAL check only (intermediate rounds ran with
    # spec=None) — one critic call (a parse/timeout retry makes ≤2).
    verdict = None
    if spec is not None:
        if store is None:
            raise ValueError("expand_until_range with spec requires a store")
        judge_provider = load_provider_full("judge")
        verdict = judge_prose(final_prose, spec, judge_provider, trace_id,
                              knob_set_hash_arg=knob_set_hash(spec))
    # L3 爽点自动返工：verdict 爽点 < floor（spec 的 爽点 维度）或 arc climax 违规时，
    # 用 payoff beat 做定向重扩，每章最多 SAT_RETRY_CAP 轮；超了 HARD_BLOCK（防死循环）。
    # 仅在 spec（knob set）已判分时生效；无 spec（back-compat）跳过，不放大成本。
    sat_retries = 0
    if spec is not None and store is not None:
        from novel_engine.condensed import satisfaction, story_arc
        arc = story_arc.load_arc(satisfaction.arc_path())
        # Per-chapter floor (L4 dual-target): a climax chapter targets the high
        # climax floor (spec 爽点, e.g. 4); a non-climax / suppression-exempt
        # chapter targets the lower NON_CLIMAX_SAT_FLOOR. This is what L3 re-
        # expand chases, so suppression-heavy chapters don't burn all 3 rounds
        # chasing a climax bar they are designed to miss.
        climax_floor = spec.get("爽点")
        chapter_num = manifest["chapter"]
        floor_sat = satisfaction.satisfaction_floor_for(chapter_num, arc, climax_floor) \
            if (arc is not None or spec is not None) else climax_floor
        card_for_arc = card if isinstance(card, dict) else {}
        cur_scores = verdict
        while sat_retries < satisfaction.SAT_RETRY_CAP and floor_sat is not None:
            arc_violation = False
            if arc is not None:
                # check_arc(chapter, verdict, arc, history) — same order viral_loop uses.
                arc_res = story_arc.check_arc(manifest["chapter"], cur_scores, arc, history=[])
                arc_violation = any("climax" in v or "suppression" in v
                                    for v in arc_res.get("violations", []))
            decision = satisfaction.satisfaction_retry_decision(
                cur_scores, {"爽点": floor_sat}, arc_violation,
                round_no=sat_retries + 1, cap=satisfaction.SAT_RETRY_CAP)
            if decision["action"] == "pass":
                break
            if decision["action"] == "hard_block":
                print(f"[expand ch{manifest['chapter']}] satisfaction HARD_BLOCK: "
                      f"{decision['reason']}; stopping re-expand", flush=True)
                break
            # retry_satisfaction: targeted payoff re-expand (L3)
            sat_retries += 1
            payoff_hint = _payoff_hint(card)
            sat_fix = SAT_REPAIR_TEMPLATE.format(
                target=target, current=han_count(final_prose),
                floor_satisfaction=floor_sat,
                payoff_hint=payoff_hint,
                forbidden_terms=_forbidden_terms_block(manifest.get("chapter", 0)),
                prose=final_prose)
            sat_out = _call_llm([{"role": "system",
                                  "content": "你是小说正文爽点强化修订者。只输出修订后的完整正文，不加标签或说明。"},
                                 {"role": "user", "content": sat_fix}],
                                max_tokens=10000, temperature=0.5)
            sat_clean, _ = extract_prose(sat_out)
            if han_count(sat_clean) < max(0.5 * han_count(final_prose), han_count(final_prose) - 800):
                print(f"[expand ch{manifest['chapter']}] satisfaction repair collapsed the "
                      f"prose; keeping the previous draft", flush=True)
                break
            final_prose = sat_clean
            verdict = judge_prose(final_prose, spec, judge_provider, trace_id,
                                  knob_set_hash_arg=knob_set_hash(spec))
            cur_scores = verdict
    final_report = check_prose(final_prose, card, manifest, spec=spec, verdict=verdict)
    summary["final_blocking"] = [b.get("check") for b in final_report["blocking"]]
    summary["quality_notes"] = final_report.get("quality_notes", [])
    summary["satisfaction_retries"] = sat_retries
    summary["pass"] = not final_report["blocking"] and lo <= han_count(final_prose) <= hi
    if spec is not None:
        prose_sha256 = hashlib.sha256(final_prose.encode("utf-8")).hexdigest()
        quality_store.record_verdict(
            store.db, store, manifest["chapter"], verdict, prose_sha256,
            trace_id, knob_set_hash=knob_set_hash(spec),
            judge_model=verdict.get("judge_model"), prompt_ver=verdict.get("prompt_ver"))
        summary["verdict_recorded"] = True
    report_path = out / f"expand_report_ch{manifest['chapter']:04d}.json"
    report_path.write_text(dumps(summary), encoding="utf-8")
    return summary


def main(argv=None) -> int:
    load_project_env()
    parser = argparse.ArgumentParser(description="Staged expansion until word count is in range")
    parser.add_argument("--package", type=Path, required=True, help="expansion package directory")
    parser.add_argument("--draft", type=Path, help="optional initial draft; omit to call the full expansion prompt")
    parser.add_argument("--max-rounds", type=int, default=5)
    parser.add_argument("--json", action="store_true", help="print JSON summary")
    args = parser.parse_args(argv)

    from .providers import load_provider_full
    base_url, api_key_env, model, opts = load_provider_full("polish")
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        print(f"missing env var {api_key_env}", file=sys.stderr)
        return 2
    summary = expand_until_range(args.package, base_url=base_url, api_key=api_key,
                                 model=model, initial_draft=args.draft,
                                 max_rounds=args.max_rounds,
                                 temperature=opts.get("temperature"),
                                 reasoning_effort=opts.get("reasoning_effort"))
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"chapter {summary['chapter']}: {len(summary['rounds'])} rounds, "
              f"final_han={summary['final_han']}, pass={summary['pass']}, "
              f"draft={summary['final_draft']}")
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
