"""Architecture walkthrough for the two-level detector. No backbone, no verdict."""

import hashlib
import json
import math
import re
from pathlib import Path

from vendor.sentence_splitter import split_paragraphs, split_sentences
from vendor.serve_contract import (
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_REJECT,
    DOC_CLASS_ENUM,
    DOC_CLASSES,
    LOW_MESSAGE,
    RESULT_MESSAGES,
    SENT_CLASSES,
    SERVICE_VERSION,
    NEAT_VERSION,
    classify_confidence,
)

_VENDOR = Path(__file__).resolve().parent / "vendor"
SENTENCE_TOKEN_LIMIT = 128
DOCUMENT_TOKEN_LIMIT = 512


def _load_json(name: str) -> dict:
    return json.loads((_VENDOR / name).read_text(encoding="utf-8"))


def calibration_demo() -> dict:
    return _load_json("calibration_demo.json")


def response_demo() -> dict:
    return _load_json("detect_response_demo.json")


def approx_tokens(text: str) -> int:
    """Rough word/CJK count used only to illustrate the 128/512 token budgets."""
    return len(re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", text))


def analyze_structure(text: str) -> dict:
    """Split one document into paragraphs and globally indexed sentences."""
    raw = text or ""
    paragraphs = split_paragraphs(raw) if raw.strip() else []
    sentence_rows = []
    coverage = []
    cursor = 0
    for paragraph_index, paragraph in enumerate(paragraphs):
        sentences = split_sentences(paragraph)
        start = cursor
        for sentence in sentences:
            sentence_rows.append(
                {
                    "sentence_index": cursor,
                    "paragraph_index": paragraph_index,
                    "text": sentence,
                    "approx_tokens": approx_tokens(sentence),
                    "over_sentence_budget": approx_tokens(sentence) > SENTENCE_TOKEN_LIMIT,
                }
            )
            cursor += 1
        coverage.append(
            {
                "paragraph_index": paragraph_index,
                "start_sentence_index": start,
                "num_sentences": len(sentences),
                "approx_tokens": approx_tokens(paragraph),
            }
        )
    document_tokens = approx_tokens(raw)
    return {
        "n_doc": 1 if raw.strip() else 0,
        "n_sent": len(sentence_rows),
        "sentences": sentence_rows,
        "paragraphs": coverage,
        "approx_document_tokens": document_tokens,
        "over_document_budget": document_tokens > DOCUMENT_TOKEN_LIMIT,
        "sentence_token_limit": SENTENCE_TOKEN_LIMIT,
        "document_token_limit": DOCUMENT_TOKEN_LIMIT,
    }


def temperature_scale(probabilities: list[float], temperature: float) -> list[float]:
    """Soften a probability vector. Temperature scaling does not change argmax for T > 0."""
    if temperature <= 0:
        raise ValueError("temperature must be positive")
    logits = [math.log(max(value, 1e-12)) for value in probabilities]
    scaled = [logit / temperature for logit in logits]
    peak = max(scaled)
    weights = [math.exp(value - peak) for value in scaled]
    total = sum(weights)
    return [weight / total for weight in weights]


def demo_class_probabilities() -> list[float]:
    document = response_demo()["data"]["documents"][0]
    return [float(document["classProbabilities"][name]) for name in DOC_CLASSES]


def calibrate_demo(temperature: float) -> dict:
    """Apply temperature scaling to the published demo document probabilities."""
    baseline = demo_class_probabilities()
    calibrated = temperature_scale(baseline, temperature)
    before = DOC_CLASSES[max(range(len(baseline)), key=baseline.__getitem__)]
    after = DOC_CLASSES[max(range(len(calibrated)), key=calibrated.__getitem__)]
    confidence = max(calibrated)
    return {
        "temperature": temperature,
        "fitted_temperature": float(calibration_demo()["temperature"]),
        "classes": DOC_CLASSES,
        "baseline": baseline,
        "calibrated": calibrated,
        "argmax_before": before,
        "argmax_after": after,
        "argmax_unchanged": before == after,
        "confidence": confidence,
        "band": classify_confidence(confidence),
        "bands": {
            "reject": f"< {CONFIDENCE_REJECT}",
            "low": f"{CONFIDENCE_REJECT} – {CONFIDENCE_LOW}",
            "medium": f"{CONFIDENCE_LOW} – {CONFIDENCE_MEDIUM}",
            "high": f"≥ {CONFIDENCE_MEDIUM}",
        },
    }


def _round_distribution(values: list[float]) -> list[float]:
    rounded = [round(value, 4) for value in values]
    drift = round(1.0 - sum(rounded), 4)
    rounded[-1] = round(rounded[-1] + drift, 4)
    return rounded


def build_result_payload(text: str, temperature: float) -> dict:
    """Fill the published response contract from this paste.

    Sentence text and paragraph indexes come from the splitter. Probability
    fields are the published demo vector, softened by the temperature slider,
    so the JSON has the same fields as detect_response_demo.json.
    """
    raw = text or ""
    structure = analyze_structure(raw)
    report = calibrate_demo(float(temperature))
    document_probs = {
        name: value
        for name, value in zip(report["classes"], _round_distribution(report["calibrated"]))
    }
    predicted = report["argmax_after"]
    confidence = document_probs[predicted]
    band = classify_confidence(confidence)
    if band in ("low", "reject"):
        message = LOW_MESSAGE
    else:
        message = RESULT_MESSAGES.get((predicted, band), LOW_MESSAGE)

    demo_sentences = response_demo()["data"]["documents"][0]["sentences"]
    sentences = []
    for index, row in enumerate(structure["sentences"]):
        template = demo_sentences[index % len(demo_sentences)]["classProbabilities"]
        baseline = [float(template[name]) for name in SENT_CLASSES]
        scaled = _round_distribution(temperature_scale(baseline, float(temperature)))
        class_probabilities = {name: value for name, value in zip(SENT_CLASSES, scaled)}
        generated = class_probabilities["ai"]
        sentences.append(
            {
                "generatedProb": generated,
                "sentence": row["text"],
                "perplexity": 0,
                "classProbabilities": class_probabilities,
                "highlightSentenceForAi": generated >= 0.5,
                "specialHighlightType": None,
            }
        )
    paragraphs = [
        {
            "startSentenceIndex": row["start_sentence_index"],
            "numSentences": row["num_sentences"],
            "completelyGeneratedProb": confidence,
        }
        for row in structure["paragraphs"]
    ]
    han = len(re.findall(r"[\u4e00-\u9fff]", raw))
    latin = len(re.findall(r"[A-Za-z]", raw))
    language = "zh" if han > latin else "en"
    scan_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    document = {
        "paragraphs": paragraphs,
        "sentences": sentences,
        "classProbabilities": document_probs,
        "confidenceThresholdsRaw": response_demo()["data"]["documents"][0]["confidenceThresholdsRaw"],
        "confidenceScoresRaw": {"identity": dict(document_probs)},
        "subclass": {},
        "pageNumber": 0,
        "language": language,
        "inputText": raw,
        "documentId": scan_id,
        "predictedClass": predicted,
        "confidenceScore": confidence,
        "confidenceCategory": band,
        "documentClassification": DOC_CLASS_ENUM[predicted],
        "resultMessage": message,
        "completelyGeneratedProb": confidence,
        "averageGeneratedProb": confidence,
        "overallBurstiness": 0,
        "writingStats": {},
        "version": SERVICE_VERSION,
        "neatVersion": NEAT_VERSION,
    }
    return {
        "code": 0,
        "msg": "success",
        "data": {
            "version": SERVICE_VERSION,
            "neatVersion": NEAT_VERSION,
            "scanId": scan_id,
            "documents": [document],
        },
    }


def contract_field_report() -> list[dict]:
    """Confirm the demo payload still carries the published response fields."""
    payload = response_demo()
    document = payload["data"]["documents"][0]
    paragraph = document["paragraphs"][0]
    sentence = document["sentences"][0]
    class_probabilities = sentence["classProbabilities"]
    checks = [
        ("code", "code" in payload),
        ("msg", "msg" in payload),
        ("data", "data" in payload),
        ("data.version", payload["data"].get("version") == SERVICE_VERSION),
        ("documents", bool(payload["data"].get("documents"))),
        ("paragraphs.startSentenceIndex", "startSentenceIndex" in paragraph),
        ("paragraphs.numSentences", "numSentences" in paragraph),
        ("paragraphs.completelyGeneratedProb", "completelyGeneratedProb" in paragraph),
        ("sentences.generatedProb", "generatedProb" in sentence),
        ("sentences.classProbabilities.human", "human" in class_probabilities),
        ("sentences.classProbabilities.ai", "ai" in class_probabilities),
        ("sentences.classProbabilities.paraphrased", "paraphrased" in class_probabilities),
        ("sentences.highlightSentenceForAi", "highlightSentenceForAi" in sentence),
    ]
    return [{"field": name, "present": bool(ok)} for name, ok in checks]


def sentence_class_names() -> list[str]:
    return list(SENT_CLASSES)
