# Galaxy-Diag 部署文档

面向断网客户环境的完整离线部署流程。

## 目标环境

| 项目 | 要求 |
|------|------|
| 操作系统 | Ubuntu 22.04 x86_64（或其他 Linux x86_64） |
| CPU | 4 核及以上（小模型 1.5B–3B 可 2 核） |
| 内存 | 8 GB 及以上（小模型可 4 GB） |
| 磁盘 | 10 GB 及以上可用空间 |
| GPU | 可选（无 GPU 以 CPU 模式运行，推理速度较慢） |
| 网络 | 无公网出站要求；内网可用 |
| Python | 3.10 及以上 |

> 上表为默认模型（约 8B）的推荐配置。**实际最低要求根据 `config.yaml` 中 `llm.model` 的参数量自动推导**（见 `src/galaxy_diag/config/model_profile.py`）：如 `qwen3:1.7b` → 2核/4GB/1.94GB显存/3.5GB磁盘；`qwen3:8b` → 8核/7.4GB/5.4GB/9.2GB。如需固定某项要求，可在 `config.yaml` 显式配置 `hardware` 段（显式值优先于自动推导）。

### 客户机需预装的系统依赖

`install_offline.sh` 依赖以下系统级包，断网环境无法 `apt-get install`，必须**提前预装**或从内网镜像源安装：

| 包 | 用途 | 安装命令（联网时） |
|----|------|------------------|
| `python3` (≥3.10) | 运行工具 | `apt-get install python3` |
| `python3-venv` | 创建虚拟环境 | `apt-get install python3-venv` |
| `zstd` | 解压 Ollama `.tar.zst` | `apt-get install zstd` |

> 若客户机无这些包，可在联网准备机上 `apt-get download` 下载 deb 包，一并随介质传输后 `dpkg -i` 安装。

## 部署介质准备（在联网准备机上操作）

**核心原则**：下载机和安装机是两台不同的机器。断网客户机假设完全无网络，所有介质必须在联网准备机上下载好，再通过 U 盘 / 移动硬盘 / 内网文件服务器 / scp 传输到客户机。

准备机可以是 Windows / Linux / Mac，但需安装：
- `curl`（下载 Ollama 二进制和 GGUF）
- `docker`（下载 Linux 版 Python wheel，保证平台匹配）

> ⚠ **平台陷阱**：在 Windows 上直接 `pip download` 会得到 `win_amd64` wheel，装不进 Linux。
> 必须用 Docker 容器（Linux 镜像）下载 wheel。`prepare_offline.sh` 已自动处理。

### 一键下载全部介质

```bash
cd galaxy-diag
bash deploy/prepare_offline.sh
```

脚本会下载三样介质到 `deploy/offline/`：

| 介质 | 大小（约） | 下载方式 |
|------|-----------|---------|
| `ollama-linux-amd64.tar.zst` | ~1.3 GB | curl 从 GitHub Releases 下载 |
| `Qwen3-8B-Q4_K_M.gguf` | ~4.7 GB | curl 从 ModelScope 下载 |
| `wheels/` | ~50 MB | Docker 容器内 `pip download`，Linux wheel |
| **总计** | **~6.1 GB** | |

**下载源可通过环境变量覆盖**（默认源不可达时使用）：

```bash
# 例：改用 HuggingFace 下载模型
MODEL_GGUF_URL="https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/qwen3-8b-q4_k_m.gguf" \
  bash deploy/prepare_offline.sh
```

### 传输到客户机

将整个 `galaxy-diag` 目录（含 `deploy/offline/`）传输到断网客户机：

```bash
# 方式一：scp（若有内网 SSH 通道）
scp -r galaxy-diag/ user@客户机:/home/user/galaxy-diag

# 方式二：打包后用 U 盘 / 移动硬盘
tar czf galaxy-diag.tar.gz --exclude='__pycache__' --exclude='venv' galaxy-diag/
# 拷贝到 U 盘，在客户机解压：tar xzf galaxy-diag.tar.gz
```

## 客户机部署步骤（在断网机器上操作）

假设客户机完全无网络，所有介质已通过 U 盘 / 内网传输到位。

### Step 1：传输项目到客户机

```bash
# 如果是 scp 传输
cd ~/galaxy-diag   # 项目已就位

# 如果是 U 盘 / 移动硬盘
tar xzf galaxy-diag.tar.gz
cd galaxy-diag
```

### Step 2：一键离线安装

```bash
# 一键安装 Ollama + 导入模型 + 创建 venv + 安装 Python 依赖
bash deploy/install_offline.sh
```

`install_offline.sh` 会自动完成四件事：
1. 检测并创建 Python 虚拟环境（`venv/`），若不存在则自动创建
2. 安装 Ollama（解压 `.tar.zst`，安装二进制 + 运行时库到 `/usr/local/lib/ollama/`），创建 systemd 服务并启动，绑定 `127.0.0.1:11434`
3. 从 `deploy/offline/*.gguf` 通过 `ollama create` 离线导入模型
4. 在 venv 内从 `deploy/offline/wheels/` 离线安装 Python 依赖

### Step 3：启动系统

```bash
source venv/bin/activate
galaxy-diag
```

> 启动时自动执行硬件资源预检（REQ-A-01 验收标准 6）：检测 CPU 核数、内存、磁盘、GPU 显存是否满足最低要求，不满足时打印差距表并**拒绝启动**。需要绕过时用 `galaxy-diag --skip-precheck`（调试用）。

预期输出：

```
Galaxy-Diag — 银河平台部署问题定位工具

📂 加载配置...
  推理服务: http://localhost:11434/v1
  模型: qwen3:8b

🔍 硬件资源预检
┌──────────┬──────────┬──────────┬──────┐
│ 项目     │ 最低要求 │ 实际     │ 状态 │
├──────────┼──────────┼──────────┼──────┤
│ CPU 核数 │ 4 核     │ 4 核     │ ✅   │
│ 内存     │ 8.0 GB   │ 14.2 GB  │ ✅   │
│ 磁盘     │ 10.0 GB  │ 35.6 GB  │ ✅   │
│ GPU 显存 │ 6.0 GB   │ 0.0 GB   │ ✅   │
└──────────┴──────────┴──────────┴──────┘
  GPU 显存: 未检测到 GPU，将以 CPU 模式运行（推理速度较慢）

  硬件预检通过

🔍 推理服务健康检查
  推理服务就绪，模型: qwen3:8b

✅ 系统就绪  模型: qwen3:8b  |  服务: http://localhost:11434/v1
```

### Step 4：重启验证

```bash
# 重启系统后确认无需重新导入模型（REQ-A-01 验收标准 3）
# Ollama 模型存储在 /var/lib/ollama（持久化目录），重启后自动加载
sudo reboot

# 重启后 Ollama 服务自动启动（如果已 enable）
sudo systemctl status ollama

# 再次运行
cd galaxy-diag
source venv/bin/activate
galaxy-diag
# 应正常启动，无需任何额外操作
```

## 模型版本更新替换

当需要更换模型（如从 qwen3:8b 升级到其他版本）时：

### 1. 准备新模型

```bash
# 在联网准备机上下载新模型的 GGUF 文件
# 修改 prepare_offline.sh 的环境变量，或直接 curl 下载：
# MODEL_GGUF_URL="https://modelscope.cn/.../qwen3-8b-q8_0.gguf" \
# MODEL_GGUF_NAME="qwen3-8b-q8_0.gguf" \
#   bash deploy/prepare_offline.sh
```

### 2. 传输到客户机

```bash
# 同初始部署方式：U 盘 / 内网传输 / scp
scp deploy/offline/qwen3-8b-q8_0.gguf user@客户机:~/galaxy-diag/deploy/offline/
```

### 3. 重建模型

```bash
# 在客户机上，从新的 GGUF 离线导入
ollama rm qwen3:8b   # 删除旧模型（可选，ollama create 会覆盖同名模型）

# 动态生成 Modelfile 并导入
echo "FROM /root/galaxy-diag/deploy/offline/qwen3-8b-q8_0.gguf" | \
  ollama create qwen3:8b -f -

# 验证
ollama list
```

### 4. 更新配置（如模型名变化）

```bash
# 如果新模型名称不同，修改 config.yaml
# llm.model: "qwen3:8b"  →  "新模型名"
# 或通过环境变量覆盖：
# export GALAXY_LLM_MODEL="新模型名"
```

## 离线验证清单

部署完成后，按以下步骤验证红线 1（零公网依赖）：

```bash
# 1. 确认 Ollama 仅监听本地
ss -tlnp | grep 11434
# 应显示 127.0.0.1:11434，而非 0.0.0.0:11434

# 2. 断开公网出站（仅保留内网）
sudo iptables -A OUTPUT -d <内网网段> -j ACCEPT
sudo iptables -A OUTPUT -j DROP

# 3. 重启系统
sudo reboot

# 4. 执行完整启动流程
cd galaxy-diag && source venv/bin/activate && galaxy-diag

# 5. 检查日志中无对外网域名的请求
journalctl -u ollama --since "1 hour ago" | grep -iE "(openai|huggingface|google|amazon)"
grep -riE "(openai\.com|huggingface\.co)" galaxy-diag/

# 6. 验证模型推理可用
curl http://127.0.0.1:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3:8b","messages":[{"role":"user","content":"hi"}],"max_tokens":10}'
```

## 故障排查

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| 硬件预检内存显示 0.0 GB | 非 Linux 环境 | 确认目标机器为 Linux，/proc/meminfo 可读 |
| Ollama 连接拒绝 | 服务未启动 | `systemctl start ollama` |
| Ollama 监听 0.0.0.0 | systemd 配置中 OLLAMA_HOST 未绑定 | 编辑 `/etc/systemd/system/ollama.service`，设为 `127.0.0.1:11434`，然后 `systemctl daemon-reload && systemctl restart ollama` |
| 模型未找到 | 未导入或名称不匹配 | `ollama list` 检查；名称需与 config.yaml 一致 |
| ollama create 报 llama-quantize not found | Ollama 安装不完整，缺运行时库 | 重新执行 `install_offline.sh`，确保 `lib/ollama/` 目录已安装到 `/usr/local/lib/ollama/` |
| pip 离线安装失败 | wheel 文件不匹配平台/Python 版本 | 必须用 Docker 容器下载 Linux wheel（`prepare_offline.sh` 已自动处理） |
| zstd 未安装 | 解压 .tar.zst 需要 | `apt-get install zstd`（需离线准备或预装） |
| python3-venv 未安装 | 创建虚拟环境需要 | `apt-get install python3-venv`（需离线准备或预装） |
| 推理超时 | 纯 CPU 环境推理慢 | 正常现象，可增大 config.yaml 中 timeout 值 |
| 推理时终端混入服务端日志（`[GIN]`、`slot print_timing:`、`srv server_strea:`） | llama-server/Ollama 与 galaxy-diag 共用同一终端，服务端 stderr 写入 TTY | galaxy-diag 无法控制已运行的服务进程；重启服务并重定向 stderr：`ollama serve >>/var/log/galaxy-diag/ollama.log 2>&1 &`（或 `2>/dev/null`）。非 systemd 部署的 `install_offline.sh` 已自动重定向；也可导出 `LLAMA_LOG_LEVEL=3`、`GIN_MODE=release`、`OLLAMA_LOG_LEVEL=ERROR` 后重启服务 |
| 切换到 llama-server 后日志仍很多 | 旧版 llama.cpp 不识别 `LLAMA_LOG_LEVEL` | 旧版用启动参数 `--log-disable`，或直接 `2>logfile` 重定向 stderr |
