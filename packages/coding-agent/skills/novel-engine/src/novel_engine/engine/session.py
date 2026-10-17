"""
DurableSession: 实现类似 Git 分支树的小说创作 Durable Sessions 与 Session Tree，
支持在质量较差（评分不及格）或剧情需要时一键级联回滚。
"""
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional, Any, List


class SessionNode:
    """Session 树的一个节点，表示在特定剧情发展状态下的一个快照。"""

    def __init__(
        self,
        chapter_num: int,
        content_hash: str,
        world_state_snapshot: dict,
        score: Optional[float] = None,
        parent_id: Optional[str] = None,
        branch_name: str = "main",
    ):
        self.node_id = str(uuid.uuid4())[:8]
        self.chapter_num = chapter_num
        self.content_hash = content_hash
        self.world_state_snapshot = world_state_snapshot
        self.score = score
        self.parent_id = parent_id
        self.branch_name = branch_name
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.children_ids: list[str] = []

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "chapter_num": self.chapter_num,
            "content_hash": self.content_hash,
            "world_state_snapshot": self.world_state_snapshot,
            "score": self.score,
            "parent_id": self.parent_id,
            "branch_name": self.branch_name,
            "timestamp": self.timestamp,
            "children_ids": self.children_ids,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SessionNode':
        node = cls(
            chapter_num=data["chapter_num"],
            content_hash=data["content_hash"],
            world_state_snapshot=data["world_state_snapshot"],
            score=data.get("score"),
            parent_id=data.get("parent_id"),
            branch_name=data.get("branch_name", "main"),
        )
        node.node_id = data["node_id"]
        node.timestamp = data.get("timestamp", datetime.now(timezone.utc).isoformat())
        node.children_ids = data.get("children_ids", [])
        return node


class SessionTree:
    """Session 树：管理所有创作分支与快照，提供高容错、可回退的会话隔离机制。"""

    def __init__(self):
        self.nodes: dict[str, SessionNode] = {}
        self.branches: dict[str, str] = {}  # branch_name -> leaf_node_id
        self.root_node_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "nodes": {nid: node.to_dict() for nid, node in self.nodes.items()},
            "branches": self.branches,
            "root_node_id": self.root_node_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SessionTree':
        tree = cls()
        tree.branches = data.get("branches", {})
        tree.root_node_id = data.get("root_node_id")
        tree.nodes = {}
        for nid, ndata in data.get("nodes", {}).items():
            tree.nodes[nid] = SessionNode.from_dict(ndata)
        return tree

    def add_commit(
        self,
        chapter_num: int,
        content_hash: str,
        world_state_snapshot: dict,
        score: Optional[float] = None,
        branch_name: str = "main",
    ) -> SessionNode:
        """提交一个新的节点。如果该分支已有叶子节点，新节点自动链接为子节点。"""
        parent_id = self.branches.get(branch_name)
        if not parent_id and branch_name != "main" and self.root_node_id:
            # Fork 默认从 main 的当前叶子节点切出
            parent_id = self.branches.get("main")

        node = SessionNode(
            chapter_num=chapter_num,
            content_hash=content_hash,
            world_state_snapshot=world_state_snapshot,
            score=score,
            parent_id=parent_id,
            branch_name=branch_name,
        )

        self.nodes[node.node_id] = node
        self.branches[branch_name] = node.node_id

        if parent_id and parent_id in self.nodes:
            self.nodes[parent_id].children_ids.append(node.node_id)
        else:
            if not self.root_node_id:
                self.root_node_id = node.node_id

        return node

    def fork_branch(self, source_branch: str, new_branch_name: str) -> str:
        """从已有分支切出一个创意/实验平行分支（类似 git branch）。"""
        source_leaf_id = self.branches.get(source_branch)
        if not source_leaf_id:
            raise ValueError(f"源分支 {source_branch} 不存在或无节点。")

        self.branches[new_branch_name] = source_leaf_id
        return source_leaf_id

    def rollback_to_node(self, node_id: str, branch_name: str = "main") -> dict:
        """
        一键级联回退至指定的 Session 节点，
        返回当时的世界状态快照，从而无缝对接剧情，彻底解决概率 RAG 带来的脏数据问题。
        """
        if node_id not in self.nodes:
            raise KeyError(f"节点 {node_id} 未找到。")

        target_node = self.nodes[node_id]
        # 更新该分支的最新叶子节点指针
        self.branches[branch_name] = node_id

        return target_node.world_state_snapshot

    def get_branch_history(self, branch_name: str) -> list[SessionNode]:
        """获取分支的自底向上完整创作历史链路。"""
        leaf_id = self.branches.get(branch_name)
        if not leaf_id:
            return []

        history = []
        curr_id = leaf_id
        while curr_id and curr_id in self.nodes:
            node = self.nodes[curr_id]
            history.append(node)
            curr_id = node.parent_id

        return list(reversed(history))

    def prune_snapshots(self, branch_name: str = "main", keep: int = 100) -> int:
        """修剪分支历史中的世界状态快照，仅保留最近 keep 个节点。

        回滚仅用于上一章（rollback_to_node），因此更早的快照可以安全清空，
        大幅减小 session_tree.json 的体积。节点元数据（hash/score/父子关系）
        保留不变，仅清空 world_state_snapshot。返回被修剪的节点数。
        """
        history = self.get_branch_history(branch_name)
        prune_count = 0
        for node in history[:-keep] if keep > 0 else history:
            if node.world_state_snapshot:
                node.world_state_snapshot = {}
                prune_count += 1
        return prune_count
