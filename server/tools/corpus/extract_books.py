"""把用户上传的电子书（mobi / epub / azw3）批量转成纯文本，按语言分到两个文件夹，并给出版权年份供人工核对。

2026-10-27：运行环境重启后，临时目录里转好的书库文本全部丢失（原始上传文件还在）。为了随时能从原始文件重建，
把转换脚本放进仓库；书库文本本身受版权保护，不进公开仓库，只以加密包的形式存放（见 backup_corpus.sh）。

用法：python extract_books.py <上传文件夹> <输出文件夹>
  输出：<输出>/en/*.txt（英文）、<输出>/zh/*.txt（含大量汉字）、<输出>/index.tsv（文件名、词数、汉字数、版权年份）
"""
from __future__ import annotations

import html
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path


def from_zip(p: str) -> str:
    z = zipfile.ZipFile(p)
    key = lambda n: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", n)]
    keep = [n for n in z.namelist() if n.lower().endswith((".html", ".xhtml", ".htm", ".xml")) and not n.startswith("META-INF/")]
    return "\n".join(z.read(n).decode("utf-8", "ignore") for n in sorted(keep, key=key))


def to_text(path: str) -> str:
    if open(path, "rb").read(2) == b"PK":
        raw = from_zip(path)
    else:
        import mobi  # pip install mobi
        tmp, out = mobi.extract(path)
        try:
            raw = from_zip(out) if out.endswith(".epub") else open(out, "rb").read().decode("utf-8", "ignore")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    raw = re.sub(r"(?i)</?p\b[^>]*>|<br[^>]*>|</?blockquote\b[^>]*>|</?div\b[^>]*>|</h\d>", "\n", raw)
    raw = html.unescape(re.sub(r"<[^>]+>", "", raw))
    return re.sub(r"\n\s*\n+", "\n", raw)


def clean_name(f: str) -> str:
    base = os.path.basename(f)
    base = re.sub(r"^[0-9a-f]{8}-", "", base)                       # 上传时加的前缀
    base = re.sub(r"\.(original_)?(mobi|epub|azw3)$", "", base, flags=re.I)
    base = re.sub(r"_?z-library.*$", "", base)
    return (re.sub(r"[^\w.-]", "_", base).strip("_") or "book")[:90]


def main(src: str, dst: str) -> None:
    out = Path(dst)
    (out / "en").mkdir(parents=True, exist_ok=True)
    (out / "zh").mkdir(parents=True, exist_ok=True)
    rows = []
    for f in sorted(Path(src).iterdir()):
        if not re.search(r"\.(original_)?(mobi|epub|azw3)$", f.name, re.I):
            continue
        name = clean_name(str(f))
        try:
            raw = to_text(str(f))
        except Exception as e:  # noqa: BLE001
            rows.append((name, "FAIL", str(e)[:80], ""))
            continue
        cjk = len(re.findall(r"[一-鿿]", raw))
        words = len(raw.split())
        lang = "zh" if cjk > 0.3 * max(words, 1) else "en"
        yrs = sorted({int(y) for y in re.findall(r"(?:©|Copyright|copyright|版权)\s*(?:\(c\)\s*)?((?:19|20)\d\d)", raw[:60000])})
        dest = out / lang / f"{name}.txt"
        if dest.exists():
            continue
        dest.write_text(raw, "utf-8")
        rows.append((f"{lang}/{name}", words, cjk, ",".join(map(str, yrs[-3:]))))
    with open(out / "index.tsv", "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write("\t".join(map(str, r)) + "\n")
    print(f"处理 {len(rows)} 个文件 → {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
