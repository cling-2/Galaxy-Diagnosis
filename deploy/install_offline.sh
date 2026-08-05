#!/usr/bin/env bash
# 在【断网】客户机器上执行：从本地介质离线安装 Ollama + Python 依赖 + 模型
#
# 前置条件：
#   deploy/offline/ 目录已存在（由 prepare_offline.sh 生成），包含：
#     - wheels/                    Python 依赖
#     - ollama-linux-amd64.tar.zst Ollama 安装包
#     - *.gguf                     模型文件
#
# 用法：
#   bash deploy/install_offline.sh
#
# 虚拟环境：
#   脚本会自动检测并创建 Python 虚拟环境（项目目录下的 venv/）。
#   后续运行时需先激活：source venv/bin/activate

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OFFLINE_DIR="$SCRIPT_DIR/offline"
REQ_FILE="$PROJECT_DIR/requirements.txt"
VENV_DIR="$PROJECT_DIR/venv"

# 颜色输出
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; }

# ---------- 检查介质 ----------
if [ ! -d "$OFFLINE_DIR" ]; then
    fail "$OFFLINE_DIR 目录不存在"
    echo "💡 请先在有网机器上执行 bash deploy/prepare_offline.sh 生成离线介质"
    exit 1
fi

echo "=============================="
echo "  Galaxy-Diag 离线部署"
echo "=============================="
echo ""

# ---------- 0. Python 虚拟环境 ----------
echo "==> [0/3] 准备 Python 虚拟环境..."

if [ -d "$VENV_DIR/bin" ] && [ -f "$VENV_DIR/bin/python3" ]; then
    ok "虚拟环境已存在: $VENV_DIR"
else
    echo "    创建虚拟环境: $VENV_DIR"
    python3 -m venv "$VENV_DIR"
    if [ ! -f "$VENV_DIR/bin/python3" ]; then
        fail "虚拟环境创建失败"
        echo "💡 请确认 python3-venv 已安装: apt-get install python3-venv"
        exit 1
    fi
    ok "虚拟环境创建成功"
fi

# 激活虚拟环境（仅影响本脚本进程，不污染用户 shell）
source "$VENV_DIR/bin/activate"
ok "已激活虚拟环境: $(which python3)"

# ---------- 1. 安装 Ollama ----------
echo "==> [1/3] 安装 Ollama..."

# 检查是否已安装
if command -v ollama &>/dev/null; then
    ok "Ollama 已安装: $(ollama --version 2>&1 | head -1)"
else
    # 查找 Ollama 安装包：支持裸二进制或 .tar.zst 压缩包
    OLLAMA_BIN="$OFFLINE_DIR/ollama"
    OLLAMA_TARZST=$(ls "$OFFLINE_DIR"/ollama-linux-amd64*.tar.zst 2>/dev/null | head -1)

    if [ -f "$OLLAMA_BIN" ]; then
        # 裸二进制方式
        echo "    从裸二进制安装..."
        install -m 0755 "$OLLAMA_BIN" /usr/local/bin/ollama
    elif [ -n "$OLLAMA_TARZST" ]; then
        # .tar.zst 压缩包方式（Ollama 官方格式，含二进制+库文件）
        echo "    从 $OLLAMA_TARZST 解压安装..."
        # 检查 zstd 是否可用
        if ! command -v zstd &>/dev/null; then
            fail "zstd 未安装，无法解压 .tar.zst"
            echo "💡 安装 zstd: apt-get install zstd（离线环境需提前准备）"
            exit 1
        fi
        # 解压到临时目录
        zstd -d "$OLLAMA_TARZST" -o /tmp/ollama.tar --force
        mkdir -p /tmp/ollama_extract
        tar xf /tmp/ollama.tar -C /tmp/ollama_extract/

        # 安装 ollama 二进制
        EXTRACTED_BIN=$(find /tmp/ollama_extract -name "ollama" -type f -executable 2>/dev/null | head -1)
        if [ -z "$EXTRACTED_BIN" ]; then
            fail "解压后未找到 ollama 二进制"
            rm -rf /tmp/ollama.tar /tmp/ollama_extract
            exit 1
        fi
        install -m 0755 "$EXTRACTED_BIN" /usr/local/bin/ollama

        # 安装 lib/ollama/ 目录（含 llama-quantize 等运行时库，ollama create 需要）
        EXTRACTED_LIB=$(find /tmp/ollama_extract -type d -name "ollama" -path "*/lib/ollama" 2>/dev/null | head -1)
        if [ -n "$EXTRACTED_LIB" ]; then
            mkdir -p /usr/local/lib/ollama
            cp -r "$EXTRACTED_LIB"/* /usr/local/lib/ollama/
            chmod -R 0755 /usr/local/lib/ollama
            ok "运行时库已安装: $(ls /usr/local/lib/ollama/ | wc -l) 个文件"
        else
            echo "    ⚠ 未找到 lib/ollama 目录，ollama create 导入模型可能失败"
        fi

        rm -rf /tmp/ollama.tar /tmp/ollama_extract
    else
        fail "未找到 Ollama 安装包（裸二进制或 .tar.zst）"
        echo "💡 请先在有网机器上执行 prepare_offline.sh 下载"
        exit 1
    fi

    # 创建 ollama 用户和 systemd 服务
    if ! id ollama &>/dev/null; then
        useradd -r -s /bin/false -d /var/lib/ollama ollama
    fi
    mkdir -p /var/lib/ollama
    chown -R ollama:ollama /var/lib/ollama

    # 写入 systemd unit
    cat > /etc/systemd/system/ollama.service <<'EOF'
[Unit]
Description=Ollama Service
After=network-online.target

[Service]
User=ollama
Group=ollama
ExecStart=/usr/local/bin/ollama serve
Environment="OLLAMA_HOST=127.0.0.1:11434"
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF

    systemctl daemon-reload
    systemctl enable ollama
    systemctl start ollama
    sleep 2

    if systemctl is-active --quiet ollama; then
        ok "Ollama 服务已启动"
    else
        fail "Ollama 服务启动失败，请检查: systemctl status ollama"
        exit 1
    fi
fi

# ---------- 2. 导入模型 ----------
echo ""
echo "==> [2/3] 导入模型..."

# 找到 gguf 文件
GGUF_FILE=$(ls "$OFFLINE_DIR"/*.gguf 2>/dev/null | head -1)

if [ -z "$GGUF_FILE" ]; then
    fail "未找到模型文件 (*.gguf) in $OFFLINE_DIR"
    echo "💡 请先在有网机器上执行 prepare_offline.sh 下载模型"
    exit 1
fi

MODEL_NAME="qwen3:8b"
GGUF_BASENAME=$(basename "$GGUF_FILE")

# 检查模型是否已导入
if ollama list | grep -q "^$MODEL_NAME"; then
    ok "模型已存在: $MODEL_NAME"
else
    # 生成临时 Modelfile（指向 offline 目录中的 gguf）
    TMP_MODelfILE=$(mktemp)
    cat > "$TMP_MODelfILE" <<EOF
FROM $GGUF_FILE
EOF

    echo "    从 $GGUF_BASENAME 导入模型..."
    if ollama create "$MODEL_NAME" -f "$TMP_MODelfILE"; then
        ok "模型导入成功: $MODEL_NAME"
    else
        fail "模型导入失败"
        rm -f "$TMP_MODelfILE"
        exit 1
    fi
    rm -f "$TMP_MODelfILE"
fi

# ---------- 3. 安装 Python 依赖 ----------
echo ""
echo "==> [3/3] 安装 Python 依赖..."

WHEELS_DIR="$OFFLINE_DIR/wheels"
if [ ! -d "$WHEELS_DIR" ]; then
    fail "wheels 目录不存在: $WHEELS_DIR"
    echo "💡 请先在有网机器上执行 prepare_offline.sh 下载依赖"
    exit 1
fi

wheel_count=$(ls -1 "$WHEELS_DIR"/*.whl 2>/dev/null | wc -l)
if [ "$wheel_count" -eq 0 ]; then
    fail "$WHEELS_DIR 中没有 wheel 文件"
    exit 1
fi

echo "    离线安装 $wheel_count 个 wheel 文件..."
python3 -m pip install \
    --no-index \
    --find-links="$WHEELS_DIR" \
    -r "$REQ_FILE"

# 安装项目本身（src layout），注册 galaxy-diag 命令
python3 -m pip install \
    --no-index \
    --find-links="$WHEELS_DIR" \
    --no-deps \
    -e "$PROJECT_DIR"

ok "Python 依赖安装完成"

# ---------- 汇总 ----------
echo ""
echo "=============================="
echo "  部署完成"
echo "=============================="
echo ""
echo "  Ollama:  $(ollama --version 2>&1 | head -1)"
echo "  模型:    $MODEL_NAME"
echo "  依赖:    $(python3 -c 'import openai, httpx, yaml, rich; print(\"OK\")' 2>&1)"
echo ""
echo "下一步："
echo "  source venv/bin/activate"
echo "  galaxy-diag        # 或 python3 -m galaxy_diag"
