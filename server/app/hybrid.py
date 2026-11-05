"""人机混写切分：同一段里人写和 AI 写的句子交替出现时，先找出“作者换了”的位置，再按切开后的每一块分别判断。

为什么要这样做（2026-11-05 用户 cs1105）：真实的文章不会给句子标“A / Z”，也不会把 AI 写的部分单独拿出来；
按固定长度（中文约 1000 字 / 英文约 1000 词）切段时，一段里人写、AI 写各占一半，平均下来大多“未达阈值”。
篇幅下限（英文 150 词等）应当看整篇交来的文字，而不是看其中 AI 写的那几句——AI 写的部分有多长没有规定。

做法（参考 SenDetEX / HACo-Det / M4GT-Bench 的句级边界检测思路，以及经典的两状态隐马尔可夫切分）：
1) 把段落切成“句子单元”（太短的句子并入下一句：中文 ≥ 20 字、英文 ≥ 12 词）；
2) 每个单元单独、以及与下一单元连起来，各用“见过新一代大模型”的分类器打一次分（分类器很快）；
3) 两状态（人写 / AI）维特比解码：每换一次作者要付出一个固定代价，零星几句高分不会被当成一块 AI；
4) 太短的块并回相邻块；只有“AI 块明显高、人写块明显低”时才真的切开，否则保持原段不动。
切开后的每一块作为单独的段落走完整的检测流程（语言模型 + 分类器 + 校准阈值），篇幅下限仍按整篇计算。
"""
import math
import re

_ZH_SENT = re.compile(r"(?<=[。！？!?；;…])(?![”’」』\"'）)])|(?<=[。！？!?；;…][”’」』\"'）)])")
_EN_SENT = re.compile(r"(?<=[.!?])(?=\s|[A-Z\"“‘(])|(?<=[.!?][\"”’)])(?=\s|[A-Z])")
_CJK = re.compile(r"[一-鿿]")


def size_of(text: str, register: str) -> int:
    return len(text.split()) if register == "en" else len(_CJK.findall(text))


def sentence_units(text: str, register: str, min_zh: int = 20, min_en: int = 12) -> list[tuple[int, int]]:
    """切成首尾相接、覆盖全文的句子单元 [(起, 止)]。"""
    pat = _EN_SENT if register == "en" else _ZH_SENT
    cuts = sorted({m.start() for m in pat.finditer(text) if 0 < m.start() < len(text)})
    bounds = [0] + cuts + [len(text)]
    spans = [(a, b) for a, b in zip(bounds, bounds[1:]) if text[a:b].strip()]
    need = min_en if register == "en" else min_zh
    out, a = [], None
    for x, y in spans:
        if a is None:
            a = x
        if size_of(text[a:y], register) >= need:
            out.append((a, y))
            a = None
    if a is not None:
        if out:
            out[-1] = (out[-1][0], len(text))
        else:
            out.append((a, len(text)))
    # 首尾相接（空白归入前一个单元）
    fixed = []
    for k, (x, y) in enumerate(out):
        x = 0 if k == 0 else fixed[-1][1]
        y = len(text) if k == len(out) - 1 else y
        fixed.append((x, y))
    return fixed


def _logit(p: float) -> float:
    p = min(max(p, 0.02), 0.98)
    return math.log(p / (1 - p))


def unit_evidence(single: list, pairs: list) -> list:
    """每个单元的证据（对数几率）：单元自己的分数与包含它的两个“相邻两单元”分数的平均。"""
    n = len(single)
    ev = []
    for i in range(n):
        xs = []
        if single[i] is not None:
            xs.append(_logit(single[i]))
        for j in (i - 1, i):
            if 0 <= j < len(pairs) and pairs[j] is not None:
                xs.append(_logit(pairs[j]))
        ev.append(sum(xs) / len(xs) if xs else 0.0)
    return ev


def viterbi(ev: list, switch_cost: float = 2.0, temp: float = 1.5) -> list:
    """两状态维特比：状态 1 = AI。发射对数似然取 log σ(e/T) 与 log(1−σ(e/T))。"""
    if not ev:
        return []
    def ll(e, s):
        z = e / temp
        return -math.log1p(math.exp(-z)) if s else -math.log1p(math.exp(z))
    score = [ll(ev[0], 0), ll(ev[0], 1)]
    back = []
    for e in ev[1:]:
        new, bk = [], []
        for s in (0, 1):
            stay, move = score[s], score[1 - s] - switch_cost
            bk.append(s if stay >= move else 1 - s)
            new.append(max(stay, move) + ll(e, s))
        score, back = new, back + [bk]
    s = 0 if score[0] >= score[1] else 1
    path = [s]
    for bk in reversed(back):
        s = bk[s]
        path.append(s)
    return path[::-1]


def runs_of(path: list) -> list[list]:
    """[[状态, 起单元, 止单元(不含)]]"""
    out = []
    for i, s in enumerate(path):
        if out and out[-1][0] == s:
            out[-1][2] = i + 1
        else:
            out.append([s, i, i + 1])
    return out


def merge_short_runs(path: list, sizes: list, min_size: int) -> list:
    """太短的块（字数 / 词数不足 min_size）并入相邻块，从最短的开始，直到都够长或只剩一块。"""
    path = list(path)
    while True:
        rs = runs_of(path)
        if len(rs) <= 1:
            return path
        lens = [sum(sizes[a:b]) for _, a, b in rs]
        k = min(range(len(rs)), key=lambda i: lens[i])
        if lens[k] >= min_size:
            return path
        s, a, b = rs[k]
        for i in range(a, b):
            path[i] = 1 - s


def plan_split(text: str, register: str, single: list, pairs: list, spans: list, cfg: dict) -> list | None:
    """返回切开后的块 [(起, 止, 状态, 平均分)]；不该切时返回 None。"""
    if len(spans) < 3:
        return None
    ev = unit_evidence(single, pairs)
    path = viterbi(ev, cfg["switch_cost"], cfg["temp"])
    sizes = [size_of(text[a:b], register) for a, b in spans]
    path = merge_short_runs(path, sizes, cfg["min_en"] if register == "en" else cfg["min_zh"])
    rs = runs_of(path)
    if len(rs) < 2 or not any(s for s, _, _ in rs):
        return None
    out = []
    for s, a, b in rs:
        ps = [p for p in single[a:b] if p is not None]
        mean = sum(ps) / len(ps) if ps else None
        if mean is None:
            return None
        if s and mean < cfg["ai_mean"]:
            return None
        if not s and mean > cfg["human_mean"]:
            return None
        out.append((spans[a][0], spans[b - 1][1], s, round(mean, 4)))
    return out
