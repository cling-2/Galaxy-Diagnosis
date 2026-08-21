---
env_type: container
tags: [galaxy-test-agent, health-check, config, check_interval]
---

# 容器内健康检查配置项为 0 导致检查不触发

## 现象

银河平台测试组件 galaxy-test-agent 健康检查未触发，检查发现配置文件 /tmp/galaxy-test/health.conf 中
check_interval 配置为 0，导致健康检查间隔为 0 不执行。

## 根因

check_interval=0 表示检查间隔为 0 秒，健康检查逻辑会跳过执行。需修正为合理间隔值（如 60 秒）。

## 修复步骤

1. 修改配置项 check_interval 从 0 改为 60：
   sed -i 's/check_interval=0/check_interval=60/' /tmp/galaxy-test/health.conf
2. 验证配置已修正：
   grep 'check_interval' /tmp/galaxy-test/health.conf

## 验证

执行 grep 'check_interval' /tmp/galaxy-test/health.conf，应输出 check_interval=60，exit 0
即修复成功。