# 银河部署问题定位工具 — 验收测试方案

> 本文档供现场验收演示时参考，覆盖任务书典型验证场景中 **2 个 Case** + **1 个诚实兜底 Case** + **2 个选做加分项 Case**。

---

## Case 1：配置残留致升级失败（容器环境，对齐任务书「容器」环境验证）

### 考核能力

配置采集 + 根因定位 + 快照回滚（REQ-B-01/B-02/C-01/C-02/D-01/D-02/E-01/E-02/E-03/E-04/F-01/F-02/F-03）

选择理由：该 Case 是闭环中**唯一以"快照回滚"为核心考核点**的 Case，七步全部触发，覆盖 REQ 最全；同时覆盖任务书典型验证场景中的「容器环境」。

### 测试命令

```bash
galaxy-diag run -d \
  "银河平台升级到 v2.4 后，galaxy-api 组件启动失败，日志报错，\
  怀疑升级后旧配置文件未清理干净。这是容器化部署环境。" \
  --auto
```

> `--auto` 减少中间暂停，但 **REVIEWING 步骤仍需人工确认**（红线 2 保证）。
> 如需逐步观察每步细节，去掉 `--auto`。

### 逐步验收要点

#### 步骤 1/7：环境识别

| 验收项 | 预期 | 对应 REQ |
|---|---|---|
| 环境类型 | 输出 `容器` / `CONTAINER` | B-01 |
| 容器运行时 | 输出 `KUBERNETES` 或 `DOCKER`（根据实际环境） | B-01 |
| 是否有 docker/kubectl CLI | 检测结果与实际一致 | B-01 |

#### 步骤 2/7：信息采集

| 验收项 | 预期 | 对应 REQ |
|---|---|---|
| 组件状态采集 | 调用 `collect_component_status`，输出 galaxy-api 等组件状态 | B-02, C-01 |
| 系统资源采集 | 调用 `collect_system_resources`，输出 CPU/内存/磁盘使用 | B-02 |
| 关键词→Tool 映射 | 问题描述含"启动""失败""组件"→激活 `TOOL_COMPONENT` + `TOOL_RESOURCES` | C-01 |
| 无采集静默吞错 | 单项采集失败应有 warning 提示，不阻断 | 工程质量 |

#### 步骤 3/7：根因分析

| 验收项 | 预期 | 对应 REQ |
|---|---|---|
| 诊断结论 | 根因涉及"配置文件/版本不匹配/升级残留"（具体措辞依赖 LLM 或规则） | C-02 |
| 置信度 | `confirmed` 或 `suspected`（非 `insufficient`） | C-03 |
| 证据列表 | 非空，包含支撑根因的采集证据 | C-02 |
| 诊断来源 | `RULE_MATCH`（规则快路径命中）或 `LLM`（LLM 推理） | C-02 |

> **Mock 模式说明**：`--mock` 下 mock_client 按关键词返回预设响应，诊断结论为"系统资源异常，可能是配置错误导致组件运行不正常"（suspected）。Mock 用于验证流程闭环，不验证诊断质量。

#### 步骤 4/7：修复建议

| 验收项 | 预期 | 对应 REQ |
|---|---|---|
| 修复命令 | 包含带参数占位符的命令（如 `<SERVICE_NAME>`、`<BACKUP_PATH>`） | D-01 |
| 修复脚本 | ≥2 步非验证命令时自动生成 bash/python 脚本，含 `set -euo pipefail` | D-02 |
| 验证步骤 | 包含 `is_verification=True` 的命令（如 `systemctl status`） | D-02 |
| 占位符编辑 | 存在未解析占位符时自动进入参数编辑流程 | D-01, F-03 |

##### 4a：生成后检测（D-03）

| 验收项 | 预期 | 对应 REQ |
|---|---|---|
| 语法检查 | 无 CRITICAL 语法错误（未解析占位符为 CRITICAL → 回退重新生成） | D-03 |
| 环境兼容性检查 | 容器内 `kubectl`/`docker`/`modprobe` 不可用 → WARNING | D-03 |
| 危险操作建议性提示 | `systemctl restart` 等 → WARNING（不阻止，E-02 才强制拦截） | D-03 |

#### 步骤 5/7：人工审核

| 验收项 | 预期 | 对应 REQ |
|---|---|---|
| E-02 执行前熔断 | `execution_guard_check` 输出 guard level（pass/warning/critical） | E-02 |
| 审核菜单 | 显示 `y(确认) / n(拒绝) / e(编辑) / d(删除步骤) / r(重排序)` | F-03 |
| **红线 2 保证** | 确认输入走 stdin `input()`，**不经 LLM 通道** | E-01 |
| 危险操作 CONFIRM | guard level 为 warning/critical 时，需手敲 `CONFIRM`（非 y） | E-02 |
| 拒绝不执行 | 输入 n → 工具不执行，不反复要求确认 | E-01 |
| 编辑回退 | 输入 e → 重新进入 SECURITY_CHECKING → 再审核 | F-03 |

**验证方法**：在审核步骤尝试以下操作：
1. 直接输入 `n` → 工具应拒绝并终止，**不反复要求确认**
2. 重新运行，审核时输入 `y` → 正常进入下一步

#### 步骤 6/7：快照 + 执行

| 验收项 | 预期 | 对应 REQ |
|---|---|---|
| 自动创建快照 | 输出 `创建快照 snap_YYYYMMDD_HHMMSS`，备份受影响文件到 `~/.galaxy-diag/snapshots/snap_*/bak/` | E-03 |
| 服务状态记录 | 快照记录相关服务的 `systemctl status` 输出 | E-03 |
| 执行修复命令 | 逐条执行非验证命令，输出执行结果 | F-02 |
| `requires_host` 命令 | 容器内无法执行的命令仅打印不执行 | D-03 |

**快照验证**：检查 `~/.galaxy-diag/snapshots/` 目录：
```bash
ls ~/.galaxy-diag/snapshots/snap_*/bak/     # 应有备份文件
cat ~/.galaxy-diag/snapshots/snap_*/meta.json  # 应有 affected_files/affected_services
```

#### 步骤 7/7：结果验证

| 验收项 | 预期 | 对应 REQ |
|---|---|---|
| 验证命令执行 | 运行 `is_verification=True` 的命令 | F-02 |
| 验证通过 | 所有验证命令 exit 0 → 输出"修复验证通过" | F-02 |
| 验证失败 | 任一命令非 0 → 输出"修复验证未通过" + 下一步建议 + 一键回滚提示 | E-03 |

#### 回滚验证（核心考核点）

若修复执行失败或验证未通过，工具应**自动触发回滚**：

| 验收项 | 预期 | 对应 REQ |
|---|---|---|
| 自动回滚 | 执行失败时输出"开始自动回滚..." | E-03 |
| 文件恢复 | 从快照备份恢复原始文件 | E-03 |
| 服务恢复 | 重启受影响的服务 | E-03 |
| 回滚审计 | 审计日志记录 `result=rollback` | E-04 |

**手动回滚验证**：
```bash
galaxy-diag snapshot list   # 查看快照列表
# 若验证失败，工具输出回滚提示，按提示操作
```

#### 全流程审计日志

| 验收项 | 预期 | 对应 REQ |
|---|---|---|
| 日志文件 | `~/.galaxy-diag/audit.jsonl` 存在且非空 | E-04 |
| 记录完整 | 包含 confirmed/success（或 failure/rollback）等 result | E-04 |
| LLM 不可篡改 | 审计写入走 `open().write()` 直写文件，不经 Agent/LLM 输出流 | E-04 |

```bash
# 查看审计日志
cat ~/.galaxy-diag/audit.jsonl
```

---

## Case 2：虚假故障判断（用户描述的故障不存在，REQ-C-03 诚实兜底专项）

### 考核能力

采集验证 + 不编造根因 + 诚实兜底（REQ-C-03 专项考察）

选择理由：本 Case 专项考察任务书 REQ-C-03 要求的"当信息不足或超出知识范围时，必须诚实声明不确定性，而非编造似是而非的结论"——其他 Case 都有真实故障，本 Case 考的是**当故障不存在时工具不编造**。这是 REQ-C-03 在所有 Case 贯穿要求的集中验证。

### 测试命令

```bash
galaxy-diag --skip-precheck run -d \
  "网络不通，ping 不通其他节点，请排查原因。这是容器化部署环境。" \
  --auto
```

### 设计逻辑

- 用户声称"服务启动失败"，但实际环境服务运行正常（或不存在→不会报 failed）
- 工具应：采集组件状态 → 发现无 failed 组件 → **反幻觉事实校验触发矛盾** → 终止，不编造根因
- 这条路径是纯规则判定（`hallucination_guard.py`），**零 LLM 依赖，零幻觉风险**，确定性最强

### 逐步验收要点

#### 步骤 1/7：环境识别

| 验收项 | 预期 | 对应 REQ |
|---|---|---|
| 环境类型 | 正确识别当前运行环境（裸金属/VM/容器） | B-01 |

#### 步骤 2/7：信息采集 → 反幻觉拦截

| 验收项 | 预期 | 对应 REQ |
|---|---|---|
| 组件状态采集 | 采集 galaxy-api、galaxy-gateway 等组件状态 | B-02, C-01 |
| **关键：反幻觉事实校验** | `service_ok` 规则触发：用户说"启动失败"但采集显示无 failed 组件 → **矛盾** | C-03 |
| 工具输出 | `"您的部署环境中服务运行正常，不存在启动失败问题"` | C-03 |
| **工作流终止** | 不进入根因分析、不生成修复命令 | C-03 |

#### 核心验证：不编造

| 验收项 | 预期 | 对应 REQ |
|---|---|---|
| **不编造根因** | 工具**不输出**任何"可能的根因"（如"配置错误""端口冲突"等推测） | C-03 |
| **不生成修复** | 不进入 PLANNING 步骤，不输出任何修复命令 | C-03 |
| **不附和用户猜测** | 输出中不出现对"启动失败"的确认或推测 | C-03 |
| 审计记录 | 审计日志记录 `failure` + 反幻觉拦截原因 | E-04 |

### 反幻觉校验规则说明

工具内置 4 条事实校验规则（`hallucination_guard.py`），均为纯规则判定，不经 LLM：

| 规则 ID | 用户声称 | 采集证明不存在 | 矛盾消息 |
|---|---|---|---|
| `network_ok` | 网络不通 | 所有 ping 目标可达 | "网络连通性正常，不存在网络不通问题" |
| `service_ok` | 服务启动失败 | 无组件状态为 failed | "服务运行正常，不存在启动失败问题" |
| `mount_ok` | 挂载失败 | 日志无 mount error/stale file handle | "存储挂载状态正常，不存在挂载失败问题" |
| `resource_ok` | OOM/内存不足 | 无 OOM 且内存使用率 < 90% | "内存资源充足，不存在 OOM 问题" |

> 本 Case 触发 `service_ok` 规则。如需验证其他规则，修改问题描述即可：
> - 网络：`"...网络不通，无法 ping 通 10.0.0.1..."` （10.0.0.1 实际可达时触发）
> - 挂载：`"...NFS 挂载失败，日志出现 mount error..."`（日志无 mount error 时触发）

---

## 两个 Case 的互补性

| 维度 | Case 1（配置残留） | Case 2（虚假故障） |
|---|---|---|
| 闭环完整度 | 七步全跑通（环境→采集→根因→修复→审核→执行→验证） | 采集后即终止（诚实兜底，不继续） |
| 环境类型 | 容器（对齐任务书场景 2） | 任意（裸金属/VM/容器均可） |
| 根因置信度 | confirmed/suspected → **继续修复** | 矛盾 → **终止不修** |
| 安全机制触发 | 快照 + 回滚 + 人工审核 + 审计日志 | 不触发（无写操作） |
| 核心考核 | 修复闭环 + 安全可控（REQ-D/E/F） | 诚实兜底 + 不编造（REQ-C-03） |
| 走到最远步骤 | 步骤 7/7（验证） | 步骤 2/7（采集后拦截） |

一正一反：**Case 1 验证"有问题时能安全地修"，Case 2 验证"没问题时诚实地不编"**。覆盖任务书考核最核心的两个维度：修复闭环与诚实声明。

---

## Mock 模式 vs 真实 LLM 模式

| 项目 | Mock 模式 (`--mock`) | 真实 LLM 模式 |
|---|---|---|
| 用途 | 验证流程闭环（状态机、审核、快照、审计） | 验证诊断质量（根因准确性、修复合理性） |
| 诊断质量 | 预设关键词匹配，诊断较笼统 | 基于真实采集上下文推理，诊断更精准 |
| Case 1 诊断结论 | "系统资源异常，可能是配置错误..."（suspected） | "升级后旧版本配置文件残留..."（confirmed） |
| Case 2 行为 | 反幻觉校验仍正常触发（纯规则，不依赖 LLM） | 同左（反幻觉校验是纯规则，与 LLM 无关） |
| 建议用途 | 快速验证流程 | **正式验收演示必须用真实 LLM** |

> ⚠️ 正式验收必须使用真实推理服务，不得预置诊断结果。Mock 模式（`--mock`）仅用于开发验证流程闭环，**正式验收必须连接真实 Ollama 推理服务**，走真实采集 + 真实 LLM 推理路径。

---

## Case 3（加分项 A1）：RAG 客户知识库集成（REQ-X-02）

### 考核能力

客户案例导入 + 语义检索 + 诊断增强 + 来源标注（REQ-X-02 四项验收标准全覆盖）

选择理由：任务书 X-02 为选做加分项，本 Case 完整验证"导入→检索→注入→标注"端到端链路，展示系统利用客户特有经验提升诊断质量的能力。

### 前置准备

1. **embedding 模型已部署**：`config.yaml` 中 `llm.embed_model` 非空（如默认 `"bge:large"`），且该模型已通过 `ollama create` 导入到 Ollama
2. **准备案例文件**：编写一份模拟客户历史故障案例（Markdown 格式，含 frontmatter），内容与 Case 1 的"配置残留升级失败"场景语义相关

示例案例文件 `case_config_stale.md`：

```bash
cat > case_config_stale.md <<'EOF'
---
env_type: container
tags: [galaxy-api, config, upgrade]
---

# 配置残留导致升级后启动失败

## 现象
银河平台从 v2.2 升级到 v2.3 后，galaxy-api 组件启动失败，日志报 config version mismatch。

## 根因
升级脚本未清理 /etc/galaxy/ 下旧版本配置文件，api 启动时读到 v2.2 schema 与 v2.3 代码不兼容。

## 修复
1. 备份旧配置：cp /etc/galaxy/api.conf /etc/galaxy/api.conf.bak
2. 清理残留：rm /etc/galaxy/api.conf
3. 重新生成：galaxy-api config init --version v2.3
4. 重启服务：systemctl restart galaxy-api
5. 验证：systemctl status galaxy-api
EOF
```

### 测试步骤

#### 步骤 1：导入案例

```bash
galaxy-diag --skip-precheck kb import case_config_stale.md
```

| 验收项 | 预期 | 对应 X-02 验收标准 |
|---|---|---|
| 导入成功 | 输出 `case_id`（格式 `kb_<hash12>`） | 标准① 支持导入 Markdown |
| 向量生成 | Ollama embedding 调用成功（非 mock） | 标准① |
| 持久化 | `~/.galaxy-diag/knowledge_base/` 下新增 `index.json` + `vectors.npy` + `cases/<case_id>.md` | 标准① |

#### 步骤 2：列出案例

```bash
galaxy-diag kb list
```

| 验收项 | 预期 | 对应 X-02 验收标准 |
|---|---|---|
| 案例可见 | 列表包含刚导入的 case_id、env_type=container、tags | 标准④ 管理命令-列表 |

#### 步骤 3：知识库增强诊断

用与导入案例**语义相关但表述不同**的问题触发诊断，验证 RAG 检索命中：

```bash
galaxy-diag --skip-precheck run -d \
  "升级银河平台后 API 组件起不来，配置版本对不上" \
  --auto
```

| 验收项 | 预期 | 对应 X-02 验收标准 |
|---|---|---|
| RAG 检索触发 | 诊断步骤输出中显示"检索到 N 条相关案例" | 标准② 语义检索被引用 |
| 来源标注 | 诊断结果中 `referenced_knowledge` 非空，包含 `case_id` / `similarity` / `summary` | 标准③ 标注信息来源 |
| 诊断增强 | 诊断结论中引用了客户案例的根因或修复思路（如"升级脚本未清理旧配置"） | 标准② |
| 来源区分 | 输出明确标注"来自客户知识库"vs"来自通用推理" | 标准③ |

#### 步骤 4：RAG 禁用对比

将 `llm.embed_model` 设为空字符串（禁用 RAG），再次运行同一问题：

```bash
# 临时禁用 RAG
export GALAXY_LLM_EMBED_MODEL=""
galaxy-diag run -d "升级银河平台后 API 组件起不来，配置版本对不上" --auto
```

| 验收项 | 预期 | 对应 X-02 验收标准 |
|---|---|---|
| RAG 跳过 | 诊断步骤输出中显示"RAG 已禁用"或无检索提示 | — |
| referenced_knowledge 为空 | 诊断结果中无客户案例引用 | 对比验证：禁用 vs 启用 |
| 诊断质量差异 | 无 RAG 时诊断结论更笼统（仅有通用推理，无客户特有经验） | 验证 RAG 增量价值 |

#### 步骤 5：删除案例

```bash
galaxy-diag kb delete <case_id>
galaxy-diag kb list    # 确认已删除
```

| 验收项 | 预期 | 对应 X-02 验收标准 |
|---|---|---|
| 删除成功 | list 输出中不再包含该 case_id | 标准④ 管理命令-删除 |

### 任务书验收标准对照

| X-02 验收标准 | 本 Case 覆盖步骤 |
|---|---|
| ① 支持导入 Markdown 或纯文本 | 步骤 1（Markdown frontmatter 导入） |
| ② 导入案例可通过语义检索在诊断中被引用 | 步骤 3（RAG 检索命中 + 诊断增强） |
| ③ 诊断输出中标注信息来源 | 步骤 3（referenced_knowledge + 来源区分） |
| ④ 提供知识库管理命令（导入、列表、删除） | 步骤 1/2/5（import/list/delete） |

---

## Case 4（加分项 A2）：Agent Trace 推理可观测（REQ-X-04）

### 考核能力

推理链路记录 + 命令查看 + 持久化 + 链路一致性（REQ-X-04 四项验收标准全覆盖）

选择理由：任务书 X-04 为选做加分项，本 Case 验证"运维人员能回答'系统为什么建议这样修复'"，这是审计和信任建设的基础。

### 前置准备

无额外准备——任何一次 `galaxy-diag run` 执行后都会自动产生 trace 记录。可直接复用 Case 1 的执行结果。

### 测试步骤

#### 步骤 1：执行诊断产生 trace

执行 Case 1 的测试命令（或任意一次完整诊断）：

```bash
galaxy-diag run -d \
  "银河平台从 v2.3 升级到 v2.4 后，galaxy-api 组件启动失败，日志报 \
  config version mismatch: found v2.3 schema in /etc/galaxy/api.conf，\
  怀疑升级后旧配置文件未清理干净。这是容器化部署环境。" \
  --auto
```

| 验收项 | 预期 | 对应 X-04 验收标准 |
|---|---|---|
| trace 文件生成 | `~/.galaxy-diag/traces/<session_id>.jsonl` 存在且非空 | 标准③ 持久化存储 |
| JSONL 格式 | 每行为合法 JSON，包含 `trace_open` / `span_open` / `event` / `span_close` / `trace_close` | 标准① 记录完整链路 |

#### 步骤 2：查看推理链路

```bash
galaxy-diag trace <session_id>
```

| 验收项 | 预期 | 对应 X-04 验收标准 |
|---|---|---|
| 命令可用 | 输出 Rich 树形渲染的推理链路（按 Step 分组） | 标准② 命令查看 |
| 关键事件可见 | 链路中包含以下事件类型：ToolCall / RuleMatch / LLMCall / SecurityCheck / HITL | 标准① 调用了哪些工具 + 什么结果 + 什么逻辑 |
| Step 分组 | 链路按 ENV_RECOGNISING → COLLECTING → DIAGNOSING → PLANNING → ... 分组 | 标准① 完整推理链路 |

#### 步骤 3：按步骤过滤

```bash
galaxy-diag trace <session_id> --step DIAGNOSING
```

| 验收项 | 预期 | 对应 X-04 验收标准 |
|---|---|---|
| 过滤生效 | 仅显示 DIAGNOSING 步骤的 span + event | — |
| 关键决策可见 | 显示规则匹配结果、LLM 输入 prompt 摘要、LLM 输出 completion 摘要 | 标准① 基于什么逻辑 |

#### 步骤 4：详细模式

```bash
galaxy-diag trace <session_id> --verbose
```

| 验收项 | 预期 | 对应 X-04 验收标准 |
|---|---|---|
| 完整输出 | 显示完整的 LLM completion 文本和 ToolCall output_summary | — |
| 截断标记 | 非 `--verbose` 模式下长文本被截断并标记 `[truncated]` | — |

#### 步骤 5：链路一致性验证

将 trace 中的 LLMCall completion 与实际 `DiagnosisResult` 对比：

```bash
# 先查看 trace 中的 LLM 输出
galaxy-diag trace <session_id> --step DIAGNOSING --verbose
# 再查看审计日志中的诊断结果
galaxy-diag audit-log --session <session_id>
```

| 验收项 | 预期 | 对应 X-04 验收标准 |
|---|---|---|
| 结论与依据一致 | trace 中 LLMCall completion 的 root_cause 与审计日志记录的诊断结论一致 | 标准④ 不存在"结论与依据矛盾" |
| RAG 检索可追溯 | 若 RAG 命中，trace 中有 RAGRetrieval event，含 query / top-k / scores | 标准① 得到了什么结果 |
| 人工审核可追溯 | trace 中有 HITL event，记录用户决策（approved/rejected/modified） | 标准① |

#### 步骤 6：服务重启后可查询

```bash
# 重启 Ollama 服务（模拟重启场景）
sudo systemctl restart ollama
# 再次查看 trace（不依赖 Ollama，纯本地 JSONL 读取）
galaxy-diag trace <session_id>
```

| 验收项 | 预期 | 对应 X-04 验收标准 |
|---|---|---|
| 重启后可查 | trace 命令正常输出，不依赖任何运行时服务 | 标准③ 服务重启后可查询 |

### 任务书验收标准对照

| X-04 验收标准 | 本 Case 覆盖步骤 |
|---|---|
| ① 每次诊断记录完整推理链路 | 步骤 1（JSONL 生成）+ 步骤 2（事件类型覆盖） |
| ② 用户可通过命令查看推理过程 | 步骤 2（`galaxy-diag trace`）+ 步骤 3/4（过滤/详细模式） |
| ③ 推理链路持久化存储，服务重启后可查询 | 步骤 1（JSONL 持久化）+ 步骤 6（重启后可查） |
| ④ 推理链路内容与诊断结论一致 | 步骤 5（一致性比对） |

---

## 验收前置条件

### 通用前置条件

1. **Ollama 服务运行**：`ollama serve` 已启动，`config.yaml` 中 `llm.model` 指定的模型已加载（默认 `qwen3:1.7b`；生产环境建议 `qwen3:8b`）
2. **硬件预检通过**：CPU/内存/磁盘/GPU 满足 `config.yaml` 中 `llm.model` 对应的自动推导最低要求（`config/model_profile.py`）；可通过 `galaxy-diag --skip-precheck` 跳过预检
3. **目标环境为 Linux**：采集工具依赖 `/proc`、`systemctl`、`journalctl` 等 Linux 专有接口
4. **无公网依赖**：断网环境下所有核心功能仍可执行
5. **审计日志目录可写**：`~/.galaxy-diag/` 目录存在且可写

### A1（RAG 知识库）额外前置条件

6. **embedding 模型已部署**：`config.yaml` 中 `llm.embed_model` 非空（如 `"bge:large"`），且该模型已通过 `ollama create` 导入 Ollama；`kb import` / `kb reindex` 会自动触发 embedding 预检
7. **知识库目录可写**：`~/.galaxy-diag/knowledge_base/` 目录存在且可写（可由 `GALAXY_KB_DIR` 环境变量覆盖）
8. **准备测试案例文件**：至少一份 Markdown 格式（含 frontmatter `env_type` / `tags`）的客户故障案例，内容与待诊断问题语义相关

### A2（Trace 可观测）额外前置条件

9. **无需额外准备**：任何一次 `galaxy-diag run` 执行后自动产生 `~/.galaxy-diag/traces/<session_id>.jsonl`；`galaxy-diag trace` 命令纯本地读取，不依赖任何运行时服务
