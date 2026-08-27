# 银河诊断工具：工作流智能跳过与反幻觉能力设计

> 日期: 2026-08-18
> 范围: B类(已知故障跳过采集) + C类(按需精简硬件采集) + 反幻觉(事实校验拦截)

## 1. B类：已知故障模式跳过 COLLECTING

### 1.1 行为变更

**当前:** ENV_RECOGNISING → COLLECTING(必执行) → [规则短路] → DIAGNOSING/PLANNING
**目标:** ENV_RECOGNISING → [规则预匹配] → COLLECTING(跳过) → PLANNING

- ENV_RECOGNISING 完成后，对 `problem_description` 做基于关键词的规则预匹配
- 命中 CONFIRMED 规则 且 环境过滤通过 → 跳过 COLLECTING + DIAGNOSING，直接 PLANNING
- 用户看到: `"已知故障模式 [rule_id]，跳过信息采集，直接生成修复建议"`

### 1.2 规则预匹配逻辑

复用现有 `DiagnosisRule.match_conditions` 和 `rule.env_types`：
- 对 `problem_description` 做 AND 关键词匹配（全命中才算匹配）
- 环境过滤用 ENV_RECOGNISING 已拿到的 `env_type`
- 仅 CONFIRMED 规则触发跳过；SUSPECTED 不跳过（保守）

### 1.3 涉及文件

- `workflow/engine.py` — `_do_env_recognising()` 末尾加规则预匹配；`_do_collecting()` 开头检查跳过标记
- `workflow/states.py` — `TRANSITIONS` 加 `ENV_RECOGNISING → PLANNING`；`SKIP_TARGETS` 更新
- `shared/types.py` — `WorkflowState` 加 `should_skip_collecting: bool = False`

## 2. C类：按问题类型跳过 B-02 完整硬件采集

### 2.1 行为变更

**当前:** `collect_env()` 总是执行 HardwareCollector + StorageCollector
**目标:** 根据问题描述判断是否需要硬件/存储信息，不需要则跳过

### 2.2 判断逻辑

新增 `should_collect_hardware(problem_description) -> bool`：

- 关键词命中"需要硬件"→ 返回 True
- 关键词命中"不需要硬件"→ 返回 False
- 默认 True（保守：不确定就采）

**需要硬件的关键词:** 磁盘, 盘, disk, I/O, smart, raid, 固件, firmware, 存储, storage, mount, 挂载, 利旧, 控制器, io error, 数据盘, lsblk, fsck

**不需要硬件的关键词:** 网络, network, ping, cni, iptables, 路由, dns, 服务, 启动, service, 容器, pod, k8s, OOM, 内存不足, 内存溢出

优先级: "需要硬件"关键词优先于"不需要硬件"关键词（同命中时采）。

### 2.3 涉及文件

- `collector/__init__.py` — `collect_env()` 接受 `skip_hardware: bool = False` 参数
- `diagnoser/context.py` — 新增 `should_collect_hardware()`
- `workflow/engine.py` — `_do_env_recognising()` 传参调用

## 3. 反幻觉：采集后规则映射校验

### 3.1 行为变更

**当前:** COLLECTING → [规则短路] → DIAGNOSING/PLANNING
**目标:** COLLECTING → [事实校验] → [矛盾则终止] / [规则短路] → DIAGNOSING/PLANNING

- COLLECTING 完成后、规则短路之前，执行事实校验
- 校验结果为"矛盾" → 打印矛盾消息 + 终止工作流 + 写审计日志
- 校验结果为"不矛盾"或"无匹配规则" → 正常继续

### 3.2 事实校验规则

```python
@dataclass
class FactCheckRule:
    rule_id: str
    problem_keywords: list[str]        # OR 逻辑：任一命中即激活
    check_fn: Callable                 # (DiagnosticContext) -> bool  True=问题不存在(矛盾)
    message: str                       # 矛盾时的输出
```

预置规则:

| rule_id | problem_keywords | 检查逻辑 | message |
|---|---|---|---|
| `network_ok` | 网络, 不通, ping, network, 连通 | network_checks 全部 reachable | "您的部署环境中网络连通性正常，不存在您描述的'网络不通'问题" |
| `service_ok` | 服务启动失败, service, fail, 启动失败 | component_status 无 failed 状态 | "您的部署环境中服务运行正常，不存在启动失败问题" |
| `mount_ok` | 挂载失败, mount error, 挂载 | log_snippets 无 mount error / stale file handle | "您的部署环境中存储挂载状态正常" |
| `resource_ok` | OOM, 内存不足, 内存溢出, Out of memory | system_resources 无 OOM 且内存使用率<90% | "您的部署环境中内存资源充足，不存在 OOM 问题" |

### 3.3 涉及文件

- 新增 `diagnoser/hallucination_guard.py`
- `workflow/engine.py` — `_do_collecting()` 末尾调用校验
- `shared/types.py` — `WorkflowState` 加 `hallucination_check_result: str | None = None`

## 4. 工作流状态转换变更汇总

```
现有:  ENV_RECOGNISING → COLLECTING → [短路]DIAGNOSING → PLANNING → ...

新增:  ENV_RECOGNISING ─┬→ COLLECTING → [反幻觉] → [短路]DIAGNOSING → PLANNING → ...
                       └→ PLANNING (B类跳过)
```

TRANSITIONS 变更:
- `ENV_RECOGNISING` 新增 `→ PLANNING`（B类跳过）
- `SKIP_TARGETS` 新增 `ENV_RECOGNISING: [PLANNING]`

## 5. 审计与日志

- B类跳过: history 记录 `{"step": "collecting_skipped", "rule_id": "..."}`
- C类跳过: history 记录 `{"step": "hardware_skipped"}`
- 反幻觉拦截: audit 写入 `action="hallucination_guard", result="contradiction_detected"`
