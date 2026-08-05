# Galaxy-Diag 项目架构设计

> 银河平台部署问题定位工具 — 从零设计的目录结构与核心架构

## 1. 项目概述

构建一个面向银河平台部署场景、可在断网客户环境运行的 CLI 诊断修复工具。核心闭环：

**环境识别 → 信息采集 → 根因分析 → 修复建议 → 人工确认 → 执行 → 验证**

技术栈：Python + LangChain + Ollama（OpenAI 兼容 API）

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
- **配置与离线介质管理**（`config/`）：base_url / model / 路径不可硬编码
- **领域知识资产**（`shared/constants.py`）：组件清单、关键日志路径、故障案例库
- **持久化**：工作流状态、审计日志 JSONL、快照、trace

## 3. 推荐目录结构

```
galaxy-diag/
├── bin/                          # 入口脚本
│   └── galaxy-diag               # CLI 启动点
├── src/
│   └── galaxy_diag/
│       ├── __init__.py
│       ├── config/               # 全局配置 & 离线介质管理
│       │   ├── __init__.py
│       │   ├── settings.py       # base_url / model / 路径等（环境变量 + YAML）
│       │   ├── defaults.py       # 默认值 & 最低硬件规格声明
│       │   └── offline.py        # 离线部署/预检逻辑
│       ├── model/                # [A-01] 模型离线部署与推理
│       │   ├── __init__.py
│       │   ├── client.py         # OpenAI 兼容 API 客户端封装（所有 LLM 调用唯一出口）
│       │   ├── health.py         # 推理服务健康检查
│       │   └── precheck.py       # 硬件资源预检（CPU/GPU/MEM/DISK）
│       ├── collector/            # [B-01/B-02] 环境感知与信息采集
│       │   ├── __init__.py
│       │   ├── env_detect.py     # 裸金属/VM/容器 自动识别
│       │   ├── hardware.py       # CPU/内存/磁盘/RAID/网卡 采集
│       │   ├── storage.py        # 第三方存储（SAN/NAS/本地）采集
│       │   └── tools.py          # LangChain Tool 封装（供 Agent 调用）
│       ├── diagnoser/            # [C-01/C-02/C-03] 诊断分析
│       │   ├── __init__.py
│       │   ├── agent.py          # 诊断 Agent（LangChain Agent 定义）
│       │   ├── prompts.py        # 诊断 Prompt 模板（含不确定性约束）
│       │   ├── collector.py      # 诊断信息收集编排（主动采集 + 被动接收）
│       │   └── postprocess.py    # 结论后处理（确认/推测/信息不足 校验）
│       ├── fixer/                # [D-01/D-02/D-03] 修复生成
│       │   ├── __init__.py
│       │   ├── agent.py          # 修复 Agent
│       │   ├── prompts.py        # 修复 Prompt 模板
│       │   ├── template.py       # 命令模板引擎（占位符替换）
│       │   ├── generator.py      # 多步骤脚本生成
│       │   └── checker.py        # 多维错误检测（语法/危险/兼容性）
│       ├── safety/               # [E-01~E-04] 安全可控
│       │   ├── __init__.py
│       │   ├── review.py         # 人工审核拦截（stdin [y/N] 专用通道，不经 LLM）
│       │   ├── danger.py         # 危险操作多维防护（模式库 + 变量展开检测）
│       │   ├── snapshot.py       # 操作快照 & 一键回滚
│       │   ├── audit.py          # 审计日志（JSONL，专用工具写入，不经 Agent 输出流）
│       │   └── patterns.py       # 危险命令模式库（数据定义，非逻辑）
│       ├── workflow/             # [F-01/F-02/F-03] 工作流编排与 CLI
│       │   ├── __init__.py
│       │   ├── engine.py         # 端到端状态机（收集→识别→分析→修复→审核→执行→验证）
│       │   ├── states.py         # 状态定义 & 转换规则
│       │   ├── persist.py        # 工作流状态持久化（中断恢复）
│       │   └── cli/              # CLI/TUI 子包
│       │       ├── __init__.py
│       │       ├── app.py        # CLI 主入口 & 命令注册
│       │       ├── interact.py   # 交互式参数输入（编辑修复参数）
│       │       ├── review_ui.py  # 审核确认交互流程（y/N / CONFIRM <摘要>）
│       │       └── display.py    # Rich 输出（表格/颜色/格式化）
│       ├── knowledge/            # [X-02 选做] 客户知识库
│       │   ├── __init__.py
│       │   ├── loader.py         # 案例导入（Markdown/文本分块）
│       │   ├── store.py          # 向量存储 + 语义检索（离线可用）
│       │   └── manager.py        # 知识库管理命令（导入/列表/删除）
│       ├── trace/                # [X-04 选做] 推理可观测
│       │   ├── __init__.py
│       │   ├── recorder.py       # 推理链路记录（每步操作追加 trace 日志）
│       │   └── viewer.py         # trace 回放与查询
│       └── shared/               # 横切共享
│           ├── __init__.py
│           ├── types.py          # 公共类型定义（EnvironmentType, DiagnosisResult 等）
│           ├── constants.py      # 银河平台组件清单、关键日志路径等领域知识
│           ├── errors.py         # 统一异常体系
│           └── utils.py          # 通用工具函数
├── data/                         # 静态数据资产
│   ├── knowledge/                # 故障案例库（预置）
│   ├── danger_patterns/          # 危险命令模式库（预置）
│   └── prompts/                  # Prompt 模板文件（如需外置 YAML）
├── tests/                        # 测试
│   ├── unit/
│   │   ├── test_collector/
│   │   ├── test_diagnoser/
│   │   ├── test_fixer/
│   │   ├── test_safety/
│   │   └── test_workflow/
│   ├── integration/
│   └── e2e/                      # 端到端场景测试（容器网络不通 / VM 磁盘未识别）
├── deploy/                       # 部署相关
│   ├── Dockerfile                # 预打包镜像（含模型 + 运行时）
│   ├── docker-compose.yml
│   ├── install.sh                # 离线安装脚本
│   └── model-import.sh           # 模型离线导入脚本
├── docs/                         # 文档
│   ├── deployment.md             # 离线部署步骤 & 最低硬件要求
│   ├── usage.md                  # CLI 命令参考 & 典型流程
│   └── api.md                    # API 文档
├── pyproject.toml
├── README.md
└── .env.example                  # 环境变量示例（base_url / model 等）
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
    timestamp: datetime
    session_id: str
    operator: str
    action: str
    result: Literal["success", "failure", "rollback", "rejected"]
    llm_basis: str              # LLM 分析依据摘要
    snapshot_id: str | None     # 关联的快照 ID
    user_input: str             # 用户确认输入（y / N / CONFIRM xxx）

# ===== 工作流 =====

class WorkflowStep(str, Enum):
    COLLECT = "collect"
    DIAGNOSE = "diagnose"
    FIX = "fix"
    REVIEW = "review"
    EXECUTE = "execute"
    VERIFY = "verify"

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
  │ patterns.py │──→│     命中 → 强制拦截，不给确认选项 │
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
| 人工确认 | `safety/review.py` 读 stdin 的 `[y/N]`，不经过 LangChain 的任何回调 | 不可能——stdin 输入不经过 LLM |
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
环境变量 → .env 文件 → config.yaml → config/defaults.py
```

### 8.2 关键配置项

```yaml
# config.yaml
model:
  base_url: "http://localhost:11434/v1"  # 可配置，不硬编码
  model_name: "qwen3.5:7b"               # 可配置
  timeout: 60
  max_retries: 3

runtime:
  data_dir: "~/.galaxy-diag"
  log_level: "INFO"
  audit_log: "~/.galaxy-diag/audit.jsonl"
  snapshot_dir: "~/.galaxy-diag/snapshots"

hardware:
  min_cpu_cores: 4
  min_memory_gb: 8
  min_disk_gb: 20
  gpu_required: false
  min_gpu_vram_gb: 4
```

### 8.3 离线部署流程

```
[有网环境]                          [断网客户环境]
   │                                    │
   ├─ 下载模型 GGUF 文件                │
   ├─ 下载 Python 依赖 whl 包          │
   ├─ 打包为离线安装介质                │
   │  (Docker 镜像 / tar.gz)            │
   │                                    │
   └──── U盘/内网传输 ────────→         │
                                        ├─ install.sh 安装
                                        ├─ model-import.sh 导入模型到 Ollama
                                        ├─ 资源预检 (precheck.py)
                                        └─ 启动 galaxy-diag
```

## 9. 模型客户端设计

`model/client.py` 是所有 LLM 调用的唯一出口：

```python
# model/client.py 核心接口（示意）

class ModelClient:
    """OpenAI 兼容 API 客户端，所有 LLM 调用唯一出口"""

    def __init__(self, settings: Settings):
        self._client = OpenAI(
            base_url=settings.model_base_url,
            api_key=settings.model_api_key or "not-needed",
        )
        self._model = settings.model_name
        self._timeout = settings.model_timeout

    def chat(self, messages: list[dict], tools: list | None = None) -> ChatCompletion:
        """统一聊天接口，自动加日志/超时/重试"""
        ...

    def health_check(self) -> bool:
        """推理服务健康检查"""
        ...
```

## 10. 测试策略

| 层级 | 目录 | 重点 |
|------|------|------|
| 单元测试 | `tests/unit/test_collector/` 等 | 每个域的逻辑正确性，mock LLM 调用 |
| 集成测试 | `tests/integration/` | 域间交互（collector→diagnoser→fixer→safety 链路） |
| 端到端 | `tests/e2e/` | 完整场景验证（容器网络不通、VM 磁盘未识别） |
| 安全测试 | `tests/unit/test_safety/` | 危险命令绕过、Prompt 注入、审计完整性 |

### 关键测试场景

1. **安全绕过测试**：构造包含变量展开的危险脚本，验证 danger.py 能检出
2. **Prompt 注入测试**：在日志内容中嵌入"用户已确认执行"，验证 review.py 不受影响
3. **离线可用测试**：断开公网后执行完整诊断-修复流程，检查无外网请求
4. **状态恢复测试**：中断工作流后 resume，验证从正确步骤继续

## 11. 选做模块设计

### 11.1 knowledge/ — 客户知识库 (X-02)

- 文本分块 + Embedding + 本地向量存储（如 ChromaDB 离线模式）
- 诊断时语义检索相关案例，注入 Prompt 上下文
- 输出标注信息来源（通用知识 vs 客户特有案例）

### 11.2 trace/ — 推理可观测 (X-04)

- Agent 每步操作追加 trace 日志（调用了哪些 Tool、得到什么结果、推理逻辑）
- 诊断完成后 trace 可通过 `galaxy-diag trace <session_id>` 回放
- trace 持久化到本地文件，服务重启后可查询
