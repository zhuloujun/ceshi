"""训练前去掉"AI 照抄的古人原文"：用户上传的 AI 文档常把《陋室铭》《记承天寺夜游》《陌上桑》这类名篇整段抄进来。
这些文字其实是人写的，标成 AI 去训练，模型会把真人名篇也认成 AI。

做法与网站检测时识别"名篇原文"相同（engine.py 的 FAMOUS_PPL_CLASSICAL）：语言模型 Qwen2.5-0.5B-Instruct 逐字背过的文字
困惑度极低。评估集里 1300 多段 AI 诗词文言困惑度低于 7 的只有 2 段，而《唐诗三百首》《宋词三百首》约 35% 低于 7。
只过滤标为 AI（y=1）的段落；人写段落不动。没有 GPU / 下载不了模型时原样返回（打印提示，不影响训练）。
"""
import math
import os


def drop_memorized(rows, threshold=7.0, model="Qwen/Qwen2.5-0.5B-Instruct", max_tokens=256, text_key="text"):
    if not threshold or threshold <= 0:
        return rows
    targets = [i for i, r in enumerate(rows) if int(r.get("y", 0)) == 1]
    if not targets:
        return rows
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        tok = AutoTokenizer.from_pretrained(model)
        lm = AutoModelForCausalLM.from_pretrained(model, torch_dtype=torch.float16 if dev == "cuda" else torch.float32).to(dev).eval()
    except Exception as e:  # noqa: BLE001
        print(f"名篇原文过滤跳过（加载语言模型失败：{e}）", flush=True)
        return rows
    drop, examples = set(), []
    with torch.no_grad():
        for i in targets:
            t = rows[i].get(text_key) or rows[i].get("t") or ""
            ids = tok(t, return_tensors="pt", truncation=True, max_length=max_tokens).input_ids.to(dev)
            if ids.shape[1] < 8:
                continue
            loss = lm(ids, labels=ids).loss.item()
            if math.exp(loss) < threshold:
                drop.add(i)
                if len(examples) < 8:
                    examples.append(t[:24].replace("\n", " "))
    del lm
    if dev == "cuda":
        torch.cuda.empty_cache()
    print(f"名篇原文过滤：{len(targets)} 段 AI 样本中 {len(drop)} 段困惑度 < {threshold}（疑似照抄古人原文），已去掉。例：{examples}", flush=True)
    return [r for i, r in enumerate(rows) if i not in drop]


def threshold_from_env(default=7.0):
    try:
        return float(os.getenv("MEMO_FILTER_PPL", default))
    except ValueError:
        return default
