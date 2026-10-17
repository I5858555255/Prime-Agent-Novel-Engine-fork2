"""
流水线编排器：将所有章节生成 Agent 串联起来。
负责：
1. 状态机状态管理
2. Agent 调用顺序
3. 失败重试与恢复
4. 数据传递
"""
import json
import logging
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from novel_engine.core.state_machine import StateMachine, ChapterPhase
from novel_engine.core.checkpoint import CheckpointManager, create_draft_checkpoint, novel_chapter_path
from novel_engine.core.quality_policy import load_quality_policy, is_blocking, derive_scene_targets
from novel_engine.agents.world_simulator import WorldSimulator
from novel_engine.agents.chapter_director import ChapterDirector, merge_thin_scene_blueprints
from novel_engine.agents.writer_agent import SynopsisAgent, WriterAgent
from novel_engine.agents.scene_schema import SceneOutput, extract_beats_fallback, validate_scene_text, detect_scene_overlap, build_scene_prompt, validate_beats_covered
from novel_engine.core.errors import (
    SceneUnrecoverableError, ChapterResampleRequiredError, ChapterQualityGapError,
)
from novel_engine.pipeline.chapter_journal import (
    append_scene, completed_scene_ids, load_scenes,
    load_authoritative_scenes, authoritative_completed_scene_ids,
    snapshot_journal, restore_journal,
)


def _unstable_upgrade_eligible(policy: dict | None, score) -> bool:
    """ch292 归因：review_unstable 且低分（0<score<floor）且 A 类整章重写启用时，
    先给一次整章重写预算（重写改变文本后评审噪声可能收敛，ch299 已验证），
    而非直接强制人工——只有高分 unstable 或升级未启用才走人工复核。"""
    _cfg = (policy or {}).get("low_score_rewrite") or {}
    _floor = float(_cfg.get("floor", int((policy or {}).get("fix_threshold", 60))))
    return (
        bool(_cfg.get("enabled", True))
        and isinstance(score, (int, float))
        and 0 < score < _floor
    )
from novel_engine.pipeline.event_ledger import (
    append_chapter_events, recent_event_block,
    append_end_state, latest_end_state, end_state_anchor_block,
)
from novel_engine.pipeline.fact_changes import record_change
from novel_engine.pipeline.boundary_guard import (
    stage1_score, scene_overlap_scores,
    build_stage2_prompt, parse_stage2_verdict,
)
import novel_engine.pipeline.gates as _gates
from novel_engine.quality.degeneration import find_self_repetition, is_near_empty
from novel_engine.quality.timeline_gate import detect_timeline_jump, anchor_fix_directive
from novel_engine.quality.continuity_gate import (
    normalize_sequence, build_specs, detect_inversions, continuity_fix_directive,
    extract_prior_acquired_entities,
)
from novel_engine.quality.scope_gate import (
    detect_scope_violations, scope_fix_directive, _extract_card_hard_terms,
excise_dawn_overrun,
)
from novel_engine.quality.temporal_continuity import excise_from_text as excise_temporal_violations
from novel_engine.quality.review_votes import (
    should_run_extra_reviews, aggregate as aggregate_votes,
)
from novel_engine.quality import review_hybrid
from novel_engine.pipeline.phase_cache import PhaseCache, content_key, model_fingerprint, PROMPT_VERSIONS
from novel_engine.agents.reviewer_agent import ReviewerAgent, DIM_MAX
from novel_engine.agents.pacing_advisor import PacingAdvisor
from novel_engine.core.memory_manager import MemoryManager
from novel_engine.core.llm_client import LLMClient, call_llm, get_call_log, reset_call_log
from novel_engine.core.call_metrics import snapshot as get_metrics_snapshot, load_pricing
from novel_engine.pipeline.chapter_status import set_status, COMMITTED, HALTED, get_status
from novel_engine.core.llm_failover import FailoverLLMClient
from novel_engine.core.model_router import ModelRouter
from novel_engine.quality.defects_store import DefectsStore
from novel_engine.quality.outline_coverage_gate import (
    validate_outline_coverage,
    extract_must_cover_beats, check_scene_must_cover_beats, build_mandatory_beat_directive,
)
from novel_engine.quality.forbidden_scanner import ForbiddenScanner
from novel_engine.quality.continuity_auditor import ContinuityAuditor
from novel_engine.engine.db import StateDB
from novel_engine.engine.session import SessionTree
from novel_engine.quality.repetition_detector import (
    detect_repetition,
    detect_truncation,
    detect_length_anomaly,
    purify_novel_for_publish,
    verify_seams,
)

logger = logging.getLogger(__name__)


def _review_issue_is_blocking(policy: dict, issue: dict) -> tuple[bool, bool]:
    # Severity-gated blocking: hard-dimension issues only block when severity is
    # high or medium; low-severity issues on hard dimensions are advisory only.
    # Note-dimension issues never block but high severity generates advisory_high signal.
    # Category field takes priority over dimension when present.
    is_hard_dim = issue.get("category") or issue.get("dimension")
    sev = issue.get("severity", "").lower()

    if is_blocking(policy, is_hard_dim):
        # Hard dimension: block only for high/medium severity
        return sev in ("high", "medium"), False

    # Note dimension or unmapped: check if severity is high for advisory tracking
    if sev == "high":
        return False, True

    return False, False


def _forbidden_violation_is_blocking(policy: dict, violation: dict) -> bool:
    """违禁命中是否阻断：只读 policy severity_map，命中即属 forbidden_block 类别。

    生产者（scanner severity 标签）暂不改；比较点按类别查表。
    """
    return is_blocking(policy, violation.get("category", "forbidden_block"))


def _beat_to_str(b) -> str:
    """Coerce one task-card beat to a plain string (models sometimes emit objects)."""
    if isinstance(b, str):
        return b.strip()
    if isinstance(b, dict):
        for k in ("beat", "desc", "content", "summary", "text", "title"):
            v = b.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        try:
            return json.dumps(b, ensure_ascii=False)
        except Exception:
            return ""
    if b is None:
        return ""
    return str(b).strip()


def _is_coherent_short(obj, need_beats: int) -> bool:
    """结构化合法但篇幅极短的“连贯短文”（真机：flash 会在合法 JSON/beats 全覆盖下只写
    约120字就 finish=stop）。这类不是空/乱码/重复，应走中性参数重生与有界扩写，而不是
    近空 penalty（实测 penalty 反而把它打成0字）。

    判据（同时满足）：structured 合法、非 length 截断、beats_covered 已覆盖全部 beat、
    无占位/自重复、含≥40个中文字且中文占比≥0.5。
    """
    if not getattr(obj, "structured", False):
        return False
    if str(getattr(obj, "finish_reason", "") or "") == "length":
        return False
    _covered = [x for x in (getattr(obj, "beats_covered", []) or []) if str(x).strip()]
    if int(need_beats or 0) > 0 and len(_covered) < int(need_beats):
        return False
    _text = getattr(obj, "scene_text", "") or ""
    if any(m in _text for m in (
            "待补充", "此处省略", "（略）", "(略)", "TODO", "【待写", "此处留白", "暂缺")):
        return False
    if find_self_repetition(_text)[0]:
        return False
    _cjk = sum(1 for c in _text if "\u4e00" <= c <= "\u9fff")
    if _cjk < 40 or _cjk / max(1, len(_text)) < 0.5:
        return False
    return True


def _normalize_blueprint_beats(task_card: dict) -> None:
    """Canonicalize every blueprint's beats + CC round-8 temporal fields."""
    for idx, bp in enumerate(task_card.get("scene_blueprints") or []):
        if not isinstance(bp, dict):
            continue
        bp["beats"] = [s for s in (_beat_to_str(b) for b in (bp.get("beats") or [])) if s]
        # CC round-8 P0-1：时序字段规范化（缺失安全回退，不因此判任务卡失败）
        _sid = int(bp.get("scene_num", idx + 1) or (idx + 1))
        try:
            _seq = int(bp.get("sequence_index"))
            if _seq <= 0:
                _seq = _sid
        except (TypeError, ValueError):
            _seq = _sid
        bp["sequence_index"] = _seq
        bp["narrative_time"] = str(bp.get("narrative_time", "") or "").strip()
        _states = bp.get("do_not_depict_before")
        bp["do_not_depict_before"] = (
            [str(x).strip() for x in _states if str(x).strip()]
            if isinstance(_states, list) else []
        )
        _kw = bp.get("do_not_depict_keywords")
        _groups: list[list[str]] = []
        if isinstance(_kw, list):
            for _g in _kw:
                if isinstance(_g, list):
                    _terms = sorted({str(x).strip() for x in _g if str(x).strip()})
                    if len(_terms) >= 2:
                        _groups.append(_terms)
        bp["do_not_depict_keywords"] = _groups


class PipelineOrchestrator:
    """章节生成流水线编排器。"""

    def __init__(self, project_root: str | Path = None, llm_client: Optional[LLMClient] = None):
        self.root = Path(project_root or Path(__file__).parent.parent)
        # Ensure runtime dir exists (StateMachine/StateDB write into it during init)
        (self.root / "runtime").mkdir(parents=True, exist_ok=True)

        # Load runtime config first so failover bounds can be read before building clients
        try:
            self.config = json.loads((self.root / "config" / "runtime_config.json").read_text(encoding="utf-8"))
        except Exception:
            self.config = {}

        fb_trigger = self.config.get("autonomy", {}).get("failover_trigger_consecutive_errors", 3)
        fb_backoff = self.config.get("autonomy", {}).get("max_backoff_seconds", 1800)
        # Task 5: per-phase model routers (duck-typed chat_completion).
        # These replace the single FailoverLLMClient with phase-specific pools.
        if llm_client is not None:
            # Test mode: use injected mock client for ALL routers
            self.scene_router = llm_client
            self.polish_router = llm_client
            self.outline_router = llm_client
            self.review_router = llm_client
            self.refine_router = llm_client
        else:
            self.scene_router = ModelRouter("scenes", self.root)
            self.polish_router = ModelRouter("polish", self.root)
            self.outline_router = ModelRouter("outline", self.root)
            self.review_router = ModelRouter("review", self.root)
            # CC round-27 Phase2：文字精修池（有界扩写/length-floor 补写/CC25 文学性重写），
            # 配置 phases.refine 指向 agnes-2.5-pro；与大纲相位解耦，避免牵连 outline/director。
            # refine 为可选相位：精简/旧 profile（如测试、备用供应商）未定义时回退复用 polish_router。
            try:
                self.refine_router = ModelRouter("refine", self.root)
            except Exception as _refine_miss:
                logger.info(f"phase 'refine' absent, reuse polish router: {_refine_miss}")
                self.refine_router = self.polish_router

        if llm_client is not None:
            self.llm = llm_client
        else:
            self.llm = self.outline_router  # default for pure-text calls (director/synopsis also get dedicated routers below)

        self.state_machine = StateMachine(self.root)
        self.checkpoint_mgr = CheckpointManager(self.root)
        # CC P0: content-addressed disk cache (director post-validation / reviewer by final text)
        self._phase_cache = PhaseCache(self.root)
        # reviewer 缓存只在“每章每进程首次评审”读取：同进程修复循环允许对新结果再评审，
        # 跨进程(supervisor 重启)重跑同稿时才命中，消除 infra 重复计费。
        self._review_read_chapters: set = set()
        # CC round-7: 跨章边界硬门（assembly 后判定，未解决则并入确定性硬门阻断提交）
        self._boundary_hard: list[str] = []
        # CC28 批B 3b：章内相邻场同一现场近重复重演硬门（跨场校验后判定，并入确定性硬门）
        self._reprise_hard: list[str] = []
        # CC round-7 P0-1: 整章 rewrite 默认下线（实测会降分），只保留场景定点重生
        self._allow_whole_rewrite = bool(
            (self.config.get("pipeline", {}) or {}).get("allow_whole_chapter_rewrite", False))
        self.simulator = WorldSimulator(self.root)
        if llm_client is not None:
            # Test mode: use injected mock client for director and synopsis too
            self.director = ChapterDirector(self.root, llm_client=llm_client)
            self.synopsis_agent = SynopsisAgent(llm_client=llm_client)
        else:
            self.director = ChapterDirector(self.root, llm_client=ModelRouter("director", self.root))
            self.synopsis_agent = SynopsisAgent(llm_client=ModelRouter("synopsis", self.root))

        # Instantiate StateDB
        db_dir = self.root / "runtime"
        db_dir.mkdir(parents=True, exist_ok=True)
        self.db = StateDB(db_path=str(db_dir / "state.db"), project_root=self.root)

        # Instantiate or load SessionTree
        self.session_tree_path = self.root / "runtime" / "session_tree.json"
        self._load_session_tree()

        self.writer = WriterAgent(llm_client=self.scene_router)
        # CC30（DS Q1/Q2/Q3）：仅真机开启按场 flash->pro 故障转移。真机判据用
        # production_runner 设置的 NOVEL_ENGINE_REAL_LANG_ENFORCE=1，并排除 MockLLMClient；
        # 注入的是真实 client 时为 pro 单独建 refine 池（agnes-2.5-pro），mock/离线零影响。
        _real30 = os.environ.get("NOVEL_ENGINE_REAL_LANG_ENFORCE") == "1"
        # P5-1(a)：_is_mock30 同时识别 MockLLMClient 类名与 LLMClient(use_mock=True)
        _is_mock30 = (
            llm_client is not None
            and (type(llm_client).__name__ == "MockLLMClient"
                 or getattr(llm_client, "use_mock", False))
        )
        if _real30 and not _is_mock30:
            try:
                _pro30 = self.refine_router if llm_client is None else ModelRouter("refine", self.root)
                self.writer.set_pro_failover(True, client=_pro30)
            except Exception as _e30:
                logger.warning(f"CC30 pro failover not armed: {_e30}")
        # CC round-12：显式给每个 router 持有的 client 下发 phase（幂等保险），使
        # LLMClient 的空/极短正文退化门在场景等阶段按 phase 生效（含定点重生路径）。
        for _rname, _router in (("scenes", self.scene_router), ("polish", self.polish_router),
                                ("outline", self.outline_router), ("review", self.review_router),
                                ("refine", self.refine_router)):
            for _c in getattr(_router, "providers", {}).values():
                if _c is not None and hasattr(_c, "phase"):
                    _c.phase = _rname
        self.memory = MemoryManager(self.root)

        # 存储当前章节的产出，供 commit 使用
        self.current_novel = ""
        self._draft_novel = ""
        self.current_synopsis = {}
        self.current_outline = {}
        # P7C：保存含※分隔符的权威最终分场，供危机门按场评估
        self._assembled_with_seps: str = ""
        # Cost tracking
        self._call_log = get_call_log
        self._reset_call_log = reset_call_log
        self.cost_tracker = {
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_reasoning_tokens": 0,
            "total_tokens": 0,
            "api_calls": 0,
            "per_chapter_costs": {},
            "sliding_window_constraint_adjustments": 0,
        }
        # Pre-compute director's fixed context (bible + volumes + plot_graph) — loaded once at init
        self._director_fixed_context = self._load_director_fixed_context()
        # P0 修复：冻结单章 task_card，避免移动目标死循环；全局目标批次级冻结（读 quality_policy，默认 7500）
        self._frozen_task_cards: dict[int, dict] = {}
        self._frozen_synopsis: dict[int, dict] = {}
        self._global_target: int = int(load_quality_policy(self.root)["chapter_target_chars"])

        # Runtime config + provider config for chapter-level retry logic
        from novel_engine.core.llm_client import _provider_config_from_runtime
        self.provider_cfg = _provider_config_from_runtime()

        # Defect store for recording unrecovered gaps (below-min-ch chapters)
        self.defects = DefectsStore(str(self.root / "audit" / "defects.json"))

        # Task 7/8: continuity auditor + audit cadence
        self.audit_interval = self.config.get("autonomy", {}).get("audit_interval_chapters", 50)
        self.auditor = ContinuityAuditor(llm_client=self.llm)
        self._last_audit_snapshot = None

        # Task 9: forbidden gate before commit
        try:
            self.forbidden = ForbiddenScanner(rules_path=str(self.root / "config" / "forbidden.json"))
        except Exception as e:
            logger.warning(f"ForbiddenScanner init failed (using empty rules): {e}")
            self.forbidden = ForbiddenScanner.__new__(ForbiddenScanner)
            self.forbidden.rules = []

        # 独立质检 LLM（评审打分），与生成 LLM 隔离，避免“自评”虚高导致弱章放行。
        # Task 5: routed through the review phase pool (review_router) so review
        # models are isolated from generation models per the model_router config.
        review_cfg = self.config.get("review_llm") or {}
        if review_cfg and not review_cfg.get("use_mock"):
            self.review_llm = self.review_router
            self.review_provider_cfg = _provider_config_from_runtime(
                self.config, section="review_provider")
        else:
            self.review_llm = self.review_router
            self.review_provider_cfg = self.provider_cfg
        self.reviewer = ReviewerAgent(
            llm_client=self.review_router, provider_config=self.review_provider_cfg)

        self._attempt_index = 0

    def _load_session_tree(self):
        if self.session_tree_path.exists():
            try:
                data = json.loads(self.session_tree_path.read_text(encoding="utf-8"))
                self.session_tree = SessionTree.from_dict(data)
                logger.info("Loaded existing SessionTree from disk.")
                return
            except Exception as e:
                logger.error(f"Failed to load SessionTree: {e}. Starting fresh.")
        self.session_tree = SessionTree()
        # Initialize with Chapter 0 root commit representing initial world state
        try:
            init_state = {
                "characters": self.simulator.characters.get("characters", {}),
                "factions": self.simulator.factions.get("factions", {}),
                "power_system": self.simulator.power_system,
            }
            self.session_tree.add_commit(
                chapter_num=0,
                content_hash="init",
                world_state_snapshot=init_state,
                score=100,
                branch_name="main"
            )
            self._save_session_tree()
            logger.info("Initialized SessionTree with Chapter 0 root node.")
        except Exception as e:
            logger.error(f"Failed to initialize Chapter 0 node: {e}")

    def _save_session_tree(self):
        try:
            # 只保留最近100章的世界状态快照，避免 session_tree.json 无限膨胀
            # （回滚仅需上一章快照，更早的可安全清空）
            self.session_tree.prune_snapshots(branch_name="main", keep=100)
            self.session_tree_path.parent.mkdir(parents=True, exist_ok=True)
            self.session_tree_path.write_text(
                json.dumps(self.session_tree.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            logger.info("Saved SessionTree to disk.")
        except Exception as e:
            logger.error(f"Failed to save SessionTree: {e}")

    def _load_director_fixed_context(self) -> dict:
        """一次性加载固定上下文（bible + planning + foreshadow），避免每章重复 I/O。"""
        def _load_json(path: Path) -> dict:
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        return json.load(f)
                except (json.JSONDecodeError, ValueError):
                    return {}
            return {}

        def _load_text(path: Path) -> str:
            if path.exists():
                return path.read_text(encoding="utf-8")
            return ""

        volumes = _load_json(self.root / "config" / "planning" / "volumes.json")
        plot_graph = _load_json(self.root / "config" / "planning" / "plot_graph.json")
        foreshadow_registry = _load_json(self.root / "config" / "foreshadow" / "registry.json")

        # Load bible files once — these are static and reused every chapter
        bible_cache = {
            "world": _load_text(self.root / "bible" / "world_bible.md"),
            "character": _load_text(self.root / "bible" / "character_bible.md"),
            "style": _load_text(self.root / "bible" / "style_bible.md"),
            "author_intent": _load_text(self.root / "bible" / "author_intent.md"),
        }

        return {
            "volumes": volumes,
            "plot_graph": plot_graph,
            "foreshadow_registry": foreshadow_registry,
            "bible_cache": bible_cache,
        }

    def _sync_db_if_changed(self):
        """Lazy DB reload: only sync StateDB instances when source JSON files change."""
        import os
        world_state_dir = self.root / "memory" / "world_state"
        json_files = list(world_state_dir.glob("*.json"))
        if not json_files:
            return
        # Compute max mtime of source JSON files
        max_mtime = max(os.path.getmtime(f) for f in json_files)
        # Compare with stored mtime
        stored = getattr(self, "_db_source_mtime", 0.0)
        if max_mtime <= stored:
            return  # No changes, skip reload
        # Reload all DB instances
        self.db.import_from_json()
        if hasattr(self.simulator, "db"):
            self.simulator.db.import_from_json()
        if hasattr(self.director, "db"):
            self.director.db.import_from_json()
        self._db_source_mtime = max_mtime

    @property
    def _policy(self):
        """Cached quality policy for this run."""
        if not hasattr(self, '_cached_policy'):
            self._cached_policy = load_quality_policy(self.root)
        return self._cached_policy

    def generate_single_chapter(self, chapter_num: int) -> dict:
        """
        生成单章的完整流水线。
        返回结果字典。
        """
        result = {
            "chapter": chapter_num,
            "success": False,
            "score": None,
            "errors": [],
        }

        logger.info(f"=== Starting chapter {chapter_num} ===")

        # 清除上一章遗留的 pending changes
        pending_dir = self.root / "memory" / "world_state" / "pending"
        pending_dir.mkdir(parents=True, exist_ok=True)
        pending_file = pending_dir / "pending_changes.json"
        pending_file.write_text(json.dumps({"pending_changes": []}), encoding="utf-8")

        # Lazy DB reload: only sync if source JSON files have changed (mtime check)
        self._sync_db_if_changed()

        sm = self.state_machine
        sm.current_chapter = chapter_num
        sm.current_phase = ChapterPhase.INIT
        sm.retry_count = 0

        # 阶段0：规划
        try:
            sm.transition(ChapterPhase.PLANNING)
        except Exception as e:
            result["errors"].append(f"PLANNING: {e}")
            sm.handle_failure("planning_error", str(e))
            return result

        # 阶段1：世界模拟器预计算
        try:
            world_state = self._stage_world_sim(chapter_num)
        except Exception as e:
            result["errors"].append(f"WORLD_SIM: {e}")
            sm.handle_failure("world_sim_error", str(e))
            return result

        # 阶段2：章节导演生成任务卡（P0 冻结：复用首轮 task_card，避免移动目标）
        try:
            if chapter_num in self._frozen_task_cards:
                task_card = self._frozen_task_cards[chapter_num]
                # 仍需推进状态机，避免 WORLD_SIM→WRITE_SCENE 非法跳转；直接置位确保合法
                sm.current_phase = ChapterPhase.DIRECTING
                sm._save_state()
                logger.info(f"State transition: WORLD_SIM → DIRECTING (chapter {chapter_num}) [frozen]")
                logger.info(f"Using frozen task_card for chapter {chapter_num}")
            else:
                task_card = self._stage_directing(chapter_num, world_state)
                self._frozen_task_cards[chapter_num] = task_card

            # round100: 时间线锚点声明+连续性校验（task_card 就绪后，非阻塞）
            try:
                from novel_engine.timeline.timeline_anchors import save_anchor, validate_continuity, get_prev_anchor
                _ts = str(task_card.get("time_setting", ""))
                _anchor = {
                    "chapter_id": chapter_num,
                    "in_world_datetime_start": _ts,
                    "in_world_datetime_end": "",
                    "elapsed_since_prev": _ts if _ts else "数日内",
                    "jump": ("年" in _ts) if _ts else False,
                    "character_age_snapshot": {},
                }
                _ok, _errs = validate_continuity(_anchor, get_prev_anchor(chapter_num))
                if not _ok:
                    logger.warning(f"[timeline] ch{chapter_num} continuity fail: {_errs}")
                save_anchor(_anchor)
                logger.info(f"Timeline anchor saved for chapter {chapter_num} (elapsed={_anchor['elapsed_since_prev']})")
            except Exception:
                logger.exception(f"[timeline] anchor save failed ch{chapter_num} (non-blocking)")
        except Exception as e:
            result["errors"].append(f"DIRECTING: {e}")
            sm.handle_failure("directing_error", str(e))
            return result

        # 阶段3：缩写生成（若 task_card 已含 synopsis 则复用，避免重生成导致目标漂移）
        try:
            if chapter_num in self._frozen_synopsis:
                synopsis = self._frozen_synopsis[chapter_num]
                sm.current_phase = ChapterPhase.SYNOPSIS
                sm._save_state()
                logger.info(f"State transition: DIRECTING → SYNOPSIS (chapter {chapter_num}) [frozen]")
                logger.info(f"Using frozen synopsis for chapter {chapter_num}")
            else:
                synopsis = self._stage_synopsis(task_card)
                self._frozen_synopsis[chapter_num] = synopsis
        except Exception as e:
            result["errors"].append(f"SYNOPSIS: {e}")
            sm.handle_failure("synopsis_error", str(e))
            return result

        # 阶段4：正文生成（场景级）+ 润色
        try:
            novel_text = self._stage_write(task_card, synopsis)
            novel_text = self._ensure_chinese(novel_text)
            self.current_novel = novel_text
            # P1 场景数校验（B2 结构版）：结构化场景按构造保证数量（generate_full_chapter
            # 缺任一场景即抛错走 WRITE 失败路径，不会静默缺失），故不再按标记计数。
            # 此处仅做结构复核：计数不一致时按蓝图补生成缺失场景，并经
            # _assemble_chapter_text 重组（无标记拼接）。writer.last_scenes 为空
            #（mock/改写等未走结构化生成的路径）则跳过。
            try:
                expected_bps = task_card.get("scene_blueprints", []) or []
                staged = list(getattr(self.writer, "last_scenes", None) or [])
                if staged and expected_bps and len(staged) != len(expected_bps):
                    logger.warning(f"Scene count mismatch: expected {len(expected_bps)}, got {len(staged)} (chapter {chapter_num}),补写缺失场景")
                    have_ids = {s.scene_id for s in staged}
                    for bp in expected_bps:
                        if bp.get("scene_num") not in have_ids:
                            try:
                                new_scene = self.writer.generate_scene(task_card, bp, synopsis.get("synopsis",""), self.current_novel[-600:] if len(self.current_novel)>600 else "", "")
                                staged.append(new_scene)
                                try:
                                    append_scene(self.root, chapter_num,
                                                 {"scene_id": new_scene.scene_id, "scene_text": new_scene.scene_text,
                                                  "hook": new_scene.hook, "beats": new_scene.beats})
                                except Exception as je:
                                    logger.warning(f"Journal append failed for补写 scene {bp.get('scene_num')}: {je}")
                            except Exception as ce:
                                logger.error(f"补写缺失场景 {bp.get('scene_num')} 失败: {ce}")
                    staged.sort(key=lambda s: s.scene_id)
                    novel_text = self._assemble_chapter_text(staged)
                    self.current_novel = novel_text
            except Exception as sce:
                logger.warning(f"Scene count check skipped: {sce}")
        except (ChapterResampleRequiredError, SceneUnrecoverableError) as ce:
            # CC round-7 P0-2：整章近空簇（≥2）或单场景两救仍真退化（近空/空/乱码，
            # 非连贯短文）都判“质量类整章重采”：不进 NEEDS_HUMAN、不在本章用同一任务卡
            # 烧重试；交 runner HALT→看门狗，绕过 director 缓存带新 end_state/timeline
            # 约束重新规划，避免复用同一张坏任务卡造成死循环。
            _ids = list(getattr(ce, "scene_ids", None) or [])
            if not _ids and getattr(ce, "scene_id", None) is not None:
                _ids = [ce.scene_id]
            logger.warning(f"WRITE REPLAN ch{task_card.get('chapter_num')}: {ce}")
            result["errors"].append(f"WRITE_REPLAN: {ce}")
            result["quality_replan"] = True
            result["replan_scene_ids"] = _ids
            result["replan_reason"] = getattr(ce, "replan", None) or "scene_near_empty_cluster"
            if isinstance(ce, ChapterQualityGapError):  # CC20：质量留空，不整批 HALT
                result["quality_gap_continue"] = True
                result["gap_kind"] = getattr(ce, "gap_kind", None) or "quality"
                result["gap_violations"] = list(getattr(ce, "violations", None) or [])
            return result
        except Exception as e:
            import traceback
            logger.error(f"WRITE: {e}\n{traceback.format_exc()}")
            result["errors"].append(f"WRITE: {e}")
            sm.handle_failure("writing_error", str(e))
            return result

        # 阶段4.5：字数强制（P0 单一权威：终稿评审前必达标；P1 全局常量 7500，避免漂移）
        try:
            # P1-目标全局常量：顶配网文章节 7000-8000 为优，不再每章 LLM 生成目标
            # 单一策略源：目标字数读 quality_policy（默认 7500，与旧常量同值）
            _qp = self._policy
            GLOBAL_CONSTANT = int(_qp["chapter_target_chars"])
            if not hasattr(self, "_global_target") or self._global_target is None:
                self._global_target = GLOBAL_CONSTANT
                logger.info(f"Global target frozen: {self._global_target} (constant 7500, P1)")
            total_target = self._global_target  # 字数硬门锚定全局章节目标（模型 blueprint 字数和会漂移）
            if total_target > 0:
                policy = _qp
                target_min = int(total_target * policy["min_ratio"])
                target_max = int(total_target * policy["max_ratio"] + max(policy["tolerance_chars"], total_target * 0.02))  # +容差避免 1 字符误杀
                novel_text = self._enforce_word_count(novel_text, target_min, target_max)
                self.current_novel = novel_text
                # 强制后重跑确定性门控，以最终长度为准（用全局目标校验）
                _gate_card = {"scene_blueprints": [{"word_count_target": total_target}]}
                gate_after = self._deterministic_quality_gate(novel_text, _gate_card)
                self._last_deterministic_issues = gate_after["issues"]
                if gate_after["passed"]:
                    logger.info(f"Post-enforce gate passed for chapter {chapter_num} (target {total_target} → {target_min}-{target_max})")
                else:
                    logger.warning(f"Post-enforce gate still flagged: {gate_after['issues']}")
        except Exception as _we:
            logger.warning(f"Word-count enforcement skipped: {_we}")

        # R16c P0-A/B: 预评审+轮兜底强制必填伏笔闭环（独立于 reviewer 归因，每轮 review 前执行）
        # 首次调用在场景拼装后执行；后续轮次在 patch 回写 journal 后再次调用，确保重生
        # 的 scene_text 真正进入当轮评审文本与最终成稿。
        _pre_mand = self._enforce_mandatory_beats(chapter_num, task_card, synopsis_text=synopsis.get('synopsis', '') if isinstance(synopsis, dict) else str(synopsis or ''))
        if _pre_mand.get('blocked'):
            logger.error(
                f'ch{chapter_num} MANDATORY HARD BLOCK at pre-review: '
                f'foreshadow beats unresolved in scenes {list(_pre_mand.get("missing_fs_ids", []))}')
            self._mandatory_blocked = _pre_mand['missing_scenes']
            result['success'] = False
            result['published'] = False
            result['hard_block'] = True
            result['hard_block_reason'] = (
                f'mandatory foreshadow beats unresolved: '
                f'{list(_pre_mand.get("missing_fs_ids", []))}')
            self._flag_for_human(
                chapter_num, 0,
                f'mandatory foreshadow beats unresolved in scenes '
                f'{list(_pre_mand.get("missing_scenes", {}).keys())}; isolate to draft/failed')
            result["success"] = False
            return result
        self._mandatory_blocked = _pre_mand.get('missing_scenes') or {}
        # R16c P0-B: 当 _enforce_mandatory_beats 本轮重生后，将自刷新到局部变量，
        # R16c P0-B: initialize mirrors up-front to avoid UnboundLocalError
        # when enforced branch runs but caller never enters fix-loop below.
        current = self.current_novel
        current_draft = getattr(self, "_draft_novel", current)
        if _pre_mand.get('enforced') and not _pre_mand.get('blocked'):
            current = self.current_novel or current
            current_draft = self._draft_novel or current_draft

        # 阶段5：审查评分
        try:
            self._finalize_current_novel(chapter_num)
            stage_review = self._stage_review(chapter_num, task_card, synopsis, self._novel_string(), world_state)
            review = stage_review["review"]
            score = stage_review["score"]
            verdict = stage_review["verdict"]
            result["score"] = score
            apply_world_state = True

            # CC round-8 P0-3：三评极差>15 → 评审本身不可靠，无论中位数是否过线都不自动提交，
            # 落 draft 强制人工复核（不做整章重采，重采无法消除评审噪声）。
            # 2026-10-13 ch292 归因补充：低分 unstable 章（如 ch292，5258 字严重短于 7310 下限）
            # 先给一次 A 类整章重写预算——重写改变文本后评审噪声可能收敛（ch299 已验证 rewrite→fix
            # 有效），这与"重采"（同文重评）不同；只有高分 unstable（score>=60）或升级未启用时
            # 才直接强制人工，避免绕过已验证有效的修复路径。
            if stage_review.get("review_unstable"):
                _unstable_upgrade = _unstable_upgrade_eligible(self._policy, score)
                if _unstable_upgrade:
                    _lsr_floor = float(((self._policy or {}).get("low_score_rewrite") or {}).get(
                        "floor", int((self._policy or {}).get("fix_threshold", 60))))
                    logger.warning(
                        f"Chapter {chapter_num} review highly unstable but low score "
                        f"({score} < {_lsr_floor}) -> try whole-chapter rewrite first (A-class upgrade); "
                        f"human review only if still unstable/low")
                    verdict = "fail"  # 强制走 low_score_eligible 整章重写路径，不直接挂人工
                else:
                    logger.error(
                        f"Chapter {chapter_num} review highly unstable (median-of-3 range>15, "
                        f"score={score}) -> force human review, no commit")
                    try:
                        _dp = self.root / "chapters" / "draft" / f"chapter_{chapter_num}.txt"
                        _dp.parent.mkdir(parents=True, exist_ok=True)
                        _dp.write_text(self._novel_string(), encoding="utf-8")
                    except Exception as _de:
                        logger.warning(f"save unstable draft failed: {_de}")
                    result["success"] = False
                    result["published"] = False
                    result["score"] = score
                    result["review_unstable"] = True
                    result["pending_human_review"] = True
                    result["human_review_note"] = (
                        f"score={score} 三评极差>15，评审极不稳定，强制人工复核（不自动重采）")
                    self._flag_for_human(chapter_num, score, "review highly unstable (median-of-3 range>15)")
                    return result

            # A类：低分整章重写——verdict="fail"（score 低于 fix_threshold）且启用时
            # 也进入修复路径（fix loop 内低分升级为整章重写），不再直接挂人工。
            _lsr_cfg = (self._policy or {}).get("low_score_rewrite") or {}
            _lsr_enabled = bool(_lsr_cfg.get("enabled", True))
            _lsr_floor = float(_lsr_cfg.get(
                "floor", int(self._policy.get("fix_threshold", 60))))
            _low_score_eligible = (
                _lsr_enabled
                and verdict == "fail"
                and isinstance(score, (int, float))
                and 0 < score < _lsr_floor
            )
            if verdict == "pass":
                result["success"] = True
                logger.info(f"Chapter {chapter_num} PASSED (score={score})")
            elif verdict == "fix" or _low_score_eligible:
                if _low_score_eligible:
                    logger.warning(
                        f"Chapter {chapter_num} low score ({score} < {_lsr_floor}) "
                        f"→ routed into fix loop for low-score whole-chapter rewrite")
                orig_novel = self.current_novel
                orig_draft = getattr(self, "_draft_novel", orig_novel)
                pre_fix_score = score
                publication_line = int(self._policy["publication_line"])
                _soft_line = int(self._policy.get("soft_publication_line", publication_line - 3))
                min_ch = publication_line
                # CC E 包：整章级 fix 最多 2 轮；单场景定点重生另有每场景 ≤2 次独立预算
                max_fix = int(self.config.get("pipeline", {}).get("max_chapter_fix_rounds", 2))
                current = orig_novel
                current_draft = orig_draft
                # CC28 批B：把初始候选（修复前）与当前 journal 事实态绑定。各轮修复都会就地
                # 覆写 journal；最终采纳 best 时必须把 journal 一并原子回滚到该快照，否则
                # 字符串层回滚了、journal 仍是劣化轮的内容，后续从 journal 组装/发布即成劣稿。
                best_state = _gates.BestCandidateState(
                    best_score=pre_fix_score,
                    best_novel=orig_novel,
                    best_draft=orig_draft,
                    best_journal_snapshot=snapshot_journal(self.root, chapter_num),
                )
                det_issues_pre = getattr(self, "_last_deterministic_issues", [])
                if det_issues_pre:
                    logger.warning(f"Deterministic gate has issues pre-fix: {det_issues_pre} → force patch/rewrite")
                try:
                    # frozen 任务卡在 patch / rewrite 两条分支都会用到，循环外无条件绑定，
                    # 避免定点修复返回 None 改走 rewrite 分支时 frozen 未绑定引发 UnboundLocalError
                    frozen = self._frozen_task_cards.get(chapter_num, task_card)
                    _reaction_fix_budget = 1  # CC round-19 P9d: 整章至多1次 reaction 定点修订
                    for _ in range(max_fix):
                        current, current_draft, best_state, _reaction_fix_budget, _stop = \
                            self._run_fix_loop_iteration(
                                chapter_num, task_card, synopsis,
                                current, current_draft, best_state,
                                _, min_ch, _soft_line,
                                world_state, result,
                                _reaction_fix_budget, frozen)
                        if _stop:
                            break
                    # CC28 批B：优先采纳"硬门全绿"候选；没有任何全绿候选时才退回最高分候选
                    # （供人审队列），并保持其分数为最终分。
                    best_score, best_novel, best_draft, _best_journal_snapshot = self._select_final_candidate(chapter_num, best_state, result)
                    # CC round-18 P0-3：修订后二次长度门（E-loop“先删后补”可能让成稿缩水）
                    try:
                        from novel_engine.quality.post_fix_length import post_fix_length_decision
                        _wp18 = self._policy
                        _soft_floor18 = int(_wp18["chapter_target_chars"] * _wp18["min_ratio"])
                        _len18 = post_fix_length_decision(len(best_novel or ""), soft_floor=_soft_floor18)
                        if _len18["status"] == "needs_topup" and not (self.config.get("llm") or {}).get("use_mock"):
                            logger.warning(f"Post-fix length ch{chapter_num}: {_len18['current']} in topup band "
                                           f"(floor {_soft_floor18}, gap {_len18['gap']}) -> bounded descriptive topup")
                            _js18 = load_authoritative_scenes(self.root, chapter_num)  # CC19 Q2
                            if _js18:
                                _sc18 = [SceneOutput(scene_id=int(d["scene_id"]), scene_text=d.get("scene_text", "") or "",
                                                     hook=d.get("hook", "") or "") for d in sorted(_js18, key=lambda x: x["scene_id"])]
                                _frozen18 = self._frozen_task_cards.get(chapter_num, task_card)
                                _to18 = self._apply_length_floor(chapter_num, _frozen18, _sc18, best_novel)
                                if _to18 and _to18 != best_novel and len(_to18) > len(best_novel):
                                    best_novel = _to18
                                    best_draft = _to18
                                    best_state = _gates.BestCandidateState(
                                        best_score=best_score,
                                        best_novel=best_novel,
                                        best_draft=best_draft,
                                        best_journal_snapshot=_best_journal_snapshot,
                                        best_green=best_state.best_green,  # 保留原best_green状态
                                    )
                                    self.current_novel = best_novel
                                    self._draft_novel = best_draft
                                    logger.info(f"Post-fix topup ch{chapter_num}: -> {len(best_novel)} chars")
                        elif _len18["status"] == "severe_shortfall":
                            logger.warning(f"Post-fix length ch{chapter_num}: SEVERE shortfall {_len18['current']} "
                                           f"< topup line {_len18['topup_line']} -> flag human, no large auto-topup")
                            result["severe_shortfall"] = True
                            result["severe_shortfall_chars"] = _len18["current"]
                            self._flag_for_human(chapter_num, best_score,
                                                 f"post-fix severe shortfall {_len18['current']}/{_soft_floor18}; please verify")
                    except Exception as _e18:
                        logger.warning(f"Post-fix length check skipped: {_e18}")
                    # P7D+P8B+P8C：最终门 scene_texts 同源约定——与 best_novel 同源同序。
                    # best_novel 是已选定候选；best_novel 在阶段5评审前已通过
                    # _finalize_current_novel 完成长句定稿（pipeline_orchestrator.py ~L4095），
                    # 故此处无需重复 finalize，直接按※切出分场即可。
                    _SEP = chr(0x203B)  # ※
                    if best_novel and _SEP in best_novel:
                        from novel_engine.quality.cross_scene_crisis_gate import _split_text_into_scenes
                        _scene_texts_for_gate = _split_text_into_scenes(best_novel, self._frozen_task_cards.get(chapter_num, task_card))
                    else:
                        _scene_texts_for_gate = None
                    _final_det_g = self._deterministic_quality_gate(best_novel, self._frozen_task_cards.get(chapter_num, task_card), scene_texts=_scene_texts_for_gate)
                    # D3/E3 P8F: 氛围去重检测（软 note，不阻断）——对与危机门同源的 best_novel 跑检测
                    # 命中后：real-lang 才发 LLM 定向重抛光；抛光后全章全门回归；任一回归即回滚。
                    _atm_repolished = False
                    try:
                        from novel_engine.quality.atmosphere_dedup import (
                            cn_chars,
                            run_atmosphere_dedup as _rad,
                            generate_repolish_directive as _grd,
                            roll_back_ok as _rbo,
                            _split_scenes as _split,
                        )
                        _atm_result = _rad(best_novel, root=self.root)
                        _atm_hits = _atm_result.get("hits") or []
                        if _atm_hits:
                            _hit_strs = [
                                f"场{h['scene_num']}:{h.get('family','?')}({h.get('conc_scene_a',0):.1f}/{h.get('conc_scene_b',0):.1f})/k"
                                for h in _atm_hits
                            ]
                            logger.info(
                                f"ch{chapter_num} atmosphere dedup hits: {len(_atm_hits)} "
                                f"notes={_hit_strs}")
                            result["atmosphere_dedup_hits"] = _atm_hits
                            result["atmosphere_dedup_soft_note"] = "; ".join(_hit_strs)

                            # E3 P8F: real-lang 才发 LLM 重抛光；全章候选回归 + 采纳即回写 best_novel/best_draft
                            _use_mock = bool((self.config.get("llm") or {}).get("use_mock"))
                            if not _use_mock and _atm_hits:
                                try:
                                    _atm_scenes = _split(best_novel)
                                    _bps = self._frozen_task_cards.get(chapter_num, task_card).get("scene_blueprints", [])
                                    _bps_by_sid = {int(bp.get("scene_num", 0) or 0): bp for bp in _bps if isinstance(bp, dict)}
                                    _max_repolish = 2  # 整章上限 2 场
                                    _repolished_count = 0
                                    _accepted_sids: list[int] = []
                                    _orig_scene_texts = dict(_scene_texts_for_gate or {})
                                    # Baseline gate issues for comparison
                                    _base_issues = set(_final_det_g.get("issues") or [])
                                    _base_passed = _final_det_g.get("passed", True)
                                    _sep_char = chr(0x203B)
                                    # Pre-bind violations: empty until forbidden gate runs.
                                    violations = None
                                    for hit in _atm_hits:
                                        if _repolished_count >= _max_repolish:
                                            break
                                        _sid = hit["scene_num"]
                                        _idx = _sid - 1
                                        if _idx < 0 or _idx >= len(_atm_scenes):
                                            continue
                                        _orig_scene = _atm_scenes[_idx]
                                        if not _orig_scene:
                                            continue
                                        _dir = _grd(hit)
                                        _bp = _bps_by_sid.get(_sid, {})
                                        _prev = best_novel[-600:] if len(best_novel) > 600 else ""
                                        try:
                                            _ns = self.writer.generate_scene(
                                                task_card, _bp,
                                                synopsis.get("synopsis", "") if isinstance(synopsis, dict) else "",
                                                previous_context=_prev,
                                                fix_directive=_dir,
                                            )
                                            if _ns is None:
                                                continue
                                            _polished = getattr(_ns, "scene_text", "") or ""
                                            if not _polished:
                                                continue
                                            # F3: 构建全章候选文本（与 best_novel 同源分隔符）
                                            _candidate_parts = list(_atm_scenes)
                                            _candidate_parts[_idx] = _polished
                                            _candidate_text = _sep_char.join(_candidate_parts)
                                            _candidate_scenes = _split(_candidate_text)
                                            # 场数与※数不变
                                            if len(_candidate_scenes) != len(_atm_scenes):
                                                logger.warning(f"ch{chapter_num} atm repolish scene{_sid}: scene count changed {len(_candidate_scenes)}!={len(_atm_scenes)} -> rollback")
                                                continue
                                            # 全章候选门回归（与最终门同口径）
                                            _cand_gate = self._deterministic_quality_gate(
                                                _candidate_text,
                                                self._frozen_task_cards.get(chapter_num, task_card),
                                                scene_texts={i+1: s for i, s in enumerate(_candidate_scenes)},
                                            )
                                            _forbidden = self._forbidden_violations(_candidate_text, "")
                                            # 对比 baseline：新增 hard/high、危机翻坏、极性翻坏、新禁词/套话
                                            _cand_issues = set(_cand_gate.get("issues") or [])
                                            _new_issues = _cand_issues - _base_issues
                                            _has_hard_high = any(
                                                i.get("severity") in ("hard", "high")
                                                for i in (_cand_gate.get("issues") or [])
                                            )
                                            _score_ok = (
                                                not _cand_gate.get("passed", True) and _base_passed
                                            ) or (
                                                _has_hard_high and not any(
                                                    i.get("severity") in ("hard", "high")
                                                    for i in (_final_det_g.get("issues") or [])
                                                )
                                            )
                                            _new_forbidden = bool(_forbidden) and not any(
                                                v.get("name") in [iv.get("name") for iv in violations or []]
                                                for v in _forbidden
                                            ) if violations is not None else bool(_forbidden)
                                            # 字数守限
                                            _new_total_cn = sum(cn_chars(s) for s in _candidate_scenes)
                                            _qp = self._policy
                                            _bpt_total = sum(self._bpt(bp) for bp in _bps) or 0
                                            _topup_min = int(_bpt_total * _qp["min_ratio"]) if _bpt_total else 6800
                                            _ok_len = (
                                                all(cn_chars(s) >= 1000 for s in _candidate_scenes)
                                                and _topup_min <= _new_total_cn <= 10660
                                            )
                                            # 回滚判定：全门回归 + 字数
                                            _ok = _rbo(
                                                _polished, _orig_scene, _new_total_cn,
                                                gate_result=_cand_gate,
                                                forbidden_result=_forbidden,
                                            ) and not _score_ok and not _new_forbidden and _ok_len
                                            if _ok:
                                                # 采纳：重建 best_novel/best_draft，重算 scene_texts_for_gate
                                                _best_novel_polished = _candidate_text
                                                _best_draft_polished = _candidate_text
                                                _scene_texts_polished = {
                                                    i + 1: s for i, s in enumerate(_candidate_scenes)
                                                }
                                                best_novel = _best_novel_polished
                                                best_draft = _best_draft_polished
                                                _scene_texts_for_gate = _scene_texts_polished
                                                self.current_novel = _best_novel_polished
                                                self._draft_novel = _best_draft_polished
                                                self._finalize_current_novel(chapter_num)
                                                _accepted_sids.append(_sid)
                                                _repolished_count += 1
                                                _atm_scenes = _split(best_novel)  # 刷新 local cache
                                            else:
                                                logger.info(
                                                    f"ch{chapter_num} atm repolish scene{_sid} rejected: "
                                                    f"gate_pass={_cand_gate.get('passed')}, "
                                                    f"hard_high={_has_hard_high}, "
                                                    f"forbidden={bool(_forbidden)}, len_ok={_ok_len}")
                                        except Exception as _erep:
                                            logger.warning(f"ch{chapter_num} atm repolish scene{_sid} failed: {_erep}")
                                            continue
                                    if _accepted_sids:
                                        result["atmosphere_repolished_scenes"] = _accepted_sids
                                        logger.info(
                                            f"ch{chapter_num} atmosphere repolish accepted: {len(_accepted_sids)} scenes")
                                except Exception as _ae3:
                                    logger.warning(f"ch{chapter_num} atmosphere repolish failed: {_ae3}")
                    except Exception as _ade:
                        logger.warning(f"ch{chapter_num} atmosphere dedup skipped: {_ade}")
                    _final_pol_g = self._policy
                    _final_high_g = any(b for b, _ in (_review_issue_is_blocking(_final_pol_g, iss) for iss in (review.get("issues") or [])))
                    _soft_line = int(_final_pol_g.get("soft_publication_line", min_ch - 3))
                    _gray_final = (_soft_line <= best_score < min_ch) and not _final_high_g and _final_det_g["passed"]
                    if _gray_final:
                        logger.warning(f"Gray-band final release ch{chapter_num} score {best_score} (gates pass, no-high) → publish to novel, flagged human spot-check")
                        result["gray_band_release"] = True
                        result["gray_band_score"] = best_score
                        self._flag_for_human(chapter_num, best_score, f"gray-band final release {best_score}: spot-read required" + ("; reaction consistency 未消解（已尝试定点修订）" if (_final_det_g.get("reaction_hits") and _reaction_fix_budget == 0) else ""))
                    # Hoisted out of the if-branch: ensure final_det is always bound
                    # for the else path (force-best when best_score < min_ch and not gray_final).
                    # Previously UnboundLocalError was silently swallowed by the outer except.
                    final_det = self._deterministic_quality_gate(best_novel, self._frozen_task_cards.get(chapter_num, task_card), scene_texts=_scene_texts_for_gate)
                    if best_score >= min_ch or _gray_final:
                        # 最终仍需校验硬门控；若仍硬阻断则强制发布 best 供人审阅（附 note），不跳过章节
                        # P7D：scene_texts 与 best_novel 同源（见上方 _scene_texts_for_gate 计算）
                        _final_policy = self._policy
                        final_high = any(b for b, _ in (_review_issue_is_blocking(_final_policy, iss) for iss in (review.get("issues") or [])))
                        if (final_high or not final_det["passed"]) and not _gray_final:
                            logger.warning(f"Best score {best_score} ≥ {min_ch} but hard gate still blocked: high={final_high} det={final_det['issues']} → force publish best with note")
                            result["success"] = True
                            result["published"] = True
                            result["note"] = f"hard gate blocked but force publish {best_score}: det={final_det['issues']} high={final_high}"
                            apply_world_state = False
                            self._force_publish_best = True
                            self._flag_for_human(chapter_num, best_score, f"force publish despite hard gate: det={final_det['issues']}")
                        else:
                            result["success"] = True
                            apply_world_state = True
                    else:
                        # P0-终态发布：耗尽后仍发布 best 供人审阅（附 note），不重启整章
                        logger.warning(f"Fix exhausted, force-best to draft {best_score} < {min_ch} (never novel/) with note (hard gate may still block)")
                        # 即使 <88 也发布，但不落库世界状态，附 note 供人审。
                        # P0-2: 快照 det/high 到 note，避免 future force 无归因证据（backlog 2026-09-06）。
                        _snapshot_det = list(final_det.get("issues") or [])
                        result["success"] = True
                        result["published"] = True
                        result["note"] = f"best {best_score} < {min_ch} (hard gate {_snapshot_det})"
                        apply_world_state = False
                        # 直接准备落盘 best，不再走 _recover
                        # 将 best 设为当前，便于后续 commit 使用
                        self.current_novel = best_novel
                        self._draft_novel = best_draft
                        # 强制通过发布门控（附 note），由最终提交阶段处理
                        # 标记为需人工复核但仍写盘
                        self._force_publish_best = True
                except Exception as _pe:
                    logger.warning(f"Auto-fix failed ({_pe}); keeping pre-fix (score={pre_fix_score})")
                    self.current_novel = orig_novel
                    result["score"] = pre_fix_score
                    if pre_fix_score >= min_ch:
                        result["success"] = True
                        apply_world_state = True
                    else:
                        result["success"] = False
                        apply_world_state = False
                        self._flag_for_human(chapter_num, pre_fix_score, "auto-fix failed; below min_ch")
            else:
                # 全量回退：回滚世界状态，避免污染后续章节。
                # 不在章节内递归重试 —— 递归会和外层 _run_with_retry 的整章重试叠加，
                # 造成无限自旋并大量浪费 API。交由 _run_with_retry 以不同温度重试整章。
                result["errors"].append(f"FAIL: score={score}")

                prev_chapter = chapter_num - 1
                logger.warning(f"Review failed (Score={score}). Rolling back world state to Chapter {prev_chapter}.")

                history = self.session_tree.get_branch_history("main")
                target_node = None
                for node in reversed(history):
                    if node.chapter_num == prev_chapter:
                        target_node = node
                        break

                if target_node:
                    try:
                        rolled_snapshot = self.session_tree.rollback_to_node(target_node.node_id, branch_name="main")
                        self._save_session_tree()
                        if "characters" in rolled_snapshot:
                            self.simulator.characters = rolled_snapshot["characters"]
                            self.simulator._save_characters()
                        if "factions" in rolled_snapshot:
                            self.simulator.factions = rolled_snapshot["factions"]
                            self.simulator._save_factions()
                        if "power_system" in rolled_snapshot:
                            self.simulator.power_system = rolled_snapshot["power_system"]
                            self.simulator._save_power_system()
                        self.simulator.db.import_from_json()
                        self.db.import_from_json()
                        logger.info("World state successfully rolled back and StateDB synchronized.")
                    except Exception as _rb:
                        logger.error(f"Rollback failed: {_rb}")
                else:
                    logger.warning(f"No SessionNode found for Chapter {prev_chapter} in SessionTree history.")

                sm.handle_failure("review_exhausted", f"Score={score}")
                result["success"] = False
                return result
        except Exception as e:
            result["errors"].append(f"REVIEW: {e}")
            sm.handle_failure("review_error", str(e))
            return result

        # Task 9: forbidden gate — evaluate via gates.py pure function
        from novel_engine.pipeline.gates import evaluate_forbidden_gate
        _fg_result = evaluate_forbidden_gate(
            chapter_num,
            self.current_novel,
            review,
            self._policy,
            apply_world_state,
            self._forbidden_violations,
        )
        apply_world_state = _fg_result.apply_world_state
        violations = _fg_result.violations
        if violations:
            for v in violations:
                self.defects.add(chapter_num, "forbidden_violation",
                                 f"{v.get('name')}:{v.get('match')}")
            if not _fg_result.success:
                result["success"] = False
                result["forbidden_violations"] = _fg_result.violations
                if _fg_result.flag_human:
                    self._flag_for_human(chapter_num, result.get("score", 0), _fg_result.flag_reason or "forbidden violation")
            else:
                logger.warning(
                    f"Chapter {chapter_num} non-blocking forbidden hits recorded: {[_v.get('name') for _v in _fg_result.violations]}")

        # Save review journal for sliding-window quality memory
        from novel_engine.pipeline.review_journal import save_review_journal
        save_review_journal(self.root, chapter_num, score, review)
        # R16c/R17: cur_score 必须在 stage6 前置校验前定义
        cur_score = float(result.get("score", score or 0) or 0)

        # 阶段6：提交（P0-A2 加闸：仅 publication_line 以上且无 policy 硬阻断且确定性硬门控通过才写最终目录）
        _commit_policy = self._policy
        publication_line = int(_commit_policy["publication_line"])
        has_high_issue = any(b for b, _ in (_review_issue_is_blocking(_commit_policy, iss) for iss in (review.get("issues") or [])))
        high_list = [f"{iss.get('dimension')}/{iss.get('severity')}:{iss.get('description','')[:50]}" for iss in (review.get("issues") or []) if _review_issue_is_blocking(_commit_policy, iss)[0]]
        # _last_deterministic_issues 仅含 hard，soft 另取
        det_issues = getattr(self, "_last_deterministic_issues", [])
        det_soft = getattr(self, "_last_deterministic_soft", [])
        # 重新校验当前 purified 文本的硬门控（以防 fix 循环后未更新）
        # P0-写盘单一净化源：当前 purified 即为最终发布文本，检测与写盘同源
        final_text = self._novel_string()
        # 确保最终文本已按正确章号净化（防缓存标题章号错误）
        from novel_engine.quality.repetition_detector import purify_novel_for_publish as _purify_final
        final_text = _purify_final(final_text, chapter_num=chapter_num)
        self.current_novel = final_text
        # R16c P0-C2: stage6 foreshadow coverage check
        from novel_engine.pipeline.final_gate import run_stage6_foreshadow_check
        _stage6_result = run_stage6_foreshadow_check(
            self.root, chapter_num, task_card, final_text, cur_score)
        if _stage6_result.get('blocked'):
            logger.error(
                f'ch{chapter_num} MANDATORY HARD BLOCK at stage6: '
                f'{_stage6_result.get("hard_block_reason", "unknown")}')
            result['success'] = False
            result['published'] = False
            result['hard_block'] = True
            result['hard_block_reason'] = _stage6_result.get('hard_block_reason', 'stage6 foreshadow')
            self._flag_for_human(
                chapter_num, cur_score,
                f'stage6 foreshadow hard block: {_stage6_result.get("hard_block_reason", "unknown")}')
            return result

        # CC30（DS Q5）：真机提交前清洗混入正文的英文脚手架字段词（id/beats/covered 等)。
        # 保守：只清带 JSON 上下文/复合词/同分句>=2 的字段词，白名单与普通英文不碰；清洗后
        # 由紧随的 final_det 重跑长度/密度/拉丁/beats 门；整段全字段词->硬块走既有隔离 gap。
        _scaffold_rollback30 = False
        if not bool((self.config.get("llm") or {}).get("use_mock")):
            try:
                from novel_engine.quality.scaffold_scrub import scrub_scaffold_latin
                _sc30 = scrub_scaffold_latin(final_text)
                if _sc30["status"] == "scrubbed":
                    final_text = _sc30["text"]
                    self.current_novel = final_text
                    logger.warning(f"ch{chapter_num} scaffold-latin scrub removed "
                                   f"{len(_sc30['removed_tokens'])} token(s): {_sc30['removed_tokens'][:8]}")
                elif _sc30["status"] == "rollback_best":
                    _scaffold_rollback30 = True
                    logger.error(f"ch{chapter_num} scaffold-latin whole-paragraph pollution "
                                 f"({_sc30['removed_tokens'][:8]}) -> hard block + gap-continue")
            except Exception as _e30:
                logger.warning(f"scaffold scrub skipped: {_e30}")
        final_det = self._deterministic_quality_gate(final_text, task_card)
        det_issues = final_det["issues"]
        det_soft = final_det.get("soft_issues", [])
        self._last_deterministic_issues = det_issues
        if _scaffold_rollback30:
            det_issues = det_issues + ["scaffold_latin_whole_paragraph_pollution"]
            self._last_deterministic_issues = det_issues
        # P0-发布前泄漏终检：成品不得含任何脚手架 token（与写盘同源）
        from novel_engine.quality.repetition_detector import verify_no_scaffolding
        leak_issues = verify_no_scaffolding(final_text)
        if leak_issues:
            logger.error(f"Leak check failed for chapter {chapter_num}: {leak_issues}")
            det_issues = det_issues + [f"[泄漏] {x}" for x in leak_issues]
            self._last_deterministic_issues = det_issues
        # CC round-19 Q1：提交前对"即将落盘的这同一份 final_text"做统一 fail-closed 终检
        try:
            _fg19 = self._run_final_precommit_gate(chapter_num, task_card, final_text)
        except Exception as _e19:
            logger.warning(f"Final pre-commit gate skipped due to error: {_e19}")
            _fg19 = {"repaired": False, "hard_blocked": False, "violations": [], "text": final_text}
        if _fg19.get("repaired"):
            final_text = _fg19["text"]
            self.current_novel = final_text
            logger.warning(f"Final pre-commit gate ch{chapter_num}: repaired once and re-verified clean")
        if _fg19.get("hard_blocked"):
            _vd19 = [f"{v.get('kind')}:{v.get('detail')}" for v in _fg19.get("violations", [])]
            try:
                _qdir19 = self.root / "chapters" / "draft" / "failed" / f"chapter_{chapter_num}"
                _qdir19.mkdir(parents=True, exist_ok=True)
                (_qdir19 / "final_gate_reject.txt").write_text(final_text, encoding="utf-8")
            except Exception as _qe19:
                logger.error(f"Failed to quarantine final-gate reject ch{chapter_num}: {_qe19}")
            logger.error(f"FINAL GATE BLOCK ch{chapter_num} score={cur_score}: {_vd19} "
                         f"-> quarantine+human, cursor continues")
            self._flag_for_human(chapter_num, cur_score,
                                 "final pre-commit gate blocked: " + "; ".join(_vd19))
            result["success"] = False
            result["published"] = False
            result["score"] = cur_score
            result["final_gate_blocked"] = True
            result["final_gate_violations"] = _vd19
            return result
        # P0-终态发布：fix 耗尽后的 best 按 Q5 只进 chapters/draft（从不进 novel），供人审阅（泄漏仍阻断并走正常仲裁）。
        force_best = bool(getattr(self, "_force_publish_best", False))
        if force_best and result.get("gray_band_release"):
            # CC ch62：gray-band 已授权发布 novel/（全绿发布路径已走通），force-best
            # 不得再以更差版本覆盖。不变量：novel/ 是唯一发布源，已发布章节不降级
            # 为 draft，也不覆盖 checkpoint。短路后走下方 _decide_publish 的
            # _gray_commit 正常发布路径。
            logger.warning(
                f"ch{chapter_num} gray_band_release={result.get('gray_band_score')} — "
                f"force-best short-circuited (novel publish path wins; draft/checkpoint untouched)")
            self._force_publish_best = False
            force_best = False
            result["score"] = cur_score
            result["published"] = True
        if force_best and not leak_issues:
            logger.warning(f"Force-best to draft {cur_score} despite hard gate (note={result.get('note','')}) hard={det_issues} soft={det_soft} high={high_list}")
            self._force_publish_best = False
            try:
                draft_path = self.root / "chapters" / "draft" / f"chapter_{chapter_num}.txt"
                draft_path.parent.mkdir(parents=True, exist_ok=True)
                draft_path.write_text(self._novel_string(), encoding="utf-8")
                create_draft_checkpoint(self.checkpoint_mgr, self.root, chapter_num, self._novel_string())
            except Exception as e:
                logger.error(f"Failed to save force-best draft for chapter {chapter_num}: {e}")
            result["score"] = cur_score
            result["published"] = False
            result["force_published"] = True
            self._flag_for_human(chapter_num, cur_score, f"force-best to draft: {result.get('note','')} high={high_list} det_hard={det_issues} soft={det_soft}")
            return result
        can_publish, _publish_verdict = self._decide_publish(
            chapter_num=chapter_num, task_card=task_card,
            synopsis=synopsis, world_state=world_state,
            result=result, score=score, sm=sm,
            review=review, final_text=final_text,
            violations=violations, det_issues=det_issues,
            det_soft=det_soft, high_list=high_list,
            apply_world_state=apply_world_state,
            cur_score=cur_score, _commit_policy=_commit_policy,
        )

        if has_high_issue and not can_publish:
            logger.warning(f"Chapter {chapter_num} has high/block issue → force non-publish (score={cur_score} high={high_list})")
        if det_issues and not can_publish:
            logger.warning(f"Chapter {chapter_num} deterministic hard gate failed → force non-publish (score={cur_score} det_hard={det_issues} det_soft={det_soft})")
        elif det_soft:
            logger.info(f"Chapter {chapter_num} deterministic soft issues (不阻断): {det_soft}")
        if violations and any(_forbidden_violation_is_blocking(_commit_policy, v) for v in violations) and not can_publish:
            logger.warning(f"Chapter {chapter_num} forbidden policy-hard → force non-publish: {violations}")

        if not can_publish:
            # --- 1a) 分档发布机制 ---
            # ≥88 直接发布 novel/（此处已被 force_best 或硬阻断拦截，不应到达此分支）
            # 82-87 → 人工复核队列：落 draft 且 production_report 标注 pending_human_review，不再叫 force-best
            # <82  → 强制 draft（原 force-best 流程）
            if cur_score >= 88:
                # 高分但维度门槛未过：保留好章落 draft 供人工，不再 reject 空跑
                try:
                    draft_path = self.root / "chapters" / "draft" / f"chapter_{chapter_num}.txt"
                    draft_path.parent.mkdir(parents=True, exist_ok=True)
                    draft_path.write_text(self._novel_string(), encoding="utf-8")
                    logger.warning(
                        f"Chapter {chapter_num} score={cur_score}>=88 dim-gate blocked → draft pending_human_review"
                    )
                except Exception as e:
                    logger.error(f"Failed to save high-score draft for chapter {chapter_num}: {e}")
                result["success"] = False
                result["published"] = False
                result["score"] = cur_score
                result["pending_human_review"] = True
                result["human_review_note"] = (f"score={cur_score}>=88 dim-gate blocked (high={high_list} "\
                                       f"det_hard={det_issues} det_soft={det_soft}), saved draft pending human review")
                return result
            elif 82 <= cur_score < 88:
                # 82-87：人工复核队列 - 落 draft 并标记，early return 不进入 novel/ 提交
                draft_path = self.root / "chapters" / "draft" / f"chapter_{chapter_num}.txt"
                draft_path.parent.mkdir(parents=True, exist_ok=True)
                draft_path.write_text(self._novel_string(), encoding="utf-8")
                logger.warning(
                    f"Chapter {chapter_num} 82-87 人工复核队列（ score={cur_score}  → draft pending_human_review )"
                )
                result["success"] = False
                result["published"] = False
                result["score"] = cur_score
                result["pending_human_review"] = True  # 新增标记
                result["human_review_note"] = f"score={cur_score} in 82-87 range, queued for human review, not force-best"
                # ch267 归因：gray-band 授权被最终 det 硬项否决时，必须带归因进 note+errors，
                # 禁止静默降级（之前 85.9 分无归因地 FAILED，日志查不出原因）
                _gbd = result.get("gray_band_det_block")
                if _gbd:
                    result["human_review_note"] += f"; gray-band 授权被最终 det 硬项否决: {_gbd}"
                    result["errors"].append(f"gray-band authorized but final det hard: {_gbd}")
                elif _publish_verdict and _publish_verdict.get("reasons"):
                    result["human_review_note"] += f"; 否决原因: {_publish_verdict['reasons'][:3]}"
                # early return，避免进入下面成功后的 world_state 提交流程
                return result
            else:
                # <82：强制 draft（原逻辑）
                try:
                    draft_path = self.root / "chapters" / "draft" / f"chapter_{chapter_num}.txt"
                    draft_path.parent.mkdir(parents=True, exist_ok=True)
                    draft_path.write_text(self._novel_string(), encoding="utf-8")
                    logger.warning(
                        f"Chapter {chapter_num} 强制 draft（ score={cur_score} < 82 )"
                    )
                except Exception as e:
                    logger.error(f"Failed to save draft for chapter {chapter_num}: {e}")
                result["success"] = False
                result["published"] = False
                result["score"] = cur_score
                self._flag_for_human(chapter_num, cur_score, f"below 82: forced draft, score={cur_score}")
                return result

        self._commit_chapter(
            chapter_num=chapter_num, task_card=task_card,
            synopsis=synopsis, world_state=world_state,
            result=result, score=cur_score, sm=sm, apply_world_state=apply_world_state,
        )


        # Accumulate cost data for this chapter
        # Accumulate cost data for this chapter
        from novel_engine.core.call_metrics import snapshot as _snapshot_metrics
        self.cost_tracker.update(_snapshot_metrics())

        return result


    def _commit_chapter(self, chapter_num, task_card, synopsis, world_state,
                        result, score, sm, apply_world_state=False):
        """Submit chapter: checkpoint, events, end_state, session_tree."""
        try:
            # 提取关键词用于索引
            keywords = self._extract_keywords(task_card, synopsis)
            self.memory.update_chapter_index(chapter_num, keywords)
            self.memory.add_recent_chapter(chapter_num, {
                "goal": task_card.get("core_goal", ""),
                "summary": synopsis.get("synopsis", ""),
                "word_count": len(self._novel_string()),
            })

            # 正式提交状态变更（已通过发布门，安全落库）
            pending_changes = synopsis.get("state_changes", [])
            source = getattr(self, "_current_synopsis_source", "director")
            if pending_changes:
                if apply_world_state:
                    self.simulator.apply_pending_changes(pending_changes, chapter=chapter_num, source=source)
                    self.memory.commit_pending_changes(pending_changes)
                else:
                    # Deferred path: record fact_changes with status=deferred
                    for _i, _chg in enumerate(pending_changes):
                        try:
                            record_change(
                                self.root,
                                chapter=chapter_num,
                                change_type=_chg.get("type", "unknown"),
                                target=str(_chg.get("target", "")),
                                old_value=_chg.get("old_value"),
                                new_value=_chg.get("new_value"),
                                source=source,
                                gate_result="deferred",
                                status="deferred",
                            )
                        except Exception as _fc_defer:
                            logger.warning(f"fact_changes deferred write failed (non-fatal): {_fc_defer}")
                    logger.warning(
                        f"Chapter {chapter_num} 世界状态变更已暂缓"
                        f"(score={result.get('score')} < min_ch)，未应用到世界模拟器，留待人工复核/返工。"
                    )
                    self._flag_for_human(chapter_num, result.get("score", 0), "world-state deferred: below min_ch")

            checkpoint = sm.commit_chapter(
                chapter_num=chapter_num,
                novel_content=self._novel_string(),
                synopsis_content=json.dumps(synopsis, ensure_ascii=False),
                outline_content=json.dumps(task_card, ensure_ascii=False),
                world_state_snapshot=world_state,
            )
            # Mark chapter as committed in status tracking
            set_status(self.root, chapter_num, COMMITTED, score=score)
            # CC P0：仅在原子提交成功后追加跨章事件台账（HALT/隔离章不写，失败不回滚已提交章节）。
            try:
                _n_ev = append_chapter_events(self.root, chapter_num, task_card)
                logger.info(f"Chapter {chapter_num}: appended {_n_ev} event-ledger entries")
            except Exception as _le:
                logger.error(f"event ledger append failed for chapter {chapter_num}: {_le}")
            # CC round-7 P0-3：持久化章末精确停点，供下一章 director 开场锚点
            try:
                append_end_state(self.root, chapter_num, task_card)
            except Exception as _ee:
                logger.warning(f"end_state append failed (non-fatal) for ch{chapter_num}: {_ee}")

            # Save commit snapshot in SessionTree
            try:
                import hashlib
                content_bytes = (self._novel_string() + json.dumps(synopsis) + json.dumps(task_card)).encode("utf-8")
                content_hash = hashlib.sha256(content_bytes).hexdigest()[:16]
                post_world_state = {
                    "characters": self.simulator.characters,
                    "factions": self.simulator.factions,
                    "power_system": self.simulator.power_system,
                }
                self.session_tree.add_commit(
                    chapter_num=chapter_num,
                    content_hash=content_hash,
                    world_state_snapshot=post_world_state,
                    score=result.get("score", score),
                    branch_name="main"
                )
                self._save_session_tree()
                logger.info(f"Chapter {chapter_num} node committed to SessionTree.")
            except Exception as e:
                logger.error(f"Failed to commit chapter to SessionTree: {e}")

            # success 标记已在质检阶段如实写入，此处不再覆盖，避免掩盖弱章
            logger.info(f"Chapter {chapter_num} COMMITTED (success={result.get('success')})")
            # P0 冻结目标：成功发布后清理冻结缓存，下一章重新生成
            self._frozen_task_cards.pop(chapter_num, None)
            self._frozen_synopsis.pop(chapter_num, None)
        except Exception as e:
            result["errors"].append(f"COMMIT: {e}")
            sm.handle_failure("commit_error", str(e))
            # Mark chapter as HALTED on commit failure
            set_status(self.root, chapter_num, HALTED, reason="commit_error")
            return result


    def _decide_publish(self, chapter_num, task_card, synopsis, world_state,
                        result, score, sm, review, final_text, violations,
                        det_issues, det_soft, high_list, apply_world_state,
                        cur_score, _commit_policy):
        """Decide whether chapter can be published.
        
        Returns (can_publish, verdict_dict).
        """
        force_best = bool(getattr(self, "_force_publish_best", False))
        if force_best and result.get("gray_band_release"):
            # CC ch62 不变量（与 generate_single_chapter L1174 同源双保险）：
            # gray-band 已授权发布 novel/，force-best 不得再降级为 draft。
            logger.warning(
                f"ch{chapter_num} _decide_publish: gray_band_release={result.get('gray_band_score')} — "
                f"force-best short-circuited (novel publish path wins)")
            self._force_publish_best = False
            force_best = False
        from novel_engine.pipeline.quality_gate import evaluate_publish
        leak_issues = getattr(self, "_last_leak_issues", [])
        if force_best and not leak_issues:
            logger.warning(f"Force-best to draft {cur_score}")
            self._force_publish_best = False
            try:
                draft_path = self.root / "chapters" / "draft" / f"chapter_{chapter_num}.txt"
                draft_path.parent.mkdir(parents=True, exist_ok=True)
                draft_path.write_text(self._novel_string(), encoding="utf-8")
            except Exception as e:
                logger.error(f"Failed to save force-best draft: {e}")
            result["score"] = cur_score
            result["published"] = False
            result["force_published"] = True
            return False, {"publish": False}
        elif force_best:
            logger.warning(f"Force publish blocked by leak")

        _gray_commit = bool(result.get("gray_band_release")) and cur_score + 1e-9 >= int(
            _commit_policy.get("soft_publication_line", int(_commit_policy["publication_line"]) - 3))
        has_high = any(b for b, _ in (_review_issue_is_blocking(_commit_policy, iss) for iss in (review.get("issues") or [])))
        if _gray_commit and not leak_issues and not has_high:
            if det_issues:
                # ch267 归因：gray-band 授权（fix loop 检测快照全绿）后，最终门对净化/修复后
                # 文本重测出现新 det 硬项——授权失效是合理否决，但必须带归因，禁止静默
                # 降级到 82-87 队列（无归因 FAILED，日志查不出为什么）。
                result["gray_band_det_block"] = list(det_issues)
                verdict = evaluate_publish(score=cur_score, reviewer_issues=review.get("issues", []),
                                           det_hard=det_issues, leak=leak_issues, violations=violations,
                                           policy=_commit_policy, dim_scores=review.get("dim_scores"), total_score=cur_score)
                can_publish = verdict["publish"]
            else:
                can_publish = True
                verdict = {"publish": True, "reasons": [],
                           "note": f"gray-band release {cur_score}"}
        else:
            verdict = evaluate_publish(score=cur_score, reviewer_issues=review.get("issues", []),
                                       det_hard=det_issues, leak=leak_issues, violations=violations,
                                       policy=_commit_policy, dim_scores=review.get("dim_scores"), total_score=cur_score)
            can_publish = verdict["publish"]

        if (not can_publish) and (not bool((self.config.get("llm") or {}).get("use_mock"))):
            try:
                _block_forbidden28 = bool(violations) and any(
                    _forbidden_violation_is_blocking(_commit_policy, _v) for _v in violations)
                _nonscore_reasons28 = [_r for _r in (verdict.get("reasons", []) or [])
                                       if not (isinstance(_r, str) and _r.startswith("score<"))]
                if (not leak_issues) and (not det_issues) and not has_high \
                        and (not _block_forbidden28) and (not _nonscore_reasons28):
                    from novel_engine.quality import publish_router as _pr28
                    _tier28 = _pr28.decide_publish_tier(cur_score, True, chapter_num)
                    if _tier28.get("auto_submit"):
                        can_publish = True
                        result["auto_submit_tier"] = _tier28["tier"]
                        result["sample_audit_flag"] = bool(_tier28.get("needs_manual_audit"))
            except Exception as _e28:
                logger.warning(f"publish_router override skipped: {_e28}")

        return can_publish, verdict

    # ====== Stage Methods (extracted from generate_single_chapter) ======

    def _stage_world_sim(self, chapter_num: int) -> dict:
        """阶段1：世界模拟器预计算。"""
        self.state_machine.transition(ChapterPhase.WORLD_SIM)
        world_state = self.simulator.build_world_state_for_chapter(chapter_num)
        self.current_world_state = world_state
        logger.info(f"World simulation done for chapter {chapter_num}")
        return world_state

    def _stage_directing(self, chapter_num: int, world_state: dict,
                         bypass_cache: bool | None = None) -> dict:
        """阶段2：章节导演生成任务卡（校验失败自动重试）。

        bypass_cache=True（或环境变量 NOVEL_DIRECTOR_BYPASS_CACHE=1，质量类重规划时由
        runner 设置）时跳过 director 磁盘缓存、带最新 end_state/timeline 约束重新规划；
        infra 类失败重试仍正常读缓存。
        """
        self.state_machine.transition(ChapterPhase.DIRECTING)
        max_attempts = 3
        last_errors: list = []
        # CC round-7 Q6：质量 HALT 重试绕过 director 缓存换规划；infra 重试仍走缓存。
        _bypass = (
            bool(bypass_cache)
            or os.environ.get("NOVEL_DIRECTOR_BYPASS_CACHE") == "1"
            or os.environ.get("NOVEL_DIRECTOR_BYPASS_CACHE_CHAPTER") == str(chapter_num)
        )
        if _bypass:
            logger.info(f"Director cache BYPASS for chapter {chapter_num} (quality replan)")
        # CC P0: director disk cache 仅在首轮(无 feedback)读取；命中也必须通过校验才采用。
        _director_cache_hit = False
        _director_cache_key = None

        def _finalize_blueprints(card: dict) -> dict:
            """结构合法卡的统一收尾：薄场合并 + beat 规范化 + 技法元素轮换。"""
            _bps0 = card.get("scene_blueprints") or []
            _bps1 = merge_thin_scene_blueprints(_bps0, self._global_target)
            if len(_bps1) != len(_bps0):
                logger.info(f"Chapter {chapter_num}: merged thin scene blueprints "
                            f"{len(_bps0)} -> {len(_bps1)}")
            card["scene_blueprints"] = _bps1
            _normalize_blueprint_beats(card)
            # CC round-26h：infant 章节大纲洗词——导演生成的 beat/core_goal
            # 若含修炼体系词（吐纳/口诀/呼吸法等），正文必然越界（scope/timeline gate
            # 对婴儿篇禁写），统一替换为中性词，从源头切断每轮重写撞词。
            try:
                _agency = str(card.get("protagonist_agency_level") or "")
                if _agency.startswith("infant"):
                    _card_scrub_infant_words(card)
            except Exception:
                logger.warning(f"ch{chapter_num} infant beat scrub skipped", exc_info=True)
            try:
                from novel_engine.agents.craft_elements import ensure_scene_craft_elements
                ensure_scene_craft_elements(card)
            except Exception:
                pass
            return card

        def _card_scrub_infant_words(card: dict) -> None:
            """把 infant 章节大纲中的修炼体系提示词替换为中性词，避免诱发正文越界。"""
            _wm = {"吐纳": "吐息", "口诀": "那套步骤", "呼吸法": "呼吸节奏",
                    "调息": "平稳呼吸", "功法": "那套动作", "法门": "那套做法",
                    "修炼": "练习", "行气": "呼吸", "内功": "那套功夫"}
            def _sc(t: str) -> str:
                for _k, _v in _wm.items():
                    t = t.replace(_k, _v)
                return t
            for _k in ("core_goal", "chapter_hook"):
                if isinstance(card.get(_k), str):
                    card[_k] = _sc(card[_k])
            for _bp in card.get("scene_blueprints") or []:
                for _k2, _v2 in list(_bp.items()):
                    if isinstance(_v2, str):
                        _bp[_k2] = _sc(_v2)
                    elif isinstance(_v2, list):
                        _bp[_k2] = [_sc(x) if isinstance(x, str) else x for x in _v2]
                    elif isinstance(_v2, dict):
                        _bp[_k2] = {_kk: (_sc(_vv) if isinstance(_vv, str) else _vv)
                                    for _kk, _vv in _v2.items()}
            for _k3 in ("must_cover_beats", "chapter_events", "foreshadow_actions",
                        "foreshadow_execution", "state_changes"):
                if isinstance(card.get(_k3), list):
                    card[_k3] = [_sc(x) if isinstance(x, str) else x for x in card[_k3]]

        def _is_volume_opening(ch: int) -> bool:
            try:
                _vols = (self._director_fixed_context or {}).get("volumes", {})
                _lst = _vols.get("volumes", _vols) if isinstance(_vols, dict) else _vols
                for _v in (_lst or []):
                    _rng = _v.get("chapter_range") or _v.get("range") or []
                    if len(_rng) >= 2 and int(_rng[0]) == int(ch):
                        return True
            except Exception:
                return False
            return False

        def _apply_opening_density(card: dict) -> dict:
            """CC29：真机任务卡开场密度门（mock/离线不拦截）。STRICT 硬违规回灌重生≤2次，
            仍不合格 fail-open（注入 writer 硬约束+打标，不 HALT）；SOFT 仅记录信号/注入。"""
            if bool((self.config.get("llm") or {}).get("use_mock")):
                return card
            try:
                from novel_engine.quality import opening_density as _od
                _vol_open = _is_volume_opening(chapter_num)
                _cur = card
                for _datt in range(_od.DENSITY_REGEN_BUDGET + 1):
                    _assess = _od.assess_opening_density(
                        _cur.get("scene_blueprints") or [], chapter_num, _vol_open)
                    _action = _od.decide_density_action(
                        _assess["level"], bool(_assess["hard_violations"]), _datt)
                    if _action == "clean":
                        if _assess["soft_signals"]:
                            _cur["density_soft_signals"] = _assess["soft_signals"]
                            _cur["_density_scene_constraints"] = _assess["scene_constraints"]
                        return _cur
                    if _action == "regenerate":
                        _fb = ["开场事件密度零LLM硬校验未通过（规划阶段，不让LLM自评），请逐条修正后重出完整任务卡："]
                        for _v in _assess["hard_violations"]:
                            if _v["type"] == "consecutive_interior_only":
                                _fb.append(f"- 场景{_v['scenes']} 连续多场无 external_event（纯内省连场），"
                                           "除至多1个过渡场外每场都要规划可被旁人观察到的外部事件")
                            elif _v["type"] == "duplicate_anchor_no_progression":
                                _fb.append(f"- 相邻场景{_v['scenes']} 地点/时间/人物锚点相同且后场无 "
                                           "irreversible_state_delta，属同一现场重演，必须推进时间或地点并落下不可逆变化")
                            elif _v["type"] == "no_world_anomaly_in_opening":
                                _fb.append("- 第1章前2场至少1场必须给出非空 world_anomaly_signal：在场普通村民"
                                           "也能看到的外部物理异象，只描述现象、不解释成因、不用修炼术语")
                        logger.warning(f"ch{chapter_num} opening-density hard violations "
                                       f"(attempt {_datt + 1}/{_od.DENSITY_REGEN_BUDGET}): "
                                       f"{_assess['hard_violations']} -> regenerate blueprint")
                        _regen = self.director.generate_task_card_cached(
                            chapter_num, self._director_fixed_context, feedback=_fb)
                        if self.director.validate_task_card(_regen, chapter_num):
                            logger.error("opening-density regen produced structurally invalid card; "
                                         "keep previous candidate and fail-open")
                            _action = "fail_open"
                        else:
                            _cur = _finalize_blueprints(_regen)
                            continue
                    # fail_open
                    _cur["density_blueprint_fail_open"] = True
                    _cur["density_soft_signals"] = _assess["soft_signals"]
                    _cur["_density_scene_constraints"] = _assess["scene_constraints"]
                    logger.error(f"ch{chapter_num} opening-density fail-open after {_datt + 1} tries; "
                                 f"constraints injected to writer, hard={_assess['hard_violations']}")
                    return _cur
                return _cur
            except Exception as _ode:
                logger.warning(f"opening density gate skipped: {_ode}")
                return card

        for attempt in range(max_attempts):
            task_card = None
            if attempt == 0 and not last_errors and not _bypass:
                try:
                    _dmodel = model_fingerprint(self.root, "director")
                    _director_cache_key = content_key(
                        {"chapter": chapter_num,
                         "fixed_context": self._director_fixed_context,
                         "recent_events": recent_event_block(self.root, chapter_num),
                         "end_anchor": end_state_anchor_block(self.root, chapter_num)},
                        version=PROMPT_VERSIONS["director"], model=_dmodel)
                    task_card = self._phase_cache.get(chapter_num, "director", _director_cache_key)
                    if task_card is not None:
                        _director_cache_hit = True
                        logger.info(f"Director cache hit for chapter {chapter_num}")
                except Exception as _ce:
                    logger.warning(f"director cache read skipped: {_ce}")
                    task_card = None
            if task_card is None:
                task_card = self.director.generate_task_card_cached(
                    chapter_num, self._director_fixed_context, feedback=(last_errors or None)
                )
            errors = self.director.validate_task_card(task_card, chapter_num)
            if not errors:
                # T4: 大纲任务覆盖校验（mock/离线直接放行）
                # R1: 正确取得上一章事件块（复用 recent_event_block）
                _prior_events_block = ""
                if not bool((self.config.get("llm") or {}).get("use_mock")):
                    try:
                        _prior_events_block = recent_event_block(self.root, chapter_num) or ""
                    except Exception as _pe:
                        logger.warning("recent_event_block failed: %s", _pe)
                    try:
                        _ctx = self.director._build_shared_context(chapter_num)[0]
                        _ch_task = _ctx.get("chapter_outline_task") or ""
                        if _ch_task:
                            # validate_outline_coverage 是纯函数，独立 try 不吞 NameError
                            _cov_passed, _cov_issues = validate_outline_coverage(
                                task_card, _ch_task)
                            if not _cov_passed:
                                _regen_fb = (
                                    "[大纲任务未覆盖] 第%d章大纲核心任务：%s。"
                                    "当前蓝图缺失要素：%s。"
                                    "上一章已写内容（禁止重演）：%s。"
                                    "请重新规划蓝图，确保本章大纲任务关键要素被承载。"
                                    % (chapter_num, _ch_task,
                                       "；".join(_cov_issues[:3]),
                                       _prior_events_block[:300])
                                )
                                last_errors = [_regen_fb]
                                logger.warning(
                                    "ch%d outline coverage fail: %s",
                                    chapter_num, _cov_issues[:2])
                                continue
                    except Exception as _oce:
                        # R1: 仅 coverage 计算失败时 skip；变量/接线错误不吞
                        logger.error("outline coverage check error: %s", _oce)
                task_card = _finalize_blueprints(task_card)
                # CC29：真机开场密度门（STRUCT 合法后；mock/离线直接放行）
                task_card = _apply_opening_density(task_card)
                # CC round-18 R17-2：写入期对齐 — density 之后、phase_cache.set 之前，
                # 对最终卡重算 extract_must_cover_beats，使显式 must_cover_beats 与
                # foreshadow_actions 对齐（density 可能触发重生，改变卡内容）。
                # 读时纠正（stage6 ~1107 行）已在；此处补写入时对齐，双重保险。
                # 异常不静默：fail-closed，记 error 日志但不中断任务卡生成。
                try:
                    from novel_engine.quality.outline_coverage_gate import extract_must_cover_beats as _emb_r17
                    _r17_reconciled = _emb_r17(task_card)
                    if _r17_reconciled:
                        task_card["must_cover_beats"] = _r17_reconciled
                        logger.info(f"ch{chapter_num} R17-2 write-time must_cover_beats aligned ({len(_r17_reconciled)} beats)")
                    else:
                        task_card.pop("must_cover_beats", None)
                except Exception as _re17:
                    logger.error(f"ch{chapter_num} R17-2 write-time alignment FATAL: {_re17}")
                self.current_outline = task_card
                if attempt == 0 and not _director_cache_hit and _director_cache_key:
                    try:
                        self._phase_cache.set(chapter_num, "director", _director_cache_key, task_card)
                    except Exception:
                        pass
                logger.info(f"Task card generated for chapter {chapter_num}")
                return task_card
            last_errors = errors
            logger.warning(f"Task card validation failed (attempt {attempt + 1}/{max_attempts}): {errors}")
        raise ValueError(f"Task card validation failed: {last_errors}")

    def _stage_synopsis(self, task_card: dict) -> dict:
        """阶段3：缩写生成。合并模式下直接复用导演任务卡中的 synopsis，跳过独立 LLM 调用。"""
        self.state_machine.transition(ChapterPhase.SYNOPSIS)
        merge = self.config.get("pipeline", {}).get("merge_synopsis_into_directing", False)
        if merge:
            raw = task_card.get("synopsis")
            if isinstance(raw, str) and len(raw.strip()) >= 50:
                synopsis = {
                    "chapter_num": task_card.get("chapter_num", 0),
                    "synopsis": raw.strip(),
                    "state_changes": task_card.get("state_changes", []) or [],
                    "foreshadow_execution": task_card.get("foreshadow_execution", []) or [],
                }
                logger.info(f"Synopsis (merged from task card) for chapter {task_card.get('chapter_num', 0)}")
            else:
                synopsis = self.synopsis_agent.build_synopsis_from_task_card(task_card)
                logger.info(f"Synopsis (deterministic fallback) for chapter {task_card.get('chapter_num', 0)}")
        else:
            synopsis = self.synopsis_agent.generate_synopsis(task_card)
        self.current_synopsis = synopsis
        for change in synopsis.get("state_changes", []):
            self.memory.add_pending_change(change, chapter=task_card.get("chapter_num", 0))
        source = "task_card" if (merge and isinstance(raw, str) and len(raw.strip()) >= 50) else ("deterministic_fallback" if merge else "agent")
        self._current_synopsis_source = source
        logger.info(f"Synopsis ready for chapter {task_card.get('chapter_num', 0)} (source={source})")
        return synopsis

    @staticmethod
    def _bpt(bp: dict) -> int:
        """防御 word_count_target 偶发为 list（LLM 畸形输出）。"""
        v = bp.get("word_count_target", 0)
        if isinstance(v, list):
            v = v[0] if v else 0
        try:
            return int(v or 0)
        except Exception:
            return 0

    @staticmethod
    def _structure_fingerprint(text: str, tail: int = 400) -> set:
        """跨章结尾结构指纹：提取结尾段场景母题，用于检测章节收尾同构。"""
        tail_text = text[-tail:]
        marks = ["夜", "灯", "静", "沉默", "看", "睡", "风", "光", "黑暗", "影子", "呼吸"]
        return {m for m in marks if m in tail_text}

    def _resolved_foreshadow_ids(self, chapter_num: int) -> set[str]:
        """已回收伏笔 id 集合（StateDB status='resolved'），供远期回收校验用。

        chapter_num 保留参数位以便将来按章收敛；当前返回全部 resolved 集合。
        """
        try:
            return set(self.db.resolved_foreshadow_ids())
        except Exception as _rf:
            logger.warning(f"ch{chapter_num} resolved foreshadow query failed: {_rf}")
            return set()

    def _deterministic_quality_gate(self, text: str, task_card: dict, scene_texts: dict | None = None) -> dict:
        """确定性校验（重复/截断硬；长度仅记soft）。

        scene_texts：权威最终分场（含※分隔符的原始 assembled 文本），供危机门按场评估。
        若为 None，危机门回退到 heuristic 切分。
        """
        purified = purify_novel_for_publish(text, chapter_num=task_card.get("chapter_num"))
        # 若净化前后长度差异过大，说明脚手架污染严重，也视为问题
        issues: list[str] = []
        soft_issues: list[str] = []
        rep = detect_repetition(purified)
        if rep["has_repetition"]:
            issues.extend([f"[重复] {x}" for x in rep["issues"]])
        trunc = detect_truncation(purified)
        if trunc["is_truncated"]:
            issues.extend([f"[截断] {x}" for x in trunc["issues"]])
        # 长度：对照 scene_blueprints 目标
        blueprints = task_card.get("scene_blueprints") or []
        target = sum(self._bpt(bp) for bp in blueprints) or None
        length = detect_length_anomaly(purified, target)
        if length["anomaly"]:
            soft_issues.extend([f"[长度] {x}" for x in length["issues"]])
        # 脚手架残留二次校验（净化后不应再含这些 token）→ 硬
        if any(tok in purified for tok in ["【场景", "※", "（章末钩子", "（注："]):
            issues.append("[净化] 成品仍含脚手架标记")
        # P2-C4 套话黑名单 → 软（仅预警，不阻断发布，-reported in soft）
        cliches = ["死水石子", "未出鞘", "达摩克利斯", "如野草疯长", "石头砸", "深井", "古井", "悬停", "一潭", "枯木"]
        for c in cliches:
            if purified.count(c) >= 2:  # 至少出现2次才视为堆砌
                soft_issues.append(f"[套话-软] 命中黑名单“{c}”≥2次")
                break
            elif c in purified:
                soft_issues.append(f"[套话-软] 命中“{c}”1次（预警不阻断）")
                break
        # D1 时间线穿帮：婴儿期出现“十年”级表述 → 软（需结合主语，宽松）
        ch_num = int(task_card.get("chapter_num", 0) or 0)
        if ch_num and ch_num <= 10:
            # 仅当“陆烬/主角 + 十年”同句出现才判硬，否则软
            if any(x in purified for x in ["陆烬在此十年", "陆烬十年", "婴儿.*十年"]):
                issues.append("[时间线] 婴儿期主角出现“十年”级表述")
            elif any(x in purified for x in ["十年", "十年后"]):
                soft_issues.append("[时间线-软] 出现“十年”表述（历史背景可能，预警）")
        # 伏笔台账超期提醒（CC P0#3，soft）+ 设定贯通远期回收校验（hard）
        try:
            _fs_db = self.db.query_active_foreshadows(int(ch_num or 0))
            for _fs in _fs_db:
                _rc = _fs.get("resolve_chapter")
                if _rc and int(_rc) < int(ch_num or 0):
                    soft_issues.append("[伏笔-超期] " + str(_fs.get("id")) + " 计划" + str(_rc) + "章回收仍open")
        except Exception:
            pass
        # 设定贯通：registry 全量扫描（不依赖 query_active 的 clue_plan 命中），
        # importance>=0.7 且 resolve_chapter 已过且未 resolved → 硬拦截（远期锚点）。
        try:
            from novel_engine.quality.outline_coverage_gate import validate_foreshadow_closure
            _reg_path = self.root / "config" / "foreshadow" / "registry.json"
            _registry = {}
            if _reg_path.exists():
                with open(_reg_path, "r", encoding="utf-8") as _freg:
                    _registry = json.load(_freg)
            _closure_ok, _closure_issues = validate_foreshadow_closure(
                _registry, int(ch_num or 0),
                resolved_ids=self._resolved_foreshadow_ids(ch_num),
            )
            for _ci in _closure_issues:
                issues.append(f"[设定] {_ci}")
        except Exception as _ce:
            logger.warning(f"foreshadow closure check skipped: {_ce}")
        # CC round-7 P0-3：跨章重演边界门未解决的硬伤在此并入（assembly 阶段判定）
        for _bh in (getattr(self, "_boundary_hard", []) or []):
            if _bh not in issues:
                issues.append(_bh)
        # CC28 批B 3b：章内相邻场重演未解决的硬伤并入
        for _rh in (getattr(self, "_reprise_hard", []) or []):
            if _rh not in issues:
                issues.append(_rh)
        # CC28 批B 3c：同章同角色多称谓裸切（叙述无同指锚点）→ 硬伤
        try:
            from novel_engine.quality.alias_consistency_gate import (
                detect_bare_alias_switch, load_alias_groups)
            _groups3c = load_alias_groups(self.root)
            _alias = detect_bare_alias_switch(purified, groups=_groups3c)
            for _ai in _alias.get("issues", []) or []:
                issues.append(
                    f"[称谓] 同一角色“{_ai['primary']}”在叙述中无锚点裸切称谓 "
                    f"{_ai['used']}（主用“{_ai['dominant']}”→{_ai['switched_to']}），"
                    "需统一主用名或补同指锚点（如“本名…转生后名…”）")
        except Exception as _ae:
            logger.warning(f"alias consistency gate skipped: {_ae}")
        # CC round-18 P0-1：跨场悬空危机门（off-card crisis 未承接 + 时间/状态重置 → 硬阻断）
        _root_for_gates = getattr(self, "root", None)
        try:
            from novel_engine.quality.cross_scene_crisis_gate import detect_off_card_crisis
            _crisis = detect_off_card_crisis(purified, ch_num, task_card, root=_root_for_gates, scene_texts=scene_texts)
            _crisis_count = len(_crisis.get("off_card_crises") or [])
            if _crisis_count > 0:
                _cd = [c.get("crisis_desc", "")[:60] for c in _crisis["off_card_crises"]]
                logger.warning(
                    f"Cross-scene crisis gate ch{ch_num}: {_crisis_count} off-card crisis(es) "
                    f"hit -> hard block; examples={_cd}")
                for _ci in _cd:
                    issues.append(f"[跨场危机] off-card crisis 未承接：{_ci}")
            else:
                logger.info(f"Cross-scene crisis gate ch{ch_num}: 0 hits, pass")
        except Exception as _ce:
            # D3：fail-closed — 异常即计入 hard，不静默放行
            logger.error(f"cross_scene_crisis_gate FATAL ch{ch_num}: {_ce} -> hard block")
            issues.append(f"[跨场危机] gate异常(fail-closed): {_ce}")
        # CC round-18 P0-2：命名一致性 + 伏笔极性一致性门
        # （古井/枯井矛盾、排斥/吸引无铺垫矛盾 → 硬阻断）
        try:
            from novel_engine.quality.naming_consistency_gate import (
                detect_naming_conflict, detect_polarity_conflict)
            _naming = detect_naming_conflict(purified, ch_num, root=_root_for_gates)
            _naming_count = len(_naming.get("conflicts") or [])
            if _naming_count > 0:
                _nd = [c.get("desc", "")[:80] for c in _naming["conflicts"]]
                logger.warning(
                    f"Naming consistency gate ch{ch_num}: {_naming_count} conflict(s) hit -> hard block; "
                    f"details={_nd}")
                for _ni in _nd:
                    issues.append(f"[命名矛盾] {_ni}")
            else:
                logger.info(f"Naming consistency gate ch{ch_num}: 0 naming conflicts, pass")
            _polarity = detect_polarity_conflict(purified, ch_num, root=_root_for_gates)
            _pol_count = len(_polarity.get("conflicts") or [])
            if _pol_count > 0:
                _pd = [c.get("desc", "")[:80] for c in _polarity["conflicts"]]
                logger.warning(
                    f"Polarity conflict gate ch{ch_num}: {_pol_count} conflict(s) hit -> hard block; "
                    f"details={_pd}")
                for _pi in _pd:
                    issues.append(f"[伏笔极性矛盾] {_pi}")
            else:
                logger.info(f"Polarity conflict gate ch{ch_num}: 0 polarity conflicts, pass")
        except Exception as _ne:
            # D3：fail-closed — 异常即计入 hard，不静默放行
            logger.error(f"naming_consistency_gate FATAL ch{ch_num}: {_ne} -> hard block")
            issues.append(f"[命名/极性门] gate异常(fail-closed): {_ne}")
        # CC round-19 P9：跨场角色反应一致性门（soft，命中触发修订提示，不 hard 阻断）
        _react = None
        _react_dir = ""
        try:
            from novel_engine.quality.reaction_consistency_gate import (
                detect_reaction_inconsistency, reaction_consistency_fix_directive)
            _react = detect_reaction_inconsistency(
                purified, ch_num, task_card, root=_root_for_gates, scene_texts=scene_texts)
            _react_count = len(_react.get("hits") or [])
            _react_dir = reaction_consistency_fix_directive(_react.get("hits") or [])
            if _react_count > 0:
                _rd = [h.get("desc", "")[:80] for h in _react["hits"]]
                logger.warning(
                    f"Reaction consistency gate ch{ch_num}: {_react_count} hit(s) -> soft fix; "
                    f"details={_rd}")
                for _ri in _rd:
                    soft_issues.append(f"[反应一致性] {_ri}")
            else:
                logger.info(f"Reaction consistency gate ch{ch_num}: 0 hits, pass")
        except Exception as _re:
            logger.warning(f"reaction_consistency_gate skipped ch{ch_num}: {_re}")
        # D3：canon/阶段约束（infant.json hard_block）纳入早期确定性门，
        # 使所有候选在发布/force-best/gray 前统一受约束并能触发定点重生。
        # 无 root 时（测试 Fake 对象等）跳过，不阻断非真机路径。
        if _root_for_gates is not None:
            try:
                from novel_engine.quality.scope_gate import detect_scope_violations
                _scope_h = detect_scope_violations(
                    purified, int(ch_num or 0),
                    timeline_anchor=task_card.get("timeline_anchor"),
                    root=_root_for_gates).get("hard", [])
                if _scope_h:
                    _scope_terms = sorted({v.get("term", "") for v in _scope_h})
                    logger.warning(
                        f"Canon scope gate ch{ch_num}: {len(_scope_h)} hard hit(s) -> hard block; "
                        f"terms={_scope_terms}")
                    for _sh in _scope_h:
                        issues.append(f"[canon硬词] {_sh.get('term')}：{_sh.get('sentence', '')[:80]}")
                else:
                    logger.info(f"Canon scope gate ch{ch_num}: 0 hard hits, pass")
            except Exception as _se:
                # D3：fail-closed — 异常即计入 hard
                logger.error(f"canon scope gate FATAL ch{ch_num}: {_se} -> hard block")
                issues.append(f"[canon门] gate异常(fail-closed): {_se}")
        if soft_issues:
            logger.info(f"Deterministic soft issues (不阻断) for ch{ch_num}: {soft_issues}")
        return {"passed": not issues, "issues": issues, "soft_issues": soft_issues,
                "purified": purified,
                "reaction_hits": (_react.get("hits") if _react is not None else []),
                "reaction_directive": _react_dir}

    def _assemble_chapter_text(self, scenes) -> str:
        """B2 管道组装：只拼 scene_text，hook 字段保留在 SceneOutput 供 journal/评审引用，不追加进正文。"""
        return "\n\n".join(s.scene_text for s in scenes)

    def _journal_validated_scenes(self, chapter_num: int) -> None:
        """D1 runner-owned journaling：落盘每个验证通过的结构化场景。

        Writer 只产出内存对象，持久化归 runner 层。写失败只记日志，
        不阻断正文流（journal 是可重建的派生状态）。已落盘 id 跳过，
        使章节重试幂等（completed_scene_ids 为集合，重复行无害）。
        """
        try:
            done = authoritative_completed_scene_ids(self.root, chapter_num)  # CC19 Q2: 空记录不算完成
        except Exception:
            done = set()
        rejected_sids: list[int] = []
        for s in (getattr(self.writer, "last_scenes", None) or []):
            if s.scene_id in done:
                continue
            try:
                ok = append_scene(self.root, chapter_num,
                                  {"scene_id": s.scene_id, "scene_text": s.scene_text,
                                   "hook": s.hook, "beats": s.beats})
                # CC28：写入层语言纯度硬闸拒写（整段非中文退化输出）→ 同步从内存剔除，
                # 保持 journal 与 writer.last_scenes 一致；缺场由既有韧性/补场景流程重生。
                if ok is False:
                    rejected_sids.append(int(s.scene_id))
            except Exception as je:
                logger.warning(f"Journal append failed for chapter {chapter_num} "
                               f"scene {getattr(s, 'scene_id', '?')}: {je}")
        if rejected_sids:
            _rs = set(rejected_sids)
            try:
                self.writer.last_scenes = [
                    sc for sc in (getattr(self.writer, "last_scenes", None) or [])
                    if int(getattr(sc, "scene_id", -1)) not in _rs]
            except Exception:
                pass
            logger.error(f"Chapter {chapter_num}: pruned degenerate non-Chinese scenes "
                         f"{sorted(_rs)} from memory+journal; resilience must regenerate them")
    def _write_polish_flags(self, chapter_num: int, scenes: list, flags: list[bool]) -> None:
        """将 polished 标记写入本章 journal/state。

        格式：[{"scene": <scene_id>, "polished": <bool>}]
        原子写入（tmp + os.replace）。
        """
        import os
        from pathlib import Path
        try:
            state_path = Path(self.root) / "chapters" / "state" / f"chapter_{chapter_num}_polish.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = state_path.with_suffix(".tmp")
            data = [{"scene": int(s.scene_id), "polished": bool(f)} for s, f in zip(scenes, flags)]
            content = json.dumps(data, ensure_ascii=False, indent=2)
            tmp_path.write_text(content, encoding="utf-8")
            os.replace(str(tmp_path), str(state_path))
            logger.info(f"Wrote polish flags for chapter {chapter_num}: {sum(flags)}/{len(flags)} polished")
        except Exception as e:
            logger.warning(f"Failed to write polish flags for chapter {chapter_num}: {e}")

    def _load_journal_scenes(self, chapter_num: int) -> list:
        """D1 resume：从 journal 重建已落盘场景（坏行按 corrupt-line 规则跳过，见 chapter_journal.load_scenes）。"""
        # CC19 Q2：同一 scene_id 取最新非空记录，丢弃 len=0/过期重复，保证重建即权威
        scenes = [SceneOutput(d["scene_id"], d["scene_text"], d.get("hook", ""), d.get("beats", []))
                  for d in load_authoritative_scenes(self.root, chapter_num)]
        return scenes

    def _validate_and_regen_scenes(
        self,
        scenes: list,
        task_card: dict,
        chapter_num: int,
    ) -> None:
        """pkg2e: validate each scene, detect cross-scene overlap, regen-violating scenes up to 2x.

        Raises SceneUnrecoverableError if any scene cannot be recovered.
        Does NOT enter the 0.6 expand branch for near-empty scenes (they get regenerated).
        """
        from novel_engine.core.quality_policy import load_quality_policy, derive_scene_targets

        _wp = load_quality_policy(self.root)
        _chapter_target = int(_wp["chapter_target_chars"])
        bps = task_card.get("scene_blueprints", []) or []
        _scene_targets = derive_scene_targets(_chapter_target, len(bps)) if bps else []

        # CC round-15 L0：净化每场初稿里泄漏的脚手架字段（如裸 "C009"），重定句末。
        # 纯规则、零 LLM、不计重试；净化后的文本才参与字数/句末/beat 判定。
        try:
            from novel_engine.quality import scene_resilience as _sr15
            for _sc in scenes:
                _raw = getattr(_sc, "scene_text", "") or ""
                _clean, _leaked = _sr15.strip_leaked_tokens(_raw)
                if _leaked and _clean != _raw:
                    _sc.scene_text = _clean
                    self._chapter_gate_fired = True
                    logger.warning(
                        f"Scene {getattr(_sc, 'scene_id', '?')} ch{chapter_num}: "
                        "stripped leaked scaffolding token(s) at L0")
        except Exception as _se15:
            logger.warning(f"L0 leak-strip skipped: {_se15}")

        # Phase 1: per-scene validation (length/placeholders/degradation).
        # 近空场景判废后有界重生（最多 2 次）；仍不达标直接抛 SceneUnrecoverableError 停机，
        # 绝不放进 0.6 扩写分支去“注水”。

        def _target_for(idx, obj):
            if idx < len(_scene_targets):
                return _scene_targets[idx]
            return _chapter_target // max(1, len(scenes))

        # CC round-25 P0-1：章内救援阶梯重构。初稿近空不再立刻整章重排——先走更便宜的章内层：
        #   L1 既有单场跨温度重试（下方 _attempt 循环，0.7→0.85，1 次）
        #   L2 同任务卡单场"聚焦重写"（只带本场 beats+推进契约+技法，预算 1 次）
        #   L3 单 beat 拆分重写（每场 1 遍，逐 beat 150-300 字拼接）
        #   L4 整章重排：仅当"同批 ≥2 个场景在 L2/L3 耗尽后仍未达标"才判真·近空簇（见文末）。
        # 这里的初稿扫描只用于 P1(a) HTTP 层诊断埋点（区分采样退化 vs 连接/截断），不触发 HALT。
        for _i, _s in enumerate(scenes):
            _bp0 = next(
                (b for b in bps if int(b.get("scene_num", 0) or 0) == int(getattr(_s, "scene_id", 0) or 0)),
                None)
            _need0 = len([b for b in ((_bp0 or {}).get("beats") or []) if b])
            if getattr(_s, "structured", False) and is_near_empty(
                    getattr(_s, "scene_text", "") or "", _target_for(_i, _s)) and not _is_coherent_short(_s, _need0):
                logger.warning(
                    f"NEAR_EMPTY_HTTP ch{chapter_num} scene {getattr(_s, 'scene_id', _i + 1)} "
                    f"len={len(getattr(_s, 'scene_text', '') or '')} target={_target_for(_i, _s)} "
                    f"finish_reason={getattr(_s, 'finish_reason', '') or '?'} "
                    f"latency_ms={getattr(_s, 'latency_ms', 0)} "
                    f"http_signal={getattr(_s, 'http_signal', '') or '?'} "
                    "-> enters L1/L2/L3 in-chapter rescue (no immediate chapter resample)")

        _unrecoverable_ids: list[int] = []  # CC round-15: 真·无法自动修复场景收集
        for i, s in enumerate(scenes):
            target = _target_for(i, s)
            cur = s
            last_issues = []
            ok = False
            _degen_attempts23 = 0  # CC round-23 P0-3：连续空/摆烂稿跨温度计数
            _bp = next((b for b in bps if int(b.get("scene_num", 0) or 0) == int(getattr(s, "scene_id", 0) or 0)), None)
            _need_beats = len([b for b in ((_bp or {}).get("beats") or []) if b])
            for _attempt in range(2):
                _passed, _issues = validate_scene_text(cur.scene_text, target)
                _all_issues = list(_issues)
                # CC P0：仅对真实 strict JSON 场景强制 beats_covered 逐条覆盖（mock/纯散文回退不强制）。
                if getattr(cur, "structured", False):
                    _bok, _bissue = validate_beats_covered(getattr(cur, "beats_covered", []) or [], _need_beats)
                    if not _bok:
                        _all_issues.append(_bissue)
                # CC round-7 Q5：场景级时间线/年龄越界（先在场景层有界重生，硬门兜底）
                _tl_issue = detect_timeline_jump(
                    cur.scene_text, task_card.get("timeline_anchor"), chapter_num)
                if _tl_issue:
                    _all_issues.append(_tl_issue)
                if _passed and not _all_issues:
                    ok = True
                    break
                last_issues = _all_issues
                logger.warning(f"Scene {cur.scene_id} validation failed (attempt {_attempt+1}/2): {_all_issues}, regenerating")
                directives = self._scene_issues_to_directives(_all_issues, target, _need_beats)
                if _tl_issue:
                    self._chapter_gate_fired = True
                    directives.append(anchor_fix_directive(_tl_issue, task_card.get("timeline_anchor")))
                bad_sample = cur.scene_text.strip()[:120] if (cur.scene_text or "").strip() else None
                # CC round-7 P0-2 救援分层（取代旧的 0.95→1.10 递增温度，高温会诱发逐字重复）：
                #  - 截断(length)：默认温度，不强惩罚
                #  - 已出现自重复退化：降到 0.7 + 强 penalty（0.75/0.6），专治重复循环
                #  - 近空(<max(t*0.15,150))/占位/乱码等真退化：默认温度 + penalty 0.4
                #  - 连贯但偏短(≥近空阈值、仅字数/beat不足)：保持默认温度、不上 penalty
                #    （实测 penalty 在结构化短场景上偶发空/乱码）；两次仍短则交给有界扩写，不判死
                _truncated = (str(getattr(cur, "finish_reason", "") or "") == "length") or any(
                    str(x).startswith("truncated") for x in _all_issues)
                _repeated, _rep_sample = find_self_repetition(cur.scene_text or "")
                _near_now = is_near_empty(cur.scene_text or "", target)
                _garbled = any(str(x).startswith(
                    ("placeholder_detected", "high_freq_repeat", "continuous_same_char"))
                    for x in _all_issues)
                # CC round-8: 合法 beats 全覆盖的连贯极短不算真退化，保持默认参数、不上 penalty
                _coherent_now = _is_coherent_short(cur, _need_beats)
                _degenerate = (_near_now or _garbled) and not _coherent_now
                if _truncated:
                    _temp, _fp, _pp = None, None, None
                elif _repeated:
                    _temp, _fp, _pp = 0.7, 0.75, 0.6
                    directives.append(
                        f"上一稿陷入逐字自我重复（重复片段如“{_rep_sample[:24]}…”）。"
                        "本次必须用全新、不重复的表达一次性写足篇幅，严禁回环复读同一句段。")
                elif _degenerate:
                    # CC round-23 P0-3：0字/<下限摆烂稿跨温度快速重试（0.7→0.85，封顶0.85）
                    _temp = _sr15.degenerate_retry_temperature(_degen_attempts23)
                    _degen_attempts23 += 1
                    _fp, _pp = 0.4, 0.4
                    _cur_len23 = len(cur.scene_text or "")
                    if _cur_len23 < max(int(target * 0.15), 150):
                        directives.append(_sr15.empty_scene_retry_directive(_cur_len23, _need_beats))
                else:
                    _temp, _fp, _pp = None, None, None
                cur = self._regen_scene_for_id(
                    task_card, cur.scene_id, scenes,
                    negative_examples=[bad_sample] if bad_sample else None,
                    temperature_override=_temp,
                    frequency_penalty=_fp, presence_penalty=_pp,
                    fix_directive=("\n".join(f"- {d}" for d in directives) if directives else None),
                )
            # CC round-21 P1：结构与硬伤都合格、但初稿系统性偏短（< target*0.7）时，源头再
            # 定向重生一次（每场景最多 +1 次调用），不把上千字缺口甩给有界扩写；新稿不更长
            # 则保留旧稿，下游扩写/长度门兜底。
            _len_floor21 = int(target * 0.7)
            if ok and getattr(cur, 'structured', False) and len(cur.scene_text or '') < _len_floor21:
                try:
                    _ld21 = (
                        f"上一稿仅 {len(cur.scene_text or '')} 字，远低于本场景 {target} 字硬性下限"
                        f"（至少 {_len_floor21} 字）。必须把每个 beat 用动作、对话、感官细节与即时心理"
                        "逐拍演足，一次性写满篇幅；严禁概述、跳步、一笔带过或提前收尾。"
                    )
                    _ns21 = self._regen_scene_for_id(
                        task_card, cur.scene_id, scenes,
                        negative_examples=[(cur.scene_text or '')[:120]],
                        fix_directive=_ld21)
                    if len(_ns21.scene_text or '') > len(cur.scene_text or ''):
                        cur = _ns21
                        logger.warning(
                            f"Scene {s.scene_id} CC21 under-target regen -> "
                            f"{len(cur.scene_text or '')} chars (floor {_len_floor21}, target {target})")
                except Exception as _e21:
                    logger.warning(f"CC21 under-target regen failed scene {s.scene_id}: {_e21}")
            if not ok:
                # 两次重生后仍只是“连贯短文”（达到近空阈值，且无截断/占位/重复/乱码/时间线等
                # 硬伤，仅字数或 beats 覆盖不足）：不在此判死，交给 Phase2 有界扩写补细节，
                # 避免一个 300~800 字的正常短场景拖垮整章。真退化（近空/乱码）仍判废。
                _hard_left = [
                    x for x in last_issues
                    if not str(x).startswith(("length_too_short", "beats_covered_insufficient"))
                ]
                _coherent_floor = max(int(target * 0.15), 150)
                # CC round-10 Q3：只剩字数/beat 不足、且无截断/乱码/占位/重复等硬伤时，
                # 连贯短稿下限放宽到约 target*0.06（≥120字），交有界扩写救回，避免 197 字这类
                # 完整但短的场景被直接判死 HALT；真正近空(<120)/乱码仍在此判废。
                _soft_coherent_floor = max(int(target * 0.06), 120)
                _coherent_final = _is_coherent_short(cur, _need_beats)
                if (not _hard_left and (_coherent_final or
                        len(cur.scene_text or "") >= _coherent_floor or
                        len(cur.scene_text or "") >= _soft_coherent_floor)):
                    logger.warning(
                        f"Scene {s.scene_id} coherently short ({len(cur.scene_text or '')} chars, "
                        f"beats_ok={_coherent_final}) after 2 regens, no structural defect "
                        f"→ defer to bounded expansion")
                    scenes[i] = cur
                    continue
                # CC round-15：用韧性分级判定“真·无法自动修复”。有句末/演到beat的短稿（L3）
                # 即使字数<80 也不再判死（有界扩写负责补长）；只有 HARD_DEGENERATE / MALFORMED_SHORT
                # （无句末或几乎空）才计为无法修复。
                _bp_b = next((b for b in bps if int(b.get("scene_num", 0) or 0) == int(getattr(s, "scene_id", 0) or 0)), None)
                _beats15 = [b for b in ((_bp_b or {}).get("beats") or []) if b]
                try:
                    _cls15 = _sr15.classify_scene_output(
                        cur.scene_text or "", str(getattr(cur, "finish_reason", "") or ""),
                        target, _beats15)
                except Exception:
                    _cls15 = "MALFORMED_SHORT"
                if _cls15 in ("DEGENERATE_SHORT", "UNDER_COVERED", "USABLE"):
                    logger.warning(
                        f"Scene {s.scene_id} class={_cls15} ({len(cur.scene_text or '')} chars) "
                        f"after 2 regens → salvage to bounded expansion/density gate, not HALT")
                    scenes[i] = cur
                    continue
                # CC round-7 语义保留：mock/纯散文回退（非结构化）仍按单场有界重生处理，
                # 救不活即抛 SceneUnrecoverableError，不进入 CC15 结构化场景的章级聚合。
                if not getattr(cur, "structured", False):
                    raise SceneUnrecoverableError(
                        f"Scene {s.scene_id} near-empty/invalid after 2 regenerations: {last_issues}",
                        chapter=chapter_num, scene_id=s.scene_id,
                    )
                # CC round-25 P0-1：结构化真退化（HARD/MALFORMED/截断或仍<300）先走章内 L2/L3 便宜抢救，
                # 救回即采用；两级耗尽仍不达标才计入 _unrecoverable_ids（文末 ≥2 才 L4 整章重排）。
                _rescued25 = self._cc25_rescue_scene_l2l3(task_card, cur, _bp_b, target, _need_beats)
                if _rescued25 is not None:
                    scenes[i] = _rescued25
                    continue
                _unrecoverable_ids.append(int(getattr(s, "scene_id", i + 1) or (i + 1)))
                scenes[i] = cur
                logger.error(
                    f"Scene {s.scene_id} UNRECOVERABLE class={_cls15} "
                    f"({len(cur.scene_text or '')} chars): {last_issues} → mark known-defect, "
                    "chapter kept alive (HALT only if >=2 such scenes)")
            scenes[i] = cur

        # CC round-25 P0-1 Level-4（取代旧的章级近空熔断）：这里的 _unrecoverable_ids 只包含
        # L1 跨温度重试 + L2 聚焦重写 + L3 单beat拆分全部耗尽后仍未达标的场景。同批 ≥2 个这样
        # 的场景才判真·近空簇、整章重采（上限沿用既有整章重排预算）；仅 1 个时保留为已知缺陷。
        if len(_unrecoverable_ids) >= 2:
            raise ChapterResampleRequiredError(
                f"{len(_unrecoverable_ids)} unrecoverable scenes {_unrecoverable_ids} "
                "after rescue ladder; resample chapter with fresh plan",
                chapter=chapter_num, scene_ids=_unrecoverable_ids,
                replan="scene_near_empty_cluster",
            )
        if _unrecoverable_ids:
            self._flag_for_human(
                chapter_num, 0,
                f"known-defect scene(s) {_unrecoverable_ids}: near-empty after rescue, "
                "assembled anyway; please spot-read")

        # Phase 2: cross-scene overlap detection
        overlaps = detect_scene_overlap(scenes)
        if overlaps:
            for sid, desc_list in overlaps.items():
                logger.warning(f"Scene {sid} overlap detected: {desc_list}")
            # Regenerate all overlapping scenes (up to 2x)
            for sid in sorted(overlaps.keys()):
                scene_obj = next((s for s in scenes if s.scene_id == sid), None)
                if scene_obj is None:
                    continue
                # Collect representative overlap text as negative example
                neg = []
                for d in desc_list[:2]:
                    # Extract sample n-gram from description
                    if "samples:" in d:
                        samples = d.split("samples:")[-1].strip().split(", ")
                        neg.extend(samples[:1])
                for attempt in range(2):
                    try:
                        new_scene = self._regen_scene_for_id(
                            task_card, sid, scenes,
                            negative_examples=neg if neg else [desc_list[0]],
                        )
                        # Replace in list
                        idx = next((i for i, s in enumerate(scenes) if s.scene_id == sid), None)
                        if idx is not None:
                            scenes[idx] = new_scene
                        break
                    except SceneUnrecoverableError:
                        if attempt == 1:
                            raise
                        logger.warning(f"Scene {sid} regen attempt {attempt+1} still failing")
                else:
                    raise SceneUnrecoverableError(
                        f"Scene {sid} overlap unrecoverable after 2 attempts",
                        chapter=chapter_num, scene_id=sid,
                    )
            # Re-check overlaps after regen
            overlaps2 = detect_scene_overlap(scenes)
            if overlaps2:
                raise SceneUnrecoverableError(
                    f"Cross-scene overlap still present after regeneration: {overlaps2}",
                    chapter=chapter_num,
                )

        # Phase 3 (CC28 批B 3b)：相邻场同一现场近重复重演（零 LLM）。逐字 overlap 门判不了
        # “相邻两场把同一事件再演一遍”，这里用 CJK 4-gram 高相似 + 时空未推进判定；命中对
        # 后场定点重生一次（压缩为过渡/推进新时空），重生后仍重演则记硬阻断（进人验，不做
        # 昂贵整章重采）。完全换词的语义重演由批C blueprint 结构化门在源头拦截。
        self._reprise_hard = []
        try:
            from novel_engine.quality.cross_scene_reprise import detect_adjacent_reprise, score_pair
            _reprise_hits = detect_adjacent_reprise(
                [getattr(s, "scene_text", "") or "" for s in scenes])
            for _hit in _reprise_hits:
                _idx = _hit["index"]
                _sid = getattr(scenes[_idx], "scene_id", _idx + 1)
                _directive3b = (
                    "上一场景已经把当前这片现场演完了，你这一稿却把同一地点、同一时刻的同一事件"
                    "近乎原样又演了一遍（叙事时间没有推进）。本次严禁重演其发生经过：要么把前场结果"
                    "用一两句概括为过渡，要么直接推进到新的时间或地点，只写前场尚未发生过的新动作、"
                    "新信息或不可逆的状态变化。")
                try:
                    _new3b = self._regen_scene_for_id(
                        task_card, _sid, scenes, fix_directive=_directive3b,
                        frequency_penalty=0.4, presence_penalty=0.4)
                    scenes[_idx] = _new3b
                    _re = score_pair(
                        getattr(scenes[_idx - 1], "scene_text", "") or "",
                        getattr(_new3b, "scene_text", "") or "")
                    if _re["is_reprise"]:
                        self._reprise_hard.append(
                            f"[相邻重演] 场景{getattr(scenes[_idx-1],'scene_id',_idx)}→{_sid} "
                            f"定点重生后仍高度近重复 sim={_re['similarity']}")
                    else:
                        logger.info(f"Adjacent reprise resolved after scene {_sid} regen {_re['similarity']}")
                except Exception as _e3b:
                    self._reprise_hard.append(
                        f"[相邻重演] 场景{_sid} 与前场重演且定点重生失败: {_e3b}")
            if self._reprise_hard:
                logger.warning(f"ch{chapter_num} adjacent-scene reprise unresolved: {self._reprise_hard}")
        except Exception as _e3b0:
            logger.warning(f"adjacent reprise gate skipped: {_e3b0}")

    def _scene_issues_to_directives(self, issues, target, need_beats: int = 0):
        """Turn terse validator diagnostics into constructive, model-facing repair directives."""
        import re as _re
        out = []
        for it in issues:
            if it.startswith("length_too_short"):
                m = _re.search(r"length_too_short:\s*(\d+)\s*<\s*(\d+)", it)
                actual, floor = (m.group(1), m.group(2)) if m else ("?", "?")
                out.append(
                    f"上一稿正文仅约{actual}字（硬下限{floor}字、目标{target}字），篇幅不足。"
                    f"本次 scene_text 必须写满至少{floor}个中文字并逼近{target}字：把每个 beat 逐一演足"
                    "（每拍约150-250字，动作、环境、感官细节、对话与即时心理交替展开），"
                    "严禁概述、跳步或一笔带过；不得改变既定事件、人物与场景边界。"
                )
            elif it.startswith("beats_covered_insufficient"):
                _need = need_beats or 3
                out.append(
                    f"上一稿未按结构化契约在 beats_covered 中逐条列出本场景全部 beat（要求至少{_need}条）。"
                    f"本次必须先在 beats_covered 中为每个 beat 各写一句完整的覆盖说明（不少于{_need}条、一一对应），"
                    "确认无遗漏后再据此把 scene_text 逐拍演足；严禁跳过列点或只回一两句正文就提前收尾。"
                )
            elif it.startswith("high_freq_repeat"):
                # CC round-26f：把具体重复子串写进指令，LLM 才知道要改哪里
                import re as _re
                _m = _re.search(r"high_freq_repeat: ('[^']*'|\"[^\"]*\") x(\d+)", it)
                _sub = _m.group(1) if _m else "某一句式"
                _cnt = _m.group(2) if _m else "3"
                out.append(
                    f"上一稿出现句式重复：{_sub} 反复出现 {_cnt} 次（8字以上片段命中高频重复门）。"
                    "本次必须把这些重复句逐一改写：同一句法/开头连续使用不得超过两次，"
                    "用不同的动作、环境细节或视角展开替代原样复用；严禁整句原样复制。"
                    "scene_text 内严禁出现引号键名、换行缩进的 JSON 结构或 ``` 代码围栏。"
                )
            elif it.startswith("placeholder"):
                out.append("上一稿含有占位/省略文字（待补充、略、TODO 等）。严禁任何占位，必须写出完整成稿正文。")
            elif it.startswith("continuous_same_char"):
                out.append("上一稿含有连续重复字符。本次输出须为通顺自然的中文叙事。")
            elif it.startswith("[canon硬词]"):
                _m2 = _re.search(r"\[canon硬词\] (.+?)：(.+)$", it)
                _term = _m2.group(1) if _m2 else "修炼术语"
                _sent = (_m2.group(2) if _m2 else "")[:60]
                directive = (
                    f"上一稿出现婴儿篇禁写修炼术语「{_term}」：{_sent}。"
                    "本章为婴儿视角场景，严禁出现修炼体系词汇（吐纳/调息/引气/气感/经脉/丹田/灵根/仙门/武者/传功/传授呼吸法）。"
                    "如需描写婴儿呼吸异常，请只用普通生理词汇（呼吸节奏/气息/鼻息），用动作和环境细节侧面呈现；"
                    "如需交代成年人物往事/思虑，请用平凡日常语言，不出现任何修炼术语。"
                )
                # CC round-27：如果命中词属于 concept_unlocks 且未解锁，补充解锁上下文
                from novel_engine.quality.scope_gate import _build_term_to_concept_map, reset_concept_cache
                reset_concept_cache()
                concept_map = _build_term_to_concept_map(self.root)
                concept_ids = set()
                for t in _re.findall(r"\[canon硬词\] (.+?)：", it):
                    cid = concept_map.get(t)
                    if cid:
                        concept_ids.add(cid)
                if concept_ids:
                    try:
                        from novel_engine.engine.db import StateDB
                        if not hasattr(self, '_concept_db') or self._concept_db is None:
                            self._concept_db = StateDB(project_root=self.root)
                        concept_parts = []
                        for cid in sorted(concept_ids):
                            info = self._concept_db.get_concept_info(cid)
                            if info:
                                terms_str = "、".join(info["terms"][:8])
                                concept_parts.append(
                                    f"概念「{cid}」（含 {terms_str}）将在 ch{info['unlocked_at_chapter']} 由「{info['unlocked_by']}」解锁；"
                                    f"若 applies_to_character≠'*' 则仅限该角色相关叙述可出现。"
                                )
                        directive += "\n".join(concept_parts)
                    except Exception:
                        pass
                out.append(directive)
            elif it.startswith("truncated"):
                out.append(
                    "上一稿在句子中途被截断、没有完整收尾（末尾不是句号/问号/感叹号/省略号）。"
                    "本次必须写完整段并让最后一句自然收束；如内容较多，优先保证本场景在句末完整结束，严禁半句或破折号悬空收尾。"
                )
            elif it.startswith("markdown_scaffolding"):
                out.append(
                    "上一稿不是纯叙事正文：含有“## 正文”之类的 markdown 标题/分节标记，或先用一段概述预演后续情节再分节重演。"
                    "本次只交付本场景当下发生的连续叙事正文，严禁任何 # 号标题、‘正文/引子/楔子’等分节字样，"
                    "严禁提前概述或搬演后续场景（含后续的啼鸣异象、村民聚集、天亮等尚未在本场景发生的情节）；开头直接进入本场景动作。"
                )
            else:
                out.append(f"修正上一稿的问题：{it}")
        return out

    def _regen_scene_for_id(
        self,
        task_card: dict,
        scene_id: int,
        all_scenes: list,
        negative_examples: list[str] | None = None,
        temperature_override: float | None = None,
        fix_directive: str | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
    ) -> "SceneOutput":
        """Regenerate a single scene by id, injecting negative examples into prompt.

        出口硬拦（修复D，ch325 归因）：latin_leak_gate 只在主流程对原始场景跑一次，
        density/punct/continuity/scope 等后续通道的重生产物不再复检英文——ch325 的
        859 处英文正是经这些 regen 通道进入最终稿、绕过 latin 门直进 final gate BLOCK。
        这里在统一重生出口拦截：产物含拉丁泄漏（整句=语言跑飞、残片=混入）→ 携带对应
        directive 重试 1 次；重试成功返回干净场景，仍泄漏则记录 hard（final gate 兜底
        隔离），不改变既有异常控制流。
        """
        from novel_engine.quality import latin_leak_gate as _llg_d
        bps = task_card.get("scene_blueprints", [])
        bp = next((b for b in bps if int(b.get("scene_num", 0) or 0) == scene_id), None)
        if bp is None:
            bp = {"scene_num": scene_id, "goal": "", "location": "", "characters": [], "beats": []}
        synopsis_text = (getattr(self, "_current_synopsis_text", "") or "")

        def _gen(_fix=None, _neg=None, _fp=None, _pp=None):
            return self.writer.generate_scene(
                task_card, bp, synopsis_text,
                negative_examples=_neg if _neg is not None else negative_examples,
                all_blueprints=bps, temperature_override=temperature_override,
                fix_directive=_fix if _fix is not None else fix_directive,
                frequency_penalty=_fp if _fp is not None else frequency_penalty,
                presence_penalty=_pp if _pp is not None else presence_penalty,
            )

        try:
            ns = _gen()
        except Exception as e:
            logger.error(f"Scene {scene_id} regeneration failed: {e}")
            raise SceneUnrecoverableError(
                f"Scene {scene_id} regeneration failed: {e}",
                chapter=task_card.get("chapter_num"), scene_id=scene_id,
            )
        # 出口拉丁硬拦：任何 regen 通道的产物若再含英文，带语言 directive 重试 1 次
        try:
            _txt = getattr(ns, "scene_text", "") or ""
            _leak = _llg_d.detect_latin_leak(_txt)
            if not _leak["has_leak"]:
                return ns
            _sent = _llg_d.detect_english_sentences(_txt)
            if _sent["has_english_sentence"]:
                _d = ("本场景整段输出了英文句子（示例：" + (_sent["sample"] or "") + "）。"
                      "这是模型语言跑飞，本场景必须整体重写为纯中文：正文 100% 中文，"
                      "禁止任何英文字母、英文单词、英文句子与拉丁字符，专有名词一律用"
                      "中文或约定译名；不得改变剧情与情节点。")
                _ne = [(_sent["sample"] or "The purple fog pressed against the outside of his body"),
                       "（正文整段英文，严禁——必须整体重写为中文）"]
            else:
                _d = ("正文出现了英文字母/英文单词，这在纯中文古风里是严重错误。重写本场景时"
                      "严禁出现任何英文字母、英文单词或拉丁字符，正文必须全部为中文，"
                      "专有名词也一律用中文或约定译名；不得改变剧情与情节点。")
                _ne = ["he decision 不对外透露（正文混入英文残片，严禁）"]
            logger.warning(
                f"Scene {scene_id} regen output leaked latin "
                f"({_leak['leaked_tokens'][:5]}) -> language retry")
            try:
                ns2 = _gen(_fix=_d, _neg=_ne, _fp=0.3, _pp=0.3)
            except Exception as _e2:
                logger.warning(f"Scene {scene_id} language retry failed: {_e2}")
                ns2 = None
            if ns2 is not None and not _llg_d.detect_latin_leak(
                    getattr(ns2, "scene_text", "") or "")["has_leak"]:
                return ns2
            _still = _llg_d.detect_english_sentences(getattr(ns2, "scene_text", "") or "")
            hard = (f"[非中文] 场景{scene_id} 重生产物含整段英文（样例="
                    f"{_still['sample'] or (getattr(ns2, 'scene_text', '') or '')[:80]}）"
                    if _still["has_english_sentence"]
                    else f"[非中文] 场景{scene_id} 重生产物仍含拉丁字符 "
                         f"{_llg_d.detect_latin_leak(getattr(ns2, 'scene_text', '') or '')['leaked_tokens'][:6]}")
            self._last_deterministic_issues = getattr(self, "_last_deterministic_issues", []) + [hard]
            logger.error(f"Scene {scene_id} regen leaked latin twice -> hard (final gate will block)")
            return ns2 if ns2 is not None else ns
        except Exception as _ee:
            logger.warning(f"Scene {scene_id} latin exit-check skipped: {_ee}")
            return ns

    def _cc25_rescue_scene_l2l3(
        self,
        task_card: dict,
        cur: "SceneOutput",
        bp: dict | None,
        target: int,
        need_beats: int,
    ) -> "SceneOutput | None":
        """CC round-25 P0-1：单场近空的章内便宜抢救（Level 2 聚焦重写 → Level 3 单beat拆分）。

        仅在 L1 跨温度重生后该场仍空/<300字/截断时调用。两级各 1 次预算；任一级产出
        >=300 字、无脚手架且经韧性分级非 HARD/MALFORMED 的正文即采用（仍偏长不足的部分
        交给既有有界扩写/密度门）。两级都救不回返回 None，由调用方计入 L4 章级聚合。
        """
        from novel_engine.quality import scene_resilience as _sr25
        sid = int(getattr(cur, "scene_id", 0) or (bp or {}).get("scene_num", 0) or 0)
        beats = [b for b in ((bp or {}).get("beats") or []) if b]

        def _accept(prose: str, level: str) -> "SceneOutput | None":
            prose = (prose or "").strip()
            try:
                prose, _leaked = _sr25.strip_leaked_tokens(prose)
            except Exception:
                pass
            if len(prose) < 300 or "【" in prose or "】" in prose:
                return None
            try:
                _cls = _sr25.classify_scene_output(prose, "stop", target, beats)
            except Exception:
                _cls = "MALFORMED_SHORT"
            if _cls in ("HARD_DEGENERATE", "MALFORMED_SHORT"):
                return None
            repaired = SceneOutput(
                sid, prose, getattr(cur, "hook", "") or "",
                list(getattr(cur, "beats", []) or beats),
                beats_covered=list(beats) if beats else list(getattr(cur, "beats_covered", []) or []),
                structured=True, finish_reason="stop",
            )
            logger.warning(
                f"Scene {sid} ch{task_card.get('chapter_num', 0)} rescued at {level}: "
                f"class={_cls} {len(prose)} chars")
            return repaired

        # Level 2：同任务卡单场聚焦重写
        try:
            _p2 = self.writer.focused_rewrite_scene(task_card, bp or {"scene_num": sid}, target)
        except Exception as _e2:
            logger.warning(f"Scene {sid} L2 focused rewrite raised: {_e2}")
            _p2 = ""
        _r2 = _accept(_p2, "L2_focused") if _p2 else None
        if _r2 is not None:
            return _r2

        # Level 3：单 beat 拆分重写（整场仅 1 遍）
        try:
            _p3 = self.writer.beat_split_rewrite_scene(task_card, bp or {"scene_num": sid}, target)
        except Exception as _e3:
            logger.warning(f"Scene {sid} L3 beat-split raised: {_e3}")
            _p3 = ""
        return _accept(_p3, "L3_beat_split") if _p3 else None

    def _stage_write(self, task_card: dict, synopsis: dict) -> str:
        """阶段4：正文生成 + 润色（带确定性校验与成品净化）。

        规范顺序：生成结构化场景 → 扩写偏短场景 → polish_scenes 并发润色
        → 只组装一次 novel_text → 净化 → 发布。
        由此保证扩写与润色结果必然进入成稿文本。

        兼容路径：generate_full_chapter 返回字符串且 last_scenes 为空时（mock/重写路径），
        直接使用返回值作为成稿文本，跳过扩写和 polish_scenes。
        """
        self.state_machine.transition(ChapterPhase.WRITE_SCENE)
        # CC round-9：本章是否有任一确定性门命中（即使已修复）；命中章默认三评
        self._chapter_gate_fired = False
        # CC round-9：是否结构化场景章（用于禁用旧的整章续写凑字兜底）
        self._chapter_structured = False
        synopsis_text = synopsis.get("synopsis", "")
        pacing_constraints = PacingAdvisor().pre_write_constraints(synopsis_text)
        chapter_num = int(task_card.get("chapter_num", 0) or 0)
        # D1 resume-skip：journal 已有场景不再重生成，只生成缺失场景
        bps = task_card.get("scene_blueprints", []) or []
        done_ids = completed_scene_ids(self.root, chapter_num) if bps else set()
        need_merge = False
        missing = []  # fresh 路径无已完成场景；结构化路径统一引用
        # 记录 generate_full_chapter 的字符串返回值，用于非结构化兼容路径
        _gen_str_result = ""
        if done_ids:
            missing = [bp for bp in bps if int(bp.get("scene_num", 0) or 0) not in done_ids]
            need_merge = True
            if missing:
                logger.info(f"Resume chapter {chapter_num}: skip journaled {sorted(done_ids)}, "
                            f"generate {[b.get('scene_num') for b in missing]}")
                gen_card = dict(task_card, scene_blueprints=missing)
                _gen_str_result = self.writer.generate_full_chapter(gen_card, synopsis, pacing_constraints)
                self._journal_validated_scenes(chapter_num)
            else:
                logger.info(f"Resume chapter {chapter_num}: all scenes journaled {sorted(done_ids)}, reuse journal")
        else:
            _gen_str_result = self.writer.generate_full_chapter(task_card, synopsis, pacing_constraints)
            self._journal_validated_scenes(chapter_num)
        if need_merge:
            # resume 合并：journal 场景为底，新生成场景覆盖同 id，按 scene_id 排序
            journaled = {s.scene_id: s for s in self._load_journal_scenes(chapter_num)}
            for s in (getattr(self.writer, "last_scenes", None) or []):
                journaled[s.scene_id] = s
            merged = [journaled[k] for k in sorted(journaled)]
            if merged:
                self.writer.last_scenes = merged
        # 获取结构化场景列表（扩写 + 润色统一在此列表上操作）
        _scenes_list = getattr(self.writer, "last_scenes", None) or []
        # pkg2e: deterministic validation + overlap detection before expand/polish
        if _scenes_list:
            self._validate_and_regen_scenes(_scenes_list, task_card, chapter_num)
            # CC round-8 P0-2：按 sequence_index 故事时间排序（不可唯一排序→质量重规划）
            self._apply_sequence_order(task_card, _scenes_list)
        # 兼容路径：last_scenes 为空时（mock/重写），直接使用字符串返回值
        novel_text = ""
        if _scenes_list:
            # 结构化路径：近空场景已在上方判废重生；此处只对 0.4~0.6 目标的偏短场景扩写一次，
            # 再统一 polish_scenes，最后在唯一组装点拼出成稿（杜绝 4 份重写拼接）。
            _new_ids = {s.scene_id for s in _scenes_list}
            try:
                from novel_engine.core.quality_policy import load_quality_policy, derive_scene_targets
                _wp = load_quality_policy(self.root)
                _chapter_target = int(_wp["chapter_target_chars"])
                if bps:
                    _scene_targets = derive_scene_targets(_chapter_target, len(bps))
                    if missing:
                        _new_ids = {int(bp.get("scene_num", 0)) for bp in missing}
                    elif not done_ids:
                        _new_ids = {s.scene_id for s in _scenes_list}
                    else:
                        _new_ids = set()
                    def _target_for(_idx):
                        if _idx < len(_scene_targets):
                            return _scene_targets[_idx]
                        return _chapter_target // max(1, len(_scenes_list))
                    from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _asc
                    _expand_conc = int(getattr(self.outline_router, "concurrency", 4) or 4)
                    # CC round-9 Q4-3：有界扩写按场景并发（原为串行）；连贯极短先到 0.6 目标，首轮仍偏短且在增长再补一轮。
                    # CC round-26（r34 实测 4798/8000 偏短硬伤）：flash 常在 0.6~0.85 倍目标交付，
                    # 旧 0.6 门槛会让这些场跳过扩写，4 场累计仅约 4800~6000 字。把扩写门槛提到
                    # 0.85×目标（对齐 chapter min_ratio），两轮内把每场抬到接近目标，保证整章≈8000。
                    for _ea in range(2):
                        _todo = [(_i, _s) for _i, _s in enumerate(_scenes_list)
                                 if _s.scene_id in _new_ids
                                 and len(_s.scene_text) < int(0.85 * _target_for(_i))]
                        if not _todo:
                            break
                        def _expand_one(_item):
                            _i, _s = _item
                            _st = _target_for(_i)
                            _p = (
                                "请在【不改变任何情节、beats、出场人物与事件顺序】的前提下，扩写下面这一场小说正文，"
                                f"把篇幅自然补到约{_st}字。扩写只能做信息增量，严禁注水：\n"
                                "【禁止】1)堆砌形容词/副词渲染（如“格外/异常/深深地/仿佛”等修饰词叠床架屋）；"
                                "2)近义复句（同一意思换措辞重复，如“寒意刺骨，冷得彻骨”）；"
                                "3)不指向任何具体可感细节的抽象氛围渲染；4)新增事件、人物或情节。\n"
                                "【只允许】1)补一个具体感官细节（视/听/触/嗅觉中能被直接感知的细节，而非抽象形容）；"
                                "2)把一个概括动作拆成具体动作分解（如“他走过去”→“他迈过门槛，鞋底碾过碎石”）；"
                                "3)若本场有对白，补一句说话的语气、停顿或伴随小动作。\n"
                                "新增字数必须与新增的具体名词/动词数量成正比；直接输出扩写后的完整场景正文，"
                                "不要解释、不要分条、不要保留任何标记。\n\n"
                                f"{_s.scene_text}")
                            _ex = call_llm(_p, system_prompt="你是资深网文责编，只做“具体信息增量”式扩写：补感官、补动作分解、补对白语气；绝不加形容词渲染、近义复句、抽象氛围或任何新事件", output_json=False, client=self.refine_router)
                            return _i, _s, (_ex or "")
                        _grew = 0
                        with _TPE(max_workers=max(1, _expand_conc), thread_name_prefix="expand") as _exx:
                            _futs = [_exx.submit(_expand_one, _it) for _it in _todo]
                            for _f in _asc(_futs):
                                try:
                                    _i, _s, _ex = _f.result()
                                except Exception as _ee:
                                    logger.warning(f"Scene expansion failed: {_ee}")
                                    continue
                                if _ex and len(str(_ex)) > len(_s.scene_text):
                                    _diff = len(str(_ex)) - len(_s.scene_text)
                                    _s.scene_text = str(_ex)
                                    _grew += 1
                                    logger.info(f"Expanded scene {_s.scene_id} (round {_ea+1}) to {len(_s.scene_text)} chars (+{_diff})")
                        if _grew == 0:
                            break
                self.state_machine.transition(ChapterPhase.POLISH)
                try:
                    # CC round-9 Q4-1：达字数且过门场景跳过 polish（零风险省时）；可用 pipeline.skip_polish_on_target=false 关闭。
                    _skip_polish = bool((self.config.get("pipeline", {}) or {}).get("skip_polish_on_target", True))
                    if _skip_polish:
                        _polish_ids = {_s.scene_id for _i, _s in enumerate(_scenes_list)
                                       if _s.scene_id in _new_ids and len(_s.scene_text) < _target_for(_i)}
                        if _polish_ids != _new_ids:
                            logger.info(f"Skip polish for on-target scenes; polish {sorted(_polish_ids)} of {sorted(_new_ids)}")
                    else:
                        _polish_ids = _new_ids
                    _polished, _flags = self.writer.polish_scenes(_scenes_list, task_card, client=self.polish_router, only_ids=_polish_ids)
                    self.writer.last_scenes = _polished
                    _scenes_list = _polished
                    self._write_polish_flags(chapter_num, _scenes_list, _flags)
                    logger.info(f"polish_scenes completed: {sum(_flags)}/{len(_flags)} scenes polished")
                except Exception as _pe:
                    logger.warning(f"polish_scenes skipped: {_pe}")
                # CC round-26（r35 实测 8741->4508 硬伤）：扩写+polish 后的【最终篇幅】必须回写
                # journal——load_scenes 对同一 scene_id 取最新非空记录，后续确定性门/修复循环都以
                # journal 为唯一事实源；不回写则它们会把扩写成果丢弃、回退成原始短稿。
                if not (self.config.get("llm") or {}).get("use_mock"):
                    try:
                        for _sfin in _scenes_list:
                            append_scene(self.root, chapter_num, {
                                "scene_id": _sfin.scene_id,
                                "scene_text": _sfin.scene_text or "",
                                "hook": getattr(_sfin, "hook", ""),
                                "beats": getattr(_sfin, "beats", [])})
                        logger.info(
                            f"Journal synced with expanded/polished scenes ch{chapter_num}: "
                            f"{[(s.scene_id, len(s.scene_text or '')) for s in _scenes_list]}")
                    except Exception as _je26:
                        logger.warning(f"expanded-scene journal sync failed: {_je26}")
            except Exception as _se:
                logger.warning(f"Single-scene lower bound check skipped: {_se}")
            novel_text = self._assemble_chapter_text(_scenes_list) if _scenes_list else ""
        else:
            # 兼容路径：generate_full_chapter 返回纯字符串（mock/整章重写）时直接作为成稿
            novel_text = _gen_str_result or ""
        self._chapter_structured = bool(_scenes_list)

        # 成品净化：草稿保留标记供 patcher，发布版剥离（单一净化源，章号强制校正）
        purified = purify_novel_for_publish(novel_text, chapter_num=task_card.get("chapter_num"))
        self._draft_novel = novel_text
        self.current_novel = purified
        logger.info(f"Novel text generated (draft {len(novel_text)} chars → purified {len(purified)} chars)")
        # CC round-7 P0-3：跨章重演边界门（仅结构化路径；assembly 后 reviewer 前独立门）
        _bn = int(task_card.get("chapter_num", 0) or 0)
        if _scenes_list:
            try:
                purified = self._run_boundary_gate(_bn, task_card, _scenes_list, purified)
                self.current_novel = purified
            except Exception as _be:
                logger.warning(f"Boundary gate skipped: {_be}")
        # CC round-8 P0-2：章内时间/因果一致性门（乱序已在上方排序；此处抓“提前演后果”）
        if _scenes_list:
            purified = self._run_continuity_gate(_bn, task_card, _scenes_list, purified)
            self.current_novel = purified
        # CC round-9 P0-1：设定泄漏 / 时间锚越界 / 越权新增场景 确定性硬门
        if _scenes_list:
            purified = self._run_scope_gate(_bn, task_card, _scenes_list, purified)
            self.current_novel = purified
        # CC round-18 P0-1：非中文拉丁字符泄漏（重生1次；残留硬阻断）
        if _scenes_list:
            purified = self._run_latin_leak_gate(_bn, task_card, _scenes_list, purified)
            self.current_novel = purified
        # CC round-22 P0-1：低能动性主角成人化内心独白（确定性截断/定点重生）
        if _scenes_list:
            purified = self._run_pov_interiority_gate(_bn, task_card, _scenes_list, purified)
            self.current_novel = purified
        # CC round-22 P0-2：任务卡硬约束词面化履约（动作禁行/身份保密）
        if _scenes_list:
            purified = self._run_constraint_compliance_gate(_bn, task_card, _scenes_list, purified)
            self.current_novel = purified
        # CC round-10 P0-1：内容密度覆盖率门（具体事件/具名互动/伏笔揭示）
        if _scenes_list:
            purified = self._run_density_gate(_bn, task_card, _scenes_list, purified)
            self.current_novel = purified
        # CC round-23 P0-1：场景差异化推进（新状态/不可逆变化）+ 相邻场氛围去同质
        if _scenes_list:
            purified = self._run_scene_progression_gate(_bn, task_card, _scenes_list, purified)
            self.current_novel = purified
        # CC round-9 P0-3：过全部确定性门后仍不足软下限 → 最短1-2场景定向补写（防注水）
        if _scenes_list:
            purified = self._apply_length_floor(_bn, task_card, _scenes_list, purified)
            self.current_novel = purified
        # CC round-14 P0-1：跨场景重复门（扩写易搬运别场景素材）。句级近逐字确定性切除，
        # 意象级搬运对后现场景定点重生；必须在 length_floor 之后、reviewer 之前。
        if _scenes_list:
            purified = self._run_cross_scene_repeat_gate(_bn, task_card, _scenes_list, purified)
            self.current_novel = purified
            # CC round-16 P0-2：相邻场景边界近逐字重演（同义改写交 reviewer/E-loop）
            purified = self._run_boundary_reprise_gate(_bn, task_card, _scenes_list, purified)
            self.current_novel = purified
            # CC round-16 P0-1：成稿标点健康门（流水句仅插标点修复/整段重生兜底）
            purified = self._run_punctuation_health_gate(_bn, task_card, _scenes_list, purified)
            self.current_novel = purified
        # 确定性硬校验：命中则在日志中标记，后续在提交前强制拦截
        gate = self._deterministic_quality_gate(purified, task_card)
        if not gate["passed"]:
            logger.warning(f"Deterministic gate flagged chapter {task_card.get('chapter_num')}: {gate['issues']}")
            self._last_deterministic_issues = gate["issues"]
        else:
            self._last_deterministic_issues = []
        # 跨章结尾结构指纹：与前一章 novel 结尾对比，同构则记 soft 提示
        try:
            _cn = task_card.get("chapter_num") or 0
            _prev = novel_chapter_path(self.root, _cn - 1)
            if _prev.exists():
                _fp_cur = self._structure_fingerprint(purified)
                _fp_prev = self._structure_fingerprint(_prev.read_text(encoding="utf-8"))
                _overlap = _fp_cur & _fp_prev
                if len(_overlap) >= 5:
                    logger.warning(f"Chapter {_cn} ending overlaps prev ({sorted(_overlap)}); suggest varied closing")
        except Exception as _se:
            logger.warning(f"structure fingerprint check skipped: {_se}")
        return purified

    def _apply_sequence_order(self, task_card: dict, scenes: list) -> None:
        """CC round-8：assembly 前按 sequence_index 故事时间升序排列场景。

        sequence_index 缺失/重复导致无法唯一排序时，判任务卡规划失败并交导演绕过缓存
        重排（不在本章猜测时序）。
        """
        bps = task_card.get("scene_blueprints", []) or []
        order, ok = normalize_sequence(bps)
        if not ok:
            raise ChapterResampleRequiredError(
                "sequence_index 缺失或重复，无法确定故事时间顺序，需导演重排任务卡",
                chapter=task_card.get("chapter_num"),
                scene_ids=[int(b.get("scene_num", 0) or 0) for b in bps],
                replan="continuity_block",
            )

        def _key(s):
            sid = int(getattr(s, "scene_id", 0) or 0)
            return (order.get(sid, sid), sid)

        scenes.sort(key=_key)


    # Gate delegation: all gate logic moved to pipeline/gates.py
    # Using proper instance methods to preserve automatic self-binding

    def _run_continuity_gate(self, *a, **k):
        return _gates._run_continuity_gate(self, *a, **k)

    def _run_scope_gate(self, *a, **k):
        return _gates._run_scope_gate(self, *a, **k)

    def _run_density_gate(self, *a, **k):
        return _gates._run_density_gate(self, *a, **k)

    def _run_scene_progression_gate(self, *a, **k):
        return _gates._run_scene_progression_gate(self, *a, **k)

    def _run_cross_scene_repeat_gate(self, *a, **k):
        return _gates._run_cross_scene_repeat_gate(self, *a, **k)

    def _run_latin_leak_gate(self, *a, **k):
        return _gates._run_latin_leak_gate(self, *a, **k)

    def _run_pov_interiority_gate(self, *a, **k):
        return _gates._run_pov_interiority_gate(self, *a, **k)

    def _run_constraint_compliance_gate(self, *a, **k):
        return _gates._run_constraint_compliance_gate(self, *a, **k)

    def _run_punctuation_health_gate(self, *a, **k):
        return _gates._run_punctuation_health_gate(self, *a, **k)

    def _run_final_precommit_gate(self, *a, **k):
        return _gates._run_final_precommit_gate(self, *a, **k)

    def _run_boundary_reprise_gate(self, *a, **k):
        return _gates._run_boundary_reprise_gate(self, *a, **k)

    def _run_boundary_gate(self, *a, **k):
        return _gates._run_boundary_gate(self, *a, **k)

    def _apply_length_floor(self, *a, **k):
        return _gates._apply_length_floor(self, *a, **k)

    def _punct_only_repair(self, *a, **k):
        return _gates._punct_only_repair(self, *a, **k)

    def _repair_paragraphs_punct_parallel(self, *a, **k):
        return _gates._repair_paragraphs_punct_parallel(self, *a, **k)

    def _repair_paragraphs_punct_batched(self, *a, **k):
        return _gates._repair_paragraphs_punct_batched(self, *a, **k)


    def _finalize_current_novel(self, chapter_num: int) -> str:
        """CC round-18 P8C/P8D：对 current_novel 跑一次 finalize，返回切后字符串。

        幂等、保留 ※ 分场符；同步回写 self.current_novel。
        返回 finalize 后文本（与 self.current_novel 同源）。
        调用点统一用返回值，杜绝局部变量不同源。
        """
        try:
            from novel_engine.quality.punctuation_health import finalize_text_long_sentences
            _cur = self.current_novel or ""
            if not _cur:
                return _cur
            _finalized, _ls_stats = finalize_text_long_sentences(_cur)
            if _finalized != _cur:
                logger.info(
                    f"finalize ch{chapter_num}: detected={_ls_stats['detected']}, "
                    f"resolved={_ls_stats['resolved']}, residual={_ls_stats['residual']}")
                self.current_novel = _finalized
            return _finalized
        except Exception as _pe:
            logger.warning(f"finalize skipped ch{chapter_num}: {_pe}")
            return _cur

    def _stage_review(self, chapter_num: int, task_card: dict, synopsis: dict,
                      novel_text: str, world_state: dict) -> dict:
        """阶段5：审查评分。"""
        # 修复流中会以 REVIEW 状态再次调用本方法，避免无效的 REVIEW->REVIEW 转换报错
        if self.state_machine.current_phase != ChapterPhase.REVIEW:
            self.state_machine.transition(ChapterPhase.REVIEW)
        # CC round-8 P0-3：每次评审入口复位“极不稳定”标记
        self._review_highly_unstable = False
        # CC round-26：单章评审 LLM 调用硬上限（首评/三评/单离群补票/修复后复评合计）。
        # 跨修复轮持续累计、按章复位；超限不再补票，强制走 gap（防补票递归到凑分为止）。
        from novel_engine.quality import review_votes as _rv26mod
        _REVIEW_CALL_CAP26 = int(getattr(_rv26mod, "REVIEW_CALL_CAP", 6))
        if getattr(self, "_review_call_chapter", None) != chapter_num:
            self._review_call_chapter = chapter_num
            self._review_call_count = 0
        # CC P0: 成稿文本不变则复用评审结果（跨进程 infra retry 不二次计费/打分）；
        # 定点重生/重写后文本改变，hash 自动变化而 miss。同进程修复循环内的再评审
        # (本章已读过一次) 绕过缓存读，保留“编辑→再评审”的既有收敛语义。
        review = None
        _rk = None
        _allow_read = chapter_num not in self._review_read_chapters
        self._review_read_chapters.add(chapter_num)
        _was_cache_hit = False
        if _allow_read:
            try:
                _rv_model = model_fingerprint(self.root, "review")
                _rk = content_key(
                    {"novel_text": novel_text, "task_card": task_card,
                     "synopsis": synopsis, "world_state": world_state},
                    version=PROMPT_VERSIONS["review"], model=_rv_model)
                review = self._phase_cache.get(chapter_num, "review", _rk)
                if review is not None:
                    _was_cache_hit = True
                    logger.info(f"Review cache hit for chapter {chapter_num} (identical final text)")
            except Exception as _ce:
                logger.warning(f"review cache read skipped: {_ce}")
                review = None
        # CC round-24 P0-1：真实 LLM 模式下构建零成本确定性信号（推进契约覆盖/单场氛围/
        # 相邻共享/硬门状态），供 reviewer 锚点注入 + pacing/retention 混合分 + 异常票判定。
        # mock/测试模式整体关闭，保持历史评分与既有离线测试不变。
        _real24 = not bool((self.config.get("llm") or {}).get("use_mock"))
        _signals24 = None
        _supplementary_used24 = False
        if _real24:
            try:
                from novel_engine.quality import density_gate as _dg24
                _a24 = _dg24.arc_for_chapter(chapter_num, self.root)
                _sc24 = [{"scene_id": d.get("scene_id"), "scene_text": d.get("scene_text", "") or ""}
                         for d in load_authoritative_scenes(self.root, chapter_num)]
                _hgclean24 = not (getattr(self, "_last_deterministic_issues", [])
                                  or getattr(self, "_boundary_hard", []))
                _signals24 = review_hybrid.build_deterministic_signals(
                    task_card, _sc24, self.root, arc=_a24, hard_gates_clean=_hgclean24)
                # 设定贯通·揭示尺度：前世描写 soft_warn（ch48 口径，不做 hard block）。
                # 确定性检测，经 deterministic_signals 注入 reviewer，不新增 LLM 调用。
                try:
                    from novel_engine.quality.reveal_scale_gate import (
                        detect_past_life_reveal_scale)
                    _qt_cfg = {}
                    try:
                        _qt_path = self.root / "config" / "quality_thresholds.json"
                        if _qt_path.exists():
                            import json as _json_qt
                            _qt_cfg = _json_qt.loads(
                                _qt_path.read_text(encoding="utf-8"))
                    except Exception:
                        _qt_cfg = {}
                    _plr_cfg = (_qt_cfg.get("past_life_reveal") or {})
                    _plr = detect_past_life_reveal_scale(
                        novel_text,
                        detail_terms=_plr_cfg.get("detail_terms"),
                        window_chars=int(_plr_cfg.get("window_chars", 150)),
                        detail_hits=int(_plr_cfg.get("detail_hits", 3)),
                    )
                    if _plr.get("reveal_scale_high") and isinstance(_signals24, dict):
                        _signals24["past_life_reveal"] = _plr
                        logger.info(
                            f"ch{chapter_num} past-life reveal scale HIGH "
                            f"(max_window_hits={_plr.get('max_window_hits')}) → soft note")
                except Exception as _plre:
                    logger.warning(f"past-life reveal scale check skipped: {_plre}")
            except Exception as _e24sig:
                logger.warning(f"deterministic signals build failed-open: {_e24sig}")
                _signals24 = None

        def _one_review24():
            self._review_call_count = int(getattr(self, "_review_call_count", 0)) + 1
            rv = self.reviewer.review_chapter(
                chapter_num=chapter_num, task_card=task_card, synopsis=synopsis,
                novel_text=novel_text, world_state=world_state,
                deterministic_signals=_signals24 if (_real24 and _signals24 is not None) else None)
            if _real24 and _signals24 is not None:
                review_hybrid.annotate_raw_total(rv)
                review_hybrid.hybridize_review(rv, _signals24)
            return rv

        if review is None:
            review = _one_review24()
        score = review.get("normalized_score", review.get("total_score", 0))
        # CC round-7 Q4：首评异常低(<65)且文本已过全部确定性硬门时，同稿确定性复核一次，
        # 取两次最高，避免一次假低分（同稿 58/87/92 那种高方差）触发昂贵返工；分差>20 记不稳定。
        if (
            _allow_read and not _was_cache_hit and isinstance(score, (int, float))
            and 0 < score < 65 and not (getattr(self, "_last_deterministic_issues", []) or [])
        ):
            logger.warning(
                f"ch{chapter_num} first review {score}<65 with no deterministic hard issue "
                "→ deterministic same-text recheck")
            try:
                review2 = _one_review24()
                score2 = review2.get("normalized_score", review2.get("total_score", 0)) or 0
                try:
                    if abs(float(score2) - float(score)) > 20:
                        logger.warning(
                            f"review_unstable ch{chapter_num}: {score} vs {score2} (|gap|>20)")
                except (TypeError, ValueError):
                    pass
                if float(score2) > float(score):
                    review, score = review2, score2
                    logger.info(f"ch{chapter_num} recheck higher → adopt score={score2}")
            except Exception as _re:
                logger.warning(f"review recheck failed-open ({_re}); keep first score")
        # CC round-8 P0-3：首评落入 85-90 近阈值区 → 确定性三评取中位数（同稿同档连调3次）；
        # 三评极差>15 标 review_highly_unstable，无论中位数是否过线都强制人工复核。
        _gate_fired = bool(getattr(self, "_chapter_gate_fired", False))
        # CC round-24 Q5①：确定性指标全绿（硬门 clean + 推进/氛围无违规）时，门即使触发过
        # 修复也不强制三评，首评即可；只有分数落 85-90 带或出现极差才补评。非全绿仍按旧规
        # 门命中即三评。mock 模式沿用旧逻辑（_green24=False）。
        _green24 = _real24 and review_hybrid.signals_all_green(_signals24)
        _force_triple24 = _gate_fired and not _green24
        # CC28/批D 提速：真机用窄灰带 [81.5,85.5] 决定是否三评（>=85.5 且硬门绿→单评免三；
        # <=81.5→不三评直接修复；落带才三评）；门命中且非全绿仍强制三评。mock/测试沿用旧 85-90 带。
        if _real24:
            try:
                from novel_engine.quality import gray_band as _gb28
                _band_triple28 = _gb28.decide_review_count(
                    score, bool(_green24),
                    band=tuple(getattr(self, "_gray_band28", _gb28.INITIAL_BAND)),
                    force_dual=bool(getattr(self, "_gray_force_dual28", False)),
                )["plan"] == _gb28.REVIEW_TRIPLE
            except Exception:
                _band_triple28 = should_run_extra_reviews([score])
        else:
            _band_triple28 = should_run_extra_reviews([score])
        if _allow_read and not _was_cache_hit and _force_triple24 and not _band_triple28:
            logger.warning(f"ch{chapter_num} deterministic gate fired (repaired) -> default triple review despite score={score}")
        if _allow_read and not _was_cache_hit and (_band_triple28 or _force_triple24):
            try:
                votes = [(review, float(score))]
                for _ in range(2):
                    _rv = _one_review24()
                    votes.append((_rv, float(_rv.get("normalized_score", _rv.get("total_score", 0)) or 0)))
                # CC round-24 P0-1(c)：剔除技术性错误票（raw=0 解析失败 / raw>120 越界 /
                # 硬门已证伪却声称拉丁英文泄漏）。有效票不足 2 时不做中位数结论，转不稳定低分。
                if _real24:
                    _hg24 = bool(_signals24 and _signals24.get("hard_gates_all_clean"))
                    _fvotes = review_hybrid.filter_valid_votes(votes, _hg24)
                    if len(_fvotes) >= 2:
                        votes = _fvotes
                    elif _fvotes:
                        logger.error(
                            f"ch{chapter_num} only {len(_fvotes)}/3 valid review vote(s) "
                            f"(others parse/out-of-range) -> unstable, no median conclusion")
                        votes = _fvotes
                _vals = [v[1] for v in votes]
                _agg = aggregate_votes(_vals)
                self._review_highly_unstable = bool(_agg["highly_unstable"])
                _med = float(_agg["median"])
                _chosen = next((rv for rv, sc in votes if abs(sc - _med) < 1e-9), None)
                if _chosen is None and votes:
                    _chosen = sorted(votes, key=lambda x: x[1])[len(votes) // 2][0]
                if not votes:
                    # 三票全部技术无效：无可信分，按 0 分走不稳定 gap（绝不伪达标出版）
                    logger.error(f"ch{chapter_num} all 3 review votes technically invalid -> score 0 gap")
                    self._review_highly_unstable = True
                    score = 0.0
                else:
                    review, score = _chosen, _med
                # CC round-22 P0-3：中位本就<软线 -> 维持 unstable（runner gap-continue）；
                # 中位>=软线却分歧大 -> 追加1次复评，4票取中位：新中位>=软线则清 unstable 走正常
                # 提交/E-loop 流程，<软线则留 unstable 交 runner gap。复评本身异常才保守保留 HALT 兜底。
                if self._review_highly_unstable:
                    if "_med" not in locals():
                        _med = float(score)
                    try:
                        _soft5 = int(load_quality_policy(self.root).get("soft_publication_line", 85))
                    except Exception:
                        _soft5 = 85
                    # ── CC round-26：单离群崩票识别 + 恰好1次补票收敛（最重要原则修正）──
                    # 旧逻辑：极差>15 即 unstable，中位<软线直接 gap、跳过整个 E-loop/文学性修订。
                    # 新逻辑：三票呈"一张离群+两票紧簇"时，补 1 票交叉验证：
                    #   新票落入紧簇±8 → 确认离群票是崩票，用[紧簇两票+新票]取中位、清除 unstable，
                    #     即使中位<软线也照常进入 E-loop/文学性修订并重新评审（内容不达标≠评审不可信）；
                    #   新票不收敛 → 维持 unstable 走 gap。真三分布分裂(true_split)不走本块，维持人工。
                    _pat26 = str((_agg or {}).get("pattern", ""))
                    _cluster26 = list((_agg or {}).get("cluster") or [])
                    _outlier26 = (_agg or {}).get("outlier")
                    _cc26_outlier_attempted = False
                    if (_real24
                            and _pat26 in ("single_outlier_low", "single_outlier_high")
                            and len(_cluster26) == 2
                            and not _supplementary_used24
                            and self._review_call_count < _REVIEW_CALL_CAP26):
                        _cc26_outlier_attempted = True
                        _cluster_pairs26 = [p for p in votes
                                            if any(abs(p[1] - float(cv)) < 1e-9 for cv in _cluster26)]
                        _outlier_rv26 = next((rv for rv, scx in votes
                                              if _outlier26 is not None and abs(scx - float(_outlier26)) < 1e-9),
                                             None)
                        try:
                            _supplementary_used24 = True
                            _rv26 = _one_review24()
                            _valid26 = review_hybrid.is_valid_vote(_rv26) \
                                and not review_hybrid.dim_sum_mismatch(_rv26)
                            if not _valid26:
                                logger.warning(
                                    f"ch{chapter_num} CC26 outlier-check supplementary vote invalid; "
                                    f"keep unstable (pattern={_pat26}) -> gap")
                            else:
                                _s26 = float(_rv26.get("normalized_score",
                                                       _rv26.get("total_score", 0)) or 0)
                                # 三类矛盾特征仅作辅助佐证（绝不单独丢票），落日志供回溯。
                                try:
                                    _feats26 = review_hybrid.vote_anomaly_features(
                                        _outlier_rv26 or {}, _signals24,
                                        ending_text=(novel_text or "")[-400:])
                                    _hits26 = [k for k, v in _feats26.items() if v]
                                    if _hits26:
                                        logger.info(
                                            f"ch{chapter_num} CC26 outlier vote {float(_outlier26):.1f} "
                                            f"auxiliary contradiction features: {_hits26}")
                                except Exception:
                                    pass
                                if _rv26mod.supplementary_converges(_cluster26, _s26):
                                    _pairs26 = _cluster_pairs26 + [(_rv26, _s26)]
                                    _vals26 = [sc26 for _, sc26 in _pairs26]
                                    _agg26 = aggregate_votes(_vals26)
                                    _med26 = float(_agg26["median"])
                                    _chosen26 = next(
                                        (rv26 for rv26, sc26 in _pairs26 if abs(sc26 - _med26) < 1e-9),
                                        None)
                                    if _chosen26 is None and _pairs26:
                                        _chosen26 = sorted(_pairs26, key=lambda x: x[1])[
                                            len(_pairs26) // 2][0]
                                    review = _chosen26 or review
                                    score = _med26
                                    _med = _med26
                                    _vals = _vals26
                                    _agg = _agg26
                                    self._review_highly_unstable = False
                                    logger.warning(
                                        f"ch{chapter_num} CC26 single-outlier resolved: votes={_vals} "
                                        f"(discarded crash vote {float(_outlier26):.1f}, supplementary "
                                        f"{_s26:.1f} converged) -> median={_med26:.2f}, unstable cleared; "
                                        f"proceed to normal E-loop/commit routing")
                                else:
                                    logger.warning(
                                        f"ch{chapter_num} CC26 single-outlier NOT confirmed: supplementary "
                                        f"{_s26:.1f} outside cluster {_cluster26} tol; keep unstable -> gap")
                        except Exception as _e26:
                            logger.warning(
                                f"ch{chapter_num} CC26 outlier-check failed-open; keep unstable: {_e26}")
                    # 旧 rescue/bias 补充票：单离群块已用掉唯一补充票、或已达调用上限时不再追加。
                    _legacy_supp_ok26 = (
                        not _cc26_outlier_attempted
                        and bool(self._review_highly_unstable)
                        and self._review_call_count < _REVIEW_CALL_CAP26)
                    if _legacy_supp_ok26:
                        _rescue4 = _med >= _soft5
                        # CC round-24 P0-1(c)：中位<软线但确定性信号全绿 -> 疑似评审偏严，
                        # 追加恰好 1 次交叉验证（与救援共用唯一一次补充票，每章至多 1 次）。
                        _bias4 = (_real24 and _green24
                                  and review_hybrid.diagnose_low_score(
                                      _med, _signals24, _soft5) == "suspected_reviewer_bias")
                        # CC round-27：缓坡形态（consistent + 极差>15，无单离群/无真分裂，
                        # 如 49.2/62.5/69.7）——中位<软线且非全绿时旧逻辑直接人工、不给补票机会。
                        # 同样给恰好 1 次交叉验证：4 票中间两票差<=8 视为共识成形清 unstable。
                        _slope4 = (str((_agg or {}).get("pattern", "")) == "consistent"
                                   and float((_agg or {}).get("range", 0)) > 15)
                    else:
                        _rescue4 = False
                        _bias4 = False
                        _slope4 = False
                    if _rescue4 or _bias4 or _slope4:
                        def _pick_pair(pairs, target):
                            w = next((rv for rv, sc in pairs if abs(sc - target) < 1e-9), None)
                            if w is None and pairs:
                                w = sorted(pairs, key=lambda x: x[1])[len(pairs) // 2][0]
                            return w or review
                        try:
                            _supplementary_used24 = True
                            _rv4 = _one_review24()
                            _valid4 = (not _real24) or review_hybrid.is_valid_vote(_rv4)
                            if not _valid4:
                                logger.warning(
                                    f"ch{chapter_num} supplementary vote technically invalid; "
                                    f"keep median={_med:.2f}")
                                _med4, _ch4 = _med, review
                            else:
                                _s4 = float(_rv4.get("normalized_score", _rv4.get("total_score", 0)) or 0)
                                _vals4 = _vals + [_s4]
                                _med4_raw = float(aggregate_votes(_vals4)["median"])
                                _pairs4 = votes + [(_rv4, _s4)]
                                if _rescue4:
                                    # CC round-23：追加复评是“救援”不是“否决”——第4票不得把
                                    # 三票中位已达标章往下拉，取 max(三票中位, 四票中位)。
                                    _med4 = max(_med, _med4_raw)
                                    if _med4 == _med and _med4_raw < _med:
                                        _ch4 = _chosen if "_chosen" in locals() else review
                                        logger.warning(
                                            f"ch{chapter_num} supplementary 4th review {_s4:.1f} lower "
                                            f"(raw4med={_med4_raw:.2f}); rescue floor keeps median={_med:.2f}")
                                    else:
                                        _ch4 = _pick_pair(_pairs4, _med4)
                                else:
                                    # 偏严交叉验证：诚实采纳新中位；多数票仍低则交 runner gap。
                                    _med4 = _med4_raw
                                    _ch4 = _pick_pair(_pairs4, _med4)
                                    if _slope4:
                                        # CC round-27：缓坡补票——4 票中间两票差<=8 视为评审
                                        # 共识成形，清 unstable 走正常 E-loop/提交路由（内容不达标
                                        # ≠评审不可信）；中间两票仍宽则维持 unstable 交人工复核。
                                        _slope_ok26 = _rv26mod.slope_median_converges(_vals4)
                                        self._review_highly_unstable = not _slope_ok26
                                        _midpair26 = float(
                                            sorted(float(v) for v in _vals4)[2]
                                            - sorted(float(v) for v in _vals4)[1])
                                        logger.warning(
                                            f"ch{chapter_num} slope-check supplementary: "
                                            f"votes={[round(v, 1) for v in _vals4]} "
                                            f"midpair_range={_midpair26:.2f} converged={_slope_ok26} "
                                            f"median={_med4:.2f}")
                                    else:
                                        self._review_highly_unstable = bool(_med4 < _soft5)
                                    logger.warning(
                                        f"ch{chapter_num} supplementary review "
                                        f"({'rescue' if _rescue4 else ('slope-check' if _slope4 else 'bias-crosscheck')}): "
                                        f"votes={[round(v, 1) for v in _vals4]} raw4med={_med4_raw:.2f} "
                                        f"adopted={_med4:.2f}")
                            review, score = _ch4, _med4
                        except Exception as _se4:
                            logger.warning(
                                f"supplementary review failed-open ch{chapter_num}; keep "
                                f"median={_med} unstable: {_se4}")
                if "_med" not in locals():
                    _med = float(score)
                logger.warning(
                    f"ch{chapter_num} median-of-3 votes={_vals} median={_med} "
                    f"range={_agg['range']} highly_unstable={self._review_highly_unstable}")
            except Exception as _ve:
                logger.warning(f"median-of-3 review failed-open ({_ve}); keep first score {score}")
        # CC round-24 P0-1(c)：未触发三评的“单评 + 确定性全绿但分数<软线”场景，做恰好 1 次
        # 交叉验证（每章至多 1 次补充票）；新中位仍低则诚实保留低分，交 runner gap，不伪达标。
        if (_real24 and _signals24 is not None and _allow_read and not _was_cache_hit
                and not _supplementary_used24):
            try:
                _softb = int(load_quality_policy(self.root).get("soft_publication_line", 85))
            except Exception:
                _softb = 85
            if (isinstance(score, (int, float)) and float(score) < _softb
                    and not getattr(self, "_review_highly_unstable", False)
                    and review_hybrid.signals_all_green(_signals24)):
                try:
                    _rvb = _one_review24()
                    if review_hybrid.is_valid_vote(_rvb):
                        _sb = float(_rvb.get("normalized_score", _rvb.get("total_score", 0)) or 0)
                        _medb = float(aggregate_votes([float(score), _sb])["median"])
                        _chb = _rvb if abs(_sb - _medb) < 1e-9 else review
                        logger.warning(
                            f"ch{chapter_num} green-but-low single review {float(score):.1f}; "
                            f"one crosscheck vote {_sb:.1f} -> median {_medb:.2f}")
                        review, score = _chb, _medb
                except Exception as _eb:
                    logger.warning(f"green-low crosscheck failed-open ch{chapter_num}: {_eb}")
        # 首次成稿评审才落缓存（修复循环 _rk=None 不写，避免 review_None 噪声）
        if not _was_cache_hit and _rk:
            try:
                self._phase_cache.set(chapter_num, "review", _rk, review)
            except Exception:
                pass
        if score <= 0:
            logger.warning("Review parse returned non-positive score for ch" + str(chapter_num))
        if 0 < score < 60:
            logger.warning("Review score unusually low for ch" + str(chapter_num) + " score=" + str(score))
        verdict = self.reviewer.grade_review(review)
        self._last_review_score = score  # Store for commit stage
        logger.info(f"Chapter {chapter_num} review: normalized_score={score}, verdict={verdict}")
        return {"review": review, "score": score, "verdict": verdict,
                "review_unstable": bool(getattr(self, "_review_highly_unstable", False))}

    def _enforce_word_count(self, novel, target_min, target_max):
        cur = len(novel)
        # 提取章末 hook（若有）以便保留
        hook_match = None
        hook_text = ""
        # hook 可能在 purified 后已被剥离，此处从 draft 中提取
        try:
            draft = getattr(self, "_draft_novel", "")
            if draft and "（章末钩子" in draft:
                import re as _re
                m = _re.search(r"（章末钩子[^）]*）", draft)
                if m:
                    hook_text = m.group(0)
        except Exception:
            pass
        _structured_chapter = bool(getattr(self, "_chapter_structured", False))
        if cur < target_min and not _structured_chapter:
            for _ in range(3):
                add = target_min - len(novel) + 50
                extra = call_llm(
                    f"请在不改变剧情前提下，为下文续写约{add}字使其更丰满：\n{novel}",
                    client=self.llm, output_json=False)
                novel = novel.rstrip() + "\n\n" + extra.strip()
                if len(novel) >= target_min:
                    break
        elif cur > target_max:
            # P0-截断保完整：按场景边界裁剪，保留所有场景与 hook，不制造 high
            # 若为净化后文本（无 【场景 标记），则按段落裁剪
            import re as _re3
            has_hook = bool(hook_text)
            body = novel
            if has_hook and hook_text in body:
                body = body.replace(hook_text, "").rstrip()
            # 尝试按场景拆分（优先 draft 标记，其次 ※）
            scenes = None
            if "【场景" in body:
                parts = _re3.split(r"(?=\n*【场景\d+：)", body)
                scenes = [p for p in parts if p.strip()]
            else:
                # 净化后无标记，按 ※ 或段落拆分
                parts = _re3.split(r"\n?\s*※\s*\n?", body)
                scenes = [p.strip() for p in parts if p.strip()]
                if len(scenes) <= 1:
                    # 退化为段落裁剪
                    scenes = None
            if scenes and len(scenes) >= 2:
                # 按场景等比裁剪，保留所有场景框架
                per_scene_budget = target_max // len(scenes)
                trimmed_scenes = []
                for sc in scenes:
                    if len(sc) > per_scene_budget * 1.2:
                        # 场景内按段落裁剪
                        cut_sc = sc[:per_scene_budget]
                        last_para = cut_sc.rfind("\n\n")
                        last_punct = max(cut_sc.rfind("。"), cut_sc.rfind("！"), cut_sc.rfind("？"))
                        if last_para > per_scene_budget * 0.7:
                            cut_sc = cut_sc[:last_para].rstrip()
                        elif last_punct > per_scene_budget * 0.7:
                            cut_sc = cut_sc[:last_punct+1]
                        trimmed_scenes.append(cut_sc.rstrip())
                    else:
                        trimmed_scenes.append(sc)
                body = "\n\n※\n\n".join(trimmed_scenes) if "※" in novel or len(scenes) > 1 else "\n\n".join(trimmed_scenes)
            else:
                # 段落级裁剪，保 hook
                cut = body[:target_max]
                last_para = cut.rfind("\n\n")
                last_punct = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"), cut.rfind("…"), cut.rfind("”"))
                if last_para > target_max * 0.8:
                    body = cut[:last_para].rstrip()
                elif last_punct > target_max * 0.8:
                    body = cut[:last_punct+1]
                else:
                    last_nl = cut.rfind("\n")
                    body = cut[:last_nl].rstrip() if last_nl > target_max*0.7 else cut.rstrip()
            if has_hook:
                novel = body.rstrip() + f"\n\n{hook_text}"
            else:
                if body and body[-1] not in "。！？…）":
                    body = body.rstrip() + "。"
                novel = body
        if "（章末钩子" not in novel and hook_text and not _structured_chapter:
            # 仅当原文无 hook 时补，当前 purified 已无 hook 标记，补一个叙事化收束而非标记
            if len(novel) < target_min:
                novel = novel.rstrip() + "\n\n夜色渐深，远处传来隐约的声响，预示着新的变局将至。"
        if _structured_chapter and cur < target_min:
            # CC round-9：结构化章已由场景级篇幅地板处理；软下限不足也不再整章续写，
            # 避免无约束续写把 scope 门删掉的越界内容/天亮时间锚重新写回（软下限不硬凑）。
            logger.info(f"Structured chapter below soft min ({cur} < {target_min}); accept scene-floor length, no whole-chapter continuation")
        return novel

    def _enforce_mandatory_beats(self, chapter_num: int, task_card: dict,
                                   synopsis_text: str = '') -> dict:
        """R16c/R17: 必填伏笔强制闭环。

        扫描当前权威 journal 中所有场景，对 category==foreshadow 的 beat
        做逐场景覆盖检查；缺失场景按 budget=2 进行定点重生。
        重生后把重组文本回灌到 self.current_novel / self._draft_novel。

        返回 {enforced, missing_scenes, blocked}。
        """
        try:
            scenes_data = load_authoritative_scenes(self.root, chapter_num)
        except Exception:
            scenes_data = []
        if not scenes_data:
            return {"enforced": False, "missing_scenes": {}}

        # R17-2 read-time correction
        try:
            _mcb_reconciled = extract_must_cover_beats(task_card)
            if _mcb_reconciled:
                task_card["must_cover_beats"] = _mcb_reconciled
            else:
                task_card.pop("must_cover_beats", None)
        except Exception as _re:
            logger.warning(f"ch{chapter_num} mandatory enforce reconcile failed: {_re}")

        all_beats = task_card.get("must_cover_beats") or extract_must_cover_beats(task_card)
        mcb_list = [b for b in all_beats if b.get("category") == "foreshadow"]
        # R17-3
        _registered_fs_ids = {
            (fa.get("foreshadow_id") or "").strip()
            for fa in (task_card.get("foreshadow_actions") or [])
            if isinstance(fa, dict) and (fa.get("foreshadow_id") or "").strip()
        }
        _resolved_fs_ids = {b.get("foreshadow_id", "") for b in mcb_list if b.get("foreshadow_id")}
        _missing_fs_ids = sorted(_registered_fs_ids - _resolved_fs_ids)
        logger.info(
            f"ch{chapter_num} hard-mandatory foreshadow beats: {len(mcb_list)} "
            f"(from {len(all_beats)} total), registered={_registered_fs_ids}, resolved={_resolved_fs_ids}")
        if _missing_fs_ids:
            logger.error(
                f"ch{chapter_num} MANDATORY HARD BLOCK at pre-review: "
                f"foreshadow_actions present {_registered_fs_ids} but resolved 0 foreshadow beats; "
                f"missing_ids={_missing_fs_ids}")
            return {
                "enforced": False, "missing_scenes": {},
                "blocked": True, "missing_fs_ids": _missing_fs_ids,
            }
        if not mcb_list:
            # D2 P8D: 即使无 foreshadow beat，若存在 soft beat（opening_anchor/confirmation）仍需处理
            _soft_beats = [b for b in all_beats if b.get("category") in ("opening_anchor", "confirmation")]
            if not _soft_beats:
                return {"enforced": False, "missing_scenes": {}}
            # 继续走软 beat 通道（不设 mcb_list 则跳过 foreshadow 硬逻辑）

        # D2 P8D: 提前初始化 scene_by_id / bps_by_sid，供软 beat 通道使用
        scene_by_id: dict[int, str] = {}
        for _d in scenes_data:
            _sid = int(_d.get("scene_id", 0) or 0)
            if _sid:
                scene_by_id[_sid] = _d.get("scene_text", "") or ""
        bps_by_sid = {
            int(bp.get("scene_num", 0) or 0): bp
            for bp in (task_card.get("scene_blueprints") or [])
            if isinstance(bp, dict)
        }

        # D2 P8D: 软 beat 通道（opening_anchor / confirmation）——不硬阻断，仅 soft note + 软重生
        _soft_beats = [b for b in all_beats if b.get("category") in ("opening_anchor", "confirmation")]
        if _soft_beats:
            try:
                _soft_misses_by_scene: dict[int, list[str]] = {}
                _none_conf_misses: list[str] = []
                for b in _soft_beats:
                    sn = b.get("scene_num")
                    bt = (b.get("beat_text") or "").strip()
                    if not bt:
                        continue
                    if sn is None:
                        _none_conf_misses.append(bt)
                    else:
                        _soft_misses_by_scene.setdefault(int(sn), []).append(bt)

                # 按 scene_num 归场检查
                for sid, misses in sorted(_soft_misses_by_scene.items()):
                    txt = scene_by_id.get(sid, "")
                    if not txt:
                        continue
                    ok, miss_descs = check_scene_must_cover_beats(txt, [
                        {"scene_num": sid, "beat_text": m, "category": "soft"} for m in misses
                    ], sid)
                    if not ok and miss_descs:
                        _soft_misses_by_scene[sid] = miss_descs

                # scene_num=None 的 confirmation：任一场覆盖即满足；全缺则定向目标场
                if _none_conf_misses:
                    _all_scene_texts = {sid: txt for sid, txt in scene_by_id.items() if txt}
                    _covered_any = any(
                        any(m in txt for m in _none_conf_misses)
                        for txt in _all_scene_texts.values()
                    )
                    if not _covered_any:
                        # 确定性选场：goal/conflict/location 命中确认/看/浊气/油灯关键词最高
                        _kw_score: dict[int, int] = {}
                        _confirm_kws = ["确认", "看", "浊气", "油灯", "体弱"]
                        for sid, txt in _all_scene_texts.items():
                            score = sum(txt.count(kw) for kw in _confirm_kws)
                            if score > 0:
                                _kw_score[sid] = score
                        if _kw_score:
                            _max_sc = max(_kw_score.values())
                            # 平分取末场（最高 scene_num）
                            _target_sid = max(sid for sid, sc in _kw_score.items() if sc == _max_sc)
                        else:
                            # fallback：取最大 scene_num
                            _target_sid = max(_all_scene_texts) if _all_scene_texts else None
                        if _target_sid and _target_sid in scene_by_id:
                            txt = scene_by_id[_target_sid]
                            ok_t, miss_t = check_scene_must_cover_beats(
                                txt,
                                [{"scene_num": _target_sid, "beat_text": m, "category": "soft"}
                                 for m in _none_conf_misses],
                                _target_sid,
                            )
                            if not ok_t and miss_t:
                                _soft_misses_by_scene[_target_sid] = miss_t
                        else:
                            # 无任何场，记录为 soft note，不阻塞
                            logger.info(f"ch{chapter_num} soft confirmation: no target scene found, soft note only")

                # 软重生：每场至多 1 次，绝不 hard block
                _soft_regenerated: list[int] = []
                for sid, misses in sorted(_soft_misses_by_scene.items()):
                    _key = (chapter_num, sid)
                    _soft_used = int(getattr(self, "_soft_regen_used", {}).get(_key, 0) or 0)
                    if _soft_used >= 1:
                        continue
                    _bp = bps_by_sid.get(sid, {})
                    _dir = build_mandatory_beat_directive(misses)
                    logger.info(
                        f"ch{chapter_num} SOFT regen scene{sid} (opening_anchor/confirmation): "
                        f"{misses[:2]}")
                    try:
                        _ns = self.writer.generate_scene(
                            task_card, _bp, synopsis_text,
                            previous_context=self._novel_string()[-600:] if len(self._novel_string()) > 600 else "",
                            fix_directive=_dir,
                        )
                        if _ns is not None:
                            _nt = getattr(_ns, "scene_text", "") or ""
                            if _nt:
                                append_scene(self.root, chapter_num, {
                                    "scene_id": sid, "scene_text": _nt,
                                    "hook": getattr(_ns, "hook", "") or "",
                                    "beats": list(getattr(_ns, "beats", []) or []),
                                })
                                scene_by_id[sid] = _nt
                                _soft_regenerated.append(sid)
                    except Exception as _esr:
                        logger.warning(f"ch{chapter_num} soft regen scene{sid} failed: {_esr}")
                    finally:
                        if not hasattr(self, "_soft_regen_used"):
                            self._soft_regen_used = {}
                        self._soft_regen_used[_key] = _soft_used + 1

                # 软重生后回灌 current_novel + finalize
                if _soft_regenerated:
                    try:
                        _all_sids = set(scene_by_id.keys())
                        try:
                            _existing = load_authoritative_scenes(self.root, chapter_num)
                            for _d in _existing:
                                _dsid = int(_d.get("scene_id", 0) or 0)
                                if _dsid and _dsid not in _all_sids:
                                    scene_by_id[_dsid] = _d.get("scene_text", "") or ""
                        except Exception:
                            pass
                        _fresh = sorted(scene_by_id.items(), key=lambda x: x[0])
                        _parts = [t for _, t in _fresh if t]
                        if _parts:
                            _assembled = chr(9733).join(_parts)
                            _purified = purify_novel_for_publish(_assembled, chapter_num=chapter_num)
                            _bps = task_card.get("scene_blueprints") or []
                            _total = sum(self._bpt(bp) for bp in _bps) or None
                            if _total and _total > 0:
                                _qp = self._policy
                                _purified = self._enforce_word_count(
                                    _purified,
                                    int(_total * _qp["min_ratio"]),
                                    int(_total * _qp["max_ratio"] + max(_qp.get("tolerance_chars", 0), _total * 0.02)),
                                )
                            self.current_novel = _purified
                            self._draft_novel = _purified
                            # D1：软重生后必须再 finalize 保切分
                            self._finalize_current_novel(chapter_num)
                            logger.info(f"ch{chapter_num} soft enforce: scenes={_soft_regenerated}")
                    except Exception as _es:
                        logger.warning(f"ch{chapter_num} soft enforce sync failed: {_es}")

                # 记录 soft note（不阻塞流程）
                if _soft_misses_by_scene:
                    for _sid, _ms in _soft_misses_by_scene.items():
                        logger.info(
                            f"ch{chapter_num} SOFT NOTE: scene{_sid} still misses "
                            f"opening_anchor/confirmation beats: {_ms[:2]}")

            except Exception as _ser:
                logger.warning(f"ch{chapter_num} soft beat channel failed: {_ser}")

        scene_by_id: dict[int, str] = {}
        for d in scenes_data:
            sid = int(d.get("scene_id", 0) or 0)
            if sid:
                scene_by_id[sid] = d.get("scene_text", "") or ""

        missing_scenes: dict[int, list[str]] = {}
        for sid, txt in sorted(scene_by_id.items()):
            ok, misses = check_scene_must_cover_beats(txt, mcb_list, sid)
            if not ok and misses:
                missing_scenes[sid] = misses

        if not missing_scenes:
            return {"enforced": True, "missing_scenes": {}}

        max_budget = 1
        regenerated: list[int] = []
        still_bad: list[int] = []
        bps_by_sid = {
            int(bp.get("scene_num", 0) or 0): bp
            for bp in (task_card.get("scene_blueprints") or [])
            if isinstance(bp, dict)
        }

        for sid, misses in sorted(missing_scenes.items()):
            key = (chapter_num, sid)
            used = int(getattr(self, "_scene_regen_used", {}).get(key, 0) or 0)
            if used >= max_budget:
                still_bad.append(sid)
                continue
            bp = bps_by_sid.get(sid, {})
            directive = build_mandatory_beat_directive(misses)
            logger.warning(
                f"ch{chapter_num} scene{sid} mandatory foreshadow miss -> targeted regen "
                f"(budget {used+1}/{max_budget}): {misses[:2]}")
            try:
                ns = self.writer.generate_scene(
                    task_card, bp, synopsis_text,
                    previous_context=self._novel_string()[-600:] if len(self._novel_string()) > 600 else "",
                    fix_directive=directive,
                )
                if ns is None:
                    still_bad.append(sid)
                    continue
                new_text = getattr(ns, "scene_text", "") or ""
                result = append_scene(self.root, chapter_num, {
                    "scene_id": sid, "scene_text": new_text,
                    "hook": getattr(ns, "hook", "") or "",
                    "beats": list(getattr(ns, "beats", []) or []),
                })
                scene_by_id[sid] = new_text
                regenerated.append(sid)
                used += 1
                if not hasattr(self, "_scene_regen_used"):
                    self._scene_regen_used = {}
                self._scene_regen_used[key] = used
            except Exception as _er:
                logger.warning(f"ch{chapter_num} scene{sid} mandatory regen failed: {_er}")
                still_bad.append(sid)

        if not regenerated:
            if still_bad:
                return {"enforced": True, "missing_scenes": {s: missing_scenes[s] for s in still_bad}, "blocked": True}
            return {"enforced": True, "missing_scenes": {}}

        # 回灌：重组权威 journal -> purify -> 字数 -> 写 current / current_draft
        try:
            # 使用已更新的 scene_by_id（含再生后的场景文本）重建完整成稿，
            # 不重新从磁盘加载 journal，避免测试中 patch 的 load_authoritative_scenes 被绕过。
            all_sids = set(scene_by_id.keys())
            try:
                existing = load_authoritative_scenes(self.root, chapter_num)
                for d in existing:
                    sid = int(d.get("scene_id", 0) or 0)
                    if sid and sid not in all_sids:
                        scene_by_id[sid] = d.get("scene_text", "") or ""
            except Exception:
                pass
            fresh_sorted = sorted(scene_by_id.items(), key=lambda x: x[0])
            parts = [txt for _, txt in fresh_sorted if txt]
            if parts:
                assembled = chr(9733).join(parts)
                purified = purify_novel_for_publish(assembled, chapter_num=chapter_num)
                bps = task_card.get("scene_blueprints") or []
                total = sum(self._bpt(bp) for bp in bps) or None
                if total and total > 0:
                    _qp = self._policy
                    purified = self._enforce_word_count(
                        purified,
                        int(total * _qp["min_ratio"]),
                        int(total * _qp["max_ratio"] + max(_qp.get("tolerance_chars", 0), total * 0.02)),
                    )
                self.current_novel = purified
                self._draft_novel = purified
                logger.info(f"ch{chapter_num} mandatory enforce: current synced from journal, scenes={regenerated}")
        except Exception as _e_sync:
            logger.warning(f"ch{chapter_num} mandatory enforce current sync failed: {_e_sync}")

        return {"enforced": True, "missing_scenes": {s: missing_scenes[s] for s in still_bad}, "blocked": bool(still_bad)}

    def _patch_weak_scenes(self, novel: str, review: dict, task_card: dict, synopsis: dict,
                           chapter_num: int | None = None) -> str | None:
        """场景级增量缝合：scene_id-indexed replace（THE patch path）。

        直接经 chapter_journal partials 按 scene_id 替换并以 ※ 重组；
        不做 marker 匹配（生产 miss 率 12:1，marker 优先的两段式已否决），
        不再调用 IncrementalPatcher.apply_scene_patch。
        缝合后仅调用一次只读 verify_seams 记 info 备注：永不门控、永不重抛光。
        目标场景不在 partials 中时记 warning 并返回 None（fix loop 落到 rewrite 路径）。
        """
        # Mock/测试模式下跳过增量缝合，避免确定性 mock 的调用计数被额外 LLM 调用打乱
        try:
            if (self.config.get("llm") or {}).get("use_mock"):
                return None
        except Exception:
            pass
        import re
        fix_scope = (review.get("fix_scope") or "").strip()

        def _as_int(x):
            try:
                return int(x)
            except (TypeError, ValueError):
                return None

        # E 包：优先使用 reviewer 结构化扣分归因 scene_ids，并为每个场景聚合针对性返工指令
        target_nums: set[int] = set()
        scene_dirs: dict[int, list[str]] = {}
        for iss in (review.get("issues") or []):
            raw_sids = iss.get("scene_ids") if isinstance(iss.get("scene_ids"), list) else []
            sids = [n for n in (_as_int(x) for x in raw_sids) if n is not None]
            if not sids:
                continue
            dim = iss.get("dimension", "")
            sev = iss.get("severity", "")
            desc = str(iss.get("description", "")).strip()
            sfix = str(iss.get("suggested_fix", "")).strip()
            line = f"[{dim}/{sev}] {desc}" + (f" → 修复要求：{sfix}" if sfix else "")
            for sid in sids:
                if sid >= 1:
                    target_nums.add(sid)
                    scene_dirs.setdefault(sid, []).append(line)
        # R16c P0-D: 伏笔全场景扫描置于 target_nums 早退之前——reviewer 无归因（[2,3,4]/无归因）时
        # 仍能通过全场景 journal 扫描发现缺失并填入 target_nums，避免 _patch_weak_scenes 直接 return None。
        # foreshadow-only 过滤：只补 category==foreshadow 的 beat；普通 goal/conflict/event beat 不动。
        try:
            _ch_for_patch = int(chapter_num or (task_card.get("chapter_num", 0) or 0))
            _journal_for_patch = {d["scene_id"]: d.get("scene_text", "")
                                  for d in load_authoritative_scenes(self.root, _ch_for_patch)}
        except Exception:
            _journal_for_patch = {}
        if _journal_for_patch:
            _mcb_all = task_card.get("must_cover_beats") or extract_must_cover_beats(task_card)
            _mcb_f = [b for b in _mcb_all if b.get("category") == "foreshadow"]
            if _mcb_f:
                for _sn_p in sorted(_journal_for_patch):
                    _ok_p, _miss_p = check_scene_must_cover_beats(_journal_for_patch[_sn_p], _mcb_f, _sn_p)
                    if not _ok_p and _miss_p:
                        target_nums.add(_sn_p)
                        logger.info(
                            f"ch{_ch_for_patch} scene {_sn_p}: foreshadow miss detected pre-guard "
                            f"-> added to target_nums (reviewer attribution may be absent)")
        # 兜底：旧 reviewer 未给结构化 scene_ids 时，回退 fix_scope / 文本中的场景号
        if not target_nums:
            for m in re.finditer(r"(?:场景|scene)[\s_]*(\d+)", fix_scope, flags=re.I):
                n = _as_int(m.group(1))
                if n is not None:
                    target_nums.add(n)
        if not target_nums:
            for iss in (review.get("issues") or []):
                txt = f"{iss.get('description','')} {iss.get('suggested_fix','')}"
                for m in re.finditer(r"场景\s*(\d+)", txt):
                    n = _as_int(m.group(1))
                    if n is not None:
                        target_nums.add(n)
        if not target_nums:
            return None
        # 仅处理 1-2 个场景的局部问题，避免全章重写；纯全局性问题（无场景归因）不在此处理
        if len(target_nums) > 2:
            return None
        # CC round-23 P0-2：节奏/留存/情节类低分 -> E-loop 必须"补具体事件"而非润色措辞。
        # CC round-25 P0-2：hook（钩子张力）/innovation（去套路）/style/cliff（场末悬崖感）已从
        # 逐场补事件中移除，改由整章一次性文学性重写处理——补事件既不对症文字问题、还扰动情节。
        _density_dims = {"pacing", "retention", "plot"}
        _density_sids = set()
        # CC round-25 修正（r30 实测降分）：若低分原因是"信息重复/台词重叠/拖沓"，正确修法是
        # 删减合并而非新增事件；记录这些场，fallback 指令改为去重（避免越'补'越拖、分数反降）。
        _rep_re = __import__("re").compile(r"重复|重叠|拖沓|冗余|雷同|啰嗦|反复|赘述")
        _rep_sids = set()
        for iss in (review.get("issues") or []):
            _dimv = str(iss.get("dimension", "")).lower()
            _txtv = f"{iss.get('description', '')} {iss.get('suggested_fix', '')}"
            for _x in (iss.get("scene_ids") or []):
                try:
                    _xn = int(_x)
                except (TypeError, ValueError):
                    continue
                if _dimv in _density_dims:
                    _density_sids.add(_xn)
                    if _rep_re.search(_txtv):
                        _rep_sids.add(_xn)
        try:
            from novel_engine.quality import scene_progression_gate as _spg23
            _prog_cfg = _spg23.load_progression_config(self.root)
            _metrics = getattr(self, "_scene_progression_metrics", {}) or {}
            for _sid in sorted(target_nums):
                # 有明确密度问题场景归因时只补那些场；否则（全局性节奏薄弱）所有目标场都补
                if _density_sids and _sid not in _density_sids:
                    continue
                _m = _metrics.get(_sid, {}) or {}
                _miss = _m.get("missing_new_state") or []
                _ratio = _m.get("atmosphere_ratio")
                _parts = []
                if _miss:
                    _parts.append(_spg23.new_state_directive(
                        {"detail": {"missing_new_state": [{"desc": _d} for _d in _miss]}}))
                if isinstance(_ratio, (int, float)) and _ratio >= 0.4:
                    _parts.append(_spg23.atmosphere_ratio_directive(
                        float(_ratio), float(_prog_cfg.get("scene_atmosphere_ratio", 0.55))))
                if not _parts:
                    if _sid in _rep_sids:
                        _parts.append(
                            "审查判定本场存在信息重复/台词重叠/节奏拖沓。请【删减合并】："
                            "删除反复强调同一信息的台词与近义句，把重叠对话压成一次有信息量的交锋，"
                            "用省出的篇幅补一个此前未出现的具体动作或反应（不改变既定事件与走向），"
                            "总字数基本保持不变；严禁再新增同类重复信息或再渲染气氛。")
                    else:
                        _parts.append(
                            "审查判定本场情节推进/留存薄弱。请【新增】一个此前在本章未出现过的"
                            "具体动作、对话或信息揭示（不是润色措辞、不是再渲染雾/寒/气氛），"
                            "让故事实质向前走一步；删除等量重复氛围描写，总字数基本保持不变。")
                scene_dirs.setdefault(_sid, []).extend(_parts)
        except Exception as _e23:
            logger.warning(f"Progression E-loop directive skipped: {_e23}")
        blueprints = {bp.get("scene_num"): bp for bp in (task_card.get("scene_blueprints") or [])}
        ch = int(chapter_num or (task_card.get("chapter_num", 0) or 0))
        try:
            journal = {d["scene_id"]: d.get("scene_text", "")
                       for d in load_authoritative_scenes(self.root, ch)}  # CC19 Q2
        except Exception as e:
            logger.warning(f"Incremental patch aborted: load partials failed for chapter {ch}: {e}")
            return None
        if not journal:
            logger.warning(f"Incremental patch aborted: no partials for chapter {ch}")
            return None
        for sn in sorted(target_nums):
            if sn not in journal:
                logger.warning(f"Scene {sn} not in chapter {ch} partials; skipping patch (falls through to rewrite path)")
                return None
        synopsis_text = synopsis.get("synopsis", "") if isinstance(synopsis, dict) else str(synopsis or "")
        # 单场景定点重生预算：每个 (章,场景) 最多 2 次（CC E 包），耗尽则不再重生，交由终态判定（隔离转人工，不硬凑）
        budget = getattr(self, "_scene_regen_used", None)
        if budget is None:
            budget = {}
            self._scene_regen_used = budget
        SCENE_REGEN_MAX = 2
        regenerated = False
        patched_ids: set[int] = set()
        patched_outs: dict[int, tuple] = {}
        for sn in sorted(target_nums):
            bp = blueprints.get(sn)
            if not bp:
                continue
            used = budget.get((ch, sn), 0)
            if used >= SCENE_REGEN_MAX:
                logger.warning(f"Scene {sn} chapter {ch} exhausted targeted regen budget ({SCENE_REGEN_MAX}); leave to terminal decision, no padding")
                continue
            directive = "\n".join(f"- {ln}" for ln in scene_dirs.get(sn, [])) or ""
            # CC round-18 P0-2：修订不得让场景缩水——删冗余后须补等量新正文，净字数保持±15%
            _orig_len = len(journal.get(sn, "") or "")
            directive += (
                "\n- 字数保持（硬性）：删除冗余/重复后必须在本场景内补写等量的新正文"
                "（感官细节/环境描写/角色反应，不得新增情节事件），使修订后本场景字数"
                f"不显著低于原稿（原稿约{_orig_len}字，净变化控制在±15%以内，目标不短于约"
                f"{int(_orig_len * 0.85)}字）；严禁只删不补、严禁因修订让场景大幅缩短。"
            )
            directive = directive.strip() or None
            try:
                new_scene_out = self.writer.generate_scene(task_card, bp, synopsis_text, "", "", fix_directive=directive)
                # B2：generate_scene 返回 SceneOutput；热插拔缝合的是纯叙事 scene_text
                new_scene = new_scene_out.scene_text if hasattr(new_scene_out, "scene_text") else new_scene_out
            except Exception as e:
                logger.warning(f"Incremental patch scene {sn} generation failed: {e}")
                return None
            budget[(ch, sn)] = used + 1
            journal[sn] = new_scene.strip() if isinstance(new_scene, str) else new_scene
            regenerated = True
            patched_ids.add(sn)
            patched_outs[sn] = (new_scene_out, new_scene)
        if not regenerated:
            return None
        # Journal write-back：被 patch 的 scene_id 即刻回写 journal（append 即 update，
        # load 侧 dict 后写覆盖先写），使 journal 在多轮 fix loop 中保持为真正的
        # source of truth；否则 round-2 patch 会从陈旧落盘复活原文，丢弃 round-1 增益。
        for sn in sorted(patched_ids):
            try:
                new_scene_out, new_scene = patched_outs.get(sn, (None, journal.get(sn, "")))
                beats = list(getattr(new_scene_out, "beats", []) or []) or extract_beats_fallback(new_scene)
                append_scene(self.root, ch, {"scene_id": sn, "scene_text": journal[sn],
                                             "hook": getattr(new_scene_out, "hook", ""), "beats": beats})
            except Exception as je:
                logger.warning(f"Journal write-back failed for chapter {ch} scene {sn}: {je}")
        patched = "\n\n※\n\n".join(journal[k] for k in sorted(journal))
        try:
            seam_notes = verify_seams(patched)
        except Exception:
            seam_notes = []
        if seam_notes:
            logger.info(f"Seam notes (read-only, non-blocking) for chapter {ch}: {seam_notes}")
        if patched == novel:
            return None
        logger.info(f"Incremental patch applied for scenes {sorted(target_nums)} (fix_scope={fix_scope!r})")
        return patched

    def _run_fix_loop_iteration(self, chapter_num: int, task_card: dict, synopsis: dict,
                                 current: str, current_draft: str,
                                 best_state: "_gates.BestCandidateState",
                                 iteration: int, min_ch: int, _soft_line: int,
                                 world_state: dict, result: dict,
                                 _reaction_fix_budget: int,
                                 frozen: dict) -> tuple:
        """Run one iteration of the fix loop.

        Returns (current, current_draft, best_state, _reaction_fix_budget, stop_reason).
        stop_reason is None to continue, or one of:
        'hard_block', 'pass', 'gray_band', 'early_stop', 'rewrite_disabled', 'rewrite_empty'.
        """
        # R16c P0-B: 每轮 fix 前复检必填伏笔（patch 回写 journal 后，确保重生文本进入当轮评审）
        _iter_mand = self._enforce_mandatory_beats(
            chapter_num, task_card,
            synopsis_text=synopsis.get('synopsis', '') if isinstance(synopsis, dict) else str(synopsis or ''))
        if _iter_mand.get('blocked'):
            logger.error(
                f'ch{chapter_num} MANDATORY HARD BLOCK at fix-loop iter={iteration}: '
                f'foreshadow beats unresolved: {list(_iter_mand.get("missing_fs_ids", []))}')
            self._mandatory_blocked = _iter_mand['missing_scenes']
            result['success'] = False
            result['published'] = False
            result['hard_block'] = True
            result['hard_block_reason'] = (
                f'mandatory foreshadow beats unresolved after fix-loop iter={iteration}: '
                f'{list(_iter_mand.get("missing_fs_ids", []))}')
            self._flag_for_human(
                chapter_num, 0,
                f'mandatory foreshadow beats unresolved after fix loop; isolate to draft/failed')
            return current, current_draft, best_state, _reaction_fix_budget, 'hard_block'
        self._finalize_current_novel(chapter_num)
        # D1 P8D: 无条件刷新 local current 以与 self.current_novel 同源（含 finalize 切分后文本）
        current = self.current_novel or current
        current_draft = self._draft_novel or current_draft
        # Delegate to extracted B and C methods
        current, current_draft, best_state, _reaction_fix_budget, stop_reason, staged = self._decide_fix_loop_outcome(
            chapter_num, task_card, synopsis, current, current_draft,
            best_state, iteration, min_ch, _soft_line,
            world_state, result, _reaction_fix_budget)
        if stop_reason is not None:
            return current, current_draft, best_state, _reaction_fix_budget, stop_reason
        current, current_draft, best_state, _reaction_fix_budget, stop_reason = self._dispatch_remediation(
            chapter_num, task_card, synopsis, current, current_draft,
            best_state, _reaction_fix_budget, frozen, staged, world_state)
        if stop_reason is not None:
            return current, current_draft, best_state, _reaction_fix_budget, stop_reason
        return current, current_draft, best_state, _reaction_fix_budget, None



    def _decide_fix_loop_outcome(
        self, chapter_num: int, task_card: dict, synopsis: dict,
        current: str, current_draft: str,
        best_state: "_gates.BestCandidateState",
        iteration: int, min_ch: int, _soft_line: int,
        world_state: dict, result: dict,
        _reaction_fix_budget: int,
    ) -> tuple:
        """B-type: evaluate review/gate results and decide pass/gray/early-stop.

        Returns (current, current_draft, best_state, _reaction_fix_budget, stop_reason)
        where stop_reason is 'pass', 'gray_band', 'early_stop', or None to continue.
        """
        staged = self._stage_review(chapter_num, task_card, synopsis, current, world_state)
        s = staged["score"]
        result["score"] = s
        _fix_policy = self._policy
        has_high = any(b for b, _ in (_review_issue_is_blocking(_fix_policy, iss) for iss in (staged["review"].get("issues") or [])))
        high_list = [f"{iss.get('dimension')}/{iss.get('severity')}:{iss.get('description','')[:60]}" for iss in (staged["review"].get("issues") or []) if _review_issue_is_blocking(_fix_policy, iss)[0]]
        det = self._deterministic_quality_gate(current, task_card)
        soft = det.get("soft_issues", [])
        _react_hits = det.get("reaction_hits") or []
        _react_dir = det.get("reaction_directive") or ""
        # CC round-19 P9c/P9d: reaction 命中且预算未耗尽 → 注入合成评审问题，
        # 使本轮 _patch_weak_scenes 针对命中场做定点修订，再重跑门/评审。
        _reaction_pending = bool(_react_hits) and _reaction_fix_budget > 0
        if _reaction_pending and staged.get("review"):
            _r_first = _react_hits[0]
            _r_scene_ids = _r_first.get("scene_ids", [])
            staged["review"].setdefault("issues", []).append({
                "dimension": "reaction_consistency",
                "severity": "soft",
                "scene_ids": _r_scene_ids,
                "description": f"[反应一致性] {_r_first.get('desc', '')[:80]}",
                "suggested_fix": _react_dir,
            })
            _reaction_fix_budget -= 1
            logger.info(
                f"Reaction fix injected ch{chapter_num}: scenes={_r_scene_ids} "
                f"budget_left={_reaction_fix_budget}")
        best_state = _gates.track_best_candidate(
            best_state, s, current, current_draft,
            det["passed"], has_high,
            lambda: snapshot_journal(self.root, chapter_num))
        # CC round-14 P0-3：确定性门全过+无high 时，>=88 正常过；85-87.9 灰带也放行（打标人工抽读）
        _gray_ok = (_soft_line <= s < min_ch) and not has_high and det["passed"]
        if s >= min_ch:
            if not has_high and det["passed"] and not _reaction_pending:
                if soft:
                    logger.info(f"Score {s} ≥ {min_ch} soft issues (不阻断): {soft}")
                return current, current_draft, best_state, _reaction_fix_budget, 'pass', staged
            elif _reaction_pending:
                logger.warning(f"Score {s} ≥ {min_ch} but reaction pending fix → continue")
            else:
                logger.warning(f"Score {s} ≥ {min_ch} but blocked → high={high_list} det_hard={det['issues']} det_soft={soft} → continue fix")
        elif _gray_ok and not _reaction_pending:
            logger.warning(f"Gray-band release ch{chapter_num}: score {s} in [{_soft_line},{min_ch}) + gates pass + no-high → release to novel (flagged for human spot-check) soft={soft}")
            result["gray_band_release"] = True
            result["gray_band_score"] = s
            _flag_reason = f"gray-band release {s}: deterministic gates passed, no high issue, please spot-read" + ("; reaction consistency 未消解（已尝试定点修订）" if (_react_hits and _reaction_fix_budget == 0) else "")
            self._flag_for_human(chapter_num, s, _flag_reason)
            return current, current_draft, best_state, _reaction_fix_budget, 'gray_band', staged
        elif _reaction_pending:
            logger.warning(f"Score {s} in gray band but reaction pending fix → continue")
        # 3 轮不超 best 即提前终止，避免单章空转 1.5h
        if _gates.check_fix_loop_early_stop(iteration, best_state.no_improve, best_state.best_score):
            return current, current_draft, best_state, _reaction_fix_budget, 'early_stop', staged
        # Return None to signal: continue to remediation dispatch
        return current, current_draft, best_state, _reaction_fix_budget, None, staged

    def _dispatch_remediation(
        self, chapter_num: int, task_card: dict, synopsis: dict,
        current: str, current_draft: str,
        best_state: "_gates.BestCandidateState",
        _reaction_fix_budget: int,
        frozen: dict,
        staged: dict,
        world_state: dict,
    ) -> tuple:
        """C-type: dispatch remediation (patch/rewrite) based on review outcome.

        Returns (current, current_draft, best_state, _reaction_fix_budget, stop_reason).
        """
        # 优先场景级增量缝合（基于 draft 带标记文本，避免 purified 找不到 marker）
        # CC round-25 P0-2：E-loop 逐场定点只处理可场景归因的结构性维度；
        # hook/style/innovation 三维从补丁评审中剔除，交给下方整章一次性文学性重写。
        try:
            from novel_engine.agents.literary_pass import (
                strip_literary_from_review as _strip_lit25,
                literary_weak as _lit_weak25)
            _review_for_patch = _strip_lit25(staged["review"])
        except Exception:
            _review_for_patch = staged["review"]
            _lit_weak25 = lambda _r: False  # noqa: E731
        patched_draft = self._patch_weak_scenes(current_draft, _review_for_patch, task_card, synopsis,
                                              chapter_num=chapter_num)
        _cc25_fixed = False  # CC25：本轮结构补丁或文学性重写任一落地即统一重评审
        if patched_draft is not None and patched_draft != current_draft and len(patched_draft) >= len(current_draft) // 2:
            # 缝合后需重新净化并强制字数
            patched_purified = purify_novel_for_publish(patched_draft, chapter_num=chapter_num)
            # CC round-18 P0-3：fix-loop 采纳评审建议前，对拟新增/修改文本过 canon/阶段约束硬过滤；
            # 命中 infant.json hard_block → 拒绝本 patch，改走整章重生/人工标记，禁止静默落稿。
            try:
                from novel_engine.quality.scope_gate import detect_scope_violations
                _prefilter_h = detect_scope_violations(
                    patched_purified, int(chapter_num or 0),
                    timeline_anchor=None, root=self.root).get("hard", [])
                if _prefilter_h:
                    _pf_terms = sorted({v.get("term", "") for v in _prefilter_h})
                    logger.warning(
                        f"ch{chapter_num} fix pre-filter REJECTS patch: "
                        f"canon hard_block hits={_pf_terms} -> abort patch, force full rewrite")
                    patched_draft = None  # abort: fall through to full rewrite path
                else:
                    logger.info(f"ch{chapter_num} fix pre-filter: 0 hard hits, patch accepted")
            except Exception as _pe:
                # D3：fail-closed — 异常拒绝 patch，不静默接受
                logger.error(
                    f"ch{chapter_num} fix pre-filter FATAL: {_pe} -> REJECT patch")
                patched_draft = None
            # 强制字数（用冻结目标）
            total = sum(self._bpt(bp) for bp in (frozen.get("scene_blueprints") or []))
            if total > 0:
                _pol = self._policy
                patched_purified = self._enforce_word_count(
                    patched_purified,
                    int(total * _pol["min_ratio"]),
                    int(total * _pol["max_ratio"] + max(_pol["tolerance_chars"], total * 0.02)))
            if patched_draft is not None:
                current = patched_purified
                current_draft = patched_draft
                self.current_novel = current
                self._draft_novel = current_draft
            # 注：patch 增益已在 _patch_weak_scenes 内回写 journal，
            # 此处无需额外同步（journal 即 source of truth）。
            # 更新 gate
            gate_after = self._deterministic_quality_gate(current, frozen)
            self._last_deterministic_issues = gate_after["issues"]
            _cc25_fixed = True
        # CC33: det 硬项全文级定点修复（称谓/canon 残留/泄漏/命名矛盾/跨场危机/
        # 伏笔极性——无 scene_ids 无法走 patch 的质量类与规则类 det 硬项共用此通道）
        _det_hard_now = [x for x in (getattr(self, "_last_deterministic_issues", []) or [])
                         if isinstance(x, str) and x.startswith(
                             ("[称谓]", "[canon", "[泄漏]",
                              "[命名矛盾]", "[跨场危机]", "[伏笔极性矛盾]"))]
        _det_fu = getattr(self, "_det_fulltext_used", None)
        if _det_fu is None:
            _det_fu = set()
            self._det_fulltext_used = _det_fu
        if _det_hard_now and chapter_num not in _det_fu:
            _det_res = self._det_fulltext_fix(current, _det_hard_now, chapter_num, frozen)
            if _det_res is not None:
                _det_fu.add(chapter_num)
                current, current_draft = _det_res
                self.current_novel = current
                self._draft_novel = current_draft
                gate_after = self._deterministic_quality_gate(current, frozen)
                self._last_deterministic_issues = gate_after["issues"]
                _cc25_fixed = True
                logger.info(
                    f"ch{chapter_num} det fulltext fix applied ({len(_det_hard_now)} hard items)")
        # CC round-25 P0-2：结构补丁与文学性重写在同一轮【叠加】执行——
        # 结构补丁负责 pacing/retention/plot/cliff 等可场景归因维度（已回写 journal），
        # 文学性 pass 紧接着针对 hook/style/innovation 在【最新 journal】上做整章一次性
        # 场末/措辞重写；避免"结构补丁一应用就 continue"把三维修复饿死（r30 实测该缺陷）。
        _lit_used25 = getattr(self, "_literary_pass_used", None)
        if _lit_used25 is None:
            _lit_used25 = set()
            self._literary_pass_used = _lit_used25
        if _lit_weak25(staged["review"]) and chapter_num not in _lit_used25:
            _prev_current, _prev_draft = current, current_draft
            _lit_res25 = self._cc25_literary_rewrite(
                current, staged["review"], task_card, synopsis, chapter_num)
            if _lit_res25 is not None:
                _lit_used25.add(chapter_num)
                current, current_draft = _lit_res25
                self.current_novel = current
                self._draft_novel = current_draft
                # D5：文学重写落稿前过同一 canon/阶段约束硬过滤
                try:
                    from novel_engine.quality.scope_gate import detect_scope_violations
                    _lit_h = detect_scope_violations(
                        current, int(chapter_num or 0),
                        timeline_anchor=None, root=self.root).get("hard", [])
                    if _lit_h:
                        _lit_terms = sorted({v.get("term", "") for v in _lit_h})
                        logger.warning(
                            f"ch{chapter_num} literary pass REJECTS: "
                            f"canon hard_block hits={_lit_terms} -> discard rewrite")
                        current, current_draft = _prev_current, _prev_draft
                        self.current_novel = current
                        self._draft_novel = current_draft
                    else:
                        logger.info(f"ch{chapter_num} literary pass pre-filter: 0 hard hits, accepted")
                except Exception as _le:
                    logger.warning(f"ch{chapter_num} literary pre-filter error: {_le} -> keep rewrite")
                gate_after = self._deterministic_quality_gate(current, frozen)
                self._last_deterministic_issues = gate_after["issues"]
                _cc25_fixed = True
            else:
                logger.info(f"ch{chapter_num}: CC25 literary pass produced no gain")
        if _cc25_fixed:
            return current, current_draft, best_state, _reaction_fix_budget, None
        # A类：低分整章重写升级——patch/literary 无改善且 best_score 低于 floor 时，
        # 先做整章重写（回滚世界状态 + 按任务卡重新生成 + 重评审），把稿子拉回
        # "可修复"区间继续 fix loop，而不是直接 rewrite_disabled 结束挂人工。
        _lsr_cfg = (self._policy or {}).get("low_score_rewrite") or {}
        _lsr_enabled = bool(_lsr_cfg.get("enabled", True))
        _lsr_max = int(_lsr_cfg.get("max_rounds", 2))
        _lsr_floor = float(_lsr_cfg.get(
            "floor", int(self._policy.get("fix_threshold", 60))))
        _lsr_done = int(getattr(self, "_low_score_rewrites_used", 0))
        _s_after = float(staged.get("score") or best_state.best_score)
        if (_lsr_enabled and _lsr_done < _lsr_max
                and not _cc25_fixed
                and isinstance(best_state.best_score, (int, float))
                and (best_state.best_score < _lsr_floor or _s_after < _lsr_floor)):
            logger.warning(
                f"ch{chapter_num} LOW-SCORE ESCALATION: best={best_state.best_score} "
                f"cur={_s_after} < floor={_lsr_floor} -> whole-chapter rewrite "
                f"(round {_lsr_done + 1}/{_lsr_max})")
            if self._low_score_rewrite(chapter_num, task_card, synopsis, world_state):
                self._low_score_rewrites_used = _lsr_done + 1
                # 重写成功：返回 None 让 fix loop 下一轮以新稿重新评审
                return current, current_draft, best_state, _reaction_fix_budget, None
            logger.warning(
                f"ch{chapter_num} low-score rewrite yielded no gain "
                f"(still below {_lsr_floor}) -> terminal routing")
        # CC round-7 P0-1：整章 rewrite 实测会降分（84.2→65.8），默认下线；
        # 只保留 E 场景定点重生，patch 走不通即结束修复循环 → 终态隔离/HALT。
        if not self._allow_whole_rewrite:
            logger.info(
                f"ch{chapter_num}: whole-chapter rewrite disabled "
                "(allow_whole_chapter_rewrite=false); end fix loop → terminal routing")
            return current, current_draft, best_state, _reaction_fix_budget, 'rewrite_disabled'
        rewritten = self._rewrite_weak_dimensions(current, staged["review"])
        if not rewritten or len(rewritten) < len(current) // 2:
            return current, current_draft, best_state, _reaction_fix_budget, 'rewrite_empty'
        # ── 按场景标记切分，按 scene_id 升序写入 journal，避免 draft/journal 双本本漂移
        _scenes = []
        # 优先 ※ 分割，其次 【场景N】 两种格式
        import re as _re
        parts = _re.split(r'(※|【场景\d+】)', rewritten)
        i = 1  # 跳过 parts[0]（标记前内容）
        while i + 1 < len(parts):
            marker = parts[i].strip()
            text = parts[i + 1].strip()
            m = _re.search(r'※\s*(\d+)|【场景(\d+)\]', marker)
            if m:
                sid = int(m.group(1) or m.group(2))
            else:
                existing = load_scenes(self.root, chapter_num)
                sid = (max((s["scene_id"] for s in existing), default=0) + 1) if existing else 1
            _scenes.append({"scene_id": sid, "scene_text": text})
            i += 2
        if i <= len(parts) - 1:
            trailing = parts[i].strip()
            if trailing and not _re.match(r'※|【场景\d+]', trailing):
                existing = load_scenes(self.root, chapter_num)
                sid = (max((s["scene_id"] for s in existing), default=0) + 1) if existing else len(_scenes) + 1
                _scenes.append({"scene_id": sid, "scene_text": trailing})
        from novel_engine.pipeline.chapter_journal import append_scene, load_scenes as _load_scenes
        existing_scenes = _load_scenes(self.root, chapter_num)
        _by_id = {s["scene_id"]: s for s in existing_scenes}
        for s in _scenes:
            if s["scene_id"] in _by_id:
                _by_id[s["scene_id"]]["scene_text"] = s["scene_text"]
            else:
                existing_scenes.append(s)
                _by_id[s["scene_id"]] = s
        existing_scenes.sort(key=lambda s: s["scene_id"])
        journal_path = __import__('pathlib').Path(self.root) / "chapters" / "draft" / f"chapter_{chapter_num}_partial.jsonl"
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_path, "w", encoding="utf-8") as f:
            for s in existing_scenes:
                f.write(json.dumps({"scene_id": s["scene_id"], "scene_text": s["scene_text"],
                                    "hook": s.get("hook", ""), "beats": list(s.get("beats", []) or []),
                                    "entities_json": s.get("entities_json", "")}, ensure_ascii=False) + "\n")
        ordered = existing_scenes
        current = "※".join([s["scene_text"] for s in ordered])
        current_draft = current
        self.current_novel = current
        self._draft_novel = current_draft
        self._assembled_with_seps = current
        gate_after = self._deterministic_quality_gate(current, frozen)
        self._last_deterministic_issues = gate_after["issues"]
        return current, current_draft, best_state, _reaction_fix_budget, None


    def _select_final_candidate(self, chapter_num: int, best_state: "_gates.BestCandidateState", result: dict) -> tuple:
        """Select final candidate after fix loop completes.

        Returns (best_score, best_novel, best_draft, best_journal_snapshot).
        """
        best_score = best_state.best_score
        best_novel = best_state.best_novel
        best_draft = best_state.best_draft
        _best_journal_snapshot = best_state.best_journal_snapshot
        if best_state.best_green is not None:
            gs = best_state.best_green
            if gs[0] < best_state.best_score:
                logger.warning(
                    f"ch{chapter_num}: adopt gate-green candidate {gs[0]} over "
                    f"higher-score blocked candidate {best_state.best_score}")
            best_score, best_novel, best_draft, _best_journal_snapshot = gs
        self.current_novel = best_novel
        self._draft_novel = best_draft
        result["score"] = best_score
        if restore_journal(self.root, chapter_num, _best_journal_snapshot):
            logger.info(f"ch{chapter_num}: journal atomically rolled back to best "
                        f"candidate snapshot (score={best_score})")
        else:
            logger.error(f"ch{chapter_num}: journal rollback to best snapshot FAILED "
                         f"(score={best_score}); journal may diverge from chosen text")
        return best_score, best_novel, best_draft, _best_journal_snapshot

    def _sync_journal_from_draft(self, chapter_num: int, draft_text: str) -> None:
        """Fix-loop journal write-back for full-rewrite gains.

        按 ※ 切分新 draft 并按 journal 已有 scene_id 顺序逐个回写（append 即
        update，后写覆盖先写）。切分数与 journal 场景数不一致时跳过并记 warning，
        使非结构化重写永不污染 journal。Best-effort：失败只记日志。
        """
        import re
        try:
            existing = load_authoritative_scenes(self.root, chapter_num)  # CC19 Q2
        except Exception as je:
            logger.warning(f"Journal write-back skipped (load failed, ch {chapter_num}): {je}")
            return
        if not existing:
            return
        parts = [p.strip() for p in re.split(r"\n?\s*※\s*\n?", draft_text or "") if p.strip()]
        ids = sorted(d["scene_id"] for d in existing)
        if len(parts) != len(ids):
            logger.warning(f"Journal write-back skipped: draft parts {len(parts)} vs "
                           f"journal scenes {len(ids)} (ch {chapter_num})")
            return
        for sid, text in zip(ids, parts):
            try:
                append_scene(self.root, chapter_num, {"scene_id": sid, "scene_text": text,
                                                      "hook": "", "beats": extract_beats_fallback(text)})
            except Exception as je:
                logger.warning(f"Journal write-back failed for chapter {chapter_num} scene {sid}: {je}")

    def _det_fulltext_fix(self, current: str, det_issues: list[str],
                          chapter_num: int, task_card: dict):
        """CC33: det 硬项全文级定点修复（称谓锚点/canon 残留/泄漏）。

        det 硬项（字符串，无 scene_ids）无法走 _patch_weak_scenes 的场景级路径；
        此处构造针对性的全文级修复指令，一次 LLM 调用产出锚点替换编辑
        （复用 literary_pass 的解析/应用），应用后重跑 det gate，门过才采纳
        （journal 不动）。返回 (purified, draft_with_markers) 或 None。
        """
        try:
            if (self.config.get("llm") or {}).get("use_mock"):
                return None
        except Exception:
            pass
        from novel_engine.agents import literary_pass as _lp33
        try:
            _auth33 = load_authoritative_scenes(self.root, chapter_num)
        except Exception as _e33:
            logger.warning(f"det fulltext fix abort: load scenes ch{chapter_num}: {_e33}")
            return None
        texts33 = {int(d["scene_id"]): (d.get("scene_text", "") or "") for d in _auth33}
        if not texts33:
            return None
        ordered33 = sorted(texts33)
        reqs33: list[str] = []
        for _d33 in det_issues:
            if not isinstance(_d33, str):
                continue
            if _d33.startswith("[称谓]"):
                reqs33.append(
                    "叙述中同一角色多称谓无锚点裸切（如陆烬/李淳）。请在首次出现处补充"
                    "同指锚点（如“本名…，转生后名…”），并统一主用名。")
            elif _d33.startswith("[canon"):
                _s33 = _d33.split("：", 1)[-1][:80]
                reqs33.append(
                    f"文中出现 canon 硬词/修炼样式泄漏：{_s33}。删除或改写成不含修炼意象的"
                    "合规表述（不得出现静坐吐纳/引气/气感/气流流转/暖意灌注等）。")
            elif _d33.startswith("[泄漏]"):
                _s33 = _d33.split("]", 1)[-1][:80]
                reqs33.append(f"文中出现脚手架/英文字段泄漏：{_s33}。清除该表述。")
            elif _d33.startswith("[命名矛盾]"):
                _s33 = _d33.split("]", 1)[-1][:80]
                reqs33.append(
                    f"文中存在命名/称谓矛盾：{_s33}。统一该实体（人物/地点）的用名，"
                    "全文替换为一致称呼，不得同章出现同指异名且证据成立的冲突（如"
                    "\"古井\"与\"枯井\"指同一处却状态互斥），必要时补一句同指说明。")
            elif _d33.startswith("[跨场危机]"):
                _s33 = _d33.split("]", 1)[-1][:80]
                reqs33.append(
                    f"上一场景提出的危机未在本场景承接：{_s33}。在本场景中补写对该危机"
                    "的呼应/推进（提及后续、状态变化或解决动作），不得让危机悬空无下文。")
            elif _d33.startswith("[伏笔极性矛盾]"):
                _s33 = _d33.split("]", 1)[-1][:80]
                reqs33.append(
                    f"伏笔极性矛盾（如排斥/吸引无铺垫反转）：{_s33}。补上铺垫或调整为"
                    "与既有伏笔极性一致的表述，不得无铺垫反转。")
            else:
                reqs33.append(str(_d33)[:120])
        if not reqs33:
            return None
        _body33 = "\n\n".join(f"【场景{sid}】\n{texts33[sid]}" for sid in ordered33)
        _fix_req33 = "；".join(reqs33)
        prompt33 = (
            "以下是章节正文（按场景编号给出）。请针对以下确定性硬伤做最小锚点替换：\n"
            f"{_fix_req33}\n"
            "输出 JSON：{\"edits\":[{\"scene_id\":N,\"op\":\"replace_span\","
            "anchor\":\"原文短语\",\"replacement\":\"新文本（至少8字，以句号结尾）\"}]}。\n"
            "只做必要修改，不要重写整章。")
        user33 = f"场景文本：\n{_body33}\n\n修复要求：\n{prompt33}"
        try:
            resp33 = self.polish_router.chat_completion(
                [{"role": "system",
                  "content": "你是严谨的文字编辑，只做最小定点修改，保持文风。输出严格 JSON。"},
                 {"role": "user", "content": user33}],
                temperature=0.4, max_tokens=4096)
        except Exception as _e33r:
            logger.warning(f"det fulltext fix request failed ch{chapter_num}: {_e33r}")
            return None
        raw33 = resp33.get("content", "") if isinstance(resp33, dict) else str(resp33)
        edits33 = _lp33.parse_literary_edits(raw33)
        if not edits33:
            logger.info(
                f"det fulltext fix ch{chapter_num}: no valid edits; raw head: "
                f"{(raw33 or '')[:300]!r}")
            return None
        new33, applied33, notes33 = _lp33.apply_literary_edits(texts33, edits33)
        if not applied33:
            logger.info(f"det fulltext fix ch{chapter_num}: 0 edits applied: {notes33}")
            return None
        rebuilt33 = "\n\n※\n\n".join(new33[k] for k in ordered33)
        try:
            cand33 = purify_novel_for_publish(rebuilt33, chapter_num=chapter_num)
        except Exception:
            cand33 = rebuilt33
        frozen33 = self._frozen_task_cards.get(chapter_num, task_card)
        total33 = sum(self._bpt(bp) for bp in (frozen33.get("scene_blueprints") or []))
        if total33 > 0:
            _pol33 = self._policy
            cand33 = self._enforce_word_count(
                cand33,
                int(total33 * _pol33["min_ratio"]),
                int(total33 * _pol33["max_ratio"] + max(_pol33["tolerance_chars"], total33 * 0.02)))
        gate33 = self._deterministic_quality_gate(cand33, frozen33)
        if not gate33.get("passed"):
            logger.warning(
                f"det fulltext fix ch{chapter_num} rejected: deterministic gates fail "
                f"{gate33.get('issues')} (journal untouched)")
            return None
        logger.info(
            f"det fulltext fix ch{chapter_num}: applied {len(edits33)} edits, "
            f"gate passed ({gate33.get('issues', [])[:3]})")
        return cand33, rebuilt33

    def _cc25_literary_rewrite(self, current: str, review: dict, task_card: dict,
                               synopsis: dict, chapter_num: int):
        """CC round-25 P0-2：整章一次性、聚焦 hook/style/innovation 的定向文学性重写。

        一次 LLM 调用产出少量"锚点替换"，确定性缝进对应场景；缝入后重跑全部确定性门，
        门不过整体弃用（journal 不动）。成功返回 (purified_current, draft_with_markers)，
        否则 None。下一轮 fix loop 顶部的 _stage_review 即"必须的重新评审"。
        """
        try:
            if (self.config.get("llm") or {}).get("use_mock"):
                return None
        except Exception:
            pass
        from novel_engine.agents import literary_pass as _lp25
        issues = _lp25.select_literary_issues(review)
        try:
            _auth = load_authoritative_scenes(self.root, chapter_num)
        except Exception as _e:
            logger.warning(f"CC25 literary pass abort: load scenes ch{chapter_num}: {_e}")
            return None
        texts = {int(d["scene_id"]): (d.get("scene_text", "") or "") for d in _auth}
        if not texts:
            return None
        ordered = sorted(texts)
        prompt = _lp25.build_literary_prompt(
            [(sid, texts[sid]) for sid in ordered], issues, chapter_num)
        try:
            resp = self.polish_router.chat_completion(
                [{"role": "system", "content": _lp25.SYSTEM_PROMPT},
                 {"role": "user", "content": prompt}],
                temperature=0.8, max_tokens=8192)
        except Exception as _e:
            logger.warning(f"CC25 literary pass request failed ch{chapter_num}: {_e}")
            return None
        raw = resp.get("content", "") if isinstance(resp, dict) else str(resp)
        edits = _lp25.parse_literary_edits(raw)
        if not edits:
            logger.info(
                f"CC25 literary pass ch{chapter_num}: no valid edits parsed; raw head: "
                f"{(raw or '')[:400]!r}")
            return None
        logger.info(
            f"CC25 literary pass ch{chapter_num}: parsed {len(edits)} edits "
            f"{[(e['scene_id'], e['op'], len(e['replacement'])) for e in edits]}")
        new_texts, applied, notes = _lp25.apply_literary_edits(texts, edits)
        if not applied:
            logger.info(f"CC25 literary pass ch{chapter_num}: 0 edits applied: {notes}")
            return None
        rebuilt = "\n\n※\n\n".join(new_texts[k] for k in ordered)
        try:
            cand = purify_novel_for_publish(rebuilt, chapter_num=chapter_num)
        except Exception:
            cand = rebuilt
        frozen = self._frozen_task_cards.get(chapter_num, task_card)
        total = sum(self._bpt(bp) for bp in (frozen.get("scene_blueprints") or []))
        if total > 0:
            _pol = self._policy
            cand = self._enforce_word_count(
                cand,
                int(total * _pol["min_ratio"]),
                int(total * _pol["max_ratio"] + max(_pol["tolerance_chars"], total * 0.02)))
        gate = self._deterministic_quality_gate(cand, frozen)
        if not gate.get("passed"):
            logger.warning(
                f"CC25 literary pass ch{chapter_num} rejected: deterministic gates fail "
                f"{gate.get('issues')} (journal untouched)")
            return None
        # 门过才回写 journal（只写被改场，保留为 source of truth；topup 不进 journal）
        from novel_engine.pipeline.chapter_journal import append_scene
        for sid in ordered:
            if new_texts[sid] != texts.get(sid, ""):
                try:
                    append_scene(self.root, chapter_num, {
                        "scene_id": sid, "scene_text": new_texts[sid], "hook": "",
                        "beats": extract_beats_fallback(new_texts[sid])})
                except Exception as je:
                    logger.warning(f"CC25 literary journal write-back ch{chapter_num} s{sid}: {je}")
        logger.warning(f"CC25 literary pass ch{chapter_num} applied {applied} edits: {notes}")
        return cand, rebuilt

    def _rewrite_weak_dimensions(self, novel, review):
        from novel_engine.agents.reviewer_agent import DIM_MAX
        scores = review.get("scores") or {}
        weak = [k for k, v in scores.items() if (v / DIM_MAX.get(k, 10)) < 0.85]
        issues = review.get("issues") or []
        issue_text = "\n".join(
            f"- [{i.get('dimension')}/{i.get('severity')}] {i.get('description')} → 修复建议: {i.get('suggested_fix')}"
            for i in issues
        ) or "（无具体意见）"
        prompt = (
            "你是一位资深小说润色编辑。请基于审查意见改写以下章节，"
            "重点修复偏弱维度，保持其余情节、人物与伏笔不变。\n"
            "【长度约束】修复时保持或缩短正文长度，严禁无条件增加内容；若原文字数已超标（>目标*1.35），请精简、合并段落而非扩充。\n\n"
            f"偏弱维度（需重点提升）: {weak}\n\n"
            f"审查意见:\n{issue_text}\n\n"
            f"原文章节:\n{novel}"
        )
        return call_llm(prompt, system_prompt="你是资深小说润色编辑，擅长根据审查意见精准改写章节（保持或缩短，不增字）", output_json=False, client=self.refine_router)

    def _low_score_rewrite(self, chapter_num: int, task_card: dict, synopsis: dict,
                           world_state: dict) -> bool:
        """A类：低分整章重写——回滚世界状态后，按任务卡整章重新生成并重新评审。

        与 _recover_chapter 的区别：达标线是 fix_threshold（默认60）而非
        publication_line（88）——低分重写只需把稿子拉回"可修复"区间，
        后续继续交给 fix loop；只有仍低于 fix_threshold 才视为无改善。
        """
        try:
            self._rollback_world_to(chapter_num - 1)
            novel = self.writer.generate_full_chapter(
                task_card, synopsis, None, temperature_override=0.9)
            self._journal_validated_scenes(chapter_num)
            novel = self.writer.polish_chapter(novel, task_card, llm_client=self.polish_router)
            novel = self._ensure_chinese(novel)
            # G1: A类整章重写产物先过拉丁复检——整句英文或拉丁泄漏视为无效重写（不采纳），
            # 与修复D（scene regen 出口拦截）同一检测函数，覆盖 generate_full_chapter 直出路径。
            try:
                from novel_engine.quality.latin_leak_gate import (
                    detect_latin_leak, detect_english_sentences)
                _txt_l = self._novel_string(novel)
                _leak_l = detect_latin_leak(_txt_l)
                if _leak_l["has_leak"]:
                    _sent_l = detect_english_sentences(_txt_l)
                    logger.warning(
                        f"ch{chapter_num} low-score rewrite discarded: latin leak "
                        f"tokens={_leak_l['leaked_tokens'][:6]} "
                        f"english_sentence={_sent_l['has_english_sentence']}")
                    return False
            except Exception as _e_l:
                logger.warning(f"ch{chapter_num} low-score rewrite latin check skipped: {_e_l}")
            self.current_novel = novel
            novel = self._finalize_current_novel(chapter_num)
            _st = self._stage_review(chapter_num, task_card, synopsis, novel, world_state)
            _new_score = _st["score"]
            _floor = float((self._policy or {}).get("low_score_rewrite", {}).get(
                "floor", int(self._policy.get("fix_threshold", 60))))
            logger.warning(
                f"ch{chapter_num} low-score rewrite -> score={_new_score} "
                f"verdict={_st['verdict']} (floor={_floor})")
            if isinstance(_new_score, (int, float)) and _new_score >= _floor:
                self.current_novel = novel
                self._low_score_rewritten_score = _new_score
                return True
        except Exception as e:
            logger.warning(f"ch{chapter_num} low-score rewrite failed: {e}")
        return False

    def _rollback_world_to(self, prev_chapter: int):
        history = self.session_tree.get_branch_history("main")
        target_node = None
        for node in reversed(history):
            if node.chapter_num == prev_chapter:
                target_node = node
                break
        if not target_node:
            return
        try:
            rolled = self.session_tree.rollback_to_node(target_node.node_id, branch_name="main")
            self._save_session_tree()
            if "characters" in rolled:
                self.simulator.characters = rolled["characters"]
                self.simulator._save_characters()
            if "factions" in rolled:
                self.simulator.factions = rolled["factions"]
                self.simulator._save_factions()
            if "power_system" in rolled:
                self.simulator.power_system = rolled["power_system"]
                self.simulator._save_power_system()
            self.simulator.db.import_from_json()
            self.db.import_from_json()
        except Exception as e:
            logger.error(f"Rollback failed: {e}")

    def _recover_chapter(self, chapter_num, task_card, synopsis, world_state, min_ch) -> bool:
        max_retries = self.config.get("autonomy", {}).get("chapter_regen_max_retries", 2)
        temps = [0.9, 1.0]
        for i in range(max_retries):
            try:
                self._rollback_world_to(chapter_num - 1)
                novel = self.writer.generate_full_chapter(
                    task_card, synopsis, None, temperature_override=temps[i % len(temps)])
                self._journal_validated_scenes(chapter_num)
                novel = self.writer.polish_chapter(novel, task_card, llm_client=self.polish_router)
                novel = self._ensure_chinese(novel)
                # D1 P8D: 先写入 self.current_novel，再 finalize，再以切后文本送评审
                self.current_novel = novel
                novel = self._finalize_current_novel(chapter_num)
                review = self._stage_review(chapter_num, task_card, synopsis, novel, world_state)
                if review["score"] >= min_ch:
                    self.current_novel = novel
                    self._recovered_score = review["score"]
                    return True
            except Exception as e:
                logger.warning(f"Recovery attempt {i+1} failed: {e}")
        self.defects.add(chapter_num, "below_min_ch",
                        f"regen failed after {max_retries} retries (target min_ch={min_ch})")
        return False

    def _regenerate_chapter(self, chapter_num, temperature=None):
        # Force a different sampling temperature for the retry, then re-run the whole chapter.
        if temperature is not None and temperature in self.provider_cfg.retry_temperatures:
            self._attempt_index = self.provider_cfg.retry_temperatures.index(temperature)
        self.generate_single_chapter(chapter_num)
        return self.current_novel

    def _flag_for_human(self, chapter_num, score, reason):
        import json
        from pathlib import Path
        p = Path(__file__).parent.parent / "audit" / "needs_human_review.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"queue": []}
        data.setdefault("queue", []).append({"chapter": chapter_num, "score": score, "reason": reason})
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _run_continuity_audit(self, chapter_num):
        try:
            import copy, json
            cur = copy.deepcopy(getattr(self, "current_world_state", None) or {})
            if self._last_audit_snapshot is None:
                self._last_audit_snapshot = cur
                return
            merged = {**cur, "_baseline": self._last_audit_snapshot}
            report = self.auditor.audit_full(merged)
            self._last_audit_snapshot = cur
            path = self.root / "audit" / "continuity_report.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"chapter": chapter_num, **report}, ensure_ascii=False) + "\n")
            if not report["passed"]:
                logger.warning(f"[audit] continuity issues at ch{chapter_num}: {report['realms']}")
        except Exception as e:
            logger.error(f"[audit] failed: {e}")

    def _novel_string(self, novel=None):
        """Return the plain-text content of a chapter, whether it is stored as a
        str or as a lightweight novel object exposing a `.content` attribute."""
        n = self.current_novel if novel is None else novel
        if n is None:
            return ""
        if hasattr(n, "content"):
            return n.content
        return n if isinstance(n, str) else str(n)

    def _ensure_chinese(self, novel):
        """Fail-closed Chinese hard gate: any Latin leak triggers LLM repair via outline router.

        Calls ensure_chinese_hard_gate with self.outline_router as the text fixer.
        Pass-through (no LLM call) when text is already clean.
        """
        from novel_engine.quality.chinese_gate import ensure_chinese_hard_gate
        text = self._novel_string(novel)
        fixed = ensure_chinese_hard_gate(
            text=text,
            llm_client=self.outline_router,
            root=self.root,
        )
        if hasattr(novel, "content"):
            novel.content = fixed
            return novel
        return fixed

    # Gate delegation: _forbidden_violations moved to pipeline/gates.py
    def _forbidden_violations(self, novel, review_text=""):
        return _gates._forbidden_violations(self, novel, review_text)

    def _extract_keywords(self, task_card: dict, synopsis: dict) -> list[str]:
        """从任务卡和缩写中提取关键词。"""
        keywords = []
        keywords.append(task_card.get("core_goal", "")[:30])

        for scene in task_card.get("scene_blueprints", []):
            keywords.extend(scene.get("location", "").split("·")[:2])

        synopsis_text = synopsis.get("synopsis", "")
        # 简单分词：取前20个非空字符段
        words = [w.strip() for w in synopsis_text.replace("，", " ").replace("。", " ").split() if len(w.strip()) > 1][:20]
        keywords.extend(words)

        return list(set(keywords))

    def _run_with_retry(self, chapter_num):
        MAX_CHAPTER_RETRIES = int(self.config.get("pipeline", {}).get("max_review_retries", 3))
        last_exc = None
        replan_used = False
        for attempt in range(MAX_CHAPTER_RETRIES + 1):
            self._attempt_index = attempt
            try:
                result = self.generate_single_chapter(chapter_num)
            except Exception as e:
                last_exc = e
                logger.warning(f"Chapter {chapter_num} attempt {attempt+1} failed: {e}")
                continue
            # CC round-7 P0-2：质量类整章重采不在进程内用同一任务卡反复重试，直接交 runner/看门狗
            # CC round-19/20/21：终检硬阻断 / 时序 gap / 质量留 gap 同样是确定性终局隔离，
            # runner 会 gap-continue；绝不在进程内整章重排重试（否则每章多烧一整遍 LLM）。
            if result.get("final_gate_blocked") or result.get("quality_gap_continue"):
                logger.warning(
                    f"Chapter {chapter_num} terminal quality gap "
                    f"(final_gate={bool(result.get('final_gate_blocked'))}, "
                    f"continuity/quality={bool(result.get('quality_gap_continue'))}) "
                    "-> skip in-process retries, runner gap-continues")
                return result
            if result.get("quality_replan"):
                # CC ch295/ch66：WRITE REPLAN 后直接交 runner 意味着"坏任务卡→外部重跑"
                # 一整轮往返；改为进程内重排一次（绕过 director 缓存重新规划 + 重走 writer），
                # 限 1 次，仍失败才返回（不再继续烧 retry 配额）。
                if not replan_used:
                    replan_used = True
                    os.environ["NOVEL_DIRECTOR_BYPASS_CACHE_CHAPTER"] = str(chapter_num)
                    logger.warning(
                        f"Chapter {chapter_num} in-process replan (limit 1) "
                        f"scenes={result.get('replan_scene_ids')} reason={result.get('replan_reason')}")
                    try:
                        result = self.generate_single_chapter(chapter_num)
                    except Exception as e2:
                        import traceback as _tb2
                        logger.error(f"Chapter {chapter_num} in-process replan raised: {e2}\n{_tb2.format_exc()}")
                        result = {"chapter": chapter_num, "success": False,
                                  "errors": [f"REPLAN: {e2}"]}
                    # 重排后统一出口：成功/通过即返回；仍质量重排或失败也返回（限 1 次）
                    if result.get("quality_replan"):
                        logger.warning(
                            f"Chapter {chapter_num} still quality-replan after in-process replan → return")
                        return result
                    if result.get("final_gate_blocked") or result.get("quality_gap_continue"):
                        return result
                    if result.get("success") or result.get("pending_human_review"):
                        return result
                    logger.warning(f"Chapter {chapter_num} in-process replan returned success=False")
                    return result
                logger.warning(
                    f"Chapter {chapter_num} quality-replan after replan → skip in-process retries")
                return result
            if result.get("success") or result.get("pending_human_review"):
                return result
            last_exc = RuntimeError(f"chapter {chapter_num} returned success=False")
            logger.warning(f"Chapter {chapter_num} attempt {attempt+1} returned success=False")
        logger.error(f"Chapter {chapter_num} exhausted retries: {last_exc}")
        self._record_slo_failure(chapter_num, str(last_exc))
        raise RuntimeError(f"Chapter {chapter_num} hard-failed after retries")

    def _attempt_temperature(self, attempt_index):
        temps = self.provider_cfg.retry_temperatures
        return temps[attempt_index % len(temps)]

    def _record_slo_failure(self, chapter_num, reason):
        import json
        from pathlib import Path
        p = Path(__file__).parent.parent / "audit" / "slo_report.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"failures": []}
        data.setdefault("failures", []).append({"chapter": chapter_num, "reason": reason})
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _run_chapters_gated(self, start_chapter, total_chapters, chapter_fn, on_done=None):
        """Run chapter generation with optional pipeline overlap.

        When model_router.max_parallel_chapters <= 1 chapters run strictly
        sequentially. Otherwise up to max_parallel_chapters chapters run concurrently
        (gated by the executor); on_done(chapter_num, result) is invoked as each chapter
        completes and may return False to stop the run. Results are returned in chapter
        order. NOTE: the orchestrator shares per-chapter mutable state, so raise
        max_parallel_chapters only if you accept the (guarded) concurrency trade-off.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        para = max(1, int(self.config.get("model_router", {}).get("max_parallel_chapters", 1)))
        if para <= 1:
            results = []
            for ch in range(start_chapter, total_chapters + 1):
                res = chapter_fn(ch)
                results.append(res)
                if on_done is not None and on_done(ch, res) is False:
                    break
            return results

        with ThreadPoolExecutor(max_workers=para) as ex:
            futures = {ex.submit(chapter_fn, ch): ch for ch in range(start_chapter, total_chapters + 1)}
            results_map = {}
            for fut in as_completed(futures):
                ch = futures[fut]
                res = fut.result()
                results_map[ch] = res
                if on_done is not None and on_done(ch, res) is False:
                    break
        return [results_map[c] for c in range(start_chapter, total_chapters + 1) if c in results_map]

    def run_mini_test(self, num_chapters: int = 10) -> list[dict]:
        """运行 Mini 测试：生成 N 章。"""
        self.state_machine.current_chapter = 0

        def _chapter(i):
            self.state_machine.current_chapter = i
            return self._run_with_retry(i)

        def _done(i, result):
            # Task 8: continuity audit cadence (non-blocking, autonomous)
            if i % self.audit_interval == 0:
                self._run_continuity_audit(i)
            if not result["success"]:
                logger.error(f"Mini test FAILED at chapter {i}")
                return False
            return True

        return self._run_chapters_gated(1, num_chapters, _chapter, _done)

    # ====== Task 10: remediation loop + terminal termination + production report ======

    def run_remediation_and_finalize(self, task_card, synopsis, world_state, total_chapters=None):
        import json
        max_rounds = self.config.get("autonomy", {}).get("remediation_max_rounds", 1)
        per_ch_max = self.config.get("autonomy", {}).get("remediation_per_chapter_retries", 2)
        pub_line = int(load_quality_policy(self.root)["publication_line"])
        # Q6=A: exactly one bounded remediation round
        for _ in range(max_rounds):
            pending = [d for d in self.defects.pending()]
            if not pending:
                break
            for d in pending:
                ch = d["chapter"]
                for _i in range(per_ch_max):
                    res = self.generate_single_chapter(ch)
                    if res.get("success"):
                        self.defects.mark_known(ch)
                        from novel_engine.pipeline.production_runner import save_resume_state; save_resume_state(self.root, [ch])
                        break
        pending = self.defects.pending()
        final_ok = (len(pending) == 0)
        report = {
            "status": "published" if final_ok else "needs_human",
            "total_chapters": total_chapters,
            "pending_defects": len(pending),
            "known_defects": len(self.defects.all()),
            "defects": pending,
        }
        try:
            if final_ok and getattr(self, "current_novel", None) is not None:
                fin = self.reviewer.review_chapter(
                    0, task_card, synopsis, self._novel_string(), world_state or {})
                fs = fin.get("score") or fin.get("total_score")
                report["final_score"] = fs
                if fs is not None and fs < pub_line:
                    report["status"] = "needs_human"
                    report["reason"] = f"final score {fs} < publication_line {pub_line}"
        except Exception as e:
            report["final_review_error"] = str(e)
        path = self.root / "audit" / "production_report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    def run_full_unattended(self, total_chapters, task_card=None, synopsis=None,
                            world_state=None, start_chapter=1):
        tc = task_card if task_card is not None else self._load_task_card_file()
        sy = synopsis if synopsis is not None else self._load_synopsis_file()
        ws = world_state if world_state is not None else self._load_world_state_file()

        def _done(ch, res):
            if res.get("success"):
                from novel_engine.pipeline.production_runner import save_resume_state; save_resume_state(self.root, [ch])
            return True

        self._run_chapters_gated(start_chapter, total_chapters,
                                 self.generate_single_chapter, _done)
        return self.run_remediation_and_finalize(tc, sy, ws, total_chapters)

    def _load_task_card_file(self):
        p = self.root / "task_card.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def _load_synopsis_file(self):
        p = self.root / "synopsis.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def _load_world_state_file(self):
        import json
        p = self.root / "world_state.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def run_medium_test(self, num_chapters: int = 70) -> list[dict]:
        """运行中等规模测试：生成 N 章（含滑动窗口审查）。"""

        def _done(i, result):
            # Task 8: continuity audit cadence (non-blocking, autonomous)
            if i % self.audit_interval == 0:
                self._run_continuity_audit(i)

            # 每50章触发滑动窗口审查
            if self.memory.should_trigger_sliding_window(i, window_size=50):
                logger.info(f"Triggering sliding window review at chapter {i}")
                self._run_sliding_window_review(i)

            if not result["success"]:
                logger.error(f"Medium test FAILED at chapter {i}")
                return False
            return True

        return self._run_chapters_gated(1, num_chapters, self._run_with_retry, _done)

    def _run_sliding_window_review(self, current_chapter: int):
        """执行滑动窗口审查。"""
        window_start = current_chapter - 49

        # 读取每章单章审查结果（含评分/亮点/问题）
        review_file = self.root / "audit" / "per_chapter_reviews.json"
        all_reviews = []
        if review_file.exists():
            try:
                all_reviews = json.loads(review_file.read_text(encoding="utf-8")).get("reviews", [])
            except (json.JSONDecodeError, ValueError):
                all_reviews = []

        window_reviews = [r for r in all_reviews if window_start <= r.get("chapter_num", 0) <= current_chapter]

        # 同时收集缩写文本，供节奏/重复/伏笔密度分析
        synopsis_texts = []
        for ch in range(window_start, current_chapter + 1):
            synopsis_path = self.root / "chapters" / "synopsis" / f"chapter_{ch}.txt"
            if synopsis_path.exists():
                synopsis_texts.append({
                    "chapter": ch,
                    "synopsis": synopsis_path.read_text(encoding="utf-8"),
                })

        window_review = {
            "window_start": window_start,
            "window_end": current_chapter,
            "issues": {
                "pacing": [],
                "repetition": [],
                "foreshadow_density": [],
                "character_development": []
            },
            "summary": "滑动窗口审查完成。"
        }

        # 无窗口审查数据时保持 0，避免后续 NameError
        constraint_adjustments = 0

        if window_reviews or synopsis_texts:
            # 调用 LLM 生成评估对象
            prompt = f"""请分析第 {window_start} 到第 {current_chapter} 章的写作情况。
我为你提供这两部分输入：
1. 这 50 章的历史审查反馈（包含评分、亮点、发现的问题等）：
{json.dumps(window_reviews, ensure_ascii=False, indent=2)[:8000]}

2. 这 50 章的章节缩写：
{json.dumps(synopsis_texts, ensure_ascii=False, indent=2)[:8000]}

请从以下四个方面全面评估这 50 章：
- 节奏控制（pacing）：是否存在连续无情节波动的平淡章节？
- 套路重复（repetition）：是否频繁出现相同的剧情套路或战斗情节？
- 伏笔密度（foreshadow_density）：clue_plan 的执行密度是否合理，是否太高或太低？
- 人物成长（character_development）：主角及重要配角是否符合成长曲线？

请输出以下格式的 JSON（注意 issues 字典中的 key 必须完全一致）：
{{
  "window_start": {window_start},
  "window_end": {current_chapter},
  "summary": "50章整体评估概述...",
  "issues": {{
    "pacing": [
      {{
        "description": "节奏过于平淡或紧凑的具体描述",
        "severity": "high|medium|low"
      }}
    ],
    "repetition": [
      {{
        "description": "套路重复的具体描述",
        "severity": "high|medium|low"
      }}
    ],
    "foreshadow_density": [
      {{
        "description": "伏笔执行问题的描述",
        "severity": "high|medium|low"
      }}
    ],
    "character_development": [
      {{
        "description": "人物设定或成长不一致/缓慢的描述",
        "severity": "high|medium|low"
      }}
    ]
  }}
}}"""

            system_prompt = "你是一位资深的网络小说总编辑，擅长进行多章节长线结构和质量审查。"
            try:
                window_review = call_llm(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    client=self.llm,
                    output_json=True
                )
                logger.info(f"Sliding window review generated successfully via LLM for chapters {window_start}-{current_chapter}")
            except Exception as e:
                logger.error(f"Failed to generate sliding window review via LLM: {e}. Falling back to default.")

            # Count constraint adjustments from sliding window issues
            issues_dict = window_review.get("issues", {})
            for issue_key in ["pacing", "repetition", "foreshadow_density", "character_development"]:
                issues = issues_dict.get(issue_key, [])
                constraint_adjustments += len(issues)

            self.memory.save_sliding_window_review(window_review)

            # Refresh quality memory with real review data
            self.memory.refresh_quality_memory(window_reviews, current_chapter)

            # Clean up expired quality memory entries
            self.memory.cleanup_quality_memory(current_chapter)

            logger.info(f"Sliding window review completed for chapters {window_start}-{current_chapter}")
            logger.info(f"Constraint adjustments from sliding window: {constraint_adjustments}")

        # Track constraint adjustments in cost tracker
        self.cost_tracker["sliding_window_constraint_adjustments"] += constraint_adjustments

    def get_cost_sandbox_report(self, num_chapters: int) -> dict:
        """Generate cost sandbox report from accumulated cost data.

        Returns a report with total token usage, per-chapter averages,
        sliding window constraint adjustments, and cost estimates for full production.
        """
        tracker = self.cost_tracker
        total_calls = tracker["api_calls"]
        total_prompt = tracker["total_prompt_tokens"]
        total_completion = tracker["total_completion_tokens"]
        total_reasoning = tracker["total_reasoning_tokens"]
        total_all = tracker["total_tokens"]

        avg_prompt = total_prompt // num_chapters if num_chapters else 0
        avg_completion = total_completion // num_chapters if num_chapters else 0
        avg_reasoning = total_reasoning // num_chapters if num_chapters else 0
        avg_total = total_all // num_chapters if num_chapters else 0
        avg_calls = total_calls / num_chapters if num_chapters else 0

        production_chapters = 3000
        config_path = self.root / "config" / "runtime_config.json"
        if config_path.exists():
            try:
                runtime_cfg = json.loads(config_path.read_text(encoding="utf-8"))
                production_chapters = runtime_cfg.get("pipeline", {}).get("total_chapters", 3000)
            except (json.JSONDecodeError, ValueError):
                pass
        est_prompt = avg_prompt * production_chapters
        est_completion = avg_completion * production_chapters
        est_reasoning = avg_reasoning * production_chapters
        est_total = avg_total * production_chapters
        est_calls = avg_calls * production_chapters

        sandbox_cfg_path = self.root / "config" / "cost_sandbox.json"
        pricing = {"input_per_1k": 0.0001, "output_per_1k": 0.0002}
        if sandbox_cfg_path.exists():
            try:
                sandbox_cfg = json.loads(sandbox_cfg_path.read_text(encoding="utf-8"))
                p = sandbox_cfg.get("currency_conversion", {}).get("api_pricing", {})
                pricing["input_per_1k"] = p.get("input_per_1k_tokens", p.get("input_per_1k", 0.0001))
                pricing["output_per_1k"] = p.get("output_per_1k_tokens", p.get("output_per_1k", 0.0002))
            except (json.JSONDecodeError, ValueError):
                pass

        input_tokens = total_prompt + total_reasoning
        output_tokens = total_completion
        actual_cost_usd = (input_tokens / 1000 * pricing["input_per_1k"]) + \
                          (output_tokens / 1000 * pricing["output_per_1k"])

        est_production_input = est_prompt + est_reasoning
        est_production_output = est_completion
        est_production_cost_usd = (est_production_input / 1000 * pricing["input_per_1k"]) + \
                                   (est_production_output / 1000 * pricing["output_per_1k"])

        budget_cfg_path = self.root / "config" / "cost_sandbox.json"
        budget = {"total": 500.0, "full_production_max": 400.0}
        if budget_cfg_path.exists():
            try:
                bc = json.loads(budget_cfg_path.read_text(encoding="utf-8"))
                b = bc.get("budget", {})
                budget["total"] = b.get("total", 500.0)
                budget["full_production_max"] = b.get("full_production_max", 400.0)
            except (json.JSONDecodeError, ValueError):
                pass

        budget_pct = (actual_cost_usd / budget["total"] * 100) if budget["total"] else 0
        production_budget_pct = (est_production_cost_usd / budget["full_production_max"] * 100) if budget["full_production_max"] else 0

        sw_adjustments = tracker.get("sliding_window_constraint_adjustments", 0)

        return {
            "test_type": "cost_sandbox",
            "num_chapters_tested": num_chapters,
            "total_api_calls": total_calls,
            "avg_calls_per_chapter": round(avg_calls, 2),
            "token_usage": {
                "total_prompt_tokens": total_prompt,
                "total_completion_tokens": total_completion,
                "total_reasoning_tokens": total_reasoning,
                "total_tokens": total_all,
                "avg_prompt_per_chapter": avg_prompt,
                "avg_completion_per_chapter": avg_completion,
                "avg_reasoning_per_chapter": avg_reasoning,
                "avg_total_per_chapter": avg_total,
            },
            "cost_usd": {
                "sandbox_test_actual": round(actual_cost_usd, 6),
                "estimated_full_production": round(est_production_cost_usd, 2),
                "pricing_input_per_1k": pricing["input_per_1k"],
                "pricing_output_per_1k": pricing["output_per_1k"],
            },
            "budget": {
                "total_budget_usd": budget["total"],
                "full_production_max_usd": budget["full_production_max"],
                "sandbox_test_budget_pct": round(budget_pct, 4),
                "production_budget_pct": round(production_budget_pct, 2),
                "within_budget": est_production_cost_usd <= budget["full_production_max"],
            },
            "sliding_window": {
                "constraint_adjustment_count": sw_adjustments,
                "adjustments_per_50_chapters": sw_adjustments,
            },
            "per_chapter_token_breakdown": tracker.get("per_chapter_costs", {}),
        }

    def close(self):
        """Close all StateDB connections and release resources."""
        for name, get_conn in [
            ("self.db", lambda: self.db),
            ("self.simulator.db", lambda: self.simulator.db),
            ("self.director.db", lambda: self.director.db),
            ("self.director.simulator.db", lambda: self.director.simulator.db),
        ]:
            try:
                get_conn().close()
            except Exception as e:
                logger.warning(f"Error closing {name}: {e}")
