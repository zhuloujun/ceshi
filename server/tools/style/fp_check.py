"""独立真人文本上检验千问套语规则（engine._qwen_style）和挖掘出的千问短语表（qwen_lex.json）的误报。
数据：BookCorpusOpen 当代小说（每本取前 6 万词，4000 词窗口）、WritingPrompts 真人故事（按 8 篇拼成约 4000 词）。"""
import json, re, sys, collections, itertools
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
src = (Path(__file__).resolve().parents[2] / "app/engine.py").read_text("utf-8")
ns = {}
exec(src[src.index("_QW_PATS = {"):src.index("def is_short")], ns)
lex = json.loads((Path(__file__).parent / "qwen_lex.json").read_text())
tok = lambda t: " " + " ".join(re.findall(r"[a-z']+", t.lower())) + " "
def lexscore(s):
    s = tok(s); h = [g for g in lex if (" " + g + " ") in s]
    return len([g for g in h if not any(g != m and (" " + g + " ") in (" " + m + " ") for m in h)])
from datasets import load_dataset
def windows(texts, n=4000):
    for t in texts:
        w = t.split()
        for i in range(0, min(len(w), 60000), n):
            if len(w[i:i + n]) >= 1500:
                yield " ".join(w[i:i + n])
def books(k):
    ds = load_dataset("lucadiliello/bookcorpusopen", split="train", streaming=True)
    for r in itertools.islice(ds, k):
        yield r.get("text") or ""
def wp(k):
    ds = load_dataset("euclaise/writingprompts", split="train", streaming=True)
    buf = []
    for r in itertools.islice(ds, k * 8):
        buf.append(r.get("story") or "")
        if len(buf) == 8:
            yield "\n\n".join(buf); buf = []
for name, gen in [("bookcorpusopen", windows(books(int(sys.argv[1])))), ("writingprompts", wp(int(sys.argv[2])))]:
    n = 0; rule = 0; hist = collections.Counter(); ex = []
    for s in gen:
        n += 1
        k = ns["_qwen_style"](s)
        if k:
            rule += 1; ex.append(("rule", k, s[:120]))
        sc = lexscore(s); hist[min(sc, 10)] += 1
        if sc >= 3: ex.append(("lex", sc, s[:120]))
    print(f"::warning title={name}::windows {n} | qwen_style rule hits {rule} | lexicon score hist {sorted(hist.items())}", flush=True)
    for e in ex[:15]:
        print(f"::notice title={name} hit::{json.dumps(e)[:900]}", flush=True)
