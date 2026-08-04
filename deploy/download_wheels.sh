#!/usr/bin/env bash
# 在【有网】机器上执行：下载本项目全部依赖（含传递依赖）为 wheel 文件
#
# 用途：为断网客户环境准备离线依赖包。
# 建议直接在目标 VM（Linux x86_64）上执行，确保 wheel 与目标平台/Python 版本完全匹配。
#
# 用法：
#   bash deploy/download_wheels.sh
#
# 产物：deploy/wheels/ 目录下的所有 .whl 文件，供 install_offline.sh 使用。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WHEELS_DIR="$SCRIPT_DIR/wheels"
REQ_FILE="$PROJECT_DIR/requirements.txt"

echo "==> 依赖文件: $REQ_FILE"
echo "==> wheel 输出目录: $WHEELS_DIR"

mkdir -p "$WHEELS_DIR"

# 下载所有依赖（含传递依赖）为 wheel
# --dest 指定输出目录
# pip 会自动解析依赖树并下载全部所需包
python3 -m pip download \
    -r "$REQ_FILE" \
    -d "$WHEELS_DIR"

echo ""
echo "==> 下载完成，wheel 文件清单："
ls -1 "$WHEELS_DIR"

echo ""
echo "==> 下一步：将 deploy/wheels/ 目录连同 requirements.txt 一起拷贝到断网机器，"
echo "    然后在断网机器执行：bash deploy/install_offline.sh"
