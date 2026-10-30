# Vendored from lynote-ai/ai-text-detector@f7db1e4 detection-model-pipeline/code/m_serve_hier_api.py.
# Constants and the confidence-band helper only. Model loading is intentionally omitted.
# Do not edit here; sync from source.

"""Response-contract constants from the hierarchical detection server."""

SERVICE_VERSION = "V2-hier-1"
NEAT_VERSION = "V2h"
MAX_SENTENCES = 256

DOC_CLASS_ENUM = {"human": "HUMAN_ONLY", "ai": "AI_ONLY", "mixed": "MIXED"}
DOC_CLASSES = ["human", "ai", "mixed"]
SENT_CLASSES = ["human", "ai", "paraphrased"]

# Bands: reject < 0.33 <= low < 0.6 <= medium < 0.8 <= high.
CONFIDENCE_REJECT = 0.33
CONFIDENCE_LOW = 0.6
CONFIDENCE_MEDIUM = 0.8

RESULT_MESSAGES = {
    ("human", "high"): "High confidence that the text was written entirely by a human.",
    ("human", "medium"): "Moderate confidence that the text was written entirely by a human.",
    ("ai", "high"): "High confidence that the text was written by AI.",
    ("ai", "medium"): "Moderate confidence that the text was written by AI.",
    ("mixed", "high"): "High confidence that the text mixes human-written and AI-written parts.",
    ("mixed", "medium"): "Moderate confidence that the text mixes human-written and AI-written parts.",
}
LOW_MESSAGE = (
    "Low confidence: the signal is not strong enough to call this text "
    "human-written or AI-written."
)


def classify_confidence(score: float) -> str:
    """置信度分档阈值：0.33 / 0.6 / 0.8（与上游日志的 confidenceCategory 口径一致）。"""
    if score >= 0.8:
        return "high"
    if score >= 0.6:
        return "medium"
    if score >= 0.33:
        return "low"
    return "reject"


def confidence_thresholds_raw() -> dict:
    """confidenceThresholdsRaw：分档阈值表（三类结构相同）。"""
    levels = {"reject": 0.33, "low": 0.6, "medium": 0.8}
    return {"identity": {name: dict(levels) for name in DOC_CLASSES}}
