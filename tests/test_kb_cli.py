"""kb 命令测试"""

import pytest

from galaxy_diag.workflow.cli.cmd_kb import (
    cmd_kb_import, cmd_kb_list, cmd_kb_delete, cmd_kb_reindex,
)


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.fixture
def kb_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GALAXY_KB_DIR", str(tmp_path / "kb"))
    return tmp_path / "kb"


def test_kb_import_then_list(kb_dir, tmp_path):
    f = tmp_path / "case.md"
    f.write_text("---\nenv_type: container\n---\nCNI 异常内容", encoding="utf-8")
    cmd_kb_import(_Args(file=str(f), mock=True))
    # list 捕获输出
    from io import StringIO
    from rich.console import Console
    from galaxy_diag.workflow.cli import display as disp
    buf = StringIO()
    old = disp.get_console()
    disp._console = Console(file=buf, width=120, force_terminal=False)
    cmd_kb_list(_Args())
    disp._console = old
    out = buf.getvalue()
    assert "kb_" in out


def test_kb_delete(kb_dir, tmp_path):
    f = tmp_path / "case.md"
    f.write_text("CNI 异常内容", encoding="utf-8")
    cmd_kb_import(_Args(file=str(f), mock=True))
    from galaxy_diag.knowledge.store import KnowledgeStore
    store = KnowledgeStore.load()
    cid = store.list_cases()[0].case_id
    cmd_kb_delete(_Args(case_id=cid))
    store2 = KnowledgeStore.load()
    assert store2.get(cid) is None


def test_kb_reindex(kb_dir, tmp_path):
    f1 = tmp_path / "a.md"; f1.write_text("内容A", encoding="utf-8")
    f2 = tmp_path / "b.md"; f2.write_text("内容B", encoding="utf-8")
    cmd_kb_import(_Args(file=str(f1), mock=True))
    cmd_kb_import(_Args(file=str(f2), mock=True))
    cmd_kb_reindex(_Args(mock=True))
    from galaxy_diag.knowledge.store import KnowledgeStore
    store = KnowledgeStore.load()
    assert len(store.list_cases()) == 2
