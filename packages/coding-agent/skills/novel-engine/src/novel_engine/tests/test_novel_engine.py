# ── 场景正文生成：四套实义词不相交句池（来自 sandbox_gen2）──────────────────
OPEN = ["夜色如墨，朔风卷过旷原，陆烬独自赶路。",
        "晨雾未散，渡头小镇的茶寮刚挑起青帘。",
        "密林深处忽有异响，三道黑影自树后扑出。",
        "荒庙残碑之后，他触到一道冰冷的机括。"]
# 每场一句专属"线索"句（互不共享≥12字片段）
CLUE = ["古玉表面缓缓浮起的纹路，像是谁故意留下的一条线索。",
        "船工支吾的口供拼到一处，竟也凑出一条要紧线索。",
        "对方招法里那处迟疑，恰好暴露了师承来路的线索。",
        "残碑角落多出的半个篆字，成了破译整件往事的线索。"]
# 每场一句含"动作/发生/可见"的事件句（命中 density event 实词≥2），四套异构
ACT = ["这一连串动作发生得极快，连寒星都似可见他眼底未散的冷意。",
       "那递银子的小动作就发生在桌下，邻座几人皆可见那只悄悄按刀的手。",
       "夺刀格杀的动作接连发生，昏暗林间四下皆可见迸溅而起的泥点。",
       "机括弹动的动作已然发生，暗门缝隙里透进的微光隐约可见。"]
SETS = [
{"P":["旷原尽头","乱坟岗前","枯河河床","野店残墙","断桥北侧","沙丘背风处","烽火台基","盐碱滩涂","猎户废栅","霜白田埂"],
 "V":["压低身形辨听四周","攥紧袖中温润古玉","跨过冻硬的车辙","哈开睫上凝结的霜","回望身后空无一人","数着远处零落犬吠","把干粮掰碎慢慢嚼","就着雪粒咽下冷水","替垂死的篝火添柴","摊开泛黄旧图辨认","掐算离下一镇的脚程","察觉灵气在经脉里流转","压下胸口翻涌的气血","低声背熟门规条目"],
 "O":["几点寒星","一线磷火","半块焦木","几声鸦啼","残月下的孤影","风里隐约的铃音","古玉表面的细纹","远处更夫的梆子","草尖抖落的霜","天幕低垂的墨色"]},
{"P":["茶寮靠窗的桌","卖汤饼的摊前","泊着乌篷船的埠头","咸鱼铺的檐下","挂着骡铃的桩边","典当行厚重的柜台","客栈通铺的角落","漕帮把守的卡子","卖草鞋老人的凳旁","镇东破土地庙"],
 "V":["叫了一碗粗茶慢慢喝","装作挑拣炊饼偷听邻桌","丢给小二两枚铜钱","用外乡口音含糊搭话","比对告示上的图形","记下那伙人雇船的时辰","跟船工讨水道的深浅","借添柴看清对座的脸","把听来的名号在心里排序","假意抱怨官府盘剥","摸了摸怀里空白名帖","算清各帮派间的旧怨","忍住回头去看的冲动","盘算该先收买哪一个"],
 "O":["锅里翻滚的白雾","檐下成串的干椒","掌柜拨算盘的脆响","河上摇橹的吱呀声","窗纸渗进的鱼肚白","案头豁口的粗瓷碗","门帘带进的鱼腥味","墙上褪色的年画","脚夫肩头的厚茧","烛火跳了一下的光"]},
{"P":["两株老槐之间","铺满腐叶的坡地","藤蔓绞结的隘口","溪水淹过的石面","倒塌山神像前","齐腰深的蕨草丛","斜生的老松横枝","苔滑的断崖边缘","乱石堆砌的浅坑","光线透不进的林荫"],
 "V":["贴着树干滑步避开横刀","反手抽出靴中短匕","用剑脊格开抓来的腕","听风辨位矮身扫腿","就势滚入下风头","肘击对方肋下软处","夺下劈来的柴刀","踢起碎石打向追兵眼","揪住衣领撞向树根","拧脱被擒住的臂膀","屏住呼吸伏进泥水","借藤条荡过窄沟","割断缠住脚踝的网","数清还站着的敌手"],
 "O":["刀光带起的寒芒","腐叶炸开的碎末","林鸟惊飞的乱影","伤口渗出的热血","铁器交击的火星","对手粗重的喘息","雨前闷湿的气息","断枝弹回的锐响","泥点糊住的视线","刀刃上缺口的寒光"]},
{"P":["残碑背面的凹龛","莲台底下的暗槽","剥落壁画的夹层","倒塌经柜的背后","铺地砖的活动处","裂开的山墙缝里","积灰厚积的供桌","石像底座的环扣","蛛网封死的耳室","干涸放生池的池底"],
 "V":["擦去碑面厚积的尘","指尖沿刻痕逐字摸索","对上火漆残印的纹路","按下凸起的兽首纹","听见机括轻咔一响","取出用油布裹紧的卷","就着微光读那行小字","把写着生辰的纸反复看","辨认血脉图谱的支系","想起师父临终的哑谜","将信物与古玉并在一处","听见庙外脚步逼近","迅速把卷帛藏回怀里","在两条去路间定下主意"],
 "O":["青苔覆盖的篆字","壁画残留的朱砂","暗槽里蜷伏的虫","卷帛脆薄的边沿","一行触目惊心的批语","玉面新亮起的纹路","供桌上倾倒的铜炉","耳室漏下的一线天光","潮冷里浮动的尘","自己骤然加快的心跳"]},
]
PAT=["他在{P}{V}，留意着{O}。","{P}附近，他{V}，目光落在{O}。","趁着无人，他在{P}{V}，心头闪过{O}。"]

def gen(scene_idx,target=1660):
    s=SETS[scene_idx]; t=OPEN[scene_idx]
    seen=set()
    for i in range(60):
        key=(i%len(s["V"]),(i*3+1)%len(s["P"]),(i*7+5)%len(s["O"]))
        if key in seen:continue
        seen.add(key)
        t+=PAT[i%3].format(P=s["P"][key[1]],V=s["V"][key[0]],O=s["O"][key[2]])
        if i==6: t+=CLUE[scene_idx]   # 中段插入线索句
        if i==12: t+=ACT[scene_idx]   # 插入含动作/发生/可见的事件句
        if len(t)>=target:break
    if t[-1] not in "。！？":t=t.rstrip("，、；：")+"。"
    return t


_SCENE_ACTS = [
    ["俯身贴近", "侧耳凝听", "举目远眺", "屏息凝神", "缓步绕行", "低首查看", "仰面望天", "驻足观望"],
    ["跨步上前", "探臂前伸", "回身张望", "垂目沉思", "迈步前行", "蹲身细看", "直身站定", "转首回望"],
    ["贴墙而行", "攀岩而上", "涉水而过", "穿林疾走", "循径而下", "扶杖而立", "拢袖轻拂", "拂尘而过"],
    ["拨草寻路", "倚柱稍歇", "闭目养神", "张目四顾", "捻须沉吟", "按剑环视", "举火照明", "逐字默念"]]
_SCENE_LOCS = [
    ["东廊檐下", "西岭背阴", "北窗近旁", "南阶尽头", "后园井边", "前堂门侧", "山道拐角", "渡口石阶"],
    ["巷尾墙根", "城头垛口", "河岸柳下", "桥洞深处", "庙前石坪", "树下阴影", "屋后柴堆", "篱边土径"],
    ["林隙空地", "崖畔风口", "溪中浅滩", "洞内拐弯", "坡顶平台", "谷底洼地", "岩后凹处", "树冠之下"],
    ["案头烛旁", "架顶尘间", "柜底夹层", "墙缝深处", "龛前砖下", "梁上阴影", "瓮中暗处", "屉底角落"]]
_SCENE_OBJS = [
    ["半枚铜钱", "一角残纸", "几粒沙砾", "半截绳头", "一片落叶", "数点水痕", "些许灰烬", "几缕蛛丝"],
    ["一扇旧门", "半块招牌", "几根枯枝", "一角布巾", "数声犬吠", "几点灯火", "半盏残茶", "一缕炊烟"],
    ["一片刀光", "数道爪痕", "几点血渍", "半段断刃", "一缕腥风", "几滴露水", "一片黑影", "数条藤蔓"],
    ["一纸批语", "半个篆字", "数行小字", "一角火漆", "几道裂纹", "一缕微光", "半枚指印", "一处凹痕"]]
_SCENE_ENDS = [
    ["隐约", "飘忽", "明灭", "沉浮", "若现", "游移", "闪烁", "明晦"],
    ["若隐", "徐来", "渐远", "未散", "微动", "轻颤", "低回", "悠长"],
    ["乍现", "未歇", "稍定", "犹存", "暗涌", "初醒", "未明", "渐沉"],
    ["泛冷", "发沉", "生涩", "滞重", "发烫", "微苦", "泛潮", "作痛"]]


def _scene_text(scene_num: int, target: int = 1000) -> str:
    """按 scene_num 生成场景正文。

    句子模板「第{seq}程，他{act}{loc}，{obj}{end}。」：act×loc 与 obj×end
    各 64 种组合，句数（约 50）不超过组合数 → 任何 8 字窗口全局唯一
    （validate_scene_text 的 high_freq_repeat 不触发）；四套词表互不相交 →
    跨场景 8 字/句级零共享（cross-scene repeat gate 不触发）。
    target 取 task_card 该场景的 word_count_target。
    """
    card = json.loads(_mock_full_task_card(1))
    for bp in card.get("scene_blueprints", []):
        if int(bp.get("scene_num", 0) or 0) == int(scene_num or 0):
            target = int(bp.get("word_count_target", 1000) or 1000)
            break
    scene_num = int(scene_num or 1)
    acts, locs, objs, ends = (
        _SCENE_ACTS[(scene_num - 1) % 4], _SCENE_LOCS[(scene_num - 1) % 4],
        _SCENE_OBJS[(scene_num - 1) % 4], _SCENE_ENDS[(scene_num - 1) % 4])
    out: list[str] = []
    k = 0
    while sum(len(x) for x in out) < target:
        # 单场景内序号唯一（k+1 < 100 → 中文数字）；跨场景唯一性由四套
        # 互不相交词表保证（8 字窗口必含词表词）。
        seq = k + 1
        act = acts[(k * 3 + scene_num) % len(acts)]
        loc = locs[(k * 5 + scene_num * 2) % len(locs)]
        obj = objs[(k * 7 + scene_num) % len(objs)]
        end = ends[(k * 11 + scene_num) % len(ends)]
        # 每个词缀唯一序号：任何 8 字滑窗必含序号片段 → 单场景内 8 字窗口全局唯一
        n = _cn_num(seq)
        out.append(f"第{n}程，他{act}{n}{loc}{n}，{obj}{n}{end}{n}。")
        k += 1
        if k > 400:
            break
    body = "".join(out)[:target]
    # 测试断言成品含"夜色如墨"（OPEN[0] 意象）——仅场景 1 前置，跨场景无共享。
    if int(scene_num) == 1:
        body = "夜色如墨，浓雾自荒原尽头漫卷而来。" + body
    # density gate 覆盖：concrete_events（动作发生/可见行为）与
    # info_reveal_points（线索）实词必须出现；场景间共享前缀 <12 字
    # （"动作发生状可见，第"=10 字）→ 不触发 cross-scene repeat 的 lcs≥12。
    body += f"动作发生状可见，第{_cn_num(scene_num)}条线索。"
    if body[-1] not in "。！？":
        body = body.rstrip("，、；：") + "。"
    return json.dumps({
        "scene_id": scene_num, "scene_text": body,
        "hook": "暗处一道目光骤然投来，危机未散。",
        "beats": [str(i) for i in range(6)],
        "beats_covered": [str(i) for i in range(6)],
    }, ensure_ascii=False)

"""
测试与验证 Novel-Engine 核心功能：确保 SQL 精确状态检索、Session 树分支与回滚、增量缝合 Patcher 全部运行完美！
"""
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from novel_engine.engine.db import StateDB
from novel_engine.engine.session import SessionTree
from novel_engine.engine.patcher import IncrementalPatcher
import json

from novel_engine.core.llm_client import _mock_synopsis, _mock_full_task_card, _cn_num


# ── 四套异构句子池：每套主题/词汇/句式完全独立 ─────────
POOL_S1 = [
    "韩玄提剑前冲，剑光如虹斩向敌人咽喉。",
    "他侧身滑步避开利刃，反手一掌拍出。",
    "韩玄飞身跃起，双脚踏碎后方岩石。",
    "他旋身踢出，腿风扫落满地枯枝败叶。",
    "韩玄咬破舌尖，以血激发体内灵力。",
    "他纵身掠过树冠，衣袂在风中猎猎作响。",
    "韩玄挥剑劈开面前的藤蔓障碍。",
    "他飞脚踹断门栓，木门轰然倒地。",
    "韩玄甩出符纸，金光一闪困住敌人。",
    "他翻墙越脊，轻巧落在另一座院墙。",
    "韩玄拧转手腕，剑尖点在敌人腕脉。",
    "他闪身绕过石柱，避过迎面飞来的暗器。",
    "韩玄双手结印，一道护盾凭空浮现。",
    "他跃上梁柱，从高处俯瞰下方局势。",
    "韩玄踏碎地面，碎石溅起迷住对手双眼。",
    "他矮身钻过门框，顺势滚入走廊。",
    "韩玄双掌推出，气浪将前方敌人掀飞。",
    "他抓住垂落的藤蔓，荡过宽阔峡谷。",
    "韩玄掷出短刃，钉入敌人肩头将其定住。",
    "他飞步登上台阶，三级并跃直达厅堂。",
    "韩玄扣住敌人咽喉，将其狠狠按在墙上。",
    "他翻身落地，长剑已稳稳指向前方。",
    "韩玄跃上屋檐，足尖点瓦如履平地。",
    "他抽出腰间软剑，剑身弯如满月。",
    "韩玄踏水而行，脚尖轻点涟漪不散。",
    "他倒挂金钩，从梁上直坠敌群中心。",
    "韩玄挥袖拂去肩头落雪，神色自若。",
    "他飞脚踢出，将逼近的敌人踹下悬崖。",
    "韩玄捏碎传音玉简，将情报送回宗门。",
    "他跃上战马，缰绳一勒策马疾驰。",
    "韩玄拔出匕首，抵住敌人颈动脉。",
    "他矮身扫堂腿，将对手膝盖骨踢碎。",
    "韩玄双手合十，灵力凝聚成光球射出。",
    "他翻身滚入草垛，藏身躲避追兵视线。",
    "韩玄飞脚踢飞桌上茶盏，热水泼向敌人。",
    "他抓住敌人手腕，反关节将其按倒在地。",
    "韩玄腾空而起，空中连环三脚连踢。",
    "他伏地滑行，从桌底钻到对方背后。",
    "韩玄甩出锁链，铁钩缠住敌人双腿。",
    "他纵身越过火墙，衣角未沾半点火星。",
    "韩玄扣动机括，石墙机关轰然启动。",
    "他飞身捞起坠落的师妹，稳稳落地。",
    "韩玄横剑格挡，震得对手虎口发麻。",
    "他倒提长剑，从剑柄滑入对方怀中。",
    "韩玄跃上树梢，枝丫不堪重负弯折。",
    "他飞脚踢翻门板，木板碎裂声中冲入屋内。",
    "韩玄双指并拢，灵力化剑点向敌人穴道。",
    "他旋身扫腿，将逼近的三人全部放倒。",
    "韩玄翻墙而入，落地无声宛若狸猫。",
    "他飞身接住毒镖，反手掷回袭击者。",
    "韩玄提气纵跃，三丈宽护城河一跃而过。",
    "他矮身切入敌阵，剑走偏锋直取要害。",
    "韩玄飞脚踢飞手中长刀，刀身入木三分。",
    "他抓一把泥沙撒向敌人面门，趁机突围。",
    "韩玄凌空翻身，避开脚下绊索落地稳健。",
    "他飞身扑向敌人，将其撞得跌出窗外。",
    "韩玄双掌齐出，左右开弓击倒两名刺客。",
    "他踩碎瓦片制造声响，引开追兵注意。",
    "韩玄提剑斜劈，剑光划过半空应声而断。",
    "他飞身掠上屋檐，如燕子穿檐般轻盈。",
    "韩玄拧腰送胯，一拳将敌人轰出三米。",
    "他倒翻筋斗，从马腹下闪过躲过追杀。",
    "韩玄拔剑格开三支冷箭，剑鸣清越。",
    "他飞身跃上巨石，居高临下观察地形。",
    "韩玄甩出千丝线，将远处敌人缚个结实。",
    "他矮身钻入车底，拖着身体缓缓前行。",
    "韩玄腾空翻转，双足连环踢中敌人胸口。",
    "他飞身捞起地上断剑，反手刺入敌喉。",
    "韩玄双掌推泰山，将巨像推得摇晃欲倒。",
    "他翻身落地，顺势将敌人拖入掩体。",
    "韩玄跃上房梁，屏息聆听下方动静。",
    "他飞脚踢翻油灯，火光映红了整面墙壁。",
    "韩玄捏碎辟毒丹，将剧毒化解于无形。",
    "他矮身从敌裆下钻过，起身时剑已抵喉。",
    "韩玄腾空后翻，避开身后偷袭的毒针。",
    "他飞身跃入河中，潜水游出百步远。",
    "韩玄双指弹飞来袭暗器，叮当落地。",
    "他翻墙入院，落地时已摆好戒备姿态。",
    "韩玄提气跃上三丈高墙，衣袂飘飘。",
    "他飞身扑向敌人下盘，将其绊倒在地。",
    "韩玄甩出符纸，火焰顿时吞噬半间屋宇。",
    "他矮身绕过守卫，贴墙潜行如鬼魅。",
    "韩玄双掌翻飞，将逼近的飞刃全部击落。",
    "他飞身掠上树梢，折枝掷向追兵。",
    "韩玄拧身避开一刀，顺势扣住敌人手腕。",
    "他倒翻而出，从窗棂间翻进密室。",
    "韩玄踏雪无痕，雪地脚印瞬间消失不见。",
    "他飞身跃上战马，缰绳一抖马嘶人起。",
]

POOL_S2 = [
    "夜雾自谷底升起，如轻纱笼罩整片山林。",
    "远处山峦在夜色中蜿蜒起伏，轮廓如龙脊。",
    "月光穿透云层缝隙，洒下斑驳陆离的银辉。",
    "松涛阵阵，如潮水般在山谷间来回激荡。",
    "夜风裹挟着寒意，从林间缝隙中呼啸穿过。",
    "溪涧潺潺流淌，水声在寂静夜里格外清晰。",
    "石阶上覆满青苔，在月光下泛着幽绿光泽。",
    "枯藤老树缠绕交错，在风中发出嘎吱声响。",
    "远处传来不知名夜鸟的啼鸣，凄厉而悠长。",
    "晨露打湿衣襟，带着草木特有的清苦气息。",
    "岩壁上渗出水珠，汇聚成细流蜿蜒而下。",
    "天边残月如钩，悬挂在墨色苍穹一角。",
    "萤火虫在草丛中明明灭灭，如碎星散落。",
    "寒风卷起落叶，在地面上打着旋儿飞舞。",
    "雾气渐浓，十步之外已难辨人影轮廓。",
    "山泉撞击礁石，溅起晶莹水花四散飞溅。",
    "远处峰顶积雪反射月光，泛着冷冽银蓝。",
    "林间偶有兽踪踏过，留下新鲜爪痕印记。",
    "夜色如浓墨泼洒，天地间只剩下无边沉寂。",
    "山崖边野花摇曳，散发淡淡幽香沁人心脾。",
    "云层裂开缝隙，月光如瀑布倾泻而下。",
    "松针铺满地面，踩上去柔软无声如毯。",
    "远山犬吠隐隐传来，更衬出四野空旷。",
    "溪水冰凉刺骨，倒映着满天星斗碎银。",
    "雾气弥漫不散，将整座古刹笼罩其中。",
    "夜寒砭骨，呼气成霜在空气中凝而不散。",
    "远处闪电划破天际，照亮嶙峋怪石轮廓。",
    "林间雾气渐散，露出下方深邃峡谷全貌。",
    "月光如水银倾泻，在地面铺开一片寒光。",
    "山风骤起，卷落枝头积霜簌簌作响。",
    "夜色深沉如海，唯有几点渔火在远方闪烁。",
    "岩缝中钻出几株劲松，根系紧抓岩壁。",
    "远处传来沉闷雷声，预示着暴雨即将来临。",
    "露珠顺着叶尖滑落，在石板上碎成银珠。",
    "山雾缭绕峰峦，如白色绸带缠绕腰间。",
    "月光照亮古旧石阶，青苔在银辉下泛光。",
    "夜风掠过松林，万壑松涛声此起彼伏。",
    "山崖绝壁悬垂古藤，随风轻轻摇曳不定。",
    "远处星河横贯天际，璀璨如钻钉在黑绒。",
    "林间萤火明灭，如流动的光点穿梭往来。",
    "寒霜凝结在瓦檐，月光下折射七彩光晕。",
    "山泉叮咚作响，在幽谷中奏出空灵乐章。",
    "夜色中的古庙轮廓狰狞，如蛰伏巨兽。",
    "晨雾弥漫原野，远处的村落若隐若现。",
    "月光穿透密林，在地面投下斑驳光影。",
    "山风掠过草尖，发出沙沙的低语声。",
    "远山层峦叠嶂，在夜色中如黛色剪影。",
    "夜雨敲打着窗棂，滴滴答答不绝于耳。",
    "雾气中的古松轮廓隐约，如水墨画境。",
    "月光照在潭面上，泛起层层银色涟漪。",
    "山道上落叶堆积，踩上去发出碎裂声响。",
    "远处狼嚎此起彼伏，在空旷山谷回荡。",
    "夜雾笼罩古桥，桥下流水声隐隐可闻。",
    "月光如水洗过青石，泛起幽幽冷光。",
    "林间夜枭啼叫，声音在寂静中格外刺耳。",
    "山崖上野花盛放，在夜风中轻轻摇曳。",
    "远处钟声悠悠传来，穿透重重夜色。",
    "月光下的古井深不见底，隐约泛起幽光。",
    "夜风裹挟松香，从密林中扑面而来。",
    "山巅积雪在月光下熠熠生辉，璀璨夺目。",
    "林间小径被落叶覆盖，月光下斑驳陆离。",
    "远处溪流声潺潺，伴着蛙鸣此起彼伏。",
    "月光穿透云层，在雪地铺上一层银纱。",
    "山雾如轻纱缠绕古松，营造出空灵意境。",
    "夜空中星辰璀璨，银河横跨天际两端。",
    "月光照在古刹飞檐，金顶闪闪发亮。",
    "山间寒气袭人，呼吸间白雾袅袅升腾。",
    "月光下的湖面波光粼粼，如碎银铺展。",
    "远处松林在风中沙沙作响，如低声絮语。",
    "夜雾中的古桥轮廓模糊，只剩朦胧剪影。",
    "月光如水银泻地，将庭院照得宛如白昼。",
    "山涧清泉叮咚作响，声声入耳沁人心脾。",
    "夜色中的古松如苍龙盘踞，气势威严。",
    "月光穿透云层缝隙，在地面投下光柱。",
    "远处犬吠鸡鸣，打破了深山夜的沉寂。",
    "夜雾散去露出山径，月光照亮前方道路。",
]

POOL_S3 = [
    "韩玄沉声问道：你究竟是谁派来的。",
    "对方冷笑回答：你死到临头还敢嘴硬。",
    "韩玄冷哼一声：废话少说，亮招吧。",
    "青衣女子急呼：小心身后！有人偷袭。",
    "白发老者叹息：当年之事，终究逃不过。",
    "韩玄握紧剑柄，沉声道：今日必有分晓。",
    "对方阴恻恻开口：交出古玉，留你全尸。",
    "韩玄淡然回应：做梦。",
    "青衣女子哽咽：师兄，我们还能回去吗。",
    "白发老者抚须沉吟：此事另有隐情。",
    "韩玄厉声喝道：休想从我手中夺走它。",
    "对方狂笑：就凭你这点修为也敢放肆。",
    "韩玄咬牙道：今日便是你的死期。",
    "青衣女子哀求：放过他们，我一个人走。",
    "白发老者摇头：有些事，避无可避。",
    "韩玄低声道：跟我走，我来掩护你。",
    "对方嗤笑：你以为我会信你的鬼话。",
    "韩玄正色道：胜负尚未可知。",
    "青衣女子泣道：我不想再看到流血了。",
    "白发老者捋须道：年轻人，莫要冲动。",
    "韩玄沉声道：此事我管定了。",
    "对方怒喝：找死。",
    "韩玄淡淡道：你的口气倒是不小。",
    "青衣女子慌道：三位前辈，还请饶命。",
    "白发老者摇头：因果循环，报应不爽。",
    "韩玄冷声道：多说无益，出手吧。",
    "对方狞笑：死到临头还嘴硬。",
    "韩玄冷哼：要杀便杀，休要废话。",
    "青衣女子哭诉：我真的什么都不知道。",
    "白发老者叹息：天道轮回，谁能逃脱。",
    "韩玄沉声问：幕后主使究竟是谁。",
    "对方嗤笑：你以为我会告诉你吗。",
    "韩玄冷声道：你不说有的是办法让你说。",
    "青衣女子哀求：各位大侠，请行行好。",
    "白发老者摇头：贫道无能为力。",
    "韩玄沉声道：退后，我来对付他。",
    "对方狂笑：不自量力。",
    "韩玄冷哼：你也不过如此。",
    "青衣女子抽泣：为什么会变成这样。",
    "白发老者叹息：缘分已尽，何必强求。",
    "韩玄低喝：让开。",
    "对方冷笑：我猖狂又如何。",
    "韩玄沉声道：今日便让你见识真本事。",
    "青衣女子哭道：我不想死在这里。",
    "白发老者摇头：生死有命，非人力可改。",
    "韩玄冷声道：你的话太多了。",
    "对方怒喝：找死。",
    "韩玄冷哼：来啊。",
    "青衣女子哀求：各位英雄，请看在老朽面上。",
    "白发老者叹息：阿弥陀佛，善哉善哉。",
    "韩玄沉声问：古玉究竟在何处。",
    "对方冷笑：你猜。",
    "韩玄冷声道：看来不给你点教训是不行了。",
    "青衣女子哭诉：我真的不知道。",
    "白发老者摇头：施主，冤冤相报何时了。",
    "韩玄低喝：让开，否则休怪我不客气。",
    "对方冷笑：你以为我怕你。",
    "韩玄沉声道：你会怕的。",
    "青衣女子哀求：请不要伤害他。",
    "白发老者叹息：痴儿，这是劫数。",
    "韩玄冷声道：不必多言。",
    "对方怒喝：冥顽不灵。",
    "韩玄冷哼：多说无益。",
    "青衣女子哭道：求求你们别打了。",
    "白发老者摇头：一切皆有定数。",
    "韩玄沉声问：你可认得这枚玉佩。",
    "对方冷笑：不过是块普通石头。",
    "韩玄冷声道：你最好说实话。",
    "青衣女子哀求：放过她吧。",
    "白发老者叹息：贫道也是无可奈何。",
    "韩玄低喝：退后。",
    "对方冷笑：你动不了我。",
    "韩玄沉声道：那就试试。",
    "青衣女子哭道：我不想失去家人。",
    "白发老者摇头：因果循环，报应不爽。",
    "韩玄冷声道：废话少说。",
    "对方怒喝：找死。",
    "韩玄冷哼：来。",
    "青衣女子哀求：各位高手，请高抬贵手。",
    "白发老者叹息：阿弥陀佛，善哉善哉。",
    "韩玄沉声问：幕后黑手是谁。",
    "对方冷笑：你查不到。",
    "韩玄冷声道：我偏要查。",
    "青衣女子哭诉：我真的什么都不知道。",
    "白发老者摇头：施主，放下执念吧。",
    "韩玄低喝：让开。",
    "对方冷笑：你以为我怕你。",
    "韩玄沉声道：你会怕的。",
    "青衣女子哭道：我不想失去亲人。",
    "白发老者叹息：一切皆有定数。",
]

class MockLLMForFix:
    def __init__(self):
        self.call_count = 0
        self.review_count = 0
        self.scene_call_count = 0
        import threading
        self._lock = threading.Lock()

    def _next_scene_num(self, capped: bool = True) -> int:
        # 线程安全计数；capped 时取模到 SETS 范围内（gen 只有 4 套句池，
        # 扩写/润色并发调用会让计数无界增长 → SETS[i] 越界 IndexError）。
        with self._lock:
            self.scene_call_count += 1
            n = self.scene_call_count
        if capped:
            n = ((n - 1) % 4) + 1
        return n

    def chat_completion(self, messages, temperature=None, max_tokens=None, retry_on_error=True, max_retries=3, extra_body=None, timeout_spec=None):
        self.call_count += 1
        combined = "\n".join(m.get("content", "") for m in messages)

        if "审查要求" in combined:
            self.review_count += 1
            print(f"CALL {self.call_count}: MATCHED review (attempt {self.review_count})")
            # On first call, return verdict "fix" with fix_scope "场景1"
            if self.review_count == 1: # first chapter review
                return {
                    "role": "assistant",
                    "content": json.dumps({
                        "chapter_num": 1,
                        "scores": {"plot_consistency": 20, "character_consistency": 15, "foreshadow_execution": 18, "style_match": 12, "pacing": 8, "innovation": 7},
                        "total_score": 80,
                        "verdict": "fix",
                        "issues": [{"dimension": "character_consistency", "severity": "medium", "description": "场景1中好感度表现不一致", "suggested_fix": "增加对话说明"}],
                        "praise": "场景2和场景3很好",
                        "fix_scope": "场景1"
                    }, ensure_ascii=False)
                }
            else:
                return {
                    "role": "assistant",
                    "content": json.dumps({
                        "chapter_num": 1,
                        "scores": {"plot_consistency": 25, "character_consistency": 20, "foreshadow_execution": 20, "style_match": 15, "pacing": 10, "innovation": 10},
                        "total_score": 100,
                        "verdict": "pass",
                        "issues": [],
                        "praise": "完美",
                        "fix_scope": ""
                    }, ensure_ascii=False)
                }
        elif "生成任务卡" in combined or "生成4个场景的骨架" in combined \
                or "生成章级元数据" in combined:
            return {"role": "assistant", "content": _mock_full_task_card(1)}
        # writer 主写作：prompt 头部是"你是一位专业的网络小说作家…根据场景蓝图生成…"，
        # 且 prompt 内嵌了"剧情缩写"上下文，必须在缩写分支之前命中，否则会被下面的
        # "缩写" in combined 抢先截获，返回 352 字的 _mock_synopsis（含英文 chapter 键）。
        elif "网络小说作家" in combined or "根据场景蓝图生成" in combined:
            import re
            m = re.search(r"第\s*(\d+)\s*章第\s*(\d+)\s*场", combined)
            if m:
                scene_num = int(m.group(2))
            else:
                scene_num = self._next_scene_num()
            print(f"CALL {self.call_count}: MATCHED writer (num={scene_num})")
            d = json.loads(_scene_text(scene_num, target=1000))
            return {"role": "assistant", "content": d["scene_text"]}
        elif "缩写生成器" in combined \
                or ("剧情缩写" in combined and "网络小说作家" not in combined):
            return {"role": "assistant", "content": _mock_synopsis(1)}
        elif "补充每个场景" in combined:
            # Craft call: return full task card so craft parse yields 4 scenes
            return {"role": "assistant", "content": _mock_full_task_card(1)}
        elif "正文" in combined or "场景" in combined:
            import re
            m = re.search(r"第\s*(\d+)\s*章第\s*(\d+)\s*场景", combined)
            if m:
                scene_num = int(m.group(2))
            else:
                scene_num = self._next_scene_num()
            print(f"CALL {self.call_count}: MATCHED scene (num={scene_num})")
            d = json.loads(_scene_text(scene_num, target=1000))
            return {"role": "assistant", "content": d["scene_text"]}
        elif "补充每个场景" in combined:
            # Craft call: return full task card so craft parse yields 4 scenes with defaults
            return {"role": "assistant", "content": _mock_full_task_card(1)}
        elif "场景原内容" in combined:
            print(f"CALL {self.call_count}: MATCHED patcher")
            return {"role": "assistant", "content": "【场景1：东荒村落】\n韩玄在东荒村落醒来，抚摸古玉，古玉放出微微玄光。"}
        elif "润色" in combined:
            print(f"CALL {self.call_count}: MATCHED writing/polishing")
            # polish 期望纯文本正文（writer_agent 的 _coerce_polish_text 不解析 JSON，
            # 返回 JSON 字符串会被当正文 → 含 {} 触发 prompt .format 崩溃）。
            d = json.loads(_scene_text(self._next_scene_num(), target=1000))
            return {"role": "assistant", "content": d["scene_text"]}
        else:
            return {"role": "assistant", "content": "默认回复"}


class TestNovelEngine(unittest.TestCase):

    def test_state_db(self):
        """测试代码化确定性精确查询。"""
        db = StateDB(db_path=":memory:")

        # 确保有测试数据，做自包含种子填充
        cursor = db.conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO characters (id, name, realm, location) VALUES ('C001', '韩玄', '炼气三层', '雾隐村')")
        cursor.execute("INSERT OR REPLACE INTO foreshadows (id, status) VALUES ('F001', 'planned')")
        cursor.execute("INSERT OR REPLACE INTO clue_plans (foreshadow_id, chapter, intensity, method) VALUES ('F001', 120, '隐晦提示', '古玉异动')")
        db.conn.commit()

        # 验证 C001 角色
        realm = db.get_character_realm("C001")
        self.assertEqual(realm, "炼气三层")
        self.assertTrue(db.is_character_alive("C001"))

        # 验证时序过滤伏笔线索动作
        active_fs = db.query_active_foreshadows(120)
        self.assertEqual(len(active_fs), 1)
        self.assertEqual(active_fs[0]["method"], "古玉异动")
        db.close()

    def test_session_tree(self):
        """测试 Durable Sessions 的会话分支与级联回退。"""
        tree = SessionTree()
        # 1. 提交初始 Chapter 1 快照
        root_node = tree.add_commit(1, "hash_ch_1", {"characters": {"C001": {"realm": "炼气一层"}}}, score=90)
        self.assertEqual(root_node.chapter_num, 1)

        # 2. 从 Chapter 1 分叉出两个实验分支
        tree.fork_branch("main", "branch_a_harmony")
        tree.fork_branch("main", "branch_b_fight")

        # 提交分支 A 的 Chapter 2 快照
        tree.add_commit(2, "hash_ch_2_a", {"characters": {"C001": {"realm": "炼气二层"}}}, score=95, branch_name="branch_a_harmony")
        # 提交分支 B 的 Chapter 2 快照
        tree.add_commit(2, "hash_ch_2_b", {"characters": {"C001": {"realm": "炼气三层"}}}, score=50, branch_name="branch_b_fight")

        # 3. 验证分支独立性
        history_a = tree.get_branch_history("branch_a_harmony")
        self.assertEqual(len(history_a), 2)
        self.assertEqual(history_a[-1].world_state_snapshot["characters"]["C001"]["realm"], "炼气二层")

        history_b = tree.get_branch_history("branch_b_fight")
        self.assertEqual(len(history_b), 2)
        self.assertEqual(history_b[-1].world_state_snapshot["characters"]["C001"]["realm"], "炼气三层")

        # 4. 因分支 B 评分过低（50分），执行级联回滚
        rolled_snapshot = tree.rollback_to_node(root_node.node_id, branch_name="branch_b_fight")
        self.assertEqual(rolled_snapshot["characters"]["C001"]["realm"], "炼气一层")

    def test_session_tree_serialization(self):
        """测试 SessionTree 序列化与反序列化。"""
        tree = SessionTree()
        tree.add_commit(1, "hash_ch_1", {"characters": {"C001": {"realm": "炼气一层"}}}, score=90)
        tree.fork_branch("main", "test_branch")
        tree.add_commit(2, "hash_ch_2", {"characters": {"C001": {"realm": "炼气二层"}}}, score=95, branch_name="test_branch")

        # 序列化为字典
        serialized = tree.to_dict()
        self.assertIn("nodes", serialized)
        self.assertIn("branches", serialized)
        self.assertEqual(serialized["branches"]["test_branch"], tree.branches["test_branch"])

        # 反序列化
        new_tree = SessionTree.from_dict(serialized)
        self.assertEqual(new_tree.branches["test_branch"], tree.branches["test_branch"])

        # 验证节点信息
        history = new_tree.get_branch_history("test_branch")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[-1].world_state_snapshot["characters"]["C001"]["realm"], "炼气二层")

    def test_incremental_patcher(self):
        """测试高阶增量自修复与缝合。"""
        full_text = (
            "【场景1：东荒村落】\n韩玄在东荒村落醒来，抚摸红色的旧玉佩。\n\n※\n\n"
            "【场景2：藏经阁】\n韩玄在藏经阁翻阅古籍，汗水顺着脖子流下。"
        )

        # 1. 提取特定场景文本进行分析
        scene_1 = IncrementalPatcher.extract_scene(full_text, 1)
        self.assertIn("东荒村落", scene_1)

        # 2. 精确局部缝合 (不重写其他场景)
        patched_scene_1 = "【场景1：东荒村落】\n韩玄在东荒村落醒来，抚摸古玉，古玉放出微微玄光。"
        updated_full_text = IncrementalPatcher.apply_scene_patch(full_text, 1, patched_scene_1)

        self.assertIn("古玉放出微微玄光", updated_full_text)
        self.assertIn("韩玄在藏经阁翻阅古籍", updated_full_text)  # 保证场景2完好无损！

    def test_pipeline_incremental_patcher(self):
        """测试流水线中集成 IncrementalPatcher 的局部热插拔修复逻辑。"""
        import tempfile
        import shutil
        from pathlib import Path
        import logging
        logging.basicConfig(level=logging.INFO)

        try:
            from novel_engine.core.llm_client import LLMClient
            from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
        except ImportError:
            from novel_engine.core.llm_client import LLMClient
            from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator

        tmpdir = tempfile.mkdtemp()
        try:
            # Create dummy folders to mimic "小说工程"
            root_path = Path(tmpdir)
            for d in ["config", "config/simulation", "memory/world_state", "config/foreshadow", "config/planning", "planning", "bible", "runtime"]:
                (root_path / d).mkdir(parents=True, exist_ok=True)

            # Copy or write essential files
            (root_path / "config" / "runtime_config.json").write_text('{"llm": {"use_mock": true}}', encoding="utf-8")
            (root_path / "config" / "simulation" / "rules.json").write_text('{}', encoding="utf-8")
            (root_path / "config" / "simulation" / "constraints.json").write_text('{}', encoding="utf-8")
            (root_path / "memory/world_state/characters.json").write_text('{"characters": {}}', encoding="utf-8")
            (root_path / "memory/world_state/factions.json").write_text('{"factions": {}}', encoding="utf-8")
            (root_path / "memory/world_state/power_system.json").write_text('{"current_power_balance": {}}', encoding="utf-8")
            (root_path / "config" / "foreshadow" / "registry.json").write_text('{"foreshadows": []}', encoding="utf-8")
            (root_path / "config" / "planning" / "volumes.json").write_text('{"volumes": [{"id": "V01", "chapter_range": [1, 100]}]}', encoding="utf-8")
            (root_path / "config" / "planning" / "plot_graph.json").write_text('{"nodes": []}', encoding="utf-8")
            (root_path / "bible" / "world_bible.md").write_text('', encoding="utf-8")
            (root_path / "bible" / "character_bible.md").write_text('', encoding="utf-8")
            (root_path / "bible" / "style_bible.md").write_text('', encoding="utf-8")
            (root_path / "bible" / "author_intent.md").write_text('', encoding="utf-8")
            (root_path / "planning" / "吸氧证道_V2_1_完整大纲.md").write_text('', encoding="utf-8")

            # Setup LLMClient with MockLLMForFix
            mock_llm_internal = MockLLMForFix()
            llm_client = LLMClient(use_mock=True)
            llm_client._mock = mock_llm_internal

            orchestrator = PipelineOrchestrator(project_root=tmpdir, llm_client=llm_client)
            result = orchestrator.generate_single_chapter(1)

            # 验证流程完成并应用了局部修复
            self.assertTrue(result["success"])
            self.assertEqual(result["chapter"], 1)
            # B2 zero-scaffolding：成品只含纯叙事，断言叙事正文存在且无脚手架标记残留
            novel_text = orchestrator.current_novel
            self.assertIn("夜色如墨", novel_text)
            for marker in ("【", "】", "※", "章末钩子"):
                self.assertNotIn(marker, novel_text)
            orchestrator.close()
        finally:
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass


class _EmptyContentClient:
    def __init__(self, reasoning="这是正文内容"):
        self._r = reasoning
    def chat_completion(self, messages, temperature=None, max_tokens=None,
                        retry_on_error=True, max_retries=3, extra_body=None):
        return {"role": "assistant", "content": "", "reasoning_content": self._r,
                "finish_reason": "stop"}

def test_provider_uses_reasoning_fallback():
    from novel_engine.core.llm_provider import LLMProvider
    p = LLMProvider(_EmptyContentClient("兜底正文"))
    out = p.complete([{"role": "user", "content": "写一章"}], output_json=False)
    assert out == "兜底正文"

class _BothEmptyClient:
    def chat_completion(self, messages, temperature=None, max_tokens=None,
                        retry_on_error=True, max_retries=3, extra_body=None):
        return {"role": "assistant", "content": "", "reasoning_content": "",
                "finish_reason": "stop"}

def test_provider_both_empty_raises():
    from novel_engine.core.llm_provider import LLMProvider, ProviderConfig
    import pytest
    cfg = ProviderConfig(retry_temperatures=[0.85, 0.7])
    with pytest.raises(RuntimeError):
        LLMProvider(_BothEmptyClient(), cfg).complete(
            [{"role": "user", "content": "写一章"}], output_json=False)


def test_call_llm_via_provider():
    from novel_engine.core.llm_client import call_llm, MockLLMClient
    out = call_llm("生成任务卡", client=MockLLMClient())
    assert isinstance(out, str) and len(out) > 0


def test_provider_config_loads():
    import json
    from pathlib import Path
    cfg = json.loads(Path("novel_engine/config/runtime_config.json").read_text(encoding="utf-8"))
    providers = json.loads(Path("novel_engine/config/llm_providers.json").read_text(encoding="utf-8"))
    active = providers["active_profile"]
    active_profile = providers["profiles"][active]
    # runtime provider must match the active llm_providers profile (model-agnostic)
    assert cfg["provider"]["family"] == active_profile["family"]
    assert cfg["llm"]["api_base"] == active_profile["base_url"]
    # fallback_llm remains agnes (AGNES_API_KEY not configured, do not switch back)
    assert "agnes" in cfg["fallback_llm"]["api_base"]
    assert cfg["quality"]["publication_line"] == 88
    assert cfg["quality"]["min_chapter_score"] == 60


def test_chapter_auto_retry_then_pass():
    class Flaky:
        def __init__(self): self.n = 0
        def chat_completion(self, messages, temperature=None, max_tokens=None,
                            retry_on_error=True, max_retries=3, extra_body=None):
            self.n += 1
            if self.n < 3:
                return {"role": "assistant", "content": "", "reasoning_content": "",
                        "finish_reason": "stop"}
            return {"role": "assistant", "content": "正常正文", "finish_reason": "stop"}
    from novel_engine.core.llm_provider import LLMProvider
    assert LLMProvider(Flaky()).complete(
        [{"role": "user", "content": "x"}], output_json=False) == "正常正文"


def test_slo_fail_rate_threshold():
    from novel_engine.tests.slo_gate import evaluate_slo
    report = {"passed": 198, "failed": 3, "results": [{"score": 86}] * 201,
              "quality": {"dimension_averages": {}}}
    res = evaluate_slo(report, max_fail_rate=0.01)
    assert res["meets_stability"] is False


def test_grade_review_uses_publication_line():
    from novel_engine.agents.reviewer_agent import ReviewerAgent
    import json
    from pathlib import Path
    cfg = json.loads(Path("novel_engine/config/runtime_config.json").read_text(encoding="utf-8"))
    line = cfg["quality"]["publication_line"]
    fix_t = cfg["quality"]["fix_threshold"]
    a = ReviewerAgent.__new__(ReviewerAgent)
    assert a.grade_review({"total_score": line}) == "pass"
    assert a.grade_review({"total_score": line - 1}) == "fix"
    assert a.grade_review({"total_score": fix_t - 1}) == "fail"


def test_enforce_word_count_pads_short():
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator

    class _LongClient:
        def chat_completion(self, messages, temperature=None, max_tokens=None,
                            retry_on_error=True, max_retries=3, extra_body=None):
            return {"role": "assistant", "content": "补充" * 100, "finish_reason": "stop"}

    class _Fake:
        llm = _LongClient()

    out = PipelineOrchestrator._enforce_word_count(_Fake(), "短", 100, 200)
    assert len(out) >= 100


def test_agents_prompt_keywords():
    from novel_engine.agents.pacing_advisor import PacingAdvisor

    assert "节奏" in PacingAdvisor().pre_write_constraints("某章大纲")


def test_flag_for_human_appends():
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
    PipelineOrchestrator._flag_for_human(object(), 7, 70, "below line")
    import json
    data = json.load(open("novel_engine/audit/needs_human_review.json", encoding="utf-8"))
    assert any(r["chapter"] == 7 for r in data["queue"])




def test_generate_task_card_cached_feedback_passthrough(monkeypatch):
    """CC round-33：feedback 透传至 Call1 skeleton prompt；None 时不注入。"""
    import inspect, tempfile, shutil, os
    from pathlib import Path
    from novel_engine.agents.chapter_director import ChapterDirector

    sig = inspect.signature(ChapterDirector.generate_task_card_cached)
    assert 'feedback' in sig.parameters
    sig2 = inspect.signature(ChapterDirector._call_scene_skeleton)
    assert 'extra_feedback' in sig2.parameters

    captured = []
    def _fake_skeleton(self, chapter_num, context, prior_events_block, extra_feedback=None):
        captured.append(extra_feedback)
        return [{'scene_num': i, 'location': 'L'+str(i), 'characters': ['X'], 'goal': 'g'+str(i),
                 'conflict': 'c'+str(i), 'emotion': 'e'+str(i), 'beats': ['b1','b2','b3']} for i in range(1,5)]
    def _fake_craft(self, *a, **k):
        return [{'scene_num': i, 'location': 'L'+str(i), 'characters': ['X'], 'goal': 'g'+str(i),
                 'conflict': 'c'+str(i), 'emotion': 'e'+str(i), 'beats': ['b1','b2','b3'],
                 'concrete_events': [], 'named_interactions': [], 'info_reveal_points': [],
                 'protagonist_interiority': '', 'scene_constraints': [],
                 'scene_progression_contract': {}, 'scene_craft_elements': {}} for i in range(1,5)]
    def _fake_metadata(self, *a, **k):
        return {'chapter_num': 1, 'protagonist_agency_level': 'normal', 'core_goal': 'g',
                'conflicts': {'internal': 'i', 'external': 'e'},
                'emotion_curve': {'start': 's', 'middle': 'm', 'climax': 'c', 'end': 'e'},
                'chapter_hook': 'h', 'foreshadow_actions': [], 'chapter_events': [],
                'state_changes': [], 'foreshadow_execution': [],
                'end_state': {'narrative_position': 'p', 'location': 'l', 'completed_actions': [], 'pending_actions': [], 'time_marker': '当日'},
                'timeline_anchor': {'chapter_start_marker': 'm', 'max_time_progression': 't', 'forbidden_markers': []}}

    monkeypatch.setattr(ChapterDirector, '_call_scene_skeleton', _fake_skeleton)
    monkeypatch.setattr(ChapterDirector, '_call_scene_craft', _fake_craft)
    monkeypatch.setattr(ChapterDirector, '_call_chapter_metadata', _fake_metadata)
    monkeypatch.setattr(ChapterDirector, '_try_template_task_card', lambda self, *a, **k: None)

    tmpdir = tempfile.mkdtemp()
    try:
        for d in ['config','config/planning','bible','runtime']:
            Path(tmpdir, d).mkdir(parents=True, exist_ok=True)
        (Path(tmpdir)/'config'/'runtime_config.json').write_text('{"llm":{"use_mock":true}}', encoding='utf-8')
        (Path(tmpdir)/'config'/'llm_providers.json').write_text('{"active_profile":"t","profiles":{"t":{"base_url":"http://x","api_key_env":"K","timeout_s":1,"max_retries":1,"phases":{"director":{"models":["m"],"response_format":"json_object"}}}}}', encoding='utf-8')
        os.environ['K'] = 'sk-x'
        d = ChapterDirector(tmpdir)
        ctx = d._build_shared_context(1)[0]

        captured.clear()
        d.generate_task_card_cached(1, ctx, feedback=['-禁止OOC','-场景必须4场'])
        assert len(captured) == 1 and captured[0] is not None
        assert '-禁止OOC' in captured[0]

        captured.clear()
        d.generate_task_card_cached(1, ctx, feedback=None)
        assert len(captured) == 1 and captured[0] is None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        os.environ.pop('K', None)

if __name__ == "__main__":
    unittest.main()

