"""独立真人文本上检验 DeepSeek 句式规则（engine._deepseek_style：短语表 + "is the thing that" 密度）的误报。
数据：BookCorpusOpen 当代小说（每本前 6 万词，4000 词窗口）、WritingPrompts 真人故事（8 篇拼成约 4000 词）。"""
import json, re, sys, collections, itertools, types
from pathlib import Path
app = Path(__file__).resolve().parents[2] / "app"
src = (app / "engine.py").read_text("utf-8")
ns = {"re": re, "config": types.SimpleNamespace(DS_LEX_MIN=4, DS_THING_MIN=6), "__file__": str(app / "engine.py")}
exec(src[src.index("_STYLE_LEX: dict = {}"):src.index("_QW_LEX = None")], ns)
from datasets import load_dataset
def windows(texts, n=4000):
    for t in texts:
        w = t.split()
        for i in range(0, min(len(w), 60000), n):
            if len(w[i:i + n]) >= 1500:
                yield " ".join(w[i:i + n])
def books(k):
    for r in itertools.islice(load_dataset("lucadiliello/bookcorpusopen", split="train", streaming=True), k):
        yield r.get("text") or ""
def wp(k):
    buf = []
    for r in itertools.islice(load_dataset("euclaise/writingprompts", split="train", streaming=True), k * 8):
        buf.append(r.get("story") or "")
        if len(buf) == 8:
            yield "\n\n".join(buf); buf = []
for name, gen in [("bookcorpusopen", windows(books(int(sys.argv[1])))), ("writingprompts", wp(int(sys.argv[2])))]:
    n = rule = 0; th = collections.Counter(); lx = collections.Counter(); ex = []
    for s in gen:
        n += 1
        t = ns["_thing_that_density"](s); th[min(t, 10)] += 1
        l = len(ns["_style_lex_hits"](s, "deepseek_lex.json")); lx[min(l, 10)] += 1
        h = ns["_deepseek_style"](s)
        if h:
            rule += 1; ex.append((t, l, h[:6], s[:100]))
    print(f"::warning title={name}::windows {n} | deepseek rule hits {rule} | thing/1000w hist {sorted(th.items())} | lex hist {sorted(lx.items())}", flush=True)
    for e in ex[:15]:
        print(f"::notice title={name} hit::{json.dumps(e)[:900]}", flush=True)
