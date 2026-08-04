# Galaxy-Diag 部署文档

面向断网客户环境的完整离线部署流程。

## 目标环境

| 项目 | 要求 |
|------|------|
| 操作系统 | Ubuntu 22.04 x86_64（或其他 Linux x86_64） |
| CPU | 4 核及以上 |
| 内存 | 8 GB 及以上 |
| 磁盘 | 10 GB 及以上可用空间 |
| GPU | 可选（无 GPU 以 CPU 模式运行，推理速度较慢） |
| 网络 | 无公网出站要求；内网可用 |
| Python | 3.10 及以上 |

## 部署介质准备（在有网机器上操作）

在可访问互联网的机器上，准备以下文件，打包后通过 U 盘 / 移动硬盘 / 内网文件服务器传输到客户机。

### 1. 项目代码

```bash
# 打包项目目录（不含 __pycache__ 和虚拟环境）
tar czf galaxy-diag.tar.gz --exclude='__pycache__' --exclude='venv' galaxy-diag/
```

### 2. Python 依赖 wheel 文件

```bash
# 在有网机器上下载所有依赖为 wheel
cd galaxy-diag
bash deploy/download_wheels.sh
# 产物：deploy/wheels/ 目录
```

### 3. Ollama 安装包

```bash
# 下载 Ollama Linux 安装脚本
curl -fsSL https://ollama.com/install.sh -o ollama_install.sh
# 或下载 Ollama 二进制
curl -fsSL https://ollama.com/download/ollama-linux-amd64 -o ollama
```

### 4. 模型文件（GGUF）

```bash
# 方式一：从 HuggingFace / ModelScope 下载 Qwen3-8B Q4_K_M 量化版
# HuggingFace: https://huggingface.co/Qwen/Qwen3-8B-GGUF
# ModelScope:  https://modelscope.cn/models/Qwen/Qwen3-8B-GGUF
# 下载文件: qwen3-8b-q4_k_m.gguf（约 4.9 GB）

# 方式二：从已安装 Ollama 的机器上导出
# 联网机器上先拉取：
ollama pull qwen3:8b
# 导出模型 blob（需要找到对应的 sha256 文件）：
ollama show qwen3:8b --modelfile
```

### 5. 传输介质汇总

| 文件/目录 | 大小（约） | 用途 |
|-----------|-----------|------|
| galaxy-diag.tar.gz | < 1 MB | 项目代码 |
| deploy/wheels/ | ~50 MB | Python 依赖 |
| ollama_install.sh 或 ollama 二进制 | ~800 MB | Ollama 运行时 |
| qwen3-8b-q4_k_m.gguf | ~4.9 GB | 模型文件 |
| deploy/Modelfile | < 1 KB | Ollama 模型定义 |
| **总计** | **~5.8 GB** | |

## 客户机部署步骤（在断网机器上操作）

### Step 1：解压项目代码

```bash
tar xzf galaxy-diag.tar.gz
cd galaxy-diag
```

### Step 2：安装 Ollama

```bash
# 方式一：使用安装脚本（需要 bash 和 curl）
bash ollama_install.sh

# 方式二：手动安装二进制
sudo cp ollama /usr/local/bin/
sudo useradd -r -s /bin/false ollama
sudo systemctl enable ollama    # 需要自行创建 systemd unit 文件

# 启动 Ollama 服务
ollama serve &
# 或通过 systemd
sudo systemctl start ollama
```

### Step 3：导入模型

```bash
# 确保 GGUF 文件和 Modelfile 在同一目录
# GGUF 文件路径需与 Modelfile 中 FROM 指令一致
ollama create qwen3:8b -f deploy/Modelfile

# 验证模型已导入
ollama list
# 应显示：qwen3:8b
```

### Step 4：安装 Python 依赖（离线）

```bash
cd galaxy-diag

# 建议使用虚拟环境
python3 -m venv venv
source venv/bin/activate

# 从本地 wheel 离线安装
bash deploy/install_offline.sh

# 验证安装
python3 -c "import openai, httpx, yaml, rich; print('依赖安装成功')"
```

### Step 5：启动系统

```bash
python3 main.py
```

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

### Step 6：重启验证

```bash
# 重启系统后确认无需重新导入模型
sudo reboot

# 重启后 Ollama 服务自动启动（如果已 enable）
sudo systemctl status ollama

# 再次运行
cd galaxy-diag
source venv/bin/activate
python3 main.py
# 应正常启动，无需任何额外操作
```

## 模型版本更新替换

当需要更换模型（如从 qwen3:8b 升级到其他版本）时：

### 1. 准备新模型

```bash
# 在有网机器上下载新模型的 GGUF 文件
# 如：qwen3-8b-q8_0.gguf（更高质量但更大）
```

### 2. 传输到客户机

```bash
# 同初始部署方式：U 盘 / 内网传输
scp qwen3-8b-q8_0.gguf user@客户机:/tmp/
```

### 3. 更新 Modelfile

```bash
# 编辑 deploy/Modelfile，修改 FROM 路径
# FROM ./qwen3-8b-q4_k_m.gguf  →  FROM ./qwen3-8b-q8_0.gguf
```

### 4. 重建模型

```bash
# 删除旧模型（可选，不删也不会冲突，ollama create 会覆盖同名模型）
ollama rm qwen3:8b

# 创建新模型
ollama create qwen3:8b -f deploy/Modelfile

# 验证
ollama list
```

### 5. 更新配置（如模型名变化）

```bash
# 如果新模型名称不同，修改 config.yaml
# llm.model: "qwen3:8b"  →  "新模型名"
# 或通过环境变量覆盖：
# export GALAXY_LLM_MODEL="新模型名"
```

## 离线验证清单

部署完成后，按以下步骤验证红线 1（零公网依赖）：

```bash
# 1. 断开公网出站（仅保留内网）
sudo iptables -A OUTPUT -d <内网网段> -j ACCEPT
sudo iptables -A OUTPUT -j DROP

# 2. 重启系统
sudo reboot

# 3. 执行完整启动流程
cd galaxy-diag && source venv/bin/activate && python3 main.py

# 4. 检查日志中无对外网域名的请求
journalctl -u ollama --since "1 hour ago" | grep -iE "(openai|huggingface|google|amazon)"
grep -riE "(openai\.com|huggingface\.co)" galaxy-diag/

# 5. 验证模型推理可用
python3 -c "
from model.adapter import ModelAdapter
from config.schema import LLMConfig
a = ModelAdapter(LLMConfig())
print(a.chat([{'role':'user','content':'hi'}], max_tokens=10))
"
```

## 故障排查

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| 硬件预检内存显示 0.0 GB | 非 Linux 环境 | 确认目标机器为 Linux，/proc/meminfo 可读 |
| Ollama 连接拒绝 | 服务未启动 | `systemctl start ollama` 或 `ollama serve &` |
| 模型未找到 | 未导入或名称不匹配 | `ollama list` 检查；名称需与 config.yaml 一致 |
| pip 离线安装失败 | wheel 文件不匹配平台/Python 版本 | 在目标平台执行 `download_wheels.sh` |
| 推理超时 | 纯 CPU 环境推理慢 | 正常现象，可增大 config.yaml 中 timeout 值 |
