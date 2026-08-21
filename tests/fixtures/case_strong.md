---
env_type: container
tags: [galaxy-api, config, upgrade]
---

# 配置残留导致升级后启动失败

## 现象

银河平台升级后 galaxy-api 启动失败，日志报 config version mismatch。

## 根因

升级脚本未清理 /etc/galaxy/ 下旧版本配置文件，api 读到旧 schema 与新代码不兼容。

## 修复

备份旧配置后清理残留，重新生成 v2.3 配置，重启 galaxy-api。