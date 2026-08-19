"""galaxy-diag kb — 客户知识库管理（REQ-X-02 验收标准 4）

子命令：import / list / delete / reindex
import / reindex 需要 embedding 模型（触发 precheck）；list / delete 纯本地操作。
"""

from __future__ import annotations

import argparse

from galaxy_diag.knowledge.indexer import index_file, reindex_all
from galaxy_diag.knowledge.store import KnowledgeStore
from galaxy_diag.model.client import ModelAdapter
from galaxy_diag.model.mock_client import MockModelAdapter
from galaxy_diag.shared.errors import GalaxyDiagError, ModelCallError
from galaxy_diag.workflow.cli.display import get_console


def register(subparsers: argparse._SubParsersAction) -> None:
    sub = subparsers.add_parser(
        "kb",
        help="客户知识库管理 (REQ-X-02)",
        description="导入/列出/删除/重建索引客户故障案例",
    )
    kb_sub = sub.add_subparsers(dest="kb_action", title="kb 子命令")

    p_import = kb_sub.add_parser("import", help="导入案例文件")
    p_import.add_argument("file", help="案例文件路径（Markdown / 纯文本）")
    p_import.add_argument("--mock", action="store_true", help="使用 mock embedding（测试）")
    p_import.set_defaults(callback=cmd_kb_import)

    p_list = kb_sub.add_parser("list", help="列出已导入案例")
    p_list.set_defaults(callback=cmd_kb_list)

    p_delete = kb_sub.add_parser("delete", help="删除案例")
    p_delete.add_argument("case_id", help="案例 ID")
    p_delete.set_defaults(callback=cmd_kb_delete)

    p_reindex = kb_sub.add_parser("reindex", help="重建全部案例向量索引")
    p_reindex.add_argument("--mock", action="store_true", help="使用 mock embedding（测试）")
    p_reindex.set_defaults(callback=cmd_kb_reindex)


def _make_adapter(args) -> object:
    """构造 embedding 适配器（--mock 走 MockModelAdapter，否则走 ModelAdapter）"""
    if getattr(args, "mock", False):
        return MockModelAdapter()
    from galaxy_diag.config.settings import load_config
    config = load_config(getattr(args, "config", "config.yaml"))
    return ModelAdapter(config.llm)


def cmd_kb_import(args: argparse.Namespace) -> None:
    """导入案例文件"""
    console = get_console()
    adapter = _make_adapter(args)
    store = KnowledgeStore.load()
    try:
        cid = index_file(store, adapter, args.file)
        store.save()
    except ModelCallError as e:
        console.print(f"[danger]✗ 导入失败: {e.message}[/danger]")
        console.print(f"[dim]  {e.hint}[/dim]")
        return
    except GalaxyDiagError as e:
        console.print(f"[danger]✗ 导入失败: {e.message}[/danger]")
        return
    console.print(f"[success]✓ 已导入案例: {cid}[/success]")


def cmd_kb_list(args: argparse.Namespace) -> None:
    """列出已导入案例"""
    console = get_console()
    store = KnowledgeStore.load()
    cases = store.list_cases()
    if not cases:
        console.print("[dim]知识库为空，使用 'galaxy-diag kb import <file>' 导入案例[/dim]")
        return
    consistent = store.is_dimension_consistent()
    from rich.table import Table
    table = Table(show_header=True, header_style="bold", pad_edge=False)
    table.add_column("case_id", min_width=20)
    table.add_column("env_type", width=10)
    table.add_column("tags", width=15)
    table.add_column("摘要", min_width=30)
    for c in cases:
        env = c.env_type.value if c.env_type else "全部"
        summary = c.content.replace("\n", " ")[:50]
        table.add_row(c.case_id, env, ",".join(c.tags), summary)
    console.print(table)
    if not consistent:
        console.print(
            "[warning]⚠ 检测到向量维度不一致（embedding 模型可能已更换），"
            "请运行 'galaxy-diag kb reindex' 重建索引[/warning]"
        )


def cmd_kb_delete(args: argparse.Namespace) -> None:
    """删除案例"""
    console = get_console()
    store = KnowledgeStore.load()
    if not store.delete(args.case_id):
        console.print(f"[warning]⚠ 未找到案例: {args.case_id}[/warning]")
        return
    store.save()
    console.print(f"[success]✓ 已删除案例: {args.case_id}[/success]")


def cmd_kb_reindex(args: argparse.Namespace) -> None:
    """重建全部案例向量索引"""
    console = get_console()
    adapter = _make_adapter(args)
    store = KnowledgeStore.load()
    cases = store.list_cases()
    if not cases:
        console.print("[dim]知识库为空，无需重建索引[/dim]")
        return
    try:
        n = reindex_all(store, adapter)
        store.save()
    except ModelCallError as e:
        console.print(f"[danger]✗ 重建索引失败: {e.message}[/danger]")
        console.print(f"[dim]  {e.hint}[/dim]")
        return
    console.print(f"[success]✓ 已重建 {n} 条案例的向量索引[/success]")
