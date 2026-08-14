# 安全可控模块设计 (REQ-E-01 ~ E-04, REQ-F-03)

> 银河平台部署问题定位工具 — 安全可控模块详细设计
> 覆盖范围：REQ-E-01（人工审核强制拦截）、REQ-E-02（危险操作多维防护）、REQ-E-03（操作快照与一键回滚）、REQ-E-04（操作留痕与审计日志）、REQ-F-03（审核确认交互流程）、红线 2（生产环境写操作必须人工显式确认）
> 本文档与 `Fix_Generation_design.md` 互补：D-03 解决"LLM 写得对不对"，E-02 解决"用户该不该执行"

## 模块概述

### 职责边界

safety/ 模块是红线 2 的物理实现层。任务书红线 2 要求："任何修改生产环境的操作执行前必须有人类显式确认，确认必须通过专用交互流程完成，不得由 LLM 解析用户自然语言来判定'确认'"。本模块承载四条安全关键路径，**全部不经 LLM**：

1. **危险操作拦截**（`danger.py`）— 正则硬编码匹配，命中即进入人工审核要求 CONFIRM
2. **人工审核确认**（`review.py` + `review_ui.py`）— stdin `[y/N]` 专用通道
3. **操作快照与回滚**（`snapshot.py`）— 执行前自动备份，失败可回退
4. **审计日志**（`audit.py`）— 专用函数写入 JSONL，不经 Agent 输出流

safety 模块只接收"待审核的修复建议"作为输入，**不调用任何 LLM 推理**，不依赖 model/diagnoser/fixer 包。这是架构设计 §6 强调的"安全层独立旁路"原则的落地。

### 与 fixer D-03 的分工

fixer 模块的 D-03 多维错误检测（`fixer/checker.py`）与 safety 模块的 E-02 执行前熔断（`danger.py`）构成**纵深防御**，两者目标不同、不可互相替代：

| 维度 | D-03 生成后检测 (fixer/checker.py) | E-02 执行前熔断 (safety/danger.py) |
|------|-----------------------------------|-------------------------------------|
| 防护阶段 | 代码生成后、用户确认前 | 用户确认前、执行前 |
| 核心目标 | 确保 LLM 产出的代码正确、可用 | 防止高风险操作被误执行 |
| 性质 | 质量保障（建议性） | 安全兜底（强制性） |
| CRITICAL 处理 | 回退 PLANNING 重新生成 | 要求输入 CONFIRM（输入不匹配则终止） |
| WARNING 处理 | 提醒用户，允许继续 | 要求输入 CONFIRM 确认 |

> D-03 解决"LLM 写得对不对"，E-02 解决"用户该不该执行"。用户编辑后可能引入新风险（D-03 检测不到），某些操作在特定环境上下文中才危险（D-03 静态匹配无法感知），两层缺一不可。

### 四大组件总览

| 组件 | 文件 | 职责 | 对应需求 | 不经 LLM 的实现方式 |
|------|------|------|---------|-------------------|
| 危险模式库 | `patterns.py` | 危险命令正则模式数据定义 | E-02 | 纯数据，无逻辑 |
| 危险防护 | `danger.py` | 危险命令匹配 + 变量展开 + 影响评估 | E-02 | 正则 + 硬编码算法 |
| 人工审核 | `review.py` + `review_ui.py` | 操作摘要展示 + 确认/拒绝/修改 | E-01, F-03 | stdin 输入 |
| 快照回滚 | `snapshot.py` | 执行前备份 + 一键回滚 | E-03 | 文件系统操作 |
| 审计日志 | `audit.py` | 操作留痕 JSONL | E-04 | 专用函数写文件 |

## 整体架构

### 双通道架构

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

上图与架构设计 §6.1 双通道架构一致。LLM 路径只产出 `FixProposal`（建议），safety 路径独立完成拦截→审核→快照→留痕，两条路径在 `FixProposal` 处交接，之后不再交叉。

### 文件职责

| 文件 | 核心职责 | 禁止事项 | 关键导出 |
|------|---------|---------|---------|
| `__init__.py` | 包导出 | — | 顶层函数 |
| `patterns.py` | 危险命令模式库数据定义 | 不含匹配逻辑 | `DANGER_PATTERNS` |
| `danger.py` | 危险操作多维防护逻辑 | 不调用 LLM | `execution_guard_check()` |
| `review.py` | 审核确认逻辑（确认/拒绝/修改判定） | 确认不经 LLM | `review_confirm()` |
| `review_ui.py` | 审核交互界面（操作摘要展示 + 输入） | 位于 workflow/cli/，调用 interact.py | `render_summary()`, `prompt_choice()` |
| `snapshot.py` | 快照创建与回滚 | 不修改 FixProposal | `create_snapshot()`, `rollback()` |
| `audit.py` | 审计日志写入与查询 | 不经 Agent 输出流 | `write_audit()`, `query_audit()` |

> `review_ui.py` 物理上位于 `workflow/cli/`（属 CLI 层），但逻辑归属安全模块。它只负责渲染和收集 stdin 输入，确认判定逻辑在 `safety/review.py` 中。

### 依赖规则

```
safety/ ──→ shared (types, constants, errors)   # 唯一依赖
       ✗ 不依赖 model/      # 不调用 LLM
       ✗ 不依赖 diagnoser/  # 不参与推理
       ✗ 不依赖 fixer/      # 只接收 FixProposal 作为输入
```

`workflow/engine.py` 是唯一同时依赖 fixer 和 safety 的地方，负责将 fixer 产出的 `FixProposal` 传递给 safety 审核组件。

### 数据流

```
FixProposal (fixer 产出)
    │
    ▼
danger.execution_guard_check()  ──→  GuardResult (pass/warning/critical + impact_summary)
    │
    ▼
review.review_confirm(proposal, guard_result)  ──→  ReviewDecision (yes/no/edit)
    │  （通过 review_ui.py 收集 stdin 输入）
    │
    ├─ yes ──→ audit.write_audit(result=confirmed, user_input="y/CONFIRM")
    │              │
    │              ▼
    │          snapshot.create_snapshot()  ──→  SnapshotMeta
    │              │
    │              ▼
    │          executor.run()  ──→  ExecuteResult (success/failure)
    │              │
    │              ├─ success ──→ audit.write_audit(result=success)
    │              └─ failure ──→ snapshot.rollback() ──→ audit.write_audit(result=rollback)
    │
    ├─ no  ──→ audit.write_audit(result=rejected)  ──→  终止
    │
    └─ edit ──→ 回到 fixer SECURITY_CHECKING（编辑后重走 D-03）
```

每一步的输入/输出类型化、可静态检查，对齐 `shared/types.py` 契约。

## 数据结构设计

### 新增类型概览

本模块新增以下类型，建议放入 `shared/types.py`（跨域契约集中管理）：

| 类型 | 用途 | 归属 |
|------|------|------|
| `DangerPattern` | 危险模式库条目 | shared/types.py |
| `GuardResult` | E-02 熔断结果 | shared/types.py |
| `ReviewDecision` | 审核决定（枚举） | shared/types.py |
| `RollbackResult` | 回滚结果 | shared/types.py |

已有类型 `SnapshotMeta`、`AuditRecord`、`CheckSeverity` 无需变更（字段已满足需求）。

### DangerPattern

危险模式库的单条模式定义：

```python
@dataclass
class DangerPattern:
    """危险命令模式条目"""
    pattern: str                      # 正则表达式
    category: str                     # 类别：data_loss / privilege / network / system
    severity: CheckSeverity           # 严重级别（CRITICAL/WARNING 均要求 CONFIRM 确认）
    description: str                  # 风险说明（展示给用户）
    suggestion: str                   # 建议操作（展示给用户）
```

> `severity` 复用已有 `CheckSeverity` 枚举（CRITICAL/WARNING/INFO），避免引入新的严重级别体系。

### GuardResult

E-02 熔断检查的返回值，传递给 review 组件决定是否要求 CONFIRM：

```python
@dataclass
class GuardResult:
    """执行前熔断结果"""
    level: Literal["pass", "warning", "critical"]  # 熔断级别
    matched_patterns: list[DangerPattern]            # 命中的危险模式
    impact_summary: str                           # 影响范围一句话汇总（如"影响 3 个挂载点、2 个服务"）
    message: str                                     # 汇总提示信息
```

`level` 含义：
- `pass` — 无危险模式命中，正常进入审核
- `warning` — 命中 WARNING 级模式，审核时要求输入 CONFIRM
- `critical` — 命中 CRITICAL 级模式，审核时要求输入 CONFIRM，输入不匹配则终止

> 影响范围不单独定义结构化类型。`danger.py` 影响范围评估内部可临时收集 files/services 列表用于计数和生成 summary，但这是函数内局部逻辑，不暴露成跨域类型。评估结果写入 `GuardResult.impact_summary`（字符串）和 `FixProposal.impact_scope`（已有 str 字段），供 `review_ui` 展示即可。

### 现有类型变更

- `SnapshotMeta`：当前字段（snapshot_id / timestamp / operation_summary / affected_files / affected_services / backup_path）已满足 REQ-E-03，无需变更。
- `AuditRecord`：字段无需变更，仅 `result` 枚举新增 `confirmed` 值（用户确认执行时记录，与 `rejected` 对称），用于在审核同意后、执行前留痕。
- `FixProposal.impact_scope`：当前为 `str` 类型，E-02 评估时填充该字段（一句话汇总）供 review 展示；`GuardResult.impact_summary` 同样为字符串，两者内容可一致，由 `danger.py` 统一产出。

### 数据流转全景

```
[fixer] FixProposal
    │
    ├─ commands: list[CommandTemplate]  ──┐
    ├─ script: str | None                 ├─→ danger.execution_guard_check()
    └─ script_language                    │       │
                                          │       ├─ 正则匹配 patterns.DANGER_PATTERNS
                                          │       ├─ 变量展开检测
                                          │       └─ 影响范围评估
                                          │       │
                                          │       ▼ GuardResult
                                          │       │
[safety] review.review_confirm ◄──────────┘
    │   （review_ui 渲染摘要 + 收集 stdin）
    │
    ├─ ReviewDecision.YES   → audit.write_audit(result=confirmed) → snapshot.create_snapshot → SnapshotMeta
    ├─ ReviewDecision.NO    → audit.write_audit(result=rejected)
    └─ ReviewDecision.EDIT  → 回 fixer SECURITY_CHECKING
```

## 危险命令模式库设计 (patterns.py) — REQ-E-02 数据层

### 设计定位

`patterns.py` 只定义数据（危险命令正则模式列表），不含匹配逻辑。这样安全/SRE 团队可以独立维护模式库（新增危险命令模式），无需修改 `danger.py` 的匹配算法。符合架构设计"数据与逻辑分离"原则。

### 模式分类

按风险类型分四类，与任务书 REQ-E-02 验收标准列出的危险命令一致：

| 类别 | category 值 | 典型模式 | 默认 severity |
|------|------------|---------|--------------|
| 数据破坏 | `data_loss` | `rm -rf`、`mkfs`、`dd of=/dev/` | CRITICAL |
| 权限变更 | `privilege` | `chmod 777`、`chown -R /` | WARNING |
| 网络安全 | `network` | `iptables -F`、`iptables -X` | CRITICAL |
| 系统关键 | `system` | 停止 sshd/docker/kubelet | WARNING |

### 模式条目结构

```python
# safety/patterns.py（示意）
DANGER_PATTERNS: list[DangerPattern] = [
    DangerPattern(
        pattern=r"rm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+|.*--no-preserve-root)",
        category="data_loss",
        severity=CheckSeverity.CRITICAL,
        description="危险删除（rm -rf 或 --no-preserve-root）",
        suggestion="请确认删除目标路径，避免误删根目录或系统文件",
    ),
    # ... mkfs / dd / iptables -F / systemctl stop sshd 等
]
```

### 与 D-03 危险模式的互补关系

fixer D-03 的 `fixer/checker.py` 中也维护一组危险模式（`DANGER_PATTERNS_ADVISORY`），两者定位不同、互不重复：

| | D-03 (fixer/checker.py) | E-02 (safety/patterns.py) |
|--|------------------------|--------------------------|
| 性质 | 通用代码质量提醒 | 业务安全策略强制拦截 |
| severity | WARNING（建议性，不阻止） | CRITICAL/WARNING（均要求 CONFIRM 确认） |
| 维护方 | 研发/工具链团队 | 安全/SRE 团队 |
| 目的 | 信息展示和教育 | 安全兜底 |

> D-03 提醒"这条命令有风险"，E-02 拦截"这条命令不能执行"。即使 D-03 漏检，E-02 仍会拦截。

### 模式库扩展机制

当前阶段硬编码在 `patterns.py` 的 `DANGER_PATTERNS` 列表中。预留从 YAML 文件加载的扩展点（见 §后续扩展点），加载接口保持 `list[DangerPattern]` 返回类型不变，`danger.py` 无需改动。

## 危险操作多维防护设计 (danger.py) — REQ-E-02 逻辑层

### 检测维度

E-02 执行前熔断包含三个检测维度，缺一不可：

1. **危险命令正则匹配** — 直接匹配命令/脚本中的危险模式
2. **变量展开检测** — 防止危险命令被拆成变量绕过正则匹配
3. **影响范围评估** — 评估操作将影响哪些文件/服务/挂载点

### 危险命令正则匹配

遍历 `FixProposal.commands` 中每条命令和 `FixProposal.script`（如有），逐条用 `patterns.DANGER_PATTERNS` 中的正则匹配。命中的模式收集到 `matched_patterns`，取最高 severity 作为 `GuardResult.level`。

### 变量展开检测算法

防止 `CMD="rm -rf"; $CMD /` 这类绕过。检测思路（伪代码）：

1. 扫描脚本中的变量赋值语句（`VAR=value` 形式）
2. 提取变量值，用危险模式正则匹配变量值本身
3. 若变量值含危险片段，标记该变量为"危险变量"
4. 扫描脚本中对该变量的引用（`$VAR`），将展开后的完整命令再过一次正则匹配
5. 任何一环命中，加入 `matched_patterns`

> 变量展开检测是 E-02 区别于 D-03 静态匹配的关键能力。D-03 做文本级静态匹配，E-02 做语义级变量追踪。

### 影响范围评估

预分析命令/脚本涉及的服务名、路径、挂载点，基于 `shared/constants.py` 中的领域知识（`GALAXY_COMPONENTS`、`KEY_LOG_PATHS`）：

1. 提取命令中涉及的文件路径（`/etc/...`、`/var/...`、挂载点路径）
2. 提取涉及的服务名（`systemctl`/`docker`/`kubectl` 后跟的服务名）
3. 与 `GALAXY_COMPONENTS` 交叉，标注受影响的银河平台组件
4. 生成一句话汇总字符串（如"影响 3 个挂载点、2 个运行中的服务"），写入 `GuardResult.impact_summary` 和 `FixProposal.impact_scope`

以上 files/services 列表的收集和计数是 `danger.py` 的**函数内局部逻辑**，不暴露成跨域类型。最终只产出 summary 字符串供展示。

### 熔断结果分级策略

| 命中情况 | GuardResult.level | 后续行为 |
|---------|------------------|---------|
| 无命中 | `pass` | 正常进入 REVIEWING，普通 `[y/N]` 确认 |
| 仅命中 WARNING 级 | `warning` | 进入 REVIEWING，要求输入 CONFIRM 确认 |
| 命中 CRITICAL 级 | `critical` | 进入 REVIEWING，要求输入 CONFIRM 确认；输入不匹配则终止 |

> 对齐 `Workflow-design.md` §2.4：EXECUTION_GUARD 无论 pass/warning/critical 均进入 REVIEWING，由 REVIEWING 根据 `GuardResult.level` 决定是否要求 CONFIRM。CRITICAL 不直接终止，而是给用户一次 CONFIRM 确认的机会；用户输入不匹配则终止工作流。

### 顶层接口 execution_guard_check()

```python
def execution_guard_check(
    proposal: FixProposal,
    env_type: EnvironmentType,
) -> GuardResult:
    """执行前熔断检查 (E-02)

    Args:
        proposal: 待执行的修复建议
        env_type: 当前环境类型（影响范围评估需要）

    Returns:
        GuardResult: 熔断结果，含 level / matched_patterns / impact_summary

    不经 LLM，纯硬编码正则 + 算法。
    """
```

### 与 engine.py _do_execution_guard 的集成

当前 `engine.py` 的 `_do_execution_guard` 是 stub（直接返回 `pass`）。替换方案：

1. 调用 `execution_guard_check(self.state.fix, self.state.env_info.env_type)` 获取 `GuardResult`
2. 将 `GuardResult` 存入实例变量 `self._guard_result`（当前为字符串 `"pass"`，替换为 `GuardResult` 对象）
3. 渲染熔断结果给用户（通过 display.py）
4. 转换到 REVIEWING

`_guard_result` 在 EXECUTION_GUARD → REVIEWING 间传递，供 `_do_reviewing` 决定是否要求 CONFIRM。对齐 `Workflow-design.md` §5.3 隐藏子步骤的用户感知：EXECUTION_GUARD 归属用户步骤 5/7"人工审核"，不打印独立步骤标题。

## 人工审核强制拦截设计 (review.py + review_ui.py) — REQ-E-01 + REQ-F-03

### 双通道隔离

确认输入走 stdin，不经过 LLM 通道。这是防 Prompt 注入的核心防线：即使日志内容或用户描述中嵌入"用户已确认执行"等恶意文本，LLM 也无法控制 stdin 输入。

对齐架构设计 §6.2 安全关卡详细设计：审核确认的实现方式是 `review.py` 读 stdin 的 `[y/N]`，不经过 LangChain 的任何回调。

### 普通确认流程

`GuardResult.level == "pass"` 时使用普通确认：

- 提示：`确认执行? [y/N]:`
- 确认：输入 `y` 或 `Y`
- 拒绝：回车 / `n` / 任意非 y
- 默认拒绝（安全优先）

调用 `interact.confirm(prompt, default=False, danger=False)`。

### 危险操作二次确认

`GuardResult.level` 为 `warning` 或 `critical` 时，要求输入 `CONFIRM` 全称确认（任务书 REQ-E-02 验收标准第 4 条、REQ-F-03 验收标准第 4 条）：

- 红色提示：`此操作需要额外确认，请输入 CONFIRM 以继续`
- 确认：输入 `CONFIRM`（全大写，精确匹配）
- 拒绝：任意其他输入 → 终止工作流

对齐已实现的 `engine.py` `_do_reviewing` 中的 CONFIRM 逻辑（当前直接 `input()` 读取，后续可收敛到 `interact.confirm(danger=True)`）。

### 操作摘要展示

执行前展示操作摘要，对齐任务书 REQ-F-03 验收标准第 1 条。摘要包含：

- **将执行什么**：修复命令列表（编号 + 命令 + 说明）
- **影响什么**：`GuardResult.impact_summary` 渲染（受影响文件/服务/挂载点汇总）
- **风险等级**：基于 `GuardResult.level`（pass=低 / warning=中 / critical=高）
- **回滚方案**：快照创建后展示快照 ID 和回滚命令

使用 `display.py` 的 `Panel`/`Table` 渲染，对齐 `CLI_Framework_design.md` §4.2 输出格式设计。

### 三种用户操作的状态转换

审核菜单提供确认/拒绝/修改（及删除/重排）操作，对齐 `Workflow-design.md` §2.4 转换规则：

| 用户输入 | ReviewDecision | 状态转换 | 审计记录 |
|---------|---------------|---------|---------|
| `y` | YES | → SNAPSHOT → EXECUTING | result=confirmed（确认时）+ result=success/failure/rollback（执行后） |
| `n` | NO | → 终止（REVIEWING_NEXT_ON_REJECT） | result=rejected |
| `e` | EDIT | → SECURITY_CHECKING（编辑后重走 D-03） | 记录编辑操作 |
| `d` | — | 删除步骤后重新展示 | 记录删除操作 |
| `r` | — | 重排步骤后重新展示 | 记录重排操作 |

### 拒绝不反复要求确认

红线 2 明确："用户拒绝时不执行且不反复要求确认"。用户输入 `n` 后：

1. 记录审计日志（`result=rejected`，`user_input="n"`）
2. 标记工作流终止（`_mark_rejected()`）
3. 直接退出，不再次弹出确认提示

### 与 engine.py _do_reviewing 的集成对接

当前 `engine.py` 的 `_do_reviewing` 已实现交互菜单（y/n/e/d/r）和 CONFIRM 逻辑。后续替换方案：

- 操作摘要渲染收敛到 `review_ui.render_summary(proposal, guard_result)`
- CONFIRM 判定收敛到 `review.review_confirm(proposal, guard_result)` 返回 `ReviewDecision`
- `engine.py` 只负责根据 `ReviewDecision` 做状态转换

> F-03 的完整交互细节（渲染、输入收集）在 `workflow/cli/review_ui.py`，确认判定逻辑在 `safety/review.py`。`engine.py` 作为编排者调用两者。

## 操作快照与回滚设计 (snapshot.py) — REQ-E-03

### 快照内容

执行写操作前，自动创建恢复快照，至少包含（任务书 REQ-E-03 验收标准第 1 条）：

1. **被修改的配置文件备份**：将受影响的配置文件复制到 `.bak/` 目录，保留原始内容
2. **受影响服务运行状态记录**：记录服务的当前状态（如 `systemctl status` 输出、`docker inspect` 结果），用于回滚时恢复服务状态
3. **快照元数据 JSON**：记录 `SnapshotMeta`（snapshot_id / timestamp / operation_summary / affected_files / affected_services / backup_path）

### 快照创建时机

审核同意后、执行前创建快照。作为用户可见步骤 6/7"执行"的隐藏子步骤（对齐 `Workflow-design.md` §2.2 STEP_TO_USER_STEP 中 SNAPSHOT 归属步骤 6）：

- 不打印独立步骤标题（与 EXECUTION_GUARD、SECURITY_CHECKING 一致）
- CLI 显示提示：`正在创建快照...`
- 创建完成后显示：`✓ 快照已创建: snap_xxxxxxxx`
- 然后进入 EXECUTING

对齐已实现的 `engine.py` `_do_snapshot` 的提示输出。

### 快照元数据存储

- 快照文件：`~/.galaxy-diag/snapshots/<snapshot_id>/` 目录，含 `.bak/` 子目录和 `meta.json`
- 元数据 JSON：`SnapshotMeta` 序列化，关联 `session_id` 便于按会话查询
- 备份路径记录在 `SnapshotMeta.backup_path`

### 一键回滚命令

提供 CLI 命令（对齐 `CLI_Framework_design.md` §3.2 `galaxy-diag snapshot rollback`）：

```
galaxy-diag snapshot rollback <snapshot_id>
```

回滚流程：
1. 读取快照元数据，获取 `affected_files` 和 `affected_services`
2. 从 `.bak/` 恢复原始配置文件
3. 重启受影响服务（恢复到快照时状态）
4. 记录审计日志（`result=rollback`）

### 回滚本身也需经 REQ-E-01 人工审核

任务书 REQ-E-03 验收标准第 4 条："回滚操作本身也需经过 REQ-E-01 的人工审核"。

手动回滚（`galaxy-diag snapshot rollback`）执行前弹出确认提示，要求用户确认。但**执行失败时的自动回滚**（`EXECUTING_NEXT_ON_FAILURE`）不需要额外确认——这是安全兜底机制，由 `engine.py` 在执行失败时自动触发，回滚结果记录审计日志供事后审查。

### 与 engine.py 失败回滚的集成

对齐 `Workflow-design.md` §6 异常处理和 `engine.py` 的 `_mark_rollback()`：

1. `_do_executing` 执行失败时，调用 `snapshot.rollback(self.state.snapshot.snapshot_id)`
2. 回滚成功：调用 `_mark_rollback(reason)`，标记 `SessionStatus.ROLLED_BACK`
3. 回滚失败：标记为需要人工介入（回滚本身失败属于严重情况，打印告警 + 记录审计）
4. 无论回滚成功失败，均记录审计日志

## 审计日志设计 (audit.py) — REQ-E-04

### JSONL 格式与存储位置

- 存储位置：`~/.galaxy-diag/audit.jsonl`（对齐 `config/defaults.py` 的 `audit_log` 配置项）
- 格式：JSON Lines，每行一条 `AuditRecord` 的 JSON
- 选择 JSONL 而非 JSON 数组的原因：追加写入无需读取整个文件、无需并发锁、服务重启不丢失、便于 `grep`/`tail` 离线查看

### 专用写入函数

```python
def write_audit(record: AuditRecord) -> None:
    """写入审计日志（不经 Agent / LLM 输出流）

    使用 json.dumps() + 文件追加写入，Agent 没有修改审计日志的 Tool，
    防 Prompt 注入篡改日志内容。
    """
```

- 不经 Agent 输出流：Agent 的 Tool 列表中不包含修改/删除审计日志的 Tool
- 不经 LLM：日志内容由调用方（engine.py）硬编码构造，LLM 无法影响日志内容
- 对齐架构设计 §6.2：`audit.py` 用 `json.dumps().write()` 直接写文件

### 记录字段

逐字段说明 `AuditRecord` 各字段含义与填写来源（对齐 `shared/types.py` 当前定义）：

| 字段 | 类型 | 填写来源 |
|------|------|---------|
| `timestamp` | datetime | 写入时自动填充 |
| `session_id` | str | WorkflowState.session_id |
| `operator` | str | 当前系统用户（`os.getlogin()` 或配置） |
| `action` | str | 操作内容描述（如"执行修复脚本"） |
| `result` | confirmed/success/failure/rollback/rejected | 操作结果（confirmed=用户确认执行、rejected=用户拒绝） |
| `llm_basis` | str | LLM 分析依据摘要（diagnosis.root_cause 摘要） |
| `snapshot_id` | str \| None | 关联的快照 ID（无快照时为 None） |
| `user_input` | str | 用户确认输入（`y` / `n` / `CONFIRM`） |

> 任务书 REQ-E-04 验收标准第 1 条要求记录"时间戳、操作者、操作内容、操作结果、LLM 分析依据摘要"，`AuditRecord` 字段完整覆盖。

### 查询命令

CLI 命令（对齐 `CLI_Framework_design.md` §3.2）：

```
galaxy-diag audit-log [--session ID] [--limit N] [--since DATETIME]
```

实现：读取 JSONL 文件 → 按条件过滤 → 通过 `display.print_audit_records()` 渲染表格。

### 持久化与防丢失

每次操作后**立即写盘**（append 模式），不缓存在内存。对齐任务书设计警示："操作日志存在内存中，服务重启后丢失——出事后无法回查历史操作"。本设计确保服务重启后审计日志完整保留。

**两阶段留痕**：审核同意后先写一条 `result=confirmed` 记录（含用户输入 `y`/`CONFIRM`），再进入快照与执行；执行完成后再写 `result=success`/`failure`/`rollback`。这样即使执行过程崩溃未能写出第二条，用户的"确认"决策仍已留痕，满足 REQ-E-01 "确认与拒绝均记录审计日志"。

### 防篡改设计

- Agent 无修改/删除审计日志的 Tool（物理隔离）
- 日志文件建议权限设置为只追加（`chmod +a`，Linux append-only 属性）
- 未来可选：日志哈希链（每条记录含前一条哈希，防篡改可检测）

## 受控执行器设计 — 执行修复 (EXECUTING)

### 设计定位

受控执行器是 safety 安全闭环的最后一环：拦截→审核→快照→**执行**→回滚。负责按步骤执行修复命令并监控，失败自动触发回滚。

> 架构设计 `safety/` 包未单列 `executor.py`，受控执行逻辑可放入 `snapshot.py`（与回滚同属执行相关）或独立为 `executor.py`。本设计建议独立为 `safety/executor.py`，职责单一。

### 受控执行策略

1. **逐步执行**：按 `FixProposal.commands` 顺序逐条执行
2. **失败即停**：某步失败时不继续执行后续步骤（对齐任务书 REQ-D-02 验收标准第 2 条"某步骤失败时不继续执行后续步骤"）
3. **错误捕获**：捕获每条命令的退出码、stdout、stderr
4. **超时控制**：每条命令设置超时（`subprocess.run(timeout=...)`），防止命令挂起
5. **执行环境隔离**：使用 `subprocess` 执行，不直接 `os.system`，便于捕获结果和超时控制

### 与 snapshot 回滚的衔接

执行失败时自动调用 `snapshot.rollback()`：

1. 执行某步失败 → 立即停止后续步骤
2. 调用 `snapshot.rollback(self.state.snapshot.snapshot_id)`
3. 回滚成功：标记 `SessionStatus.ROLLED_BACK`，记录审计（`result=rollback`）
4. 回滚失败：标记为需人工介入，打印告警，记录审计（`result=failure`，含回滚失败信息）

### 与 engine.py _do_executing 的集成

当前 `engine.py` 的 `_do_executing` 是 stub（模拟成功）。替换方案：

1. 调用 `executor.run(self.state.fix)` 执行修复
2. 执行结果写入 `WorkflowState.history`
3. 成功：转换到 VERIFYING
4. 失败：调用 `snapshot.rollback()` + `_mark_rollback()`

对齐 `Workflow-design.md` §7 各步骤与模块的调用关系：EXECUTING 调用 `safety.executor.run()`。

## 顶层入口设计

### 各模块顶层函数签名

```python
# safety/danger.py
def execution_guard_check(
    proposal: FixProposal,
    env_type: EnvironmentType,
) -> GuardResult:
    """执行前熔断检查 (E-02)，不经 LLM"""

# safety/review.py
def review_confirm(
    proposal: FixProposal,
    guard_result: GuardResult,
) -> ReviewDecision:
    """审核确认判定 (E-01/F-03)，基于 guard_result 决定是否要求 CONFIRM
    通过 review_ui.py 收集 stdin 输入，确认不经 LLM"""

# safety/snapshot.py
def create_snapshot(proposal: FixProposal) -> SnapshotMeta:
    """创建恢复快照 (E-03)，执行前自动备份"""

def rollback(snapshot_id: str) -> RollbackResult:
    """一键回滚 (E-03)，从快照恢复"""

# safety/executor.py
def run(proposal: FixProposal) -> ExecuteResult:
    """受控执行修复 (EXECUTING)，失败自动触发回滚"""

# safety/audit.py
def write_audit(record: AuditRecord) -> None:
    """写入审计日志 (E-04)，不经 Agent/LLM 输出流"""

def query_audit(
    *, session_id: str | None, limit: int, since: datetime | None
) -> list[AuditRecord]:
    """查询审计日志 (E-04)"""
```

### safety/__init__.py 导出

```python
# safety/__init__.py
from galaxy_diag.safety.danger import execution_guard_check
from galaxy_diag.safety.review import review_confirm
from galaxy_diag.safety.snapshot import create_snapshot, rollback
from galaxy_diag.safety.executor import run as execute
from galaxy_diag.safety.audit import write_audit, query_audit
from galaxy_diag.safety.patterns import DANGER_PATTERNS

__all__ = [
    "execution_guard_check",
    "review_confirm",
    "create_snapshot",
    "rollback",
    "execute",
    "write_audit",
    "query_audit",
    "DANGER_PATTERNS",
]
```

## 工作流集成

### engine.py _do_execution_guard 实现替换

当前 stub（`engine.py` 第 627-651 行）直接返回 `pass`。替换为：

1. 调用 `execution_guard_check(self.state.fix, self.state.env_info.env_type)`
2. 将 `GuardResult` 存入 `self._guard_result`（当前为字符串，替换为对象）
3. 渲染熔断结果（通过 display.py，显示命中模式和影响范围）
4. 转换到 REVIEWING

对齐 `Workflow-design.md` §2.4：EXECUTION_GUARD → REVIEWING（pass/warning/critical 均进入 REVIEWING）。

### _guard_result 在 EXECUTION_GUARD → REVIEWING 间的传递

`_do_reviewing` 读取 `self._guard_result`（当前第 667 行 `getattr(self, "_guard_result", "pass")`），替换为读取 `GuardResult` 对象的 `level` 字段：

- `level == "pass"`：普通 `[y/N]` 确认
- `level == "warning"` 或 `"critical"`：要求输入 CONFIRM

对齐已实现的 CONFIRM 逻辑（`engine.py` 第 668-690 行）。

### engine.py _do_snapshot 实现替换

当前 stub（第 752-772 行）创建 mock 快照。替换为：

1. 调用 `create_snapshot(self.state.fix)` 创建真实快照
2. 存入 `self.state.snapshot`
3. 显示"正在创建快照..."和"✓ 快照已创建"提示（当前已实现）
4. 转换到 EXECUTING

对齐 `Workflow-design.md` §5.3 隐藏子步骤的用户感知：SNAPSHOT 归属步骤 6/7"执行"，不打印独立步骤标题。

### engine.py _do_executing 实现替换

当前 stub（第 774-784 行）模拟成功。替换为：

1. 调用 `executor.run(self.state.fix)` 执行修复
2. 成功：转换到 VERIFYING
3. 失败：调用 `snapshot.rollback(self.state.snapshot.snapshot_id)` + `_mark_rollback()`

### 用户可见步骤映射对照

safety 模块的内部状态在用户可见 7 步中的归属（对齐 `Workflow-design.md` §2.2 `STEP_TO_USER_STEP`）：

| safety 内部状态 | 用户可见步骤 | 说明 |
|----------------|------------|------|
| EXECUTION_GUARD | 5/7 人工审核（开头） | 熔断检查，不打印独立标题 |
| REVIEWING | 5/7 人工审核 | 审核确认 |
| SNAPSHOT | 6/7 执行（开头） | 自动快照，显示"正在创建快照"提示 |
| EXECUTING | 6/7 执行 | 执行修复 |

## 异常处理设计

### 设计原则

**fail-safe（故障安全）**：安全模块在异常时偏向拦截而非放行。宁可保守地阻止一次合法操作，也不放过一次危险操作。

- 快照创建失败 → 阻止执行（宁可保守）
- 审计写失败 → 告警但允许继续（审计不阻塞核心操作，但必须告警）
- 危险检测异常 → 视为 WARNING（保守策略，要求确认）

### 异常分类与处理

| 异常场景 | 处理方式 | 用户提示 | 状态影响 |
|---------|---------|---------|---------|
| 快照创建失败 | 阻止执行，提示用户 | "快照创建失败，已阻止执行以保护系统" | 保持在 REVIEWING |
| 回滚失败 | 标记需人工介入，打印告警 | "回滚失败，请人工介入检查系统状态" | 标记 ROLLED_BACK_FAILED |
| 审计写入失败 | 告警 + 尝试写备用位置 + 不阻止操作 | "审计日志写入失败（已记录到备用位置）" | 操作继续 |
| 危险检测异常 | 视为 WARNING，要求 CONFIRM | "危险检测异常，请谨慎确认" | 进入 REVIEWING 要求 CONFIRM |
| 变量展开检测异常 | 视为 WARNING（保守） | "变量展开检测异常，请谨慎确认" | 进入 REVIEWING 要求 CONFIRM |

### 降级策略

- **审计写失败**：打印告警 → 尝试写入备用位置（如 `~/.galaxy-diag/audit.failed.jsonl`） → 不阻止当前操作。审计是留痕，不应阻塞核心修复流程，但失败必须可见。
- **快照失败**：提示用户 → 询问是否不快照直接执行（需输入 CONFIRM）。默认阻止执行，给用户一次知情后的选择权。
- **变量展开检测异常**：视为 WARNING（保守策略）。检测工具异常时不能默认安全，必须要求用户确认。

## 安全约束设计

### 不经 LLM 约束

四条安全关键路径的物理隔离，全部不经 LLM：

| 安全关键路径 | 实现方式 | 为何不经 LLM |
|------------|---------|-------------|
| 危险命令拦截 | `danger.py` 正则 + 硬编码算法 | LLM 可被 Prompt 注入影响判断 |
| 人工审核确认 | `review.py` 读 stdin | LLM 无法控制 stdin 输入 |
| 快照创建 | `snapshot.py` 文件系统操作 | 无需 LLM 参与 |
| 审计日志 | `audit.py` 专用函数写文件 | Agent 无修改审计日志的 Tool |

### 绕过防护矩阵

对齐架构设计 §6.2 安全关卡详细设计：

| 安全关卡 | 实现方式 | 绕过防护 |
|---------|---------|---------|
| 危险命令拦截 | `danger.py` 正则 + 变量展开检测匹配 `patterns.py` | 不可能——在用户确认前拦截，转入 CONFIRM 流程 |
| 人工确认 | `review.py` 读 stdin 的 `[y/N]` | 不可能——stdin 不经过 LangChain 回调 |
| 二次确认 | 危险操作要求输入 `CONFIRM` | 不可能——Prompt 注入无法控制 stdin |
| 审计日志 | `audit.py` 用 `json.dumps().write()` 直接写文件 | 不可能——Agent 没有修改审计日志的 Tool |
| 快照回滚 | `snapshot.py` 备份到 `.bak/` | 回滚本身也需经 review.py 确认（手动回滚） |

### Prompt 注入防护

- **场景 1**：日志内容中嵌入"用户已确认执行"恶意文本，诱导 LLM 认为用户已确认。
  - **防线**：确认走 stdin，LLM 无法控制 stdin 输入。即使 LLM 被注入影响其"建议"，决定权仍在 `review.py` 的 stdin 确认。
- **场景 2**：Agent 试图修改审计日志掩盖操作痕迹。
  - **防线**：Agent 的 Tool 列表不含修改/删除审计日志的 Tool，`audit.py` 用专用函数直接写文件，不经 Agent 输出流。

### 回滚安全

- 手动回滚（`galaxy-diag snapshot rollback`）需经 REQ-E-01 人工审核
- 执行失败时的自动回滚（`EXECUTING_NEXT_ON_FAILURE`）不需额外确认——属安全兜底，由 `engine.py` 自动触发
- 回滚结果记录审计日志供事后审查

### 权限设计

- **最小权限**：safety 模块只读 `FixProposal`（danger/review/audit 不修改 FixProposal 内容；snapshot 只备份不修改原文件）
- **只读优先**：danger/review/audit 均为只读操作，不修改系统状态
- **文件权限**：快照目录（`~/.galaxy-diag/snapshots/`）和审计日志（`~/.galaxy-diag/audit.jsonl`）设置合理权限，建议审计日志设为 append-only

## 验收对照

### REQ-E-01 人工审核强制拦截

| 验收标准 | 实现位置 |
|---------|---------|
| 所有写操作执行前必须有人类显式确认 | `review.py` + `engine.py` `_do_reviewing` |
| 确认通过专用交互流程，不由 LLM 判定 | `review.py` 读 stdin，不经 LLM（§双通道隔离） |
| 用户拒绝时不执行且不反复要求确认 | `review.py` + `_mark_rejected()`（§拒绝不反复要求确认） |
| 确认与拒绝均记录审计日志 | `audit.write_audit(result=confirmed/rejected)` |

### REQ-E-02 危险操作多维防护

| 验收标准 | 实现位置 |
|---------|---------|
| 维护危险命令清单，命中进入人工审核要求 CONFIRM | `patterns.py` + `danger.py`（§模式分类、§危险命令正则匹配） |
| 对脚本进行安全校验 | `danger.py` `execution_guard_check()` |
| 评估影响范围并展示 | `danger.py` 影响范围评估 → `GuardResult.impact_summary` → `review_ui` 展示 |
| 危险操作需额外步骤（输入 CONFIRM） | `review.py` 二次确认（§危险操作二次确认） |
| 变量展开检测防绕过 | `danger.py` 变量展开检测算法 |

### REQ-E-03 操作快照与一键回滚

| 验收标准 | 实现位置 |
|---------|---------|
| 执行前自动创建恢复快照 | `snapshot.py` `create_snapshot()` + `engine.py` `_do_snapshot` |
| 快照含配置文件内容和服务状态 | `snapshot.py`（§快照内容） |
| 提供一键回滚命令 | `snapshot.py` `rollback()` + CLI `galaxy-diag snapshot rollback` |
| 快照元数据可查询 | `SnapshotMeta` + CLI `galaxy-diag snapshot show` |
| 回滚本身需经 REQ-E-01 审核 | `snapshot.py`（§回滚本身也需经人工审核） |

### REQ-E-04 操作留痕与审计日志

| 验收标准 | 实现位置 |
|---------|---------|
| 记录时间戳/操作者/内容/结果/LLM 依据 | `AuditRecord` 字段（§记录字段） |
| 审计日志持久化存储 | `audit.py` JSONL 追加写入（§持久化与防丢失） |
| 提供日志查询命令 | `audit.py` `query_audit()` + CLI `galaxy-diag audit-log` |
| 日志不可被 LLM 修改或绕过 | `audit.py` 专用函数 + Agent 无修改 Tool（§防篡改设计） |

### REQ-F-03 审核确认交互流程

| 验收标准 | 实现位置 |
|---------|---------|
| 执行前展示操作摘要（执行什么/影响什么/回滚方案） | `review_ui.py` `render_summary()`（§操作摘要展示） |
| 三种操作：确认/拒绝/修改 | `review.py` `ReviewDecision`（§三种用户操作的状态转换） |
| 确认通过键盘输入，不通过自然语言 | `review.py` stdin（§双通道隔离） |
| 危险操作增加额外步骤（输入 CONFIRM） | `review.py`（§危险操作二次确认） |
| 确认/拒绝/修改均记录审计日志 | `audit.write_audit()` |

### 红线 2 对照

红线 2："生产环境写操作必须人工显式确认，确认必须通过专用交互流程完成，不得由 LLM 解析用户自然语言来判定'确认'"。

- **人工显式确认**：`review.py` 的 `[y/N]` 和 CONFIRM 流程
- **专用交互流程**：stdin 通道，不经 LLM 对话通道
- **不由 LLM 判定**：四条安全关键路径全部不经 LLM（§不经 LLM 约束）
- **拒绝不反复要求**：`_mark_rejected()` 直接终止（§拒绝不反复要求确认）

## 后续扩展点

### 模式库外置 YAML

当前 `DANGER_PATTERNS` 硬编码在 `patterns.py`。未来支持从 YAML 文件加载（如 `data/danger_patterns/rules.yaml`），安全团队可在不修改代码的情况下新增/调整危险模式。加载接口返回 `list[DangerPattern]` 不变，`danger.py` 无需改动。

### 审计日志轮转与归档

`audit.jsonl` 长期运行会增大。未来增加按大小/时间轮转策略（如 `audit.jsonl` → `audit.2026-08.jsonl`），归档到压缩文件。查询命令支持跨归档文件查询。

### 快照差分备份

当前全量备份受影响文件。未来支持差分备份（只备份变化的文件），形成增量快照链，节省磁盘空间。适用于频繁修复场景。

### 多机审计聚合

分布式场景下多台服务器的审计日志聚合查询。需要 SSH 采集或中心化存储——但需注意离线约束（红线 1），中心化存储应限于内网，不依赖公网。
