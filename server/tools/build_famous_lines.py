import json, glob, re, zlib, numpy as np
from opencc import OpenCC
cc = OpenCC("t2s")
SPLIT = re.compile(r"[，。！？；：、,.!?;:\s「」『』“”（）()《》]+")
lines = set()
def add(text):
    for frag in SPLIT.split(text):
        f = re.sub(r"[^一-鿿]", "", frag)
        if 4 <= len(f) <= 12:
            lines.add(f); lines.add(cc.convert(f))
def walk(o):
    if isinstance(o, dict):
        for k in ("paragraphs", "content", "para"):
            v = o.get(k)
            if isinstance(v, list): add("。".join(x for x in v if isinstance(x, str)))
            elif isinstance(v, str): add(v)
        for v in o.values():
            if isinstance(v, (list, dict)): walk(v)
    elif isinstance(o, list):
        for x in o: walk(x)
for f in glob.glob("/tmp/claude-0/cpoetry/**/*.json", recursive=True):
    if re.search(r"author|intro|preface", f): continue
    try: walk(json.load(open(f, encoding="utf-8")))
    except Exception as e: print("skip", f, e)
print("lines", len(lines))
h = np.unique(np.array([zlib.crc32(l.encode()) for l in lines], dtype=np.uint32))
print("hashes", len(h))
np.save("/tmp/claude-0/r8/fam/famous_lines.npy", h)
