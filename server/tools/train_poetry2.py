"""训练"诗词专用"AI 检测分类器 v2（在 GitHub Actions 的 CPU 上运行，见 .github/workflows/train-poetry-v2.yml）。

v2 相对 v1（train_poetry.py）：人写训练样本加入唐宋古人作品，AI 训练样本加入 DeepSeek / Kimi / 文心一言写的诗词；
评估额外报告《唐诗三百首》《宋词三百首》的误判、国产新模型诗词与"复述故事"诗词的检出（这些都不参与训练）。

以下为 v1 说明：

依据：ChangAn 论文（ACL 2026，arXiv:2604.10101）的评测中，通用中文检测器在诗词上的 AUROC 只有约 72%，
零样本方法（Fast-DetectGPT 等）接近随机；在 ChangAn 上微调的 RoBERTa 可达约 95%，对没见过的生成模型也有 93%–98%。

做法：以 hfl/chinese-roberta-wwm-ext 为底座，在 ChangAn 的"训练"划分上微调二分类（人写 / AI）。
人写与 AI 各取相同数量；AI 样本在 DeepSeek、豆包（Seed）、GPT-4.1 三个模型和两种生成策略间均衡。
Kimi-K2 与"校准 / 评估"划分完全不参与训练（划分规则见 tools/changan.py）。

用法：python tools/train_poetry.py --changan <ChangAn 目录> --out <输出目录>
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import changan  # noqa: E402
import evaluate as ev  # noqa: E402


def load_classic(cpoetry_dir, n, rnd):
    """全唐诗 / 全宋词里随机抽 n 首作人写训练样本；《唐诗三百首》《宋词三百首》只留作评估，不参与训练。"""
    import glob
    from opencc import OpenCC
    cc = OpenCC("t2s")
    d = Path(cpoetry_dir)
    norm = lambda t: "".join(ch for ch in t if "\u4e00" <= ch <= "\u9fff")
    tang300 = [cc.convert("\n".join(p["paragraphs"])) for p in json.loads((d / "全唐诗" / "唐诗三百首.json").read_text("utf-8"))]
    song300 = ["\n".join(p["paragraphs"]) for p in json.loads((d / "宋词" / "宋词三百首.json").read_text("utf-8"))]
    evals = [{"text": t, "y": 0, "model": "唐宋三百首"} for t in tang300 + song300 if len(t) >= 16]
    held = {norm(r["text"])[:24] for r in evals}
    pool = []
    for f in sorted(glob.glob(str(d / "全唐诗" / "poet.tang.*.json"))):
        pool += [cc.convert("\n".join(p.get("paragraphs") or [])) for p in json.loads(Path(f).read_text("utf-8"))]
    for f in sorted(glob.glob(str(d / "宋词" / "ci.song.*.json"))):
        pool += ["\n".join(p.get("paragraphs") or []) for p in json.loads(Path(f).read_text("utf-8"))]
    pool = [t for t in pool if 16 <= len(t) <= 240 and norm(t)[:24] not in held]
    rnd.shuffle(pool)
    out = [{"text": t, "y": 0, "model": "tang-song"} for t in pool[:n]]
    # v6 新增：元曲、诗经、楚辞、纳兰词、曹操诗、五代词（chinese-poetry）也作人写样本——以前的训练集里几乎没有曲和
    # 骚体、古风歌行，用户文档里真人的元曲、汉魏古诗被判成 AI。元曲每 5 首留 1 首作评估（"元曲误判"）。
    extra, yq_eval = [], []
    for name, f, key in (("元曲", "元曲/yuanqu.json", "paragraphs"), ("诗经", "诗经/shijing.json", "content"),
                         ("楚辞", "楚辞/chuci.json", "content"), ("纳兰", "纳兰性德/纳兰性德诗集.json", "para"),
                         ("曹操", "曹操诗集/caocao.json", "paragraphs")):
        fp = d / f
        if not fp.exists():
            continue
        for k, p in enumerate(json.loads(fp.read_text("utf-8"))):
            body = p.get(key) or p.get("paragraphs") or p.get("content") or []
            if isinstance(body, str):
                body = [body]
            t = cc.convert("\n".join(body))
            for i in range(0, len(t), 200):          # 楚辞等长篇切成 200 字以内
                piece = t[i:i + 200]
                if len(piece) < 16:
                    continue
                if name == "元曲" and k % 5 == 0:
                    yq_eval.append({"text": piece, "y": 0, "model": "元曲"})
                else:
                    extra.append({"text": piece, "y": 0, "model": "classic-" + name})
    for sub in ("huajianji", "nantang"):
        for f in sorted(glob.glob(str(d / "五代诗词" / sub / "*.json"))):
            try:
                items = json.loads(Path(f).read_text("utf-8"))
            except ValueError:
                continue
            for p in items if isinstance(items, list) else []:
                t = cc.convert("\n".join(p.get("paragraphs") or []))
                if 16 <= len(t) <= 240:
                    extra.append({"text": t, "y": 0, "model": "classic-五代"})
    rnd.shuffle(extra)
    out += extra[: max(1500, n // 2)]
    load_classic.yuanqu_eval = yq_eval[:400]
    return out, evals


def gen_cl_rows(register):
    """tools/data/gen_cl/ai_*.jsonl（gen_classical_ai.py 生成的五家国产模型古典体裁作品）：按网站自己的文体判断
    （detect_register）分给诗词 / 文言分类器；按题目 + 体裁的哈希每 5 篇留 1 篇作评估，评估篇目从不参与训练。"""
    import hashlib
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.segmenter import detect_register, extract_couplets
    rows = []
    for f in sorted((ev.DATA_DIR / "gen_cl").glob("ai_*.jsonl")):
        for line in f.read_text("utf-8").split("\n"):
            if not line.strip():
                continue
            r = json.loads(line)
            t = r["text"].strip()
            key = f"{r['title']}|{r['genre']}"
            split = "test" if int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % 5 == 0 else "train"
            # 对联：模型常一次写好几副、带"上联：/下联：/横批："标签——拆成一副一副，按用户常见的写法"上联，下联。"
            pieces = extract_couplets(t) if r["genre"] == "duilian" else []
            for k, c in enumerate(pieces or [t]):
                if detect_register(c) != register:
                    continue
                rows.append({"text": c, "y": 1, "model": f"gen-{f.stem[3:]}", "genre": r["genre"], "split": split})
    return rows


def human_couplets(d, n_train, n_test, rnd):
    """真人对联（wb14123/couplet-dataset，冯重朴《梨味斋散叶》等七十多万副）：train/ 取一部分作人写训练样本，test/ 作评估。"""
    out = {}
    for part, n in (("train", n_train), ("test", n_test)):
        p = Path(d) / part
        if not (p / "in.txt").exists():
            out[part] = []
            continue
        ins = (p / "in.txt").read_text("utf-8").split("\n")
        outs = (p / "out.txt").read_text("utf-8").split("\n")
        pairs = [(a.replace(" ", "").strip(), b.replace(" ", "").strip()) for a, b in zip(ins, outs)]
        pairs = [(a, b) for a, b in pairs if 4 <= len(a) == len(b) <= 30]
        rnd.shuffle(pairs)
        out[part] = [{"text": (f"{a}；{b}。" if "，" in a else f"{a}，{b}。"), "y": 0, "model": "human-couplet"}
                     for a, b in pairs[:n]]
    return out["train"], out["test"]


def human_cl_poetry(rnd):
    """用户提供的古典诗曲 / 楹联选本（tools/data/human_cl：全元散曲、元明清散曲、先秦至南北朝诗、宋词鉴赏里的原词；
    楹联丛话、古代楹联里的对联），只保留原文、已剔除与评估集和用户测试文档重复的部分。每 5 段留 1 段作评估。"""
    import hashlib
    out = {"train": [], "test": []}
    for name in ("poetry", "couplet"):
        f = ev.DATA_DIR / "human_cl" / f"{name}.jsonl"
        if not f.exists():
            continue
        for line in f.read_text("utf-8").split("\n"):
            if not line.strip():
                continue
            r = json.loads(line)
            part = "test" if int(hashlib.md5((r["source"] + r["text"][:20]).encode("utf-8")).hexdigest(), 16) % 5 == 0 else "train"
            out[part].append({"text": r["text"], "y": 0, "model": "名家-" + r["source"], "kind": name})
    for v in out.values():
        rnd.shuffle(v)
    return out["train"], out["test"]


def auroc(pos, neg):
    if not pos or not neg:
        return None
    allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    # 秩和法（处理并列）
    ranks, i = {}, 0
    rank_sum = 0.0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        r = (i + j) / 2 + 1
        rank_sum += sum(r for k in range(i, j + 1) if allv[k][1] == 1)
        i = j + 1
    n1, n0 = len(pos), len(neg)
    return (rank_sum - n1 * (n1 + 1) / 2) / (n1 * n0)


def load_user_docs(path, register):
    """用户提供的已标注文档（chalw2、gw 这类"单数 AI、双数真人"的测试文档里，与评估文档不重复的段落）：
    {text, y, register, doc}。只在训练时通过私有 Kaggle 脚本传入，不进公开仓库。"""
    rows = []
    if not path:
        return rows
    for line in Path(path).read_text("utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            reg = r.get("register") or {"c": "zh_classical", "p": "zh_poetry"}.get(r.get("r"), "")   # 紧凑格式 {t, y, r, d}
            if reg == register:
                rows.append({"text": r.get("text", r.get("t")), "y": int(r["y"]), "model": "user-doc-" + r.get("doc", r.get("d", "?"))})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--changan", required=True)
    ap.add_argument("--cpoetry", required=True, help="chinese-poetry 仓库目录（全唐诗、宋词）")
    ap.add_argument("--n-classic", type=int, default=2500)
    ap.add_argument("--user-share", type=float, default=0.15, help="国产新模型样本（过采样后）占 AI 训练样本的比例")
    ap.add_argument("--gen-share", type=float, default=0.35, help="gen_cl 国产模型古典体裁样本（过采样后）占 ChangAn AI 训练样本的比例")
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="hfl/chinese-roberta-wwm-ext")
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--lr", type=float, default=3e-5)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--max-len", type=int, default=128)
    ap.add_argument("--max-train-per-class", type=int, default=5000)
    ap.add_argument("--n-test", type=int, default=3000)
    ap.add_argument("--time-budget-min", type=float, default=240)
    ap.add_argument("--couplets", default="", help="couplet-dataset 解压目录（含 train/、test/）：真人对联")
    ap.add_argument("--n-couplets", type=int, default=2500)
    ap.add_argument("--n-literary", type=int, default=3000, help="名家诗曲、楹联选本取多少段作人写训练样本")
    ap.add_argument("--eval-model", default="", help="只评估这个已训练好的模型（同样的评估集，用于新旧版本对比），不训练")
    ap.add_argument("--user-docs", default="", help="用户提供的已标注文档段落（jsonl，见 load_user_docs）")
    ap.add_argument("--user-docs-reps", type=int, default=6, help="用户文档段落在训练集里重复几次")
    args = ap.parse_args()

    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup

    torch.set_num_threads(int(os.getenv("TORCH_THREADS", os.cpu_count() or 2)))
    torch.manual_seed(0)
    rnd = random.Random(0)

    rows = changan.load(args.changan)
    train_h = [r for r in rows if r["split"] == "train" and r["y"] == 0]
    train_a = [r for r in rows if r["split"] == "train" and r["y"] == 1]
    rnd.shuffle(train_h); rnd.shuffle(train_a)
    n = min(len(train_h), len(train_a), args.max_train_per_class)
    by_model = {}
    for r in train_a:
        by_model.setdefault(r["model"], []).append(r)
    per = n // len(by_model)
    train_a = [r for m in sorted(by_model) for r in by_model[m][:per]]
    train_h = train_h[:n]
    # v2 新增 1：唐宋古人作品作为人写训练样本（不含评估用的《唐诗三百首》《宋词三百首》），
    #          纠正 v1 把古典名篇当成 AI 的问题（v1 在唐宋名篇上误判约 19%）
    classic_train, classic_eval = load_classic(args.cpoetry, args.n_classic, rnd)
    # v2 新增 2：国产新模型（DeepSeek / Kimi / 文心一言）写的诗词，每个模型 2/3 参与训练（过采样），1/3 留作评估
    user = [dict(r, y=1) for r in ev.user_ai_rows("poem")]
    user_train = [r for r in user if r["split"] == "fit"]
    user_test = [r for r in user if r["split"] == "test"]
    # v3 新增：五家国产模型按普通用户指令写的诗、词、对联（tools/data/gen_cl），过采样到约占 AI 训练样本的 gen_share
    gen = gen_cl_rows("zh_poetry")
    gen_train = [r for r in gen if r["split"] == "train"]
    gen_test = [r for r in gen if r["split"] == "test"]
    if gen_train:
        reps_g = max(1, round(args.gen_share * len(train_a) / len(gen_train)))
        gen_train = gen_train * reps_g
    # v6 新增：真人对联作人写训练样本（AI 对联来自 gen_cl），对联单独评估
    cp_train, cp_test = human_couplets(args.couplets, args.n_couplets, 500, rnd) if args.couplets else ([], [])
    # v8 新增：名家古典诗曲、楹联选本作人写训练样本
    lit_train, lit_test = human_cl_poetry(random.Random(9))
    lit_train = lit_train[: args.n_literary]
    lit_test = lit_test[:800]
    base = train_h + classic_train + train_a + user_train + gen_train + cp_train + lit_train
    rnd.shuffle(base)
    dev = base[: min(max(200, len(base) // 20), len(base) // 5)]
    train = base[len(dev):]
    ud = load_user_docs(args.user_docs, "zh_poetry")
    print(f"用户已标注文档（诗词）：AI {sum(r['y'] for r in ud)} / 人写 {sum(1 - r['y'] for r in ud)}，各重复 {args.user_docs_reps} 次", flush=True)
    train += ud * args.user_docs_reps
    # 国产新模型样本少：过采样到约占 AI 训练样本的 user_share（开发集里的不再复制，避免泄漏）
    ut = [r for r in train if r.get("split") == "fit" and r["y"] == 1 and r.get("model", "").startswith("repo-ai-")]
    n_ai = sum(r["y"] == 1 for r in train)
    reps = max(1, round(args.user_share * n_ai / max(1, len(ut))))
    train += ut * (reps - 1)
    n_h = sum(r["y"] == 0 for r in train)
    n_a = sum(r["y"] == 1 for r in train)
    if n_h > n_a:   # 人写多了唐宋古人作品，AI 这边补齐，保持两类平衡
        pool_a = [r for r in train if r["y"] == 1 and not r.get("model", "").startswith("repo-ai-")]
        train += rnd.sample(pool_a, min(len(pool_a), n_h - n_a))
    rnd.shuffle(train)
    test = [r for r in rows if r["split"] == "test"]
    rnd.shuffle(test)
    test_h = [r for r in test if r["y"] == 0][: args.n_test // 2]
    test_a = [r for r in test if r["y"] == 1][: args.n_test // 2]
    story = [{"text": t, "y": 1, "model": "story"} for t in ev.read_blocks(ev.DATA_DIR / "ai_poems_story.txt")]
    print(f"训练 {len(train)}（ChangAn 人写 {len(train_h)} + 唐宋古人 {len(classic_train)} / ChangAn AI {len(train_a)} + "
          f"国产新模型 {len(ut)}×{reps}），开发 {len(dev)}；评估：ChangAn {len(test_h)} 人写 / {len(test_a)} AI，"
          f"唐宋三百首 {len(classic_eval)}，国产新模型 {len(user_test)}，故事诗 {len(story)}", flush=True)

    members = []
    if args.eval_model and (Path(args.eval_model) / "ensemble.json").exists():
        # 多版本合议包：各版本分别打分，取对数几率的平均（与线上 app/detectors/classifier.py 相同）
        names = json.loads((Path(args.eval_model) / "ensemble.json").read_text("utf-8"))["members"]
        members = [AutoModelForSequenceClassification.from_pretrained(str(Path(args.eval_model) / n), dtype=torch.float32)
                   for n in names]
        tok = AutoTokenizer.from_pretrained(str(Path(args.eval_model) / names[0]))
        model = members[0]
    elif args.eval_model:
        tok = AutoTokenizer.from_pretrained(args.eval_model)
        model = AutoModelForSequenceClassification.from_pretrained(args.eval_model, dtype=torch.float32)
    else:
        tok = AutoTokenizer.from_pretrained(args.base)
        model = AutoModelForSequenceClassification.from_pretrained(
            args.base, num_labels=2, id2label={0: "human", 1: "ai"}, label2id={"human": 0, "ai": 1})

    def batches(data, bs, shuffle):
        idx = list(range(len(data)))
        if shuffle:
            rnd.shuffle(idx)
        for i in range(0, len(idx), bs):
            chunk = [data[j] for j in idx[i:i + bs]]
            enc = tok([r["text"] for r in chunk], truncation=True, max_length=args.max_len, padding=True, return_tensors="pt")
            yield enc, torch.tensor([r["y"] for r in chunk])

    def predict(data):
        if members:
            out = []
            with torch.inference_mode():
                for enc, _ in batches(data, 64, False):
                    lg = [m(**enc).logits.float() for m in members]
                    z = sum(l[:, 1] - l[:, 0] for l in lg) / len(lg)
                    out.extend(torch.sigmoid(z).tolist())
            return out
        model.eval()
        out = []
        with torch.inference_mode():
            for enc, _ in batches(data, 64, False):
                out.extend(torch.softmax(model(**enc).logits.float(), -1)[:, 1].tolist())
        model.train()
        return out

    steps_per_epoch = math.ceil(len(train) / args.batch)
    total = max(1, int(steps_per_epoch * args.epochs))
    if not train:
        raise SystemExit("没有训练样本")
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    sched = get_linear_schedule_with_warmup(opt, int(total * 0.06), total)
    model.train()
    t0 = time.time()
    step, best, best_state = 0, -1.0, None
    evals = sorted({int(total * f) for f in (0.5, 0.75, 1.0)})
    done = bool(args.eval_model)
    if done:
        best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
    while not done:
        for enc, y in batches(train, args.batch, True):
            loss = torch.nn.functional.cross_entropy(model(**enc).logits, y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step(); sched.step(); opt.zero_grad()
            step += 1
            if step % 50 == 0:
                el = time.time() - t0
                print(f"  step {step}/{total} loss {loss.item():.4f}  {el/60:.1f} 分钟（剩余约 {el/step*(total-step)/60:.0f} 分钟）", flush=True)
            over_budget = (time.time() - t0) / 60 > args.time_budget_min
            if step in evals or step >= total or over_budget:
                p = predict(dev)
                a = auroc([q for q, r in zip(p, dev) if r["y"] == 1], [q for q, r in zip(p, dev) if r["y"] == 0])
                print(f"  开发集 AUROC {a:.4f}（step {step}）", flush=True)
                if a > best:
                    best = a
                    best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            if step >= total or over_budget:
                if over_budget:
                    print(f"::warning title=诗词分类器::超出时间预算，在第 {step} 步停止", flush=True)
                done = True
                break

    model.load_state_dict(best_state)
    ph = predict(test_h)
    pa = predict(test_a)
    pc = predict(classic_eval)
    pu = predict(user_test)
    ps = predict(story)
    pg = predict(gen_test)
    pcp = predict(cp_test) if cp_test else []
    pyq = predict(getattr(load_classic, "yuanqu_eval", []))
    plit = predict(lit_test) if lit_test else []
    lit_by = {}
    for p_, r in zip(plit, lit_test):
        lit_by.setdefault(r["model"], []).append(p_)
    pcp_ai = [p for p, r in zip(pg, gen_test) if r["genre"] == "duilian"]
    by_gen = {}
    for p, r in zip(pg, gen_test):
        by_gen.setdefault(r["model"], []).append(p)
        by_gen.setdefault("体裁-" + r["genre"], []).append(p)
    by = {}
    for p, r in zip(pa, test_a):
        by.setdefault(r["model"], []).append(p)
    thr = sorted(ph)[int(len(ph) * 0.95)]          # ChangAn 人写误判约 5% 的参考阈值
    rate = lambda v: round(sum(x >= thr for x in v) / len(v), 3) if v else None
    res = {"dev_auroc": round(best, 4), "test_auroc": round(auroc(pa, ph), 4),
           "test_auroc_by_model": {m: round(auroc(v, ph), 4) for m, v in by.items()},
           "changan_ai_vs_tang_song_300_auroc": round(auroc(pa, pc), 4),
           "user_models_vs_changan_human_auroc": round(auroc(pu, ph), 4) if pu else None,
           "user_models_vs_tang_song_300_auroc": round(auroc(pu, pc), 4) if pu else None,
           "story_poems_vs_tang_song_300_auroc": round(auroc(ps, pc), 4),
           "gen_cl_vs_changan_human_auroc": round(auroc(pg, ph), 4) if pg else None,
           "gen_cl_vs_tang_song_300_auroc": round(auroc(pg, pc), 4) if pg else None,
           "couplet_ai_vs_human_couplet_auroc": round(auroc(pcp_ai, pcp), 4) if pcp and pcp_ai else None,
           "gen_cl_vs_literary_auroc": round(auroc(pg, plit), 4) if pg and plit else None,
           "ai_couplets_vs_literary_couplets_auroc": round(auroc(pcp_ai, [p_ for p_, r in zip(plit, lit_test) if r["kind"] == "couplet"]), 4) if pcp_ai and plit else None,
           "n_couplet_test": [len(pcp_ai), len(pcp)],
           "reference_threshold": round(thr, 4),
           "at_threshold": {"changan_ai_caught": rate(pa), "user_models_caught": rate(pu), "story_caught": rate(ps),
                            "tang_song_300_flagged": rate(pc), "gen_cl_caught": rate(pg),
                            "human_couplets_flagged": rate(pcp), "ai_couplets_caught": rate(pcp_ai),
                            "yuanqu_flagged": rate(pyq), "literary_flagged": rate(plit),
                            "literary_flagged_by": {k: rate(v) for k, v in sorted(lit_by.items()) if len(v) >= 10},
                            "gen_cl_caught_by": {k: rate(v) for k, v in sorted(by_gen.items())}},
           "n_gen_cl_train_unique": len({r["text"] for r in gen_train}), "n_gen_cl_test": len(gen_test),
           "n_train": len(train), "n_test_human": len(ph), "n_test_ai": len(pa),
           "base": args.base, "epochs": args.epochs, "lr": args.lr, "steps": step,
           "minutes": round((time.time() - t0) / 60, 1)}
    print(json.dumps(res, ensure_ascii=False, indent=1), flush=True)
    print("::notice title=诗词分类器评估（没见过的作者、名篇与模型）::" + json.dumps(res, ensure_ascii=False), flush=True)
    if args.eval_model:
        return

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    model.half().save_pretrained(out, safe_serialization=True)
    tok.save_pretrained(out)
    (out / "training_result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    (out / "README.md").write_text(
        "# 诗词 AI 检测分类器\n\n由 tools/train_poetry2.py 在 ChangAn（ACL 2026，MIT）上微调 "
        f"{args.base} 得到，标签 0 = 人写、1 = AI。\n\n评估结果：\n\n```json\n"
        + json.dumps(res, ensure_ascii=False, indent=1) + "\n```\n", "utf-8")


if __name__ == "__main__":
    main()
