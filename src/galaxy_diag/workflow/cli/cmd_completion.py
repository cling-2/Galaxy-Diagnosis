"""galaxy-diag completion — 生成 Shell 补全脚本

支持 bash / zsh / fish。
优先使用 argcomplete（如已安装），否则生成静态补全脚本。
"""

from __future__ import annotations

import argparse

from galaxy_diag.workflow.cli.app import _COMMANDS
from galaxy_diag.workflow.cli.display import get_console

# 子命令简短描述（用于补全脚本）
_COMMAND_HELP = {
    "env": "环境识别 & 硬件采集",
    "diagnose": "问题诊断",
    "snapshot": "快照管理/回滚",
    "audit-log": "审计日志查询",
    "run": "端到端工作流",
    "completion": "生成 Shell 补全脚本",
}


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "completion",
        help="生成 Shell 补全脚本",
        description="生成 bash/zsh/fish 的补全脚本，安装后支持 Tab 自动补全",
    )
    sub.add_argument(
        "shell",
        choices=["bash", "zsh", "fish"],
        help="目标 Shell 类型",
    )
    sub.set_defaults(callback=handle)


def handle(args: argparse.Namespace) -> None:
    """completion 子命令回调"""
    console = get_console()

    # 检查 argcomplete 是否可用
    try:
        import argcomplete  # noqa: F401
        has_argcomplete = True
    except ImportError:
        has_argcomplete = False

    if has_argcomplete:
        console.print("[success]✓ argcomplete 已安装[/success]")
        console.print("\n安装补全:")
        console.print("  [dim]# 一次性（当前终端）[/dim]")
        console.print("  eval \"$(register-python-argcomplete galaxy-diag)\"")
        console.print("\n  [dim]# 持久化[/dim]")
        if args.shell == "bash":
            console.print("  register-python-argcomplete galaxy-diag > /etc/bash_completion.d/galaxy-diag")
        elif args.shell == "zsh":
            console.print("  # 将以下内容加入 ~/.zshrc:")
            console.print('  eval "$(register-python-argcomplete galaxy-diag)"')
        elif args.shell == "fish":
            console.print("  register-python-argcomplete galaxy-diag > ~/.config/fish/completions/galaxy-diag.fish")
    else:
        # 生成静态补全脚本
        console.print("[warning]⚠ argcomplete 未安装，生成静态补全脚本[/warning]")
        script = _generate_static_completion(args.shell)
        console.print(f"\n[dim]# 安装补全（持久化）:[/dim]")
        if args.shell == "bash":
            console.print(f"  galaxy-diag completion bash > /etc/bash_completion.d/galaxy-diag")
        console.print(f"\n[dim]# 补全脚本内容:[/dim]")
        console.print(script)


def _generate_static_completion(shell: str) -> str:
    """生成静态补全脚本（不依赖 argcomplete）"""
    commands = " ".join(name for _, name in _COMMANDS)

    if shell == "bash":
        return f"""#!/bin/bash
# galaxy-diag bash completion（静态生成）
_galaxy_diag_completion() {{
    local cur prev commands
    COMPREPLY=()
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"
    commands="{commands}"

    if [ $COMP_CWORD -eq 1 ]; then
        COMREPLY=($(compgen -W "$commands" -- "$cur"))
    fi
}}
complete -F _galaxy_diag_completion galaxy-diag
"""
    elif shell == "zsh":
        commands_list = " ".join(f'{name}:"{_COMMAND_HELP.get(name, "")}"' for _, name in _COMMANDS)
        return f"""#compdef galaxy-diag
# galaxy-diag zsh completion（静态生成）
_galaxy_diag() {{
    local -a commands
    commands=(
        {commands_list}
    )
    _describe 'command' commands
}}
compdef _galaxy_diag galaxy-diag
"""
    elif shell == "fish":
        commands_list = "\n".join(
            f"complete -c galaxy-diag -n '__fish_use_subcommand' -a {name} -d '{_COMMAND_HELP.get(name, '')}'"
            for _, name in _COMMANDS
        )
        return f"""# galaxy-diag fish completion（静态生成）
{commands_list}
"""
    return ""
