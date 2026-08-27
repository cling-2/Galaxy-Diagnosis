#!/usr/bin/env bash
# 清除 restart 失败标志：systemctl restart 成功 → 验证通过演示
rm -f /tmp/restart_should_fail
echo "[demo] 成功路径已启用：下次 galaxy-diag run 修复将执行成功并通过验证"
