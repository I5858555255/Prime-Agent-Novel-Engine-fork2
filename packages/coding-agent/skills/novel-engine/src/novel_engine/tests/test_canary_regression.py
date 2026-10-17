from pathlib import Path

_RULES = str(Path(__file__).resolve().parents[1] / "config" / "forbidden.json")


def test_canary_no_false_positive_on_normal_chinese():
    from novel_engine.quality.forbidden_scanner import ForbiddenScanner
    s = ForbiddenScanner(_RULES)
    text = "林晚推开石门，洞府内灵气氤氲，青石台上放着一枚玉简。她深吸一口气，目光扫过墙上的符文。"
    assert s.scan(text) == []


def test_canary_still_blocks_control_words():
    from novel_engine.quality.forbidden_scanner import ForbiddenScanner
    s = ForbiddenScanner(_RULES)
    hits = s.scan("本章需要规划 chapter hook 与 climax 结构")
    assert len(hits) > 0


def test_canary_foreign_names_not_blocked():
    from novel_engine.quality.forbidden_scanner import ForbiddenScanner
    s = ForbiddenScanner(_RULES)
    text = "夜千凛踏月而来，苏挽歌站在廊下，白衣胜雪。"
    assert s.scan(text) == []