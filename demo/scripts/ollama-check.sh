#!/usr/bin/env bash
# 手动检查并重启 ollama（watchdog 不可靠时的兜底）
export PATH="/opt/galaxy-diag/venv/bin:$PATH"

_OLLAMA_LOG="/var/log/galaxy-diag/ollama.log"

if curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then
  echo "[ollama-check] ✓ ollama 运行中"
  ollama list 2>/dev/null
  exit 0
fi

echo "[ollama-check] ✗ ollama 不可达，重启中..."
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

for i in $(seq 1 30); do
  if curl -sf http://localhost:11434/api/version >/dev/null 2>&1; then
    echo "[ollama-check] ✓ ollama 已就绪"
    ollama list 2>/dev/null
    exit 0
  fi
  sleep 1
done
echo "[ollama-check] ⚠ 启动超时，查看日志: $_OLLAMA_LOG"
exit 1
