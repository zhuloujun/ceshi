#!/usr/bin/env bash
# 把整理好的书库文本（受版权保护，不能明文放进公开仓库）打包、加密，分卷推送到数据分支，环境重启后可一键恢复。
# 2026-10-27：运行环境重启，临时目录里转好的 586 本书全部丢失，之后加上这个备份。
#
# 备份：bash backup_corpus.sh backup <书库文件夹> <密钥文件> [数据分支，默认 data-1020]
# 恢复：bash backup_corpus.sh restore <恢复到的文件夹> <密钥文件> [数据分支]
# 密钥只放在本地文件和用户自己的 Google Drive（data_key.txt），永远不写进仓库、不打印。
set -euo pipefail
MODE=$1; DIR=$2; KEY=$3; BRANCH=${4:-data-1020}
REPO=https://github.com/zhuloujun/ceshi
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
git clone -q --depth 1 -b "$BRANCH" "$REPO" "$WORK/repo"
if [ "$MODE" = backup ]; then
  tar -C "$DIR" -cf - . | xz -T0 -6 | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass "file:$KEY" \
    | split -b 90m -d -a 2 - "$WORK/repo/corpus_books.enc.part"
  cd "$WORK/repo"
  git rm -q --ignore-unmatch corpus_books.enc.part* >/dev/null 2>&1 || true
  git add corpus_books.enc.part*
  git commit -qm "encrypted corpus backup" && git push -q origin "HEAD:$BRANCH"
  ls -la corpus_books.enc.part* | awk '{print $5, $9}'
elif [ "$MODE" = restore ]; then
  mkdir -p "$DIR"
  cat "$WORK"/repo/corpus_books.enc.part* | openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "file:$KEY" \
    | xz -dc | tar -C "$DIR" -xf -
  echo "已恢复到 $DIR：$(find "$DIR" -name '*.txt' | wc -l) 个文本文件"
fi
