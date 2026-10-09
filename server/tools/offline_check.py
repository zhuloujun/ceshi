"""离线验收（.github/workflows/offline-check.yml）：在 GitHub 的免费 CPU 上加载与线上相同的模型，检测用户测试文档。
不调用 Modal 线上服务，所以不产生 Modal 费用。输出格式与"临时验收"（adhoc-check.yml）相同：每篇作品的 AI 率、
每段开头几个字与关键得分（不打印原文）。

环境变量 PAYLOAD = base64(xz(JSON 列表 [{name, text, genre?}]))。
模型目录：部署工作流同样的步骤把专用分类器解压到 server/ 下（poetry-classifier、classical-classifier……）。
"""
import base64
import json
import lzma
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
for env, d in (("POETRY_CLASSIFIER_MODEL", "poetry-classifier"), ("CLASSICAL_CLASSIFIER_MODEL", "classical-classifier"),
               ("EN2_CLASSIFIER_MODEL", "english-classifier"), ("ZH2_CLASSIFIER_MODEL", "chinese-classifier"),
               ("EN3_CLASSIFIER_MODEL", "english-doc-classifier"), ("EN4_CLASSIFIER_MODEL", "english-doc-classifier-2")):
    p = HERE / d
    if (p / "config.json").exists() or (p / "ensemble.json").exists():
        os.environ[env] = str(p)
os.environ.setdefault("OBSERVER_MODEL", "Qwen/Qwen2.5-0.5B")
os.environ.setdefault("PERFORMER_MODEL", "Qwen/Qwen2.5-0.5B-Instruct")
os.environ.setdefault("CLASSIFIER_MODEL", "yuchuantian/AIGC_detector_zhv3")
os.environ.setdefault("EN_CLASSIFIER_MODEL", "desklib/ai-text-detector-v1.01")
os.environ["USER_CALIBRATION_FILE"] = "/tmp/none_user_calibration.json"
os.environ["USER_LABELS_FILE"] = "/tmp/none_user_labels.json"

from app.engine import Engine  # noqa: E402


def main():
    docs = json.loads(lzma.decompress(base64.b64decode(os.environ["PAYLOAD"])).decode("utf-8"))
    eng = Engine()
    eng.load_all()
    print("模型：", json.dumps(eng.status(), ensure_ascii=False)[:1500], flush=True)
    for d in docs:
        t0 = time.time()
        res = eng.analyze(d["text"], "full", True, True, None, d.get("genre", "auto"))
        s = res.get("summary") or {}
        works = " ； ".join(f"{w['title'][:24]} {'—' if w.get('ai_rate') is None else round(w['ai_rate'] * 100)}%"
                           for w in res.get("works", []))
        print(f"::warning title={d['name']} 分篇::AI 率 {s.get('ai_rate')} · {works}"[:4000], flush=True)
        segs = []
        for g in res.get("segments", []):
            r = g.get("raw") or {}
            f = lambda k: "" if r.get(k) is None else f" {k.replace('classifier_', '').replace('classifier', 'cls')}{round(r[k] * 100)}"
            reg = {"zh_poetry": "po", "zh_classical": "cl", "en": "en"}.get(g.get("register"), "")
            segs.append(f"[{g['text'][:6].replace(chr(10), ' ')}] {g.get('label') or g.get('kind')} p{round((g.get('prob') or g.get('ref_prob') or 0) * 100)}"
                        f" {reg}{f('classifier')}{f('classifier_zh2')}{f('classifier_en2')}{f('classifier_en3')}{f('classifier_en4')}")
        # 注释条数有上限：每篇文档的分段明细合并成尽量少的几条
        chunk, out = "", []
        for x in segs:
            if len(chunk) + len(x) + 3 > 3800:
                out.append(chunk)
                chunk = ""
            chunk += (" | " if chunk else "") + x
        if chunk:
            out.append(chunk)
        for i, c in enumerate(out):
            print(f"::notice title={d['name']} 分段 {i + 1}::{c}", flush=True)
        print(f"{d['name']}: {time.time() - t0:.0f} 秒", flush=True)
        Path("/tmp/offline_results").mkdir(exist_ok=True)
        (Path("/tmp/offline_results") / f"{d['name']}.json").write_text(json.dumps(res, ensure_ascii=False), "utf-8")


if __name__ == "__main__":
    main()
