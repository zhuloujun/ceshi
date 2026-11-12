"""作品结构单元：一首词、一首诗是一个整体，判断“谁写的”时不能从中间切开（2026-11-11 用户 cs1111：
AI 写的《蝶恋花》上下两阕被切成两段、分别判断）。

做法：
- 词：按《全宋词》21035 首统计出每个词牌最常见的分句字数（data/cipu.json，344 个词牌、483 种句式），
  连续若干分句的字数与某个词牌完全吻合，就是一首完整的词（上下阕一起）；
- 近体诗：连续 ≥ 4 个分句都是 5 字或都是 7 字，按 8 句（律诗）或 4 句（绝句）为一首；
- 韵文（2026-11-12 cs1111）：不合词牌的自度曲、歌谣（“想家乡，念爹娘，旧屋门前老树黄……”），
  连续 ≥ 6 个短分句（3–8 字）、从句首起到句末止，半数以上分句押同一韵（按普通话韵母归并），算一首；
返回原文中的 [(起, 止, 种类)]，供逐句判断时把同一首作品里的句子绑在一起。
"""
import json
import re
from pathlib import Path

_CJK = re.compile(r"[一-鿿]")
_CLAUSE = re.compile(r"[^，。、？！；：,.?!;:\n]+[，。、？！；：,.?!;:]*")
_PU = None


def _cipu():
    global _PU
    if _PU is None:
        try:
            raw = json.loads((Path(__file__).parent / "data" / "cipu.json").read_text("utf-8"))
        except Exception:  # noqa: BLE001
            raw = {}
        pats = []
        for name, ps in raw.items():
            for p in ps:
                if len(p) >= 6 and sum(p) >= 30:
                    pats.append((tuple(p), name))
        pats.sort(key=lambda x: -len(x[0]))
        _PU = pats
    return _PU


def clauses(text: str):
    """[(起, 止, 汉字数)]；只数汉字（编号、字母、标点不算）。"""
    out = []
    for m in _CLAUSE.finditer(text):
        n = len(_CJK.findall(m.group(0)))
        if n:
            out.append((m.start(), m.end(), n))
    return out


_SENT_END = "。！？!?"
RHYMED = True
_MODERN = re.compile(r"[的了着吗呢吧们]")


def _rhyme(ch: str):
    try:
        from pypinyin import pinyin, Style
    except Exception:  # noqa: BLE001
        return None
    f = pinyin(ch, style=Style.FINALS, strict=False)[0][0]
    if not f:
        return None
    if len(f) > 1 and f[0] in "iuv":
        f = f[1:]
    return {"ng": "eng", "ong": "eng", "n": "en", "e": "e", "ou": "u", "iu": "u", "ui": "i", "ei": "i"}.get(f, f)


def _rhymed_runs(text: str, cl: list, lens: list, taken: set) -> list:
    """韵文：连续短分句（3–8 字，不跨换行、不含已认出的词 / 诗），按整句（。！？）取舍，
    首尾不押韵的句子去掉；剩下 ≥ 3 句、≥ 6 个分句、≥ 30 字，且句末 ≥ 75%、分句末 ≥ 50% 押同一韵。"""
    from collections import Counter
    out, i = [], 0
    end = lambda k: text[cl[k][0]:cl[k][1]].rstrip().rstrip("”」』\"'").endswith(tuple(_SENT_END))
    while i < len(cl):
        if not (3 <= lens[i] <= 8) or i in taken:
            i += 1
            continue
        j = i
        while j < len(cl) and 3 <= lens[j] <= 8 and j not in taken and \
                (j == i or "\n" not in text[cl[j - 1][1]:cl[j][0]]):
            j += 1
        # 句子：起点须是句首
        sents, st = [], i
        if i > 0 and not end(i - 1) and "\n" not in text[cl[i - 1][1]:cl[i][0]]:
            st = None
        for k in range(i, j):
            if st is None:
                if end(k):
                    st = k + 1
                continue
            if end(k):
                sents.append((st, k + 1))
                st = k + 1
        i = j
        if len(sents) < 3:
            continue
        key = lambda k: (lambda cj: _rhyme(cj[-1]) if cj else None)(_CJK.findall(text[cl[k][0]:cl[k][1]]))
        top = Counter(key(e - 1) for _, e in sents).most_common(1)[0][0]
        if top is None:
            continue
        while sents and key(sents[0][1] - 1) != top:
            sents.pop(0)
        while sents and key(sents[-1][1] - 1) != top:
            sents.pop()
        if len(sents) < 3:
            continue
        a, b = sents[0][0], sents[-1][1]
        n = b - a
        if n < 6 or sum(lens[a:b]) < 30:
            continue
        if sum(key(e - 1) == top for _, e in sents) < 0.75 * len(sents):
            continue
        if sum(key(k) == top for k in range(a, b)) < 0.5 * n:
            continue
        last = [(_CJK.findall(text[cl[k][0]:cl[k][1]]) or [""])[-1] for k in range(a, b)]
        if len({c for k, c in zip(range(a, b), last) if key(k) == top}) < 3:
            continue      # 押韵字都是同一两个字（“一员，正三品。侍郎一员，正四品”），是排比不是韵文
        if sum(c in "也者矣焉乎哉耳" for c in last) > 1:
            continue      # 句末多用语气词，是文言散文
        seg = text[cl[a][0]:cl[b - 1][1]]
        if len(_MODERN.findall(seg)) > sum(lens[a:b]) // 30:
            continue
        out.append((cl[a][0], cl[b - 1][1], "韵文"))
    return out


def work_spans(text: str) -> list[tuple[int, int, str]]:
    cl = clauses(text)
    lens = [c[2] for c in cl]
    spans, i, taken = [], 0, set()
    pats = _cipu()
    while i < len(cl):
        hit = None
        for p, name in pats:
            k = len(p)
            if tuple(lens[i:i + k]) == p:
                hit = (k, f"词·{name}")
                break
        if hit is None and lens[i] in (5, 7):
            j = i
            while j < len(cl) and lens[j] == lens[i]:
                j += 1
            run = j - i
            if run >= 4:
                size = 8 if run % 8 == 0 or (run >= 8 and run % 4 != 0) else 4
                hit = (min(size, run) if run >= size else run, "诗")
        if hit:
            k, kind = hit
            spans.append((cl[i][0], cl[i + k - 1][1], kind))
            taken.update(range(i, i + k))
            i += k
        else:
            i += 1
    if RHYMED:
        spans = sorted(spans + _rhymed_runs(text, cl, lens, taken))
    return spans
