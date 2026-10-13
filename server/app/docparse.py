"""服务端文档解析（供 API 直接上传文件使用；网页端在浏览器里解析，不走这里）。"""
from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


MAX_DOCX_XML = 200 * 1024 * 1024        # 解压后的正文 XML 最大 200 MB（防"压缩炸弹"把内存撑爆）
MAX_PDF_PAGES = 2000


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        try:
            info = z.getinfo("word/document.xml")
        except KeyError:
            raise ValueError("不是有效的 .docx 文件（缺少 word/document.xml）")
        if info.file_size > MAX_DOCX_XML or (info.compress_size and info.file_size / info.compress_size > 400):
            raise ValueError("文档内容异常（解压后过大），请检查文件")
        with z.open(info) as f:
            xml = f.read(MAX_DOCX_XML + 1)
    if len(xml) > MAX_DOCX_XML:
        raise ValueError("文档内容异常（解压后过大），请检查文件")
    # Word 正文里不会有 DTD / 实体定义；有的话可能是"实体膨胀"攻击，直接拒绝
    if b"<!DOCTYPE" in xml[:4096].upper() or b"<!ENTITY" in xml.upper():
        raise ValueError("文档格式异常，请用 Word 另存为 .docx 后再上传")
    root = ET.fromstring(xml)
    paras = []
    for p in root.iter(f"{W}p"):
        parts = []
        for node in p.iter():
            if node.tag == f"{W}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{W}tab":
                parts.append("\t")
            elif node.tag in (f"{W}br", f"{W}cr"):
                parts.append("\n")
            elif node.tag == f"{W}noBreakHyphen":
                parts.append("-")          # 不间断连字符是单独的元素（"AI-Generated"），丢掉会把英文单词粘连
        # 跳过修订中被删除的文字（w:delText 不是 w:t，天然不会被收集）
        paras.append("".join(parts))
    return "\n".join(paras)


def _pdf_text(data: bytes) -> str:
    from pdfminer.high_level import extract_text
    return extract_text(io.BytesIO(data), maxpages=MAX_PDF_PAGES)


def _txt(data: bytes) -> str:
    for enc in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".docx"):
        text = _docx_text(data)
    elif name.endswith(".pdf"):
        text = _pdf_text(data)
    elif name.endswith((".txt", ".md")):
        text = _txt(data)
    else:
        raise ValueError("只支持 .docx / .pdf / .txt / .md 文件")
    # PDF 提取常见的"汉字之间多余空格 / 行内断行"做轻度清理
    text = re.sub(r"(?<=[一-鿿]) (?=[一-鿿])", "", text)
    return text.replace("\r\n", "\n").replace("\x0c", "\n")
