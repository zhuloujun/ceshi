"""Lynote AI Text Detector Lite — bilingual, dependency-light heuristic demo.

The detector is transparent and CPU-only: it computes a small set of
well-documented statistical signals for AI-generated prose (low burstiness,
formulaic phrase density, repeated n-grams, uniform vocabulary) and combines
them into a single probabilistic score. It needs no model download, starts
instantly, and works on English, Chinese, and other languages.

The score is a probabilistic signal, not proof of authorship.
"""

import math
import re
from collections import Counter

import gradio as gr


MAX_CHARS = 5_000
MIN_CHARS = 80
UTM_URL = (
    "https://lynote.ai/ai-detector?utm_source=huggingface"
    "&utm_medium=space&utm_campaign=hf_launch&utm_content=text_detector"
)

# High-confidence formulaic phrases (mirrors spaces/humanizer-lite).
EN_PHRASES = [
    "in today's rapidly evolving world",
    "in today's fast-paced world",
    "it is important to note that",
    "it is worth noting that",
    "serves as a testament to",
    "delve into",
    "leverage",
    "seamless",
    "robust",
    "game-changer",
    "groundbreaking",
    "cutting-edge",
    "state-of-the-art",
    "plays a crucial role",
    "in the realm of",
    "in conclusion",
    "moreover",
    "furthermore",
]

ZH_PHRASES = [
    "值得注意的是",
    "综上所述",
    "在当今快速发展的时代",
    "赋能",
    "助力",
    "降本增效",
    "闭环",
    "无缝",
    "总而言之",
    "由此可见",
    "发挥着重要作用",
]

WEIGHTS = {
    "burstiness": 0.35,
    "formulaic_phrases": 0.25,
    "repetition": 0.20,
    "vocabulary_uniformity": 0.20,
}


def _words(text: str):
    latin = re.findall(r"[A-Za-z0-9]+", text)
    han = re.findall(r"[\u4e00-\u9fff]", text)
    return latin + han


def _sentences(text: str):
    return [
        s.strip()
        for s in re.split(r"[。！？!?；;\n]+|\.(?:\s+|$)", text)
        if s.strip()
    ]


def _burstiness(text: str) -> float:
    """1.0 = uniform sentence rhythm (AI-like); 0.0 = bursty (human-like)."""
    lengths = [max(len(_words(s)), 1) for s in _sentences(text)]
    if len(lengths) < 3:
        return 0.5
    mean = sum(lengths) / len(lengths)
    variance = sum((length - mean) ** 2 for length in lengths) / len(lengths)
    cv = math.sqrt(variance) / mean
    return 1.0 - min(cv / 0.75, 1.0)


def _formulaic_phrases(text: str) -> float:
    """Hits of known AI clichés per 100 tokens, clamped to [0, 1]."""
    lowered = text.lower()
    hits = 0
    for phrase in EN_PHRASES:
        hits += len(re.findall(re.escape(phrase), lowered))
    for phrase in ZH_PHRASES:
        hits += text.count(phrase)
    per_hundred = hits * 100 / max(len(_words(text)), 1)
    return min(per_hundred / 2.0, 1.0)


def _repetition(text: str) -> float:
    """Repeated 3-grams plus repeated sentence starters, scaled to [0, 1]."""
    tokens = _words(text)
    if len(tokens) < 12:
        return 0.5
    grams = [tuple(tokens[i:i + 3]) for i in range(len(tokens) - 2)]
    counts = Counter(grams)
    total = len(grams)
    repeated = sum(count for count in counts.values() if count > 1)
    ngram_rate = repeated / total

    sentences = _sentences(text)
    starters = [tuple(_words(s)[:2]) for s in sentences if _words(s)]
    starter_counts = Counter(starters)
    starter_repeats = sum(count - 1 for count in starter_counts.values() if count > 1)
    starter_rate = starter_repeats / max(len(starters), 1)

    return min(ngram_rate * 2.5 + starter_rate, 1.0)


def _vocabulary_uniformity(text: str) -> float:
    """1.0 = very repetitive vocabulary; 0.0 = highly diverse."""
    tokens = _words(text)
    if len(tokens) < 8:
        return 0.5
    ttr = len(set(tokens)) / len(tokens)
    return 1.0 - ttr


def detect(text: str):
    original = (text or "").strip()
    if not original:
        return "Paste some text to run the detector.", {}
    if len(original) > MAX_CHARS:
        return f"Input is limited to {MAX_CHARS:,} characters in this demo.", {}
    if len(original) < MIN_CHARS:
        return (
            f"Add at least {MIN_CHARS:,} characters for a meaningful signal "
            "(short texts are easy to misjudge).",
            {},
        )

    signals = {
        "burstiness": round(_burstiness(original), 3),
        "formulaic_phrases": round(_formulaic_phrases(original), 3),
        "repetition": round(_repetition(original), 3),
        "vocabulary_uniformity": round(_vocabulary_uniformity(original), 3),
    }
    probability = sum(signals[name] * weight for name, weight in WEIGHTS.items())
    probability = min(max(probability, 0.0), 1.0)

    if probability < 0.35:
        band = "Weak AI-generated signal"
    elif probability < 0.65:
        band = "Uncertain / mixed signal"
    else:
        band = "Strong AI-generated signal"

    message = f"""
## {band}

Estimated AI-generated score: **{probability:.1%}**

This is a transparent statistical heuristic, not proof of origin. Human writing
can be polished, and AI writing can be varied; very short, translated, or
heavily edited texts are especially easy to misjudge. Do not use this demo
alone for disciplinary, legal, or moderation decisions.

[Try Lynote's full detection experience]({UTM_URL})
"""
    return message, signals


DESCRIPTION = f"""
This free, local demo estimates how much a text resembles AI-generated prose
using four transparent signals—sentence-rhythm uniformity (burstiness),
formulaic phrase density, repeated n-grams, and vocabulary uniformity. It is
CPU-only, needs no model download, and works for English, Chinese, and other
languages. It is a heuristic demo, not the full [Lynote](https://lynote.ai)
production pipeline.
"""

EXAMPLES = [
    [
        "In today's rapidly evolving world, it is important to note that this "
        "robust solution serves as a testament to our commitment to innovation. "
        "Moreover, we leverage cutting-edge technology to deliver a seamless "
        "experience to our users. Furthermore, our team has built a "
        "comprehensive approach that plays a crucial role in improving "
        "efficiency. It is worth noting that this groundbreaking platform "
        "fosters collaboration and drives growth across the organization."
    ],
    [
        "I walked the dog this morning and the sky was grey and low. Halfway "
        "through the park it started raining, which I hadn't expected, so we "
        "ran home. The dog seemed to enjoy it more than me. When we got back "
        "I made coffee and watched the rain run down the window for a while."
    ],
    [
        "值得注意的是，在当今快速发展的时代，我们通过赋能团队来助力企业实现"
        "降本增效。综上所述，这一方案形成了完整的业务闭环，无缝连接各个环节，"
        "发挥着重要作用。由此可见，未来我们需要继续优化流程，从而为企业创造"
        "更大的价值。"
    ],
    [
        "今天早上我带狗去公园，出门时天阴阴的。走到一半忽然下起雨，我们只好"
        "跑回家，狗倒是挺高兴，一路甩着尾巴。回来以后我煮了杯咖啡，坐在窗边"
        "看了会儿雨，觉得周末就这么过也挺好。"
    ],
]

with gr.Blocks(title="Lynote AI Text Detector") as demo:
    gr.Markdown("# 🔍 Lynote AI Text Detector")
    gr.Markdown(DESCRIPTION)
    source = gr.Textbox(
        label="Text to analyze",
        lines=12,
        max_lines=20,
        placeholder=f"Paste {MIN_CHARS:,}-{MAX_CHARS:,} characters of text…",
    )
    run = gr.Button("Analyze text", variant="primary")
    with gr.Row():
        result = gr.Markdown()
        signals = gr.JSON(label="Signal breakdown")
    run.click(detect, inputs=source, outputs=[result, signals])
    gr.Examples(examples=EXAMPLES, inputs=source)
    gr.Markdown(
        "**Privacy:** text is analyzed in memory and is not intentionally stored "
        "by this app. Hugging Face infrastructure remains subject to its platform "
        "policies."
    )


if __name__ == "__main__":
    demo.launch()
