"""训练"英文第二分类器"：专门识别国产大模型（DeepSeek / Kimi / 文心一言 / 通义千问）写的英文论文，
与 desklib 英文分类器（主要见过 GPT、LLaMA 等国外模型）互补。

v1 / v2 的教训（2026-10）：训练用的真人英文太少、太单一（约 500 篇 arXiv 摘要 + 1200 段 MAGE），模型没学会
"真人英文长什么样"——在没见过的领域里约 40% 的真人文字也被打到 95% 以上，综合判断时只能把它的权重压得很低，
结果国产模型写的论文（如农业、经济类）仍然漏检。v3 起：
  · 真人英文大幅扩充：MAGE 各领域 6000 段 + arXiv + PubMed 2012–2021 年论文摘要（一半是中国作者，
    这是最容易被冤枉的人群）；
  · AI 样本贴近真实用法：同题 PubMed 论文让各家模型按中文指令写整篇论文 / 摘要 / 引言 / 结果；
  · 长文取多个窗口（与线上 1000–1500 字符一段一致），标签平滑防止过度自信；
  · 按"论文标题"划分训练 / 评估，评估题目的真人版和 AI 版都从不参与训练；按 1% 和 5% 误判率报告各来源的检出与误判。
有 GPU 时自动使用（.github/workflows/train-english.yml 在 Modal 的 GPU 上运行），否则用 CPU（很慢）。

用法：python tools/train_english.py --mage-dir <MAGE 目录> --out <输出目录>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate as ev  # noqa: E402

GEN_DIR = ev.DATA_DIR / "gen_en"
HUMAN_FILES = {"arxiv_human", "pubmed_human", "pmc_human", "genre_human"}


def split_of(key: str) -> str:
    return "test" if int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % 5 == 0 else "train"


def windows(text: str, rnd: random.Random, k: int) -> list[str]:
    """与线上一致：长文用网站同一套分段规则（app.segmenter）切段，最多取 k 段——训练时见到的段落与线上检测的段落
    长得一样（带不带小标题、从哪句开始），模型才不会因为分段位置不同而判断大幅波动（v3 的教训：同一段引言，
    前面带不带一行 Keywords，得分能从 96% 掉到 14%）。另随机去掉一部分段落的首行（小标题），增加鲁棒性。"""
    if k > 1 and len(text) > 1500:
        from app.segmenter import segment_text
        segs = [x.text for x in segment_text(text) if x.counted and x.register == "en" and len(x.text) >= 300]
        if len(segs) > k:
            segs = rnd.sample(segs, k)
        out = []
        for t in segs:
            lines = t.split("\n")
            if len(lines) > 1 and len(lines[0]) < 90 and rnd.random() < 0.4:
                t = "\n".join(lines[1:])
            out.append(" ".join(t.split()))
        if out:
            return out
    text = " ".join(text.split())
    if len(text) <= 1500:
        return [text]
    out, pos = [], 0
    while pos < len(text) - 600 and len(out) < k:
        chunk = text[pos:pos + 1500]
        end = chunk.rfind(". ")
        chunk = chunk[:end + 1] if end > 900 else chunk
        out.append(chunk)
        pos += len(chunk) + rnd.randint(0, 300)
    return out


def clean_ai(text: str) -> str:
    """去掉生成文字里的 Markdown 痕迹（表格行、*、#、---）和夹杂的中文行：用户粘贴到 Word 里的论文没有这些，
    留着会让模型靠"有没有星号"来判断，而不是学真正的文风。"""
    lines = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("|") or re.fullmatch(r"[-=*_\s]{3,}", s) or re.search(r"[\u4e00-\u9fff]", s):
            continue
        lines.append(re.sub(r"[*#`]+", "", s).strip())
    return "\n".join(lines)


def load_jsonl(f: Path) -> list[dict]:
    return [json.loads(l) for l in f.read_text("utf-8").split("\n") if l.strip()] if f.exists() else []


NOVEL_DATASETS = ("lucadiliello/bookcorpusopen", "bookcorpusopen")


def novel_excerpts(want: int, rnd: random.Random, per_book: int = 3, max_minutes: float = 25.0):
    """BookCorpusOpen 小说片段：跳过开头 8%（版权页、目录、致谢）和结尾 5%，每本取 per_book 段 250–600 词的连续段落。
    下载失败时返回空列表（训练照常进行，结果里看不到 novel-human 一栏）。"""
    try:
        from datasets import load_dataset
    except Exception as e:  # noqa: BLE001
        print(f"::warning title=小说片段::没有 datasets 库：{e}", flush=True)
        return []
    t0, out = time.time(), []
    for name in NOVEL_DATASETS:
        try:
            ds = load_dataset(name, split="train", streaming=True)
            for i, b in enumerate(ds):
                if (time.time() - t0) / 60 > max_minutes or len(out) >= want:
                    break
                if i % 3:                              # 隔几本取一本，题材更分散
                    continue
                title = str(b.get("title") or i)
                paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n|\n", b.get("text") or "")]
                paras = [p for p in paras if len(p.split()) >= 3]
                if len(paras) < 200:
                    continue
                body = paras[int(len(paras) * 0.08): int(len(paras) * 0.95)]
                head = " ".join(body[:80])
                if len(re.findall(r"[^\x00-\x7f\u2018-\u201d\u2026\u2014\u2013]", head)) > 0.005 * len(head):
                    continue                           # 只要英文书
                for _ in range(per_book):
                    st = rnd.randint(0, max(0, len(body) - 60))
                    buf, n, goal = [], 0, rnd.randint(250, 600)
                    for p in body[st:]:
                        buf.append(p)
                        n += len(p.split())
                        if n >= goal:
                            break
                    txt = "\n".join(buf)
                    if n >= 200 and not re.search(r"copyright|smashwords|all rights reserved|chapter \w+$", txt, re.I | re.M):
                        out.append((txt, title))
            if out:
                break
        except Exception as e:  # noqa: BLE001
            print(f"::warning title=小说片段 {name}::{e}", flush=True)
    print(f"::notice title=小说片段::{len(out)} 段，{len({t for _, t in out})} 本（{(time.time() - t0) / 60:.1f} 分钟）", flush=True)
    return out[:want]


# 其他领域的真人英文（2026-10，用户要求"各行各业、各个题材的论文、图书、文章、报告"）：都取 ChatGPT 之前的公开数据集，
# 训练时在训练机上临时下载，不提交进仓库。每个来源按文档编号划分训练 / 评估（评估部分从不参与训练）。
EXTRA_HUMAN = [  # (数据集, 配置, 正文字段, 名称)
    ("launch/gov_report", None, "document", "govreport"),          # 美国政府问责局 / 国会研究处报告
    ("FiscalNote/billsum", None, "text", "bill"),                  # 美国法案文本
    ("EdinburghNLP/xsum", None, "document", "bbc-news"),            # BBC 新闻（2010–2017）
    ("legacy-datasets/wikipedia", "20220301.en", "text", "wiki"),  # 维基百科 2022-03 快照（ChatGPT 之前）
    ("multi_news", None, "document", "multinews"),                 # 多来源新闻报道
    # 论文全文（用户要求加大论文比例：网站上最多的就是论文，真人论文误判代价最大）——各取 2 倍数量
    ("ccdv/arxiv-summarization", None, "article", "arxiv-paper", 2),      # arXiv 论文正文（物理、计算机、数学等，2018 年前）
    ("ccdv/pubmed-summarization", None, "article", "pubmed-paper", 2),    # PubMed 论文正文（医学、生物）
    ("tomasg25/scientific_lay_summarisation", "plos", "article", "plos-paper", 2),   # PLOS 期刊论文正文
    # 各学科（含人文社科）：Elsevier 开放获取论文（2014–2020，按学科分类，含艺术人文、社会科学、经济、心理、法律等 27 个大类）、
    # 斯坦福哲学百科、欧洲人权法院判决书（法学）
    ("orieg/elsevier-oa-cc-by", None, "body_text", "elsevier-paper", 3),
    ("AiresPucrs/stanford-encyclopedia-philosophy", None, "text", "philosophy", 1),
    ("coastalcph/lex_glue", "ecthr_a", "text", "law-ecthr", 1),
]


def extra_human_excerpts(want_each: int, rnd: random.Random, max_minutes: float = 8.0):
    try:
        from datasets import load_dataset
    except Exception:  # noqa: BLE001
        return []
    out = []
    for name, cfg, field, tag, *mult in EXTRA_HUMAN:
        t0, got = time.time(), 0
        want = want_each * (mult[0] if mult else 1)
        try:
            ds = load_dataset(name, cfg, split="train", streaming=True) if cfg else load_dataset(name, split="train", streaming=True)
            for i, row in enumerate(ds):
                if got >= want or (time.time() - t0) / 60 > max_minutes:
                    break
                if i % 7:
                    continue
                v = row.get(field) or ""
                if isinstance(v, list):                    # 正文按句 / 按段存成列表（Elsevier、lex_glue）
                    v = "\n".join(x.get("sentence") or x.get("text") or "" if isinstance(x, dict) else str(x) for x in v)
                txt = str(v).replace("|||||", "\n")
                if tag == "elsevier-paper" and len(txt.split("\n")) < 5:
                    txt = re.sub(r"(?<=[.!?]) (?=[A-Z])", "\n", txt, count=60)   # 没有分段的正文按句切开
                paras = [" ".join(p.split()) for p in re.split(r"\n+", txt)]
                paras = [p for p in paras if len(p.split()) >= 12]
                if sum(len(p.split()) for p in paras) < 220:
                    continue
                st = rnd.randint(0, max(0, len(paras) - 3))
                buf, n, goal = [], 0, rnd.randint(200, 500)
                for p in paras[st:]:
                    buf.append(p)
                    n += len(p.split())
                    if n >= goal:
                        break
                if n >= 180:
                    out.append(("\n".join(buf), f"{tag}:{row.get('id', i)}", tag))
                    got += 1
        except Exception as e:  # noqa: BLE001
            print(f"::warning title=真人英文 {name}::{str(e)[:200]}", flush=True)
        print(f"::notice title=真人英文 {tag}::{got} 段（{(time.time() - t0) / 60:.1f} 分钟）", flush=True)
    return out


def build_rows(args, rnd):
    from app.segmenter import normalize_english
    raw = []   # (text, y, src, split, n_windows)
    for h in load_jsonl(GEN_DIR / "arxiv_human.jsonl"):
        raw.append((h["text"], 0, "arxiv-human", split_of(h["title"]), 1))
    for h in load_jsonl(GEN_DIR / "pubmed_human.jsonl"):
        raw.append((h["text"], 0, "pubmed-human-cn" if h.get("cn") else "pubmed-human", split_of(h["title"]), 1))
    for h in load_jsonl(GEN_DIR / "pmc_human.jsonl"):      # 真人论文正文（引言、方法、结果、讨论），每篇取 4 个窗口
        text = re.sub(r"\s+([.,;:])", r"\1", h["text"])    # 去掉引用标注后留下的"空格 + 句号"，免得成了"真人"的标志
        raw.append((text, 0, "pmc-human-cn" if h.get("cn") else "pmc-human", split_of(h["title"]), 6))
    # 非论文体裁的真人长文（新闻、故事、影评、学生论文、经典散文；tools/collect_human_en.py 收集）——
    # 与 AI 一方的童话、散文、读后感（ge_*.jsonl）对应；没有它，模型会把真人新闻、故事也判成 AI（v8 的教训）
    for h in load_jsonl(GEN_DIR / "genre_human.jsonl"):
        if "genre_human" not in args.skip_prefix:
            raw.append((h["text"], 0, f"genre-human-{h['source']}", split_of(h["title"]), args.genre_human_windows))
    # 当代出版小说（真人）：v11 把 Nicholas Sparks《At First Sight》这类当代小说的段落约八成打到 0.9 以上——训练数据里的
    # 真人故事只有 Reddit 习作和古腾堡老书，没有"当代成熟小说"的文风（对话多、心理描写细、用词规范）。训练时从 BookCorpusOpen
    # （2015 年前 Smashwords 上的真人小说，ChatGPT 之前）现取若干书的片段；按书名划分训练 / 评估，评估书的片段从不参与训练。
    # 不把原文提交进公开仓库（受版权保护），只在训练机上临时下载。
    if args.novel_human > 0:
        for text, title in novel_excerpts(args.novel_human, rnd):
            raw.append((text, 0, "novel-human", split_of("novel:" + title), 2))
    if args.extra_human > 0:
        for text, key, tag in extra_human_excerpts(args.extra_human, rnd):
            raw.append((text, 0, f"{tag}-human", split_of(key), 1))
    gen_models = []
    for f in sorted(GEN_DIR.glob("*.jsonl")):
        if f.stem in HUMAN_FILES or any(f.stem.startswith(x) for x in args.skip_prefix):
            continue
        gen_models.append(f.stem)
        model = re.sub(r"^(pm|tr|us|ge|hu|nv|dc|ap)_", "", f.stem) + {"tr": "-译", "us": "-用户式", "ge": "-体裁", "hu": "-去AI味", "nv": "-小说", "dc": "-行业文体", "ap": "-学科论文"}.get(f.stem[:2], "")
        for g in load_jsonl(f):
            raw.append((clean_ai(g["text"]), 1, f"gen-{model}", split_of(g["title"]), 6))
    if not gen_models:
        raise SystemExit("tools/data/gen_en 里还没有生成数据，请先运行“生成英文 AI 训练数据”工作流")
    mage = ev.read_mage(Path(args.mage_dir) / "valid.csv")
    mh = [r for r in mage if r["y"] == 0]
    ma = [r for r in mage if r["y"] == 1 and ev.EN_STRONG.search(r["model"])] or [r for r in mage if r["y"] == 1]
    for r in ev.balanced_sample(mh, args.n_mage_human, lambda r: r["domain"], 41):
        raw.append((r["text"], 0, f"mage-human-{r['domain']}", split_of(r["text"][:200]), 1))
    for r in ev.balanced_sample(ma, args.n_mage_ai, lambda r: r["domain"], 42):
        raw.append((r["text"], 1, "mage-ai", split_of(r["text"][:200]), 1))
    for r in ev.user_ai_rows("english"):
        raw.append((r["text"], 1, r["model"], "test" if r["split"] == "test" else "train", 2))
    for t in ev.read_blocks(ev.DATA_DIR / "ai_english_extra.txt"):
        raw.append((t, 1, "repo-ai-english", "train", 1))
    # 用户提供的已标注英文（私有 Kaggle 脚本传入，不进公开仓库）：{t, y, r:'en', d, split?}
    if getattr(args, "user_docs", ""):
        n_ud = 0
        for line in Path(args.user_docs).read_text("utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if (r.get("register") or {"en": "en"}.get(r.get("r"), "")) != "en":
                continue
            t = r.get("text", r.get("t", "")).strip()
            src = f"user-{r.get('doc', r.get('d', '?')).split('-')[0]}-{'ai' if int(r['y']) else 'human'}"
            raw.append((t, int(r["y"]), src, "test" if r.get("split") == "test" else "train",
                        2 if r.get("split") == "test" else max(1, args.user_docs_reps)))
            n_ud += 1
        print(f"用户英文段落 {n_ud}", flush=True)
    rows = []
    for text, y, src, split, k in raw:
        for w in windows(text, rnd, k):
            w = normalize_english(w)
            if len(w) >= 300:
                rows.append({"text": w, "y": y, "src": src, "split": split})
    return rows, gen_models


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mage-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="FacebookAI/roberta-base")
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--max-len", type=int, default=320)
    ap.add_argument("--n-mage-human", type=int, default=6000, help="MAGE 人写取多少段（各领域均衡）")
    ap.add_argument("--n-mage-ai", type=int, default=2500, help="MAGE AI 取多少段")
    ap.add_argument("--genre-human-windows", type=int, default=3, help="每篇非论文真人长文取几段")
    ap.add_argument("--skip-prefix", nargs="*", default=[], help="不使用这些前缀的生成数据（如 tr_），用于对比实验")
    ap.add_argument("--label-smoothing", type=float, default=0.1)
    ap.add_argument("--time-budget-min", type=float, default=270)
    ap.add_argument("--user-docs", default="", help="用户提供的已标注段落（jsonl，r='en' 为英文）")
    ap.add_argument("--user-docs-reps", type=int, default=3, help="用户英文在训练集里取几个窗口")
    ap.add_argument("--extra-human", type=int, default=0, help="其他领域真人英文（政府报告、法案、BBC 新闻、维基百科等）每个来源取多少段")
    ap.add_argument("--novel-human", type=int, default=0, help="从 BookCorpusOpen 取多少段当代真人小说片段（0 = 不用）")
    args = ap.parse_args()

    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    if dev == "cpu":
        torch.set_num_threads(int(os.getenv("TORCH_THREADS", os.cpu_count() or 2)))
    else:
        args.batch = max(args.batch, 16 if "large" in args.base else 32)
    print(f"设备：{dev}", flush=True)
    torch.manual_seed(0)
    rnd = random.Random(0)
    rows, gen_models = build_rows(args, rnd)
    train = [r for r in rows if r["split"] == "train"]
    test = [r for r in rows if r["split"] == "test"]
    rnd.shuffle(train)
    dev_set = train[: max(300, len(train) // 15)]
    train = train[len(dev_set):]
    # 两类平衡：少的一类过采样
    h_tr = [r for r in train if r["y"] == 0]
    a_tr = [r for r in train if r["y"] == 1]
    if len(h_tr) < len(a_tr):
        h_tr = (h_tr * math.ceil(len(a_tr) / len(h_tr)))[:len(a_tr)]
    elif len(a_tr) < len(h_tr):
        a_tr = (a_tr * math.ceil(len(h_tr) / len(a_tr)))[:len(h_tr)]
    train = h_tr + a_tr
    rnd.shuffle(train)
    by = lambda rs: {s: sum(r["src"] == s for r in rs) for s in sorted({r["src"] for r in rs})}
    print(f"训练 {len(train)}：{json.dumps(by(train), ensure_ascii=False)}", flush=True)
    print(f"评估 {len(test)}：{json.dumps(by(test), ensure_ascii=False)}", flush=True)

    tok = AutoTokenizer.from_pretrained(args.base)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.base, num_labels=2, id2label={0: "human", 1: "ai"}, label2id={"human": 0, "ai": 1}).to(dev)

    def batches(data, bs, shuffle):
        idx = list(range(len(data)))
        if shuffle:
            rnd.shuffle(idx)
        for i in range(0, len(idx), bs):
            chunk = [data[j] for j in idx[i:i + bs]]
            enc = tok([r["text"] for r in chunk], truncation=True, max_length=args.max_len, padding=True, return_tensors="pt")
            yield {k: v.to(dev) for k, v in enc.items()}, torch.tensor([r["y"] for r in chunk], device=dev)

    def predict(data):
        model.eval()
        out = []
        with torch.inference_mode(), torch.autocast(dev, enabled=dev == "cuda"):
            for enc, _ in batches(data, 64, False):
                out.extend(torch.softmax(model(**enc).logits.float(), -1)[:, 1].tolist())
        model.train()
        return out

    total = max(1, int(math.ceil(len(train) / args.batch) * args.epochs))
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    sched = get_linear_schedule_with_warmup(opt, int(total * 0.06), total)
    scaler = torch.amp.GradScaler(enabled=dev == "cuda")
    model.train()
    t0 = time.time()
    step, best, best_state = 0, -1.0, None
    evals = sorted({int(total * f) for f in (0.25, 0.5, 0.75, 1.0)})
    done = False
    while not done:
        for enc, y in batches(train, args.batch, True):
            with torch.autocast(dev, enabled=dev == "cuda"):
                logits = model(**enc).logits
            loss = torch.nn.functional.cross_entropy(logits.float(), y, label_smoothing=args.label_smoothing)
            scaler.scale(loss).backward()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            scaler.step(opt); scaler.update(); sched.step(); opt.zero_grad()
            step += 1
            if step % 100 == 0:
                el = time.time() - t0
                print(f"  step {step}/{total} loss {loss.item():.4f}  {el/60:.1f} 分钟（剩余约 {el/step*(total-step)/60:.0f} 分钟）", flush=True)
            over = (time.time() - t0) / 60 > args.time_budget_min
            if step in evals or step >= total or over:
                p = predict(dev_set)
                a = ev.auroc([q for q, r in zip(p, dev_set) if r["y"] == 1], [q for q, r in zip(p, dev_set) if r["y"] == 0])
                print(f"  开发集 AUROC {a:.4f}（step {step}）", flush=True)
                if a > best:
                    best, best_state = a, {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            if step >= total or over:
                done = True
                break

    model.load_state_dict(best_state)
    pt = predict(test)
    hum = sorted(p for p, r in zip(pt, test) if r["y"] == 0)
    thr = {f: (hum[min(len(hum) - 1, int(len(hum) * (1 - f)))] if hum else 0.5) for f in (0.01, 0.05)}
    groups = {}
    for p, r in zip(pt, test):
        groups.setdefault(r["src"], []).append(p)
    is_h = lambda s: "human" in s
    res = {"dev_auroc": round(best, 4),
           "test_auroc_all": round(ev.auroc([p for p, r in zip(pt, test) if r["y"] == 1], hum), 4),
           "thresholds": {f"fpr_{int(f*100)}pct": round(t, 4) for f, t in thr.items()}}
    for f, t in thr.items():
        k = f"{int(f*100)}pct"
        res[f"ai_caught_at_{k}"] = {s: round(sum(x >= t for x in v) / len(v), 3) for s, v in groups.items() if not is_h(s)}
        res[f"human_flagged_at_{k}"] = {s: round(sum(x >= t for x in v) / len(v), 3) for s, v in groups.items() if is_h(s)}
    res.update({"n_train": len(train), "n_test": len(test), "test_by_source": by(test), "gen_models": gen_models,
                "base": args.base, "epochs": args.epochs, "steps": step, "minutes": round((time.time() - t0) / 60, 1),
                "device": dev})
    # 用户实际检测过、确认来源的文档（只评估、从不训练）：逐段打分
    from app.segmenter import normalize_english
    ud = load_jsonl(ev.DATA_DIR / "eval_en_user_docs.jsonl")
    if ud:
        pu = predict([{"text": normalize_english(" ".join(r["text"].split())), "y": r["y"]} for r in ud])
        docs = {}
        for p_, r in zip(pu, ud):
            docs.setdefault(f"{r['doc']}（{'AI' if r['y'] else '真人'}）", []).append(round(p_, 3))
        res["user_docs"] = docs
        res["user_docs_flagged_at_5pct"] = {d: f"{sum(x >= thr[0.05] for x in v)}/{len(v)}" for d, v in docs.items()}
        res["user_docs_flagged_at_1pct"] = {d: f"{sum(x >= thr[0.01] for x in v)}/{len(v)}" for d, v in docs.items()}
    print(json.dumps(res, ensure_ascii=False, indent=1), flush=True)
    for k in ("1pct", "5pct"):
        print(f"::notice title=英文第二分类器评估（误判率 {k}，没参与训练的题目与样本）::" + json.dumps(
            {"test_auroc_all": res["test_auroc_all"], "ai_caught": res[f"ai_caught_at_{k}"],
             "human_flagged": res[f"human_flagged_at_{k}"], "user_docs": res.get(f"user_docs_flagged_at_{k}")},
            ensure_ascii=False), flush=True)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    model.half().save_pretrained(out, safe_serialization=True)
    tok.save_pretrained(out)
    (out / "training_result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    (out / "README.md").write_text("# 英文第二分类器（国产大模型英文）\n\n由 tools/train_english.py 微调 "
                                   f"{args.base} 得到，标签 0 = 人写、1 = AI。\n\n```json\n"
                                   + json.dumps(res, ensure_ascii=False, indent=1) + "\n```\n", "utf-8")


if __name__ == "__main__":
    main()
