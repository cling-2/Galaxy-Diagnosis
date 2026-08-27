# Case 1 配置残留致升级失败 — 本地故障模拟方案设计

> 配套文档：`docs/acceptance_test_plan.md` Case 1
> 目标：在 Windows 10 本地用 Docker 容器模拟银河平台"升级后旧配置残留导致 galaxy-api 启动失败"故障，供验收演示使用。

---

## 1. 背景与约束

### 1.1 演示环境

- 工具 `galaxy-diag` 运行在 **Docker 容器** 内（Windows 10 宿主机上的 Docker）。
- 选择容器环境的理由：天然命中 Case 1 的"容器化部署环境"设定，`/.dockerenv` 存在使 `ContainerDetector` 自动识别为 CONTAINER + DOCKER 运行时，无需伪造环境标记。
- Ollama 推理服务安装在容器内，使用真实 LLM 推理（对齐验收文档"正式验收必须使用真实推理服务"）。

### 1.2 核心约束（决定方案成败的技术点）

1. **反幻觉校验 `service_ok` 不会拦截**：`hallucination_guard._check_service_ok` 在采集后检查——若用户描述"启动失败"但所有组件状态均非 `failed`，工作流在步骤 2/7 终止（退化成 Case 2 行为）。因此 **必须有组件被采集到 `failed` 状态**，Case 1 才能继续走全闭环。
2. **容器内采集路径**：`collect_component_status` 在 DOCKER 运行时优先用 `docker ps -a`；若 `docker` CLI 不存在则回退到 `/proc` 进程树扫描（只返回 running/inactive，永远不会有 failed）。所以必须提供可被 `subprocess` 调用的 `docker` 垫片，返回 galaxy-api 为 Exited（→ status="failed"）。
3. **`requires_host` 命令被自动跳过**：`engine.py:1096` 过滤掉 `requires_host` 命令不进入执行器；`verifier.py:49` 同样过滤。LLM 生成的 `docker restart <CONTAINER>` 之类命令会自动标记 `requires_host`，仅打印"需在宿主机执行"，不影响本机执行流。
4. **回滚只在执行失败或验证未通过时触发**（`engine.py` 自动调用 `snapshot.rollback()`）：要演示回滚这个核心考核点，必须让修复执行步骤失败。
5. **快照备份目标**：`create_snapshot` 从命令中提取绝对路径（非 `/dev/` 开头且 `os.path.exists` 为真）作为受影响文件。需提供 `/etc/galaxy/api.conf` 等真实存在的文件，使其被备份到快照 `bak/` 目录。
6. **`--skip-precheck`**：容器资源可能不满足 `config.yaml` 中 model 对应的最低硬件要求，演示时需跳过硬件预检。

### 1.3 诚实性原则

**不修改 `galaxy-diag` 工具任何一行代码。** 环境状态是伪造的（垫片 + fixture 文件），但工具的全部逻辑——采集、反幻觉校验、规则匹配、LLM 推理、安全检查、人工审核、快照、执行、回滚、审计——都是真实运行的。这与验收文档"不得预置诊断结果"的要求一致：诊断结论由真实 LLM 基于真实采集（垫片输出）推理得出。

---

## 2. 模拟架构

### 2.1 总体思路

构建一个 Docker 镜像，内置 **伪造 CLI 垫片（shim）** 拦截 `subprocess` 调用并返回预设故障输出。工具代码原样运行，垫片放在 PATH 靠前位置覆盖系统命令。

### 2.2 伪造 CLI 垫片

| 垫片 | 被调用场景 | 关键行为 |
|------|-----------|---------|
| `docker` | 组件状态采集（`ps -a`）、日志采集（`logs`）、网络（`network ls`） | `ps -a` 返回 galaxy-api **Exited**（→ status="failed"）+ 其余 Up；`logs` 返回 config version mismatch 错误日志；`network ls` 返回正常网络 |
| `systemctl` | 快照记录服务状态（`status`）、修复执行（`restart`）、验证（`status`）、回滚（`restart`） | `status` 返回 active（exit 0，验证通过）；**`restart` 是可控失败点**——读取 `/tmp/restart_should_fail` 标志，存在则删除标志并返回 exit 1，不存在则 exit 0 |
| `journalctl` | 日志采集兜底（DOCKER 环境下主走 `docker logs`） | 返回 config mismatch 错误日志 |
| `galaxy-api` | LLM 可能生成的 `galaxy-api config init` 等修复命令 | 接受任意子命令，exit 0（确保执行流到达 restart 步骤） |

### 2.3 伪造故障文件（fixtures）

| 文件 | 内容 | 作用 |
|------|------|------|
| `/etc/galaxy/api.conf` | v2.3 旧版配置（含 `schema_version: v2.3`） | 快照备份目标，回滚时恢复对象 |
| `/var/log/galaxy/control.log` | config version mismatch 错误行 | 采集证据，LLM 诊断依据 |
| `/var/log/galaxy/network.log` | 正常运行日志 | 对照 |
| `/var/log/galaxy/storage.log` | 正常运行日志 | 对照 |

### 2.4 回滚 vs 成功切换机制

通过 `/tmp/restart_should_fail` 标志文件控制 `systemctl restart` 行为：

- **回滚演示**：运行前设置标志 → 修复步骤 `systemctl restart galaxy-api` 失败（标志一次性消费） → 执行失败 → 自动触发回滚 → 回滚的 `systemctl restart` 成功（标志已消费） → 展示文件恢复 + 审计日志
- **成功演示**：不设标志 → `systemctl restart galaxy-api` 成功 → `systemctl status galaxy-api` 验证通过 → 展示"修复验证通过"

---

## 3. 镜像结构

```
galaxy-diag/demo/
├── Dockerfile              # 基础镜像 + galaxy-diag + ollama + shims + fixtures
├── shims/
│   ├── docker              # 伪造 docker CLI
│   ├── systemctl           # 伪造 systemctl（含 restart 失败标志）
│   ├── journalctl          # 伪造 journalctl
│   └── galaxy-api          # 伪造 galaxy-api 二进制（任意子命令 exit 0）
├── fixtures/
│   ├── etc/galaxy/api.conf
│   ├── var/log/galaxy/control.log
│   ├── var/log/galaxy/network.log
│   └── var/log/galaxy/storage.log
├── scripts/
│   ├── demo-rollback.sh    # 设置 restart 失败标志
│   ├── demo-success.sh     # 清除标志
│   └── entrypoint.sh       # 启动 ollama + 进入 bash
└── README.md               # 演示操作步骤
```

---

## 4. 构建与运行

### 4.1 构建

```bash
cd galaxy-diag
docker build -t galaxy-diag-demo -f demo/Dockerfile .
```

### 4.2 运行

```bash
# CPU 即可跑 qwen3:1.7b；有 GPU 加 --gpus all
docker run -it --rm \
  -v "$(pwd)/src:/app/src" \
  -v "$(pwd)/config.yaml:/app/config.yaml" \
  galaxy-diag-demo
```

### 4.3 演示操作（容器内）

```bash
# 1. 演示回滚路径
demo-rollback.sh
galaxy-diag run -d \
  "银河平台升级到 v2.4 后，galaxy-api 组件启动失败，日志报错，\
  怀疑升级后旧配置文件未清理干净。这是容器化部署环境。" \
  --skip-precheck --auto

# 2. 演示成功路径
demo-success.sh
galaxy-diag run -d \
  "银河平台升级到 v2.4 后，galaxy-api 组件启动失败，日志报错，\
  怀疑升级后旧配置文件未清理干净。这是容器化部署环境。" \
  --skip-precheck --auto
```

---

## 5. 验收要点映射

| Case 1 步骤 | 模拟提供 | 工具行为 |
|-------------|---------|---------|
| 1/7 环境识别 | 真实 Docker 容器 | 输出 CONTAINER + DOCKER |
| 2/7 信息采集 | 垫片返回 galaxy-api=failed + config mismatch 日志 | 反幻觉校验通过，继续 |
| 3/7 根因分析 | LLM 看到 config mismatch 证据 | 根因涉及"配置残留/版本不匹配" |
| 4/7 修复建议 | LLM 生成备份 + 删除 + 重新生成 + 重启 | 含占位符 / 验证步骤 / 修复脚本 |
| 5/7 人工审核 | 真实 stdin 确认 | y/n/e/d/r 操作 |
| 6/7 快照 + 执行 | `/etc/galaxy/api.conf` 被备份 | 回滚路径：restart 失败 → 自动回滚 |
| 7/7 验证 | 成功路径 status 返回 active | 成功："修复验证通过"；回滚：文件恢复 + 审计 |

---

## 6. 待实现清单

1. `demo/Dockerfile`：基于 python:3.11-slim，安装 ollama + galaxy-diag 依赖，复制垫片与 fixtures，配置 PATH。
2. `demo/shims/*`：四个垫片脚本，按 §2.2 行为实现。
3. `demo/fixtures/*`：四个 fixture 文件，按 §2.3 内容编写。
4. `demo/scripts/*`：便捷脚本与 entrypoint。
5. `demo/README.md`：端到端演示操作指南。
