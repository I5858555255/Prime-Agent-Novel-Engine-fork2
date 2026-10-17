# -*- coding: utf-8 -*-
"""CC30（DS 裁决 Q5）：purify 阶段英文脚手架字段词清洗（零 LLM，保守）。

只清混入正文的 JSON/脚手架字段词（r42 终检拦到 id/beats/beats/covered），绝不碰合法
外来语/品牌/拟声（白名单）与普通英文。必须【上下文限定】才判污染，避免误杀。
清洗后由调用方重跑确定性门（长度/密度/拉丁词流/JSON/beats/终检）；整段几乎全是字段词
则回滚 best 快照走 gap-continue，不提交。终检仍保留兜底。
"""
from __future__ import annotations
import re

FIELD_WORDS = {
    "id", "beats", "covered", "beats_covered", "index", "type", "score",
    "scene_id", "scene", "title", "content", "json", "schema", "validate",
    "density", "coverage", "raw", "gap", "chapter_id", "outline",
    "director", "writer", "reviewer", "polish", "refine", "failover",
    "token", "prompt", "api", "llm", "hook", "chapter_hook",
}
WHITELIST = {"iphone", "nba", "wifi", "app", "ai", "dna", "ok"}

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_JSON_CTX = set('"\':,{}[]_')
# 分句分隔（保留分隔符切分）
_CLAUSE_SPLIT_RE = re.compile(r"([，。；！？\n])")
# 单字段词删除后允许连带去掉的紧邻 ASCII 脚手架附着物
_ATTACH_RE_TMPL = r"[\s]*[\"':,{}\[\]_]*\s*\d*[\"':,{}\[\]_]*"


def _has_json_context(text: str, start: int, end: int) -> bool:
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    if before in _JSON_CTX or after in _JSON_CTX:
        return True
    if after.isdigit():
        return True
    # 近邻（3 字符内）出现 ASCII 冒号/引号/花方括号也算脚手架上下文
    near = text[max(0, start - 3):start] + text[end:end + 3]
    return any(c in '"\':{}[]' for c in near)


def _field_tokens_in(clause: str):
    """返回分句内被判污染的字段词 (token, start, end) 列表。

    字段词在 FIELD_WORDS 且非白名单。判污染：词含下划线 / 紧邻 JSON 标点或数字，
    或【同一分句出现 >=2 个字段词】（DS：同段≥2 字段词即视为脚手架污染）。
    """
    matches = []
    for m in _TOKEN_RE.finditer(clause):
        tok = m.group(0)
        low = tok.lower()
        if low in WHITELIST or low not in FIELD_WORDS:
            continue
        matches.append((tok, m.start(), m.end()))
    if len(matches) >= 2:
        return matches
    if len(matches) == 1:
        tok, s, e = matches[0]
        if "_" in tok or _has_json_context(clause, s, e):
            return matches
    return []


def _cjk_count(s: str) -> int:
    return sum(1 for c in s if "\u4e00" <= c <= "\u9fff")


def _remove_single_token(clause: str, tok: str) -> str:
    # 仅删该 token（整词，大小写不敏感）+ 紧邻脚手架附着物，保留中文
    pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(tok) + r"(?![A-Za-z0-9_])"
                     + _ATTACH_RE_TMPL, re.IGNORECASE)
    out = pat.sub("", clause, count=1)
    # 收敛多余空白
    out = re.sub(r"[ \t]{2,}", " ", out).strip()
    return out


def scrub_scaffold_latin(text: str):
    """返回 {status, text, removed_tokens}。
    status ∈ {"unchanged","scrubbed","rollback_best"}。
    """
    if not text:
        return {"status": "unchanged", "text": text, "removed_tokens": []}
    full_polluted = _field_tokens_in(text)
    # 整段几乎全是字段词（中文极少且污染>=2）→ 回滚 best
    if len(full_polluted) >= 2 and _cjk_count(text) < 4:
        return {"status": "rollback_best", "text": text,
                "removed_tokens": [t for t, _, _ in full_polluted]}

    parts = _CLAUSE_SPLIT_RE.split(text)
    removed: list[str] = []
    rebuilt = []
    for part in parts:
        if part in ("，", "。", "；", "！", "？", "\n") or part == "":
            rebuilt.append(part)
            continue
        toks = _field_tokens_in(part)
        if not toks:
            rebuilt.append(part)
            continue
        if len(toks) >= 2:
            # 多字段词污染分句：丢弃整个分句（连同其分隔符随后处理）
            removed.extend(t for t, _, _ in toks)
            # 标记为空串；其后的分隔符在收尾时清掉重复
            rebuilt.append("")
            continue
        # 单字段词：删 token + 紧邻附着物
        tok = toks[0][0]
        new_clause = _remove_single_token(part, tok)
        removed.append(tok)
        # 删掉后该分句已无中文内容 → 丢弃
        rebuilt.append(new_clause if _cjk_count(new_clause) >= 1 else "")

    out = "".join(rebuilt)
    # 清理因分句删除产生的悬空/重复标点
    out = re.sub(r"\s*([，；！？])\s*(?=[，。；！？])", "", out)
    out = re.sub(r"[，；]\s*(?=。|$)", "", out)
    out = re.sub(r"[ \t]{2,}", " ", out).strip()
    if not removed:
        return {"status": "unchanged", "text": text, "removed_tokens": []}
    return {"status": "scrubbed", "text": out, "removed_tokens": removed}
