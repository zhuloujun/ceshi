"""零样本检测：Binoculars（ICML 2024）与 Fast-DetectGPT（ICLR 2024）。

两种方法都只需要"观察者"(基础模型) 和 "执行者"(对话模型) 各做一次前向计算，
因此共用同一次推理结果，一起算出。

记号：对文本的第 t 个位置，
  O_t = softmax(observer 的 logits)          观察者预测的下一个字的分布
  L_t = log_softmax(performer 的 logits)     执行者的对数概率
  x_t = 实际出现的下一个 token

Binoculars（按官方实现 ahans30/Binoculars）：
  ppl   = mean_t( -L_t[x_t] )                执行者对原文的对数困惑度
  x_ppl = mean_t( -Σ_v O_t[v] · L_t[v] )      观察者分布与执行者之间的交叉困惑度
  B     = ppl / x_ppl                        越低越像 AI

Fast-DetectGPT（解析版，按官方实现 baoguangsheng/fast-detect-gpt，采样模型=观察者，打分模型=执行者）：
  μ_t   = Σ_v O_t[v] · L_t[v]
  σ²_t  = Σ_v O_t[v] · L_t[v]² − μ_t²
  D     = ( Σ_t L_t[x_t] − Σ_t μ_t ) / sqrt( Σ_t σ²_t )   越高越像 AI
"""
from __future__ import annotations

import logging
import math
import threading

from .. import config

log = logging.getLogger("lm_scorer")


class LMScorer:
    def __init__(self):
        self.ready = False
        self.error: str | None = None
        self._lock = threading.Lock()

    def load(self):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        torch.set_num_threads(config.TORCH_THREADS)
        self.torch = torch
        log.info("loading tokenizer %s", config.OBSERVER_MODEL)
        self.tok = AutoTokenizer.from_pretrained(config.OBSERVER_MODEL)
        tok2 = AutoTokenizer.from_pretrained(config.PERFORMER_MODEL)
        if self.tok.get_vocab() != tok2.get_vocab():
            raise RuntimeError("OBSERVER_MODEL 与 PERFORMER_MODEL 的分词器不一致，必须使用同一系列的基础版与对话版模型")
        dtype = {"bfloat16": torch.bfloat16, "float16": torch.float16}.get(config.LM_DTYPE, torch.float32)
        kw = dict(torch_dtype=dtype, low_cpu_mem_usage=True)
        log.info("loading observer %s", config.OBSERVER_MODEL)
        self.observer = AutoModelForCausalLM.from_pretrained(config.OBSERVER_MODEL, **kw).eval()
        log.info("loading performer %s", config.PERFORMER_MODEL)
        self.performer = AutoModelForCausalLM.from_pretrained(config.PERFORMER_MODEL, **kw).eval()
        # 两个模型的输出维度可能因填充而不同，只比较共同的词表部分
        self.vocab = min(self.observer.config.vocab_size, self.performer.config.vocab_size, len(self.tok))
        self.ready = True

    def _chunks(self, ids: list[int]):
        n = config.LM_MAX_TOKENS
        if len(ids) <= n:
            yield ids
            return
        # 按固定长度切块；每块单独计算，最后按 token 数汇总
        for i in range(0, len(ids), n - 1):
            chunk = ids[i:i + n]
            if len(chunk) >= 8:
                yield chunk

    def score(self, text: str) -> dict | None:
        """返回 {'binoculars', 'fastdetect', 'ppl', 'x_ppl', 'tokens'}；文本太短返回 None。"""
        return self.score_ids(self.tok(text, add_special_tokens=False)["input_ids"])

    def score_ids(self, ids: list[int]) -> dict | None:
        """在同一次推理里同时算出以下特征（都不增加模型前向计算）：

        binoculars / fastdetect   —— 见文件开头
        fastdetect_norm           —— Fast-DetectGPT ÷ √token 数，消除段落长短的影响
        log_rank                  —— 实际 token 在执行者预测中的平均对数名次（Log-Rank，Gehrmann 2019 / Mitchell 2023）
        lrr                       —— Log-Likelihood Log-Rank Ratio = Σ(−log p) ÷ Σ log 名次（DetectLLM，Su et al. 2023）
        entropy                   —— 执行者预测分布的平均熵
        top1 / top10              —— 实际 token 排在第 1 / 前 10 名的比例（GLTR，Gehrmann et al. 2019）
        lp_burstiness             —— 每 24 个 token 为一窗，窗内平均困惑度的变异系数（与 GPTZero 的 burstiness 思路相同）
        div_*                     —— 逐 token 惊奇度（−log p）分布的多样性（DivEye，Basani & Chen，TMLR 2026）：
                                     标准差、偏度、峰度，以及一阶 / 二阶差分的标准差（惊奇度起伏的节奏）
        """
        tok = self._per_token(ids)
        return None if tok is None else _aggregate(tok, 0, len(tok["ll"]))

    def score_with_spans(self, text: str, spans: list[tuple[int, int]]) -> tuple[dict | None, list[dict | None]]:
        """整段打分，同时给出段内每个字符区间（逐句窗口）的同一套特征。只做一次前向计算：窗口特征由
        整段推理得到的逐 token 数值按区间汇总（窗口里的字仍以前文为上下文，和整段打分一致）。"""
        enc = self.tok(text, add_special_tokens=False, return_offsets_mapping=True)
        ids, offs = enc["input_ids"], enc["offset_mapping"]
        tok = self._per_token(ids)
        if tok is None:
            return None, [None] * len(spans)
        whole = _aggregate(tok, 0, len(tok["ll"]))
        out = []
        for a, b in spans:
            # 第 k 个逐 token 数值对应 ids[k + 1]（预测下一个 token）
            ks = [k for k in range(len(tok["ll"])) if a <= offs[k + 1][0] < b]
            out.append(_aggregate(tok, ks[0], ks[-1] + 1) if len(ks) >= 16 else None)
        return whole, out

    def _per_token(self, ids: list[int]) -> dict | None:
        torch = self.torch
        if len(ids) < 16:
            return None
        acc = {k: [] for k in ("ll", "mu", "var", "logrank", "ent", "rank")}
        with self._lock, torch.inference_mode():
            for chunk in self._chunks(ids):
                inp = torch.tensor([chunk])
                o = self.observer(input_ids=inp).logits[0, :-1, : self.vocab].float()
                p = self.performer(input_ids=inp).logits[0, :-1, : self.vocab].float()
                labels = inp[0, 1:]
                lp_perf = torch.log_softmax(p, dim=-1)
                del p
                pr_obs = torch.softmax(o, dim=-1)
                del o
                ll = lp_perf.gather(-1, labels.unsqueeze(-1)).squeeze(-1)       # L_t[x_t]
                mu = (pr_obs * lp_perf).sum(-1)                                  # μ_t
                var = (pr_obs * lp_perf.square()).sum(-1) - mu.square()          # σ²_t
                del pr_obs
                # 名次：比实际 token 概率更高的候选有几个（+1）
                rank = (lp_perf > ll.unsqueeze(-1)).sum(-1) + 1
                ent = -(lp_perf.exp() * lp_perf).sum(-1)
                acc["ll"].extend(ll.tolist())
                acc["mu"].extend(mu.tolist())
                acc["var"].extend(var.clamp_min(0).tolist())
                acc["logrank"].extend(torch.log(rank.float()).tolist())
                acc["rank"].extend(rank.tolist())
                acc["ent"].extend(ent.tolist())
                del lp_perf, ll, rank, mu, var, ent
        return acc if acc["ll"] else None


def _aggregate(t: dict, lo: int, hi: int) -> dict | None:
    """把逐 token 数值的 [lo, hi) 区间汇总成检测特征（整段和逐句窗口用同一个公式）。"""
    n_tok = hi - lo
    if n_tok <= 0:
        return None
    ll, mu = t["ll"][lo:hi], t["mu"][lo:hi]
    sum_lp, sum_mu, sum_var = sum(ll), sum(mu), sum(t["var"][lo:hi])
    sum_xent = -sum_mu
    sum_logrank = sum(t["logrank"][lo:hi])
    ranks = t["rank"][lo:hi]
    n_top1 = sum(1 for r in ranks if r == 1)
    n_top10 = sum(1 for r in ranks if r <= 10)
    sum_ent = sum(t["ent"][lo:hi])
    nll_all = [-x for x in ll]
    ppl = -sum_lp / n_tok
    x_ppl = sum_xent / n_tok
    fd = (sum_lp - sum_mu) / math.sqrt(sum_var) if sum_var > 0 else None
    w = 24
    wins = [sum(nll_all[i:i + w]) / len(nll_all[i:i + w]) for i in range(0, len(nll_all), w) if len(nll_all[i:i + w]) >= w // 2]
    burst = None
    if len(wins) >= 2:
        m = sum(wins) / len(wins)
        sd = math.sqrt(sum((x - m) ** 2 for x in wins) / len(wins))
        burst = sd / m if m > 0 else None
    div = _surprisal_diversity(nll_all)
    return {
        **div,
        "binoculars": ppl / x_ppl if x_ppl > 0 else None,
        "fastdetect": fd,
        "fastdetect_norm": fd / math.sqrt(n_tok) if fd is not None else None,
        "ppl": ppl,
        "x_ppl": x_ppl,
        "log_rank": sum_logrank / n_tok,
        "lrr": (-sum_lp) / sum_logrank if sum_logrank > 0 else None,
        "entropy": sum_ent / n_tok,
        "top1": n_top1 / n_tok,
        "top10": n_top10 / n_tok,
        "lp_burstiness": burst,
        "tokens": n_tok,
    }


def _surprisal_diversity(nll: list) -> dict:
    """DivEye 特征：人写文字的"意外程度"起伏更大、更不规则；模型生成的文字惊奇度更平稳。"""
    keys = ("div_std", "div_skew", "div_kurt", "div_d1_std", "div_d2_std")
    if len(nll) < 16:
        return dict.fromkeys(keys)

    def moments(v):
        n = len(v)
        m = sum(v) / n
        var = sum((x - m) ** 2 for x in v) / n
        sd = math.sqrt(var)
        if sd == 0:
            return m, 0.0, 0.0, 0.0
        skew = sum((x - m) ** 3 for x in v) / n / sd ** 3
        kurt = sum((x - m) ** 4 for x in v) / n / sd ** 4 - 3
        return m, sd, skew, kurt
    _, sd, skew, kurt = moments(nll)
    d1 = [b - a for a, b in zip(nll, nll[1:])]
    d2 = [b - a for a, b in zip(d1, d1[1:])]
    return {"div_std": sd, "div_skew": skew, "div_kurt": kurt,
            "div_d1_std": moments(d1)[1], "div_d2_std": moments(d2)[1]}
