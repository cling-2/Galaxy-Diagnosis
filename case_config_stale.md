---
env_type: container
tags: [galaxy-api, config, upgrade]
---

# 配置残留导致升级后启动失败

## 现象
银河平台从 v2.2 升级到 v2.3 后，galaxy-api 组件启动失败，日志报 config version mismatch。

## 根因
升级脚本未清理 /etc/galaxy/ 下旧版本配置文件，api 启动时读到 v2.2 schema 与 v2.3 代码不兼容。

## 修复
1. 备份旧配置：cp /etc/galaxy/api.conf /etc/galaxy/api.conf.bak
2. 清理残留：rm /etc/galaxy/api.conf
3. 重新生成：galaxy-api config init --version v2.3
4. 重启服务：systemctl restart galaxy-api
5. 验证：systemctl status galaxy-api