# CLI框架搭建(REQ-F-01)

## 模块概述

提供 CLI 交互方式，使运维人员在无图形界面的服务器环境中也能完整使用系统功能。

## 项目结构

galaxy-diag/
├── cli/              # CLI入口与交互逻辑
│   ├── main.py       # 主入口
│   └── commands/     # 子命令模块
├── core/             # 核心业务逻辑（与CLI解耦）
│   ├── model_client.py
│   ├── env_detector.py
│   └── workflow.py
├── config/           # 配置文件（模型地址、阈值等）
├── tests/            # 单元测试
└── requirements.txt  # 依赖清单（需提前离线下载wheel包）

文件路径	核心职责	禁止事项	关键导出/接口
cli/main.py	解析命令行参数，分发子命令，初始化全局Console	不包含任何业务逻辑	main() 入口函数
cli/ui.py	封装Rich Console、样式常量、交互组件	不调用core层任何函数	console, confirm(), prompt_input()
cli/commands/diagnose.py	diagnose子命令的参数定义与回调	不直接实例化模型客户端	register(subparsers)
core/model_client.py	LLM API调用封装，超时/重试逻辑	不处理CLI渲染	ModelClient.chat(messages)

设计要点：cli/ 层只能依赖 core/ 层的接口，严禁反向依赖。这是后续单元测试和解耦的基础。

## Rich UI 样式规范与交互组件API

### 全局样式常量表

### 交互组件函数签名

## 离线依赖管理策略

考虑到项目要求离线依赖安装，依赖库需能按照[部署文档](deployment.md)的步骤离线导入

## 验收测试用例

测试场景	输入/前置条件	预期输出/行为	验证方式
help美化	python -m cli.main diagnose --help	带颜色的格式化帮助，含示例	快照测试或字符串断言
confirm默认值	用户直接回车，default=False	返回False	单元测试mock input
confirm危险模式	danger=True, 用户输入"y"	红色提示符，返回True	单元测试+样式断言
无效输入重试	validator=lambda x: x.isdigit(), 输入"abc"再输入"123"	先提示"输入无效"，后返回"123"	单元测试mock多次input
配置缺失兜底	yaml中缺少log_level字段	使用默认值"INFO"，不报错	单元测试
外网请求检测	grep全项目代码	无https://或非localhost的http://	CI脚本/静态扫描