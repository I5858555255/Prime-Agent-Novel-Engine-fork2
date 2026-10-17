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


async def _generate_batch(prompt: str, logger: logging.Logger,
                          retries: int = 2) -> list[dict]:
    """一次 LLM 调用生成一批中纲条目（JSON 数组）。

    单批失败（空响应/解析失败）自动重试 retries 次（每批独立，不 abort 整轮）；
    重试耗尽返回 []，由上层记录缺失章并继续。
    """
    from novel_engine.core.llm_client import call_llm
    from novel_engine.core.llm_client import _load_runtime_config, LLMClient
    cfg = _load_runtime_config() or {}
    llm_cfg = cfg.get("llm", {}) or {}
    # api_key 显式解析：按 runtime_config 的 api_key_env 字段读取
    # （当前 active provider 是 agnes → AGNES_API_KEY）。
    # 修复：SILICONFLOW_API_KEY 从优先级移除/置后——系统环境变量里残留的硅基 key
    # 曾被优先选中、发到 agnes 导致 401 Invalid token（2026-10-10 中纲生成事故）。
    api_key = (
        os.environ.get(llm_cfg.get("api_key_env", "LLM_API_KEY"))
        or os.environ.get("AGNES_API_KEY")
        or os.environ.get("LLM_API_KEY")
        or ""
    )
    client = LLMClient(
        api_base=llm_cfg.get("api_base"),
        model=llm_cfg.get("model"),
        api_key=api_key,
        timeout=int(llm_cfg.get("timeout_seconds", 120)),
    )
    last_err = "unknown"
    for attempt in range(retries + 1):
        try:
            raw = call_llm(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                client=client,
                output_json=False,  # siliconflow DeepSeek 的 output_json 模式系统性返回空 content
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

    # 世界状态摘要（用 ch=start 的状态，作为承接参考）
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
    if not (os.environ.get("AGNES_API_KEY") or os.environ.get("LLM_API_KEY")):
        logger.error("No API key (AGNES_API_KEY/LLM_API_KEY). Aborting.")
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
