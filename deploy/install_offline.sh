#!/usr/bin/env bash
# 在【断网】客户机器上执行：从本地 wheel 文件离线安装依赖
#
# 前置条件：
#   1. deploy/wheels/ 目录已存在（由 download_wheels.sh 生成）
#   2. requirements.txt 存在
#   3. Python 3.10+ 已安装
#
# 用法：
#   bash deploy/install_offline.sh
#
# 可选：使用虚拟环境隔离
#   python3 -m venv venv && source venv/bin/activate
#   bash deploy/install_offline.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WHEELS_DIR="$SCRIPT_DIR/wheels"
REQ_FILE="$PROJECT_DIR/requirements.txt"

if [ ! -d "$WHEELS_DIR" ]; then
    echo "❌ 错误: $WHEELS_DIR 目录不存在"
    echo "💡 请先在有网机器上执行 bash deploy/download_wheels.sh 生成 wheel 文件"
    exit 1
fi

wheel_count=$(ls -1 "$WHEELS_DIR"/*.whl 2>/dev/null | wc -l)
if [ "$wheel_count" -eq 0 ]; then
    echo "❌ 错误: $WHEELS_DIR 中没有 wheel 文件"
    echo "💡 请先在有网机器上执行 bash deploy/download_wheels.sh 生成 wheel 文件"
    exit 1
fi

echo "==> wheel 文件数量: $wheel_count"
echo "==> 开始离线安装..."

python3 -m pip install \
    --no-index \
    --find-links="$WHEELS_DIR" \
    -r "$REQ_FILE"

echo ""
echo "✅ 依赖离线安装完成"
