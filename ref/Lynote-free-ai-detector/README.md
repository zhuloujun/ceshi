---
title: Free AI Detector
emoji: 🔍
colorFrom: blue
colorTo: purple
sdk: gradio
sdk_version: 4.44.1
app_file: app.py
python_version: "3.10"
pinned: true
license: mit
short_description: Free, no-signup AI detector with sentence-level analysis.
tags:
  - ai-detector
  - free-ai-detector
  - gptzero-alternative
  - turnitin
  - content-authenticity
  - chatgpt-detector
  - gpt-detector
  - ai-checker
  - detect-ai-text
  - ai-plagiarism-checker
  - multilingual
---

# Free AI Detector

Paste text into **Detect · Lite** for a four-signal heuristic that runs on this page, or open **Two-level Detection Lab** to walk the open detection pipeline. The lab splits sentences for real and then shows the published response contract. It does not load production weights and it does not score your paste.

**Two tiers.** This Space is the free CPU layer. The open method is in [`detection-model-pipeline`](https://github.com/lynote-ai/ai-text-detector/tree/f7db1e4/detection-model-pipeline). The production detector — paste, upload, and sentence-level review — is at [lynote.ai/ai-detector](https://lynote.ai/ai-detector?utm_source=huggingface&utm_medium=space&utm_campaign=hf_launch&utm_content=text_detector).

**Open source:** [github.com/lynote-ai/ai-text-detector](https://github.com/lynote-ai/ai-text-detector)

## What runs here

1. **Detect · Lite** — sentence-length variation (0.35), formulaic phrases (0.25), repetition (0.20), and vocabulary uniformity (0.20). 80–5,000 characters. The label on the tab is the whole claim: a transparent statistical heuristic, not evidence of authorship.
2. **Two-level Detection Lab** — `sentence_splitter.py`, the fitted temperature **T = 1.1842**, confidence bands (reject &lt; 0.33 ≤ low &lt; 0.6 ≤ medium &lt; 0.8 ≤ high), and the `{code, msg, data}` demo response at version **V2-hier-1**. The banner on that tab says the probabilities are illustrative.

The loss sketch **L = L_doc + α · L_sent** is why a mixed document can be trained: sentence probabilities stay differentiable and feed the document head. This page never runs that forward pass.

## Before you judge anyone

- A score is a **probabilistic signal, not proof of authorship**.
- No detector is perfect. Short, formal, technical, translated, or heavily edited text is easy to misjudge.
- Do not use this demo alone for disciplinary, legal, or moderation decisions.
- No tool can guarantee a specific score.

## More free Lynote tools

- [Free AI Humanizer](https://huggingface.co/spaces/Lynote/free-ai-humanizer) — rule editor, open T5 checkpoints, and a structure-first scorer
- [Free AI Image Detector](https://huggingface.co/spaces/Lynote/free-ai-image-detector)
- [Free AI Note Taker](https://huggingface.co/spaces/Lynote/free-ai-notes)
- [humanize-text-model](https://huggingface.co/Lynote/humanize-text-model)

## Privacy

The app processes text in memory and does not intentionally store submitted drafts. Hugging Face infrastructure and platform logs remain subject to Hugging Face policies.

## License

MIT. See the linked repositories for their source and license notices.
