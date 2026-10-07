"""有监督分类器。
- 中文：MPU 检测模型 yuchuantian/AIGC_detector_zhv3
  （Tian et al., "Multiscale Positive-Unlabeled Detection of AI-Generated Texts", ICLR 2024）。
- 英文：desklib/ai-text-detector-v1.01（DeBERTa-v3-large，用 RAID 基准训练，发布时位列 RAID 排行榜首位，MIT 许可）。"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path

from .. import config

log = logging.getLogger("classifier")

_AI_WORDS = re.compile(r"(^|[^a-z])(ai|machine|gpt|chatgpt|generated|fake|llm|aigc|robot)([^a-z]|$)", re.I)
_HUMAN_WORDS = re.compile(r"(human|real|人类|人工)", re.I)


class Classifier:
    def __init__(self, model_name: str | None = None, max_tokens: int | None = None):
        self.model_name = model_name or config.CLASSIFIER_MODEL
        self.max_tokens = max_tokens or config.CLS_MAX_TOKENS
        self.ready = False
        self.error: str | None = None
        self.ai_index = 1
        self.labels: dict = {}
        self._lock = threading.Lock()

    def load(self):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.torch = torch
        # 模型目录里有 ensemble.json 时是"多版本合议"：几个同底座（同一分词器）的版本各自打分，取对数几率的平均。
        # 例：文言分类器 v5 = v4（新增千问古典体裁数据）+ v3（对 Kimi / 文心等旧来源更稳），比任一单版的最差来源都好
        ens = Path(self.model_name) / "ensemble.json"
        names = ([str(Path(self.model_name) / m) for m in json.loads(ens.read_text("utf-8"))["members"]]
                 if ens.exists() else [self.model_name])
        self.tok = AutoTokenizer.from_pretrained(names[0])
        self.models = [AutoModelForSequenceClassification.from_pretrained(n).eval() for n in names]
        self.model = self.models[0]
        self.labels = {int(k): str(v) for k, v in self.model.config.id2label.items()}
        self.ai_index = self._resolve_ai_index()
        log.info("classifier labels=%s ai_index=%s members=%d", self.labels, self.ai_index, len(self.models))
        self.ready = True

    def _resolve_ai_index(self) -> int:
        setting = config.CLASSIFIER_AI_LABEL.strip()
        if setting and setting.lower() != "auto":
            if setting.isdigit():
                return int(setting)
            for i, name in self.labels.items():
                if name.lower() == setting.lower():
                    return i
        for i, name in self.labels.items():
            if _AI_WORDS.search(name) and not _HUMAN_WORDS.search(name):
                return i
        for i, name in self.labels.items():
            if _HUMAN_WORDS.search(name) and len(self.labels) == 2:
                return 1 - i
        return 1 if len(self.labels) > 1 else 0

    def predict(self, texts: list[str], batch_size: int = 8) -> list[float]:
        torch = self.torch
        out: list[float] = []
        with self._lock, torch.inference_mode():
            for i in range(0, len(texts), batch_size):
                enc = self.tok(texts[i:i + batch_size], truncation=True, max_length=self.max_tokens,
                               padding=True, return_tensors="pt")
                models = getattr(self, "models", None) or [self.model]
                lo = None
                for m in models:
                    p = torch.softmax(m(**enc).logits.float(), dim=-1)[:, self.ai_index].clamp(1e-6, 1 - 1e-6)
                    x = torch.log(p) - torch.log1p(-p)
                    lo = x if lo is None else lo + x
                out.extend(torch.sigmoid(lo / len(models)).tolist())
        return out


class DesklibClassifier(Classifier):
    """desklib/ai-text-detector-v1.01：DeBERTa-v3 + 平均池化 + 单输出（sigmoid 即 AI 概率）。
    模型卡给出的是自定义类，这里按模型卡的结构实现。"""

    def load(self):
        import torch
        import torch.nn as nn
        from transformers import AutoConfig, AutoModel, AutoTokenizer, PreTrainedModel

        class DesklibAIDetectionModel(PreTrainedModel):
            config_class = AutoConfig

            def __init__(self, cfg):
                super().__init__(cfg)
                self.model = AutoModel.from_config(cfg)
                self.classifier = nn.Linear(cfg.hidden_size, 1)
                self.post_init()

            def forward(self, input_ids, attention_mask=None):
                hidden = self.model(input_ids, attention_mask=attention_mask)[0]
                mask = attention_mask.unsqueeze(-1).expand(hidden.size()).float()
                pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
                return self.classifier(pooled)

        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(self.model_name)
        self.model = DesklibAIDetectionModel.from_pretrained(self.model_name).eval()
        self.labels = {0: "human", 1: "ai"}
        self.ai_index = 1
        self.ready = True
        log.info("english classifier loaded: %s", self.model_name)

    def predict(self, texts: list[str], batch_size: int = 4) -> list[float]:
        torch = self.torch
        out: list[float] = []
        with self._lock, torch.inference_mode():
            for i in range(0, len(texts), batch_size):
                enc = self.tok(texts[i:i + batch_size], truncation=True, max_length=self.max_tokens,
                               padding=True, return_tensors="pt")
                logits = self.model(enc["input_ids"], attention_mask=enc["attention_mask"])
                out.extend(torch.sigmoid(logits.float()).view(-1).tolist())
        return out


def make_english_classifier():
    """按模型名选择实现：desklib 用自定义结构，其余按普通的序列分类模型加载。"""
    name = config.EN_CLASSIFIER_MODEL
    if "desklib" in name.lower():
        return DesklibClassifier(name, config.EN_CLS_MAX_TOKENS)
    return Classifier(name, config.EN_CLS_MAX_TOKENS)
