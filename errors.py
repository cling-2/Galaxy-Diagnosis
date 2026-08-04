"""统一错误类定义

所有业务错误继承 GalaxyDiagError，附带可操作 hint，
对齐任务书"错误处理不能吞"原则。
"""


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
