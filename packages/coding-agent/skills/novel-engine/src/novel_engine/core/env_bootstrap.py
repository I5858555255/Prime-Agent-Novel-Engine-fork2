"""项目 .env 引导：启动时自动加载环境变量。

标准库实现，零第三方依赖。重复调用幂等。
仅当 os.environ 中不存在该 key 时才注入，不覆盖已有值。
"""
import logging
import os
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_ENV_CACHE: set = set()


def load_project_env(env_path: Optional[Path] = None) -> Dict[str, str]:
    """加载项目 .env 文件并注入缺失的环境变量。

    默认定位到 novel_engine 包所在目录的 .env（即 ENG/.env）。
    解析简单 KEY=VALUE 行，忽略空行/# 注释/export 前缀，去除外层引号。
    返回实际注入的 {key: value} 字典（幂等，重复调用返回空 dict）。

    Raises:
        FileNotFoundError: 当指定路径不存在时抛出，但不影响进程继续。
    """
    injected: Dict[str, str] = {}

    if env_path is None:
        # novel_engine/core/env_bootstrap.py 上一级是 src，再上一级是 novel_engine (ENG 根)
        _default = Path(__file__).parent.parent.parent / ".env"
        env_path = _default

    if not env_path.exists():
        logger.debug("env file not found at %s, skipping", env_path)
        return injected

    content = env_path.read_text(encoding="utf-8")
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip optional 'export ' prefix
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if not key:
            continue
        if key in _ENV_CACHE:
            continue
        if key in os.environ:
            _ENV_CACHE.add(key)
            continue
        os.environ[key] = val
        injected[key] = val
        _ENV_CACHE.add(key)

    logger.info("load_project_env: injected %d vars from %s", len(injected), env_path)
    return injected
