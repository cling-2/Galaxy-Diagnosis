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
        """模拟非流式调用

        根据消息内容判断场景，返回预设 JSON 响应。
        """
        full_text = "\n".join(m.get("content", "") for m in messages)

        if "诊断结论" in full_text or "root-cause" in full_text:
            # 提取实际的用户上下文消息（排除 system prompt 和 few-shot 示例）
            # few-shot 示例是 role=user/assistant 的预设对话，含 kubectl/kube-system 等
            # 词会干扰环境判断。真实用户上下文以 "## 环境信息" 开头，取最后一条 user 消息。
            user_content = self._extract_real_user_content(messages)
            return self._mock_fix_response(user_content)
        else:
            # 同样排除 system prompt 和 few-shot 示例
            # system prompt 中含"网络"等词，会干扰故障类型判断
            user_content = self._extract_real_user_content(messages)
            return self._mock_diagnose_response(user_content)

    @staticmethod
    def _extract_real_user_content(messages: list[dict[str, str]]) -> str:
        """提取真实的用户上下文（排除 system prompt 和 few-shot 示例）

        few-shot 示例以预设对话形式注入（role=user/assistant），但它们是
        短小的示例文本，不含 "## 环境信息" 标记。真实用户上下文由
        format_fix_context / format_diagnosis_context 生成，以 "## 环境信息"
        或 "## " 开头。取最后一条包含该标记的 user 消息。
        """
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if "## 环境信息" in content or "root-cause" in content or "<user-input>" in content:
                return content
        # 兜底：取最后一条 user 消息
        user_msgs = [m.get("content", "") for m in messages if m.get("role") == "user"]
        return user_msgs[-1] if user_msgs else ""

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
        按"问题描述关键词"匹配故障类型，而非环境关键词。
        """
        # 判断环境类型
        env_type = "vm"
        if "容器" in full_text or "container" in full_text.lower():
            env_type = "container"
        elif "裸金属" in full_text or "bare_metal" in full_text.lower():

            env_type = "bare_metal"

        # 判断是否为 Kubernetes 环境（只有明确出现 k8s/kubernetes 关键词才算）
        is_k8s = "kubernetes" in full_text.lower() or "k8s" in full_text.lower() or "kube-system" in full_text.lower()

        # 根据问题描述关键词判断故障类型（优先匹配更具体的关键词）
        if "磁盘" in full_text or "存储" in full_text or "disk" in full_text.lower() or "pvscsi" in full_text.lower():
            # 磁盘/存储故障
            if env_type == "container":
                response = {
                    "root_cause": "galaxy-storage 容器挂载卷异常，数据磁盘对应的 volume 未正确挂载",
                    "confidence": "confirmed",
                    "evidence": [
                        "docker inspect galaxy-storage 显示 Mounts 中缺少数据卷",
                        "容器内 lsblk 未显示数据磁盘设备",
                    ],
                    "missing_info": [],
                    "investigation_steps": [
                        "检查容器挂载: docker inspect galaxy-storage --format='{{json .Mounts}}'",
                        "检查宿主机磁盘: lsblk",
                    ],
                    "fault_scope": "存储层：容器卷挂载异常",
                }
            else:
                response = {
                    "root_cause": "VM 磁盘控制器驱动 vmw_pvscsi 未加载，导致数据磁盘不可见",
                    "confidence": "suspected",
                    "evidence": [
                        "lsblk 仅显示系统盘 sda",
                        "dmesg 中发现 'pvscsi: unknown device' 警告",
                    ],
                    "missing_info": [],
                    "investigation_steps": [
                        "检查驱动模块: modprobe --dry-run vmw_pvscsi",
                        "查看内核日志: dmesg | grep pvscsi",
                    ],
                    "fault_scope": "存储层：VM 磁盘控制器驱动",
                }
        elif "网络" in full_text or "network" in full_text.lower():
            # 网络故障
            if env_type == "container" and is_k8s:
                response = {
                    "root_cause": "CNI 网络插件（calico-node）异常导致容器网络不通，Pod 处于 CrashLoopBackOff 状态",
                    "confidence": "confirmed",
                    "evidence": [
                        "kubectl get pods -n kube-system 显示 calico-node CrashLoopBackOff",
                        "容器间 ping 测试失败",
                    ],
                    "missing_info": [],
                    "investigation_steps": [
                        "检查 CNI Pod 状态: kubectl get pods -n kube-system -l k8s-app=calico-node",
                        "查看 Pod 日志: kubectl logs -n kube-system <calico-pod>",
                    ],
                    "fault_scope": "网络层：CNI 插件异常",
                }
            elif env_type == "container":
                response = {
                    "root_cause": "galaxy-network 容器异常退出，容器网络配置丢失导致网络不通",
                    "confidence": "confirmed",
                    "evidence": [
                        "docker ps 显示 galaxy-network 容器未运行",
                        "docker logs galaxy-network 显示配置加载失败",
                    ],
                    "missing_info": [],
                    "investigation_steps": [
                        "检查容器状态: docker ps -a --filter name=galaxy-network",
                        "查看容器日志: docker logs galaxy-network --tail 50",
                    ],
                    "fault_scope": "网络层：容器网络服务异常",
                }
            else:
                response = {
                    "root_cause": "网络配置异常导致 galaxy-network 服务启动失败",
                    "confidence": "suspected",
                    "evidence": [
                        "systemctl status galaxy-network 显示 failed",
                        "日志中存在 bind: address already in use",
                    ],
                    "missing_info": [],
                    "investigation_steps": [
                        "检查服务状态: systemctl status galaxy-network",
                        "检查端口占用: ss -tlnp | grep <PORT>",
                    ],
                    "fault_scope": "网络层：服务配置异常",
                }
        else:
            # 通用/其他故障
            response = {
                "root_cause": "系统资源异常，可能是配置错误导致组件运行不正常",
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
        is_container = "容器" in full_text or "container" in full_text.lower()
        is_k8s = "kubernetes" in full_text.lower() or "k8s" in full_text.lower() or "kube-system" in full_text.lower()

        if is_container and is_k8s:
            # Kubernetes 容器环境：CNI 修复
            response = {
                "steps": [
                    {
                        "command": "kubectl describe pod <CNI_POD> -n kube-system",
                        "description": "查看 CNI 插件 Pod 详情",
                        "risk_note": "只读操作，无风险",
                        "parameters": {"CNI_POD": "calico-node-abc12"},
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
                        "description": "验证 CNI DaemonSet 已恢复",
                        "risk_note": "只读操作，无风险",
                        "parameters": {"CNI_DAEMONSET": "calico-node"},
                        "is_verification": True,
                    },
                ],
                "script_language": "bash",
                "risk_notes": ["重启 CNI 期间集群网络不可用，建议在维护窗口操作"],
                "impact_scope": "重启 CNI DaemonSet，期间容器网络中断约 30-60 秒",
            }
        elif is_container and not is_k8s:
            # Docker 容器环境
            if "网络" in full_text or "network" in full_text.lower():
                # 容器网络故障
                response = {
                    "steps": [
                        {
                            "command": "docker logs <CONTAINER_NAME> --tail 100",
                            "description": "查看网络容器日志",
                            "risk_note": "只读操作，无风险",
                            "parameters": {"CONTAINER_NAME": "galaxy-network"},
                            "is_verification": True,
                        },
                        {
                            "command": "docker restart <CONTAINER_NAME>",
                            "description": "重启网络容器",
                            "risk_note": "重启期间网络服务不可用",
                            "parameters": {"CONTAINER_NAME": "galaxy-network"},
                            "is_verification": False,
                        },
                        {
                            "command": "docker ps --filter name=<CONTAINER_NAME>",
                            "description": "验证容器已恢复运行",
                            "risk_note": "只读操作，无风险",
                            "parameters": {"CONTAINER_NAME": "galaxy-network"},
                            "is_verification": True,
                        },
                    ],
                    "script_language": "bash",
                    "risk_notes": ["重启容器期间网络服务不可用"],
                    "impact_scope": "重启 galaxy-network 容器，期间网络服务中断约 5-10 秒",
                }
            elif "磁盘" in full_text or "存储" in full_text or "disk" in full_text.lower():
                # 容器存储/磁盘故障
                response = {
                    "steps": [
                        {
                            "command": "docker inspect <CONTAINER_NAME> --format='{{json .Mounts}}'",
                            "description": "检查容器挂载配置",
                            "risk_note": "只读操作，无风险",
                            "parameters": {"CONTAINER_NAME": "galaxy-storage"},
                            "is_verification": True,
                        },
                        {
                            "command": "docker restart <CONTAINER_NAME>",
                            "description": "重启存储容器以重新挂载",
                            "risk_note": "重启期间存储服务不可用",
                            "parameters": {"CONTAINER_NAME": "galaxy-storage"},
                            "is_verification": False,
                        },
                        {
                            "command": "docker ps --filter name=<CONTAINER_NAME>",
                            "description": "验证容器已恢复运行",
                            "risk_note": "只读操作，无风险",
                            "parameters": {"CONTAINER_NAME": "galaxy-storage"},
                            "is_verification": True,
                        },
                    ],
                    "script_language": "bash",
                    "risk_notes": ["重启容器期间存储服务不可用"],
                    "impact_scope": "重启 galaxy-storage 容器，期间存储服务中断约 5-10 秒",
                }
            else:
                # 容器通用故障
                response = {
                    "steps": [
                        {
                            "command": "docker logs <CONTAINER_NAME> --tail 100",
                            "description": "查看异常容器日志",
                            "risk_note": "只读操作，无风险",
                            "parameters": {"CONTAINER_NAME": "galaxy-api"},
                            "is_verification": True,
                        },
                        {
                            "command": "docker restart <CONTAINER_NAME>",
                            "description": "重启异常容器",
                            "risk_note": "重启期间该容器提供的服务不可用",
                            "parameters": {"CONTAINER_NAME": "galaxy-api"},
                            "is_verification": False,
                        },
                        {
                            "command": "docker ps --filter name=<CONTAINER_NAME>",
                            "description": "验证容器已恢复运行",
                            "risk_note": "只读操作，无风险",
                            "parameters": {"CONTAINER_NAME": "galaxy-api"},
                            "is_verification": True,
                        },
                    ],
                    "script_language": "bash",
                    "risk_notes": ["重启容器期间相关服务不可用"],
                    "impact_scope": "重启 galaxy-api 容器，期间 API 服务中断约 5-10 秒",
                }
        elif "磁盘" in full_text or "存储" in full_text or "pvscsi" in full_text.lower():
            # VM/存储环境：驱动加载修复
            response = {
                "steps": [
                    {
                        "command": "modprobe <DRIVER_MODULE>",
                        "description": "加载磁盘控制器驱动",
                        "risk_note": "加载内核模块需确认与当前内核版本兼容",
                        "parameters": {"DRIVER_MODULE": "vmw_pvscsi"},
                        "is_verification": False,
                    },
                    {
                        "command": "echo '- - -' > /sys/class/scsi_host/host0/scan",
                        "description": "重新扫描 SCSI 总线",
                        "risk_note": "只触发总线扫描，不修改数据",
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
                        "risk_note": "重启期间该服务不可用",
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
