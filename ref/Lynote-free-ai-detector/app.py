"""Lynote free-ai-detector: a CPU heuristic plus an architecture lab.

Detect · Lite runs on this page. The lab explains the two-level pipeline and
renders the published response contract. It does not load a backbone.
"""

import json

import gradio as gr

from lab import (
    analyze_structure,
    build_result_payload,
    calibrate_demo,
    contract_field_report,
    response_demo,
)
from lite import MAX_CHARS, MIN_CHARS, WEIGHTS, analyze

UTM_URL = (
    "https://lynote.ai/ai-detector?utm_source=huggingface"
    "&utm_medium=space&utm_campaign=hf_launch&utm_content=text_detector"
)
GITHUB_URL = "https://github.com/lynote-ai/ai-text-detector"
PIPELINE_URL = "https://github.com/lynote-ai/ai-text-detector/tree/f7db1e4/detection-model-pipeline"
API_URL = "https://lynote.ai/ai-detector-api"

HEAD = """
<title>Free AI Detector — Check AI Text Generation from ChatGPT, Claude &amp; Gemini | Lynote</title>
<meta name="description" content="Free AI detector: check if text was written by AI. Sentence-level highlights for ChatGPT, Claude, Gemini and DeepSeek. No sign-up, private." />
<meta name="keywords" content="ai detector, free ai detector, ai text detector, ai checker, detect ai text, gpt detector, chatgpt detector, ai content detector, ai writing detector" />
<meta property="og:title" content="Free AI Detector — AI Text Checker | Lynote" />
<meta property="og:description" content="Free AI detector with sentence-level highlights. No sign-up required." />
<meta property="og:type" content="website" />
"""

LITE_EXAMPLES = [
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
]


def run_lite(text: str):
    try:
        result = analyze(text)
    except ValueError as exc:
        return f"**{exc}**", {}
    weights = ", ".join(f"{name} {weight:.2f}" for name, weight in WEIGHTS.items())
    message = f"""
## {result['band']}

Estimated AI-generated score: **{result['probability']:.1%}**

This is a transparent statistical heuristic, not proof of authorship. Human writing
can be polished, and AI writing can be varied; very short, translated, or
heavily edited texts are especially easy to misjudge. Do not use this demo
alone for disciplinary, legal, or moderation decisions.

Weights: {weights}.

[Try Lynote's production detector]({UTM_URL})
"""
    return message, result["signals"]


def _structure_markdown(structure: dict) -> str:
    if structure["n_doc"] == 0:
        return "Paste some text to see sentence indexes and paragraph coverage."
    lines = [
        f"**Sentences:** {structure['n_sent']}  ",
        f"**Approximate document tokens** (word / CJK count, not the model tokenizer): "
        f"{structure['approx_document_tokens']}",
        "",
        "| Index | Paragraph | ~Tokens | Over 128? | Sentence |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in structure["sentences"]:
        flag = "yes" if row["over_sentence_budget"] else "no"
        snippet = row["text"].replace("|", "\\|")
        lines.append(
            f"| {row['sentence_index']} | {row['paragraph_index']} | {row['approx_tokens']} | {flag} | {snippet} |"
        )
    lines.extend(["", "| Paragraph | startSentenceIndex | numSentences | ~Tokens |", "| --- | --- | --- | --- |"])
    for row in structure["paragraphs"]:
        lines.append(
            f"| {row['paragraph_index']} | {row['start_sentence_index']} | {row['num_sentences']} | {row['approx_tokens']} |"
        )
    return "\n".join(lines)


def _architecture_markdown(structure: dict) -> str:
    if structure["n_doc"] == 0:
        return "The batch diagram fills in after you run a split."
    over = "This text's approximate length is over the 512-token full-text budget." if structure["over_document_budget"] else "This text's approximate length fits the 512-token full-text budget."
    return f"""
### Two-level batch (illustration)

- **N_doc = {structure['n_doc']}** document on this page, flattened to **N_sent = {structure['n_sent']}** sentences.
- Sentence channel: each sentence is capped at **128 tokens** in the real model.
- Document channel: the full text is capped at **512 tokens** and mean-pooled.
- {over}
- The training loss is **L = L_doc + α · L_sent**. Differentiable sentence-probability cascade statistics are what make the `mixed` class trainable.
- **This page does not load the backbone and does not run a forward pass.** Probabilities below are the published demo, not a reading of your text.
"""


def render_calibration(temperature: float) -> str:
    report = calibrate_demo(float(temperature))
    rows = ["| Class | Demo probability | After temperature |", "| --- | --- | --- |"]
    for name, before, after in zip(report["classes"], report["baseline"], report["calibrated"]):
        rows.append(f"| {name} | {before:.4f} | {after:.4f} |")
    same = "yes — calibration only softens the distribution" if report["argmax_unchanged"] else "no"
    bands = report["bands"]
    return f"""
### Temperature scaling

Fitted value from `calibration_demo.json`: **T = {report['fitted_temperature']}**. Slider value: **T = {report['temperature']:.4f}**.

{chr(10).join(rows)}

Argmax before: `{report['argmax_before']}` · after: `{report['argmax_after']}` · unchanged: **{same}**.

### Confidence bands

reject {bands['reject']} · low {bands['low']} · medium {bands['medium']} · high {bands['high']}

The highest calibrated demo probability ({report['confidence']:.4f}) falls in **{report['band']}**.
That band describes the demo vector, not the text in the box.
"""


def _result_json(text: str, temperature: float) -> str:
    return json.dumps(build_result_payload(text, temperature), ensure_ascii=False, indent=2)


def run_lab(text: str, temperature: float):
    structure = analyze_structure(text)
    return (
        _structure_markdown(structure),
        _architecture_markdown(structure),
        render_calibration(temperature),
        _result_json(text, temperature),
    )


def refresh_temperature(text: str, temperature: float):
    return render_calibration(temperature), _result_json(text, temperature)


def field_markdown() -> str:
    lines = ["| Field | Present |", "| --- | --- |"]
    for item in contract_field_report():
        lines.append(f"| `{item['field']}` | {'yes' if item['present'] else 'no'} |")
    return "\n".join(lines)


CONTRACT = response_demo()

with gr.Blocks(title="Lynote AI Text Detector", head=HEAD) as demo:
    gr.Markdown(
        f"""
# Free AI Detector

**Two layers.** Detect · Lite runs a four-signal heuristic **on this page**. The Two-level Detection Lab is an architecture and contract demo — the production model runs at [Lynote.ai]({UTM_URL}). GitHub holds the open method ([detection-model-pipeline]({PIPELINE_URL})).

[Detect AI Text for Free →]({UTM_URL})
"""
    )
    gr.HTML(
        f"""
<div style="display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;margin:2px 0 8px;">
  <div style="display:flex;flex-wrap:wrap;align-items:center;gap:0.2rem 0.15rem;">
    <a href="{GITHUB_URL}" target="_blank" rel="noopener noreferrer" style="color:#1d4ed8;">GitHub</a>
    <span>·</span>
    <a href="https://huggingface.co/spaces/Lynote/free-ai-humanizer" style="color:#1d4ed8;">Free AI Humanizer</a>
    <span>·</span>
    <a href="https://huggingface.co/spaces/Lynote/free-ai-image-detector" style="color:#1d4ed8;">Free AI Image Detector</a>
    <span>·</span>
    <a href="https://huggingface.co/spaces/Lynote/free-ai-notes" style="color:#1d4ed8;">Free AI Note Taker</a>
  </div>
  <a href="{API_URL}" target="_blank" rel="noopener noreferrer" style="margin-left:auto;display:inline-flex;align-items:center;padding:7px 14px;border:1px solid #ea580c;border-radius:8px;background:#fff7ed;color:#c2410c;font-weight:600;text-decoration:none;white-space:nowrap;">Lynote Detector API →</a>
</div>
"""
    )
    gr.Markdown(
        """
**Privacy:** text is analyzed in memory and is not intentionally stored by this app. Hugging Face infrastructure remains subject to its platform policies. A score is not proof of authorship.
"""
    )

    with gr.Tabs():
        with gr.Tab("Detect · Lite — runs on this page"):
            gr.Markdown(
                """
> **A transparent statistical heuristic, not evidence of authorship.**
> Weights: sentence-length variation 0.35, formulaic-phrase density 0.25, repetition 0.20, vocabulary uniformity 0.20.
> Input length: 80–5,000 characters. This is not the production Lynote detector.
"""
            )
            source = gr.Textbox(
                label="Text to analyze",
                lines=12,
                max_lines=20,
                placeholder=f"Paste {MIN_CHARS:,}–{MAX_CHARS:,} characters…",
            )
            run = gr.Button("Analyze text", variant="primary")
            with gr.Row():
                result = gr.Markdown()
                signals = gr.JSON(label="Signal breakdown")
            run.click(run_lite, inputs=source, outputs=[result, signals])
            gr.Examples(examples=LITE_EXAMPLES, inputs=source)

        with gr.Tab("Two-level Detection Lab — architecture demo"):
            gr.Markdown(
                """
> **Architecture and pipeline demo. Production weights are not published, and the probabilities shown are illustrative.**
>
> Steps 1–2 run the open sentence splitter on your text. Steps 3–5 use the published demo files. Nothing here is a verdict on your paste.
"""
            )
            lab_text = gr.Textbox(
                label="Text to split",
                lines=8,
                placeholder="Paste a document. The splitter is real; the probabilities are not.",
            )
            temperature = gr.Slider(
                minimum=0.5,
                maximum=3.0,
                value=1.1842,
                step=0.0001,
                label="Temperature T (fitted demo value is 1.1842)",
            )
            lab_run = gr.Button("Run pipeline demo", variant="primary")
            with gr.Row():
                with gr.Column(scale=3):
                    structure_view = gr.Markdown(label="1. Sentences and paragraphs")
                    architecture_view = gr.Markdown()
                    calibration_view = gr.Markdown()
                with gr.Column(scale=2):
                    result_json = gr.Code(
                        label="Result JSON",
                        language="json",
                        lines=28,
                        interactive=False,
                    )
            lab_run.click(
                run_lab,
                inputs=[lab_text, temperature],
                outputs=[structure_view, architecture_view, calibration_view, result_json],
            )
            temperature.release(
                refresh_temperature,
                inputs=[lab_text, temperature],
                outputs=[calibration_view, result_json],
            )
            gr.Markdown(
                """
### Response contract

The Result JSON on the right uses the same `{code, msg, data}` fields as `detect_response_demo.json` below. Sentence text and paragraph indexes come from your paste. Probability fields are the published demo vector, softened by the slider. Version **V2-hier-1** is unchanged. `classProbabilities` uses `human` / `ai` / `paraphrased` on sentences, and `human` / `ai` / `mixed` on the document.
"""
            )
            gr.Markdown(field_markdown())
            gr.Code(json.dumps(CONTRACT, indent=2, ensure_ascii=False), language="json", label="detect_response_demo.json")

    gr.Markdown(
        f"""
## What Lynote's detector gives you

Sentence-level highlights tell you where to look again. The production app adds per-model scores and batch review: [lynote.ai/ai-detector]({UTM_URL}).

- **Sentence-level highlights** — which sentences are worth a second read.
- **Several model families** — ChatGPT, Claude, Gemini, DeepSeek, and other LLMs, including text that was edited afterward.
- **Paraphrase signals** — the production model has a paraphrased sentence class. This page does not score that class.
- **Multilingual** — multiple languages; English and Chinese are the most thoroughly checked.
- **No sign-up** on the basic tool.
- **Private** — drafts are not used to train these open demos.

## How to read a report

An AI score, a mixed score, and a human score describe resemblance. Highlighted sentences show where to review. They are not an authorship finding.

## Who it's for

Students, educators, editors, marketers, recruiters, and freelancers who want a review step with a person still in the loop.

## FAQ

**Is this free?** Yes. Paste text and run the heuristic here, or use the full product, without an account for basic use.

**Are detectors exact?** No detector is perfect. Use highlights to review the writing instead of trusting one score. No tool can guarantee a specific score.

**How does the lite demo work?** It measures sentence rhythm, formulaic phrases, repeated n-grams, and vocabulary uniformity, then combines them with the weights above.

**Other languages?** Multiple languages are supported. English and Chinese are the most thoroughly checked.

**Is text stored?** This app keeps text in memory for the request and does not intentionally store it.

[Detect AI Text for Free →]({UTM_URL})
"""
    )


if __name__ == "__main__":
    demo.queue(max_size=16).launch()
