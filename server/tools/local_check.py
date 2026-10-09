"""在 GitHub 的免费机器上跑完整检测（与线上相同的模型和内置校准），不调用 Modal、不花 Modal 的钱。
输入与 adhoc-check.yml 相同：环境变量 PAYLOAD = base64(xz(JSON [{name, text, genre?}]))。
输出：每篇作品的 AI 率、每段的关键得分（只打印每段开头几个字，不打印原文）。
注意：不含线上"用我的标注校准"的个人校准，只用内置校准。"""
import base64
import json
import lzma
import os
import sys
from pathlib import Path

S = Path(__file__).resolve().parent.parent
for env, d in (("POETRY_CLASSIFIER_MODEL", "poetry-classifier"), ("CLASSICAL_CLASSIFIER_MODEL", "classical-classifier"),
               ("EN2_CLASSIFIER_MODEL", "english-classifier"), ("ZH2_CLASSIFIER_MODEL", "chinese-classifier"),
               ("EN3_CLASSIFIER_MODEL", "english-doc-classifier"), ("EN4_CLASSIFIER_MODEL", "english-doc-classifier-2")):
    p = S / d
    os.environ.setdefault(env, str(p) if ((p / "config.json").exists() or (p / "ensemble.json").exists()) else "")
os.environ.setdefault("OBSERVER_MODEL", "Qwen/Qwen2.5-0.5B")
os.environ.setdefault("PERFORMER_MODEL", "Qwen/Qwen2.5-0.5B-Instruct")
os.environ.setdefault("CLASSIFIER_MODEL", "yuchuantian/AIGC_detector_zhv3")
os.environ.setdefault("EN_CLASSIFIER_MODEL", "desklib/ai-text-detector-v1.01")
os.environ.setdefault("USER_CALIBRATION_FILE", "/tmp/none_user_cal.json")
os.environ.setdefault("USER_LABELS_FILE", "/tmp/none_user_labels.json")
os.environ.setdefault("MAX_TEXT_CHARS", "400000")
sys.path.insert(0, str(S))
from app.engine import Engine  # noqa: E402

docs = json.loads(lzma.decompress(base64.b64decode(os.environ["PAYLOAD"])).decode("utf-8"))
eng = Engine()
eng.load_all()
print("::notice title=模型::" + json.dumps({k: os.environ.get(k) for k in os.environ if k.endswith("_MODEL")}, ensure_ascii=False))
for d in docs:
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
        segs.append(f"[{g['text'][:6].replace(chr(10), ' ')}] {g.get('label') or g.get('kind')} p{round((g.get('prob') or g.get('ref_prob') or 0) * 100)} {reg}"
                    f"{f('classifier')}{f('classifier_zh2')}{f('classifier_en2')}{f('classifier_en3')}{f('classifier_en4')}")
    for i in range(0, len(segs), 30):
        print(f"::notice title={d['name']} 分段 {i // 30 + 1}::" + " | ".join(segs[i:i + 30]), flush=True)
    Path("/tmp/local_check").mkdir(exist_ok=True)
    Path(f"/tmp/local_check/{d['name']}.json").write_text(json.dumps(res, ensure_ascii=False), "utf-8")
