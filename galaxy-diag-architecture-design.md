# Galaxy-Diag 项目架构设计

> 银河平台部署问题定位工具 — 从零设计的目录结构与核心架构

## 1. 项目概述

构建一个面向银河平台部署场景、可在断网客户环境运行的 CLI 诊断修复工具。核心闭环：

**环境识别 → 信息采集 → 根因分析 → 修复建议 → 人工确认 → 执行 → 验证**

技术栈：Python 3.10+ + openai SDK + Ollama（OpenAI 兼容 API）+ Rich（终端输出）+ numpy（RAG 向量计算）

> 任务书实现指引曾建议 LangChain，本实现为保持离线轻量改用 openai SDK 直接调用 OpenAI 兼容 API（功能等价、依赖更少），符合约束 8.4"框架不限定"。

## 2. 核心模块矩阵

| # | 模块域 | 包名 | 对应需求 | 核心职责 |
|---|--------|------|---------|---------|
| 1 | 模型/推理服务 | `model/` | A-01 | 离线部署小参量模型、健康检查、资源预检、OpenAI 兼容 API 客户端封装 |
| 2 | 环境感知与采集 | `collector/` | B-01/B-02 | 识别裸金属/VM/容器；采集异构硬件与第三方存储信息（只读、结构化输出） |
| 3 | 诊断分析 | `diagnoser/` | C-01/C-02/C-03 | 信息收集编排、根因分析、不确定性声明；调用采集 Tool + LLM 推理 |
| 4 | 修复生成 | `fixer/` | D-01/D-02/D-03 | 命令模板（带占位符）、多步骤脚本生成、语法/危险/兼容性多维检测 |
| 5 | 安全可控 | `safety/` | E-01~E-04 | 人工确认专用通道、危险操作防护、快照回滚、审计日志（不经 LLM） |
| 6 | 工作流编排与 CLI | `workflow/` | F-01/F-02/F-03 | 端到端状态机编排、CLI/TUI、审核确认交互、状态持久化 |
| 7 | 知识库与可观测（选做） | `knowledge/` + `trace/` | X-02/X-04 | 客户案例库导入与语义检索、推理链路 trace 持久化 |

贯穿性关注点：
- **配置与硬件推导**（`config/`）：base_url / model / embed_model / 路径不可硬编码；硬件要求按模型参数量自动推导
- **领域知识资产**（`shared/constants.py`）：银河平台组件清单、关键日志路径、各类标签映射
- **持久化约定**：统一 `~/.galaxy-diag/` 下 sessions / audit / snapshots / knowledge_base / traces，可经环境变量覆盖

## 3. 推荐目录结构

```
galaxy-diag/
├── bin/                          # 入口脚本
│   └── galaxy-diag               # CLI 启动点（pyproject.toml 注册命令）
├── src/
│   └── galaxy_diag/
│       ├── __init__.py
│       ├── __main__.py           # 支持 python -m galaxy_diag
│       ├── config/               # 全局配置 & 硬件要求推导
│       │   ├── __init__.py
│       │   ├── settings.py       # YAML 加载 → 环境变量覆盖 → 默认值
│       │   ├── defaults.py       # 配置数据类（LLMConfig, HardwareRequirement, KnowledgeConfig, AppConfig）
│       │   └── model_profile.py  # 按模型参数量自动推导硬件要求
│       ├── model/                # [A-01] 模型离线部署与推理
│       │   ├── __init__.py
│       │   ├── client.py         # ModelAdapter：OpenAI 兼容 API 客户端（所有 LLM/embed 调用唯一出口）
│       │   ├── health.py         # 推理服务健康检查（服务可达→模型存在→推理可用）
│       │   ├── precheck.py       # 硬件资源预检（CPU/GPU/MEM/DISK，零网络零 LLM）
│       │   └── mock_client.py    # MockModelAdapter（--mock 测试用，零网络）
│       ├── collector/            # [B-01/B-02] 环境感知与信息采集
│       │   ├── __init__.py       #   collect_env() 编排入口
│       │   ├── env_detect.py     # 裸金属/VM/容器自动识别 + 容器运行时检测
│       │   ├── hardware.py       # CPU/内存/磁盘/RAID/网卡采集
│       │   └── storage.py        # 第三方存储（SAN/NAS/本地）采集
│       ├── diagnoser/            # [C-01/C-02/C-03] 诊断采集与根因分析
│       │   ├── __init__.py       #   导出 build_diagnostic_context + diagnose
│       │   ├── context.py        # 诊断上下文构建（关键词→Tool 定向采集 + 用户补充）
│       │   ├── tools.py          # 采集工具（组件状态/日志/资源/连通性）
│       │   ├── rules.py          # 规则匹配快路径 + 预匹配短路
│       │   ├── hallucination_guard.py  # 反幻觉事实校验（纯规则，零 LLM）
│       │   ├── prompts.py        # 诊断 Prompt 模板（含防注入包裹 + 不确定性约束）
│       │   ├── postprocess.py    # LLM 输出解析与降级（format_fallback/error_fallback）
│       │   └── agent.py          # diagnose() 顶层入口（规则→RAG→LLM→后处理）
│       ├── fixer/                # [D-01/D-02/D-03] 修复生成
│       │   ├── __init__.py       #   导出 generate
│       │   ├── template.py       # 命令模板引擎（占位符渲染 + 可编辑参数/删除/重排）
│       │   ├── generator.py      # 多步骤脚本生成（bash/python，set -euo pipefail）
│       │   ├── checker.py        # D-03 多维错误检测（语法/危险/兼容/占位符）
│       │   ├── prompts.py        # 修复 Prompt 模板
│       │   ├── postprocess.py    # 修复输出解析与降级
│       │   └── agent.py          # generate() 顶层入口
│       ├── safety/               # [E-01~E-04, F-03] 安全可控（全部不经 LLM）
│       │   ├── __init__.py       #   包导出：execution_guard_check, review_confirm, ...
│       │   ├── patterns.py       # 危险命令模式库（DangerPattern 数据定义，非逻辑）
│       │   ├── danger.py         # E-02 执行前熔断（正则+变量展开检测+影响评估）
│       │   ├── review.py         # E-01/F-03 审核确认判定（stdin [y/N]，不经 LLM）
│       │   ├── snapshot.py       # E-03 操作快照 & 一键回滚
│       │   ├── executor.py       # 受控执行器（逐步执行 + 失败即停 + 超时控制）
│       │   ├── verifier.py       # 结果验证
│       │   └── audit.py          # E-04 审计日志（JSONL，专用函数写入，不经 Agent 输出流）
│       ├── workflow/             # [F-01/F-02/F-03] 工作流编排与 CLI
│       │   ├── __init__.py
│       │   ├── states.py         # 10 态状态机 + 7 步用户视图映射 + 转换规则
│       │   ├── persist.py        # 工作流状态持久化与恢复
│       │   ├── engine.py         # WorkflowEngine 主编排
│       │   └── cli/              # CLI 子包
│       │       ├── __init__.py
│       │       ├── app.py        # CLI 主入口 & 命令注册（argparse + argcomplete）
│       │       ├── interact.py   # 交互式参数输入（编辑修复参数、CONFIRM 确认）
│       │       ├── review_ui.py  # 审核交互界面（渲染摘要 + 收集选择）
│       │       ├── display.py    # Rich 输出（Table/Panel/Markdown）
│       │       ├── cmd_run.py    # run 命令
│       │       ├── cmd_env.py    # env 命令
│       │       ├── cmd_diagnose.py  # diagnose 命令
│       │       ├── cmd_fix.py    # fix 命令
│       │       ├── cmd_review.py # review 命令
│       │       ├── cmd_snapshot.py  # snapshot 命令（list/show/rollback）
│       │       ├── cmd_audit_log.py # audit-log 命令
│       │       ├── cmd_kb.py     # kb 命令（import/list/delete/reindex）
│       │       └── cmd_completion.py  # completion 命令
│       ├── knowledge/            # [X-02 选做] RAG 客户知识库
│       │   ├── __init__.py       #   导出 retrieve_similar + KnowledgeStore
│       │   ├── types.py          # KnowledgeCase / RetrievalResult
│       │   ├── store.py          # 向量存储（numpy 落盘：index.json + vectors.npy）
│       │   ├── indexer.py        # 导入与索引（frontmatter 解析 + 增量 embedding）
│       │   └── retriever.py      # 语义检索（余弦 top-k + 环境过滤 + 阈值过滤）
│       ├── trace/                # [X-04 选做] 推理可观测
│       │   ├── __init__.py       #   导出 TraceRecorder + get_recorder
│       │   └── recorder.py       # TraceRecorder（contextvars + Span 栈 + JSONL 追加写入）
│       └── shared/               # 横切共享
│           ├── __init__.py
│           ├── types.py          # 全部 dataclass/enum 数据契约（EnvironmentType, DiagnosisResult 等）
│           ├── constants.py      # 银河平台组件清单、关键日志路径、中文标签等领域知识
│           └── errors.py         # 统一异常体系（GalaxyDiagError + 子类）
├── tests/                        # 测试
│   ├── unit/                     # 单元测试
│   │   ├── test_collector/
│   │   ├── test_diagnoser/
│   │   ├── test_fixer/
│   │   ├── test_workflow/
│   │   └── test_workflow_cli/
│   ├── test_*.py                 # 集成/功能测试（顶层）
│   └── conftest.py               # 公共 fixture
├── deploy/                       # 离线部署
│   ├── Dockerfile                # Linux 平台 wheel 下载容器
│   ├── prepare_offline.sh        # 有网机器：下载 Ollama + 模型 GGUF + Python wheels
│   ├── install_offline.sh        # 断网机器：一键安装 + 导入模型 + 创建 venv
│   ├── Modelfile                 # Ollama 模型定义文件（离线导入参考）
│   └── offline/                  # 离线介质存放目录（不入库）
├── docs/                         # 设计文档
├── pyproject.toml                # 包定义与入口（galaxy-diag = galaxy_diag.workflow.cli.app:main）
├── config.yaml                   # 默认配置（零外网地址，model=qwen3:1.7b）
├── requirements.txt              # Python 依赖（openai/httpx/pyyaml/rich/numpy）
└── README.md
```

### 组织原则

1. **7 大域 ↔ 7 个顶层包**：与任务书需求矩阵一一对应，评审/演示时可直接映射
2. **每个包内 3-5 个文件**：单个文件可控在 200-400 行
3. **safety 包独立**：确保安全关键路径不经 LLM 的物理隔离
4. **选做模块独立包**：`knowledge/`、`trace/` 删除不影响主线
5. **shared 层薄**：只放真正跨域的类型、常量、异常、工具

## 4. 模块依赖关系与数据流

核心原则：**数据单向流动，安全层独立旁路。**

```
用户问题 ──→ workflow/engine.py (状态机)
                 │
                 ▼
           ┌─ collector/ ──→ 结构化环境信息 (EnvInfo)
           │     │
           ▼     ▼
        diagnoser/ ──→ 诊断结论 (DiagnosisResult: 确认/推测/信息不足)
           │
           ▼
        fixer/ ──→ 修复建议 (FixProposal) + 多维检测结果
           │
           ▼
        safety/review.py ──→ 人工确认 (stdin [y/N]，不经 LLM)
           │              safety/danger.py ──→ 危险操作拦截（正则硬编码）
           │              safety/snapshot.py ──→ 快照创建
           │
     ┌─────┴──────┐
     ▼            ▼
  执行修复     用户拒绝→终止
     │
     ▼
  验证结果 ──→ safety/audit.py (审计日志，专用函数写入 JSONL)
```

### 依赖规则

| 规则 | 说明 |
|------|------|
| `safety/` 不依赖 `diagnoser/` 和 `fixer/` | 安全层只接收"待审核操作"作为输入，不调用任何 LLM 推理 |
| `collector/` 不依赖 `diagnoser/` | 采集是纯只读操作，独立可测 |
| `workflow/` 依赖所有域，但只做编排 | engine.py 是唯一知道"先采集→再诊断→再修复→再审核"的地方 |
| `model/client.py` 是所有 LLM 调用的唯一出口 | diagnoser/fixer 都通过它调模型，便于统一加日志/超时/重试 |
| `shared/types.py` 定义所有跨域数据结构 | 确保域间契约类型化、可静态检查 |

### 依赖关系图

```
workflow ──→ diagnoser ──→ collector
         ──→ fixer      ──→ model (client)
         ──→ safety     ──→ shared (types, constants)

model ──→ config (settings)
collector ──→ shared
diagnoser ──→ model, collector, shared
fixer ──→ model, shared
safety ──→ shared (独立！不依赖 model/diagnoser/fixer)
```

## 5. 跨域核心数据结构

`shared/types.py` 定义 7 大域之间传递的契约——每个域的输出就是下一个域的输入。

```python
# shared/types.py 核心类型

from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

# ===== 环境感知 =====

class EnvironmentType(str, Enum):
    BARE_METAL = "bare_metal"
    VM = "vm"
    CONTAINER = "container"

@dataclass
class HardwareInfo:
    """硬件基本信息"""
    cpu_model: str
    cpu_cores: int
    memory_total_gb: float
    disks: list[dict]       # [{type, capacity, model}]
    raid_cards: list[dict]  # [{model, firmware_version}]
    nics: list[dict]        # [{model, driver}]

@dataclass
class StorageInfo:
    """第三方存储设备信息"""
    storage_type: Literal["SAN", "NAS", "local"]
    mount_path: str
    filesystem: str
    details: dict           # 扩展字段

@dataclass
class EnvInfo:
    """collector → diagnoser 的采集结果"""
    env_type: EnvironmentType
    hardware: HardwareInfo
    storage: list[StorageInfo]
    raw_output: dict        # 原始采集数据（供 LLM 上下文）

# ===== 诊断分析 =====

class Confidence(str, Enum):
    CONFIRMED = "confirmed"       # 已确认
    SUSPECTED = "suspected"       # 推测
    INSUFFICIENT = "insufficient" # 信息不足

@dataclass
class DiagnosisResult:
    """diagnoser → fixer 的诊断结论"""
    root_cause: str
    confidence: Confidence
    missing_info: list[str]       # 信息不足时，列出缺失项
    evidence: list[str]           # 支撑结论的证据
    env_type: EnvironmentType     # 来源环境类型

# ===== 修复生成 =====

@dataclass
class CommandTemplate:
    """单条命令模板"""
    command: str                  # 含占位符如 <IP>, <MOUNT_POINT>
    description: str
    risk_note: str               # 安全风险提示
    editable_params: dict[str, str]  # 占位符名 → 默认值

@dataclass
class FixProposal:
    """fixer → safety 的修复建议"""
    commands: list[CommandTemplate]
    script: str | None            # 多步骤脚本内容（可选）
    script_language: Literal["bash", "python"] | None
    risk_notes: list[str]         # 整体风险提示
    check_passed: bool            # 多维检测是否通过
    check_issues: list[str]       # 检测发现的问题
    impact_scope: str             # 影响范围描述

# ===== 安全可控 =====

@dataclass
class SnapshotMeta:
    """快照元数据"""
    snapshot_id: str
    timestamp: datetime
    operation_summary: str
    affected_files: list[str]
    affected_services: list[str]
    backup_path: str

@dataclass
class AuditRecord:
    """审计日志记录"""
    timestamp: datetime | None = None
    session_id: str
    operator: str
    action: str
    result: Literal["confirmed", "success", "failure", "rollback", "rejected", "verify_failed"]
    llm_basis: str              # LLM 分析依据摘要
    snapshot_id: str | None     # 关联的快照 ID
    user_input: str             # 用户确认输入（y / n / CONFIRM）

# ===== 工作流 =====

class WorkflowStep(str, Enum):
    """工作流内部状态机（10 态），映射到 7 个用户可见步骤"""
    ENV_RECOGNISING = "env_recognising"       # 步骤1 环境识别
    COLLECTING = "collecting"                 # 步骤2 信息采集
    DIAGNOSING = "diagnosing"                 # 步骤3 根因分析
    PLANNING = "planning"                     # 步骤4 修复建议
    SECURITY_CHECKING = "security_checking"   # 步骤4 D-03 生成后检测
    EXECUTION_GUARD = "execution_guard"       # 步骤5 E-02 执行前熔断
    REVIEWING = "reviewing"                   # 步骤5 人工审核
    SNAPSHOT = "snapshot"                     # 步骤6 创建快照
    EXECUTING = "executing"                   # 步骤6 执行修复
    VERIFYING = "verifying"                   # 步骤7 结果验证

@dataclass
class WorkflowState:
    """工作流持久化状态"""
    session_id: str
    current_step: WorkflowStep
    problem_description: str
    env_info: EnvInfo | None
    diagnosis: DiagnosisResult | None
    fix: FixProposal | None
    snapshot: SnapshotMeta | None
    history: list[dict]         # 步骤历史（含时间戳和结果）
```

## 6. 安全关键路径架构

**核心原则：LLM 只能"建议"，"决定权"在硬编码逻辑。**

### 6.1 双通道架构

```
                          LLM 路径（可被 Prompt 注入影响）
                    ┌─────────────────────────────────┐
                    │  diagnoser/  →  fixer/           │
                    │  "可能是 X 原因"  "建议执行 Y"   │
                    └────────────┬────────────────────┘
                                 │ FixProposal
                                 ▼
                    ┌─────────────────────────────────┐
                    │  safety/ (硬编码逻辑，不经 LLM)   │
                    │                                  │
  ┌─────────────┐   │  ① danger.py: 正则匹配危险命令   │
  │ patterns.py │──→│     命中 → 人工审核，要求 CONFIRM │
  │ (危险模式库) │   │                                  │
  └─────────────┘   │  ② review.py: CLI 弹出 [y/N]    │
                    │     输入走 stdin，不走 LLM 通道   │
                    │     危险操作 → 要求输入 CONFIRM   │
                    │                                  │
                    │  ③ snapshot.py: 执行前自动备份    │
                    │     .bak/ + 元数据 JSON           │
                    │                                  │
                    │  ④ audit.py: 专用函数写入 JSONL   │
                    │     不经 Agent/LLM 输出流         │
                    └─────────────────────────────────┘
```

### 6.2 安全关卡详细设计

| 安全关卡 | 实现方式 | 绕过防护 |
|---------|---------|---------|
| 危险命令拦截 | `safety/danger.py` 正则+变量展开检测匹配 `patterns.py` 中的模式 | 不可能——在 Agent 输出后、用户确认前硬编码拦截 |
| 人工确认 | `safety/review.py` 读 stdin 的 `[y/N]`，不经过 LLM 调用通道 | 不可能——stdin 输入不经过 LLM |
| 二次确认 | 危险操作要求输入 `CONFIRM <操作摘要>`，摘要由硬编码逻辑生成 | 不可能——Prompt 注入无法控制 stdin |
| 审计日志 | `safety/audit.py` 用 `json.dumps().write()` 直接写文件 | 不可能——Agent 没有修改审计日志的 Tool |
| 快照回滚 | `safety/snapshot.py` 备份文件到 `.bak/`，回滚命令从备份恢复 | 回滚本身也需经 review.py 确认 |

### 6.3 危险命令模式库设计

`patterns.py` 维护一组正则模式，`danger.py` 用它做匹配。需做变量展开检测防止绕过：

```python
# safety/patterns.py (示意)

DANGER_PATTERNS = [
    # 数据破坏
    (r"rm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+|.*--no-preserve-root)", "危险删除", "critical"),
    (r"mkfs\.", "格式化文件系统", "critical"),
    (r"dd\s+.*of=/dev/", "直接写块设备", "critical"),

    # 权限变更
    (r"chmod\s+(777|666)", "过度宽松权限", "high"),
    (r"chown\s+.*-R\s+/", "递归修改所有权", "high"),

    # 网络安全
    (r"iptables\s+-F", "清空防火墙规则", "critical"),
    (r"iptables\s+-X", "删除防火墙链", "critical"),

    # 系统关键
    (r"systemctl\s+(stop|disable)\s+(sshd|docker|kubelet)", "停止关键服务", "high"),
]
```

变量展开检测：扫描脚本中的变量赋值，追踪变量值是否包含危险命令片段，防止 `CMD="rm -rf"; $CMD /` 绕过。

### 6.4 审核确认交互流程

```
┌─────────────────────────────────────────────────┐
│  即将执行以下操作:                                │
│                                                  │
│  操作: modprobe vmw_pvscsi && rescan-scsi-bus.sh │
│  影响范围: 加载内核模块 vmw_pvscsi, 扫描 SCSI 总线 │
│  快照: snap_20260805_001 (已创建)                │
│  回滚命令: galaxy-diag rollback snap_20260805_001│
│                                                  │
│  风险等级: 中 (加载内核模块)                       │
│                                                  │
│  确认执行? [y/N]: _                              │
│                                                  │
│  (危险操作时: 请输入 CONFIRM modprobe_vmw_pvscsi) │
└─────────────────────────────────────────────────┘
```

## 7. 工作流状态机

### 7.1 状态定义

```
collect ──→ diagnose ──→ fix ──→ review ──→ execute ──→ verify
   │            │          │        │          │
   │            │          │        └─ 拒绝 → 终止
   │            │          └─ 检测失败 → 回到 fix
   │            └─ 信息不足 → 回到 collect (补充信息)
   └─ 采集失败 → 报错终止
```

### 7.2 关键状态转换规则

| 当前状态 | 下一状态 | 触发条件 |
|---------|---------|---------|
| collect | diagnose | 采集完成且 env_info 有效 |
| diagnose | fix | confidence=CONFIRMED 或 SUSPECTED |
| diagnose | collect | confidence=INSUFFICIENT，需补充采集 |
| fix | review | check_passed=True |
| fix | fix | check_passed=False，需重新生成 |
| review | execute | 用户确认 y |
| review | (终止) | 用户拒绝 N |
| execute | verify | 执行成功 |
| execute | (回滚) | 执行失败，自动从快照恢复 |

### 7.3 状态持久化

`workflow/persist.py` 将 `WorkflowState` 序列化为 JSON 文件（`~/.galaxy-diag/sessions/<session_id>.json`），用户中断后可通过 `galaxy-diag resume <session_id>` 恢复。

## 8. 配置与离线策略

### 8.1 配置加载优先级

```
config.yaml（显式 config_path 或 GALAXY_CONFIG_FILE 环境变量指定）→ 环境变量覆盖（前缀 GALAXY_）→ config/defaults.py 数据类默认值
```

硬件要求额外规则：显式 `hardware:` 段字段优先于按 `llm.model` 参数量自动推导的值（`config/model_profile.py`），未配置的字段回退到自动推导。

### 8.2 关键配置项

```yaml
# config.yaml
llm:
  base_url: "http://localhost:11434/v1"  # 可配置，不硬编码
  model: "qwen3:1.7b"                    # 可配置；生产建议 qwen3:8b
  api_key: "ollama"                      # Ollama 不验证 key，SDK 要求非空
  timeout: 600                           # 纯 CPU 推理 8B 需 3-5 分钟
  max_retries: 3
  max_tokens: 1024
  embed_model: "bge:large"               # RAG embedding 模型；空字符串=禁用 RAG

# hardware 段默认根据 llm.model 参数量自动推导；如需手动覆盖某项取消注释
# hardware:
#   min_cpu_cores: 4
#   min_ram_gb: 3.0
#   min_gpu_vram_gb: 6.0
#   min_disk_gb: 10.0
#   gpu_required: false

knowledge:                               # RAG 检索配置（REQ-X-02）
  top_k: 3                               # 检索返回最大案例数
  min_similarity: 0.0                    # 最低余弦相似度阈值，0.0=不过滤
```

> 运行时持久化路径（sessions / audit / snapshots / knowledge_base / traces）统一约定为 `~/.galaxy-diag/`，可通过环境变量覆盖（`GALAXY_SESSION_DIR` / `GALAXY_KB_DIR` 等），不在 config.yaml 中集中配置。

### 8.3 离线部署流程

```
[有网环境]                          [断网客户环境]
   │                                    │
   ├─ prepare_offline.sh                │
   │   下载 Ollama .tar.zst             │
   │   下载模型 GGUF                    │
   │   Docker 容器下载 Linux wheels     │
   │                                    │
   └──── U盘/内网传输 ────────→         │
                                        ├─ install_offline.sh 安装（创建 venv）
                                        ├─ Modelfile + ollama create 导入模型
                                        ├─ 资源预检 (precheck.py)
                                        └─ 启动 galaxy-diag
```

## 9. 模型客户端设计

`model/client.py` 中的 `ModelAdapter` 是所有 LLM 调用的唯一出口，基于 `LLMConfig` 构造，内部包装 `openai.OpenAI` 客户端。核心方法：

- `chat(messages, tools=None, timeout=None, max_tokens=None) -> str | None` — 同步对话，返回助手回复文本（含 `tool_calls` 时通过 `ToolCall` dataclass 返回）；失败抛 `ModelCallError`
- `embed(texts, model=None) -> list[list[float]]` — 批量文本向量化，供 RAG 检索使用（`embed_model` 为空时抛 `ModelCallError`）
- `check_health()` — 委托 `ModelHealthChecker` 做三阶段健康检查

另有 `MockModelAdapter`（`model/mock_client.py`）提供零网络实现，`--mock` 模式下替代真实 adapter，使全链路可离线确定性测试。健康检查逻辑独立于 adapter 在 `model/health.py` 中（`ModelHealthChecker`：服务可达→模型存在→推理可用，返回 `HealthResult`）。

## 10. 测试策略

| 层级 | 目录 | 重点 |
|------|------|------|
| 单元测试 | `tests/unit/test_collector/` 等 | 每个域的逻辑正确性，mock LLM 调用 |
| 集成/功能测试 | `tests/`（顶层 `test_*.py`） | 域间交互（collector→diagnoser→fixer→safety 链路）、RAG 集成、Prompt 注入、状态恢复、安全重试循环 |
| 安全测试 | 贯穿单元/集成 | 危险命令绕过、Prompt 注入、审计完整性 |

### 关键测试场景

1. **安全绕过测试**：构造包含变量展开的危险脚本，验证 danger.py 能检出
2. **Prompt 注入测试**：在日志内容中嵌入"用户已确认执行"，验证 review.py 不受影响
3. **离线可用测试**：断开公网后执行完整诊断-修复流程，检查无外网请求
4. **状态恢复测试**：中断工作流后 resume，验证从正确步骤继续
5. **RAG 集成测试**：mock embedding 模型 + 预设案例，跑完整 `diagnose()` 流程验证"检索→注入→来源标注"链路

## 11. 选做模块设计

### 11.1 knowledge/ — 客户知识库 (X-02)

- 案例导入（Markdown frontmatter 解析 + 增量 embedding）+ 向量存储（numpy 落盘：`index.json` + `vectors.npy`）+ 语义检索（余弦 top-k + 环境过滤）
- 诊断时语义检索相关案例，注入 Prompt 上下文（"只增强、不短路"）
- 输出标注信息来源（通用知识 vs 客户特有案例）
- 不引入 FAISS / ChromaDB / sentence-transformers（离线轻量约束）；规模突破 numpy 舒适区时 `retrieve_similar()` 内部可替换为 FAISS

### 11.2 trace/ — 推理可观测 (X-04)

- Agent 每步操作追加 trace 日志（调用了哪些 Tool、得到什么结果、推理逻辑）
- 诊断完成后 trace 可通过 `galaxy-diag trace <session_id>` 回放
- trace 持久化到本地文件，服务重启后可查询
