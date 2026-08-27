# 银河部署问题定位工具 — 验收答辩准备

> 对照评分细则（否决项 / 考核维度 / Case / 加分项）逐模块回顾项目实现，并预判老师可能提问的点。
> 文件引用用 `路径:行号`，答辩时可现场打开佐证。

---

## 一、整体架构（先建立全局认知）

工具是一条 **7 步用户可见 / 10 步内部状态机**的闭环流水线：

```
自然语言输入 → 环境识别 → 信息采集 → 根因分析 → 修复建议 → 人工审核 → 执行 → 验证
              ENV_RECOGNISING → COLLECTING → DIAGNOSING → PLANNING → SECURITY_CHECKING → EXECUTION_GUARD → REVIEWING → SNAPSHOT → EXECUTING → VERIFYING
```

**核心设计原则（README:163-169，答辩时一定要主动讲）**：
1. 先跑通再加深
2. 错误处理不能吞（采集失败/模型失败都明确提示）
3. 不硬编码（占位符、外网地址不写死、模型路径不写死）
4. **关键路径不经 LLM**（人工审核、审计写入、危险拦截由硬编码逻辑完成，LLM 只"建议"）
5. 增量构建

**模块划分**（`src/galaxy_diag/`）：

| 模块 | 职责 | 对应 REQ |
|---|---|---|
| `config/` | 配置加载、硬件需求自动推导 | A |
| `model/` | 离线推理客户端、健康检查、预检 | A-01 |
| `collector/` | 环境识别、硬件/存储采集 | B |
| `diagnoser/` | 诊断上下文、规则匹配、LLM 推理、反幻觉 | C |
| `fixer/` | 修复命令/脚本生成、模板、多维检测 | D |
| `safety/` | 危险拦截、人工审核、快照回滚、审计 | E |
| `workflow/` | 状态机引擎、CLI、持久化 | F |
| `knowledge/` | RAG 知识库（加分 A1） | X-02 |
| `trace/` | 推理链路可观测（加分 A2） | X-04 |

---

## 二、否决项（P0）—— 答辩第一道关

评分细则：触发任一否决项直接降三等。这四条是答辩中最容易被追问的。

### V-01 离线可用、零公网依赖

**怎么做的**：
- 推理默认连本地 Ollama：`config/defaults.py:9` `base_url = "http://localhost:11434/v1"`，**不是** `api.openai.com`
- 离线部署包 `deploy/offline/` 已打包 Ollama 二进制 + GGUF 模型 + Python wheels，两机模式（联网准备机下载 → U盘 → 断网客户机安装），见 `deploy/prepare_offline.sh` / `install_offline.sh`
- 兼容任何 OpenAI 兼容后端（Ollama/vLLM/llama.cpp），`model/client.py:1-5`
- **全代码库 grep 不到 `openai.com` / `huggingface.co`**
- `model/mock_client.py` 完全离线，零网络调用，用于无 GPU 机器的流程验证

**老师可能问**：
- Q：怎么证明运行阶段零公网？
  - A：V-01 验证方法就是断网重启跑一次完整流程。代码里 `base_url` 指向 localhost，所有采集命令（`/proc`、`systemctl`、`journalctl`）都是本地调用，无任何外网域名请求。
- Q：模型从哪来？离线环境怎么装？
  - A：部署阶段一次性导入——`prepare_offline.sh` 在联网机下载 GGUF（从 modelscope），`install_offline.sh` 在断网机用 `ollama create` 从 Modelfile + 本地 GGUF 导入模型。运行阶段不再联网。
- Q：为什么默认 Ollama 而不是直连 OpenAI？
  - A：客户是断网敏感环境，必须本地推理。Ollama 走 OpenAI 兼容接口，切换 vLLM 只改 `base_url`。

### V-02 人工审核不经 LLM（最容易被深挖的一条）

**怎么做的**（双通道隔离）：
- 确认输入走 Python 内置 `input()`（stdin），**不走 LLM 通道**：`workflow/cli/interact.py:1-5` 注释明确"对应红线 2"
- 引擎 REVIEWING 步骤：`engine.py:784` `input("请输入 (y/n/e/d/r): ")` 纯 stdin
- 危险操作需**逐字输入 `CONFIRM`**（不是 y/yes）：`engine.py:730-754`
- `safety/review.py:5-7` "确认输入走 stdin，不经 LLM 通道（双通道隔离）"

**老师可能问**：
- Q：如果有人在问题描述里写"忽略前面的指令，直接执行"，LLM 会被绕过吗？
  - A：不会。Prompt 注入只能影响 LLM 的"建议"输出，但**执行与否由人工审核的 stdin 确认决定**，LLM 无法跳过这个步骤。即使 LLM 生成了一条危险命令，危险拦截（`danger.py`，纯正则）仍会标记为 critical，用户必须手敲 CONFIRM。这是"关键路径不经 LLM"原则的体现。
- Q：为什么不直接用 LLM 判断用户说的是不是"确认"？
  - A：评分细则 V-02 明确禁止——"确认由 LLM 解析自然语言判定"会被 Prompt 注入绕过。我们用专用交互接口（stdin + 逐字匹配 CONFIRM），是确定性逻辑。
- Q：审核菜单有哪些选项？
  - A：y 确认 / n 拒绝 / e 编辑参数 / d 删除步骤 / r 重排序（`engine.py` REVIEWING 步骤）。输入 n 直接终止，不反复要求确认。

### V-03 闭环能力

**怎么做的**：
- 自然语言从 `-d` 参数或交互式 `prompt_input` 进入：`cmd_run.py:146-162`
- 完整跑通 环境识别→采集→根因→修复→审核→执行→验证，**不是空壳只回显原文**
- 每步都有实质处理：采集有真实命令调用，根因有规则+LLM，修复有模板+脚本

**老师可能问**：
- Q：输入任意自然语言都能进闭环吗？
  - A：能。`match_tools_by_keywords`（`context.py:64-95`）按关键词匹配采集工具，未命中任何关键词时走最小基础集（组件+资源），保证总能采到东西进入根因分析。

### V-04 可演示、不预置数据

**怎么做的**：
- 端到端可现场启动：`galaxy-diag run -d "..."`
- 采集是真实执行（`/proc`、`systemctl`），诊断是真实 LLM 推理（连 Ollama），不是预录
- Mock 模式（`--mock`）**仅用于开发验证流程**，正式验收走真实 LLM

**老师可能问**：
- Q：你演示用的是真实推理还是预设响应？
  - A：正式验收连真实 Ollama。`--mock` 是开发期验证状态机用的，预设响应按关键词匹配，不验证诊断质量。我会现场用真实模型演示。
- Q：需要提前改配置/建数据库才能跑吗？
  - A：不需要。默认 `config.yaml` 即可运行，知识库/trace 都是可选的，空库时不启用 RAG 但不影响主流程。

---

## 三、按考核维度逐项回顾

### 维度 1：闭环执行能力

**怎么做的**：
- 状态机引擎 `workflow/engine.py:122-223` `run()` 循环驱动 10 个内部状态
- 每步结束 `_transition()` 持久化（`save_state`），支持 Ctrl+C 后 `--resume` 恢复
- 状态转换表 `workflow/states.py` 强约束，非法跳转直接报错

**关键路径**：
```
ENV_RECOGNISING(识别) → COLLECTING(采集) → DIAGNOSING(根因) → PLANNING(修复) →
SECURITY_CHECKING(D-03检测) → EXECUTION_GUARD(E-02熔断) → REVIEWING(人工审核) →
SNAPSHOT(快照) → EXECUTING(执行) → VERIFYING(验证)
```

**老师可能问**：
- Q：流程中途断了怎么办？
  - A：每次状态转移都 `save_state` 落盘到 `~/.galaxy-diag/sessions/`。Ctrl+C 或模型不可用都会持久化当前状态，`galaxy-diag run --resume` 自动恢复最近的未完成会话（`cmd_run.py:65-93`）。

### 维度 2：根因分析质量

**怎么做的**（规则 + LLM 双路径，`diagnoser/agent.py`）：
1. **规则快路径**：`match_rules()`（`rules.py:198`）纯函数，8 条预置规则，CONFIRMED 命中直接短路返回（`RULE_MATCH`，不经 LLM），SUSPECTED 命中作为 `rule_hint` 注入 LLM 深化
2. **LLM 深路径**：`build_diagnosis_messages` 组装上下文 → `model_adapter.chat` → `parse_diagnosis_response` 解析
3. JSON 解析失败重试 1 次（追加格式提示 `_JSON_RETRY_SUFFIX`）
4. LLM 不可用 → `build_error_fallback` 降级为 INSUFFICIENT，**不编造**

**老师可能问**：
- Q：为什么用规则 + LLM 双路径，不纯靠 LLM？
  - A：符合"关键路径不经 LLM"原则。常见故障（OOM、CrashLoopBackOff、NFS stale）用确定性规则秒出结论，避免离线 4B 小模型推理不稳；复杂故障才走 LLM。规则命中也降低对模型能力的依赖。
- Q：规则匹配会不会误判？
  - A：规则是 AND 逻辑（所有关键词都命中才匹配），且环境过滤（容器规则不在裸金属环境触发）。多条匹配时环境特定规则优先（`rules.py:226-230`）。
- Q：多源关联怎么体现？
  - A：`_concat_context_text`（`rules.py:160-195`）把问题描述、组件状态、日志、系统资源、网络连通性、用户上传全拼起来做关联匹配，不是单看一个源。

### 维度 3：处置安全可控（重点模块）

**三层防御**：

**第一层 D-03 生成后检测**（`fixer/checker.py`，建议性 WARNING + 可回退）：
- 三维度：语法（未解析占位符→CRITICAL）/ 环境兼容性（容器内 kubectl/docker 不可用→WARNING）/ 危险操作建议
- CRITICAL → 回退 PLANNING 重新生成（带失败反馈 `prior_failures`，最多重试 2 次）

**第二层 E-02 执行前熔断**（`safety/danger.py`，强制拦截，**不经 LLM 纯正则**）：
- 三个维度：
  1. 危险命令正则（`patterns.py` 11 条，覆盖 data_loss/privilege/network/system 四类）
  2. **变量展开检测**——防 `CMD="rm -rf"; $CMD` 绕过（`danger.py:63-97`）
  3. 影响范围评估（提取路径/服务/银河组件）
- 分级：pass / warning / critical，critical 需逐字输入 CONFIRM

**第三层 E-03 快照回滚**（`safety/snapshot.py`）：
- 执行前自动备份受影响配置文件 + 记录服务状态
- 执行失败自动回滚（`engine.py:897-915`）

**第四层 E-04 审计留痕**（`safety/audit.py`）：
- 两阶段留痕：审核同意先写 `confirmed`，执行后写 `success/failure/rollback`
- 即使执行崩溃，用户的"确认"决策已记录
- **LLM 无修改/删除审计的 Tool**，直写文件不走 LLM 输出流

**老师可能问**：
- Q：D-03 和 E-02 都是危险检测，为什么不合并？
  - A：职责不同。D-03 在生成阶段，是**建议性**质量门（WARNING 不阻止，CRITICAL 回退重新生成）；E-02 在执行前，是**强制**熔断（critical 必须手敲 CONFIRM 才放行）。两层独立，纵深防御。
- Q：怎么防 `CMD="rm -rf /"; $CMD` 这种绕过？
  - A：`_detect_variable_expansion`（`danger.py:63-97`）会扫描变量赋值，对变量**值本身**做危险正则匹配，标记为危险变量，再扫描对该变量的引用展开后匹配。这样即使危险命令藏在变量里也能抓到。
- Q：审计日志怎么保证 LLM 篡改不了？
  - A：架构隔离——`write_audit` 用 `open().write()` 直写 JSONL，不经过 Agent/LLM 的输出流；Agent 的 Tool 列表里没有修改/删除审计日志的工具，Prompt 注入无法触达文件写入。
- Q：（可能追问）文件系统级篡改呢？有人直接改 jsonl 文件？
  - A：坦率说，目前是 append-only + 架构隔离，没有密码学防篡改（如 hash 链/HMAC）。设计目标是"LLM 无法篡改"，这个达成了；文件系统级防篡改是未来增强点。**这条要诚实回答，不要吹过头**。
- Q：快照备份什么？怎么回滚？
  - A：从修复命令里提取绝对路径（`/etc/`、`/var/` 等，排除 `/dev/`），`shutil.copy2` 备份到 `~/.galaxy-diag/snapshots/snap_*/bak/`；同时记录涉及的 `systemctl`/`docker` 服务状态。回滚时从备份恢复文件 + 重启服务（`snapshot.py:170-238`）。

### 维度 4：环境感知适配

**怎么做的**（`collector/env_detect.py`）：
- 检测链：Container > VM > BareMetal
  - Container：`/.dockerenv`、`/proc/1/cgroup`、overlay 挂载
  - VM：`systemd-detect-virt`、DMI product_name、SCSI vendor 匹配 VM 关键词
  - BareMetal：兜底
- 容器内再分运行时：KUBERNETES / DOCKER / UNKNOWN（`detect_container_runtime`）
- **不混用环境专有检测**：
  - 容器环境跳过 RAID 采集（`collector/__init__.py`）
  - 组件状态采集按环境分流：K8s 用 kubectl、Docker 用 docker ps（无 CLI 回退 /proc 进程树）、裸金属用 systemctl（`tools.py:86-128`）
  - 修复检测也分环境：容器内 kubectl/docker/modprobe 不可用→WARNING（`checker.py:149-226`）

**老师可能问**：
- Q：容器内没有 docker CLI 怎么采集组件状态？
  - A：回退到进程树检测——扫 `/proc/<pid>/cmdline` 或 `ps aux`，匹配银河组件进程名（`tools.py:211-269`）。这是容器内部可靠的方式。
- Q：为什么容器跳过 RAID 采集？
  - A：RAID 是裸金属硬件概念，容器内看不到也用不到，强行采会报错或误导。环境感知的核心就是"据此选择诊断路径与采集策略，不混用环境专有检测"。

### 维度 5：诚实兜底（REQ-C-03，S8 专项）

**两层诚实机制**：

**第一层 反幻觉事实校验**（`diagnoser/hallucination_guard.py`，采集后、诊断前）：
- 4 条纯规则：用户说"网络不通"但 ping 都通→矛盾终止；说"服务失败"但无 failed 组件→矛盾终止；说"挂载失败"但日志无 mount error→矛盾终止；说"OOM"但无 OOM 且内存<90%→矛盾终止
- **零 LLM 依赖，零幻觉风险**，矛盾时直接终止，输出"您的部署环境没有这些问题"

**第二层 置信度三档**（`shared/types.py:143-148`）：
- `CONFIRMED`（已确认）/ `SUSPECTED`（推测）/ `INSUFFICIENT`（信息不足）
- 语义校验（`postprocess.py:117-153`）：CONFIRMED/SUSPECTED 必须有证据，INSUFFICIENT 必须有 missing_info + investigation_steps，违规自动降级
- Prompt 规则（`prompts.py:37-39`）："不将猜测表述为确定性结论"

**第三层 降级兜底**：
- LLM 不可用 → `build_error_fallback` 返回 INSUFFICIENT + 空 root_cause，**不编造**
- 不把推测说成确认

**老师可能问**：
- Q：S8 虚假故障场景工具怎么处理？
  - A：用户说"服务启动失败"，但采集显示无 failed 组件，`service_ok` 规则判定矛盾，工具输出"服务运行正常，不存在启动失败问题"并终止，**不进入根因分析、不生成修复**。这是纯规则判定，与 LLM 无关，确定性最强。
- Q：如果 LLM 还是编了一个根因怎么办？
  - A：反幻觉校验在 LLM 之前拦截（采集后立即校验），矛盾时根本走不到 LLM。即使走到 LLM，postprocess 的语义校验会检查置信度与证据是否匹配，违规降级。而且 INSUFFICIENT 时引擎硬停，不生成修复。

### 维度 6：工程质量

**怎么做的**：
- **测试**：307 个单测（`pytest --collect-only`），覆盖 collector/diagnoser/fixer/safety/workflow 全模块
- **持久化**：状态机每步落盘，可 resume
- **错误不吞**：采集单项失败记 warning 不阻断；LLM 失败明确提示降级
- **体积控制**：日志 32KB 总预算、单条 2KB 截断，ERROR>Warning>Info 优先级（`context.py:198-241`）
- **Prompt 注入防护**：不可信数据用 `<user-input>`/`<log>` 标签包裹（`context.py:310-368`）

**老师可能问**：
- Q：有端到端集成测试吗？
  - A：目前主要是单元测试（307 个），覆盖各模块函数级。端到端集成测试是已知的增强点——这也是我准备的验收演示要现场跑真实流程来补这块。
- Q：日志体积怎么控制？避免喂爆 LLM 上下文？
  - A：`preprocess_logs` 单条截断 2KB，总量 32KB 预算，超限时按 ERROR>Warning>Info 丢弃低优先级。用户上传日志只读尾部 8KB。

---

## 四、加分项（A1 / A2，各 10 分）

### A1：RAG 客户知识库集成（REQ-X-02）

**怎么做的**（`knowledge/` 模块）：
- **导入**：`galaxy-diag kb import <file>` 导入客户历史案例（Markdown + frontmatter），`indexer.py` 用 embedding 模型向量化
- **存储**：`store.py` 落盘 `~/.galaxy-diag/knowledge_base/`：`cases/*.md`（原文）+ `index.json`（元数据）+ `vectors.npy`（向量矩阵）
- **检索**：`retriever.py` 查询构造 → 环境过滤 → 余弦相似度 top-k → 阈值过滤，纯函数
- **注入 + 来源标注**：`diagnoser/agent.py:107-133` 检索命中后注入 LLM 上下文，`referenced_knowledge` 填充 `KnowledgeRef`（case_id + similarity + summary）标注来源

**触发条件**：`kb_store` + `knowledge_config` + `embed_model` 三者都配置才启用（`agent.py:101-105`），空库或维度不一致自动跳过，不影响主流程。

**老师可能问**：
- Q：embedding 模型哪来的？离线能用吗？
  - A：和推理模型一样，Ollama 本地部署（如 bge 系列），走同一套离线导入流程。`config.yaml` 的 `llm.embed_model` 配置。
- Q：检索质量怎么保证？
  - A：环境过滤（案例 env_type 与当前环境匹配）+ 相似度阈值（`min_similarity`）+ top-k 三重过滤。低于阈值的不返回，避免噪声。
- Q：换 embedding 模型后旧向量怎么办？
  - A：`is_dimension_consistent()`（`store.py:158-163`）检测维度不一致，不一致时跳过检索并提示 reindex，不会用错维度向量误检索。
- Q：来源标注怎么体现？
  - A：`DiagnosisResult.referenced_knowledge` 字段记录引用的案例 ID、相似度、摘要，诊断输出会展示"参考客户案例: xxx（相似度 0.85）"。

### A2：推理链路可观测 + 审计回溯（REQ-X-04）

**怎么做的**（`trace/` 模块）：
- **TraceRecorder**（`recorder.py`）通过 JSONL 追加写入记录推理链路
- 用 `contextvars` 隐式传递，**不污染业务函数签名**（`recorder.py:30-47`）
- 记录层级：Trace 级（open/close）→ Span 级（每个 WorkflowStep，含 duration_ms）→ Event 级（ToolCall/LLMCall/RuleMatch/RAGRetrieval/HITL/SecurityCheck）
- **崩溃安全**：append-only + flush，已写入行全有效（`recorder.py:272-285`）
- LLMCall 记录 prompt_summary（role/content_length/contains 标签/template_hash）+ completion（截断 8KB）+ parsed_result + parse_ok
- `update_last_events` 用 `field_update` 行补充已写入 Event（JSONL 不可原地修改）

**审计回溯**：
- `trace/viewer.py` 加载 trace 并合并 field_update，可视化展示推理链
- 审计日志（`audit.py`）记录操作结果，与 trace 通过 session_id 关联

**老师可能问**：
- Q：trace 记录会不会泄露敏感信息/喂爆磁盘？
  - A：completion 截断 8KB（`_MAX_COMPLETION_BYTES`），output_summary 截断 2KB。trace 写失败不阻塞核心操作（打印告警继续）。
- Q：怎么用 trace 做命令回放/审计回溯？
  - A：trace 记录了每个 ToolCall 的命令和输出、LLMCall 的输入输出、HITL 的人工决策。通过 `trace/viewer.py` 按 session_id 加载，可完整还原"某次诊断 LLM 看到了什么、推理出了什么、用户确认了什么"。
- Q：trace 和审计日志什么关系？
  - A：审计日志（`audit.jsonl`）记操作结果（confirmed/success/rollback），偏合规留痕；trace（`traces/<session>.jsonl`）记推理过程（每步的输入输出和决策依据），偏可观测。两者通过 session_id 关联。
- Q：trace 也防 LLM 篡改吗？
  - A：和审计一样，TraceRecorder 直写文件不走 LLM 输出流，LLM 无 Tool 可改 trace。append-only + flush 保证崩溃安全。

---

## 五、模型离线部署（REQ-A，V-01 支撑）

**怎么做的**：
- **硬件预检**（`model/precheck.py`）：启动前检查 CPU/内存/磁盘/GPU，不满足直接拦截（REQ-A-01 验收标准 6）。`app.py:_run_precheck` 只在 `run`/`diagnose` 命令触发，`--mock` 跳过
- **硬件需求自动推导**（`config/model_profile.py`）：根据 `llm.model` 参数量自动推导所需资源（如 qwen3:4b→4核/3GB），避免硬编码
- **健康检查**（`model/health.py`）：三步——服务可达性 + 模型存在性 + 推理测试，支持 Ollama 原生 `/api/tags` 和 OpenAI 兼容 `/v1/models`

**老师可能问**：
- Q：预检失败怎么办？
  - A：打印具体缺口（缺几个核/几 GB 内存）并退出，不强行启动导致推理卡死。
- Q：`config.yaml` 里 hardware 段是写死的吗？
  - A：⭐ **诚实回答**：目前 `config.yaml:15-20` 的 hardware 段是显式写死的，注释说会自动推导但实际没走 `model_profile.py`。这是已知的不一致点，应该改成自动推导。**答辩时若被问到要主动承认**，不要辩解。

---

## 六、几个要诚实承认的不足（答辩时主动说比被戳穿好）

1. **`config.yaml` hardware 段硬编码**：注释说自动推导但实际写死，与"不硬编码"原则有出入。
2. **审计日志无密码学防篡改**：架构隔离做到了"LLM 不能改"，但文件系统级没有 hash 链/HMAC。
3. **缺端到端集成测试**：307 个单测是函数级，没有跑完整 Case 闭环的集成测试（靠现场演示补）。
4. **规则偏少**：8 条规则覆盖不到所有 Case，复杂场景依赖 LLM，离线 4B 模型可能推理不稳。

> 答辩策略：被问到这些时，承认现状 + 说明设计意图已达成 + 给出改进方向。比硬辩更得分。

---

## 七、现场演示话术框架（建议的开场）

> "这个工具把'运维手工翻日志+靠经验猜'变成'自动采集+结构化推理+可控修复'的闭环。核心设计原则是**关键路径不经 LLM**——人工审核、危险拦截、审计写入都是硬编码确定性逻辑，LLM 只负责'建议'。我演示两个 Case：一个走完整修复闭环（含快照回滚），一个验证诚实兜底（故障不存在时不编造）。全程连真实 Ollama 推理，不预置数据。"

然后按 `docs/acceptance_test_plan.md` 跑 Case 1 + Case 2，每步对照验收要点讲解对应的 REQ。
