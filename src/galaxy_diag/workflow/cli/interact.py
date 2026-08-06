"""交互式参数输入 & 通用确认

所有输入函数走 Python 内置 input()，不经 LLM 通道。
对应红线 2：确认必须通过专用交互流程完成。
"""

from __future__ import annotations

from typing import Callable

from galaxy_diag.workflow.cli.display import STYLE_DANGER, get_console


def confirm(
    prompt: str,
    *,
    default: bool = False,
    danger: bool = False,
) -> bool:
    """安全确认交互。

    Args:
        prompt: 确认提示文本
        default: 回车默认值（False = 默认拒绝，安全优先）
        danger: 是否为危险操作模式
                - False: [y/N] 输入 y 确认
                - True:  红色提示，输入 CONFIRM 确认（F-03 预留）

    Returns:
        True: 用户确认  False: 用户拒绝

    关键约束:
        - 输入走 stdin (input())，不走 LLM 通道（红线 2）
        - 拒绝后不反复要求确认
    """
    console = get_console()

    if danger:
        console.print(f"[danger]⚠ 危险操作![/danger]")
        console.print(f"[danger]{prompt}[/danger]")
        try:
            user_input = input("请输入 CONFIRM 确认: ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("")
            return False
        if user_input == "CONFIRM":
            return True
        console.print("[dim]  未输入 CONFIRM，操作已取消[/dim]")
        return False
    else:
        choices = "[Y/n]" if default else "[y/N]"
        try:
            user_input = input(f"{prompt} {choices}: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("")
            return False
        if user_input == "":
            return default
        return user_input in ("y", "yes")


def prompt_input(
    prompt: str,
    *,
    validator: Callable[[str], str | None] | None = None,
    default: str = "",
    max_retries: int = 3,
) -> str:
    """交互式输入，支持校验与重试。

    Args:
        prompt: 输入提示文本
        validator: 校验函数，返回 None 表示通过，返回 str 为错误提示
        default: 默认值（用户直接回车时使用）
        max_retries: 最大重试次数

    Returns:
        用户输入值（已通过校验）

    Raises:
        RuntimeError: 超过最大重试次数
    """
    console = get_console()
    default_hint = f" [{default}]" if default else ""

    for attempt in range(max_retries):
        try:
            user_input = input(f"{prompt}{default_hint}: ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("")
            raise RuntimeError("输入被中断")

        if user_input == "" and default:
            user_input = default

        if validator is None:
            return user_input

        error = validator(user_input)
        if error is None:
            return user_input

        console.print(f"[warning]  ✗ {error}[/warning]")
        remaining = max_retries - attempt - 1
        if remaining > 0:
            console.print(f"[dim]  (剩余 {remaining} 次尝试)[/dim]")

    raise RuntimeError(f"输入校验失败，已超过最大重试次数 ({max_retries})")


def prompt_edit_params(
    template: str,
    placeholders: dict[str, str],
) -> dict[str, str]:
    """交互式编辑修复命令的参数占位符。

    对应 REQ-D-01 "用户能在 CLI 中直接修改建议的参数值"。

    Args:
        template: 含占位符的命令模板，如 "mount -t <FS_TYPE> <DEVICE> <MOUNT_POINT>"
        placeholders: 占位符名 → 默认值，如 {"FS_TYPE": "ext4", "DEVICE": "/dev/sdb1", ...}

    Returns:
        用户填写后的参数 dict

    交互流程:
        1. 展示完整模板（占位符高亮）
        2. 逐个提示用户输入或接受默认值
        3. 展示替换后的完整命令
        4. 用户确认或重新编辑
    """
    console = get_console()

    while True:
        result: dict[str, str] = {}

        # 1. 展示模板（占位符高亮）
        console.print("\n[heading]修复命令模板:[/heading]")
        highlighted = template
        for name in placeholders:
            highlighted = highlighted.replace(f"<{name}>", f"[info]<{name}>[/info]")
        console.print(f"  {highlighted}")

        # 2. 逐个输入参数
        console.print("\n[heading]请填写参数:[/heading]")
        for name, default_val in placeholders.items():
            value = prompt_input(
                f"  {name}",
                default=default_val,
            )
            result[name] = value

        # 3. 展示替换后的命令
        filled = template
        for name, value in result.items():
            filled = filled.replace(f"<{name}>", value)

        console.print("\n[heading]替换后命令:[/heading]")
        console.print(f"  [success]{filled}[/success]")

        # 4. 确认
        if confirm("确认?", default=True):
            return result

        console.print("[dim]重新编辑参数...[/dim]")
