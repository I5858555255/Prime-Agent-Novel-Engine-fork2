"""
缩写生成 Agent + 正文生成 Agent。
阶段2：缩写生成（500字缩写 + 待提交状态变更）
阶段3：正文生成（场景级分段生成）
"""
import json
import logging
import re
import threading
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from novel_engine.core.llm_client import LLMClient, call_llm
from novel_engine.core.quality_policy import load_quality_policy, derive_scene_targets
from novel_engine.agents.scene_schema import (
    SceneOutput, parse_scene, build_scene_prompt, validate_scene_text,
    detect_scene_overlap,
)
from novel_engine.core.errors import SceneUnrecoverableError

logger = logging.getLogger(__name__)
def _get_root():
    """获取项目根目录。"""
    return Path(__file__).parent.parent




class SynopsisAgent:
    """缩写生成 Agent：根据任务卡生成500字缩写 + 状态变更提案。"""

    SYSTEM_PROMPT = """你是一位严谨的网络小说缩写生成器。你的任务是根据章节任务卡，生成500字左右的剧情缩写。

要求：
1. 严格按照 scene_blueprints 的顺序组织场景
2. 每个场景的 goal 必须在缩写中体现
3. 缩写要包含关键情节转折和情绪变化
4. 在缩写末尾列出待提交的状态变更（JSON数组）
5. 遵守伏笔动作指令

输出格式：
{{
  "chapter_num": 1,
  "synopsis": "500字左右的剧情缩写...",
  "state_changes": [
    {{
      "type": "character_realm|character_location|relationship_update|timeline_event",
      "target": "目标ID或关系ID",
      "new_value": "新值",
      "chapter": 1
    }}
  ],
  "foreshadow_execution": [
    {{
      "foreshadow_id": "F001",
      "executed": true,
      "note": "如何执行的"
    }}
  ]
}}"""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or LLMClient()

    def generate_synopsis(self, task_card: dict) -> dict:
        """根据任务卡生成缩写。"""
        chapter_num = task_card.get("chapter_num", 0)

        prompt = f"""请为第 {chapter_num} 章生成剧情缩写。

## 任务卡
{json.dumps(task_card, ensure_ascii=False, indent=2)}

## 约束
- 严格遵守 scene_blueprints 顺序
- 每个场景 goal 必须完成
- 执行所有 foreshadow_actions
- 字数控制在450-550字"""

        try:
            synopsis = call_llm(
                prompt=prompt,
                system_prompt=self.SYSTEM_PROMPT,
                client=self.llm,
                output_json=True,
            )
            logger.info(f"Synopsis generated for chapter {chapter_num}")
            return synopsis
        except Exception as e:
            logger.error(f"Failed to generate synopsis for chapter {chapter_num}: {e}")
            raise

    def build_synopsis_from_task_card(self, task_card: dict) -> dict:
        """无 LLM 的兜底：从任务卡确定性构造缩写（用于合并模式下模型未输出 synopsis 时）。"""
        chapter_num = task_card.get("chapter_num", 0)
        parts = [task_card.get("core_goal", "")]
        for bp in task_card.get("scene_blueprints", []):
            goal = bp.get("goal", "")
            if goal:
                parts.append(f"场景{bp.get('scene_num', '?')}：{goal}")
        synopsis_text = "。".join(p for p in parts if p).strip()
        if synopsis_text and not synopsis_text.endswith("。"):
            synopsis_text += "。"
        return {
            "chapter_num": chapter_num,
            "synopsis": synopsis_text,
            "state_changes": task_card.get("state_changes", []) or [],
            "foreshadow_execution": task_card.get("foreshadow_execution", []) or [],
        }



def validate_scene_order(full_text: str, task_card: dict) -> bool:
    """Validate that scenes appear in the correct order as specified in task card."""
    import re
    blueprints = task_card.get("scene_blueprints", [])
    if not blueprints:
        return True
    
    # Find scene markers in text
    scene_pattern = r"【场景\d+：[^】]*】"
    scenes_in_text = re.findall(scene_pattern, full_text)
    
    if len(scenes_in_text) < len(blueprints):
        return False
    
    # Check order matches
    for i, bp in enumerate(blueprints):
        scene_num = bp.get("scene_num", i + 1)
        if f"【场景{scene_num}：" not in scenes_in_text[i]:
            return False
    
    return True


class WriterAgent:
    """正文生成 Agent：按场景拆分生成正文。"""

    SCENE_SYSTEM_PROMPT = """你是一位专业的网络小说作家（凡人流写实，参考《凡人修仙传》《仙逆》笔法）。你的任务是根据场景蓝图生成高质量的正文段落。

写作要求：
1. 第三人称有限视角（以**陆烬**为主视角），视角深度随年龄动态变化（婴儿期仅感官，无推理）
2. 半文半白，通俗易懂但有古风韵味；每段最多一个比喻，比喻必须服务信息
3. 对话符合角色身份
4. 战斗描写简洁有力，重意境轻招式罗列
5. 心理描写克制内敛，通过动作和环境折射
6. 环境描写服务于情绪，不单独铺陈超过200字；穿越/通道/传送等过渡场景为功能场景，单场景正文不超过800字，禁止纯感官铺陈堆叠
7. 全章总字数目标与每个场景的目标字数以用户指令中给出的”目标字数”为准，各场景目标之和应≈全章目标，严禁写短
8. 遵守 scene_blueprints 中的 goal/conflict/emotion
9. 若任务卡中有 foreshadow_actions，必须在本场景中执行
10. 严禁套话堆砌（”死水石子/古井/未出鞘/达摩克利斯/如野草疯长”等出现即扣分）
11. 【句长纪律】以中短句为主、一句一义：凡超过约40字的句子主动断成两句或三句，不得用逗号串起三个以上分句形成流水句；同义氛围词不得堆叠，每个氛围词只出现一次
12. 【发展场反饱和】非高潮场景必须包含：①一个具体外部事件（可观察的动作/对话，不纯内心）；②一个具名对手戏角色并发生实际互动；③一个不可逆的新状态或新信息推进下一章钩子；静态氛围/心理独白占比不得超过全场景30%

禁忌：
- 禁止现代词汇、西典词汇
- 禁止OOC
- 禁止无意义水字数对话
- 禁止主角光环过强（每次胜利必须有代价）
- 【语言硬约束】正文必须 100% 中文：严格禁止出现任何英文字母、英文单词、英文句子或拉丁字符（连一个英文单词也不允许）；人名地名功法名一律使用中文或约定译名，不得用英文原文；若发现自己写出了英文，必须立即改用中文重写该句
- 必须按照 scene_blueprints 顺序生成，不得打乱
- 必须完整呈现所有场景，不得截断
- 严格执行 foreshadow_actions 中的所有伏笔指令
- 禁止人物台词风格与设定不符
- 禁止场景内容重复；若多个场景属于同一时刻的同一事件（如发现婴儿/某人介入），只允许一个场景详细展开，其余场景必须推进时间线或展开新事件，严禁换视角重述已发生内容
- 严禁超龄认知（婴儿期出现”棋局/十年后”等未来知识直接判失败）
- 【婴儿期/低龄章节禁写修炼体系词】凡主角处于婴儿/幼童/低龄（agency=infant_*）阶段，scene_text 严禁出现修炼体系词汇：吐纳、调息、口诀、呼吸法、法门、功法、丹田、经脉、灵根、气感、引气、炼气、筑基、武者、仙门、传功、传授、行气、内息、内功、守护者。婴儿视角只写感官与动作；成年角色的修炼背景一律用普通日常语言侧写（如”夜间静坐”“呼吸节奏”“那套动作”），严禁直接铺陈修炼术语；婴儿自身不得出现任何修炼行为描述

输出契约：
- 输出必须为严格 JSON（结构见用户指令中的”结构化输出契约”），scene_text 为纯叙事正文
- 严禁输出散文体或任何脚手架标记（【】/※/（）/章末钩子字样）"""

    def __init__(self, llm_client: Optional[LLMClient] = None, project_root: str | Path = None):
        self.llm = llm_client or LLMClient()
        self.root = Path(project_root or Path(__file__).parent.parent)
        self._bible_cache: dict[str, str] = {}
        # B2：最近一次成功生成的结构化场景（供管道做结构复核；mock/改写路径下为空）
        self.last_scenes: list[SceneOutput] = []
        # CC30（DS Q1/Q2/Q3）：按场 flash->pro 故障转移。默认关闭；真机由 orchestrator
        # 调 set_pro_failover(enabled=True, client=refine_router=agnes-2.5-pro) 开启。
        self._failover_enabled = False
        self.pro_client = None
        from novel_engine.quality.scene_model_router import ProSceneBudget
        from novel_engine.quality.degrade_window import DegradeWindow
        self._fb_budget = ProSceneBudget()   # 跨章持久：10章/50章 pro 预算
        self._fb_degrade = DegradeWindow()   # 跨章持久：退化滑窗状态机
        self._fb = None                       # 本章 SceneFailoverController
        self._fb_idx_of_scene: dict[int, int] = {}
        # CC30 Q2：退化窗口 P0/P2 全局冷却协调（多场景线程共享同一截止时刻）
        self._fb_pause_lock = threading.Lock()
        self._fb_pause_until = 0.0
        self._load_bible_cache()

    def _load_bible_cache(self):
        for key, rel in {
            "world": "bible/world_bible.md",
            "character": "bible/character_bible.md",
            "style": "bible/style_bible.md",
            "author_intent": "bible/author_intent.md",
        }.items():
            p = self.root / rel
            try:
                self._bible_cache[key] = p.read_text(encoding="utf-8") if p.exists() else ""
            except OSError:
                self._bible_cache[key] = ""

    def _extract_current_volume_forbidden(self, chapter_num: int) -> list[str]:
        """author_intent 当前卷 forbidden 列表（完整提取，不截断）。

        按"## 第N阶段：xxx（第A-B章）"标题切分定位当前章所属阶段，
        收集该阶段内 `- "..."` 列表项。定位失败返回空列表（不阻断）。
        """
        _ai = (self._bible_cache.get("author_intent", "") or "")
        if not _ai:
            return []
        import re
        for _blk in re.split(r"^## ", _ai, flags=re.M):
            _m = re.match(
                r"第[一二三四五六七八九十]+阶段[：:].*?（.*?第?(\d+)-(\d+)章?）",
                _blk)
            if not _m:
                continue
            _lo, _hi = int(_m.group(1)), int(_m.group(2))
            if _lo <= chapter_num <= _hi:
                return [_ln.strip().lstrip("- ").strip('"')
                        for _ln in _blk.splitlines()
                        if _ln.strip().startswith("-")]
        return []

    def _locate_bible_by_names(
        self, key: str, names: list[str], fallback_chars: int = 600) -> str:
        """按实体名定位 bible 相关段落；定位失败退回固定截断兜底。

        names 为空或无有效名时直接走 fallback；命中行保留并拼接（上限
        fallback_chars*2，避免超长段落撑爆 prompt）。
        """
        text = (self._bible_cache.get(key, "") or "").strip()
        if not text:
            return ""
        _needles = [n for n in names if n and len(str(n).strip()) >= 2]
        if not _needles:
            return text[:fallback_chars]
        _needles = [str(n) for n in _needles]
        lines = text.splitlines()
        hit_lines = [ln for ln in lines if any(n in ln for n in _needles)]
        if not hit_lines:
            return text[:fallback_chars]
        return "\n".join(hit_lines)[:fallback_chars * 2]

    def _bible_snippet(self, chapter_num: int = 0,
                       character_names: list[str] | None = None,
                       location_names: list[str] | None = None,
                       max_chars: int = 1500) -> str:
        """按卷定向注入（替代固定 world[:600]+character[:600] 截断）。

        核心变更：author_intent 当前卷 forbidden 完整注入（不再缺失）；
        character/world 优先按本章实体名定位相关段落，定位失败退回固定
        截断兜底（保留原 600 作为兜底值，不新增风险）。
        """
        characters = [str(c) for c in (character_names or []) if c]
        locations = [str(l) for l in (location_names or []) if l]
        parts = []
        if chapter_num:
            forbidden = self._extract_current_volume_forbidden(chapter_num)
            if forbidden:
                parts.append(
                    f"【本卷禁止事项（务必遵守）】{('；'.join(forbidden))}")
        world = self._locate_bible_by_names("world", locations)
        if world.strip():
            parts.append(f"【世界观】{world.strip()}")
        char = self._locate_bible_by_names("character", characters)
        if char.strip():
            parts.append(f"【人物卡】{char.strip()}")
        text = "\n\n".join(parts)
        return text[:max_chars]

    def _scene_strict_models(self) -> list:
        """返回按 strict JSON 解析的场景模型列表（这些模型均以 response_format=json_object 调用）。

        优先级：客户端已绑定的 .models（标量或列表均可）；否则取 llm_providers.json
        当前 active profile 的 scenes 模型；再退回 runtime_config 主模型；最终 Agnes。
        """
        models = getattr(self.llm, "models", None)
        if models:
            return list(models) if isinstance(models, (list, tuple)) else [models]
        # 权威来源：llm_providers.json 当前 active profile 的 scenes 模型
        try:
            pc = json.loads((self.root / "config" / "llm_providers.json").read_text(encoding="utf-8"))
            prof = (pc.get("profiles") or {}).get(pc.get("active_profile"), {}) or {}
            sm = ((prof.get("phases") or {}).get("scenes") or {}).get("models") or []
            if sm:
                return list(sm)
        except (OSError, ValueError, KeyError):
            pass
        # 兼容回退：runtime_config 主模型（由 LLM 选择器同步维护）
        try:
            cfg = json.loads((self.root / "config" / "runtime_config.json").read_text(encoding="utf-8"))
            mm = cfg.get("llm", {}).get("model")
            if mm:
                return [mm]
        except (OSError, ValueError, KeyError):
            pass
        return ["agnes-2.5-flash"]

    def _pro_strict_models(self) -> list:
        """CC30：可用于场景严格 JSON 重出的 pro 模型（取 phases.refine.models）。"""
        try:
            pc = json.loads((self.root / "config" / "llm_providers.json").read_text(encoding="utf-8"))
            prof = (pc.get("profiles") or {}).get(pc.get("active_profile"), {}) or {}
            rm = list(((prof.get("phases") or {}).get("refine") or {}).get("models") or [])
            if rm:
                return rm
        except (OSError, ValueError, KeyError):
            pass
        return ["agnes-2.5-pro"]
    # ---- CC30 按场 flash->pro 故障转移（real-only，默认休眠；mock 路径永不进入）----
    def set_pro_failover(self, enabled: bool, client=None):
        self._failover_enabled = bool(enabled)
        self.pro_client = client if enabled else None
        if enabled:
            self._attach_degen_observers(self.llm)
            if client is not None and client is not self.llm:
                self._attach_degen_observers(client)

    def _attach_degen_observers(self, router, _seen=None):
        """把退化事件观察者挂到 router 背后的具体 LLMClient（含 Failover/ModelRouter）。"""
        if router is None:
            return
        if _seen is None:
            _seen = set()
        if id(router) in _seen:
            return
        _seen.add(id(router))
        if isinstance(router, LLMClient):
            try:
                router.degenerate_observer = self._on_client_degenerate
            except Exception:
                pass
        pro = getattr(router, "providers", None)
        if isinstance(pro, dict):
            for _v in pro.values():
                self._attach_degen_observers(_v, _seen)
        elif isinstance(pro, (list, tuple)):
            for _v in pro:
                self._attach_degen_observers(_v, _seen)
        for _attr in ("primary", "fallback", "client", "_client"):
            _v = getattr(router, _attr, None)
            if _v is not None and _v is not router:
                self._attach_degen_observers(_v, _seen)

    def _on_client_degenerate(self, deg_cls: str):
        if not (self._failover_enabled and self._fb is not None):
            return
        import time as _t30
        action = self._fb.observe_client_degenerate(deg_cls, now=_t30.monotonic())
        if action and action.get("cooldown_seconds"):
            until = _t30.monotonic() + int(action["cooldown_seconds"])
            with self._fb_pause_lock:
                self._fb_pause_until = max(self._fb_pause_until, until)
            logger.warning(f"CC30 provider degrade {deg_cls} -> {action.get('level')} "
                           f"global cooldown {action['cooldown_seconds']}s")

    def _fb_wait_before_call(self):
        if not (self._failover_enabled and self._fb is not None):
            return
        import time as _t30
        d = self._fb_pause_until - _t30.monotonic()
        if d > 0:
            logger.warning(f"CC30 waiting out degrade cooldown {d:.0f}s")
            _t30.sleep(d)

    def begin_chapter_failover(self, task_card: dict, scenes: list, force_whole: bool = False):
        # 先把【上一章】最终 pro 计划计入跨章预算（重生阶段发生在初稿之后，故延后到此处结算）
        if self._failover_enabled and self._fb is not None:
            _p = self._fb.plan
            self._fb_budget.record_chapter(
                self._fb.chapter_num, _p.get("pro_scene_count", 0),
                whole_chapter_pro=bool(_p.get("whole_chapter_pro")))
        if not self._failover_enabled:
            self._fb = None
            return
        chapter_num = int(task_card.get("chapter_num", 0) or 0)
        nums = sorted(int(bp.get("scene_num", 0) or 0) for bp in (scenes or []))
        self._fb_idx_of_scene = {sn: i for i, sn in enumerate(nums)}
        from novel_engine.quality.scene_failover_controller import SceneFailoverController
        self._fb = SceneFailoverController(
            chapter_num, self._fb_budget, self._fb_degrade,
            force_whole_chapter=force_whole, n_scenes=len(nums), enabled=True)
        logger.info(f"CC30 failover armed ch{chapter_num} scenes={nums} force_whole={force_whole}")

    def finish_chapter_failover(self):
        """把本章 pro 计划用量计入跨章预算（上界口径）。"""
        if self._failover_enabled and self._fb is not None:
            plan = self._fb.plan
            self._fb_budget.record_chapter(
                self._fb.chapter_num, plan.get("pro_scene_count", 0),
                whole_chapter_pro=bool(plan.get("whole_chapter_pro")))

    def _fb_pick_client(self, scene_num: int):
        if (self._failover_enabled and self.pro_client is not None
                and self._fb is not None):
            idx = self._fb_idx_of_scene.get(int(scene_num))
            if idx is not None and self._fb.use_pro_for_scene(idx):
                return self.pro_client
        return self.llm

    def _fb_report(self, scene_num: int, out: SceneOutput, scene_blueprint: dict):
        if not (self._failover_enabled and self._fb is not None):
            return
        idx = self._fb_idx_of_scene.get(int(scene_num))
        if idx is None:
            return
        import time as _t30
        try:
            _wt = scene_blueprint.get("word_count_target", 2000)
            if isinstance(_wt, (list, tuple)):
                _wt = _wt[0] if _wt else 2000
            _floor = max(860, int(float(_wt) * 0.5))
        except (TypeError, ValueError):
            _floor = 860
        try:
            from novel_engine.quality.scene_language_gate import is_degenerate_non_chinese
            _latin = is_degenerate_non_chinese(out.scene_text or "")
        except Exception:
            _latin = False
        from novel_engine.quality.scene_failover_controller import classify_scene_attempt
        cls = classify_scene_attempt(
            scene_text=out.scene_text or "",
            beats_covered_count=len(getattr(out, "beats_covered", []) or []),
            finish_reason=getattr(out, "finish_reason", "") or "",
            short_floor=_floor, latin_wordstream=_latin)
        action = self._fb.report_scene_attempt(idx, cls, now=_t30.monotonic())
        if action and action.get("cooldown_seconds"):
            logger.warning(f"CC30 degrade cooldown {action['cooldown_seconds']}s "
                           f"level={action.get('level')} scene{scene_num}")
            _t30.sleep(int(action["cooldown_seconds"]))

    def _fb_report_waste(self):
        if self._failover_enabled and self._fb is not None:
            import time as _t30
            self._fb.report_parse_waste(now=_t30.monotonic())

    def generate_scene(
        self,
        task_card: dict,
        scene_blueprint: dict,
        chapter_synopsis: str,
        previous_context: str = "",
        pacing_constraints: str = "",
        temperature_override: Optional[float] = None,
        negative_examples: list[str] | None = None,
        all_blueprints: list[dict] | None = None,
        fix_directive: str | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
    ) -> SceneOutput:
        """生成单个场景（结构化契约）：模型只输出严格 JSON，经 parse_scene 解析为 SceneOutput。

        hook 为独立字段收集，永不与标记拼接进正文。strict 模型解析失败则再生一次，
        仍失败则抛错走补丁路径（绝不用正则清洗凑形）。
        """
        chapter_num = task_card.get("chapter_num", 0)
        scene_num = scene_blueprint.get("scene_num", 0)
        self._fb_wait_before_call()

        # Use deterministic prompt builder (pkg2e)
        bps = all_blueprints or task_card.get("scene_blueprints", []) or [scene_blueprint]
        prompt = build_scene_prompt(task_card, scene_blueprint, bps, negative_examples, fix_directive=fix_directive)

        # Add bible/perspective blocks (unchanged from before)
        # 设定贯通：按卷定向注入（当前卷 forbidden 完整 + 按本章实体定位 bible 段落）
        _char_names: list[str] = []
        _loc_names: list[str] = []
        try:
            for _bp in (task_card.get("scene_blueprints") or []):
                _p = _bp.get("participants") or _bp.get("characters") or []
                if isinstance(_p, list):
                    _char_names.extend(str(x) for x in _p if x)
                _loc = _bp.get("location") or _bp.get("place") or ""
                if _loc:
                    _loc_names.append(str(_loc))
        except Exception:
            pass
        bible_block = self._bible_snippet(
            chapter_num=chapter_num,
            character_names=_char_names or None,
            location_names=_loc_names or None)
        bible_section = f"\n\n## 人物/世界观显式设定（逐章注入，防 OOC/漂移）\n{bible_block}\n" if bible_block else ""
        perspective_block = ""
        if chapter_num <= 5:
            perspective_block = f"\n\n## 视角与时间线锚点\n- 当前时间：第 {chapter_num} 章（主角为新生儿/婴儿期，年龄≈0岁）\n- 严禁任何成人式全知内心独白、未来知识（\"棋局\"\"十年后\"等）与超龄推理；仅保留感官、本能与外部观察\n- 内心独白强度：0（无）\n"
        elif chapter_num <= 20:
            perspective_block = f"\n\n## 视角与时间线锚点\n- 当前时间：第 {chapter_num} 章（幼年期）\n- 心理描写需符合年龄，避免超龄谋略独白\n"
        else:
            perspective_block = f"\n\n## 视角与时间线锚点\n- 当前时间：第 {chapter_num} 章\n- 按当前年龄与状态描写心理，禁止时间线穿帮\n"

        full_prompt = f"{prompt}{bible_section}{perspective_block}"
        if pacing_constraints:
            full_prompt += f"\n\n## 节奏硬性约束\n{pacing_constraints}\n"
        if previous_context:
            full_prompt += f"\n\n## 前序场景摘要\n{previous_context}"
        # CC P0：注入近 N 章已演事件台账，禁止重演、只能写余波
        try:
            from novel_engine.pipeline.event_ledger import recent_event_block
            _ledger_block = recent_event_block(self.root, chapter_num)
            if _ledger_block:
                full_prompt += f"\n\n{_ledger_block}\n"
        except Exception:
            pass

        # CC30：pro 重出同样以 json_object 严格契约返回，需并入 strict 名单，避免
        # pro 模型名不在 scene 名单时被当宽松模型而 fail-open。
        strict_models = list(dict.fromkeys(self._scene_strict_models() + self._pro_strict_models()))

        # 给足输出预算：默认客户端 4096 tokens 会在约 1400-2000 中文字处被 finish_reason=length
        # 截断（JSON/beats 还占用预算）。按本场景目标字数换算并夹在 4096-8192。
        _mt_raw = scene_blueprint.get("word_count_target", 2000)
        if isinstance(_mt_raw, (list, tuple)):
            _mt_raw = _mt_raw[0] if _mt_raw else 2000
        try:
            _scene_max_tokens = max(4096, min(8192, int(float(_mt_raw) * 2.0) + 1024))
        except (TypeError, ValueError):
            _scene_max_tokens = 6144

        def _call_once() -> SceneOutput:
            # CC round-7: 摆烂/退化救援用 frequency/presence penalty 抑制自我重复，
            # 经 extra_body 作为 OpenAI 兼容顶层字段下发（缺省 None 不带，行为不变）。
            _extra = None
            if frequency_penalty is not None or presence_penalty is not None:
                _extra = {}
                if frequency_penalty is not None:
                    _extra["frequency_penalty"] = float(frequency_penalty)
                if presence_penalty is not None:
                    _extra["presence_penalty"] = float(presence_penalty)
            # CC round-25 P1(a): 记录单场景往返时延，便于近空时区分"连接层异常"与"完整但摆烂"。
            import time as _time25
            _t0 = _time25.monotonic()
            _signal25 = ""
            _client30 = self._fb_pick_client(scene_num)
            try:
                result = _client30.chat_completion(
                    [{"role": "system", "content": self.SCENE_SYSTEM_PROMPT},
                     {"role": "user", "content": full_prompt}],
                    temperature=temperature_override,
                    max_tokens=_scene_max_tokens,
                    extra_body=_extra,
                )
            except Exception as _exc25:
                _signal25 = f"error:{type(_exc25).__name__}"
                _out25 = SceneOutput(int(scene_blueprint.get("scene_num", 0) or 0),
                                     "", "", [])
                _out25.latency_ms = int((_time25.monotonic() - _t0) * 1000)
                _out25.http_signal = _signal25
                raise
            _lat_ms = int((_time25.monotonic() - _t0) * 1000)
            model_used = result.get("_model_used", "") if isinstance(result, dict) else ""
            raw = result.get("content", "") if isinstance(result, dict) else str(result)
            _out = parse_scene(raw or "", model_used, strict_models)
            # 透传 finish_reason，供上游区分 stop(摆烂) 与 length(截断) 两类失败
            if isinstance(result, dict):
                _out.finish_reason = str(result.get("finish_reason", "") or "")
            _out.latency_ms = _lat_ms
            # 连接/采样归因：length=截断（预算或提前收束）；往返异常快(<1.2s)且内容极短更像
            # 连接层给了不完整片段；正常往返但 content 短=模型采样退化（降并发无法解决）。
            _n25 = len(_out.scene_text or "")
            if _out.finish_reason == "length":
                _signal25 = "length_truncated"
            elif _lat_ms < 1200 and _n25 < 150:
                _signal25 = "suspicious_fast_short"
            else:
                _signal25 = "complete_but_short" if _n25 < int(_scene_max_tokens * 0.15) else "ok"
            _out.http_signal = _signal25
            return _out

        try:
            out = _call_once()
        except ValueError:
            self._fb_report_waste()
            logger.warning(f"Scene {scene_num} structured parse failed, regenerating once (chapter {chapter_num})")
            out = _call_once()
        if not out.scene_id:
            out.scene_id = int(scene_blueprint.get("scene_num", 0) or 0)
        self._fb_report(scene_num, out, scene_blueprint)
        logger.info(f"Scene {scene_num} generated for chapter {chapter_num}")
        return out

    def _generate_scene_sync(self, task_card, scene_blueprint, chapter_synopsis, previous_context="", pacing_constraints="") -> SceneOutput:
        """同步生成单个场景（用于顺序执行）。"""
        return self.generate_scene(task_card, scene_blueprint, chapter_synopsis, previous_context, pacing_constraints)

    # ---- CC round-25 P0-1：近空场景章内抢救阶梯 Level 2 / Level 3（更便宜，先于整章重排）----
    FOCUSED_SYSTEM_PROMPT = (
        "你是专业中文网文写手。你只输出成稿正文本身：纯中文第三人称有限视角叙事，"
        "半文半白、对白使用引号、正常中文标点。严禁输出 JSON、字段名、解释、思考过程、"
        "前缀说明、markdown/代码围栏或任何【】（）指令标记。\n"
        "【语言硬约束】正文必须 100% 中文：禁止出现任何英文字母、英文单词、英文句子、"
        "拉丁字符或英文标点（连一个英文单词也不允许）；人名地名功法名一律使用中文，"
        "不得用英文原文；若发现自己写出了英文，必须立即改用中文重写该句。"
    )

    @staticmethod
    def _plain_prose(raw) -> str:
        """把 focused / beat-split 调用返回净化为纯正文；容忍 flash 偶尔仍包一层 JSON/fence。"""
        if isinstance(raw, dict):
            raw = raw.get("content") or raw.get("reasoning_content") or ""
        t = (raw or "").strip()
        # 容忍 {"scene_text": "..."} 包裹
        try:
            from novel_engine.agents.scene_schema import _loads_scene_object
            data = _loads_scene_object(t)
            if isinstance(data, dict) and str(data.get("scene_text", "")).strip():
                t = str(data["scene_text"]).strip()
        except Exception:
            t = re.sub(r"^\s*```(?:json|scene)?\s*|\s*```\s*$", "", t).strip()
        t = re.sub(r"^(?:正文|场景正文|回复|答案)\s*[:：]\s*", "", t.strip()).strip()
        return t

    def _scene_contract_lines(self, scene_blueprint: dict) -> list[str]:
        bp = scene_blueprint or {}
        lines: list[str] = []
        pc = bp.get("scene_progression_contract") or {}
        for x in (pc.get("new_state_or_entity") or []):
            if str(x).strip():
                lines.append(f"- 必须落地本场新状态/新实体：{str(x).strip()}")
        ic = pc.get("irreversible_change")
        if str(ic or "").strip():
            lines.append(f"- 必须演到本场不可逆变化：{str(ic).strip()}")
        return lines

    def focused_rewrite_scene(self, task_card: dict, scene_blueprint: dict, target_chars: int) -> str:
        """Level 2：同任务卡单场"聚焦重写"（只给本场 beats+推进契约+技法，不带整章上下文）。

        成功（>=300 字且无脚手架标记）返回纯正文；失败/仍近空返回 ""。预算 1 次。
        """
        from novel_engine.agents.craft_elements import craft_directive_lines
        bp = scene_blueprint or {}
        sn = bp.get("scene_num", 0)
        cn = task_card.get("chapter_num", 0)
        beats = [str(b) for b in (bp.get("beats") or []) if str(b).strip()]
        beat_block = "\n".join(f"{i + 1}. {b}" for i, b in enumerate(beats)) or \
            "（任务卡未列 beats：按本场目标自主补足必要的具体动作，不得空场）"
        contract = self._scene_contract_lines(bp) or ["- 本场必须演到一个可验证的新事件或新信息，不能只渲染气氛"]
        craft = craft_directive_lines(bp)
        floor = max(300, int(float(target_chars) * 0.4))
        user = (
            f"只写第 {cn} 章第 {sn} 场这一个场景的完整正文，不要写其它任何场景。\n"
            f"地点：{bp.get('location', '未指定')}\n"
            f"出场人物：{'、'.join(str(c) for c in (bp.get('characters') or [])) or '未指定'}\n"
            f"本场目标：{bp.get('goal', '')}\n"
            f"本场冲突：{bp.get('conflict', '')}\n"
            f"本场情绪：{bp.get('emotion', '')}\n"
            "本场必须依次演足的情节点（每条都要写成具体动作/对白/感官，不许概述）：\n"
            f"{beat_block}\n"
            "本场推进契约（必须在正文里演出来）：\n" + "\n".join(contract) + "\n"
            + ("\n".join(f"- {c}" for c in craft) + "\n" if craft else "")
            + f"篇幅：正文不少于 {floor} 个中文字、向 {int(target_chars)} 字写足，严禁写短。\n"
            "写作纪律（硬性）：同一意象全章只准出现 1 次、禁止反复渲染同一氛围；单句不超 40 字需逗号断句；"
            "未知事物用'异动/标记/纹路'等模糊词，禁'符箓/阵纹/功法/灵力'等超规格术语；动作/对白/新发现为主，纯心理与环境描写占比 ≤30%。\n"
            "必须直接输出本场景完整正文：禁止任何前导说明、禁止思考过程、禁止 JSON 或代码围栏、"
            "禁止空 content、禁止提前结束。"
        )
        max_tokens = min(8192, max(2048, int(float(target_chars) * 2) + 512))
        try:
            res = self.llm.chat_completion(
                [{"role": "system", "content": self.FOCUSED_SYSTEM_PROMPT},
                 {"role": "user", "content": user}],
                temperature=0.8, max_tokens=max_tokens)
        except Exception as e:
            logger.warning(f"L2 focused rewrite scene {sn} request failed: {e}")
            return ""
        prose = self._plain_prose(res)
        if isinstance(res, dict) and res.get("finish_reason") == "length" and len(prose) < floor:
            logger.warning(f"L2 scene {sn} hit length cap at {len(prose)} chars")
        if len(prose) >= 300 and "【" not in prose and "】" not in prose:
            logger.info(f"L2 focused rewrite scene {sn} OK ({len(prose)} chars)")
            return prose
        logger.warning(f"L2 focused rewrite scene {sn} still near-empty ({len(prose)} chars) -> Level 3")
        return ""

    def beat_split_rewrite_scene(self, task_card: dict, scene_blueprint: dict, target_chars: int) -> str:
        """Level 3：单 beat 拆分重写——每个 beat 一次短请求(150-300字)，顺序拼接。

        整场只走 1 遍（不逐 beat 各自重试）。成功返回拼接正文；无法救回返回 ""。
        """
        bp = scene_blueprint or {}
        sn = bp.get("scene_num", 0)
        cn = task_card.get("chapter_num", 0)
        beats = [str(b) for b in (bp.get("beats") or []) if str(b).strip()]
        if not beats:
            return ""
        per = max(150, min(300, int(float(target_chars) / max(1, len(beats)))))
        loc = bp.get("location", "未指定")
        chars = "、".join(str(c) for c in (bp.get("characters") or [])) or "未指定"
        parts: list[str] = []
        for i, b in enumerate(beats):
            bridge = f"上一段结尾（衔接，不得复述）：…{parts[-1][-60:]}" if parts else "本段为本场开头"
            user = (
                f"为第 {cn} 章第 {sn} 场写其中【第 {i + 1}/{len(beats)} 个情节点】的正文片段，约 {per} 字（150-300）。\n"
                f"地点：{loc}；出场人物：{chars}\n"
                f"只具体演出这一个情节点：{b}\n{bridge}\n"
                "写作纪律：同一意象只写一次不重复渲染；单句不超40字；未知事物用模糊词禁超规格术语。\n"
                "直接输出该片段的成稿正文：具体动作/对白/感官，无解释、无 JSON、无标记、无标题。\n"
                "【语言硬约束】正文 100% 中文：禁止任何英文字母、英文单词、英文句子或拉丁字符，"
                "若写出了英文必须立即改用中文重写。"
            )
            try:
                res = self.llm.chat_completion(
                    [{"role": "system", "content": self.FOCUSED_SYSTEM_PROMPT},
                     {"role": "user", "content": user}],
                    temperature=0.8, max_tokens=700)
            except Exception as e:
                logger.warning(f"L3 beat {i + 1} scene {sn} failed: {e}")
                continue
            seg = self._plain_prose(res)
            if len(seg) >= 40 and "【" not in seg and "】" not in seg:
                parts.append(seg)
        text = "\n\n".join(parts).strip()
        if len(text) >= 300:
            logger.info(f"L3 beat-split scene {sn} OK ({len(parts)}/{len(beats)} beats, {len(text)} chars)")
            return text
        logger.warning(f"L3 beat-split scene {sn} insufficient ({len(parts)} beats, {len(text)} chars)")
        return ""

    def _group_independent_scenes(self, scenes: list[dict]) -> list[list[dict]]:
        """
        将场景按人物重叠分组。
        不相邻且无共享人物的场景可并行生成。
        返回：[(scene_group_1), (scene_group_2), ...]，每组内顺序依赖。
        """
        if len(scenes) <= 1:
            return [scenes]

        groups = []
        current_group = [scenes[0]]

        for i in range(1, len(scenes)):
            prev_chars = set(current_group[-1].get("characters", []))
            curr_chars = set(scenes[i].get("characters", []))
            if not prev_chars.intersection(curr_chars):
                shares_with_any = any(
                    set(s.get("characters", [])).intersection(curr_chars)
                    for s in current_group
                )
                if not shares_with_any:
                    groups.append(current_group)
                    current_group = [scenes[i]]
                else:
                    current_group.append(scenes[i])
            else:
                current_group.append(scenes[i])

        if current_group:
            groups.append(current_group)

        return groups

    def _generate_group(
        self, task_card: dict, group: list[dict], synopsis_text: str, pacing_constraints: str = "",
        temperature_override: Optional[float] = None,
    ) -> list[tuple[dict, SceneOutput]]:
        """顺序生成一个场景组（组内场景共享人物，存在上下文依赖）。

        串行路径：每个场景传入前一场景的 scene_text 作为 previous_context，
        同时走 build_scene_prompt 获取确定性前情锚点。
        """
        all_bps = task_card.get("scene_blueprints", []) or group
        group_contents = []
        previous_context = ""
        for bp in group:
            content = self.generate_scene(
                task_card, bp, synopsis_text, previous_context, pacing_constraints,
                temperature_override, all_blueprints=all_bps,
            )
            group_contents.append((bp, content))
            previous_context = "\n\n※\n\n".join(
                c.scene_text for _, c in group_contents[-3:]
            ) if len(group_contents) >= 3 else "\n\n".join(c.scene_text for _, c in group_contents)
        return group_contents

    def generate_scenes(self, task_card: dict, synopsis: dict, pacing_constraints: str = "",
                        temperature_override: Optional[float] = None) -> list[SceneOutput]:
        """
        并发生成本章全部结构化场景。
        P1 修复：场景并发（3 场景并行，~2-3min），共享 synopsis 与 beat 去重提示，无需前序上下文串行。
        返回按 scene_num 排序的 SceneOutput 列表；任一场景契约失败即抛错。
        成功后记 self.last_scenes 供管道结构复核。
        """
        scenes = task_card.get("scene_blueprints", []) or task_card.get("scenes", [])
        synopsis_text = synopsis.get("synopsis", "")

        if not scenes:
            self.last_scenes = []
            return []

        # 跨章已用 beat 预取（仅一次，避免每场景重复 IO）
        try:
            from novel_engine.core.memory_manager import MemoryManager
            _mm = MemoryManager(self.root)
            _recent_beats = _mm.get_recent_beats(limit=5)
        except Exception:
            _recent_beats = []
        # Runner-owned persistence (D1): Writer produces memory objects only.
        # The orchestrator journals validated scenes via chapter_journal.append_scene.
        # 场景并发：独立场景组并行，组内无需上下文依赖（同任务卡，已通过 beat 去重）
        from concurrent.futures import ThreadPoolExecutor, as_completed
        self.begin_chapter_failover(task_card, scenes)
        _base_workers = max(1, min(len(scenes), 4))
        max_workers = self._fb.concurrency(4) if (self._failover_enabled and self._fb is not None) else _base_workers
        max_workers = max(1, min(_base_workers, int(max_workers)))
        # 预先计算 beat 去重提示（基于任务卡，非运行时）
        covered_beats = [f"场景{bp.get('scene_num')}({bp.get('location','')}:{bp.get('goal','')[:18]})" for bp in sorted(scenes, key=lambda x: x.get("scene_num", 0))]
        def _do_one(bp):
            beat_hint = f"\n已覆盖情节（避免重复）：{'; '.join(covered_beats)}\n" if len(covered_beats) > 1 else ""
            if _recent_beats:
                beat_hint += f"\n已用剧情节点（不可复用）：{'; '.join(_recent_beats)}\n"
            combined_pacing = (pacing_constraints or "") + beat_hint
            # 并发场景无需 previous_context（同章独立），传空避免串行依赖
            # 传入 all_blueprints 使 build_scene_prompt 可生成前情锚点/禁写清单
            content = self.generate_scene(
                task_card, bp, synopsis_text, "", combined_pacing, temperature_override,
                all_blueprints=scenes,
            )
            return (bp, content)

        all_scene_contents: list[tuple[dict, SceneOutput]] = []
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="scene") as ex:
            fut2bp = {ex.submit(_do_one, bp): bp for bp in scenes}
            for fut in as_completed(fut2bp):
                bp, content = fut.result()
                all_scene_contents.append((bp, content))
        # 按 scene_num 排序确保顺序
        all_scene_contents.sort(key=lambda x: x[0].get("scene_num", 0))
        self.last_scenes = [content for _, content in all_scene_contents]
        return self.last_scenes

    def generate_full_chapter(
        self, task_card: dict, synopsis: dict, pacing_constraints: str = "",
        temperature_override: Optional[float] = None,
    ) -> str:
        """
        生成完整章节正文：并发结构化场景 + 组装（仅拼 scene_text，无 hook 追加）。
        """
        chapter_num = task_card.get("chapter_num", 0)
        scenes = self.generate_scenes(task_card, synopsis, pacing_constraints, temperature_override)
        if not scenes:
            return ""
        chapter_text = "\n\n".join(s.scene_text for s in scenes)
        logger.info(f"Full chapter {chapter_num} generated ({len(chapter_text)} chars)")
        return chapter_text


    def _split_scenes(self, chapter_text, scene_count):
        import re
        parts = [p.strip() for p in re.split(r"【场景\d+[:：][^】]*】|\n?\s*※\s*\n?", chapter_text) if p.strip()]
        if len(parts) < 2:
            parts = [chapter_text[i:i + 2400].strip() for i in range(0, len(chapter_text), 2400) if chapter_text[i:i + 2400].strip()]
        return parts if len(parts) >= 2 else [chapter_text]

    @staticmethod
    def _polish_max_tokens(n: int) -> int:
        """Measured polish budget shared by both paths (no old /1.5 estimate)."""
        return min(16000, max(4096, int(n / 0.9) + 512))

    @staticmethod
    def _coerce_polish_text(raw, orig_text: str) -> str | None:
        """Shared dict→finish_reason-guard→length-check for both polish paths.

        Returns the stripped polished text, or None when the caller must keep
        the original (length-truncated or degenerate response).
        """
        if isinstance(raw, dict):
            if raw.get("finish_reason") == "length":
                logger.warning(f"scene polish hit token cap, keep original ({len(orig_text)} chars)")
                return None
            raw = raw.get("content") or raw.get("reasoning_content") or ""
        resp = raw if isinstance(raw, str) else ""
        if len(resp.strip()) >= max(200, int(len(orig_text) * 0.5)):
            return resp.strip()
        return None

    def _polish_single(self, scene_text, task_card, client, scene=False):
        """单场景（或单次全文兜底）polish：测量预算经 _polish_max_tokens 统一计算。"""
        prompt = (
            "请润色并修正以下完整章节，保持人设、伏笔与节奏一致，正常使用中文标点（每个完整句不超过约40字、每2-4个分句用逗号分隔、对话用引号，严禁整段无标点的流水句），仅返回润色后的完整正文，不要解释。\n\n"
            f"【任务卡】{task_card.get('title', '')}\n\n【正文】\n{scene_text}"
        )
        max_tokens = self._polish_max_tokens(len(scene_text))
        try:
            raw = client.chat_completion(
                [{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=max_tokens,
            )
            resp = self._coerce_polish_text(raw, scene_text)
            if resp is not None:
                return resp
            logger.warning(f"polish degenerate vs orig {len(scene_text)}, keep original")
        except Exception as exc:
            logger.warning(f"polish failed ({exc}), keep original")
        return scene_text

    def _polish_by_scenes_concurrent(self, chapter_text: str, task_card: dict, client) -> str:
        """按场景并发 polish：复用 ThreadPoolExecutor，并发度取配置；
        每个场景附带相邻场景结尾/开头作为只读衔接锚点。

        单场景失败回退原文，不得因一段失败拖垮整章。
        并发完成后做确定性接缝校验，失败则打 seam_warning 标记。
        """
        expected = len(task_card.get("scene_blueprints") or []) or 1
        scenes = self._split_scenes(chapter_text, expected)
        if len(scenes) < 2:
            logger.warning(f"scene split {len(scenes)} vs expected {expected}, single-call fallback")
            return self._polish_single(chapter_text, task_card, client)

        # 读取 concurrency 配置（从 model_router phases.polish.concurrency）
        concurrency = getattr(client, "concurrency", 4) if hasattr(client, "concurrency") else 4

        def _do_one(idx: int, scene_text: str) -> tuple[int, str, bool]:
            """Polish 单个场景，返回 (index, polished_text, success)。"""
            # 构造衔接锚点
            prev_anchor = ""
            next_anchor = ""
            if idx > 0:
                prev_anchor = scenes[idx - 1][-300:] if len(scenes[idx - 1]) >= 300 else scenes[idx - 1]
                prev_anchor = f"\n\n## 上一场景结尾（只读衔接，不得改写）\n{prev_anchor}"
            if idx < len(scenes) - 1:
                next_anchor = scenes[idx + 1][:300] if len(scenes[idx + 1]) >= 300 else scenes[idx + 1]
                next_anchor = f"\n\n## 下一场景开头（只读衔接，不得改写）\n{next_anchor}"

            prompt = (
                "请润色并修正以下场景正文，保持人设、伏笔与节奏一致，"
                "正常使用中文标点（每个完整句不超过约40字、每2-4个分句用逗号分隔、对话用引号，严禁整段无标点的流水句），仅返回润色后的场景正文，不要解释。\n\n"
                f"【任务卡】{task_card.get('title', '')}\n"
                f"{prev_anchor}\n{next_anchor}\n"
                "【正文】\n{scene_text}"
            ).format(scene_text=scene_text)

            max_tokens = self._polish_max_tokens(len(scene_text))
            try:
                raw = client.chat_completion(
                    [{"role": "user", "content": prompt}],
                    temperature=0.7,
                    max_tokens=max_tokens,
                )
                resp = self._coerce_polish_text(raw, scene_text)
                if resp is not None:
                    return (idx, resp, True)
                logger.warning(f"Scene {idx} polish degenerate, keep original")
                return (idx, scene_text, False)
            except Exception as exc:
                logger.warning(f"Scene {idx} polish failed ({exc}), keep original")
                return (idx, scene_text, False)

        # 并发执行
        results: list[tuple[int, str, bool]] = []
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="polish") as ex:
            futures = {ex.submit(_do_one, i, s): i for i, s in enumerate(scenes)}
            for fut in as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception as e:
                    idx = futures[fut]
                    logger.warning(f"Scene {idx} polish executor error: {e}")
                    results.append((idx, scenes[idx], False))

        # 按 index 排序重组
        results.sort(key=lambda x: x[0])
        out = [r[1] for r in results]

        # 接缝校验：相邻场景衔接处不得出现明显冲突
        seam_warnings = self._validate_seams(out)
        if seam_warnings:
            logger.warning(f"Seam warnings after polish: {seam_warnings[:3]}")

        return "\n\n※\n\n".join(out)

    def _validate_seams(self, scenes: list[str]) -> list[str]:
        """轻量接缝校验：检查相邻场景衔接处的人称/时间线冲突。"""
        warnings = []
        for i in range(len(scenes) - 1):
            curr_end = scenes[i][-100:] if len(scenes[i]) >= 100 else scenes[i]
            next_start = scenes[i + 1][:100] if len(scenes[i + 1]) >= 100 else scenes[i + 1]
            # 检查重复句子（简单实现）
            if curr_end.strip() and next_start.strip():
                # 如果结尾和开头有超过20字的重复，记 warning
                overlap_len = 0
                for l in range(min(len(curr_end), len(next_start)), 0, -1):
                    if curr_end[-l:] == next_start[:l]:
                        overlap_len = l
                        break
                if overlap_len > 20:
                    warnings.append(f"Seam overlap between scene {i} and {i+1}: {overlap_len} chars")
        return warnings

    def polish_chapter(self, chapter_text: str, task_card: dict, llm_client=None) -> str:
        """章节润色：统一过渡与语气。

        大章节按场景并发 polish（取 phases.polish.concurrency），
        小章节单次全文 polish。
        校验润色结果长度：过短（退化/空输出）时提高温度重试；
        全部失败后回退到原始正文，避免生成残缺章节。
        """
        client = llm_client or self.llm
        max_tokens = self._polish_max_tokens(len(chapter_text))

        # 大章节走并发场景路径
        if len(chapter_text) > 4000:
            return self._polish_by_scenes_concurrent(chapter_text, task_card, client)

        # 小章节单次全文 polish
        prompt = (
            "请润色并修正以下完整章节，保持人设、伏笔与节奏一致，正常使用中文标点（每个完整句不超过约40字、每2-4个分句用逗号分隔、对话用引号，严禁整段无标点的流水句），仅返回润色后的完整正文，不要解释。\n\n"
            f"【任务卡】{task_card.get('title', '')}\n\n【正文】\n{chapter_text}"
        )
        temps = [0.7, 0.85, 0.95]
        for attempt in range(len(temps)):
            try:
                raw = client.chat_completion(
                    [{"role": "user", "content": prompt}],
                    temperature=temps[attempt],
                    max_tokens=max_tokens,
                )
            except Exception as exc:
                logger.warning(f"polish_chapter call failed ({exc}); falling back to original text")
                return chapter_text
            resp = self._coerce_polish_text(raw, chapter_text)
            if resp is not None:
                return resp
            logger.warning(
                f"polish_chapter attempt {attempt + 1} returned degenerate/truncated output, retrying"
            )
        logger.warning("polish_chapter: all attempts degenerate, falling back to original text")
        return chapter_text

    def polish_scenes(self, scenes: list, task_card: dict, client=None, only_ids: set[int] | None = None) -> tuple:
        """结构化场景级并发润色。

        以 SceneOutput 为边界，每个场景一次纯文本流式调用，
        并发度取 phases.polish.concurrency，用 ThreadPoolExecutor。
        每场景附相邻场景结尾/开头约 250 字作为只读衔接锚点。
        返回 (list[SceneOutput], list[bool])，bool 表示该场景是否成功润色。
        任一场景失败或退化则保留原文，对应 bool=False。
        only_ids: 只对这些 scene_id 进行润色，其他场景原样保留（flag=False）。
        """
        from novel_engine.quality.chinese_gate import is_chinese_clean

        if not scenes or len(scenes) < 1:
            return (list(scenes), [])

        client = client or self.llm
        # 取并发度：ModelRouter 有 concurrency 属性，否则默认 4
        concurrency = getattr(client, "concurrency", 4) if hasattr(client, "concurrency") else 4

        def _do_one(idx: int, scene: "SceneOutput") -> tuple[int, "SceneOutput", bool]:
            """Polish 单个场景，返回 (index, polished_scene, success)。"""
            # 跳过不在 only_ids 中的场景（journaled 场景）
            if only_ids is not None and scene.scene_id not in only_ids:
                return (idx, scene, False)
            orig_text = scene.scene_text

            # 构造衔接锚点（只读，不得改写）
            prev_anchor = ""
            next_anchor = ""
            if idx > 0:
                prev = scenes[idx - 1].scene_text
                prev_anchor = prev[-250:] if len(prev) >= 250 else prev
                prev_anchor = f"\n\n## 上一场景结尾（只读衔接，不得改写）\n{prev_anchor}"
            if idx < len(scenes) - 1:
                nxt = scenes[idx + 1].scene_text
                next_anchor = nxt[:250] if len(nxt) >= 250 else nxt
                next_anchor = f"\n\n## 下一场景开头（只读衔接，不得改写）\n{next_anchor}"

            prompt = (
                "请润色并修正以下场景正文，保持人设、伏笔与节奏一致，"
                "正常使用中文标点（每个完整句不超过约40字、每2-4个分句用逗号分隔、对话用引号，严禁整段无标点的流水句），仅返回润色后的场景正文，不要解释。\n\n"
                f"【任务卡】{task_card.get('title', '')}\n"
                f"{prev_anchor}\n{next_anchor}\n"
                "【正文】\n{scene_text}"
            ).format(scene_text=orig_text)

            max_tokens = self._polish_max_tokens(len(orig_text))
            try:
                raw = client.chat_completion(
                    [{"role": "user", "content": prompt}],
                    temperature=0.7,
                    max_tokens=max_tokens,
                )
                resp = self._coerce_polish_text(raw, orig_text)
                if resp is None:
                    logger.warning(f"Scene {scene.scene_id} polish degenerate, keep original")
                    return (idx, scene, False)
                # 确定性校验：非空、中文主导、无脚手架标记、长度在 0.5-1.5 倍
                if not resp.strip():
                    return (idx, scene, False)
                if not is_chinese_clean(resp):
                    logger.warning(f"Scene {scene.scene_id} polish not Chinese-clean, keep original")
                    return (idx, scene, False)
                if "【" in resp or "】" in resp or "※" in resp:
                    logger.warning(f"Scene {scene.scene_id} polish has scaffolding, keep original")
                    return (idx, scene, False)
                orig_len = len(orig_text)
                resp_len = len(resp)
                if resp_len < orig_len * 0.5 or resp_len > orig_len * 1.5:
                    logger.warning(f"Scene {scene.scene_id} polish length out of range ({resp_len} vs {orig_len}), keep original")
                    return (idx, scene, False)
                # 创建新 SceneOutput，保留原 hook 和 beats
                new_scene = SceneOutput(scene.scene_id, resp.strip(), scene.hook, list(scene.beats))
                return (idx, new_scene, True)
            except Exception as exc:
                logger.warning(f"Scene {scene.scene_id} polish failed ({exc}), keep original")
                return (idx, scene, False)

        results: list[tuple[int, "SceneOutput", bool]] = []
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="polish_scenes") as ex:
            futures = {ex.submit(_do_one, i, s): i for i, s in enumerate(scenes)}
            for fut in as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception as e:
                    idx = futures[fut]
                    logger.warning(f"Scene {idx} polish executor error: {e}")
                    results.append((idx, scenes[idx], False))

        results.sort(key=lambda x: x[0])
        polished_scenes = [r[1] for r in results]
        polished_flags = [r[2] for r in results]

        # 接缝校验（只读）
        seam_texts = [s.scene_text for s in polished_scenes]
        seam_warnings = self._validate_seams(seam_texts)
        if seam_warnings:
            logger.warning(f"Seam warnings after polish_scenes: {seam_warnings[:3]}")

        return (polished_scenes, polished_flags)
