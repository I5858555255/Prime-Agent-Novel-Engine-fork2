"""Bounded production with durable budgets, independent review and atomic commits."""
from __future__ import annotations

import argparse
import json
import logging
import re
import copy
import os
import uuid
import threading
import time
from dataclasses import asdict
from pathlib import Path

from novel_engine.core.env_bootstrap import load_project_env
from novel_engine.core.execution import AttemptBudget, Execution
from novel_engine.core.model_router import ModelRouter
from novel_engine.core.publication_transaction import atomic_write, publication_lock

from .contracts import (ProductionConfig, assign_plan_budgets, assign_scene_budgets, assign_state_preconditions, bind_review_evidence, digest, dumps, dumps_compact,
                       parse_object, validate_chapter, validate_plan, validate_review)
from .context import scope_canon
from .draft_guard import draft_guard, draft_guard_terms_for, skeleton_lock
from .export import archive_exports, compile_manuscript, export_chapter, verify_export
from .mid_outline_loader import load_mid_outline_batch, build_plan_digest
from .prompts import DRAFT, PLAN, REPAIR, REVIEW, SYSTEM
from .render import generation_view, render_brief
from .store import Store
from .timeline import normalize_timeline, phase, validate_timeline
from . import quality_store, volume_knobs

logger = logging.getLogger(__name__)


class QualityFailure(RuntimeError):
    pass


class DurableBudget(AttemptBudget):
    def __init__(self, store, name, config):
        extra_calls, extra_seconds = store.allowance(name)
        super().__init__(name, max_llm_calls=config.max_calls_per_operation + extra_calls,
                         max_wall_seconds=config.max_seconds_per_operation + extra_seconds)
        self.store, self.name = store, name
        self.calls, elapsed = store.operation(name)
        self.started -= elapsed

    def request(self):
        super().request()
        self.store.charge_request(self.name)


def load_canon(source):
    source = Path(source)
    mapping = {"world": "world_bible", "characters": "character_bible", "rules": "decision_rules",
               "intent": "author_intent", "ending": "ending_bible", "style": "style_bible"}
    canon = {key: (source / "bible" / f"{name}.md").read_text(encoding="utf-8") for key, name in mapping.items()}
    milestones = json.loads((source / "config" / "condensed_milestones.json").read_text(encoding="utf-8"))
    canon["milestones"] = milestones["nodes"]
    canon["planning_policy"] = milestones["policy"]
    canon["hash"] = digest(canon)
    return canon


class CondensedPipeline:
    def __init__(self, source, output, config=None, generation=None, review=None, refresh_uncommitted_canon=False):
        self.source, self.root = Path(source).resolve(), Path(output).resolve()
        if self.root == self.source or self.root.parent == self.source:
            raise ValueError("condensed output must be separate from source canon")
        self.settings = config or ProductionConfig()
        self.store = Store(self.root)
        self.canon = load_canon(self.source)
        identity = {"schema": 1, "config": asdict(self.settings), "canon_hash": self.canon["hash"]}
        existing = self.store.metadata("identity")
        if existing and existing != identity and refresh_uncommitted_canon:
            if self.store.head() or existing["config"] != identity["config"]:
                self.store.close()
                raise ValueError("canon refresh requires zero committed chapters and unchanged production budgets")
            self.store.metadata("previous_identity_" + existing["canon_hash"], existing)
            with self.store.db:
                self.store.db.execute("DELETE FROM plans")
            existing = None
        if existing and existing != identity:
            self.store.close()
            raise ValueError("canon/config changed; explicit migration or a new output project is required")
        self.store.metadata("identity", identity)
        self.store.prefix()
        for trace_path in (self.root / "audit" / "latency").glob("*.json"):
            self.store.record_trace(json.loads(trace_path.read_text(encoding="utf-8")))
        atomic_write(self.root / "project.json", dumps(identity))
        atomic_write(self.root / "canon.json", dumps(self.canon))
        self.generation = generation
        self.reviewer = review
        self.config = {"pipeline": {"attempt_budget": {"max_llm_calls": self.settings.max_calls_per_operation,
                                                        "max_wall_seconds": self.settings.max_seconds_per_operation}}}
        self.active = None
        self._execution_lock = threading.RLock()

    def _routers(self):
        if self.generation is None:
            self.generation = ModelRouter("director", self.source)
        if self.reviewer is None:
            self.reviewer = ModelRouter("review", self.source)

    def close(self):
        for router in (self.generation, self.reviewer):
            for client in getattr(router, "providers", {}).values():
                client.close()
        self.store.close()

    def _start(self, name):
        self.active = Execution(self, name)
        self.active.budget = DurableBudget(self.store, name, self.settings)
        for router in (self.generation, self.reviewer):
            router._chapter_execution = self.active
            for client in getattr(router, "providers", {}).values():
                client._chapter_execution = self.active

    def _ask(self, router, prompt, max_tokens=9000, temperature=0.5):
        started = time.monotonic()
        try:
            response = router.chat_completion(
                [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                max_tokens=max_tokens, temperature=temperature)
            atomic_write(self.root / "audit" / "responses" / f"{self.active.run_id}_{self.active.budget.calls}.json",
                         dumps({"operation": self.active.chapter, "prompt_hash": digest(prompt),
                                "prompt": prompt, "system_prompt": SYSTEM,
                                "response": response}))
            if response.get("finish_reason") == "length":
                raise ValueError("truncated JSON response; not eligible for adoption")
            return parse_object(response.get("content", ""))
        finally:
            self.store.save_wall(self.active.chapter, time.monotonic() - self.active.budget.started)
            trace = self.active.save({"success": False, "published": False})
            self.store.record_trace(trace)
            logger.info("operation=%s call wall_s=%.3f", self.active.chapter, time.monotonic() - started)

    def _context(self, cast=None, through=None):
        chapter = self.store.head() + 1
        if cast is None:
            cast = {name.strip() for name in re.findall(r"^### C\d+ ([^（\n]+)", self.canon["characters"], re.MULTILINE)}
        cast = set(cast) | self.store.recent_cast()
        return {"canon": scope_canon(self.canon, chapter, through or chapter), "continuity": self.store.context(cast),
                "production": {"chapters": self.settings.chapters,
                               "expanded_total_han": self.settings.total_expanded_han}}

    def _draft_guard(self, chapter):
        """Generation-time forbidden-terms block for this chapter's draft/repair rounds.

        Surfaced from the SAME authority the validator enforces
        (accept_check stage terms, concept_unlocks.json driven) so the model
        avoids the collision on the first draft instead of paying repair rounds.
        """
        return draft_guard(chapter)

    def _op_draft_guard(self, name: str):
        """Guard block keyed by the operation name (plan_START_END or chapter_N).

        Used in the contract identity so persisted drafts track which
        instruction set generated them. Plan operations get the batch union;
        chapter operations get the single-chapter block.
        """
        m = re.match(r"^plan_(\d+)_(\d+)$", name)
        if m:
            start, end = int(m.group(1)), int(m.group(2))
            return self._plan_draft_guard(start, end)
        m = re.match(r"^chapter_(\d+)$", name)
        if m:
            return self._draft_guard(int(m.group(1)))
        return ""

    def _plan_draft_guard(self, start, end):
        """Union of forbidden terms across a planning batch (start..end)."""
        terms = []
        for chapter in range(start, end + 1):
            terms.extend(draft_guard_terms_for(None, chapter))
        if not terms:
            return ""
        return (
            "\n\n【认知边界硬约束（本批规划必须遵守）】"
            f"第{start}-{end}章尚未解锁以下概念词，objective/boundaries/events 中不得正向点明机制或名称"
            "（仅可用模糊感知/传闻/物证）：" + dumps_compact(terms) + "。"
        )

    def _is_forbidden_only(self, issues) -> bool:
        """True when every issue is a forbidden-term violation (draft perception
        fields / title / expansion guidance), i.e. safe to scrub deterministically."""
        if not issues:
            return False
        markers = ("标题含认知边界禁词", "扩写指引含禁词", "感知层字段含禁词")
        for issue in issues:
            text = issue if isinstance(issue, str) else str(issue)
            if not any(m in text for m in markers):
                return False
        return True

    def _plan_opening_guard(self, start, end):
        """Restate the opening-facts hard constraint when the batch covers ch1/ch2.

        The recurring plan-phase QualityFailure is the model rewriting ch1 as
        'village-woman childbirth' instead of 'abandoned infant at the fog's
        edge'. The SYSTEM prompt states it once, but the PLAN context doesn't,
        so the model drifts. This guard makes it a visible per-batch hard rule.
        """
        if end < 2:
            return ""
        return (
            "\n\n【开篇固定事实（第1-2章硬约束，违反即规划作废）】"
            "第1章：陆烬是雾隐村外迷雾边缘出现的弃婴（湿布包裹、冰冷、无哭声），"
            "陈老根独自抱婴归家。不得设产室、产妇、产婆接生，不得写成村内分娩。"
            "第2章：陈老根抱婴归家后取名、安顿；婴儿仍为迷雾边缘来历，未揭示真实身份。"
            "规划 events 只能承接上述事实，不得改写来历。"
        )

    def _repair_plan(self, candidate, issues, context, only_chapter=None):
        entries = candidate.get("chapters", [])
        if not isinstance(entries, list) or not all(isinstance(entry, dict) and type(entry.get("chapter")) is int for entry in entries):
            return None
        available = {entry.get("chapter") for entry in entries}
        targets = {int(number) for issue in issues if isinstance(issue, dict)
                   for number in re.findall(r"(?:CH|Chapter\s*|第\s*)(\d+)", issue.get("location", ""), re.IGNORECASE)} & available
        if only_chapter is not None:
            targets = {only_chapter}
        if not targets or (only_chapter is None and len(targets) > len(entries) * 0.6):
            return None
        prompt = ("仅修复指定章节的规划，其他章节保持原样。评审意见需对照原规则，不采纳其中虚构的规则或机制。\n"
                  "保留章节号、事件编号、必要里程碑和预算，只改明确问题。输出JSON对象chapters数组，"
                  "每个条目必须完整保留chapter、objective、cast、events、ending、boundaries、milestone_ids、expanded_han全部字段。"
                  f"只包含这些章节：{sorted(targets)}。不得输出其他章节。\n"
                  + "设定和前章事实：\n" + dumps(context) + "\n原规划（相邻章节供衔接）：\n" + dumps(candidate)
                  + "\n问题：\n" + dumps(issues))
        partial = self._ask(self.generation, prompt, max_tokens=min(9000, 2400 * len(targets)))
        updates = partial.get("chapters")
        if not isinstance(updates, list) or not all(isinstance(entry, dict) for entry in updates) or {entry.get("chapter") for entry in updates} != targets or len(updates) != len(targets):
            raise ValueError("partial planning repair changed the requested chapter set")
        merged = copy.deepcopy(candidate)
        by_number = {entry["chapter"]: entry for entry in updates}
        merged["chapters"] = [by_number.get(entry["chapter"], entry) for entry in merged["chapters"]]
        return merged

    def _checked(self, name, contract, context, validate, kind, normalize=None, semantic_review=True):
        self._start(name)
        candidate = None
        issues = []
        history = []
        awaiting_review = False
        restored_contract_same = False
        format_repairs = 0
        previous_review = None
        contract_hash = digest({"contract": contract, "kind": kind, "system": SYSTEM, "review": REVIEW, "repair": REPAIR,
                                "contract_revision": 2, "draft_guard": self._op_draft_guard(name)})
        round_limit = self.settings.repair_rounds + 1
        draft_path = self.root / "drafts" / f"{name}.json"
        if draft_path.exists():
            saved = json.loads(draft_path.read_text(encoding="utf-8"))
            candidate, issues, history = saved.get("candidate"), saved.get("issues", []), saved.get("history", [])
            restored_contract_same = saved.get("contract_hash") == contract_hash
            awaiting_review = saved.get("awaiting_review", False)
            if saved.get("contract_hash") != contract_hash:
                round_limit += len(history)
            else:
                round_limit = saved.get("round_limit", round_limit)
                format_repairs = saved.get("format_repairs", 0)
            previous_review = saved.get("previous_review")
        # ② 陈旧毒化草稿自动失效：同合同、历史已耗尽轮次、且最后一轮无进展
        # （结构报错或评审打回）的草稿，若原样回放只会原地抛 QualityFailure。
        # 这里清空重生成，让骨架锁/校验器修复在"生成时"生效，而非事后回放。
        # 通过/待评审的草稿不会命中（最后一轮要么无错要么评审全过）。
        if (draft_path is not None and draft_path.exists() and restored_contract_same
                and len(history) >= round_limit
                and history and not any(
                    isinstance(i, dict) and i.get("phase") == "review_format" for i in issues)):
            last = history[-1]
            last_ok = (not last.get("errors")) and (
                (last.get("review") is None) or all(last["review"].get("checks", {}).values()))
            if not last_ok:
                logger.warning("auto-resetting exhausted no-progress draft %s "
                               "(history=%d rounds) for clean regeneration",
                               name, len(history))
                candidate = None
                issues = []
                history = []
                awaiting_review = False
                round_limit = self.settings.repair_rounds + 1
                draft_path.unlink(missing_ok=True)
        def persist():
            atomic_write(draft_path, dumps({"candidate": candidate, "issues": issues, "history": history,
                                           "contract_hash": contract_hash, "round_limit": round_limit,
                                           "awaiting_review": awaiting_review, "format_repairs": format_repairs,
                                           "previous_review": previous_review}))
        if len(history) >= round_limit and format_repairs < 1 and any(isinstance(issue, dict) and issue.get("phase") == "review_format" for issue in issues):
            round_limit += 1
            format_repairs += 1
        try:
            if candidate is not None and normalize:
                candidate = normalize(candidate)
            if candidate is not None:
                restored_errors = validate(candidate)
                if restored_errors:
                    issues, awaiting_review = restored_errors, False
                elif not history and not issues:
                    awaiting_review = True
            if candidate is not None and issues and all(isinstance(issue, dict) and issue.get("phase") == "review_format" for issue in issues):
                expected = digest({"context": context, "candidate": candidate})
                for raw_path in sorted((self.root / "audit" / "responses").glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True):
                    raw = json.loads(raw_path.read_text(encoding="utf-8"))
                    if raw.get("operation") != name or raw.get("response", {}).get("_phase") != "review":
                        continue
                    try:
                        prior = parse_object(raw["response"]["content"])
                        if prior.get("input_hash") == expected and not validate_review(prior, expected):
                            issues = prior["issues"]
                            awaiting_review = False
                            history[-1]["recovered_failed_review"] = {"source": str(raw_path), "review": prior}
                            break
                    except (ValueError, KeyError):
                        continue
            if candidate is not None and history and history[-1].get("errors") and not validate(candidate):
                failed_same_input = any(item.get("hash") == digest(candidate) and item.get("review")
                                        and not all(item["review"].get("checks", {}).values()) for item in history)
                if not failed_same_input:
                    awaiting_review = True
            if candidate is not None and not issues and history and history[-1].get("review") and all(history[-1]["review"].get("checks", {}).values()):
                awaiting_review = True
            if not semantic_review and candidate is not None:
                if not validate(candidate):
                    return candidate, {"method": "structural", "semantic_gate": "chapter_review",
                                       "pending_issues": issues, "input_hash": digest(candidate)}
            if candidate is not None and history and saved.get("contract_hash") == contract_hash:
                last_review = history[-1].get("review")
                hashed = digest({"context": context, "candidate": candidate})
                if last_review and all(last_review.get("checks", {}).values()) and last_review.get("input_hash") == hashed and not validate(candidate) and validate_review(last_review, hashed, candidate, context.get("plan")):
                    return candidate, last_review
            while len(history) < round_limit:
                round_number = len(history)
                phase = "generation"
                try:
                    incoming_issues = copy.deepcopy(issues)
                    if not incoming_issues and history:
                        incoming_issues = copy.deepcopy(history[-1].get("errors", []))
                    technical_only = bool(incoming_issues) and all(isinstance(issue, dict) and issue.get("phase") == "review_format" for issue in incoming_issues)
                    if (technical_only or awaiting_review) and candidate is not None:
                        pass
                    elif candidate is None:
                        candidate = self._ask(self.generation, contract)
                    elif kind == "规划" and any("重复事件" in str(issue) for issue in issues):
                        candidate = self._ask(self.generation, contract + "\n上次规划作废。重新设计，必须解决：\n" + dumps_compact(issues))
                    elif candidate is not None and kind != "规划" and self._is_forbidden_only(issues):
                        # Draft layer: failure is purely forbidden terms in the
                        # perception fields. Deterministically scrub in place, then
                        # re-validate; if clean, hand to the review gate (no LLM
                        # regen, which risks introducing new violations).
                        from .draft_guard import scrub_draft_forbidden
                        ch_num = int(re.match(r"^chapter_(\d+)$", name or "").group(1)) if name and name.startswith("chapter_") else self.store.head() + 1
                        fixed = scrub_draft_forbidden(candidate, ch_num)
                        if fixed:
                            logger.info("scrubbed draft forbidden terms %s (ch%s)", fixed, ch_num)
                            if normalize:
                                candidate = normalize(candidate)
                            scrub_errors = validate(candidate)
                            history.append({"round": round_number, "hash": digest(candidate),
                                            "contract_hash": contract_hash, "scrubbed": fixed})
                            persist()
                            if not scrub_errors:
                                awaiting_review = True
                                continue  # re-validate clean -> review gate next iteration
                        # scrub did nothing or still has errors: fall through to LLM REPAIR
                        candidate = self._ask(self.generation, REPAIR.format(contract=contract,
                                    candidate=dumps_compact(generation_view(candidate)), issues=dumps_compact(issues),
                                    guard=self._op_draft_guard(name)))
                    elif candidate is not None and len(incoming_issues) >= 3:
                        # Structural/schema errors (missing fields, broken
                        # budgets) usually mean the candidate's shape drifted
                        # from the contract; targeted text repair rarely fixes
                        # that. Regenerate from scratch and tell the model the
                        # exact schema gaps, but keep the old candidate in the
                        # context so proven content is not lost.
                        chapter_num = int(re.match(r"^chapter_(\d+)$", name or "").group(1)) if name and name.startswith("chapter_") else 0
                        candidate = self._ask(self.generation, contract +
                            "\n\n上一稿候选保留在下方。上一稿的问题：" + dumps_compact(issues) +
                            "\n重新生成完整章节，必须解决上述所有问题；保留上一稿中正确的内容与事实，"
                            "不得改变事件顺序与已确认的事实。\n\n【上一稿候选】\n" + dumps_compact(generation_view(candidate))
                            + skeleton_lock(candidate, chapter_num))
                    else:
                        repaired = self._repair_plan(candidate, issues, context) if kind == "规划" else None
                        candidate = repaired if repaired is not None else self._ask(self.generation, REPAIR.format(contract=contract,
                                               candidate=dumps_compact(generation_view(candidate)), issues=dumps_compact(issues),
                                                guard=self._op_draft_guard(name)))
                    if normalize:
                        candidate = normalize(candidate)
                    phase = "validation"
                    issues = validate(candidate)
                    if not issues:
                        if not semantic_review:
                            history.append({"round": round_number, "hash": digest(candidate), "structural_pass": True})
                            return candidate, {"method": "structural", "semantic_gate": "chapter_review",
                                               "pending_issues": [], "input_hash": digest(candidate)}
                        hashed = digest({"context": context, "candidate": candidate})
                        rejected = next((item["review"] for item in reversed(history)
                                         if (item.get("contract_hash") == contract_hash or (not item.get("contract_hash") and restored_contract_same)) and item.get("review", {}).get("input_hash") == hashed
                                         and not all(item["review"]["checks"].values())), None)
                        if rejected is not None:
                            issues = rejected["issues"]
                            awaiting_review = False
                            history.append({"round": round_number, "hash": digest(candidate), "contract_hash": contract_hash,
                                            "unchanged_repair": True, "issues": issues})
                            persist()
                            continue
                        awaiting_review = True
                        format_issues = incoming_issues if technical_only else []
                        persist()
                        phase = "review_format"
                        review_prompt = REVIEW.format(input_hash=hashed, kind=kind, context=dumps_compact(context), candidate=dumps_compact(candidate))
                        if technical_only:
                            review_prompt += ("\n上次评审响应格式不完整。只修复评审协议，不修改章节，不丢弃已有真实问题。"
                                              "必须逐个覆盖本章全部规划事件和里程碑，不能只返回首个事件。\n格式错误："
                                              + dumps_compact(format_issues) + "\n上次响应：" + dumps_compact(previous_review))
                        review = self._ask(self.reviewer, review_prompt,
                                           max_tokens=3500, temperature=0)
                        previous_review = copy.deepcopy(review)
                        # Bind the synchronous response to its actual request, not a model's SHA transcription.
                        review["model_input_hash"] = review.get("input_hash")
                        review["input_hash"] = hashed
                        basic_pass = validate_review(review, hashed)
                        if basic_pass:
                            bind_review_evidence(review, candidate, context["plan"], self.canon["milestones"])
                            passed = validate_review(review, hashed, candidate, context.get("plan"))
                        else:
                            passed = False
                        review["quality_contract"] = 3
                        awaiting_review = False
                        history.append({"round": round_number, "hash": digest(candidate), "contract_hash": contract_hash, "review": review})
                        if passed:
                            return candidate, review
                        issues = review["issues"]
                    else:
                        awaiting_review = False
                        history.append({"round": round_number, "hash": digest(candidate), "errors": issues})
                except (ValueError, json.JSONDecodeError) as exc:
                    issues = [{"technical_error": str(exc), "phase": phase, "prior_issues": issues}]
                    history.append({"round": round_number, "errors": issues})
                    if phase == "review_format" and format_repairs < 1:
                        round_limit += 1
                        format_repairs += 1
                persist()
            raise QualityFailure(f"{name}: bounded automatic repair exhausted")
        finally:
            persist()
            self.active.save({"success": False, "published": False})

    def _plan(self, chapter):
        start = ((chapter - 1) // self.settings.plan_batch) * self.settings.plan_batch + 1
        end = min(self.settings.chapters, start + self.settings.plan_batch - 1)
        cached = self.store.plan(start)
        if cached:
            normalized = copy.deepcopy(cached)
            for entry in normalized["chapters"]:
                if entry["chapter"] > self.store.head():
                    assign_plan_budgets({"chapters": [entry]}, self.settings)
            if normalized != cached:
                self.store.update_plan(start, normalized)
                cached = normalized
            relevant = [issue for issue in cached.get("pending_issues", [])
                        if not isinstance(issue, dict) or chapter in
                        {int(number) for number in re.findall(r"(?:CH|Chapter\s*|第\s*)(\d+)", issue.get("location", ""), re.IGNORECASE)}]
            if relevant:
                return self._replan_chapter(chapter, relevant)
            return cached["chapters"][chapter - start]
        context = self._context(through=end)
        planning_dir = self.source / "config" / "planning"
        mo_entries = load_mid_outline_batch(planning_dir, start, end)
        mo_digest = build_plan_digest(mo_entries)
        contract = PLAN.format(start=start, end=end, chapters=self.settings.chapters,
                               total=self.settings.total_expanded_han,
                               budgets=dumps_compact({n: self.settings.chapter_budget(n) for n in range(start, end + 1)}),
                               mid_outline=mo_digest,
                               context=dumps_compact(context)) + self._plan_draft_guard(start, end) \
            + self._plan_opening_guard(start, end)
        plan, review = self._checked(f"plan_{start}_{end}", contract, context,
                                    lambda value: validate_plan(value, start, end, self.settings, self.canon["milestones"]), "规划",
                                    lambda value: assign_plan_budgets(value, self.settings), semantic_review=False)
        plan["pending_issues"] = review.get("pending_issues", [])
        self.store.save_plan(start, end, plan, review)
        return self._plan(chapter)

    def _replan_chapter(self, chapter, issues):
        start = ((chapter - 1) // self.settings.plan_batch) * self.settings.plan_batch + 1
        plan = self.store.plan(start)
        self._start(f"chapter_{chapter}")
        revised = self._repair_plan(plan, issues, self._context(), only_chapter=chapter)
        if revised is None:
            raise QualityFailure("unable to repair current chapter plan")
        assign_plan_budgets(revised, self.settings)
        errors = validate_plan(revised, start, min(start + self.settings.plan_batch - 1, self.settings.chapters),
                               self.settings, self.canon["milestones"])
        atomic_write(self.root / "audit" / "plans" / f"{self.active.run_id}.json", dumps({"before": plan, "after": revised, "issues": issues, "errors": errors}))
        if errors:
            raise QualityFailure("replanned chapter failed structural checks: " + "; ".join(errors[:3]))
        revised["pending_issues"] = [issue for issue in plan.get("pending_issues", []) if issue not in issues]
        self.store.update_plan(start, revised)
        return revised["chapters"][chapter - start]

    def generate(self, chapter):
        with self._execution_lock, publication_lock(self.root, "condensed_generation"):
            existing = self.store.chapter(chapter)
            if existing:
                export_chapter(self.store, chapter, self.canon, self.settings)
                return {"chapter": chapter, "committed": True, "skipped": True}
            if chapter != self.store.head() + 1:
                raise ValueError("cannot generate before predecessor commit")
            self._routers()
            plan = self._plan(chapter)
            context = self._context(plan["cast"])
            context["plan"] = plan
            state = self.store.state()
            previous = self.store.chapter(chapter - 1)
            day = previous["payload"]["story_day"] if previous else 0
            prior_phase = previous["payload"].get("timeline_proof", {}).get("ending_daypart", phase(previous["payload"]["scenes"][-1]["time"])) if previous else "白天"
            def prepare(value):
                assign_scene_budgets(value, plan["expanded_han"])
                assign_state_preconditions(value, state)
                normalize_timeline(value, day, prior_phase, plan=plan)
                from .contracts import normalize_event_tags
                normalize_event_tags(value, plan)
                return render_brief(value)
            contract = DRAFT.format(context=dumps_compact(context)) + self._draft_guard(chapter)
            for planning_attempt in range(3):
                try:
                    payload, review = self._checked(f"chapter_{chapter}", contract, context,
                                                    lambda value: validate_chapter(value, plan, state, day, self.store.recent_cast()) + validate_timeline(value, day), "章节简写",
                                                    prepare)
                    break
                except QualityFailure:
                    if planning_attempt == 2:
                        raise
                    saved = json.loads((self.root / "drafts" / f"chapter_{chapter}.json").read_text(encoding="utf-8"))
                    if not any(isinstance(issue, dict) and issue.get("check") for issue in saved["issues"]):
                        raise
                    plan = self._replan_chapter(chapter, saved["issues"])
                    context = self._context(plan["cast"])
                    context["plan"] = plan
                    contract = DRAFT.format(context=dumps_compact(context)) + self._draft_guard(chapter)
            payload["plan"] = plan
            review["chapter_hash"] = digest(payload)
            self.store.commit(payload, review)
            self.active.save({"success": True, "published": True})
            directory = export_chapter(self.store, chapter, self.canon, self.settings)
            verify_export(directory)
            return {"chapter": chapter, "committed": True, "hash": digest(payload), "export": str(directory)}

    def status(self):
        prefix = self.store.head()
        calls, wall = self.store.db.execute("SELECT COALESCE(SUM(calls),0), COALESCE(SUM(wall),0) FROM operations").fetchone()
        tokens, reported = self.store.db.execute("SELECT COALESCE(SUM(total_tokens),0), COALESCE(SUM(reported_requests),0) FROM usage").fetchone()
        exported = self.store.db.execute("SELECT COUNT(*) FROM exports e JOIN chapters c ON e.number=c.number AND e.source_hash=c.hash").fetchone()[0]
        final_audit = self.store.metadata("final_audit")
        audited = bool(prefix and final_audit and final_audit.get("head_hash") == self.store.chapter(prefix)["hash"])
        worker = dict(self.store.metadata("worker") or {"state": "idle"})
        if worker.get("state") == "running" and isinstance(worker.get("pid"), int):
            pid = worker["pid"]
            try:
                if os.name == "posix":
                    os.kill(pid, 0)
                    proc = Path(f"/proc/{pid}/stat")
                    if proc.exists() and proc.read_text().split(")", 1)[1].strip().startswith("Z"):
                        worker["state"] = "interrupted"
                else:
                    import ctypes
                    PROCESS_QUERY_INFORMATION = 0x0400
                    kernel = ctypes.windll.kernel32
                    handle = kernel.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
                    if not handle:
                        worker["state"] = "interrupted"
                    else:
                        exit_code = ctypes.c_ulong()
                        kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                        kernel.CloseHandle(handle)
            except (ProcessLookupError, KeyError, PermissionError, OSError):
                worker["state"] = "unknown"
        # Quality-layer read surface (G6): verdict pass-rate over the LATEST-version
        # leaves, where the denominator excludes verdict_error leaves and the
        # numerator counts leaves where every dim meets its knob (pass). The knob
        # set is resolved from the leaf's own knob_set_hash against the active
        # quality_config.json so the rate tracks the criterion, not a static bar.
        verdict_pass_rate = None
        verdict_error_rate = None
        budget = None
        if getattr(self.store, "quality_enabled", False):
            db = self.store.db
            leaves = db.execute(
                "SELECT chapter, version, verdict, knob_set_hash, verdict_error FROM quality_verdicts").fetchall()
            knob_cfg = volume_knobs.load_quality_config()
            # Map each distinct knob_set_hash -> its knob_set via the config stages.
            hash_to_knob = {}
            for entry in knob_cfg.get("knob_sets", []):
                hash_to_knob[volume_knobs.knob_set_hash(entry["knob_set"])] = entry["knob_set"]
            latest = {}
            for chapter, version, verdict_raw, knob_set_hash, verdict_error in leaves:
                # latest-version leaf per chapter (max version); a verdict_error
                # leaf is counted only in the error rate, never the pass-rate denom.
                if chapter not in latest or version > latest[chapter][0]:
                    latest[chapter] = (version, verdict_raw, knob_set_hash, verdict_error)
            denom = 0
            numer = 0
            error_count = 0
            total_latest = len(latest)
            for _chapter, (version, verdict_raw, knob_set_hash, verdict_error) in latest.items():
                if verdict_error:
                    error_count += 1
                    continue
                verdict = json.loads(verdict_raw)
                knob = hash_to_knob.get(knob_set_hash)
                # A scored leaf is one that carries a resolvable knob set; it counts
                # toward the pass-rate denominator. The error rate is over ALL
                # latest-version leaves (the spec: "counted in verdict_error_rate"),
                # so its denominator is total_latest, not just the scored subset.
                if knob is not None:
                    denom += 1
                    if all(verdict.get(dim) is not None and verdict.get(dim) >= knob.get(dim, 0) for dim in ("爽点", "钩子", "压抑")):
                        numer += 1
            verdict_pass_rate = round(numer / denom, 4) if denom else None
            verdict_error_rate = round(error_count / total_latest, 4) if total_latest else 0.0
            budget = quality_store.budget_status(db, int(knob_cfg.get("token_cap", 0)))
        out = {"mode": "condensed", "committed_chapters": prefix, "target_chapters": self.settings.chapters,
               "exported_chapters": exported, "complete": prefix == self.settings.chapters and exported == prefix and audited,
               "expanded_budget_han": self.settings.total_expanded_han * prefix // self.settings.chapters,
               "physical_requests": calls, "operation_wall_s": round(wall, 3),
               "reported_total_tokens": tokens, "requests_without_usage": max(0, calls - reported),
               "external_expansion_verified": False, "worker": worker}
        if getattr(self.store, "quality_enabled", False):
            out["verdict_pass_rate"] = verdict_pass_rate
            out["verdict_error_rate"] = verdict_error_rate
            out["quality_budget"] = budget
        return out

    def worker_state(self, state, chapter=None):
        self.store.metadata("worker", {"state": state, "pid": os.getpid(), "chapter": chapter})

    def verify(self):
        prefix = self.store.prefix()
        for n in range(1, prefix + 1):
            if self.store.chapter(n)["review"].get("quality_contract") != 3:
                raise ValueError("legacy review lacks event-by-event evidence; revalidation is required")
            manifest = verify_export(self.root / "expansion" / f"chapter_{n:04d}")
            if manifest["source_hash"] != self.store.chapter(n)["hash"]:
                raise ValueError("export does not match committed chapter")
        if prefix:
            self.store.metadata("final_audit", {"head_hash": self.store.chapter(prefix)["hash"], "chapters": prefix})
        # Quality-layer chain integrity (Fork 3a): recompute every verdict leaf
        # hash + parent link before returning. A tampered leaf is a hard failure.
        if getattr(self.store, "quality_enabled", False):
            quality_store.validate_quality_chain(self.store.db, self.store)
        return self.status()

    def qc(self):
        """Read-only per-chapter verdict lines + pass-rate (G6). A verdict_error
        leaf is readable here and is excluded from the pass-rate denominator."""
        if not getattr(self.store, "quality_enabled", False):
            return {"lines": [], "pass_rate": None, "error_rate": 0.0, "note": "quality layer disabled"}
        db = self.store.db
        knob_cfg = volume_knobs.load_quality_config()
        hash_to_knob = {}
        for entry in knob_cfg.get("knob_sets", []):
            hash_to_knob[volume_knobs.knob_set_hash(entry["knob_set"])] = entry["knob_set"]
        lines = []
        denom = 0
        numer = 0
        error_count = 0
        scored = 0
        for chapter, version, verdict_raw, knob_set_hash, verdict_error in db.execute(
                "SELECT chapter, version, verdict, knob_set_hash, verdict_error FROM quality_verdicts "
                "ORDER BY chapter, version DESC"):
            # Only the latest version per chapter is the operator's read surface;
            # a verdict_error leaf is surfaced as a line but excluded from the
            # pass-rate denominator (the spec's test_verdict_error_not_in_pass_rate).
            if lines and lines[-1]["chapter"] == chapter:
                continue
            verdict = json.loads(verdict_raw)
            knob = hash_to_knob.get(knob_set_hash)
            if verdict_error:
                error_count += 1
                scored += 1
                lines.append({"chapter": chapter, "version": version, "status": "verdict_error",
                              "error": verdict_error})
                continue
            scored += 1
            if knob is None:
                lines.append({"chapter": chapter, "version": version, "status": "unresolved_knob",
                              "scores": {k: verdict.get(k) for k in ("爽点", "钩子", "压抑")}})
                continue
            denom += 1
            passed = all(verdict.get(dim) is not None and verdict.get(dim) >= knob.get(dim, 0)
                         for dim in ("爽点", "钩子", "压抑"))
            if passed:
                numer += 1
            lines.append({"chapter": chapter, "version": version,
                          "status": "PASS" if passed else "BELOW_KNOB",
                          "scores": {k: verdict.get(k) for k in ("爽点", "钩子", "压抑")},
                          "target": knob})
        return {"lines": lines,
                "pass_rate": round(numer / denom, 4) if denom else None,
                "error_rate": round(error_count / scored, 4) if scored else 0.0}

    def knobs(self, show=False, history=False):
        """Deterministic per-stage knob read surface (G3/G4 v0). `--show` returns
        the active per-stage knob sets + rationale; `--history` lists the
        knob_overrides staging rows (M2 commit_override folds them into the chain)."""
        cfg = volume_knobs.load_quality_config()
        if not getattr(self.store, "quality_enabled", False) and not show:
            return {"enabled": False, "note": "quality layer disabled"}
        out: dict = {"enabled": bool(cfg.get("enable_quality"))}
        if show:
            out["active"] = [{"stage": entry["stage"], "knob_set": entry["knob_set"],
                              "hash": volume_knobs.knob_set_hash(entry["knob_set"])}
                             for entry in cfg.get("knob_sets", [])]
        if history:
            if getattr(self.store, "quality_enabled", False):
                rows = self.store.db.execute(
                    "SELECT chapter, dim, new_value, rationale, override_hash, created_at "
                    "FROM knob_overrides ORDER BY chapter, dim").fetchall()
                out["overrides"] = [{"chapter": r[0], "dim": r[1], "new_value": r[2],
                                     "rationale": r[3], "override_hash": r[4], "created_at": r[5]}
                                    for r in rows]
        return out

    def note_quality(self, chapter):
        """Stamp the manifest's soft-class `quality_note` from the chapter's
        verdict leaf (G5, separate from compliance). Read-only over the chain;
        the only write is the manifest field."""
        from .mark_verified import note_quality
        if not getattr(self.store, "quality_enabled", False):
            raise ValueError("note-quality requires the quality layer")
        leaf = quality_store.verdict_for(self.store.db, self.store, chapter)
        if leaf is None:
            raise ValueError(f"chapter {chapter} has no verdict leaf; run expand first")
        pkg = self.root / "expansion" / f"chapter_{chapter:04d}"
        manifest = note_quality(pkg, leaf, out_path=pkg / "manifest.json")
        return manifest

    def commit_override(self, chapter, dim, new_value, rationale=""):
        """Fold a knob override into the chain (M2-3, G4 write path). Records the
        staging row if missing, then folds it into a knob_commits leaf; returns
        the leaf hash. Gated on enable_quality (RuntimeError when disabled)."""
        if not getattr(self.store, "quality_enabled", False):
            raise RuntimeError("quality layer not enabled")
        db = self.store.db
        # Ensure the staging row exists (idempotent upsert with identical content),
        # then fold it into the chain. commit_override re-reads the staging row's
        # override_hash, so the two calls stay consistent on the same content.
        quality_store.record_override(db, chapter, dim, new_value, rationale)
        leaf_hash = quality_store.commit_override(db, self.store, chapter, dim, new_value, rationale)
        return leaf_hash


def main(argv=None):
    # Windows: block the task/session Ctrl+C that otherwise interrupts the
    # blocking LLM socket connect mid-stream (KeyboardInterrupt at connect).
    import signal as _signal
    if hasattr(_signal, "SIGINT"):
        try:
            _signal.signal(_signal.SIGINT, _signal.SIG_IGN)
        except (ValueError, OSError):
            pass

    parser = argparse.ArgumentParser(description="Generate independently reviewed condensed chapters and external LLM packages")
    parser.add_argument("command", choices=("run", "status", "export", "archive", "verify", "compile", "invalidate", "extend-budget", "accept-check", "mark-verified", "expand", "dual-check", "qc", "knobs", "note-quality", "commit-override"))
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--end", type=int, default=3800)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--compact", action="store_true", help="share canon once inside the batch ZIP")
    parser.add_argument("--from-chapter", type=int)
    parser.add_argument("--reason", default="")
    parser.add_argument("--operation")
    parser.add_argument("--extra-calls", type=int, default=0)
    parser.add_argument("--extra-seconds", type=int, default=0)
    parser.add_argument("--prose", type=Path, help="model-generated prose file for accept-check")
    parser.add_argument("--llm-report", type=Path, help="optional LLM acceptance report for mark-verified")
    parser.add_argument("--waiver", type=Path, help="optional JSON waiver for waiver-able blocks (cognition_boundary / infant_self_awareness)")
    parser.add_argument("--program-report", type=Path,
                        help="accept-check report for mark-verified (default: <pkg>/accept_report.json)")
    parser.add_argument("--max-rounds", type=int, default=5, help="max continue-rounds for expand")
    parser.add_argument("--show", action="store_true", help="knobs: print the active per-stage knob sets")
    parser.add_argument("--history", action="store_true", help="knobs: list the knob_overrides staging rows")
    parser.add_argument("--chapter", type=int, help="note-quality: the chapter to stamp the manifest quality_note")
    parser.add_argument("--dim", default="爽点", help="commit-override: knob dimension (爽点/钩子/压抑)")
    parser.add_argument("--new-value", type=int, default=0, help="commit-override: the overridden knob value (1..5)")
    parser.add_argument("--rationale", default="", help="commit-override: operator rationale recorded on the leaf")
    parser.add_argument("--refresh-uncommitted-canon", action="store_true",
                        help="adopt corrected canon only before any chapter commits; retain all request budgets")
    args = parser.parse_args(argv)
    load_project_env()
    args.output.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=args.output / "production.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.command in ("run", "status", "export", "archive", "verify", "compile", "invalidate", "extend-budget",
                        "qc", "knobs", "note-quality", "commit-override"):
        pipeline = CondensedPipeline(args.source, args.output, refresh_uncommitted_canon=args.refresh_uncommitted_canon)
    else:
        pipeline = None
    exit_code = 0
    try:
        if args.command == "run":
            if not 1 <= args.end <= pipeline.settings.chapters:
                raise ValueError("end outside project range")
            old_failure = args.output / "failure.json"
            if old_failure.exists():
                resolved = args.output / "audit" / "previous_failures" / f"{uuid.uuid4().hex}.json"
                resolved.parent.mkdir(parents=True, exist_ok=True)
                old_failure.rename(resolved)
            pipeline.worker_state("running", pipeline.store.head() + 1)
            for chapter in range(1, pipeline.store.head() + 1):
                directory = args.output / "expansion" / f"chapter_{chapter:04d}"
                try:
                    verify_export(directory)
                except (OSError, ValueError):
                    export_chapter(pipeline.store, chapter, pipeline.canon, pipeline.settings)
            for chapter in range(pipeline.store.head() + 1, args.end + 1):
                if (args.output / "STOP").exists():
                    pipeline.worker_state("stopped", chapter)
                    print(dumps({"event": "stopped", **pipeline.status()}), flush=True)
                    break
                # Refund the LLM-call budget if a prior run halted on this chapter
                # (spent calls but never committed). A fresh attempt gets full
                # allowance instead of immediately raising "budget exhausted".
                worker = pipeline.store.metadata("worker") or {}
                if worker.get("state") in ("halted", "invalidated") and worker.get("chapter") == chapter:
                    pipeline.store.reset_operation_budget(f"chapter_{chapter}")
                    pipeline.worker_state("running", chapter)
                print(dumps({"event": "chapter_start", "chapter": chapter}), flush=True)
                pipeline.worker_state("running", chapter)
                atomic_write(args.output / "status.json", dumps(pipeline.status()))
                try:
                    result = pipeline.generate(chapter)
                    atomic_write(args.output / "status.json", dumps(pipeline.status()))
                    print(dumps(result), flush=True)
                except Exception as exc:
                    pipeline.worker_state("halted", chapter)
                    failure = {"event": "halted", "chapter": chapter, "error_type": type(exc).__name__, **pipeline.status()}
                    atomic_write(args.output / "failure.json", dumps(failure))
                    print(dumps(failure), flush=True)
                    logger.exception("Production halted at chapter %s", chapter)
                    exit_code = 1
                    break
            if pipeline.store.head() == pipeline.settings.chapters:
                pipeline.verify()
            compile_manuscript(pipeline.store, pipeline.settings)
            if pipeline.store.metadata("worker")["state"] == "running":
                pipeline.worker_state("complete" if pipeline.store.head() == pipeline.settings.chapters else "paused", pipeline.store.head())
        elif args.command == "export":
            with publication_lock(pipeline.root, "condensed_generation"):
                for chapter in range(1, pipeline.store.prefix() + 1):
                    export_chapter(pipeline.store, chapter, pipeline.canon, pipeline.settings)
        elif args.command == "archive":
            with publication_lock(pipeline.root, "condensed_generation"):
                for chapter in range(args.from_chapter or 1, min(args.end, pipeline.store.head()) + 1):
                    export_chapter(pipeline.store, chapter, pipeline.canon, pipeline.settings)
                pipeline.verify()
                archive_exports(args.output, args.archive or args.output / "expansion.zip", compact=args.compact,
                                start=args.from_chapter or 1, end=args.end)
        elif args.command == "verify":
            with publication_lock(pipeline.root, "condensed_generation"):
                pipeline.verify()
        elif args.command == "compile":
            with publication_lock(pipeline.root, "condensed_generation"):
                compile_manuscript(pipeline.store, pipeline.settings)
        elif args.command == "invalidate":
            with publication_lock(pipeline.root, "condensed_generation"):
                archive = pipeline.store.invalidate_from(args.from_chapter or 0, args.reason)
                pipeline.worker_state("invalidated", args.from_chapter)
                compile_manuscript(pipeline.store, pipeline.settings)
                print(dumps({"invalidated_archive": str(archive)}), flush=True)
        elif args.command == "extend-budget":
            with publication_lock(pipeline.root, "condensed_generation"):
                pipeline.store.extend_budget(args.operation, args.extra_calls, args.extra_seconds, args.reason)
        elif args.command == "accept-check":
            from novel_engine.condensed.accept_check import run as accept_run
            if not args.prose:
                raise ValueError("accept-check requires --prose <file>")
            pkg = args.output / "expansion" / f"chapter_{args.from_chapter:04d}" if args.from_chapter else None
            if pkg is None:
                raise ValueError("accept-check requires --from-chapter N (package = expansion/chapter_NNNN)")
            report = accept_run(pkg, args.prose, pkg / "accept_report.json")
            print(dumps(report), flush=True)
            exit_code = 0 if report["pass"] else 1
        elif args.command == "dual-check":
            from novel_engine.condensed.dual_check import dual_check as dual_run
            if args.from_chapter is None or args.prose is None:
                raise ValueError("dual-check requires --from-chapter N and --prose F")
            pkg = args.output / "expansion" / f"chapter_{args.from_chapter:04d}"
            merged = dual_run(pkg, args.prose, out_path=pkg / "dual_check_report.json")
            print(dumps(merged), flush=True)
            exit_code = 0 if merged["verdict"] == "PASS" else 1
        elif args.command == "expand":
            from novel_engine.condensed.expand_run import expand_until_range
            from novel_engine.condensed.providers import load_provider_full
            from novel_engine.condensed import observability
            import os
            if args.from_chapter is None:
                raise ValueError("expand requires --from-chapter N")
            pkg = args.output / "expansion" / f"chapter_{args.from_chapter:04d}"
            # G9 clean quota halt: read the monthly budget from the real `usage`
            # table BEFORE the expansion calls; at 100% the run halts with a
            # clear "quota" reason instead of the round-2 mystery freeze.
            budget = observability.budget_guard_configured(pipeline.store.db)
            if budget.get("halt"):
                print(dumps({"event": "halted", "reason": "quota", **budget,
                             "chapter": args.from_chapter}), flush=True)
                atomic_write(args.output / "failure.json", dumps({"event": "halted", "reason": "quota", **budget}))
                exit_code = 1
                return exit_code
            base_url, key_env, model, opts = load_provider_full("polish")
            key = os.environ.get(key_env, "")
            if not key:
                raise ValueError(f"missing env var {key_env}")
            # G2/G3: pass the active volume's knob set as `spec` + the store so
            # the final check_prose + judge_prose + record_verdict fire. The knob
            # is resolved from config/planning/volumes.json for the manifest
            # chapter (None when the chapter is not inside a shipped volume ->
            # spec=None back-compat, quality_notes=[]).
            spec = None
            try:
                from novel_engine.condensed.dual_check import _active_knob_set_for_chapter
                spec = _active_knob_set_for_chapter(args.from_chapter)
            except Exception as exc:
                # A missing/malformed volumes.json or knob config would otherwise
                # silently degrade the whole quality signal to spec=None. Log it
                # so the operator knows WHY this chapter ships without a verdict.
                logger.warning("expand ch%s: could not resolve a knob spec (%s); "
                               "expanding with spec=None (no quality verdict)", args.from_chapter, exc)
                spec = None
            summary = expand_until_range(pkg, base_url=base_url, api_key=key, model=model,
                                         initial_draft=args.prose, max_rounds=args.max_rounds,
                                         temperature=opts.get("temperature"),
                                         reasoning_effort=opts.get("reasoning_effort"),
                                         spec=spec, store=pipeline.store)
            print(dumps(summary), flush=True)
            exit_code = 0 if summary["pass"] else 1
        elif args.command == "mark-verified":
            from novel_engine.condensed.mark_verified import mark_verified
            if args.from_chapter is None:
                raise ValueError("mark-verified requires --from-chapter N")
            pkg = args.output / "expansion" / f"chapter_{args.from_chapter:04d}"
            program = args.program_report or (pkg / "accept_report.json")
            if not program.exists():
                raise ValueError(f"missing program report {program}; run accept-check first")
            manifest = mark_verified(pkg, program, args.llm_report, args.waiver)
            print(dumps({"chapter": manifest["chapter"], "verification": manifest["verification"]}), flush=True)
        elif args.command == "qc":
            # G6 read surface: per-chapter verdict lines + pass-rate. Read-only
            # over the chain; a verdict_error leaf is surfaced, never a crash.
            print(dumps(pipeline.qc()), flush=True)
        elif args.command == "knobs":
            out = pipeline.knobs(show=args.show, history=args.history)
            print(dumps(out), flush=True)
        elif args.command == "note-quality":
            if args.chapter is None:
                raise ValueError("note-quality requires --chapter N")
            manifest = pipeline.note_quality(args.chapter)
            print(dumps({"chapter": manifest["chapter"], "quality_note": manifest.get("quality_note")}), flush=True)
        elif args.command == "commit-override":
            if args.chapter is None:
                raise ValueError("commit-override requires --chapter N")
            if not 1 <= args.new_value <= 5:
                raise ValueError("commit-override --new-value must be 1..5")
            leaf_hash = pipeline.commit_override(args.chapter, args.dim, args.new_value, args.rationale)
            chain = quality_store.validate_knob_commits(pipeline.store.db, pipeline.store)
            print(dumps({"chapter": args.chapter, "dim": args.dim, "new_value": args.new_value,
                         "knob_commit_hash": leaf_hash, "validate": chain}), flush=True)
        if pipeline is not None:
            print(dumps(pipeline.status()), flush=True)
            atomic_write(args.output / "status.json", dumps(pipeline.status()))
    finally:
        if pipeline is not None:
            worker = pipeline.store.metadata("worker")
            if args.command == "run" and worker and worker.get("state") == "running":
                pipeline.worker_state("interrupted", pipeline.store.head() + 1)
            pipeline.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
