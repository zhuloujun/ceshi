"""训练数据的轻度扰动增强（2026-11-05）。

依据：2025 年综述《Enhancing the Robustness of AI-Generated Text Detectors: A Survey》（Mathematics 13(13):2145）把
“文本扰动”列为检测器最常见的失效原因，推荐用 EDA / AEDA 式的数据增强（随机插入标点、删词、交换相邻词、错字）训练；
2026 年《Trusting AI to detect AI?》（Computers & Education 249）发现“混合编辑”能让最多 88% 的 AI 文字逃过检测。
做法：给一部分 AI 训练样本做轻微的人工式改动（不改变“是 AI 写的”这一标签），让分类器不依赖个别字词、标点的表面特征。
真人样本也做同样比例的扰动，避免模型学到“有错字 = 人写”。
"""
from __future__ import annotations

import random
import re

_EN_PUNCT = [",", ";", ":", "—", "!", "?"]
_ZH_PUNCT = ["，", "、", "；", "：", "——"]


def perturb_en(text: str, rnd: random.Random, rate: float = 0.03) -> str:
    words = text.split(" ")
    out = []
    for w in words:
        r = rnd.random()
        if r < rate * 0.3 and len(words) > 20:            # 删词
            continue
        if r < rate * 0.6 and len(w) > 3 and w.isalpha():  # 错字：交换两个相邻字母
            i = rnd.randrange(len(w) - 1)
            w = w[:i] + w[i + 1] + w[i] + w[i + 2:]
        out.append(w)
        if rnd.random() < rate * 0.5:                    # AEDA：随机插入标点
            out[-1] = out[-1] + rnd.choice(_EN_PUNCT)
    if len(out) > 10 and rnd.random() < 0.5:             # 交换一对相邻词
        i = rnd.randrange(len(out) - 1)
        out[i], out[i + 1] = out[i + 1], out[i]
    return " ".join(out)


def perturb_zh(text: str, rnd: random.Random, rate: float = 0.02) -> str:
    out = []
    for ch in text:
        r = rnd.random()
        if r < rate * 0.3 and re.match(r"[一-鿿]", ch):   # 删字
            continue
        out.append(ch)
        if rnd.random() < rate * 0.3:
            out.append(rnd.choice(_ZH_PUNCT))
    s = "".join(out)
    s = re.sub(r"[的地得]", lambda m: rnd.choice("的地得") if rnd.random() < 0.1 else m.group(0), s)
    return s


def augment_rows(rows: list[dict], frac: float, lang: str, rnd: random.Random) -> list[dict]:
    """给 rows 里约 frac 比例的样本加一份扰动副本（标签不变），返回新增的副本。"""
    f = perturb_en if lang == "en" else perturb_zh
    extra = []
    for r in rows:
        if rnd.random() < frac:
            extra.append({**r, "text": f(r["text"], rnd), "src": r["src"] + "+扰动"})
    return extra
