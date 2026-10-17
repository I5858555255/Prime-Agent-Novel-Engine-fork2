"""统一异常类型（pkg2a 新增，pkg2b 继续使用）。

标准库 only，零第三方依赖。
"""


class EngineError(Exception):
    """引擎根异常，带可选 chapter / phase / transient 属性。"""

    def __init__(self, message="", chapter=None, phase=None, transient=False):
        super().__init__(message)
        self.chapter = chapter
        self.phase = phase
        self.transient = transient


class TransientLLMError(EngineError):
    """网络/限流/服务端/空闲超时类错误（transient=True），可退避重试。"""

    def __init__(self, message="", chapter=None, phase=None):
        super().__init__(message, chapter=chapter, phase=phase, transient=True)


class PermanentLLMError(EngineError):
    """400/401/403 等配置或请求确定性错误（transient=False），不做退避重试。"""

    def __init__(self, message="", chapter=None, phase=None):
        super().__init__(message, chapter=chapter, phase=phase, transient=False)


class ContentRejectError(EngineError):
    """英文硬门/合规/结构契约等「内容不达标」，不是基础设施问题（transient=False）。"""

    def __init__(self, message="", chapter=None, phase=None):
        super().__init__(message, chapter=chapter, phase=phase, transient=False)


class ConfigFatalError(EngineError):
    """配置/代码确定性错误（缺 key、schema 校验失败、KeyError 类），应立即停机。"""

    def __init__(self, message="", chapter=None, phase=None):
        super().__init__(message, chapter=chapter, phase=phase, transient=False)
class SceneUnrecoverableError(EngineError):
    """场景内容不可恢复（连续越界/近空判废）。"""

    def __init__(self, message="", chapter=None, scene_id=None):
        super().__init__(message, chapter=chapter, phase="write", transient=False)
        self.scene_id = scene_id


class ChapterResampleRequiredError(EngineError):
    """整章需要重新规划/重采样（CC round-7 P0-2）。

    同一章出现 >=2 个近空场景时，逐场景重试只是在一个退化批次里反复砸调用；
    此异常要求直接 HALT 本章并交看门狗整章重采，且重采时绕过 director 缓存、
    带上新的 end_state / timeline 约束重新规划（质量类重规划，非基础设施故障）。
    """

    def __init__(self, message="", chapter=None, scene_ids=None, replan=None):
        super().__init__(message, chapter=chapter, phase="write", transient=False)
        self.scene_ids = list(scene_ids or [])
        # CC round-8: 质量重规划细分原因。near_empty=scene_near_empty_cluster（默认），
        # continuity=continuity_block（章内时序/因果倒置无法定点修复，需重排重规划）。
        self.replan = replan or "scene_near_empty_cluster"


class ChapterQualityGapError(ChapterResampleRequiredError):
    """CC round-20：质量硬伤在重生+确定性自救后仍不可消除，但不应整批 HALT。

    编排层抛此异常后，runner 走 gap-continue：隔离该章 + flag 人验 + 记 gap 台账 +
    游标继续下一章，而不是中断整批。语义上是"本章留空待补"，不是基础设施故障，
    也不是要求导演重排（重排已尝试仍失败）。
    """

    def __init__(self, message="", chapter=None, scene_ids=None, replan="continuity_gap",
                 gap_kind=None, violations=None):
        super().__init__(message, chapter=chapter, scene_ids=scene_ids, replan=replan)
        self.gap_kind = gap_kind or replan
        self.violations = list(violations or [])
