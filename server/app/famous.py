"""古诗词名篇逐句比对。

语言模型"背过"的名篇困惑度极低，已有 FAMOUS_PPL_* 规则排除；但用户引用时常改动一两个字（"归思欲沾巾"写成
"归思欲沾襟"）、只引半首，困惑度就不够低，诗词分类器又会把工整的名句当成 AI（2026-10 用户测试：杜审言
《和晋陵陆丞早春游望》后四句判为 93%）。这里把《全唐诗》《全宋词》《元曲》《诗经》《楚辞》《花间集》等
（chinese-poetry 公开数据，繁简两种写法）每个 4–12 字的诗句做 CRC32 哈希（约 128 万句）。
2026-10-19 起又加入古文名篇与古典小说：《古文观止》、四书、《论语》、蒙学读物、《世说新语》、唐传奇、《聊斋志异》、
《陶庵梦忆》等笔记，以及《红楼梦》《三国演义》《西游记》《水浒传》《金瓶梅》《镜花缘》"三言二拍"《儒林外史》等
（合计约 351 万个哈希；只存哈希，不存原文）。用户测试 cs1019.docx：司马光《谏院题名记》、《西游记》里的赋赞
（"平生正直，秉性贤明……""芙蓉面，冰雪肌……"）被文言 / 诗词分类器判为 AI，加入后都按引用处理；
1717 段 AI 文言诗词里按此标准算作引用的仍是 3 段，没有增加。
文件 app/data/famous_lines.u16d.xz：排好序的哈希相邻差值（uint16），生成脚本 tools/build_famous_lines.py。一段诗词里至少 2 句、且一半以上的句子与古人原句完全相同，就视为引用古人
作品，不计入 AI 率。验证：230 首 AI 诗词（ChatGPT / DeepSeek / Kimi / 文心 / 豆包）没有一首达到这个标准，
只有 3 首有个别句子与古句相同。"""
from __future__ import annotations

import lzma
import re
import threading
import zlib
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "data" / "famous_lines.u16d.xz"
_SPLIT = re.compile(r"[，。！？；：、,.!?;:\s「」『』“”‘’（）()《》〈〉—…·]+")
_lock = threading.Lock()
_H = None


def _load():
    global _H
    with _lock:
        if _H is None:
            import numpy as np
            try:
                d = np.frombuffer(lzma.decompress(_PATH.read_bytes()), dtype="<u2")
                _H = np.cumsum(d.astype(np.uint64)).astype(np.uint32)
            except Exception:  # noqa: BLE001  数据文件缺失时不影响检测
                _H = np.zeros(0, dtype=np.uint32)
    return _H


def matched_lines(text: str) -> tuple[int, int]:
    """返回（与古人原句完全相同的句数, 4–12 字的句子总数）。"""
    import numpy as np
    frags = [re.sub(r"[^一-鿿]", "", f) for f in _SPLIT.split(text)]
    frags = [f for f in frags if 4 <= len(f) <= 12]
    H = _load()
    if not frags or not len(H):
        return 0, len(frags)
    hs = np.array([zlib.crc32(f.encode("utf-8")) for f in frags], dtype=np.uint32)
    i = np.searchsorted(H, hs)
    i[i >= len(H)] = 0
    return int((H[i] == hs).sum()), len(frags)


def is_famous_verse(text: str) -> bool:
    m, n = matched_lines(text)
    return m >= 2 and m >= 0.5 * n


_CLAUSE = re.compile(r"[^，。！？；：、,.!?;:\s「」『』“”‘’（）()《》〈〉—…·]+")
_SENT_END = re.compile(r"[。！？!?；;]")


def famous_spans(line: str, min_hits: int = 2, max_gap: int = 1) -> list[tuple[int, int]]:
    """一段（可能很长、没有换行）文字里，与古人原句完全相同的句子连成一片的区间 [(起, 止)]，起止扩到整句。
    片内至少 min_hits 句命中、命中句占 4–12 字句子的一半以上；中间允许夹 max_gap 句没命中的（改了一两个字的）。"""
    import numpy as np
    H = _load()
    if not len(H):
        return []
    cl = [(m.start(), m.end(), re.sub(r"[^一-鿿]", "", m.group())) for m in _CLAUSE.finditer(line)]
    cl = [c for c in cl if c[2]]
    if not cl:
        return []
    hs = np.array([zlib.crc32(f.encode("utf-8")) for _, _, f in cl], dtype=np.uint32)
    i = np.searchsorted(H, hs)
    i[i >= len(H)] = 0
    hit = [bool(H[k] == h) and 4 <= len(c[2]) <= 12 for k, h, c in zip(i, hs, cl)]
    spans, k = [], 0
    while k < len(cl):
        if not hit[k]:
            k += 1
            continue
        a = b = k
        gap = 0
        j = k + 1
        while j < len(cl):
            if hit[j]:
                b, gap = j, 0
            else:
                gap += 1
                if gap > max_gap:
                    break
            j += 1
        seg = range(a, b + 1)
        hits = sum(hit[x] for x in seg)
        elig = sum(4 <= len(cl[x][2]) <= 12 for x in seg)
        if hits >= min_hits and hits >= 0.5 * max(elig, 1):
            # 起点退到本句开头（上一个句末标点之后），终点扩到本句结尾
            st = cl[a][0]
            m = None
            for m in _SENT_END.finditer(line, 0, st):
                pass
            st = m.end() if m else 0
            en = cl[b][1]
            m2 = _SENT_END.search(line, en)
            en = m2.end() if m2 else len(line)
            if spans and st <= spans[-1][1]:
                spans[-1] = (spans[-1][0], max(en, spans[-1][1]))
            else:
                spans.append((st, en))
        k = b + 1
    return spans


# —— 引文核对（2026-11-13）——
# 用户 cs1112：AI 仿写的文言（“夫学之难，不难于读……”“书中所记，非王侯将相之事……”）夹在真古籍引文（《金刚经》、《汉书》、
# 孔庙碑文）中间，整段被当成“正文中引用的古文”不计入。知网、Turnitin 的做法是把引文与来源库比对：能逐句找到出处的是引文，
# 找不到出处的另行检测。这里另建一个更大的古籍句库（app/data/classics_ext.u16d.xz，约 2083 万个句子哈希，只存哈希）：
# 二十四史、《资治通鉴》《续资治通鉴》、十三经与四书、宋明理学语录（《传习录》《朱子语类》《近思录》等）、诸子、兵家、法家、
# 《全唐文》《全上古三代秦汉三国六朝文》《文选》《唐宋八大家文钞》等总集、唐宋明清名家别集与笔记、常用佛经（《金刚经》
# 《心经》《法华经》《坛经》《楞严经》《华严经》等）与道经（《道德经》《南华真经》等）、NiuTrans 古文原文全集。
# 来源：殆知阁古籍文献（garychowcmu/daizhigev20）、NiuTrans/Classical-Modern。生成脚本 tools/build_classics_ext.py。
# 单句哈希偶有碰撞（一句约 0.5%），所以要求至少 2 句命中、中间最多隔 2 句没命中的，才算“找到出处”。
_EXT_PATH = Path(__file__).resolve().parent / "data" / "classics_ext.u16d.xz"
_HX = None


def _load_ext():
    global _HX
    with _lock:
        if _HX is None:
            import numpy as np
            try:
                d = np.frombuffer(lzma.decompress(_EXT_PATH.read_bytes()), dtype="<u2")
                _HX = np.cumsum(d.astype(np.uint64)).astype(np.uint32)
            except Exception:  # noqa: BLE001
                _HX = np.zeros(0, dtype=np.uint32)
    return _HX


def _hits(frags: list[str]) -> list[bool]:
    import numpy as np
    if not frags:
        return []
    hs = np.array([zlib.crc32(f.encode("utf-8")) for f in frags], dtype=np.uint32)
    out = np.zeros(len(frags), dtype=bool)
    for H in (_load(), _load_ext()):
        if len(H):
            i = np.searchsorted(H, hs)
            i[i >= len(H)] = 0
            out |= H[i] == hs
    return [bool(h) and 4 <= len(f) <= 12 for h, f in zip(out, frags)]


def source_parts(text: str, min_score: float = 2.0, max_gap: int = 2, strict: bool = False) -> list[tuple[int, int, bool]]:
    """把一段文字按“能否在古籍句库里逐句找到出处”切成几块 [(起, 止, 有出处)]，起止都在句末标点之后。
    有出处：命中的小句（4–12 字）连成一片，中间最多隔 max_gap 个没命中的 4–12 字小句（不足 4 字的小句不计）；
    6 字以上的命中句记 1 分、4–5 字的记 0.5 分（“一言一动”“日出而作”这类成语 AI 也常用），合计 ≥ min_score，
    且命中句占片内 4–12 字小句的 60% 以上。
    没有任何一块有出处时返回 []。
    strict=True（正文里的文言 / 诗词，事先没有“这是引文”的迹象）：4 字的命中句（多是成语、套语）不计分（5 字的仍记 0.5 分，
    五言诗句；2026-11-14 cs1113 王维“漾楫溯清川……”四句五言原诗因此找不到出处），
    中间最多隔 1 个没命中的小句，命中句占 75% 以上，且整块（扩到句末）的汉字里命中的占 70% 以上
    （2026-11-13：AI 写的书信里“如闻其声，如见其人”“自以为工”几个成语命中，宽松规则把整句当成了古籍原文）。"""
    if strict:
        max_gap = min(max_gap, 1)
    cl = [(m.start(), m.end(), re.sub(r"[^一-鿿]", "", m.group())) for m in _CLAUSE.finditer(text)]
    cl = [c for c in cl if c[2]]
    hit = _hits([c[2] for c in cl])
    # 引用时断句常与底本不同（底本“教人为学不可执一偏”，引文“教人为学，不可执一偏”）：相邻 2–3 个小句连起来再查一次
    combo = [(k, n) for n in (2, 3) for k in range(len(cl) - n + 1)
             if 4 <= sum(len(cl[k + x][2]) for x in range(n)) <= 12]
    for (k, n), h in zip(combo, _hits(["".join(cl[k + x][2] for x in range(n)) for k, n in combo])):
        if h and (not strict or sum(len(cl[k + x][2]) for x in range(n)) >= 8):
            for x in range(n):
                hit[k + x] = True
    runs, k = [], 0
    while k < len(cl):
        if not hit[k]:
            k += 1
            continue
        w = lambda x: 1.0 if len(cl[x][2]) >= 6 else (0.5 if len(cl[x][2]) == 5 or not strict else 0.0)
        a = b = k
        gap, j, sc = 0, k + 1, w(k)
        while j < len(cl):
            if hit[j]:
                b, gap, sc = j, 0, sc + w(j)
            elif 4 <= len(cl[j][2]) <= 12:
                gap += 1
                if gap > max_gap:
                    break
            j += 1
        elig = sum(4 <= len(cl[x][2]) <= 12 for x in range(a, b + 1))
        nh = sum(hit[x] for x in range(a, b + 1))
        if sc >= min_score and nh >= (0.75 if strict else 0.6) * elig:
            m = None
            for m in _SENT_END.finditer(text, 0, cl[a][0]):
                pass
            st = m.end() if m else 0
            m2 = _SENT_END.search(text, cl[b][1])
            en = m2.end() if m2 else len(text)
            if strict:
                hc = sum(len(cl[x][2]) for x in range(a, b + 1) if hit[x])
                if hc < 0.7 * len(re.findall(r"[一-鿿]", text[st:en])):
                    k = b + 1
                    continue
            if runs and st <= runs[-1][1]:
                runs[-1] = (runs[-1][0], max(en, runs[-1][1]))
            else:
                runs.append((st, en))
        k = b + 1
    if not runs:
        return []
    parts, pos = [], 0
    for a, b in runs:
        if a > pos:
            parts.append((pos, a, False))
        parts.append((max(a, pos), b, True))
        pos = max(pos, b)
    if pos < len(text):
        parts.append((pos, len(text), False))
    return [p for p in parts if text[p[0]:p[1]].strip()]
