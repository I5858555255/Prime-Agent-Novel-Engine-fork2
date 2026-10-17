# -*- coding: utf-8 -*-
"""CC round-15 R15-1：伏笔覆盖判定收紧回归测试（密闭，永不 skip）。
"""
from __future__ import annotations

from novel_engine.quality.outline_coverage_gate import (
    _check_foreshadow_coverage,
    check_scene_must_cover_beats,
    _ABNORMAL_OBJECT_TERMS,
    _INFANT_REACTION_WORDS,
)

REAL_SCENE2_TEXT = (
    "天刚蒙蒙亮，薄雾还笼着村口那口水井的青石井沿。"
    "陈老根抱着陆烬，手里提着两个空木桶，脚步比平日慢些。"
    "襁褓里的婴孩难得安睡。井边空无一人。他把陆烬小心放在井旁一块平整的石墩上，"
    "用外衣垫着，又仔细掖好襁褓边角。井绳垂入幽深的井口，木桶触水的声音闷闷地传上来，"
    "带着一股地底的凉气。他弯腰提桶，手臂肌肉绷紧，水花溅湿了裤脚。"
    "就在这时，石板路的另一头传来了脚步声。来人是赵老四，扛着扁担，"
    "扁担两头挂着空桶。他嘴里哼着不成调的曲子，步子轻快，显然是刚起，精神头正好。"
    "可当他转过村口那棵老槐树，视线落在井边时，那哼唱声就像被掐断了喉咙，戛然而止。"
    "赵老四的脚步顿住了。他先看见了弯腰打水的陈老根，目光随即扫向石墩上那团小小的襁褓。"
    "晨光熹微，落在婴儿安静的脸庞上，也落在陈老根沾着水渍的粗布衣衫上。"
    "赵老四脸上的轻松瞬间褪去，取而代之的是一种混杂着警惕、疏离，甚至是一丝不易察觉的畏惧的神色。"
    "他的嘴唇动了动，似乎想说什么客套话，可视线一触及那婴儿，话便卡在了喉咙里。"
    "陈老根直起身，将第二桶水提上来，放在脚边。他看见了赵老四，也看见了对方脸上的变化。"
    "他没有开口招呼，只是沉默地弯下腰，用井绳将两只水桶系在扁担两头。动作不紧不慢，"
    "每一个绳结都打得扎实。石墩上的陆烬似乎被这细微的动静惊扰，"
    "小小的身子动了动，发出一点含混的声响。赵老四像是被针扎了一下，"
    "猛地收回目光，原本朝向井边的脚步硬生生转了方向。他低下头，假装没看见陈老根，"
    "也没看见那婴儿，扛着空扁担，匆匆绕过井台，走向村子另一头更远的一口小水洼——"
    "那是平日里只有牲畜才去饮水的地方。他的背影有些仓促，甚至带着点逃也似的意味。"
    "陈老根系好了最后一根绳子。他直起腰，目光淡淡地扫过赵老四远去的背影，"
    "又落回石墩上。陆烬又安静下来，小手无意识地攥着襁褓边缘。陈老根走过去，俯身将婴儿"
    "重新抱进怀里。孩子的体温透过粗布传来，带着鲜活的生命力，与他掌心因打水而沾染的"
    "井水凉意形成鲜明对比。那凉意仿佛也渗进了心里。他早料到会这样。自打昨日抱着这孩子"
    "从祠堂回来，村里那些躲闪的眼神、压低的交谈，就像这清晨的雾，看不见摸不着，却无处不在。"
    "弃婴本就是晦气事，何况这孩子来得蹊跷——村外捡的，紧挨着那片让人谈之色变的禁区。"
    "村里老人传下来的话里，总有些关于来历不明婴孩的禁忌，说是会带来不祥，冲撞了村子"
    "赖以生存的那点稀薄地气。陈老根知道这些传言，他年轻时行走四方，听过更荒诞的说法。"
    "可雾隐村闭塞，村民祖祖辈辈守着这点薄田和山林讨生活，最信这些。他收养了陆烬，"
    "便等于将这份不祥揽到了自己身上。疏远与排斥，不过是再自然不过的结果。随他们去吧。"
    "陈老根心里默念了一句，脸上依旧没什么表情。他挑起水桶，扁担压在肩头，沉甸甸的。"
    "一手还得稳稳托住怀里的陆烬。两桶水加起来百十来斤，加上个孩子，走起来并不轻松。"
    "他迈开步子，沿着被露水打湿的村中小路往回走。怀里的陆烬似乎被颠簸醒了，"
    "发出细微的哼唧。陈老根调整了一下手臂的姿势，让婴儿贴得更稳些。他能感觉到那小小"
    "身躯的柔软和温度，也能感觉到自己肩头扁担的坚硬与沉重。路两旁，偶尔有早起的人家"
    "开门泼水，或是在院中收拾农具。但每每有人影晃动，看见是他，特别是看见他怀里那显眼的"
    "襁褓，动作便会停顿，然后或是迅速转身回屋，或是刻意移开视线，装作忙碌别的事情。"
    "没有一声招呼，更没有往日里邻里间吃过了没之类的寻常问询。沉默像一道无形的墙，"
    "将他与这个他生活了多年的村子隔开。只有肩上的扁担吱呀作响，还有怀中婴儿偶尔无意识"
    "的咂嘴声，陪伴着他一路行去。路旁的菜畦里，昨夜新浇的菜苗挂着水珠，绿意盈盈。"
    "而更远处，村边那些老树的枝桠依旧光秃秃的，伸向灰白的天穹，透着股冬日未尽般的枯败。"
    "这份鲜活与枯败并存的景象，此刻看在眼里，却只让人觉得这村子既熟悉又陌生。"
    "走到自家那处略显偏僻的小院外，陈老根放下水桶，单手推开篱笆门。门轴发出干涩的摩擦声。"
    "他先将陆烬抱进屋里，放在铺着旧棉絮的炕上，仔细盖好，这才转身出来，将两桶水一一"
    "提进灶房，倒入水缸。清水注入缸底，发出哗哗的声响，在寂静的院子里显得格外清晰。"
    "他站在灶房门口，用袖子擦了擦额角并不存在的汗。目光越过低矮的篱笆墙，望向村子方向。"
    "薄雾正在散去，几缕炊烟袅袅升起，那是别人家的烟火气。他这里，只有冷灶，空缸，"
    "和一个嗷嗷待哺、来历不明的孩子。院门在他身后轻轻合上，木栓落下，发出一声轻微的闷响。"
    "几乎就在同时，隔着一段距离，从村子那头隐约飘来几句压得极低的交谈声，听不真切，"
    "但那断续的语调里，分明带着禁区、晦气、老根糊涂之类的字眼"
)

POSITIVE_COVERAGE_TEXT = "井底泛起浊气，襁褓中的陆烬本能侧过头、屏息避开那股异味。"
POSITIVE_COVERAGE_SHORT = "井底泛起浊气，陆烬本能侧头屏息避开那股异味。"
BEAT = "[F001] 陆烬对井边浊气本能侧头避开"


def test_abnormal_object_terms_contains_zhuoqi():
    assert "浊气" in _ABNORMAL_OBJECT_TERMS
    assert "瘴气" in _ABNORMAL_OBJECT_TERMS
    assert "异样气息" in _ABNORMAL_OBJECT_TERMS


def test_abnormal_object_terms_excludes_well_words():
    assert "井" not in _ABNORMAL_OBJECT_TERMS
    assert "打水" not in _ABNORMAL_OBJECT_TERMS
    assert "井边" not in _ABNORMAL_OBJECT_TERMS
    assert "井台" not in _ABNORMAL_OBJECT_TERMS


def test_infant_reaction_excludes_adult_behaviors():
    assert "避开" not in _INFANT_REACTION_WORDS
    assert "转身" not in _INFANT_REACTION_WORDS
    assert "停顿" not in _INFANT_REACTION_WORDS
    assert "警觉" not in _INFANT_REACTION_WORDS
    assert "躲" not in _INFANT_REACTION_WORDS


def test_infant_reaction_includes_baby_specific():
    assert "侧头" in _INFANT_REACTION_WORDS
    assert "屏息" in _INFANT_REACTION_WORDS
    assert "本能" in _INFANT_REACTION_WORDS
    assert "缩" in _INFANT_REACTION_WORDS
    assert "躲开" in _INFANT_REACTION_WORDS


def test_real_scene2_not_covered():
    covered, reason = _check_foreshadow_coverage(REAL_SCENE2_TEXT, BEAT)
    assert covered is False, f"Expected MISS, got reason={reason}"
    assert "abnormal_object" in reason or "not in scene_text" in reason


def test_real_scene2_check_scene_must_cover_beats_miss():
    beats = [
        {"scene_num": 2, "beat_text": BEAT,
         "category": "foreshadow", "foreshadow_id": "F001"},
    ]
    passed, missing = check_scene_must_cover_beats(REAL_SCENE2_TEXT, beats, scene_id=2)
    assert passed is False
    assert len(missing) >= 1
    assert "F001" in missing[0]


def test_adult_avoid_not_counted_as_infant_reaction():
    text = "赵老四在井边刻意避开陈老根、转身就走。陆烬安静坐在旁边。"
    covered, reason = _check_foreshadow_coverage(text, BEAT)
    assert covered is False, f"Expected MISS, got reason={reason}"


def test_well_location_only_no_abnormal_object():
    text = "井边空无一人，陈老根打水。陆烬安静坐着。"
    covered, reason = _check_foreshadow_coverage(text, BEAT)
    assert covered is False
    assert "abnormal_object" in reason


def test_scene4_chapter_end_not_scene2_coverage():
    text = "夜里想起白日里避开浊气的事。"
    covered, reason = _check_foreshadow_coverage(text, BEAT)
    assert covered is False, f"Expected MISS, got reason={reason}"


def test_true_coverage_with_infant_reaction_near_subject():
    covered, reason = _check_foreshadow_coverage(POSITIVE_COVERAGE_TEXT, BEAT)
    assert covered is True, f"Expected covered, got reason={reason}"


def test_true_coverage_short():
    covered, reason = _check_foreshadow_coverage(POSITIVE_COVERAGE_SHORT, BEAT)
    assert covered is True, f"Expected covered, got reason={reason}"


def test_r14c_false_coverage_still_fails():
    text = ("陈老根去村口打水，赵老四远远避开交谈，"
            "陆烬安静地坐在旁边看着。")
    covered, reason = _check_foreshadow_coverage(text, BEAT)
    assert covered is False, f"Expected MISS, got reason={reason}"


def test_proximity_window_infant_reaction():
    distant_text = ("陆烬安静地躺在炕上。" + "。" * 50 + "井底泛起浊气。"
                    "远处的赵老四侧过头看着那边。")
    covered, reason = _check_foreshadow_coverage(distant_text, BEAT)
    assert covered is False, f"Expected MISS, got reason={reason}"


def test_scene_check_passes_true_coverage():
    beats = [
        {"scene_num": 2, "beat_text": BEAT,
         "category": "foreshadow", "foreshadow_id": "F001"},
    ]
    passed, missing = check_scene_must_cover_beats(POSITIVE_COVERAGE_TEXT, beats, scene_id=2)
    assert passed is True
    assert missing == []


def test_scene_check_fails_no_abnormal_object():
    beats = [
        {"scene_num": 2, "beat_text": BEAT,
         "category": "foreshadow", "foreshadow_id": "F001"},
    ]
    scene_text = "井边空无一人，陈老根打水。陆烬安静坐着。"
    passed, missing = check_scene_must_cover_beats(scene_text, beats, scene_id=2)
    assert passed is False
    assert len(missing) == 1
    assert "F001" in missing[0]


def test_scene_check_different_scene_not_confused():
    beats = [
        {"scene_num": 2, "beat_text": BEAT,
         "category": "foreshadow", "foreshadow_id": "F001"},
        {"scene_num": 4, "beat_text": "[F001] 夜里想起避开浊气的事",
         "category": "foreshadow", "foreshadow_id": "F001"},
    ]
    scene2_text = "井边空无一人，陈老根打水。陆烬安静坐着。"
    passed, missing = check_scene_must_cover_beats(scene2_text, beats, scene_id=2)
    assert passed is False


# ── ch426 回归：普通章伏笔 beat 无婴儿反应词，异常对象命中即覆盖 ───────────
NON_INFANT_BEAT = "[F001] 旗号金纹与古字异象产生视觉共振"


def test_non_infant_beat_abnormal_hit_is_covered():
    # 普通章（非婴儿篇）F001 伏笔：场景出现"异象"，但无婴儿/反应词。
    # R15-1 婴儿篇双条件不得外溢——异常对象命中即判覆盖（ch426 根因回归）。
    text = "考院前旗号猎猎，陆烬抬眼，金纹与古字在日光下浮起一层异象。"
    covered, reason = _check_foreshadow_coverage(text, NON_INFANT_BEAT)
    assert covered is True, f"Expected covered, got reason={reason}"
    assert "non-infant-beat" in reason


def test_non_infant_beat_abnormal_missing_still_miss():
    # 异常对象词未在场景出现时仍判 miss（条件1 不受影响）
    text = "考院前旗号猎猎，陆烬抬眼望向长街尽头。"
    covered, reason = _check_foreshadow_coverage(text, NON_INFANT_BEAT)
    assert covered is False, f"Expected MISS, got reason={reason}"
    assert "abnormal_object" in reason


def test_non_infant_beat_scene_must_cover_passes():
    beats = [
        {"scene_num": 2, "beat_text": NON_INFANT_BEAT,
         "category": "foreshadow", "foreshadow_id": "F001"},
    ]
    scene_text = "考院前旗号猎猎，陆烬抬眼，金纹与古字在日光下浮起一层异象。"
    passed, missing = check_scene_must_cover_beats(scene_text, beats, scene_id=2)
    assert passed is True
    assert missing == []


def test_infant_beat_still_requires_reaction_even_with_abnormal():
    # 婴儿篇 beat（含反应词"侧头"）双条件收紧必须保留：有异常词但无婴儿反应仍 miss
    text = "井底泛起浊气。陆烬安静地站在井边看着。"
    covered, reason = _check_foreshadow_coverage(text, BEAT)
    assert covered is False, f"Expected MISS, got reason={reason}"
    assert "infant_reaction" in reason
