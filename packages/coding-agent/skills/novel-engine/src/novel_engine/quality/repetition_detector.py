"""
确定性质量检测器：重复/截断/长度异常。
用于在 POLISH 后对成品进行硬校验，命中即触发修复而非静默通过。
"""
import re
from collections import Counter
from pathlib import Path

from novel_engine.core import quality_policy as _quality_policy


def detect_repetition(text: str, min_block: int = 200, min_repeats: int = 3) -> dict:
    """检测整块重复：若同一段落/块连续出现 ≥min_repeats 次，或最长重复子串 ≥min_block，判定为重复灾难。"""
    issues = []
    # 按段落切分（空行分隔）
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) >= 4:
        # 统计完全相同段落
        cnt = Counter(paras)
        for para, c in cnt.most_common(3):
            if c >= min_repeats and len(para) >= 50:
                issues.append(f"段落重复 {c} 次（≥{min_repeats}）：{para[:60]}…")
                break
        # 连续大块重复（n-gram 滑窗 200字）
        for window in [200, 400]:
            seen = set()
            for i in range(len(text) - window):
                chunk = text[i:i+window]
                if len(chunk.strip()) < window * 0.8:
                    continue
                if chunk in seen:
                    # 二次出现即判
                    if text.count(chunk) >= 2:
                        issues.append(f"检测到 {window} 字级整块重复")
                        break
                seen.add(chunk)
            if issues:
                break
        # 最长重复子串快速检测（对 8KB 以上文本，仅检查前 8KB 避免 O(n^2)）
        if not issues and len(text) > 4000:
            sample = text[:8000]
            # 使用后缀数组简化：检查是否有 200 字块在 sample 中出现 ≥3 次
            for i in range(0, len(sample) - min_block, min_block):
                block = sample[i:i+min_block]
                if sample.count(block) >= min_repeats:
                    issues.append(f"最长重复子串 ≥{min_block} 字且出现 ≥{min_repeats} 次")
                    break
    return {"has_repetition": bool(issues), "issues": issues}


def detect_truncation(text: str) -> dict:
    """检测硬截断：不能断在半句/半段，且必须含完整句读收尾。"""
    issues = []
    stripped = text.strip()
    if not stripped:
        return {"is_truncated": True, "issues": ["正文为空"]}
    # 末尾必须以句读收尾（允许 ）」等闭合符号）
    if stripped[-1] not in "。！？」”’\"…—）":
        if len(stripped) > 100 and stripped[-1] not in "。！？」”’\"…—） \n":
            issues.append(f"疑似硬截断：末字符为“{stripped[-1]}”非句读")
    # 检测半句截断（如以“的”“了”“在”等虚词结尾）
    if re.search(r"[的了在与和或但而]$", stripped[-12:]):
        issues.append("疑似半句截断：末尾为虚词收尾")
    # 检测章节长度异常：过短
    if len(stripped) < 1500:
        issues.append(f"章节过短（{len(stripped)} 字），目标 80% 未达")
    return {"is_truncated": bool(issues), "issues": issues}


def detect_length_anomaly(text: str, target: int | None) -> dict:
    """对照 scene_blueprints 目标字数校验。P0 容差 +2% 避免 1 字符边界误杀。"""
    if not target or target <= 0:
        return {"anomaly": False, "issues": []}
    actual = len(text)
    # 单一策略源：比率/容差一律读 quality_policy（默认 0.65/1.35/50，与旧硬编码同值）
    policy = _quality_policy.load_quality_policy(Path(__file__).resolve().parent.parent)
    low = int(target * policy["min_ratio"])
    high = int(target * policy["max_ratio"] + max(policy["tolerance_chars"], target * 0.02))
    issues = []
    if actual < low:
        issues.append(f"字数不足：实际 {actual} < 目标 {target}*{policy['min_ratio']}={low}")
    elif actual > high:
        issues.append(f"字数超标：实际 {actual} > 目标 {target}*{policy['max_ratio']}+容差={high} (1 字符级已放宽)")
    return {"anomaly": bool(issues), "issues": issues}


def _pair_ascii_quotes(text: str) -> str:
    """中文出版规范：flash 在对话里会混用半角直引号 " 与 '。统一按出现次序
    成对归一化为中文双弯引号（独立对话一律 “ ”；嵌套引用极罕见，拉平后仍成对）。"""
    out = []
    opened = False
    for ch in text:
        if ch == '"' or ch == "'":
            out.append("”" if opened else "“")
            opened = not opened
        else:
            out.append(ch)
    return "".join(out)


def purify_novel_for_publish(draft: str, chapter_num: int | None = None) -> str:
    """成品净化：剥离所有脚手架标记，仅保留可发布正文。P0 增强版 + Round8 结构性段落判伪。P1 保留场景边界。"""
    text = draft
    # P1 保留场景边界：发布时保留 【场景...】 标记以确保场景数校验通过，不因样式被误删致 mismatch
    # 仅对纯指令性 【...】（含字数/节拍等）做剥离，场景标记保留
    # 若传入 chapter_num，强制校正首个章节标题为正确章号（防缓存模板章号错误）
    if chapter_num is not None:
        def _fix_title(m):
            return f"# 第{chapter_num}章"
        text = re.sub(r"^\s*#\s*第[一二三四五六七八九十\d]+章", _fix_title, text, count=1, flags=re.MULTILINE)
    # 去除 # 续写 / Markdown 一级标题残留（长章节续写调用偶发泄漏）
    text = re.sub(r"^\s*#\s*续写\s*$[^\n]*\n*", "", text, flags=re.MULTILINE)
    # 去除 # 续写 / Markdown 一级标题残留（长章节续写调用偶发泄漏）
    text = re.sub(r"^\s*#\s*续写\s*$[^\n]*\n*", "", text, flags=re.MULTILINE)
    # 去除 【场景N：...】 标题行（兼容有无空格、全角/半角冒号）
    text = re.sub(r"【场景\s*\d+\s*[:：][^】]*】\s*\n*", "", text)
    # 去除所有含 元指令关键词 的 【...】 块（当前字数、节拍、拍点、本节、小结等）
    text = re.sub(r"【[^】]*?(?:字数|节拍|拍点|当前|本节|小结|场景小结)[^】]*】\s*\n*", "", text)
    # 去除纯指令性短行（如 "场景 1：..." 单独成行）
    text = re.sub(r"^\s*(?:场景|Scene)\s*\d+\s*[:：].*$\n*", "", text, flags=re.MULTILINE)
    text = re.sub(r"(?i)\bscene\s*\d*\s*[:：]?", "", text)
    text = re.sub(r'"\s*(?:chapter|hook|scene_id|scene_text|blueprints|synopsis|forbidden|checks|pacing|goal|world_state|summary|events|entities)\s*"\s*:\s*', "", text)
    text = re.sub(r'\{\s*"scene_id"[\s\S]*?\n\}', "", text)
    # 去除 ※ 分隔符（含两侧空白）
    text = re.sub(r"\n?\s*※\s*\n?", "\n\n", text)
    # 去除 --- / **** / *** 等分隔线（纯符号行）
    text = re.sub(r"^\s*[-*]{3,}\s*$\n*", "", text, flags=re.MULTILINE)
    # 彻底剥离章末钩子标记（含截断残缺），不保留指令原文（hook 由正文自然收束）
    text = re.sub(r"\*?\s*（章末钩子[^）\n]*）?\s*\n*", "", text)
    text = re.sub(r"（章末钩子[^）]*）", "", text)
    # 去除 （注：...）及所有括号指令（章末钩子/场景目标/伏笔等），含截断半行（无闭合 ）也一并移除
    # 任何包含这些关键词的行，无论是否闭合、无论括号嵌套，一律整行移除
    text = re.sub(r"^[^\n]*?(?:章末钩子|场景目标|伏笔)[^\n]*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"（(?:章末钩子|场景目标|伏笔|注意|提示)[^）]*）\s*\n*", "", text)
    text = re.sub(r"（章末钩子[^）\n]*", "", text)
    text = re.sub(r"（场景目标[^）\n]*", "", text)
    text = re.sub(r"（伏笔[^）\n]*", "", text)
    text = re.sub(r"（注：[^）]*）", "", text)
    text = re.sub(r"\(注：[^)]*\)", "", text)
    # 去除残留的场景标记变体
    text = re.sub(r"###\s*[一二三四五六七八九十]+\s*\n*", "", text)
    # 结构性段落判伪：逐段分类，剥离指令段/元信息段（P0）
    paras = re.split(r"\n\s*\n", text)
    cleaned_paras = []
    for para in paras:
        stripped = para.strip()
        if not stripped:
            continue
        # 指令段特征：含 节拍点\d / 场景小结 / 当前字数 / 拍点 / 纯加粗短指令行
        is_instruction = False
        # 1. 节拍点\d（如 **节拍点1（冲突酝酿）：...**）
        if re.search(r"节拍点\s*\d+", stripped):
            is_instruction = True
        # 2. 场景小结块
        elif "场景小结" in stripped:
            is_instruction = True
        # 3. 当前字数 / 拍点 / 本节
        elif any(kw in stripped for kw in ["当前字数", "本节拍", "拍点完成"]):
            is_instruction = True
        # 4. 纯加粗短指令行（整段为 **...** 且长度 <100 且含指令词）
        elif re.match(r"^\s*\*{2,}.*\*{2,}\s*$", stripped) and len(stripped) < 200:
            is_instruction = True
        # 5. 独立 【】 包裹的元信息段（已在上步部分去除，此处兜底）
        elif re.match(r"^\s*【[^】]*】\s*$", stripped):
            is_instruction = True
        # 6. 纯符号行
        elif re.match(r"^\s*[-*]{3,}\s*$", stripped):
            is_instruction = True
        if is_instruction:
            continue
        cleaned_paras.append(para)
    text = "\n\n".join(cleaned_paras)
    # 去除章节内残留的 "# 第四章·..." 多余标题（仅保留首个，且 normalize）
    lines = text.split("\n")
    seen_first_title = False
    cleaned_lines = []
    for line in lines:
        m = re.match(r"^\s*#\s*第([一二三四五六七八九十\d]+)章", line)
        if m:
            if not seen_first_title:
                # 规范化为 "# 第N章" 统一格式（去除多余幕/标题后缀，保留首个）
                # 提取章号并重写为标准格式
                raw_num = m.group(1)
                # 尝试保留原标题的后缀（取第一个 "·" 或 " " 后的标题）
                title_part = ""
                # 查找 "章" 后的标题
                after = line.split("章", 1)[-1].strip()
                # 去除 "·" " " 等分隔符后的额外幕信息，统一为 "第N章"
                # 这里简化：只保留 "# 第N章" + 标题首段
                if after:
                    # 去除 leading "·" " " "第M幕" 等
                    after = re.sub(r"^[·\s第幕\d一二三四五六七八九十\(\)（）·\s]+", "", after).strip()
                    if after:
                        title_part = f" {after.split()[0][:12]}"
                cleaned_lines.append(f"# 第{raw_num}章{title_part}")
                seen_first_title = True
            continue
        # CC round-19 Q3：除我方章节标题外，任何行首 markdown 标题/指令行（如 "# 扩写正文"、
        # "# 正文"、"# 续写"）一律剥离。纯中文叙事正文不存在合法的行首 "#"，
        # 出现即 LLM 把脚手架指令原样吐出，故用结构特征而非指令词白名单判定。
        if line.lstrip().startswith("#"):
            continue
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)
    # 压缩多余空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    # 中文出版规范：对话中的半角直引号成对归一化为中文弯引号（零 LLM 确定性步骤）
    text = _pair_ascii_quotes(text)
    # P1 保 hook：若末尾无句读收束，补句号
    stripped = text.strip()
    if stripped and stripped[-1] not in "。！？…）」”":
        text = stripped + "。"
    return text.strip()


def verify_no_scaffolding(text: str) -> list[str]:
    """发布前终检：成品不得含脚手架 token。P0 增强版。"""
    issues = []
    if re.search(r"【[^】]*】", text):
        issues.append("残留【】包裹的指令/标记")
    for tok in ["# 续写", "※", "（章末钩子", "（注：", "节拍点", "场景小结", "当前字数", "本节拍", "拍点", "（场景目标", "（伏笔"]:
        if tok in text:
            issues.append(f"残留脚手架 token: {tok}")
    # 检查纯加粗指令行残留
    if re.search(r"^\s*\*{2,}.*节拍.*\*{2,}\s*$", text, flags=re.MULTILINE):
        issues.append("残留加粗节拍标注")
    if re.search(r"^\s*\*{4,}\s*$", text, flags=re.MULTILINE):
        issues.append("残留 **** 分隔符")
    # 检查场景标记格式混乱：仅统计行首 # 第X章
    if len(re.findall(r"^\s*#\s*第[一二三四五六七八九十\d]+章", text, flags=re.MULTILINE)) > 1:
        issues.append("章节内含多个 # 第X章 标题，格式混乱")
    # 检查是否仍有场景标题残留（应已被剥离）
    if re.search(r"【场景\s*\d+", text):
        issues.append("残留【场景 标题")
    # 检查括号指令残留（含截断）
    if re.search(r"（(?:章末钩子|场景目标|伏笔)", text):
        issues.append("残留括号指令（章末钩子/场景目标/伏笔）")
    issues.extend(detect_director_speak_leak(text))
    return issues


# ── 中文"导演话语"泄漏检测（CC 2026-10-05）────────────────────────────
# 背景：ch25 正文出现"这就是他必须执行的第一个外部动作：翻阅旧笔记"——
# 纯中文的规划式表述混入正文，现有 latin/脚手架正则（[A-Za-z] 类）抓不到。
# 两层检测：
#   A. 兜底短语：导演规划式措辞（有限、可枚举的句式），命中即报。
#   B. 结构化反查：task_card/schema 内部规划词汇（core_goal→"本章核心目标"、
#      concrete_events→"外部动作" 等，由 chapter_director prompt 写死、集合有限）
#      原样出现在正文即报——检查"内部实现的词汇泄漏到外部产物"，不依赖人工
#      逐条读出来再补词，避免退回关键词打地鼠。
_DIRECTOR_SPEAK_PATTERNS = (
    r"必须执行的第一个(?:外部|内部)?动作",
    r"(?:接下来|下一步|下面)(?:要|该|需要|应当)?做的",
    r"这一步(?:的)?(?:任务|动作|目标)是",
    r"按照(?:安排|规划|计划)(?:他|她|陆烬|陈老根)需要",
    r"执行(?:完|过|好|了)?(?:这些|那些|完)?(?:指令|安排|任务)",
    r"本章(?:的)?(?:核心|最高)?(?:目标|任务)",
)
_SCHEMA_LEAK_TERMS = (
    # chapter_director prompt 写死的内部字段标签（原样进正文即泄漏）
    "本章核心目标", "大纲核心任务", "核心任务", "场景目标", "场景冲突",
    "场景情绪", "情节点", "外部动作", "内部动作", "concrete_events",
    "observable_action", "must_cover_beats", "foreshadow_actions",
    "narrative_position", "completed_actions", "pending_actions",
    "核心事件",
)


def detect_director_speak_leak(text: str) -> list[str]:
    """检测纯中文导演话语/内部规划词汇泄漏进正文。返回命中项列表（空=干净）。"""
    issues: list[str] = []
    if not text:
        return issues
    # A. 规划式短语
    for pat in _DIRECTOR_SPEAK_PATTERNS:
        m = re.search(pat, text)
        if m:
            issues.append(f"残留导演话语（规划式表述）: {m.group(0)}")
    # B. schema 内部词汇反查
    for term in _SCHEMA_LEAK_TERMS:
        if term in text:
            issues.append(f"残留内部规划词汇: {term}")
    return issues


# 相邻场景边界常见时间词（只读 seam 检查用，不做任何改写依据）
_SEAM_TIME_TOKENS = (
    "凌晨", "清晨", "早晨", "上午", "正午", "中午",
    "午后", "下午", "傍晚", "黄昏", "夜晚", "深夜", "子夜",
)


def verify_seams(text: str) -> list[str]:
    """Read-only: adjacent-scene time/appellation consistency. Notes only.

    只读缝检查：相邻场景边界的时间词一致性。仅返回备注列表，
    永不改写、永不抛异常、永不作为门控或重抛光触发条件。
    """
    notes: list[str] = []
    try:
        src = text if isinstance(text, str) else ""
        parts = [p.strip() for p in re.split(r"\n?\s*※\s*\n?", src) if p.strip()]
        if len(parts) < 2:
            # 兜底：按 【场景N】 标记切分
            alt = [p.strip() for p in re.split(r"(?=\n*【场景\d+\s*[:：])", src) if p.strip()]
            if len(alt) >= 2:
                parts = alt
        if len(parts) < 2:
            return []
        for i in range(len(parts) - 1):
            tail = parts[i][-120:]
            head = parts[i + 1][:120]
            tail_times = {t for t in _SEAM_TIME_TOKENS if t in tail}
            head_times = {t for t in _SEAM_TIME_TOKENS if t in head}
            shared = tail_times & head_times
            if shared:
                notes.append(
                    f"seam {i + 1}->{i + 2}: 相邻场景重复时间词“{'/'.join(sorted(shared))}”（仅备注，不阻断）"
                )
            elif tail_times and head_times:
                notes.append(
                    f"seam {i + 1}->{i + 2}: 时间跳跃“{'/'.join(sorted(tail_times))}”→“{'/'.join(sorted(head_times))}”（仅备注，不阻断）"
                )
    except Exception:
        # 只读检查永不抛异常：有备注就返回已有备注，否则空列表
        pass
    return notes
