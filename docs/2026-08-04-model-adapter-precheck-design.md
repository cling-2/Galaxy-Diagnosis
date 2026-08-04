# 模型适配层与硬件预检设计（第一步）

- **日期**：2026-08-04
- **对应需求**：REQ-A-01（模型离线部署与运行）、技术约束 8.1（模型协议）
- **语言决策**：新建 Python CLI 项目（详见下文"语言决策与迁移策略"）
- **模型/服务选型**：qwen3:8b + Ollama（已完成本地服务化）
- **目标环境**：典型 KVM 虚拟化环境，4 核 / 16 GB 内存 / 无 GPU，Ubuntu 22.04 x86_64

## 1. 背景与目标

任务书要求构建一个可在断网客户环境运行的银河平台部署问题定位工具。第一步是 REQ-A-01：模型离线部署与运行。已选定 qwen3:8b，使用 Ollama 实现本地服务化。本步需完成两件事：

1. **封装统一的 Model Adapter**：确保 `base_url` 和 `model` 可通过配置文件指定，代码中零硬编码外网地址（红线 1 + 约束 8.1）。
2. **启动前硬件资源预检**（GPU 显存 / CPU 核数 / 内存）：不满足时给出明确错误提示并退出。

### 选型评估

- **qwen3:8b**：落在任务书"纯 CPU 环境建议 3~9B 级"范围内（8B 处于该区间上段）；中文能力强，匹配银河平台中文运维场景。Q4_K_M 量化后模型文件约 4.9 GB，运行时占用约 6 GB 内存，在 16 GB 内存环境下富余充足；无 GPU 下以纯 CPU 模式推理，4 核约 5-10 token/s，满足诊断分析场景（非实时对话，可容忍秒级响应）。
- **Ollama**：自带 OpenAI 兼容 API（`http://localhost:11434/v1`）；`ollama create` 可从本地 GGUF 离线导入（符合红线 1）；上手最简单，与任务书"实现指引"首推一致。

### 目标环境与最低配置对照

| 资源项 | qwen3:8b 实际需求 | 目标环境 | 最低配置（config 默认值） | 判定 |
|--------|------------------|---------|------------------------|------|
| 模型文件 | ~4.9 GB (Q4_K_M) | — | — | — |
| 运行时内存 | ~6 GB (加载 ×1.2) | 16 GB | 8 GB | ✅ 富余 |
| CPU 核数 | ≥4 核可流畅推理 | 4 核 | 4 核 | ✅ 满足 |
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

```
galaxy-diag/
├── config/
│   ├── __init__.py
│   ├── schema.py           # LLMConfig, HardwareRequirement, AppConfig 数据类
│   └── loader.py           # YAML 加载 → 环境变量覆盖 → 校验
├── model/
│   ├── __init__.py
│   ├── adapter.py          # ModelAdapter 统一调用入口
│   └── health.py           # Ollama 健康检查
├── precheck/
│   ├── __init__.py
│   └── hardware.py         # 硬件资源预检（GPU/VRAM, CPU, RAM, Disk）
├── config.yaml             # 默认配置（零外网地址，默认连本地 Ollama）
├── requirements.txt        # 最小依赖集
└── main.py                 # CLI 入口：预检 → 健康检查 → 启动
```

## 4. 配置设计

### 4.1 配置文件 `config.yaml`

```yaml
llm:
  base_url: "http://localhost:11434/v1"   # Ollama 默认，可改为 vLLM 等地址
  model: "qwen3:8b"
  api_key: "ollama"                        # Ollama 不需要真实 key，OpenAI SDK 要求非空
  timeout: 120
  max_retries: 3

hardware:
  min_cpu_cores: 4
  min_ram_gb: 8.0
  min_gpu_vram_gb: 6.0                     # GPU 存在时才检查
  min_disk_gb: 10.0
  gpu_required: false                      # GPU 可选
```

### 4.2 加载优先级

YAML 文件 → 环境变量覆盖（前缀 `GALAXY_`，如 `GALAXY_LLM_BASE_URL`）→ 代码默认值。

### 4.3 关键决策

- `api_key` 默认值 `"ollama"`：OpenAI SDK 要求非空，而 Ollama 不验证 key，部署时无需手动填 key。
- 环境变量前缀 `GALAXY_`：避免与其他工具的环境变量冲突。
- 无硬编码外网地址：完全符合红线 1 + 约束 8.1。

## 5. Model Adapter

### 5.1 核心类

`ModelAdapter` 是统一的 LLM 调用入口，所有模块通过此类与模型交互：

- `__init__(config: LLMConfig)`：基于配置创建 `openai.OpenAI` 客户端。
- `chat(messages, **kwargs) -> str`：同步调用，返回助手回复文本。
- `chat_stream(messages, **kwargs)`：流式调用，返回内容迭代器。
- `chat_with_tools(messages, tools, **kwargs) -> ChatResponse`：带工具调用的对话，返回封装的 `ChatResponse`（含 `content` + `tool_calls`）。

### 5.2 健康检查 `HealthChecker`

检查本地推理服务是否就绪、目标模型是否可用：

1. **服务可达性**：请求模型列表端点（同时尝试 Ollama 原生 `/api/tags` 和 OpenAI 兼容 `/v1/models`，适配不同后端）。
2. **模型存在性**：验证 `config.model` 是否在可用模型列表中；不存在则列出可用模型。
3. **推理可用性**：发送简单请求（"hi"，5 秒超时）验证模型能实际响应。

返回结构化 `HealthResult(ok: bool, message: str)`。

### 5.3 关键决策

| 决策 | 理由 |
|------|------|
| 使用 `openai` Python SDK 而非自研 HTTP 客户端 | Ollama 原生兼容 OpenAI API，SDK 内置重试/超时/流式；未来换 vLLM/llama.cpp 仅改 base_url |
| `chat` / `chat_stream` / `chat_with_tools` 三个方法 | 覆盖后续所有场景：诊断分析用 `chat`，CLI 交互用 `chat_stream`，Agent 工具调用用 `chat_with_tools` |
| 健康检查三步（服务可达 → 模型存在 → 推理可用） | REQ-A-01 验收标准要求"返回推理服务就绪状态"，只检查连通性不够 |
| `ChatResponse` 封装原始响应 | 后续 Agent 编排需同时拿到 `content` 和 `tool_calls`，统一封装避免散落 |
| 健康检查同时尝试 Ollama 原生 API 和 OpenAI 兼容 API | 部署时可能直接 `ollama serve` 或走 `/v1` 代理，两种都兼容 |

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

`main.py` 流程：加载配置 → 硬件预检 → 模型健康检查 → 系统就绪 →（占位）进入 CLI 交互。

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
| `detect_env_type()` 占位 | 环境识别是 REQ-B-01 内容，此处只留接口 |
| main.py 暂不引入 CLI 框架 | 当前只有启动流程，REQ-F 时再引入 argparse/click |

## 8. 错误处理与依赖管理

### 8.1 错误处理原则

直接对齐任务书第 325 行"错误处理不能吞"。定义统一错误基类 `GalaxyDiagError(message, hint)`，所有业务错误继承并附带可操作 `hint`。

子类：`ConfigError`、`PrecheckError`、`ModelUnavailableError`、`ModelCallError`。

| 模块 | 失败场景 | 处理方式 | hint 示例 |
|------|---------|---------|----------|
| config/loader | YAML 语法错误 | 抛 ConfigError 退出 | "请检查 config.yaml 格式" |
| config/loader | 缺少必填字段 | 抛 ConfigError，提示字段名 | "缺少 llm.base_url 配置" |
| precheck/hardware | CPU/内存不足 | 打印差距表，exit(1) | "请升级至 4 核 CPU / 8 GB 内存" |
| model/health | 服务不可达 | 打印连接信息，exit(1) | "请确认 Ollama 已启动: systemctl status ollama" |
| model/health | 模型不存在 | 列出可用模型，exit(1) | "请先导入模型: ollama create qwen3:8b -f Modelfile" |
| model/adapter | 推理超时 | 抛 ModelCallError | "推理超时(120s)，纯 CPU 环境建议使用更小模型" |
| model/adapter | 429 限频 | OpenAI SDK 内置重试，耗尽后抛错 | "模型服务限频，请稍后重试" |

规则：不静默忽略任何错误；每个错误附带可操作 hint；预检/健康检查阶段阻断（exit），运行阶段可恢复（抛异常由上层处理）。

### 8.2 依赖管理

```txt
# requirements.txt — 最小依赖集
openai>=1.30.0          # OpenAI 兼容 SDK（支持 Ollama/vLLM/llama.cpp）
httpx>=0.27.0           # 健康检查 HTTP 请求（openai 已依赖，显式声明）
pyyaml>=6.0             # 配置文件解析
rich>=13.0.0            # CLI 终端美化输出
```

刻意不引入：`psutil`（标准库替代）、`langchain`/`llamaindex`（后续按需）、`click`/`typer`（REQ-F 再引入）、`textual`（后续按需）。

离线打包：所有依赖可通过 `pip download` 预下载为 wheel，U盘/内网传输后 `pip install --no-index --find-links` 离线安装；直接依赖仅 4 个，打包体积小。

## 9. 测试策略

- **配置加载**：YAML 解析、环境变量覆盖、默认值回退、缺字段报错。
- **ModelAdapter**：使用 mock OpenAI client 验证 `chat`/`chat_stream`/`chat_with_tools`；真实 Ollama 健康检查作为集成测试（需本地 Ollama 运行）。
- **硬件预检**：mock `/proc/meminfo`、`nvidia-smi` 输出，验证通过/不通过/无 GPU 三种情况下的判定与提示。
- **main 启动流程**：mock 预检与健康检查结果，验证失败时 exit(1)、成功时打印就绪信息。

测试框架：pytest。

## 10. 验收对照

| REQ-A-01 验收标准 | 本设计对应 |
|------------------|-----------|
| 无公网出站环境能完成部署并启动推理服务 | 配置默认连本地 Ollama，零硬编码外网地址（红线 1） |
| 部署完成后执行一次健康检查，返回就绪状态 | `HealthChecker.check()` 三步检查 |
| 部署不需要编辑配置文件；重启无需重新导入模型 | `config.yaml` 含合理默认值，Ollama 模型持久化 |
| 文档明确描述模型文件离线导入流程 | 见后续部署文档（本步范围外，将独立文档化） |
| 文档明确列出最低硬件配置 | `HardwareRequirement` 默认值即最低配置 |
| 启动前检测硬件，不满足拒绝启动 | `HardwarePrechecker` + `sys.exit(1)` |

| 技术约束 8.1 | 本设计对应 |
|------------|-----------|
| 通过兼容 OpenAI Chat Completions API 调用 | `openai` SDK + Ollama `/v1` |
| base_url 可配置 | `LLMConfig.base_url` + YAML + 环境变量 |
| model 字段可配置 | `LLMConfig.model` + YAML + 环境变量 |
| 不得硬编码外网地址 | 配置文件驱动，默认值为本地地址 |
