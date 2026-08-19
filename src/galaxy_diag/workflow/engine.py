"""工作流引擎：状态机主循环与步骤编排

对应 workflow-design.md §2 状态机 + §7 与 CLI 的集成。
当前各步骤回调为 stub（返回 mock 数据），业务模块实现后替换回调即可。

用户可见步骤（7 步）：
  环境识别 → 信息收集 → 根因分析 → 修复建议 → 人工审核 → 执行 → 结果验证

内部状态（10 个）与用户可见步骤的映射：
  - 修复建议 = PLANNING + SECURITY_CHECKING（生成后检测在建议末尾执行）
  - 人工审核 = EXECUTION_GUARD（执行前熔断）+ REVIEWING（审核确认）
  - 执行     = SNAPSHOT（自动创建快照）+ EXECUTING
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
    CheckSeverity,
    CommandTemplate,
    Confidence,
    DiagnosisResult,
    DiagnosisSource,
    EnvInfo,
    EnvironmentType,
    FixProposal,
    FixSource,
    GuardResult,
    HardwareInfo,
    ReviewDecision,
    SessionStatus,
    StorageInfo,
    WorkflowStep,
    WorkflowState,
)
from galaxy_diag.safety import audit, danger, executor, snapshot
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
    STEP_TO_USER_STEP,
    TOTAL_USER_STEPS,
    VERIFYING_NEXT_ON_FAILURE,
    VERIFYING_NEXT_ON_SUCCESS,
    is_valid_transition,
)


# 诊断回退次数上限（防止 LLM 反复返回 INSUFFICIENT 死循环）
MAX_DIAGNOSING_RETRIES = 2

# 安全检测回退次数上限（防止 D-03 反复失败导致 PLANNING 死循环）
MAX_SECURITY_RETRIES = 2


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
    5. 用户可见 7 步，内部 10 个状态自动映射
    """

    def __init__(
        self,
        state: WorkflowState,
        *,
        auto: bool = False,
        verbose: bool = False,
        user_log_files: list[str] | None = None,
        mock: bool = False,
    ):
        """
        Args:
            state: 初始工作流状态
            auto: 自动模式（中间步骤只展示不暂停，REVIEWING 仍需人工）
            verbose: 详细输出
            user_log_files: 用户上传的日志文件路径（被动接收，--log-file）
            mock: Mock 模式（使用预设响应，不连接真实 LLM，用于测试）
        """
        self.state = state
        self.auto = auto
        self.verbose = verbose
        self.mock = mock
        self._user_log_files = user_log_files or []
        self._console = display.get_console()
        self._last_user_step_num = 0  # 上一次打印的用户可见步骤编号（避免重复打印步骤标题）
        self._fix_retry_feedback: list[str] | None = None  # D-03 CRITICAL 失败反馈（回退 PLANNING 时回灌）

        # 初始化 ModelAdapter（延迟导入避免循环依赖）
        if mock:
            from galaxy_diag.model.mock_client import MockModelAdapter

            self._model_adapter = MockModelAdapter()
            self._console.print("[dim]  [Mock 模式] 使用预设响应，不连接真实推理服务[/dim]")
        else:
            from galaxy_diag.config.settings import load_config
            from galaxy_diag.model.client import ModelAdapter

            config = load_config()
            self._model_adapter = ModelAdapter(config.llm)

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
                elif step == WorkflowStep.EXECUTION_GUARD:
                    self._do_execution_guard()
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
        user_log_files: list[str] | None = None,
        mock: bool = False,
    ) -> WorkflowEngine:
        """创建新工作流

        Args:
            problem_description: 用户问题描述
            auto: 自动模式
            verbose: 详细输出
            user_log_files: 用户上传的日志文件路径（--log-file）
            mock: Mock 模式（使用预设响应，不连接真实 LLM）

        Returns:
            初始化后的 WorkflowEngine
        """
        state = WorkflowState(
            session_id=generate_session_id(),
            current_step=WorkflowStep.ENV_RECOGNISING,
            problem_description=problem_description,
        )
        engine = cls(state, auto=auto, verbose=verbose, user_log_files=user_log_files, mock=mock)
        engine._save()
        return engine

    @classmethod
    def resume(
        cls,
        session_id: str,
        *,
        auto: bool = False,
        verbose: bool = False,
        user_log_files: list[str] | None = None,
        mock: bool = False,
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

        engine = cls(state, auto=auto, verbose=verbose, user_log_files=user_log_files, mock=mock)
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
        mock: bool = False,
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
            return cls.resume(latest.session_id, auto=auto, verbose=verbose, mock=mock)

        return None

    # ===== 步骤实现 =====

    def _do_env_recognising(self) -> None:
        """ENV_RECOGNISING: 环境识别

        调用 collector 模块识别环境类型、采集软硬件信息。
        用户可见步骤 1/7: 环境识别

        B类：完成后检查规则预匹配，CONFIRMED 则跳过 COLLECTING。
        C类：根据问题类型决定是否跳过完整硬件采集。
        """
        from galaxy_diag.diagnoser.context import should_collect_hardware
        from galaxy_diag.diagnoser.rules import prematch_rules_by_description

        # C类：判断是否跳过完整硬件采集
        skip_hw = not should_collect_hardware(self.state.problem_description)
        self.state.should_skip_hardware = skip_hw

        env_info = collect_env(skip_hardware=skip_hw)

        self.state.env_info = env_info
        display.print_env_info(env_info, skip_hardware=skip_hw)

        # B类：规则预匹配，CONFIRMED 则跳过 COLLECTING + DIAGNOSING
        if env_info is not None:
            pre_diagnosis = prematch_rules_by_description(
                self.state.problem_description,
                env_info.env_type,
            )
            if pre_diagnosis is not None:
                self.state.diagnosis = pre_diagnosis
                self.state.should_skip_collecting = True
                display.print_diagnosis(pre_diagnosis)
                self._console.print("[dim]已知故障模式，跳过信息采集和深度诊断[/dim]")
                self._save()
                self._transition(WorkflowStep.PLANNING)
                return

        self._transition(WorkflowStep.COLLECTING)

    def _do_collecting(self) -> None:
        """COLLECTING: 诊断信息采集

        调用 build_diagnostic_context() 按问题描述定向采集，
        产出 DiagnosticContext 写入 WorkflowState。
        用户可见步骤 2/7: 信息收集
        """
        # B类：已知故障模式跳过采集
        if self.state.should_skip_collecting:
            self._console.print("[dim]已知故障模式，跳过信息采集[/dim]")
            self._transition(WorkflowStep.PLANNING)
            return

        from galaxy_diag.diagnoser import build_diagnostic_context
        from galaxy_diag.diagnoser.rules import match_rules

        if not self.state.env_info:
            raise WorkflowError(
                "缺少环境信息，请先完成环境感知步骤",
                hint="工作流应从 ENV_RECOGNISING 开始",
            )

        self._console.print("[info]采集诊断信息...[/info]")
        ctx = build_diagnostic_context(
            problem_description=self.state.problem_description,
            env_info=self.state.env_info,
            user_log_files=self._user_log_files,
            existing_context=self.state.diagnostic_context,  # 增量采集
        )

        self.state.diagnostic_context = ctx
        display.print_diagnostic_context(ctx)
        self._save()

        # 反幻觉：事实校验（采集后、诊断前）
        from galaxy_diag.diagnoser.hallucination_guard import check_facts

        hallucination_result = check_facts(
            self.state.problem_description, ctx,
        )
        if hallucination_result is not None:
            self.state.hallucination_check_result = hallucination_result.rule_id
            self._save()

            if hallucination_result.contradiction:
                self._console.print(
                    f"\n[warning]⚠ 反幻觉校验: {hallucination_result.message}[/warning]"
                )
                # 写审计日志
                self._write_audit(result="failure", user_input="")
                self._mark_done(f"反幻觉拦截: {hallucination_result.message}")
                return

        # 短路预检：已知故障模式可跳过 DIAGNOSING（REQ-F-02 验收标准 4）
        pre_diagnosis = match_rules(ctx)
        if pre_diagnosis is not None and pre_diagnosis.confidence == Confidence.CONFIRMED:
            pre_diagnosis.diagnosis_source = DiagnosisSource.RULE_MATCH
            self.state.diagnosis = pre_diagnosis
            display.print_diagnosis(pre_diagnosis)
            self._console.print("[dim]已知故障模式，跳过深度诊断[/dim]")
            self._save()
            self._transition(WorkflowStep.PLANNING)  # 短路跳过 DIAGNOSING
            return

        # 逐步模式：COLLECTING 后允许用户查看采集结果、补充描述
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

    def _do_diagnosing(self) -> None:
        """DIAGNOSING: 根因分析

        调用 diagnoser.diagnose() 推理根因（规则匹配 + LLM）。
        用户可见步骤 3/7: 根因分析
        """
        from galaxy_diag.diagnoser import diagnose

        if not self.state.diagnostic_context:
            raise WorkflowError(
                "缺少诊断上下文，请先完成信息采集步骤",
                hint="工作流应从 ENV_RECOGNISING 开始",
            )
        if not self.state.env_info:
            raise WorkflowError(
                "缺少环境信息，请先完成环境感知步骤",
                hint="工作流应从 ENV_RECOGNISING 开始",
            )

        with self._console.status(
            "[info]分析故障根因... LLM 推理中，纯 CPU 模式下可能需要 3-5 分钟[/info]",
            spinner="dots",
        ):
            diagnosis = diagnose(
                problem_description=self.state.problem_description,
                env_info=self.state.env_info,
                diagnostic_context=self.state.diagnostic_context,
                model_adapter=self._model_adapter,
            )

        self.state.diagnosis = diagnosis

        # 根据来源输出提示（异常处理：明确告知用户故障原因）
        if diagnosis.diagnosis_source == DiagnosisSource.ERROR_FALLBACK:
            self._console.print(
                "[error]⚠ LLM 推理服务不可用，已降级为信息不足结论[/error]"
            )
        elif diagnosis.diagnosis_source == DiagnosisSource.FORMAT_FALLBACK:
            self._console.print(
                "[warning]⚠ LLM 输出格式异常（非服务故障），"
                "模型未能生成结构化 JSON，已降级[/warning]"
            )
        elif diagnosis.diagnosis_source == DiagnosisSource.LLM_FALLBACK:
            self._console.print(
                "[warning]⚠ LLM 推理结果校验部分失败，已自动修复[/warning]"
            )

        display.print_diagnosis(diagnosis)
        self._save()

        # ── LLM 服务不可用时直接终止，不回退补充采集（否则死循环）──
        # ── FORMAT_FALLBACK（格式异常）不终止，降级后继续流程 ──
        if diagnosis.diagnosis_source == DiagnosisSource.ERROR_FALLBACK:
            self._console.print(
                "\n[error]✗ LLM 推理服务不可用，无法完成根因分析。[/error]"
            )
            self._console.print(
                "[dim]  请修复推理服务后使用 galaxy-diag run --resume 恢复[/dim]"
            )
            self._mark_done("LLM 推理服务不可用")
            return

        # FORMAT_FALLBACK：模型可用但输出格式异常，降级但继续流程
        # （小模型常见问题：能推理但不会严格输出 JSON）
        if diagnosis.diagnosis_source == DiagnosisSource.FORMAT_FALLBACK:
            self._console.print(
                "\n[warning]⚠ 模型输出格式异常，建议使用更大参数的模型"
                "（如 qwen3:8b）以获得结构化诊断结论[/warning]"
            )
            # 不终止，降级为 INSUFFICIENT 后继续到 PLANNING
            self._transition(WorkflowStep.PLANNING)
            return

        # 分支判断
        if diagnosis.confidence == Confidence.INSUFFICIENT:
            # 检查回退次数上限（防止 LLM 反复返回 insufficient 死循环）
            retry_count = sum(
                1 for h in self.state.history
                if h.get("step") == WorkflowStep.COLLECTING.value
                and h.get("result") == "entered"
            )
            if retry_count >= MAX_DIAGNOSING_RETRIES:
                self._console.print(
                    f"[warning]已回退补充采集 {retry_count} 次，"
                    f"基于当前信息继续分析[/warning]"
                )
                self._transition(WorkflowStep.PLANNING)
                return

            # 信息不足，回退到 COLLECTING 补充采集
            self._console.print("\n[warning]⚠ 信息不足，需要补充采集[/warning]")
            if not self.auto:
                if interact.confirm("是否补充采集信息?", default=True):
                    supplement = "；".join(diagnosis.missing_info)
                    self.state.problem_description += f"\n[补充采集] {supplement}"
                    self._transition(WorkflowStep.COLLECTING)
                else:
                    self._console.print("[dim]跳过补充采集，基于当前信息继续[/dim]")
                    self._transition(WorkflowStep.PLANNING)
            else:
                # 自动模式：自动回退补充采集
                supplement = "；".join(diagnosis.missing_info)
                self.state.problem_description += f"\n[补充采集] {supplement}"
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
        用户可见步骤 4/7: 修复建议（ SECURITY_CHECKING 在此步骤末尾执行）

        注意：如果从 SECURITY_CHECKING 回退到 PLANNING（CRITICAL 检测失败），
        用户仍然在"修复建议"步骤中，不会看到步骤切换。
        """
        from galaxy_diag.fixer import generate

        if not self.state.diagnosis:
            raise WorkflowError(
                "缺少诊断结论，请先完成根因分析步骤",
                hint="工作流应从 ENV_RECOGNISING 开始",
            )
        if not self.state.env_info:
            raise WorkflowError(
                "缺少环境信息，请先完成环境感知步骤",
                hint="工作流应从 ENV_RECOGNISING 开始",
            )

        with self._console.status(
            "[info]生成修复建议... LLM 推理中，纯 CPU 模式下可能需要 1-3 分钟[/info]",
            spinner="dots",
        ):
            proposal = generate(
                diagnosis=self.state.diagnosis,
                env_info=self.state.env_info,
                model_adapter=self._model_adapter,
                prior_failures=self._fix_retry_feedback,  # 回退重生成时回灌 CRITICAL 失败原因
            )
        # 消费即清空：无论本次生成是否通过，反馈不跨次保留
        self._fix_retry_feedback = None

        self.state.fix = proposal

        # 根据来源输出提示
        if proposal.source == FixSource.ERROR_FALLBACK:
            self._console.print("[error]⚠ 修复建议生成失败[/error]")
            for note in proposal.risk_notes:
                self._console.print(f"  [error]- {note}[/error]")
            self._mark_done("修复建议生成失败，无法继续")
            return
        elif proposal.source == FixSource.FORMAT_FALLBACK:
            self._console.print("[warning]⚠ 模型输出格式异常，未能生成结构化修复建议[/warning]")
            for note in proposal.risk_notes:
                self._console.print(f"  [warning]- {note}[/warning]")
            self._console.print(
                "[dim]  建议使用更大参数的模型（如 qwen3:8b）以获得结构化修复建议[/dim]"
            )
            self._mark_done("模型输出格式异常，无法生成修复建议")
            return
        elif proposal.source == FixSource.LLM_FALLBACK:
            self._console.print("[warning]⚠ 修复建议部分校验失败，已自动修复[/warning]")

        display.print_fix_proposal(proposal)

        # 占位符自动编辑：有未替换占位符时，直接引导用户填写
        has_unresolved = any(cmd.editable_params for cmd in proposal.commands)
        if has_unresolved:
            from galaxy_diag.fixer.template import is_fully_resolved
            unresolved_cmds = [cmd for cmd in proposal.commands if not is_fully_resolved(cmd)]
            if unresolved_cmds:
                self._console.print(
                    f"\n[warning]⚠ 检测到 {len(unresolved_cmds)} 条命令含未替换的占位符，请填写实际值[/warning]"
                )
                self._edit_fix_params(proposal)

        # 逐步模式下允许再次编辑参数
        if not self.auto and proposal.commands:
            has_editable = any(cmd.editable_params for cmd in proposal.commands)
            if has_editable and interact.confirm("是否再次编辑修复参数?", default=False):
                self._edit_fix_params(proposal)

        # PLANNING 完成后进入 SECURITY_CHECKING（在修复建议步骤末尾执行检测）
        self._transition(WorkflowStep.SECURITY_CHECKING)

    def _do_security_checking(self) -> None:
        """SECURITY_CHECKING: D-03 生成后检测（代码质量保障）

        在"修复建议"步骤末尾执行，显示安全性提示。
        策略：CRITICAL（语法/兼容性错误）→ 回退 PLANNING 重新生成（附失败反馈）
              WARNING（危险模式提醒）→ 允许继续
        注意：此步骤不打印独立的步骤标题，归属于用户可见步骤 4/7"修复建议"
        """
        from galaxy_diag.fixer.checker import check

        if not self.state.fix or not self.state.env_info:
            raise WorkflowError("缺少修复建议或环境信息")

        proposal = self.state.fix
        env_type = self.state.env_info.env_type

        # 运行 D-03 检测（先检测再判断，以便在耗尽时展示本次 CRITICAL 原因）
        self._console.print("[info]执行生成后检测 (D-03)...[/info]")
        result = check(
            commands=proposal.commands,
            script=proposal.script,
            script_language=proposal.script_language,
            env_type=env_type,
            has_docker_cli=self.state.env_info.has_docker_cli,
            has_kubectl_cli=self.state.env_info.has_kubectl_cli,
        )

        proposal.check_passed = result.passed
        proposal.check_issues = [i.message for i in result.issues]
        proposal.check_detail = result
        self._save()

        if result.has_critical:
            # CRITICAL: 显示安全性提示，记录失败，判断是否耗尽
            self._console.print("\n[danger]✗ 生成后检测未通过[/danger]")
            for issue in result.issues:
                if issue.severity == CheckSeverity.CRITICAL:
                    self._console.print(f"  [danger]- [{issue.category}] {issue.message}[/danger]")
                    if issue.suggestion:
                        self._console.print(f"    💡 {issue.suggestion}")

            # 记录本次失败
            self.state.history.append({
                "step": WorkflowStep.SECURITY_CHECKING.value,
                "timestamp": datetime.now().isoformat(),
                "result": "failed",
            })
            self._save()

            # 检查是否达到重试上限
            security_retry_count = sum(
                1 for h in self.state.history
                if h.get("step") == WorkflowStep.SECURITY_CHECKING.value
                and h.get("result") == "failed"
            )
            if security_retry_count > MAX_SECURITY_RETRIES:
                # 耗尽：安全终止，绝不执行被判 CRITICAL 的修复
                self._console.print(
                    f"\n[danger]✗ 修复建议已 {security_retry_count} 次未通过生成后检测，"
                    f"无法自动生成与环境兼容的修复，停止以避免执行不兼容操作[/danger]"
                )
                self._mark_done("修复建议多次未通过生成后检测（已达重试上限），请人工介入或调整诊断输入")
                return

            # 未耗尽：存入失败反馈，回退 PLANNING 重新生成
            self._fix_retry_feedback = [
                f"{i.message}" + (f"（建议: {i.suggestion}）" if i.suggestion else "")
                for i in result.issues if i.severity == CheckSeverity.CRITICAL
            ]
            self._console.print("[dim]回退到修复建议生成步骤[/dim]")
            self._transition(WorkflowStep.PLANNING)
            return

        # 通过或仅 WARNING：显示安全性提示后继续
        if result.has_warning:
            self._console.print("\n[warning]⚠ 生成后检测通过（有警告）[/warning]")
            for issue in result.issues:
                if issue.severity == CheckSeverity.WARNING:
                    self._console.print(f"  [warning]- [{issue.category}] {issue.message}[/warning]")
        else:
            self._console.print("[success]✓ 生成后检测通过[/success]")

        # 安全性提示显示完毕，进入执行前熔断
        self._transition(WorkflowStep.EXECUTION_GUARD)

    def _do_execution_guard(self) -> None:
        """EXECUTION_GUARD: E-02 执行前熔断

        在"人工审核"步骤前执行安全性评估。
        - 通过 → 进入 REVIEWING（正常审核）
        - WARNING/CRITICAL → 进入 REVIEWING，要求 CONFIRM 确认

        注意：此步骤不打印独立的步骤标题，归属于用户可见步骤 5/7"人工审核"
        """
        self._console.print("[info]执行前熔断检查 (E-02)...[/info]")

        guard_result = danger.execution_guard_check(
            proposal=self.state.fix,
            env_type=self.state.env_info.env_type if self.state.env_info else EnvironmentType.BARE_METAL,
        )
        self._guard_result = guard_result

        # 渲染熔断结果
        if guard_result.level == "pass":
            self._console.print("[success]✓ 执行前熔断通过[/success]")
        elif guard_result.level == "warning":
            self._console.print(f"[warning]⚠ 执行前熔断检测到警告: {guard_result.message}[/warning]")
        else:  # critical
            self._console.print(f"[danger]✗ 执行前熔断检测到危险操作: {guard_result.message}[/danger]")

        # 展示命中模式详情
        for pat in guard_result.matched_patterns:
            icon = "⚠" if pat.severity == CheckSeverity.WARNING else "✗"
            style = "warning" if pat.severity == CheckSeverity.WARNING else "danger"
            self._console.print(f"  [{style}]{icon} [{pat.category}] {pat.description}[/{style}]")
            if pat.suggestion:
                self._console.print(f"    💡 {pat.suggestion}")

        # 展示影响范围
        if guard_result.impact_summary:
            self._console.print(f"  [info]📊 {guard_result.impact_summary}[/info]")

        self._transition(WorkflowStep.REVIEWING)

    def _do_reviewing(self) -> None:
        """REVIEWING: 人工审核

        展示修复建议 + 风险评估，等待用户确认/拒绝/修改。
        此步骤始终需要人工确认（红线 2），无论逐步还是自动模式。

        如果执行前熔断检测到危险操作，需要额外输入 CONFIRM 确认。
        用户确认后，自动创建快照（显示"正在创建快照"提示），然后进入执行。

        用户可见步骤 5/7: 人工审核（含 EXECUTION_GUARD 的熔断结果）
        """
        proposal = self.state.fix
        if not proposal:
            raise WorkflowError("无可审核的修复建议")

        # ── 执行前熔断结果处理 ──
        guard_result = getattr(self, "_guard_result", None)
        if guard_result is None:
            guard_result = GuardResult(level="pass")
        guard_level = guard_result.level if isinstance(guard_result, GuardResult) else "pass"

        if guard_level in ("warning", "critical"):
            self._console.print(
                f"\n[{'danger' if guard_level == 'critical' else 'warning'}]"
                f"⚠ 执行前熔断检测到{'危险' if guard_level == 'critical' else '警告'}操作"
                f"[/{'danger' if guard_level == 'critical' else 'warning'}]"
            )
            if guard_result.impact_summary:
                self._console.print(f"  [warning]影响范围: {guard_result.impact_summary}[/warning]")

            # 要求输入 CONFIRM 全称以确认
            self._console.print(
                "\n[danger]此操作需要额外确认，请输入 CONFIRM 以继续[/danger]"
            )
            try:
                confirm_input = input("请输入: ").strip()
            except (EOFError, KeyboardInterrupt):
                self._console.print("\n[dim]确认已取消，工作流终止[/dim]")
                self._mark_rejected()
                return

            if confirm_input != "CONFIRM":
                self._console.print("[dim]确认输入不匹配，工作流终止[/dim]")
                self._mark_rejected()
                return

            self._console.print("[success]✓ 危险操作已确认[/success]")

        # 展示操作摘要
        self._console.print("\n[heading]📋 操作摘要[/heading]")
        if proposal.commands:
            self._console.print("[dim]将要执行以下命令:[/dim]")
            for i, cmd in enumerate(proposal.commands, 1):
                verify_tag = " [验证]" if cmd.is_verification else ""
                self._console.print(f"  {i}. [info]{cmd.command}[/info]{verify_tag}")
                if cmd.description:
                    self._console.print(f"     {cmd.description}")

        if proposal.impact_scope:
            self._console.print(f"\n[warning]📊 影响范围: {proposal.impact_scope}[/warning]")

        if proposal.risk_notes:
            self._console.print("[warning]⚠ 风险提示:[/warning]")
            for note in proposal.risk_notes:
                self._console.print(f"  - {note}")

        # 交互式操作菜单（支持编辑参数、删除步骤、重排步骤）
        while True:
            self._console.print("\n请选择操作:")
            self._console.print("  [success]y[/success] - 确认执行")
            self._console.print("  [danger]n[/danger] - 拒绝（终止工作流）")
            self._console.print("  [info]e[/info] - 编辑参数")
            self._console.print("  [info]d[/info] - 删除步骤")
            self._console.print("  [info]r[/info] - 重排步骤顺序")

            try:
                choice = input("请输入 (y/n/e/d/r): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                raise

            if choice in ("y", "yes"):
                # 用户确认 → 写审计日志（confirmed）→ 自动创建快照 → 执行
                self._write_audit(result="confirmed", user_input="CONFIRM" if guard_level != "pass" else "y")
                self._transition(WorkflowStep.SNAPSHOT)
                return
            elif choice in ("n", "no"):
                self._console.print("[dim]用户拒绝执行，工作流终止[/dim]")
                self._write_audit(result="rejected", user_input="n")
                self._mark_rejected()
                return
            elif choice in ("e", "edit"):
                if proposal.commands:
                    self._edit_fix_params(proposal)
                # 编辑后重走安全检测（回到修复建议步骤内部）
                self._transition(WorkflowStep.SECURITY_CHECKING)
                return
            elif choice in ("d", "delete"):
                self._edit_delete_step(proposal)
                # 删除步骤后重新展示
                display.print_fix_proposal(proposal)
            elif choice in ("r", "reorder"):
                self._edit_reorder_steps(proposal)
                # 重排后重新展示
                display.print_fix_proposal(proposal)
            else:
                self._console.print("[dim]无效输入，请重新选择[/dim]")

    def _do_snapshot(self) -> None:
        """SNAPSHOT: 创建恢复快照

        用户审核同意后自动执行，不是用户可见的独立步骤。
        在 CLI 显示"正在创建快照"提示，创建完毕后进入执行修复。

        用户可见：此步骤归属于用户可见步骤 6/7"执行"
        """
        self._console.print("[info]正在创建快照...[/info]")

        proposal = self.state.fix
        try:
            snap_meta = snapshot.create_snapshot(
                proposal=proposal,
                session_id=self.state.session_id,
            )
        except GalaxyDiagError as e:
            self._console.print(f"\n[danger]✗ {e.message}[/danger]")
            if e.hint:
                self._console.print(f"  💡 {e.hint}")
            # fail-safe：快照失败阻止执行
            self._console.print("[warning]快照创建失败，已阻止执行以保护系统[/warning]")
            self._write_audit(result="failure", user_input="")
            self._save()
            return

        self.state.snapshot = snap_meta
        self._console.print(f"[success]✓ 快照已创建: {snap_meta.snapshot_id}[/success]")
        self._transition(WorkflowStep.EXECUTING)

    def _do_executing(self) -> None:
        """EXECUTING: 执行修复（仅非验证命令）

        只执行 is_verification=False 的修复命令。
        验证命令（is_verification=True）留在 proposal.commands 中，
        由步骤 7/7 VERIFYING 消费。

        用户可见步骤 6/7: 执行
        """
        self._console.print("[info]执行修复...[/info]")

        proposal = self.state.fix

        # 过滤出本机可执行的修复命令（不含验证步骤、不含 requires_host 命令）
        # 验证命令留给 VERIFYING；requires_host 命令不在本机执行
        fix_commands = [cmd for cmd in proposal.commands
                        if not cmd.is_verification and not cmd.requires_host]
        host_commands = [cmd for cmd in proposal.commands
                         if not cmd.is_verification and cmd.requires_host]

        if host_commands:
            self._console.print("\n[warning]⚠ 以下修复命令需在宿主机执行（容器内无法执行）:[/warning]")
            for hc in host_commands:
                self._console.print(f"  [dim]- {hc.command}  ({hc.description})[/dim]")
            self._console.print("")

        fix_only_proposal = FixProposal(
            commands=fix_commands,
            script=proposal.script,
            script_language=proposal.script_language,
            risk_notes=proposal.risk_notes,
            impact_scope=proposal.impact_scope,
            source=proposal.source,
        )

        exec_result = executor.run(fix_only_proposal)

        # 展示执行输出
        if exec_result.output:
            for line in exec_result.output.split("\n"):
                self._console.print(f"  {line}")

        if exec_result.success:
            self._console.print("[success]✓ 修复执行完成[/success]")
            self._write_audit(
                result="success",
                user_input="",
                snapshot_id=self.state.snapshot.snapshot_id if self.state.snapshot else None,
            )
            self._transition(WorkflowStep.VERIFYING)
        else:
            # 执行失败 → 自动回滚
            self._console.print(
                f"\n[danger]✗ 修复执行失败（步骤 {exec_result.failed_step}），开始自动回滚...[/danger]"
            )
            if self.state.snapshot:
                try:
                    rb_result = snapshot.rollback(self.state.snapshot.snapshot_id)
                    self._console.print(f"[warning]⚠ 已从快照回滚: {rb_result.message}[/warning]")
                except GalaxyDiagError as e:
                    self._console.print(f"[danger]✗ 回滚失败: {e.message}，请人工介入[/danger]")
            else:
                self._console.print("[danger]✗ 无可用快照，无法回滚，请人工介入[/danger]")

            self._write_audit(
                result="rollback",
                user_input="",
                snapshot_id=self.state.snapshot.snapshot_id if self.state.snapshot else None,
            )
            self._mark_rollback(f"执行失败于步骤 {exec_result.failed_step}")

    def _do_verifying(self) -> None:
        """VERIFYING: 结果验证

        执行修复建议中的验证命令（is_verification=True），判定修复是否生效。
        验证通过 → 标记完成。
        验证失败 → 展示失败详情 + 进一步方案 + 一键回滚提示，标记完成。

        用户可见步骤 7/7: 结果验证
        """
        from galaxy_diag.safety import verifier

        self._console.print("[info]验证修复结果...[/info]")

        proposal = self.state.fix
        verify_result = verifier.verify(proposal)

        # 展示验证输出
        if verify_result.output:
            for line in verify_result.output.split("\n"):
                self._console.print(f"  {line}")

        if verify_result.success:
            if verify_result.total_steps == 0:
                # 无验证命令：保守通过但提示人工确认
                if verify_result.host_required_commands:
                    self._console.print(
                        "[warning]⚠ 所有验证命令需在宿主机执行，无法在本机自动验证[/warning]"
                    )
                else:
                    self._console.print(
                        "[warning]⚠ 未执行验证（修复建议无验证步骤），建议人工确认修复效果[/warning]"
                    )
            else:
                self._console.print(
                    f"[success]✓ 修复验证通过: {verify_result.passed_steps}/{verify_result.total_steps} 步骤成功[/success]"
                )
            # 展示需宿主机执行的验证/修复命令
            if verify_result.host_required_commands:
                self._console.print("\n[warning]⚠ 以下命令需在宿主机执行以完成验证:[/warning]")
                for hc in verify_result.host_required_commands:
                    self._console.print(f"  [dim]- {hc}[/dim]")
            self._write_audit(result="success")
            self._mark_done("修复验证通过")
        else:
            # 验证失败：展示失败详情 + 进一步方案 + 回滚提示
            self._console.print(
                f"\n[danger]✗ 修复验证失败: 步骤 {verify_result.failed_step} "
                f"\"{verify_result.failed_description}\" 返回非零退出码[/danger]"
            )
            self._console.print(
                f"  [dim]验证结果: {verify_result.passed_steps}/{verify_result.total_steps} 步骤通过[/dim]"
            )

            # 展示进一步解决方案概要 + 回滚提示
            display.print_next_steps(
                proposal=proposal,
                snapshot_id=self.state.snapshot.snapshot_id if self.state.snapshot else None,
            )

            self._write_audit(result="verify_failed")
            self._mark_done("修复验证未通过")

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

    def _write_audit(
        self,
        *,
        result: str,
        user_input: str = "",
        snapshot_id: str | None = None,
    ) -> None:
        """写入审计日志的便捷方法

        自动填充 session_id / action / llm_basis 等字段。
        审计写入失败不阻塞工作流（仅告警）。
        """
        action = f"工作流步骤: {STEP_LABELS.get(self.state.current_step, self.state.current_step.value)}"
        llm_basis = ""
        if self.state.diagnosis:
            llm_basis = self.state.diagnosis.root_cause[:200]

        record = audit.build_record(
            session_id=self.state.session_id,
            action=action,
            result=result,
            user_input=user_input,
            llm_basis=llm_basis,
            snapshot_id=snapshot_id,
        )
        audit.write_audit(record)

    # ===== 辅助方法 =====

    def _print_step_header(self, step: WorkflowStep) -> None:
        """打印步骤标题（仅在用户可见步骤切换时打印）

        内部状态 SECURITY_CHECKING / EXECUTION_GUARD / SNAPSHOT
        分别归属于用户可见步骤 4/5/6，不会触发新的步骤标题打印。
        """
        user_step = STEP_TO_USER_STEP.get(step, (0, step.value))
        user_num, user_label = user_step

        if user_num != self._last_user_step_num:
            self._console.print(
                f"\n[heading]━━━ 步骤 {user_num}/{TOTAL_USER_STEPS}: {user_label} ━━━[/heading]"
            )
            if self.verbose:
                desc = STEP_DESCRIPTIONS.get(step, "")
                if desc:
                    self._console.print(f"[dim]  {desc}[/dim]")
            self._last_user_step_num = user_num

    def _edit_fix_params(self, proposal: FixProposal) -> None:
        """交互式编辑修复参数

        使用 template.apply_param_values 替换占位符，返回新的 CommandTemplate（不修改原对象）。
        编辑参数后重新生成脚本（脚本中的占位符也需同步替换）。
        """
        from galaxy_diag.fixer.generator import generate_script
        from galaxy_diag.fixer.template import apply_param_values

        updated_commands = []
        for cmd in proposal.commands:
            if cmd.editable_params:
                new_params = interact.prompt_edit_params(
                    template=cmd.command,
                    placeholders=cmd.editable_params,
                )
                updated_cmd = apply_param_values(cmd, new_params)
                updated_commands.append(updated_cmd)
            else:
                updated_commands.append(cmd)
        proposal.commands = updated_commands

        # 重新生成脚本（命令参数已替换，脚本需同步更新）
        self._regenerate_script(proposal)

        # 编辑后重新展示
        display.print_fix_proposal(proposal)

    def _regenerate_script(self, proposal: FixProposal) -> None:
        """基于当前 commands 重新生成脚本

        当 commands 被编辑（参数替换/删除步骤/重排步骤）后，
        需要重新生成脚本以保持同步。
        """
        from galaxy_diag.fixer.generator import generate_script

        non_verify_cmds = [c for c in proposal.commands if not c.is_verification]
        if len(non_verify_cmds) >= 2:
            proposal.script = generate_script(
                commands=non_verify_cmds,
                language=proposal.script_language or "bash",
                root_cause=self.state.diagnosis.root_cause if self.state.diagnosis else "",
            )
        else:
            # 少于 2 个非验证步骤，不需要脚本
            proposal.script = None
            proposal.script_language = None

    def _edit_delete_step(self, proposal: FixProposal) -> None:
        """交互式删除修复步骤

        对应 REQ-D-01 "可编辑"：用户可删除不适用的步骤。
        """
        from galaxy_diag.fixer.template import remove_step

        if not proposal.commands:
            self._console.print("[dim]没有可删除的步骤[/dim]")
            return

        # 展示当前步骤
        self._console.print("\n[heading]当前步骤:[/heading]")
        for i, cmd in enumerate(proposal.commands, 1):
            verify_tag = " [验证]" if cmd.is_verification else ""
            self._console.print(f"  {i}. [info]{cmd.command}[/info]{verify_tag} - {cmd.description}")

        try:
            raw = input("请输入要删除的步骤编号（1-{}），0 取消: ".format(len(proposal.commands))).strip()
        except (EOFError, KeyboardInterrupt):
            return

        try:
            index = int(raw)
        except ValueError:
            self._console.print("[dim]无效输入[/dim]")
            return

        if index == 0:
            self._console.print("[dim]已取消[/dim]")
            return

        if index < 1 or index > len(proposal.commands):
            self._console.print(f"[dim]步骤编号 {index} 越界[/dim]")
            return

        # 确认删除
        deleted_cmd = proposal.commands[index - 1]
        if not interact.confirm(f"确认删除步骤 {index}: {deleted_cmd.description}?", default=False):
            self._console.print("[dim]已取消[/dim]")
            return

        proposal.commands = remove_step(proposal.commands, index - 1)
        # 删除步骤后重新生成脚本
        self._regenerate_script(proposal)
        self._console.print(f"[success]✓ 已删除步骤 {index}[/success]")

    def _edit_reorder_steps(self, proposal: FixProposal) -> None:
        """交互式重排修复步骤顺序

        对应 REQ-D-01 "可编辑"：用户可修改步骤执行顺序。
        """
        from galaxy_diag.fixer.template import reorder_steps

        if len(proposal.commands) < 2:
            self._console.print("[dim]少于 2 个步骤，无需重排[/dim]")
            return

        # 展示当前步骤
        self._console.print("\n[heading]当前步骤顺序:[/heading]")
        for i, cmd in enumerate(proposal.commands, 1):
            verify_tag = " [验证]" if cmd.is_verification else ""
            self._console.print(f"  {i}. [info]{cmd.command}[/info]{verify_tag} - {cmd.description}")

        self._console.print("\n请输入新的步骤顺序（如原 1,2,3 改为 3,1,2 表示先执行原第3步）")
        try:
            raw = input("新顺序（0 取消）: ").strip()
        except (EOFError, KeyboardInterrupt):
            return

        if raw.strip() == "0":
            self._console.print("[dim]已取消[/dim]")
            return

        try:
            new_order = [int(x.strip()) - 1 for x in raw.split(",")]
        except ValueError:
            self._console.print("[dim]无效输入，请使用逗号分隔的数字（如 3,1,2）[/dim]")
            return

        if sorted(new_order) != list(range(len(proposal.commands))):
            self._console.print(
                f"[dim]无效排列，需要包含 1-{len(proposal.commands)} 的所有数字[/dim]"
            )
            return

        proposal.commands = reorder_steps(proposal.commands, new_order)
        # 重排后重新生成脚本
        self._regenerate_script(proposal)
        self._console.print("[success]✓ 步骤顺序已更新[/success]")

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
            investigation_steps=["检查驱动模块: modprobe --dry-run vmw_pvscsi"],
            fault_scope="存储层：VM 磁盘控制器驱动",
            diagnosis_source=DiagnosisSource.RULE_MATCH,
        )
