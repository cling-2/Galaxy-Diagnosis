#!/usr/bin/env bash
# 设置 restart 失败标志：下次 systemctl restart 失败 → 触发自动回滚演示
echo 1 > /tmp/restart_should_fail
echo "[demo] 回滚路径已启用：下次 galaxy-diag run 修复执行将失败并自动回滚"
echo "[demo] 标志文件: /tmp/restart_should_fail (一次性消费)"
