"""句级“作者标注”模型（tools/tagger_train.py 训练）：逐词元给出“AI 写”的概率，按句子区间取平均。
用于人机混写切分（app/hybrid.py）：比拿整段分类器给一两句话打分稳定得多。"""
from __future__ import annotations

import threading


class SentenceTagger:
    def __init__(self, model_dir: str, max_tokens: int = 512, stride: int = 128):
        self.model_dir = model_dir
        self.max_tokens = max_tokens
        self.stride = stride
        self.ready = False
        self.error = None
        self._lock = threading.Lock()

    def load(self):
        import torch
        from transformers import AutoModelForTokenClassification, AutoTokenizer
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(self.model_dir)
        self.model = AutoModelForTokenClassification.from_pretrained(self.model_dir).eval()
        self.ready = True

    def span_probs(self, text: str, spans: list[tuple[int, int]]) -> list[float | None]:
        """每个字符区间 [起, 止) 的平均 AI 概率；长文按词元窗口滑动（重叠部分取平均）。"""
        torch = self.torch
        enc = self.tok(text, add_special_tokens=False, return_offsets_mapping=True)
        ids, offs = enc["input_ids"], enc["offset_mapping"]
        if not ids:
            return [None] * len(spans)
        n = len(ids)
        acc, cnt = [0.0] * n, [0] * n
        win = self.max_tokens - 2
        starts = list(range(0, max(1, n - win + self.stride), win - self.stride)) or [0]
        cls_id, sep_id = self.tok.cls_token_id, self.tok.sep_token_id
        with self._lock, torch.inference_mode():
            for a in starts:
                chunk = ids[a:a + win]
                inp = ([cls_id] if cls_id is not None else []) + chunk + ([sep_id] if sep_id is not None else [])
                pr = torch.softmax(self.model(input_ids=torch.tensor([inp])).logits[0].float(), -1)[:, 1].tolist()
                off = 1 if cls_id is not None else 0
                for k in range(len(chunk)):
                    acc[a + k] += pr[k + off]
                    cnt[a + k] += 1
                if a + win >= n:
                    break
        out = []
        for s0, s1 in spans:
            ps = [acc[k] / cnt[k] for k, (x, y) in enumerate(offs) if cnt[k] and y > x and s0 <= x < s1]
            out.append(sum(ps) / len(ps) if ps else None)
        return out
