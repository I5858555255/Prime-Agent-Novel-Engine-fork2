"""ModelRouter：基于 llm_providers.json 的 Phase 级模型路由。

设计要点：
- 启动时加载 active_profile，fail-fast 校验必填字段
- 每个 phase 有独立的 models / response_format / extra_body / concurrency / timeout
- 请求时合并 profile 级 default_extra_body ← phase 级 extra_body 覆盖
- 故障转移：同一 phase 的 models 列表依次重试
- 不再依赖 runtime_config.json 的 model_router 段
"""
import json
import logging
import math
import os
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from .llm_client import LLMClient, TimeoutSpec
from .errors import ConfigFatalError, TransientLLMError, PermanentLLMError
from .rate_limiter import ModelRateLimiter, RateLimitError

logger = logging.getLogger(__name__)

# 默认阶段列表（兜底，防止配置缺失时崩溃）
DEFAULT_PHASES = {
    "outline": {"models": ["agnes-2.5-flash"], "response_format": None},
    "director": {"models": ["agnes-2.5-flash"], "response_format": "json_object"},
    "synopsis": {"models": ["agnes-2.5-flash"], "response_format": "json_object"},
    "scenes": {"models": ["agnes-2.5-flash"], "response_format": "json_object", "concurrency": 4},
    "polish": {"models": ["agnes-2.5-flash"], "response_format": None, "concurrency": 4},
    "review": {"models": ["agnes-2.5-flash"], "response_format": "json_object"},
}

# TimeoutSpec 缺省值
DEFAULT_TIMEOUT = TimeoutSpec(stream=True, idle_s=100, total_s=650, connect_s=15, write_s=30, pool_s=30)


def _load_providers_config(root: Path) -> dict:
    """加载 llm_providers.json；文件缺失时返回空配置（允许 use_mock 离线路径降级）。"""
    cfg_path = root / "config" / "llm_providers.json"
    if not cfg_path.exists():
        return {"active_profile": "", "profiles": {}}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"llm_providers.json invalid JSON: {e}") from e


def _validate_profile(name: str, profile: dict) -> None:
    """校验 profile 必填字段，非法直接抛错。"""
    missing = []
    if not profile.get("base_url"):
        missing.append("'base_url'")
    if not profile.get("api_key_env"):
        missing.append("'api_key_env'")
    if not profile.get("phases"):
        missing.append("'phases'")
    if missing:
        raise ConfigFatalError(f"Profile '{name}' missing required fields: {', '.join(missing)}")

    # 检查 api_key 环境变量非空
    key_env = profile.get("api_key_env", "")
    if key_env and not os.environ.get(key_env):
        raise ConfigFatalError(f"Profile '{name}' env var '{key_env}' is not set")


@dataclass(frozen=True)
class TimeoutSpec:
    """不可变超时配置，随调用传入 LLMClient。"""
    stream: bool = True
    idle_s: int = 100
    total_s: int = 650
    connect_s: int = 15
    write_s: int = 30
    pool_s: int = 30

    @classmethod
    def from_phase_cfg(cls, phase_cfg: dict, phase_name: str) -> "TimeoutSpec":
        """从 phase 配置构建 TimeoutSpec；缺失字段用默认值。"""
        if not isinstance(phase_cfg, dict):
            phase_cfg = {}
        stream = phase_cfg.get("stream", True)
        idle_s = phase_cfg.get("idle_timeout_s", cls.idle_s)
        total_s = phase_cfg.get("total_timeout_s", cls.total_s)
        connect_s = phase_cfg.get("connect_timeout_s", cls.connect_s)
        write_s = phase_cfg.get("write_timeout_s", cls.write_s)
        pool_s = phase_cfg.get("pool_timeout_s", cls.pool_s)
        # 保底校验
        if idle_s <= 0:
            idle_s = cls.idle_s
        if total_s <= 0:
            total_s = cls.total_s
        logger.debug(f"TimeoutSpec for '{phase_name}': stream={stream}, idle={idle_s}s, total={total_s}s")
        return cls(
            stream=bool(stream),
            idle_s=int(idle_s),
            total_s=int(total_s),
            connect_s=int(connect_s),
            write_s=int(write_s),
            pool_s=int(pool_s),
        )

    def effective_total(self, max_tokens: Optional[int]) -> int:
        """返回实际总超时：min(phase_total_s, ceil(max_tokens/3) + 60)。"""
        cap = self.total_s
        if max_tokens is not None and max_tokens > 0:
            cap = min(cap, math.ceil(max_tokens / 3) + 60)
        return int(cap)


class ModelRouter:
    """Phase 级模型路由器。

    启动时解析 active_profile，为每个 phase 构建独立的 LLMClient 池。
    chat_completion 按 phase models 列表故障转移，带错误三分类与有界重试。
    """

    def __init__(
        self,
        phase: str,
        root: Path,
        providers: Optional[dict] = None,
        extra_body_overrides: Optional[dict] = None,
        llm_client: Optional["LLMClient"] = None,
    ):
        self.phase = phase
        self.root = root
        self._lock = threading.Lock()

        # 测试路径：外部注入 llm_client 时跳过配置加载
        if llm_client is not None:
            self.models = [llm_client.model if hasattr(llm_client, 'model') else "mock"]
            self.response_format = None
            self.concurrency = 1
            self.extra_body = {}
            self.timeout = TimeoutSpec()
            self.max_retries = 1
            self.providers = {self.models[0]: llm_client}
            self.limiters = {}
            return

        # 加载配置
        cfg = _load_providers_config(root)
        active = cfg.get("active_profile", "")
        profiles = cfg.get("profiles") or {}

        # 无 providers 配置时（use_mock 测试路径，llm_providers.json 不存在）：降级使用 DEFAULT_PHASES
        # 注意：active 已设置但不在 profiles 中属启动期致命错误，必须 raise
        if not active:
            phase_cfg = DEFAULT_PHASES.get(phase, {})
            self.models = list(phase_cfg.get("models") or ["agnes-2.5-flash"])
            self.response_format = phase_cfg.get("response_format")
            self.concurrency = phase_cfg.get("concurrency", 1)
            self.extra_body = {}
            self.timeout = TimeoutSpec.from_phase_cfg(phase_cfg, phase)
            self.max_retries = int(phase_cfg.get("max_retries", 2))
            # P4-4/P5-1(b)：无 providers 配置时检查 runtime_config 的 use_mock，
            # 若为 True 则注入 MockLLMClient 避免 client_not_found
            try:
                rc_path = root / "config" / "runtime_config.json"
                if rc_path.exists():
                    rc = json.loads(rc_path.read_text(encoding="utf-8"))
                    if rc.get("llm", {}).get("use_mock") or rc.get("review_llm", {}).get("use_mock"):
                        from .llm_client import MockLLMClient
                        mock_client = MockLLMClient()
                        _model = self.models[0] if self.models else "mock"
                        self.providers = {_model: mock_client}
                        # P5-1(b)：补齐所有属性，禁止提前 return 导致 limiters 缺失
                        self._clients = {}
                        self.limiters = {}
                        return
            except Exception:
                pass
            self.providers = {}
            self._clients = {}
            self.limiters = {}
            return

        if active not in profiles:
            available = list(profiles.keys())
            raise ConfigFatalError(
                f"active_profile '{active}' not found in llm_providers.json. "
                f"Available: {available}"
            )

        profile = profiles[active]
        _validate_profile(active, profile)

        # 解析 phase 配置
        profile_phases = profile.get("phases", {})
        if phase not in profile_phases and phase not in DEFAULT_PHASES:
            available = sorted(set(list(profile_phases.keys()) + list(DEFAULT_PHASES.keys())))
            raise ConfigFatalError(
                f"Phase '{phase}' not found in profile '{active}'. "
                f"Available phases: {available}"
            )
        phase_cfg = profile_phases.get(phase) or DEFAULT_PHASES.get(phase, {})
        self.models = list(phase_cfg.get("models") or [])
        if not self.models:
            raise ConfigFatalError(
                f"Phase '{phase}' in profile '{active}' has empty models list"
            )
        self.response_format = phase_cfg.get("response_format")
        self.concurrency = phase_cfg.get("concurrency", 1)
        # 合并 extra_body：profile 级 default ← phase 级覆盖
        self.extra_body = dict(profile.get("default_extra_body") or {})
        phase_extra = phase_cfg.get("extra_body") or {}
        self.extra_body.update(phase_extra)

        # TimeoutSpec
        self.timeout = TimeoutSpec.from_phase_cfg(phase_cfg, phase)
        # max_retries 由 phase_cfg 或 profile 级决定
        self.max_retries = int(phase_cfg.get("max_retries") or profile.get("max_retries", 2))

        # 构建 LLMClient 池
        self._clients: dict[str, LLMClient] = {}
        if providers is not None:
            # 测试模式：外部注入
            self.providers = providers
            for _c in self.providers.values():
                if _c is not None:
                    _c.phase = phase
        else:
            self.providers = self._build_clients(profile, phase_cfg)

        # 速率限制器
        self.limiters: dict[str, ModelRateLimiter] = {}

        logger.info(
            f"ModelRouter initialized: phase={self.phase}, "
            f"profile={active}, models={self.models}, "
            f"response_format={self.response_format}, extra_body={self.extra_body}, "
            f"timeout={self.timeout}, max_retries={self.max_retries}"
        )

    def _build_clients(self, profile: dict, phase_cfg: dict | None = None) -> dict[str, LLMClient]:
        """为当前 phase 的所有模型构建 LLMClient。

        CC33：支持相位级 temperature（评审等评分任务应确定性打分）；未配置时
        沿用 LLMClient 默认 0.85，保持历史行为不变。
        """
        out = {}
        base_url = profile["base_url"].rstrip("/")
        key_env = profile["api_key_env"]
        api_key = os.environ.get(key_env, "")
        timeout = int(profile.get("timeout_s") or 120)
        _temp = (phase_cfg or {}).get("temperature")

        for model in self.models:
            if model in out:
                continue
            out[model] = LLMClient(
                api_base=base_url,
                model=model,
                api_key=api_key,
                timeout=timeout,
                temperature=_temp if _temp is not None else 0.85,
            )
            # CC round-11：把阶段标识下发给 client，使空/退化短正文门能按阶段生效
            out[model].phase = self.phase
        return out

    def chat_completion(
        self,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        extra_body: Optional[dict] = None,
        retry_on_error: bool = True,
        max_retries: int = 3,
        timeout: Optional[Union[int, TimeoutSpec]] = None,
    ) -> dict:
        """发送请求，按 phase models 列表故障转移，带错误三分类与有界重试。

        错误分类：
        - TransientLLMError: connect/read/idle/RemoteProtocolError/408/429/5xx
        - PermanentLLMError: 400
        - ConfigFatalError: 401/403/缺 key/profile 缺失
        """
        est = int(max_tokens or 2000)
        candidates = list(self.models) or ["agnes-2.5-flash"]
        last_err: Optional[Exception] = None
        last_model = ""

        # 合并 extra_body
        merged_extra = dict(self.extra_body)
        if extra_body:
            merged_extra.update(extra_body)

        # 注入 response_format
        if self.response_format:
            merged_extra["response_format"] = {"type": self.response_format}

        effective_retries = (self.max_retries if retry_on_error else 0) + max(0, max_retries - 3)
        effective_retries = min(effective_retries, 3)  # 上限 3
        total_attempts = max(1, len(candidates)) * max(1, effective_retries + 1)

        for attempt in range(total_attempts):
            model_idx = attempt // (effective_retries + 1)
            model = candidates[model_idx % len(candidates)]
            client = self.providers.get(model)
            if client is None:
                last_err = RuntimeError(f"client_not_found:{model}")
                continue

            # 构造本次调用的超时
            if timeout is not None:
                if isinstance(timeout, TimeoutSpec):
                    call_timeout = timeout
                else:
                    call_timeout = TimeoutSpec(idle_s=int(timeout), total_s=int(timeout) * 2)
            else:
                call_timeout = self.timeout

            effective_total = call_timeout.effective_total(max_tokens)

            try:
                # 速率限制
                lim = self.limiters.get(model)
                if lim:
                    lim.acquire(est)

                kwargs: dict = {}
                # 传入 TimeoutSpec 给 LLMClient（用于流式空闲/总超时检测）
                kwargs["timeout_spec"] = call_timeout

                result = client.chat_completion(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    extra_body=merged_extra if merged_extra else None,
                    timeout=effective_total,
                    **kwargs,
                )

                if lim:
                    lim.report_success()

                # Convert string result (e.g. fake test clients) to dict
                if isinstance(result, str):
                    result = {"role": "assistant", "content": result}

                if isinstance(result, dict):
                    result["_model_used"] = model
                    result["_phase"] = self.phase
                    result["_profile"] = ""
                    try:
                        result["_profile"] = self.providers and list(self.providers.values())[0].model or ""
                    except AttributeError:
                        pass
                    logger.debug(
                        f"ModelRouter phase={self.phase} succeeded on {model} after {attempt + 1} attempt(s)"
                    )
                    return result

            except (TransientLLMError, RateLimitError) as e:
                last_err = e
                last_model = model
                msg = str(e)
                logger.warning(
                    f"ModelRouter phase={self.phase} transient error on {model}: {msg[:120]}"
                )
                # 指数退避 + 抖动：min(2^n*5, 60) 秒
                retry_count = attempt % (effective_retries + 1)
                if retry_count < effective_retries:
                    delay = min((2 ** retry_count) * 5, 60)
                    jitter = random.uniform(0, delay * 0.2)
                    sleep_time = delay + jitter
                    if lim:
                        lim.report_429()
                    logger.info(
                        f"ModelRouter phase={self.phase} backing off {sleep_time:.1f}s "
                        f"before retry {retry_count + 1}/{effective_retries} on {model}"
                    )
                    time.sleep(sleep_time)
                    continue
                # 本模型重试耗尽，换下一个候选
                continue

            except PermanentLLMError as e:
                logger.error(
                    f"ModelRouter phase={self.phase} permanent error on {model}: {str(e)[:120]}"
                )
                # 不换模型，直接上抛
                raise

            except ConfigFatalError as e:
                logger.error(
                    f"ModelRouter phase={self.phase} config fatal on {model}: {str(e)[:120]}"
                )
                # 直接上抛
                raise

            except Exception as e:
                last_err = e
                last_model = model
                msg = str(e)
                # 识别是否需要 fast-fail
                is_fast_fail = any(
                    kw in msg for kw in [
                        "Server disconnected",
                        "RemoteProtocolError",
                        "Token is invalid",
                        "401",
                        "Unauthorized",
                    ]
                )
                logger.warning(
                    f"ModelRouter phase={self.phase} error on {model}: {msg[:120]}"
                    + (" [fast-fail]" if is_fast_fail else "")
                )
                if is_fast_fail:
                    continue
                if not retry_on_error or attempt >= len(candidates) * effective_retries:
                    raise
                time.sleep(1)

        # 全部候选耗尽
        if last_err is not None:
            err_msg = f"ModelRouter phase={self.phase} exhausted all {len(candidates)} models. Last error: {last_err}"
            logger.error(err_msg)
            # 尝试构造合适的异常
            if isinstance(last_err, (TransientLLMError, RateLimitError)):
                raise last_err
            elif isinstance(last_err, PermanentLLMError):
                raise last_err
            else:
                raise TransientLLMError(err_msg, phase=self.phase) from last_err

        raise RuntimeError(
            f"ModelRouter phase={self.phase} no result after {total_attempts} attempts"
        )

    def close(self) -> None:
        """关闭所有 LLMClient。"""
        for client in self.providers.values():
            try:
                client.close()
            except Exception as e:
                logger.warning(f"ModelRouter close error: {e}")
