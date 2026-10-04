"""收集"非论文体裁"的真人英文长文，作为英文分类器训练时的真人样本 → tools/data/gen_en/genre_human.jsonl

为什么需要：英文分类器训练数据里，AI 一方有大量童话、散文、读后感、清单（ge_*.jsonl），真人一方却几乎只有论文摘要
和 MAGE 短段。2026-10 的 v8 因此把真人 CNN 新闻、Reddit 故事也打到 0.95（150 篇新闻里 16 篇 ≥0.9），没法用于整篇判断。

数据全部取各数据集的 train 划分（en_doc_check.py 验证时用 test / validation 划分，二者不重叠），都是 ChatGPT 之前的真人文字：
  · CNN / DailyMail 新闻   · Reddit WritingPrompts 故事   · IMDB 长影评
  · IvyPanda 学生论文 / 议论文   · 古腾堡电子书片段（经典散文、小说，与"田园散文"文风最接近）

用法（gen-english.yml，source = humangenre）：python tools/collect_human_en.py
"""
import json
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data" / "gen_en" / "genre_human.jsonl"
SOURCES = [  # (数据集, 配置, 字段, 名称, 篇数, 最少词数)
    ("abisee/cnn_dailymail", "3.0.0", "article", "news", 450, 450),
    ("euclaise/writingprompts", None, "story", "story", 450, 350),
    ("stanfordnlp/imdb", None, "text", "review", 250, 300),
    ("qwedsacf/ivypanda-essays", None, "TEXT", "essay", 450, 450),
    ("gutendex", None, None, "classic", 350, 0),
]
# 古腾堡（公版）经典散文 / 随笔 / 自然与乡村题材——与豆包写的"田园散文"文风最接近
GUTENDEX_TOPICS = ["essays", "country life", "nature", "natural history", "seasons", "description and travel", "farm life"]


def gutenberg_docs(want, rnd):
    books, seen = [], set()
    for topic in GUTENDEX_TOPICS:
        for page in (1, 2):
            try:
                d = _get(f"https://gutendex.com/books/?languages=en&topic={urllib.parse.quote(topic)}&page={page}")
            except Exception as e:  # noqa: BLE001
                print(f"::warning title=gutendex {topic}::{e}", flush=True)
                break
            for b in d.get("results", []):
                if b["id"] in seen:
                    continue
                years = [a.get("death_year") or 0 for a in b.get("authors", [])]
                if years and max(years) > 1960:        # 只要早已进入公版的作者
                    continue
                url = next((u for k, u in b.get("formats", {}).items() if k.startswith("text/plain") and not u.endswith(".zip")), None)
                if url:
                    seen.add(b["id"])
                    books.append((b["title"], url))
            if not d.get("next"):
                break
    rnd.shuffle(books)
    print(f"::notice title=gutendex::找到 {len(books)} 本", flush=True)
    out = []
    for title, url in books:
        try:
            t = _get(url, raw=True).decode("utf-8", "replace").replace("\r", "")
        except Exception as e:  # noqa: BLE001
            print(f"  {title}: {e}", flush=True)
            continue
        m = re.search(r"\*\*\* ?START OF.*?\*\*\*(.*)\*\*\* ?END OF", t, re.S)
        t = m.group(1) if m else t
        paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", t)]
        paras = [p for p in paras if len(p.split()) >= 40 and not re.search(r"gutenberg|chapter [ivxl\d]+\b|^\[|illustration", p, re.I)]
        if len(paras) < 30:
            continue
        for _ in range(4):                                     # 每本书取 4 个约 900 词的片段
            st = rnd.randint(len(paras) // 10, max(len(paras) // 10, len(paras) - 15))
            buf, n = [], 0
            for p in paras[st:]:
                buf.append(p)
                n += len(p.split())
                if n >= 900:
                    break
            if n >= 600:
                out.append("\n\n".join(buf))
        if len(out) >= want:
            break
    return out[:want]


def _get(url, raw=False):
    for i in range(4):
        try:
            time.sleep(1.0)                                   # 放慢，避免 429 Too Many Requests
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research data collection)"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
                return data if raw else json.loads(data)
        except urllib.error.HTTPError as e:
            if i == 3:
                raise
            time.sleep(30 if e.code == 429 else 5)
        except Exception:  # noqa: BLE001
            if i == 3:
                raise
            time.sleep(5)


def rows(dataset, config, field, want, min_words, rnd):
    q = urllib.parse.quote(dataset, safe="")
    sp = _get(f"https://datasets-server.huggingface.co/splits?dataset={q}")["splits"]
    if config:
        sp = [s for s in sp if s["config"] == config] or sp
    sp = [s for s in sp if s["split"] == "train"] or sp
    cfg, split = sp[0]["config"], sp[0]["split"]
    try:
        size = _get(f"https://datasets-server.huggingface.co/size?dataset={q}")["size"]["splits"]
        n_rows = next((s["num_rows"] for s in size if s["config"] == cfg and s["split"] == split), 20000)
    except Exception:  # noqa: BLE001
        n_rows = 20000
    # 跳过最前面 3000 行，避开可能与评估重叠的开头部分
    offsets = list(range(3000 if n_rows > 6000 else 0, max(1, n_rows - 20), 37 * 20))
    rnd.shuffle(offsets)
    out, seen, probed, errs = [], set(), False, 0
    for off in offsets[:150]:
        try:
            rs = _get(f"https://datasets-server.huggingface.co/rows?dataset={q}&config={urllib.parse.quote(cfg)}"
                      f"&split={urllib.parse.quote(split)}&offset={off}&length=20")["rows"]
        except Exception as e:  # noqa: BLE001
            errs += 1
            if errs <= 2:
                body = e.read()[:300] if hasattr(e, "read") else b""
                print(f"::warning title={dataset} 读取失败::{cfg}/{split} offset {off}: {e} {body!r}", flush=True)
            if errs >= 15 and not out:
                break
            continue
        for r in rs:
            row = r["row"]
            if not probed:
                probed = True
                print(f"::notice title=字段 {dataset}::{cfg}/{split} {n_rows} 行；" + ", ".join(
                    f"{k}({len(str(v))})" for k, v in row.items()), flush=True)
            t = row.get(field) or row.get(field.lower()) or max((v for v in row.values() if isinstance(v, str)), key=len, default="")
            if not isinstance(t, str):
                continue
            t = t.replace("<newline>", "\n").replace("<br />", "\n").replace("\r", "")
            if dataset.endswith("gutenberg_english"):     # 电子书：跳过版权页和目录，取中间约 900 词，保留原来的段落
                paras = [p.strip() for p in re.split(r"\n\s*\n", t) if len(p.split()) >= 40]
                if len(paras) < 40:
                    continue
                st = rnd.randint(len(paras) // 5, len(paras) * 3 // 5)
                buf, n = [], 0
                for p in paras[st:]:
                    p = " ".join(p.split())
                    buf.append(p)
                    n += len(p.split())
                    if n >= 900:
                        break
                t = "\n\n".join(buf)
                if n < 600 or re.search(r"gutenberg|copyright|chapter [ivxl]+\b", t, re.I):
                    continue
            elif dataset.endswith("cnn_dailymail"):
                t = re.sub(r"^.{0,120}?\(CNN\)\s*--\s*", "", t)
                t = re.sub(r"(?<=[.!?\"]) (?=[A-Z\"])", "\n", t, count=12)   # 新闻原文没有分段：按句子切成小段
            if len(t.split()) >= min_words and t[:80] not in seen:
                seen.add(t[:80])
                out.append(t.strip())
        if len(out) >= want:
            break
    return out[:want]


def main():
    rnd = random.Random(23)
    have = [json.loads(l) for l in OUT.read_text("utf-8").split("\n") if l.strip()] if OUT.exists() else []
    cnt = {}
    for h in have:
        cnt[h["source"]] = cnt.get(h["source"], 0) + 1
    for ds, cfg, field, name, want, min_words in SOURCES:
        if cnt.get(name, 0) >= want * 0.8:
            continue
        have = [h for h in have if h["source"] != name]       # 不够数的来源整批重新收集
        try:
            docs = gutenberg_docs(want, rnd) if ds == "gutendex" else rows(ds, cfg, field, want, min_words, rnd)
        except Exception as e:  # noqa: BLE001
            print(f"::warning title=真人英文 {name} 下载失败::{ds} {e}", flush=True)
            continue
        have += [{"title": f"{name}-{i}", "source": name, "text": t} for i, t in enumerate(docs)]
        print(f"::notice title=真人英文 {name}::{len(docs)} 篇（{ds}）", flush=True)
    OUT.write_text("".join(json.dumps(h, ensure_ascii=False) + "\n" for h in have), "utf-8")


if __name__ == "__main__":
    sys.exit(main())
