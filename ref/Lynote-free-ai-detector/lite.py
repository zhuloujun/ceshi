"""Four-signal heuristic copied from this Space's previous app.py.

Transparent statistics only. Not evidence of authorship.
"""

import math
import re
from collections import Counter


MAX_CHARS = 5_000
MIN_CHARS = 80

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


def analyze(text: str) -> dict:
    """Return the weighted score, band, and per-signal values.

    Raises ValueError with a user-facing message when the input is unusable.
    """
    original = (text or "").strip()
    if not original:
        raise ValueError("Paste some text to run the detector.")
    if len(original) > MAX_CHARS:
        raise ValueError(f"Input is limited to {MAX_CHARS:,} characters in this demo.")
    if len(original) < MIN_CHARS:
        raise ValueError(
            f"Add at least {MIN_CHARS:,} characters for a meaningful signal "
            "(short texts are easy to misjudge)."
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
    return {"probability": probability, "band": band, "signals": signals}
