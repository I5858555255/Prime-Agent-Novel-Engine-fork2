"""LLM critic for the viral-quality verdict (spec item 4, G1 + OQ3 default).

One critic call per chapter (temperature 0, reasoning_effort none), returning
a **bounded** verdict dict:

    {
      "爽点": int 0-5, "钩子": int 0-5, "压抑": int 0-5,
      "rationale": str (truncated to whole sentence + "…" at
                      quality_config.json:rationale_max_chars),
      "judge_model": str,      # provider[2]
      "prompt_ver":  PROMPT_VER,
      "knob_set_hash": str,    # from the arg, else spec's own knob_set_hash
      "verdict_error": null | {"type": "parse" | "timeout"},
    }

Parse/timeout failure → retry ONCE (≤2 critic calls/chapter total); a second
failure yields a `verdict_error` leaf value, never a crash. Budget halts are
run-level and never appear as a verdict_error (spec item 4).

`prose_sha256` is NOT part of this dict — the caller (`expand_run`) computes
it from the assembled text and passes it to `quality_store.record_verdict`.

The critic is a JSON-schema producer (temperature 0), not the prose-writer
polish model.
"""
from __future__ import annotations

import json
import os
import re

import httpx

from .volume_knobs import knob_set_hash, load_quality_config

# OQ3 default: one rubric prompt, one version. The M2 per-dimension suite
# (three prompt_vers) replaces this.
PROMPT_VER = "v1"

JUDGE_PROMPT = """你是网文爆款质量评审员。依据三个维度给分（0-5 整数，越高越好），只输出一个 JSON 对象，不加代码围栏、不加任何前后说明。
维度定义与打分口径（对照 spec 阈值）：
- 爽点：读者爽感峰值（打脸/逆风翻盘/认可/资源/反转）的强度与前置密度；5=强爽点前置且反转到位，0=无爽点或反转悬置超 1 场景。
- 钩子：章末悬念与"想知道下一章"的强度；5=章末有未解决危机/新悬念/即将兑现的承诺，0=章末抒情或"日子依旧平静"类弱钩子。
- 压抑：负面情绪/危机/羞辱/刁难的累积深度（压抑是铺垫，需被爽点兑现才形成节奏）；5=压抑层厚实且服务后续爽点，0=无压抑层或压抑被爽点过早消解。

判定规则：
- 每个维度分数必须是 0-5 的整数；分数须与 spec 阈值对照，低于阈值的维度是软性 quality_note（非阻断）。
- 不得以主观修辞评分替代事实；rationale 引用正文原句作为证据。
- rationale 长度不超过 {max_chars} 字，整句结尾（超长按整句截断并加省略号）。

输出格式（严格 JSON，键顺序固定）：
{{"爽点": <int 0-5>, "钩子": <int 0-5>, "压抑": <int 0-5>, "rationale": "<str, ≤{max_chars}字>"}}

spec（每维最低阈值，分数必须 ≥ 阈值才视为达标）：
{spec}

待评审正文：
{prose}
"""


def _truncate_to_sentence(text: str, max_chars: int) -> str:
    """Truncate `text` to at most max_chars, ending at a whole sentence.

    A sentence = the substring up to the last sentence-ending punctuation
    (。！？!?….;；) at or before max_chars; if none exists in the window,
    hard-truncate at max_chars and append "…" (the whole-sentence + "…"
    fallback required by the data contract).
    """
    if text is None:
        return ""
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    last_end = -1
    for i, ch in enumerate(window):
        if ch in "。！？!?….;；":
            last_end = i
    if last_end >= 0:
        return window[:last_end + 1]
    return window[:-1] + "…"


def _judge_http(provider, prompt: str) -> str:
    """One critic HTTP call. Returns the raw content string.

    `provider` is the 4-tuple from `providers.load_provider_full(phase)`:
    `(base_url, api_key_env, model, opts)`. `api_key_env` is the env-var
    NAME, not the value — resolved with `os.environ.get(api_key_env)`.

    temperature / reasoning_effort come from quality_config.json:judge,
    NOT from the provider's phase opts (the critic is a JSON-schema
    producer, not the polish writer).
    """
    base_url, api_key_env, model, _opts = provider
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise ValueError(f"missing env var {api_key_env} for the quality critic")
    cfg = load_quality_config().get("judge", {})
    temperature = cfg.get("temperature", 0.0)
    reasoning_effort = cfg.get("reasoning_effort")
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    body: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是严格的事实型评审员。只输出 JSON，不加说明。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 2000,
        "temperature": temperature,
    }
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort
    with httpx.Client(timeout=httpx.Timeout(connect=15, read=300, write=30, pool=30)) as client:
        r = client.post(url, headers={"Authorization": f"Bearer {api_key}"}, json=body)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def _parse_bounded(content: str, max_chars: int) -> dict:
    """Parse critic content into a bounded verdict dict.

    Raises ValueError on malformed JSON / missing keys / out-of-range
    scores; the caller converts that to verdict_error={"type": "parse"}.
    """
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("critic JSON root must be an object")
    out: dict = {}
    for dim in ("爽点", "钩子", "压抑"):
        v = obj.get(dim)
        if isinstance(v, bool) or not isinstance(v, int) or not (0 <= v <= 5):
            raise ValueError(f"critic score for {dim!r} must be int 0-5, got {v!r}")
        out[dim] = v
    rationale = obj.get("rationale", "")
    if not isinstance(rationale, str):
        raise ValueError(f"critic rationale must be a string, got {type(rationale).__name__}")
    out["rationale"] = _truncate_to_sentence(rationale, max_chars)
    return out


def judge_prose(prose: str, spec: dict, provider, trace_id: str,
                knob_set_hash_arg: str | None = None, post=None) -> dict:
    """One critic call (+ one retry on parse/timeout) → a bounded verdict.

    - `spec` is a `{爽点,钩子,压抑}` knob-set dict (or a dict that carries
      its own `knob_set_hash` key, as `volume_knobs` results can).
    - `provider` is the 4-tuple from `providers.load_provider_full`; the
      HTTP call resolves `api_key = os.environ.get(provider[1])` and uses
      `provider[2]` as judge_model.
    - `trace_id` is accepted for observability correlation (G8); it is NOT
      part of the returned verdict JSON.
    - `knob_set_hash_arg` is preferred when provided; otherwise the spec's
      own `knob_set_hash` key; otherwise the hash of the bare knob set.
    - `post` is an optional transport injection point: a callable
      `(provider, prompt) -> str`. When None, the real `_judge_http` is
      used. Tests inject a fake to count calls without HTTP.

    On a parse/timeout failure the critic is called a SECOND time
    (≤2 calls/chapter total). If the second call also fails, the returned
    verdict carries `verdict_error={"type":"parse"|"timeout"}` and **no
    score keys** — this is not a crash; it is the exact leaf payload
    `record_verdict` expects (a verdict_error leaf, prose_sha256 NULL).
    """
    transport = post if post is not None else _judge_http
    max_chars = int(load_quality_config().get("rationale_max_chars", 80))
    judge_model = provider[2]
    if knob_set_hash_arg is not None:
        ksh = knob_set_hash_arg
    elif isinstance(spec, dict) and "knob_set_hash" in spec:
        ksh = spec["knob_set_hash"]
    else:
        ksh = knob_set_hash({d: spec[d] for d in ("爽点", "钩子", "压抑")})

    prompt = JUDGE_PROMPT.format(
        spec=json.dumps({d: spec[d] for d in ("爽点", "钩子", "压抑")}, ensure_ascii=False),
        prose=prose,
        max_chars=max_chars,
    )

    parsed: dict | None = None
    verdict_error: dict | None = None
    for _attempt in range(2):  # ≤2 critic calls/chapter total
        try:
            raw = transport(provider, prompt)
            parsed = _parse_bounded(raw, max_chars)
            verdict_error = None
            break
        except httpx.TimeoutException:
            verdict_error = {"type": "timeout"}
        except httpx.HTTPStatusError:
            # A non-2xx response is an unusable payload: parse-class failure.
            verdict_error = {"type": "parse"}
        except (json.JSONDecodeError, ValueError, KeyError, TypeError):
            verdict_error = {"type": "parse"}

    if verdict_error is not None:
        return {
            "judge_model": judge_model,
            "prompt_ver": PROMPT_VER,
            "knob_set_hash": ksh,
            "verdict_error": verdict_error,
        }
    return {
        "爽点": parsed["爽点"], "钩子": parsed["钩子"], "压抑": parsed["压抑"],
        "rationale": parsed["rationale"],
        "judge_model": judge_model,
        "prompt_ver": PROMPT_VER,
        "knob_set_hash": ksh,
        "verdict_error": None,
    }
