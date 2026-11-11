"""作品结构单元：一首词、一首诗是一个整体，判断“谁写的”时不能从中间切开（2026-11-11 用户 cs1111：
AI 写的《蝶恋花》上下两阕被切成两段、分别判断）。

做法：
- 词：按《全宋词》21035 首统计出每个词牌最常见的分句字数（data/cipu.json，344 个词牌、483 种句式），
  连续若干分句的字数与某个词牌完全吻合，就是一首完整的词（上下阕一起）；
- 近体诗：连续 ≥ 4 个分句都是 5 字或都是 7 字，按 8 句（律诗）或 4 句（绝句）为一首；
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


def work_spans(text: str) -> list[tuple[int, int, str]]:
    cl = clauses(text)
    lens = [c[2] for c in cl]
    spans, i = [], 0
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
            i += k
        else:
            i += 1
    return spans
