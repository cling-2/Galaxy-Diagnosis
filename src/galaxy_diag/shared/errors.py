"""统一异常体系

所有业务错误继承 GalaxyDiagError，附带可操作 hint，
对齐任务书"错误处理不能吞"原则。
对应架构设计 shared 层。
"""

from __future__ import annotations


class GalaxyDiagError(Exception):
    """基础错误类"""

    def __init__(self, message: str, hint: str = ""):
        self.message = message
        self.hint = hint
        super().__init__(self.format())

    def format(self) -> str:
        if self.hint:
            return f"{self.message}\n💡 {self.hint}"
        return self.message


class ConfigError(GalaxyDiagError):
    """配置加载/校验失败"""


class PrecheckError(GalaxyDiagError):
    """硬件预检失败"""


class ModelUnavailableError(GalaxyDiagError):
    """推理服务不可用（服务不可达 / 模型不存在 / 推理失败）"""


class ModelCallError(GalaxyDiagError):
    """模型调用失败（超时 / 限频 / 响应异常）"""


class CollectorError(GalaxyDiagError):
    """信息采集失败"""


class CollectorPermissionError(CollectorError):
    """采集权限不足（如非 root 读 DMI）"""


class CollectorPartialError(CollectorError):
    """部分采集失败"""


class CollectorToolNotFoundError(CollectorError):
    """采集工具未安装（如 storcli64/lspci 缺失）"""


class DiagnoseError(GalaxyDiagError):
    """诊断分析失败"""


class FixerError(GalaxyDiagError):
    """修复生成失败"""


class SafetyError(GalaxyDiagError):
    """安全审核失败（危险操作拦截等）"""


class WorkflowError(GalaxyDiagError):
    """工作流编排异常"""
