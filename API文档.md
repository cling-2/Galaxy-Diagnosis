# Galaxy-Diag 接口文档

> 银河平台部署问题定位工具 — 系统边界契约定义

## 目录

- [概述](#概述)
- [CLI 接口规范](#cli-接口规范)
  - [全局选项](#全局选项)
  - [run](#run)
  - [env](#env)
  - [diagnose](#diagnose)
  - [snapshot](#snapshot)
  - [audit-log](#audit-log)
  - [trace](#trace)
  - [kb](#kb)
  - [completion](#completion)
- [LLM 后端兼容契约](#llm-后端兼容契约)
- [持久化文件契约](#持久化文件契约)
- [扩展点契约](#扩展点契约)

---

## 概述

galaxy-diag 是纯 CLI 工具，**不暴露 HTTP/REST 端点**。系统的对外边界由四类契约定义：

| 契约类型 | 方向 | 消费者 | 本文档章节 |
|----------|------|--------|------------|
| CLI 接口 | 外→内（运维向工具发指令） | 运维人员（SSH 终端） | [CLI 接口规范](#cli-接口规范) |
| LLM 后端 | 内→外（工具调用推理服务） | Ollama / vLLM / llama-server | [LLM 后端兼容契约](#llm-后端兼容契约) |
| 持久化文件 | 内→外（工具产出供外部读取） | 运维脚本 / 审计系统 / SIEM | [持久化文件契约](#持久化文件契约) |
| 扩展点 | 外→内（运维/研发扩展工具能力） | SRE 团队 / 二次开发 | [扩展点契约](#扩展点契约) |

---

## CLI 接口规范

入口函数 `galaxy_diag.workflow.cli.app:main`，通过 `argparse` 解析参数，子命令动态注册。

### 全局选项

适用于所有子命令，写在子命令之前：

```
galaxy-diag [--config PATH] [--verbose] [--no-color] [--skip-precheck] [--version] <子命令> [子命令选项]
```

| 选项 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--config` | str | `config.yaml` | 配置文件路径 |
| `--verbose` | flag | false | 详细输出（log_level=DEBUG，出错显示完整堆栈） |
| `--no-color` | flag | false | 禁用颜色输出（等同 `NO_COLOR=1`） |
| `--skip-precheck` | flag | false | 跳过硬件资源预检（调试/CI 用，不推荐生产环境） |
| `--version` | flag | — | 输出版本号（`0.1.0`） |

### run

> REQ-F-02 | 端到端 7 步闭环工作流

```
galaxy-diag run [-d TEXT] [--resume [ID]] [--auto] [--log-file PATH] [--clean] [--mock]
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `-d`, `--description` | str | 否 | — | 问题描述；省略时交互式提示输入 |
| `--resume` | str? | 否 | — | 恢复中断的工作流；不指定 ID 时恢复最近的未完成会话 |
| `--auto` | flag | 否 | false | 自动模式（中间步骤只展示不暂停，审核步骤仍需人工确认） |
| `--log-file` | str× | 否 | — | 上传日志文件供诊断参考（可多次指定） |
| `--clean` | flag | 否 | false | 清理所有未完成会话后再启动新工作流 |
| `--mock` | flag | 否 | false | Mock 模式：使用预设响应，不连接真实 LLM（开发测试用） |

**触发预检：** 是（`--mock` 时跳过）

### env

> REQ-B-01 / B-02 | 环境识别 + 硬件采集

```
galaxy-diag env [--type-only] [--output {table,json,yaml}]
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `--type-only` | flag | 否 | false | 仅输出环境类型（裸金属/VM/容器），不采集硬件详情 |
| `--output` | enum | 否 | `table` | 输出格式：`table` / `json` / `yaml` |

**触发预检：** 否（不调用 LLM）

### diagnose

> REQ-C-01 / C-02 / C-03 | 独立诊断分析

```
galaxy-diag diagnose -d TEXT [--log-file PATH] [--no-collect] [--output {table,json}]
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `-d`, `--description` | str | **是** | — | 问题描述 |
| `--log-file` | str× | 否 | — | 用户上传的日志文件路径（可多次指定） |
| `--no-collect` | flag | 否 | false | 跳过信息采集，仅做 LLM 推理（需配合已有采集上下文使用） |
| `--output` | enum | 否 | `table` | 输出格式：`table` / `json` |

**触发预检：** 是

**输出置信度：** `confirmed`（已确认）/ `suspected`（推测）/ `insufficient`（信息不足）

### snapshot

> REQ-E-03 | 快照管理与回滚

```
galaxy-diag snapshot {list | show SNAPSHOT_ID | rollback SNAPSHOT_ID}
```

| 子操作 | 位置参数 | 说明 |
|--------|----------|------|
| `list` | — | 列出所有快照 |
| `show` | `snapshot_id`（必填） | 展示快照详情（时间、操作内容、影响范围） |
| `rollback` | `snapshot_id`（必填） | 一键回滚到快照状态（**危险操作，需输入 CONFIRM 确认**） |

**触发预检：** 否

### audit-log

> REQ-E-04 | 审计日志查询

```
galaxy-diag audit-log [-s ID] [-n N] [--since DATETIME]
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `-s`, `--session` | str | 否 | — | 按会话 ID 过滤 |
| `-n`, `--limit` | int | 否 | `50` | 最大返回记录数 |
| `--since` | str | 否 | — | 仅返回此时间之后的记录（ISO 格式，如 `2026-08-14`） |

**触发预检：** 否

**审计日志特性：** JSONL 持久化，追加写入不经 LLM，不可被 Agent 修改

### trace

> REQ-X-04（选做） | 推理链路查看

```
galaxy-diag trace SESSION_ID [-s STEP] [-v]
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `session_id` | str | **是**（位置） | — | 诊断会话 ID |
| `-s`, `--step` | str | 否 | — | 按步骤过滤（如 `DIAGNOSING`、`COLLECTING`、`REVIEWING`） |
| `-v`, `--verbose` | flag | 否 | false | 显示完整 LLM completion / output_summary 等详细内容 |

**触发预检：** 否

**可用步骤过滤值：** `ENV_RECOGNISING` / `COLLECTING` / `DIAGNOSING` / `PLANNING` / `SECURITY_CHECKING` / `EXECUTION_GUARD` / `REVIEWING` / `SNAPSHOT` / `EXECUTING` / `VERIFYING`

### kb

> REQ-X-02（选做） | 客户知识库管理

```
galaxy-diag kb {import FILE [--mock] | list | delete CASE_ID | reindex [--mock]}
```

| 子操作 | 参数 | 说明 | 触发预检 |
|--------|------|------|----------|
| `import` | `file`（必填，位置），`--mock`（flag） | 导入案例文件（Markdown / 纯文本） | 是（需 embedding 模型） |
| `list` | — | 列出已导入案例 | 否 |
| `delete` | `case_id`（必填，位置） | 删除指定案例 | 否 |
| `reindex` | `--mock`（flag） | 重建全部案例向量索引 | 是（需 embedding 模型） |

### completion

> Shell Tab 补全脚本生成

```
galaxy-diag completion {bash | zsh | fish}
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `shell` | enum | **是** | 目标 Shell：`bash` / `zsh` / `fish` |

**触发预检：** 否

---

## LLM 后端兼容契约

工具通过 OpenAI Chat Completions 兼容接口消费推理服务（**内→外**方向）。工具自身不实现任何 HTTP 端点。

### 要求的端点

| 端点 | 方法 | 用途 | 调用方 |
|------|------|------|--------|
| `/v1/chat/completions` | POST | Chat 推理 | `ModelAdapter.chat()` |
| `/v1/embeddings` | POST | 文本嵌入（RAG） | `ModelAdapter.embed()` |
| `/v1/models` | GET | 模型列表探测 | `check_health()` |

### 请求契约

**Chat Completion 请求（`ModelAdapter.chat()`）：**

```json
{
  "model": "<config.llm.model>",
  "messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
  "max_tokens": "<config.llm.max_tokens>",
  "temperature": 0.3
}
```

- `model` 和 `max_tokens` 从 `config.yaml` 读取，可环境变量覆盖
- 超时上限 `config.llm.timeout`（默认 600s，纯 CPU 推理 8B 模型需 3-5 分钟）
- 失败自动重试 `config.llm.max_retries` 次（默认 3）

**Embedding 请求（`ModelAdapter.embed()`）：**

```json
{
  "model": "<config.llm.embed_model>",
  "input": ["text1", "text2", ...]
}
```

- `embed_model` 为空字符串时禁用 RAG，不调用此端点

### 兼容后端

| 后端 | base_url 示例 | 说明 |
|------|---------------|------|
| Ollama | `http://localhost:11434/v1` | 默认配置，支持 `ollama create` 离线导入 |
| vLLM | `http://localhost:8000/v1` | GPU 加速，吞吐高 |
| llama.cpp (llama-server) | `http://localhost:8080/v1` | 极轻量，纯 CPU |

**切换方式：** 仅需修改 `config.yaml` 中 `llm.base_url` 和 `llm.model` 两个值，业务代码无需任何改动。

```yaml
# 切换到 vLLM
llm:
  base_url: "http://localhost:8000/v1"
  model: "Qwen/Qwen3-8B"
```

### 离线约束（红线 1）

- `base_url` 和 `model` 必须可配置，不得硬编码任何外网地址
- 工具代码中不存在 `api.openai.com`、`huggingface.co` 等外网地址
- 运行阶段所有推理调用走内网 `base_url`，无公网出站依赖

---

## 持久化文件契约

工具运行时产出持久化文件供外部系统读取（**内→外**方向）。所有文件位于 `~/.galaxy-diag/` 下，格式为 JSON / JSONL / NPY / Markdown，无数据库依赖。

### 文件清单

| 路径 | 格式 | 写入方 | 说明 | 外部读取场景 |
|------|------|--------|------|-------------|
| `sessions/<id>.json` | JSON | `workflow.persist` | 工作流会话状态 | 会话恢复、状态查询 |
| `audit.jsonl` | JSONL | `safety.audit` | 审计日志（追加写入，不经 LLM） | SIEM/合规审查、操作追溯 |
| `audit.failed.jsonl` | JSONL | `safety.audit` | 审计日志写入失败的备用文件 | 运维排查 |
| `traces/<id>.jsonl` | JSONL | `trace.recorder` | Agent 推理链路 | 质量审计、问题复现 |
| `traces.failed/` | — | `trace.recorder` | trace 写入失败的备用目录 | 运维排查 |
| `snapshots/snap_<ts>/meta.json` | JSON | `safety.snapshot` | 快照元数据 | 回滚决策依据 |
| `snapshots/snap_<ts>/bak/` | 原始文件 | `safety.snapshot` | 被修改文件的备份 | 灾难恢复 |
| `snapshots/snap_<ts>/service_<svc>.txt` | 纯文本 | `safety.snapshot` | 服务状态记录 | 回滚后验证 |
| `knowledge_base/index.json` | JSON | `knowledge.store` | 知识库索引 | 案例管理 |
| `knowledge_base/vectors.npy` | NumPy | `knowledge.store` | 向量矩阵（float32） | 离线迁移 |
| `knowledge_base/cases/<id>.md` | Markdown | `knowledge.indexer` | 案例原文 | 人工查阅 |

### JSONL 记录格式

**审计日志（`audit.jsonl`）每行格式：**

```json
{
  "timestamp": "2026-08-21T14:30:00",
  "session_id": "abc123",
  "operator": "system",
  "action": "execute_fix",
  "result": "success",
  "llm_basis": "根因: 配置文件残留...",
  "snapshot_id": "snap_20260821_1430",
  "user_input": "y"
}
```

- `result` 取值：`confirmed` / `success` / `failure` / `rollback` / `rejected` / `verify_failed`
- 写入走 `open().write()` 直写文件，不经 Agent/LLM 输出流（E-04 不可篡改保证）

**Trace（`traces/<id>.jsonl`）每行格式：**

```json
{
  "event": "span_open|event|span_close|trace_open|trace_close",
  "trace_id": "...",
  "span_id": "...",
  "step": "DIAGNOSING",
  "timestamp": "2026-08-21T14:30:05",
  "event_type": "LLMCall|RuleMatch|ToolCall|RAGRetrieval|SecurityCheck|HITL",
  ...
}
```

### 可靠性保证

- 审计日志和 trace 均为**追加写入 + flush**，进程崩溃不丢失已写入记录
- 写入失败时自动降级到 `*.failed*` 备用路径，不阻断业务流程
- 所有文件可通过 `GALAXY_KB_DIR` 等环境变量重定向存储位置

---

## 扩展点契约

工具预留了若干扩展入口，运维/研发团队无需修改核心逻辑即可增强工具能力（**外→内**方向）。

### 诊断规则扩展

| 项 | 说明 |
|-----|------|
| 扩展位置 | `src/galaxy_diag/diagnoser/rules.py` → `DIAGNOSIS_RULES: list[DiagnosisRule]` |
| 扩展方式 | 向列表追加新的 `DiagnosisRule` 实例 |
| 生效机制 | `match_rules()` / `prematch_rules_by_description()` 自动遍历列表 |
| 当前内置 | 8 条规则（覆盖配置不匹配、磁盘未识别、驱动未加载、网络异常等） |

```python
# 示例：追加自定义规则
from galaxy_diag.diagnoser.rules import DIAGNOSIS_RULES, DiagnosisRule

DIAGNOSIS_RULES.append(DiagnosisRule(
    name="custom_raid_degrade",
    keywords=["raid", "降级", "degrade"],
    ...
))
```

### 危险命令模式扩展

| 项 | 说明 |
|-----|------|
| 扩展位置 | `src/galaxy_diag/safety/patterns.py` → `DANGER_PATTERNS: list[DangerPattern]` |
| 扩展方式 | 向列表追加新的 `DangerPattern` 实例 |
| 生效机制 | `execution_guard_check()` 自动匹配 |
| 当前内置 | 11 条模式，覆盖 `data_loss` / `privilege` / `network` / `system` 四类 |
| 约束 | 仅追加数据条目，不修改 `danger.py` 中的匹配逻辑 |

### 反幻觉规则扩展

| 项 | 说明 |
|-----|------|
| 扩展位置 | `src/galaxy_diag/diagnoser/hallucination_guard.py` → `_FACT_CHECK_RULES` |
| 扩展方式 | 追加 `(rule_id, check_fn, message)` 元组 |
| 生效机制 | `check_facts()` 自动执行所有规则 |
| 当前内置 | 4 条：`network_ok` / `service_ok` / `mount_ok` / `resource_ok` |
| 约束 | 纯规则判定，零 LLM 依赖，确定性最强 |

### CLI 子命令扩展

| 项 | 说明 |
|-----|------|
| 扩展位置 | `src/galaxy_diag/workflow/cli/` 下新建 `cmd_*.py` 文件 |
| 扩展方式 | 实现 `register(subparsers)` + `handle(args)` 两个函数 |
| 生效机制 | `app.py` 通过 `importlib` 动态加载所有 `cmd_*.py` 模块 |
| 约束 | 需在 `app.py` 的 `_COMMANDS` 列表中注册模块路径 |

### LLM 后端切换

| 项 | 说明 |
|-----|------|
| 扩展方式 | 修改 `config.yaml` 中 `llm.base_url` 和 `llm.model` |
| 生效机制 | `ModelAdapter` 基于 OpenAI SDK，任何兼容后端即插即用 |
| 约束 | 后端必须实现 `/v1/chat/completions`（必选）和 `/v1/embeddings`（RAG 时需） |

### Embedding 模型切换

| 项 | 说明 |
|-----|------|
| 扩展方式 | 修改 `config.yaml` 中 `llm.embed_model`，然后运行 `galaxy-diag kb reindex` |
| 维度冲突检测 | `KnowledgeStore.is_dimension_consistent()` 自动检测新旧模型向量维度不一致 |
| 约束 | 空字符串 = 禁用 RAG |

---

> 本文档对应 galaxy-diag v0.1.0。运维操作指南见[使用文档](usage.md)。
