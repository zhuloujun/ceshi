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
    raw = Path(os.environ["PAYLOAD_FILE"]).read_text() if os.environ.get("PAYLOAD_FILE") else os.environ["PAYLOAD"]
    docs = json.loads(lzma.decompress(base64.b64decode(raw)).decode("utf-8"))
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
        for n in (s.get("reliability_notes") or []):
            if "逐字重复" in n or "the whole of" in n or "千问英文" in n or "参考文献核查" in n or "零宽" in n:
                print(f"::notice title={d['name']} 提示::{n[:300]}", flush=True)
        if s.get("refcheck"):
            print(f"::notice title={d['name']} 文献核查::{json.dumps(s['refcheck'], ensure_ascii=False)[:1500]}", flush=True)
        segs = []
        _cur = 0
        for g in res.get("segments", []):
            _k = (g.get("text") or "").strip()[:15]
            _o = d["text"].find(_k, max(0, _cur - 200)) if _k else -1
            if _o >= 0:
                _cur = _o + 1
            r = g.get("raw") or {}
            f = lambda k: "" if r.get(k) is None else f" {k.replace('classifier_', '').replace('classifier', 'cls')}{round(r[k] * 100)}"
            reg = {"zh_poetry": "po", "zh_classical": "cl", "en": "en"}.get(g.get("register"), "")
            segs.append(f"[{g['text'][:6].replace(chr(10), ' ')}]@{g.get('start')}+{g.get('chars')} {g.get('label') or g.get('kind')} p{round((g.get('prob') or g.get('ref_prob') or 0) * 100)} u{round((g.get('prob_unsmoothed') or 0) * 100)} b{g.get('block')} t{round((g.get('threshold') or 0) * 100)}"
                        f" o{_o} {reg}{f('classifier')}{f('classifier_zh2')}{f('classifier_en2')}{f('classifier_en3')}{f('classifier_en4')}"
                        + ("" if r.get("classifier_zh2") is None else f" z3{round(r['classifier_zh2'] * 1000)}")
                        + (f" P{g['partial'][0]}{g.get('ai_chars')}" if g.get("partial") else "")
                        + ("" if not g.get("partial") else " W" + ";".join(
                            f"{w['start']}+{w['chars']}{(w.get('flag') or '-')[0]}" for w in g.get("windows") or [] if w.get("flag"))))
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
        if os.environ.get("DEBUG_UNITS"):
            # 诊断：全文每个句子单元的分数（各文体分类器 + 句级标注模型），供离线模拟“作者切换”切分
            from app import hybrid
            units, cur2 = [], 0
            for g in res.get("segments", []):
                reg = g.get("register")
                if reg not in ("zh", "en", "zh_classical", "zh_poetry"):
                    continue
                gt = g.get("text") or ""
                o = d["text"].find(gt.strip()[:15], max(0, cur2 - 200))
                if o < 0:
                    continue
                cur2 = o + 1
                sp = hybrid.sentence_units(gt, reg, 12, 8)
                if not sp:
                    continue
                tx = [gt[a:b] for a, b in sp]
                regs = [reg] * len(tx)
                cols = []
                if reg == "en":
                    cols.append(eng.classify_en2(tx, regs))
                    cols.append(eng.cls_en4.predict(tx) if (eng.cls_en4 and eng.cls_en4.ready) else [None] * len(tx))
                elif reg == "zh":
                    cols.append(eng.classify_zh2(tx, regs))
                    cols.append(eng.classify(tx, regs))
                else:
                    cols.append(eng.classify(tx, regs))
                    cols.append(eng.classify_second(tx, regs))
                tg = {"en": eng.tag_en, "zh": eng.tag_zh, "zh_classical": eng.tag_zc, "zh_poetry": eng.tag_zp}.get(reg)
                cols.append(tg.span_probs(gt, sp) if (tg and tg.ready) else [None] * len(tx))
                q = lambda v: "-" if v is None else str(round(v * 1000))
                for k, (a, b) in enumerate(sp):
                    units.append(f"{o + a}+{b - a}{reg[3:4] or reg[0]}" + ":" + ":".join(q(c[k]) for c in cols))
            chunk = ""
            n_out = 0
            for u in units + [None]:
                if u is None or len(chunk) + len(u) + 1 > 3800:
                    if chunk:
                        n_out += 1
                        print(f"::notice title={d['name']} 单元 {n_out}::{chunk}", flush=True)
                    chunk = ""
                if u is not None:
                    chunk += (" " if chunk else "") + u
        print(f"{d['name']}: {time.time() - t0:.0f} 秒", flush=True)
        Path("/tmp/offline_results").mkdir(exist_ok=True)
        (Path("/tmp/offline_results") / f"{d['name']}.json").write_text(json.dumps(res, ensure_ascii=False), "utf-8")


if __name__ == "__main__":
    main()
