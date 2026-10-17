# -*- coding: utf-8 -*-
"""CC round-16 P0-1：标点/断句健康门（零 LLM 纯规则）。

Agnes flash 在扩写/polish 快速补字时偶发输出"整段只有段尾一个句号、中间零逗号"的流水句。
初稿的句末校验只看最后一个字符，抓不到这类段落；本模块在扩写/polish/最终组装三处复用。
对话/引语内容豁免（语言节奏可不同）；命中后走"仅加标点"的定点 LLM 修复，并用
去标点逐字比对校验模型没有越界改写。

CC round-12 R2：新增句末口径长句检测（。！？之间，逗号不算句末）与零字改写安全断句，
阈值 48 中文字。修复在成稿组装后、评审前静态作用；残留只记软 issue 不重生。
"""
from __future__ import annotations

import re

# 句读标点（逗号也算，流水句主要缺的就是句内停顿）
_PAUSE_CHARS = "。！？!?...，,；;：:、"
# 成对引号（中英文）
Q_L, Q_R = "“", "”"
SQ_L, SQ_R = "‘", "’"
# CC round-12 R2：中文角括号也作引号处理，确保旁白/对话正确切分
CB_L, CB_R = "「", "」"
_QUOTE_PAIRS = [(Q_L, Q_R), (SQ_L, SQ_R), (CB_L, CB_R), (chr(34), chr(34))]

# 默认阈值（CC round-16，中文网文宽松下限；CC round-12 R2b：降至 2.0 以兼容零标点流水段修复后密度）
MAX_UNPUNCTUATED_RUN = 60
MIN_DENSITY_PER_100 = 2.0

# CC round-12 R2：句末口径长句阈值（中文字数，含标点计字符数）
# CC round-13 R2：reviewer 口径约 40 字，降至 40 以覆盖 41–47 漏检段
LONG_SENTENCE_HARD = 40

# CC round-12 R2：句末标点正则（。！？…）
_SENTENCE_END_PAT = re.compile(r"[。！？…]")
# CC round-12 R2c：引号占位符（chr(0)）与换行亦为硬句界，防止跨对话拼接假长句
_SENTENCE_HARD_BOUNDARY_PAT = re.compile(r"[。！？…\x00\n]")
# CC round-12 R2：中文字符正则
_CN_RE = re.compile(r"[一-鿿]")

# CC round-12 R2b：引号占位符（不改变正文偏移，防止引号被删后相邻旁白拼成假长句）
_Q_SENTINEL = "##Q##"


def remove_quoted_content(text: str) -> str:
    """剥离成对引号内的对话/引语（含引号本身），只留旁白叙事文本。

    无法成对的孤立引号按普通字符保留（不强行吞掉后续正文）。
    """
    s = text or ""
    for op, cl in _QUOTE_PAIRS:
        while True:
            i = s.find(op)
            if i == -1:
                break
            j = s.find(cl, i + len(op))
            if j == -1:
                break
            s = s[:i] + s[j + len(cl):]
    return s


def _body_with_quote_sentinel(para: str) -> tuple[str, list[int]]:
    """构建旁白正文并建立 body_offset→para_offset 映射。

    引号内容整体替换为一个不可见占位符（不增加 body 长度），防止引号被删后相邻
    旁白拼成假长句；占位符位置在 body_to_para 中保留 para_offset，供切分时定位。
    返回 (body, body_to_para)。
    """
    body: list[str] = []
    body_to_para: list[int] = []
    i = 0
    n = len(para)
    while i < n:
        # 尝试匹配成对引号
        matched = None
        for op, cl in _QUOTE_PAIRS:
            if para[i:i+len(op)] == op:
                j = para.find(cl, i + len(op))
                if j >= 0:
                    matched = (i, j + len(cl))
                    break
        if matched is not None:
            _, end = matched
            # 占位符：单字符，不影响 body 总长
            body.append(chr(0))
            # 映射到引号起始位置（后续切分用）
            body_to_para.append(i)
            i = end
            continue
        body.append(para[i])
        body_to_para.append(i)
        i += 1
    return "".join(body), body_to_para


def _longest_run(text: str) -> int:
    longest = cur = 0
    for ch in text:
        if ch in _PAUSE_CHARS or ch.isspace():
            cur = 0
        else:
            cur += 1
            if cur > longest:
                longest = cur
    return longest


def check_paragraph_punctuation(text: str, max_run: int = MAX_UNPUNCTUATED_RUN,
                                min_density: float = MIN_DENSITY_PER_100) -> dict:
    """单段标点健康度。返回 longest run / 每百字句读数 / 是否不健康（引号内豁免）。"""
    body = remove_quoted_content(text or "")
    n = len(body.strip())
    longest = _longest_run(body)
    punct = sum(1 for ch in body if ch in _PAUSE_CHARS)
    density = (punct / n * 100.0) if n else 0.0
    unhealthy = (longest > max_run) or (density < min_density and n >= max_run)
    return {
        "chars": n,
        "max_unpunctuated_run": longest,
        "punct_density_per_100": round(density, 2),
        "is_unhealthy": bool(unhealthy),
    }


def check_text_punctuation(text: str, max_run: int = MAX_UNPUNCTUATED_RUN,
                           min_density: float = MIN_DENSITY_PER_100) -> list[dict]:
    """对整章按自然段检查，返回所有不健康段落（含段索引与原文），供定点修复。"""
    bad = []
    for idx, para in enumerate((text or "").split(chr(10))):
        p = para.strip()
        if not p:
            continue
        r = check_paragraph_punctuation(p, max_run=max_run, min_density=min_density)
        if r["is_unhealthy"]:
            bad.append({"para_index": idx, "text": p, **r})
    return bad


_ALL_PUNCT_RE = re.compile(r"[\s，。！？!?…、；：：\"''''（）()《》〈〉,.‿~·:;、]")


def strip_all_punctuation(text: str) -> str:
    return _ALL_PUNCT_RE.sub("", text or "")


def is_punctuation_only_fix(original: str, fixed: str) -> bool:
    """校验"仅加标点"修复是否合规：去掉全部标点和空白后必须逐字相同。

    模型若顺带增删/替换/改写了任何文字，即判不合规（应改走整段重生）。
    """
    return strip_all_punctuation(original) == strip_all_punctuation(fixed)


# ============================================================================
# CC round-12 R2：句末口径长句检测 + 零字改写安全断句
# ============================================================================

_LONG_SENT_STRONG_BOUNDARY = (
    "于是", "然后", "接着", "随后", "最后", "终于", "直到", "这时", "那一刻",
    "此刻", "此时", "突然", "忽然", "霎时", "刹那", "很快", "不久", "因此",
    "所以", "但是", "可是", "然而", "不过", "紧接着", "这时候",
)
_LONG_SENT_WEAK_BOUNDARY = (
    "因为", "由于", "如果", "假如", "虽然", "尽管", "一边", "一面", "随即",
    "渐渐", "逐渐", "慢慢", "缓缓", "隐约", "似乎", "仿佛", "好像", "就在",
    "同时", "而且", "并且", "不禁", "只得", "只好", "下意识", "本能地",
)
_LONG_SENT_PRON_BOUNDARY = ("他", "她", "它", "那", "这")


def detect_long_sentences(text: str, hard: int = LONG_SENTENCE_HARD) -> list[dict]:
    """按句末口径（。！？之间）检测旁白超长句（中文字数>=hard）。引号内对话豁免。

    CC round-12 R2b：引号内容替换为占位符而非直接删空，避免引号前后旁白
    被错误拼接成假长句。
    CC round-12 R2c：引号占位符（chr(0)）与换行（\n）亦为硬句界，不参与字数统计、
    不作为切分插入点，确保跨对话/跨段旁白不拼成假长句。
    """
    body, _ = _body_with_quote_sentinel(text or "")
    # 用硬句界切分：句号 + chr(0) + 换行
    splits = list(_SENTENCE_HARD_BOUNDARY_PAT.finditer(body))
    sentences: list[tuple[int, str]] = []
    last_end = 0
    for m in splits:
        sent = body[last_end:m.end()]
        if sent.strip():
            sentences.append((len(sentences), sent))
        last_end = m.end()
    tail = body[last_end:]
    if tail.strip():
        sentences.append((len(sentences), tail))
    result: list[dict] = []
    for idx, sent in sentences:
        cn_chars = len(_CN_RE.findall(sent))
        if cn_chars >= hard:
            result.append({"sentence_index": idx, "cn_chars": cn_chars, "text": sent.strip()})
    return result


# Strong-only boundaries: only these can split a long sentence (they yield independent clauses).
_LONG_SENT_BOUNDARIES_FOR_SPLIT = _LONG_SENT_STRONG_BOUNDARY

# CC round-12 R2b：逗号后可作为切分点的词（强/弱连接词 + 代词主语）
_LONG_SENT_COMMA_BOUNDARY_WORDS = (
    # 强连接词
    "于是", "然后", "接着", "随后", "最后", "终于", "直到", "这时", "那一刻",
    "此刻", "此时", "突然", "忽然", "霎时", "刹那", "很快", "不久", "因此",
    "所以", "但是", "可是", "然而", "不过", "紧接着", "这时候",
    # 弱连接词
    "随即", "因为", "由于", "如果", "假如", "虽然", "尽管", "一边", "一面",
    "渐渐", "逐渐", "慢慢", "缓缓", "隐约", "似乎", "仿佛", "好像", "就在",
    "同时", "而且", "并且", "不禁", "只得", "只好", "下意识", "本能地",
)
# 代词主语（后接动词性分句）
_LONG_SENT_PRONOUN_BOUNDARY = ("他", "她", "它", "那", "这")
# CC round-18 P8B：枚举分号升级候选——分号右侧紧跟连接词/代词主语时升级为句号
_LONG_SENT_SEMICOLON_CONNECTIVES = (
    "想起", "还有", "又有", "却又", "却也", "但又", "同时又", "还有一",
    "以及", "乃至", "进而", "从而", "反之", "相对", "相较", "对应", "同样",
    "同理", "另起", "继续", "接着又", "随后又", "然后再",
)
# CC round-18 P8B：破折号同位语枚举边界——破折号内解释段结束时，后续分号可升级
_LONG_SENT_EM_DASH_CLOSE = ("——", "―", "─")
# CC round-18 P8B：逗号链兜底阈值（连续逗号数≥threshold 且无强边界词时触发）
_LONG_SENT_COMMA_CHAIN_THRESHOLD = 4
# CC round-18 P8C：C1A 逗号链最小剩余逗号数（至少还需1个逗号才切，防止切到末尾）
_MIN_REMAINING_COMMAS_FOR_C1A = 2
# 逗号/分号/顿号（逗号升级候选）
_LONG_SENT_SPLIT_PUNCT = set("，；、")
# 最小分段字数（防止切碎残句）
_MIN_SPLIT_SEGMENT_CN = 8
# CC round-18 P8C：逗号链均衡切分最小左/右段汉字数
_MIN_COMMA_CHAIN_LEFT_CN = 12
_MIN_COMMA_CHAIN_RIGHT_CN = 12
# CC round-18 P8C：枚举分号两侧最小汉字数（放宽连接词要求）
_MIN_ENUM_SEMI_CN = 12
# CC round-18 P8C：逗号链右侧独立小句起始词（谓语短语引导词）
_COMMA_CHAIN_RIGHT_OPENERS = (
    "像", "不是", "将", "把", "被", "在", "向", "朝", "又", "也", "只", "都", "就", "才",
    "有", "是", "有", "还", "又", "再", "且", "并", "同", "跟", "与",
)
# CC round-18 P8C：助词黑名单（逗号右侧不得以此起头，防切碎）
_COMMA_CHAIN_AUX_BLACKLIST = ("的", "地", "得", "了", "着", "过")
# CC round-18 P8C：地名/方位词中“地/上/下/中”等不构成助词切分阻止
# 当助词后紧跟名词性字符（方位/处所/物体）时视为复合词而非语法助词
_COMMA_CHAIN_AUX_COMPOUND_FOLLOWERS = set("上下中里外边面头手心病口眼鼻耳肩背胸腹腿脚门窗桌椅床车山 waters 树木花草土石路街巷村庄城天云风雨雪雷电日月星火光影音声色触念思情意魂魄精神气血血肉筋骨脉络皮毛发指牙舌唇颊腮颈")
_COMMA_CHAIN_AUX_COMPOUND_FOLLOWERS |= set("一二三四五六七八九十百千万亿两第初再又还也却但而并且或或者或若即就算纵纵使即便纵然哪怕即使只要只有除非不管不论无论无论")
# CC round-18 P8C：连词黑名单（逗号右侧不得以此起头）
_COMMA_CHAIN_CONJ_BLACKLIST = (
    "并且", "而且", "以及", "或者", "还是", "然而", "可是", "但是", "因而", "因此", "所以",
    "虽然", "尽管", "如果", "即使", "只要", "除非", "不管", "无论",
    # CC round-19 P9：承接词黑名单——避免切出残句
    "反而", "反倒", "非但", "不但", "不仅", "不光", "不只", "倒是", "却", "倒", "而",
    "与", "和", "及", "或", "被", "把", "将",
)
# CC round-19 P9：逗号左侧末尾单字若为这些助词/时态标记，右侧不得以上述承接词起头
# （防止"……时。/……里。/……中。"类残句）
_COMMA_CHAIN_LEFT_AUX_SINGLE = frozenset({"时", "里", "中", "间", "处", "旁", "后", "前", "边"})


def _split_para_on_strong_boundaries(para: str, max_splits: int = 2) -> tuple[str, int]:
    """对单段做零字改写安全断句。

    CC round-12 R2b + CC round-18 P8B + CC round-18 P8C：
    - 引号内容用占位符替换，防止相邻旁白拼成假长句。
    - Case 1：逗号/顿号 + 强边界词（原有逻辑）。
    - Case 2：无逗号时，直接匹配强边界词（原有逻辑）。
    - P8B/C1B：分号 + 枚举连接词（想起/还有/却又…）→ 升级为句号。
    - P8C C1B-fallback：分号两侧各自 cn≥12 → 升级为句号（放宽连接词要求）。
    - P8C C1A：通用逗号链均衡切分——左 cn≥12、右 cn≥12、右不以助词/连词起头 → 升级。
    - 将切分点【原地升级】为句号（不产生『，。』等冗余标点）。
    - 每句最多 max_splits 处；两段各自 >= 8 中文字才切；右向左应用。
    """
    if not para:
        return para, 0
    body, body_to_para = _body_with_quote_sentinel(para)
    if len(body) < LONG_SENTENCE_HARD + 5:
        return para, 0

    # 找出所有句子边界（在 body 上）——使用硬句界：句号 + chr(0) + 换行
    splits = list(_SENTENCE_HARD_BOUNDARY_PAT.finditer(body))
    sentences: list[tuple[int, int]] = []
    last_end = 0
    for m in splits:
        sentences.append((last_end, m.end()))
        last_end = m.end()
    tail = body[last_end:]
    if tail.strip():
        sentences.append((last_end, len(body)))

    insert_points: list[int] = []  # para offsets where to replace punct → 。
    insert_shifts: list[int] = []  # how many chars to skip after insertion (1 for punct upgrade, 0 for direct insert)
    for sent_start, sent_end in sentences:
        sent_body = body[sent_start:sent_end]
        cn_chars = len(_CN_RE.findall(sent_body))
        if cn_chars < LONG_SENTENCE_HARD:
            continue

        # 在句子内部寻找切分点
        inserted_shift = 0  # 累计前面插入导致的偏移
        found = False
        case1_handled = False  # P4: 追踪 Case 1 是否已处理当前逗号（跨迭代保持）
        for pos in range(sent_start, sent_end - 1):
            # 跳过引号占位符位置
            if body[pos] == chr(0):
                continue
            ch = body[pos]
            shift = 0
            # Case 1: 逗号/顿号 + 强/弱边界词 → 升级逗号为句号
            if ch in ('，', '、'):
                case1_handled = True
                rest = body[pos + 1:]
                skip = 0
                while skip < len(rest) and rest[skip].isspace():
                    skip += 1
                rest = rest[skip:]
                if rest:
                    bound_w = None
                    for w in _LONG_SENT_COMMA_BOUNDARY_WORDS:
                        if rest.startswith(w):
                            bound_w = w
                            break
                    if bound_w is None and rest[0] in _LONG_SENT_PRONOUN_BOUNDARY:
                        bound_w = rest[0]
                    if bound_w is not None:
                        left_body = body[sent_start:pos]
                        right_body = body[pos + 1:]
                        left_cn = len(_CN_RE.findall(left_body))
                        right_cn = len(_CN_RE.findall(right_body))
                        if left_cn >= _MIN_SPLIT_SEGMENT_CN and right_cn >= _MIN_SPLIT_SEGMENT_CN:
                            insert_points.append(body_to_para[pos] + inserted_shift)
                            insert_shifts.append(1)  # 替换逗号，跳过1个字符
                            inserted_shift += 1
                            found = True
                            break
            # P8C C1A: 通用逗号链均衡切分——左 cn≥12、右 cn≥12、右侧不以助词/连词起头
            # 注意：必须是独立的 if（非 elif），因为 Case 1 也可能进入此分支（当 bound_w 为 None）
            if ch == '，' and case1_handled:
                remaining_commas = sum(1 for c in body[pos:sent_end] if c == '，')
                if remaining_commas >= _MIN_REMAINING_COMMAS_FOR_C1A:
                    left_body = body[sent_start:pos]
                    right_body = body[pos + 1:]
                    left_cn = len(_CN_RE.findall(left_body))
                    right_cn = len(_CN_RE.findall(right_body))
                    if left_cn >= _MIN_COMMA_CHAIN_LEFT_CN and right_cn >= _MIN_COMMA_CHAIN_RIGHT_CN:
                        # 检查逗号是否在破折号解释内部（—…—之间）
                        prev_dash = body[sent_start:pos].rfind('——')
                        next_dash = body[pos:sent_end].find('——', 1)
                        in_dash_explain = (prev_dash >= 0 and next_dash > 0 and next_dash > pos)
                        if not in_dash_explain:
                            # 检查右侧首字符是否是助词或连词（不应切碎）
                            right_rest = right_body.lstrip()
                            right_first = right_rest[0] if right_rest else ''
                            right_starts_with_aux = (
                                right_first in _COMMA_CHAIN_AUX_BLACKLIST
                                and not right_rest[len(right_first):len(right_first)+1]
                                .startswith(tuple(_COMMA_CHAIN_AUX_COMPOUND_FOLLOWERS))
                            )
                            right_starts_with_conj = any(
                                right_rest.startswith(cwj) for cwj in _COMMA_CHAIN_CONJ_BLACKLIST
                            )
                            # CC round-19 P9：逗号左侧末字为"时/里/中"等时，右侧不得以上述承接词起头
                            left_last_char = left_body[-1] if left_body else ''
                            left_ends_with_aux = left_last_char in _COMMA_CHAIN_LEFT_AUX_SINGLE
                            if not right_starts_with_aux and not right_starts_with_conj and not (left_ends_with_aux and right_starts_with_conj):
                                insert_points.append(body_to_para[pos] + inserted_shift)
                                insert_shifts.append(1)  # 替换逗号为句号
                                inserted_shift += 1
                                found = True
                                break
            # P8B/C1B: 分号 + 枚举连接词（想起/还有/却又…）→ 升级为句号
            elif ch == '；':
                rest = body[pos + 1:]
                skip = 0
                while skip < len(rest) and rest[skip].isspace():
                    skip += 1
                rest = rest[skip:]
                if rest:
                    # 预先计算左右段汉字数（P8B/C1B 和 P8C C1B-fallback 都需要）
                    left_body = body[sent_start:pos]
                    right_body = body[pos + 1:]
                    left_cn = len(_CN_RE.findall(left_body))
                    right_cn = len(_CN_RE.findall(right_body))
                    is_connective = any(rest.startswith(w) for w in _LONG_SENT_SEMICOLON_CONNECTIVES)
                    is_pronoun = rest[0] in _LONG_SENT_PRONOUN_BOUNDARY
                    if is_connective or is_pronoun:
                        if left_cn >= _MIN_SPLIT_SEGMENT_CN and right_cn >= _MIN_SPLIT_SEGMENT_CN:
                            insert_points.append(body_to_para[pos] + inserted_shift)
                            insert_shifts.append(1)  # 替换分号为句号
                            inserted_shift += 1
                            found = True
                            break
                    # P8C C1B-fallback：分号两侧各自 cn≥12，放宽连接词要求
                    elif left_cn >= _MIN_ENUM_SEMI_CN and right_cn >= _MIN_ENUM_SEMI_CN:
                        # 检查是否在破折号解释内部（不做拆分）
                        # 规则：左侧破折号数 >= 右侧破折号数 → 枚举结构，可切；
                        #       左侧破折号数 < 右侧破折号数 → 解释内部，不切
                        left_dashes = body[sent_start:pos].count('——')
                        right_dashes = body[pos+1:sent_end].count('——')
                        if left_dashes >= right_dashes:
                            insert_points.append(body_to_para[pos] + inserted_shift)
                            insert_shifts.append(1)
                            inserted_shift += 1
                            found = True
                            break
            # Case 2: 无逗号时，直接匹配强边界词（仅强边界，不匹配弱边界/代词）
            elif any(body[pos:].startswith(w) for w in _LONG_SENT_STRONG_BOUNDARY):
                left_body = body[sent_start:pos]
                right_body = body[pos + 1:]
                left_cn = len(_CN_RE.findall(left_body))
                right_cn = len(_CN_RE.findall(right_body))
                if left_cn >= _MIN_SPLIT_SEGMENT_CN and right_cn >= _MIN_SPLIT_SEGMENT_CN:
                    insert_points.append(body_to_para[pos] + inserted_shift)
                    insert_shifts.append(0)  # 直接在边界词前插入
                    inserted_shift += 0
                    found = True
                    break
            if len(insert_points) >= max_splits:
                break
        if len(insert_points) >= max_splits:
            break

    if not insert_points:
        return para, 0

    out = para
    for pos, shift in zip(reversed(insert_points), reversed(insert_shifts)):
        out = out[:pos] + "。\n" + out[pos + shift:]
    return out, len(insert_points)


def split_long_sentences(text: str, hard: int = LONG_SENTENCE_HARD) -> tuple[str, dict]:
    """对超长旁白句做零字改写安全断句。引号内豁免，找不到强边界只计数不拆。

    CC round-18 P8B：迭代切分——单次 _split_para_on_strong_boundaries 可能无法将
    多处分号/逗号链全部切完（切后左侧仍≥40字），因此对每段重复调用直至稳定。
    幂等：已切完的文本再次调用返回不变。
    """
    if not text:
        return text, {"long_sentences_detected": 0, "split_count": 0}
    paras = text.split("\n")
    total_detected = 0
    total_split = 0
    out_paras: list[str] = []
    for para in paras:
        body = remove_quoted_content(para)
        long_sents = detect_long_sentences(body, hard=hard)
        if not long_sents:
            out_paras.append(para)
            continue
        total_detected += len(long_sents)
        # 迭代切分：每轮最多 max_splits 处，直到无变化或达到迭代上限
        fixed_para = para
        for _pass in range(5):  # 最多5轮，防止死循环
            new_para, n_split = _split_para_on_strong_boundaries(fixed_para, max_splits=3)
            total_split += n_split
            if new_para == fixed_para or n_split == 0:
                break
            fixed_para = new_para
        out_paras.append(fixed_para)
    return "\n".join(out_paras), {
        "long_sentences_detected": total_detected,
        "split_count": total_split,
    }


def _fix_para_long_sentences(para: str, body: str,
                              long_sents: list[dict],
                              hard: int) -> str:
    """兼容旧接口：委托给 _split_para_on_strong_boundaries（迭代版本）。"""
    fixed = para
    for _ in range(5):
        new_fixed, n = _split_para_on_strong_boundaries(fixed, max_splits=3)
        if new_fixed == fixed or n == 0:
            break
        fixed = new_fixed
    return fixed


# ============================================================================
# CC28 3a：零 LLM 确定性断句修复（PunctuationSplitRepair）
# ============================================================================

REPAIR_SOFT_RUN = 36
REPAIR_HARD_RUN = 45
REPAIR_SEG_HARD = 55
REPAIR_MIN_GAP = 20
REPAIR_MAX_INSERTS_PER_PARA = 5

_STRONG_BOUNDARY = (
    "于是", "然后", "接着", "随后", "最后", "终于", "直到", "这时", "那一刻",
    "此刻", "此时", "突然", "忽然", "霎时", "刹那", "很快", "不久", "因此",
    "所以", "但是", "可是", "然而", "不过", "紧接着", "这时候",
)
_WEAK_BOUNDARY = (
    "因为", "由于", "如果", "假如", "虽然", "尽管", "一边", "一面", "随即",
    "渐渐", "逐渐", "慢慢", "缓缓", "隐约", "似乎", "仿佛", "好像", "就在",
    "同时", "而且", "并且", "不禁", "只得", "只好", "下意识", "本能地",
)
_PRON_BOUNDARY = ("他", "她", "它", "那", "这")
_QUOTE_FLIP = set(Q_L + Q_R + SQ_L + SQ_R + chr(34) + chr(39) + chr(96))


def _match_boundary(s: str, i: int):
    """返回位置 i 处命中的边界 (length, is_strong)；无命中返回 None。"""
    for w in _STRONG_BOUNDARY:
        if s.startswith(w, i):
            return len(w), True
    for w in _WEAK_BOUNDARY:
        if s.startswith(w, i):
            return len(w), False
    for w in _PRON_BOUNDARY:
        if s.startswith(w, i):
            return len(w), False
    return None


def repair_paragraph_long_runs(paragraph: str,
                               soft_run: int = REPAIR_SOFT_RUN,
                               hard_run: int = REPAIR_HARD_RUN,
                               seg_hard: int = REPAIR_SEG_HARD,
                               min_gap: int = REPAIR_MIN_GAP,
                               max_inserts: int = REPAIR_MAX_INSERTS_PER_PARA) -> tuple[str, int]:
    """对一个自然段做零 LLM 断句。返回 (修复后文本, 插入停点数)。

    只在旁白区（成对引号之外）的安全边界插入；强边界插"。\n"，弱边界插"，"。
    绝不删除/替换任何原字符；无超长 run 时原样返回。
    """
    p = paragraph or ""
    body = remove_quoted_content(p)
    if _longest_run(body) < hard_run:
        return p, 0
    n = len(p)
    inserts: list[tuple[int, str]] = []
    inside = False
    run_last = 0
    seg_last = 0
    count = 0
    i = 0
    while i < n:
        ch = p[i]
        if ch in _QUOTE_FLIP:
            inside = not inside
            i += 1
            continue
        if inside:
            i += 1
            continue
        if ch == "\n":
            run_last = seg_last = i
            i += 1
            continue
        if ch in _PAUSE_CHARS:
            run_last = i
            i += 1
            continue
        if ch.isspace():
            run_last = i
            i += 1
            continue
        gap_run = i - run_last
        gap_seg = i - seg_last
        if gap_run >= hard_run:
            inserts.append((i, "。\n"))
            run_last = seg_last = i
            i += 1
            continue
        if count < max_inserts and gap_run >= soft_run:
            m = _match_boundary(p, i)
            if m is not None:
                wlen, strong = m
                if strong:
                    inserts.append((i, "。\n"))
                    run_last = seg_last = i
                else:
                    inserts.append((i, "，"))
                    run_last = i
                count += 1
                i += wlen
                continue
        i += 1
    if not inserts:
        return p, 0
    out = p
    for pos, tok in sorted(inserts, key=lambda x: x[0], reverse=True):
        out = out[:pos] + tok + out[pos:]
    return out, count


def repair_text_punctuation(text: str,
                            max_run: int = MAX_UNPUNCTUATED_RUN,
                            min_density: float = MIN_DENSITY_PER_100) -> tuple[str, dict]:
    """对整章按自然段执行零 LLM 断句修复。CC round-12 R2：修复前额外执行长句安全断句。

    CC round-12 R2b：对每个不健康段落迭代执行 repair_paragraph_long_runs，
    直至段落不再变化（收敛）；最终复检所有子段的健康状态。
    """
    if not text:
        return text, {"changed_paragraphs": 0, "inserts": 0, "residual_unhealthy": 0,
                      "long_sentences_detected": 0, "long_sentences_resolved": 0}
    text, ls_stats = split_long_sentences(text)
    paras = text.split("\n")
    changed = 0
    inserts = 0
    for idx, para in enumerate(paras):
        p = para.strip()
        if not p:
            continue
        if not check_paragraph_punctuation(p, max_run=max_run, min_density=min_density)["is_unhealthy"]:
            continue
        fixed_para = p
        while True:
            fixed_para, k = repair_paragraph_long_runs(fixed_para)
            if k == 0 or fixed_para == p:
                break
            inserts += k
            p = fixed_para
        # 用 check_text_punctuation 做最终子段级复检，统计真正不健康的子段数
        sub_bad = check_text_punctuation(fixed_para, max_run=max_run, min_density=min_density)
        if sub_bad:
            paras[idx] = fixed_para
        else:
            changed += 1
            paras[idx] = fixed_para
    result = "\n".join(paras)
    # 最终子段级健康检查
    residual = len(check_text_punctuation(result, max_run=max_run, min_density=min_density))
    return result, {
        "changed_paragraphs": changed,
        "inserts": inserts,
        "residual_unhealthy": residual,
        "long_sentences_detected": ls_stats.get("long_sentences_detected", 0),
        "long_sentences_resolved": ls_stats.get("split_count", 0),
    }


# ============================================================================
# CC round-13 R1：终稿定稿包装器（确定性，零 LLM）
# ============================================================================

def finalize_text_long_sentences(text: str) -> tuple[str, dict]:
    """终稿级长句定稿：对完整 assembled text 执行 split_long_sentences。

    用于 pipeline_orchestrator 的三处接线：首次组装后、E-loop 补丁后、
    best_novel 选定后。内部直接调用 split_long_sentences（含引号占位符保护、
    chr(0)/\\n 硬句界、逗号升级等全部 R2b/R2c 逻辑）。

    返回 (定稿后文本, {detected, resolved, residual})，residual 为拆分后
    仍 >40 字的不可切长句计数（软 issue，不阻断）。
    """
    fixed, stats = split_long_sentences(text)
    residual = len(detect_long_sentences(fixed, hard=LONG_SENTENCE_HARD))
    return fixed, {
        "detected": stats.get("long_sentences_detected", 0),
        "resolved": stats.get("split_count", 0),
        "residual": residual,
    }

