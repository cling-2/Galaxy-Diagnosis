"""MockModelAdapter：模拟 LLM 调用（不连接真实推理服务）

用于开发测试和流程验证，跳过真实 LLM 调用，根据消息内容返回预设响应。
支持 diagnose 和 fix 两种调用场景。

用法：galaxy-diag run -d "问题描述" --mock
"""

from __future__ import annotations

import json
import re

from galaxy_diag.config.defaults import LLMConfig
from galaxy_diag.shared.errors import ModelCallError


class MockModelAdapter:
    """模拟 LLM 调用，返回预设响应

    不连接任何推理服务，不依赖 OpenAI SDK。
    根据消息内容判断是诊断还是修复场景，返回对应的模拟 JSON。
    """

    def __init__(self, config: LLMConfig | None = None):
        self.config = config or LLMConfig()

    def chat(self, messages: list[dict[str, str]], **kwargs) -> str:
        """模拟同步调用，返回预设 JSON 响应

        根据消息内容判断场景：
        - 包含"诊断结论"/"根因" → 修复场景
        - 其他 → 诊断场景
        """
        # 合并所有消息文本以判断场景
        full_text = "\n".join(m.get("content", "") for m in messages)

        if "诊断结论" in full_text or "root-cause" in full_text:
            return self._mock_fix_response(full_text)
        else:
            return self._mock_diagnose_response(full_text)

    def chat_stream(self, messages: list[dict[str, str]], **kwargs):
        """模拟流式调用，一次性返回"""
        yield self.chat(messages, **kwargs)

    def chat_with_tools(self, messages, tools, **kwargs):
        """模拟工具调用（不支持，返回空内容）"""
        from galaxy_diag.model.client import ChatResponse
        return ChatResponse(content=self.chat(messages), tool_calls=[], finish_reason="stop")

    # ===== 预设响应 =====

    def _mock_diagnose_response(self, full_text: str) -> str:
        """模拟诊断响应

        根据环境类型和问题描述返回合理的诊断结论。
        """
        # 判断环境类型
        env_type = "vm"
        if "容器" in full_text or "container" in full_text.lower():
            env_type = "container"
        elif "裸金属" in full_text or "bare_metal" in full_text.lower():
            env_type = "bare_metal"

        # 根据关键词判断故障类型
        if "网络" in full_text or "network" in full_text.lower() or "不通" in full_text:
            if env_type == "container":
                response = {
                    "root_cause": "CNI 网络插件（calico-node）异常导致容器网络不通，Pod 处于 CrashLoopBackOff 状态",
                    "confidence": "confirmed",
                    "evidence": [
                        "kubectl get pods -n kube-system 显示 calico-node CrashLoopBackOff",
                        "容器间 ping 测试失败",
                        "CNI 配置文件存在异常"
                    ],
                    "missing_info": [],
                    "investigation_steps": [
                        "检查 CNI Pod 状态: kubectl get pods -n kube-system",
                        "查看 CNI Pod 日志: kubectl logs -n kube-system <calico-pod>",
                    ],
                    "fault_scope": "网络层：CNI 插件异常",
                }
            else:
                response = {
                    "root_cause": "网络接口配置异常导致网络不通，IP 地址未正确分配或路由缺失",
                    "confidence": "suspected",
                    "evidence": [
                        "ip addr 显示网卡无 IP 地址",
                        "路由表缺少默认网关",
                    ],
                    "missing_info": [],
                    "investigation_steps": [
                        "检查网络接口状态: ip addr",
                        "检查路由表: ip route",
                    ],
                    "fault_scope": "网络层：接口配置",
                }
        elif "磁盘" in full_text or "存储" in full_text or "disk" in full_text.lower():
            response = {
                "root_cause": "VM 磁盘控制器驱动 vmw_pvscsi 未加载，导致 SCSI 设备不可见",
                "confidence": "suspected",
                "evidence": [
                    "lsblk 仅显示系统盘 sda",
                    "dmesg 中发现 'pvscsi: unknown device' 警告",
                ],
                "missing_info": [],
                "investigation_steps": [
                    "检查驱动模块: modprobe --dry-run vmw_pvscsi",
                ],
                "fault_scope": "存储层：VM 磁盘控制器驱动",
            }
        elif "服务" in full_text and ("启动" in full_text or "失败" in full_text):
            response = {
                "root_cause": "galaxy-storage 服务启动失败，配置文件中 NFS 挂载路径不存在",
                "confidence": "confirmed",
                "evidence": [
                    "systemctl status galaxy-storage 显示 failed",
                    "日志报错: mount point /data/nfs does not exist",
                ],
                "missing_info": [],
                "investigation_steps": [
                    "检查服务状态: systemctl status galaxy-storage",
                    "检查挂载点: ls -la /data/nfs",
                ],
                "fault_scope": "服务层：galaxy-storage 启动失败",
            }
        else:
            # 通用响应
            response = {
                "root_cause": "系统资源异常，可能是内核模块缺失或配置错误导致组件运行不正常",
                "confidence": "suspected",
                "evidence": [
                    "系统日志中发现异常错误信息",
                    "关键服务状态异常",
                ],
                "missing_info": ["具体故障组件的详细日志", "系统配置变更记录"],
                "investigation_steps": [
                    "检查系统日志: journalctl -n 100",
                    "检查服务状态: systemctl status galaxy-*",
                ],
                "fault_scope": "系统层：资源/配置异常",
            }

        response["env_type"] = env_type
        return json.dumps(response, ensure_ascii=False, indent=2)

    def _mock_fix_response(self, full_text: str) -> str:
        """模拟修复响应

        根据诊断结论和环境类型返回合理的修复建议。
        """
        # 判断环境类型
        if "容器" in full_text or "container" in full_text.lower() or "kubectl" in full_text:
            # 容器环境：CNI 修复
            response = {
                "steps": [
                    {
                        "command": "kubectl describe pod <POD_NAME> -n <NAMESPACE>",
                        "description": "查看异常 Pod 详情",
                        "risk_note": "只读操作，无风险",
                        "parameters": {"POD_NAME": "abnormal-pod", "NAMESPACE": "default"},
                        "is_verification": True,
                    },
                    {
                        "command": "kubectl rollout restart daemonset <CNI_DAEMONSET> -n kube-system",
                        "description": "重启 CNI DaemonSet",
                        "risk_note": "重启期间容器网络不可用",
                        "parameters": {"CNI_DAEMONSET": "calico-node"},
                        "is_verification": False,
                    },
                    {
                        "command": "kubectl get pods -n kube-system -l k8s-app=<CNI_DAEMONSET>",
                        "description": "验证 CNI Pod 已恢复",
                        "risk_note": "只读操作，无风险",
                        "parameters": {"CNI_DAEMONSET": "calico-node"},
                        "is_verification": True,
                    },
                ],
                "script_language": "bash",
                "risk_notes": ["重启 CNI 期间集群网络不可用，建议在维护窗口操作"],
                "impact_scope": "重启 CNI DaemonSet，期间容器网络中断约 30-60 秒",
            }
        elif "磁盘" in full_text or "存储" in full_text or "pvscsi" in full_text.lower():
            # VM/存储环境：驱动加载修复
            response = {
                "steps": [
                    {
                        "command": "modprobe <DRIVER_MODULE>",
                        "description": "加载磁盘控制器驱动模块",
                        "risk_note": "加载内核模块可能影响系统稳定性",
                        "parameters": {"DRIVER_MODULE": "vmw_pvscsi"},
                        "is_verification": False,
                    },
                    {
                        "command": "rescan-scsi-bus.sh",
                        "description": "重新扫描 SCSI 总线",
                        "risk_note": "热扫描可能导致短暂的 I/O 延迟",
                        "parameters": {},
                        "is_verification": False,
                    },
                    {
                        "command": "lsblk",
                        "description": "验证数据磁盘是否可见",
                        "risk_note": "只读操作，无风险",
                        "parameters": {},
                        "is_verification": True,
                    },
                ],
                "script_language": "bash",
                "risk_notes": ["加载内核模块需确认与当前内核版本兼容"],
                "impact_scope": "加载内核模块 vmw_pvscsi，扫描 SCSI 总线，无服务中断",
            }
        else:
            # 通用修复
            response = {
                "steps": [
                    {
                        "command": "systemctl restart <SERVICE_NAME>",
                        "description": "重启异常服务",
                        "risk_note": "重启期间服务不可用",
                        "parameters": {"SERVICE_NAME": "galaxy-storage"},
                        "is_verification": False,
                    },
                    {
                        "command": "systemctl status <SERVICE_NAME>",
                        "description": "验证服务已恢复",
                        "risk_note": "只读操作，无风险",
                        "parameters": {"SERVICE_NAME": "galaxy-storage"},
                        "is_verification": True,
                    },
                ],
                "script_language": "bash",
                "risk_notes": ["重启服务期间相关功能不可用"],
                "impact_scope": "重启 galaxy-storage 服务，期间存储功能中断",
            }

        return json.dumps(response, ensure_ascii=False, indent=2)

    def _call_error_hint(self, error: Exception) -> str:
        return "Mock 模式不应出现错误"
