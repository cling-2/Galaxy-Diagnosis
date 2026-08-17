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
#
# 模型导入：
#   遍历 deploy/offline/*.gguf，按文件名自动推导 Ollama 注册名并导入。
#   放几个 gguf 就导入几个，名字与文件实际参数量一致，不硬编码。

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

    # 创建 ollama 用户（如果不存在且为 root）
    if ! id ollama &>/dev/null && [ "$(id -u)" -eq 0 ]; then
        useradd -r -s /bin/false -d /var/lib/ollama ollama 2>/dev/null || true
    fi
    mkdir -p /var/lib/ollama

    # 启动 Ollama：优先 systemd，降级为后台进程（如 Docker 容器无 systemd）
    if command -v systemctl &>/dev/null && systemctl --quiet is-system-running 2>/dev/null; then
        # systemd 可用：创建服务单元
        cat > /etc/systemd/system/ollama.service <<SVCEOF
[Unit]
Description=Ollama Service
After=network-online.target

[Service]
User=ollama
Group=ollama
ExecStart=/usr/local/bin/ollama serve
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KEEP_ALIVE=30m"
Environment="OLLAMA_NUM_PARALLEL=1"
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
SVCEOF
        chown -R ollama:ollama /var/lib/ollama
        systemctl daemon-reload
        systemctl enable ollama
        systemctl start ollama
        sleep 2
        if systemctl is-active --quiet ollama; then
            ok "Ollama 服务已启动 (systemd)"
        else
            fail "Ollama 服务启动失败，请检查: systemctl status ollama"
            exit 1
        fi
    else
        # 无 systemd（如 Docker 容器）：后台启动
        echo "    无 systemd，以后台进程方式启动 Ollama..."
        OLLAMA_HOST=127.0.0.1:11434 /usr/local/bin/ollama serve &
        OLLAMA_PID=$!
        sleep 2
        if kill -0 "$OLLAMA_PID" 2>/dev/null; then
            ok "Ollama 已启动 (PID=$OLLAMA_PID, 非 systemd 模式)"
        else
            fail "Ollama 后台启动失败"
            exit 1
        fi
    fi
fi

# ---------- 2. 导入模型 ----------
# 确保 Ollama 服务正在运行（Docker 每次新容器进程不保留，需重新启动）
if ! curl -sf http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    echo "    Ollama 服务未运行，正在启动..."
    OLLAMA_HOST=127.0.0.1:11434 \
    OLLAMA_FLASH_ATTENTION=1 \
    OLLAMA_KEEP_ALIVE=30m \
    OLLAMA_NUM_PARALLEL=1 \
    /usr/local/bin/ollama serve &
    OLLAMA_PID=$!
    sleep 2
    if kill -0 "$OLLAMA_PID" 2>/dev/null; then
        ok "Ollama 已启动 (PID=$OLLAMA_PID)"
    else
        fail "Ollama 启动失败"
        exit 1
    fi
else
    ok "Ollama 服务已运行"
fi

echo ""
echo "==> [2/3] 导入模型..."

# 从 GGUF 文件名自动推导 Ollama 模型注册名
# 例：Qwen3-8B-Q4_K_M.gguf → qwen3:8b；Qwen3-1.7B-Q8_0.gguf → qwen3:1.7b
_gguf_to_model_name() {
    local fname="${1##*/}"  # basename，不依赖外部命令
    local base="${fname%.gguf}"
    # 按横杠拆分：Qwen3  8B  Q4_K_M  或  Qwen3  1.7B  Q8_0
    local IFS='-'
    local parts=($base)
    if [ ${#parts[@]} -lt 2 ]; then
        return 1
    fi
    local series="${parts[0],,}"   # qwen3
    local param="${parts[1],,}"    # 8b / 4b / 1.7b
    echo "${series}:${param}"
}

# 遍历 offline 目录中所有 .gguf 文件，按实际文件名注册（不硬编码参数量）
GGUF_FILES=()
while IFS= read -r -d '' f; do
    GGUF_FILES+=("$f")
done < <(find "$OFFLINE_DIR" -maxdepth 1 -name '*.gguf' -print0 | sort -z)

if [ ${#GGUF_FILES[@]} -eq 0 ]; then
    fail "未找到模型文件 (*.gguf) in $OFFLINE_DIR"
    echo "💡 请先在有网机器上执行 prepare_offline.sh 下载模型"
    exit 1
fi

ANY_MODEL_IMPORTED=0
IMPORTED_NAMES=()
for GGUF_FILE in "${GGUF_FILES[@]}"; do
    GGUF_BASENAME=$(basename "$GGUF_FILE")
    MODEL_NAME=$(_gguf_to_model_name "$GGUF_FILE")
    if [ -z "$MODEL_NAME" ]; then
        echo "    ⚠ 无法从文件名推导模型名: $GGUF_BASENAME，跳过"
        continue
    fi

    if ollama list | grep -q "^$MODEL_NAME"; then
        ok "模型已存在: $MODEL_NAME"
    else
        TMP_MODElFILE=$(mktemp)
        cat > "$TMP_MODElFILE" <<EOF
FROM $GGUF_FILE
EOF
        echo "    从 $GGUF_BASENAME 导入为 $MODEL_NAME ..."
        if ollama create "$MODEL_NAME" -f "$TMP_MODElFILE"; then
            ok "模型导入成功: $MODEL_NAME"
        else
            fail "模型导入失败: $MODEL_NAME"
            rm -f "$TMP_MODElFILE"
            if [ "$ANY_MODEL_IMPORTED" -eq 0 ]; then
                exit 1
            fi
        fi
        rm -f "$TMP_MODElFILE"
    fi
    ANY_MODEL_IMPORTED=1
    IMPORTED_NAMES+=("$MODEL_NAME")
done

if [ "$ANY_MODEL_IMPORTED" -eq 0 ]; then
    fail "没有成功导入任何模型"
    exit 1
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

# 生成 galaxy-diag 启动脚本（不依赖 pip 构建，直接用 PYTHONPATH 跑模块）
# 注：src layout + pip install -e . 需要 setuptools 构建依赖，离线 wheels 不含，
# 故改用启动器方式：设 PYTHONPATH=src 后执行 python -m galaxy_diag。
LAUNCHER="$VENV_DIR/bin/galaxy-diag"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# Galaxy-Diag 启动器（离线部署生成）
export PYTHONPATH="\${PYTHONPATH:+\$PYTHONPATH:}$PROJECT_DIR/src"
exec "$VENV_DIR/bin/python" -m galaxy_diag "\$@"
EOF
chmod +x "$LAUNCHER"
ok "启动器已安装: $LAUNCHER"

ok "Python 依赖安装完成"

# ---------- 汇总 ----------
echo ""
echo "=============================="
echo "  部署完成"
echo "=============================="
echo ""
echo "  Ollama:  $(ollama --version 2>&1 | head -1)"
echo "  模型:    ${IMPORTED_NAMES[*]}"
echo "  依赖:    $(python3 -c 'import openai, httpx, yaml, rich; print("OK")' 2>&1)"
echo ""
echo "下一步："
echo "  source venv/bin/activate"
echo "  galaxy-diag        # 或 python3 -m galaxy_diag"
echo ""
echo "可用模型（通过 GALAXY_LLM_MODEL 或 config.yaml 切换）："
for name in "${IMPORTED_NAMES[@]}"; do
    echo "  - $name"
done
