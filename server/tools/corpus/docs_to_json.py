"""把用户上传的 AI / 真人文档（docx、txt、md、pdf）用网站同一套读取方式转成 JSON 列表 [{name, src, text}]，
去掉参考文献、"字数说明"之类的提示语，供查重、离线验收和训练打包使用。

用法：python docs_to_json.py <输出.json> <来源标签> <文件1> [文件2 ...]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.docparse import _docx_text  # noqa: E402  与网站读取 docx 的方式一致


def read_any(p: Path) -> str:
    suf = p.suffix.lower()
    if suf == ".docx":
        return _docx_text(p.read_bytes())
    if suf == ".pdf":
        return subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True).stdout
    return p.read_text("utf-8", errors="ignore")


def clean(t: str) -> str:
    keep = []
    for line in t.replace("\f", "\n").split("\n"):
        s = line.strip()
        if re.match(r"^(Selected )?(References|Sources|Works Cited|Bibliography|Further Reading|参考文献)\b", s, re.I) and len(s.split()) < 6:
            break
        if re.search(r"word count|body length|detector|https?://|字数", s, re.I) and len(s.split()) < 25:
            continue
        if re.match(r"^\[\d+\]", s):
            continue
        keep.append(line)
    t = re.sub(r"\s*\[\d+(?:[,–-]\s*\d+)*\]", "", "\n".join(keep))
    t = re.sub(r"\$\$.*?\$\$|```.*?```", "", t, flags=re.S)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def main() -> None:
    out, src, files = sys.argv[1], sys.argv[2], sys.argv[3:]
    rows = []
    for f in files:
        p = Path(f)
        name = re.sub(r"^[0-9a-f]{8}-", "", p.stem)
        rows.append({"name": name, "src": src, "text": clean(read_any(p))})
    Path(out).write_text(json.dumps(rows, ensure_ascii=False), "utf-8")
    for r in rows:
        print(r["name"][:50], len(r["text"].split()))


if __name__ == "__main__":
    main()
