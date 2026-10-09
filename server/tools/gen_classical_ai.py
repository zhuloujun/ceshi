"""生成古典文学体裁的 AI 训练数据：诗（绝句、律诗、古风歌行）、词（常用词牌）、赋、骈文、文言文（记、序、说、传、
书信、铭、论）、对联 → tools/data/gen_cl/ai_<家>.jsonl

为什么需要：现有的 AI 诗词 / 文言样本只有一百多篇，且只来自 DeepSeek / Kimi / 文心；国产新模型写的诗词只认出约 39%。
各大检测平台的做法是用"新模型按普通用户的指令写的同类文本"持续训练专门的分类器，这里照此处理古典体裁：
五家国产模型（DeepSeek、千问、豆包、Kimi、文心）、传统题材 + 现代题材、普通指令 + "像古人写的"指令。

每条记录：{"title", "genre", "model", "ver", "prompt", "text"}，text 只保留作品本身（去掉题目行、解说、注释）。
用法（gen-chinese.yml，providers 里写 classical:deepseek,qwen,...）：python tools/gen_classical_ai.py --n 160
"""
import argparse
import json
import os
import random
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_english_ai as g  # noqa: E402

OUT = Path(__file__).resolve().parent / "data" / "gen_cl"

TRAD = ["春日", "秋思", "送别", "思乡", "登高", "月夜", "咏梅", "咏竹", "咏菊", "咏兰", "江上", "山居", "边塞", "怀古",
        "闺怨", "寒食", "清明", "中秋", "重阳", "除夕", "元宵", "七夕", "雪夜", "听雨", "夜泊", "渔父", "牧童", "田家",
        "西湖", "黄鹤楼", "赤壁", "长城", "泰山", "洞庭", "姑苏", "金陵", "长安", "庐山", "黄河", "春江",
        "落花", "柳", "杏花", "荷", "残荷", "芦花", "孤雁", "鹤", "古寺", "隐士", "老将", "游子", "读书", "饮酒", "梅雨"]
MODERN = ["高铁", "春运", "毕业季", "抗疫", "航天", "外卖骑手", "深夜加班", "手机", "支教", "乡村振兴", "回乡", "二十四节气",
          "城市夜景", "老同学聚会", "退休", "父亲节", "母亲节", "教师节", "国庆", "冬奥", "长江大桥", "打工人", "考研"]
CIPAI = ["浣溪沙", "蝶恋花", "临江仙", "水调歌头", "念奴娇", "满江红", "如梦令", "西江月", "鹧鸪天", "清平乐", "沁园春",
         "菩萨蛮", "江城子", "卜算子", "虞美人", "定风波", "青玉案", "声声慢", "采桑子", "长相思"]
WENTI = [("记", "用文言文写一篇《{t}记》，三百字左右。"), ("序", "用文言写一篇《{t}序》。"),
         ("说", "仿照《爱莲说》《马说》，用文言文写一篇《{t}说》。"), ("传", "用文言文为一位{t}写一篇人物传记，仿照史书列传的笔法。"),
         ("书", "用文言写一封书信，向友人诉说{t}的心情。"), ("铭", "仿照《陋室铭》，写一篇《{t}铭》。"),
         ("论", "用文言写一篇议论文，题目是《{t}论》。"), ("笔记", "用文言写一则笔记小说，讲一个关于{t}的奇闻。")]

# v2 新增（对照用户提供的《全唐文》《全上古三代秦汉三国六朝文》《历代笔记小说大观》《诸子百家》《楚辞》《乐府诗》
# 等真人古籍补充的体裁）：碑志、表奏、诏令、祭文、游记、题跋、家书、寓言说理、世说体、志怪传奇
PEOPLE = ["一位清廉的县令", "一位隐居山林的老儒", "一位戍边的老将", "一位孝顺的农家女", "一位乐善好施的商人", "一位苦读成名的书生",
          "一位医术高明的郎中", "一位刚直敢谏的御史", "一位守节的寡母", "一位游方的高僧", "一位善画的山人", "一位早逝的少年"]
AFFAIRS = ["兴修水利", "赈济灾民", "减免赋税", "整顿吏治", "选拔人才", "劝课农桑", "禁绝奢靡", "修缮学宫", "抵御边患",
           "平定盗贼", "请求致仕", "辞让封赏", "请立太子", "整顿盐法", "开科取士", "劝谏君王勿事游猎"]
SCENES = ["游西山", "登岳阳楼", "夜泊瓜洲", "游虎丘", "观钱塘潮", "宿山寺", "游石钟山", "过三峡", "游雁荡山", "访隐者不遇",
          "雪后游园", "泛舟太湖"]
WENTI2 = [("墓志", PEOPLE, ["用文言为{t}写一篇墓志铭，有志有铭。", "仿照韩愈的笔法，为{t}写一篇墓志铭。"]),
          ("表奏", AFFAIRS, ["用文言写一篇上皇帝的奏疏，奏请{t}。", "以臣子的口吻，写一篇关于{t}的表文。"]),
          ("诏令", AFFAIRS, ["以皇帝的口吻，用文言写一道关于{t}的诏书。"]),
          ("祭文", PEOPLE, ["用文言为{t}写一篇祭文，仿照《祭十二郎文》。", "写一篇祭奠{t}的文言祭文，四言韵文。"]),
          ("游记", SCENES, ["用文言写一篇游记，记{t}。", "仿照柳宗元《永州八记》，写一篇《{t}记》。"]),
          ("题跋", ["一幅山水画", "一卷古帖", "友人诗集", "一部旧书", "一方古砚", "一幅墨竹"], ["用文言为{t}写一篇题跋。"]),
          ("家书", ["读书", "做人", "治家", "为官", "交友", "节俭"], ["仿照古人家书，用文言写一封告诫子弟{t}之道的家书。",
                                                          "仿照《诫子书》，以父亲的口吻用文言写一封关于{t}的家书。"]),
          ("寓言", ["守株待兔式的愚人", "学步的人", "养猴的人", "种树的老人", "射箭的人", "卖药的人", "渡河的人"],
           ["仿照先秦诸子，用文言写一则关于{t}的寓言，并在末尾点明道理。"]),
          ("世说", ["名士饮酒", "清谈", "雅量", "任诞", "言语机敏的孩童", "简傲"], ["仿照《世说新语》，用文言写几则关于{t}的小故事。"]),
          ("志怪", ["狐仙", "书生遇鬼", "古镜", "龙女", "画中人", "老树成精", "还魂"],
           ["仿照《聊斋志异》，用文言写一篇关于{t}的志怪故事，五百字左右。", "仿照《搜神记》，用文言写一则关于{t}的志怪短篇。"])]
QUPAI = ["天净沙", "山坡羊", "沉醉东风", "折桂令", "水仙子", "清江引", "卖花声", "寿阳曲", "四块玉", "红绣鞋"]

GENRES = [  # (genre, 题目池, 指令模板)
    ("jueju5", TRAD + MODERN, ["写一首五言绝句，题目是《{t}》。", "以{t}为题作一首五绝。", "帮我写一首关于{t}的五言绝句。"]),
    ("jueju7", TRAD + MODERN, ["写一首七言绝句，题目是《{t}》。", "以{t}为题作一首七绝，要押韵、讲平仄。", "帮我写一首关于{t}的七言绝句。"]),
    ("lvshi5", TRAD + MODERN, ["写一首五言律诗《{t}》，中间两联要对仗。", "以{t}为题写一首五律。"]),
    ("lvshi7", TRAD + MODERN, ["写一首七言律诗，题目《{t}》，严格遵守格律。", "以{t}为题作一首七律。", "帮我写一首关于{t}的七律。"]),
    ("gufeng", TRAD, ["写一首古风歌行，题目是《{t}行》。", "仿照李白的风格，写一首关于{t}的古体诗。"]),
    ("ci", TRAD + MODERN, ["用词牌《{p}》写一首词，主题是{t}。", "以{t}为题，填一首{p}。", "写一首《{p}·{t}》。"]),
    ("fu", TRAD[:40] + MODERN, ["写一篇《{t}赋》，仿照古人辞赋的格式。", "以{t}为题作一篇骈赋，四六对仗。", "帮我写一篇关于{t}的赋。"]),
    ("pianwen", TRAD[:40], ["用骈文写一段关于{t}的文字，对仗工整。"]),
    ("wenyan", TRAD + MODERN, None),
    ("sao", TRAD[:40], ["仿照《离骚》《九歌》的楚辞体，以{t}为题写一首骚体诗，句中用“兮”字。"]),
    ("yuefu", TRAD, ["仿照汉乐府民歌，写一首关于{t}的乐府诗。", "以{t}为题，拟一首汉魏乐府古辞。"]),
    ("sanqu", TRAD + MODERN, ["用曲牌【{q}】写一首元曲小令，题目是{t}。", "以{t}为题写一支散曲【{q}】。"]),
    ("duilian", TRAD + MODERN, ["以{t}为题写三副对联。", "写一副关于{t}的长联，上下联各二十字以上。",
                                "写一副七言对联，主题是{t}，只要上联和下联。", "以{t}为题写一副五言对联。",
                                "为{t}写一副楹联，上下联字数相等、平仄相对。", "写一副春联，内容与{t}有关，只写上下联。",
                                "帮我对一副对联，题目是{t}，上下联各九字到十一字。"]),
]
PLACES = ["黄鹤楼", "岳阳楼", "滕王阁", "杜甫草堂", "西湖", "寒山寺", "峨眉山", "武侯祠", "大观楼", "孔庙", "岳王庙",
          "天一阁", "拙政园", "泰山南天门", "兰亭", "白帝城", "趵突泉", "桂林山水", "蓬莱阁", "黄山", "苏堤", "书院", "茶馆", "药铺"]
STYLE_TAIL = ["", "", "只输出作品本身，不要解释。", "要求像古人写的，不要出现现代词汇。", "语言典雅一些。",
              "写得自然一点，不要堆砌辞藻，像真人写的。"]


_TAIL = re.compile(r"^(这(篇|首|副|三副|几副|组|长联|两副|些)|此(文|赋|联|篇|作)以|本(文|赋|联|篇|作)|希望|以上|注解|\*?注[：:*]|> |[-•] |\*|全联|——\s*(全联|共)|共[一二三四五六七八九十百\d]+字|[（(]注)")


def clean(text: str, genre: str) -> str:
    """只保留作品本身：去掉 Markdown、题目行、"注释 / 赏析 / 解析"等说明文字。"""
    t = text.replace("**", "").replace("#", "")
    t = re.split(r"\n\s*(注释|注[:：]|【注|赏析|解析|说明|创作说明|简析|译文|白话|翻译|格律|平仄|韵脚|这首|此诗|此词|本词|本诗|以上)", t)[0]
    lines = [l.strip() for l in t.split("\n")]
    out = []
    for l in lines:
        if out and _TAIL.match(l):
            break             # 作品后面的"注解 / 说明 / 逐联解析"（模型常用"- ""> ""• "列点或"这篇……"开头）
        if re.fullmatch(r"[—\-]*\s*完?\s*[—\-]*", l) or re.fullmatch(r"\[[^\]]{1,12}\]", l):
            continue          # "——完——"、"[您的名字]"之类

        if not l or re.fullmatch(r"[-=*_~·\s]{3,}", l):
            continue
        if not out and (re.fullmatch(r"[《〈]?[^，。！？、；]{1,24}[》〉]?", l) or re.match(r"^(题目|标题|词牌|作者)[:：]", l)):
            continue          # 开头的题目行 / 词牌行
        if re.match(r"^(好的|当然|以下是|这是|为您|下面)", l):
            continue
        l = re.split(r"(?<=[。！？])[^。！？]*(希望(这|它|能|您|你)|满足您)", l)[0]
        out.append(l)
    return "\n".join(out).strip()


def run(name, key, endpoints, n, t0, budget, lock):
    eps = g.working_endpoints(name, key, endpoints, limit=3)
    if not eps:
        return
    out_f = OUT / f"ai_{name}.jsonl"
    done = set()
    if out_f.exists():
        for l in out_f.read_text("utf-8").split("\n"):
            if l.strip():
                r = json.loads(l)
                done.add(f"{r['title']}|{r['genre']}|{r['ver']}")
    rnd = random.Random(f"cl-{name}")
    jobs = []
    for genre, topics, tpls in GENRES:
        for t in topics:
            if genre == "wenyan":
                for wt, tpl in WENTI:
                    jobs.append((genre + "-" + wt, t, tpl))
            else:
                for tpl in tpls:
                    jobs.append((genre, t, tpl))
    for wt, topics2, tpls2 in WENTI2:
        for t in topics2:
            for tpl in tpls2:
                jobs.append(("wenyan2-" + wt, t, tpl))
    only_g = [x for x in os.getenv("CL_GENRES", "").split(",") if x]
    if only_g:          # 只生成指定体裁（如 CL_GENRES=duilian）：对联另加名胜楹联题目
        jobs = [j for j in jobs if j[0].split("-")[0] in only_g]
        if "duilian" in only_g:
            jobs += [("duilian", t, tpl) for t in PLACES for tpl in GENRES[-1][2]]
    rnd.shuffle(jobs)
    n_new = 0
    for i, (genre, t, tpl) in enumerate(jobs[: n * 3]):
        if n_new >= n or (time.time() - t0) / 60 > budget:
            break
        url, model = eps[i % len(eps)]
        if f"{t}|{genre}|{model}" in done:
            continue
        prompt = tpl.format(t=t, p=rnd.choice(CIPAI), q=rnd.choice(QUPAI)) + rnd.choice(STYLE_TAIL)
        text = g.chat(url, key, model, prompt, rnd.choice([0.7, 0.85, 1.0]), 1500)
        text = clean(text or "", genre)
        if len(re.findall(r"[一-鿿]", text)) >= (16 if genre.startswith("jueju") else 30):
            with lock, out_f.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"title": t, "genre": genre, "model": name, "ver": model, "prompt": prompt,
                                     "text": text}, ensure_ascii=False) + "\n")
            n_new += 1
    print(f"::notice title={name}（古典体裁）::新生成 {n_new} 篇（模型：{', '.join(m for _, m in eps)}）", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=160)
    ap.add_argument("--time-budget-min", type=float, default=260)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    op = os.getenv("ONLY_PROVIDERS", "")
    if op.startswith("couplet:"):          # couplet:deepseek,qwen,… = 只生成对联
        os.environ["CL_GENRES"] = "duilian"
    if op.startswith("classical+"):        # classical+wenyan2.sao.yuefu.sanqu:deepseek,qwen = 只生成指定体裁
        os.environ["CL_GENRES"] = op.split(":", 1)[0].split("+", 1)[1].replace(".", ",")
        op = "classical:" + op.split(":", 1)[1]
    only = [x for x in op.replace("classical:", "", 1).replace("couplet:", "").split(",") if x]
    t0, lock, ths = time.time(), threading.Lock(), []
    for name, (env, eps) in g.PROVIDERS.items():
        if only and name not in only:
            continue
        key = os.getenv(env, "").strip()
        if not key:
            print(f"::notice title=未设置 {env}::跳过 {name}", flush=True)
            continue
        th = threading.Thread(target=run, args=(name, key, eps, a.n, t0, a.time_budget_min, lock), daemon=True)
        th.start()
        ths.append(th)
    for th in ths:
        th.join()


if __name__ == "__main__":
    main()
