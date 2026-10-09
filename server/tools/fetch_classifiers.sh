#!/usr/bin/env bash
# 把线上用到的各个自训练分类器（本仓库 Release）下载解压到 server/ 下，与 deploy-server.yml 的做法相同。
# 用法（仓库根目录，需要 GH_TOKEN）：bash server/tools/fetch_classifiers.sh
set -u
R="${GITHUB_REPOSITORY:-zhuloujun/ceshi}"
cd server
get() {  # $1 = Release 标签  $2 = 包名  $3 = 解压后的目录名  $4 = 放到 server/ 下的目录名
  [ -z "$1" ] && return 0
  mkdir -p /tmp/fc_$4 && gh release download "$1" -p "$2.tar.gz" -D /tmp/fc_$4 -R "$R" --clobber && \
    tar xzf /tmp/fc_$4/$2.tar.gz -C /tmp/fc_$4 && rm -rf "$4" && mv /tmp/fc_$4/$3 "$4" && echo "已取出 $1 → server/$4"
}
P=$(grep -oE '"poetry-classifier-v[0-9]+"' app/default_calibration.json | head -1 | tr -d '"')
C=$(grep -oE '"classical-classifier-v[0-9]+"' app/default_calibration.json | head -1 | tr -d '"')
E2=$(grep -oE 'english-classifier-(exp-)?v[0-9]+' app/default_calibration.json | head -1 || true)
Z2=$(grep -oE 'chinese-classifier-[A-Za-z0-9.-]+' app/config.py | head -1 || true)
E3=$(grep -oE 'EN3_CLASSIFIER_ID", "english-classifier-[A-Za-z0-9.-]+' app/config.py | grep -oE 'english-classifier-[A-Za-z0-9.-]+' || true)
E4=$(grep -oE 'EN4_CLASSIFIER_ID", "english-classifier-[A-Za-z0-9.-]+' app/config.py | grep -oE 'english-classifier-[A-Za-z0-9.-]+' || true)
get "$P" poetry-classifier poetry-classifier poetry-classifier
get "$C" classical-classifier classical-classifier classical-classifier
get "$E2" english-classifier english-classifier english-classifier
get "$Z2" chinese-classifier chinese-classifier chinese-classifier
get "$E3" english-classifier english-classifier english-doc-classifier
get "$E4" english-classifier english-classifier english-doc-classifier-2
