#!/usr/bin/env bash
set -e

# 1. 前置 shims / scripts / venv 到 PATH（确保优先于 /usr/bin）
export PATH="/opt/galaxy-diag-demo/shims:/opt/galaxy-diag-demo/scripts:/opt/galaxy-diag/venv/bin:$PATH"

# --- ollama 启动与 watchdog ---
_OLLAMA_LOG="/var/log/galaxy-diag/ollama.log"

_ensure_ollama() {
  # 检测 ollama 是否存活，不活则重启
  if curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then
    return 0
  fi
  echo "[ollama-watchdog] ollama 不可达，重新启动..."
  # 杀掉可能残留的旧进程
  pkill -x ollama 2>/dev/null || true
  sleep 1
  mkdir -p /var/log/galaxy-diag
  OLLAMA_HOST=127.0.0.1:11434 \
  OLLAMA_FLASH_ATTENTION=1 \
  OLLAMA_KEEP_ALIVE=30m \
  OLLAMA_NUM_PARALLEL=1 \
  OLLAMA_LOG_LEVEL=ERROR \
  LLAMA_LOG_LEVEL=3 \
  GIN_MODE=release \
  /usr/local/bin/ollama serve >>"$_OLLAMA_LOG" 2>&1 &
  # 等待就绪（最多 30 秒）
  for i in $(seq 1 30); do
    if curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then
      echo "[ollama-watchdog] ollama 已就绪"
      return 0
    fi
    sleep 1
  done
  echo "[ollama-watchdog] ⚠ ollama 启动超时，请检查 $_OLLAMA_LOG"
  return 1
}

# 2. 首次启动 ollama
_ensure_ollama

# 3. 启动 watchdog 后台循环：每 15 秒检测一次，ollama 挂了自动拉起
(
  while true; do
    sleep 15
    _ensure_ollama >/dev/null 2>&1
  done
) &
WATCHDOG_PID=$!
echo "[entrypoint] ollama watchdog 已启动 (PID=$WATCHDOG_PID)"

# 4. 确认模型已导入（构建时 install_offline.sh 已导入，此处仅校验）
echo "[entrypoint] 可用模型:"
ollama list 2>/dev/null || echo "  (ollama list 失败，请检查 $_OLLAMA_LOG)"

echo ""
echo "[entrypoint] 环境就绪。可用命令："
echo "  demo-rollback.sh   # 启用回滚路径"
echo "  demo-success.sh    # 启用成功路径"
echo "  galaxy-diag --skip-precheck run -d \"...\" --auto"
echo "  galaxy-diag --skip-precheck run -d \"...\" --mock --auto   # mock 快速预演"
echo ""
echo "  ollama-check       # 手动检查并重启 ollama"

exec "$@"
