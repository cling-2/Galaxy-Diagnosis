# 执行修复与结果验证设计 (EXECUTING + VERIFYING)

> 银河平台部署问题定位工具 — 执行修复与结果验证功能详细设计
> 覆盖范围：步骤 6/7「执行修复」的验证命令剥离、步骤 7/7「结果验证」的真实实现、验证失败后的进一步方案展示
> 前置依赖：Safety_design.md（executor / snapshot / audit）、Workflow-design.md（状态机）

## 1. 需求概述

### 1.1 现状

| 组件 | 文件 | 状态 |
|------|------|------|
| 受控执行器 | `safety/executor.py` | ✅ 已实现（subprocess 逐命令执行、失败即停、验证命令短超时） |
| 执行步骤编排 | `engine._do_executing` | ✅ 已接入 executor.run()，成功→VERIFYING，失败→自动回滚 |
| 结果验证 | `engine._do_verifying` | ❌ **STUB**：始终输出"验证通过（模拟）" |
| 验证命令 | `FixProposal.commands` 中 `is_verification=True` | 当前在步骤6与修复命令一起执行 |

### 1.2 目标

1. **验证命令从步骤6移到步骤7**：EXECUTING 只执行 `is_verification=False` 的修复命令；VERIFYING 单独执行 `is_verification=True` 的验证命令
2. **规则判定**：执行验证命令，全部退出码=0 → 问题已解决；任一非零 → 未解决
3. **确保 proposal 含验证步骤**：Prompt 强制要求 + 后处理兜底校验（双保险）
4. **验证失败处理**：展示进一步解决方案概要（排查顺序 + 补充信息 + 重新运行建议）+ 一键回滚提示

### 1.3 设计约束

- 不改动 `safety/executor.py` 的接口和核心逻辑（仅调用方传参变化）
- 不改动状态机转换规则（VERIFYING → done / DIAGNOSING 的分支由 engine 内部处理）
- 不经 LLM 判定验证结果（纯退出码 + 规则，对齐红线2"安全关键路径不经 LLM"）

## 2. 数据结构

### 2.1 新增 `VerifyResult`（`shared/types.py`）

```python
@dataclass
class VerifyResult:
    """验证结果（safety/verifier.py verify() 产出）"""
    success: bool = False
    output: str = ""              # 各验证命令的输出汇总
    failed_step: int = -1         # 失败的验证步骤序号（1-based），-1 表示全部通过
    failed_description: str = ""  # 失败步骤的 description
    total_steps: int = 0          # 总验证步骤数
    passed_steps: int = 0         # 通过的步骤数
```

### 2.2 现有类型变更

- `ExecuteResult`：不变（executor.run() 仍返回此类型）
- `FixProposal`：不变（commands 列表中仍含验证命令，由调用方过滤）
- `WorkflowState`：不变（无需新增字段，验证结果记入 history）
- `AuditRecord.result`：Literal **新增 `"verify_failed"`** 值，与 `"failure"`（执行失败，已回滚）区分——`verify_failed` 表示执行本身成功但修复未解决问题。便于 REQ-E-04 留痕区分"执行失败"与"修复未生效"。

## 3. 验证器设计 — `safety/verifier.py`（新建）

### 3.1 设计定位

与 `safety/executor.py` 对称：executor 执行修复命令，verifier 执行验证命令。两者职责单一、互不依赖，同属 safety 模块。

### 3.2 核心函数

```python
def verify(proposal: FixProposal, *, dry_run: bool = False) -> VerifyResult:
    """执行验证命令并判定修复是否生效 (VERIFYING)

    从 proposal.commands 中筛选 is_verification=True 的命令，
    逐条 subprocess 执行（只读操作、短超时 60s），
    全部退出码=0 → success=True，
    任一非零 → success=False（附带失败步骤信息）。

    无验证命令时 → 自动判定 success=True（保守策略：
    修复命令已全部成功执行，无验证手段时视为通过，
    由 engine 层展示"未执行验证"提示）。

    不经 LLM，纯 subprocess 退出码判定。
    """
```

### 3.3 执行策略

| 策略 | 说明 |
|------|------|
| 逐条执行 | 按验证命令在 proposal.commands 中的原始顺序执行 |
| 失败即停 | 某验证命令非零退出 → 停止后续验证 → success=False |
| 超时控制 | 每条验证命令超时 60s（与 executor 中验证步骤超时一致） |
| 捕获输出 | 收集 stdout/stderr，汇总到 VerifyResult.output |
| dry_run | dry_run=True 时只打印不执行，用于测试 |

### 3.4 无验证命令的处理

当 proposal.commands 中无 `is_verification=True` 的命令时：
- 返回 `VerifyResult(success=True, output="无验证命令，修复步骤已全部执行完毕")`
- engine 层额外展示提示："本次修复建议未包含验证步骤，建议人工确认修复效果"

### 3.5 依赖规则

```
safety/verifier.py ──→ shared (types, errors)   # 唯一依赖
               ✗ 不依赖 model/      # 不调用 LLM
               ✗ 不依赖 diagnoser/  # 不参与推理
               ✗ 不依赖 fixer/      # 只接收 FixProposal 作为输入
```

## 4. Prompt 强化 — 确保生成验证步骤

### 4.1 Prompt 修改（`fixer/prompts.py` SYSTEM_PROMPT）

将规则4从：
> "4. 验证步骤放在末尾，is_verification=true，验证命令必须为只读操作（如 lsblk、systemctl status）"

强化为：
> "4. **必须至少包含 1 个验证步骤**（is_verification=true），放在 steps 末尾，用于确认修复是否生效。验证命令必须为只读操作（如 lsblk、systemctl status、docker ps、kubectl get）。**缺少验证步骤的输出视为不合格。**"

### 4.2 后处理兜底校验（`fixer/agent.py` generate()）

在 `parse_fix_response()` 之后、`render_all()` 之前，检查 `suggestion.steps` 是否含 `is_verification=True`。若无，根据 `env_info` 补一个兜底验证步骤：

```python
def _ensure_verification_step(
    suggestion: FixSuggestion,
    env_info: EnvInfo,
) -> FixSuggestion:
    """确保 suggestion 至少含一个验证步骤

    若 LLM 未生成验证步骤，根据环境类型补一个兜底验证命令。
    """
    has_verify = any(s.is_verification for s in suggestion.steps)
    if has_verify:
        return suggestion

    # 根据环境类型选择兜底验证命令
    fallback_cmd = _fallback_verify_command(env_info, suggestion)
    suggestion.steps.append(fallback_cmd)
    suggestion.source = FixSource.LLM_FALLBACK
    return suggestion
```

兜底验证命令映射：

| 环境条件 | 兜底验证命令 | description |
|---------|------------|-------------|
| CONTAINER + KUBERNETES | `kubectl get pods -n kube-system` | 验证 Kubernetes Pod 状态 |
| CONTAINER + DOCKER/UNKNOWN | `docker ps` | 验证容器运行状态 |
| VM/BARE_METAL + fault_scope 含"存储/磁盘" | `lsblk` | 验证磁盘可见性 |
| VM/BARE_METAL + fault_scope 含"网络" | `ss -tlnp` | 验证网络端口监听 |
| VM/BARE_METAL + 其他 | `systemctl status galaxy-* --no-pager` | 验证银河平台服务状态 |

> 兜底验证命令不完美（可能不直接验证修复点），但确保 VERIFYING 步骤总有可执行的内容。LLM 正常生成时此逻辑不触发。

## 5. Engine 集成

### 5.1 修改 `_do_executing` — 只执行修复命令

**变更**：将 `proposal.commands` 中 `is_verification=False` 的命令过滤出来传给 `executor.run()`，验证命令留在 proposal 中由 VERIFYING 消费。

```python
def _do_executing(self) -> None:
    """EXECUTING: 只执行修复命令（is_verification=False）"""
    proposal = self.state.fix

    # 过滤出修复命令（不含验证步骤）
    fix_commands = [cmd for cmd in proposal.commands if not cmd.is_verification]
    # 构造只含修复命令的临时 proposal 供 executor 执行
    fix_only_proposal = FixProposal(
        commands=fix_commands,
        script=proposal.script,
        script_language=proposal.script_language,
        risk_notes=proposal.risk_notes,
        impact_scope=proposal.impact_scope,
        source=proposal.source,
    )

    exec_result = executor.run(fix_only_proposal)
    # ... 后续处理不变（成功→VERIFYING，失败→回滚）
```

### 5.2 重写 `_do_verifying` — 真实验证 + 失败处理

```python
def _do_verifying(self) -> None:
    """VERIFYING: 执行验证命令并判定修复是否生效"""
    # verifier 随 audit/danger/executor/snapshot 一起在 engine 顶部导入
    # （对齐 engine.py 现有 from galaxy_diag.safety import executor 模式）
    from galaxy_diag.safety import verifier

    proposal = self.state.fix

    # 1. 执行验证
    verify_result = verifier.verify(proposal)

    # 2. 展示验证结果
    display.print_verify_result(verify_result)

    if verify_result.success:
        # 验证通过
        self._console.print("[success]✓ 修复验证通过[/success]")
        self._write_audit(result="success")
        self._mark_done("修复验证通过")
    else:
        # 验证失败：展示失败详情 + 进一步方案 + 回滚提示
        self._console.print(
            f"\n[danger]✗ 修复验证失败: 步骤 {verify_result.failed_step} "
            f"\"{verify_result.failed_description}\" 返回非零退出码[/danger]"
        )

        # 展示进一步解决方案概要
        display.print_next_steps(
            proposal=proposal,
            snapshot_id=self.state.snapshot.snapshot_id if self.state.snapshot else None,
        )

        self._write_audit(result="verify_failed")
        self._mark_done("修复验证未通过")
```

### 5.3 验证失败时的展示内容（`display.print_next_steps`）

```
━━━ 进一步排查建议 ━━━

本次修复未解决问题，建议按以下顺序排查：

  1. 检查验证命令的输出日志，确认具体失败原因
  2. 使用 galaxy-diag snapshot rollback <snapshot_id> 一键回滚到修复前状态
  3. 回滚后，补充以下信息重新运行诊断：
     - 验证命令的完整输出和错误信息
     - 修复执行期间系统日志: journalctl --since "5 minutes ago"
     - 受影响服务的当前状态
  4. 重新运行: galaxy-diag run -d "补充描述" --resume

⚠ 回滚提示:
  执行 galaxy-diag snapshot rollback <snapshot_id>
  可恢复到修复前的系统状态
```

### 5.4 无验证命令时的提示

当 `verify_result.total_steps == 0` 时，额外展示：
> "本次修复建议未包含验证步骤，建议人工确认修复效果"

## 6. Display 层新增

### 6.1 `print_verify_result(result: VerifyResult)`

| 场景 | 渲染 |
|------|------|
| 成功 | `✓ 验证通过: 3/3 步骤成功` |
| 成功（无验证命令） | `⚠ 未执行验证（修复建议无验证步骤），建议人工确认` |
| 失败 | `✗ 验证失败: 步骤 2 "验证CNI Pod已恢复" 返回非零退出码` + 通过/失败步骤明细表 |

### 6.2 `print_next_steps(proposal, snapshot_id)`

渲染进一步排查建议面板（§5.3 内容），使用 Rich Panel + 项目符号列表。

## 7. safety/__init__.py 导出变更

新增 `verifier.verify` 到 `__all__`（与 executor 的 `execute` 导出对称）：

```python
from galaxy_diag.safety.verifier import verify

__all__ = [
    # ... 现有导出 ...
    "verify",
]
```

> engine.py 采用子模块导入（`from galaxy_diag.safety import verifier`），与现有的 `executor` / `snapshot` / `audit` 用法一致。`__init__.py` 的导出供外部调用方使用。

## 8. 不改动

| 模块 | 原因 |
|------|------|
| `safety/executor.py` | 接口不变，仅调用方过滤传入的 commands 列表 |
| `safety/snapshot.py` | 仅被 engine 调用，接口不变 |
| `safety/audit.py` | 仅被 engine 调用，函数接口不变（AuditRecord.result 的 Literal 新增 "verify_failed" 见 §2.2） |
| `workflow/persist.py` | SessionStatus 由 history 推导，无需新增枚举 |
| `workflow/states.py` | VERIFYING 转换规则不变 |
| `fixer/postprocess.py` | 不变（兜底校验在 agent.py 层做，避免传入 env_info 改接口） |
| `fixer/template.py` | 不变 |
| `fixer/generator.py` | 不变 |

## 9. 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `shared/types.py` | 修改 | 新增 VerifyResult 数据类；AuditRecord.result Literal 新增 "verify_failed" |
| `safety/verifier.py` | **新建** | 验证器核心逻辑（~80行） |
| `safety/__init__.py` | 修改 | 新增 verify 导出 |
| `fixer/prompts.py` | 修改 | SYSTEM_PROMPT 规则4强化措辞 |
| `fixer/agent.py` | 修改 | generate() 中新增 _ensure_verification_step 兜底校验 |
| `workflow/engine.py` | 修改 | _do_executing 过滤验证命令 + _do_verifying 真实验证 |
| `workflow/cli/display.py` | 修改 | 新增 print_verify_result + print_next_steps |

## 10. 验收对照

### 任务书相关验收标准

| 验收标准 | 实现位置 |
|---------|---------|
| REQ-F-02: 按核心流程编排到"结果验证"步骤 | engine._do_verifying + safety/verifier.py |
| REQ-D-02: 生成的脚本包含验证步骤 | fixer/prompts.py 强化 + fixer/agent.py 兜底 |
| 场景2/3: 执行并验证 | verifier.run() 执行 is_verification 命令 |
| 端到端演示 | 步骤6执行修复 + 步骤7执行验证，完整闭环 |

### 红线对照

| 红线 | 本设计满足情况 |
|------|-------------|
| 红线1 离线可用 | verifier.py 纯 subprocess，无公网依赖 |
| 红线2 写操作人工确认 | 验证命令均为只读（is_verification=True），无需额外确认；验证失败不自动回滚，提示用户决定 |
| 红线3 端到端演示 | 步骤6+7 可用真实/mock 数据走通 |

## 11. 测试要点

| 测试场景 | 预期 |
|---------|------|
| 验证命令全部退出码=0 | VerifyResult.success=True |
| 某验证命令退出码≠0 | VerifyResult.success=False, failed_step 指向失败步骤 |
| 无验证命令 | VerifyResult.success=True, output 含"无验证命令"提示 |
| 验证命令超时 | 视为失败 |
| _ensure_verification_step: LLM 已生成验证步骤 | 不补兜底，返回原 suggestion |
| _ensure_verification_step: LLM 未生成验证步骤 | 补兜底验证步骤，source 标记 LLM_FALLBACK |
| engine: 修复命令执行成功 → VERIFYING | 验证命令在步骤7执行 |
| engine: 验证失败 | 展示进一步方案 + 回滚命令，标记 done |
