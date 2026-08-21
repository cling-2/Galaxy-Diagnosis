# 模型适配与硬件预检（REQ-A-01）

- **日期**：2026-08-04
- **对应需求**：REQ-A-01（模型离线部署与运行）、技术约束 8.1（模型协议）
- **语言决策**：新建 Python CLI 项目（详见下文"语言决策与迁移策略"）
- **模型/服务选型**：qwen3 系列 + Ollama（`config.yaml` 默认 `qwen3:1.7b`，生产建议 `qwen3:8b`；已完成本地服务化）
- **目标环境**：典型 KVM 虚拟化环境，4 核 / 16 GB 内存 / 无 GPU，Ubuntu 22.04 x86_64

## 1. 背景与目标

任务书要求构建一个可在断网客户环境运行的银河平台部署问题定位工具。第一步是 REQ-A-01：模型离线部署与运行。已选定 qwen3:8b，使用 Ollama 实现本地服务化。本步需完成两件事：

1. **封装统一的 Model Adapter**：确保 `base_url` 和 `model` 可通过配置文件指定，代码中零硬编码外网地址（红线 1 + 约束 8.1）。
2. **启动前硬件资源预检**（GPU 显存 / CPU 核数 / 内存）：不满足时给出明确错误提示并退出。

### 选型评估

- **qwen3:8b**：落在任务书"纯 CPU 环境建议 3~9B 级"范围内（8B 处于该区间上段）；中文能力强，匹配银河平台中文运维场景。Q4_K_M 量化后模型文件约 4.9 GB，运行时占用约 6 GB 内存，在 16 GB 内存环境下富余充足；无 GPU 下以纯 CPU 模式推理，4 核约 5-10 token/s，满足诊断分析场景（非实时对话，可容忍秒级响应）。
- **Ollama**：自带 OpenAI 兼容 API（`http://localhost:11434/v1`）；`ollama create` 可从本地 GGUF 离线导入（符合红线 1）；上手最简单，与任务书"实现指引"首推一致。

### 目标环境与最低配置对照

> 实际最低要求根据 `config.yaml` 中 `llm.model` 的参数量自动推导（`config/model_profile.py`），下表为 `qwen3:8b` 的配置。

| 资源项 | qwen3:8b 实际需求 | 目标环境 | qwen3:8b 最低配置（推导值） | 判定 |
|--------|------------------|---------|------------------------|------|
| 模型文件 | ~4.9 GB (Q4_K_M) | — | — | — |
| 运行时内存 | ~6 GB (加载 ×1.2) | 16 GB | 7.4 GB | ✅ 富余 |
| CPU 核数 | ≥4 核可流畅推理 | 4 核 | 8 核（推导值，4核可跑但慢） | ✅ 可用 |
| GPU | 不需要 | 无 | 可选 | ✅ CPU 模式 |

## 2. 语言决策与迁移策略

现有项目为 Go（Eino 框架 + Gin Web UI），但存在形态不匹配：任务书明确要求 CLI 优先（REQ-F-01）、目标运行环境 Linux x86_64、Web UI 不在必做范围。经对比，决定**新建 Python CLI 项目**。

**迁移策略：增量构建（非等价平移）**

- 保留现有 Go 项目的**设计思想**作为架构蓝图：Supervisor + Specialist 模式、HITL 独立于 LLM（关键路径不经 LLM）、配置优先级链、上下文管理与会话恢复。
- **不保留** Web 相关代码（Gin / session / ACL / 前端）及等价的包结构。
- 按任务书依赖链增量构建，每步只引入所需组件并立即被业务逻辑验证（能跑通、能测试），而非空架子。

| 任务书步骤 | 引入的框架组件 | 参考的 Go 设计 |
|-----------|--------------|--------------|
| REQ-A-01（本步）| ModelAdapter + 配置加载 | LLMConfig 配置优先级链 |
| REQ-B（环境感知）| 采集工具注册机制 | toolreg 注册中心思路 |
| REQ-C（诊断）| Agent 编排 | Supervisor + Specialist 模式 |
| REQ-D（修复）| 脚本生成 + 校验 | — |
| REQ-E（安全）| HITL 审批 + 审计日志 | hitl 包"关键路径不经 LLM" |
| REQ-F（CLI 工作流）| 状态机编排 | 上下文管理 + 会话恢复 |

## 3. 项目结构

> 以下为 A-01 步骤引入的文件，后续步骤增量扩展为完整项目（详见 `galaxy-diag-architecture-design.md` §3）。

```
galaxy-diag/
├── src/galaxy_diag/
│   ├── config/
│   │   ├── __init__.py
│   │   ├── defaults.py       # LLMConfig, HardwareRequirement, KnowledgeConfig, AppConfig
│   │   ├── settings.py       # YAML 加载 → 环境变量覆盖 → 默认值
│   │   └── model_profile.py  # 按模型参数量自动推导硬件要求
│   ├── model/
│   │   ├── __init__.py
│   │   ├── client.py         # ModelAdapter：OpenAI 兼容 API 客户端（chat + embed）
│   │   ├── health.py         # 推理服务健康检查（服务可达→模型存在→推理可用）
│   │   ├── precheck.py       # 硬件资源预检（CPU/GPU/MEM/DISK，零网络零 LLM）
│   │   └── mock_client.py    # MockModelAdapter（--mock 测试用）
│   ├── shared/
│   │   ├── types.py          # 跨域数据契约
│   │   ├── constants.py      # 领域知识常量
│   │   └── errors.py         # 统一异常体系（GalaxyDiagError + 子类）
│   └── __main__.py           # CLI 入口（python -m galaxy_diag）
├── deploy/
│   ├── prepare_offline.sh    # 联网准备机：下载 Ollama + 模型 GGUF + Python wheels
│   ├── install_offline.sh    # 断网客户机：一键安装 Ollama + 模型 + 依赖
│   └── Modelfile             # 对话模型 Ollama 定义（含推理参数，install 脚本自动复用）
├── tests/
├── config.yaml               # 默认配置（零外网地址，model=qwen3:1.7b）
├── requirements.txt          # 依赖集（openai/httpx/pyyaml/rich/numpy）
└── pyproject.toml            # 包定义与 CLI 入口注册
```

## 4. 配置设计

### 4.1 配置文件 `config.yaml`

```yaml
llm:
  base_url: "http://localhost:11434/v1"   # Ollama 默认，可改为 vLLM 等地址
  model: "qwen3:1.7b"                     # 默认轻量模型；生产建议 qwen3:8b
  api_key: "ollama"                        # Ollama 不需要真实 key，OpenAI SDK 要求非空
  timeout: 600                             # 纯 CPU 推理 8B 需 3-5 分钟
  max_retries: 3
  max_tokens: 1024                         # 最大输出 token，防止无限生成
  embed_model: "bge:large"                 # RAG embedding 模型；空字符串=禁用 RAG

# hardware 段默认根据 llm.model 参数量自动推导；显式字段优先于推导值
# hardware:
#   min_cpu_cores: 4
#   min_ram_gb: 8.0
#   min_gpu_vram_gb: 6.0
#   min_disk_gb: 10.0
#   gpu_required: false

knowledge:                                 # RAG 检索配置（REQ-X-02）
  top_k: 3
  min_similarity: 0.0
```

### 4.2 加载优先级

config.yaml 文件 → 环境变量覆盖（前缀 `GALAXY_`，如 `GALAXY_LLM_BASE_URL`）→ 数据类默认值。

硬件要求额外规则：显式 `hardware:` 段字段优先于按 `llm.model` 参数量自动推导的值，未配置字段回退到推导值。

### 4.3 关键决策

- `api_key` 默认值 `"ollama"`：OpenAI SDK 要求非空，而 Ollama 不验证 key，部署时无需手动填 key。
- 环境变量前缀 `GALAXY_`：避免与其他工具的环境变量冲突。
- 无硬编码外网地址：完全符合红线 1 + 约束 8.1。
- Ollama 绑定 `127.0.0.1:11434`：仅本地访问，防止局域网内其他机器误调用推理服务。

## 5. Model Adapter

### 5.1 核心类

`ModelAdapter` 是统一的 LLM 调用入口，所有模块通过此类与模型交互：

- `__init__(config: LLMConfig)`：基于配置创建 `openai.OpenAI` 客户端。
- `chat(messages, tools=None, timeout=None, max_tokens=None) -> str | None`：同步对话，返回助手回复文本（含 tool_calls 时通过 `ToolCall` dataclass 返回）；失败抛 `ModelCallError`。
- `embed(texts, model=None) -> list[list[float]]`：批量文本向量化，供 RAG 检索使用。

另有 `MockModelAdapter`（`model/mock_client.py`）提供零网络实现，`--mock` 模式下替代真实 adapter，使全链路可离线确定性测试。

### 5.2 健康检查 `HealthChecker`

检查本地推理服务是否就绪、目标模型是否可用：

1. **服务可达性**：请求模型列表端点（同时尝试 Ollama 原生 `/api/tags` 和 OpenAI 兼容 `/v1/models`，适配不同后端）。
2. **模型存在性**：验证 `config.model` 是否在可用模型列表中；不存在则列出可用模型。
3. **推理可用性**：发送简单请求（"hi"，5 秒超时）验证模型能实际响应。

返回结构化 `HealthResult(ok: bool, message: str)`。

### 5.3 关键决策

| 决策 | 理由 |
|------|------|
| 使用 `openai` Python SDK 而非自研 HTTP 客户端 | Ollama 原生兼容 OpenAI API，SDK 内置重试/超时；未来换 vLLM/llama.cpp 仅改 base_url |
| `chat` + `embed` 两个核心方法 | `chat` 覆盖诊断/修复所有 LLM 对话场景；`embed` 支持 RAG 知识库向量检索 |
| 健康检查三步（服务可达 → 模型存在 → 推理可用） | REQ-A-01 验收标准要求"返回推理服务就绪状态"，只检查连通性不够 |
| 健康检查同时尝试 Ollama 原生 API 和 OpenAI 兼容 API | 部署时可能直接 `ollama serve` 或走 `/v1` 代理，两种都兼容 |
| MockModelAdapter 零网络实现 | `--mock` 模式下全链路可离线确定性测试，开发/演示不依赖 Ollama |

## 6. 硬件资源预检

### 6.1 数据结构

- `CheckItem(name, required, actual, unit, passed)`：单项检测结果。
- `PrecheckResult(passed, items, summary)`：预检汇总。
- `HardwarePrechecker`：执行预检。

### 6.2 检测项实现

| 检测项 | Linux 实现方式 | 说明 |
|--------|---------------|------|
| CPU 核数 | `os.cpu_count()` 或 `/proc/cpuinfo` | 标准 Python API |
| 内存 | `/proc/meminfo` 读取 MemAvailable | 比 `psutil` 更无依赖 |
| 磁盘 | `shutil.disk_usage("/")` | 标准库 |
| GPU 显存 | `nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits` | 仅 NVIDIA GPU；无 GPU 时返回 None |

### 6.3 GPU 检测策略（GPU 可选）

- 有 GPU：检查显存是否满足 `min_gpu_vram_gb`，不满足则失败。
- 无 GPU 且 `gpu_required=false`：不阻断，但明确提示"未检测到 GPU，将以 CPU 模式运行（推理速度较慢）"。
- 判定：非 GPU 项全过 +（GPU 不存在时不算失败）即为通过。

### 6.4 关键决策

| 决策 | 理由 |
|------|------|
| 优先用标准库（`os`/`shutil`/`/proc`）而非 `psutil` | 减少离线环境依赖，符合"部署过程不需要手动编译依赖" |
| GPU 检测仅支持 NVIDIA（`nvidia-smi`） | 银河平台目标 x86_64 Linux 服务器，NVIDIA 是主流 |
| 无 GPU 时不阻断仅警告 | 任务书明确"纯 CPU 环境建议 3~9B 级"，允许无 GPU |
| 预检结果结构化为 `PrecheckResult` | 后续可写入审计日志、供诊断模块读取 |

### 6.5 不满足时输出示例

```
❌ 硬件资源预检未通过

  CPU 核数:   需要 4 核, 实际 2 核  ✗ (差 2 核)
  内存:       需要 8.0 GB, 实际 3.8 GB  ✗ (差 4.2 GB)
  磁盘:      需要 10.0 GB, 实际 50.0 GB  ✓
  GPU 显存:   需要 6.0 GB, 实际 8.0 GB  ✓

  请升级硬件后重试。参考最低配置：
  - CPU: 4 核及以上
  - 内存: 8 GB 及以上
  - 磁盘: 10 GB 及以上
  - GPU: 6 GB 显存及以上（可选，无 GPU 将以 CPU 模式运行）
```

## 7. CLI 入口与启动流程

`__main__.py` 委托 `workflow/cli/app.py:main()`，流程：加载配置 → 硬件预检 → 模型健康检查 → 命令分发。

> A-01 步骤仅建立入口骨架（预检 + 健康检查 + 命令占位），CLI 框架（argparse 子命令注册 + argcomplete 补全）在 REQ-F-01 完整引入。

```
┌─────────────┐     失败     ┌──────────┐
│ 加载配置     │────────────→│ 退出(1)  │
└──────┬──────┘              └──────────┘
       │ 通过
┌──────▼──────┐     失败     ┌──────────┐
│ 硬件预检     │────────────→│ 退出(1)  │
└──────┬──────┘              └──────────┘
       │ 通过
┌──────▼──────┐     失败     ┌──────────┐
│ 模型健康检查  │────────────→│ 退出(1)  │
└──────┬──────┘              └──────────┘
       │ 通过
┌──────▼──────┐
│ 系统就绪     │
└─────────────┘
```

### 关键决策

| 决策 | 理由 |
|------|------|
| 预检失败立即 `sys.exit(1)` | REQ-A-01："不满足时给出明确提示并拒绝启动"；不继续、不降级、不静默 |
| 健康检查放在预检之后 | 先确认硬件够，再尝试连接服务，避免资源不足时浪费等待时间 |
| 用 Rich 输出报告 | REQ-F-01 要求"输出格式对终端友好"，Rich 表格 + 颜色一步到位 |
| CLI 框架（argparse）在 F-01 引入 | A-01 阶段先建入口骨架，子命令树随 F-01 落地 |

## 8. 错误处理与依赖管理

### 8.1 错误处理原则

直接对齐任务书"错误处理不能吞"。定义统一错误基类 `GalaxyDiagError(message, hint)`，所有业务错误继承并附带可操作 `hint`。

子类（按模块）：`ConfigError`、`PrecheckError`、`ModelUnavailableError`、`ModelCallError`、`CollectorError`（及 `CollectorPermissionError` / `CollectorPartialError` / `CollectorToolNotFoundError`）、`DiagnoseError`、`FixerError`、`SafetyError`、`WorkflowError`。

| 模块 | 失败场景 | 处理方式 | hint 示例 |
|------|---------|---------|----------|
| config/loader | YAML 语法错误 | 抛 ConfigError 退出 | "请检查 config.yaml 格式" |
| config/loader | 缺少必填字段 | 抛 ConfigError，提示字段名 | "缺少 llm.base_url 配置" |
| precheck/hardware | CPU/内存不足 | 打印差距表，exit(1) | "请升级至 4 核 CPU / 8 GB 内存" |
| model/health | 服务不可达 | 打印连接信息，exit(1) | "请确认 Ollama 已启动: systemctl status ollama" |
| model/health | 模型不存在 | 列出可用模型，exit(1) | "请先导入模型（install_offline.sh 会自动 ollama create）" |
| model/adapter | 推理超时 | 抛 ModelCallError | "推理超时(120s)，纯 CPU 环境建议使用更小模型" |
| model/adapter | 429 限频 | OpenAI SDK 内置重试，耗尽后抛错 | "模型服务限频，请稍后重试" |

规则：不静默忽略任何错误；每个错误附带可操作 hint；预检/健康检查阶段阻断（exit），运行阶段可恢复（抛异常由上层处理）。

### 8.2 依赖管理

```txt
# requirements.txt — 核心依赖
openai>=1.30.0          # OpenAI 兼容 SDK（支持 Ollama/vLLM/llama.cpp）
httpx>=0.27.0           # 健康检查 HTTP 请求（openai 已依赖，显式声明）
pyyaml>=6.0             # 配置文件解析
rich>=13.0.0            # CLI 终端美化输出
numpy>=1.26.0           # RAG 知识库向量计算（余弦相似度）
```

可选依赖：`argcomplete>=3.0.0`（Shell 补全，未安装时降级为静态补全）。

刻意不引入：`psutil`（标准库替代）、`langchain`/`llamaindex`（直接使用 openai SDK 足够）、`click`/`typer`（argparse 标准库足够）、`faiss-cpu`/`chromadb`（numpy 足够，规模增长时再替换）。

离线打包：所有依赖可通过 `pip download` 预下载为 wheel，U盘/内网传输后 `pip install --no-index --find-links` 离线安装；直接依赖仅 4 个，打包体积小。

## 9. 测试策略

- **配置加载**：YAML 解析、环境变量覆盖、默认值回退、缺字段报错。
- **ModelAdapter**：使用 mock OpenAI client 验证 `chat`/`chat_stream`/`chat_with_tools`；真实 Ollama 健康检查作为集成测试（需本地 Ollama 运行）。
- **硬件预检**：mock `/proc/meminfo`、`nvidia-smi` 输出，验证通过/不通过/无 GPU 三种情况下的判定与提示。
- **main 启动流程**：mock 预检与健康检查结果，验证失败时 exit(1)、成功时打印就绪信息。

测试框架：pytest。

## 9.5 离线部署机制

**核心原则**：下载机和安装机是两台不同的机器。断网客户机假设完全无网络，所有介质在联网准备机下载后传输。
**部署步骤**：见[部署文档](deployment.md)

### 两阶段脚本

| 脚本 | 执行位置 | 作用 |
|------|---------|------|
| `deploy/prepare_offline.sh` | 联网准备机 | 一键下载上述三样介质到 `deploy/offline/` |
| `deploy/install_offline.sh` | 断网客户机 | 一键安装 Ollama + 导入模型 + 装 Python 依赖 |

### 关键决策

| 决策 | 理由 |
|------|------|
| Ollama 安装包：准备机下载 `.tar.zst` 后重打包为 `.tar.gz` | Ollama 官方只发 `.tar.zst`，需 zstd 解压；断网客户机未必预装 zstd，是部署隐患。准备机有网可装 zstd，重打包为 `.tar.gz` 后客户机只需系统自带 `tar` 即可解压 |
| Ollama 安装包包含 `lib/ollama/` 运行时库 | 裸二进制缺 `llama-quantize` 会导致 `ollama create` 失败，压缩包含完整库 |
| Python wheel 用 Docker 容器下载 | Windows 直接 `pip download` 得到 `win_amd64` wheel 装不进 Linux；Docker 容器确保 Linux 平台匹配；下载前清空 wheels 目录避免重复版本累积 |
| 模型用 GGUF + `ollama create` 离线导入 | 符合断网假设，不依赖 `ollama pull` 联网 |
| 对话模型使用 `deploy/Modelfile` | 含 temperature/stop 等推理参数，`install_offline.sh` 自动复用并替换 FROM 路径；Embedding 模型（bge-*）仅 FROM 注册 |
| Ollama 绑定 `127.0.0.1` | 防止局域网内其他机器误调用推理服务 |
| install 脚本自动创建 venv | 避免依赖污染系统 Python，符合工程规范 |
| 客户机解压仅需 `tar` | `.tar.zst` 旧介质仍兼容（需 zstd），但 `.tar.gz` 为推荐格式，消除 zstd 隐式依赖 |

## 10. 验收对照

| REQ-A-01 验收标准 | 本设计对应 |
|------------------|-----------|
| 无公网出站环境能完成部署并启动推理服务 | 配置默认连本地 Ollama，零硬编码外网地址（红线 1）；`prepare_offline.sh` + `install_offline.sh` 全程离线 |
| 部署完成后执行一次健康检查，返回就绪状态 | `HealthChecker.check()` 三步检查（服务可达→模型存在→推理可用） |
| 部署不需要编辑配置文件；重启无需重新导入模型 | `config.yaml` 含合理默认值，Ollama 模型持久化存储，systemd 开机自启 |
| 文档明确描述模型文件离线导入流程 | `docs/deployment.md` + `deploy/Modelfile`，GGUF + `ollama create` 流程 |
| 文档明确列出最低硬件配置 | `HardwareRequirement` 默认值即最低配置 |
| 启动前检测硬件，不满足拒绝启动 | `HardwarePrechecker` + `sys.exit(1)` |
| 断网后预检能正确判断资源 | `precheck/hardware.py` 只读本地 `/proc`、`shutil`、`nvidia-smi`，不依赖网络 |
| curl 推理接口返回正常响应 | Ollama `/v1/chat/completions` 兼容 OpenAI API |
| Ollama 绑定 127.0.0.1 | systemd 配置 `OLLAMA_HOST=127.0.0.1:11434` |

| 技术约束 8.1 | 本设计对应 |
|------------|-----------|
| 通过兼容 OpenAI Chat Completions API 调用 | `openai` SDK + Ollama `/v1` |
| base_url 可配置 | `LLMConfig.base_url` + YAML + 环境变量 |
| model 字段可配置 | `LLMConfig.model` + YAML + 环境变量 |
| 不得硬编码外网地址 | 配置文件驱动，默认值为本地地址 |
