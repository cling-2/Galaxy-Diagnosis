"""规则匹配引擎（REQ-C-02 验收标准 3）

对常见故障模式，通过预置规则快速匹配根因（低成本、确定性高）。
match_rules() 是纯函数——无副作用、不依赖 LLM、不修改状态，
可从 COLLECTING（短路预检）和 DIAGNOSING（规则快路径）两处安全调用。

对应设计文档 §规则匹配设计。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from galaxy_diag.shared.types import (
    Confidence,
    DiagnosisResult,
    DiagnosisSource,
    DiagnosticContext,
    EnvironmentType,
)


@dataclass
class DiagnosisRule:
    """一条诊断规则"""

    rule_id: str                     # 规则唯一标识
    description: str                 # 规则描述
    env_types: list[EnvironmentType]  # 适用环境类型（空列表=全环境适用）
    match_conditions: list[str]      # 匹配条件：关键词（AND 逻辑，全部命中才匹配）
    root_cause: str                  # 匹配后的根因描述
    confidence: Confidence           # 匹配后的置信度
    evidence_template: list[str]     # 证据模板
    investigation_steps: list[str]   # 排查步骤
    fault_scope: str                 # 故障范围


# ===== 预置规则（8 条） =====

DIAGNOSIS_RULES: list[DiagnosisRule] = [
    DiagnosisRule(
        rule_id="container_kubelet_down",
        description="Kubelet 服务未运行",
        env_types=[EnvironmentType.CONTAINER],
        match_conditions=["kubelet", "failed"],
        root_cause="Kubelet 服务未运行，容器编排异常",
        confidence=Confidence.SUSPECTED,
        evidence_template=["Kubelet 服务状态异常"],
        investigation_steps=[
            "检查 Kubelet 服务状态: systemctl status kubelet",
            "查看 Kubelet 日志: journalctl -u kubelet -n 200",
        ],
        fault_scope="容器编排层：Kubelet 服务",
    ),
    DiagnosisRule(
        rule_id="container_pod_crashloop",
        description="Pod 处于崩溃循环",
        env_types=[EnvironmentType.CONTAINER],
        match_conditions=["CrashLoopBackOff"],
        root_cause="Pod 处于崩溃循环，应用启动失败",
        confidence=Confidence.CONFIRMED,
        evidence_template=["Pod 状态为 CrashLoopBackOff"],
        investigation_steps=[
            "查看 Pod 事件: kubectl describe pod <pod-name>",
            "查看 Pod 日志: kubectl logs <pod-name> --previous",
        ],
        fault_scope="容器编排层：Pod 运行时",
    ),
    DiagnosisRule(
        rule_id="storage_nfs_stale",
        description="NFS 挂载失效",
        env_types=[],
        match_conditions=["stale file handle", "nfs"],
        root_cause="NFS 挂载失效，NAS 服务端不可达或网络中断",
        confidence=Confidence.CONFIRMED,
        evidence_template=["日志中发现 stale file handle 错误", "存储类型为 NFS"],
        investigation_steps=[
            "检查 NFS 服务端可达性: ping <nfs-server>",
            "重新挂载: umount /data/nfs && mount /data/nfs",
        ],
        fault_scope="存储层：NFS 挂载点",
    ),
    DiagnosisRule(
        rule_id="storage_mount_fail",
        description="存储挂载失败",
        env_types=[],
        match_conditions=["mount error"],
        root_cause="存储挂载失败，可能原因：认证/网络/权限",
        confidence=Confidence.SUSPECTED,
        evidence_template=["日志中发现 mount error"],
        investigation_steps=[
            "检查挂载点: mount | grep <path>",
            "查看详细错误: dmesg | tail -50",
            "检查存储服务端状态",
        ],
        fault_scope="存储层：挂载子系统",
    ),
    DiagnosisRule(
        rule_id="network_unreachable",
        description="目标网络不可达",
        env_types=[],
        match_conditions=["unreachable"],
        root_cause="目标网络不可达，可能原因：路由/防火墙/CNI 配置",
        confidence=Confidence.SUSPECTED,
        evidence_template=["网络连通性检测: 目标不可达"],
        investigation_steps=[
            "检查路由: ip route get <target>",
            "检查防火墙规则: iptables -S",
            "检查 CNI 配置: ls /etc/cni/net.d/",
        ],
        fault_scope="网络层：连通性",
    ),
    DiagnosisRule(
        rule_id="resource_oom",
        description="内存不足触发 OOM",
        env_types=[],
        match_conditions=["Out of memory", "OOM"],
        root_cause="内存不足触发 OOM",
        confidence=Confidence.CONFIRMED,
        evidence_template=["日志中发现 OOM 事件", "系统内存资源紧张"],
        investigation_steps=[
            "查看内存使用: free -h",
            "查看 OOM 日志: dmesg | grep -i oom",
            "查看占用内存最多的进程: ps aux --sort=-%mem | head -10",
        ],
        fault_scope="资源层：内存",
    ),
    DiagnosisRule(
        rule_id="service_start_fail",
        description="服务启动失败",
        env_types=[EnvironmentType.BARE_METAL, EnvironmentType.VM],
        match_conditions=["failed"],
        root_cause="服务启动失败，需查看日志确定具体原因",
        confidence=Confidence.SUSPECTED,
        evidence_template=["组件状态显示 failed"],
        investigation_steps=[
            "查看服务状态: systemctl status <service-name>",
            "查看服务日志: journalctl -u <service-name> -n 200",
        ],
        fault_scope="服务层：启动子系统",
    ),
    DiagnosisRule(
        rule_id="disk_io_error",
        description="磁盘 I/O 错误",
        env_types=[],
        match_conditions=["I/O error"],
        root_cause="磁盘 I/O 错误，可能磁盘故障或文件系统损坏",
        confidence=Confidence.CONFIRMED,
        evidence_template=["日志中发现 I/O error"],
        investigation_steps=[
            "检查磁盘健康: smartctl -a /dev/sdX",
            "检查文件系统: fsck -n /dev/sdXN",
            "查看磁盘错误: dmesg | grep -i 'error\\|fail' | grep -i disk",
        ],
        fault_scope="存储层：磁盘 I/O",
    ),
]


def _concat_context_text(ctx: DiagnosticContext) -> str:
    """将 DiagnosticContext 中所有可搜索字段拼接为一段文本

    用于关键词子串匹配，与 context.py 的 match_tools_by_keywords 策略一致。
    """
    parts: list[str] = []

    # 问题描述
    parts.append(ctx.problem_description)

    # 组件状态
    for comp in ctx.component_status:
        parts.append(f"{comp.get('name', '')} {comp.get('status', '')} {comp.get('detail', '')}")

    # 日志片段
    for snippet in ctx.log_snippets:
        parts.append(snippet.content)

    # 系统资源（key=value 文本）
    for key, value in ctx.system_resources.items():
        parts.append(f"{key} {value}")

    # 网络连通性
    for check in ctx.network_checks:
        reachable = "unreachable" if not check.get("reachable") else "reachable"
        parts.append(f"{check.get('target', '')} {reachable} {check.get('detail', '')}")

    # 用户上传
    for user_text in ctx.user_provided:
        parts.append(user_text)

    # 采集降级提示
    for warning in ctx.collection_warnings:
        parts.append(warning)

    return " ".join(parts)


def match_rules(ctx: DiagnosticContext) -> DiagnosisResult | None:
    """规则匹配：根据诊断上下文匹配已知故障模式

    纯函数，无副作用，不依赖 LLM。
    可从 COLLECTING（短路预检）和 DIAGNOSING（规则快路径）两处调用。

    Args:
        ctx: 诊断信息采集产出的 DiagnosticContext

    Returns:
        匹配成功返回 DiagnosisResult（source=RULE_MATCH），未匹配返回 None
    """
    concat_text = _concat_context_text(ctx).lower()
    env_type = ctx.env_info_ref  # EnvironmentType 字符串

    matched_rule: DiagnosisRule | None = None
    matched_env_specific = False  # 是否匹配到了环境特定规则

    for rule in DIAGNOSIS_RULES:
        # 1. 环境过滤
        if rule.env_types and env_type not in [e.value for e in rule.env_types]:
            continue

        # 2. 条件匹配（AND 逻辑：所有关键词都必须命中）
        all_hit = all(kw.lower() in concat_text for kw in rule.match_conditions)
        if not all_hit:
            continue

        # 3. 多条规则匹配时：环境特定规则优先
        is_env_specific = bool(rule.env_types)
        if matched_rule is None or (is_env_specific and not matched_env_specific):
            matched_rule = rule
            matched_env_specific = is_env_specific

    if matched_rule is None:
        return None

    # 构建 DiagnosisResult
    return DiagnosisResult(
        root_cause=matched_rule.root_cause,
        confidence=matched_rule.confidence,
        missing_info=[],
        evidence=matched_rule.evidence_template[:],
        env_type=EnvironmentType(env_type) if env_type in [e.value for e in EnvironmentType] else EnvironmentType.BARE_METAL,
        investigation_steps=matched_rule.investigation_steps[:],
        fault_scope=matched_rule.fault_scope,
        diagnosis_source=DiagnosisSource.RULE_MATCH,
    )
