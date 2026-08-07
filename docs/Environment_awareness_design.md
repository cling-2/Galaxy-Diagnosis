# 环境感知模块设计

> 对应需求：REQ-B-01（运行环境类型自动识别）、REQ-B-02（异构软硬件信息采集）
> 实现位置：`src/galaxy_diag/collector/`（对齐 `galaxy-diag-architecture-design.md` §3 目录结构）
> 工作流集成：`WorkflowStep.ENV_RECOGNISING`（对齐 `workflow-design.md` §2 状态机）

## 模块概述

环境感知是诊断-修复闭环的第一步（`ENV_RECOGNISING`），其产出 `EnvInfo` 是后续诊断分析（`diagnoser/`）的结构化输入。本模块负责：

1. **识别银河平台部署环境类型**：裸金属（Bare Metal）、虚拟机（VM）、容器（Container）三种类型
2. **采集异构软硬件信息**：
   - 硬件基本信息：CPU 型号、内存容量、磁盘类型与容量、RAID 卡型号与固件版本、网卡型号
   - 第三方存储设备信息：存储类型（SAN / NAS / 本地）、挂载路径、文件系统类型
3. **按环境类型差异化采集**：根据 B-01 识别结果选择采集策略（裸金属采完整硬件、容器采可见信息并提示需宿主机补充）
4. **结构化输出**：转换为标准化 JSON / YAML，注入诊断上下文

### 职责边界

| 范畴 | 说明 |
|------|------|
| 本模块负责 | 环境识别、只读信息采集、结构化输出、采集失败降级与提示 |
| 本模块不负责 | 根因分析（`diagnoser/`）、修复建议生成（`fixer/`）、人工审核（`safety/`）、执行修复（`safety/`）、结果验证（`diagnoser/`） |

## 整体架构设计

### 分层架构

```
                    Diagnosis Agent (diagnoser/)
                          │
                          │ 调用 LangChain Tool
                          ▼
            ┌─────────────────────────────┐
            │   collector/tools.py        │  Tool 封装层（@tool 装饰器）
            │   - detect_environment      │  Agent 唯一调用入口
            │   - collect_hardware        │
            │   - collect_network         │
            │   - collect_storage         │
            └──────────────┬──────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                                     ▼
┌───────────────────┐              ┌─────────────────────┐
│  env_detect.py    │              │  hardware.py        │
│  EnvironmentDetector              │  HardwareCollector  │
│  (策略模式)        │              │  - CPU/MEM          │
│  ├─ ContainerDetector             │  - 磁盘/RAID        │
│  ├─ VMDetector    │              │  - 网卡             │
│  └─ BareMetalDetector             ├─────────────────────┤
└───────────────────┘              │  storage.py         │
                                   │  StorageCollector   │
                                   │  - SAN/NAS/本地检测 │
                                   └─────────────────────┘
```

### 文件职责（对齐架构文档 `collector/` 包）

| 文件 | 职责 | 对应需求 |
|------|------|---------|
| `env_detect.py` | 环境类型识别（Detector 策略模式） | REQ-B-01 |
| `hardware.py` | CPU/内存/磁盘/RAID/网卡 采集 | REQ-B-02 |
| `storage.py` | 第三方存储（SAN/NAS/本地）采集 | REQ-B-02 |
| `tools.py` | LangChain Tool 封装，Agent 唯一调用入口 | REQ-B-02 / C-01 |
| `__init__.py` | 导出 `collect_env()` 顶层编排函数 | — |

> **命名说明**：架构图中的 `ContainerDetector / VMDetector / BareMetalDetector` 是 `env_detect.py` 内部的策略类，非独立文件；`HardwareCollector / StorageCollector` 对应 `hardware.py` / `storage.py`。

### 依赖规则

- `collector/` 只依赖 `shared/`（types / constants / errors）与标准库，**不依赖** `diagnoser/`、`model/`、`workflow/`
- `tools.py` 是 Agent 与采集层的唯一边界；采集层本身不感知 LangChain
- 全部采集为**只读操作**，不调用任何写命令

## 环境识别设计（REQ-B-01）

### 支持环境类型

| 类型 | 枚举值 | 说明 | 任务书映射 |
|------|--------|------|-----------|
| 裸金属 | `EnvironmentType.BARE_METAL` | 物理服务器 | 场景 1 |
| 虚拟机 | `EnvironmentType.VM` | QEMU-KVM / VMware / Xen 等 | 场景 3 |
| 容器 | `EnvironmentType.CONTAINER` | Docker / Kubernetes 容器 | 场景 2 |

枚举定义在 `shared/types.py`，识别结果中文标签由 `shared/constants.py` 的 `ENV_TYPE_LABELS` 提供。

### 检测决策树

环境识别采用**多信号组合 + 优先级判定**，优先检测容器（嵌套场景下容器优先），再判 VM，最后兜底裸金属：

```
1. 容器检测（优先）
   ├─ /.dockerenv 存在                          → CONTAINER
   ├─ /proc/1/cgroup 含 docker/containerd/kubepods → CONTAINER
   └─ /proc/self/mountinfo 含容器运行时挂载       → CONTAINER

2. VM 检测
   ├─ systemd-detect-virt 可用
   │   输出含 vmware/kvm/qemu/xen/virtualbox    → VM
   │   输出 none 或不存在                        → 进入 DMI 兜底
   └─ DMI 兜底（无 systemd 环境）
       /sys/class/dmi/id/product_name 含 VM 厂商特征 → VM

3. 兜底
   └─ 以上均不命中                              → BARE_METAL
```

### 各 Detector 检测信号明细

| Detector | 检测信号 | 命令/文件 | 命中判定 |
|----------|---------|----------|---------|
| `ContainerDetector` | dockerenv | `os.path.exists("/.dockerenv")` | 文件存在 |
| | cgroup | 读取 `/proc/1/cgroup` | 含 `docker`/`containerd`/`kubepods` |
| | mountinfo | 读取 `/proc/self/mountinfo` | 含容器运行时 overlay 挂载 |
| `VMDetector` | systemd-detect-virt | `systemd-detect-virt` 命令输出 | 返回非 `none` 的虚拟化类型 |
| | DMI 产品名 | `/sys/class/dmi/id/product_name` | 含 `VMware`/`VirtualBox`/`KVM`/`QEMU`/`Xen` |
| | SCSI 厂商 | `/sys/class/scsi_disk/*/device/vendor` | 含 `VMware`/`QEMU` |
| `BareMetalDetector` | 上述均不命中 | — | 兜底返回 |

### 关键设计：嵌套环境与优先级

容器内可能看到 VM 特征（如在 VM 上跑容器），此时**容器优先**：容器内采集策略本就受限，误判为 VM 会触发宿主机硬件采集命令（多数在容器内失效或返回误导信息）。优先级链：

```
CONTAINER > VM > BARE_METAL
```

容器检测命中即终止，不再判断 VM。

### 降级方案

| 场景 | 降级策略 |
|------|---------|
| `systemd-detect-virt` 未安装 | 回退到 DMI 产品名判断 |
| 无 root 权限读 DMI | 回退到 SCSI 厂商特征判断 |
| 所有 VM 信号均不可用 | 记录 warning，兜底为 `BARE_METAL`（保守判定，避免误判 VM 后采集失败） |

### 识别结果对用户可见（验收标准）

识别完成后通过 `display.print_env_info()` 输出，其中首行声明环境类型：

```
🔍 环境识别结果
  环境类型: 虚拟机
```

对应任务书 REQ-B-01 验收标准 "输出中声明当前环境为 XXX"。

## 信息采集设计（REQ-B-02）

### Collector 清单

| Collector | 采集内容 | 采集方式 | 依赖命令/文件 | 环境差异 |
|-----------|---------|---------|--------------|---------|
| **HardwareCollector** | CPU 型号、核数 | `/proc/cpuinfo` | 无（纯文件读取） | 全环境通用 |
| | 内存容量 | `/proc/meminfo` | 无 | 全环境通用 |
| | 磁盘类型/容量/型号 | `lsblk -o NAME,TYPE,SIZE,MODEL` | `lsblk` | 容器仅见挂载盘 |
| | RAID 卡型号/固件版本 | `storcli64` / `megacli` / `lspci` | 任一可用 | **容器/VM 通常不可见** |
| | 网卡型号/驱动 | `lspci` + `/sys/class/net/*/device/driver` | `lspci` | 容器见 veth，非宿主网卡 |
| **StorageCollector** | SAN 存储（iSCSI/FC） | `iscsiadm -m session` / `multipath -ll` | `iscsiadm`/`multipath` | 容器内通常不可见 |
| | NAS 存储（NFS/SMB） | `findmnt -t nfs,nfs4,cifs` | `findmnt` | 全环境通用 |
| | 本地存储 | `findmnt` + `lsblk` | `findmnt`/`lsblk` | 全环境通用 |
| | 文件系统类型 | `findmnt -o TARGET,FSTYPE` | `findmnt` | 全环境通用 |

### 按环境差异化采集策略

任务书 REQ-B-02 实现指引要求："裸金属采集完整硬件信息，容器环境采集容器可见信息并提示可能需要宿主机信息补充"。

| 环境类型 | 采集范围 | 受限项 | 处理 |
|---------|---------|--------|------|
| `BARE_METAL` | 全量硬件 + 全量存储 | 无 | 正常采集 |
| `VM` | 虚拟化硬件 + 存储 | RAID 卡可能透传不可见 | 受限项记入 `collection_warnings` |
| `CONTAINER` | 容器可见信息 | 宿主硬件/RAID/物理网卡/部分存储不可见 | **强烈提示需宿主机补充**，记入 `collection_warnings` |

容器环境采集受限时，`EnvInfo.collection_warnings` 写入明确提示，例如：

```
["容器环境无法直接采集宿主机硬件信息（CPU/RAID/物理网卡），建议在宿主机上执行 galaxy-diag env 补充"]
```

### 存储类型判定逻辑

第三方存储是客户痛点场景的核心输入（任务书场景 C），`StorageCollector` 按挂载文件系统区分 SAN/NAS/本地：

| 文件系统/协议 | 判定方式 | storage_type |
|--------------|---------|-------------|
| NFS / NFSv4 | `findmnt -t nfs,nfs4` 命中 | `NAS` |
| CIFS / SMB | `findmnt -t cifs` 命中 | `NAS` |
| iSCSI | `iscsiadm -m session` 有活动会话 | `SAN` |
| FC / FCoE | `multipath -ll` 含多路径设备 | `SAN` |
| ext4 / xfs 等本地 | 上述均不命中 | `local` |

每条 `StorageInfo` 包含挂载路径、文件系统类型，`details` 扩展字段存储协议细节（如 iSCSI target、NFS server）。

## Tool 接口设计

`collector/tools.py` 用 LangChain `@tool` 装饰器封装，作为 Agent 唯一调用入口。Agent 调用顺序：先 `detect_environment`，再根据 `env_type` 选择性调用采集工具。

| Tool 名称 | 输入参数 | 输出 | 说明 |
|-----------|---------|------|------|
| `detect_environment` | 无 | `EnvironmentType`（str） | 总是第一个调用，返回环境类型 |
| `collect_hardware` | `env_type: EnvironmentType` | `HardwareInfo`（JSON 序列化） | 根据 env_type 调整采集策略 |
| `collect_network` | `env_type: EnvironmentType` | `dict`（网卡+容器网络信息） | 容器环境额外采集 CNI/挂载点 |
| `collect_storage` | `env_type: EnvironmentType` | `list[StorageInfo]`（JSON 序列化） | SAN/NAS/本地存储检测 |

### 顶层编排函数

`__init__.py` 暴露 `collect_env()` 供工作流引擎直接调用（非 Agent 路径），编排识别 + 全量采集：

```python
def collect_env() -> EnvInfo:
    """环境感知顶层编排：识别环境类型 → 采集软硬件 → 组装 EnvInfo"""
    env_type = EnvironmentDetector().detect()           # env_detect.py
    hardware = HardwareCollector().collect(env_type)    # hardware.py
    storage = StorageCollector().collect(env_type)      # storage.py
    warnings = build_collection_warnings(env_type, hardware, storage)
    return EnvInfo(
        env_type=env_type,
        hardware=hardware,
        storage=storage,
        collection_warnings=warnings,
        raw_output={...},  # 各 Collector 原始输出汇总（供 LLM 上下文）
    )
```

### Tool 与编排的关系

- **Agent 路径**：Agent 通过 `tools.py` 的 4 个 Tool 自主决定调用哪些（如仅需网络诊断时只调 `collect_network`）
- **工作流路径**：`WorkflowEngine._do_env_recognising()` 调用 `collect_env()` 一次性采集全部，结果写入 `WorkflowState.env_info`

## 数据结构设计

数据结构定义在 `shared/types.py`，本节明确各字段约束与采集映射。

### EnvInfo（collector → diagnoser 契约）

```python
@dataclass
class EnvInfo:
    env_type: EnvironmentType             # B-01 识别结果
    hardware: HardwareInfo                # 硬件采集结果
    storage: list[StorageInfo]            # 第三方存储列表
    collection_warnings: list[str]        # 采集受限/降级提示（新增）
    raw_output: dict                      # 原始采集数据汇总（供 LLM 上下文）
```

> **变更说明**：相对原 `types.py`，新增 `collection_warnings` 字段，承载容器/VM 环境采集受限的明确提示，对齐任务书 "容器环境采集容器可见信息并提示可能需要宿主机信息补充" 要求。

### HardwareInfo

建议将三个 `list[dict]` 升级为 typed dataclass，避免拼写错误与字段遗漏：

```python
@dataclass
class DiskInfo:
    type: str = ""           # SSD / HDD / NVMe
    capacity: str = ""       # 如 "500GB"
    model: str = ""          # 设备型号

@dataclass
class RaidCardInfo:
    model: str = ""                # RAID 卡型号
    firmware_version: str = ""     # 固件版本（任务书明确要求）

@dataclass
class NicInfo:
    model: str = ""     # 网卡型号
    driver: str = ""    # 驱动模块

@dataclass
class HardwareInfo:
    cpu_model: str = ""
    cpu_cores: int = 0
    memory_total_gb: float = 0.0
    disks: list[DiskInfo] = field(default_factory=list)
    raid_cards: list[RaidCardInfo] = field(default_factory=list)
    nics: list[NicInfo] = field(default_factory=list)
```

> **兼容性**：`display.py` 当前按 `dict.get('model')` 等方式读取，升级为 dataclass 后需同步调整 `print_env_info()` 改为属性访问。若短期不升级，至少在本设计文档中明确每个 `dict` 的键约束（见上表）。

### StorageInfo

```python
@dataclass
class StorageInfo:
    storage_type: Literal["SAN", "NAS", "local"] = "local"
    mount_path: str = ""          # 挂载路径
    filesystem: str = ""          # 文件系统类型（nfs4/cifs/ext4...）
    details: dict = field(default_factory=dict)
    # details 示例：
    #   NAS: {"server": "nas-01.internal", "export": "/data"}
    #   SAN: {"target": "iqn.2026...", "session": "1"}
    #   local: {}
```

### raw_output 用途与约束

| 属性 | 说明 |
|------|------|
| 内容 | 各 Collector 原始命令输出摘要（非全量，避免上下文过大） |
| 用途 | 注入诊断 LLM Prompt，供推理时回溯原始细节（LLM 的证据链） |
| 截断 | 单条原始输出超过阈值（如 2KB）截断并标注 `[truncated]` |
| 与结构化字段关系 | 结构化字段（hardware/storage）是 raw_output 的提取子集；raw_output 是超集 |

## Agent 调用流程设计

### 工作流集成（对齐 workflow-design.md §2）

环境感知对应工作流第一步 `WorkflowStep.ENV_RECOGNISING`：

```
WorkflowEngine.run()
        │
        ▼
ENV_RECOGNISING (_do_env_recognising)
        │
        ├─ collector.collect_env()
        │     ├─ EnvironmentDetector.detect()      → env_type
        │     ├─ HardwareCollector.collect()       → hardware
        │     ├─ StorageCollector.collect()        → storage
        │     └─ 组装 EnvInfo + collection_warnings
        │
        ├─ state.env_info = env_info               # 写入 WorkflowState
        ├─ display.print_env_info(env_info)        # 对用户可见（声明环境类型）
        │
        ├─ [逐步模式] interact.confirm("环境识别完成，是否继续?")
        │
        ▼
_transition(COLLECTING)
```

### 两种调用路径

| 路径 | 调用方 | 入口 | 场景 |
|------|--------|------|------|
| 工作流编排 | `WorkflowEngine._do_env_recognising()` | `collect_env()` | `galaxy-diag run` 端到端流程 |
| Agent 自主 | Diagnosis Agent | `tools.py` 的 4 个 Tool | Agent 按需选择性采集（如只诊断网络） |
| 独立命令 | `galaxy-diag env` | `collect_env()` | 单独执行环境采集（当前为 stub） |

### 状态持久化

`EnvInfo` 写入 `WorkflowState.env_info` 后随 `_transition()` 立即落盘（`~/.galaxy-diag/sessions/<session_id>.json`）。Resume 时已采集的环境信息不重复采集。

## 异常处理设计

任务书红线 "错误处理不能吞"：采集失败不静默忽略，但也不全盘失败——采用**降级采集 + 部分成功 + 明确提示**策略。

### 异常分类与处理

| 异常场景 | 异常类型（`shared/errors.py`） | 处理策略 | 状态影响 |
|---------|-------------------------------|---------|---------|
| 权限不足（如非 root 读 DMI） | `CollectorPermissionError` | 降级到无需 root 的信号，记 warning | 部分采集，流程继续 |
| 采集命令不存在（如 `lshw` 未装） | `CollectorToolNotFoundError` | 跳过该项，回退替代命令，记 warning | 部分采集，流程继续 |
| 部分采集成功、部分失败 | `CollectorPartialError` | 返回已成功部分 + `collection_warnings` | 部分采集，流程继续 |
| 整体采集失败（如环境识别完全无法判定） | `CollectorError` | 抛出异常，由 engine 捕获并提示 | 流程暂停，可 resume |

> **新增异常子类**（建议补充到 `shared/errors.py`）：
> ```python
> class CollectorPermissionError(CollectorError): """采集权限不足"""
> class CollectorPartialError(CollectorError): """部分采集失败"""
> class CollectorToolNotFoundError(CollectorError): """采集工具未安装"""
> ```

### 降级采集原则

1. **单项失败不阻断整体**：RAID 卡采集失败不应导致 CPU/内存采集丢弃
2. **受限信息记入 warnings**：容器内不可见的硬件信息写入 `collection_warnings`，而非报错
3. **关键缺失才报错**：仅当环境类型完全无法判定时抛 `CollectorError`

### 与工作流引擎的衔接

`engine.py` 主循环已捕获 `GalaxyDiagError`（含 `CollectorError`）并展示 `e.message` + `e.hint`，保存状态后返回。`CollectorError` 应携带可操作 hint，例如：

```python
raise CollectorPermissionError(
    "无法读取 DMI 信息识别 VM 类型",
    hint="请以 root 权限运行 galaxy-diag，或在宿主机上执行环境采集",
)
```

## 安全约束设计

### 只读操作约束（任务书 REQ-B-02 验收标准）

环境感知过程**不得修改生产系统状态**，全部为只读操作。

| 约束 | 实现 |
|------|------|
| 仅读取系统文件 | `/proc/*`、`/sys/*` 等文件读取 |
| 仅执行查询命令 | `lsblk`/`lspci`/`findmnt`/`iscsiadm -m session`（查询模式，无副作用） |
| 禁止写命令 | 采集层不调用任何 `set`/`config`/`mod`/`restart` 类命令 |
| 命令白名单 | `tools.py` 仅调用 `collect_*` 函数，不暴露任意命令执行 |

### 权限设计

| 采集项 | 是否需 root | 非 root 降级 |
|--------|------------|-------------|
| `/proc/cpuinfo`、`/proc/meminfo` | 否 | — |
| `lsblk` / `lspci` / `findmnt` | 否 | — |
| `/sys/class/dmi/*`（DMI 识别） | 是 | 回退 SCSI 厂商特征 |
| `storcli64` / `megacli`（RAID） | 是 | 记 warning，标记不可见 |
| `iscsiadm -m session` | 是 | 记 warning，跳过 SAN 检测 |

非 root 运行时，将权限受限项统一记入 `collection_warnings`，并在输出中提示：

```
⚠ 以下信息需 root 权限采集，当前以非 root 运行已跳过：
  - RAID 卡型号与固件版本
  - iSCSI 会话（SAN 存储）
建议以 sudo 重新执行 galaxy-diag env
```

### 与安全模块的关系

环境感知是只读操作，**不经过** `safety/review.py` 人工审核（红线 2：只读/纯诊断操作无需确认）。其产出 `EnvInfo` 仅作为诊断输入，不触发任何写操作。

## 验收对照

| 验收标准（任务书） | 本设计落点 |
|------------------|-----------|
| 自动识别 VM、容器三种环境类型 | §环境识别设计 检测决策树 + 三 Detector |
| 识别结果对用户可见 | `display.print_env_info()` 首行声明环境类型 |
| 识别逻辑在三种环境中均能正确执行 | §降级方案 覆盖无 systemd / 无 root 场景 |
| 采集硬件基本信息（CPU/内存/磁盘/RAID/网卡） | §信息采集设计 HardwareCollector |
| 采集第三方存储（SAN/NAS/本地、挂载路径、文件系统） | §信息采集设计 StorageCollector + 存储类型判定 |
| 结构化输出（JSON/YAML） | `EnvInfo` dataclass + `--output json/yaml`（`cmd_env.py`） |
| 采集过程不影响生产系统（只读） | §安全约束设计 只读 + 命令白名单 |
| 采集失败不静默忽略 | §异常处理设计 降级 + warnings + 异常子类 |
| 容器环境采集可见信息并提示需宿主机补充 | §按环境差异化采集策略 + `collection_warnings` |

## 后续扩展点

- **多 Agent 演进**：`collect_env()` 编排可被 Supervisor 拆分为 Domain Agent（NetworkAgent/StorageAgent）按故障域选择性采集，状态机框架无需改动
- **trace 集成**：各 Collector 采集动作可追加 trace 记录，对接 X-04 可观测需求
- **缓存复用**：同一 session 内 `EnvInfo` 已持久化，多次诊断不重复采集
