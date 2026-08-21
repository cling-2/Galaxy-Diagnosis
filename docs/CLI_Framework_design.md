# CLI 框架搭建 (REQ-F-01)

## 模块概述

提供 CLI 交互方式，使运维人员在无图形界面的服务器环境中也能完整使用系统功能。
对应任务书 REQ-F-01 四项验收标准：

1. 命令行交互界面，支持全部核心操作
2. 交互式参数输入
3. 终端友好输出（表格、颜色、缩进）
4. --help 和命令补全

> 本文档仅覆盖 REQ-F-01（CLI 框架骨架）。审核确认交互流程（F-03）的设计见 `review_ui.py` 函数签名预留，完整流程在安全模块设计文档中展开。

## 1. CLI 框架选型

**选择 argparse**（标准库），理由：

| 考量 | 决策 |
|------|------|
| 离线依赖 | argparse 为标准库，零额外 wheel，符合离线优先原则 |
| 子命令组织 | `subparsers` 满足 7+ 子命令需求，够用 |
| 补全支持 | 通过 `argcomplete`（1 个 wheel）或自生成 completion 脚本 |
| 与架构一致 | 架构设计已将 CLI 归入 `workflow/cli/`，argparse 嵌入 `app.py` 最轻量 |

**不选 click/typer 的原因**：引入额外依赖链，且当前子命令数量不多，argparse 完全胜任。如后续子命令膨胀到 15+，可平滑迁移至 click（函数签名不变，只改注册方式）。

## 2. 项目结构（对齐架构设计）

CLI 代码位于 `src/galaxy_diag/workflow/cli/`，是 `workflow` 包的子包：

```
src/galaxy_diag/workflow/cli/
├── __init__.py        # 包导出
├── app.py             # CLI 主入口 & 命令注册
├── display.py         # Rich 输出（表格/颜色/格式化）
├── interact.py        # 交互式参数输入 & 通用确认
└── review_ui.py       # 审核确认交互（F-03 预留，当前仅定义签名）
```

### 文件职责表

| 文件 | 核心职责 | 禁止事项 | 关键导出/接口 |
|------|---------|---------|--------------|
| `app.py` | 解析命令行参数，分发子命令，初始化全局 Console | 不包含任何业务逻辑 | `main()` 入口函数 |
| `display.py` | 封装 Rich Console、样式常量、领域输出组件 | 不调用 workflow 外层任何函数 | `console`, `print_env_info()`, `print_diagnosis()`, `print_fix_proposal()` |
| `interact.py` | 交互式输入（参数编辑、通用确认） | 不直接实例化模型客户端 | `confirm()`, `prompt_input()`, `prompt_edit_params()` |
| `review_ui.py` | 审核确认交互（F-03 专用） | 确认流程不经 LLM 通道 | `review_confirm()`, `review_reject()`, `review_modify()` |

### 依赖规则

```
app.py ──→ display.py, interact.py, review_ui.py
interact.py ──→ display.py（仅用 console 输出提示）
review_ui.py ──→ interact.py, display.py
display.py ──→ 无外部依赖（仅 Rich）

workflow/cli/ ──→ workflow/engine.py（通过 app.py 回调调用）
workflow/cli/ ──→ shared/types.py（读取数据结构做渲染）

workflow/cli/ ←── 严禁被 workflow/ 外层反向依赖
```

## 3. 子命令树

### 3.1 命令总览

```
galaxy-diag [全局选项] <子命令> [子命令选项]

全局选项:
  --config PATH       配置文件路径 (默认: config.yaml 或 GALAXY_CONFIG_FILE)
  --verbose           详细输出模式
  --no-color          禁用颜色输出 (等同 NO_COLOR=1)
  --skip-precheck     跳过硬件资源预检（调试用）
  --version           显示版本号

子命令:
  env                 环境识别 & 硬件采集 (REQ-B)
  diagnose            问题诊断 (REQ-C)
  fix                 修复建议查看/编辑 (REQ-D)
  review              审核确认 (REQ-E/F-03)
  snapshot            快照管理/回滚 (REQ-E-03)
  audit-log           审计日志查询 (REQ-E-04)
  run                 端到端工作流 (REQ-F-02)
  completion          生成 Shell 补全脚本
  kb                  客户知识库管理 (REQ-X-02: import/list/delete/reindex)
  trace               查看诊断任务推理链路 (REQ-X-04)
```

### 3.2 各子命令参数签名

#### `galaxy-diag env`

```
galaxy-diag env [选项]

选项:
  --type-only         仅输出环境类型，不采集硬件详情
  --output FORMAT     输出格式: table (默认), json, yaml
```

输出示例：
```
🔍 环境识别结果
  环境类型: 虚拟机 (KVM)

📋 硬件信息
┌──────────┬──────────────────────┐
│ 项目     │ 值                    │
├──────────┼──────────────────────┤
│ CPU      │ Intel Xeon E5-2680   │
│ 核数     │ 4                    │
│ 内存     │ 16.0 GB              │
│ 磁盘     │ sda 100GB SSD        │
│ RAID 卡  │ 未检测到              │
│ 网卡     │ virtio-net           │
└──────────┴──────────────────────┘
```

#### `galaxy-diag diagnose`

```
galaxy-diag diagnose [选项]

选项:
  --description TEXT  问题描述（交互式输入的替代方式）
  --session ID        继续已有诊断会话
  --output FORMAT     输出格式: table (默认), json
```

#### `galaxy-diag snapshot`

```
galaxy-diag snapshot <动作> [选项]

动作:
  list                列出所有快照
  show ID             查看快照详情
  rollback ID         回滚到指定快照（默认：最近一次快照）

选项:
  --session ID        按会话筛选
```

#### `galaxy-diag audit-log`

```
galaxy-diag audit-log [选项]

选项:
  --session ID        按会话筛选
  --limit N           显示最近 N 条 (默认: 20)
  --since DATETIME    起始时间
```

#### `galaxy-diag run`

```
galaxy-diag run [选项]

选项:
  --description TEXT, -d TEXT  问题描述
  --resume [ID]               恢复中断的工作流；不指定 ID 时默认恢复最近的未完成会话
  --auto                      自动模式（中间步骤只展示不暂停，审核步骤仍需人工）
  --log-file PATH             上传日志文件供诊断参考（可多次指定）
  --clean                     清理所有未完成会话后再启动新工作流
  --mock                      Mock 模式：使用预设响应，不连接真实 LLM（用于开发测试和流程验证）
```

**用户可见步骤流程（7 步）**：

```
━━━ 步骤 1/7: 环境识别 ━━━
    识别运行环境类型 (VM/容器/裸机) 并采集软硬件信息

━━━ 步骤 2/7: 信息收集 ━━━
    采集日志、系统状态等诊断信息

━━━ 步骤 3/7: 根因分析 ━━━
    基于诊断上下文推理根因，声明不确定性

━━━ 步骤 4/7: 修复建议 ━━━
    生成修复命令/脚本建议
    ┈┈ 生成后自动执行 D-03 安全检测 ┈┈
    ✓ 生成后检测通过 / ⚠ 有警告 / ✗ 未通过（回退重新生成）

━━━ 步骤 5/7: 人工审核 ━━━
    ┈┈ 执行前熔断检查 (E-02) ┈┈
    ✓ 熔断通过 / ⚠ 检测到危险操作（需输入 CONFIRM 确认）
    📋 操作摘要展示
    请选择操作: y(确认) / n(拒绝) / e(编辑) / d(删除) / r(重排)

━━━ 步骤 6/7: 执行 ━━━
    正在创建快照...
    ✓ 快照已创建: snap_xxxxxxxx
    执行修复...
    ✓ 修复执行完成

━━━ 步骤 7/7: 结果验证 ━━━
    验证修复是否生效
    ✓ 修复验证通过

✅ 工作流完成
```

> **内部状态机与用户步骤的映射**：内部 10 个状态（含 SECURITY_CHECKING、EXECUTION_GUARD、SNAPSHOT）自动映射为用户可见的 7 步。隐藏子步骤的执行效果以提示信息形式展示在对应步骤内，不会打印独立的步骤标题。详见 `Workflow-design.md` §2.2。

#### `galaxy-diag completion`

```
galaxy-diag completion <shell>

shell: bash | zsh | fish
```

安装补全：
```bash
# bash
eval "$(galaxy-diag completion bash)"
# 或持久化：
galaxy-diag completion bash > /etc/bash_completion.d/galaxy-diag
```

#### `galaxy-diag kb`

```
galaxy-diag kb <动作> [选项]

动作:
  import <file>       导入客户故障案例文件（Markdown/纯文本）
  list                列出已导入案例
  delete <case_id>    删除指定案例
  reindex             重新索引全部案例（embedding 模型更换后使用）

选项:
  --mock              Mock 模式：使用预设 embedding，不连接真实 Ollama（import/reindex 可用）
```

> `kb import` / `kb reindex` 触发 embedding 模型预检；`kb list` / `kb delete` 为纯本地操作。详见 `RAG_Knowledge_Base_design.md`。

#### `galaxy-diag trace`

```
galaxy-diag trace <session_id> [选项]

选项:
  --step STEP         按步骤过滤（如 DIAGNOSING）
  --verbose           显示完整 completion / output_summary
```

> 查看指定诊断任务的推理链路记录。详见 `Trace_design.md`。

### 3.3 子命令注册机制

`app.py` 维护命令注册表，每个子命令模块导出 `register(subparsers)` 函数：

```python
# workflow/cli/app.py 核心结构（示意）

from galaxy_diag.workflow.cli import display, interact, review_ui

# 子命令注册表：模块路径 → 命令名
_COMMANDS = [
    ("galaxy_diag.workflow.cli.cmd_env", "env"),
    ("galaxy_diag.workflow.cli.cmd_diagnose", "diagnose"),
    ("galaxy_diag.workflow.cli.cmd_fix", "fix"),
    ("galaxy_diag.workflow.cli.cmd_review", "review"),
    ("galaxy_diag.workflow.cli.cmd_snapshot", "snapshot"),
    ("galaxy_diag.workflow.cli.cmd_audit_log", "audit-log"),
    ("galaxy_diag.workflow.cli.cmd_run", "run"),
]

def _register_commands(subparsers):
    for module_path, name in _COMMANDS:
        mod = importlib.import_module(module_path)
        mod.register(subparsers)

def main():
    parser = argparse.ArgumentParser(
        prog="galaxy-diag",
        description="银河平台部署问题定位工具",
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")

    subparsers = parser.add_subparsers(dest="command")
    _register_commands(subparsers)

    args = parser.parse_args()

    # 初始化全局 Console（--no-color / NO_COLOR 环境变量）
    display.init_console(no_color=args.no_color)

    if args.command is None:
        parser.print_help()
        return

    # 分发到子命令回调
    args.callback(args)
```

每个子命令文件结构统一：

```python
# workflow/cli/cmd_env.py（示意）

from galaxy_diag.workflow.cli.display import console
from galaxy_diag.workflow.cli.interact import confirm

def register(subparsers):
    sub = subparsers.add_parser("env", help="环境识别 & 硬件采集")
    sub.add_argument("--type-only", action="store_true")
    sub.add_argument("--output", choices=["table", "json", "yaml"], default="table")
    sub.set_defaults(callback=handle)

def handle(args):
    # 调用 workflow/engine.py 或 collector/ 的接口
    # 用 display.py 渲染输出
    ...
```

## 4. Rich UI 样式规范

### 4.1 全局样式常量

```python
# workflow/cli/display.py

from rich.console import Console
from rich.theme import Theme

# 语义化样式常量（与 Rich markup 一致）
STYLE_SUCCESS = "green"
STYLE_DANGER  = "red bold"
STYLE_WARNING = "yellow"
STYLE_INFO    = "cyan"
STYLE_DIM     = "dim"
STYLE_HEADING = "bold cyan"

# Rich Theme 注册（确保样式集中定义）
GALAXY_THEME = Theme({
    "success": STYLE_SUCCESS,
    "danger":  STYLE_DANGER,
    "warning": STYLE_WARNING,
    "info":    STYLE_INFO,
    "heading": STYLE_HEADING,
})

# 全局 Console 实例（整个 CLI 共用）
_console: Console | None = None

def init_console(*, no_color: bool = False):
    """初始化全局 Console，支持 --no-color 和 NO_COLOR 环境变量"""
    global _console
    _console = Console(theme=GALAXY_THEME, no_color=no_color)

def get_console() -> Console:
    """获取全局 Console 实例"""
    global _console
    if _console is None:
        init_console()  # 默认初始化
    return _console

# 模块级快捷引用
console = property(lambda self: get_console())
```

**使用规范**：
- ✅ `console.print("[success]✓ 预检通过[/success]")` — 用语义样式名
- ❌ `console.print("[green]✓ 预检通过[/green]")` — 禁止直接写颜色值
- ✅ `console.print("[danger]✗ 硬件不满足最低要求[/danger]")` — 危险信息
- ❌ `console.print("[bold red]...[/bold red]")` — 禁止在业务代码中组合样式

### 4.2 输出格式设计

| 内容类型 | 渲染方式 | 示例 |
|---------|---------|------|
| 硬件信息 | `rich.table.Table` | 竖线表格，项目/值两列 |
| 诊断结论 | `rich.panel.Panel` + `rich.markdown.Markdown` | 面板包裹，支持 Markdown 格式结论 |
| 修复步骤 | `rich.table.Table` | 编号/命令/风险三列，风险列用颜色标签 |
| 审计日志 | `rich.table.Table` | 时间/操作/结果三列，结果列颜色区分 |
| 进度提示 | `rich.status.Status` | 旋转动画 + 文本（模型推理等待等） |
| 操作摘要 | `rich.panel.Panel` | 审核确认前的信息展示 |

### 4.3 NO_COLOR 与管道模式

- `--no-color` 参数和 `NO_COLOR` 环境变量均禁用颜色（Rich Console 原生支持）
- 检测 `sys.stdout.isatty() == False` 时自动进入简洁模式（无动画、无颜色），确保管道 `galaxy-diag env --output json | jq .` 正常工作

### 4.4 终端宽度适配

- Console 宽度使用 Rich 默认自动检测（`os.get_terminal_size()`）
- SSH 环境下宽度可能较窄，表格列设置 `width` / `min_width` 而非 `no_wrap=True`
- 超宽内容使用 `console.print(..., overflow="ellipsis")` 防溢出

## 5. 交互组件 API

### 5.1 `confirm()` — 安全确认

```python
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
        - 输入走 stdin，不走 LLM 通道（红线 2）
        - 拒绝后不反复要求确认
    """
```

行为说明：

| 模式 | 提示 | 确认方式 | 拒绝方式 |
|------|------|---------|---------|
| 普通确认 | `确认执行? [y/N]:` | 输入 `y` 或 `Y` | 回车 / `n` / 任意非 y |
| 危险确认 | `[bold red]⚠ 危险操作! 请输入 CONFIRM 确认:[/bold red]` | 输入 `CONFIRM`（全大写） | 任意其他输入 |

> `danger=True` 的完整逻辑（二次确认、输入摘要等）在 REQ-E-02/F-03 阶段实现，当前仅预留参数和分支。

### 5.2 `prompt_input()` — 带校验的交互输入

```python
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
```

使用示例：
```python
# 校验 IP 地址
ip = prompt_input("目标 IP 地址:", validator=lambda v:
    None if re.match(r"\d+\.\d+\.\d+\.\d+", v) else "请输入合法 IP 地址")

# 校验必填
desc = prompt_input("问题描述:", validator=lambda v:
    None if v.strip() else "问题描述不能为空")
```

### 5.3 `prompt_edit_params()` — 参数占位符交互编辑

对应 REQ-D-01 "用户能在 CLI 中直接修改建议的参数值"。

```python
def prompt_edit_params(
    template: str,
    placeholders: dict[str, str],
) -> dict[str, str]:
    """交互式编辑修复命令的参数占位符。

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
```

交互示例：
```
修复命令模板:
  mount -t <FS_TYPE> <DEVICE> <MOUNT_POINT>

请填写参数:
  FS_TYPE [ext4]: ext4
  DEVICE [/dev/sdb1]: /dev/sdc1
  MOUNT_POINT [/data]: /mnt/data

替换后命令:
  mount -t ext4 /dev/sdc1 /mnt/data

确认? [y/N]: y
```

### 5.4 `review_ui` 预留签名

```python
# workflow/cli/review_ui.py（F-03 阶段实现，当前仅占位）

def review_confirm(operation: "FixProposal") -> bool:
    """展示操作摘要，等待用户确认/拒绝/修改。F-03 阶段实现。"""
    raise NotImplementedError("审核确认交互流程将在 REQ-F-03 阶段实现")

def review_reject(operation: "FixProposal") -> None:
    """记录用户拒绝，不执行且不反复要求确认。F-03 阶段实现。"""
    raise NotImplementedError

def review_modify(operation: "FixProposal") -> "FixProposal":
    """用户修改参数后重新提交（重新走 D-03 检测）。F-03 阶段实现。"""
    raise NotImplementedError
```

## 6. CLI 参数与配置的优先级

对齐架构设计 §8.1 的配置加载链，CLI 参数为最高优先级：

```
CLI 参数 (--base-url 等)  >  环境变量 (GALAXY_LLM_BASE_URL)  >  config.yaml  >  config/defaults.py
```

### 子命令中的配置覆盖参数

| CLI 参数 | 对应配置项 | 说明 |
|---------|----------|------|
| `--config PATH` | 配置文件路径 | 指定非默认配置文件 |
| `--verbose` | `runtime.log_level` | 等效于 `log_level=DEBUG` |
| `--no-color` | — | 等效于 `NO_COLOR=1` |

> 额外的覆盖参数（如 `--base-url`、`--model`）暂不在全局选项中提供，运维人员通过 `config.yaml` 或环境变量修改。如后续有需求可按相同模式扩展。

## 7. Shell 补全

### 方案

使用 `argcomplete`（1 个 wheel，约 30KB），零配置即可支持 bash/zsh/fish/tcsh。

### 安装方式

```bash
# 注册补全（一次性）
eval "$(register-python-argcomplete galaxy-diag)"
# 或持久化
register-python-argcomplete galaxy-diag > /etc/bash_completion.d/galaxy-diag
```

### 代码集成

```python
# workflow/cli/app.py 顶部
import argcomplete

def main():
    parser = ...
    _register_commands(subparsers)
    argcomplete.autocomplete(parser)  # 补全入口
    args = parser.parse_args()
    ...
```

### 离线部署

`argcomplete` 的 wheel 已包含在离线介质中（`deploy/offline/wheels/`），安装脚本自动安装。

> 如需零额外依赖，可改用 `galaxy-diag completion bash` 命令自生成补全脚本（基于注册表反射生成静态 bash 函数），但实现复杂度更高，当前阶段优先 argcomplete。

## 8. 启动入口

对齐架构设计 `bin/galaxy-diag`：

```bash
#!/usr/bin/env python3
"""galaxy-diag CLI 入口脚本"""

from galaxy_diag.workflow.cli.app import main

if __name__ == "__main__":
    main()
```

`pyproject.toml` 中注册入口点：

```toml
[project.scripts]
galaxy-diag = "galaxy_diag.workflow.cli.app:main"
```

## 9. 与现有代码的关系

入口流程已落地于 `workflow/cli/app.py:main()`：全局参数解析 →（按命令触发）硬件预检 → 模型健康检查 → 命令分发。各子命令模块在 `workflow/cli/cmd_*.py` 中实现 `register(subparsers)` + `handle(args)`。

| 职责 | 落地位置 | 说明 |
|---------|---------|------|
| 启动流程（配置加载 → 预检 → 健康检查） | `workflow/cli/app.py:main()` | 全局共享，需预检的命令（run/diagnose）触发，其余跳过 |
| 命令注册表 | `app.py:_COMMANDS` | 10 个子命令动态注册 |
| 各命令实现 | `workflow/cli/cmd_*.py` | `register()` + `handle()` 模式 |
| 配置层 | `src/galaxy_diag/config/` | `settings.py` 加载 / `defaults.py` 数据类 / `model_profile.py` 硬件推导 |
| 模型层 | `src/galaxy_diag/model/` | `client.py` ModelAdapter / `health.py` / `precheck.py` / `mock_client.py` |
| 错误体系 | `src/galaxy_diag/shared/errors.py` | 统一异常基类 + 子类 |

> 设计文档以已落地的新架构为准。

## 10. 错误处理策略

| 错误类型 | 处理方式 | 示例 |
|---------|---------|------|
| `GalaxyDiagError` 子类 | `console.print(f"[danger]✗ {e.message}[/danger]")` + hint | 配置错误、预检失败 |
| argparse 解析错误 | argparse 默认输出 + 退出码 2 | 参数缺失、无效选项 |
| 未预期异常 | `--verbose` 时 `console.print_exception()`，否则 `console.print("[danger]内部错误，使用 --verbose 查看详情[/danger]")` | 运行时异常 |
| 用户中断 (Ctrl+C) | `KeyboardInterrupt` → `console.print("\n[dim]已中断[/dim]")` → 退出码 130 | 交互中途退出 |

退出码规范：

| 退出码 | 含义 |
|-------|------|
| 0 | 成功 |
| 1 | 业务错误（配置/预检/模型调用失败） |
| 2 | 参数错误（argparse 约定） |
| 130 | 用户中断 (Ctrl+C) |

## 11. 离线依赖管理

CLI 框架引入的新依赖：

| 库 | 版本 | 用途 | 是否必须 |
|----|------|------|---------|
| `rich` | ≥13.0.0 | 终端输出 | ✅ 已在 requirements.txt |
| `argcomplete` | ≥3.0.0 | Shell 补全 | ⚠️ 可选（无它仍可运行，只是无补全） |
| `argparse` | — | 命令解析 | ✅ 标准库 |

`argcomplete` 的 wheel 需加入 `deploy/offline/wheels/` 目录和 `deploy/prepare_offline.sh` 脚本。

## 12. 验收测试用例

| # | 测试场景 | 输入/前置条件 | 预期输出/行为 | 验证方式 |
|---|---------|-------------|-------------|---------|
| 1 | 顶层 help | `galaxy-diag --help` | 列出所有子命令 + 全局选项，带颜色 | 快照测试 |
| 2 | 子命令 help | `galaxy-diag diagnose --help` | 参数说明 + 用法示例 | 快照测试 |
| 3 | confirm 默认拒绝 | `confirm("确认?", default=False)` + 回车 | 返回 False | 单元测试 mock input |
| 4 | confirm 确认 | `confirm("确认?")` + 输入 `y` | 返回 True | 单元测试 mock input |
| 5 | confirm 危险模式 | `confirm("确认?", danger=True)` + 输入 `y` | 红色提示，返回 False（需 CONFIRM） | 单元测试 |
| 6 | REVIEWING 危险操作 CONFIRM | 执行前熔断检测到 WARNING，进入 REVIEWING 要求 CONFIRM | 输入 CONFIRM 通过，输入其他终止 | 集成测试 |
| 7 | prompt_input 校验重试 | `validator=lambda x: None if x.isdigit() else "需数字"` + 输入 `abc` 再 `123` | 先提示"需数字"，后返回 `"123"` | 单元测试 mock 多次 input |
| 7 | prompt_edit_params | 模板含 `<IP>` 占位符，输入 `10.0.0.1` | 返回 `{"IP": "10.0.0.1"}`，展示替换后命令 | 单元测试 |
| 8 | 配置缺失兜底 | YAML 缺少 `log_level` | 使用默认值 `"INFO"`，不报错 | 单元测试 |
| 9 | CLI 参数覆盖 | `--verbose` + YAML 中 `log_level: INFO` | 日志级别为 DEBUG | 单元测试 |
| 10 | NO_COLOR | `NO_COLOR=1 galaxy-diag env` | 输出无 ANSI 颜色码 | 集成测试 |
| 11 | 管道模式 | `galaxy-diag env --output json \| jq .` | JSON 输出，无颜色/动画 | 集成测试 |
| 12 | 外网请求检测 | grep 全项目代码 | 无 `https://` 或非 localhost 的 `http://` | CI 脚本/静态扫描 |
| 13 | 无效子命令 | `galaxy-diag invalid_cmd` | argparse 错误提示 + 退出码 2 | 集成测试 |
| 14 | Ctrl+C 中断 | 交互过程中按 Ctrl+C | 输出"已中断" + 退出码 130 | 手动测试 |
| 15 | 用户可见步骤标题 | `galaxy-diag run --mock` 全流程 | 打印 7 个步骤标题（1/7 ~ 7/7），SECURITY_CHECKING/EXECUTION_GUARD/SNAPSHOT 不打印独立标题 | 集成测试 |
| 16 | SECURITY_CHECKING 归属修复建议 | D-03 检测通过 | 安全性提示显示在步骤 4/7 内，不切换步骤标题 | 集成测试 |
| 17 | EXECUTION_GUARD 归属人工审核 | E-02 熔断通过 | 熔断结果显示在步骤 5/7 内，不切换步骤标题 | 集成测试 |
| 18 | SNAPSHOT 归属执行 | 用户确认后 | 显示「正在创建快照」提示，快照创建后进入执行，不切换步骤标题 | 集成测试 |
| 19 | D-03 CRITICAL 回退 | 生成后检测有 CRITICAL 问题 | 回退到 PLANNING 重新生成，用户仍停留在步骤 4/7 | 集成测试 |
