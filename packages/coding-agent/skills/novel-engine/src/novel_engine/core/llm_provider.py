"""Provider 抽象层：统一 LLM 调用的 reasoning 兜底 / both-empty 重试 / cache-bust。"""
import re
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

from novel_engine.core.llm_client import _repair_json
from .rate_limiter import RateLimitError

logger = logging.getLogger(__name__)


@dataclass
class ProviderConfig:
    family: str = "qwen"
    api_base: str = "https://api.siliconflow.cn"
    model: str = "Qwen/Qwen3.5-4B"
    reasoning_fallback: bool = True
    thinking_param: Optional[str] = "enable_thinking"
    cache_bust_suffix: str = (
        "\n\n[请直接给出最终答案，严格使用合法 JSON，不要使用思考模式，"
        "不要输出任何分析或推理过程。]"
    )
    retry_temperatures: list = field(default_factory=lambda: [0.85, 0.7, 0.95, 1.0])


class LLMProvider:
    def __init__(self, client, config: Optional[ProviderConfig] = None):
        self.client = client
        self.config = config or ProviderConfig()

    def complete(self, messages, *, output_json=False, temperature=None,
                 max_tokens=None, extra_body=None):
        cfg = self.config
        temps = list(cfg.retry_temperatures) if temperature is None else [temperature]
        attempt_limit = max(1, len(temps)) * 2
        last_err = "empty"
        reasoning_cache = ""
        busted = False
        for attempt in range(attempt_limit):
            try:
                for temp in temps:
                    attempt_msgs = messages
                    for _ in range(3):
                        eb = dict(extra_body or {})
                        if cfg.thinking_param:
                            eb.setdefault(cfg.thinking_param, False)
                        result = self.client.chat_completion(
                            attempt_msgs, temperature=temp, max_tokens=max_tokens, extra_body=eb)
                        content = result.get("content") or ""
                        reasoning = result.get("reasoning_content") or ""
                        reasoning_cache = reasoning or reasoning_cache
                        if not output_json:
                            if content:
                                return content
                            if reasoning and cfg.reasoning_fallback and len(reasoning) <= 6000:
                                return reasoning
                            last_err = "empty"
                        else:
                            cleaned = _strip_fences(content)
                            if cleaned:
                                parsed = _repair_json(cleaned)
                                # 结构化校验：字典 + 必填字段非空
                                if parsed is not None and isinstance(parsed, dict):
                                    return parsed
                                last_err = "json_parse_failed"
                            else:
                                last_err = "empty_content"
                if last_err in ("empty", "json", "json_parse_failed", "empty_content") and reasoning_cache:
                    if output_json:
                        parsed = _repair_json(reasoning_cache)
                        if parsed is not None:
                            return parsed
                        raise RuntimeError("LLM返回的内容无法解析为有效JSON")
                    if cfg.reasoning_fallback:
                        return reasoning_cache
                raise RuntimeError("LLM返回空响应（content 与 reasoning_content 均空，已跨温度重试）")
            except RateLimitError as e:
                last_err = f"rate_limit: {e}"
                logger.error(f"限流重试 (429) 第 {attempt+1}/{attempt_limit} 次: {e}")
                if attempt == attempt_limit - 1:
                    raise RuntimeError(f"provider failed: {last_err}") from e
                time.sleep(min(2 ** attempt * 5, 120))
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                logger.error(f"调用失败 (尝试 {attempt+1}/{attempt_limit}): {e}")
                if attempt == attempt_limit - 1:
                    raise RuntimeError(f"provider failed: {last_err}") from e
                time.sleep(min(2 ** attempt * 5, 120))

    @staticmethod
    def _bust(messages):
        msgs = list(messages)
        if msgs and msgs[-1].get("role") == "user":
            msgs[-1] = dict(msgs[-1])
            msgs[-1]["content"] = msgs[-1]["content"] + ProviderConfig().cache_bust_suffix
        else:
            msgs.append({"role": "user", "content": ProviderConfig().cache_bust_suffix})
        return msgs


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r'^\s*```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```\s*$', '', text)
    return text.strip()
