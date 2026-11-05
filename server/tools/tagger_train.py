"""句级"作者标注"模型（人机混写定位）：给一篇文字里的每个词元标"人写 / AI 写"，按句子汇总后就知道哪几句是 AI 写的。

为什么需要（2026-11-05 用户 cs1105）：真实文章里 AI 写了几句、在哪、多长都没有规定；现有的检测模型都是按"整段"训练的，
拿来给一两句话打分时分数偏高、很不稳定（真人说明文平均 AI 率因此升了约 1 个百分点）。
做法与 SeqXGPT（Wang 等 EMNLP 2023）、HACo-Det（Su 等 ACL 2025）、LLMTrace（Findings EMNLP 2026）、M4GT-Bench 子任务 3 相同：
把同一批训练数据里的真人文章与 AI 文章按句子拆开，拼成"带标准答案的混写文章"，训练逐词元分类。

拼接方式（每篇不超过 max_len 个词元）：
  · 纯人写 25%、纯 AI 15%（让模型在没有混写时保持安静，防止冤枉真人）；
  · 人写文章中插入一段 AI（1–6 句）20%、AI 文章中插入一段人写 15%；
  · 人写 / AI 每 1–4 句交替 25%。
每一块取自同一篇来源文章的连续句子；训练 / 评估按来源文章划分（与整段分类器的划分一致），评估文章从不参与训练。

由 train_english.py / train_chinese.py 的 --tagger 参数调用：数据（含加密的真人语料、用户文档）与整段分类器完全相同。
"""
import json
import math
import random
import re
import time
from pathlib import Path

_EN = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“‘(])|(?<=[.!?][\"”’)])\s+(?=[A-Z])")
_ZH = re.compile(r"(?<=[。！？!?；;])(?![”’」』\"'）)])|(?<=[。！？!?；;][”’」』\"'）)])")


def split_sents(text: str, lang: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip() if lang == "en" else re.sub(r"\s+", "", text)
    pat = _EN if lang == "en" else _ZH
    out = [s.strip() for s in pat.split(text) if s and s.strip()]
    if lang == "en":
        return [s for s in out if len(s.split()) >= 4]
    return [s for s in out if len(s) >= 6]


def build_pools(rows, lang):
    """rows: [{text, y, src, split}] → {split: {0: [[句子…]…], 1: […]}}（每篇文章一组连续句子）。"""
    pools = {"train": {0: [], 1: []}, "test": {0: [], 1: []}}
    for r in rows:
        ss = split_sents(r["text"], lang)
        if len(ss) >= 2:
            pools["test" if r["split"] == "test" else "train"][int(r["y"])].append(ss)
    return pools


def _run(rnd, docs, k):
    d = rnd.choice(docs)
    if len(d) <= k:
        return list(d)
    i = rnd.randrange(0, len(d) - k + 1)
    return d[i:i + k]


def make_doc(rnd, pool, lang, budget):
    """返回 (文本, [(起, 止, 标签)])；budget 为英文词数 / 中文字数上限。"""
    size = (lambda s: len(s.split())) if lang == "en" else len
    sep = " " if lang == "en" else ""
    u = rnd.random()
    if u < 0.25:
        plan = [(0, 40)]
    elif u < 0.40:
        plan = [(1, 40)]
    elif u < 0.60:
        plan = [(0, rnd.randint(2, 8)), (1, rnd.randint(1, 6)), (0, rnd.randint(2, 8))]
    elif u < 0.75:
        plan = [(1, rnd.randint(2, 8)), (0, rnd.randint(1, 6)), (1, rnd.randint(2, 8))]
    else:
        y = rnd.randint(0, 1)
        plan = []
        for _ in range(12):
            plan.append((y, rnd.randint(1, 4)))
            y = 1 - y
    text, spans, used = "", [], 0
    for y, k in plan:
        for s in _run(rnd, pool[y], k):
            if used + size(s) > budget:
                break
            if text:
                text += sep
            a = len(text)
            text += s
            spans.append((a, len(text), y))
            used += size(s)
        if used >= budget * 0.9:
            break
    return text, spans


def train_tagger(rows, args, lang, out_dir):
    import torch
    from transformers import AutoModelForTokenClassification, AutoTokenizer, get_linear_schedule_with_warmup

    t0 = time.time()
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    rnd = random.Random(1105)
    pools = build_pools(rows, lang)
    for sp in ("train", "test"):
        print(f"句子池 {sp}：人写 {len(pools[sp][0])} 篇，AI {len(pools[sp][1])} 篇", flush=True)
    if not all(pools[sp][y] for sp in ("train", "test") for y in (0, 1)):
        raise SystemExit("句子池为空")
    budget = 240 if lang == "en" else 380
    tok = AutoTokenizer.from_pretrained(args.base)
    model = AutoModelForTokenClassification.from_pretrained(
        args.base, num_labels=2, id2label={0: "human", 1: "ai"}, label2id={"human": 0, "ai": 1}).to(dev)
    if args.grad_ckpt:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})

    def encode(docs):
        enc = tok([d[0] for d in docs], truncation=True, max_length=args.max_len, padding=True,
                  return_offsets_mapping=True, return_tensors="pt")
        offs = enc.pop("offset_mapping")
        labels = torch.full(offs.shape[:2], -100, dtype=torch.long)
        for b, (text, spans) in enumerate(docs):
            for t, (a, z) in enumerate(offs[b].tolist()):
                if z <= a:
                    continue
                for s0, s1, y in spans:
                    if s0 <= a < s1:
                        labels[b, t] = y
                        break
        return {k: v.to(dev) for k, v in enc.items()}, labels.to(dev), offs

    n_steps = int(getattr(args, "tagger_steps", 0) or 6000)
    bs = max(8, args.batch // 2)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    sch = get_linear_schedule_with_warmup(opt, int(0.06 * n_steps), n_steps)
    scaler = torch.amp.GradScaler("cuda", enabled=dev == "cuda")
    lossf = torch.nn.CrossEntropyLoss(ignore_index=-100, label_smoothing=args.label_smoothing)
    budget_s = args.time_budget_min * 60 * 0.85
    model.train()
    step = 0
    while step < n_steps and time.time() - t0 < budget_s:
        docs = [make_doc(rnd, pools["train"], lang, budget) for _ in range(bs)]
        enc, labels, _ = encode(docs)
        with torch.autocast(dev, enabled=dev == "cuda"):
            logits = model(**enc).logits
        loss = lossf(logits.float().view(-1, 2), labels.view(-1))
        opt.zero_grad()
        scaler.scale(loss).backward()
        scaler.unscale_(opt)
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        scaler.step(opt)
        scaler.update()
        sch.step()
        step += 1
        if step % 200 == 0:
            print(f"步 {step}/{n_steps} loss {loss.item():.4f}（{(time.time() - t0) / 60:.0f} 分钟）", flush=True)

    # 评估：没参与训练的来源文章拼成的混写文章（固定种子），按句子汇总词元概率
    model.eval()
    ev_rnd = random.Random(7)
    test_docs = [make_doc(ev_rnd, pools["test"], lang, budget) for _ in range(800)]
    sent_p, sent_y, pure_h = [], [], []
    with torch.inference_mode():
        for i in range(0, len(test_docs), 32):
            chunk = test_docs[i:i + 32]
            enc, _, offs = encode(chunk)
            with torch.autocast(dev, enabled=dev == "cuda"):
                pr = torch.softmax(model(**enc).logits.float(), -1)[..., 1].cpu()
            for b, (text, spans) in enumerate(chunk):
                o = offs[b].tolist()
                doc_ps = []
                for s0, s1, y in spans:
                    ps = [pr[b, t].item() for t, (a, z) in enumerate(o) if z > a and s0 <= a < s1]
                    if ps:
                        sent_p.append(sum(ps) / len(ps))
                        sent_y.append(y)
                        doc_ps.append((sent_p[-1], y))
                if doc_ps and all(y == 0 for _, y in doc_ps):
                    pure_h.append(sum(p >= 0.5 for p, _ in doc_ps) / len(doc_ps))

    def auroc(pos, neg):
        if not pos or not neg:
            return None
        allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
        r, rs = 0, 0.0
        for k, (v, l) in enumerate(allv, 1):
            if l:
                rs += k
        return (rs - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))
    pos = [p for p, y in zip(sent_p, sent_y) if y == 1]
    neg = sorted(p for p, y in zip(sent_p, sent_y) if y == 0)
    res = {"kind": "sentence-tagger", "lang": lang, "base": args.base, "steps": step,
           "minutes": round((time.time() - t0) / 60, 1), "test_sentences": len(sent_p),
           "test_auroc_all": round(auroc(pos, neg) or 0, 4),
           "sentence_acc_at_0.5": round(sum((p >= 0.5) == bool(y) for p, y in zip(sent_p, sent_y)) / max(len(sent_p), 1), 4),
           "pure_human_docs_flagged_share": round(sum(pure_h) / max(len(pure_h), 1), 4)}
    for k, q in (("1pct", 0.99), ("5pct", 0.95)):
        thr = neg[min(len(neg) - 1, int(q * len(neg)))] if neg else 0.5
        res[f"threshold_{k}"] = round(thr, 4)
        res[f"ai_caught_at_{k}"] = round(sum(p > thr for p in pos) / max(len(pos), 1), 4)
        res[f"human_flagged_at_{k}"] = round(sum(p > thr for p in neg) / max(len(neg), 1), 4)
    # 训练工作流读取的键（与整段分类器的结果文件同名，免得发布前的汇总步骤报错）
    for k in ("1pct", "5pct"):
        res[f"segments_at_{k}"] = {"ai_caught": res[f"ai_caught_at_{k}"], "human_flagged": res[f"human_flagged_at_{k}"]}
    res["doc_level"] = {"pure_human_docs_flagged_share": res["pure_human_docs_flagged_share"]}
    print("::notice title=句级标注模型评估::" + json.dumps(res, ensure_ascii=False), flush=True)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out)
    tok.save_pretrained(out)
    (out / "training_result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), "utf-8")
    return res
