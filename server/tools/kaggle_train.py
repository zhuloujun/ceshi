"""在 Kaggle 的免费 GPU（每周约 30 小时）上训练中文 / 英文分类器，训练完把模型包下载回来。

由 .github/workflows/train-chinese.yml 和 train-english.yml 调用（需要仓库 Secrets：KAGGLE_USERNAME + KAGGLE_KEY，
或 KAGGLE_API_TOKEN）。流程：
  1. 生成一个 Kaggle 脚本（kernel）：开 GPU、开联网，克隆本仓库指定提交 → 跑 train_chinese.py / train_english.py
     → 把模型打包成 chinese-classifier.tar.gz / english-classifier.tar.gz 放到 /kaggle/working
  2. kaggle kernels push 提交运行，每分钟查一次状态
  3. 运行完成后 kaggle kernels output 下载模型包到当前目录（与原来 Modal 版的输出位置相同）

用法：python server/tools/kaggle_train.py --lang zh --sha <提交> --args "..."
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

KERNEL = r'''
import os, subprocess, sys, tarfile, time
REPO, SHA, LANG, ARGS = {repo!r}, {sha!r}, {lang!r}, {args!r}
def sh(*cmd, **kw):
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)
t0 = time.time()
sh("git", "init", "-q", "/tmp/ceshi")
sh("git", "-C", "/tmp/ceshi", "fetch", "-q", "--depth", "1", f"https://github.com/{{REPO}}", SHA)
sh("git", "-C", "/tmp/ceshi", "checkout", "-q", "FETCH_HEAD")
sh(sys.executable, "-m", "pip", "install", "-q", "transformers>=4.45,<5", "tokenizers>=0.20", "sentencepiece", "protobuf", "safetensors", "pypinyin")
import torch
print("torch", torch.__version__, "cuda", torch.cuda.is_available(),
      torch.cuda.get_device_name(0) if torch.cuda.is_available() else "-", flush=True)
torch.zeros(4, device="cuda").sum().item()   # GPU 不可用会在这里直接报错，避免在 CPU 上白跑几个小时
if LANG == "zh":
    sh("git", "clone", "-q", "--depth", "1", "https://github.com/NLP2CT/NLPCC-2025-Task1", "/tmp/nlpcc")
    cmd = ["tools/train_chinese.py", "--nlpcc", "/tmp/nlpcc/data", "--out", "/tmp/chinese-classifier"]
    name = "chinese-classifier"
elif LANG == "poetry":
    sh(sys.executable, "-m", "pip", "install", "-q", "opencc-python-reimplemented", "openpyxl")
    sh("git", "clone", "-q", "--depth", "1", "https://github.com/VelikayaScarlet/ChangAn", "/tmp/changan")
    sh("git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse", "https://github.com/chinese-poetry/chinese-poetry", "/tmp/cpoetry")
    sh("git", "-C", "/tmp/cpoetry", "sparse-checkout", "set", "--no-cone", "/全唐诗/唐诗三百首.json", "/宋词/宋词三百首.json",
       "/全唐诗/poet.tang.*.json", "/宋词/ci.song.*.json", "/元曲/", "/诗经/", "/楚辞/", "/纳兰性德/", "/曹操诗集/", "/五代诗词/")
    cmd = ["tools/train_poetry2.py", "--changan", "/tmp/changan", "--cpoetry", "/tmp/cpoetry", "--out", "/tmp/poetry-classifier"]
    try:   # 真人对联（couplet-dataset，七十多万副）
        sh("wget", "-q", "-O", "/tmp/couplet.tar.gz", "https://github.com/wb14123/couplet-dataset/releases/download/1.0/couplet.tar.gz")
        sh("tar", "xzf", "/tmp/couplet.tar.gz", "-C", "/tmp")
        cmd += ["--couplets", "/tmp/couplet"]
    except Exception as e:  # noqa: BLE001
        print("没有取到对联数据集：", e, flush=True)
    name = "poetry-classifier"
elif LANG == "classical":
    sh(sys.executable, "-m", "pip", "install", "-q", "openpyxl")
    books = subprocess.run([sys.executable, "-c", "from train_classical import TRAIN_BOOKS, LONG_CHECK_BOOKS; "
                            "from evaluate import CLASSICAL_TEST_BOOKS, SPLIT_BOOKS; "
                            "print(chr(10).join(sorted(set(TRAIN_BOOKS + CLASSICAL_TEST_BOOKS + LONG_CHECK_BOOKS + list(SPLIT_BOOKS)))))"],
                           cwd="/tmp/ceshi/server/tools", capture_output=True, text=True, check=True).stdout.split()
    sh("git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse", "https://github.com/NiuTrans/Classical-Modern", "/tmp/classical")
    sh("git", "-C", "/tmp/classical", "sparse-checkout", "set", "--no-cone", *[f"/古文原文/{{b}}/" for b in books])
    cmd = ["tools/train_classical.py", "--classical-dir", "/tmp/classical", "--out", "/tmp/classical-classifier", "--epochs", "4"]
    name = "classical-classifier"
else:
    from huggingface_hub import hf_hub_download
    hf_hub_download("yaful/MAGE", "valid.csv", repo_type="dataset", local_dir="/tmp/mage")
    cmd = ["tools/train_english.py", "--mage-dir", "/tmp/mage", "--out", "/tmp/english-classifier"]
    name = "english-classifier"
sh(sys.executable, "-u", *cmd, *ARGS, cwd="/tmp/ceshi/server")
with tarfile.open(f"/kaggle/working/{{name}}.tar.gz", "w:gz") as t:
    t.add(f"/tmp/{{name}}", arcname=name)
print(f"完成，用时 {{(time.time() - t0) / 60:.0f}} 分钟", flush=True)
'''


def kaggle(*args, check=True):
    r = subprocess.run(["kaggle", *args], capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if check and r.returncode:
        raise SystemExit(f"kaggle {' '.join(args)} 失败：{out}")
    return r.returncode, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["zh", "en", "poetry", "classical"], required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY", "zhuloujun/ceshi"))
    ap.add_argument("--args", default="")
    ap.add_argument("--max-hours", type=float, default=5.5)
    a = ap.parse_args()
    for k in [k for k, v in os.environ.items() if k.startswith("KAGGLE_") and not v.strip()]:
        del os.environ[k]          # 没设置的 Secret 在工作流里是空字符串，删掉以免干扰 Kaggle 的登录方式判断

    user = os.getenv("KAGGLE_USERNAME", "").strip()
    if not user:
        _, out = kaggle("config", "view", check=False)
        m = re.search(r"username:\s*(\S+)", out)
        user = m.group(1) if m else ""
    if not user:
        raise SystemExit("没有 Kaggle 用户名：请在 GitHub Secrets 里设置 KAGGLE_USERNAME")
    slug = f"ceshi-train-{a.lang}"
    ref = f"{user}/{slug}"
    name = {"zh": "chinese-classifier", "en": "english-classifier", "poetry": "poetry-classifier",
            "classical": "classical-classifier"}[a.lang]

    d = Path(tempfile.mkdtemp())
    (d / "train.py").write_text(KERNEL.format(repo=a.repo, sha=a.sha, lang=a.lang, args=a.args.split()), "utf-8")
    (d / "kernel-metadata.json").write_text(json.dumps({
        "id": ref, "title": slug, "code_file": "train.py", "language": "python", "kernel_type": "script",
        "is_private": True, "enable_gpu": True, "enable_internet": True,
        "dataset_sources": [], "competition_sources": [], "kernel_sources": [], "model_sources": [],
    }), "utf-8")
    _, q = kaggle("quota", check=False)
    print(f"::notice title=Kaggle 本周 GPU 额度::{q[:800]}".replace("\n", "%0A"), flush=True)

    # 推送新版本：失败（常见原因是 Kaggle 同时运行的 GPU 会话已满）就每 5 分钟重试，最多 1 小时。
    # 必须确认推送成功：否则下面查到的是上一个版本的"complete"状态，会把上一次训练的结果当成这次的。
    pushed = False
    for attempt in range(int(os.getenv("KAGGLE_PUSH_TRIES", "13"))):
        extra = ["--accelerator", "NvidiaTeslaT4"] if attempt % 2 == 0 else []
        code, out = kaggle("kernels", "push", "-p", str(d), *extra, check=False)
        print(out, flush=True)
        if code == 0 and "successfully" in out.lower() and "error" not in out.lower():
            pushed = True
            break
        print(f"::warning title=Kaggle::第 {attempt + 1} 次推送失败，5 分钟后重试：{out.strip()[-400:]}".replace("\n", "%0A"), flush=True)
        time.sleep(300)
    if not pushed:
        raise SystemExit("::error::Kaggle 推送一直失败（见上面的输出），没有开始训练")
    t0, last, seen_active = time.time(), "", False
    time.sleep(60)
    while True:
        _, st = kaggle("kernels", "status", ref, check=False)
        s = st.lower()
        if s != last:
            print(f"[{(time.time() - t0) / 60:.0f} 分钟] {st}", flush=True)
            last = s
        if "running" in s or "queued" in s:
            seen_active = True
        if seen_active and ("complete" in s or "error" in s or "cancel" in s):
            break
        if not seen_active and time.time() - t0 > 1800:
            raise SystemExit("::error::推送后 30 分钟仍未开始运行，可能拿到的是旧版本状态")
        if time.time() - t0 > a.max_hours * 3600:
            raise SystemExit("Kaggle 训练超时")
        time.sleep(60)

    outdir = d / "out"
    outdir.mkdir()
    kaggle("kernels", "output", ref, "-p", str(outdir), check=False)
    for log in outdir.glob("*.log"):           # Kaggle 运行日志（JSON 格式），打印最后部分方便排查
        try:
            lines = "".join(x.get("data", "") for x in json.loads(log.read_text("utf-8")))
        except Exception:  # noqa: BLE001
            lines = log.read_text("utf-8", "replace")
        print("----- Kaggle 日志（最后 6000 字）-----\n" + lines[-6000:], flush=True)
        tail = lines[-3500:].replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::notice title=Kaggle 日志末尾::{tail}", flush=True)
    tar = outdir / f"{name}.tar.gz"
    if "complete" not in last or not tar.exists():
        raise SystemExit(f"::error::Kaggle 训练失败（状态：{last}），见上面的日志")
    shutil.move(str(tar), f"{name}.tar.gz")
    print(f"::notice title=Kaggle 训练完成::用时 {(time.time() - t0) / 60:.0f} 分钟，"
          f"模型包 {Path(f'{name}.tar.gz').stat().st_size / 1e6:.0f} MB", flush=True)


if __name__ == "__main__":
    sys.exit(main())
