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

from .contracts import (ProductionConfig, assign_plan_budgets, assign_scene_budgets, assign_state_preconditions, bind_review_evidence, digest, dumps, parse_object, validate_chapter,
                        validate_plan, validate_review)
from .context import scope_canon
from .export import archive_exports, compile_manuscript, export_chapter, verify_export
from .prompts import DRAFT, PLAN, REPAIR, REVIEW, SYSTEM
from .render import generation_view, render_brief
from .store import Store
from .timeline import normalize_timeline, phase, validate_timeline

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
        if self.root == self.source or self.root.is_relative_to(self.source):
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
        contract_hash = digest({"contract": contract, "kind": kind, "system": SYSTEM, "review": REVIEW, "repair": REPAIR, "contract_revision": 2})
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
                        candidate = self._ask(self.generation, contract + "\n上次规划作废。重新设计，必须解决：\n" + dumps(issues))
                    else:
                        repaired = self._repair_plan(candidate, issues, context) if kind == "规划" else None
                        candidate = repaired if repaired is not None else self._ask(self.generation, REPAIR.format(contract=contract,
                                               candidate=dumps(generation_view(candidate)), issues=dumps(issues)))
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
                        review_prompt = REVIEW.format(input_hash=hashed, kind=kind, context=dumps(context), candidate=dumps(candidate))
                        if technical_only:
                            review_prompt += ("\n上次评审响应格式不完整。只修复评审协议，不修改章节，不丢弃已有真实问题。"
                                              "必须逐个覆盖本章全部规划事件和里程碑，不能只返回首个事件。\n格式错误："
                                              + dumps(format_issues) + "\n上次响应：" + dumps(previous_review))
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
        contract = PLAN.format(start=start, end=end, chapters=self.settings.chapters,
                               total=self.settings.total_expanded_han,
                               budgets=dumps({n: self.settings.chapter_budget(n) for n in range(start, end + 1)}),
                               context=dumps(context))
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
                return render_brief(value)
            contract = DRAFT.format(context=dumps(context))
            for planning_attempt in range(2):
                try:
                    payload, review = self._checked(f"chapter_{chapter}", contract, context,
                                                    lambda value: validate_chapter(value, plan, state, day, self.store.recent_cast()) + validate_timeline(value, day), "章节简写",
                                                    prepare)
                    break
                except QualityFailure:
                    if planning_attempt:
                        raise
                    saved = json.loads((self.root / "drafts" / f"chapter_{chapter}.json").read_text(encoding="utf-8"))
                    if not any(isinstance(issue, dict) and issue.get("check") for issue in saved["issues"]):
                        raise
                    plan = self._replan_chapter(chapter, saved["issues"])
                    context = self._context(plan["cast"])
                    context["plan"] = plan
                    contract = DRAFT.format(context=dumps(context))
            payload["plan"] = plan
            payload["context"] = context["continuity"]
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
        if worker.get("state") == "running":
            try:
                os.kill(worker["pid"], 0)
                proc = Path(f"/proc/{worker['pid']}/stat")
                if proc.exists() and proc.read_text().split(")", 1)[1].strip().startswith("Z"):
                    worker["state"] = "interrupted"
            except (ProcessLookupError, KeyError):
                worker["state"] = "interrupted"
            except PermissionError:
                worker["state"] = "unknown"
        return {"mode": "condensed", "committed_chapters": prefix, "target_chapters": self.settings.chapters,
                "exported_chapters": exported, "complete": prefix == self.settings.chapters and exported == prefix and audited,
                "expanded_budget_han": self.settings.total_expanded_han * prefix // self.settings.chapters,
                "physical_requests": calls, "operation_wall_s": round(wall, 3),
                "reported_total_tokens": tokens, "requests_without_usage": max(0, calls - reported),
                "external_expansion_verified": False, "worker": worker}

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
        return self.status()


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
    parser.add_argument("command", choices=("run", "status", "export", "archive", "verify", "compile", "invalidate", "extend-budget", "accept-check"))
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
    parser.add_argument("--refresh-uncommitted-canon", action="store_true",
                        help="adopt corrected canon only before any chapter commits; retain all request budgets")
    args = parser.parse_args(argv)
    load_project_env()
    args.output.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=args.output / "production.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    pipeline = CondensedPipeline(args.source, args.output, refresh_uncommitted_canon=args.refresh_uncommitted_canon)
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
        print(dumps(pipeline.status()), flush=True)
        atomic_write(args.output / "status.json", dumps(pipeline.status()))
    finally:
        worker = pipeline.store.metadata("worker")
        if args.command == "run" and worker and worker.get("state") == "running":
            pipeline.worker_state("interrupted", pipeline.store.head() + 1)
        pipeline.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
