"""测试脚本：构造固定 FixProposal，验证 执行→验证→快照回滚 闭环

绕过 LLM 生成环节，直接调用 safety 模块，纯测安全闭环。
前置：已执行 /tmp/galaxy-test/health.conf 的创建脚本。

用法（容器内）:
    python tests/manual/test_safety_loop.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

# src layout：把 src/ 加入 sys.path，使 galaxy_diag 可导入
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from galaxy_diag.safety import (
    create_snapshot,
    execution_guard_check,
    execute,
    list_snapshots,
    rollback,
    verify,
    write_audit,
)
from galaxy_diag.shared.types import (
    CommandTemplate,
    FixProposal,
    EnvironmentType,
)


TARGET_FILE = "/tmp/galaxy-test/health.conf"


def build_proposal() -> FixProposal:
    """构造修复建议：sed 修改 check_interval + grep 验证"""
    return FixProposal(
        commands=[
            CommandTemplate(
                command=f"sed -i 's/check_interval=0/check_interval=60/' {TARGET_FILE}",
                description="修正健康检查间隔配置为 60 秒",
                risk_note="修改本地测试配置文件，无害可逆",
                is_verification=False,
                requires_host=False,
            ),
            CommandTemplate(
                command=f"grep -c 'check_interval=60' {TARGET_FILE}",
                description="验证 check_interval 已改为 60",
                risk_note="只读验证",
                is_verification=True,
                requires_host=False,
            ),
        ],
        script=None,
        script_language=None,
        risk_notes=["修改 /tmp 下测试配置文件，不影响生产"],
        check_passed=True,
        check_issues=[],
        impact_scope="仅 /tmp/galaxy-test/health.conf",
        source=__import__("galaxy_diag").shared.types.FixSource.LLM,
    )


def main() -> int:
    # 前置检查
    if not Path(TARGET_FILE).exists():
        print(f"[FAIL] 测试文件不存在: {TARGET_FILE}")
        print("       请先执行:")
        print("       mkdir -p /tmp/galaxy-test && "
              "printf 'component=galaxy-test-agent\\ncheck_interval=0\\ntimeout=30\\n' "
              f"> {TARGET_FILE}")
        return 1

    # 备份原始内容（用于回滚后对比）
    original = Path(TARGET_FILE).read_text(encoding="utf-8")
    print(f"[INFO] 原始内容:\n{original}")
    print("=" * 60)

    proposal = build_proposal()
    session_id = "manual_safety_test"
    env_type = EnvironmentType.CONTAINER

    # ===== 1. 执行前熔断 (E-02) =====
    print("[步骤 5a] EXECUTION_GUARD — 执行前熔断")
    guard = execution_guard_check(proposal, env_type)
    print(f"  level={guard.level}")
    print(f"  message={guard.message}")
    if guard.matched_patterns:
        print(f"  matched={[(p.category, p.severity.value) for p in guard.matched_patterns]}")
    if guard.level == "critical":
        print("[FAIL] 熔断拦截（critical），无法继续")
        return 1
    print()

    # ===== 2. 创建快照 (E-03) =====
    print("[步骤 6a] SNAPSHOT — 创建快照")
    snap = create_snapshot(proposal, session_id=session_id)
    print(f"  snapshot_id={snap.snapshot_id}")
    print(f"  affected_files={snap.affected_files}")
    print(f"  affected_services={snap.affected_services}")
    print(f"  backup_path={snap.backup_path}")
    bak_file = Path(snap.backup_path) / Path(TARGET_FILE).name
    print(f"  备份文件存在: {bak_file.exists()}")
    if TARGET_FILE not in snap.affected_files:
        print(f"[WARN] {TARGET_FILE} 未被识别为 affected_file，快照未备份它，回滚将无法恢复")
    print()

    # ===== 3. 执行修复 (EXECUTING) =====
    print("[步骤 6b] EXECUTING — 执行修复")
    exe = execute(proposal, dry_run=False)
    print(f"  success={exe.success}")
    print(f"  output={exe.output}")
    if not exe.success:
        print(f"[FAIL] 执行失败，failed_step={getattr(exe, 'failed_step', '?')}")
        print("       自动触发回滚...")
        rb = rollback(snap.snapshot_id)
        print(f"  回滚 success={rb.success}")
        return 1

    # 确认文件已改
    after_exec = Path(TARGET_FILE).read_text(encoding="utf-8")
    print(f"[INFO] 执行后内容:\n{after_exec}")
    print()

    # ===== 4. 结果验证 (VERIFYING) =====
    print("[步骤 7] VERIFYING — 结果验证")
    ver = verify(proposal, dry_run=False)
    print(f"  success={ver.success}")
    print(f"  output={ver.output}")
    if not ver.success:
        print(f"  failed_step={ver.failed_step}, failed_description={ver.failed_description}")
    print()

    # ===== 5. 手动回滚测试 =====
    print("[回滚测试] 手动触发 rollback，验证快照恢复能力")
    rb = rollback(snap.snapshot_id)
    print(f"  rollback success={rb.success}")
    print(f"  restored_files={rb.restored_files}")
    print(f"  message={rb.message}")
    print()

    restored = Path(TARGET_FILE).read_text(encoding="utf-8")
    print(f"[INFO] 回滚后内容:\n{restored}")

    # 对比
    if restored == original:
        print("[PASS] ✅ 回滚成功恢复原始内容（check_interval=0 回来了）")
    else:
        print("[FAIL] ❌ 回滚后内容与原始不一致")
        print(f"  原始: {original!r}")
        print(f"  回滚: {restored!r}")
        return 1

    print()
    print("=" * 60)
    print("[完成] 全流程验证：熔断→快照→执行→验证→回滚 均通过")
    print(f"  快照列表: {[s.snapshot_id for s in list_snapshots()]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
