"""
Novel-Engine 核心库：实现代码化精确查询、Durable Session 树、递归子 Agent 及增量自修复。
"""

from .db import StateDB
from .session import SessionTree
from .patcher import IncrementalPatcher

__all__ = [
    "StateDB",
    "SessionTree",
    "IncrementalPatcher",
]