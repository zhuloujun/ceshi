"""对比几个版本的中文第二分类器（zh-model-compare.yml 调用）：
用户测试文档（环境变量 PAYLOAD）按网站分段规则分篇，加上仓库验收文档 eval_zh_user_docs.jsonl，
逐篇打印每个模型的整篇加权中位数（两段取较低）和各段得分。"""
import base64
import json
import lzma
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.segmenter import segment_text  # noqa: E402

DATA = Path(__file__).resolve().parent / "data"


def wmed(vals):
    if len(vals) == 2:
        return min(v for v, _ in vals)
    vals = sorted(vals)
    half, acc = sum(n for _, n in vals) / 2, 0
    for v, n in vals:
        acc += n
        if acc >= half:
            return v
    return vals[-1][0] if vals else None


def main():
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    torch.set_num_threads(4)
    tags = [t for t in os.environ["TAGS"].split(",") if t]
    models = {}
    for t in tags:
        p = f"/tmp/m/{t}/chinese-classifier"
        models[t] = (AutoTokenizer.from_pretrained(p), AutoModelForSequenceClassification.from_pretrained(p).float().eval())

    def score(tag, texts):
        tok, m = models[tag]
        out = []
        with torch.inference_mode():
            for i in range(0, len(texts), 16):
                enc = tok(texts[i:i + 16], truncation=True, max_length=384, padding=True, return_tensors="pt")
                out += torch.softmax(m(**enc).logits.float(), -1)[:, 1].tolist()
        return out

    works = []          # (名称, [段落文本])
    if os.getenv("PAYLOAD"):
        for d in json.loads(lzma.decompress(base64.b64decode(os.environ["PAYLOAD"])).decode("utf-8")):
            by = {}
            titles = {}
            for s in segment_text(d["text"]):
                if s.counted and s.register == "zh":
                    by.setdefault(s.block, []).append(s.text)
                    if s.title and s.block not in titles:
                        titles[s.block] = s.title
            for b, segs in by.items():
                works.append((f"{d['name']}·{titles.get(b, segs[0][:12])[:20]}", segs))
    by = {}
    for line in (DATA / "eval_zh_user_docs.jsonl").read_text("utf-8").split("\n"):
        if line.strip():
            r = json.loads(line)
            by.setdefault(f"验收·{r['doc'][:26]}（{'AI' if r['y'] else '真人'}）", []).append(r["text"])
    for name, paras in by.items():
        segs = [s.text for s in segment_text("\n\n".join(paras)) if s.counted and s.register == "zh"]
        if segs:
            works.append((name, segs))
    lines = []
    for name, segs in works:
        row = [name]
        for t in tags:
            ps = score(t, segs)
            row.append(f"{t.replace('chinese-classifier-', '')}={wmed(list(zip(ps, map(len, segs)))):.3f}"
                       f"[{' '.join(str(round(p * 100)) for p in ps)}]")
        lines.append(" ".join(row))
    for i in range(0, len(lines), 12):
        print("::notice title=中文模型对比 " + str(i // 12 + 1) + "::" + " ； ".join(lines[i:i + 12]), flush=True)


if __name__ == "__main__":
    main()
