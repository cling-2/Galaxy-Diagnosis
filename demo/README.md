# galaxy-diag Case 1 故障模拟演示

本地用 Docker 容器模拟"银河平台升级后旧配置残留致 galaxy-api 启动失败"故障，供 Case 1 验收演示。

## 原理

容器内置伪造 CLI 垫片（docker/systemctl/journalctl/galaxy-api）拦截工具的 subprocess 调用，返回预设故障输出。工具代码原样运行，不修改任何源码。通过 `/tmp/restart_should_fail` 标志切换两种演示结果：

- **回滚路径**：`systemctl restart` 失败 → 自动触发回滚（核心考核点）
- **成功路径**：`systemctl restart` 成功 → 验证通过

## 构建（离线，复用 deploy/ 介质）

镜像基于本地已有的 `python:3.10-slim`，ollama + 模型 + Python 依赖全部走 `deploy/offline/` 离线介质安装，**无需访问 docker.io / ollama.com**。

```bash
cd galaxy-diag
docker build -t galaxy-diag-demo -f demo/Dockerfile .
```

> 构建时间约 10-15 分钟（传输 3.4G 离线介质 + ollama 安装 + 模型导入 + pip 依赖）。APT 用阿里云镜像（需联网装 curl 等少量系统包）。

## 运行

```bash
docker run -it --rm --memory=12g --cpus=6 --shm-size=1g galaxy-diag-demo
```

容器启动时 entrypoint 会自动：启动 ollama → 前置垫片到 PATH → 确认模型可用。

## 演示一：回滚路径（核心考核点）

```bash
demo-rollback.sh    # 启用 restart 失败标志

galaxy-diag --skip-precheck run -d \
  "银河平台升级到 v2.4 后，galaxy-api 组件启动失败，日志报错，\
  怀疑升级后旧配置文件未清理干净。这是容器化部署环境。" \
  --auto
```

预期：步骤 6 执行修复时 `systemctl restart galaxy-api` 失败 → 自动回滚 → 从快照恢复 `/etc/galaxy/api.conf` → 审计日志记录 `rollback`。

查看快照与审计：
```bash
ls ~/.galaxy-diag/snapshots/snap_*/bak/
cat ~/.galaxy-diag/audit.jsonl | python -m json.tool
```

## 演示二：成功路径

```bash
demo-success.sh     # 清除 restart 失败标志

galaxy-diag --skip-precheck run -d \
  "银河平台升级到 v2.4 后，galaxy-api 组件启动失败，日志报错，\
  怀疑升级后旧配置文件未清理干净。这是容器化部署环境。" \
  --auto
```

预期：修复执行成功 → `systemctl status galaxy-api` 验证通过 → 输出"修复验证通过"。

## Mock 模式快速预演

不连接真实 LLM，用 `--mock` 快速跑通流程闭环（诊断结论为预设笼统结论，但采集/快照/回滚/审计链路全真）：

```bash
galaxy-diag --skip-precheck run -d \
  "银河平台升级到 v2.4 后，galaxy-api 组件启动失败，日志报错，\
  怀疑升级后旧配置文件未清理干净。这是容器化部署环境。" \
  --mock --auto
```

## 人工审核步骤

`--auto` 模式下 REVIEWING 步骤仍需人工确认。审核菜单出现时：
- `y` 确认执行
- `n` 拒绝（演示拒绝路径，工具应终止不反复要求确认）
- `e` 编辑参数（重新进入安全检查）
- `d` 删除步骤 / `r` 重排序

> **占位符填写**：若修复命令含 `<SERVICE_NAME>` 等占位符，工具会在执行前交互式要求填写实际值（Mock 模式常见，真实 LLM 一般直接给出完整命令）。填好后回车确认进入执行。

## 验收要点对照

| 步骤 | 模拟提供 | 工具行为 |
|------|---------|---------|
| 1/7 环境识别 | 真实 Docker 容器 | CONTAINER + DOCKER |
| 2/7 信息采集 | 垫片返回 galaxy-api=failed + mismatch 日志 | 反幻觉校验通过 |
| 3/7 根因分析 | LLM 看到 mismatch 证据 | 根因涉及配置残留/版本不匹配 |
| 4/7 修复建议 | LLM 生成备份+清理+重启 | 含占位符/验证步骤/脚本 |
| 5/7 人工审核 | 真实 stdin | y/n/e/d/r |
| 6/7 快照+执行 | api.conf 被备份 | 回滚或成功 |
| 7/7 验证 | status 返回 active | 验证通过/回滚审计 |

## 故障排查

- **构建慢**：首次需传输 3.4G 离线介质（ollama + 模型），后续构建有 Docker layer 缓存。
- **ollama 启动失败**：检查 `/var/log/galaxy-diag/ollama.log`；确保容器有足够内存（`--memory=12g`）。
- **硬件预检失败**：演示命令已加 `--skip-precheck` 跳过。
- **垫片未生效**：确认 `which docker` 指向 `/opt/galaxy-diag-demo/shims/docker`。
