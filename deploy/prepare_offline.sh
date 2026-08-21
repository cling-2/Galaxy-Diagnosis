#!/usr/bin/env bash
# 在【联网】准备机上执行：一次性下载全部离线部署介质
#
# 离线部署原则：下载机和安装机是两台不同的机器。
#   联网准备机（本脚本）  →  U盘/内网/scp  →  断网客户机（install_offline.sh）
#
# 断网客户机假设完全无网络，所有介质必须在此下载好。
#
# 产物：deploy/offline/ 目录，包含：
#   - ollama                Ollama Linux 二进制（约 800MB）
#   - qwen3-8b-q4_k_m.gguf  模型文件（约 4.9GB）
#   - wheels/               Python 依赖（Linux wheel，约 50MB）
#
# 平台注意：
#   - Python wheel 必须是 Linux 版。本脚本用 Docker 容器下载，确保平台匹配。
#     在 Windows 上直接 pip download 会得到 win_amd64 wheel，无法装到 Linux。
#   - Ollama 二进制和 GGUF 与准备机 OS 无关，任何 OS 都能下载。
#
# 用法：
#   bash deploy/prepare_offline.sh
#
# 可选环境变量覆盖默认下载源：
#   OLLAMA_URL=...  MODEL_GGUF_URL=...  PYTHON_IMAGE=python:3.10-slim bash deploy/prepare_offline.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OFFLINE_DIR="$SCRIPT_DIR/offline"
REQ_FILE="$PROJECT_DIR/requirements.txt"

# 默认下载源（可通过环境变量覆盖）
OLLAMA_URL="${OLLAMA_URL:-https://github.com/ollama/ollama/releases/download/v0.32.5/ollama-linux-amd64.tar.zst}"
OLLAMA_NAME="${OLLAMA_NAME:-ollama-linux-amd64.tar.zst}"
OLLAMA_REPACK="${OLLAMA_REPACK:-ollama-linux-amd64.tar.gz}"
MODEL_GGUF_URL="${MODEL_GGUF_URL:-https://modelscope.cn/api/v1/models/Qwen/Qwen3-1.7B-GGUF/repo?Revision=master&FilePath=Qwen3-1.7B-Q8_0.gguf}"
MODEL_GGUF_NAME="${MODEL_GGUF_NAME:-Qwen3-1.7B-Q8_0.gguf}"
EMBED_GGUF_URL="${EMBED_GGUF_URL:-https://modelscope.cn/api/v1/models/BAAI/bge-large-zh-v1.5-GGUF/repo?Revision=master&FilePath=bge-large-zh-v1.5-q8_0.gguf}"
EMBED_GGUF_NAME="${EMBED_GGUF_NAME:-bge-large-zh-v1.5-q8_0.gguf}"
PYTHON_IMAGE="${PYTHON_IMAGE:-python:3.10-slim}"

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'
info()  { echo -e "${YELLOW}▶${NC} $1"; }
ok()    { echo -e "${GREEN}✓${NC} $1"; }
fail()  { echo -e "${RED}✗${NC} $1"; }

echo "=============================="
echo "  Galaxy-Diag 离线介质下载"
echo "=============================="
echo ""

mkdir -p "$OFFLINE_DIR/wheels"

# ---------- 1. Ollama（下载 .tar.zst 后重打包为 .tar.gz）----------
# Ollama 官方只发布 .tar.zst 格式。.tar.zst 解压需要 zstd，断网客户机未必预装，
# 是离线部署的隐患。准备机有网，此处下载后用 zstd 解开再用 gzip 重新打包为
# 通用 .tar.gz，install_offline.sh 只需系统自带 tar 即可解压，消除 zstd 依赖。
echo "==> [1/4] 下载 Ollama Linux 安装包并重打包为 .tar.gz..."
OLLAMA_PKG="$OFFLINE_DIR/$OLLAMA_NAME"
OLLAMA_GZ="$OFFLINE_DIR/$OLLAMA_REPACK"

# 最终产物 .tar.gz 已存在则直接跳过
if [ -f "$OLLAMA_GZ" ] && [ -s "$OLLAMA_GZ" ]; then
    ok "已存在，跳过: $(basename "$OLLAMA_GZ") ($(du -h "$OLLAMA_GZ" | cut -f1))"
else
    # 下载 .tar.zst 源包（若已存在则复用）
    if [ ! -f "$OLLAMA_PKG" ] || [ ! -s "$OLLAMA_PKG" ]; then
        info "下载: $OLLAMA_URL"
        info "安装包约 1.3GB，请耐心等待..."
        curl -fSL -o "$OLLAMA_PKG" "$OLLAMA_URL"
        ok "下载完成: $(du -h "$OLLAMA_PKG" | cut -f1)"
    else
        ok "复用已下载的源包: $OLLAMA_NAME"
    fi

    # 重打包为 .tar.gz（需要 zstd，准备机有网可装）
    if ! command -v zstd &>/dev/null; then
        fail "zstd 未安装，无法解压 .tar.zst 进行重打包"
        echo "💡 准备机安装 zstd 后重试：apt-get install zstd（或 brew install zstd）"
        echo "  （仅准备机需要 zstd，断网客户机不需要）"
        exit 1
    fi
    info "重打包 .tar.zst → .tar.gz（消除客户机 zstd 依赖）..."
    zstd -d "$OLLAMA_PKG" -o /tmp/ollama-repack.tar --force -f
    gzip -c /tmp/ollama-repack.tar > "$OLLAMA_GZ"
    rm -f /tmp/ollama-repack.tar
    # 源 .tar.zst 不再需要，删除以减小介质体积
    rm -f "$OLLAMA_PKG"
    ok "重打包完成: $(basename "$OLLAMA_GZ") ($(du -h "$OLLAMA_GZ" | cut -f1))"
fi

# ---------- 2. 对话模型 GGUF ----------
echo ""
echo "==> [2/4] 下载对话模型 GGUF..."
MODEL_PATH="$OFFLINE_DIR/$MODEL_GGUF_NAME"

if [ -f "$MODEL_PATH" ] && [ -s "$MODEL_PATH" ]; then
    ok "已存在，跳过: $MODEL_GGUF_NAME ($(du -h "$MODEL_PATH" | cut -f1))"
else
    info "下载: $MODEL_GGUF_URL"
    info "模型文件约 1.8GB，请耐心等待..."
    curl -fSL -o "$MODEL_PATH" "$MODEL_GGUF_URL"
    ok "下载完成: $(du -h "$MODEL_PATH" | cut -f1)"
fi

# ---------- 3. Embedding 模型 GGUF（RAG 用，可选）----------
echo ""
echo "==> [3/4] 下载 Embedding 模型 GGUF..."
EMBED_PATH="$OFFLINE_DIR/$EMBED_GGUF_NAME"

if [ -f "$EMBED_PATH" ] && [ -s "$EMBED_PATH" ]; then
    ok "已存在，跳过: $EMBED_GGUF_NAME ($(du -h "$EMBED_PATH" | cut -f1))"
else
    info "下载: $EMBED_GGUF_URL"
    info "模型文件约 350MB，请耐心等待..."
    curl -fSL -o "$EMBED_PATH" "$EMBED_GGUF_URL"
    ok "下载完成: $(du -h "$EMBED_PATH" | cut -f1)"
fi

# ---------- 4. Python wheel（Docker 容器确保 Linux 平台）----------
echo ""
echo "==> [4/4] 下载 Python wheel（Docker 容器，Linux 平台）..."

# 先清空 wheels 目录，避免多次运行累积重复版本包
rm -f "$OFFLINE_DIR"/wheels/*.whl

if command -v docker &>/dev/null; then
    info "使用 Docker 容器下载（确保 Linux 平台匹配）"
    info "使用镜像: $PYTHON_IMAGE"
    docker run --rm \
        -v "$PROJECT_DIR:/project" \
        "$PYTHON_IMAGE" \
        bash -c "pip download -r /project/requirements.txt -d /project/deploy/offline/wheels"
elif uname -s | grep -qi linux; then
    # Docker 不可用但当前已是 Linux：直接 pip download（平台天然匹配）
    info "Docker 不可用，但当前为 Linux 环境，直接 pip download"
    pip3 download -r "$REQ_FILE" -d "$OFFLINE_DIR/wheels"
else
    echo ""
    echo "    ⚠ Docker 不可用且非 Linux 环境！"
    echo "    Python wheel 必须在 Linux 环境下载以保证平台匹配。"
    echo "    请安装 Docker Desktop，或在任意 Linux 机器上执行："
    echo "      pip3 download -r requirements.txt -d deploy/offline/wheels"
    exit 1
fi

wheel_count=$(ls -1 "$OFFLINE_DIR/wheels"/*.whl 2>/dev/null | wc -l)
ok "下载完成: $wheel_count 个 wheel 文件"

# ---------- 汇总 ----------
echo ""
echo "=============================="
echo "  离线介质汇总"
echo "=============================="
echo ""
echo "目录: $OFFLINE_DIR/"
echo ""

all_ok=1

if [ -f "$OLLAMA_GZ" ] && [ -s "$OLLAMA_GZ" ]; then
    ok "ollama     Ollama 安装包 (.tar.gz) ($(du -h "$OLLAMA_GZ" | cut -f1))"
else
    echo "  ollama     ❌ 未下载"
    all_ok=0
fi

if [ -f "$MODEL_PATH" ] && [ -s "$MODEL_PATH" ]; then
    ok "*.gguf     对话模型 ($(du -h "$MODEL_PATH" | cut -f1))"
else
    echo "  *.gguf     ❌ 对话模型未下载"
    all_ok=0
fi

if [ -f "$EMBED_PATH" ] && [ -s "$EMBED_PATH" ]; then
    ok "*.gguf     Embedding 模型 ($(du -h "$EMBED_PATH" | cut -f1))"
else
    echo "  *.gguf     ❌ Embedding 模型未下载"
    all_ok=0
fi

if [ "$wheel_count" -gt 0 ]; then
    ok "wheels/    Python 依赖 ($wheel_count 个文件)"
else
    echo "  wheels/    ❌ 未下载"
    all_ok=0
fi

echo ""
if [ "$all_ok" -eq 1 ]; then
    echo "全部介质就绪。下一步："
    echo "  1. 将 deploy/offline/ 目录拷贝到断网客户机"
    echo "  2. 在断网客户机执行: bash deploy/install_offline.sh"
else
    echo "⚠ 部分介质缺失，请按上方提示补齐后重新运行本脚本。"
    exit 1
fi
