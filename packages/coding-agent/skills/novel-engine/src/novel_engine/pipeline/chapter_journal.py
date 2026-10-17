"""Runner-owned chapter persistence (Q6/Q11). Writer produces memory objects only."""
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# CC round-27：单场正文硬护栏（字符数）。一场正文若超过【整章目标】体量，必为 LLM 失控
# （把整章/多场内容吐进一个 scene，r39 实测 scene1=20047）。这类记录保留在 append-only
# 审计日志中，但不得成为权威正文；权威读取回退到该 scene 上一条合理版本。
SCENE_RUNAWAY_DEFAULT_CHARS = 8000

# CC28：真机运行时由 production_runner 置 "1"，在【写入层】启用场景语言纯度硬闸——
# 纯英文词汇瀑布等“整段非中文退化输出”直接拒写 journal，由上游重试。离线/测试不置位，
# 保持 append_scene 原语行为不变；也可用 append_scene(enforce_language=...) 显式覆盖。
LANG_ENFORCE_ENV = "NOVEL_ENGINE_REAL_LANG_ENFORCE"


def _language_enforcement_enabled(override) -> bool:
    if override is not None:
        return bool(override)
    return os.environ.get(LANG_ENFORCE_ENV, "") == "1"

def journal_path(root, chapter: int) -> Path:
    return Path(root) / "chapters" / "draft" / f"chapter_{chapter}_partial.jsonl"

def _path(root, chapter: int) -> Path:
    return journal_path(root, chapter)

def _load_plot_nodes() -> list:
    """Load plot_graph.json nodes once at module level for entity matching."""
    import json
    from pathlib import Path
    plot_graph_path = Path(
        r"D:\AI\Prime-Agent-Novel-Engine\Prime-Agent-Novel-Engine\packages\coding-agent\skills\novel-engine"
    ) / "src" / "novel_engine" / "config" / "planning" / "plot_graph.json"
    if plot_graph_path.exists():
        with plot_graph_path.open(encoding="utf-8") as f:
            data = json.load(f)
        nodes = data.get("nodes", [])
        # 候选 = character_bible 角色名（权威实体名）+ plot_graph 跨 node 短语
        import re
        from collections import Counter
        # 停用词：抽象/描述性词，非实体名
        _STOP = {"真实", "存在", "力量", "恐慌", "禁区", "心跳", "出现", "发现", "神色",
                 "身体", "体内", "异常", "迷雾", "边界", "婴儿", "村民", "呼吸", "法门",
                 "伏笔", "呼应", "首次", "隐隐", "共鸣", "察觉", "强身", "健体", "修炼",
                 "碎语", "自语", "外乡", "独自", "拾得", "严令", "远离", "传闻", "渐起",
                 "体弱", "多病", "残篇", "只言", "不提", "神色骤变", "阶段", "收束",
                 "核心", "人物", "关系", "触发", "点", "本卷", "主线"}
        # 1) character_bible 角色名（权威实体名）
        _bible = Path(__file__).resolve().parent.parent / "bible" / "character_bible.md"
        candidates = []
        if _bible.exists():
            for line in _bible.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^###\s+C\d+\s+(.+)$", line.strip())
                if m:
                    nm = m.group(1).split("（")[0].strip()
                    if nm and nm not in candidates:
                        candidates.append(nm)
        # 2) plot_graph description 跨 node 实体短语（>=2 个 node 出现，非停用词）
        freq = Counter()
        for node in nodes:
            desc = node.get("description", "")
            seen = set()
            for phrase in re.findall(r'[\u4e00-\u9fff]{2,}', desc):
                if phrase not in _STOP and phrase not in seen:
                    freq[phrase] += 1
                    seen.add(phrase)
        for p_, c in freq.items():
            if c >= 2 and p_ not in candidates:
                candidates.append(p_)
        return candidates
    return []

# Module-level candidate list, loaded once at import
_PLOT_ENTITY_CANDIDATES = _load_plot_nodes()

def _make_entities_json(scene: dict, plot_nodes: list = None) -> list:
    """Extract entity stubs from a scene dict for journal tracking via plot_graph match.
    Returns a JSON-serializable list of {canonical_name, status} dicts.
    Status is set to 'hypothesis' by default.
    
    Architecture:
    1. If plot_nodes provided (pre-built candidate list), use it for substring match.
    2. Otherwise fall back to module-level _PLOT_ENTITY_CANDIDATES.
    3. Substring match: scene_text contains candidate → record entity.
    3. No match → return [] (legal, don't fabricate entities).
    """
    entities = []
    scene_text = scene.get("scene_text", "")
    if not scene_text:
        return entities
    
    # Use provided plot_nodes or module-level candidates
    candidates = plot_nodes if plot_nodes is not None else _PLOT_ENTITY_CANDIDATES
    
    # Substring inclusion match: scene_text contains candidate → record entity
    for candidate in candidates:
        if candidate in scene_text:
            entities.append({"canonical_name": candidate, "status": "hypothesis"})
    
    # If no candidate matched scene_text, return [] (legal, don't fabricate entities)
    return entities

def append_scene(root, chapter: int, scene: dict, enforce_language: bool | None = None) -> bool:
    # CC28：真机写入层语言纯度硬闸。整段非中文退化输出（英文词汇瀑布等）直接拒写，
    # 不污染 append-only journal，返回 False 由上游判废重试。离线/测试默认不启用。
    if _language_enforcement_enabled(enforce_language):
        try:
            from novel_engine.quality.scene_language_gate import assess_scene_language
            _verdict = assess_scene_language(scene.get("scene_text", "") or "")
            if _verdict["is_degenerate"]:
                logger.warning(
                    f"append_scene ch{chapter} sid{scene.get('scene_id')} REJECTED as "
                    f"degenerate non-Chinese output ({_verdict['reasons']}); not written to journal")
                return False
        except Exception as _e:  # 护栏自身异常不得阻断正常写入
            logger.warning(f"scene language gate error ch{chapter}: {_e}")
    p = _path(root, chapter)
    p.parent.mkdir(parents=True, exist_ok=True)
    entities_json = json.dumps(_make_entities_json(scene), ensure_ascii=False)
    line = json.dumps({"scene_id": int(scene["scene_id"]), "scene_text": scene["scene_text"],
                       "hook": scene.get("hook", ""), "beats": list(scene.get("beats", [])),
                       "entities_json": entities_json}, ensure_ascii=False)
    json.loads(line)  # validate before write
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return True

def _load_entities_json(d: dict) -> list:
    """Read entities_json from a loaded dict; return empty list if field missing/empty."""
    ej = d.get("entities_json")
    if not ej:
        return []
    try:
        parsed = json.loads(ej) if isinstance(ej, str) else ej
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return []

def load_scenes(root, chapter: int) -> list[dict]:
    """Shared loader: validated scene dicts in file order; corrupt lines skipped.

    Canonical corrupt-line rule (Q11/Q15c): a line that is not valid JSON, has
    no integer scene_id, or is not a dict means that scene is incomplete — it is
    skipped and the file itself stays valid.
    """
    p = journal_path(root, chapter)
    scenes: list[dict] = []
    if not p.exists():
        return scenes
    for raw in p.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(raw)
            if not isinstance(d, dict):
                continue  # valid JSON but not a scene record
            scenes.append({"scene_id": int(d["scene_id"]), "scene_text": d.get("scene_text", ""),
                           "hook": d.get("hook", ""), "beats": list(d.get("beats", []) or []),
                           "entities_json": _load_entities_json(d)})
        except (ValueError, KeyError, TypeError):
            continue  # corrupt line = scene incomplete, file stays valid
    return scenes

def completed_scene_ids(root, chapter: int) -> set[int]:
    return {s["scene_id"] for s in load_scenes(root, chapter)}


class SceneContentMissingError(KeyError):
    """CC round-19 Q2：请求的 scene_id 在 partials 中没有任何非空内容记录。"""

    def __str__(self) -> str:
        return f"scene_id={self.args[0] if self.args else '?'} has no non-empty content in partials"


def get_authoritative_scene_content(records: list[dict], scene_id: int,
                                    max_chars: int | None = SCENE_RUNAWAY_DEFAULT_CHARS) -> str:
    """CC round-19 Q2：唯一权威读取入口。

    partials 是 append-only 审计日志（同 scene_id 可能有多条，且可能混入 len=0 的
    空记录）。任何"要拿某场景最终正文"的地方都必须走本函数：按写入顺序取该 scene_id
    的【最后一条非空且未失控】记录；没有任何非空记录则抛 SceneContentMissingError，
    不允许调用方各自取第一条/最后一条而拿到空稿或过期稿。

    CC round-27：长度超过 max_chars 的记录视为 LLM 失控（整章体量误吐进单场），不作为
    权威、回退到该 scene 上一条合理记录；只有当该 scene 全部非空记录都失控时，才退回
    最后一条非空（避免整章缺失），并记 warning。max_chars=None 可关闭护栏（测试/特殊用途）。
    """
    sid = int(scene_id)
    accepted = None   # 最后一条：非空且长度合法
    fallback = None   # 最后一条非空（即使失控），极端兜底
    runaway = 0
    for rec in records:
        try:
            if int(rec.get("scene_id")) != sid:
                continue
        except (TypeError, ValueError):
            continue
        txt = rec.get("scene_text", "") or ""
        if txt.strip():
            fallback = txt
            if max_chars is None or len(txt) <= int(max_chars):
                accepted = txt
            else:
                runaway += 1
    if accepted is not None:
        if runaway:
            logger.warning(f"scene_id={sid}: skip {runaway} runaway record(s) >{max_chars} "
                           f"chars when resolving authoritative content")
        return accepted
    if fallback is not None:
        logger.warning(f"scene_id={sid}: all non-empty records runaway (>{max_chars}); "
                       f"using last non-empty ({len(fallback)} chars) as last resort")
        return fallback
    raise SceneContentMissingError(sid)


def load_authoritative_scenes(root, chapter: int, max_chars: int | None = None) -> list[dict]:
    """CC round-19 Q2：每个 scene_id 仅返回其权威记录，按 scene_id 升序。

    用于组装成稿 / 门链等"读最终场景内容"的场景，保证内存态、journal、成稿三者
    看到的是同一份权威内容；全部为空的 scene_id 不出现在结果中（交由韧性/重生流程
    按 blueprint 数量补齐），而不是把空稿带进成稿。

    CC round-27：权威记录还必须未失控（长度<=max_chars，缺省取 quality_policy 的
    chapter_target_chars，回退 SCENE_RUNAWAY_DEFAULT_CHARS）；失控记录回退上一合理版。
    """
    recs = load_scenes(root, chapter)
    if max_chars is None:
        try:
            from novel_engine.core.quality_policy import load_quality_policy
            max_chars = int(load_quality_policy(root)["chapter_target_chars"])
        except Exception:
            max_chars = SCENE_RUNAWAY_DEFAULT_CHARS
    accepted: dict[int, dict] = {}
    fallback: dict[int, dict] = {}
    for rec in recs:
        sid = int(rec["scene_id"])
        if not (rec.get("scene_text", "") or "").strip():
            continue
        fallback[sid] = rec  # 文件即写入顺序，后者覆盖前者 = 最新
        if max_chars is None or len(rec.get("scene_text", "") or "") <= int(max_chars):
            accepted[sid] = rec
    chosen = {sid: (accepted[sid] if sid in accepted else fallback[sid])
              for sid in sorted(set(fallback) | set(accepted))}
    return [{"scene_id": sid,
             "scene_text": rec.get("scene_text", ""),
             "hook": rec.get("hook", ""),
             "beats": list(rec.get("beats", []) or []),
             "entities_json": _load_entities_json(rec)}
            for sid, rec in chosen.items()]


def snapshot_journal(root, chapter: int):
    """CC28 批B：捕获某章 journal 的整文件快照（bytes）。

    用于 fix 循环把“最佳候选”与其对应的 journal 事实态绑定。文件不存在时返回 None
    （恢复时即代表“应无文件”）。仅读字节，不解析，保留全部审计记录。
    """
    p = journal_path(root, chapter)
    if not p.exists():
        return None
    return p.read_bytes()


def restore_journal(root, chapter: int, snapshot) -> bool:
    """CC28 批B：把某章 journal 原子恢复到快照（tmp + os.replace）。

    fix 循环的各轮修复（_patch_weak_scenes / CC25 文学重写 / 整章 rewrite）都会就地
    覆写 journal；字符串层 best-of 只回滚内存不回滚 journal，会导致劣稿成事实。候选
    被采纳时用本函数把 journal 一并回滚到该候选的快照。snapshot=None 表示目标态无文件，
    则删除当前文件（若存在）。
    """
    p = journal_path(root, chapter)
    p.parent.mkdir(parents=True, exist_ok=True)
    if snapshot is None:
        try:
            if p.exists():
                p.unlink()
        except OSError as e:
            logger.warning(f"restore_journal ch{chapter} unlink failed: {e}")
            return False
        return True
    tmp = p.with_suffix(p.suffix + ".rollback.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(snapshot)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        return True
    except OSError as e:
        logger.warning(f"restore_journal ch{chapter} atomic replace failed: {e}")
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def authoritative_completed_scene_ids(root, chapter: int) -> set[int]:
    """CC round-19 Q2：只有"存在非空正文记录"的 scene_id 才算已完成。

    append 幂等守卫用：len=0 的空记录不得让后来的优质 live 场景被误判为已落盘而跳过追加。
    """
    return {int(s["scene_id"]) for s in load_authoritative_scenes(root, chapter)}
