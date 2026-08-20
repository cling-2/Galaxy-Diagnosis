# Agent Trace 可观测性设计 (REQ-X-04)

> 对应需求：REQ-X-04（Agent 推理过程可观测，选做）
> 前置依赖：REQ-F-02（诊断-修复端到端工作流，已实现）
> 实现位置：`src/galaxy_diag/trace/`（新增包，含 `recorder.py` + `viewer.py` + `__init__.py`）
> CLI 集成：`galaxy-diag trace <session_id>` 子命令，注册于 `workflow/cli/app.py`
> 存储位置：`~/.galaxy-diag/traces/<session_id>.jsonl`

## 模块概述

Agent Trace 为诊断任务提供**推理链路可观测性**——记录并持久化一次完整诊断的推理过程，使运维人员能够回答"系统为什么建议这样修复"。

Trace 不是日志系统，不是 Workflow 状态持久化的替代品，不是安全审计的替代品。它回答的核心问题：**推理依据 → 推理过程 → 推理结论**的完整链路。

### 职责边界

| 范畴 | 说明 |
|------|------|
| Trace 负责 | 推理链路记录（哪些 Tool 被调用、返回了什么摘要、RAG 检索了什么、LLM 推理了什么、规则匹配了什么、人工在何处介入、最终如何得出结论）、链路持久化、链路查询与展示 |
| Trace 不负责 | Workflow 状态持久化与恢复（`workflow/persist.py`）、安全审计凭证（`safety/audit.py`）、运行时输出渲染（`workflow/cli/display.py`） |
| 与 WorkflowState 的关系 | 两者平行独立。WorkflowState 是状态权威（"我在哪、怎么恢复"），Trace 是可观测性投影（"这一步内部发生了什么推理"）。冲突时以 WorkflowState 为准 |
| 与 AuditLog 的关系 | 两者平行独立。AuditLog 是安全凭证（"谁批准了什么"），Trace 是推理链路（"人工介入如何影响流程走向"）。REVIEWING 等安全关键 HITL 两处各记，视角不同、字段不同 |

### 核心设计原则

1. Trace 是可观测性能力，不是 Workflow 状态机的替代品
2. Trace 记录"Agent 实际消费的信息"，而非原始数据全量
3. 不设独立 DecisionEvent——决策语义由各 Event 的结果字段表达
4. 按安全等级分流 HITL 记录：安全关键确认同时进 Trace + AuditLog，低风险确认只进 Trace
5. 本机查看，不实现脱敏
6. 最小可行设计，不过度工程化

## 架构决策

### 决策 1：Trace 的定位——独立一等公民

Trace 与 WorkflowState、AuditLog 平行存在，各自有独立的存储、生命周期和查询入口。

**理由：**
- 三者核心关注点不同：WorkflowState 关注"状态恢复"，AuditLog 关注"安全审计"，Trace 关注"推理可观测"
- 存储模式不同：WorkflowState 整体读写（恢复需完整 state），AuditLog 追加写入安全日志，Trace 追加写入推理链路
- 项目已有 `sessions/`、`audit.jsonl`、`knowledge_base/` 各自独立的先例，`traces/` 子目录符合惯例
- REQ-X-04 验收标准"命令查看指定诊断任务的推理过程"暗示 Trace 是独立可查询概念

**冲突仲裁：** 当状态信息不一致时，WorkflowState 为状态权威，Trace 为可观测性投影（只记录，不裁决）。

### 决策 2：层级结构——Trace → Step Span → Event

```
Trace (session 级)
  ├─ StepSpan (ENV_RECOGNISING, seq=1)
  │    ├─ Event: RuleMatch
  │    └─ Event: ...
  ├─ StepSpan (COLLECTING, seq=1)
  │    ├─ Event: ToolCall (collect_component_status)
  │    ├─ Event: ToolCall (collect_system_resources)
  │    └─ ...
  ├─ StepSpan (DIAGNOSING, seq=1)
  │    ├─ Event: RuleMatch
  │    ├─ Event: RAGRetrieval
  │    ├─ Event: LLMCall
  │    └─ ...
  ├─ StepSpan (REVIEWING, seq=1)
  │    ├─ Event: SecurityCheck
  │    ├─ Event: HITL
  │    └─ ...
  └─ ...
```

**理由：**
- 用户想回答的 8 个问题绝大多数以 Step 为自然查询边界
- Step Span 提供耗时统计的天然锚点（验收：哪一步耗时过长）
- Step Span 的 status 可表达结构化语义（completed / failed / skipped / rolled_back / interrupted）
- Step 概念在 Trace 和 WorkflowState 中是不同视角的合理重复：WorkflowState 的 Step 是"状态机位置"，Trace 的 Step Span 是"这一步内部发生了什么推理"

**同一 Step 多次执行（rollback / retry）产生多个 Span 实例，用 `sequence_index` 区分。** Skipped Step 产生轻量 Span（status=skipped + skip_reason）。

### 决策 3：Event 记录粒度

#### ToolCall——记录 Agent 消费的摘要

```
ToolCallEvent:
  tool_name: str
  input_params: dict          # Tool 调用参数
  output_summary: str         # build_raw_summary() 输出（Agent 实际消费的内容）
  output_size_bytes: int      # 原始大小（仅元信息）
  output_status: str          # success / partial_failure / empty
```

不记录原始 Tool Output。`output_summary` = LLM 实际消费的内容（经 `build_raw_summary()` 处理），精确回答"Agent 基于什么得出结论"。原始 Output 需要时回机器查看。

#### LLMCall——completion 完整 + prompt 摘要

```
LLMCallEvent:
  model: str
  prompt_summary: list[{      # 不记完整 prompt
    role: str
    content_length: int
    contains: list[str]       # 如 ["tool_summary", "rag_context", "user_input"]
    template_hash: str|null
  }]
  completion: str             # LLM 原始输出，≤8KB（超出截断 + truncated=true）
  truncated: bool
  parsed_result: dict         # 后处理结果
  parse_ok: bool              # JSON 解析是否成功
  usage: {prompt_tokens, completion_tokens}
```

completion 是推理链路核心（含 root_cause 推理、fix 建议、confidence 判断）。prompt 的完整内容可从 Trace 中其他 Event 重建（ToolCall.summary + RAG Event + 模板版本），不需要重复存储。JSON 解析失败重试场景：两次 LLMCall 都记录，第一次 `parse_ok: false`。

#### RuleMatch——独立 Event

```
RuleMatchEvent:
  rules_count: int
  matched_rule_id: str|null
  matched_keywords: list[str]
  result: str                 # CONFIRMED / SUSPECTED / NONE
  rule_hint: str|null         # SUSPECTED 时注入 LLM 的提示
  diagnosis_source: str       # RULE_MATCH / LLM（最终结论来源标记）
```

诊断三层管道（RuleMatch → RAGRetrieval → LLMCall）各对应独立 Event。CONFIRMED 短路时 DIAGNOSING Step 只有 RuleMatchEvent，干净表达"规则秒杀"。可在多个 Step 复用（ENV_RECOGNISING prematch / COLLECTING / DIAGNOSING）。

#### RAGRetrieval——query + matches 摘要

```
RAGRetrievalEvent:
  query_text: str
  matches: list[{
    case_id: str
    similarity: float
    summary: str
    env_type: str|null
  }]
  top_k: int
  min_similarity: float
  best_similarity: float
```

不记录完整 case content。summary 是 RAG 结果的摘要表达，与 `build_raw_summary()` 之于 Tool Output 的角色对等。需要完整内容时凭 case_id 回知识库查。RAG 未启用时不产生该 Event；RAG 检索失败时记录 `status=error`。

#### HITL——按安全等级分流

```
HITLEvent:
  type: str                   # continue_confirm / review_confirm / review_reject / param_edit
  decision: str               # confirmed / rejected / edited
  guard_level: str|null       # pass / warning / critical（仅 review 有）
  edited_fields: list[str]|null   # 仅 param_edit 有
  impact: str                 # 如 "流程进入 EXECUTING" / "流程终止"
```

| HITL 类型 | 进 Trace | 进 AuditLog | 理由 |
|-----------|---------|------------|------|
| REVIEWING 确认/拒绝 | ✓ | ✓ | 安全关键操作，两处各记不同视角 |
| step-by-step continue confirm | ✓ | ✗ | 非安全关键，仅推理链路信息 |
| SUSPECTED 后继续 | ✓ | ✗ | 非安全关键，仅推理链路信息 |
| param edit | ✓ | ✗ | 流程跳转，非写操作确认 |

Trace 的 HITLEvent 不记录 `user_input` 原文、`operator` 身份（这些是 AuditLog 的安全凭证职责）。

#### SecurityCheck——独立 Event

```
SecurityCheckEvent:
  check_type: str             # d03_multi_check / execution_guard
  guard_level: str            # pass / warning / critical
  matched_patterns: list[str]
  impact_summary: str|null
  message: str|null
```

#### Decision——不设独立 Event

决策语义由各 Event 的结果字段表达：RuleMatch.result、LLMCall.parsed_result、HITL.decision、SecurityCheck.guard_level、ToolCall.output_status。"为什么"由链路自然展示：ToolCall.output_summary → LLMCall.completion → parsed_result → 最终结论。因果关系靠 Step Span 顺序表达。展示层负责高亮提取。

### 决策 4：存储格式——每 session 一个 JSONL 文件，追加写入

```
~/.galaxy-diag/traces/<session_id>.jsonl
```

每条记录一行 JSON，用 `record_type` 区分层级：

```jsonl
{"record_type": "trace_open", "session_id": "abc", "start_time": "...", "problem_description": "..."}
{"record_type": "span_open", "span_id": "ENV_RECOGNISING_1", "step": "ENV_RECOGNISING", "sequence_index": 1}
{"record_type": "event", "span_id": "ENV_RECOGNISING_1", "event_id": "ENV_RECOGNISING_1_1", "event_type": "RuleMatch", "timestamp": "...", ...}
{"record_type": "span_close", "span_id": "ENV_RECOGNISING_1", "end_time": "...", "status": "completed", "event_count": 1}
...
{"record_type": "trace_close", "end_time": "...", "final_status": "done", "span_count": 10}
```

**关键设计点：**
- **不记录 `trace_id`**：文件名已编码 session_id，每行不再重复
- **span_id = `{step}_{sequence_index}`**：如 `DIAGNOSING_2`，简单可读，无需 UUID
- **event_id = `{span_id}_{序号}`**：如 `DIAGNOSING_1_2`
- **崩溃安全**：已写入行全有效，崩溃只丢最后一行；viewer 逐行 `json.loads()`，损坏行跳过 + warning
- **`--resume` 恢复**：追加到同一文件，不重写；新增 Span/Event 行自然追加
- **Skipped Span**：只有 `span_open`（含 `status=skipped` + `skip_reason`），不产生 `span_close`

**理由：** 与 AuditLog 的 JSONL 模式一致（团队已熟悉）；追加写入崩溃安全；多 Span/Event 增量写入自然；单 session ~50~80 行 JSONL，逐行扫描重建树 < 1ms。

### 决策 5：接入方式——单一入口拦截 + 特殊点显式补充

| Event 类型 | 触发方式 | 拦截/记录位置 |
|-----------|---------|-------------|
| ToolCall | **入口拦截** | `diagnoser/context.py:_safe_collect()` |
| LLMCall | **入口拦截** | `model/client.py:ModelAdapter.chat()` |
| RuleMatch | 显式补充 | `diagnoser/agent.py` / `workflow/engine.py`（prematch） |
| RAGRetrieval | 显式补充 | `diagnoser/agent.py` |
| HITL | 显式补充 | `workflow/engine.py:_do_reviewing()` 等 |
| SecurityCheck | 显式补充 | `safety/danger.py` / `fixer/checker.py` |

**Span 声明：** 在 `engine.py` 各 `_do_xxx()` 方法中用上下文管理器 `with recorder.span(step, seq)` 包裹。open/close/duration 自动测量，异常退出标记 `status=interrupted`。

**recorder 传递：** 通过 `contextvars` 隐式传递。engine 启动 Trace 时设置 `recorder_token = _trace_context.set(recorder)`，结束时 reset。无 recorder 时（测试 / Trace 未启用）所有记录 no-op，零开销。

**Event 归属：** recorder 内部维护"当前 Span 栈"。`with recorder.span(...)` 进入时 push span_id，退出时 pop。`record_event` 自动将 Event 挂到栈顶 span_id，调用方无需传递 span_id。

**约束：** 显式记录的特殊 Event 必须在 `with recorder.span(...)` 块内调用，确保 Event 归属正确。

### 决策 6：非正常路径表达

| 场景 | Trace 表达 |
|------|-----------|
| Skip（如 should_skip_collecting） | 轻量 Span：status=skipped + skip_reason |
| Rollback（VERIFYING 失败 → 回 DIAGNOSING） | 新 Span：DIAGNOSING_2，完整 Event 列表，可与 DIAGNOSING_1 对比 |
| Rejection（REVIEWING 人工拒绝） | REVIEWING Span 含 HITLEvent(decision=rejected, impact="流程终止") |
| Retry（PLANNING↔SECURITY_CHECKING 循环） | 多 Span：PLANNING_1 + SECURITY_CHECKING_1 + PLANNING_2 + ... |
| Param edit（REVIEWING → SECURITY_CHECKING） | HITLEvent(type=param_edit) + 新 SECURITY_CHECKING Span |
| Ctrl+C / 崩溃 | 当前 Span 标记 status=interrupted，已写入行保留 |

### 决策 7：存储与展示

| 项 | 结论 | 依据 |
|----|------|------|
| 存储路径 | `~/.galaxy-diag/traces/<session_id>.jsonl` | 项目惯例 |
| 保存期限 | 不自动清理，跟随 session 生命周期 | 项目无 TTL 机制 |
| 展示方式 | CLI 命令 `galaxy-diag trace <session_id>` | 验收标准要求命令查看；项目是 CLI 工具 |
| Web UI | 最小实现不做 | 无 REQ 要求 |
| 开发/生产数据 | 同一套 | 无环境区分 |
| 数据量控制 | 由摘要策略控制 | ToolCall 摘要 + LLM completion 截断 8KB + RAG summary |

### 决策 8：脱敏——不实现

Trace 仅限运维人员本机查看，与 session JSON 同级安全属性（session JSON 同含完整 DiagnosticContext，未脱敏）。脱敏会损害可观测性价值（运维人员看到 `[REDACTED]` 无法判断哪台机器出问题）。

### 决策 9：数据模型完整定义

#### 通用 Event 字段

```
span_id: str                 # 所属 Span
event_id: str                # {span_id}_{序号}
event_type: str              # ToolCall / LLMCall / RuleMatch / RAGRetrieval / HITL / SecurityCheck
timestamp: ISO8601
duration_ms: int
status: str                  # success / error / partial
```

#### trace_open

```json
{
  "record_type": "trace_open",
  "session_id": "string",
  "start_time": "ISO8601",
  "problem_description": "string"
}
```

#### trace_close

```json
{
  "record_type": "trace_close",
  "end_time": "ISO8601",
  "final_status": "done | rejected | rolled_back | interrupted",
  "span_count": 0
}
```

#### span_open

```json
{
  "record_type": "span_open",
  "span_id": "string",
  "step": "WorkflowStep 枚举名",
  "sequence_index": 0,
  "status": "completed | skipped | ...",
  "skip_reason": "string | null"
}
```

Skipped Span：只有 span_open（含 status=skipped + skip_reason），不产生 span_close。

#### span_close

```json
{
  "record_type": "span_close",
  "span_id": "string",
  "end_time": "ISO8601",
  "status": "completed | failed | interrupted",
  "event_count": 0
}
```

#### ToolCallEvent

```json
{
  "event_type": "ToolCall",
  "tool_name": "string",
  "input_params": {},
  "output_summary": "string",
  "output_size_bytes": 0,
  "output_status": "success | partial_failure | empty"
}
```

#### LLMCallEvent

```json
{
  "event_type": "LLMCall",
  "model": "string",
  "prompt_summary": [
    {
      "role": "string",
      "content_length": 0,
      "contains": ["string"],
      "template_hash": "string | null"
    }
  ],
  "completion": "string",
  "truncated": false,
  "parsed_result": {},
  "parse_ok": true,
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0
  }
}
```

#### RuleMatchEvent

```json
{
  "event_type": "RuleMatch",
  "rules_count": 0,
  "matched_rule_id": "string | null",
  "matched_keywords": ["string"],
  "result": "CONFIRMED | SUSPECTED | NONE",
  "rule_hint": "string | null",
  "diagnosis_source": "RULE_MATCH | LLM"
}
```

#### RAGRetrievalEvent

```json
{
  "event_type": "RAGRetrieval",
  "query_text": "string",
  "matches": [
    {
      "case_id": "string",
      "similarity": 0.0,
      "summary": "string",
      "env_type": "string | null"
    }
  ],
  "top_k": 0,
  "min_similarity": 0.0,
  "best_similarity": 0.0
}
```

#### HITLEvent

```json
{
  "event_type": "HITL",
  "type": "continue_confirm | review_confirm | review_reject | param_edit",
  "decision": "confirmed | rejected | edited",
  "guard_level": "pass | warning | critical | null",
  "edited_fields": ["string | null"],
  "impact": "string"
}
```

#### SecurityCheckEvent

```json
{
  "event_type": "SecurityCheck",
  "check_type": "d03_multi_check | execution_guard",
  "guard_level": "pass | warning | critical",
  "matched_patterns": ["string"],
  "impact_summary": "string | null",
  "message": "string | null"
}
```

## 实现指引

### 包结构

```
src/galaxy_diag/trace/
├── __init__.py
├── recorder.py       # TraceRecorder 类（span 上下文管理器、record_event、JSONL 写入）
└── viewer.py         # trace 查询与展示（加载 JSONL → 重建树 → Rich 渲染）
```

### 核心类

**`TraceRecorder`**（recorder.py）：
- `start_trace(session_id, problem_description)` → 写 trace_open，设置 contextvar
- `close_trace(final_status)` → 写 trace_close，清理 contextvar
- `span(step, sequence_index)` → 上下文管理器，写 span_open / span_close，管理 Span 栈
- `record_event(event_type, **kwargs)` → 写 event，自动归属栈顶 span_id
- 内部：`_write_line(record_dict)` → `json.dumps()` + 文件追加 + flush
- 无 recorder 时（contextvar 为 None）：所有方法 no-op

**`TraceViewer`**（viewer.py）：
- `load_trace(session_id)` → 逐行读取 JSONL → 重建 Trace→Span→Event 树
- `render(trace)` → Rich 树形渲染（按 Step 分组、关键决策高亮）
- 容错：损坏行跳过 + warning；未关闭 Span 标记 interrupted

### 接入点

| 位置 | 接入内容 |
|------|---------|
| `workflow/engine.py:run()` | `recorder.start_trace()` / `recorder.close_trace()` |
| `workflow/engine.py:_do_xxx()` | `with recorder.span(step, seq)` |
| `model/client.py:ModelAdapter.chat()` | 入口拦截 LLMCall |
| `diagnoser/context.py:_safe_collect()` | 入口拦截 ToolCall |
| `diagnoser/agent.py:diagnose()` | 显式 RuleMatch / RAGRetrieval |
| `workflow/engine.py:_do_reviewing()` | 显式 HITL |
| `safety/danger.py` / `fixer/checker.py` | 显式 SecurityCheck |

### CLI 命令

```bash
galaxy-diag trace <session_id>           # 查看完整推理链路
galaxy-diag trace <session_id> --step DIAGNOSING  # 按步骤过滤
galaxy-diag trace <session_id> --verbose  # 显示完整 completion / output_summary
```

注册于 `workflow/cli/app.py:_COMMANDS`，参照 `cmd_audit_log.py` 模式。

## 验收标准对照

| REQ-X-04 验收标准 | Trace 设计如何满足 |
|-------------------|-------------------|
| 1. 每次诊断记录完整的推理链路：调用了哪些工具、得到了什么结果、基于什么逻辑得出结论 | ToolCall(output_summary) → RuleMatch → RAGRetrieval → LLMCall(completion + parsed_result) 完整记录链路 |
| 2. 用户可通过命令查看指定诊断任务的推理过程 | `galaxy-diag trace <session_id>` CLI 命令 |
| 3. 推理链路持久化存储，服务重启后可查询 | `~/.galaxy-diag/traces/<session_id>.jsonl` 持久化文件 |
| 4. 推理链路内容与诊断结论一致，不存在"结论与依据矛盾" | Trace 记录的是 Agent 实际消费的信息（output_summary = LLM 输入，completion = LLM 输出），链路可直接比对验证一致性 |

## 讨论记录

本设计通过以下架构决策逐项确定（每项经双方讨论确认，未含未经讨论的内容）：

1. Trace 职责定位——独立一等公民，平行于 WorkflowState / AuditLog
2. 层级结构——Trace → Step Span → Event，Span 映射 WorkflowStep
3. ToolCall 记录粒度——Agent 消费摘要（build_raw_summary() 输出）
4. LLMCall 记录粒度——completion 完整 + prompt 摘要
5. Decision 独立性——不设 DecisionEvent，决策语义由 Event result 字段表达
6. HITL 记录策略——按安全等级分流：关键→Trace+AuditLog，低风险→仅 Trace
7. RAGRetrieval 粒度——query_text + matches 摘要，不记完整 content
8. RuleMatch 独立性——独立 Event，三层管道各一级
9. 非正常路径表达——多 Span + sequence_index；skipped 轻量 Span
10. 存储格式——JSONL 追加写入，文件名编码 session_id，不记录 trace_id
11. 接入方式——单一入口拦截 + 特殊点显式，contextvar + Span 栈
12. 脱敏——不实现（Trace 仅本机查看）
13. 数据模型——字段去冗余（去掉 trace_id、去掉 RAGRetrieval.match_count），其余定型
