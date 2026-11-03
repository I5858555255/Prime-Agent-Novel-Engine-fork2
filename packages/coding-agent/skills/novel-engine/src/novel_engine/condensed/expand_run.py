"""Staged expansion loop: expand -> count -> continue-writing until in range.

Continues from an existing draft (or calls the full expansion prompt when no
draft is given) until the cleaned prose is inside the manifest's expanded range.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

from novel_engine.core.env_bootstrap import load_project_env

from .accept_check import check_prose, extract_prose, han_count, load_card, load_manifest
from .contracts import dumps

CONTINUE_TEMPLATE = """你是本小说的正文续写者。以下是已完成部分和剩余任务。

已完成正文（不得改写，只能续写）：
{existing}

任务：续写至全章共 {target} 汉字（当前 {current} 字，还差约 {remaining} 字）。
- 续写紧接已完成正文最后一句，保持人称、视角、文风一致。
- 遵守全部资料规则（认知边界、对话意图、场景间连续性、伏笔尺度）。
- 只输出续写的新增正文段落，不要重复已完成部分，不要添加任何标签或说明。
- 续写结束后，落在章节简写的终点状态。

{materials}
"""


def _llm(base_url: str, api_key: str, model: str, messages: list[dict],
         max_tokens: int, temperature: float) -> str:
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    with httpx.Client(timeout=httpx.Timeout(connect=15, read=300, write=30, pool=30)) as c:
        r = c.post(url, headers={"Authorization": f"Bearer {api_key}"},
                   json={"model": model, "messages": messages,
                         "max_tokens": max_tokens, "temperature": temperature})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def expand_until_range(package: Path, base_url: str, api_key: str, model: str,
                       initial_draft: Path | None = None, max_rounds: int = 5,
                       target: int | None = None, out_dir: Path | None = None) -> dict:
    card = load_card(package)
    manifest = load_manifest(package)
    lo, hi = manifest.get("expanded_range", [4000, 5000])
    target = target or manifest.get("expanded_target_han") or 4210

    prompt_text = (package / "扩写提示词.md").read_text(encoding="utf-8")
    cut = prompt_text.find("## 全书设定.md")
    materials = prompt_text[cut:] if cut != -1 else ""

    if initial_draft:
        raw = initial_draft.read_text(encoding="utf-8-sig")
    else:
        raw = _llm(base_url, api_key, model,
                   [{"role": "system",
                     "content": "你是专业小说正文扩写者。严格遵守所有资料规则。只输出纯正文，不加任何标签或说明。"},
                    {"role": "user", "content": prompt_text}],
                   max_tokens=10000, temperature=0.85)

    rounds: list[dict] = []
    prose = raw
    for i in range(max_rounds + 1):
        prose, notes = extract_prose(prose)
        han = han_count(prose)
        report = check_prose(prose, card, manifest)
        verdict = "PASS" if not report["blocking"] else "BLOCK"
        rounds.append({"round": i, "han": han, "verdict": verdict,
                       "blocking": report["blocking"], "warnings": report["warnings"],
                       "strip_notes": notes})
        if lo <= han <= hi:
            break
        if i == max_rounds or han > hi:
            break
        remaining = min(hi, target) - han
        cont_prompt = CONTINUE_TEMPLATE.format(existing=prose, target=target,
                                               current=han, remaining=remaining,
                                               materials=materials)
        continuation = _llm(base_url, api_key, model,
                            [{"role": "system", "content": "你是小说正文续写者。只输出新增正文，不重复已有内容。"},
                             {"role": "user", "content": cont_prompt}],
                            max_tokens=4000, temperature=0.85)
        prose = prose + "\n\n" + continuation

    final_prose, _ = extract_prose(prose)
    out = out_dir or package
    final_path = out / f"expanded_draft_ch{manifest['chapter']:04d}.md"
    final_path.write_text(final_prose, encoding="utf-8")
    summary = {
        "chapter": manifest["chapter"],
        "package": str(package),
        "target": target,
        "range": [lo, hi],
        "rounds": rounds,
        "final_han": han_count(final_prose),
        "final_draft": str(final_path),
        "pass": rounds[-1]["verdict"] == "PASS",
    }
    report_path = out / f"expand_report_ch{manifest['chapter']:04d}.json"
    report_path.write_text(dumps(summary), encoding="utf-8")
    return summary


def main(argv=None) -> int:
    load_project_env()
    parser = argparse.ArgumentParser(description="Staged expansion until word count is in range")
    parser.add_argument("--package", type=Path, required=True, help="expansion package directory")
    parser.add_argument("--draft", type=Path, help="optional initial draft; omit to call the full expansion prompt")
    parser.add_argument("--max-rounds", type=int, default=5)
    parser.add_argument("--json", action="store_true", help="print JSON summary")
    args = parser.parse_args(argv)

    from .providers import load_provider
    base_url, api_key_env, model = load_provider("polish")
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        print(f"missing env var {api_key_env}", file=sys.stderr)
        return 2
    summary = expand_until_range(args.package, base_url=base_url, api_key=api_key,
                                 model=model, initial_draft=args.draft,
                                 max_rounds=args.max_rounds)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"chapter {summary['chapter']}: {len(summary['rounds'])} rounds, "
              f"final_han={summary['final_han']}, pass={summary['pass']}, "
              f"draft={summary['final_draft']}")
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
