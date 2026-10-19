"""生成 app/data/famous_lines.u16d.xz：古诗词、古文名篇、古典小说里每个 4–12 字句子的 CRC32 哈希（繁简两种写法）。
只保存哈希（排好序后相邻差值，uint16，xz 压缩），不保存原文。
数据来源（本地路径按需修改）：chinese-poetry（全唐诗、宋词、元曲、诗经、楚辞、花间集、蒙学、四书、论语）、
NiuTrans 古文原文（世说新语、唐传奇、聊斋志异、陶庵梦忆等，不含正史）、四大名著等明清小说全文。"""
import glob, json, lzma, os, re, sys, zlib
import numpy as np
from opencc import OpenCC
t2s, s2t = OpenCC("t2s"), OpenCC("s2t")
SPLIT = re.compile(r"[，。！？；：、,.!?;:\s「」『』“”‘’（）()《》〈〉—…·]+")
lines = set()
def add(text):
    for frag in SPLIT.split(text):
        f = re.sub(r"[^一-鿿]", "", frag)
        if 4 <= len(f) <= 12:
            s = t2s.convert(f); lines.update((f, s, s2t.convert(s)))
def walk(o, keys=None):
    if isinstance(o, str): add(o)
    elif isinstance(o, dict):
        for k, v in o.items():
            if keys is None or k in keys or isinstance(v, (list, dict)): walk(v, keys)
    elif isinstance(o, list):
        for x in o: walk(x, keys)
POETRY = os.environ.get("CPOETRY", "/tmp/claude-0/cpoetry")          # chinese-poetry 诗词部分
for f in glob.glob(f"{POETRY}/**/*.json", recursive=True):
    if re.search(r"author|intro|preface", f): continue
    try: walk(json.load(open(f, encoding="utf-8")), {"paragraphs", "content", "para"})
    except Exception as e: print("skip", f, e)
for f in glob.glob(os.environ.get("CPOETRY2", "/tmp/claude-0/cp2") + "/**/*.json", recursive=True):   # 蒙学、四书、论语
    walk(json.load(open(f, encoding="utf-8")))
SKIP = {"资治通鉴", "新唐书", "旧五代史", "金史", "太平广记", "困学纪闻"}
NIU = os.environ.get("NIU", "/tmp/claude-0/niu/古文原文")
for d in (os.listdir(NIU) if os.path.isdir(NIU) else []):
    if d in SKIP: continue
    for f in glob.glob(f"{NIU}/{d}/**/*", recursive=True):
        if os.path.isfile(f): add(open(f, encoding="utf-8", errors="ignore").read())
for f in sys.argv[1:]:                                                   # 古典小说全文 .txt
    add(open(f, encoding="utf-8", errors="ignore").read())
h = np.unique(np.array([zlib.crc32(l.encode()) for l in lines], dtype=np.uint32))
d = np.diff(h, prepend=np.uint32(0)); assert d.max() < 65536
open(os.path.join(os.path.dirname(__file__), "..", "app", "data", "famous_lines.u16d.xz"), "wb").write(
    lzma.compress(d.astype("<u2").tobytes(), preset=9 | lzma.PRESET_EXTREME))
print("lines", len(lines), "hashes", len(h))
