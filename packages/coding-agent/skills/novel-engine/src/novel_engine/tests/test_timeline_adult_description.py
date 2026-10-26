"""Adult supporting characters are not protagonist age jumps."""
import pytest
from novel_engine.quality.timeline_gate import detect_timeline_jump


@pytest.mark.parametrize('markers', [[], ['成年']])
@pytest.mark.parametrize('text', [
    '她不是在拉一个成年壮汉，而是在拉一根木桩。',
    '成年男子也无法靠近那片迷雾。',
    '婴孩蜷在成年人的手掌里。',
    '他担心婴孩活不到成年。',
])
def test_adult_descriptions_keep_the_same_time(markers, text):
    assert detect_timeline_jump(text, {'max_time_progression': '当日', 'forbidden_markers': markers}, 1) is None


@pytest.mark.parametrize('markers', [[], ['成年']])
@pytest.mark.parametrize('text', [
    '陆烬长成了成年男子。',
    '陆烬已是个成年壮汉，走出了村口。',
    '成年以后，他走出了村口。',
    '他担心婴孩活不到成年。后来陆烬成年，离开了村子。',
])
def test_real_age_progression_remains_blocked(markers, text):
    assert detect_timeline_jump(text, {'max_time_progression': '当日', 'forbidden_markers': markers}, 1)
