"""检测引擎：加载模型、逐段打分、汇总成报告；长文档放进后台任务队列。

每段先识别文体（现代汉语 / 文言 / 英文），再用该文体对应的分类器和校准参数打分——
与知网、维普、Turnitin 等平台"先分语种、再用专门模型判断"的做法一致。"""
from __future__ import annotations

import bisect
import logging
import math
import re
import queue
import threading
import time
import uuid

from . import config, scoring
from .detectors import stylometry
from .famous import is_famous_verse
from .detectors.classifier import Classifier, make_english_classifier
from .detectors.lm_scorer import LMScorer
from .segmenter import (Segment, REGISTER_NAMES, unwrap_hard_wraps, deobfuscate, split_inline_labels, split_script_switches, split_register_switches, split_work_spans, split_famous_spans, detect_register, is_couplet_run, is_english_paper, min_chars, normalize_classical, normalize_english,
                        is_couplet, is_list_item, segment_text, strip_list_mark)

log = logging.getLogger("engine")


def style_scores(text: str, feats: dict | None = None) -> dict:
    """供校准使用的统计特征：句长变异系数、每千字套话数。"""
    f = feats or stylometry.features(text)
    return {"style_cv": f["sentence_len_cv"],
            "style_phrases": len(f["template_phrases"]) / max(len(text), 1) * 1000}


LM_KEYS = ("fastdetect", "fastdetect_norm", "binoculars", "ppl", "x_ppl", "log_rank", "lrr", "entropy",
           "top1", "top10", "lp_burstiness")


def memorized(raw: dict) -> bool:
    """语言模型几乎能逐字复现（困惑度极低），而分类器明确判为人写：多半是模型训练时背熟的公开名篇
    （莎士比亚、唐诗宋词、经典演讲等）。这时语言模型信号会误把名篇当成 AI，应当不予采信。"""
    ppl, cls = raw.get("ppl"), raw.get("classifier")
    return ppl is not None and cls is not None and math.exp(ppl) < config.MEMORIZED_PPL and cls < 0.3


def _wmedian(vals):
    """按字数加权的中位数；vals = [(值, 字数)]。"""
    vals = sorted(vals)
    if not vals:
        return None
    half, acc = sum(n for _, n in vals) / 2, 0
    for v, n in vals:
        acc += n
        if acc >= half:
            return v
    return vals[-1][0]


def _repeated_blocks(text: str, min_chars: int = 25, min_times: int = 4, min_share: float = 0.5):
    """整段/整行逐字重复：同一行（≥min_chars 字符，散文的一行就是一段，诗歌是一句）出现 ≥min_times 次、
    且多出来的重复文字占全文 ≥min_share 时，返回 (这样的行数, 最大重复次数, 重复占比)；否则 None。
    依据（2026-10-28）：用户上传的 Gemini 长文 59 篇中 53 篇重复占比 ≥ 50%（几段散文或几行诗循环凑字数，
    6700 词的小说实为 11 段重复 7 次、诗歌 294 行实为 30 行重复 12 次）；ChatGPT/千问/DeepSeek/豆包最高 2%；
    580 本真人英文书最高 29.5%（CNC 编程教程），其余 < 13%。"""
    import collections, re as _re
    lines = [_re.sub(r"\s+", " ", p).strip() for p in _re.split(r"\n+", text or "")]
    c = collections.Counter(p for p in lines if len(p) >= min_chars)
    reps = {p: n for p, n in c.items() if n >= min_times}
    share = sum(len(p) * (n - 1) for p, n in reps.items()) / max(1, len(text or ""))
    return (len(reps), max(reps.values()), share) if reps and share >= min_share else None


def _verbal_tic(text: str):
    """固定口头禅：DeepSeek 2026-10 英文文学作品大量使用 "the whole of"（"...is the whole of the record"）。
    依据（2026-10-28）：用户上传的 DeepSeek 作品每千词中位 3.7–13.3 次、最高 47 次；580 本真人英文书任意 1500 词
    窗口最高 2.7 次（柏拉图对话录、Kershaw 史书），整本最高 0.2 次；ChatGPT/千问/Gemini 为 0–0.5 次。
    出现 ≥ 8 次且每千词 ≥ 5 次时返回 (次数, 每千词次数)，否则 None。"""
    import re as _re
    n = len(_re.findall(r"\bthe whole of\b", text or "", _re.I))
    w = max(1, len((text or "").split()))
    r = 1000 * n / w
    return (n, r) if n >= 8 and r >= 5 else None


_QW_PATS = {
    "on_the_record": r"\b(?:want|put|keep)s? (?:it|that|this)? ?(?:on|in) the record",
    "want_to_be_careful": r"\bI want to be (?:careful|clear|honest|precise)",
    "which_is_the": r",? which is the (?:whole|only|part|thing|exact|point|reason)\b",
    "that_is_the_whole": r"\b(?:that|this) is the whole (?:job|story|argument|thing|point|trade|estate|religion|sermon)\b",
    "not_a_metaphor": r"\b(?:is|was) not a metaphor\b",
    "cjk_glued": r"[A-Za-z][一-鿿]{1,4}(?![一-鿿])",
    # 下面两种真人小说对话里也会出现，只作辅助
    "I_want_you_to": r"\bI want (?:you|the reader|that) to (?:know|understand|notice|hear)\b",
    "and_I_have_never": r"\band I have never\b",
}
_QW_WEAK = {"I_want_you_to", "and_I_have_never"}


def _qwen_style(text: str):
    """千问英文文学作品（回忆录、散文、小说）的典型套语："I want it on the record"、"I want to be careful"、
    "…, which is the whole/only…"、"That is the whole job/story"、"this is not a metaphor"，以及英文词后直接粘着汉字
    （"the shape of the利益"）。任意 4000 词窗口里出现 ≥ 2 种强套语，或 1 种强套语 + 共 ≥ 3 种时返回命中的种类，否则 None。
    依据（2026-10-28）：580 本真人英文书全文 0 本命中（只用两种弱套语时有 2 本小说对话命中，所以弱套语只作辅助）；
    ChatGPT 76、豆包 50、Gemini 159 篇 0 篇，DeepSeek 93 篇 1 篇；没参与选词的四批千问 80 篇 18 篇命中，其中 15 篇
    是 v14 漏判的（AI 率 < 50%，漏判共 40 篇）——正是分类器认不出的“自白体”千问文风。"""
    import re as _re
    w = (text or "").split()
    for i in range(0, max(1, len(w) - 1999), 2000):
        s = " ".join(w[i:i + 4000])
        ks = {k for k, p in _QW_PATS.items() if _re.search(p, s, 0 if k == "cjk_glued" else _re.I)}
        strong = ks - _QW_WEAK
        if len(strong) >= 2 or (strong and len(ks) >= 3):
            return sorted(ks)
        lex = _qwen_lex_hits(s)
        if len(lex) >= config.QW_LEX_MIN:
            return ["lex:" + g for g in lex]
    return None


_STYLE_LEX: dict = {}


def _qwen_lex3(text: str):
    """千问网页版英文文学作品的第三版短语表（app/qwen_lex3.json，553 条 4–6 词）：前几批千问 203 篇里 ≥ 6 篇出现、
    其他平台 1724 篇 AI 文 ≤ 1 篇、真人 1060 篇 ≤ 2 篇、620 本真人书 4000 词窗口 ≤ 15 个。任意 4000 词窗口命中
    ≥ QW_LEX3_MIN 条时返回命中的短语。
    依据（2026-10-29，没参与选词的数据）：新上传千问 15 篇 13 篇命中（其中 3 篇线上 AI 率 0–4%）；40 本经典真人书最高 2 条、
    1765 篇各类真人文体最高 1 条、PMC/PubMed/arXiv 2618 篇最高 1 条；各平台 API 生成的 4000 多篇 AI 文最高 3 条。"""
    w = (text or "").split()
    for i in range(0, max(1, len(w) - 1999), 2000):
        h = _style_lex_hits(" ".join(w[i:i + 4000]), "qwen_lex3.json")
        if len(h) >= config.QW_LEX3_MIN:
            return h
    return None


def _style_lex_hits(s: str, fname: str):
    """在一段文字里找 app/<fname> 短语表里的短语（互相包含的只算最长的一条）。"""
    lex = _STYLE_LEX.get(fname)
    if lex is None:
        import json as _json
        from pathlib import Path as _P
        try:
            lex = _json.loads((_P(__file__).parent / fname).read_text("utf-8"))
        except Exception:  # noqa: BLE001
            lex = []
        _STYLE_LEX[fname] = lex
    if not lex:
        return []
    t = " " + " ".join(re.findall(r"[a-z']+", s.lower())) + " "
    h = [g for g in lex if (" " + g + " ") in t]
    return [g for g in h if not any(g != m and (" " + g + " ") in (" " + m + " ") for m in h)]


def _deepseek_style(text: str):
    """DeepSeek 网页版英文论文体/文学作品的固定句式，比如 "The broader implication is that…"、
    "…calibrated to the evidence rather than to the enthusiasm that the topic attracts"、"Note on method: This paper is…"、
    "the X is the thing that the Y should address"。短语表 app/deepseek_lex.json（240 条，4–6 词）：用户上传的 DeepSeek 70 篇里
    ≥ 6 篇出现、其他平台 632 篇 AI 文 ≤ 1 篇、580 本真人书 14702 个 4000 词窗口 ≤ 15 个，并且 4425 篇真人论文/摘要/散文/新闻
    （PMC 全文 569、arXiv 503、PubMed 1546、各类真人文体 1765）里最多 2 篇出现。任意 4000 词窗口命中 ≥ DS_LEX_MIN 条时返回命中的短语。
    依据（2026-10-28）：没参与选词的 DeepSeek 两批 40 篇论文 37 篇命中；上述 4425 篇真人文本和 580 本真人书最高 2 条，0 篇命中；
    其他平台 653 篇里命中的 4 篇都是 AI 文（3 篇其实也是 DeepSeek，1 篇千问）。
    注意：通过 API 生成的 DeepSeek 论文（436 篇）基本不命中——这是网页版的文风，不代表 DeepSeek 的全部输出。"""
    w = (text or "").split()
    for i in range(0, max(1, len(w) - 1999), 2000):
        h = _style_lex_hits(" ".join(w[i:i + 4000]), "deepseek_lex.json")
        if len(h) >= config.DS_LEX_MIN:
            return h
    n = _thing_that_density(text)
    if n >= config.DS_THING_MIN:
        return [f"…is the thing that…（1000 词内 {n} 次）"]
    return None


_THING_RE = re.compile(r"\b(?:is|are|was|were)\s+the\s+things?\s+that\b", re.I)


def _thing_that_density(text: str) -> int:
    """任意 1000 词窗口（步长 500）里 "is/are the thing(s) that" 的最多次数。DeepSeek 网页版 2026-10 的英文论文和回忆录
    反复用 "the X is the thing that the Y is made of" 造句，每篇 20–50 次。
    依据（2026-10-28）：用户上传的 DeepSeek 第 4 批 20 篇 19 篇 ≥ 8 次（含句式表漏掉的回忆录和产品评测）；
    4383 篇真人论文/摘要/散文/新闻 0 次；580 本真人英文书最高 4 次；独立真人小说 BookCorpusOpen 23509 个 4000 词窗口最高 5 次、
    WritingPrompts 1500 个最高 1 次；其他平台 724 篇 AI 文 1 篇 9 次。阈值 DS_THING_MIN=8，DeepSeek 第 4、5 批 40 篇 37 篇达到。"""
    w = (text or "").split()
    b = 0
    for i in range(0, max(1, len(w) - 999), 500):
        b = max(b, len(_THING_RE.findall(" ".join(w[i:i + 1000]))))
    return b


# 说明 / 操作类文体（菜谱、操作指南、急救手册、手工教程）：句子大多以祈使动词开头。真人这类文字句式短而公式化，
# 分类器常打高分（r99：真人说明文 91 段平均 26%、23 段 ≥ 50%）；但 AI 写的同类文字三个英文分类器几乎全都很肯定，
# 因此这类文字只在"更严门槛"下计入（见 engine 7.6）。祈使句比例 ≥ 0.25：真人说明类书 56/184 个窗口，其他真人文字 1/4863。
_IMP_VERBS = frozenset("""add adjust allow apply arrange avoid bake baste beat begin blanch blend boil bring brown brush buy
carve check chill chop choose clean click close coat combine compare consider continue cook cool core cover crack create cream
crumble crush cube cut decide deglaze dice dip discard dissolve divide do don't drain drizzle drop dry dust enjoy fill find finish
flip fold follow form freeze fry garnish get give grate grease grill grind halve heat hold imagine insert keep knead layer leave
let line look make marinate mash measure melt mince mix moisten move note open pack pat peel pick pierce place plan pour practice
preheat prepare press process pull purée puree push put reduce refrigerate reheat remember remove repeat replace reserve return
rinse roast roll rub run saute sauté scatter scoop scrape season select separate serve set shake shape shred simmer skim slice
slide soak spoon spray spread sprinkle squeeze start stir store strain stuff sweat take taste test think tie tilt toast top toss
transfer trim try turn unmold use wait warm wash watch whip whisk wipe wrap write""".split())
_IMP_FIRST = re.compile(r"[“\"'(]?(?:\d+[.)]\s*|[-•*]\s*)?([A-Za-z']+)")


# 学术摘要 / 学术文字（engine 7.7）：单独粘贴的论文摘要没有 Introduction / Methods 等章节标题，不会被认作论文，
# 只能套用为一般英文散文校准的阈值（MAGE），真人摘要大量误判（2026-10-29：PubMed 150 篇平均 29%、≥ 50% 44 篇；
# arXiv 150 篇平均 23%、≥ 50% 32 篇）。AI 写的摘要第二分类器几乎都顶到 0.96，所以学术文字只在 v14 ≥ EN_ABS_EN2 时计入。
# 识别：学术用语密度（本表里的词占全部词的比例）≥ EN_ABS_DENSITY。真人摘要 84% 命中；真人书 400 词块 18/891、
# 各类真人文体 13/1765、AI 文学 4/1002、说明文 0/211 命中（命中只会让门槛更严，不会增加误判）。
_ACAD_WORDS = frozenset("""analysis analyses analyzed analysed analyse analyze approach approaches assess assessed assessment
associated association associations baseline cohort compared comparison conclude concluded conclusion conclusions conditions
consistent controlled correlation criteria data dataset datasets demonstrate demonstrated design determine determined effect
effects efficacy empirical estimate estimated estimates evaluate evaluated evaluation evidence examined experiment experimental
experiments factor factors findings framework groups hypothesis identified impact implications increase increased indicate
indicates indicated intervention interventions investigate investigated limitations measure measured measurement measures method
methods methodology model models observed outcome outcomes parameters participants patients performance population potential
predict predicted prediction primary proposed rate rates ratio reduced reduction regression relative reported research
respectively response results revealed risk sample samples scale secondary significant significantly simulation statistical
studies study suggest suggests survey symptoms test tested tests theory treatment trial trials validation variable variables
variance versus mean median quantified characterized characterised protocol protocols cross-sectional longitudinal randomized
randomised prospective retrospective meta-analysis systematic adjusted multivariate multivariable logistic linear coefficient
coefficients confidence interval intervals odds hazard prevalence incidence mortality specimens assay assays spectroscopy
synthesis synthesized fabricated characterization simulations algorithm algorithms benchmark benchmarks accuracy""".split())


def academic_density(text: str) -> tuple[float, int]:
    """(学术用语占全部英文词的比例, 词数)。"""
    w = re.findall(r"[a-z][a-z-]*", (text or "").lower())
    return sum(x in _ACAD_WORDS for x in w) / max(1, len(w)), len(w)


_ABS_NB = None


def abstract_style_score(text: str) -> float:
    """AI 摘要的写法得分（app/abs_style_nb.json：词和相邻两词的对数比，正数偏 AI 摘要、负数偏真人摘要；按 √词数 归一）。
    依据（2026-10-29）：用 DeepSeek、Kimi、文心、豆包、ChatGPT、Claude、千问 7 个平台的英文摘要（各平台等权）
    对比 PubMed / arXiv 真人摘要的一半（1030 篇）选出 874 个特征；另一半真人摘要（975 篇）只有 1% 超过 0.88，
    PMC 真人论文引言 569 篇 0 篇超过。轮流留出一个平台不参与选词时，留出平台超过 0.88 的比例：千问 0.93–1.00、
    豆包 0.84、ChatGPT 0.63、文心 0.43–0.56（偏低）。只用来把已被判过线、又因"学术摘要"规则降级的段落恢复计入，
    不单独把任何文字判为 AI。"""
    global _ABS_NB
    if _ABS_NB is None:
        import json as _json
        from pathlib import Path as _P
        try:
            _ABS_NB = _json.loads((_P(__file__).parent / "abs_style_nb.json").read_text("utf-8"))
        except Exception:  # noqa: BLE001
            _ABS_NB = {}
    w = re.findall(r"[a-z]+", (text or "").lower())
    if not w or not _ABS_NB:
        return 0.0
    f = set(w) | {w[i] + " " + w[i + 1] for i in range(len(w) - 1)}
    return sum(_ABS_NB[g] for g in f if g in _ABS_NB) / math.sqrt(len(w) + 1)


def imperative_ratio(text: str) -> tuple[float, int]:
    """(以祈使动词开头的句子比例, 句子数)；只数 ≥ 3 词的句子。"""
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if len(s.split()) >= 3]
    if not sents:
        return 0.0, 0
    def imp(s):
        w = ((_IMP_FIRST.match(s) or [None, ""])[1] or "").lower()
        if w == "let" and re.match(r"[“\"'(]?let(?:'s| me| us)\b", s, re.I):   # "Let me speak…" / "Let us go…" 是修辞，不是操作步骤
            return False
        return w in _IMP_VERBS
    n = sum(imp(s) for s in sents)
    return n / len(sents), len(sents)


_QW_LEX = None


def _qwen_lex_hits(s: str):
    """从用户上传的千问英文文学作品里统计出来的 3–5 词短语（app/qwen_lex.json，65 条；第 2 版：千问 140 篇里 ≥ 11 篇出现、
    其他平台 378 篇 AI 文 ≤ 1 篇、580 本真人书 14702 个 4000 词窗口 ≤ 15 个，再去掉独立真人语料 BookCorpusOpen 里
    23509 个窗口中出现 ≥ 20 个的 15 条，如 "i am going to tell"、"to be a person"）。返回这段文字里出现的短语
    （互相包含的只算最长的一条）。依据（2026-10-28）：没参与选词的千问第 8 批 20 篇，命中 ≥ 4 条的 18 篇；
    580 本真人书最高 2 条；BookCorpusOpen 23509 个窗口和 WritingPrompts 1500 个窗口在第 2 版去掉常见短语之前就没有一个 ≥ 4 条。"""
    return _style_lex_hits(s, "qwen_lex.json")


def is_short(seg, text: str) -> bool:
    if seg.register == "zh_poetry":
        return False
    if seg.register == "en":
        return len(text.split()) < config.SHORT_WORDS_EN
    return len(text) < config.SHORT_CHARS_ZH


def score_text(seg) -> str:
    """送进模型打分的文字：去掉开头的标题行（标题不是作者的正文，短诗里标题占比又很大）；文言去掉引号。"""
    text = seg.text
    if seg.register != "en" and is_list_item(text.split("\n", 1)[0]):
        text = strip_list_mark(text)       # 作品集的条目编号（"1、""1）、"）不是作品内容，不送进模型
    if seg.title and seg.text.startswith(seg.title):
        rest = seg.text[len(seg.title):].strip()
        if len(rest) >= 10:
            text = rest
    if seg.register == "zh_classical":
        return normalize_classical(text)
    if seg.register == "en":
        return normalize_english(text)
    return text


def _excl_kind(seg) -> str:
    if seg.kind == "quotation" and any("名篇" in n for n in seg.notes):
        return "famous"
    return seg.kind


EXCL_NAMES = {"reference": "参考文献", "frontmatter": "题目 / 作者 / 期刊信息", "table": "表格", "quotation": "引文",
              "famous": "疑似公开名篇原文", "reference_only": "仅供参考的文体"}

_EVAL_CACHE: dict = {}
# 不在 tools/eval_result.json 里的独立测试（来自分类器训练报告，测试样本从未参与训练）
EXTRA_EVAL = {"zh_poetry": [{"name": "对联：国产模型写的对联 vs 真人对联（couplet-dataset 测试集；对联用单独阈值）",
                             "n_ai": 162, "n_human": 500, "ai_caught": 0.691, "human_flagged": 0.042}]}


def _wilson_upper(k: int, n: int, z: float = 1.96) -> float | None:
    """误判率的单侧 95% 精确上限（Clopper–Pearson）：实测 k / n 次误判时，真实误判率不超过多少（95% 把握）。
    0 次误判时就是 1 − 0.05^(1/n)（1000 篇约 0.3%；要把上限压到 0.01% 需要约 3 万篇无误判，见 2026-10 用户提供的
    检测方法综述）。比原来的 Wilson 近似更保守、也是统计上标准的写法。函数名保持不变以兼容旧调用。"""
    if not n:
        return None
    if k >= n:
        return 1.0
    if k <= 0:
        return round(1 - 0.05 ** (1 / n), 4)

    def cdf(p):                                   # P(X ≤ k | n, p)
        term, tot = (1 - p) ** n, 0.0
        for i in range(k + 1):
            tot += term
            term *= (n - i) / (i + 1) * p / (1 - p) if p < 1 else 0
        return tot
    lo, hi = k / n, 1.0
    for _ in range(60):                           # 找使 P(X ≤ k) = 0.05 的 p
        mid = (lo + hi) / 2
        if cdf(mid) > 0.05:
            lo = mid
        else:
            hi = mid
    return round(hi, 4)


def _detectors_disagree(raw: dict) -> bool:
    """中文第二分类器 ≥ ZH2_SINGLE_THRESHOLD，或英文第二分类器与整篇分类器都 ≥ 0.9，但综合分数没过阈值。"""
    z2 = raw.get("classifier_zh2")
    if z2 is not None:
        return z2 >= config.ZH2_SINGLE_THRESHOLD
    e2, e3, e4 = raw.get("classifier_en2"), raw.get("classifier_en3"), raw.get("classifier_en4")
    return e2 is not None and e2 >= 0.9 and max(e3 or 0, e4 or 0) >= 0.9


def precision_at_prevalence(tpr: float, fpr: float, prevalence: float) -> float | None:
    """被标出的文字真是 AI 写的概率（贝叶斯公式）：TPR·π / (TPR·π + FPR·(1−π))。"""
    den = tpr * prevalence + fpr * (1 - prevalence)
    return round(tpr * prevalence / den, 4) if den else None


def known_error_rates(profiles: list) -> list:
    """本工具在独立测试集上实测的检出率 / 误判率（tools/eval_result.json，由 tools/evaluate.py 生成）。
    参考 Weber-Wulff 等（2023）与 Liang 等（2023）对检测工具的要求：报告应按文体说明已知误差，
    而不是只给一个分数。只列"未参与训练和校准"的测试集，不列对照实验。"""
    import json
    from pathlib import Path
    if "d" not in _EVAL_CACHE:
        f = Path(__file__).resolve().parent.parent / "tools" / "eval_result.json"
        try:
            _EVAL_CACHE["d"] = json.loads(f.read_text("utf-8"))
        except (OSError, ValueError):
            _EVAL_CACHE["d"] = {}
    d = _EVAL_CACHE["d"]
    out = []
    for prof in profiles:
        sets = []
        for e in ((d.get(prof) or {}).get("evaluation") or []) + EXTRA_EVAL.get(prof, []):
            if "对照" in e.get("name", "") or (not e.get("n_ai") and not e.get("n_human")):
                continue
            nh, na = e.get("n_human", 0), e.get("n_ai", 0)
            fp = e.get("human_flagged") if nh else None
            sets.append({"name": e["name"], "n_ai": na, "n_human": nh,
                         "ai_caught": e.get("ai_caught") if na else None, "human_flagged": fp,
                         # 误判率的单侧 95% 精确上限（Clopper–Pearson；0 次误判时为 1 − 0.05^(1/n)）：样本有限，实测 0% 不等于不会误判
                         "human_flagged_upper95": _wilson_upper(round(fp * nh), nh) if fp is not None else None})
        if sets:
            g = {"profile": prof, "name": scoring.PROFILE_NAMES.get(prof, prof), "sets": sets}
            both = next((e for e in sets if e["ai_caught"] is not None and e["human_flagged"] is not None), None)
            if both:     # 被标出的文字真是 AI 的概率取决于送检文章里 AI 文章的比例（基数）
                g["ppv"] = {f"{int(pi * 100)}%": precision_at_prevalence(both["ai_caught"], max(both["human_flagged"], 1e-4), pi)
                            for pi in (0.5, 0.1, 0.02)}
                g["ppv_basis"] = both["name"]
            out.append(g)
    return out


def works_summary(seg_out: list) -> list:
    """按作品（标题分开的部分）汇总，类似知网报告里的“章节 / 片段 AI 率”：每篇给出字数、AI 率和结论。"""
    works: dict = {}
    for seg in seg_out:
        w = works.setdefault(seg["block"], {"block": seg["block"], "title": "", "chars": 0, "counted": 0,
                                            "flagged": 0, "psum": 0.0, "refsum": 0.0, "refchars": 0,
                                            "registers": [], "segments": 0})
        if seg["title"] and not w["title"]:
            w["title"] = seg["title"]
        w["chars"] += seg["chars"]
        w["segments"] += 1
        if seg["register"] not in w["registers"]:
            w["registers"].append(seg["register"])
        if seg["prob"] is not None:
            w["counted"] += seg["chars"]
            w["psum"] += seg["prob"] * seg["chars"]
            if seg.get("ai_chars") is not None:          # 部分疑似 / 局部疑似：只计入窗口判出的那部分
                w["flagged"] += seg["ai_chars"]
            elif seg["level"] in ("high", "mid", "light"):
                w["flagged"] += seg["chars"]
        elif seg["kind"] == "reference_only" and seg["ref_prob"] is not None:
            w["refchars"] += seg["chars"]
            w["refsum"] += seg["ref_prob"] * seg["chars"]
    out = []
    for w in works.values():
        if not w["title"]:
            first = next((x["text"] for x in seg_out if x["block"] == w["block"]), "")
            w["title"] = first.strip().splitlines()[0][:20] + "……" if first.strip() else "（无标题）"
        if w["counted"]:
            rate = w["flagged"] / w["counted"]
            verdict = "疑似 AI 生成" if rate >= 0.5 else "部分段落疑似 AI" if rate > 0 else "未见明显 AI 特征"
            out.append({**{k: w[k] for k in ("block", "title", "chars", "registers", "segments")},
                        "ai_rate": round(rate, 4), "mean_prob": round(w["psum"] / w["counted"], 4),
                        "counted": True, "verdict": verdict})
        elif w["refchars"]:
            out.append({**{k: w[k] for k in ("block", "title", "chars", "registers", "segments")},
                        "ai_rate": None, "mean_prob": round(w["refsum"] / w["refchars"], 4),
                        "counted": False, "verdict": "仅供参考（篇幅过短或诗词，不计入）"})
        else:
            segs_w = [x for x in seg_out if x["block"] == w["block"]]
            famous = segs_w and all(any("名篇" in n for n in (x.get("notes") or [])) for x in segs_w if x["kind"] != "reference")
            out.append({**{k: w[k] for k in ("block", "title", "chars", "registers", "segments")},
                        "ai_rate": None, "mean_prob": None, "counted": False,
                        "verdict": "疑似公开名篇原文（不计入）" if famous else "未计入（引文 / 参考文献）"})
    return out


def collection_test(works: list, fpr_of: dict, min_works: int = 5) -> dict | None:
    """作品集整体判断（2026-11-03）：一首绝句 28 字、律诗 56 字，单首判断信息量太少；但同一份作品集里
    如果很多篇都过了阈值，把各篇证据合起来就可靠得多。零假设"全部是人写"：每篇以实测误判率 f 被误标，
    同一作者风格相近、误标不独立，所以 f 放宽一倍；用二项分布算 P(误标篇数 ≥ k)。只作提示，不改变 AI 率。"""
    ws = [w for w in works if w.get("counted") and w.get("ai_rate") is not None
          and set(w.get("registers") or []) <= {"zh_poetry", "zh_classical"}]
    n = len(ws)
    if n < min_works:
        return None
    k = sum(1 for w in ws if w["ai_rate"] >= 0.5)
    regs = [r for w in ws for r in w["registers"]]
    f = min(0.5, 2 * sum(fpr_of.get(r, 0.05) for r in regs) / max(1, len(regs)))
    p = sum(math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                     + i * math.log(f) + (n - i) * math.log(1 - f)) for i in range(k, n + 1)) if k else 1.0
    return {"n": n, "k": k, "fpr": round(f, 4), "p_human": float(min(1.0, p))}


_SENT_ZH = re.compile(r"[^。！？；!?;…\n]*(?:[。！？；!?;…]+[”’」』）)]*|\n|$)")
_SENT_EN = re.compile(r"[^.!?\n]*(?:[.!?]+[\"'”’)]*\s*|\n|$)")


def window_spans(text: str, register: str) -> list[tuple[int, int]]:
    """把一段文字按句子切成约 WIN_ZH_CHARS 字 / WIN_EN_WORDS 词的窗口（不重叠，首尾相接覆盖全段）。
    段落不到两个窗口长时返回空列表（短段落本身就是一个窗口，不必再切）。"""
    en = register == "en"
    size = (lambda a, b: len(text[a:b].split())) if en else (lambda a, b: len(re.findall(r"[\u4e00-\u9fff]", text[a:b])))
    target = config.WIN_EN_WORDS if en else config.WIN_ZH_CHARS
    if size(0, len(text)) < 2 * target:
        return []
    bounds = [m.end() for m in (_SENT_EN if en else _SENT_ZH).finditer(text) if m.end() > m.start()]
    spans, a = [], 0
    for b in bounds:
        if size(a, b) >= target:
            spans.append((a, b))
            a = b
    if a < len(text):
        if spans and size(a, len(text)) < target // 2:
            spans[-1] = (spans[-1][0], len(text))
        else:
            spans.append((a, len(text)))
    return spans if len(spans) >= 2 else []


def window_flag(prob, second, seg_second=None, register: str = "") -> str | None:
    """窗口判定：'ai'（综合分数很高且第二意见同意）、'human'（两者都明确判人写）或 None（不确定）。
    现代汉语另有一条：中文第二分类器对这个窗口 ≥ WIN_ZH2_AI、对整段也 ≥ WIN_ZH2_SEG（人写文章中夹入几句新一代国产
    大模型写的句子，MPU 分类器和语言模型认不出）。依据（2026-10-19）：没参与训练的 7 部真人书 3000 个约 100 字的窗口，
    第二分类器 ≥ 0.95 的只有 3 个（0.1%）；国产大模型窗口 80% ≥ 0.95。"""
    if register == "zh" and second is not None and seg_second is not None \
            and second >= config.WIN_ZH2_AI and seg_second >= config.WIN_ZH2_SEG:
        return "ai"
    if prob is None:
        return None
    if prob >= config.WIN_AI_PROB and (second is None or second >= config.WIN_AI_SECOND):
        return "ai"
    if prob < config.WIN_HUMAN_PROB and (second is None or second < config.WIN_HUMAN_SECOND):
        return "human"
    return None



def is_en_verse(text: str) -> bool:
    """英文诗 / 分行散文诗：多数行很短、按行断开（第一行当作标题不算）。"""
    lines = [l.strip() for l in text.splitlines() if l.strip()][1:] or [""]
    # 清单 / 配料表（"Fine salt: 5 g"）行也很短，但不是诗：带冒号或数字的短行占三成以上就不算
    listy = sum(bool(re.search(r":\s|\d", l)) for l in lines) >= 0.3 * len(lines)
    # 夹着项目符号列表的论文段落（"Examples include:""Measure:"后面几行短语）也有很多短行，但有两行以上以冒号结尾、引出列表，
    # 诗几乎不这样写（2026-10 用户的 pol.docx：两段这样的段落被当成诗，没有参与整篇判断）
    prose_lines = sum(l.endswith((":", "：")) for l in lines) >= 2
    if not (len(lines) >= 4 and sorted(len(l) for l in lines)[len(lines) // 2] <= 80 and not listy and not prose_lines):
        return False
    # 从 .docx 表格、公式块、"■ ... INSIGHT" 框里取出的文字也是一行一个短语（2026-10-27：用户 160 篇 AI 文档里 44 段这样的
    # 表格 / 公式被当成诗，因而不参与整篇判断，Gemini《沉积学》《历史语言学》只剩 64–67%）。诗的行尾多有标点（逗号、句号），
    # 或者一句跨行、下一行小写开头；表格单元格两样都很少（44 段里行尾有标点的最多 45%，小写开头的最多 12%；
    # 用户的 Gemini 十四行诗 81–100%）。
    punct = sum(bool(re.search(r"[.,;:!?\"”’)—–-]$", l)) for l in lines) / len(lines)
    lower = sum(l[:1].islower() for l in lines) / len(lines)
    return punct >= 0.5 or lower >= 0.3


class Engine:
    def __init__(self):
        self.lm = LMScorer() if config.ENABLE_LM else None
        self.cls = Classifier() if (config.ENABLE_CLASSIFIER and config.CLASSIFIER_MODEL) else None
        self.cls_en = (make_english_classifier()
                       if (config.ENABLE_EN_CLASSIFIER and config.EN_CLASSIFIER_MODEL) else None)
        self.cls_en2 = (Classifier(config.EN2_CLASSIFIER_MODEL, 320) if config.EN2_CLASSIFIER_MODEL else None)
        self.cls_zh2 = (Classifier(config.ZH2_CLASSIFIER_MODEL, 384) if config.ZH2_CLASSIFIER_MODEL else None)
        self.cls_en3 = (Classifier(config.EN3_CLASSIFIER_MODEL, 320) if config.EN3_CLASSIFIER_MODEL else None)
        self.cls_en4 = (Classifier(config.EN4_CLASSIFIER_MODEL, 320) if config.EN4_CLASSIFIER_MODEL else None)
        self.cls_poetry = (Classifier(config.POETRY_CLASSIFIER_MODEL, 128) if config.POETRY_CLASSIFIER_MODEL else None)
        self.cls_classical = (Classifier(config.CLASSICAL_CLASSIFIER_MODEL, 256) if config.CLASSICAL_CLASSIFIER_MODEL else None)
        from .detectors.tagger import SentenceTagger
        self.tag_en = SentenceTagger(config.TAGGER_EN_MODEL) if config.TAGGER_EN_MODEL else None
        self.tag_zh = SentenceTagger(config.TAGGER_ZH_MODEL) if config.TAGGER_ZH_MODEL else None
        self.tag_zc = SentenceTagger(config.TAGGER_ZC_MODEL) if config.TAGGER_ZC_MODEL else None
        self.tag_zp = SentenceTagger(config.TAGGER_ZP_MODEL) if config.TAGGER_ZP_MODEL else None
        self.loading = True
        self.loaded_event = threading.Event()
        self.load_started = time.time()
        self.cal, self.cal_source = self._load_cal()
        self.tokens_per_sec = None

    # ---------- 加载 ----------
    def _load_cal(self):
        cal, source = self._load_base_cal()
        user = config.load_user_profiles()
        for prof, c in user.items():
            cal = scoring.merge_profile(cal, c, prof)
        self.user_profiles = sorted(user)
        if user:
            source += "；已叠加你的标注校准（" + "、".join(scoring.PROFILE_NAMES.get(p, p) for p in user) + "）"
        return cal, source

    def reload_calibration(self):
        self.cal, self.cal_source = self._load_cal()
        try:
            self._cal_mtime = config.USER_CALIBRATION_FILE.stat().st_mtime
        except OSError:
            self._cal_mtime = None

    def maybe_refresh_calibration(self, every: float = 300.0):
        """服务同时运行多个副本：标注校准在某一个副本里重新拟合后，只有那个副本立即生效（2026-10-25 发现）。
        每个副本每隔 every 秒从共享存储重新读取一次，文件变了就重新加载。"""
        now = time.time()
        if now - getattr(self, "_cal_checked", 0.0) < every:
            return
        self._cal_checked = now
        if config.CALIBRATION_VOLUME:
            try:
                import modal
                modal.Volume.from_name(config.CALIBRATION_VOLUME).reload()
            except Exception:  # noqa: BLE001  有文件正在使用时 reload 会失败，下次再试
                pass
        try:
            m = config.USER_CALIBRATION_FILE.stat().st_mtime
        except OSError:
            m = None
        if m != getattr(self, "_cal_mtime", None):
            log.info("user calibration changed on disk; reloading")
            self.reload_calibration()

    def _load_base_cal(self):
        default = config.load_default_calibration()
        override, source = config.load_calibration_override()
        if override:
            cal = dict(scoring.DEFAULTS)
            cal.update(override)
            # 管理员只校准了部分文体时，其余文体沿用内置默认校准
            profs = dict((default or {}).get("profiles") or {})
            profs.update(override.get("profiles") or {})
            if profs:
                cal["profiles"] = profs
            return cal, source
        if default:
            cal = dict(scoring.DEFAULTS)
            cal.update(default)
            names = "、".join(["现代汉语"] * bool(default.get("calibrated")) +
                             [scoring.PROFILE_NAMES.get(k, k) for k in (default.get("profiles") or {})])
            return cal, f"内置默认校准（公开数据集；{names}）"
        return dict(scoring.DEFAULTS), "内置经验值（未校准）"

    def set_calibration(self, cal: dict, source: str):
        self.cal, self.cal_source = cal, source
        self._human_ref_cache = {}

    def human_ref(self, profile: str, prof_cal: dict):
        """同文体人写对照样本（随代码发布的校准数据里的人写段落）的综合得分，已排序。
        用于给每段算"人写样本里有多少比例得分不低于本段"（保形预测的经验 p 值，Open-Set Conformal 一类论文的做法）：
        比单个概率更好解释——它直接回答"如果把阈值定在本段的分数，人写文字会被误判多少"。"""
        cache = getattr(self, "_human_ref_cache", None)
        if cache is None:
            cache = self._human_ref_cache = {}
        key = (profile, id(prof_cal.get("lr") or prof_cal.get("weights") or prof_cal))
        if key not in cache:
            vals = []
            for r in load_builtin_calib(profile):
                if int(r.get("y", 1)) == 0 and isinstance(r.get("s"), dict):
                    pr = scoring.combine(r["s"], prof_cal)["prob"]
                    if pr is not None:
                        vals.append(pr)
            cache[key] = sorted(vals)
        return cache[key]

    def load_all(self):
        try:
            for name, det in (("语言模型", self.lm), ("中文分类器", self.cls), ("英文分类器", self.cls_en),
                              ("英文第二分类器", self.cls_en2), ("中文第二分类器", self.cls_zh2), ("英文整篇分类器", self.cls_en3), ("英文整篇分类器 2", self.cls_en4),
                              ("诗词分类器", self.cls_poetry), ("文言分类器", self.cls_classical),
                              ("英文句级标注模型", self.tag_en), ("中文句级标注模型", self.tag_zh),
                              ("文言句级标注模型", self.tag_zc), ("诗词句级标注模型", self.tag_zp)):
                if det is None:
                    continue
                try:
                    det.load()
                except Exception as e:  # noqa: BLE001 —— 单个检测器失败不影响其他
                    log.exception("failed to load %s", name)
                    det.error = f"{type(e).__name__}: {e}"
        finally:
            self.loading = False
            self.loaded_event.set()

    def wait_loaded(self, timeout: float = 900) -> bool:
        """等所有模型加载完再打分：避免"语言模型好了、分类器还没好"时算出缺项的结果。"""
        return self.loaded_event.wait(timeout)

    def status(self) -> dict:
        def st(det, model):
            if det is None:
                return {"enabled": False}
            return {"enabled": True, "ready": det.ready, "error": det.error, "model": model}
        profiles = {"zh": bool(self.cal.get("calibrated"))}
        for k in ("zh_classical", "zh_poetry", "en"):
            profiles[k] = bool(((self.cal.get("profiles") or {}).get(k) or {}).get("calibrated"))
        return {
            "loading": self.loading,
            "lm": st(self.lm, [config.OBSERVER_MODEL, config.PERFORMER_MODEL]),
            "classifier": st(self.cls, config.CLASSIFIER_MODEL),
            "classifier_en": st(self.cls_en, config.EN_CLASSIFIER_MODEL),
            "classifier_en2": st(self.cls_en2, config.EN2_CLASSIFIER_ID),
            "classifier_zh2": st(self.cls_zh2, config.ZH2_CLASSIFIER_ID),
            "classifier_en3": st(self.cls_en3, config.EN3_CLASSIFIER_ID),
            "classifier_en4": st(self.cls_en4, config.EN4_CLASSIFIER_ID),
            "classifier_poetry": st(self.cls_poetry, config.POETRY_CLASSIFIER_ID),
            "classifier_classical": st(self.cls_classical, config.CLASSICAL_CLASSIFIER_ID),
            "calibration": {"calibrated": bool(self.cal.get("calibrated")), "source": self.cal_source,
                            "threshold": self.cal.get("threshold"), "note": self.cal.get("note"),
                            "profiles": profiles,
                            # 各文体实际使用的阈值，以及哪些文体叠加了用户自己的标注校准（排查"为什么没判出来"时用）
                            "thresholds": {k: round(scoring.profile_for(self.cal, k)[0].get("threshold", 0), 4)
                                           for k in ("zh", "zh_short", "en", "en_paper", "en_poetry", "zh_classical", "zh_poetry")},
                            "user_profiles": getattr(self, "user_profiles", [])},
            "tokens_per_sec": self.tokens_per_sec,
        }

    def any_ready(self) -> bool:
        return bool((self.lm and self.lm.ready) or (self.cls and self.cls.ready) or (self.cls_en and self.cls_en.ready))

    def special_classifier(self, register: str):
        """诗词、文言各有专用分类器（配置了才有）。"""
        return {"zh_poetry": self.cls_poetry, "zh_classical": self.cls_classical}.get(register)

    def classifier_for(self, register: str):
        special = self.special_classifier(register)
        if special is not None:
            # 配置了专用分类器却没加载成功时，不退回通用分类器（校准参数是按专用分类器拟合的）
            return special if special.ready else None
        det = self.cls_en if register == "en" else self.cls
        return det if (det and det.ready) else None

    # ---------- 打分 ----------
    def classify(self, texts: list[str], registers: list[str]) -> list:
        """按文体分组送进对应的分类器；没有可用分类器的返回 None。"""
        out = [None] * len(texts)
        for reg in set(registers):
            det = self.classifier_for(reg)
            if not det:
                continue
            idx = [i for i, r in enumerate(registers) if r == reg]
            for i, p in zip(idx, det.predict([texts[i] for i in idx])):
                out[i] = p
        return out

    def classify_second(self, texts: list[str], registers: list[str]) -> list:
        """诗词、文言段落再用通用中文分类器（MPU）打一次分，作为第二意见（与专用分类器互相制衡）。"""
        out = [None] * len(texts)
        if not (self.cls and self.cls.ready):
            return out
        idx = [i for i, r in enumerate(registers) if self.special_classifier(r) is not None]
        if idx:
            for i, p in zip(idx, self.cls.predict([texts[i] for i in idx])):
                out[i] = p
        return out

    def classify_en2(self, texts: list[str], registers: list[str]) -> list:
        """英文段落再用英文第二分类器（专门见过国产大模型写的英文）打一次分。"""
        out = [None] * len(texts)
        if not (self.cls_en2 and self.cls_en2.ready):
            return out
        idx = [i for i, r in enumerate(registers) if r == "en"]
        if idx:
            for i, p in zip(idx, self.cls_en2.predict([texts[i] for i in idx])):
                out[i] = p
        return out

    def _hybrid_unit_scores(self, texts: list[str], register: str) -> list:
        """人机混写切分用的短文本分数：英文取第二、第四分类器的平均，现代汉语取中文第二分类器。"""
        regs = [register] * len(texts)
        if register == "en":
            a = self.classify_en2(texts, regs)
            b = self.cls_en4.predict(texts) if (self.cls_en4 and self.cls_en4.ready and texts) else [None] * len(texts)
            return [None if x is None and y is None else (x if y is None else y if x is None else (x + y) / 2)
                    for x, y in zip(a, b)]
        if register in ("zh_classical", "zh_poetry"):
            # 文言 / 诗词：专用分类器（文言、诗词）为主，通用中文分类器为辅（各占一半）
            a, b = self.classify(texts, regs), self.classify_second(texts, regs)
            return [None if x is None and y is None else (x if y is None else y if x is None else (x + y) / 2)
                    for x, y in zip(a, b)]
        # 现代汉语：中文第二分类器（新一代国产大模型）与主分类器取平均——C-ReD 的新闻类 AI 文字第二分类器只给 5 分左右
        a, b = self.classify_zh2(texts, regs), self.classify(texts, regs)
        return [None if x is None and y is None else (x if y is None else y if x is None else (x + y) / 2)
                for x, y in zip(a, b)]

    def _verify_quotes(self, segs):
        """引文核对：被当成“正文中引用的古文 / 诗词”（没有引用痕迹）的段落，逐句到古籍句库（famous.source_parts）里找出处。
        找得到出处的部分仍按引文不计入；夹在中间、找不到出处的部分（≥ QUOTE_VERIFY_MIN_CHARS 字）单独成段另行打分，
        模型判断几乎肯定是 AI 时计入（2026-11-13 cs1112：AI 仿写“夫学之难……”夹在《金刚经》和孔庙碑文之间，
        “书中所记……”接在《汉书》引文前，整段当引文排除）。返回 (新段落列表, 切开的段数)。"""
        from .famous import source_parts
        out, n_split = [], 0
        next_block = max((x.block for x in segs), default=0) + 1
        cjk = lambda t: len(re.findall(r"[\u4e00-\u9fff]", t))
        for s in segs:
            quote = (s.kind == "quotation" and getattr(s, "uncited", False)
                     and s.register in ("zh_classical", "zh_poetry"))
            # 正文里的文言 / 诗词也逐句核对（2026-11-13 cs1113-2：苏轼《日喻》原文与 AI 仿写的家书接在一段里，
            # 逐句判断时《日喻》中的一句被判成 AI）：与古籍原文逐句相同的部分按引文不计入，其余部分照常判断
            body = (config.SOURCE_VERIFY_BODY and s.kind == "body" and s.register in ("zh_classical", "zh_poetry"))
            if not (quote or body):
                out.append(s)
                continue
            head = s.title if (s.title and s.text.startswith(s.title)) else ""
            text = s.text[len(head):]
            parts = source_parts(text, strict=body)
            if body:
                # 正文：找得到出处的部分合计 ≥ SOURCE_VERIFY_MIN_CHARS 字才处理（零星一两句成语、典故不算）
                if not parts or sum(cjk(text[a:b]) for a, b, ok in parts if ok) < config.SOURCE_VERIFY_MIN_CHARS:
                    out.append(s)
                    continue
            # 引文：至少一块找得到出处、一块足够长且找不到出处，才切开
            elif not parts or not any(ok for _, _, ok in parts) or \
                    not any(not ok and cjk(text[a:b]) >= config.QUOTE_VERIFY_MIN_CHARS for a, b, ok in parts):
                out.append(s)
                continue
            # 太短的“找不到出处”部分（改了几个字的引文、过渡语）并入相邻的有出处部分
            merged = []
            for a, b, ok in parts:
                ok = ok or cjk(text[a:b]) < config.QUOTE_VERIFY_MIN_CHARS
                if merged and merged[-1][2] == ok:
                    merged[-1] = (merged[-1][0], b, ok)
                else:
                    merged.append((a, b, ok))
            n_split += 1
            for k, (a, b, ok) in enumerate(merged):
                piece = text[a:b]
                lead = len(piece) - len(piece.lstrip())
                if k == 0 and head:
                    piece, lead = head + piece, 0
                sub = Segment(index=0, text=piece.strip(), start=s.start + len(head) * (k > 0) + a + lead,
                              kind="quotation" if (ok or quote) else "body",
                              notes=list(s.notes) + [("引文核对：与古籍原文逐句相同（原句相同），按引文不计入" if ok else
                                                      "引文核对：古籍句库里找不到这几句的出处，单独判断")],
                              register=detect_register(piece) if not ok else s.register,
                              block=next_block, title=head if k == 0 else "")
                if sub.register not in ("zh_classical", "zh_poetry", "zh"):
                    sub.register = s.register
                sub.orig_block = s.block
                sub.uncited = (not ok) and quote
                sub.unsourced = (not ok) and quote
                next_block += 1
                out.append(sub)
        for k, x in enumerate(out):
            x.index = k
        return out, n_split

    def _hybrid_split(self, segs):
        """同一段里人写、AI 写的句子交替时，在作者换了的地方切开（见 app/hybrid.py）。返回 (新段落列表, 切开的段数)。"""
        from . import hybrid
        cfg = {"switch_cost": config.HYBRID_SWITCH_COST, "temp": config.HYBRID_TEMP,
               "min_en": config.HYBRID_MIN_EN_WORDS, "min_zh": config.HYBRID_MIN_ZH_CHARS,
               "ai_mean": config.HYBRID_AI_MEAN, "human_mean": config.HYBRID_HUMAN_MEAN,
               "size_weight": config.HYBRID_SIZE_WEIGHT, "relax": config.HYBRID_RELAX}
        out, n_split = [], 0
        next_block = max((x.block for x in segs), default=0) + 1
        for s in segs:
            classical = s.register in ("zh_classical", "zh_poetry")
            loose_quote = (config.HYBRID_SPLIT_UNCITED and classical and s.kind == "quotation"
                           and getattr(s, "uncited", False))
            if (s.kind != "body" and not loose_quote) or s.register not in ("zh", "en", "zh_classical", "zh_poetry"):
                out.append(s)
                continue
            head = s.title if (s.title and s.text.startswith(s.title)) else ""
            text = s.text[len(head):]
            if hybrid.size_of(text, s.register) < (2 * cfg["min_en"] if s.register == "en" else 2 * cfg["min_zh"]):
                out.append(s)
                continue
            spans = hybrid.sentence_units(text, s.register)
            if len(spans) < 3:
                out.append(s)
                continue
            tagger = {"en": self.tag_en, "zh": self.tag_zh, "zh_classical": self.tag_zc, "zh_poetry": self.tag_zp}.get(s.register)
            if s.register == "zh_poetry" and config.TAGGER_ZP_COUPLET_ONLY and not is_couplet_run(text, strict=True):
                tagger = None
            if classical and not (tagger and tagger.ready) and config.HYBRID_REQUIRE_TAGGER and not config.HYBRID_CLASSICAL:
                out.append(s)
                continue
            if not classical and config.HYBRID_REQUIRE_TAGGER and not (tagger and tagger.ready):
                out.append(s)
                continue
            if tagger and tagger.ready:
                # 句级标注模型：整段一起看（有上下文），按句子区间取平均；不再需要“相邻两句”的分数
                single = tagger.span_probs(text, spans)
                pairs = [None] * (len(spans) - 1)
            else:
                single = self._hybrid_unit_scores([text[a:b] for a, b in spans], s.register)
                pairs = self._hybrid_unit_scores([text[spans[k][0]:spans[k + 1][1]] for k in range(len(spans) - 1)], s.register)
            if classical:
                # 文言 / 诗词里夹着公开名篇（2026-11-06 cs1106：韩愈、柳宗元、王勃、苏轼的原文与 AI 仿写首尾相接）：
                # 语言模型几乎能逐字复现的句子（困惑度极低）就是名篇原文，按“人写”处理，与相邻的仿写分开
                if self.lm and self.lm.ready:
                    _, per = self.lm.score_with_spans(text, spans)
                    for k, r in enumerate(per):
                        if r and r.get("ppl") is not None and math.exp(r["ppl"]) < config.HYBRID_FAMOUS_PPL:
                            single[k] = 0.02
                            if k > 0 and pairs[k - 1] is not None:
                                pairs[k - 1] = min(pairs[k - 1], 0.5)
                            if k < len(pairs) and pairs[k] is not None:
                                pairs[k] = min(pairs[k], 0.5)
            plan = hybrid.plan_split(text, s.register, single, pairs, spans,
                                     dict(cfg, ai_mean=config.HYBRID_CL_AI_MEAN, min_zh=config.HYBRID_MIN_ZC_CHARS,
                                          relax=False, switch_cost=config.HYBRID_CL_SWITCH_COST,
                                          human_mean=config.HYBRID_CL_HUMAN_MEAN) if classical else cfg)
            if not plan:
                out.append(s)
                continue
            n_split += 1
            for k, (a, b, st, mean) in enumerate(plan):
                piece = text[a:b]
                lead = len(piece) - len(piece.lstrip())
                if k == 0 and head:
                    piece, lead = head + piece, 0
                sub = Segment(index=0, text=piece.strip(), start=s.start + len(head) * (k > 0) + a + lead, kind=s.kind,
                              notes=list(s.notes) + [("人机混写切分：这几句与前后文明显不同，单独判断" if st else
                                                      "人机混写切分：与相邻的疑似 AI 部分分开判断")],
                              register=s.register, block=next_block, title=head if k == 0 else "")
                sub.orig_block = s.block
                sub.hybrid = {"state": st, "mean": mean}
                if loose_quote:
                    sub.uncited = True
                next_block += 1
                out.append(sub)
        for k, x in enumerate(out):
            x.index = k
        return out, n_split

    def _coh_gain(self, text, a, b, ctx_chars=160, n_tok=24):
        """这一句开头 n_tok 个词元：有前文时比没有前文时平均每词元少多少惊奇度（nats）。大 = 与前文衔接紧密。"""
        if a is None or a <= 0:
            return None
        try:
            import torch
            lm = self.lm
            tok = lm.tok
            u_ids = tok(text[a:b], add_special_tokens=False)["input_ids"][:n_tok]
            c_ids = tok(text[max(0, a - ctx_chars):a], add_special_tokens=False)["input_ids"][-64:]
            if len(u_ids) < 4 or not c_ids:
                return None
            def nll(prefix, target):
                ids = torch.tensor([prefix + target])
                with lm._lock, torch.inference_mode():
                    lgt = lm.performer(input_ids=ids).logits[0, :-1].float()
                lp = torch.log_softmax(lgt, -1)
                ll = lp.gather(-1, ids[0, 1:].unsqueeze(-1)).squeeze(-1)
                return -ll[len(prefix) - 1:].mean().item()
            bos = [tok.eos_token_id] if tok.eos_token_id is not None else [c_ids[-1]]
            return nll(bos, u_ids) - nll(c_ids, u_ids)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _resegment(seg_out, hmm_ai, hmm_units, raw_text=""):
        """报告按“谁写的”重新分段（2026-11-11 用户 cs1111）：逐句判断为部分人写、部分 AI 的段落，在作者换了的句子处切开；
        原文里首尾相接（中间没有换行）、逐句判断为同一作者的相邻块再合并成一块显示——切文体、切长段只是为了打分，
        不再把一首词、一封信在报告里拆成两段。AI 率的计算不变（已按逐句字数计入）。"""
        out = []
        for g in seg_out:
            i = g.get("index")
            units = hmm_units.get(i)
            if not units or g.get("kind") != "body" or g.get("prob") is None:
                out.append(g)
                continue
            if g.get("partial") not in ("partial", "local"):
                h = dict(g)
                h["hmm_state"] = "ai" if g["level"] in ("high", "mid", "light") else "human"
                out.append(h)
                continue
            ai_level = g["level"] if g["level"] in ("high", "mid", "light") else "light"
            pieces = []
            for u in units:
                st = u["flag"] == "ai"
                if pieces and pieces[-1][0] == st:
                    pieces[-1][2] = u["start"] + u["chars"]
                else:
                    pieces.append([st, u["start"], u["start"] + u["chars"]])
            pieces[0][1] = 0
            pieces[-1][2] = len(g["text"])
            for k in range(1, len(pieces)):
                pieces[k][1] = pieces[k - 1][2]
            for st, a, b in pieces:
                txt = g["text"][a:b]
                if not txt.strip():
                    continue
                lead = len(txt) - len(txt.lstrip())
                h = dict(g)
                h.update({"start": (g.get("start") or 0) + a + lead, "chars": len(txt.strip()), "text": txt.strip(),
                          "partial": None, "ai_chars": None, "windows": [], "hmm_state": "ai" if st else "human",
                          "level": ai_level if st else "low", "near_threshold": False,
                          "label": ("疑似 AI（逐句判断：这几句与前后文明显不同，像 AI 写的）" if st
                                    else "未见 AI 特征（逐句判断：这几句像人写）")})
                out.append(h)
        raw = raw_text or ""
        merged, cur, prev_end = [], 0, None
        for h in out:
            k = h["text"].strip()[:15]
            o = raw.find(k, max(0, cur - 200)) if (raw and k) else -1
            if o >= 0:
                cur = o + 1
            p = merged[-1] if merged else None
            contiguous = (o >= 0 and prev_end is not None and o >= prev_end and "\n" not in raw[prev_end:o]
                          and len(raw[prev_end:o].strip()) <= 2)
            if (p is not None and h.get("hmm_state") and p.get("hmm_state") == h["hmm_state"]
                    and (contiguous or (p.get("block") == h.get("block") and p.get("register") == h.get("register")))):
                sep = raw[prev_end:o] if contiguous else ""
                p["text"] = p["text"] + sep + h["text"]
                p["chars"] = len(p["text"])
                if h.get("register") != p.get("register"):
                    p["register_mixed"] = True
                prev_end = (o + len(h["text"])) if o >= 0 else None
                continue
            merged.append(dict(h))
            prev_end = (o + len(h["text"])) if o >= 0 else None
        for k, h in enumerate(merged):
            h["orig_index"] = h.get("index")
            h["index"] = k
        return merged

    @staticmethod
    def _snap_reg(units, path, max_chars, gain=None, min_gain=0.0):
        """换作者的位置对齐到文体切换处（2026-11-12 用户 cs1111）：现代文里接着一段 AI 写的浅文言时，
        逐句判断常把切换点提前一两句（“黑洞不是空洞无物……”这类说明性句子三个模型都给高分）。
        一段判为 AI 的句子，若开头（或结尾）几句和相邻人写部分同一文体、其余部分是另一文体，
        且这几句不超过 max_chars 字、也不比另一文体部分长，就把这几句改回人写——作者切换和文体切换重合的可能远大于提前两句。
        只往“人写”方向改，不会增加误判。gain 给出时还要求接缝处语意衔接度 ≥ min_gain（这几句确实接着人写部分的话头说下去）：
        mx 基准上不加此条件时 AI 检出少了 197 字（AI 自己的现代文过渡句被改成人写）。"""
        pair = {"zh", "zh_classical"}
        n = len(units)
        ln = [u[2] - u[1] for u in units]
        reg = [u[0].register for u in units]
        path = list(path)
        k = 0
        while k < n:
            if not path[k]:
                k += 1
                continue
            s = k
            while k < n and path[k]:
                k += 1
            e = k
            for side in ("L", "R"):
                if side == "L":
                    if s == 0 or path[s - 1] or reg[s - 1] not in pair:
                        continue
                    r0, j = reg[s - 1], s
                    while j < e and reg[j] == r0:
                        j += 1
                    pre, rest = range(s, j), range(j, e)
                else:
                    if e >= n or path[e] or reg[e] not in pair:
                        continue
                    r0, j = reg[e], e
                    while j > s and reg[j - 1] == r0:
                        j -= 1
                    pre, rest = range(j, e), range(s, j)
                if not len(pre) or not len(rest) or not all(reg[i] in pair and reg[i] != r0 for i in rest):
                    continue
                cp, cr = sum(ln[i] for i in pre), sum(ln[i] for i in rest)
                if gain is not None:
                    g = gain(s if side == "L" else e)
                    if g is None or g < min_gain:
                        continue
                if cp <= max_chars and cp <= cr:
                    for i in pre:
                        path[i] = 0
        return path

    def _unit_hmm(self, segs, results, combos, smoothed, prof, memo, human_work, doc_ai, raw_text=""):
        """逐句作者判断：同一个原始自然段（切文体、切长段之前）的全部句子单元一起做两状态维特比解码；
        同一首词 / 诗（见 app/structure.py）里的句子绑在一起，只能整体判为人写或 AI。
        返回 ({段号: AI 字数}, {段号: [句子窗口]}, {应由引文改为计入的段号})。"""
        from . import hybrid, structure
        if not config.UNIT_HMM:
            return {}, {}, set()
        th = {k: float(v) for k, v in (kv.split(":") for kv in config.UNIT_HMM_TH.split(","))}
        lg = lambda p: math.log(min(max(p, 0.01), 0.99) / (1 - min(max(p, 0.01), 0.99)))
        # 每段在原文中的位置与所属原始自然段
        raw = raw_text or ""
        cur, rpos = 0, {}
        for x in sorted(segs, key=lambda y: y.start):
            k = x.text.strip()[:15]
            o = raw.find(k, max(0, cur - 200)) if (raw and k) else -1
            if o >= 0:
                rpos[x.index] = o - max(0, x.text.find(k))
                cur = o + 1
        def para_of(x):
            if x.index in rpos:
                return ("p", raw.count("\n", 0, rpos[x.index]))
            return ("b", getattr(x, "orig_block", x.block))
        blocks: dict = {}
        for x in segs:
            if x.register == "zh_poetry" and not (self.tag_zp and self.tag_zp.ready and
                                                  (not config.TAGGER_ZP_COUPLET_ONLY or is_couplet_run(x.text, strict=True))):
                continue      # 诗词还没有句级模型：逐行打分不可靠（2026-11-11 回归：AI 诗被逐行拆成人写），仍按整段判断
                # 诗词句级模型只管“几副对联首尾相接”；律诗、宋词、歌谣仍按整首判断（2026-11-13 cs1111：
                # 句级模型一载入，AI 词 / 歌谣 3 首被逐句拆成人写，AI 检出 90.1% → 85.3%）
            if x.register in th and x.kind in ("body", "quotation"):
                blocks.setdefault(para_of(x), []).append(x)
        hmm_ai, hmm_units, promote = {}, {}, set()
        famous_words = ("名篇", "逐字复现", "原句相同")
        for blk, xs in blocks.items():
            xs.sort(key=lambda x: rpos.get(x.index, x.start))
            # 本自然段里的词 / 诗（原文坐标）
            works, line_start = [], 0
            if config.UNIT_HMM_WORKS and raw and blk[0] == "p":
                lo = min(rpos[x.index] for x in xs if x.index in rpos)
                hi = max(rpos[x.index] + len(x.text) for x in xs if x.index in rpos)
                line_start = raw.rfind("\n", 0, lo) + 1
                line_end = max(hi, raw.find("\n", lo) if raw.find("\n", lo) >= 0 else len(raw))
                works = [(a + line_start, b + line_start, k) for a, b, k in structure.work_spans(raw[line_start:line_end])]
            units = []   # (段, 段内起, 段内止, 证据, 原文起, 未经英文标注模型加强的证据)
            n_boost = 0
            for x in xs:
                sp = hybrid.sentence_units(x.text, x.register, 12, 8)
                if not sp:
                    continue
                base0 = rpos.get(x.index)
                if works and base0 is not None:
                    # 句子单元不跨越一首词 / 诗的首尾（短句并入相邻句时可能跨过去）
                    cuts = sorted({c - base0 for wa, wb, _ in works for c in (wa, wb) if 0 < c - base0 < len(x.text)})
                    if cuts:
                        sp2 = []
                        for a, b in sp:
                            pts = [a] + [c for c in cuts if a < c < b] + [b]
                            sp2 += [(u, v) for u, v in zip(pts, pts[1:]) if x.text[u:v].strip()]
                        sp = sp2
                tx = [x.text[a:b] for a, b in sp]
                regs = [x.register] * len(tx)
                c1 = (self.classify_en2(tx, regs) if x.register == "en" else
                      self.classify_zh2(tx, regs) if x.register == "zh" else self.classify(tx, regs))
                tg = {"en": self.tag_en, "zh": self.tag_zh, "zh_classical": self.tag_zc, "zh_poetry": self.tag_zp}.get(x.register)
                tgp = tg.span_probs(x.text, sp) if (tg and tg.ready) else [None] * len(sp)
                fam = x.index in memo or any(w in n for n in x.notes for w in famous_words)
                thr = float(prof[x.index][0].get("threshold", 0.5)) if x.index in prof else 0.5
                pr = smoothed.get(x.index)
                b = 1.0 if (x.counted and x.index not in human_work and
                            ((pr is not None and pr >= thr) or x.index in doc_ai)) else 0.0
                base = rpos.get(x.index)
                for (a, bnd), p1, p2 in zip(sp, c1, tgp):
                    if fam:
                        e = -6.0
                    else:
                        xs_ = []
                        if p2 is not None:
                            xs_.append((lg(p2), 1 - config.UNIT_HMM_ALPHA))
                        if p1 is not None:
                            xs_.append((lg(p1), config.UNIT_HMM_ALPHA if p2 is not None else 1.0))
                        e = (sum(v * w for v, w in xs_) / sum(w for _, w in xs_) - lg(th[x.register])) if xs_ else 0.0
                        # 整篇判断 / 作品判断为 AI 的段落先验更强：逐句模型须有很强的证据才能把其中句子改判为人写
                        # （2026-11-11 回归：整篇判断为 AI 的文章被逐句判断大半改为人写，文档 AI 比例 1.0→0.22）
                        e += (config.UNIT_HMM_BETA_DOC if (b and x.index in doc_ai) else config.UNIT_HMM_BETA) * (2 * b - 1)
                        # 英文句级标注模型非常肯定（≥ UNIT_HMM_SURE_EN）的句子：只看标注模型，不再被整段分类器 / 整段先验拉低
                        # （2026-11-12 cs1111：真人文章中间插入的 AI 菜谱 542 字、天气说明 111 字，标注模型 0.94–0.96，
                        # 但英文分类器只给 0.07–0.16，逐句判断仍判为人写）。依据（mx 混合文本 1027 个英文句子单元）：
                        # 标注模型 ≥ 0.95 的真人句子 1/649，AI 句子 178/378。
                        boosted = (x.register == "en" and p2 is not None and p2 >= config.UNIT_HMM_SURE_EN
                                   and lg(p2) - lg(th["en"]) > e)
                        if boosted:
                            n_boost += 1
                            e0 = e
                            e = lg(p2) - lg(th["en"])
                    units.append((x, a, bnd, e, None if base is None else base + a, e0 if (not fam and boosted) else e))
            if len(units) < config.UNIT_HMM_MIN_UNITS:
                continue
            # 同一首词 / 诗里的句子绑成一组（按原文位置判断）
            groups = [[k] for k in range(len(units))]
            if works:
                wid = []
                for u in units:
                    w = None
                    if u[4] is not None:
                        mid = u[4] + (u[2] - u[1]) // 2
                        for j, (wa, wb, _) in enumerate(works):
                            if wa <= mid < wb:
                                w = j
                                break
                    wid.append(w)
                groups = []
                for k, w in enumerate(wid):
                    if groups and w is not None and wid[groups[-1][-1]] == w:
                        groups[-1].append(k)
                    else:
                        groups.append([k])
            gev = []
            for g in groups:
                ln = [units[k][2] - units[k][1] for k in g]
                gev.append(sum(units[k][3] * l for k, l in zip(g, ln)) / max(1, sum(ln)))
            sw_cost = config.UNIT_HMM_SWITCH
            if config.UNIT_HMM_COH > 0 and raw and self.lm and self.lm.ready and len(groups) >= 2:
                # 语意衔接度（2026-11-11 用户 cs1111）：每组开头一句“有前文”比“没有前文”好预测多少（nats / 词元）。
                # 意思一气呵成处（衔接度高）换作者的代价加大；话题断开处（≈0 或负）代价减小，便于在那里切开
                sw_cost = []
                for g in groups[1:]:
                    u = units[g[0]]
                    if u[0].register not in config.UNIT_HMM_COH_REGS:
                        sw_cost.append(config.UNIT_HMM_SWITCH)
                        continue
                    gain = self._coh_gain(raw, u[4], u[4] + (u[2] - u[1])) if u[4] is not None else None
                    f = 1.0 if gain is None else min(config.UNIT_HMM_COH_MAX, max(config.UNIT_HMM_COH_MIN, gain / config.UNIT_HMM_COH))
                    sw_cost.append(config.UNIT_HMM_SWITCH * f)
            gpath = hybrid.viterbi(gev, sw_cost, 1.0)
            path = [0] * len(units)
            for g, st in zip(groups, gpath):
                for k in g:
                    path[k] = st
            if config.UNIT_HMM_REG_SNAP > 0:
                gf = None
                if config.UNIT_HMM_REG_SNAP_COH > 0 and raw and self.lm and self.lm.ready:
                    gf = lambda k: (self._coh_gain(raw, units[k][4], units[k][4] + (units[k][2] - units[k][1]))
                                    if units[k][4] is not None else None)
                path = self._snap_reg(units, path, config.UNIT_HMM_REG_SNAP, gf, config.UNIT_HMM_REG_SNAP_COH)
            # 只在这一自然段里确实有“人写 + AI”两种成块的文字时才按逐句结果计入；整段都判为人写或都判为 AI 时，
            # 沿用整段 / 整篇的判断（2026-11-11 回归：整篇 AI 的书信、对联被逐句判断整段降为“人写”，AI 检出 969→935）
            ai_ch = sum(u[2] - u[1] for u, st in zip(units, path) if st)
            hu_ch = sum(u[2] - u[1] for u, st in zip(units, path) if not st)
            if min(ai_ch, hu_ch) < config.UNIT_HMM_MIN_MIX:
                # 英文标注模型加强后整段都成了 AI，而不加强时本是“人写 + AI”混合：仍按逐句结果（全部 AI）计入，
                # 不退回整段判断（2026-11-12 XN 回归：AI 菜谱、书信因此从逐句计入退回“说明类文字更严门槛”，4+5 篇漏检）
                mixed0 = False
                if n_boost:
                    gev0 = [sum(units[k][5] * (units[k][2] - units[k][1]) for k in g) /
                            max(1, sum(units[k][2] - units[k][1] for k in g)) for g in groups]
                    p0 = [0] * len(units)
                    for g, st in zip(groups, hybrid.viterbi(gev0, sw_cost, 1.0)):
                        for k in g:
                            p0[k] = st
                    a0 = sum(u[2] - u[1] for u, st in zip(units, p0) if st)
                    h0 = sum(u[2] - u[1] for u, st in zip(units, p0) if not st)
                    mixed0 = min(a0, h0) >= config.UNIT_HMM_MIN_MIX
                if not (mixed0 and hu_ch < config.UNIT_HMM_MIN_MIX):
                    continue
            for (x, a, bnd, e, _, _), st in zip(units, path):
                hmm_ai[x.index] = hmm_ai.get(x.index, 0) + (bnd - a if st else 0)
                hmm_units.setdefault(x.index, []).append({"start": a, "chars": bnd - a, "text": x.text[a:bnd],
                                                          "prob": None, "second": None,
                                                          "flag": "ai" if st else "human"})
            for x in xs:
                if x.kind == "quotation" and x.index in hmm_ai and hmm_ai[x.index] >= 0.5 * len(x.text) \
                        and combos[x.index]["prob"] is not None and x.index not in memo \
                        and not any(w in n for n in x.notes for w in famous_words):
                    promote.add(x.index)
        return hmm_ai, hmm_units, promote

    def classify_zh2(self, texts: list[str], registers: list[str]) -> list:
        """现代汉语段落再用中文第二分类器（见过新一代国产大模型的散文、游记、论文）打一次分。"""
        out = [None] * len(texts)
        if not (self.cls_zh2 and self.cls_zh2.ready):
            return out
        idx = [i for i, r in enumerate(registers) if r == "zh"]
        if idx:
            for i, p in zip(idx, self.cls_zh2.predict([texts[i] for i in idx])):
                out[i] = p
        return out

    def raw_scores(self, texts: list[str], progress=None, registers: list[str] | None = None) -> list[dict]:
        """对若干段文字算原始分数（校准、评估时也用这个）。"""
        registers = registers or [detect_register(t) for t in texts]
        out = [style_scores(t) for t in texts]
        for i, p in enumerate(self.classify(texts, registers)):
            if p is not None:
                out[i]["classifier"] = p
        for i, p in enumerate(self.classify_second(texts, registers)):
            if p is not None:
                out[i]["classifier_mpu"] = p
        for i, p in enumerate(self.classify_en2(texts, registers)):
            if p is not None:
                out[i]["classifier_en2"] = p
        if self.lm and self.lm.ready:
            for i, t in enumerate(texts):
                t0 = time.time()
                r = self.lm.score(t)
                if r:
                    out[i].update(r)
                    dt = time.time() - t0
                    if dt > 0:
                        tps = r["tokens"] * 2 / dt  # 两个模型
                        self.tokens_per_sec = tps if self.tokens_per_sec is None else 0.8 * self.tokens_per_sec + 0.2 * tps
                if progress:
                    progress(i + 1, len(texts))
        elif progress:
            progress(len(texts), len(texts))
        return out

    def analyze(self, text: str, mode: str = "full", exclude_references: bool = True,
                flag_quotations: bool = True, progress=None, genre: str = "auto") -> dict:
        t_start = time.time()
        self.wait_loaded()
        self.maybe_refresh_calibration()
        # 按固定宽度断行的英文散文先合并成段落（见 segmenter.unwrap_hard_wraps）：否则每行被当成独立短段落，文心作品 AI 率从 83% 掉到 35%
        text, n_zw, n_hw = deobfuscate(text)
        text, unwrapped = unwrap_hard_wraps(text)
        n_lab = n_scr = 0
        pre_split = text
        if config.SPLIT_INLINE:
            text, n_lab = split_inline_labels(text)
            text, n_scr = split_script_switches(text)
            if config.SPLIT_REGISTER:
                text, n_fam = split_famous_spans(text)
                text, n_wk = split_work_spans(text)
                text, n_reg = split_register_switches(text)
                n_reg += n_fam + n_wk
                n_scr += n_reg
        segs = segment_text(text, exclude_references, flag_quotations, genre)
        # 参考文献核查要联网查询，和模型打分同时进行（见 app/refcheck.py）
        _ref_texts = [s.text for s in segs if s.kind == "reference"]
        _ref_future = None
        if _ref_texts and config.REF_CHECK:
            import concurrent.futures as _cf
            from .refcheck import check_references
            _ref_pool = _cf.ThreadPoolExecutor(max_workers=1)
            _ref_future = _ref_pool.submit(check_references, _ref_texts)
            _ref_pool.shutdown(wait=False)
        # 全文都被判为引文/参考文献时，退回为全部计入，否则报告里没有任何数值
        fallback_all = bool(segs) and not any(s.counted for s in segs)
        if fallback_all:
            for s in segs:
                if s.kind != "body":
                    s.notes = list(s.notes) + ["全文均被判为引文/参考文献，已改为计入"]
                    s.kind = "body"
        # 某种文体的检测在评估中不够稳定时（目前可能是诗词），该文体只给参考值、不计入 AI 率
        ref_only_regs = {r for r in ("zh_poetry",)
                         if (self.cal.get("profiles") or {}).get(r, {}).get("reference_only")}
        for s in segs:
            if s.kind == "body" and s.register in ref_only_regs:
                s.kind = "reference_only"
                s.notes = list(s.notes) + [f"{REGISTER_NAMES.get(s.register, s.register)}检测不够稳定，结果仅供参考、不计入 AI 率"]
        # 篇幅过短的英文作品（按标题分开的一篇，正文不足 EN_MIN_WORDS_WORK 词）不判定、只给参考值：
        # 业内做法——Turnitin 不足 300 词不出结果、GPTZero / Lynote 等也拒绝过短文本；本工具的实测（2026-10-30，用户 cs1021）
        # 里 31 词的真人小说片段被判 83.7%。整篇只有这么短时同样只给参考值。
        en_words: dict = {}
        for s in segs:
            if s.kind == "body":
                d = en_words.setdefault(s.block, [0, True])
                d[0] += len(score_text(s).split()) if s.register == "en" else 0
                d[1] = d[1] and s.register == "en"
        for s in segs:
            d = en_words.get(s.block)
            if s.kind == "body" and d and d[1] and d[0] < config.EN_MIN_WORDS_WORK:
                s.kind = "reference_only"
                s.notes = list(s.notes) + [f"篇幅过短（不足 {config.EN_MIN_WORDS_WORK} 词），检测结果不可靠，仅供参考、不计入 AI 率"]
        n_hybrid = 0
        if config.QUOTE_VERIFY:
            segs, n_qv = self._verify_quotes(segs)
            n_hybrid += n_qv
        if config.HYBRID_SPLIT:
            segs, n_hy = self._hybrid_split(segs)
            n_hybrid += n_hy
        counted = [s for s in segs if s.counted]
        # 不计入的引文段落也打分，作为"参考值"显示（不影响 AI 率）；参考文献条目没有检测意义，不打分
        ref_scored = [s for s in segs if s.kind in ("quotation", "reference_only")]
        lm_targets = counted
        sampled = False
        if mode == "fast" and len(counted) > config.FAST_MODE_MAX_SEGMENTS:
            step = len(counted) / config.FAST_MODE_MAX_SEGMENTS
            lm_targets = [counted[int(i * step)] for i in range(config.FAST_MODE_MAX_SEGMENTS)]
            sampled = True
        else:
            lm_targets = counted + ref_scored
        lm_ids = {s.index for s in lm_targets}

        results = {s.index: {} for s in segs}
        scored = counted + ref_scored
        # 分类器很快：对所有正文和引文段落都算（按文体选分类器）
        sc_texts, sc_regs = [score_text(s) for s in scored], [s.register for s in scored]
        for s, p in zip(scored, self.classify(sc_texts, sc_regs)):
            if p is not None:
                results[s.index]["classifier"] = p
        for s, p in zip(scored, self.classify_second(sc_texts, sc_regs)):
            if p is not None:
                results[s.index]["classifier_mpu"] = p
        for s, p in zip(scored, self.classify_en2(sc_texts, sc_regs)):
            if p is not None:
                results[s.index]["classifier_en2"] = p
        for s, p in zip(scored, self.classify_zh2(sc_texts, sc_regs)):
            if p is not None:
                results[s.index]["classifier_zh2"] = p
        if self.cls_en3 and self.cls_en3.ready:
            idx = [k for k, r in enumerate(sc_regs) if r == "en"]
            for k, p in zip(idx, self.cls_en3.predict([sc_texts[k] for k in idx]) if idx else []):
                results[scored[k].index]["classifier_en3"] = p
        if self.cls_en4 and self.cls_en4.ready:
            idx = [k for k, r in enumerate(sc_regs) if r == "en"]
            for k, p in zip(idx, self.cls_en4.predict([sc_texts[k] for k in idx]) if idx else []):
                results[scored[k].index]["classifier_en4"] = p
        # 逐句窗口：长段落按句子切成小窗口，供“部分疑似 / 局部疑似”判断（诗词短小，不切）
        win_spans = {}
        if config.WINDOWS_ENABLED:
            for s in counted:
                if s.register in ("zh", "zh_classical", "en"):
                    sp = window_spans(score_text(s), s.register)
                    if sp:
                        win_spans[s.index] = sp
        win_raw: dict = {i: [{} for _ in sp] for i, sp in win_spans.items()}
        # 语言模型较慢：快速模式下抽样
        if self.lm and self.lm.ready:
            done = 0
            for s in segs:
                if s.index not in lm_ids:
                    continue
                t0 = time.time()
                if s.index in win_spans:
                    r, wl = self.lm.score_with_spans(score_text(s), win_spans[s.index])
                    for d, w in zip(win_raw[s.index], wl):
                        if w:
                            d.update(w)
                else:
                    r = self.lm.score(score_text(s))
                if r:
                    results[s.index].update(r)
                    dt = time.time() - t0
                    if dt > 0:
                        tps = r["tokens"] * 2 / dt
                        self.tokens_per_sec = tps if self.tokens_per_sec is None else 0.8 * self.tokens_per_sec + 0.2 * tps
                done += 1
                if progress:
                    progress(done, len(lm_ids))

        # 窗口的分类器分数（分类器很快）
        if win_spans:
            seg_of = {x.index: x for x in segs}
            keys = [(i, k) for i, sp in win_spans.items() for k in range(len(sp))]
            wt = [score_text(seg_of[i])[a:b] for i, sp in win_spans.items() for (a, b) in sp]
            wr = [seg_of[i].register for i, _ in keys]
            for name, fn in (("classifier", self.classify), ("classifier_mpu", self.classify_second),
                             ("classifier_en2", self.classify_en2), ("classifier_zh2", self.classify_zh2)):
                for (i, k), p in zip(keys, fn(wt, wr)):
                    if p is not None:
                        win_raw[i][k][name] = p
            for (i, k), t in zip(keys, wt):
                win_raw[i][k].update(style_scores(t))
                win_raw[i][k]["_text"] = t

        cal = self.cal
        styles = {s.index: stylometry.features(score_text(s)) for s in scored}
        for s in scored:
            results[s.index].update(style_scores(score_text(s), styles[s.index]))

        # 1) 每段按自己的文体用对应的校准参数打分（引文段落的分数只作参考值）
        scored_ids = {s.index for s in scored}
        prof, prof_name = {}, {}
        has_short = bool((cal.get("profiles") or {}).get("zh_short"))
        # 是不是英文论文按"每篇作品"判断，而不是整个文件：文集里论文和故事、散文混排时（2026-10 用户的 104.docx），
        # 以前整个文件被认作论文，故事和散文就用不上非论文的整篇判断，豆包散文、ChatGPT 童话都漏检
        blk_text: dict = {}
        for s_ in segs:
            blk_text.setdefault(s_.block, []).append(s_.text)
        paper_blocks = ({b for b, t in blk_text.items() if is_english_paper("\n".join(t))}
                        if (cal.get("profiles") or {}).get("en_paper") else set())
        en_paper = bool(paper_blocks)
        has_en_poetry = bool((cal.get("profiles") or {}).get("en_poetry"))
        for s in segs:
            reg = s.register
            if reg == "zh" and has_short and len(score_text(s)) < config.SHORT_SEGMENT_CHARS:
                reg = "zh_short"
            if reg == "en" and s.block in paper_blocks:
                reg = "en_paper"
            elif reg == "en" and has_en_poetry and is_en_verse(s.text):
                # 英文诗单独校准（2026-10：散文的阈值认不出 AI 写的十四行诗，用户的 Gemini 诗组 4 首全部漏判）
                reg = "en_poetry"
            prof[s.index] = scoring.profile_for(cal, reg)
            prof_name[s.index] = reg
            if reg == "zh_poetry" and (is_couplet(score_text(s)) or is_couplet_run(score_text(s))):
                # 对联单独的阈值：诗词阈值按当代诗词"人写误判约 5%"定，对联只有十几二十个字，同样的阈值几乎认不出 AI 对联。
                # 用没参与训练的 500 副真人对联（couplet-dataset）测定：分类器 0.975 时真人对联误判约 4%、AI 对联检出约 69%。
                pc, ok = prof[s.index]
                tc = scoring.combine({"classifier": config.COUPLET_CLASSIFIER_THRESHOLD}, pc)["prob"]
                if tc is not None and tc < float(pc.get("threshold", 0.5)):
                    prof[s.index] = ({**pc, "threshold": round(tc, 4)}, ok)
        memo = {s.index for s in scored if memorized(results[s.index])}
        # 中文名篇原文（如朱自清《背影》）：困惑度极低说明语言模型逐字背过，不论分类器怎么判，都不计入 AI 率
        # 古诗文名篇同理（语言模型背熟了大量唐诗宋词、古文）：评估集里 1300 多段 AI 诗词文言困惑度低于 7 的只有 2 段，
        # 而《唐诗三百首》《宋词三百首》有约 35%、东坡等名家古文也常低于 7
        famous_ppl = {"zh": config.FAMOUS_PPL_ZH, "zh_classical": config.FAMOUS_PPL_CLASSICAL,
                      "zh_poetry": config.FAMOUS_PPL_CLASSICAL}
        famous = {s.index for s in counted if s.register in famous_ppl and results[s.index].get("ppl") is not None
                  and math.exp(results[s.index]["ppl"]) < famous_ppl[s.register]}
        for s in segs:
            if s.index in famous:
                s.kind = "quotation"
                s.notes = list(s.notes) + ["语言模型几乎能逐字复现，疑为公开名篇原文，不计入 AI 率"]
        # 古诗词名篇：一半以上的句子与《全唐诗》《全宋词》等古人原句逐字相同（引用时改了一两个字、只引半首也能认出）
        for s in counted:
            if s.register in ("zh_poetry", "zh_classical") and s.index not in famous and is_famous_verse(strip_list_mark(s.text)):
                famous.add(s.index)
                s.kind = "quotation"
                s.notes = list(s.notes) + ["一半以上的句子与古人诗词原句相同，疑为引用的名篇，不计入 AI 率"]
        memo |= famous
        counted = [s for s in segs if s.counted]

        def for_combine(i):
            if i in memo:
                return {k: v for k, v in results[i].items() if k not in LM_KEYS}
            return results[i]
        combos = {s.index: (scoring.combine(for_combine(s.index), prof[s.index][0]) if s.index in scored_ids
                            else {"prob": None, "signals": {}})
                  for s in segs}
        # 1.9) 高分"引文"合议：AI 拟古作品（骚体、乐府、题解里的原文、文言寓言）常被当成引文排除。同一文档里
        #      ≥ QUOTE_POOL_MIN_SEGS 段引文（各 ≥ QUOTE_POOL_MIN_CHARS 字，非名篇）得分都 ≥ QUOTE_POOL_PROB，且这些高分引文
        #      占全部引文字数 ≥ QUOTE_POOL_SHARE 时，改按正文计入。真人论文大量引用古籍时，真古文得 ≥0.95 的只约 1.4%，
        #      占比条件保证引文再多也不会触发。依据（2026-11-10，25 个测试集）：真人文档 0 篇触发，AI 文档 67 篇触发。
        if config.QUOTE_UNCITED_PROB < 1:
            # 没有任何引用痕迹的文言“引文”，文言模型又几乎肯定是 AI 写的 → 改为计入（2026-11-11 cs1111）。
            # 依据：现有测试集里被当作引文排除的文言段，真人 365 段中 ≥ 0.97 的只有 4 段，AI 354 段中有 82 段
            # 2026-11-12 降到 0.93：X9 回归里仍被当作引文的文言段（≥40 字），真人 137 段 ≥0.93 的 0 段（≥0.90 的 2 段），
            # AI 99 段 ≥0.93 的 43 段（≥0.97 的 14 段，多为有引用痕迹、不受此规则影响）
            fam_words0 = ("名篇", "逐字复现", "原句相同")
            for x in segs:
                thr = config.QUOTE_UNCITED_PROB_PO if x.register == "zh_poetry" else config.QUOTE_UNCITED_PROB
                if getattr(x, "unsourced", False):
                    thr = config.QUOTE_VERIFY_PROB
                if (thr > 0 and x.kind == "quotation" and getattr(x, "uncited", False) and x.index not in memo
                        and len(x.text) >= config.QUOTE_UNCITED_MIN_CHARS and combos[x.index]["prob"] is not None
                        and combos[x.index]["prob"] >= thr
                        and not any(w in n for n in x.notes for w in fam_words0)):
                    x.kind = "body"
                    x.notes = list(x.notes) + [("古籍里找不到出处" if getattr(x, "unsourced", False) else "没有引用痕迹") + "，且" + {"zh_poetry": "诗词", "zh": "现代文"}.get(x.register, "文言") + "模型判断几乎肯定是 AI 写的，改为计入"]
            counted = [x for x in segs if x.counted]
        if config.QUOTE_POOL_MIN_SEGS > 0:
            fam_words = ("名篇", "逐字复现", "原句相同")
            qs = [x for x in segs if x.kind == "quotation" and x.index not in memo
                  and not any(w in n for n in x.notes for w in fam_words)]
            qchars = sum(len(x.text) for x in qs) or 1
            hi = [x for x in qs if len(x.text) >= config.QUOTE_POOL_MIN_CHARS and combos[x.index]["prob"] is not None
                  and combos[x.index]["prob"] >= config.QUOTE_POOL_PROB]
            if len(hi) >= config.QUOTE_POOL_MIN_SEGS and sum(len(x.text) for x in hi) / qchars >= config.QUOTE_POOL_SHARE:
                for x in hi:
                    x.kind = "body"
                    x.notes = list(x.notes) + ["原按引文排除；但同篇多段“引文”都极像 AI 拟作，改为计入"]
                counted = [x for x in segs if x.counted]
        # 2) 与相邻正文段落平滑：只在同一篇作品、同一文体的正文段落之间进行（标题行分开的作品互不影响）
        smoothed = {}
        segs_by_idx = {s.index: s for s in segs}
        groups: dict = {}
        for s in counted:
            groups.setdefault((s.block, s.register), []).append(s.index)
        for idxs in groups.values():
            if not config.HYBRID_SMOOTH_BARRIER:
                smoothed.update(zip(idxs, scoring.smooth([combos[i]["prob"] for i in idxs], config.SMOOTHING)))
                continue
            # 人机混写切分出来的块已判定“作者换了”：不再与前后段落互相平滑（2026-11-07 cs1105：一段 1023 字的 AI
            # 自身 93 分，被旁边切出来的 1 分真人块拉到 58 分、漏判）。切出来的块各自独立，也把前后段落隔开
            # 切出来的块在另一篇“作品”编号里：原作品里被它隔开的前后段落并不相邻，也不互相平滑
            # （2026-11-07 cs1105 调试：AI 段的邻居变成了隔着切分块的两段真人文字，被拉到 58 分）
            run = []
            for i in idxs + [None]:
                piece = i is not None and getattr(segs_by_idx[i], "hybrid", None) is not None
                if run and i is not None and not piece and any(getattr(segs_by_idx.get(j), "hybrid", None) is not None for j in range(run[-1] + 1, i)):
                    smoothed.update(zip(run, scoring.smooth([combos[j]["prob"] for j in run], config.SMOOTHING)))
                    run = []
                if i is None or piece:
                    if run:
                        smoothed.update(zip(run, scoring.smooth([combos[j]["prob"] for j in run], config.SMOOTHING)))
                    run = []
                    if piece:
                        smoothed[i] = combos[i]["prob"]
                else:
                    run.append(i)

        # 3) 整篇一致性（参考 Turnitin / 知网按“整篇作品”给结论的做法）：AI 文章通常整篇一次生成。
        #    同一篇作品（同一标题下、同一文体）里，已判为疑似 AI 的文字占多数时，
        #    本篇中“接近阈值”的段落也按轻度疑似计入，并注明原因。只会把接近阈值的段落往上拉，
        #    不会影响整体判为人写的作品。
        work_ai = set()
        for (blk, reg), idxs in groups.items():
            if len(idxs) < 3:
                continue
            thr_g = {i: float(prof[i][0].get("threshold", 0.5)) for i in idxs}
            tot = sum(len(segs_by_idx[i].text) for i in idxs if smoothed.get(i) is not None)
            hit = sum(len(segs_by_idx[i].text) for i in idxs
                      if smoothed.get(i) is not None and smoothed[i] >= thr_g[i])
            if tot and hit >= config.WORK_MAJORITY * tot:
                work_ai.update(i for i in idxs if smoothed.get(i) is not None
                               and thr_g[i] - scoring.NEAR_MARGIN <= smoothed[i] < thr_g[i])

        # 4) 英文学术论文的整篇判断（与 Turnitin、GPTZero 按整篇文档给结论一致）：同一篇论文里，"国产大模型英文分类器"
        #    各段得分的中位数（按字数加权）达到 EN_PAPER_DOC_THRESHOLD 时，整篇按 AI 生成计。
        #    依据：单段得分会因分段位置大幅波动（同一段引言，带不带上一行"Keywords"能差出 14% 与 96%），
        #    而整篇的中位数很稳定——没参与训练的 110 篇真人论文全文（多为中国作者）中位数最高 0.38，
        #    226 篇国产模型写的论文最低 0.95。
        # "整篇判断"只带上本身也像 AI 的段落：所在作品整体像 AI，但这一段自己的整篇分类器得分很低（< DOC_CARRY_FLOOR），
        # 多半是插进来的真人文字（2026-10：AI 论文中间插入的 Walden 片段，自己的得分只有 5%），不应跟着计入。
        def own_ok(i, *keys):
            vals = [results[i].get(k) for k in keys if results[i].get(k) is not None]
            return (not vals or max(vals) >= config.DOC_CARRY_FLOOR) and i not in en_verse

        # 英文诗 / 分行散文诗（Whitman、泰戈尔《The Journey》）：多数行很短、按行断开。整篇分类器是用散文训练的，
        # 对分行的诗不可靠（泰戈尔《吉檀迦利》两个整篇分类器都打到 0.88–0.95），这类段落只按自己的得分判断，不参与整篇判断。
        en_verse = {s_.index for s_ in segs if s_.register == "en" and is_en_verse(s_.text)}

        paper_ai = set()
        if en_paper:
            for (blk, reg), idxs in groups.items():
                if reg != "en" or blk not in paper_blocks:
                    continue
                vals = sorted((results[i]["classifier_en2"], len(segs_by_idx[i].text)) for i in idxs
                              if results[i].get("classifier_en2") is not None)
                if len(vals) < 3:
                    continue
                half, acc, med = sum(n for _, n in vals) / 2, 0, None
                for p_, n in vals:
                    acc += n
                    if acc >= half:
                        med = p_
                        break
                if med is not None and med >= config.EN_PAPER_DOC_THRESHOLD:
                    paper_ai.update(i for i in idxs if smoothed.get(i) is not None
                                    and smoothed[i] < float(prof[i][0].get("threshold", 0.5))
                                    and own_ok(i, "classifier_en2", "classifier_en4"))
                    work_ai.difference_update(idxs)

        # 5) 中文作品的整篇判断：MPU 中文分类器各段得分的加权中位数达到 ZH_DOC_THRESHOLD 时，本篇未过阈值的段落
        #    按"轻度疑似（整篇判断）"计入。针对的是 Claude、Kimi k3 这类单段特征不明显、但整篇风格一致的中文 AI 文章。
        zh_doc_ai = set()
        for (blk, reg), idxs in groups.items():
            if reg != "zh":
                continue
            vals = sorted((results[i]["classifier"], len(segs_by_idx[i].text)) for i in idxs
                          if results[i].get("classifier") is not None)
            if len(vals) < 3:
                continue
            half, acc, med = sum(n for _, n in vals) / 2, 0, None
            for p_, n in vals:
                acc += n
                if acc >= half:
                    med = p_
                    break
            # 国产大模型中文分类器（更准、真人整篇最高只有 0.08）明确判为人写时，不用 MPU 的整篇判断
            # （2026-10：真人《一个小故事》MPU 72–91%，第二分类器只有 4–5%）
            z2 = [(results[i]["classifier_zh2"], len(segs_by_idx[i].text)) for i in idxs
                  if results[i].get("classifier_zh2") is not None]
            z2_med = _wmedian(z2) if len(z2) >= 2 else None
            if z2_med is not None and z2_med < config.ZH2_HUMAN_VETO:
                continue
            if med is not None and med >= config.ZH_DOC_THRESHOLD:
                zh_doc_ai.update(i for i in idxs if smoothed.get(i) is not None
                                 and smoothed[i] < float(prof[i][0].get("threshold", 0.5)) and i not in work_ai
                                 and own_ok(i, "classifier") and (results[i].get("classifier_zh2") is None
                                                                   or results[i]["classifier_zh2"] >= config.ZH2_HUMAN_VETO))
        work_ai |= zh_doc_ai
        # 6) 反向整篇判断：中文作品整体像人写（中文分类器整篇加权中位数 < 0.5），其中个别段落只因语言模型信号偏高
        #    （名篇被部分背过、文风工整）而过线、分类器本身也判为人写（< 0.5）时，不计入，标"接近阈值"。
        #    验证：在 78 篇知乎真人长文、36 篇国产大模型中文论文上不改变任何结果（只影响《草原》这类名篇）。
        zh_human_work, famous_like, isolated = set(), set(), set()
        for (blk, reg), idxs in groups.items():
            if reg != "zh" or len(idxs) < 3:
                continue
            vals = sorted((results[i]["classifier"], len(segs_by_idx[i].text)) for i in idxs
                          if results[i].get("classifier") is not None)
            half, acc, med = sum(n for _, n in vals) / 2, 0, None
            for p_, n in vals:
                acc += n
                if acc >= half:
                    med = p_
                    break
            if med is not None and med < 0.5:
                zh_human_work.update(i for i in idxs if (results[i].get("classifier") or 1) < 0.5
                                     and smoothed.get(i) is not None
                                     and smoothed[i] >= float(prof[i][0].get("threshold", 0.5)))
        # 6.1) 国产大模型中文分类器明确判为人写的作品（整篇中位数 < ZH2_HUMAN_VETO，本段也 < 0.1）里，只到"轻度 / 中度疑似"
        #      （< 0.80）的段落多半是 MPU 分类器对翻译腔、工整文风、短句日记体的误判，不计入，标"接近阈值"（几米《向左走·向右走》
        #      选段 MPU 97%、v3 4%）。验收集里所有 AI 文章，这类段落所在作品的 v3 整篇中位数都 ≥ 0.9，不受影响。
        #      依据：v3 在没参与训练的真人文档上整篇最高 0.05，AI 文档几乎都 ≥ 0.9；2026-10 用户测试中被误判的
        #      译文散文诗《孤独的树》、《老人与海》译文段落，MPU 75–99%，而 v3 只有 4–5%。
        for (blk, reg), idxs in groups.items():
            if reg != "zh":
                continue
            z2 = [(results[i]["classifier_zh2"], len(segs_by_idx[i].text)) for i in idxs
                  if results[i].get("classifier_zh2") is not None]
            if len(z2) < 2 or _wmedian(z2) >= config.ZH2_HUMAN_VETO:
                continue
            zh_human_work.update(i for i in idxs if results[i].get("classifier_zh2") is not None
                                 and results[i]["classifier_zh2"] < 0.1 and smoothed.get(i) is not None
                                 and float(prof[i][0].get("threshold", 0.5)) <= smoothed[i] < 0.80)
        # 6.5) 中文第二分类器的整篇判断：同一篇中文作品（≥ 2 段、≥ 400 字）各段得分按字数加权的中位数达到 ZH2_DOC_THRESHOLD，
        #      本篇未过阈值的段落计为"中度疑似（整篇判断）"。针对豆包、千问等写的散文 / 游记：MPU 中文分类器和语言模型
        #      信号都不明显，但整篇文风一致。验证见 Release 里的 training_result.json（真人文档整篇中位数远低于阈值）。
        zh2_ai, zh2_groups, en3_ai = set(), set(), set()
        for (blk, reg), idxs in groups.items():
            if reg != "zh":
                continue
            vals = [(results[i]["classifier_zh2"], len(segs_by_idx[i].text)) for i in idxs
                    if results[i].get("classifier_zh2") is not None]
            if len(vals) < 2 or sum(n for _, n in vals) < 400:
                continue
            # 只有两段时取较低的一段（加权中位数会被较长的那一段决定，真人文章偶有一段得分偏高）
            med = min(v for v, _ in vals) if len(vals) == 2 else _wmedian(vals)
            if med is not None and med >= config.ZH2_DOC_THRESHOLD:
                zh2_groups.add((blk, reg))
                zh2_ai.update(i for i in idxs if smoothed.get(i) is not None
                              and smoothed[i] < float(prof[i][0].get("threshold", 0.5))
                              and own_ok(i, "classifier_zh2"))
        # 6.55) 只有一段的中文短篇（作品集里的一篇 250 字以上的小散文、短故事）：上面的整篇判断要求至少两段，这类作品
        #       永远轮不到。中文第二分类器对这一段的得分达到 ZH2_SINGLE_THRESHOLD 时，同样按"中度疑似（整篇判断）"计入。
        #       依据（2026-10-16 用户测试文档 1015.docx）：AI 短篇《幸福的月光》《踏浪》第二分类器 96%，但 MPU 分类器和
        #       语言模型信号都不明显，原来最终只有 0–9%；同一文档里的真人短篇（老舍、张晓风、余秋雨、梁实秋……）都 ≤ 10%。
        #       真人对照（单段 250–700 字，第二分类器 v3）见 config.ZH2_SINGLE_THRESHOLD 的说明。
        blk_counts: dict = {}
        for (blk, reg), idxs in groups.items():
            blk_counts[blk] = blk_counts.get(blk, 0) + len(idxs)
        for (blk, reg), idxs in groups.items():
            if reg != "zh" or len(idxs) != 1 or blk_counts.get(blk) != 1 or (blk, reg) in zh2_groups:
                continue
            i = idxs[0]
            p2 = results[i].get("classifier_zh2")
            if (p2 is not None and p2 >= config.ZH2_SINGLE_THRESHOLD and len(segs_by_idx[i].text) >= config.ZH2_SINGLE_MIN_CHARS
                    and smoothed.get(i) is not None and smoothed[i] < float(prof[i][0].get("threshold", 0.5))):
                zh2_groups.add((blk, reg))
                zh2_ai.add(i)
        # 6.56) 人写文章里插入的一整段 AI 文字：整篇判断要求多数段落像 AI，插进来的一两段永远轮不到；MPU 分类器和语言模型
        #       又认不出新一代国产大模型。中文第二分类器对这一段的得分达到 ZH2_SINGLE_THRESHOLD（且 ≥ ZH2_SEG_MIN_CHARS 字）时，
        #       单段按"中度疑似"计入。依据（2026-10-19）：没参与训练的 7 部真人书 1381 段 + 84 段，没有一段达到 0.95；
        #       在真人文章中间插入的 8 段 AI 文字（千问、DeepSeek、豆包、Kimi）全部 ≥ 0.953。
        zh2_seg = set()
        for (blk, reg), idxs in groups.items():
            if reg != "zh" or (blk, reg) in zh2_groups:
                continue
            for i in idxs:
                p2 = results[i].get("classifier_zh2")
                if (p2 is not None and p2 >= config.ZH2_SINGLE_THRESHOLD and len(segs_by_idx[i].text) >= config.ZH2_SEG_MIN_CHARS
                        and smoothed.get(i) is not None and smoothed[i] < float(prof[i][0].get("threshold", 0.5))
                        and i not in memo):
                    zh2_seg.add(i)
        # 6.57) 两个中文分类器一致：MPU 分类器（cls）≥ ZH_AGREE_CLS、第二分类器（zh2）≥ ZH_AGREE_ZH2 时，这一段按"中度疑似"计入，
        #       不再要求某一个模型单独越过 0.95 和字数门槛。依据（2026-11-10 全量诊断，12 个测试集）：漏检的现代文 AI 里
        #       有 19 篇两个模型都 ≥ 0.9，却因第二分类器 0.945–0.949、或字数不到 250 被挡；真人中文段 668 段里只有 1 段两个都 ≥ 0.9。
        #       与商用检测器（GPTZero、Originality）多模型合议的做法一致：单个模型的极端分数不如两个独立模型的一致意见可靠。
        if config.ZH_AGREE_CLS < 1:
            for (blk, reg), idxs in groups.items():
                if reg != "zh":
                    continue
                for i in idxs:
                    c, z = results[i].get("classifier"), results[i].get("classifier_zh2")
                    if (c is not None and z is not None and c >= config.ZH_AGREE_CLS and z >= config.ZH_AGREE_ZH2
                            and len(segs_by_idx[i].text) >= config.ZH_AGREE_MIN_CHARS and i not in memo
                            and smoothed.get(i) is not None and smoothed[i] < float(prof[i][0].get("threshold", 0.5))):
                        zh2_seg.add(i)
        # 6.575) 第二分类器多段合议：同一文档里 ≥ ZH2_POOL_MIN_SEGS 段现代中文（各 ≥ ZH2_POOL_MIN_CHARS 字）的第二分类器得分
        #        都达到 ZH2_POOL_THRESHOLD，但单看都没过阈值时，这几段一并按"中度疑似"计入。单段 0.945–0.95 的分数不够可靠，
        #        多段同时出现就很难是巧合（与 ChangAn 2026 等研究"多篇合并判断显著提高准确率"一致）。
        #        依据（2026-11-10，22 个测试集）：真人文档 0 篇触发；AI 文档 33 篇触发、找回 107 段漏检（题解、浅近文言、家书为主）。
        if config.ZH2_POOL_MIN_SEGS > 0:
            pool = []
            for (blk, reg), idxs in groups.items():
                if reg != "zh":
                    continue
                for i in idxs:
                    p2 = results[i].get("classifier_zh2")
                    if (p2 is not None and p2 >= config.ZH2_POOL_THRESHOLD and len(segs_by_idx[i].text) >= config.ZH2_POOL_MIN_CHARS
                            and i not in memo and i not in zh2_seg and i not in zh2_ai
                            and smoothed.get(i) is not None and smoothed[i] < float(prof[i][0].get("threshold", 0.5))):
                        pool.append(i)
            if len(pool) >= config.ZH2_POOL_MIN_SEGS:
                zh2_seg.update(pool)
        zh2_ai |= zh2_seg
        # 6.58) 古典作品合议：同一份文档里的文言 / 诗词 / 对联（不含引文、名篇）≥ 2 段时，按字数加权平均分达到 CL_POOL_MEAN，
        #       未过阈值的各段一并按"中度疑似（整组判断）"计入。单首诗、单副对联字数太少，模型逐首判断不稳定；
        #       ChangAn（ACL 2026，arXiv 2604.10101）测得：同一来源的诗从 1 首合到 6 首一起判断，AUROC 从 75.7 升到 88.0。
        #       本项目诊断（2026-11-10）：AI 文言 / 诗词有 23 篇是"一首被拆成几段、每段各差一点"或"分数中等被阈值挡"；
        #       用 12 个测试集模拟，加权平均 ≥ 0.65、≥ 2 段时 AI 多检出约 22 篇，真人古典作品（集）新增误判 0。
        cl_pool = set()
        if config.CL_POOL_MEAN < 1:
            cl_idx = [i for (blk, reg), idxs in groups.items() if reg in ("zh_classical", "zh_poetry") for i in idxs
                      if i not in memo and smoothed.get(i) is not None]
            if len(cl_idx) >= 2:
                w = sum(len(segs_by_idx[i].text) for i in cl_idx) or 1
                if sum(smoothed[i] * len(segs_by_idx[i].text) for i in cl_idx) / w >= config.CL_POOL_MEAN:
                    cl_pool = {i for i in cl_idx if smoothed[i] < float(prof[i][0].get("threshold", 0.5))}
        zh2_ai |= cl_pool
        # 6.6) 英文非论文作品（故事、散文、读后感、清单……）的整篇判断：英文整篇分类器各段得分的加权中位数（两段取较低）
        #      达到 EN3_DOC_THRESHOLD 时，本篇未过阈值的段落计为"中度疑似（整篇判断）"。英文论文另有论文整篇判断。
        if True:
            for (blk, reg), idxs in groups.items():
                if reg != "en" or blk in paper_blocks:
                    continue
                vals = [(results[i]["classifier_en3"], len(segs_by_idx[i].text)) for i in idxs
                        if results[i].get("classifier_en3") is not None]
                if len(vals) < 2 or sum(n for _, n in vals) < 800:
                    continue
                med = min(v for v, _ in vals) if len(vals) == 2 else _wmedian(vals)
                # 联合规则：v7 略低于单独阈值时，再看 v9（加入非论文真人英文训练）是否也判为整篇 AI
                vals4 = [(results[i]["classifier_en4"], len(segs_by_idx[i].text)) for i in idxs
                         if results[i].get("classifier_en4") is not None]
                med4 = (min(v for v, _ in vals4) if len(vals4) == 2 else _wmedian(vals4)) if len(vals4) >= 2 else None
                joint = med4 is not None and ((med >= config.EN3_JOINT_THRESHOLD and med4 >= config.EN4_JOINT_THRESHOLD)
                                              or (med >= config.EN3_JOINT2_THRESHOLD and med4 >= config.EN4_JOINT2_THRESHOLD)
                                              # v14 单独：整篇中位数 ≥ EN4_SOLO_THRESHOLD 且至少 EN4_SOLO_MIN_SEGS 段。397 篇真人英文长文
                                              # v14 中位数最高 0.903；新一代千问 / Claude 文学作品 v7 常只有 0.1–0.6，联合规则不触发（2026-10-29）
                                              or (med4 >= config.EN4_SOLO_THRESHOLD and len(vals4) >= config.EN4_SOLO_MIN_SEGS))
                if med >= config.EN3_DOC_THRESHOLD or joint:
                    en3_hit = {i for i in idxs if smoothed.get(i) is not None
                               and smoothed[i] < float(prof[i][0].get("threshold", 0.5))
                               and (own_ok(i, "classifier_en4" if results[i].get("classifier_en4") is not None
                                           else "classifier_en3")
                                    or (i not in en_verse
                                        and (results[i].get("classifier_en3") or 0) >= config.EN3_OWN_CARRY))}
                    zh2_ai |= en3_hit
                    en3_ai.update(en3_hit)
        # 6.7) 千问英文文学作品的典型套语（见 _qwen_style）：v14 分类器对千问“自白体”回忆录/散文和真人回忆录的段落打分
        #      同样高（中位都在 0.9 左右），最终 AI 率被校准压低。整篇（≥ 800 词）命中套语时，本篇未过阈值、但分类器自身
        #      得分 ≥ QW_STYLE_OWN 的段落计为"中度疑似（整篇判断）"。诗行不计。
        qw_style_ai, qw_kinds, qw_blocks = set(), None, set()
        # 第三版短语表按全文（所有英文非论文作品连起来）滑动 4000 词窗口判断：千问作品常按小标题被切成多篇短"作品"，
        # 单篇不足 800 词或命中不足。窗口命中 ≥ QW_LEX3_MIN 条时，窗口内自身至少命中 1 条的作品计入。
        lex3_blk: dict = {}
        _en_blks = sorted({blk for (blk, reg) in groups if reg == "en" and blk not in paper_blocks})
        if _en_blks:
            _bw, _words = [], []
            for blk in _en_blks:
                w_ = " ".join(segs_by_idx[i].text for i in sorted(groups[(blk, "en")])).split()
                _bw.append((blk, len(_words), len(_words) + len(w_)))
                _words += w_
            for a0 in range(0, max(1, len(_words) - 1999), 2000):
                h = _style_lex_hits(" ".join(_words[a0:a0 + 4000]), "qwen_lex3.json")
                if len(h) < config.QW_LEX3_MIN:
                    continue
                for blk, b0, b1 in _bw:
                    if b1 > a0 and b0 < a0 + 4000 and blk not in lex3_blk \
                            and _style_lex_hits(" ".join(_words[b0:b1]), "qwen_lex3.json"):
                        lex3_blk[blk] = h
        for (blk, reg), idxs in groups.items():
            if reg != "en" or blk in paper_blocks:
                continue
            body = "\n".join(segs_by_idx[i].text for i in idxs)
            if len(body.split()) < 800 and blk not in lex3_blk:
                continue
            kinds = _qwen_style(body) if len(body.split()) >= 800 else None
            lex3 = lex3_blk.get(blk)
            if not kinds and not lex3:
                continue
            qw_kinds = kinds or ["lex3:" + g for g in lex3[:8]]
            qw_blocks.add(blk)
            # 第三版短语表命中（≥ 6 条，真人文本最高 2 条）证据很强，分类器自己打分很低的段落也计入（千问新文风三个分类器都认不出）
            own_min = config.QW_LEX3_OWN if lex3 else config.QW_STYLE_OWN
            qw_style_ai |= {i for i in idxs if smoothed.get(i) is not None
                            and smoothed[i] < float(prof[i][0].get("threshold", 0.5)) and (lex3 or i not in en_verse)
                            and (results[i].get("classifier_en4") or results[i].get("classifier_en2") or 0)
                            >= own_min}
        qw_style_ai -= zh2_ai
        zh2_ai |= qw_style_ai
        # 6.8) DeepSeek 网页版英文作品的固定句式（见 _deepseek_style），论文也算：v14 对这类论文体综述段落常打到 0.5–0.9，
        #      最终被校准压低。整篇（≥ 800 词）命中时，本篇未过阈值、但分类器自身得分 ≥ QW_STYLE_OWN 的段落计为"中度疑似（整篇判断）"。
        ds_style_ai, ds_hits, ds_blocks = set(), None, set()
        for (blk, reg), idxs in groups.items():
            if reg != "en" or blk in qw_blocks:
                continue
            body = "\n".join(segs_by_idx[i].text for i in idxs)
            if len(body.split()) < 800:
                continue
            h = _deepseek_style(body)
            if not h:
                continue
            ds_hits = h
            ds_blocks.add(blk)
            ds_style_ai |= {i for i in idxs if smoothed.get(i) is not None
                            and smoothed[i] < float(prof[i][0].get("threshold", 0.5)) and i not in en_verse
                            and (results[i].get("classifier_en4") or results[i].get("classifier_en2") or 0)
                            >= config.QW_STYLE_OWN}
        ds_style_ai -= zh2_ai
        zh2_ai |= ds_style_ai
        work_ai -= zh2_ai
        zh_doc_ai -= zh2_ai
        zh_human_work -= {i for g in zh2_groups for i in groups[g]}
        zh_human_work -= zh2_seg

        # 7) 中文名篇 / 孤立段落保护：
        #    a) 本篇有"名篇特征"（某段困惑度很低、波动很大，或已有段落被认作名篇原文），且整篇分类器中位数 < ZH_DOC_THRESHOLD：
        #       本篇所有过线段落都不计入（老舍《草原》这类课文，部分句子被模型背过、个别段落分类器也误判）。
        #    b) 整篇像人写（分类器中位数 < 0.5），过线段落合计不足本篇 ISOLATED_MAX_SHARE、且都只是"轻度疑似"（< 0.65）：不计入（豆包《桂林山水》里 74% 的中度段落仍计入）。
        #    两条都标"接近阈值"供复核；整篇像 AI 的作品（国产模型论文中位数几乎都 ≥ 0.99）不受影响。
        famous_blocks = {s.block for s in segs if s.kind == "quotation" and any("名篇" in n for n in s.notes)}
        for (blk, reg), idxs in groups.items():
            if reg != "zh" or (blk, reg) in zh2_groups:
                continue
            med = _wmedian([(results[i]["classifier"], len(segs_by_idx[i].text)) for i in idxs
                            if results[i].get("classifier") is not None])
            if med is None:
                continue
            thr_i = {i: float(prof[i][0].get("threshold", 0.5)) for i in idxs}
            over = [i for i in idxs if smoothed.get(i) is not None and smoothed[i] >= thr_i[i] and i not in paper_ai]
            if not over:
                continue
            fam = blk in famous_blocks or any(
                results[i].get("ppl") is not None and math.exp(results[i]["ppl"]) < config.FAMOUS_WORK_PPL
                and (results[i].get("lp_burstiness") or 0) > config.FAMOUS_WORK_BURST for i in idxs)
            if fam and med < config.ZH_DOC_THRESHOLD:
                zh_human_work.update(over)
                famous_like.update(over)
                continue
            tot = sum(len(segs_by_idx[i].text) for i in idxs)
            if (len(idxs) >= 3 and med < 0.5 and all(smoothed[i] < scoring.LEVELS[1][2] for i in over)
                    and sum(len(segs_by_idx[i].text) for i in over) < config.ISOLATED_MAX_SHARE * tot):
                zh_human_work.update(over)
                isolated.update(over)
        # 7.5) 英文孤立段落保护（2026-10-26，与 7b 对应）：英文以前没有这一条。用户提供的真人畅销小说（Sparks）有 15% 的段落
        #      被判为 AI——这些段落语言模型信号、通用分类器、第二分类器全都偏高（畅销书文字简单流畅，且可能被语言模型"见过"），
        #      单看一段无法区分；但整本书第二分类器加权中位数只有 0.09，过线段落零零星星。
        #      规则：同一篇英文作品至少 EN_ISOLATED_MIN_SEGS 段、第二分类器整篇加权中位数 < EN_ISOLATED_WORK_EN2、过线文字合计
        #      < EN_ISOLATED_MAX_SHARE，其中"轻度过线"（综合分数与第二分类器都 < EN_ISOLATED_SEG_CAP）的段落不计入，标"接近阈值"供复核。
        #      整篇像 AI 的作品（第二分类器中位数几乎都 ≥ 0.9）不受影响；代价：长篇真人文章里零星插入、且只是轻度过线的 AI 段落不计入
        #      （与 Turnitin 对 20% 以下结果不给确定数字的做法一致），仍以"接近阈值"标出。
        en_isolated = set()
        for (blk, reg), idxs in groups.items():
            if reg != "en" or blk in qw_blocks or blk in ds_blocks or len(idxs) < config.EN_ISOLATED_MIN_SEGS:
                continue
            e2 = [(results[i]["classifier_en2"], len(segs_by_idx[i].text)) for i in idxs
                  if results[i].get("classifier_en2") is not None]
            med2 = _wmedian(e2) if len(e2) >= config.EN_ISOLATED_MIN_SEGS else None
            if med2 is None or med2 >= config.EN_ISOLATED_WORK_EN2:
                continue
            over = [i for i in idxs if smoothed.get(i) is not None and smoothed[i] >= float(prof[i][0].get("threshold", 0.5))
                    and i not in paper_ai and i not in zh2_ai]
            tot = sum(len(segs_by_idx[i].text) for i in idxs)
            if not over or sum(len(segs_by_idx[i].text) for i in over) >= config.EN_ISOLATED_MAX_SHARE * tot:
                continue
            soft = {i for i in over if smoothed[i] < config.EN_ISOLATED_SEG_CAP
                    and (results[i].get("classifier_en2") or 0) < config.EN_ISOLATED_SEG_CAP}
            zh_human_work |= soft
            en_isolated |= soft
        # 7.6) 说明 / 操作类英文文字（菜谱、指南、急救、手工）：本段连同前后各一段（不足 300 词再往外扩）祈使句比例
        #      ≥ EN_INSTR_IMP 时，只有三个英文分类器都很肯定（综合分数 ≥ EN_INSTR_PROB、第二 / 第三分类器 ≥ EN_INSTR_EN2 /
        #      EN_INSTR_EN3）的段落才计入，其余过线段落标"接近阈值"。r99：真人说明文 ≥ 50% 的片段 23/91 → 1/91，
        #      AI 说明文 16/16 仍 ≥ 50%。整篇套语规则（千问 / DeepSeek）命中的作品不受影响。
        en_instr = set()
        for (blk, reg), idxs in groups.items():
            if reg != "en" or blk in qw_blocks or blk in ds_blocks:
                continue
            order = sorted(idxs)
            for k, i in enumerate(order):
                pr = smoothed.get(i)
                if pr is None or i in en_verse:
                    continue
                flagged = (pr >= float(prof[i][0].get("threshold", 0.5)) or i in zh2_ai or i in work_ai
                           or i in paper_ai)
                if not flagged or i in zh_human_work:
                    continue
                a, b = max(0, k - 1), min(len(order), k + 2)
                ctx = lambda: "\n".join(segs_by_idx[j].text for j in order[a:b])
                while len(ctx().split()) < 300 and (a > 0 or b < len(order)):
                    a, b = max(0, a - 1), min(len(order), b + 1)
                ratio, ns = imperative_ratio(ctx())
                if ns < 6 or ratio < config.EN_INSTR_IMP:
                    continue
                r = results[i]
                sure = (pr >= config.EN_INSTR_PROB and (r.get("classifier_en2") or 0) >= config.EN_INSTR_EN2
                        and (r.get("classifier_en3") or 0) >= config.EN_INSTR_EN3)
                # 三个新一代英文分类器（逐段 / 整篇 / 第四）都非常肯定时，综合分数略低（第一分类器对菜谱步骤偏保守）也计入
                if (not sure and config.EN_INSTR_ALL > 0 and pr >= config.EN_INSTR_ALL_PROB
                        and min(r.get("classifier_en2") or 0, r.get("classifier_en3") or 0,
                                r.get("classifier_en4") or 0) >= config.EN_INSTR_ALL):
                    sure = True
                if not sure:
                    en_instr.add(i)
        # 7.7) 学术摘要 / 学术文字（见 academic_density）：本段（或本段所在、不足 EN_ABS_MAX_WORDS 词的作品）学术用语密度
        #      ≥ EN_ABS_DENSITY 时，过线段落只有第二分类器（v14）≥ EN_ABS_EN2 才计入，其余标"接近阈值"。论文作品不受影响
        #      （论文段落另用按真人论文校准的阈值）。
        en_abs = set()
        for (blk, reg), idxs in groups.items():
            if reg != "en" or blk in paper_blocks:
                continue
            btext = "\n".join(segs_by_idx[i].text for i in idxs)
            bd, bn = academic_density(btext)
            blk_abs = config.EN_ABS_MIN_WORDS <= bn <= config.EN_ABS_MAX_WORDS and bd >= config.EN_ABS_DENSITY
            blk_style = abstract_style_score(btext) if blk_abs else None
            for i in idxs:
                pr = smoothed.get(i)
                if pr is None or i in en_instr or i in zh_human_work:
                    continue
                flagged = (pr >= float(prof[i][0].get("threshold", 0.5)) or i in zh2_ai or i in work_ai or i in paper_ai)
                if not flagged:
                    continue
                sd, sn = academic_density(segs_by_idx[i].text)
                if not (blk_abs or (sn >= config.EN_ABS_MIN_WORDS and sd >= config.EN_ABS_DENSITY)):
                    continue
                r_i = results[i]
                if (r_i.get("classifier_en2") or 0) >= config.EN_ABS_EN2:
                    continue
                # 例外（仍计入）：第一分类器几乎满分且 v7 也高（DeepSeek 等摘要的典型情况，真人摘要 74 段 0 段），
                # 或 AI 摘要写法得分 ≥ EN_ABS_NB（见 abstract_style_score）
                # 第一分类器几乎满分时，v7 也高、或综合分数 ≥ EN_ABS_P_KEEP 也算例外（2026-10-29 续：公开基准 RAID 里 Cohere、
                # Mistral、Llama 等老一代对话模型的摘要 desklib ≈ 1.00、综合 ≥ 0.99 但 v7 只有 0.2–0.89；
                # 被本规则降级的真人摘要段落 77 段里没有一段同时满足 desklib ≥ 0.995 且综合 ≥ 0.99）
                if (r_i.get("classifier") or 0) >= config.EN_ABS_CLS_KEEP and (
                        (r_i.get("classifier_en3") or 0) >= config.EN_ABS_CLS_EN3 or pr >= config.EN_ABS_P_KEEP):
                    continue
                style = blk_style if blk_style is not None else abstract_style_score(segs_by_idx[i].text)
                if style >= config.EN_ABS_NB:
                    continue
                en_abs.add(i)
        zh2_ai -= en_instr | en_abs
        paper_ai -= en_instr | en_abs
        zh_human_work |= en_instr | en_abs
        work_ai -= zh_human_work
        if config.HYBRID_NO_CARRY:
            # 人机混写切分在这一段里找到了换作者的地方：这段文字本来就是混写的，“整篇都是 AI”的推断不成立，
            # 未过阈值的段落不再按整篇判断带上（2026-11-08 cs1108 去标注版：真人英文段被整篇判断带成“中度疑似”）
            mixed = {getattr(x, "orig_block", x.block) for x in segs if getattr(x, "hybrid", None) is not None}
            if mixed:
                carry_off = {x.index for x in segs if getattr(x, "orig_block", x.block) in mixed}
                for st_ in (work_ai, paper_ai, zh2_ai, zh_doc_ai):
                    st_ -= carry_off

        def human_ref_of(i, pr):
            if pr is None or i not in prof_name:
                return None
            try:
                h = self.human_ref(prof_name[i], prof[i][0])
            except Exception:  # noqa: BLE001
                return None
            if len(h) < 50:
                return None
            n_ge = len(h) - bisect.bisect_left(h, pr)
            return {"n": len(h), "share": round((n_ge + 1) / (len(h) + 1), 4)}

        hmm_ai, hmm_units, hmm_promote = self._unit_hmm(segs, results, combos, smoothed, prof, memo,
                                                        zh_human_work, zh2_seg | paper_ai | zh2_ai | work_ai | zh_doc_ai, pre_split)
        for i in hmm_promote:
            x = segs_by_idx[i]
            x.kind = "body"
            x.notes = list(x.notes) + ["原按引文排除；逐句判断这几句与前后文明显不同、像 AI 写的，改为计入"]
        seg_out, counted_chars, prob_weighted = [], 0, 0.0
        chars_by_level = {"high": 0, "mid": 0, "light": 0, "low": 0}
        level_counts = {"high": 0, "mid": 0, "light": 0, "low": 0}
        chars_by_register: dict[str, int] = {}
        uncalibrated_regs = set()
        near_chars = 0
        for s in segs:
            sc = results[s.index]
            comb = combos[s.index]
            pcal, has_cal = prof[s.index]
            thr = float(pcal.get("threshold", 0.5))
            prob = (smoothed.get(s.index, comb["prob"] if s.index in hmm_promote else None) if s.counted else None)
            level, label = scoring.level_of(prob, thr) if s.counted else ("none", "")
            near = bool(s.counted and prob is not None and thr - scoring.NEAR_MARGIN <= prob < thr)
            by_work = s.index in work_ai or s.index in paper_ai or s.index in zh2_ai
            if s.index in zh_human_work:
                level, near = "low", True
                label = ("接近阈值（说明/操作类文字，按更严门槛未计入）" if s.index in en_instr else
                         "接近阈值（学术摘要/学术文字，按更严门槛未计入）" if s.index in en_abs else
                         "接近阈值（整篇像人写的孤立段落，未计入）" if s.index in en_isolated else
                         "接近阈值（疑似名篇，未计入）" if s.index in famous_like else
                         "接近阈值（孤立段落，未计入）" if s.index in isolated else "接近阈值（整篇像人写，未计入）")
            elif s.index in zh2_seg:
                level, label, near = "mid", "中度疑似（单段：带有国产大模型写作风格）", False
            elif s.index in paper_ai or s.index in zh2_ai:
                level, label, near = "mid", "中度疑似（整篇判断）", False
            elif by_work:
                level, label, near = "light", "轻度疑似（整篇判断）", False
            elif (s.counted and prob is not None and prob < thr and level in ("low", "none") and getattr(s, "hybrid", None)
                  and s.hybrid["state"] and s.hybrid["mean"] >= config.HYBRID_AI_SURE):
                # 人机混写切分出来的 AI 块：这几句的“新一代大模型”分类器分数整体很高、前后文整体很低，
                # 单独拿出来篇幅短、综合分数达不到阈值时，按分类器的一致意见计入
                level, label, near = "mid", "中度疑似（人机混写：这几句与前后文明显不同，像 AI 写的）", False
            elif s.counted and prob is not None and prob < thr and level in ("low", "none") and _detectors_disagree(sc):
                # 选择性判断（selective classification）：专门见过新一代大模型的第二分类器很肯定是 AI，综合分数却很低——
                # 证据互相矛盾时不下结论，标出来请人复核，不计入 AI 率（不把"没标出"当成"确认是人写"）
                level, near = "low", True
                label = "接近阈值（检测器意见不一致，建议复核）"
            # 逐句窗口：整段判 AI 但有成片的句子明显像人写 → 只按 AI 部分计入（部分疑似）；
            # 整段未过阈值但有几句非常像 AI（综合分数和第二分类器都很肯定）→ 只把这几句计入（局部疑似）
            wins_out, ai_chars, partial = [], None, None
            if s.index in hmm_ai and s.counted and prob is not None:
                a_ch, n_ch = hmm_ai[s.index], len(s.text)
                wins_out = hmm_units.get(s.index, [])
                if a_ch == 0:
                    if level in ("high", "mid", "light"):
                        level, near = "low", True
                        label = "接近阈值（逐句判断：这段的句子与前后文相比像人写，未计入）"
                elif a_ch >= 0.85 * n_ch:
                    if level in ("low", "none"):
                        level, near = "mid", False
                        label = "中度疑似（逐句判断：这几句与前后文明显不同，像 AI 写的）"
                else:
                    ai_chars = a_ch
                    if level in ("high", "mid", "light"):
                        partial = "partial"
                        label = f"{label}（逐句判断：部分句子像人写，按约 {a_ch / n_ch:.0%} 计入）"
                    else:
                        partial, near = "local", False
                        label = f"局部疑似（逐句判断：约 {a_ch} 字像 AI 写的）"
            elif (s.index in win_spans and s.counted and prob is not None and s.index not in memo
                    and s.index not in zh_human_work):
                wprof = scoring.profile_for(cal, "zh_short")[0] if (s.register == "zh" and has_short) else pcal
                stext = score_text(s)
                for (a, b), wraw in zip(win_spans[s.index], win_raw[s.index]):
                    wtxt = wraw.get("_text") or stext[a:b]
                    wp = scoring.combine({k: v for k, v in wraw.items() if k != "_text"}, wprof)["prob"]
                    second = wraw.get({"zh": "classifier_zh2", "en": "classifier_en2"}.get(s.register, "classifier"))
                    pos = s.text.find(wtxt[:12].strip()) if wtxt.strip() else -1
                    wins_out.append({"start": pos, "chars": len(wtxt), "text": wtxt,
                                     "prob": None if wp is None else round(wp, 4),
                                     "second": None if second is None else round(second, 4),
                                     "flag": window_flag(wp, second, sc.get("classifier_zh2"), s.register)})
                tot_w = sum(w["chars"] for w in wins_out) or 1
                hum_w = sum(w["chars"] for w in wins_out if w["flag"] == "human")
                ai_w = sum(w["chars"] for w in wins_out if w["flag"] == "ai")
                sure_en = (s.register == "en" and prob >= config.WIN_PARTIAL_SKIP_PROB
                           and (sc.get("classifier_en2") or 0) >= config.WIN_PARTIAL_SKIP_EN2)
                if level in ("high", "mid", "light") and not by_work and not sure_en \
                        and hum_w >= config.WIN_PARTIAL_MIN_SHARE * tot_w:
                    ai_chars = round(len(s.text) * (1 - hum_w / tot_w))
                    partial = "partial"
                    label = f"{label}（部分句子明显像人写，按约 {ai_chars / len(s.text):.0%} 计入）"
                elif level in ("low", "none") and ai_w:
                    ai_chars = round(len(s.text) * ai_w / tot_w)
                    partial = "local"
                    n_ai = sum(1 for w in wins_out if w["flag"] == "ai")
                    label = f"局部疑似（{n_ai} 处句子很像 AI 写的或经 AI 润色，约 {ai_chars} 字计入）"
                    near = False
            if s.counted and prob is not None:
                counted_chars += len(s.text)
                prob_weighted += prob * len(s.text)
                if partial == "partial":
                    chars_by_level[level] += ai_chars
                    chars_by_level["low"] += len(s.text) - ai_chars
                elif partial == "local":
                    chars_by_level["light"] += ai_chars
                    chars_by_level["low"] += len(s.text) - ai_chars
                else:
                    chars_by_level[level] += len(s.text)
                level_counts[level] += 1
                chars_by_register[s.register] = chars_by_register.get(s.register, 0) + len(s.text)
                if near:
                    near_chars += len(s.text)
                if not has_cal:
                    uncalibrated_regs.add(s.register)
            seg_out.append({
                "index": s.index, "start": s.start, "chars": len(s.text), "text": s.text,
                "kind": s.kind, "notes": s.notes, "register": s.register, "block": getattr(s, "orig_block", s.block), "title": s.title,
                "register_name": REGISTER_NAMES.get(s.register, s.register),
                "threshold": thr,
                "prob": None if prob is None else round(prob, 4),
                "ref_prob": (None if s.counted or comb["prob"] is None else round(comb["prob"], 4)),
                "prob_unsmoothed": None if comb["prob"] is None else round(comb["prob"], 4),
                "level": level, "label": label, "near_threshold": near, "by_work": by_work,
                "partial": partial, "ai_chars": ai_chars, "windows": wins_out,
                "memorized": s.index in memo,
                "short": is_short(s, score_text(s)),
                "signals": {k: (None if v is None else round(v, 4)) for k, v in comb["signals"].items()},
                "raw": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in sc.items()},
                "style": styles.get(s.index),
                "scored_by_lm": s.index in lm_ids and "fastdetect" in sc,
                "human_ref": human_ref_of(s.index, comb["prob"]),
            })

        if config.UNIT_HMM_RESEGMENT and hmm_units:
            seg_out = self._resegment(seg_out, hmm_ai, hmm_units, pre_split)
        excluded = [s for s in segs if not s.counted]
        used_profiles = []
        for s_ in counted:
            r_ = s_.register
            if r_ == "zh" and has_short and len(score_text(s_)) < config.SHORT_SEGMENT_CHARS:
                r_ = "zh_short"
            if r_ == "en" and s_.block in paper_blocks:
                r_ = "en_paper"
            elif r_ == "en" and prof_name.get(s_.index) == "en_poetry":
                r_ = "en_poetry"
            if r_ not in used_profiles:
                used_profiles.append(r_)
        flagged_chars = chars_by_level["high"] + chars_by_level["mid"] + chars_by_level["light"]
        rate = (lambda n: round(n / counted_chars, 4) if counted_chars else None)
        main_reg = max(chars_by_register, key=chars_by_register.get) if chars_by_register else "zh"
        main_cal = scoring.profile_for(cal, main_reg)[0]

        # 可信度提示（参考 Turnitin、GPTZero 等平台对短文本、未校准、非常规文体的处理）
        notes = []
        refcheck = None
        if _ref_future is not None:
            try:
                refcheck = _ref_future.result(timeout=config.REF_CHECK_BUDGET_S + 5)
            except Exception:  # noqa: BLE001
                refcheck = None
        if refcheck and refcheck["not_found"] >= 2 and refcheck["not_found"] >= 0.2 * refcheck["total"]:
            ex_ = "；".join("“" + m[:80] + ("…" if len(m) > 80 else "") + "”" for m in refcheck["missing"][:3])
            notes.append(f"参考文献核查：共 {refcheck['total']} 条，其中 {refcheck['not_found']} 条在公开学术数据库（Crossref、OpenAlex、DBLP、Semantic Scholar、"
                         f"arXiv）里查不到，如 {ex_}。大模型常编造看似真实的文献；查不到也可能是书籍、学位论文、会议资料或数据库未收录，"
                         "请逐条人工核对。此项只作提示，不改变 AI 率。")
        if n_zw or n_hw:
            notes.append(f"文中有 {n_zw} 个零宽 / 不可见字符、{n_hw} 个夹着西里尔或希腊形近字母的英文单词（常见的规避检测手法），"
                         "检测前已还原为正常字符；这些痕迹本身见“标点与排版格式检查”。")
        if n_hybrid:
            notes.append(f"有 {n_hybrid} 段里人写和 AI 写的句子疑似交替出现，已在作者疑似换了的地方切开、分别判断"
                         "（标“人机混写切分”）。句子级判断比整段更容易出错，请重点复核这些位置。")
        if n_lab:
            notes.append(f"文中有 {n_lab + 1} 处带字母编号的条目（如“A2、”“Z3、”）连写在同一段里，已按编号拆开逐条判断；"
                         "每条较短时结果波动较大。")
        if n_scr:
            notes.append(f"同一段里中文和英文成段交替，已在 {n_scr} 处语言切换的地方断开，分别用中文、英文的模型判断。")
        if unwrapped:
            notes.append("原文按固定宽度断行（每行几十个字符就换行，常见于从 PDF、邮件或部分 AI 平台复制的文字），"
                         "已先把断开的行合并成段落再检测；否则每一行会被当成独立的短段落，检测结果会明显偏低。")
        en_chars = chars_by_register.get("en", 0)
        if counted_chars < 300 or (main_reg == "en" and en_chars < 1500):
            notes.append("参与计算的正文较短（中文不足 300 字 / 英文不足约 300 词），结果波动较大，仅供参考。")
        if len(chars_by_register) > 1:
            notes.append("文中含多种文体（" + "、".join(f"{REGISTER_NAMES.get(k, k)} {v} 字"
                                                     for k, v in chars_by_register.items()) + "），各自用对应的模型和阈值判断。")
        for reg in sorted(uncalibrated_regs):
            notes.append(f"{REGISTER_NAMES.get(reg, reg)}部分尚无专门校准，结果只宜作相对参考。")
        rep = _repeated_blocks(text)
        if rep:
            notes.append(f"文中有 {rep[0]} 段（行）文字逐字重复出现（最多一段重复 {rep[1]} 次，重复部分约占全文 {rep[2]:.0%}）。大面积原样重复在真人写作中很少见，"
                         "常见于大模型为凑字数而循环输出（例如 Gemini 长文把同一组段落或诗句重复多遍）；也可能是复制粘贴失误或模板。"
                         "此项只作提示，不改变 AI 率。")
        tic = _verbal_tic(text)
        if tic:
            notes.append(f"文中“the whole of”出现 {tic[0]} 次（每千词约 {tic[1]:.0f} 次）。这是 DeepSeek 英文文学作品的典型口头禅"
                         "（“…is the whole of the record”），真人英文书每千词一般不超过 2–3 次。此项只作提示，不改变 AI 率。")
        if qw_kinds:
            _qn = {"on_the_record": "“I want it on the record”", "want_to_be_careful": "“I want to be careful/clear”",
                   "which_is_the": "“…, which is the whole/only…”", "that_is_the_whole": "“That is the whole job/story”",
                   "not_a_metaphor": "“…is not a metaphor”", "cjk_glued": "英文词后直接粘着汉字",
                   "I_want_you_to": "“I want you to understand”", "and_I_have_never": "“and I have never”"}
            notes.append("文中集中出现千问英文文学作品的典型套语（" + "、".join(_qn[k] if k in _qn else "“" + k.split(":", 1)[1] + "”" for k in qw_kinds[:6]) + "）。"
                         "580 本真人英文书没有一本出现这种组合"
                         + (f"；据此另有 {len(qw_style_ai)} 段按“中度疑似（整篇判断）”计入。" if qw_style_ai else "。"))
        if ds_hits and ds_hits[0].startswith("…is the thing"):
            notes.append("文中反复用“…is the thing that…”造句（" + ds_hits[0][ds_hits[0].index("（") + 1:-1] + "），这是 DeepSeek 网页版英文作品的典型句式。"
                         "4383 篇真人论文、摘要、散文的任意 1000 词里都没有出现过，580 本真人书和 2 万多段真人小说最多 5 次"
                         + (f"；据此另有 {len(ds_style_ai)} 段按“中度疑似（整篇判断）”计入。" if ds_style_ai else "。"))
        elif ds_hits:
            notes.append("文中集中出现 DeepSeek 网页版英文作品的固定句式（" + "、".join("“" + g + "”" for g in ds_hits[:5]) + " 等 "
                         + f"{len(ds_hits)} 条）。4425 篇真人论文、摘要、散文和 580 本真人书都没有达到这个数量"
                         + (f"；据此另有 {len(ds_style_ai)} 段按“中度疑似（整篇判断）”计入。" if ds_style_ai else "。"))
        if counted_chars and 0 < flagged_chars / counted_chars < 0.2:
            notes.append("整体 AI 率低于 20%：这一区间误判的可能性较高（Turnitin 对 1%–19% 只显示星号“*%”，不给具体数值），"
                         "数字后加 * 表示仅作提示，请逐段复核被标出的部分，不宜据此下结论。")
        n_works = len({getattr(s, "orig_block", s.block) for s in segs})
        if genre == "classical":
            notes.append(f"按“中国古典文学作品”检测：共 {n_works} 篇（每个编号条目单独判断），文言、诗词和古白话对话都计入，不当作引文排除。")
        if chars_by_register.get("zh_poetry"):
            notes.append("诗词对联篇幅短、格律限制多，是公认最难检测的文体（ACL 2026 ChangAn 基准中多数检测器接近随机），"
                         "诗词部分的结果请只作参考。")
        if chars_by_register.get("zh_classical"):
            notes.append("文言检测难度远高于白话：名篇原文常被模型\"背过\"而显得像 AI，AI 仿写的文言又较少见，"
                         "请把文言部分的结果当作线索而非结论。")
        if main_reg == "zh" and str(cal.get("source", "")).startswith("NLPCC") and cal.get("calibrated"):
            notes.append("使用的是内置默认校准（公开数据集：学术摘要、新闻、作文）；用你自己的文字在管理页校准后会更贴合你的文风。")
        n_refonly = sum(1 for s in segs if s.kind == "reference_only")
        if n_refonly:
            notes.append(f"{n_refonly} 段诗词只给参考值、不计入 AI 率：评估发现诗词检测在不同来源之间很不稳定——"
                         "唐诗宋词名篇会被误判（约 19%），复述故事情节的 AI 诗又几乎认不出。"
                         "如需让诗词计入，可在管理页用你自己标注的诗词校准。")
        if famous:
            notes.append(f"有 {len(famous)} 段中文语言模型几乎能逐字复现（困惑度极低），疑为公开发表的名篇原文"
                         "（如课文、名家散文），AI 生成的文字达不到这种程度，这些段落不计入 AI 率。")
        if memo - famous:
            notes.append(f"有 {len(memo - famous)} 段文字语言模型几乎能逐字复现、而分类器判为人写，疑为公开名篇原文"
                         "（如经典诗文、名人演讲）；这些段落不采信语言模型信号，只按分类器判断。")
        n_short = sum(1 for s in counted if is_short(s, score_text(s)))
        if n_short:
            notes.append(f"有 {n_short} 段篇幅较短（中文不足 {config.SHORT_CHARS_ZH} 字 / 英文不足 {config.SHORT_WORDS_EN} 词），"
                         "已标“篇幅短”，这些段落的结果波动较大。")
        if paper_ai:
            notes.append(f"按整篇判断：这篇英文论文多数段落带有明显的大模型写作特征（整篇中位得分达到阈值），另有 {len(paper_ai)} 段"
                         "单看得分不高，也按“中度疑似（整篇判断）”计入。单段得分会随分段位置波动，整篇结论更可靠；"
                         "如果其中有你亲自写的段落，请以整篇结论为参考、逐段复核。")
        if en3_ai:
            notes.append(f"按整篇判断：有 {len(en3_ai)} 段英文单看得分未过阈值，但所在文章整体带有大模型写作风格"
                         "（英文整篇分类器整篇中位得分达到阈值），按“中度疑似（整篇判断）”计入。如果其中有你亲自写的段落，请逐段复核。")
        if zh2_seg:
            notes.append(f"有 {len(zh2_seg)} 段中文所在文章整体不像 AI，但这几段本身带有新一代国产大模型（豆包、千问、DeepSeek、Kimi、"
                         "文心等）的明显写作风格（中文第二分类器单段得分很高），按“中度疑似”单独计入。常见于人写文章里插入了 AI 生成的段落；"
                         "如果是你亲自写的，请结合草稿复核。")
        if zh2_ai - en3_ai - zh2_seg:
            notes.append(f"按整篇判断：有 {len(zh2_ai - en3_ai - zh2_seg)} 段中文单看得分未过阈值，但所在文章整体带有新一代国产大模型（豆包、千问、"
                         "DeepSeek、Kimi、文心等）的写作风格（中文第二分类器整篇中位得分达到阈值），按“中度疑似（整篇判断）”计入。"
                         "如果其中有你亲自写的段落，请逐段复核。")
        if famous_like:
            notes.append(f"有 {len(famous_like)} 段中文所在文章带有公开名篇的特征（语言模型对其中部分句子几乎逐字复现，"
                         "如课文、名家散文），且整篇不像 AI 写作，这些段落未计入 AI 率，标为“接近阈值”供复核。")
        if isolated:
            notes.append(f"有 {len(isolated)} 段中文单看过了阈值，但所在文章整体像人写、过线文字不足四分之一且只是轻度疑似"
                         "（孤立段落误判较多，参照 Turnitin 对低占比结果的处理），未计入 AI 率，标为“接近阈值”供复核。")
        if en_isolated:
            notes.append(f"有 {len(en_isolated)} 段英文单看过了阈值，但所在文章整体像人写（第二分类器整篇中位数很低）、过线文字不足五分之一"
                         "且只是轻度过线（畅销小说、文风简洁流畅的真人作品常见这种零星误判，参照 Turnitin 对低占比结果的处理），"
                         "未计入 AI 率，标为“接近阈值”供复核。")
        rest = zh_human_work - famous_like - isolated - en_isolated
        if rest:
            notes.append(f"有 {len(rest)} 段中文单看过了阈值，但中文分类器判为人写、所在文章整体也像人写"
                         "（常见于被大模型部分背过的名篇、文风工整的范文），未计入 AI 率，标为“接近阈值”供复核。")
        if zh_doc_ai:
            notes.append(f"按整篇判断：有 {len(zh_doc_ai)} 段中文单看得分未过阈值，但所在文章整体带有明显的大模型写作风格"
                         "（中文分类器整篇中位得分达到阈值），按“轻度疑似（整篇判断）”计入。Claude、Kimi 等模型写的中文常见这种情况；"
                         "如果其中有你亲自写的段落，请逐段复核。")
        if work_ai - zh_doc_ai:
            notes.append(f"有 {len(work_ai - zh_doc_ai)} 段 AI 概率接近阈值，但所在作品的大部分段落已判为疑似 AI，"
                         "按整篇判断计为“轻度疑似（整篇判断）”（AI 文章通常整篇生成）。")
        if near_chars and counted_chars:
            notes.append(f"另有 {near_chars / counted_chars:.0%} 的文字 AI 概率接近阈值（已标“接近阈值”，未计入 AI 率），"
                         "可重点复核；AI 翻译、经过改写或人工润色的 AI 文字常落在这一区间。")
        n_part = [x for x in seg_out if x.get("partial") == "partial"]
        n_local = [x for x in seg_out if x.get("partial") == "local"]
        if n_part:
            notes.append(f"逐句复查：有 {len(n_part)} 段整段判为疑似 AI，但其中成片的句子明显像人写"
                         "（常见于人写文章里插入了 AI 段落，或 AI 稿经过大幅改写），只按 AI 部分的字数计入（标“部分句子明显像人写”）。")
        if n_local:
            notes.append(f"逐句复查：有 {len(n_local)} 段整段未过阈值，但其中有句子非常像 AI 写的或经 AI 润色"
                         f"（共约 {sum(x['ai_chars'] for x in n_local)} 字），只把这些句子计入，标“局部疑似”。"
                         "单独几句的判断比整段更容易出错，请重点复核这些句子。")
        n_dis = sum(1 for x in seg_out if "意见不一致" in (x.get("label") or ""))
        if n_dis:
            notes.append(f"有 {n_dis} 段各检测器意见不一致（专门见过新一代大模型的分类器判为 AI，综合分数却未过阈值），"
                         "已标“检测器意见不一致，建议复核”，未计入 AI 率。没被标出不等于确认是人写，这几段请结合草稿、修改记录复核。")
        if sampled:
            notes.append(f"快速模式：语言模型只检测了 {len(lm_ids)} 段，其余段落仅用分类器。")
        if fallback_all:
            notes.append("全文都被识别为引文（引号对话多或文言虚词多）或参考文献，已改为全部计入计算。")
        elif excluded and sum(len(x.text) for x in excluded) > 0.3 * max(1, len(text)):
            notes.append("超过 30% 的文字因参考文献或引文被排除，AI 率只反映其余正文；"
                         "被排除的引文段落仍给出“参考值”。如需计入，请取消勾选“引文为主的段落不计入”。")
        if not (self.lm and self.lm.ready):
            notes.append("语言模型未就绪，本次只使用了分类器。")
        # 多次检验（Academic_AI_Detection_2026 一文："短窗口提供的信息更少，筛查的窗口越多，偶然误标的机会越多"）：
        # 一篇文章分成很多段分别判断时，即使全是人写，也会按各文体的误判率"期望"误标若干段。标出的段数与偶然误标的
        # 期望数相当时（泊松分布下 P(≥ 标出段数) ≥ 5%），提示不宜据此下结论。
        erates = known_error_rates(used_profiles)
        fpr_of = {}
        for g in erates:
            v = next((e["human_flagged"] for e in g["sets"] if e.get("human_flagged") is not None), None)
            if v is not None:
                fpr_of[g["profile"]] = max(float(v), 0.002)
        expected_false, n_judged, n_flagged = 0.0, 0, 0
        for s_ in counted:
            if smoothed.get(s_.index) is None:
                continue
            r_ = s_.register
            if r_ == "zh" and has_short and len(score_text(s_)) < config.SHORT_SEGMENT_CHARS:
                r_ = "zh_short"
            if r_ == "en" and s_.block in paper_blocks:
                r_ = "en_paper"
            elif r_ == "en" and prof_name.get(s_.index) == "en_poetry":
                r_ = "en_poetry"
            n_judged += 1
            expected_false += fpr_of.get(r_, fpr_of.get(s_.register, 0.02))
        n_flagged = sum(1 for x in seg_out if x["prob"] is not None and (x["level"] in ("high", "mid", "light") or x.get("partial") == "local"))
        p_chance = None
        if n_flagged and n_judged:
            lam = expected_false
            # 泊松分布 P(X >= n_flagged)；在对数空间里算，长文标出几百段时 k! 不会溢出（以前会报 OverflowError）
            p_chance = 1.0 - sum(math.exp(-lam + (k * math.log(lam) if lam > 0 else (0.0 if k == 0 else -math.inf))
                                          - math.lgamma(k + 1)) for k in range(n_flagged))
            p_chance = min(1.0, max(0.0, p_chance))
            if p_chance >= 0.05 and n_flagged <= 0.5 * n_judged:
                notes.append(f"本文共 {n_judged} 段参与判断。即使全部是人写，按各文体的实测误判率，平均也会有约 "
                             f"{expected_false:.1f} 段被误标；本次标出 {n_flagged} 段，与偶然误标的数量相当"
                             f"（纯属偶然的概率约 {p_chance:.0%}），不宜据此认定使用了 AI，请逐段复核。")
        collection = collection_test(works_summary(seg_out), fpr_of)
        if collection and collection["p_human"] < 0.001:
            notes.append(f"作品集整体判断：{collection['n']} 篇诗词 / 文言作品中有 {collection['k']} 篇判为疑似 AI。"
                         f"按实测的单篇误判率（约 {collection['fpr']:.0%}，已按同一作者风格相近放宽一倍）计算，"
                         f"一本全是真人写的作品集出现这么多篇的概率约为 {collection['p_human']:.1e}，"
                         "所以这份作品集中相当一部分很可能由 AI 生成。单首短诗词只有几十个字，逐首判断本来就不可靠，"
                         "没被标出的篇目不代表一定是人写。")
        missing_cls = {REGISTER_NAMES.get(r, r) for r in chars_by_register if not self.classifier_for(r)}
        if missing_cls:
            notes.append("、".join(sorted(missing_cls)) + "分类器未就绪，这部分只用了语言模型特征。")

        return {
            "summary": {
                "ai_rate": rate(flagged_chars),
                # 低于 20% 的整体 AI 率误判可能性较高（Turnitin 对 1%–19% 只显示星号、不给具体数值和高亮）
                "low_rate_caution": bool(counted_chars and 0 < flagged_chars / counted_chars < 0.2),
                "high_rate": rate(chars_by_level["high"]),
                "mid_rate": rate(chars_by_level["mid"]),
                "light_rate": rate(chars_by_level["light"]),
                "mean_prob": round(prob_weighted / counted_chars, 4) if counted_chars else None,
                "threshold": float(main_cal.get("threshold", 0.5)),
                "total_chars": len(text),
                "counted_chars": counted_chars,
                "flagged_chars": flagged_chars,
                "near_threshold_rate": rate(near_chars),
                "chars_by_level": chars_by_level,
                "chars_by_register": chars_by_register,
                "main_register": main_reg,
                "excluded_chars": sum(len(s.text) for s in excluded),
                # AI 率的分母说明：哪些文字没有计入、各多少字（参考文献、题目作者信息、表格、引文、名篇原文……）
                "excluded_by_kind": {k: sum(len(s.text) for s in excluded if _excl_kind(s) == k)
                                     for k in sorted({_excl_kind(s) for s in excluded})},
                "fallback_all_counted": fallback_all,
                "excluded_reference_segments": sum(1 for s in excluded if s.kind == "reference"),
                "excluded_quotation_segments": sum(1 for s in excluded if s.kind == "quotation"),
                "segments": len(segs),
                "segments_by_level": level_counts,
                "smoothing": config.SMOOTHING,
                "mode": mode,
                "genre": genre,
                "error_rates": erates,
                "multiple_testing": {"segments_judged": n_judged, "segments_flagged": n_flagged,
                                     "expected_false_flags": round(expected_false, 2),
                                     "p_chance": None if p_chance is None else round(p_chance, 4)},
                # 检测记录（模型版本、各文体阈值）：便于事后复核与复现，见 tools 评估报告
                "record": {"lm": [config.OBSERVER_MODEL, config.PERFORMER_MODEL],
                           "classifiers": {p_: config.classifier_for(p_.replace("_short", "").replace("_paper", ""))
                                           for p_ in used_profiles},
                           "thresholds": {p_: round(float(scoring.profile_for(cal, p_)[0].get("threshold", 0.5)), 4)
                                          for p_ in used_profiles}},
                "lm_sampled": sampled,
                "lm_scored_segments": len(lm_ids) if (self.lm and self.lm.ready) else 0,
                "calibrated": bool(main_cal.get("calibrated")),
                "calibration_note": main_cal.get("note"),
                "calibration_features": (main_cal.get("lr") or {}).get("features"),
                "reliability_notes": notes,
                "refcheck": refcheck,
                "methods": {
                    "fastdetect": bool(self.lm and self.lm.ready),
                    "binoculars": bool(self.lm and self.lm.ready),
                    "classifier": bool(self.cls and self.cls.ready),
                    "classifier_en": bool(self.cls_en and self.cls_en.ready),
                    "classifier_en2": bool(self.cls_en2 and self.cls_en2.ready),
                    "classifier_zh2": bool(self.cls_zh2 and self.cls_zh2.ready),
                    "classifier_en3": bool(self.cls_en3 and self.cls_en3.ready),
                    "classifier_en4": bool(self.cls_en4 and self.cls_en4.ready),
                    "classifier_poetry": bool(self.cls_poetry and self.cls_poetry.ready),
                    "classifier_classical": bool(self.cls_classical and self.cls_classical.ready),
                },
                "elapsed_sec": round(time.time() - t_start, 1),
            },
            "segments": seg_out,
            "works": works_summary(seg_out),
            "collection": collection,
        }

    def estimate_seconds(self, text: str, mode: str) -> float | None:
        if not self.tokens_per_sec or not (self.lm and self.lm.ready):
            return None
        chars = len(text)
        if mode == "fast":
            chars = min(chars, config.FAST_MODE_MAX_SEGMENTS * config.SEGMENT_TARGET_CHARS)
        tokens = chars / 1.4   # 中文约 1.4 字 / token（Qwen 分词器，经验值）
        return tokens * 2 / self.tokens_per_sec


# ---------------- 后台任务 ----------------

class JobCancelled(Exception):
    pass


class JobQueue:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.q: queue.Queue = queue.Queue()
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()
        threading.Thread(target=self._worker, daemon=True).start()

    def pending(self) -> int:
        with self.lock:
            return sum(1 for j in self.jobs.values() if j["status"] in ("queued", "running"))

    def cancel(self, jid: str, reason: str = "已取消") -> bool:
        """取消排队中或正在检测的任务（2026-11-06 用户反馈：取消后任务仍在队列里，后面的文档一直等）。
        排队中的直接标为已取消，轮到它时跳过；正在检测的在下一次进度回调时停下。"""
        with self.lock:
            j = self.jobs.get(jid)
            if not j or j["status"] not in ("queued", "running"):
                return False
            j["cancel"] = reason
            if j["status"] == "queued":
                j.update(status="cancelled", error=reason, finished=time.time(), payload={})
            return True

    def touch(self, jid: str):
        """页面每隔几秒查询一次进度：记下最后一次查询的时间。很久没人查询的任务视为页面已关闭，自动取消。"""
        with self.lock:
            j = self.jobs.get(jid)
            if j:
                j["seen"] = time.time()

    def _abandoned(self, job: dict) -> bool:
        lim = config.JOB_ABANDON_SECONDS
        return bool(lim and job.get("watch") and time.time() - job.get("seen", job["created"]) > lim)

    def submit(self, kind: str, owner: str, payload: dict, watch: bool = False) -> dict:
        self._gc()
        jid = uuid.uuid4().hex[:16]
        job = {"id": jid, "kind": kind, "owner": owner, "status": "queued", "progress": 0.0,
               "done": 0, "total": 0, "created": time.time(), "started": None, "finished": None,
               "result": None, "error": None, "payload": payload,
               "eta_sec": self.engine.estimate_seconds(payload.get("text", ""), payload.get("mode", "full"))
               if kind == "detect" else None, "watch": watch, "seen": time.time()}
        with self.lock:
            self.jobs[jid] = job
        self.q.put(jid)
        return self.public(job)

    def get(self, jid: str) -> dict | None:
        with self.lock:
            return self.jobs.get(jid)

    def position(self, jid: str) -> int:
        with self.lock:
            queued = sorted((j for j in self.jobs.values() if j["status"] == "queued"), key=lambda j: j["created"])
        for i, j in enumerate(queued):
            if j["id"] == jid:
                return i + 1
        return 0

    def public(self, job: dict) -> dict:
        out = {k: v for k, v in job.items() if k not in ("payload", "owner", "cancel", "seen", "watch")}
        if job["status"] == "queued":
            out["queue_position"] = self.position(job["id"])
        return out

    def _gc(self):
        now = time.time()
        with self.lock:
            for jid in [j for j, v in self.jobs.items()
                        if v["finished"] and now - v["finished"] > config.JOB_TTL_SECONDS]:
                del self.jobs[jid]

    def _worker(self):
        while True:
            jid = self.q.get()
            job = self.get(jid)
            if not job or job["status"] != "queued":
                continue                      # 已取消
            if self._abandoned(job):
                job.update(status="cancelled", error="页面已关闭或很久没有查询进度，已自动取消", finished=time.time(), payload={})
                continue
            job.update(status="running", started=time.time())

            def progress(done, total, job=job):
                if job.get("cancel") or self._abandoned(job):
                    raise JobCancelled(job.get("cancel") or "页面已关闭或很久没有查询进度，已自动取消")
                job.update(done=done, total=total, progress=round(done / total, 4) if total else 1.0)
                if done and job["started"]:
                    elapsed = time.time() - job["started"]
                    job["eta_sec"] = round(elapsed / done * (total - done), 1)

            try:
                p = job["payload"]
                if job["kind"] == "detect":
                    job["result"] = self.engine.analyze(p["text"], p.get("mode", "full"),
                                                        p.get("exclude_references", True),
                                                        p.get("flag_quotations", True), progress,
                                                        p.get("genre", "auto"))
                elif job["kind"] == "calibrate":
                    job["result"] = run_calibration(self.engine, p, progress)
                elif job["kind"] == "autocalibrate":
                    res = run_calibration(self.engine, p, progress)
                    rep = res["report"]
                    caught = rep.get("ai_caught_rate")
                    # 自动校准的安全检查：人写、AI 标注各少于 2 段，或新阈值下（内置 + 你的样本里）AI 文字检出不到一半，
                    # 说明标注太少或互相矛盾（例如一段里混着人写和 AI 的作品），这次不启用，继续用内置校准
                    reason = ("人写和 AI 标注各需要至少 2 段" if min(rep.get("user_human", 0), rep.get("user_ai", 0)) < 2 else
                              f"新校准下 AI 样本只检出 {caught:.0%}" if caught is not None and caught < 0.5 else "")
                    if reason:
                        user = config.load_user_profiles()
                        prof_ = res["calibration"].get("profile")
                        if prof_ in user:
                            del user[prof_]
                            config.save_user_profiles(user)
                            self.engine.reload_calibration()
                        job["result"] = {"report": rep, "saved": False, "rejected": reason}
                    else:
                        saved, _ = apply_user_calibration(self.engine, {**res["calibration"], "guard": AUTOCAL_GUARD})
                        job["result"] = {"report": rep, "saved": saved}
                job["status"] = "done"
            except JobCancelled as e:
                job.update(status="cancelled", error=str(e))
            except Exception as e:  # noqa: BLE001
                log.exception("job %s failed", jid)
                job.update(status="error", error=f"{type(e).__name__}: {e}")
            finally:
                job["finished"] = time.time()
                job["payload"] = {}
                job["progress"] = 1.0 if job["status"] == "done" else job["progress"]


def apply_user_calibration(engine: Engine, cal: dict):
    """启用并永久保存某一文体的用户校准（只替换这一文体）。返回 (是否保存成功, 已有用户校准的文体)。"""
    prof = cal.get("profile") or "zh"
    user = config.load_user_profiles()
    user[prof] = {k: v for k, v in cal.items() if k != "profiles"}
    saved = config.save_user_profiles(user)
    engine.reload_calibration()
    return saved, user


AUTOCAL_GUARD = 3   # 2：用户样本权重设上限（见 run_calibration）——旧的标注校准在启动后按新规则重新校准一次
#                     3（2026-10-25）：内置校准加入"或"规则后，用户标注校准也要按内置校准的或规则重新拟合（否则旧的标注校准一直盖住新规则）


class AutoCalibrator:
    """"标完自动生效"：管理员在报告页上的每一次标注都同步到服务器（永久保存），
    停手 AUTO_CALIBRATE_DELAY 秒后，用服务器上这一文体的全部标注自动重新校准并启用——越标越准。"""

    def __init__(self, engine: Engine, jobs: "JobQueue"):
        self.engine, self.jobs = engine, jobs
        self.lock = threading.Lock()
        self.timers: dict[str, threading.Timer] = {}
        self.last: dict[str, dict] = {}
        self.labels: dict = config.load_json_file(config.USER_LABELS_FILE, {}).get("labels", {})
        # 安全检查（见 JobQueue 里的 autocalibrate）上线前保存的标注校准：启动后按现有标注重新校准一次
        user = config.load_user_profiles()
        regs = {v["register"] for v in self.labels.values()}
        # 换了模型（旧校准已作废、load_user_profiles 不再返回）的文体也重新校准，免得标注白标
        for reg in regs:
            if (user.get(reg) or {}).get("guard") != AUTOCAL_GUARD:
                self.schedule(reg, 60)

    def _save(self):
        config.save_json_file(config.USER_LABELS_FILE, {"format": "user_labels_v1", "labels": self.labels})

    def update(self, items: list[dict]) -> set:
        regs = set()
        with self.lock:
            for it in items:
                key = str(it.get("key") or "")[:64]
                if not key:
                    continue
                old = self.labels.get(key)
                if it.get("label") in ("ai", "human") and (it.get("text") or "").strip():
                    reg = it.get("register") if it.get("register") in REGISTER_NAMES else "zh"
                    self.labels[key] = {"text": it["text"][:20000], "register": reg, "label": it["label"],
                                        "ts": time.time()}
                    regs.add(reg)
                elif old:
                    del self.labels[key]
                if old:
                    regs.add(old["register"])
            self._save()
        for reg in regs:
            self.schedule(reg)
        return regs

    def clear(self):
        with self.lock:
            self.labels = {}
            self._save()

    def schedule(self, reg: str, delay: float | None = None):
        with self.lock:
            t = self.timers.pop(reg, None)
            if t:
                t.cancel()
            t = threading.Timer(config.AUTO_CALIBRATE_DELAY if delay is None else delay, self._fire, args=(reg,))
            t.daemon = True
            self.timers[reg] = t
            self.last.setdefault(reg, {})["status"] = "scheduled"
            t.start()

    def _fire(self, reg: str):
        with self.lock:
            self.timers.pop(reg, None)
            items = [v for v in self.labels.values() if v["register"] == reg]
        if not items:
            user = config.load_user_profiles()
            if reg in user:     # 这一文体的标注都撤掉了：恢复内置默认校准
                del user[reg]
                config.save_user_profiles(user)
                self.engine.reload_calibration()
            self.last[reg] = {"status": "cleared", "time": time.time()}
            return
        cur_user = config.load_user_profiles().get(reg) or {}
        payload = {"human": [v["text"] for v in items if v["label"] == "human"],
                   "ai": [v["text"] for v in items if v["label"] == "ai"],
                   "profile": reg, "include_builtin": True, "trust_register": True, "target_fpr": 0.05,
                   # 诗词：管理员之前选过"计入 AI 率"就保持计入
                   "count_in_rate": bool(cur_user) and not cur_user.get("reference_only")}
        job = self.jobs.submit("autocalibrate", "admin", payload)
        self.last[reg] = {"status": "running", "job": job["id"], "time": time.time()}

    def status(self) -> dict:
        with self.lock:
            counts: dict = {}
            for v in self.labels.values():
                c = counts.setdefault(v["register"], {"ai": 0, "human": 0})
                c[v["label"]] += 1
            last = {k: dict(v) for k, v in self.last.items()}
        for v in last.values():
            j = self.jobs.get(v.get("job", "")) if v.get("job") else None
            if j:
                v["status"] = {"done": "done", "error": "error"}.get(j["status"], "running")
                v["error"] = j.get("error")
                rep = (j.get("result") or {}).get("report") or {}
                if rep:
                    v["user_samples"] = rep.get("user_samples")
                    v["human_flagged_rate"] = rep.get("human_flagged_rate")
        return {"total": len(self.labels), "by_register": counts, "last": last,
                "delay_sec": config.AUTO_CALIBRATE_DELAY}


def load_builtin_calib(profile: str) -> list[dict]:
    """随代码发布的内置校准数据（公开数据集的原始分数），见 tools/evaluate.py 的 write_calib_data。"""
    import json
    from pathlib import Path
    f = Path(__file__).resolve().parent / "calib_data" / f"{profile}.json"
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text("utf-8"))
    except (OSError, ValueError):
        return []


def run_calibration(engine: Engine, p: dict, progress=None) -> dict:
    """用管理员提供的样本校准某一文体。profile=auto 时按样本中字数最多的文体确定。
    include_builtin=True（默认）时与内置公开数据合并：用户样本合计占约 30% 的权重，
    这样即使只标了几段（甚至只标了 AI 一类），也能在不破坏整体效果的前提下向你的文字偏移。"""
    engine.wait_loaded()

    forced = p.get("profile") if p.get("trust_register") and p.get("profile") not in (None, "", "auto") else None

    def to_segments(texts):
        out = []
        for t in texts:
            for s in segment_text(t, True, False):
                if forced:
                    # 来自报告页的逐段标注：文体已在检测时判定，直接按该文体使用（诗词、单独一段文言重新切分时可能被判成别的文体）
                    if s.kind != "reference" and len(s.text) >= min_chars(forced):
                        s.register = forced
                        out.append(s)
                elif s.counted and len(s.text) >= min_chars(s.register):
                    out.append(s)
        return out

    human = to_segments(p.get("human", []))
    ai = to_segments(p.get("ai", []))
    profile = p.get("profile") or "auto"
    if profile == "auto":
        by: dict[str, int] = {}
        for s in human + ai:
            by[s.register] = by.get(s.register, 0) + len(s.text)
        profile = max(by, key=by.get) if by else "zh"
    skipped = sum(1 for s in human + ai if s.register != profile)
    human = [score_text(s) for s in human if s.register == profile]
    ai = [score_text(s) for s in ai if s.register == profile]
    total = len(human) + len(ai)
    done = [0]

    def prog(_d, _t):
        done[0] += 1
        if progress:
            progress(done[0], total)

    hs = engine.raw_scores(human, prog, [profile] * len(human)) if human else []
    as_ = engine.raw_scores(ai, prog, [profile] * len(ai)) if ai else []
    include_builtin = p.get("include_builtin", True)
    builtin = load_builtin_calib(profile) if include_builtin else []
    n_user = len(hs) + len(as_)
    if builtin and n_user:
        # 每段标注最多抵 USER_SAMPLE_MAX_WEIGHT 段内置样本：以前固定让用户样本合计占 30%，只标了十几段时每段权重高达约 50 倍，
        # 校准被这十几段带偏（2026-10：英文 2 段人写 + 11 段 AI 的标注让同一批段落的得分普遍升高 30–40 个百分点，
        # cs1021 里《At First Sight》的段落因此被判 AI）。标得越多，用户样本占比越接近 30%。
        w_user = max(1.0, min(config.USER_SAMPLE_MAX_WEIGHT,
                              config.USER_SAMPLE_SHARE * len(builtin) / ((1 - config.USER_SAMPLE_SHARE) * n_user)))
        bh = [r["s"] for r in builtin if r["y"] == 0]
        ba = [r["s"] for r in builtin if r["y"] == 1]
        H, A = bh + hs, ba + as_
        hw, aw = [1.0] * len(bh) + [w_user] * len(hs), [1.0] * len(ba) + [w_user] * len(as_)
        hu, au = [False] * len(bh) + [True] * len(hs), [False] * len(ba) + [True] * len(as_)
    else:
        H, A, hw, aw, hu, au = hs, as_, None, None, None, None
    # 沿用当前这一文体使用的特征组合（诗词、英文等都是评估后选定的）
    cur = scoring.profile_for(engine.cal, profile)[0]
    feats = (cur.get("lr") or {}).get("features") or scoring.PROFILE_FEATURES.get(profile)
    res = scoring.calibrate(H, A, float(p.get("target_fpr", 0.05)), features=feats,
                            human_w=hw, ai_w=aw, human_user=hu, ai_user=au,
                            or_feature=((((config.load_default_calibration() or {}).get("profiles") or {})
                                         .get(profile) or {}).get("or_rule") or cur.get("or_rule") or {}).get("feature"))
    res["calibration"]["models"] = {"observer": config.OBSERVER_MODEL, "performer": config.PERFORMER_MODEL,
                                    "classifier": config.classifier_for(profile)}
    res["calibration"]["profile"] = profile
    # 内置评估认定"不够稳定、只作参考"的文体（目前是诗词），除非管理员明确要求，否则保持只作参考
    if cur.get("reference_only") and not p.get("count_in_rate"):
        res["calibration"]["reference_only"] = True
    note = f"{REGISTER_NAMES.get(profile, profile)}：用你的 {len(hs)} 段人写、{len(as_)} 段 AI 样本校准"
    if builtin and n_user:
        share = w_user * n_user / (w_user * n_user + len(builtin))
        note += f"（并合并内置公开数据 {len(builtin)} 段，你的样本约占 {share:.0%} 权重）"
    res["calibration"]["note"] = note + "。"
    res["report"]["profile"] = profile
    res["report"]["profile_name"] = REGISTER_NAMES.get(profile, profile)
    res["report"]["skipped_other_register_segments"] = skipped
    res["report"]["builtin_samples"] = len(builtin) if (builtin and n_user) else 0
    res["report"]["user_human"] = len(hs)
    res["report"]["user_ai"] = len(as_)
    return res
