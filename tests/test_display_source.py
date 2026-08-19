"""来源标注输出测试 (REQ-X-02 验收标准 3)"""

from io import StringIO

from rich.console import Console

from galaxy_diag.shared.types import (
    Confidence,
    DiagnosisResult,
    EnvironmentType,
    KnowledgeRef,
)
from galaxy_diag.workflow.cli import display as disp
from galaxy_diag.workflow.cli.display import GALAXY_THEME, print_diagnosis


def _capture(result: DiagnosisResult) -> str:
    """替换全局 _console 捕获 print_diagnosis 输出"""
    buf = StringIO()
    old_console = disp._console
    disp._console = Console(
        file=buf, width=120, force_terminal=False, no_color=True, theme=GALAXY_THEME
    )
    try:
        print_diagnosis(result)
        output = buf.getvalue()
    finally:
        disp._console = old_console
    return output


def _result(refs=None) -> DiagnosisResult:
    return DiagnosisResult(
        root_cause="CNI 异常",
        confidence=Confidence.SUSPECTED,
        env_type=EnvironmentType.CONTAINER,
        referenced_knowledge=refs or [],
    )


def test_display_with_customer_cases():
    out = _capture(_result([
        KnowledgeRef(case_id="kb_1", similarity=0.8, summary="CNI 异常"),
        KnowledgeRef(case_id="kb_2", similarity=0.7, summary="网络配置"),
    ]))
    assert "客户特有案例" in out
    assert "2 条" in out
    assert "kb_1" in out


def test_display_without_customer_cases():
    out = _capture(_result([]))
    assert "通用知识" in out
