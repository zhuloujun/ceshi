"""句子文体判别（只判文体，不判作者）：一句话更像散曲 / 诗词 / 文言，还是现代白话。

detect_register 是规则判断，对夹在现代文里的散曲句子（“记当年、呵冻手题诗在灞陵”“呀，你本是补天余剩”）
多判为现代汉语，于是这些句子只用现代文模型打分（2026-11-19 cs1119、2026-11-20 cs1120：AI 散曲夹在真人现代文中间，
被现代文模型逐句判为人写）。这里用字 / 字二元组的逻辑回归给出“古典文体”概率，供 engine 的逐句分流使用。
训练数据：用户语料里的文言、诗词、对联、散曲（人写与 AI 各半）对现代白话（人写与 AI 各半），按句切分；
生成脚本 tools/train_regstyle.py。文件 app/data/regstyle.json.xz：{b: 截距, w: {特征: 权重}}。
"""
import json
import lzma
import math
import re
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "data" / "regstyle.json.xz"
_M = None
_CJ = re.compile(r"[一-鿿]")


def _load():
    global _M
    if _M is None:
        try:
            _M = json.loads(lzma.decompress(_PATH.read_bytes()).decode("utf-8"))
        except Exception:  # noqa: BLE001
            _M = {}
    return _M


def classical_prob(text: str):
    m = _load()
    if not m:
        return None
    s = "".join(c if (_CJ.match(c) or c in "，、；") else " " for c in text)
    feats = set(s) | {s[i:i + 2] for i in range(len(s) - 1)}
    w = m["w"]
    z = m["b"] + sum(w.get(f, 0.0) for f in feats)
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
