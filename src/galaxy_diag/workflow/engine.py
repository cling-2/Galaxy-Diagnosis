"""工作流引擎：状态机主循环与步骤编排

对应 workflow-design.md §2 状态机 + §7 与 CLI 的集成。
当前各步骤回调为 stub（返回 mock 数据），业务模块实现后替换回调即可。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Protocol

from galaxy_diag.collector import collect_env
from galaxy_diag.shared.errors import (
    GalaxyDiagError,
    ModelUnavailableError,
    WorkflowError,
)
from galaxy_diag.shared.types import (
    CommandTemplate,
    Confidence,
    DiagnosisResult,
    EnvInfo,
    EnvironmentType,
    FixProposal,
    HardwareInfo,
    SessionStatus,
    SnapshotMeta,
    StorageInfo,
    WorkflowStep,
    WorkflowState,
)
from galaxy_diag.workflow.cli import display, interact
from galaxy_diag.workflow.persist import (
    find_resumable_sessions,
    generate_session_id,
    load_state,
    save_state,
)
from galaxy_diag.workflow.states import (
    EXECUTING_NEXT_ON_FAILURE,
    REVIEWING_NEXT_ON_REJECT,
    STEP_DESCRIPTIONS,
    STEP_LABELS,
    VERIFYING_NEXT_ON_FAILURE,
    VERIFYING_NEXT_ON_SUCCESS,
    is_valid_transition,
)


# ===== 步骤回调协议 =====


class CollectResult:
    """collect 步骤返回值"""

    env_info: EnvInfo
    should_skip_diagnose: bool = False  # 已知故障模式短路


class DiagnoseResult:
    """diagnose 步骤返回值"""

    diagnosis: DiagnosisResult
    should_stop: bool = False  # 只需诊断，不进入修复


class FixResult:
    """fix 步骤返回值（含安全检测）"""

    proposal: FixProposal


class ExecuteResult:
    """execute 步骤返回值"""

    success: bool
    output: str = ""


class VerifyResult:
    """verify 步骤返回值"""

    success: bool
    message: str = ""


# ===== 用户审核选择 =====


class ReviewChoice:
    YES = "yes"
    NO = "no"
    EDIT = "edit"


# ===== 引擎主体 =====


class WorkflowEngine:
    """工作流状态机引擎

    职责：
    1. 按 workflow-design.md §2.3 转换规则驱动状态机
    2. 每步转换后立即持久化
    3. 调用 display.py 渲染结果、interact.py 等待人工确认
    4. 支持逐步/自动两种模式
    """

    def __init__(
        self,
        state: WorkflowState,
        *,
        auto: bool = False,
        verbose: bool = False,
    ):
        """
        Args:
            state: 初始工作流状态
            auto: 自动模式（中间步骤只展示不暂停，REVIEWING 仍需人工）
            verbose: 详细输出
        """
        self.state = state
        self.auto = auto
        self.verbose = verbose
        self._console = display.get_console()

    # ===== 公开接口 =====

    def run(self) -> None:
        """运行工作流直到终态

        主循环：读取 current_step → 执行步骤 → 转换状态 → 持久化 → 重复。
        可被 Ctrl+C 中断，状态已落盘，下次 resume 继续。
        """
        while self.state.session_status == SessionStatus.ACTIVE:
            step = self.state.current_step
            self._print_step_header(step)

            try:
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
                elif step == WorkflowStep.SNAPSHOT:
                    self._do_snapshot()
                elif step == WorkflowStep.EXECUTING:
                    self._do_executing()
                elif step == WorkflowStep.VERIFYING:
                    self._do_verifying()
                else:
                    raise WorkflowError(f"未知步骤: {step.value}")
            except KeyboardInterrupt:
                # Ctrl+C: 状态已落盘，提示 resume
                self._console.print("\n[dim]已中断，状态已保存。使用 galaxy-diag run --resume 恢复[/dim]")
                return
            except ModelUnavailableError as e:
                # LLM 不可用：保存状态，提示恢复服务后 resume
                self._console.print(f"\n[danger]✗ 推理服务不可用: {e.message}[/danger]")
                if e.hint:
                    self._console.print(f"  💡 {e.hint}")
                self._console.print("[dim]  请恢复推理服务后使用 galaxy-diag run --resume 恢复[/dim]")
                return
            except GalaxyDiagError as e:
                # 业务错误：展示错误，保存状态
                self._console.print(f"\n[danger]✗ {e.message}[/danger]")
                if e.hint:
                    self._console.print(f"  💡 {e.hint}")
                self._save()
                return

    @classmethod
    def start_new(
        cls,
        problem_description: str,
        *,
        auto: bool = False,
        verbose: bool = False,
    ) -> WorkflowEngine:
        """创建新工作流

        Args:
            problem_description: 用户问题描述
            auto: 自动模式
            verbose: 详细输出

        Returns:
            初始化后的 WorkflowEngine
        """
        state = WorkflowState(
            session_id=generate_session_id(),
            current_step=WorkflowStep.ENV_RECOGNISING,
            problem_description=problem_description,
        )
        engine = cls(state, auto=auto, verbose=verbose)
        engine._save()
        return engine

    @classmethod
    def resume(
        cls,
        session_id: str,
        *,
        auto: bool = False,
        verbose: bool = False,
    ) -> WorkflowEngine:
        """恢复已有工作流

        Args:
            session_id: 会话 ID
            auto: 自动模式
            verbose: 详细输出

        Returns:
            恢复后的 WorkflowEngine

        Raises:
            WorkflowError: 会话不存在或已完成
        """
        state = load_state(session_id)

        if state.session_status != SessionStatus.ACTIVE:
            raise WorkflowError(
                f"会话 {session_id} 已结束 (状态: {state.session_status.value})",
                hint="已完成/已拒绝/已回滚的会话无法恢复，请启动新工作流",
            )

        engine = cls(state, auto=auto, verbose=verbose)
        engine._console.print(
            f"[info]恢复会话: {session_id}[/info] "
            f"(当前步骤: {STEP_LABELS.get(state.current_step, state.current_step.value)})"
        )
        return engine

    @classmethod
    def find_and_prompt_resume(
        cls,
        *,
        auto: bool = False,
        verbose: bool = False,
    ) -> WorkflowEngine | None:
        """查找未完成会话并提示用户是否恢复

        Returns:
            WorkflowEngine（用户选择恢复），或 None（用户选择新建）
        """
        resumable = find_resumable_sessions()
        if not resumable:
            return None

        console = display.get_console()
        console.print(f"[info]检测到 {len(resumable)} 个未完成会话:[/info]")
        for s in resumable:
            step_label = STEP_LABELS.get(s.current_step, s.current_step.value)
            desc = s.problem_description[:50] + "..." if len(s.problem_description) > 50 else s.problem_description
            console.print(f"  [dim]•[/dim] {s.session_id} (步骤: {step_label}, 问题: {desc})")

        if interact.confirm("是否恢复最近的会话?", default=True):
            latest = resumable[-1]
            return cls.resume(latest.session_id, auto=auto, verbose=verbose)

        return None

    # ===== 步骤实现 =====

    def _do_env_recognising(self) -> None:
        """ENV_RECOGNISING: 环境感知

        调用 collector 模块识别环境类型、采集软硬件信息。
        """
        env_info = collect_env()

        self.state.env_info = env_info
        display.print_env_info(env_info)

        # # 逐步模式：ENV_RECOGNISING 后允许用户查看采集结果
        # if not self.auto:
        #     if not interact.confirm("环境识别完成，是否继续?", default=True):
        #         self._console.print("[dim]工作流已暂停，可使用 --resume 恢复[/dim]")
        #         return

        self._transition(WorkflowStep.COLLECTING)

    def _do_collecting(self) -> None:
        """COLLECTING: 信息采集

        基于环境信息 + 问题描述采集诊断上下文。
        当前为 stub：复用 env_info。
        """
        self._console.print("[info]采集诊断信息...[/info]")
        # 当前为 stub：env_info 已在 ENV_RECOGNISING 步骤采集
        # 实际实现：根据问题描述调用对应 Tool 采集日志/网络/存储信息
        display.print_stub_notice("REQ-B", "信息采集")

        # 逐步模式：COLLECTING 后允许用户查看采集结果、补充描述
        if not self.auto:
            self._console.print("\n[info]采集结果已展示，可补充描述或直接继续[/info]")
            supplement = interact.prompt_input("补充描述（回车跳过）", default="")
            if supplement.strip():
                self.state.problem_description += f"\n[补充] {supplement.strip()}"
                self._save()
            if not interact.confirm("信息采集完成，是否继续?", default=True):
                self._console.print("[dim]工作流已暂停，可使用 --resume 恢复[/dim]")
                return

        self._transition(WorkflowStep.DIAGNOSING)

    def _do_diagnosing(self) -> None:
        """DIAGNOSING: 根因分析

        调用 diagnoser 模块推理根因。
        当前为 stub：返回 mock 诊断结果。
        """
        display.print_stub_notice("REQ-C", "诊断分析")
        diagnosis = self._stub_diagnose()

        self.state.diagnosis = diagnosis
        display.print_diagnosis(diagnosis)

        # 分支判断
        if diagnosis.confidence == Confidence.INSUFFICIENT:
            # 信息不足，回退到 COLLECTING 补充采集
            self._console.print("\n[warning]⚠ 信息不足，需要补充采集[/warning]")
            if not self.auto:
                if interact.confirm("是否补充采集信息?", default=True):
                    self._transition(WorkflowStep.COLLECTING)
                else:
                    self._console.print("[dim]跳过补充采集，基于当前信息继续[/dim]")
                    self._transition(WorkflowStep.PLANNING)
            else:
                # 自动模式：自动回退补充采集
                self._transition(WorkflowStep.COLLECTING)
            return

        # CONFIRMED / SUSPECTED：继续到 PLANNING
        if not self.auto:
            # 逐步模式：展示结论后等待用户确认
            if not interact.confirm("是否继续生成修复建议?", default=True):
                # 只需诊断，不进入修复
                self._mark_done("用户选择仅查看诊断结论")
                return

        self._transition(WorkflowStep.PLANNING)

    def _do_planning(self) -> None:
        """PLANNING: 修复建议生成

        调用 fixer 模块生成修复命令/脚本。
        当前为 stub：返回 mock 修复建议。
        """
        display.print_stub_notice("REQ-D", "修复生成")
        proposal = self._stub_fix()

        self.state.fix = proposal
        display.print_fix_proposal(proposal)

        # 逐步模式下允许编辑参数
        if not self.auto and proposal.commands:
            has_editable = any(cmd.editable_params for cmd in proposal.commands)
            if has_editable and interact.confirm("是否编辑修复参数?", default=False):
                self._edit_fix_params(proposal)

        # 逐步模式：PLANNING 后等待用户确认继续
        if not self.auto:
            if not interact.confirm("修复建议已生成，是否继续进入安全检测?", default=True):
                self._console.print("[dim]工作流已暂停，可使用 --resume 恢复[/dim]")
                return

        self._transition(WorkflowStep.SECURITY_CHECKING)

    def _do_security_checking(self) -> None:
        """SECURITY_CHECKING: 安全检测

        对修复建议做多维检测（语法/危险/兼容性）。
        当前为 stub：直接通过。
        """
        self._console.print("[info]执行安全检测...[/info]")
        # 当前为 stub：直接标记通过
        # 实际实现：调用 fixer.checker + safety.danger 做多维检测
        if self.state.fix:
            self.state.fix.check_passed = True
            self.state.fix.check_issues = []

        proposal = self.state.fix
        if proposal and not proposal.check_passed:
            # 检测失败，展示问题后回退到 PLANNING
            self._console.print("\n[danger]✗ 安全检测未通过[/danger]")
            for issue in proposal.check_issues:
                self._console.print(f"  [danger]- {issue}[/danger]")
            self._console.print("[dim]回退到修复建议生成步骤[/dim]")
            self._transition(WorkflowStep.PLANNING)
            return

        self._console.print("[success]✓ 安全检测通过[/success]")
        self._transition(WorkflowStep.REVIEWING)

    def _do_reviewing(self) -> None:
        """REVIEWING: 人工审核

        展示修复建议 + 风险评估，等待用户确认/拒绝/修改。
        此步骤始终需要人工确认（红线 2），无论逐步还是自动模式。
        """
        proposal = self.state.fix
        if not proposal:
            raise WorkflowError("无可审核的修复建议")

        # 展示操作摘要
        self._console.print("\n[heading]📋 操作摘要[/heading]")
        if proposal.commands:
            self._console.print("[dim]将要执行以下命令:[/dim]")
            for i, cmd in enumerate(proposal.commands, 1):
                self._console.print(f"  {i}. [info]{cmd.command}[/info]")
                if cmd.description:
                    self._console.print(f"     {cmd.description}")

        if proposal.impact_scope:
            self._console.print(f"\n[warning]📊 影响范围: {proposal.impact_scope}[/warning]")

        if proposal.risk_notes:
            self._console.print("[warning]⚠ 风险提示:[/warning]")
            for note in proposal.risk_notes:
                self._console.print(f"  - {note}")

        # 三选一交互
        self._console.print("\n请选择操作:")
        self._console.print("  [success]y[/success] - 确认执行")
        self._console.print("  [danger]n[/danger] - 拒绝（终止工作流）")
        self._console.print("  [info]e[/info] - 编辑参数后重新检测")

        try:
            choice = input("请输入 (y/n/e): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            # 中断不是拒绝：保留当前状态，用户可 resume 继续
            raise

        if choice in ("y", "yes"):
            self._transition(WorkflowStep.SNAPSHOT)
        elif choice in ("e", "edit"):
            # 编辑参数后回到 PLANNING 重走安全检测
            if proposal.commands:
                self._edit_fix_params(proposal)
            self._transition(WorkflowStep.PLANNING)
        else:
            # 拒绝：不执行且不反复要求确认
            self._console.print("[dim]用户拒绝执行，工作流终止[/dim]")
            self._mark_rejected()

    def _do_snapshot(self) -> None:
        """SNAPSHOT: 创建恢复快照

        执行前创建恢复快照，用于失败回滚。
        当前为 stub：创建 mock 快照元数据。
        """
        self._console.print("[info]创建恢复快照...[/info]")
        # 当前为 stub：创建 mock 快照
        # 实际实现：调用 safety.snapshot 备份受影响的文件和服务状态
        display.print_stub_notice("REQ-E-03", "操作快照")

        proposal = self.state.fix
        affected_files = []
        affected_services = []
        if proposal and proposal.commands:
            for cmd in proposal.commands:
                if cmd.editable_params:
                    affected_files.extend(cmd.editable_params.values())

        snapshot = SnapshotMeta(
            snapshot_id=f"snap_{self.state.session_id[-8:]}",
            timestamp=datetime.now(),
            operation_summary="; ".join(
                cmd.command for cmd in (proposal.commands if proposal else [])
            )[:100],
            affected_files=affected_files,
            affected_services=affected_services,
            backup_path=f"~/.galaxy-diag/snapshots/snap_{self.state.session_id[-8:]}",
        )
        self.state.snapshot = snapshot
        self._console.print(f"[success]✓ 快照已创建: {snapshot.snapshot_id}[/success]")
        self._transition(WorkflowStep.EXECUTING)

    def _do_executing(self) -> None:
        """EXECUTING: 执行修复

        按步骤执行修复命令并监控。
        当前为 stub：模拟执行成功。
        """
        self._console.print("[info]执行修复...[/info]")
        # 当前为 stub：模拟执行
        # 实际实现：调用 safety 受控执行器，逐步执行命令
        display.print_stub_notice("REQ-D", "修复执行")

        # stub: 模拟成功
        self._console.print("[success]✓ 修复执行完成（模拟）[/success]")
        self._transition(WorkflowStep.VERIFYING)

    def _do_verifying(self) -> None:
        """VERIFYING: 结果验证

        验证修复是否生效。
        当前为 stub：模拟验证成功。
        """
        self._console.print("[info]验证修复结果...[/info]")
        # 当前为 stub：模拟验证成功
        # 实际实现：调用 diagnoser 验证或执行检查命令
        display.print_stub_notice("REQ-C", "结果验证")

        # stub: 模拟成功
        self._console.print("[success]✓ 修复验证通过（模拟）[/success]")
        self._mark_done("修复验证通过")

    # ===== 状态转换 =====

    def _transition(self, next_step: WorkflowStep) -> None:
        """执行状态转换：校验合法性 → 更新状态 → 记录 history → 持久化"""
        current = self.state.current_step

        # 校验转换合法性
        if not is_valid_transition(current, next_step):
            # 特殊转换（回退/跳过）由步骤方法直接处理，这里兜底报错
            self._console.print(
                f"[warning]⚠ 状态转换 {current.value} → {next_step.value} 不在标准路径中[/warning]"
            )

        self.state.current_step = next_step
        self.state.history.append({
            "step": next_step.value,
            "timestamp": datetime.now().isoformat(),
            "result": "entered",
        })
        self._save()

    def _mark_done(self, reason: str = "") -> None:
        """标记工作流完成"""
        self.state.history.append({
            "step": "done",
            "timestamp": datetime.now().isoformat(),
            "result": "done",
            "reason": reason,
        })
        self._save()
        self._console.print(f"\n[success]✅ 工作流完成[/success] {reason}")

    def _mark_rejected(self) -> None:
        """标记用户拒绝"""
        self.state.history.append({
            "step": "rejected",
            "timestamp": datetime.now().isoformat(),
            "result": "rejected",
        })
        self._save()
        self._console.print("[dim]工作流已终止（用户拒绝）[/dim]")

    def _mark_rollback(self, reason: str = "") -> None:
        """标记回滚（执行失败后从快照恢复）"""
        self.state.history.append({
            "step": "rollback",
            "timestamp": datetime.now().isoformat(),
            "result": "rollback",
            "reason": reason,
        })
        self._save()
        self._console.print(f"[warning]⚠ 执行失败，已从快照回滚: {reason}[/warning]")

    def _save(self) -> None:
        """持久化当前状态"""
        save_state(self.state)

    # ===== 辅助方法 =====

    def _print_step_header(self, step: WorkflowStep) -> None:
        """打印步骤标题"""
        label = STEP_LABELS.get(step, step.value)
        desc = STEP_DESCRIPTIONS.get(step, "")
        step_num = _step_number(step)
        self._console.print(
            f"\n[heading]━━━ 步骤 {step_num}/{len(STEP_LABELS)}: {label} ━━━[/heading]"
        )
        if desc and self.verbose:
            self._console.print(f"[dim]  {desc}[/dim]")

    def _edit_fix_params(self, proposal: FixProposal) -> None:
        """交互式编辑修复参数"""
        for cmd in proposal.commands:
            if cmd.editable_params:
                new_params = interact.prompt_edit_params(
                    template=cmd.command,
                    placeholders=cmd.editable_params,
                )
                # 替换命令中的占位符
                for name, value in new_params.items():
                    cmd.command = cmd.command.replace(f"<{name}>", value)
                cmd.editable_params = new_params

        # 编辑后重新展示
        display.print_fix_proposal(proposal)

    # ===== Stub 回调（mock 数据，业务模块实现后替换） =====

    def _stub_diagnose(self) -> DiagnosisResult:
        """Stub: 诊断根因分析"""
        return DiagnosisResult(
            root_cause="VM 磁盘控制器驱动 `vmw_pvscsi` 未加载，导致 SCSI 设备不可见",
            confidence=Confidence.SUSPECTED,
            evidence=[
                "lsblk 仅显示系统盘 sda",
                "dmesg 中发现 'pvscsi: unknown device' 警告",
                "VM 硬件配置使用 VMware 半虚拟化 SCSI 控制器",
            ],
            missing_info=[],
            env_type=EnvironmentType.VM,
        )

    def _stub_fix(self) -> FixProposal:
        """Stub: 修复建议生成"""
        return FixProposal(
            commands=[
                CommandTemplate(
                    command="modprobe <DRIVER_MODULE>",
                    description="加载磁盘控制器驱动模块",
                    risk_note="加载内核模块",
                    editable_params={"DRIVER_MODULE": "vmw_pvscsi"},
                ),
                CommandTemplate(
                    command="rescan-scsi-bus.sh",
                    description="重新扫描 SCSI 总线",
                    risk_note="无",
                    editable_params={},
                ),
                CommandTemplate(
                    command="lsblk",
                    description="验证磁盘是否可见",
                    risk_note="只读操作",
                    editable_params={},
                ),
            ],
            script=None,
            script_language=None,
            risk_notes=["加载内核模块可能影响系统稳定性"],
            check_passed=True,
            check_issues=[],
            impact_scope="加载内核模块 vmw_pvscsi，扫描 SCSI 总线",
        )


def _step_number(step: WorkflowStep) -> int:
    """获取步骤序号（1-based）"""
    try:
        return list(STEP_LABELS.keys()).index(step) + 1
    except ValueError:
        return 0
