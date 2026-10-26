"""候选检测器擂台：把 Hugging Face 上公开的"神经网络检测器"（微调过的分类模型）放到本项目自己的考卷上实测。

只用 GitHub 的免费 CPU，不唤醒 Modal。对每个候选模型、每份考卷给出：
  1. 单独使用的 AUROC，以及在 5% / 1% 误判率下能认出多少 AI（阈值在校准集上定，再用到考卷上）；
  2. 加进本项目现有的逻辑回归组合后（与已提交的 scores/*.json 里同一批段落的现有特征拼在一起），
     考卷上的 AUROC 与 5% 误判率下的检出率是否提高——这才是"融入本项目有没有用"的判断依据。

考卷段落的构造与 evaluate.py 的打分阶段完全一致（同样的抽样、同样的取段、同样的最短长度），
所以候选模型的得分可以和 scores/*.json 里的现有特征逐行对齐；对不齐的考卷只报单独成绩。

用法（在 server 目录下）：
  python tools/bench/bench_detectors.py --lang zh --models Juner/AI-generated-text-detection,... --data /tmp/nlpcc
  python tools/bench/bench_detectors.py --lang en --models Hello-SimpleAI/chatgpt-detector-roberta,... --mage-dir /tmp/mage
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import numpy as np

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS.parent))
sys.path.insert(0, str(TOOLS))
import evaluate as ev  # noqa: E402
from app import config  # noqa: E402

PARTS = {
    "zh": {"fit": ["cal1", "cal2"], "eval": ["test", "csl", "tests"]},
    "en": {"fit": ["en_cal1", "en_cal2", "en_gen_fit"], "eval": ["en_gen_test", "en_ood", "en_para", "en_user"]},
}
FEATS = {   # 与线上校准一致的现有特征
    "zh": ["fastdetect", "binoculars", "classifier"],
    "en": ["fastdetect", "binoculars", "classifier", "classifier_en2"],
}
AI_WORDS = re.compile(r"fake|machine|generated|chatgpt|gpt|ai\b|^ai|llm|label_1|^1$", re.I)


def build_segments(args, part):
    rows = ev.build_part(args, part)
    prof = ev.PART_PROFILE[part]
    segs, labels = [], []
    for r in rows:
        sg = ev.to_segment(r["text"], prof)
        min_len = {"zh_classical": 60, "zh_poetry": 16}.get(prof, config.SEGMENT_MIN_CHARS)
        if r["y"] == 1 and r.get("model", "").startswith("repo-ai"):
            min_len = 20
        if sg and len(sg) >= min_len:
            segs.append(sg); labels.append(r["y"])
    return segs, labels


def load_scored(part):
    f = TOOLS / "scores" / f"{part}.json"
    return json.loads(f.read_text("utf-8")) if f.exists() else None


class Lexicon:
    """数据统计出的"AI 腔"词表（tools/bench/lexicon_*.json，词 → 对数几率权重）：段落得分 = 命中词的平均权重。"""
    def __init__(self, path):
        self.w = json.loads((Path(__file__).resolve().parent / path).read_text("utf-8"))
        self.labels, self.ai_idx = {1: "lexicon"}, 1

    def score(self, texts, bs=0):
        out = []
        for t in texts:
            w = re.findall(r"[a-z]+(?:'[a-z]+)?", t.lower().replace("’", "'"))
            terms = w + [a + " " + b for a, b in zip(w, w[1:])]
            v = [self.w[x] for x in terms if x in self.w]
            m = sum(v) / max(len(w), 1) * 4
            out.append(1 / (1 + np.exp(-m)))
        return out


class Cand:
    def __init__(self, name):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(name)
        self.model = AutoModelForSequenceClassification.from_pretrained(name).eval()
        id2 = {int(k): v for k, v in (self.model.config.id2label or {}).items()}
        n = self.model.config.num_labels
        hits = [i for i, v in id2.items() if AI_WORDS.search(str(v)) and not re.search(r"human|real", str(v), re.I)]
        self.ai_idx = hits[0] if hits else (1 if n > 1 else 0)
        self.labels = id2
        self.max_len = min(512, getattr(self.tok, "model_max_length", 512) or 512)

    def score(self, texts, bs=8):
        out = []
        with self.torch.inference_mode():
            for i in range(0, len(texts), bs):
                enc = self.tok(texts[i:i + bs], truncation=True, max_length=self.max_len, padding=True, return_tensors="pt")
                logits = self.model(**enc).logits.float()
                if logits.shape[-1] == 1:
                    p = self.torch.sigmoid(logits[:, 0])
                else:
                    p = self.torch.softmax(logits, -1)[:, self.ai_idx]
                out += p.tolist()
        return out


def auroc(y, s):
    y, s = np.asarray(y), np.asarray(s, float)
    pos, neg = s[y == 1], s[y == 0]
    if not len(pos) or not len(neg):
        return None
    return float(ev.auroc(list(pos), list(neg)))


def thr_at(y, s, fpr):
    neg = np.sort(np.asarray(s, float)[np.asarray(y) == 0])
    if not len(neg):
        return None
    return float(neg[min(len(neg) - 1, int(np.ceil((1 - fpr) * len(neg))) - 1)]) + 1e-9


def rates(y, s, t):
    y, s = np.asarray(y), np.asarray(s, float)
    tp = float((s[y == 1] > t).mean()) if (y == 1).any() else None
    fp = float((s[y == 0] > t).mean()) if (y == 0).any() else None
    return tp, fp


def lr_fit_predict(Xf, yf, Xe):
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_predict
    from sklearn.preprocessing import StandardScaler
    sc = StandardScaler().fit(Xf)
    m = LogisticRegression(max_iter=2000, C=1.0)
    oof = cross_val_predict(m, sc.transform(Xf), yf, cv=5, method="predict_proba")[:, 1]
    m.fit(sc.transform(Xf), yf)
    return oof, m.predict_proba(sc.transform(Xe))[:, 1] if len(Xe) else np.array([])


def feat_matrix(scored, feats, extra=None):
    X = []
    for i, r in enumerate(scored):
        v = [r["s"].get(f) for f in feats]
        if any(x is None for x in v):
            v = [0.0 if x is None else x for x in v]
        if extra is not None:
            v.append(extra[i])
        X.append(v)
    return np.asarray(X, float)


def logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["zh", "en"], required=True)
    ap.add_argument("--models", required=True)
    ap.add_argument("--data"); ap.add_argument("--mage-dir")
    ap.add_argument("--out", default="/tmp/bench")
    for k, v in (("n_cal", 800), ("n_test", 400), ("n_csl", 240), ("n_en_cal", 600), ("n_en_test", 300)):
        ap.add_argument("--" + k.replace("_", "-"), type=int, default=v)
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    P = PARTS[args.lang]
    segs = {p: build_segments(args, p) for p in P["fit"] + P["eval"]}
    scored = {p: load_scored(p) for p in segs}
    aligned = {p: scored[p] is not None and len(scored[p]) == len(segs[p][0])
               and [r["y"] for r in scored[p]] == segs[p][1] for p in segs}
    print("考卷段数与已提交得分对齐情况：", {p: (len(segs[p][0]), aligned[p]) for p in segs}, flush=True)
    report = {}
    for name in [m.strip() for m in args.models.split(",") if m.strip()]:
        t0 = time.time()
        try:
            c = Lexicon(name.split(":", 1)[1]) if name.startswith("lexicon:") else Cand(name)
        except Exception as e:  # noqa: BLE001
            print(f"::warning title={name} 加载失败::{str(e)[:300]}", flush=True)
            continue
        sc = {p: c.score(segs[p][0]) for p in segs}
        (out / (name.replace("/", "__") + ".json")).write_text(json.dumps(sc), "utf-8")
        yf = sum((segs[p][1] for p in P["fit"]), [])
        sf = sum((sc[p] for p in P["fit"]), [])
        a_fit = auroc(yf, sf)
        flip = a_fit is not None and a_fit < 0.5           # 标签方向在校准集上确定，不看考卷
        if flip:
            sc = {p: [1 - v for v in sc[p]] for p in sc}; sf = [1 - v for v in sf]
        t5, t1 = thr_at(yf, sf, 0.05), thr_at(yf, sf, 0.01)
        rep = {"labels": c.labels, "ai_label": c.labels.get(c.ai_idx), "flipped": flip, "fit_auroc": auroc(yf, sf), "parts": {}}
        # 组合：现有特征 vs 现有特征 + 候选（候选取对数几率）
        fit_ok = all(aligned[p] for p in P["fit"])
        if fit_ok:
            Xf0 = np.vstack([feat_matrix(scored[p], FEATS[args.lang]) for p in P["fit"]])
            Xf1 = np.vstack([feat_matrix(scored[p], FEATS[args.lang], logit(sc[p])) for p in P["fit"]])
        for p in P["eval"]:
            y, s = segs[p][1], sc[p]
            d = {"n_ai": y.count(1), "n_human": y.count(0), "auroc": auroc(y, s),
                 "caught@5%": rates(y, s, t5)[0], "fp@5%": rates(y, s, t5)[1],
                 "caught@1%": rates(y, s, t1)[0], "fp@1%": rates(y, s, t1)[1]}
            if fit_ok and aligned[p]:
                for tag, Xf, Xe in (("base", Xf0, feat_matrix(scored[p], FEATS[args.lang])),
                                    ("plus", Xf1, feat_matrix(scored[p], FEATS[args.lang], logit(s)))):
                    oof, pe = lr_fit_predict(Xf, np.asarray(yf), Xe)
                    tt = thr_at(yf, oof, 0.05)
                    d[f"combo_{tag}_auroc"] = auroc(y, pe)
                    d[f"combo_{tag}_caught@5%"], d[f"combo_{tag}_fp@5%"] = rates(y, pe, tt)
            rep["parts"][p] = d
        report[name] = rep
        print(f"::notice title={name}（{time.time() - t0:.0f} 秒）::" + json.dumps(
            {"fit_auroc": round(rep["fit_auroc"] or 0, 4), "flipped": flip, **{p: {k: (round(v, 4) if isinstance(v, float) else v)
             for k, v in d.items() if k in ("auroc", "caught@5%", "fp@5%", "combo_base_auroc", "combo_plus_auroc",
                                            "combo_base_caught@5%", "combo_plus_caught@5%")} for p, d in rep["parts"].items()}},
            ensure_ascii=False), flush=True)
    (out / f"report_{args.lang}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
