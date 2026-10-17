"""
状态机：orchestrate 整个章节生成流水线。
INIT → PLANNING → WORLD_SIM → DIRECTING → SYNOPSIS → WRITE_SCENE_1..N → POLISH → REVIEW →
  ├── PASS → COMMIT → NEXT_CHAPTER
  └── FAIL → FIX（≤3次）→ REVIEW
每个状态转换写入 checkpoint。
"""
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from novel_engine.core.checkpoint import CheckpointManager, create_commit_transaction

logger = logging.getLogger(__name__)


class ChapterPhase(str, Enum):
    INIT = "INIT"
    PLANNING = "PLANNING"
    WORLD_SIM = "WORLD_SIM"
    DIRECTING = "DIRECTING"
    SYNOPSIS = "SYNOPSIS"
    WRITE_SCENE = "WRITE_SCENE"
    POLISH = "POLISH"
    REVIEW = "REVIEW"
    PASS = "PASS"
    FAIL = "FAIL"
    COMMIT = "COMMIT"
    NEXT_CHAPTER = "NEXT_CHAPTER"
    NEEDS_HUMAN = "NEEDS_HUMAN"


class StateMachine:
    """章节生成状态机。"""

    VALID_TRANSITIONS = {
        ChapterPhase.INIT: [ChapterPhase.PLANNING],
        ChapterPhase.PLANNING: [ChapterPhase.WORLD_SIM],
        ChapterPhase.WORLD_SIM: [ChapterPhase.DIRECTING],
        ChapterPhase.DIRECTING: [ChapterPhase.SYNOPSIS, ChapterPhase.NEEDS_HUMAN],
        ChapterPhase.SYNOPSIS: [ChapterPhase.WRITE_SCENE],
        ChapterPhase.WRITE_SCENE: [ChapterPhase.POLISH, ChapterPhase.REVIEW],
        ChapterPhase.POLISH: [ChapterPhase.REVIEW],
        ChapterPhase.REVIEW: [ChapterPhase.PASS, ChapterPhase.FAIL, ChapterPhase.COMMIT],
        ChapterPhase.COMMIT: [ChapterPhase.NEXT_CHAPTER],
        ChapterPhase.PASS: [ChapterPhase.COMMIT],
        ChapterPhase.FAIL: [ChapterPhase.DIRECTING, ChapterPhase.NEEDS_HUMAN],
        ChapterPhase.NEEDS_HUMAN: [],
    }

    def __init__(self, project_root: str | Path = None):
        self.root = Path(project_root or Path(__file__).parent.parent)
        self.checkpoint_mgr = CheckpointManager(self.root)
        self.current_chapter = 0
        self.current_phase = ChapterPhase.INIT
        self.retry_count = 0
        self.max_retries = 3
        self.state_log: dict[str, dict] = {}
        # 从 runtime_config.json 读取总章数，作为唯一数据源（volumes.json 规划到 3800）
        self.total_target = self._load_total_target()
        self._load_state()
        self._ensure_recovery_policy()

    def _load_total_target(self) -> int:
        """从 runtime_config.json 的 pipeline.total_chapters 读取目标总章数。"""
        config_path = self.root / "config" / "runtime_config.json"
        try:
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                return int(cfg.get("pipeline", {}).get("total_chapters", 3000))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
        return 3000

    def _ensure_recovery_policy(self):
        """确保 recovery_policy.json 存在，缺失时创建默认策略。"""
        rp_path = self._recovery_policy_file()
        if rp_path.exists():
            return
        default_policy = {
            "json_parse_error": {
                "action": "retry_same_prompt",
                "max_retries": 3,
                "on_exhausted": "fallback_to_manual_flag"
            },
            "api_disconnect": {
                "action": "wait_and_retry",
                "wait_seconds": 30,
                "max_retries": 5
            },
            "context_overflow": {
                "action": "reduce_memory_window",
                "detail": "临时压缩窗口至40章重试",
                "max_retries": 1
            },
            "world_state_conflict": {
                "action": "rollback_last_commit",
                "detail": "回滚到上一次COMMIT状态，该章标记needs_human"
            },
            "file_write_corruption": {
                "action": "restore_from_checkpoint",
                "detail": "校验文件hash，不匹配则从最近完整checkpoint恢复并重做缺失章节",
                "verify_hash": True
            },
            "unknown_error": {
                "action": "pause_and_log",
                "detail": "记录完整上下文到logs/，暂停等待人工介入"
            }
        }
        rp_path.write_text(json.dumps(default_policy, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"Created default recovery policy at {rp_path}")

    def _state_file(self) -> Path:
        return self.root / "runtime" / "state_machine.json"

    def _recovery_policy_file(self) -> Path:
        return self.root / "runtime" / "recovery_policy.json"

    def _load_state(self):
        """从文件加载状态机状态。"""
        state_file = self._state_file()
        if state_file.exists():
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.current_chapter = data.get("current_chapter", 0)
            self.current_phase = ChapterPhase(data.get("phase", "INIT"))
            self.state_log = data.get("states", {})
            logger.info(f"Loaded state: chapter={self.current_chapter}, phase={self.current_phase.value}")

    def _save_state(self):
        """持久化状态机状态。"""
        data = {
            "current_chapter": self.current_chapter,
            "total_target": self.total_target,
            "phase": self.current_phase.value,
            "states": self.state_log,
            "next_state": self._get_next_possible_states()[0].value if self._get_next_possible_states() else None,
        }
        self._state_file().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _get_next_possible_states(self) -> list[ChapterPhase]:
        return self.VALID_TRANSITIONS.get(self.current_phase, [])

    def transition(self, new_phase: ChapterPhase) -> bool:
        """执行状态转换。"""
        allowed = self._get_next_possible_states()
        if new_phase not in allowed:
            logger.error(
                f"无效状态转换: {self.current_phase.value} → {new_phase.value} "
                f"(允许: {[s.value for s in allowed]})"
            )
            return False

        old_phase = self.current_phase
        self.current_phase = new_phase
        timestamp = datetime.now(timezone.utc).isoformat()

        self.state_log[str(self.current_chapter)] = {
            "phase": new_phase.value,
            "timestamp": timestamp,
            "retry_count": self.retry_count,
            "error": None,
            "commit_hash": None,
        }

        self._save_state()
        logger.info(f"State transition: {old_phase.value} → {new_phase.value} (chapter {self.current_chapter})")
        return True

    def record_error(self, error: str):
        """记录错误信息。"""
        if str(self.current_chapter) in self.state_log:
            self.state_log[str(self.current_chapter)]["error"] = error
        self._save_state()

    def mark_needs_human(self, reason: str):
        """标记需要人工介入。"""
        self.transition(ChapterPhase.NEEDS_HUMAN)
        self.record_error(reason)
        logger.critical(f"CHAPTER {self.current_chapter} NEEDS HUMAN: {reason}")

    def advance_chapter(self):
        """推进到下一章。"""
        self.current_chapter += 1
        self.retry_count = 0
        self._save_state()
        logger.info(f"Advanced to chapter {self.current_chapter}")

    def increment_retry(self):
        """增加重试计数。"""
        self.retry_count += 1
        logger.warning(f"Retry count: {self.retry_count}/{self.max_retries}")

    def can_retry(self) -> bool:
        """检查是否还能重试。"""
        return self.retry_count < self.max_retries

    def get_recovery_action(self, error_type: str) -> dict:
        """根据错误类型获取恢复策略。"""
        recovery_file = self._recovery_policy_file()
        if recovery_file.exists():
            with open(recovery_file, "r", encoding="utf-8") as f:
                policy = json.load(f)
            return policy.get(error_type, policy.get("unknown_error", {}))
        return {"action": "pause_and_log", "detail": "未配置恢复策略"}

    def handle_failure(self, error_type: str, error_msg: str):
        """处理失败：根据 recovery_policy 分流。"""
        self.record_error(error_msg)
        action = self.get_recovery_action(error_type)

        if action["action"] == "rollback_last_commit":
            latest = self.checkpoint_mgr.get_latest_checkpoint()
            if latest and latest["chapter"] > 0:
                logger.info(f"Rolling back to chapter {latest['chapter']}")
                self.current_chapter = latest["chapter"]
                self.transition(ChapterPhase.NEEDS_HUMAN)
                return

        elif action["action"] == "restore_from_checkpoint":
            if self.checkpoint_mgr.restore_from_checkpoint(self.current_chapter):
                logger.info(f"Restored chapter {self.current_chapter} from checkpoint")
                return

        elif action["action"] == "pause_and_log":
            self.mark_needs_human(f"{error_type}: {error_msg}")
            return

        # 默认：标记人工介入
        self.mark_needs_human(f"未处理的恢复动作: {error_type} - {error_msg}")

    def commit_chapter(
        self,
        chapter_num: int,
        novel_content: str,
        synopsis_content: str,
        outline_content: str,
        world_state_snapshot: dict,
    ):
        """提交章节：写入文件 + checkpoint。"""
        checkpoint = create_commit_transaction(
            manager=self.checkpoint_mgr,
            project_root=self.root,
            chapter=chapter_num,
            novel_content=novel_content,
            synopsis_content=synopsis_content,
            outline_content=outline_content,
            world_state_snapshot=world_state_snapshot,
        )

        # 更新 state_log 中的 commit_hash
        if str(chapter_num) in self.state_log:
            self.state_log[str(chapter_num)]["commit_hash"] = checkpoint["world_state_hash"]
        self._save_state()

        self.transition(ChapterPhase.COMMIT)
        return checkpoint

    def run_pipeline(self, pipeline_executor) -> dict:
        """
        运行完整流水线。由外部传入 executor 来执行各阶段的具体LLM调用。
        状态机只负责流程控制和状态管理。

        pipeline_executor 是一个 callable，接收 (phase, chapter_data) 返回结果。
        """
        result = {
            "chapter": self.current_chapter,
            "success": False,
            "score": None,
            "errors": [],
        }

        # 初始化状态
        self.transition(ChapterPhase.INIT)
        self.transition(ChapterPhase.PLANNING)

        phases = [
            ChapterPhase.WORLD_SIM,
            ChapterPhase.DIRECTING,
            ChapterPhase.SYNOPSIS,
            ChapterPhase.WRITE_SCENE,
            ChapterPhase.POLISH,
        ]

        for phase in phases:
            try:
                self.transition(phase)
                phase_result = pipeline_executor(phase, {
                    "chapter": self.current_chapter,
                    "retry_count": self.retry_count,
                })
            except Exception as e:
                result["errors"].append(f"{phase.value}: {str(e)}")
                self.handle_failure(type(e).__name__, str(e))
                break

        if not result["success"]:
            return result

        # 审查阶段：根据分数决定PASS/FAIL
        try:
            self.transition(ChapterPhase.REVIEW)
            review_result = pipeline_executor(ChapterPhase.REVIEW, {
                "chapter": self.current_chapter,
                "retry_count": self.retry_count,
            })
            score = review_result.get("score", 0)
            result["score"] = score

            if score >= 85:
                # PASS → COMMIT → NEXT_CHAPTER
                self.transition(ChapterPhase.PASS)
                result["success"] = True
                self.commit_chapter(
                    chapter_num=self.current_chapter,
                    novel_content=pipeline_executor.last_novel,
                    synopsis_content=pipeline_executor.last_synopsis,
                    outline_content=pipeline_executor.last_outline,
                    world_state_snapshot=pipeline_executor.last_world_state,
                )
                self.advance_chapter()
            elif score >= 60:
                # FAIL（局部修复）
                self.transition(ChapterPhase.FAIL)
                result["errors"].append(f"局部修复: score={score}")
                if self.can_retry():
                    self.increment_retry()
                    # 回退到DIRECTING重新生成
                    self.current_phase = ChapterPhase.DIRECTING
                    return self.run_pipeline(pipeline_executor)
                else:
                    self.handle_failure("review_failure_exhausted", f"连续{self.max_retries}次低于60分")
            else:
                # FAIL（全量回退）
                self.transition(ChapterPhase.FAIL)
                result["errors"].append(f"全量回退: score={score}")
                if self.can_retry():
                    self.increment_retry()
                    # 回退到PLANNING重新开始
                    self.current_phase = ChapterPhase.PLANNING
                    return self.run_pipeline(pipeline_executor)
                else:
                    self.handle_failure("review_failure_exhausted", f"连续{self.max_retries}次低于60分")
        except Exception as e:
            result["errors"].append(f"REVIEW: {str(e)}")
            self.handle_failure(type(e).__name__, str(e))

        return result
