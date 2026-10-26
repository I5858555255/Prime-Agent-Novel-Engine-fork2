"""Chapter-scoped request budgets and metadata-only latency traces."""
from __future__ import annotations

import json
import logging
import math
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from functools import wraps
from pathlib import Path
from .publication_transaction import publication_lock

logger = logging.getLogger(__name__)
_CURRENT = ContextVar("novel_execution", default=None)
_STAGE = ContextVar("novel_stage", default=None)
_ROUTE_ATTEMPT = ContextVar("novel_route_attempt", default=0)
_SCENE = ContextVar("novel_scene", default=None)


class BudgetExceeded(RuntimeError):
    """Terminal for this chapter run; never retry it with a fresh budget."""


class AttemptBudget:
    def __init__(self, chapter, max_llm_calls=48, max_scene_attempts=3,
                 max_review_rounds=2, max_review_calls=6, max_wall_seconds=300):
        self.chapter = chapter
        self.limits = dict(max_llm_calls=max_llm_calls, max_scene_attempts=max_scene_attempts,
                           max_review_rounds=max_review_rounds, max_review_calls=max_review_calls,
                           max_wall_seconds=max_wall_seconds)
        for name, value in self.limits.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"attempt_budget.{name} must be positive and finite")
            if name != "max_wall_seconds" and not isinstance(value, int):
                raise ValueError(f"attempt_budget.{name} must be an integer")
        self.started = time.monotonic()
        self.calls = 0
        self.review_rounds = 0
        self.review_calls = 0
        self.scene_attempts = Counter()
        self.reason = None
        self.lock = threading.RLock()

    def _stop(self, reason):
        with self.lock:
            self.reason = self.reason or reason
            raise BudgetExceeded(self.reason)

    def check(self):
        with self.lock:
            if self.reason:
                raise BudgetExceeded(self.reason)
            if time.monotonic() - self.started >= self.limits["max_wall_seconds"]:
                self._stop("chapter wall-time budget exhausted")

    def remaining(self):
        self.check()
        return max(0.001, self.limits["max_wall_seconds"] - (time.monotonic() - self.started))

    def request(self):
        with self.lock:
            self.check()
            if self.calls >= self.limits["max_llm_calls"]:
                self._stop("chapter LLM-call budget exhausted")
            self.calls += 1

    def can_scene(self, scene_id):
        with self.lock:
            self.check()
            return self.scene_attempts[scene_id] < self.limits["max_scene_attempts"]

    def scene(self, scene_id):
        with self.lock:
            self.check()
            if self.scene_attempts[scene_id] >= self.limits["max_scene_attempts"]:
                self._stop(f"scene {scene_id} attempt budget exhausted")
            self.scene_attempts[scene_id] += 1

    def review(self, vote=False):
        with self.lock:
            self.check()
            field = "review_calls" if vote else "review_rounds"
            if getattr(self, field) >= self.limits["max_" + field]:
                self._stop(f"chapter {field} budget exhausted")
            setattr(self, field, getattr(self, field) + 1)

    def snapshot(self):
        with self.lock:
            return {"limits": self.limits, "calls": self.calls,
                    "scene_attempts": dict(self.scene_attempts), "review_rounds": self.review_rounds,
                    "review_calls": self.review_calls, "exhausted": self.reason}


class Execution:
    def __init__(self, owner, chapter):
        self.owner = owner
        self.chapter = chapter
        self.run_id = uuid.uuid4().hex
        self.budget = AttemptBudget(chapter, **((owner.config.get("pipeline") or {}).get("attempt_budget") or {}))
        self.events = []
        self.commit_started = False
        self.current_result = None
        self.lock = threading.Lock()

    @contextmanager
    def span(self, name, kind="stage", **metadata):
        start = time.monotonic()
        event = {"name": name, "kind": kind, "start_s": start - self.budget.started,
                 "parent": _STAGE.get(), "scene_id": _SCENE.get(), **metadata}
        try:
            yield event
            event["status"] = "ok"
        except BaseException as exc:
            event["status"] = "error"
            event["error_type"] = type(exc).__name__
            raise
        finally:
            event["wall_s"] = time.monotonic() - start
            with self.lock:
                self.events.append(event)

    def save(self, result):
        with self.lock:
            events = list(self.events)
        requests = [e for e in events if e["kind"] == "provider"]
        usage = Counter()
        for e in requests:
            usage.update(e.get("usage") or {})
        report = {"schema": 1, "run_id": self.run_id, "chapter": self.chapter,
                  "success": bool(result.get("success")), "published": bool(result.get("published")),
                  "wall_s": time.monotonic() - self.budget.started,
                  "budget": self.budget.snapshot(), "calls": len(requests),
                  "failed_calls": sum(e["status"] != "ok" for e in requests),
                  "retry_calls": sum(bool(e.get("retry")) for e in requests),
                  "usage": dict(usage), "events": events}
        directory = Path(self.owner.root) / "audit" / "latency"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"chapter_{self.chapter}_{self.run_id}.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
            result["trace_path"] = str(path)
        except OSError:
            logger.exception("Could not persist chapter latency trace")
        return report


def current_execution(owner=None):
    return _CURRENT.get() or getattr(owner, "_chapter_execution", None)


class ContextExecutor(ThreadPoolExecutor):
    """Each submitted task gets its own Context, sharing the locked chapter budget."""
    def submit(self, fn, /, *args, **kwargs):
        context = copy_context()
        submitted = time.monotonic()
        execution = current_execution()
        def invoke():
            if execution is not None:
                with execution.lock:
                    execution.events.append({"name": "executor_wait", "kind": "queue",
                                             "start_s": submitted - execution.budget.started,
                                             "wall_s": time.monotonic() - submitted,
                                             "status": "ok", "parent": _STAGE.get()})
                execution.budget.check()
            return fn(*args, **kwargs)
        return super().submit(context.run, invoke)


def stage(name):
    def decorate(fn):
        @wraps(fn)
        def wrapped(self, *args, **kwargs):
            execution = current_execution(self)
            if execution is None:
                return fn(self, *args, **kwargs)
            execution.budget.check()
            if name == "commit_io":
                execution.commit_started = True
            with execution.span(name):
                token = _STAGE.set(name)
                try:
                    return fn(self, *args, **kwargs)
                finally:
                    _STAGE.reset(token)
        return wrapped
    return decorate


def scene_generation(fn):
    @wraps(fn)
    def wrapped(self, task_card, scene_blueprint, *args, **kwargs):
        execution = current_execution(self)
        if execution is None:
            return fn(self, task_card, scene_blueprint, *args, **kwargs)
        sid = int(scene_blueprint.get("scene_num", 0) or 0)
        execution.budget.scene(sid)
        token = _SCENE.set(sid)
        try:
            with execution.span("scene", kind="scene", scene_id=sid):
                return fn(self, task_card, scene_blueprint, *args, **kwargs)
        finally:
            _SCENE.reset(token)
    return wrapped


def llm_request(fn):
    @wraps(fn)
    def wrapped(self, *args, **kwargs):
        execution = current_execution(self)
        if execution is None:
            return fn(self, *args, **kwargs)
        execution.budget.request()
        attempt = getattr(self._local, "request_attempt", 0)
        self._local.request_attempt = attempt + 1
        with execution.span("request", kind="provider", model=self.model,
                            phase=getattr(self, "phase", "unknown"),
                            retry=attempt > 0 or _ROUTE_ATTEMPT.get() > 0) as event:
            result = fn(self, *args, **kwargs)
            usage = (result.get("_usage") or result.get("usage") or {}) if isinstance(result, dict) else {}
            event["usage"] = {k: int(usage.get(k, 0) or 0) for k in
                              ("prompt_tokens", "completion_tokens", "reasoning_tokens", "total_tokens")}
            execution.budget.check()
            return result
    return wrapped


def budget_sleep(seconds, execution=None, kind="backoff"):
    execution = execution or current_execution()
    if execution is None:
        time.sleep(seconds)
        return
    execution.budget.check()
    with execution.span(kind, kind=kind):
        if seconds >= execution.budget.remaining():
            execution.budget._stop(f"chapter deadline cannot accommodate {kind}")
        time.sleep(seconds)
        execution.budget.check()


def _bind(execution):
    # Gate helpers also use worker threads that predate context propagation.
    # Bind the run to the actual clients as a fallback, restoring all bindings afterwards.
    owners = [execution.owner]
    saved = []
    seen = set()
    while owners:
        obj = owners.pop()
        if obj is None or id(obj) in seen:
            continue
        seen.add(id(obj))
        saved.append((obj, getattr(obj, "_chapter_execution", None)))
        obj._chapter_execution = execution
        attributes = vars(obj)
        for name in ("writer", "director", "synopsis_agent", "reviewer", "llm", "review_llm",
                     "scene_router", "polish_router", "outline_router", "review_router", "refine_router",
                     "pro_client", "primary", "fallback"):
            child = attributes.get(name)
            if child is not None and hasattr(child, "__dict__"):
                owners.append(child)
        providers = attributes.get("providers", {})
        owners.extend(providers.values() if isinstance(providers, dict) else providers or [])
    return saved


def chapter_run(fn):
    @wraps(fn)
    def wrapped(self, chapter_num, *args, **kwargs):
        active = _CURRENT.get()
        if active is not None and active.owner is self and active.chapter == chapter_num:
            active.budget.check()
            return fn(self, chapter_num, *args, **kwargs)
        with self._execution_lock, publication_lock(self.root, lock_name="generation"):
            execution = Execution(self, chapter_num)
            token = _CURRENT.set(execution)
            saved = _bind(execution)
            result = {"chapter": chapter_num, "success": False, "published": False, "errors": []}
            try:
                result = fn(self, chapter_num, *args, **kwargs)
                if not execution.commit_started:
                    execution.budget.check()
                return result
            except BudgetExceeded as exc:
                if isinstance(execution.current_result, dict):
                    result = execution.current_result
                result.update(success=False, published=False, budget_exhausted=True,
                              pending_human_review=False, quality_repair_exhausted=True)
                result.setdefault("errors", []).append(str(exc))
                return result
            except Exception as exc:
                if isinstance(execution.current_result, dict):
                    result = execution.current_result
                result.setdefault("errors", []).append(type(exc).__name__)
                if execution.budget.reason:
                    result.update(success=False, published=False, budget_exhausted=True, pending_human_review=False, quality_repair_exhausted=True)
                    return result
                raise
            finally:
                if result.get("budget_exhausted") and hasattr(self, "_save_quality_failure"):
                    self._save_quality_failure(chapter_num, result, execution.budget.reason or "budget exhausted")
                self.last_chapter_trace = execution.save(result)
                for obj, old in reversed(saved):
                    obj._chapter_execution = old
                _CURRENT.reset(token)
    return wrapped


@contextmanager
def route_attempt(attempt):
    token = _ROUTE_ATTEMPT.set(attempt)
    try:
        yield
    finally:
        _ROUTE_ATTEMPT.reset(token)
