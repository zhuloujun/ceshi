"""从公开基准 RAID（liamdugan/raid，Dugan et al., ACL 2024）取样，生成离线验收用的 PAYLOAD 文件。

RAID 有 11 个领域（abstracts = arXiv 论文摘要、recipes = 菜谱、reviews、news、books……），真人原文与
11 个模型（gpt4、chatgpt、llama-chat、mistral-chat、cohere-chat……）按同一标题生成的文字。这里只取没有对抗攻击
（attack = none）的样本，通过 Hugging Face datasets-server 的 filter 接口按条件取，不下载整个数据集。

用法（离线验收工作流里调用）：
  python tools/fetch_raid.py "abstracts:human,gpt4,chatgpt,llama-chat:40;recipes:human,gpt4:30" 0/1 /tmp/payload.txt
模型名后加 @攻击名 取对抗攻击过的样本，如 gpt4@paraphrase、chatgpt@homoglyph、human@whitespace
（RAID 的攻击：homoglyph、zero_width_space、whitespace、upper_lower、insert_paragraphs、article_deletion、
number、alternative_spelling、perplexity_misspelling、synonym、paraphrase）。
第二个参数是分片 i/n：同一份取样分成 n 份，这次只检测第 i 份（多个工作流并行跑）。
"""
import base64
import json
import lzma
import random
import sys
import time
import urllib.parse
import urllib.request

API = "https://datasets-server.huggingface.co/filter"


def fetch(domain, model, n, seed=29, attack="none"):
    where = f"\"domain\"='{domain}' AND \"model\"='{model}' AND \"attack\"='{attack}'"
    rows, offset = [], 0
    # 先取一大页，再按固定随机种子抽 n 条（每次结果一样，前后版本可比）
    while len(rows) < max(n * 3, 60) and offset < 2000:
        q = urllib.parse.urlencode({"dataset": "liamdugan/raid", "config": "raid", "split": "train",
                                    "where": where, "offset": offset, "length": 100})
        for k in range(8):
            try:
                time.sleep(2)                                    # datasets-server 有频率限制
                with urllib.request.urlopen(f"{API}?{q}", timeout=180) as r:
                    page = json.loads(r.read())
                break
            except Exception as e:  # noqa: BLE001
                print(f"retry {domain}/{model} {offset}: {e}", flush=True)
                time.sleep(15 * (k + 1))
        else:
            print(f"::warning title=RAID 取样失败::{domain}/{model} offset {offset}", flush=True)
            break
        got = [x["row"] for x in page.get("rows", [])]
        if not got:
            break
        rows += got
        offset += 100
    rows = [r for r in rows if len((r.get("generation") or "").split()) >= 80]
    random.Random(seed).shuffle(rows)
    return rows[:n]


def main():
    spec, shard, out = sys.argv[1], sys.argv[2], sys.argv[3]
    i, n = (int(x) for x in shard.split("/"))
    items = []
    for part in spec.split(";"):
        domain, models, k = part.split(":")
        for model in models.split(","):
            model, _, attack = model.partition("@")
            attack = attack or "none"
            rows = fetch(domain, model, int(k), attack=attack)
            print(f"::notice title=RAID 取样::{domain}/{model}@{attack}: {len(rows)} 条", flush=True)
            if attack != "none":
                model = f"{model}-{attack}"
            for j, r in enumerate(rows):
                title = (r.get("title") or "").strip()
                text = r["generation"].strip()
                if domain == "abstracts" and title:
                    text = f"{title}\n\nAbstract\n{text}"
                items.append({"name": f"raid_{domain}_{model}_{j:03d}", "text": text})
    items = items[i::n]
    print(f"本分片 {len(items)} 篇", flush=True)
    with open(out, "w") as f:
        f.write(base64.b64encode(lzma.compress(json.dumps(items, ensure_ascii=False).encode())).decode())


if __name__ == "__main__":
    main()
