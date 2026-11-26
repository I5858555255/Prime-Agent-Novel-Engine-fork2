"""generate_mid_outline: 为无节点章批次生成"章级中纲草稿"。

这是新增的章级意图层（bible 骨架级 与 单章大纲 LLM 临场生成 之间），
产物为结构化 JSON 存入 config/planning/（与 plot_graph.json 同目录同管理），
长期可追溯，不是临时内存对象。

原则（与任务书一致）：
- 只生成该范围内"无 plot 节点"的章（有节点章由 plot_graph 直接驱动）。
- 每章输出：一句话核心事件 + 涉及角色 + 边界标注（接近伏笔/forbidden 边界
  时显式标"需人工确认"，不自行决定放行）。
- 分批次 LLM 调用（默认 6 章/批），避免单次输出过长导致内部矛盾。
- 生成后必须经 audit_mid_outline.py 校验才能交付 Director（本脚本不含校验）。

Usage (run from src/):
    python -m novel_engine.scripts.generate_mid_outline --vid V01 --start 21 --end 50
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_SRC_ROOT = str(Path(__file__).resolve().parents[2])
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

ROOT = Path(__file__).resolve().parents[1]

SYSTEM_PROMPT = (
    "你是长篇东方玄幻小说的卷级章节规划助手。你的职责是根据卷设定、"
    "禁止事项、伏笔时间表与已确认剧情节点，为指定章节生成精确的"
    "章级意图（中纲）。你只做规划，不写正文。"
)


def _load_env() -> None:
    """Load .env from the source directory (same logic as run_volume)."""
    for candidate in (ROOT / ".env", ROOT.parent / ".env"):
        if candidate.exists():
            try:
                for line in candidate.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key, val = key.strip(), val.strip().strip('"').strip("'")
                    os.environ.setdefault(key, val)
            except Exception as exc:  # pragma: no cover
                logging.getLogger(__name__).warning(
                    "Failed to parse .env at %s: %s", candidate, exc)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logging.getLogger(__name__).error("Failed to parse %s: %s", path, exc)
        return {}


def _read_bible() -> dict:
    """读 bible 五文件（缺失容忍，返回非空子集）。"""
    bible_dir = ROOT / "bible"
    out = {}
    for name in ("world_bible", "character_bible", "style_bible",
                 "author_intent", "ending_bible"):
        p = bible_dir / f"{name}.md"
        try:
            out[name] = p.read_text(encoding="utf-8") if p.exists() else ""
        except OSError:
            out[name] = ""
    return out


def _current_volume_forbidden(author_intent: str, vid: str, chapter_num: int) -> list[str]:
    """author_intent 当前卷 forbidden 列表（按 '## 第N阶段（第A-B章）' 定位）。"""
    import re
    if not author_intent:
        return []
    for blk in re.split(r"^## ", author_intent, flags=re.M):
        m = re.match(r"第[一二三四五六七八九十]+阶段[：:].*?（.*?第?(\d+)-(\d+)章?）", blk)
        if not m:
            continue
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo <= chapter_num <= hi:
            return [ln.strip().lstrip("- ").strip('"')
                    for ln in blk.splitlines() if ln.strip().startswith("-")]
    return []


def _cognition_window(vid: str, chapter_num: int, bible: dict) -> dict:
    """按当前章节所在卷的阶段取 author_intent 的 forbidden 条款 + 关键禁词。

    根因：旧版只注入卷级 forbidden 字符串，模型仍会在 core_event 里写
    '引气术/筑基/神识/养魂经'等越界词 → audit 撞 concept 锁 → pending_human。
    这里把 forbidden 条款逐条展开为"生成时必须回避"的硬约束，并附上该卷
    的关键禁词清单（未解锁概念词），让模型在源头就不越界。
    """
    import re
    author_intent = bible.get("author_intent", "")
    if not author_intent:
        return {}
    stage_block = ""
    for blk in re.split(r"^## ", author_intent, flags=re.M):
        m = re.match(r"第[一二三四五六七八九十]+阶段.*?（第?(\d+)-?(\d+)章?）", blk)
        if not m:
            continue
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        if lo <= chapter_num <= hi:
            stage_block = blk
            break
    if not stage_block:
        stage_block = author_intent
    forbidden_lines = [ln.strip().lstrip("- ").strip('"')
                       for ln in stage_block.splitlines() if ln.strip().startswith("-")]
    key_terms = []
    for t in ("引气", "气感", "吐纳", "灵力运转", "周天", "丹田", "筑基", "神识", "灵气",
              "灵根", "神魂", "养魂经", "太上", "元婴", "化神", "金丹", "飞升", "摄魂阁"):
        if t in stage_block or t in author_intent:
            key_terms.append(t)
    return {"forbidden": forbidden_lines, "key_terms": key_terms, "stage": stage_block[:400]}


def _build_batch_prompt(
    vid: str,
    chapters: list[int],
    volume: dict,
    forbidden: list[str],
    bible: dict,
    nearby_nodes: list[dict],
    relevant_foreshadows: list[dict],
    world_state_summary: str,
) -> str:
    """构造一批章节的中纲生成 prompt。"""
    cog = _cognition_window(vid, chapters[0], bible)
    lines = [
        f"【待规划章节】{chapters[0]}-{chapters[-1]}（共 {len(chapters)} 章，均为无 plot 节点章）",
        "",
        f"【当前卷】{vid} {volume.get('title', '')}（第{volume.get('chapter_range', [0, 0])[0]}-"
        f"{volume.get('chapter_range', [0, 0])[1]}章）",
        f"卷主题：{volume.get('theme', '')}",
        "卷核心冲突：",
    ]
    for c in volume.get("core_conflicts", []):
        lines.append(f"- {c}")
    lines.append(f"卷高潮：ch{volume.get('climax_chapter')} — {volume.get('climax_description', '')}")
    lines.append("")
    lines.append("【本卷禁止事项（硬约束，任何章节不得违反）】")
    lines.append("；".join(forbidden) if forbidden else "（无显式条款）")
    lines.append("")
    # 根因修复：章级认知窗口 —— 把该阶段 forbidden 条款 + 未解锁概念词展开为生成硬约束
    if cog.get("forbidden") or cog.get("key_terms"):
        lines.append("【认知窗口硬约束（本批章节所在阶段，core_event 必须逐条回避）】")
        for fb in cog.get("forbidden", []):
            lines.append(f"- 禁：{fb[:80]}")
        if cog.get("key_terms"):
            lines.append("- 未解锁概念词（本阶段 core_event 不得出现，仅可写'模糊感知/传闻/物证'，"
                         "不得点明机制或名称）：" + "、".join(cog["key_terms"]))
        lines.append("  违反任一条 → 该章会被 audit 判 needs_regenerate，必须用中性观察语言重写。")
        lines.append("")
    lines.append("【结局与伏笔时间表（仅列与本章范围相关的条目）】")
    for fs in relevant_foreshadows:
        lines.append(
            f"- {fs.get('fid') or fs.get('id')}（{fs.get('id')}）：plant@{fs.get('plant_chapter')} "
            f"resolve@{fs.get('resolve_chapter')} importance={fs.get('importance')}；"
            f"埋设：{str(fs.get('plant_context'))[:60]}")
    lines.append("")
    lines.append("【已确认剧情节点（邻近，前后 20 章内）】")
    for n in nearby_nodes:
        lines.append(f"- ch{n.get('chapter_target')} {n.get('id')}：{str(n.get('description'))[:60]}")
    lines.append("")
    lines.append("【前置剧情与当前世界状态（承接 ch1-20）】")
    lines.append(world_state_summary[:800] if world_state_summary else "（无）")
    lines.append("")
    lines.append("【硬性红线】")
    lines.append("1. 第一阶段（第1-316章）陆烬（C001）不得出现任何修炼样式动作"
                 "（静坐吐纳/引气/气流流转/暖意/气感等）；呼吸法只能由陈老根（C002）"
                 "本人练习/提及，陆烬最多'在旁看到'或'被告知结果'。")
    lines.append("2. 各章不得违反上方'本卷禁止事项'。")
    lines.append("3. 若某章核心事件接近某条伏笔的回收节点或触及 forbidden 边界，"
                 "必须在 boundary_note 字段显式标注'需人工确认：<原因>'，不得自行决定放行。")
    lines.append("4. 每章 core_event 用 20-40 字一句话，写清'发生什么'，不写氛围描写。")
    lines.append("")
    # 爆款爽点节奏（根因：旧中纲整段压抑向无爽点，读者流失）
    lines.append("【爆款爽点节奏（每 10 章批次必须满足，防止结构塌缩/无爽点）】")
    lines.append("- 每 10 章内必须包含：≥2 冲突章（正面对抗/打脸/羞辱反转）、≥1 揭示章"
                 "（隐藏信息部分揭开/金手指线索/身份暗示）、≥1 爽点章（主角逆风翻盘/"
                 "打脸反派/获得资源或认可的情绪峰值）。")
    lines.append("- 爽点章的 core_event 必须写清'谁被打脸/谁翻盘/峰值情绪是什么'，不得只写氛围。")
    lines.append("- 同类型间隔 ≥3 章；不得连续 3 章以上为同一类型；压抑/边缘化情节每 3 章后"
                 "必须接一次反转或进展，不得无限累积。")
    lines.append("- 每章 core_event 末尾暗含一个章末钩子（未解决危机/新悬念/下章将发生的暗示），"
                 "不得以抒情或总结收尾。")
    lines.append("")
    lines.append("【角色代号】C001=陆烬（主角，婴儿-幼年期，第一阶段禁修炼动作）；"
                 "C002=陈老根（养父，可练习呼吸法）；C003/C004/C005/C006/C007/C008="
                 "后续登场的村民/江湖/仙门角色（按剧情需要引入，不要凭空造新代号）。")
    lines.append("")
    lines.append("【输出格式（非常重要）】不要输出 JSON，不要输出代码块，不要输出任何解释。"
                 "每章严格输出一行，用 | 分隔五个字段，格式为：")
    lines.append("ch<章节号>|<core_event>|<characters 逗号分隔>|<boundary_note 或 无>")
    lines.append("示例：ch21|陈老根带陆烬首次进县城赶集，偶遇猎户老周|C001,C002|无")
    lines.append("其中 boundary_note 仅在接近伏笔回收/触及 forbidden 边界时写"
                 "'需人工确认：<原因>'，否则写'无'。原因里涉及伏笔时，"
                 "优先用上方时间表的 fid（如 f005_fog_core）标注，legacy F 编号（F005）也可，均会被校验。")
    lines.append("")
    lines.append("请为以下章节逐一输出（每章一行，共 %d 行）：" % len(chapters))
    lines.append("".join(f"ch{c}|" for c in chapters))
    return "\n".join(lines)


def _build_client():
    """Build an LLMClient from config/llm_providers.json active_profile.

    The legacy runtime_config.json (llm.api_base / api_key_env) no longer ships in
    the condensed-only repo; the single source of truth is llm_providers.json
    (active_profile -> base_url / api_key_env / timeout_s / phases.<name>.models).
    """
    import json as _json
    from novel_engine.core.llm_client import LLMClient

    providers_path = ROOT / "config" / "llm_providers.json"
    if not providers_path.exists():
        raise FileNotFoundError(
            f"llm_providers.json not found at {providers_path}; cannot resolve active LLM profile")
    cfg = _json.loads(providers_path.read_text(encoding="utf-8"))
    active = cfg["active_profile"]
    profile = cfg["profiles"][active]
    base_url = profile.get("base_url", "")
    api_key_env = profile.get("api_key_env", "LLM_API_KEY")
    api_key = os.environ.get(api_key_env) or os.environ.get("AGNES_API_KEY") or ""
    # Fall back to the .env file next to the package when the env var is unset.
    if not api_key:
        for env_candidate in (ROOT / ".env", ROOT.parent / ".env"):
            if env_candidate.exists():
                for line in env_candidate.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith(api_key_env + "="):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
                if api_key:
                    break
    # model: first phase that carries models (director for planning, else outline/scenes)
    model = ""
    phases = profile.get("phases") or {}
    for pname in ("director", "outline", "scenes"):
        ms = (phases.get(pname) or {}).get("models") or []
        if ms:
            model = ms[0]
            break
    if not model:
        for c in phases.values():
            ms = (c or {}).get("models") or []
            if ms:
                model = ms[0]
                break
    if not model:
        model = profile.get("default_model", "agnes-3.0-flash")
    timeout = int(profile.get("timeout_s", 120))
    return LLMClient(api_base=base_url, model=model, api_key=api_key, timeout=timeout)


async def _generate_batch(prompt: str, logger: logging.Logger,
                          retries: int = 2) -> list[dict]:
    """一次 LLM 调用生成一批中纲条目（JSON 数组）。

    单批失败（空响应/解析失败）自动重试 retries 次（每批独立，不 abort 整轮）；
    重试耗尽返回 []，由上层记录缺失章并继续。
    """
    from novel_engine.core.llm_client import call_llm
    client = _build_client()
    last_err = "unknown"
    for attempt in range(retries + 1):
        try:
            raw = call_llm(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                client=client,
                output_json=False,  # 行格式解析，不用 output_json
            )
        except Exception as exc:
            last_err = str(exc)
            logger.warning("batch LLM call failed (attempt %d/%d): %s",
                           attempt + 1, retries + 1, last_err[:200])
            continue
        text = str(raw).strip()
        # 去掉可能的 markdown 代码块外壳
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json") or text.startswith("text"):
                text = text[4:].strip()
        # 行解析：每行 ch<N>|core_event|characters|boundary_note
        parsed: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("ch") is False:
                continue
            m = re.match(r"^ch(\d+)[|｜\s](.*)$", line)
            if not m:
                continue
            ch = int(m.group(1))
            fields = [f.strip() for f in re.split(r"[|｜]", m.group(2), maxsplit=3)]
            core_event = fields[0] if fields else ""
            chars = [c.strip() for c in fields[1].split(",") if c.strip()] if len(fields) > 1 else []
            note = fields[2] if len(fields) > 2 and fields[2] not in ("无", "-") else None
            parsed.append({"chapter": ch, "core_event": core_event,
                           "characters": chars, "boundary_note": note})
        if parsed:
            return parsed
        logger.warning("batch LLM text not parseable (attempt %d/%d): %s",
                       attempt + 1, retries + 1, text[:200])
        continue
    logger.error("batch LLM exhausted retries: %s", last_err[:200])
    return []


async def _run(
    vid: str,
    start: int,
    end: int,
    batch_size: int,
    out_path: Path,
    logger: logging.Logger,
    only_chapters: list[int] | None = None,
) -> int:
    volumes = _load_json(ROOT / "config" / "planning" / "volumes.json")
    plot_graph = _load_json(ROOT / "config" / "planning" / "plot_graph.json")
    registry = _load_json(ROOT / "config" / "foreshadow" / "registry.json")
    bible = _read_bible()

    volume = next((v for v in volumes.get("volumes", []) if v.get("id") == vid), {})
    if not volume:
        logger.error("volume %s not found in volumes.json", vid)
        return 2
    v_range = volume.get("chapter_range", [start, end])
    if not (v_range[0] <= start <= end <= v_range[1]):
        logger.warning("range %d-%d exceeds volume %s range %s; proceeding anyway",
                       start, end, vid, v_range)

    # 范围内及前后 20 章的节点
    nodes = plot_graph.get("nodes", [])
    nearby_nodes = [n for n in nodes
                    if abs(int(n.get("chapter_target", 0)) - start) <= 20
                    or (int(n.get("chapter_target", 0)) <= end + 20
                        and int(n.get("chapter_target", 0)) >= start - 20)]
    node_chapters = {int(n.get("chapter_target", 0)) for n in nodes}

    # 范围内相关伏笔（plant<=ch<=resolve 或接近 resolve）
    relevant_foreshadows = [
        fs for fs in registry.get("foreshadows", [])
        if int(fs.get("plant_chapter", 0)) <= end + 30
        and int(fs.get("resolve_chapter", 0)) >= start - 30
    ]

    forbidden = _current_volume_forbidden(bible.get("author_intent", ""), vid, start)

    # 世界状态摘要（可选；condensed-only 部署无 engine.db，缺省为空）
    world_state_summary = ""
    try:
        from novel_engine.engine.db import StateDB
        db = StateDB(project_root=ROOT)
        try:
            ws = db.execute_custom_query(
                "SELECT * FROM world_state ORDER BY chapter DESC LIMIT 1", ())
            world_state_summary = json.dumps(ws, ensure_ascii=False)[:800] if ws else ""
        except Exception:
            world_state_summary = ""
    except Exception:
        # engine 模块已移除或无历史世界状态：跳过，不影响中纲生成。
        world_state_summary = ""

    # 无节点章列表
    all_in_range = [c for c in range(start, end + 1)]
    if only_chapters:
        no_node_chapters = [c for c in only_chapters
                            if start <= c <= end and c not in node_chapters]
    else:
        no_node_chapters = [c for c in all_in_range if c not in node_chapters]
    if not no_node_chapters:
        logger.info("nothing to generate for %s (only=%s)", all_in_range, only_chapters)
        return 0
    logger.info("node-free chapters in %d-%d: %d (skip node chapters %s)",
                start, end, len(no_node_chapters),
                sorted(node_chapters & set(range(start, end + 1))))

    # 分批生成
    entries: dict[str, dict] = {}
    if only_chapters and out_path.exists():
        # --only 模式：先读已有产物，只覆盖指定章
        try:
            _existing = json.loads(out_path.read_text(encoding="utf-8"))
            for k, v in (_existing.get("entries") or {}).items():
                if isinstance(v, dict):
                    entries[str(k)] = v
        except Exception:
            entries = {}
    for i in range(0, len(no_node_chapters), batch_size):
        batch = no_node_chapters[i:i + batch_size]
        prompt = _build_batch_prompt(
            vid, batch, volume, forbidden, bible, nearby_nodes,
            relevant_foreshadows, world_state_summary)
        logger.info("batch %d/%d START chapters %s",
                    i // batch_size + 1,
                    (len(no_node_chapters) + batch_size - 1) // batch_size,
                    batch)
        data = await _generate_batch(prompt, logger)
        logger.info("batch %d DONE got %d entries",
                    i // batch_size + 1, len(data) if data else 0)
        if not data:
            logger.error("batch %d returned empty after retries; "
                         "chapters %s left missing (will be reported).",
                         i // batch_size + 1, batch)
            for c in batch:
                entries[str(c)] = {
                    "chapter": c, "core_event": "",
                    "characters": [], "boundary_note": "需人工确认：本批生成失败，待重跑",
                }
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            ch = int(item.get("chapter", 0))
            if ch not in no_node_chapters:
                logger.warning("LLM returned out-of-range chapter %s; dropped", ch)
                continue
            entries[str(ch)] = {
                "chapter": ch,
                "core_event": str(item.get("core_event", "")).strip(),
                "characters": item.get("characters") or [],
                "boundary_note": item.get("boundary_note") or None,
            }
        missing = [c for c in batch if str(c) not in entries]
        if missing:
            logger.warning("batch missing chapters: %s", missing)

    # 输出
    meta = {
        "vid": vid,
        "range": [start, end],
        "node_chapters_in_range": sorted(node_chapters & set(range(start, end + 1))),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "batch_size": batch_size,
        "status": "draft",  # 必须经 audit_mid_outline.py 校验后才可交付 Director
    }
    payload = {"meta": meta, "entries": dict(sorted(entries.items()))}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("wrote %d entries to %s", len(entries), out_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate mid-level chapter outlines (draft) for node-free chapters.")
    parser.add_argument("--vid", required=True, help="Volume id, e.g. V01")
    parser.add_argument("--start", type=int, required=True, help="First chapter (inclusive)")
    parser.add_argument("--end", type=int, required=True, help="Last chapter (inclusive)")
    parser.add_argument("--batch-size", type=int, default=6,
                        help="Chapters per LLM call (default 6)")
    parser.add_argument("--only", default=None,
                        help="Comma-separated chapters to regenerate only "
                             "(merged into existing output file, e.g. 31,48)")
    parser.add_argument("--out", default=None,
                        help="Output path override (default config/planning/"
                             "mid_outline_<vid>_ch<start>-<end>.json)")
    args = parser.parse_args(argv)

    _load_env()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger("generate_mid_outline")
    if not (os.environ.get("AGNES_TOKEN_API_KEY") or os.environ.get("AGNES_API_KEY")
            or os.environ.get("LLM_API_KEY")):
        logger.error("No API key (AGNES_TOKEN_API_KEY/AGNES_API_KEY/LLM_API_KEY). Aborting.")
        return 2

    out = args.out
    if not out:
        out = str(ROOT / "config" / "planning" /
                  f"mid_outline_{args.vid}_ch{args.start}-{args.end}.json")
    only_chapters = None
    if args.only:
        only_chapters = [int(x) for x in args.only.split(",") if x.strip().isdigit()]
    return asyncio.run(_run(
        args.vid, args.start, args.end, args.batch_size, Path(out), logger,
        only_chapters=only_chapters))


if __name__ == "__main__":
    raise SystemExit(main())
