"""
Checkpoint + Commit 事务模块。
每次章节提交时计算文件hash并写入checkpoint，支持从最近完整checkpoint恢复。
"""
import os
import json
import hashlib
import logging
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

CHECKPOINT_FILE = "runtime/checkpoint.json"

def novel_chapter_path(root, chapter: int) -> Path:
    """Return the path to a novel chapter .txt file.

    Single source of truth for the chapters/novel/ path layout.
    Callers that need backup/restore semantics should still build
    the full path directly; this helper only constructs the target.
    """
    return Path(root) / "chapters" / "novel" / f"chapter_{chapter}.txt"



def _sha256_file(filepath: str | Path) -> str:
    """计算文件SHA256 hash（统一换行符避免平台差异）。"""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        content = f.read().replace(b"\r\n", b"\n")
        h.update(content)
    return h.hexdigest()


def _sha256_string(text: str) -> str:
    """计算字符串SHA256 hash。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CheckpointManager:
    """管理checkpoint写入、读取、恢复。"""

    def __init__(self, project_root: str | Path = None):
        self.root = Path(project_root or Path(__file__).parent.parent)
        self.checkpoint_path = self.root / CHECKPOINT_FILE

    def load(self) -> dict:
        """加载checkpoint文件。"""
        if not self.checkpoint_path.exists():
            return {"checkpoints": [], "last_complete": None}
        with open(self.checkpoint_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save(self, data: dict):
        """保存checkpoint文件（原子写入）。"""
        tmp_path = self.checkpoint_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.checkpoint_path)

    def create_checkpoint(
        self,
        chapter: int,
        novel_content: str,
        synopsis_content: str,
        outline_content: str,
        world_state_snapshot: dict,
    ) -> dict:
        """
        为指定章节创建完整checkpoint。
        计算所有产出文件的hash，记录到checkpoint。
        """
        # Normalize CRLF→LF to match _sha256_file's verification path (platform-safe)
        novel_content_norm = novel_content.replace("\r\n", "\n")
        synopsis_content_norm = synopsis_content.replace("\r\n", "\n")
        outline_content_norm = outline_content.replace("\r\n", "\n")
        novel_hash = _sha256_string(novel_content_norm)
        synopsis_hash = _sha256_string(synopsis_content_norm)
        outline_hash = _sha256_string(outline_content_norm)
        world_state_hash = _sha256_string(json.dumps(world_state_snapshot, ensure_ascii=False, sort_keys=True))

        checkpoint_entry = {
            "chapter": chapter,
            "novel_hash": novel_hash,
            "synopsis_hash": synopsis_hash,
            "outline_hash": outline_hash,
            "world_state_hash": world_state_hash,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "complete": True,
        }

        cp_data = self.load()
        cp_data["checkpoints"].append(checkpoint_entry)
        cp_data["last_complete"] = chapter
        self.save(cp_data)

        logger.info(f"Checkpoint created for chapter {chapter}")
        return checkpoint_entry

    def create_draft_checkpoint(
        self,
        chapter: int,
        draft_content: str,
    ) -> dict:
        """
        为 force-best 章节创建轻量 checkpoint 条目。
        只记录 draft 文件 hash，不写 novel/synopsis/outline。
        complete=True 使 run_volume --resume 跳过该章。
        verify_integrity 对 force_best_draft 条目检查 draft/ 而非 novel/。
        """
        draft_path = self.root / "chapters" / "draft" / f"chapter_{chapter}.txt"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(draft_content, encoding="utf-8")
        draft_hash = _sha256_file(draft_path)

        checkpoint_entry = {
            "chapter": chapter,
            "novel_hash": draft_hash,
            "synopsis_hash": "",
            "outline_hash": "",
            "world_state_hash": "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "complete": True,
            "mode": "force_best_draft",
        }

        cp_data = self.load()
        cp_data["checkpoints"].append(checkpoint_entry)
        cp_data["last_complete"] = chapter
        self.save(cp_data)

        logger.info(f"Draft checkpoint created for chapter {chapter} (force_best_draft)")
        return checkpoint_entry

    def get_latest_checkpoint(self) -> Optional[dict]:
        """获取最新完整checkpoint。"""
        cp_data = self.load()
        checkpoints = cp_data.get("checkpoints", [])
        if not checkpoints:
            return None
        return checkpoints[-1]

    def get_checkpoint_by_chapter(self, chapter: int) -> Optional[dict]:
        """按章节号查找checkpoint。"""
        cp_data = self.load()
        for cp in cp_data.get("checkpoints", []):
            if cp["chapter"] == chapter:
                return cp
        return None

    def verify_integrity(self, chapter: int) -> bool:
        """
        校验指定章节的文件完整性。
        对比当前文件hash与checkpoint中的hash，确认内容一致。
        """
        cp = self.get_checkpoint_by_chapter(chapter)
        if cp is None:
            return False

        # force_best_draft 条目：只校验 draft/ 文件，跳过 novel/ 检查
        if cp.get("mode") == "force_best_draft":
            draft_path = self.root / "chapters" / "draft" / f"chapter_{chapter}.txt"
            if not draft_path.exists():
                logger.warning(f"文件缺失: {draft_path}")
                return False
            actual_hash = _sha256_file(draft_path)
            if actual_hash != cp["novel_hash"]:
                logger.warning(f"文件hash不匹配 [draft]: {draft_path}")
                return False
            return True

        novel_path = novel_chapter_path(self.root, chapter)
        synopsis_path = self.root / "chapters" / "synopsis" / f"chapter_{chapter}.txt"
        outline_path = self.root / "chapters" / "outline" / f"chapter_{chapter}.json"

        checks = [
            ("novel", novel_path, cp["novel_hash"]),
            ("synopsis", synopsis_path, cp["synopsis_hash"]),
            ("outline", outline_path, cp["outline_hash"]),
        ]

        for name, path, expected_hash in checks:
            if not path.exists():
                logger.warning(f"文件缺失: {path}")
                return False
            actual_hash = _sha256_file(path)
            if actual_hash != expected_hash:
                logger.warning(f"文件hash不匹配 [{name}]: {path}")
                return False

        # 额外验证world_state_hash是否存在且非空
        if not cp.get("world_state_hash"):
            logger.warning(f"Checkpoint {chapter} 缺少world_state_hash")
            return False

        return True

    def restore_from_checkpoint(self, chapter: int) -> bool:
        """
        从checkpoint恢复指定章节。
        读取备份目录中的原始内容，写入对应文件。
        """
        cp = self.get_checkpoint_by_chapter(chapter)
        if cp is None:
            logger.error(f"无法恢复: 章节 {chapter} 无checkpoint")
            return False

        backup_dir = self.root / "runtime" / "backups"
        if not backup_dir.exists():
            logger.error(f"备份目录不存在: {backup_dir}")
            return False

        # 尝试从备份恢复各文件
        restored = []
        for name, relative_path in [
            ("novel", f"chapters/novel/chapter_{chapter}.txt"),
            ("synopsis", f"chapters/synopsis/chapter_{chapter}.txt"),
            ("outline", f"chapters/outline/chapter_{chapter}.json"),
        ]:
            backup_path = backup_dir / relative_path
            target_path = self.root / relative_path
            if backup_path.exists():
                try:
                    content = backup_path.read_text(encoding="utf-8")
                    target_path.write_text(content, encoding="utf-8")
                    restored.append(name)
                    logger.info(f"已从备份恢复: {name} ({relative_path})")
                except Exception as e:
                    logger.error(f"恢复{ name}失败: {e}")
                    return False
            else:
                logger.warning(f"备份文件缺失: {backup_path}")
                return False

        # 恢复完成后重新计算并更新hash
        novel_content = (novel_chapter_path(self.root, chapter)).read_text(encoding="utf-8")
        synopsis_content = (self.root / "chapters" / "synopsis" / f"chapter_{chapter}.txt").read_text(encoding="utf-8")
        outline_content = (self.root / "chapters" / "outline" / f"chapter_{chapter}.json").read_text(encoding="utf-8")

        cp["novel_hash"] = _sha256_string(novel_content)
        cp["synopsis_hash"] = _sha256_string(synopsis_content)
        cp["outline_hash"] = _sha256_string(outline_content)
        cp["restored_at"] = datetime.now(timezone.utc).isoformat()
        cp["complete"] = True

        cp_data = self.load()
        for i, entry in enumerate(cp_data["checkpoints"]):
            if entry["chapter"] == chapter:
                cp_data["checkpoints"][i] = cp
                break
        self.save(cp_data)

        logger.info(f"章节 {chapter} 恢复完成，已更新hash: {restored}")
        return True

    def clear_checkpoints_up_to(self, chapter: int):
        """清理指定章节之前的checkpoint（节省空间）。"""
        cp_data = self.load()
        cp_data["checkpoints"] = [
            cp for cp in cp_data["checkpoints"] if cp["chapter"] >= chapter
        ]
        self.save(cp_data)


def create_draft_checkpoint(
    manager: CheckpointManager,
    project_root: str | Path,
    chapter: int,
    draft_content: str,
) -> dict:
    """
    模块级辅助：为 force-best 章节写 draft checkpoint（不写 novel/）。
    委托给 CheckpointManager.create_draft_checkpoint。
    """
    return manager.create_draft_checkpoint(chapter=chapter, draft_content=draft_content)


def create_commit_transaction(
    manager: CheckpointManager,
    project_root: str | Path,
    chapter: int,
    novel_content: str,
    synopsis_content: str,
    outline_content: str,
    world_state_snapshot: dict,
) -> dict:
    """
    创建事务性commit：
    1. 验证所有输入非空
    2. 写入文件
    3. 创建checkpoint
    4. 若任何步骤失败则回滚
    """
    root = Path(project_root)

    # 验证
    if not novel_content.strip():
        raise ValueError(f"章节 {chapter} 正文为空")
    if not synopsis_content.strip():
        raise ValueError(f"章节 {chapter} 缩写为空")
    if not outline_content.strip():
        raise ValueError(f"章节 {chapter} 任务卡为空")

    # 备份文件写入 runtime/backups/，供恢复使用
    backup_dir = root / "runtime" / "backups"
    try:
        novel_backup = novel_chapter_path(backup_dir, chapter)
        synopsis_backup = backup_dir / "chapters" / "synopsis" / f"chapter_{chapter}.txt"
        outline_backup = backup_dir / "chapters" / "outline" / f"chapter_{chapter}.json"
        novel_backup.parent.mkdir(parents=True, exist_ok=True)
        synopsis_backup.parent.mkdir(parents=True, exist_ok=True)
        outline_backup.parent.mkdir(parents=True, exist_ok=True)
        novel_backup.write_text(novel_content, encoding="utf-8")
        synopsis_backup.write_text(synopsis_content, encoding="utf-8")
        outline_backup.write_text(outline_content, encoding="utf-8")
    except Exception as e:
        logger.error(f"写入备份失败: {e}")
        raise
    # 正式写入章节文件
    novel_path = novel_chapter_path(root, chapter)
    synopsis_path = root / "chapters" / "synopsis" / f"chapter_{chapter}.txt"
    outline_path = root / "chapters" / "outline" / f"chapter_{chapter}.json"
    try:
        novel_path.parent.mkdir(parents=True, exist_ok=True)
        synopsis_path.parent.mkdir(parents=True, exist_ok=True)
        outline_path.parent.mkdir(parents=True, exist_ok=True)
        novel_path.write_text(novel_content, encoding="utf-8")
        synopsis_path.write_text(synopsis_content, encoding="utf-8")
        outline_path.write_text(outline_content, encoding="utf-8")
        logger.info(f"章节 {chapter} 文件已写入: {novel_path}, {synopsis_path}, {outline_path}")
    except Exception as e:
        logger.error(f"写入章节文件失败: {e}")
        raise

    # 创建checkpoint
    checkpoint = manager.create_checkpoint(
        chapter=chapter,
        novel_content=novel_content,
        synopsis_content=synopsis_content,
        outline_content=outline_content,
        world_state_snapshot=world_state_snapshot,
    )

    logger.info(f"Commit successful for chapter {chapter}")
    return checkpoint
