# Galaxy-Diag — 银河平台部署问题定位工具

面向金山云银河平台私有化部署场景的**离线诊断-修复命令行工具**。将"运维手工翻日志 + 靠经验猜原因"的过程，变为"自动采集 + 结构化推理 + 可控修复建议"的闭环流程。

## 典型场景

- 客户利旧服务器混搭不同厂商硬件，部署后存储挂载反复偶发不稳，人工排查耗时数天
- 客户为敏感断网环境，运维只能手动翻日志定位，排查周期是联网环境的 3-5 倍
- 客户使用第三方 NAS 存储，容器网络配置与平台默认参数冲突，人工排查遗漏关键配置项

## 整体需求

工具的典型使用者是专业运维人员，通过 SSH 登录客户服务器（无图形界面），在断网环境下独立完成以下闭环：

```
环境识别 → 信息采集 → 根因分析 → 修复建议 → 人工确认 → 执行 → 验证
```

### 必做功能

| 编号 | 模块 | 描述 |
|------|------|------|
| REQ-A-01 | 模型离线部署与运行 | 小参量模型离线部署、启动前硬件预检、推理服务健康检查 |
| REQ-B-01 | 运行环境类型自动识别 | 自动识别裸金属、VM、容器三种环境，据此选择诊断路径 |
| REQ-B-02 | 异构软硬件信息采集 | 采集 CPU/内存/磁盘/RAID/网卡/第三方存储等信息，结构化输出 |
| REQ-C-01 | 问题诊断信息收集 | 支持主动采集 + 用户描述两种输入，形成结构化诊断上下文 |
| REQ-C-02 | 多环境根因分析 | 基于诊断信息与环境类型进行根因分析，覆盖裸金属/VM/容器 |
| REQ-C-03 | 诊断结论与不确定性声明 | 区分"已确认/推测/信息不足"，不编造确定性结论 |
| REQ-D-01 | 修复命令建议模板 | 输出带参数占位符的修复命令，用户可编辑参数与步骤 |
| REQ-D-02 | 修复脚本生成 | 生成含错误处理的多步骤修复脚本（Bash/Python） |
| REQ-D-03 | 生成代码多维错误检测 | 语法检查 + 危险操作检测 + 环境兼容性检测 |
| REQ-E-01 | 人工审核强制拦截 | 所有写操作执行前必须人工显式确认，不经 LLM 判定 |
| REQ-E-02 | 危险操作多维防护 | 危险命令清单拦截 + 安全校验 + 影响范围评估 |
| REQ-E-03 | 操作快照与一键回滚 | 执行前自动创建恢复快照，支持一键回滚 |
| REQ-E-04 | 操作留痕与审计日志 | 全操作可追溯，日志持久化、不可被 LLM 修改 |
| REQ-F-01 | CLI 交互能力 | 命令行交互界面，终端友好输出，支持 --help 和补全 |
| REQ-F-02 | 诊断-修复端到端工作流 | 完整流程编排，状态持久化，中断后可恢复 |
| REQ-F-03 | 审核确认交互流程 | 确认/拒绝/修改三种操作，危险操作额外确认步骤 |

### 选做功能

| 编号 | 描述 |
|------|------|
| REQ-X-02 | 客户知识库集成：导入客户特有故障案例，语义检索辅助诊断 |
| REQ-X-04 | Agent 推理过程可观测：记录完整推理链路，可查询回放 |

## 硬性红线约束

任一不满足视为课题未完成：

### 红线 1：运行阶段零公网依赖（离线可用）

- 运行环境无公网出站连接；内网 DNS、内网镜像仓库、内网文件服务器可用
- 部署阶段允许一次性离线导入（U 盘 / 移动硬盘 / 内网传输 / 内网 Docker Registry），运行阶段不得有任何公网依赖
- 核心功能（模型推理、环境识别、诊断分析、修复建议、安全审核）必须能在断网后正常执行
- **代码与配置中不得硬编码任何外网地址**（如 api.openai.com、huggingface.co）

### 红线 2：生产环境写操作必须人工显式确认

- 任何修改生产环境的操作执行前必须有人类显式确认，系统不得自动执行
- 确认必须通过专用交互流程完成，**不得由 LLM 解析用户自然语言判定"确认"**
- 用户拒绝时不执行且不反复要求确认

### 红线 3：必须可端到端演示

- 必须能用真实或模拟数据走通"问题报告 → 修复完成"的完整流程
- "能运行"不等于"能工作"——系统可启动但核心功能全部报错，同样不满足

## 技术约束

| 约束 | 内容 |
|------|------|
| 模型协议（8.1） | 必须通过兼容 OpenAI Chat Completions API 的接口调用模型；`base_url` 和 `model` 必须可配置；不得硬编码外网地址 |
| 交互约束（8.2） | CLI 为首选交互方式，全部必做功能必须可通过 CLI 完成；Web UI 不在必做范围 |
| 运行环境（8.3） | 目标运行环境为 Linux（x86_64）；必须支持无公网出站连接的环境；部署阶段允许一次性离线导入，运行阶段零公网依赖 |
| 开放选型（8.4） | 框架/模型/向量存储/编程语言均不限定，Python 为推荐 |

## 技术栈

| 组件 | 选型 | 说明 |
|------|------|------|
| 编程语言 | Python 3.10+ | 任务书推荐，Agent/CLI 生态丰富 |
| 推理服务 | Ollama | OpenAI 兼容 API，`ollama create` 支持离线导入 |
| 本地模型 | qwen3 系列（推荐 8B，默认配置 1.7b） | 纯 CPU 可推理，中文能力强；`config.yaml` 中 `llm.model` 可配置，默认 `qwen3:1.7b` 便于轻量环境验证，生产建议 `qwen3:8b`（Q4_K_M），落在任务书"3~9B 级"范围内 |
| LLM SDK | openai (Python) | 兼容 Ollama/vLLM/llama.cpp，换后端仅改 `base_url` |
| 向量计算 | numpy | RAG 知识库余弦相似度检索，纯 Python 离线可运行 |
| CLI 输出 | Rich | 终端表格、颜色、Panel |
| 命令补全 | argcomplete（可选） | bash/zsh/fish 补全，未安装时降级为静态补全 |
| 配置格式 | YAML | 可读性强，支持注释，运维人员友好 |

> 任务书实现指引曾建议 LangChain / Prompt Toolkit，本实现为保持离线轻量改用 openai SDK + argcomplete（功能等价、依赖更少），符合约束 8.4"框架不限定"。

## 目标环境

典型 KVM 虚拟化环境（按 `qwen3:8b` 推导）：

- **CPU**：4 核
- **内存**：16 GB
- **GPU**：无（CPU 模式推理）
- **系统**：Ubuntu 22.04 x86_64
- **网络**：无公网出站，内网可用

### 最低硬件配置

> 最低要求根据 `config.yaml` 中 `llm.model` 的参数量**自动推导**（见 `src/galaxy_diag/config/model_profile.py`）。下表为推荐模型 `qwen3:8b` 的配置；切换为更小模型（如默认 `qwen3:1.7b` → 2核/4GB）时要求相应降低。

| 资源 | qwen3:8b 最低要求 | 说明 |
|------|---------|------|
| CPU | 4 核 | 纯 CPU 推理基本要求 |
| 内存 | 8 GB | qwen3:8b Q4_K_M 运行时约 6 GB |
| 磁盘 | 10 GB | 模型文件 + 工具 + 日志 |
| GPU | 可选 | 有 GPU 时推理加速，无 GPU 以 CPU 模式运行 |

## 项目结构

```
galaxy-diag/
├── bin/galaxy-diag              # CLI 入口脚本（pyproject.toml 注册 galaxy-diag 命令）
├── src/galaxy_diag/             # 源码包（src layout）
│   ├── config/                  #   [配置] 配置加载与数据类
│   │   ├── settings.py          #     YAML 加载 → 环境变量覆盖 → 默认值
│   │   ├── defaults.py          #     配置数据类（LLMConfig, HardwareRequirement, KnowledgeConfig, AppConfig）
│   │   └── model_profile.py     #     按模型参数量自动推导硬件要求
│   ├── model/                   #   [A-01] 模型离线部署与推理
│   │   ├── client.py            #     ModelAdapter：统一 LLM 调用入口（OpenAI 兼容，含 embed）
│   │   ├── health.py            #     推理服务健康检查（服务可达→模型存在→推理可用）
│   │   ├── precheck.py          #     硬件资源预检（GPU/VRAM, CPU, RAM, Disk）
│   │   └── mock_client.py       #     MockModelAdapter（--mock 测试用，零网络）
│   ├── collector/               #   [B-01/B-02] 环境感知与异构硬件采集
│   │   ├── env_detect.py        #     裸金属/VM/容器识别 + 容器运行时检测
│   │   ├── hardware.py          #     CPU/内存/磁盘/RAID/网卡采集
│   │   └── storage.py           #     SAN/NAS/本地存储采集
│   ├── diagnoser/               #   [C-01/C-02/C-03] 诊断采集与根因分析
│   │   ├── context.py           #     诊断上下文构建（关键词→Tool 定向采集）
│   │   ├── tools.py             #     采集工具（组件状态/日志/资源/连通性）
│   │   ├── rules.py             #     规则匹配快路径 + 预匹配短路
│   │   ├── hallucination_guard.py  #  反幻觉事实校验（纯规则，零 LLM）
│   │   ├── prompts.py           #     诊断 Prompt 构建（防注入包裹）
│   │   ├── postprocess.py       #     LLM 输出解析与降级
│   │   └── agent.py             #     diagnose() 顶层入口
│   ├── fixer/                   #   [D-01/D-02/D-03] 修复建议生成
│   │   ├── template.py          #     占位符模板引擎（可编辑参数/删除/重排）
│   │   ├── generator.py         #     多步骤脚本生成（bash/python，set -euo pipefail）
│   │   ├── checker.py           #     D-03 多维错误检测（语法/危险/兼容/占位符）
│   │   ├── prompts.py           #     修复 Prompt 构建
│   │   ├── postprocess.py       #     修复输出解析与降级
│   │   └── agent.py             #     generate() 顶层入口
│   ├── safety/                  #   [E-01~E-04, F-03] 安全可控（全部不经 LLM）
│   │   ├── patterns.py          #     危险命令模式库（数据层）
│   │   ├── danger.py            #     E-02 执行前熔断（正则+变量展开+影响评估）
│   │   ├── review.py            #     E-01/F-03 审核确认判定
│   │   ├── snapshot.py          #     E-03 快照与回滚
│   │   ├── executor.py          #     受控执行（逐步执行，失败即停）
│   │   ├── verifier.py          #     结果验证
│   │   └── audit.py             #     E-04 审计日志（JSONL，不经 Agent 流）
│   ├── knowledge/               #   [X-02 选做] RAG 客户知识库
│   │   ├── types.py             #     KnowledgeCase/RetrievalResult
│   │   ├── store.py             #     向量存储（numpy 落盘）
│   │   ├── indexer.py           #     导入与索引（frontmatter 解析）
│   │   └── retriever.py         #     语义检索（余弦 top-k）
│   ├── trace/                   #   [X-04 选做] 推理可观测
│   │   └── recorder.py          #     TraceRecorder（JSONL 追加写入）
│   ├── shared/                  #   跨域契约层
│   │   ├── types.py             #     全部 dataclass/enum 数据契约
│   │   ├── errors.py            #     统一异常体系
│   │   └── constants.py         #     领域常量（组件名/日志路径/标签）
│   ├── workflow/                #   [F-01/F-02] CLI 与工作流编排
│   │   ├── states.py            #     10 态状态机 + 7 步用户视图映射
│   │   ├── persist.py           #     会话持久化与恢复
│   │   ├── engine.py            #     WorkflowEngine 主编排
│   │   └── cli/                 #     CLI 子包（app.py 入口 + cmd_*.py 各命令）
│   └── __main__.py              #   支持 python -m galaxy_diag
├── deploy/                      # 离线部署工具
│   ├── prepare_offline.sh       #   有网机器上下载离线介质
│   ├── install_offline.sh       #   断网机器上离线安装依赖
│   ├── Dockerfile               #   用于下载 Linux 平台 wheel 的容器
│   ├── Modelfile                #   对话模型定义（含推理参数，install 脚本自动复用）
│   └── offline/                 #   离线介质（不入库）
├── docs/                        # 设计文档
├── tests/                       # 测试（单元 + 集成）
├── config.yaml                  # 默认配置（零外网地址）
├── pyproject.toml               # 包定义与入口
├── requirements.txt             # Python 依赖
└── README.md
```

> 已按任务书依赖链完成全模块增量构建：模型(A) → 环境感知(B) → 诊断分析(C) → 修复生成(D) → 安全可控(E) → CLI 工作流(F)，并落地选做项 RAG 知识库(X-02) 与 Trace 可观测(X-04)。

## 部署

完整的离线部署流程见 [docs/deployment.md](docs/deployment.md)，包括：
- 有网机器上准备部署介质（代码 / 依赖 wheel / Ollama / 模型 GGUF）
- 客户机断网环境部署步骤
- 模型版本更新替换流程
- 红线 1 离线验证清单

快速开始（已有 Ollama + 模型的开发环境）：

```bash
pip install -r requirements.txt   # 联网环境
pip install -e .                  # 注册 galaxy-diag 命令

# 端到端诊断（7 步闭环）
galaxy-diag run -d "问题描述"           # 真实 LLM 推理
galaxy-diag run -d "问题描述" --mock    # Mock 模式，验证流程闭环（不需 Ollama）

# 单步命令
galaxy-diag env                        # 环境识别（裸金属/VM/容器 + 硬件）
galaxy-diag diagnose -d "问题描述"      # 仅诊断到根因，不进入修复
galaxy-diag snapshot list              # 查看快照
galaxy-diag audit-log                  # 查看审计日志
galaxy-diag kb import <file>           # 导入客户知识库案例
galaxy-diag completion bash            # 生成 shell 补全脚本

# 恢复中断的会话
galaxy-diag run --resume               # 列出可恢复会话
galaxy-diag run --resume <session_id>  # 恢复指定会话
```

> 全局选项：`--config <path>`、`--verbose`、`--no-color`、`--skip-precheck`、`--version`。`run`/`diagnose` 命令默认触发硬件预检，`--skip-precheck` 可跳过。

离线环境：

```bash
# 有网机器：bash deploy/prepare_offline.sh
# 传输 galaxy-diag 到断网机器后：
bash deploy/install_offline.sh
galaxy-diag run -d "问题描述"
```

## 设计原则

1. **先跑通再加深**：先把端到端流程跑通（哪怕每个环节都很基础），再逐项加深
2. **错误处理不能吞**：采集失败、模型调用失败、检测报错都明确提示，不静默忽略
3. **不要硬编码**：命令建议用占位符、外网地址不写死、模型路径不写死
4. **关键路径不经 LLM**：人工审核确认、审计日志写入、危险命令拦截的判定由硬编码逻辑完成，LLM 只能"建议"
5. **增量构建**：按任务书依赖链逐步引入组件，每步立即被业务逻辑验证，不搭空架子
