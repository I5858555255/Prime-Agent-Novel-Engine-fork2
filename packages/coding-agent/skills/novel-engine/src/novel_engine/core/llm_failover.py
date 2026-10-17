import time
import logging
from novel_engine.core.llm_client import LLMClient

logger = logging.getLogger(__name__)


class FailoverLLMClient:
    def __init__(self, primary: LLMClient, fallback: LLMClient,
                 trigger: int = 3, max_backoff: int = 1800, log_prefix: str = ""):
        self.primary = primary
        self.fallback = fallback
        self.trigger = trigger
        self.max_backoff = max_backoff
        self.log_prefix = log_prefix
        self._consecutive_errors = 0
        self._using_fallback = False

    @classmethod
    def from_config_dict(cls, cfg: dict, primary_section: str, fallback_section: str,
                         primary_key_env: str, fallback_key_env: str,
                         trigger: int = 3, max_backoff: int = 1800, log_prefix: str = ""):
        primary = LLMClient.from_config_dict(cfg, section=primary_section, api_key_env=primary_key_env)
        fallback = LLMClient.from_config_dict(cfg, section=fallback_section, api_key_env=fallback_key_env)
        return cls(primary, fallback, trigger=trigger, max_backoff=max_backoff, log_prefix=log_prefix)

    def active_model(self) -> str:
        client = self.fallback if self._using_fallback else self.primary
        return f"{'fallback:' if self._using_fallback else ''}{client.model}"

    def chat_completion(self, messages, temperature=None, max_tokens=None,
                        retry_on_error=True, max_retries=3, extra_body=None):
        target = self.fallback if self._using_fallback else self.primary
        try:
            result = target.chat_completion(
                messages, temperature=temperature, max_tokens=max_tokens,
                retry_on_error=retry_on_error, max_retries=max_retries, extra_body=extra_body)
            self._consecutive_errors = 0
            return result
        except Exception as e:
            self._consecutive_errors += 1
            logger.warning(f"{self.log_prefix} LLM error ({self._consecutive_errors}/{self.trigger}): {e}")
            if not self._using_fallback and self._consecutive_errors >= self.trigger:
                logger.error(f"{self.log_prefix} switching to FALLBACK model {self.fallback.model}")
                self._using_fallback = True
                self._consecutive_errors = 0
                backoff = min(self.max_backoff, 2 ** (self.trigger + 1))
                time.sleep(backoff)
                return self.fallback.chat_completion(
                    messages, temperature=temperature, max_tokens=max_tokens,
                    retry_on_error=retry_on_error, max_retries=max_retries, extra_body=extra_body)
            raise
