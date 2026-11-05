"""参考文献真实性核查（2026-11-05）：把参考文献逐条拿到公开学术数据库里查，统计查不到的条目。

依据：大模型写论文时常编造看似真实的文献（标题、作者、期刊、DOI 都像样，但根本不存在），这是比文风更直接的证据；
CheckIfExist（arXiv:2602.15871，2026）等工作用公开书目数据库核查引文；2026 年 EMNLP Findings 的“超越文本内在特征”
一类工作主张把外部证据和文本特征结合。本项目实测：用户 11/05 上传的 DeepSeek 中文论文 13 条参考文献里有 4 条（NormDetect、
Meridian、C-HAT-Bench、SHADE）在任何数据库都查不到。

做法：有 DOI 的查 Crossref（doi.org 的元数据）；没有 DOI 的英文条目按标题查 Semantic Scholar 标题匹配，再查 Crossref、arXiv；
标题相似度 ≥ 0.85 算查到。中文条目（中文期刊 Crossref 收录很少）和查询失败的条目记为“未核查”，不算查不到。
只作为提示，不改变 AI 率：查不到也可能是书籍、学位论文、会议论文或数据库没收录。只发送文献标题 / DOI，不发送正文。
"""
from __future__ import annotations

import concurrent.futures as cf
import difflib
import json
import re
import time
import urllib.parse
import urllib.request

from . import config

UA = "Shendu-refcheck/1.0 (reference existence check; https://github.com/zhuloujun/ceshi)"
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>，。；]+)", re.I)
_ARXIV = re.compile(r"arxiv[:\s./a-z]*?(\d{4}\.\d{4,5})", re.I)
_CJK = re.compile(r"[一-鿿]")


def _get(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for k in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and k < 2:      # 限流 / 临时故障：稍等重试
                time.sleep(1.5 * (k + 1))
                continue
            return e.code, b""
        except Exception:  # noqa: BLE001
            if k < 1:
                time.sleep(1)
                continue
            return None, b""
    return None, b""


def _title_hits(title: str, timeout: float):
    """依次查 OpenAlex、DBLP（计算机会议论文最全）、Crossref、Semantic Scholar、arXiv。返回 (是否查到, 正常应答的数据库数)。"""
    q = urllib.parse.quote(title)
    answered = 0
    checks = [
        ("https://api.openalex.org/works?per-page=5&select=title&search=" + q,
         lambda d: [x.get("title") or "" for x in d.get("results", [])]),
        ("https://dblp.org/search/publ/api?format=json&h=5&q=" + q,
         lambda d: [((h.get("info") or {}).get("title") or "") for h in ((d.get("result") or {}).get("hits") or {}).get("hit", [])]),
        ("https://api.crossref.org/works?rows=5&select=title&query.bibliographic=" + q,
         lambda d: [(x.get("title") or [""])[0] for x in d["message"]["items"]]),
        ("https://api.semanticscholar.org/graph/v1/paper/search/match?fields=title&query=" + q,
         lambda d: [x.get("title", "") for x in d.get("data") or []]),
    ]
    for url, pick in checks:
        st, body = _get(url, timeout)
        if st == 404 and "semanticscholar" in url:
            answered += 1
            continue
        if st != 200:
            continue
        try:
            titles = pick(json.loads(body))
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        answered += 1
        if any(_sim(title, t) >= 0.85 for t in titles):
            return True, answered
        if answered >= 3:
            break
    st, body = _get("https://export.arxiv.org/api/query?max_results=3&search_query=" + urllib.parse.quote(f'ti:"{title}"'), timeout)
    if st == 200:
        answered += 1
        for t in re.findall(rb"<title>(.*?)</title>", body, re.S)[1:]:
            if _sim(title, t.decode("utf-8", "ignore")) >= 0.85:
                return True, answered
    return False, answered


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", (t or "").lower())).strip()


def _sim(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def split_entries(ref_text: str) -> list[str]:
    """把参考文献段落拆成一条一条：按行，或按 [1] / 1. 这样的编号。"""
    lines = [l.strip() for l in (ref_text or "").split("\n") if l.strip()]
    out = []
    for l in lines:
        if re.fullmatch(r"(参考文献|references?|bibliography|works cited)[:：]?", l, re.I):
            continue
        parts = re.split(r"(?=\[\d{1,3}\]\s)", l)
        out += [p.strip() for p in parts if len(p.strip()) >= 25 and re.search(r"(19|20)\d\d", p)]   # 没有年份的多半是上一条的续行
    return out


def guess_title(entry: str) -> str:
    """从一条英文文献里猜标题：引号里的部分；否则按“作者. 标题. 期刊”的句点分段，取第一段长度合适、不像作者名单的。"""
    e = re.sub(r"^\[\d{1,3}\]\s*|^\d{1,3}[.)]\s*", "", entry)
    m = re.search(r"[“\"]([^”\"]{15,250})[”\"]", e)
    if m:
        return m.group(1).strip(" .")
    e = re.sub(r"\(\d{4}[a-z]?\)\.?", ".", e)                    # APA 的 (2024).
    # 去掉开头的作者名单（“Lastname, X. Y., Lastname, X., & …”），否则作者缩写里的句点会把标题切碎
    e = re.sub(r"^(?:(?:and\s+|&\s*)?[A-Z][\w'\-]+,\s*(?:[A-Z]\.\s?(?:-?[A-Z]\.\s?)*),?\s*)+(?:et al\.?,?\s*)?", "", e)
    parts = [p.strip() for p in re.split(r"(?<![A-Z])\.\s+", e) if p.strip()]
    venue = re.compile(r"^(in\b|proceedings|proc\.|journal|arxiv|vol\.|pp\.|doi|https?:|retrieved|accessed|\d)", re.I)
    for p in parts:
        if venue.match(p):
            continue
        words = p.split()
        if len(words) < 4 or len(p) > 260:
            continue
        caps_initials = sum(bool(re.fullmatch(r"[A-Z]\.?,?|[A-Z]\.[A-Z]\.?,?", w)) for w in words)
        if caps_initials > len(words) / 3 or re.search(r"\bet al\b", p):
            continue
        return p.strip(" .")
    return ""


def check_entry(entry: str, timeout: float = 6.0) -> dict:
    res = {"entry": entry[:300], "status": "unverified", "source": None}
    doi = _DOI.search(entry)
    if doi:
        d = doi.group(1).rstrip(".,;)")
        st, body = _get("https://api.crossref.org/works/" + urllib.parse.quote(d), timeout)
        if st == 200:
            # DOI 存在但题目对不上（大模型常给真论文的 DOI 配上编造的题目）
            t_ = guess_title(entry)
            try:
                real = (json.loads(body)["message"].get("title") or [""])[0]
            except (ValueError, KeyError):
                real = ""
            if t_ and real and _sim(t_, real) < 0.5 and not _CJK.search(entry):
                return {**res, "status": "not_found", "source": "doi-title-mismatch", "title": t_}
            return {**res, "status": "found", "source": "crossref-doi"}
        if st == 404:
            st2, _ = _get("https://doi.org/api/handles/" + urllib.parse.quote(d), timeout)   # Crossref 以外的 DOI（DataCite 等）
            if st2 == 200:
                return {**res, "status": "found", "source": "doi-handle"}
            if st2 == 404:
                return {**res, "status": "not_found", "source": "doi"}
        return res
    ax = _ARXIV.search(entry)
    if ax:
        st, body = _get(f"https://export.arxiv.org/api/query?id_list={ax.group(1)}", timeout)
        if st == 200:
            ok = b"<entry>" in body and b"<title>Error</title>" not in body
            t_ = guess_title(entry)
            ts = [x.decode("utf-8", "ignore") for x in re.findall(rb"<title>(.*?)</title>", body, re.S)[1:]]
            if ok and t_ and ts and max(_sim(t_, x) for x in ts) < 0.5:
                return {**res, "status": "not_found", "source": "arxiv-id-title-mismatch", "title": t_}
            return {**res, "status": "found" if ok else "not_found", "source": "arxiv-id"}
        return res
    if _CJK.search(entry):
        return res                                   # 中文文献：公开数据库收录少，不核查
    title = guess_title(entry)
    if len(title) < 15:
        return res
    ok, tried = _title_hits(title, timeout)
    if ok:
        return {**res, "status": "found", "source": "title", "title": title}
    if tried >= 2:                                   # 至少两个数据库正常应答、都没查到，才算“查不到”
        return {**res, "status": "not_found", "source": "title", "title": title}
    return res


def check_references(ref_texts: list[str], budget_s: float | None = None) -> dict | None:
    if not config.REF_CHECK:
        return None
    entries = [e for t in ref_texts for e in split_entries(t)][: config.REF_CHECK_MAX]
    if not entries:
        return None
    budget = budget_s if budget_s is not None else config.REF_CHECK_BUDGET_S
    t0 = time.time()
    out = []
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(check_entry, e) for e in entries]
        for f in futs:
            left = budget - (time.time() - t0)
            try:
                out.append(f.result(timeout=max(0.1, left)))
            except Exception:  # noqa: BLE001
                out.append({"entry": "", "status": "unverified", "source": None})
    n_found = sum(r["status"] == "found" for r in out)
    n_nf = sum(r["status"] == "not_found" for r in out)
    return {"total": len(out), "found": n_found, "not_found": n_nf, "unverified": len(out) - n_found - n_nf,
            "missing": [r["entry"] for r in out if r["status"] == "not_found"][:10],
            "items": [{"status": r["status"], "source": r.get("source"), "entry": r["entry"][:120]} for r in out][:40]}
