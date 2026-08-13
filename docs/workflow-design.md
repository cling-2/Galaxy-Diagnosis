# 工作流编排设计 (REQ-F-02)

## 模块概述

定义诊断-修复端到端工作流的状态机框架，使各模块能力串联为完整闭环：

**环境识别 → 信息采集 → 根因分析 → 修复建议 → 安全检测 → 人工审核 → 执行 → 结果验证**

对应 REQ-F-02 四项验收标准：

1. 按核心流程编排，每个步骤的输入/输出定义明确，用户可在任意步骤介入
2. 工作流状态持久化，用户中断后可从上次位置继续
3. 简单问题允许跳过部分步骤
4. 各功能不再独立运行，切换时不丢失上下文

> 本文档仅定义工作流状态机框架（状态、转换、持久化、恢复）。各步骤内部的业务逻辑（采集策略、诊断推理、修复生成、安全拦截）分别由 collector/diagnoser/fixer/safety 模块实现，本文档不展开。

## 1. Agent 模式

**当前版本：单 Agent + Workflow State Machine**

```
                  CLI (workflow/cli/)
                       |
                       v
              Workflow Engine (engine.py)
                       |
             +---------+---------+
             |                   |
        Diagnosis Agent      Safety Engine
             |                   |
             v                   v
          Tool Layer          硬编码逻辑
             |              (不经 LLM)
     +---+---+---+
     |   |   |   |
    环境 日志 网络 存储
     |
     v
  Local LLM
```

选择理由：

1. 诊断流程具有强确定性（任务书定义了明确的步骤序列）
2. 单 Agent 降低系统复杂度，状态机保证流程可控
3. 安全关键路径（审核、拦截、日志）由 Safety Engine 独立处理，不经 LLM
4. 方便状态持久化和故障恢复

**演进方案（第二版）**：Supervisor + Domain Agents（NetworkAgent / StorageAgent / ComputeAgent），按故障域拆分，提升复杂场景的诊断质量。当前阶段不过早引入。

## 2. 工作流状态机

### 2.1 完整状态图

```
                        START
                          |
                          v
                  ENV_RECOGNISING
                   (环境感知)
                          |
                          v
                     COLLECTING
                    (信息采集) <─────────────────────┐
                          |                          |
                          |                          |
                   ┌──────┴──────┐                   |
                   v             v                   |
              (继续)      DIAGNOSING                 |
                   |      (根因分析)                  |
                   |         |    信息不足            |
                   |    ┌────┴────┐                  |
                   |    |         |                  |
                   | confidence  confidence          |
                   | =CONFIRMED  =INSUFFICIENT       |
                   | /SUSPECTED       |              |
                   v                  v              |
               PLANNING          提示缺失信息 ───────┘
              (修复建议生成)     (回到采集补充)
                   |
                   v
            SECURITY_CHECKING
            (D-03: 生成后检测)
                   |
            ┌──────┴──────┐
            |             |
           pass          fail
            |             |
            v             v
        REVIEWING     回到 PLANNING
       (人工审核)     (代码质量问题，重新生成)
            |
      ┌─────┼─────┐
      |     |     |
     yes    n   edit
      |     |     |
      v     v     v
  EXECUTION_GUARD  终止  SECURITY_CHECKING
   (E-02: 执行前熔断)     (编辑后重走 D-03)
      |
  ┌───┼───────────────┐
  |   |               |
 pass WARNING       CRITICAL
  |   |               |
  v   v               v
SNAPSHOT 回到           终止
(创建快照) REVIEWING      (强制拦截，不可绕过)
  |        (额外确认
  |         CONFIRM)
  v
EXECUTING
 (执行修复)
   |
  ┌──┴──┐
  |     |
 成功   失败
  |     |
  v     v
VERIFYING  回滚(从快照恢复) → 终止
 (结果验证)
   |
   v
  DONE
```

### 2.2 状态定义

| 状态 | 说明 | 输入 | 输出 | 对应模块 |
|------|------|------|------|---------|
| START | 任务开始 | 用户问题描述 | session_id | workflow |
| ENV_RECOGNISING | 识别运行环境类型 | 系统特征 | 环境类型 (VM/容器/裸机) + 软硬件信息 | collector |
| COLLECTING | 采集诊断信息 | 环境信息 + 用户描述 | 结构化诊断上下文 | collector |
| DIAGNOSING | 根因分析 | 诊断上下文 + 环境信息 | DiagnosisResult (root_cause + confidence) | diagnoser |
| PLANNING | 生成修复建议 | DiagnosisResult | FixProposal (命令/脚本，尚未检测) | fixer |
| SECURITY_CHECKING | 生成后检测 (D-03) | FixProposal | 检测结果 (pass/fail + warnings) | fixer.checker |
| REVIEWING | 人工审核 | FixProposal + 检测结果 | yes / no / edit | safety.review + workflow/cli |
| EXECUTION_GUARD | 执行前熔断 (E-02) | 审核通过的 FixProposal | 熔断结果 (pass/blocked/需额外确认) | safety.danger |
| SNAPSHOT | 创建恢复快照 | 熔断通过的 FixProposal | SnapshotMeta | safety.snapshot |
| EXECUTING | 执行修复 | FixProposal | 执行日志 | safety (受控执行) |
| VERIFYING | 结果验证 | 执行结果 | 恢复状态 | diagnoser (验证) |
| DONE | 完成 | — | — | — |

### 2.3 状态转换规则

| 当前状态 | 下一状态 | 触发条件 | 说明 |
|---------|---------|---------|------|
| START | ENV_RECOGNISING | 用户提交问题 | 创建 session，开始流程 |
| ENV_RECOGNISING | COLLECTING | 环境识别完成 | 识别结果写入 WorkflowState.env_info |
| COLLECTING | DIAGNOSING | 采集完成且信息充分 | 诊断上下文就绪 |
| DIAGNOSING | PLANNING | confidence = CONFIRMED 或 SUSPECTED | 有足够依据给出修复建议 |
| DIAGNOSING | COLLECTING | confidence = INSUFFICIENT | 信息不足，回退补充采集 |
| PLANNING | SECURITY_CHECKING | FixProposal 生成 | 进入生成后检测 |
| SECURITY_CHECKING | REVIEWING | D-03 通过（无 CRITICAL） | 代码质量合格，展示给用户 |
| SECURITY_CHECKING | PLANNING | D-03 失败（有 CRITICAL） | 代码质量问题，重新生成 |
| REVIEWING | EXECUTION_GUARD | 用户确认 yes | 进入执行前熔断 |
| REVIEWING | SECURITY_CHECKING | 用户编辑 edit | 编辑后重走 D-03 检测 |
| REVIEWING | 终止 | 用户拒绝 no | 不执行，不反复要求确认 |
| EXECUTION_GUARD | SNAPSHOT | E-02 通过（无 CRITICAL/WARNING） | 安全检查合格，创建快照 |
| EXECUTION_GUARD | REVIEWING | E-02 有 WARNING | 需用户额外确认（CONFIRM），回到 REVIEWING |
| EXECUTION_GUARD | 终止 | E-02 有 CRITICAL（强制拦截） | 不可执行，不可绕过 |
| SNAPSHOT | EXECUTING | 快照创建成功 | 从快照点安全执行 |
| EXECUTING | VERIFYING | 执行成功 | 验证修复效果 |
| EXECUTING | 回滚 → 终止 | 执行失败 | 从快照恢复，记录审计日志 |
| VERIFYING | DONE | 验证通过 | 流程完成 |
| VERIFYING | DIAGNOSING | 验证失败 | 修复未生效，重新诊断 |

### 2.4 SECURITY_CHECKING 与 EXECUTION_GUARD 的分工

| 维度 | SECURITY_CHECKING (D-03) | EXECUTION_GUARD (E-02) |
|------|--------------------------|------------------------|
| **防护阶段** | 代码生成后、用户确认前 | 用户确认后、实际执行前 |
| **核心目标** | 确保 LLM 产出的代码正确、可用、无明显隐患 | 防止高风险操作被误执行或恶意执行 |
| **性质** | 质量保障 | 安全兜底 |
| **拦截策略** | 建议性：CRITICAL 阻止自动执行，WARNING 允许继续 | 强制性：CRITICAL 强制拦截不可绕过，WARNING 要求额外确认 |
| **检测深度** | 基于文本的静态匹配（ShellCheck + 正则） | 基于语义的深度预分析（变量展开 + 影响范围评估） |
| **规则库归属** | 通用代码质量规则，研发/工具链团队维护 | 业务安全策略，安全/SRE 团队维护 |
| **检测维度** | 语法检查 + 环境兼容性 + 危险模式建议性警告 | 危险命令深度检测 + 变量展开绕过 + 影响范围评估 + 用户编辑风险 |

> **纵深防御原则**：D-03 解决"LLM 写得对不对"，E-02 解决"用户该不该执行"。两层缺一不可：用户编辑可能引入新风险（D-03 检测不到）、某些操作在特定环境上下文中才危险（D-03 静态匹配无法感知）、人工确认存在认知盲区需要影响范围评估作为决策依据。

### 2.5 跳过与短路

简单问题允许跳过部分步骤，通过 WorkflowState 中已有数据判断：

| 短路场景 | 条件 | 路径 |
|---------|------|------|
| 已知故障模式 | 规则库匹配命中 | ENV_RECOGNISING → COLLECTING → 直接 PLANNING（跳过 DIAGNOSING） |
| 单步修复 | 修复只需一条命令 | PLANNING 可生成单步 FixProposal，SECURITY_CHECKING + REVIEWING 流程不变 |
| 只需诊断 | 用户明确表示只看诊断结论 | DIAGNOSING → 输出结论 → DONE（不进入 PLANNING） |

> 短路不是跳过安全检测和审核——SECURITY_CHECKING、REVIEWING、EXECUTION_GUARD 三步是红线，不可跳过。

## 3. WorkflowState 设计

对齐架构设计 `shared/types.py` 中的 `WorkflowState` 定义，此为唯一状态结构：

```python
class WorkflowStep(str, Enum):
    ENV_RECOGNISING = "env_recognising"
    COLLECTING = "collecting"
    DIAGNOSING = "diagnosing"
    PLANNING = "planning"
    SECURITY_CHECKING = "security_checking"    # D-03: 生成后检测
    REVIEWING = "reviewing"
    EXECUTION_GUARD = "execution_guard"         # E-02: 执行前熔断
    SNAPSHOT = "snapshot"
    EXECUTING = "executing"
    VERIFYING = "verifying"

@dataclass
class WorkflowState:
    """工作流持久化状态"""
    session_id: str
    current_step: WorkflowStep
    problem_description: str
    env_info: EnvInfo | None                        # collector 产出
    diagnostic_context: DiagnosticContext | None     # C-01 产出
    diagnosis: DiagnosisResult | None                # diagnoser 产出
    fix: FixProposal | None                          # fixer 产出
    snapshot: SnapshotMeta | None                    # safety 产出
    history: list[dict]                              # 步骤历史（含时间戳、状态转换、结果）
```

**与旧版 State JSON 的映射**：

| 旧字段 | 新字段 (WorkflowState) | 变更说明 |
|--------|----------------------|---------|
| `task_id` | `session_id` | 统一命名 |
| `environment: {type, detail}` | `env_info: EnvInfo` | 类型化，含 HardwareInfo/StorageInfo |
| `diagnosis.confirmed_reason / possible_reason` | `diagnosis.root_cause + confidence` | 合并为 Confidence 枚举 |
| `diagnosis.missing_information` | `diagnosis.missing_info` | 字段名对齐 |
| `repair_plan.steps` | `fix: FixProposal` | 含 commands/script/check_passed |
| `security_check: {risk_level, warnings}` | `fix.check_issues` + `fix.check_detail` | 整合进 FixProposal，区分 D-03 与 E-02 |
| `approval: {status, operator}` | `history` 中记录 | 审核记录入 history + audit.jsonl |
| `execution: {command, result}` | `history` 中记录 | 执行记录入 history |
| `verification: {success}` | `history` 中记录 | 验证记录入 history |
| — | `snapshot: SnapshotMeta` | **新增**：快照元数据 |
| — | `diagnostic_context: DiagnosticContext` | **新增**：诊断信息采集产出 |

## 4. 状态持久化与恢复

### 4.1 存储位置

```
~/.galaxy-diag/sessions/<session_id>.json
```

### 4.2 持久化时机

**每个状态转换完成后立即落盘**（而非流程结束时），确保任意时刻崩溃都可恢复。

```python
# engine.py 伪代码
def _transition(self, next_step: WorkflowStep):
    self.state.current_step = next_step
    self.state.history.append({
        "step": next_step.value,
        "timestamp": datetime.now().isoformat(),
        "result": "entered",
    })
    self._save()  # 立即持久化

def _save(self):
    """将 WorkflowState 序列化为 JSON 写入磁盘"""
    path = self._session_path / f"{self.state.session_id}.json"
    path.write_text(json.dumps(dataclasses.asdict(self.state), ensure_ascii=False, indent=2))
```

### 4.3 恢复流程

```bash
# 方式一：通过 run 子命令
galaxy-diag run --resume <session_id>

# 方式二：无参数时自动检测未完成会话
galaxy-diag run
# 检测到 1 个未完成会话:
#   sess_20260805_001 (当前步骤: REVIEWING, 问题: "数据磁盘未识别")
# 是否恢复? [y/N]:
```

恢复逻辑：

1. 读取 `sessions/<session_id>.json`
2. 校验 JSON 完整性（字段齐全、WorkflowStep 值合法）
3. 从 `current_step` 继续——已完成的步骤读取 `WorkflowState` 中已有数据，不重复执行
4. 如当前步骤是 EXECUTING（可能执行到一半），提示用户确认是否重试

### 4.4 会话生命周期

| 会话状态 | 判断方式 | 说明 |
|---------|---------|------|
| 进行中 | `current_step` 不是 DONE/REJECTED | 可 resume |
| 已完成 | `current_step = DONE` | 可查看，不可 resume |
| 已拒绝 | history 末条 `result = rejected` | 可查看，不可 resume |
| 已回滚 | history 末条 `result = rollback` | 可查看，不可 resume |

## 5. 人工介入机制

### 5.1 逐步模式 vs 自动模式

| 模式 | 行为 | 适用场景 |
|------|------|---------|
| 逐步模式 (默认) | 每个步骤完成后暂停，展示结果，等待用户确认继续（§5.2 全部介入点生效） | 运维人员需要逐步观察 |
| 自动模式 | 自动推进，中间步骤仅展示结果不暂停，直到 REVIEWING 才等待人工确认（§5.2 仅 REVIEWING 介入点生效） | 快速排查，只需最终确认 |

```bash
# 逐步模式
galaxy-diag run -d "数据磁盘未识别"

# 自动模式（跳过中间暂停，直接到审核）
galaxy-diag run -d "数据磁盘未识别" --auto
```

> 无论哪种模式，REVIEWING 步骤**始终需要人工确认**，这是红线 2。

### 5.2 介入点

| 步骤 | 用户可执行操作 | CLI 入口 |
|------|-------------|---------|
| COLLECTING 后 | 查看采集结果、补充描述 | 自动展示 + 提示继续 |
| DIAGNOSING 后 | 查看结论、决定是否继续 | 自动展示 + 确认 |
| PLANNING 后 | 编辑修复参数 | `--edit` 或交互提示 |
| SECURITY_CHECKING 后 | 查看检测结果 | 自动展示（D-03 WARNING 提醒） |
| REVIEWING | 确认 / 拒绝 / 编辑参数 / 删除步骤 / 重排步骤 | y / n / e / d / r |
| EXECUTION_GUARD | 查看影响范围、额外确认（CONFIRM） | 自动展示 + CONFIRM 输入 |

## 6. 异常处理

| 异常场景 | 处理方式 | 状态影响 |
|---------|---------|---------|
| LLM 服务不可用 | 保存当前状态 → 暂停 → 提示用户恢复推理服务后 resume | current_step 不变 |
| Tool 执行失败 (采集) | 记录失败原因 → 提示用户 → 可选择跳过或终止 | current_step 不变 |
| 进程崩溃 / Ctrl+C | 下次 resume 从 current_step 继续（已落盘） | current_step 不变（上次保存值） |
| SECURITY_CHECKING 失败 | 回退到 PLANNING 重新生成 | current_step 回退 |
| EXECUTION_GUARD 强制拦截 (CRITICAL) | 终止工作流，标记为 rejected | 标记为系统拒绝 |
| EXECUTION_GUARD 需额外确认 (WARNING) | 回到 REVIEWING 要求用户输入 CONFIRM | current_step 回退到 REVIEWING |
| 执行失败 | 自动从快照回滚 → 记录审计日志 → 终止 | 标记回滚 |

## 7. 与 CLI 的集成

工作流通过 `galaxy-diag run` 子命令启动，engine.py 负责：

1. 解析 `--description` 或交互式收集问题描述
2. 创建 WorkflowState（生成 session_id）
3. 按状态机顺序调用各模块
4. 每步转换后调用 `display.py` 渲染结果
5. 在 REVIEWING 步骤调用 `interact.confirm()` 等待人工确认
6. 在 EXECUTION_GUARD 步骤展示影响范围评估，必要时要求 CONFIRM 确认
7. 全程持久化状态，支持 `--resume` 恢复

```python
# engine.py 伪代码
class WorkflowEngine:
    def __init__(self, state: WorkflowState, console: Console):
        self.state = state
        self.console = console

    def run(self) -> None:
        while self.state.current_step != WorkflowStep.DONE:
            step = self.state.current_step
            if step == WorkflowStep.ENV_RECOGNISING:
                self._do_env_recognising()
            elif step == WorkflowStep.COLLECTING:
                self._do_collecting()
            elif step == WorkflowStep.DIAGNOSING:
                self._do_diagnosing()
            elif step == WorkflowStep.PLANNING:
                self._do_planning()
            elif step == WorkflowStep.SECURITY_CHECKING:
                self._do_security_checking()
            elif step == WorkflowStep.REVIEWING:
                self._do_reviewing()
            elif step == WorkflowStep.EXECUTION_GUARD:
                self._do_execution_guard()
            elif step == WorkflowStep.SNAPSHOT:
                self._do_snapshot()
            elif step == WorkflowStep.EXECUTING:
                self._do_executing()
            elif step == WorkflowStep.VERIFYING:
                self._do_verifying()
```

### 各步骤与模块的调用关系

| 步骤 | 调用模块 | 说明 |
|------|---------|------|
| ENV_RECOGNISING | `collector.collect_env()` | 环境识别 |
| COLLECTING | `diagnoser.build_diagnostic_context()` + `diagnoser.rules.match_rules()` (短路预检) | 信息采集 + 已知故障短路 |
| DIAGNOSING | `diagnoser.diagnose()` | 规则匹配 + LLM 推理 |
| PLANNING | `fixer.generate()` | LLM 生成 → 后处理 → 模板渲染 → 脚本组装 |
| SECURITY_CHECKING | `fixer.checker.check()` | D-03: 语法 + 兼容性 + 危险建议性警告 |
| REVIEWING | `interact.confirm()` | 人工审核（y/n/edit） |
| EXECUTION_GUARD | `safety.danger.execution_guard_check()` | E-02: 危险深度检测 + 变量展开 + 影响评估 |
| SNAPSHOT | `safety.snapshot.create()` | 创建恢复快照 |
| EXECUTING | `safety.executor.run()` | 受控执行修复命令 |
| VERIFYING | `diagnoser.verify()` | 验证修复效果 |

## 8. 设计约束与后续扩展

### 当前约束

- EXECUTION_GUARD 内的完整交互流程（危险操作二次确认、影响范围展示），在 safety 模块设计中展开
- 快照与回滚的具体策略（备份哪些文件、服务状态如何记录），在 E-03 设计中展开
- Tool 层的接口定义与 Agent 调用逻辑，在 diagnoser 模块设计中展开
- fixer 模块的详细设计（Prompt、后处理、模板引擎、脚本生成、检测器），在 Fix_Generation_design.md 中展开

### 预留扩展点

- **多 Agent 演进**：engine.py 的 `_do_diagnose()` 内部可替换为 Supervisor 分发到 Domain Agents，状态机框架无需改动
- **trace 集成**：每个 `_transition()` 调用时可追加 trace 记录，对接 X-04 可观测需求
- **并发步骤**：ENV_RECOGNISING 与 COLLECTING 未来可并行执行，状态机调整即可
