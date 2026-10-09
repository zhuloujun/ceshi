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
        return "参考文献，不计入"
    if k == "table":
        return "表格，不计入"
    if k == "frontmatter":
        return "题目 / 作者信息，不计入"
    if k == "quotation":
        return "引文为主，不计入"
    if k == "reference_only":
        return "仅供参考，不计入"
    return seg.get("label") or ("接近阈值（未计入）" if seg.get("near_threshold") else "未达阈值")


def bar(summary, width):
    """AI 率分级色条：高度 / 中度 / 轻度 / 接近阈值 / 其余。"""
    parts = [("高度疑似", summary.get("high_rate") or 0, C_HIGH), ("中度疑似", summary.get("mid_rate") or 0, C_MID),
             ("轻度疑似", summary.get("light_rate") or 0, C_LIGHT),
             ("接近阈值（未计入）", summary.get("near_threshold_rate") or 0, C_NEAR)]
    rest = max(0.0, 1 - sum(p for _, p, _ in parts))
    d = Drawing(width, 34)
    x, h = 0.0, 12
    d.add(Rect(0, 20, width, h, fillColor=colors.HexColor("#eef1f3"), strokeColor=None))
    for _, p, c in parts:
        w = width * p
        if w > 0.3:
            d.add(Rect(x, 20, w, h, fillColor=c, strokeColor=None))
        x += w
    lx = 0
    for name, p, c in parts + [("未见 AI 特征", rest, colors.HexColor("#eef1f3"))]:
        d.add(Rect(lx, 4, 7, 7, fillColor=c, strokeColor=C_RULE, strokeWidth=0.3))
        label = f"{name} {p * 100:.1f}%"
        d.add(String(lx + 10, 4.5, label, fontName=FONT, fontSize=7.5, fillColor=C_SUB))
        lx += 12 + pdfmetrics.stringWidth(label, FONT, 7.5) + 10
    return d


def build(payload: dict) -> bytes:
    res = payload.get("result") or {}
    s = res.get("summary") or {}
    works = res.get("works") or []
    segs = res.get("segments") or []
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title="审读 · AI 文本检测报告", author="审读")
    W = doc.width
    story = [Paragraph("审读 · AI 文本检测报告", S["title"]),
             Paragraph(esc(f"来源：{payload.get('source') or '粘贴文本'}　·　生成时间："
                           f"{payload.get('generated_at') or datetime.now().strftime('%Y-%m-%d %H:%M')}"), S["meta"]),
             Spacer(1, 8)]

    # ---- 摘要：大号 AI 率 + 关键数字 ----
    ai = s.get("ai_rate")
    star = "*" if s.get("low_rate_caution") else ""
    big = Paragraph(f'<font color="{rate_color(ai).hexval().replace("0x", "#")}">{pct(ai)}{star}</font>', S["big"])
    verdict = ("疑似 AI 生成为主" if (ai or 0) >= 0.5 else "部分内容疑似 AI" if (ai or 0) > 0 else "未见明显 AI 特征")
    left = [big, Paragraph(f"AI 率 · {verdict}", S["bigsub"])]
    stats = [
        ["总字数", f"{s.get('total_chars', '—')}", "计入字数", f"{s.get('counted_chars', '—')}"],
        ["未计入（参考文献 / 引文等）", f"{s.get('excluded_chars', '—')}", "平均 AI 概率", pct(s.get("mean_prob"))],
        ["判定阈值", pct(s.get("threshold")), "校准", "已校准" if s.get("calibrated") else "未校准"],
        ["检测模式", "快速（抽样）" if s.get("mode") == "fast" else "完整",
         "文体", "、".join(f"{REG.get(k, k)} {v} 字" for k, v in (s.get("chars_by_register") or {}).items()) or "—"],
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
        story.append(Paragraph(esc("AI 率的分母 = 计入字数 " + str(s.get("counted_chars", "—")) + " 字；未计入：" +
                                   "、".join(f"{names.get(k, k)} {v} 字" for k, v in ex.items())), S["small"]))

    notes = s.get("reliability_notes") or []
    if notes:
        story.append(Paragraph("提示", S["h2"]))
        story += [Paragraph(esc(n), S["note"], bulletText="•") for n in notes]
    story.append(Spacer(1, 4))
    story.append(Paragraph(esc(payload.get("method") or ""), S["small"]))
    story.append(Paragraph("说明：任何 AI 检测都有误判，本报告只供作者自查，不能作为学术不端判定依据。", S["small"]))

    # ---- 已知误差 + 如何解读（Weber-Wulff 等 2023、Liang 等 2023 对检测报告的要求：说明测的是什么、
    #      在相应文体上的检出率与误判率、还需要哪些旁证）----
    er = s.get("error_rates") or []
    if er:
        story.append(Paragraph("已知误差（本工具在独立测试集上的实测）", S["h2"]))
        rows = [[Paragraph(x, S["cellc"]) for x in ("文体", "测试集（未参与训练和校准）", "AI 检出率", "人写误判率（95% 上限）")]]
        for g in er:
            for k, e in enumerate(g["sets"]):
                fp = pct(e.get("human_flagged"))
                if e.get("human_flagged_upper95") is not None:
                    fp += f"（≤{pct(e['human_flagged_upper95'])}）"
                rows.append([Paragraph(esc(g["name"]) if k == 0 else "", S["cell"]),
                             Paragraph(esc(f'{e["name"]}（AI {e["n_ai"]} / 人写 {e["n_human"]}）'), S["cell"]),
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
            story.append(Paragraph(esc(f"{g['name']}：按上表“{g['ppv_basis'][:30]}”的检出率与误判率估算，如果送检文章里真有一半是 AI 写的，"
                                       f"被标出的文字确实是 AI 的概率约 {pct(v.get('50%'))}；只有 10% 是 AI 写的时约 {pct(v.get('10%'))}；"
                                       f"只有 2% 时约 {pct(v.get('2%'))}。"), S["small"]))
    rec = s.get("record") or {}
    if rec:
        story.append(Paragraph(esc("检测记录：语言模型 " + " / ".join(rec.get("lm") or []) + "；分类器 " +
                                   "；".join(f"{REG.get(k, k)} {v}" for k, v in (rec.get("classifiers") or {}).items()) +
                                   "；阈值 " + "；".join(f"{REG.get(k, k)} {pct(v)}" for k, v in (rec.get("thresholds") or {}).items())),
                               S["small"]))
    story.append(Paragraph("如何解读与复核", S["h2"]))
    guide = [
        "AI 率是“被判为疑似 AI 的文字占计入字数的比例”，不是“这篇文章由 AI 写成的概率”，更不是学术不端的概率。",
        "误判有基数效应：即使误判率只有 1%，检测 1000 篇真人文章也会冤枉约 10 篇。单一检测分数不能作为定论。",
        "复核被标出的段落时，请结合旁证：写作草稿与修改记录（文档版本历史）、引用资料与参考文献的核对（是否有查不到的文献）、作者能否当面讲清文中的观点和方法。",
        "人写、AI 改写混合的文字（如 AI 润色、翻译）没有明确的“标准答案”，分数落在中间（如 40%）可能对应多种写作方式，其中有的是允许的。",
        "检测模型只认得训练时见过的 AI 写法：对更新的模型、经过人工改写或翻译的文字，检出率会下降；诗词、文言这类短小、程式化的文体误差也更大（见上表）。",
    ]
    if (s.get("chars_by_register") or {}).get("en"):
        guide.append("英文非母语作者的文字更容易被误判（Liang 等，2023）；本工具的真人英文测试集包含中国作者的论文，误判率已计入上表。")
    story += [Paragraph(esc(g), S["note"], bulletText="•") for g in guide]

    # ---- 分篇结果 ----
    if len(works) > 1:
        story.append(Paragraph("分篇结果", S["h2"]))
        rows = [[Paragraph(x, S["cellc"]) for x in ("#", "作品", "文体", "字数", "AI 率", "结论")]]
        style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef1f3")), ("GRID", (0, 0), (-1, -1), 0.3, C_RULE),
                 ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
        for i, w in enumerate(works, 1):
            r = w.get("ai_rate")
            rows.append([Paragraph(str(i), S["cellc"]), Paragraph(esc(w.get("title")), S["cell"]),
                         Paragraph(esc("、".join(REG.get(x, x) for x in w.get("registers") or [])), S["cell"]),
                         Paragraph(str(w.get("chars", "")), S["cellc"]),
                         Paragraph(f'<font color="#ffffff">{pct(r)}</font>' if r is not None else "—", S["cellc"]),
                         Paragraph(esc(w.get("verdict")), S["cell"])])
            style.append(("BACKGROUND", (4, i), (4, i), rate_color(r)))
        t = Table(rows, colWidths=[W * x for x in (0.05, 0.42, 0.13, 0.09, 0.1, 0.21)], repeatRows=1)
        t.setStyle(TableStyle(style))
        story.append(t)
        if payload.get("works_note"):
            story += [Spacer(1, 3), Paragraph(esc(payload["works_note"]), S["small"])]

    # ---- 分段明细 ----
    story.append(Paragraph("分段明细", S["h2"]))
    legend = "　".join(f'<font color="{LEVEL_COLOR[k].hexval().replace("0x", "#")}">■</font> {n}' for k, n in
                      (("high", "高度疑似"), ("mid", "中度疑似"), ("light", "轻度疑似"), ("near", "接近阈值"),
                       ("low", "未达阈值"), ("none", "不计入")))
    story += [Paragraph(legend, S["small"]), Spacer(1, 4)]
    for seg in segs:
        lv = seg_level(seg)
        c = LEVEL_COLOR[lv].hexval().replace("0x", "#")
        prob = (pct(seg.get("prob")) if seg.get("prob") is not None else
                f"{pct(seg.get('ref_prob'))}（参考值）" if seg.get("ref_prob") is not None else "—")
        head = (f'<font color="{c}">■</font> <b>第 {seg.get("index", 0) + 1} 段</b>　'
                f'{esc(REG.get(seg.get("register"), "现代汉语"))} · {seg.get("chars", "")} 字 · AI 概率 '
                f'<font color="{c}">{prob}</font>　<font backColor="{c}" color="#ffffff"> {esc(seg_tag(seg))} </font>')
        sty = ParagraphStyle("seg", parent=S["body"], backColor=colors.HexColor(TINT[lv]), borderPadding=(4, 6, 4, 6),
                             borderColor=LEVEL_COLOR[lv], borderWidth=0, leftIndent=2)
        text = seg.get("text") or ""
        ind = seg.get("indicators") or ""
        block = [Paragraph(head, S["cell"]), Spacer(1, 3), Paragraph(esc(text), sty)]
        if ind:
            block += [Spacer(1, 2), Paragraph(esc(ind), S["small"])]
        block.append(Spacer(1, 9))
        if len(text) < 600:
            story.append(KeepTogether(block))
        else:
            story += block

    # ---- 格式检查 ----
    items = payload.get("format_items") or []
    if items:
        story.append(Paragraph("标点与排版格式检查", S["h2"]))
        sev_c = {"danger": C_HIGH, "warn": C_MID, "info": C_SUB, "ok": C_LOW}
        sev_n = {"danger": "需重点核查", "warn": "不规范", "info": "统计", "ok": "正常"}
        rows, style, group = [], [("GRID", (0, 0), (-1, -1), 0.3, C_RULE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")], None
        for it in items:
            if it.get("group") != group:
                group = it.get("group")
                rows.append([Paragraph(f"<b>{esc(group)}</b>", S["cell"]), "", ""])
                style += [("SPAN", (0, len(rows) - 1), (-1, len(rows) - 1)),
                          ("BACKGROUND", (0, len(rows) - 1), (-1, len(rows) - 1), colors.HexColor("#eef1f3"))]
            extra = " ".join(x for x in (it.get("extra") or "", re.sub(r"<[^>]+>", " ", it.get("html") or "")) if x).strip()
            samples = "；".join(str(x) for x in (it.get("samples") or [])[:2])
            desc = esc(it.get("name")) + (f'<br/><font size="7" color="#6b7682">{esc(extra[:300])}</font>' if extra else "") + \
                (f'<br/><font size="7" color="#6b7682">例：{esc(samples[:200])}</font>' if samples else "")
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
        canvas.drawString(d.leftMargin, 10 * mm, "审读 · AI 文本检测报告　任何 AI 检测都有误判，仅供作者自查")
        canvas.drawRightString(d.leftMargin + d.width, 10 * mm, f"第 {d.page} 页")
        canvas.setStrokeColor(C_RULE)
        canvas.line(d.leftMargin, 13 * mm, d.leftMargin + d.width, 13 * mm)
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
