# 诊断信息采集模块设计

> 对应需求：REQ-C-01（问题诊断信息收集）
> 实现位置：`src/galaxy_diag/diagnoser/`（新建模块，对齐架构设计 §3 `diagnoser/` 包）
> 工作流集成：`WorkflowStep.COLLECTING`（对齐 `workflow-design.md` §2 状态机）

## 模块概述

诊断信息采集是诊断-修复闭环的第二步（`COLLECTING`），承接 `ENV_RECOGNISING` 产出的 `EnvInfo`，采集**与本次故障相关的运行时状态与日志**，组装为结构化诊断上下文 `DiagnosticContext`，作为根因分析（`DIAGNOSING`）的输入。本模块负责：

1. **两种信息输入方式**：主动采集（执行诊断 Tool 收集组件状态/日志/资源/连通性）+ 被动接收（用户问题描述补充 + 日志上传）
2. **按问题描述定向采集**：根据 `problem_description` 的关键词选择采集哪些 Tool，而非全量采集（区别于 B-02 的全量硬件盘点）
3. **预处理与体积控制**：原始采集输出经裁剪/去噪/截断后形成结构化上下文，控制 LLM 上下文体积
4. **结构化输出**：组装 `DiagnosticContext` 写入 `WorkflowState`，支持中断恢复

### 职责边界（与 ENV_RECOGNISING 的区分）

项目中有两个"采集"环节，本节明确二者边界，避免职责重叠与评审混淆：

| 范畴 | ENV_RECOGNISING（B-01/B-02） | COLLECTING（C-01，本设计） |
|------|------------------------------|----------------------------|
| 采集对象 | **静态硬件盘点**：CPU/内存/磁盘/RAID/网卡/存储拓扑 | **运行时诊断信息**：组件部署状态、服务日志、系统资源、网络连通性 |
| 采集时机 | 流程第一步，无条件全量采集 | 流程第二步，**按问题描述定向采集** |
| 驱动方式 | 环境类型决定采集策略 | `problem_description` 关键词决定采集哪些 Tool |
| 产出 | `EnvInfo`（写入 `state.env_info`） | `DiagnosticContext`（写入 `state.diagnostic_context`） |
| 实现包 | `collector/`（已实现） | `diagnoser/`（本设计新建） |

> **核心原则**：COLLECTING **不重复**采集 `EnvInfo` 已覆盖的静态硬件信息；它引用 `state.env_info` 作为环境上下文，只采集运行时状态与日志。

| 范畴 | 说明 |
|------|------|
| 本模块负责 | 运行时信息采集、问题描述定向选择、日志预处理与体积控制、结构化上下文组装、采集失败降级与提示 |
| 本模块不负责 | 根因推理（`diagnoser/agent.py`，DIAGNOSING 步骤）、修复生成（`fixer/`）、人工审核（`safety/`）、静态硬件盘点（`collector/`，B-02） |

## 整体架构设计

### 分层架构

```
                    Workflow Engine (engine.py _do_collecting)
                          │
                          │ 调用
                          ▼
            ┌─────────────────────────────┐
            │   diagnoser/context.py      │  顶层编排
            │   build_diagnostic_context()│  唯一入口
            │   - 关键词→Tool 映射         │
            │   - 预处理与体积控制         │
            └──────────────┬──────────────┘
                           │
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
   ┌────────────────┐ ┌──────────┐ ┌─────────────────┐
   │ diagnoser/     │ │ 被动接收 │ │ shared/         │
   │  tools.py      │ │ (用户补充│ │  constants.py   │
   │  @tool 封装    │ │  /日志上传)│ │  组件清单/日志路径│
   │  - 组件状态     │ └──────────┘ └─────────────────┘
   │  - 服务日志     │
   │  - 系统资源     │
   │  - 网络连通性   │
   └────────────────┘
```

### 文件职责（新建 `diagnoser/` 包）

| 文件 | 职责 | 对应需求 |
|------|------|---------|
| `diagnoser/context.py` | 顶层编排：关键词→Tool 映射、采集调度、预处理、组装 `DiagnosticContext` | REQ-C-01 |
| `diagnoser/tools.py` | LangChain `@tool` 封装，4 个诊断采集 Tool（Agent 唯一调用入口） | REQ-C-01 |
| `diagnoser/__init__.py` | 导出 `build_diagnostic_context()` 顶层函数 | — |

> **命名变更说明**：架构设计 §3 原定 `diagnoser/collector.py`，与 `collector/` 包重名易混淆，故改名为 `diagnoser/context.py`（语义为"组装诊断上下文"，更贴合 C-01 职责）。`diagnoser/agent.py`、`prompts.py`、`postprocess.py` 属于 DIAGNOSING 步骤，由后续诊断分析设计文档展开，本设计不涉及。

### 依赖规则

- `diagnoser/`（本模块）依赖 `shared/`（types / constants / errors）与标准库，**不依赖** `model/`、`workflow/`、`safety/`
- `diagnoser/` **可读** `collector/` 产出的 `EnvInfo`（通过参数传入，非包级依赖——`context.py` 接收 `EnvInfo` 对象，不 import `collector`）
- `tools.py` 是 Agent 与采集层的唯一边界；采集层本身不感知 LangChain
- 全部采集为**只读操作**，不调用任何写命令

## 信息输入方式设计（验收标准1）

任务书 REQ-C-01 验收标准1 要求"支持至少 2 种输入方式"。本设计提供两种，合并汇入同一 `DiagnosticContext`：

| 输入方式 | 来源 | 内容 | 入口 |
|---------|------|------|------|
| **主动采集** | 系统运行时状态 | 组件部署状态、服务日志、系统资源、网络连通性 | `diagnoser/tools.py` 的 4 个 Tool |
| **被动接收** | 用户输入 | 问题描述（已有）+ 补充描述 + 日志文件上传 | `interact.prompt_input` / `--log-file` 参数 |

### 被动接收实现

被动接收已在 `engine.py _do_collecting` stub 中部分实现（补充描述追加到 `problem_description`）。本设计将其正式化：

1. **问题描述**：`WorkflowState.problem_description`（`start_new` 时收集，COLLECTING 步骤可补充）
2. **补充描述**：逐步模式下 `interact.prompt_input("补充描述")`，追加到 `problem_description`（已实现）
3. **日志上传**：CLI `--log-file <path>` 或交互提示输入路径，`context.py` 读取文件内容作为 `user_provided` 日志片段

> 日志上传作为被动输入的补充通道，不强制要求；主动采集是主路径，满足"至少 2 种"。

## 采集内容设计（验收标准2）

任务书明确四类采集内容，每类对应一个诊断 Tool：

### 采集 Tool 清单

| Tool 名称 | 采集类 | 采集内容 | 采集方式 | 环境差异 |
|-----------|--------|---------|---------|---------|
| `collect_component_status` | 组件部署状态 | `GALAXY_COMPONENTS` 各组件运行状态 | `systemctl status` / `kubectl get pod` | 容器用 kubectl，裸金属/VM 用 systemctl |
| `collect_service_logs` | 相关服务日志 | `KEY_LOG_PATHS` 各路径尾部 + journalctl | 文件读取 + `journalctl -u` | 容器用 `kubectl logs`，裸金属/VM 用文件 + journalctl |
| `collect_system_resources` | 系统资源使用 | CPU/MEM 占用、磁盘使用、负载 | `top`/`free`/`df`/`uptime` | 全环境通用 |
| `collect_network_connectivity` | 网络连通性 | ping/端口连通/CNI/iptables | `ping`/`ss`/`iptables -S` | 容器额外采 CNI 配置 |

### 与 B-02 `collect_network` 的区分

`collector/tools.py` 已有 `collect_network`（B-02）采集**网卡型号与驱动**（静态硬件信息）；本模块 `collect_network_connectivity`（C-01）采集**连通性与路由**（运行时状态）。二者采集对象不同，不可合并：

| | B-02 `collect_network` | C-01 `collect_network_connectivity` |
|---|---|---|
| 采集对象 | 网卡型号、驱动模块 | ping 连通性、端口状态、CNI、iptables 规则 |
| 性质 | 静态硬件盘点 | 运行时连通性检测 |
| 时序 | ENV_RECOGNISING | COLLECTING |

## 关键词→Tool 映射设计（实现指引核心）

任务书实现指引明确："Agent 根据问题描述决定调用哪些采集工具"。这是 C-01 区别于 B-02 全量采集的灵魂——按 `problem_description` 定向采集。

### 映射规则

| 问题描述关键词（中文/英文） | 命中 Tool | 说明 |
|---------------------------|-----------|------|
| 网络/不通/ping/连通/network/CNI/iptables | `collect_network_connectivity` | 网络类故障 |
| 磁盘/挂载/存储/盘/识别/disk/mount/storage | `collect_service_logs`（dmesg/存储日志）+ `collect_component_status`（storage 组件） | 存储类故障 |
| 服务/启动/失败/状态/service/fail/status | `collect_component_status` + `collect_service_logs` | 服务异常 |
| 慢/卡/资源/CPU/内存/负载/slow/resource | `collect_system_resources` | 资源类问题 |
| 容器/pod/k8s/container | `collect_component_status`（kubectl） | 容器环境定向 |

### 调度策略

```
1. 解析 problem_description，匹配关键词表
2. 命中 Tool 加入待采集集合
3. 兜底：始终采集 collect_system_resources（资源是通用基础项，避免漏采）
4. 未命中任何关键词时：采集 collect_component_status + collect_system_resources（最小基础集）
5. 被动接收的日志上传：无条件纳入 user_provided
```

> **兜底原则**：关键词匹配是优化手段（减少无关采集），但不可因未命中而完全跳过基础项——资源与组件状态始终采集，避免漏采导致根因分析信息不足。

### 两种调用路径

| 路径 | 调用方 | 入口 | 场景 |
|------|--------|------|------|
| 工作流编排 | `WorkflowEngine._do_collecting()` | `build_diagnostic_context()` | `galaxy-diag run` 端到端流程，按关键词调度 |
| Agent 自主 | Diagnosis Agent | `tools.py` 的 4 个 Tool | Agent 在 DIAGNOSING 步骤按需补采（信息不足时回退 COLLECTING） |

## 预处理与体积控制设计（验收标准3）

任务书要求"采集的原始信息经预处理后形成结构化诊断上下文"。`context.py` 对原始输出做以下预处理：

### 预处理动作

| 动作 | 说明 | 实现 |
|------|------|------|
| 日志裁剪 | 按时间窗（近 N 分钟）+ 关键字（ERROR/Warning/故障关键词）过滤 | `context.py` 过滤函数 |
| 去噪 | 去除 ANSI 色码、合并连续重复行 | 正则 + 去重 |
| 单条截断 | 单条原始输出超 2KB 截断并标注 `[truncated]` | 对齐 EnvInfo.raw_output 截断策略 |
| 体积控制 | 总上下文预算 32KB，超限时按优先级保留（ERROR > Warning > Info） | 预算累加器 |
| 结构化提取 | 从原始输出提取关键字段填入结构化字段（如组件状态→component_status） | 各 Tool 返回结构化结果 |

### 体积预算策略

```
总预算: 32KB（避免 LLM 上下文过大）
优先级: ERROR 日志 > Warning > 组件状态 > 资源指标 > Info 日志
超预算时: 低优先级内容截断或丢弃，保留高优先级
raw_output: 存储预处理后摘要（非全量原始输出）
```

## 数据结构设计

### 新增 DiagnosticContext（COLLECTING → DIAGNOSING 契约）

需在 `shared/types.py` 新增，并写入 `WorkflowState`：

```python
@dataclass
class LogSnippet:
    """日志片段"""
    source: str = ""           # 来源（如 "kubelet" / "/var/log/dmesg" / "user_upload"）
    level: str = ""            # 级别（ERROR/Warning/Info）
    timestamp: str = ""        # 时间窗标注
    content: str = ""          # 预处理后的日志内容
    truncated: bool = False    # 是否已截断


@dataclass
class DiagnosticContext:
    """COLLECTING → DIAGNOSING 的结构化诊断上下文"""
    problem_description: str = ""                  # 用户问题描述（含补充）
    env_info_ref: EnvironmentType = EnvironmentType.BARE_METAL  # 引用环境类型（env_info 本身在 state 中）
    container_runtime: ContainerRuntime | None = None  # 容器运行时子类型（仅 CONTAINER 时有值）
    component_status: list[dict] = field(default_factory=list)  # 组件部署状态 [{name, status, detail}]
    log_snippets: list[LogSnippet] = field(default_factory=list)  # 日志片段
    system_resources: dict = field(default_factory=dict)  # CPU/MEM/磁盘/负载
    network_checks: list[dict] = field(default_factory=list)  # 连通性检测结果 [{target, reachable, detail}]
    user_provided: list[str] = field(default_factory=list)  # 被动接收的用户日志/描述
    collection_warnings: list[str] = field(default_factory=list)  # 采集降级提示
    raw_output: dict = field(default_factory=dict)  # 预处理后摘要（供 LLM 上下文）
    collected_tools: list[str] = field(default_factory=list)  # 实际调用的 Tool 名（可追溯）
```

> **设计决策**：`DiagnosticContext` 不内联完整 `EnvInfo`，仅存 `env_info_ref`（环境类型）+ `container_runtime`（容器子类型），完整 `EnvInfo` 已在 `state.env_info` 中，避免冗余序列化。DIAGNOSING 步骤同时读取 `state.env_info` + `state.diagnostic_context`。`container_runtime` 冗余存一份是为采集策略可追溯——审计/trace 时可直接从 `DiagnosticContext` 还原采集路径，无需回查 `EnvInfo`。

### WorkflowState 变更（决策1：进 WorkflowState）

给 `WorkflowState` 新增字段：

```python
@dataclass
class WorkflowState:
    session_id: str = ""
    current_step: WorkflowStep = WorkflowStep.ENV_RECOGNISING
    problem_description: str = ""
    env_info: EnvInfo | None = None
    diagnostic_context: DiagnosticContext | None = None   # ← 新增（C-01 产出）
    diagnosis: DiagnosisResult | None = None
    fix: FixProposal | None = None
    snapshot: SnapshotMeta | None = None
    history: list[dict] = field(default_factory=list)
```

**同步修改清单**：

| 文件 | 修改 |
|------|------|
| `shared/types.py` | 新增 `LogSnippet`、`DiagnosticContext` dataclass；`WorkflowState` 加 `diagnostic_context` 字段；`DiagnosticContext` 引用 `ContainerRuntime` |
| `shared/types.py`（容器子类型） | 新增 `ContainerRuntime` 枚举（DOCKER/KUBERNETES/UNKNOWN）；`EnvInfo` 加 `container_runtime` 字段（与 Environment_awareness_design.md 同步） |
| `collector/env_detect.py` | 新增 `detect_container_runtime()`：识别为 CONTAINER 后判定 Docker/K8s 子类型；`collect_env()` 编排中加入子类型识别步骤 |
| `workflow/persist.py` | 无需改——使用 `dataclasses.asdict` 序列化，新字段自动包含；`load_state` 反序列化需处理 `DiagnosticContext` 嵌套（确认 asdict/asdict 反向） |
| `docs/Workflow-design.md §3` | 状态映射表新增 `diagnostic_context` 行 |

> **resume 语义**：`diagnostic_context` 已持久化后，resume 到 COLLECTING 不重复采集（除非 DIAGNOSING 回退要求补充）。满足 REQ-F-02"中断后可从上次位置继续"。

## Tool 接口设计

`diagnoser/tools.py` 用 LangChain `@tool` 装饰器封装，作为 Agent 唯一调用入口：

| Tool 名称 | 输入参数 | 输出 | 说明 |
|-----------|---------|------|------|
| `collect_component_status` | `env_type: EnvironmentType`, `container_runtime: ContainerRuntime \| None`, `components: list[str]` | `list[dict]`（组件状态） | 按环境+容器运行时选 systemctl/kubectl/docker |
| `collect_service_logs` | `env_type: EnvironmentType`, `container_runtime: ContainerRuntime \| None`, `log_paths: list[str]`, `keywords: list[str]` | `list[LogSnippet]` | 按关键词过滤日志 |
| `collect_system_resources` | 无 | `dict`（CPU/MEM/磁盘/负载） | 全环境通用 |
| `collect_network_connectivity` | `env_type: EnvironmentType`, `container_runtime: ContainerRuntime \| None`, `targets: list[str]` | `list[dict]`（连通性结果） | Docker: docker network; K8s: CNI/iptables |

### 顶层编排函数

`diagnoser/__init__.py` 暴露 `build_diagnostic_context()` 供工作流引擎调用：

```python
def build_diagnostic_context(
    problem_description: str,
    env_info: EnvInfo,
    user_log_files: list[str] | None = None,
) -> DiagnosticContext:
    """COLLECTING 顶层编排：关键词匹配 → 定向采集 → 预处理 → 组装上下文"""
    env_type = env_info.env_type
    container_runtime = env_info.container_runtime  # 容器运行时子类型

    # 1. 关键词匹配，决定采集哪些 Tool
    tools_to_run = match_tools_by_keywords(problem_description)

    # 2. 定向采集（各 Tool 独立 try/except，单项失败不阻断）
    component_status = []
    log_snippets = []
    system_resources = {}
    network_checks = []
    warnings = []

    if "collect_component_status" in tools_to_run:
        component_status = _safe_collect(
            collect_component_status, env_type, container_runtime,
            GALAXY_COMPONENTS, warnings)
    if "collect_service_logs" in tools_to_run:
        log_snippets = _safe_collect(
            collect_service_logs, env_type, container_runtime,
            list(KEY_LOG_PATHS.values()),
            extract_keywords(problem_description), warnings)
    # collect_system_resources 始终采集（兜底）
    system_resources = _safe_collect(collect_system_resources, warnings=warnings)
    if "collect_network_connectivity" in tools_to_run:
        network_checks = _safe_collect(
            collect_network_connectivity, env_type, container_runtime, [], warnings)

    # 3. 被动接收：用户上传日志
    user_provided = _load_user_logs(user_log_files, warnings)

    # 4. 预处理与体积控制
    log_snippets = preprocess_logs(log_snippets, budget_kb=32)

    # 5. 组装
    return DiagnosticContext(
        problem_description=problem_description,
        env_info_ref=env_type,
        component_status=component_status,
        log_snippets=log_snippets,
        system_resources=system_resources,
        network_checks=network_checks,
        user_provided=user_provided,
        collection_warnings=warnings,
        raw_output=build_raw_summary(...),
        collected_tools=list(tools_to_run) + ["collect_system_resources"],
    )
```

## 异常处理设计

任务书红线"错误处理不能吞"：采集失败不静默忽略，但也不全盘失败——采用**降级采集 + 部分成功 + 明确提示**策略，与 ENV_RECOGNISING 设计一致。

### 异常分类与处理

| 异常场景 | 异常类型 | 处理策略 | 状态影响 |
|---------|---------|---------|---------|
| 采集命令不存在（如 kubectl 未装） | `CollectorToolNotFoundError`（复用） | 跳过该项，记 warning | 部分采集，流程继续 |
| 权限不足（如非 root 读系统日志） | `CollectorPermissionError`（复用） | 跳过该项，记 warning | 部分采集，流程继续 |
| 部分采集成功、部分失败 | `CollectorPartialError`（复用） | 返回已成功部分 + `collection_warnings` | 部分采集，流程继续 |
| 日志文件读取失败（被动接收） | `CollectorPartialError` | 记 warning，跳过该文件 | 流程继续 |
| 整体采集失败（所有 Tool 均失败） | `CollectorError` | 抛出，由 engine 捕获 | 流程暂停，可 resume |

> **异常复用决策**：C-01 采集失败复用 `shared/errors.py` 已有的 `CollectorError` 家族（`CollectorToolNotFoundError` / `CollectorPermissionError` / `CollectorPartialError`）。这些异常类语义为"采集失败"，适用于 B-02 与 C-01 两类采集，避免异常体系膨胀。若后续需区分边界，可在 `DiagnoseError` 下新增 `ContextBuildError`，当前不引入。

### 降级采集原则

1. **单项失败不阻断整体**：单个 Tool 失败不应导致其他 Tool 结果丢弃
2. **受限信息记入 warnings**：环境受限（如容器内无 kubectl）写入 `collection_warnings`
3. **关键缺失才报错**：仅当所有 Tool 均失败时抛 `CollectorError`
4. **`_safe_collect` 包装**：每个 Tool 调用包裹 try/except，失败返回空值 + warning

### 与工作流引擎的衔接

`engine.py` 主循环已捕获 `GalaxyDiagError`（含 `CollectorError`）并展示 `e.message` + `e.hint`，保存状态后返回。`CollectorError` 携带可操作 hint：

```python
raise CollectorToolNotFoundError(
    "kubectl 未安装，无法采集容器组件状态",
    hint="请在容器环境安装 kubectl，或改用 systemctl 在裸金属/VM 环境采集",
)
```

## 安全约束设计

### 只读操作约束

COLLECTING 全部为只读操作，**不经过** `safety/review.py` 人工审核（红线2：只读/纯诊断操作无需确认）。

| 约束 | 实现 |
|------|------|
| 仅执行查询命令 | `systemctl status`/`kubectl get`/`journalctl`/`ping`/`ss`/`df` 等只读命令 |
| 禁止写命令 | 采集层不调用任何 `set`/`config`/`mod`/`restart` 类命令 |
| 命令白名单 | `tools.py` 仅调用 `collect_*` 函数，不暴露任意命令执行 |

### Prompt 注入防护（任务书 §10.3）

用户上传的日志与问题描述可能含 Prompt 注入文本。C-01 作为采集入口，需在注入 Prompt 上下文时做防护：

| 防护点 | 实现 |
|--------|------|
| 日志内容作为数据注入 | 在 `raw_output` / `log_snippets` 注入 Prompt 时用分隔标记包裹（如 `<log>...</log>`），标注为不可信数据 |
| 不影响审核关键路径 | 审核走 `safety/review.py` 硬编码 stdin，与日志内容无关（红线2 已隔离） |
| 用户描述不直接判定确认 | `problem_description` 仅作为诊断输入，不参与"是否确认执行"判定 |

## 按环境差异化采集策略

C-01 同样需按 `env_type` 差异化采集（C-01 前置依赖 B-01），但与 B-02 的硬件可见性差异不同，此处是**日志/组件可达性差异**：

| 环境类型 | 组件状态采集 | 日志采集 | 网络连通性 |
|---------|------------|---------|-----------|
| `BARE_METAL` | `systemctl status` | 文件 + `journalctl` | ping/ss/iptables |
| `VM` | `systemctl status` | 文件 + `journalctl` + dmesg | ping/ss/iptables |
| `CONTAINER`（Docker） | `docker ps -a` / `docker inspect` | `docker logs` / `/var/lib/docker/containers/*/json.log` | `docker network ls/inspect` / iptables |
| `CONTAINER`（Kubernetes） | `kubectl get pod -o wide` / `kubectl describe pod` | `kubectl logs` / `/var/log/kubelet.log` / `journalctl -u kubelet` | CNI 配置(`/etc/cni/net.d/`) / `kubectl get networkpolicy` / iptables |
| `CONTAINER`（UNKNOWN） | 尝试 Docker + K8s 两套命令（各自降级） | 同上双路尝试 | 同上双路尝试 |

> **设计要点**：容器环境的采集策略由 `env_type == CONTAINER` + `container_runtime` 双维度决定。Docker 和 Kubernetes 的命令体系完全不同，不区分则必然有一半场景采集失败。`container_runtime=UNKNOWN` 时双路尝试，确保不遗漏。

## 工作流集成设计（对齐 workflow-design.md §2）

COLLECTING 对应工作流第二步 `WorkflowStep.COLLECTING`，衔接 `ENV_RECOGNISING` 与 `DIAGNOSING`：

```
ENV_RECOGNISING (_do_env_recognising)
        │
        ▼ state.env_info 已就绪
COLLECTING (_do_collecting)
        │
        ├─ build_diagnostic_context(problem_description, env_info, user_log_files)
        │     ├─ match_tools_by_keywords()          → 待采集 Tool 集合
        │     ├─ 各 Tool 定向采集（_safe_collect 包裹）
        │     ├─ 被动接收用户日志
        │     ├─ preprocess_logs()                  → 预处理与体积控制
        │     └─ 组装 DiagnosticContext
        │
        ├─ state.diagnostic_context = ctx           # 写入 WorkflowState（持久化）
        ├─ display.print_diagnostic_context(ctx)    # 对用户可见
        │
        ├─ [逐步模式] interact.prompt_input("补充描述") + interact.confirm("是否继续?")
        │
        ▼
_transition(DIAGNOSING)
        │
        ▼ [信息不足时回退]
DIAGNOSING → COLLECTING（confidence=INSUFFICIENT，补充采集）
```

### engine.py `_do_collecting` 实现替换

当前 `_do_collecting` 为 stub（仅展示提示 + 补充描述）。实现后替换为：

```python
def _do_collecting(self) -> None:
    """COLLECTING: 诊断信息采集"""
    if not self.state.env_info:
        raise WorkflowError("缺少环境信息，请先完成环境感知步骤")

    self._console.print("[info]采集诊断信息...[/info]")
    ctx = build_diagnostic_context(
        problem_description=self.state.problem_description,
        env_info=self.state.env_info,
        user_log_files=self._user_log_files,  # 来自 CLI --log-file
    )

    self.state.diagnostic_context = ctx
    display.print_diagnostic_context(ctx)

    # 逐步模式：允许补充描述
    if not self.auto:
        supplement = interact.prompt_input("补充描述（回车跳过）", default="")
        if supplement.strip():
            self.state.problem_description += f"\n[补充] {supplement.strip()}"
            ctx.problem_description = self.state.problem_description
            self._save()
        if not interact.confirm("信息采集完成，是否继续?", default=True):
            self._console.print("[dim]工作流已暂停，可使用 --resume 恢复[/dim]")
            return

    self._transition(WorkflowStep.DIAGNOSING)
```

### 状态持久化

`DiagnosticContext` 写入 `WorkflowState.diagnostic_context` 后随 `_transition()` 立即落盘。Resume 时已采集的上下文不重复采集（DIAGNOSING 回退除外）。

### DIAGNOSING 回退补充采集

`engine.py _do_diagnosing` 已实现 `confidence=INSUFFICIENT` 时回退到 `COLLECTING`（`states.py` 已支持该转换）。回退时 `build_diagnostic_context` 应**增量采集**：读取已有 `diagnostic_context`，对 `missing_info` 指定的项补采，而非全量重采。

## 领域知识资产扩充

`shared/constants.py` 现有 `GALAXY_COMPONENTS`（4 项）与 `KEY_LOG_PATHS`（4 项）较粗糙，C-01 设计需扩充以满足"命题方领域知识预置在采集工具中"：

```python
# 扩充银河平台组件清单
GALAXY_COMPONENTS: list[str] = [
    "galaxy-compute",      # 计算服务
    "galaxy-network",      # 网络服务
    "galaxy-storage",      # 存储服务
    "galaxy-control",      # 控制面
    "galaxy-scheduler",    # 调度器
    "galaxy-api",          # API 网关
]

# 扩充关键日志路径
KEY_LOG_PATHS: dict[str, str] = {
    "system": "/var/log/syslog",
    "dmesg": "/var/log/dmesg",
    "kubelet": "/var/log/kubelet.log",
    "docker": "/var/log/docker.log",
    "galaxy-control": "/var/log/galaxy/control.log",
    "galaxy-network": "/var/log/galaxy/network.log",
    "galaxy-storage": "/var/log/galaxy/storage.log",
    "messages": "/var/log/messages",
}
```

> 实际组件名与日志路径需根据银河平台真实部署确认，此处为设计占位。

## 验收对照

| 验收标准（任务书 REQ-C-01） | 本设计落点 |
|---------------------------|-----------|
| 提供标准化信息收集入口，支持至少 2 种输入方式（主动采集 + 用户描述/日志上传） | §信息输入方式设计：主动采集（4 Tool）+ 被动接收（描述/日志上传） |
| 收集信息包含：组件部署状态、服务日志、系统资源、网络连通性 | §采集内容设计：4 个 Tool 逐类对应 |
| 原始信息经预处理后形成结构化诊断上下文，传递给分析引擎 | §预处理与体积控制设计 + `DiagnosticContext` dataclass |
| 采集失败时给出明确提示，不静默忽略 | §异常处理设计：降级 + warnings + 异常子类 |
| Agent 根据问题描述决定调用哪些采集工具（实现指引） | §关键词→Tool 映射设计 |
| 命题方领域知识预置在采集工具中（实现指引） | §领域知识资产扩充（constants.py 组件清单/日志路径） |

## 后续扩展点

- **多 Agent 演进**：`build_diagnostic_context()` 的关键词映射可被 Supervisor 拆分为 Domain Agent（NetworkAgent/StorageAgent）按故障域定向采集，状态机框架无需改动
- **trace 集成**：各 Tool 采集动作可追加 trace 记录，对接 X-04 可观测需求（`collected_tools` 字段已预留可追溯性）
- **增量采集**：DIAGNOSING 回退时按 `missing_info` 增量补采，避免全量重采
- **缓存复用**：同一 session 内 `DiagnosticContext` 已持久化，多次诊断不重复采集
