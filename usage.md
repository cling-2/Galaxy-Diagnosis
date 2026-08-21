# Galaxy-Diag 使用文档

> 银河平台部署问题定位工具 — CLI 命令参考与典型使用流程

## 目录

- [概述](#概述)
- [前置条件](#前置条件)
- [命令速览](#命令速览)
- [配置](#配置)
  - [配置文件](#配置文件)
  - [环境变量](#环境变量)
  - [硬件要求自动推导](#硬件要求自动推导)
- [典型使用流程](#典型使用流程)
  - [流程一：端到端诊断修复闭环](#流程一端到端诊断修复闭环)
  - [流程二：单步诊断（仅定位根因）](#流程二单步诊断仅定位根因)
  - [流程三：恢复中断的工作流](#流程三恢复中断的工作流)
  - [流程四：Mock 模式验证流程](#流程四mock-模式验证流程)
  - [流程五：安全回滚操作](#流程五安全回滚操作)
  - [流程六：导入客户知识库](#流程六导入客户知识库)
  - [流程七：查看推理过程](#流程七查看推理过程)
- [工作流状态机](#工作流状态机)
- [人工审核与安全机制](#人工审核与安全机制)
- [故障排查](#故障排查)

---

## 概述

`galaxy-diag` 是面向银河平台私有化部署场景的离线诊断-修复命令行工具。目标用户为通过 SSH 登录客户服务器的专业运维人员，在断网环境下独立完成：

```
环境识别 → 信息采集 → 根因分析 → 修复建议 → 人工确认 → 执行 → 验证
```

**两种使用方式：**

| 方式 | 命令 | 适用场景 |
|------|------|----------|
| 端到端闭环 | `galaxy-diag run` | 完整诊断+修复流程，从问题描述到修复完成 |
| 单步命令 | `galaxy-diag env` / `diagnose` / `fix` / … | 仅执行某一环节，如只看环境信息、只做诊断 |

**调用方式：**

```bash
galaxy-diag <子命令> [选项]          # 安装后直接调用
python -m galaxy_diag <子命令> [选项]  # 通过模块运行
```

---

## 前置条件

1. **Python 3.10+** 已安装
2. **Ollama** 已运行且模型已导入（或使用 `--mock` 模式跳过）
3. **配置文件** `config.yaml` 存在（默认与程序同目录，可用 `--config` 指定）

> 需调用 LLM 的命令（`run`、`diagnose`）启动时会自动执行硬件预检，资源不满足时拒绝启动。`--skip-precheck` 可跳过（调试/CI 用，不推荐生产使用）。

---

## 命令速览

### 全局选项

| 选项 | 说明 |
|------|------|
| `--config <路径>` | 配置文件路径（默认: `config.yaml`） |
| `--verbose` | 详细输出模式（出错时显示完整堆栈） |
| `--no-color` | 禁用颜色输出（等同 `NO_COLOR=1`） |
| `--skip-precheck` | 跳过硬件资源预检 |
| `--version` | 显示版本号 |

### 子命令

| 命令 | 一句话说明 | 常用示例 |
|------|-----------|----------|
| `run` | 端到端 7 步闭环工作流 | `galaxy-diag run -d "容器网络不通"` |
| `env` | 环境识别 + 硬件采集 | `galaxy-diag env`，`galaxy-diag env --type-only` |
| `diagnose` | 独立诊断（不进入修复） | `galaxy-diag diagnose -d "磁盘挂载失败"` |
| `fix` | 修复建议查看/编辑 | `galaxy-diag fix --session <id> --edit` |
| `review` | 审核确认 | `galaxy-diag review --session <id>` |
| `snapshot` | 快照管理与回滚 | `galaxy-diag snapshot list`，`galaxy-diag snapshot rollback <id>` |
| `audit-log` | 审计日志查询 | `galaxy-diag audit-log --since "2026-08-14"` |
| `trace` | 推理链路查看 | `galaxy-diag trace <session_id>` |
| `kb` | 客户知识库管理 | `galaxy-diag kb import case.md`，`galaxy-diag kb list` |
| `completion` | Shell 补全脚本生成 | `galaxy-diag completion bash` |

### 常用命令示例

```bash
# 端到端诊断（真实 LLM 推理）
galaxy-diag run -d "问题描述"

# Mock 模式（无需 Ollama，验证流程闭环）
galaxy-diag run -d "测试问题" --mock

# 附带日志文件
galaxy-diag run -d "问题描述" --log-file /var/log/syslog

# 自动模式（减少中间暂停）
galaxy-diag run --auto -d "问题描述"

# 恢复中断的会话
galaxy-diag run --resume
galaxy-diag run --resume <session_id>

# 单步命令
galaxy-diag env                        # 环境识别 + 硬件
galaxy-diag env --type-only            # 仅环境类型
galaxy-diag env --output json          # JSON 输出
galaxy-diag diagnose -d "问题描述"      # 仅诊断
galaxy-diag snapshot list              # 查看快照
galaxy-diag audit-log                  # 审计日志
galaxy-diag kb import <file>           # 导入知识库案例
galaxy-diag trace <session_id>         # 推理过程
galaxy-diag completion bash            # Shell 补全
```

---

## 配置

### 配置文件

默认配置文件为工作目录下的 `config.yaml`，可通过 `--config` 选项或 `GALAXY_CONFIG_FILE` 环境变量指定。

**配置文件结构：**

```yaml
# LLM 推理服务配置
llm:
  base_url: "http://localhost:11434/v1"   # Ollama 默认地址；可改为 vLLM 等地址
  model: "qwen3:1.7b"                     # 模型名称（Ollama tag 格式）
  api_key: "ollama"                       # Ollama 不需要真实 key，OpenAI SDK 要求非空
  timeout: 600                            # 请求超时秒数（纯 CPU 推理 8B 模型需 3-5 分钟）
  max_retries: 3                          # 最大重试次数
  max_tokens: 1024                        # 最大输出 token 数
  embed_model: "bge:large"               # RAG embedding 模型；空字符串 = 禁用 RAG

# 最低硬件要求（默认根据 llm.model 参数量自动推导，通常无需手动配置）
# hardware:
#   min_cpu_cores: 4
#   min_ram_gb: 3.0
#   min_gpu_vram_gb: 6.0
#   min_disk_gb: 10.0
#   gpu_required: false

# 客户知识库检索配置
knowledge:
  top_k: 3                 # 检索返回的最大案例数
  min_similarity: 0.5      # 最低余弦相似度阈值（0.0 = 不过滤）
```

**配置加载优先级：**

1. 代码默认值（`defaults.py` 中的 dataclass 默认值）
2. YAML 配置文件
3. 环境变量覆盖（前缀 `GALAXY_`）

### 环境变量

所有配置项均可通过环境变量覆盖，前缀为 `GALAXY_`，分层用 `_` 连接：

| 环境变量 | 对应配置 | 类型 |
|----------|----------|------|
| `GALAXY_CONFIG_FILE` | 配置文件路径 | str |
| `GALAXY_LLM_BASE_URL` | llm.base_url | str |
| `GALAXY_LLM_MODEL` | llm.model | str |
| `GALAXY_LLM_API_KEY` | llm.api_key | str |
| `GALAXY_LLM_TIMEOUT` | llm.timeout | int |
| `GALAXY_LLM_MAX_RETRIES` | llm.max_retries | int |
| `GALAXY_LLM_MAX_TOKENS` | llm.max_tokens | int |
| `GALAXY_LLM_EMBED_MODEL` | llm.embed_model | str |
| `GALAXY_HW_MIN_CPU_CORES` | hardware.min_cpu_cores | int |
| `GALAXY_HW_MIN_RAM_GB` | hardware.min_ram_gb | float |
| `GALAXY_HW_MIN_GPU_VRAM_GB` | hardware.min_gpu_vram_gb | float |
| `GALAXY_HW_MIN_DISK_GB` | hardware.min_disk_gb | float |
| `GALAXY_HW_GPU_REQUIRED` | hardware.gpu_required | bool |
| `GALAXY_KB_TOP_K` | knowledge.top_k | int |
| `GALAXY_KB_MIN_SIMILARITY` | knowledge.min_similarity | float |

**其他受尊重的环境变量：**

| 变量 | 说明 |
|------|------|
| `NO_COLOR` | 禁用 Rich 颜色输出（等同 `--no-color`） |
| `OLLAMA_LOG_LEVEL` | Ollama 服务端日志级别（默认 `ERROR`） |
| `LLAMA_LOG_LEVEL` | llama.cpp 日志级别：0=DEBUG 1=INFO 2=WARN 3=ERROR（默认 `3`） |
| `GIN_MODE` | llama-server gin HTTP 访问日志（默认 `release`，关闭 `[GIN]` 行） |

**示例：**

```bash
# 切换到 vLLM 服务
GALAXY_LLM_BASE_URL="http://localhost:8000/v1" GALAXY_LLM_MODEL="qwen3:8b" galaxy-diag run -d "问题描述"

# 禁用 RAG
GALAXY_LLM_EMBED_MODEL="" galaxy-diag run -d "问题描述"

# 指定配置文件
GALAXY_CONFIG_FILE=/etc/galaxy-diag/config.yaml galaxy-diag env
```

### 硬件要求自动推导

`hardware` 段默认根据 `llm.model` 的参数量自动推导（见 `model_profile.py`），无需手动配置。常见模型推导结果：

| 模型 | CPU 核数 | 内存 (GB) | GPU 显存 (GB) | 磁盘 (GB) |
|------|---------|-----------|--------------|----------|
| qwen3:1.7b | 2 | 4.0 | 1.94 | 3.5 |
| qwen3:8b | 8 | 7.4 | 5.4 | 9.2 |

如需覆盖，在 `config.yaml` 中取消注释 `hardware` 段并填写对应值（显式值优先于自动推导）。

---

## 典型使用流程

### 流程一：端到端诊断修复闭环

最常用的完整工作流，适用于首次遇到部署问题需要完整排查的场景。

```bash
# 1. 启动端到端工作流
galaxy-diag run -d "容器间网络不通，ping 不通，服务注册失败"

# 工具自动执行以下步骤：
# ┌─ 步骤 1/7：环境识别 ─────────────────────────────┐
# │ 识别为容器环境（Kubernetes）                        │
# │ 采集 CNI 配置、Pod 网络状态等容器可见信息            │
# └──────────────────────────────────────────────────┘
# ┌─ 步骤 2/7：信息收集 ─────────────────────────────┐
# │ 采集组件部署状态、服务日志、网络连通性               │
# └──────────────────────────────────────────────────┘
# ┌─ 步骤 3/7：根因分析 ─────────────────────────────┐
# │ 推理根因：CNI 网络插件配置异常                       │
# │ 置信度：已确认                                     │
# └──────────────────────────────────────────────────┘
# ┌─ 步骤 4/7：修复建议 ─────────────────────────────┐
# │ 生成修复命令 + 多步骤脚本                           │
# │ D-03 检测：语法通过 / 无危险命令 / 环境兼容          │
# └──────────────────────────────────────────────────┘
# ┌─ 步骤 5/7：人工审核 ─────────────────────────────┐
# │ 展示操作摘要 + 影响范围 + 回滚方案                   │
# │ [y/N]: y  ← 用户确认                              │
# └──────────────────────────────────────────────────┘
# ┌─ 步骤 6/7：执行 ─────────────────────────────────┐
# │ 自动创建快照 → 按步骤执行修复                       │
# └──────────────────────────────────────────────────┘
# ┌─ 步骤 7/7：结果验证 ─────────────────────────────┐
# │ 验证修复是否生效                                    │
# └──────────────────────────────────────────────────┘

# 2. 如附带日志文件，帮助更精准定位
galaxy-diag run -d "磁盘挂载失败" \
  --log-file /var/log/syslog \
  --log-file /var/log/galaxy/storage.log
```

**自动模式**（减少中间步骤暂停，审核仍需人工）：

```bash
galaxy-diag run -d "问题描述" --auto
```

---

### 流程二：单步诊断（仅定位根因）

仅需要定位根因、不需要修复建议时使用。

```bash
# 1. 先看当前环境
galaxy-diag env
# 输出：环境类型、CPU/内存/磁盘/RAID/网卡/存储等信息

# 2. 执行诊断（不进入修复流程）
galaxy-diag diagnose -d "数据磁盘未识别，lsblk 只显示系统盘"

# 3. 如需进一步查看推理过程
galaxy-diag trace <session_id>
```

---

### 流程三：恢复中断的工作流

工作流状态自动持久化，中断后可恢复继续。

```bash
# 场景：SSH 断连或用户 Ctrl+C 中断了工作流

# 1. 恢复最近的未完成会话
galaxy-diag run --resume

# 2. 恢复指定会话（session_id 在会话创建时显示）
galaxy-diag run --resume abc123

# 3. 如有多个旧会话需清理
galaxy-diag run --clean -d "新问题描述"
```

---

### 流程四：Mock 模式验证流程

无需 Ollama 和模型，使用预设响应验证工具流程完整性。适用于首次安装验证、CI 流程测试。

```bash
# 1. Mock 模式端到端运行
galaxy-diag run -d "测试问题" --mock

# 2. 查看审计日志确认流程已完整执行
galaxy-diag audit-log
```

---

### 流程五：安全回滚操作

修复操作引入新问题时，使用快照回滚。

```bash
# 1. 查看所有快照
galaxy-diag snapshot list

# 2. 查看特定快照详情
galaxy-diag snapshot show snap_20260814_001

# 3. 回滚（危险操作，需输入 CONFIRM 确认）
galaxy-diag snapshot rollback snap_20260814_001
# ⚠ 危险操作!
# 即将回滚到快照: snap_20260814_001
# 请输入 CONFIRM 确认: CONFIRM
# ✓ 回滚成功: 已恢复配置文件并重启相关服务

# 4. 查看审计日志确认回滚记录
galaxy-diag audit-log --since "2026-08-14"
```

---

### 流程六：导入客户知识库

将客户环境特有的故障案例导入知识库，辅助后续诊断。

```bash
# 1. 导入案例文件（Markdown 格式，含 frontmatter 元数据）
galaxy-diag kb import cases/disk-failure.md
# ✓ 已导入案例: case_20260814_001

# 2. 列出已导入案例
galaxy-diag kb list

# 3. 诊断时系统自动检索知识库相关案例
galaxy-diag run -d "类似的磁盘挂载问题"
# 诊断输出中标注：信息来源 = 客户特有案例

# 4. 更换 embedding 模型后重建索引
galaxy-diag kb reindex
```

---

### 流程七：查看推理过程

了解"系统为什么建议这样修复"，建立信任。

```bash
# 1. 查看完整推理链路（Rich 树形展示）
galaxy-diag trace abc123

# 2. 仅查看诊断步骤的推理
galaxy-diag trace abc123 --step DIAGNOSING

# 3. 详细模式（含 LLM 完整输出）
galaxy-diag trace abc123 -v
```

---

## 工作流状态机

`run` 命令内部由 10 态状态机驱动，映射为用户可见的 7 步流程：

```
内部状态                        用户步骤
─────────────────────────────────────────────
ENV_RECOGNISING     ──→  步骤 1: 环境识别
COLLECTING          ──→  步骤 2: 信息收集
DIAGNOSING          ──→  步骤 3: 根因分析
PLANNING            ──→  步骤 4: 修复建议
SECURITY_CHECKING   ──→  步骤 4: 修复建议（检测归入建议末尾）
EXECUTION_GUARD     ──→  步骤 5: 人工审核（熔断在审核前）
REVIEWING           ──→  步骤 5: 人工审核
SNAPSHOT            ──→  步骤 6: 执行（自动创建快照）
EXECUTING           ──→  步骤 6: 执行
VERIFYING           ──→  步骤 7: 结果验证
```

**特殊转换：**

| 场景 | 转换 |
|------|------|
| 已知故障模式 | COLLECTING → PLANNING（跳过 DIAGNOSING） |
| 信息不足 | DIAGNOSING → COLLECTING（回退补充采集） |
| 检测失败（CRITICAL） | SECURITY_CHECKING → PLANNING（重新生成） |
| 用户编辑修复 | REVIEWING → SECURITY_CHECKING（重走检测） |
| 用户拒绝 | REVIEWING → 终止 |
| 验证失败 | VERIFYING → DIAGNOSING（重新诊断） |
| 执行失败 | EXECUTING → 回滚后终止 |

---

## 人工审核与安全机制

### 确认交互

所有写操作（执行命令、修改配置、重启服务）执行前必须人工显式确认：

| 操作类型 | 确认方式 | 说明 |
|----------|----------|------|
| 普通操作 | `[y/N]` 输入 `y` | 默认拒绝（安全优先），回车 = 拒绝 |
| 危险操作 | 输入 `CONFIRM` 全称 | 红色提示，需完整输入 `CONFIRM` 方可执行 |

> 确认通过 Python 内置 `input()` 完成，不经 LLM 通道（红线 2），避免 Prompt 注入绕过审核。

### 危险操作防护

- **危险命令清单**：`rm -rf`、`mkfs`、`dd`、`iptables -F` 等，命中时强制拦截
- **影响范围评估**：展示操作影响（如"此操作将影响 3 个挂载点、2 个运行中的服务"）
- **变量展开检测**：防止危险命令被拆成变量绕过

### 生成后检测（D-03）

修复建议和脚本执行前自动检测：

| 检测维度 | 检测内容 |
|----------|----------|
| 语法检查 | Shell 语法错误检测 |
| 危险操作 | 危险命令模式匹配 |
| 环境兼容性 | 如容器环境建议修改 systemd 配置（不兼容） |
| 占位符检查 | 未替换的 `<参数>` 占位符 |

---

## 故障排查

| 问题 | 原因 | 解决方法 |
|------|------|----------|
| `✗ 硬件资源不满足最低要求，拒绝启动` | CPU/内存/磁盘/GPU 不满足 `config.yaml` 中 `hardware` 要求 | 升级硬件，或切换更小模型（如 `qwen3:1.7b`），或 `--skip-precheck` 跳过 |
| `⚠ LLM 推理服务不可用，已降级为信息不足结论` | Ollama 未启动或模型未导入 | 启动 Ollama：`ollama serve`；导入模型：`ollama create qwen3 -f Modelfile` |
| `⚠ 硬件预检异常，已跳过` | 预检模块自身异常（如 `/proc` 不可读） | 检查系统权限；预检异常不阻断运行 |
| `没有未完成的工作流会话` | `--resume` 但无中断的会话 | 直接运行 `galaxy-diag run -d "问题描述"` |
| `⚠ 检测到向量维度不一致` | embedding 模型更换后索引维度不匹配 | 运行 `galaxy-diag kb reindex` 重建索引 |
| `未找到会话 xxx 的推理链路记录` | session_id 不正确或尚未运行诊断 | 确认 session_id；先运行 `galaxy-diag run` |
| 纯 CPU 推理速度慢 | 大模型（8B+）纯 CPU 推理需 3-5 分钟 | 切换更小模型（`qwen3:1.7b`）；有 GPU 时配置 GPU 加速 |
| 内部错误 + `--verbose` 查看完整堆栈 | 程序内部异常 | 使用 `galaxy-diag --verbose <命令>` 查看详细堆栈并排查 |

---

> 本文档对应 galaxy-diag v0.1.0。更多部署相关内容见 [deployment.md](deployment.md)。
