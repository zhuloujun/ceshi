"""把检测结果排版成 PDF 报告（/v1/report/pdf 调用）。

用 reportlab 自带的 Adobe 宋体 CID 字体（STSong-Light）显示中文，不需要嵌入字体文件：PDF 体积小、文字可选中可搜索。
版式：封面摘要（大号 AI 率 + 分级色条 + 关键数字）→ 提示 → 分篇结果表 → 分段明细（每段左侧色标、等级标签、
指标）→ 标点与排版格式检查表；每页页脚有页码和免责声明。
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)
from reportlab.graphics.shapes import Drawing, Rect, String

import os  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402

# 优先嵌入开源中文字体（文泉驿正黑，只嵌入用到的字）：任何 PDF 阅读器都能正常显示；
# 找不到字体文件时退回 Adobe 宋体 CID 字体（不嵌入，个别阅读器需要额外的字体包）。
FONT = "STSong-Light"
for _p in (os.getenv("REPORT_FONT", ""), "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
           "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"):
    if _p and os.path.exists(_p):
        try:
            pdfmetrics.registerFont(TTFont("CJK", _p, subfontIndex=0))
            FONT = "CJK"
            break
        except Exception:  # noqa: BLE001
            pass
if FONT == "STSong-Light":
    pdfmetrics.registerFont(UnicodeCIDFont(FONT))
from reportlab.lib.fonts import addMapping  # noqa: E402
for _b in (0, 1):
    for _i in (0, 1):
        addMapping(FONT, _b, _i, FONT)        # 中文字体没有粗体 / 斜体：<b> 用同一字体，避免报错

C_HIGH, C_MID, C_LIGHT = colors.HexColor("#c0392b"), colors.HexColor("#e67e22"), colors.HexColor("#d4a017")
C_NEAR, C_NONE, C_LOW = colors.HexColor("#95a5a6"), colors.HexColor("#c8ccd0"), colors.HexColor("#27ae60")
C_INK, C_SUB, C_RULE = colors.HexColor("#1f2a36"), colors.HexColor("#6b7682"), colors.HexColor("#dfe3e7")
TINT = {"high": "#fbeaea", "mid": "#fdf1e5", "light": "#fbf6e2", "near": "#f1f3f4", "low": "#ffffff", "none": "#f7f8f9"}
REG = {"zh": "现代汉语", "zh_classical": "文言", "zh_poetry": "诗词", "en": "英文"}

# 英文报告（界面选英文时，2026-11-04）：报告里固定的文字按下表换成英文；检测结果里由服务器生成的提示、标签、结论
# 由网页先用界面语言对照表（worker-src/i18n-en.json）翻译好再发过来。用户自己的文字（段落原文、文稿名）不翻译。
import threading  # noqa: E402
_TL = threading.local()
EN = {
    "现代汉语": "Modern Chinese", "文言": "Classical Chinese", "诗词": "Chinese poetry", "英文": "English",
    "参考文献，不计入": "References, not counted", "表格，不计入": "Table / list, not counted",
    "题目 / 作者信息，不计入": "Title / author info, not counted", "引文为主，不计入": "Mostly quotation, not counted",
    "仅供参考，不计入": "Reference only, not counted", "接近阈值（未计入）": "Near threshold (not counted)", "未达阈值": "Below threshold",
    "高度疑似": "High suspicion", "中度疑似": "Medium suspicion", "轻度疑似": "Light suspicion", "未见 AI 特征": "No AI features",
    "接近阈值": "Near threshold", "不计入": "Not counted",
    "审读 · AI 文本检测报告": "Shendu · AI Text Detection Report", "审读": "Shendu", "粘贴文本": "Pasted text",
    "来源": "Source", "生成时间": "Generated",
    "疑似 AI 生成为主": "Mostly suspected AI-generated", "部分内容疑似 AI": "Some content suspected AI",
    "未见明显 AI 特征": "No clear AI features", "AI 率": "AI rate",
    "总字数": "Total characters", "计入字数": "Counted characters", "未计入（参考文献 / 引文等）": "Not counted (references / quotations, etc.)",
    "平均 AI 概率": "Mean AI probability", "判定阈值": "Threshold", "校准": "Calibration", "已校准": "Calibrated", "未校准": "Not calibrated",
    "检测模式": "Mode", "快速（抽样）": "Fast (sampled)", "完整": "Full", "文体": "Register", "字": "chars",
    "参考文献": "references", "题目 / 作者 / 期刊信息": "title / author / journal info", "表格": "tables / lists", "引文": "quotations",
    "疑似公开名篇原文": "likely published classics", "仅供参考的文体": "reference-only registers",
    "提示": "Notes", "如何解读与复核": "How to read and verify this report", "分篇结果": "Results by work",
    "#": "#", "作品": "Work", "字数": "Chars", "结论": "Verdict", "分段明细": "Paragraph details",
    "AI 概率": "AI probability", "参考值": "reference value", "技术附录": "Technical appendix",
    "已知误差（本工具在独立测试集上的实测）": "Known error rates (measured on held-out test sets)",
    "测试集（未参与训练和校准）": "Test set (not used for training or calibration)", "AI 检出率": "AI detection rate",
    "人写误判率（95% 上限）": "Human false-positive rate (95% upper bound)",
    "标点与排版格式检查": "Punctuation and formatting check",
    "需重点核查": "Check carefully", "不规范": "Irregular", "统计": "Statistics", "正常": "OK",
    "说明：任何 AI 检测都有误判，本报告只供作者自查，不能作为学术不端判定依据。":
        "Note: every AI detector makes mistakes. This report is for authors' self-checking only and must not be used as the basis for a finding of academic misconduct.",
    "审读 · AI 文本检测报告　任何 AI 检测都有误判，仅供作者自查": "Shendu · AI Text Detection Report — every AI detector makes mistakes; for self-checking only",
}
GUIDE_EN = {
    "AI 率是": "The AI rate is the share of counted text that is flagged as suspected AI. It is not the probability that the document was written by AI, and certainly not the probability of academic misconduct.",
    "误判有基数效应": "False positives scale with volume: even at a 1% false-positive rate, checking 1,000 human-written documents wrongly flags about 10. A single detection score is never conclusive.",
    "复核被标出的段落时": "When reviewing flagged paragraphs, use corroborating evidence: drafts and revision history (document version history), whether the cited sources and references actually exist, and whether the author can explain the ideas and methods in person.",
    "报告里列出的多个检测器": "The detectors listed in this report (the classifiers, Fast-DetectGPT, Binoculars) share similar training data or scoring models, so agreement among them is not several independent pieces of evidence.",
    "分段明细里的“人写对照”": "“Human reference” in the paragraph details: the share of held-out human samples of the same register that score at least as high as this paragraph. For example, “2%” means about 2 in 100 human paragraphs score this high. Smaller means less human-like, but it is still not the probability that the paragraph was written by AI.",
    "一篇文章分成很多段分别判断时": "When a document is split into many paragraphs that are judged separately, more paragraphs also means more chance flags; be especially cautious when only a few scattered paragraphs are flagged (the Notes give the expected number of chance flags).",
    "人写、AI 改写混合的文字": "Mixed human / AI-revised text (e.g. AI polishing or translation) has no clear “ground truth”; a middling score (e.g. 40%) can correspond to several ways of writing, some of which are permitted.",
    "检测模型只认得训练时见过的 AI 写法": "Detectors only recognise AI writing styles seen during training: detection drops for newer models and for text that was rewritten by hand or translated; short, formulaic registers such as Chinese poetry and classical Chinese also have larger errors.",
    "英文非母语作者的文字更容易被误判": "Text by non-native English writers is more likely to be falsely flagged (Liang et al., 2023); this tool's human English test sets include papers by Chinese authors.",
}


def _(zh: str) -> str:
    """英文报告时把固定文字换成英文。"""
    return EN.get(zh, zh) if getattr(_TL, "en", False) else zh


def _punct(t: str) -> str:
    """英文报告里，已经没有汉字的文字（服务器提示翻译后）把全角标点换成半角。"""
    if not getattr(_TL, "en", False) or re.search(r"[\u3400-\u9fff]", t or ""):
        return t
    for a, b in (("（", " ("), ("）", ")"), ("、", ", "), ("。", ". "), ("；", "; "), ("：", ": "), ("，", ", "),
                 ("“", "“"), ("！", "! "), ("？", "? ")):
        t = t.replace(a, b)
    return re.sub(r" {2,}", " ", re.sub(r"\( ", "(", t)).strip()

S = {
    "title": ParagraphStyle("t", fontName=FONT, fontSize=20, leading=26, textColor=C_INK, spaceAfter=2),
    "meta": ParagraphStyle("m", fontName=FONT, fontSize=8.5, leading=12, textColor=C_SUB),
    "h2": ParagraphStyle("h2", fontName=FONT, fontSize=13, leading=18, textColor=C_INK, spaceBefore=10, spaceAfter=6),
    "body": ParagraphStyle("b", fontName=FONT, fontSize=9, leading=14, textColor=C_INK),
    "small": ParagraphStyle("s", fontName=FONT, fontSize=7.5, leading=11, textColor=C_SUB),
    "note": ParagraphStyle("n", fontName=FONT, fontSize=8.5, leading=13, textColor=C_INK, leftIndent=8, bulletIndent=0),
    "cell": ParagraphStyle("c", fontName=FONT, fontSize=8.5, leading=12, textColor=C_INK),
    "cellc": ParagraphStyle("cc", fontName=FONT, fontSize=8.5, leading=12, textColor=C_INK, alignment=TA_CENTER),
    "big": ParagraphStyle("big", fontName=FONT, fontSize=34, leading=40, alignment=TA_CENTER),
    "bigsub": ParagraphStyle("bs", fontName=FONT, fontSize=9, leading=12, alignment=TA_CENTER, textColor=C_SUB),
}


def pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def esc(t) -> str:
    return escape(str(t or "")).replace("\n", "<br/>")


def esc_t(t) -> str:
    """服务器生成、网页已翻译过的文字（提示、标签、结论）：英文报告时顺手把全角标点换成半角。"""
    return esc(_punct(str(t or "")))


def rate_color(r):
    if r is None:
        return C_NONE
    return C_HIGH if r >= 0.5 else C_MID if r >= 0.2 else C_LIGHT if r > 0 else C_LOW


def seg_level(seg) -> str:
    if seg.get("kind") != "body":
        return "none"
    if seg.get("level") in ("high", "mid", "light"):
        return seg["level"]
    return "near" if seg.get("near_threshold") else "low"


LEVEL_COLOR = {"high": C_HIGH, "mid": C_MID, "light": C_LIGHT, "near": C_NEAR, "low": C_LOW, "none": C_NONE}


def seg_tag(seg) -> str:
    k = seg.get("kind")
    if k == "reference":
        return _("参考文献，不计入")
    if k == "table":
        return _("表格，不计入")
    if k == "frontmatter":
        return _("题目 / 作者信息，不计入")
    if k == "quotation":
        return _("引文为主，不计入")
    if k == "reference_only":
        return _("仅供参考，不计入")
    return seg.get("label") or (_("接近阈值（未计入）") if seg.get("near_threshold") else _("未达阈值"))


def bar(summary, width):
    """AI 率分级色条：高度 / 中度 / 轻度 / 接近阈值 / 其余。"""
    parts = [(_("高度疑似"), summary.get("high_rate") or 0, C_HIGH), (_("中度疑似"), summary.get("mid_rate") or 0, C_MID),
             (_("轻度疑似"), summary.get("light_rate") or 0, C_LIGHT),
             (_("接近阈值（未计入）"), summary.get("near_threshold_rate") or 0, C_NEAR)]
    rest = max(0.0, 1 - sum(p for _n, p, _c in parts))
    d = Drawing(width, 34)
    x, h = 0.0, 12
    d.add(Rect(0, 20, width, h, fillColor=colors.HexColor("#eef1f3"), strokeColor=None))
    for _n, p, c in parts:
        w = width * p
        if w > 0.3:
            d.add(Rect(x, 20, w, h, fillColor=c, strokeColor=None))
        x += w
    lx = 0
    for name, p, c in parts + [(_("未见 AI 特征"), rest, colors.HexColor("#eef1f3"))]:
        d.add(Rect(lx, 4, 7, 7, fillColor=c, strokeColor=C_RULE, strokeWidth=0.3))
        label = f"{name} {p * 100:.1f}%"
        d.add(String(lx + 10, 4.5, label, fontName=FONT, fontSize=7.5, fillColor=C_SUB))
        lx += 12 + pdfmetrics.stringWidth(label, FONT, 7.5) + 10
    return d


def build(payload: dict) -> bytes:
    _TL.en = (payload.get("lang") == "en")
    try:
        return _build(payload)
    finally:
        _TL.en = False


def _reg(k) -> str:
    return _(REG.get(k, k))


def _build(payload: dict) -> bytes:
    en = getattr(_TL, "en", False)
    res = payload.get("result") or {}
    s = res.get("summary") or {}
    works = res.get("works") or []
    segs = res.get("segments") or []
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title=_("审读 · AI 文本检测报告"), author=_("审读"))
    W = doc.width
    # 技术细节（各检测模型分数、阈值、已知误差表、检测记录）默认不放进报告：与知网、维普、Turnitin、GPTZero 等平台的报告一致，
    # 只给结论、分级和标出的段落；页面上勾选“显示技术细节”时才附上（放在报告最后的“技术附录”里）。
    detail = bool(payload.get("detail"))
    colon = ": " if en else "："
    story = [Paragraph(_("审读 · AI 文本检测报告"), S["title"]),
             Paragraph(esc(f"{_('来源')}{colon}{payload.get('source') or _('粘贴文本')}　·　{_('生成时间')}{colon}"
                           f"{payload.get('generated_at') or datetime.now().strftime('%Y-%m-%d %H:%M')}"), S["meta"]),
             Spacer(1, 8)]

    # ---- 摘要：大号 AI 率 + 关键数字 ----
    ai = s.get("ai_rate")
    star = "*" if s.get("low_rate_caution") else ""
    big = Paragraph(f'<font color="{rate_color(ai).hexval().replace("0x", "#")}">{pct(ai)}{star}</font>', S["big"])
    verdict = _("疑似 AI 生成为主" if (ai or 0) >= 0.5 else "部分内容疑似 AI" if (ai or 0) > 0 else "未见明显 AI 特征")
    left = [big, Paragraph(f"{_('AI 率')} · {verdict}", S["bigsub"])]
    stats = [
        [_("总字数"), f"{s.get('total_chars', '—')}", _("计入字数"), f"{s.get('counted_chars', '—')}"],
        [_("未计入（参考文献 / 引文等）"), f"{s.get('excluded_chars', '—')}", _("平均 AI 概率"), pct(s.get("mean_prob"))],
        *([[_("判定阈值"), pct(s.get("threshold")), _("校准"), _("已校准") if s.get("calibrated") else _("未校准")]] if detail else []),
        [_("检测模式"), _("快速（抽样）") if s.get("mode") == "fast" else _("完整"),
         _("文体"), (", " if en else "、").join(f"{_reg(k)} {v} {_('字') if en else '字'}"
                                               for k, v in (s.get("chars_by_register") or {}).items()) or "—"],
    ]
    st = Table([[Paragraph(esc(c), S["cell"] if i % 2 else S["small"]) for i, c in enumerate(r)] for r in stats],
               colWidths=[W * 0.62 * w for w in (0.27, 0.2, 0.2, 0.33)])
    st.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LINEBELOW", (0, 0), (-1, -2), 0.3, C_RULE),
                            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    top = Table([[left, st]], colWidths=[W * 0.36, W * 0.64])
    top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                             ("BOX", (0, 0), (-1, -1), 0.6, C_RULE), ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#fafbfc")),
                             ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story += [top, Spacer(1, 8), bar(s, W), Spacer(1, 6)]
    ex = s.get("excluded_by_kind") or {}
    if ex:
        names = {"reference": "参考文献", "frontmatter": "题目 / 作者 / 期刊信息", "table": "表格", "quotation": "引文",
                 "famous": "疑似公开名篇原文", "reference_only": "仅供参考的文体"}
        if en:
            line = (f"AI rate denominator = {s.get('counted_chars', '—')} counted chars; not counted: " +
                    ", ".join(f"{_(names.get(k, k))} {v} chars" for k, v in ex.items()))
        else:
            line = ("AI 率的分母 = 计入字数 " + str(s.get("counted_chars", "—")) + " 字；未计入：" +
                    "、".join(f"{names.get(k, k)} {v} 字" for k, v in ex.items()))
        story.append(Paragraph(esc(line), S["small"]))

    notes = s.get("reliability_notes") or []
    if notes:
        story.append(Paragraph(_("提示"), S["h2"]))
        story += [Paragraph(esc_t(n), S["note"], bulletText="•") for n in notes]
    story.append(Spacer(1, 4))
    story.append(Paragraph(_("说明：任何 AI 检测都有误判，本报告只供作者自查，不能作为学术不端判定依据。"), S["small"]))

    story.append(Paragraph(_("如何解读与复核"), S["h2"]))
    guide = [
        "AI 率是“被判为疑似 AI 的文字占计入字数的比例”，不是“这篇文章由 AI 写成的概率”，更不是学术不端的概率。",
        "误判有基数效应：即使误判率只有 1%，检测 1000 篇真人文章也会冤枉约 10 篇。单一检测分数不能作为定论。",
        "复核被标出的段落时，请结合旁证：写作草稿与修改记录（文档版本历史）、引用资料与参考文献的核对（是否有查不到的文献）、作者能否当面讲清文中的观点和方法。",
        "报告里列出的多个检测器（各分类器、Fast-DetectGPT、Binoculars）共用相近的训练数据或打分模型，彼此一致不等于多份独立证据。",
        "分段明细里的“人写对照”：同文体的真人样本（没参与训练）中，得分不低于该段的比例。例如“2%”表示 100 段真人文字里约有 2 段分数这么高；"
        "比例越小越不像人写，但仍不是“这段由 AI 写成的概率”。",
        "一篇文章分成很多段分别判断时，段数越多，偶然误标的段落也越多；只有零星几段被标出时尤其要谨慎（本报告“提示”中会给出偶然误标的期望段数）。",
        "人写、AI 改写混合的文字（如 AI 润色、翻译）没有明确的“标准答案”，分数落在中间（如 40%）可能对应多种写作方式，其中有的是允许的。",
        "检测模型只认得训练时见过的 AI 写法：对更新的模型、经过人工改写或翻译的文字，检出率会下降；诗词、文言这类短小、程式化的文体误差也更大（见上表）。",
    ]
    if not detail:
        guide = [g for g in guide if not g.startswith(("报告里列出的多个检测器", "分段明细里的“人写对照”"))]
        guide = [g.replace("（见上表）", "") for g in guide]
    if (s.get("chars_by_register") or {}).get("en"):
        guide.append("英文非母语作者的文字更容易被误判（Liang 等，2023）；本工具的真人英文测试集包含中国作者的论文" +
                     ("，误判率已计入技术附录的表格。" if detail else "。"))
    if en:
        guide = [next((v for k, v in GUIDE_EN.items() if g.startswith(k)), g) for g in guide]
    story += [Paragraph(esc(g), S["note"], bulletText="•") for g in guide]

    # ---- 分篇结果 ----
    if len(works) > 1:
        story.append(Paragraph(_("分篇结果"), S["h2"]))
        rows = [[Paragraph(_(x), S["cellc"]) for x in ("#", "作品", "文体", "字数", "AI 率", "结论")]]
        style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef1f3")), ("GRID", (0, 0), (-1, -1), 0.3, C_RULE),
                 ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
        for i, w in enumerate(works, 1):
            r = w.get("ai_rate")
            rows.append([Paragraph(str(i), S["cellc"]), Paragraph(esc(w.get("title")), S["cell"]),
                         Paragraph(esc((", " if en else "、").join(_reg(x) for x in w.get("registers") or [])), S["cell"]),
                         Paragraph(str(w.get("chars", "")), S["cellc"]),
                         Paragraph(f'<font color="#ffffff">{pct(r)}</font>' if r is not None else "—", S["cellc"]),
                         Paragraph(esc_t(w.get("verdict")), S["cell"])])
            style.append(("BACKGROUND", (4, i), (4, i), rate_color(r)))
        t = Table(rows, colWidths=[W * x for x in (0.05, 0.42, 0.13, 0.09, 0.1, 0.21)], repeatRows=1)
        t.setStyle(TableStyle(style))
        story.append(t)
        if payload.get("works_note"):
            story += [Spacer(1, 3), Paragraph(esc_t(payload["works_note"]), S["small"])]

    # ---- 分段明细 ----
    story.append(Paragraph(_("分段明细"), S["h2"]))
    legend = "　".join(f'<font color="{LEVEL_COLOR[k].hexval().replace("0x", "#")}">■</font> {_(n)}' for k, n in
                      (("high", "高度疑似"), ("mid", "中度疑似"), ("light", "轻度疑似"), ("near", "接近阈值"),
                       ("low", "未达阈值"), ("none", "不计入")))
    story += [Paragraph(legend, S["small"]), Spacer(1, 4)]
    for seg in segs:
        lv = seg_level(seg)
        c = LEVEL_COLOR[lv].hexval().replace("0x", "#")
        prob = (pct(seg.get("prob")) if seg.get("prob") is not None else
                (f"{pct(seg.get('ref_prob'))} (reference value)" if en else f"{pct(seg.get('ref_prob'))}（参考值）")
                if seg.get("ref_prob") is not None else "—")
        no = f'Paragraph {seg.get("index", 0) + 1}' if en else f'第 {seg.get("index", 0) + 1} 段'
        head = (f'<font color="{c}">■</font> <b>{no}</b>　'
                f'{esc(_reg(seg.get("register") or "zh"))} · {seg.get("chars", "")} {_("字")} · {_("AI 概率")} '
                f'<font color="{c}">{prob}</font>　<font backColor="{c}" color="#ffffff"> {esc_t(seg_tag(seg))} </font>')
        sty = ParagraphStyle("seg", parent=S["body"], backColor=colors.HexColor(TINT[lv]), borderPadding=(4, 6, 4, 6),
                             borderColor=LEVEL_COLOR[lv], borderWidth=0, leftIndent=2)
        text = seg.get("text") or ""
        ind = seg.get("indicators") or ""
        block = [Paragraph(head, S["cell"]), Spacer(1, 3), Paragraph(esc(text), sty)]
        if ind:
            block += [Spacer(1, 2), Paragraph(esc_t(ind), S["small"])]
        block.append(Spacer(1, 9))
        if len(text) < 600:
            story.append(KeepTogether(block))
        else:
            story += block

    # ---- 技术附录（只在勾选“显示技术细节”时）----
    if detail:
        story.append(Paragraph(_("技术附录"), S["h2"]))
        if payload.get("method"):
            story.append(Paragraph(esc(payload["method"]), S["small"]))

        # ---- 已知误差 + 如何解读（Weber-Wulff 等 2023、Liang 等 2023 对检测报告的要求：说明测的是什么、
        #      在相应文体上的检出率与误判率、还需要哪些旁证）----
        er = s.get("error_rates") or []
        if er:
            story.append(Paragraph(_("已知误差（本工具在独立测试集上的实测）"), S["h2"]))
            rows = [[Paragraph(_(x), S["cellc"]) for x in ("文体", "测试集（未参与训练和校准）", "AI 检出率", "人写误判率（95% 上限）")]]
            for g in er:
                for k, e in enumerate(g["sets"]):
                    fp = pct(e.get("human_flagged"))
                    if e.get("human_flagged_upper95") is not None:
                        fp += f"（≤{pct(e['human_flagged_upper95'])}）"
                    cnt = f' (AI {e["n_ai"]} / human {e["n_human"]})' if en else f'（AI {e["n_ai"]} / 人写 {e["n_human"]}）'
                    rows.append([Paragraph(esc_t(g["name"]) if k == 0 else "", S["cell"]),
                                 Paragraph(esc(_punct(e["name"]) + cnt), S["cell"]),
                                 Paragraph(pct(e.get("ai_caught")), S["cellc"]),
                                 Paragraph(fp, S["cellc"])])
            t = Table(rows, colWidths=[W * x for x in (0.13, 0.52, 0.13, 0.22)], repeatRows=1)
            t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef1f3")),
                                   ("GRID", (0, 0), (-1, -1), 0.3, C_RULE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
            story.append(t)
        ppv = [g for g in er if g.get("ppv")]
        if ppv:
            story.append(Spacer(1, 4))
            for g in ppv:
                v = g["ppv"]
                if en:
                    line = (f"{_punct(g['name'])}: from the detection and false-positive rates of “{_punct(g['ppv_basis'][:60])}” above, "
                            f"if half of the submitted documents really are AI-written, flagged text is AI about {pct(v.get('50%'))} of the time; "
                            f"about {pct(v.get('10%'))} if only 10% are; about {pct(v.get('2%'))} if only 2% are.")
                else:
                    line = (f"{g['name']}：按上表“{g['ppv_basis'][:30]}”的检出率与误判率估算，如果送检文章里真有一半是 AI 写的，"
                            f"被标出的文字确实是 AI 的概率约 {pct(v.get('50%'))}；只有 10% 是 AI 写的时约 {pct(v.get('10%'))}；"
                            f"只有 2% 时约 {pct(v.get('2%'))}。")
                story.append(Paragraph(esc(line), S["small"]))
        rec = s.get("record") or {}
        if rec:
            if en:
                line = ("Detection record: language models " + " / ".join(rec.get("lm") or []) + "; classifiers " +
                        "; ".join(f"{_reg(k)} {v}" for k, v in (rec.get("classifiers") or {}).items()) +
                        "; thresholds " + "; ".join(f"{_reg(k)} {pct(v)}" for k, v in (rec.get("thresholds") or {}).items()))
            else:
                line = ("检测记录：语言模型 " + " / ".join(rec.get("lm") or []) + "；分类器 " +
                        "；".join(f"{REG.get(k, k)} {v}" for k, v in (rec.get("classifiers") or {}).items()) +
                        "；阈值 " + "；".join(f"{REG.get(k, k)} {pct(v)}" for k, v in (rec.get("thresholds") or {}).items()))
            story.append(Paragraph(esc(line), S["small"]))

    # ---- 格式检查 ----
    items = payload.get("format_items") or []
    if items:
        story.append(Paragraph(_("标点与排版格式检查"), S["h2"]))
        sev_c = {"danger": C_HIGH, "warn": C_MID, "info": C_SUB, "ok": C_LOW}
        sev_n = {k: _(v) for k, v in {"danger": "需重点核查", "warn": "不规范", "info": "统计", "ok": "正常"}.items()}
        rows, style, group = [], [("GRID", (0, 0), (-1, -1), 0.3, C_RULE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")], None
        for it in items:
            if it.get("group") != group:
                group = it.get("group")
                rows.append([Paragraph(f"<b>{esc_t(group)}</b>", S["cell"]), "", ""])
                style += [("SPAN", (0, len(rows) - 1), (-1, len(rows) - 1)),
                          ("BACKGROUND", (0, len(rows) - 1), (-1, len(rows) - 1), colors.HexColor("#eef1f3"))]
            extra = " ".join(x for x in (it.get("extra") or "", re.sub(r"<[^>]+>", " ", it.get("html") or "")) if x).strip()
            samples = "；".join(str(x) for x in (it.get("samples") or [])[:2])
            desc = esc_t(it.get("name")) + (f'<br/><font size="7" color="#6b7682">{esc(extra[:300])}</font>' if extra else "") + \
                (f'<br/><font size="7" color="#6b7682">{"e.g. " if en else "例："}{esc(samples[:200])}</font>' if samples else "")
            sv = it.get("sev") or "info"
            rows.append([Paragraph(desc, S["cell"]), Paragraph(esc(it.get("count")), S["cellc"]),
                         Paragraph(f'<font color="{sev_c.get(sv, C_SUB).hexval().replace("0x", "#")}">{sev_n.get(sv, sv)}</font>',
                                   S["cellc"])])
        t = Table(rows, colWidths=[W * 0.7, W * 0.12, W * 0.18], repeatRows=0)
        t.setStyle(TableStyle(style))
        story.append(t)

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont(FONT, 7.5)
        canvas.setFillColor(C_SUB)
        canvas.drawString(d.leftMargin, 10 * mm, _("审读 · AI 文本检测报告　任何 AI 检测都有误判，仅供作者自查"))
        canvas.drawRightString(d.leftMargin + d.width, 10 * mm, f"Page {d.page}" if en else f"第 {d.page} 页")
        canvas.setStrokeColor(C_RULE)
        canvas.line(d.leftMargin, 13 * mm, d.leftMargin + d.width, 13 * mm)
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
