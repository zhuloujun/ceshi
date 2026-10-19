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
