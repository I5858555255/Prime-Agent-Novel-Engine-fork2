"""确定性中文硬门：拉丁字母检测与定点修复。

Fail-closed 语义：任何异常都不会放行含拉丁字母的原文。
"""
from novel_engine.core.errors import TransientLLMError, ContentRejectError
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 检测模式：任意连续拉丁字母即判违规
_LATIN_RE = re.compile(r"[A-Za-z]+")

# 中文借用词豁免：拉丁字母串紧跟中文量词/名词单字（V字形/U型/T恤/X光等）
# 视为中文借用表达，不算英文混入；孤立拉丁词不受豁免。
_BORROW_SUFFIX_RE = re.compile(r"([A-Za-z]+)[字型领式恤光]")


def _load_whitelist(root: Path) -> set[str]:
    """加载 allowed_latin.json 白名单，默认空集。"""
    try:
        p = root / "config" / "allowed_latin.json"
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return set(data.get("allowed_words", []) or [])
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return set()


def detect_latin_leak(text: str, whitelist: Optional[set[str]] = None) -> list[str]:
    """返回文本中所有违规拉丁词列表（白名单内词已过滤）。

    返回空列表表示通过；非空列表含所有命中词。
    """
    if not text:
        return []
    ws = whitelist or set()
    leaked: list[str] = []
    for m in _LATIN_RE.finditer(text):
        w = m.group(0)
        if w in ws:
            continue
        # 借用词豁免：该拉丁词原文位置紧跟中文借用后缀（V字形/U型/T恤/X光）
        # 仅豁免紧邻位置，不影响文本中同拼写的孤立拉丁词。
        end = m.end()
        if end < len(text) and text[end] in "字型领式恤光":
            continue
        leaked.append(w)
    return leaked


def is_chinese_clean(text: str, whitelist: Optional[set[str]] = None) -> bool:
    """纯函数：无拉丁字母（白名单外）返回 True。"""
    return len(detect_latin_leak(text, whitelist)) == 0


def fix_latin_leak(
    text: str,
    llm_client,
    whitelist: Optional[set[str]] = None,
    max_retries: int = 2,
    backoff_s: float = 1.0,
) -> tuple[str, bool, str]:
    """尝试用 LLM 修复拉丁泄漏，返回 (修复后文本, 是否成功, 错误原因)。

    失败路径：
    - 网络类异常：重试 max_retries 次，退避 backoff_s
    - 修复后复检仍含拉丁词：返回 (原本文本, False, "post_fix_verify_failed")
    - 任何异常最终不得放行含拉丁词的文本
    """
    if not text:
        return text, True, ""

    ws = whitelist or set()
    leaked = detect_latin_leak(text, ws)
    if not leaked:
        return text, True, ""

    for attempt in range(max_retries + 1):
        try:
            leak_list = "、".join(leaked)
            prompt = (
                "以下中文网络小说正文里混入了英文/拉丁字母片段（如 Scene、steady 等）。"
                f"本次必须处理的违规拉丁片段包括：{leak_list}。"
                "请将它们全部改写为通顺、符合语境的中文（例如 V字形 改写为 人字形），"
                "保持剧情人物文风不变。只输出修正后的完整正文，不要任何解释或注释：\n\n"
                + text
            )
            resp = llm_client.chat_completion(
                [
                    {"role": "system", "content": "你是将中英混杂的小说文本纯中文化的资深编辑，只输出修正后的全文。"},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=min(len(text) + 2000, 16000),
            )
            new_text = (resp.get("content") if isinstance(resp, dict) else "") or text
            new_text = new_text.strip()
            # verify-then-trust：复检必须干净
            remaining = detect_latin_leak(new_text, ws)
            if not remaining:
                return new_text, True, ""
            logger.warning(f"Latin fix attempt {attempt+1} still has leaks: {remaining[:5]}")
            if attempt < max_retries:
                time.sleep(backoff_s * (attempt + 1))
        except Exception as e:
            msg = str(e)
            logger.warning(f"Latin fix attempt {attempt+1} network error: {msg[:120]}")
            if attempt < max_retries:
                time.sleep(backoff_s * (attempt + 1))
            else:
                return text, False, f"network_error:{msg[:80]}"

    # 所有重试耗尽，复检仍不通过
    return text, False, "post_fix_verify_failed"


def ensure_chinese_hard_gate(
    text: str,
    llm_client,
    root: Optional[Path] = None,
) -> str:
    """Fail-closed 中文硬门：任何异常都不放行含拉丁词的原文。

    接受 str 正文，返回净化后的纯中文文本。若无法修复则抛异常：
    - 网络错误 → TransientLLMError（可冷却重试）
    - 修复后复检仍不通过 → ContentRejectError（内容问题）
    """
    proj_root = root or Path(__file__).parent.parent
    whitelist = _load_whitelist(proj_root)

    if is_chinese_clean(text, whitelist):
        return text

    fixed, ok, reason = fix_latin_leak(text, llm_client, whitelist)
    if ok:
        return fixed

    logger.error(f"Chinese hard gate FAILED: {reason} for chapter (cannot publish)")
    if reason.startswith("network_error:"):
        raise TransientLLMError(f"Chinese hard gate failed: {reason} — chapter cannot be published")
    raise ContentRejectError(f"Chinese hard gate failed: {reason} — chapter cannot be published")
