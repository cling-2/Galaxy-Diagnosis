#!/usr/bin/env bash
# 在【有网】机器上执行：一次性下载全部离线部署介质
#
# 产物：deploy/offline/ 目录，包含：
#   - wheels/      Python 依赖
#   - ollama       Ollama 二进制
#   - model.gguf   模型文件
#
# 将整个 deploy/offline/ 目录拷贝到断网机器即可完成部署。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OFFLINE_DIR="$SCRIPT_DIR/offline"
REQ_FILE="$PROJECT_DIR/requirements.txt"

echo "=============================="
echo "  Galaxy-Diag 离线介质下载"
echo "=============================="
echo ""

# ---------- 1. Python 依赖 wheel ----------
echo "==> [1/3] 下载 Python 依赖 wheel..."
mkdir -p "$OFFLINE_DIR/wheels"

python3 -m pip download \
    -r "$REQ_FILE" \
    -d "$OFFLINE_DIR/wheels"

wheel_count=$(ls -1 "$OFFLINE_DIR/wheels"/*.whl 2>/dev/null | wc -l)
echo "    下载完成: $wheel_count 个 wheel 文件"

# ---------- 2. Ollama 二进制 ----------
echo ""
echo "==> [2/3] 下载 Ollama 二进制..."

if command -v curl &>/dev/null; then
    curl -fsSL -o "$OFFLINE_DIR/ollama" https://ollama.com/download/ollama-linux-amd64
    chmod +x "$OFFLINE_DIR/ollama"
    echo "    下载完成: $(du -h "$OFFLINE_DIR/ollama" | cut -f1)"
else
    echo "    ⚠ curl 不可用，请手动下载 Ollama:"
    echo "    https://ollama.com/download/ollama-linux-amd64"
    echo "    放到 $OFFLINE_DIR/ollama"
fi

# ---------- 3. 模型文件 ----------
echo ""
echo "==> [3/3] 模型文件准备..."

MODEL_PATH="$OFFLINE_DIR/qwen3-8b-q4_k_m.gguf"

if [ -f "$MODEL_PATH" ]; then
    echo "    模型文件已存在: $(du -h "$MODEL_PATH" | cut -f1)"
    echo "    如需更新，请删除后重新运行此脚本"
else
    echo "    模型文件较大（约 4.9 GB），需要手动下载："
    echo ""
    echo "    方式一：从 HuggingFace 下载"
    echo "      https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/qwen3-8b-q4_k_m.gguf"
    echo ""
    echo "    方式二：从 ModelScope 下载（国内更快）"
    echo "      pip install modelscope"
    echo '      python3 -c "from modelscope import snapshot_download; snapshot_download(\'Qwen/Qwen3-8B-GGUF\', allow_patterns=[\'*q4_k_m*\'])"'
    echo ""
    echo "    方式三：从已有 Ollama 的机器导出（最简单）"
    echo "      ollama pull qwen3:8b"
    echo "      然后把模型 blob 拷贝过来"
    echo ""
    echo "    下载后放到: $MODEL_PATH"
fi

# ---------- 汇总 ----------
echo ""
echo "=============================="
echo "  离线介质汇总"
echo "=============================="

echo ""
echo "目录: $OFFLINE_DIR/"
echo ""

if [ -d "$OFFLINE_DIR/wheels" ]; then
    echo "  wheels/    Python 依赖 ($wheel_count 个文件)"
fi
if [ -f "$OFFLINE_DIR/ollama" ]; then
    echo "  ollama     Ollama 二进制 ($(du -h "$OFFLINE_DIR/ollama" | cut -f1))"
else
    echo "  ollama     ❌ 未下载"
fi
if [ -f "$MODEL_PATH" ]; then
    echo "  *.gguf     模型文件 ($(du -h "$MODEL_PATH" | cut -f1))"
else
    echo "  *.gguf     ❌ 未下载"
fi

echo ""
echo "下一步：将 deploy/offline/ 目录拷贝到断网机器，执行："
echo "  bash deploy/install_offline.sh"
