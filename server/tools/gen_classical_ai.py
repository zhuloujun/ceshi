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
    ("duilian", TRAD + MODERN, ["以{t}为题写三副对联。", "写一副关于{t}的长联，上下联各二十字以上。"]),
]
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
    rnd.shuffle(jobs)
    n_new = 0
    for i, (genre, t, tpl) in enumerate(jobs[: n * 3]):
        if n_new >= n or (time.time() - t0) / 60 > budget:
            break
        url, model = eps[i % len(eps)]
        if f"{t}|{genre}|{model}" in done:
            continue
        prompt = tpl.format(t=t, p=rnd.choice(CIPAI)) + rnd.choice(STYLE_TAIL)
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
    only = [x for x in os.getenv("ONLY_PROVIDERS", "").replace("classical:", "").split(",") if x]
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
