# Design

## Context

仓库当前为空，本项目从零搭建，目标是在比赛限期内交付可现场演示的 AIOps 告警诊断闭环。约束如下：

- 没有真实监控系统、消息队列与生产数据可依赖，必须自带模拟源与故障场景库。
- 演示环境可能无外网或没有大模型凭据，诊断链路必须在无 LLM 时仍可完整跑通。
- 单人/小团队开发、单机部署，优先选择依赖少、启动快、易调试的技术组合。
- 评委会现场触发告警并追问"为什么是这个根因"，因此诊断过程需要可解释、可审计、可量化。

需求与验收行为见 `specs/` 下各能力；动机见 `proposal.md`。

## Goals / Non-Goals

**Goals:**

- 一条命令启动后端（含静态前端），三分钟内完成"注入故障 → 降噪关联 → 根因诊断 → 生成报告"演示。
- 告警到事件的压缩过程、根因候选排序、LLM 调用与降级均可从接口和页面观察到。
- LLM 接入保持可替换：OpenAI 兼容端点、Mock、未来本地模型共用同一适配层。
- 内置场景评估可重复执行，输出可对比的准确率与耗时指标。
- 代码按领域模块拆分，后续接真实数据源或换存储时不需要重写诊断逻辑。

**Non-Goals:**

- 不做多租户、RBAC、审计合规与生产级高可用；不引入 Kafka/Flink 等流式基础设施。
- 不做真实 Prometheus/Zabbix/云监控适配器（仅保留 Webhook 作为通用入口）。
- 不做模型训练或微调；决策模型为可解释规则打分，LLM 仅通过 Prompt 使用。
- 不做前端 SSR、国际化与移动端专门适配；看板以桌面端演示为主。
- 不自动执行任何修复命令，报告中的操作仅为建议。

## Decisions

### D1. 单体 FastAPI + SQLite，按领域拆包

后端采用单体应用，`backend/app` 下按领域拆分：`ingestion`（接警与日志）、`dedup`（降噪聚类）、`topology`（拓扑与关联）、`diagnosis`（决策模型、LLM、诊断编排）、`reporting`、`evaluation`、`api`（路由）、`models`（ORM）、`schemas`（Pydantic）、`core`（配置、日志、异常）。存储用 SQLite + SQLAlchemy 2.x（WAL 模式）。

- 理由：单进程即可演示，无需 Redis/Postgres 运维；SQLAlchemy 屏蔽方言，后续可切 PostgreSQL。
- 备选：Django（自带 admin 但对异步与算法代码偏重）、微服务（演示阶段纯增运维成本）。
- 约束：演示只启动一个 uvicorn worker；写操作通过短事务与批量插入控制锁竞争。

### D2. 数据模型围绕"告警 → 聚合 → 事件 → 诊断 → 报告"链路

核心表与职责：

| 表 | 关键字段 | 说明 |
| --- | --- | --- |
| `alerts` | id, fingerprint, source, host, service, rule, severity, message, labels(JSON), occurred_at, first_seen, last_seen, repeat_count, cluster_id | 规范化后的原始告警，幂等去重 |
| `alert_clusters` | id, fingerprint, host, rule, window_start, severity, status, member_count, first_seen, last_seen, merge_reason(JSON) | 降噪后的聚合告警 |
| `logs` | id, ts, host, service, level, message, stack, trace_id, alert_id | 带堆栈日志证据 |
| `incidents` | id, title, status, window_start, window_end, nodes(JSON), alert_count, root_candidates(JSON), topology_linked, batch_id | 拓扑关联后的故障事件 |
| `incident_alerts` | incident_id, alert_id | 事件与告警多对多 |
| `topology_nodes` / `topology_edges` | id, kind, name, upstream_id, downstream_id, weight | 从 YAML 载入的拓扑快照 |
| `diagnoses` | id, incident_id, version, status, source(llm/degraded/mock), root_cause_category, root_cause_node, confidence, reasoning, evidence(JSON), missing_evidence(JSON), needs_review, model, prompt_version, latency_ms, token_usage(JSON), config_snapshot(JSON), error | 诊断版本化记录 |
| `reports` | id, incident_id, diagnosis_id, version, format, content(JSON/Markdown), risk_level, created_at | 报告版本 |
| `scenarios` | id, name, category, definition(JSON), expected(JSON), source_file | 故障场景库 |
| `replay_batches` | id, scenario_id, seed, compression, started_at, status, stats(JSON) | 回放批次 |
| `evaluations` | id, batch_ids(JSON), config(JSON), metrics(JSON), created_at | 评估批次 |
| `evaluation_cases` | evaluation_id, scenario_id, expected(JSON), actual(JSON), top1_hit, top3_hit, node_hit, latency_ms | 逐用例结果 |

索引：`alerts(fingerprint, occurred_at)`、`alerts(cluster_id)`、`alert_clusters(fingerprint, window_start)` 唯一、`incidents(window_start)`、`diagnoses(incident_id, version)` 唯一、`logs(host, ts)`、`evaluation_cases(evaluation_id)`。

- 理由：聚合与事件分离，降噪和拓扑关联可独立演进；诊断版本化便于重跑与对比。
- 热数据（拓扑、场景、Prompt）来自文件并在启动时同步入库，文件是唯一事实来源，便于评审查看。

### D3. 降噪采用"指纹 + 固定时间桶"的幂等聚合

- 指纹：`sha256(source + host + normalized_rule)`；规则归一化去除实例编号与动态参数（如 `cpu_usage=97%` → `cpu_usage`）。
- 时间桶：`window_start = floor(occurred_at / window_seconds) * window_seconds`，默认窗口 300 秒（可配）。
- 写入：`INSERT ... ON CONFLICT(fingerprint, window_start) DO UPDATE` 累加 `member_count`、更新 `last_seen`、提升严重级别、追加成员关联。
- 边界处理：查询时同时检查当前桶与前一个桶，若前一桶在窗口内且未解决则并入，降低桶边界把同一故障拆开的概率。
- 生命周期：后台清扫任务每 30 秒把 `now - last_seen > cooldown`（默认 10 分钟）的聚合告警置为已解决；复发时若原聚合已解决且在同一指纹的重开窗口内，则重开并追加成员。
- 备选：滑动窗口内存聚合——重启即丢失、难审计；纯 SQL GROUP BY 事后聚合——无法支持实时状态与确认操作。

### D4. 拓扑关联用有向图可达性 + 时间窗，因果方向由入度决定

- 拓扑来源：`config/topology.yaml`，节点 `kind ∈ {service, host, db, cache, gateway}`，边表示故障传播方向（被依赖组件 → 依赖它的组件），可选 `weight` 表示影响强度。
- 算法步骤：
  1. 取关联时间窗（默认 120 秒）内未归属事件的聚合告警；
  2. 以节点为粒度构建"告警节点子图"，若两节点的聚合告警在时间窗内且拓扑中存在任一方向的可达路径，则连通；
  3. 用并查集得到连通分量，每个分量生成一个 `incident`；
  4. 在分量诱导子图中，入度为 0 的节点（无上游告警）作为根因候选，按 `最早告警时间、严重级别、决策模型得分、拓扑层级` 加权排序；
  5. 事件置信度综合拓扑距离、告警时间先后与严重级别，输出 `root_candidates` 及每条排序依据。
- 降级：节点不在拓扑、拓扑为空或配置非法时，不建立跨节点因果，事件标记 `topology_linked=false`，根因候选仅按告警特征排序。
- 备选：仅时间相关性聚类——无法区分上下游；引入图数据库——演示规模无必要。
- 实现：纯 Python 邻接表 + BFS，不引入 networkx，减少依赖并便于解释每条路径。

### D5. 决策模型采用可解释规则打分，而非训练模型

- 故障类别（可扩展）：`db_connection_pool_exhausted`、`network_partition`、`disk_full`、`cpu_overload`、`service_crash`、`config_error`、`dependency_timeout`。
- 特征：规则关键词、节点角色、严重级别、严重级别变化、日志错误签名（正则目录，如 `Too many connections`、`No space left on device`、`Connection reset`）、拓扑位置、告警时间聚集度。
- 打分：每个类别维护特征权重（`config/decision_model.yaml`），`score = Σ w_i * x_i`，输出 Top-3、归一化置信度与命中特征列表；同输入严格确定性。
- 理由：零训练数据即可工作、可向评委解释每个得分来源、评估后可手工调权。
- 备选：XGBoost/逻辑回归——需要标注数据；LLM 直接分类——慢且不确定。评估数据积累后可把打分模型替换为学习排序器，接口不变。
- 性能：纯内存计算，目标 p95 < 500ms（实际为毫秒级）；结果带 `model_version`。

### D6. LLM 适配层：OpenAI 兼容 + 结构化输出 + 强制降级

- 配置：`LLM_MODE ∈ {auto, llm, mock}`（默认 auto）、`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`LLM_TIMEOUT`、`LLM_MAX_RETRIES`。
- 调用：直接使用 `httpx` 调 `/chat/completions`，不引入厂商 SDK；请求 `response_format=json_object`（端点不支持时降级为纯文本解析）。
- Prompt 分层：
  - system：SRE 根因分析专家，要求只输出 JSON，不得编造证据，证据不足时降低置信度并列出缺失项；
  - user：事件卡片（时间范围、涉及节点与拓扑路径、Top 告警、日志片段、决策模型候选）、输出 JSON Schema、少量正负示例；
  - Prompt 存为 `backend/app/diagnosis/prompts/rca_v1.md`，启动时计算内容哈希作为 `prompt_version` 写入诊断记录。
- 证据预算：最多 20 条核心告警、40 行日志、总字符 ≤ 8KB；堆栈只保留顶部与异常行，重复帧折叠。
- 校验与重试：Pydantic 校验根因类别枚举、置信度范围、证据引用必须存在于证据集；首次失败把校验错误回传给模型重试一次，仍失败则降级。
- 降级：使用决策模型结论 + 模板化推理，`source=degraded`，置信度上限 0.6，记录降级原因；无任何凭据时自动使用 Mock，返回结构与真实模式一致。
- 备选：LangChain/OpenAI SDK——抽象层厚、排障困难；本层仅百余行，可控性更高。

### D7. 诊断异步执行与幂等

- 接口 `POST /api/v1/incidents/{id}/diagnose` 创建诊断记录后立即返回 `pending`，由进程内 `asyncio` 任务执行；单 worker 部署下足够，避免引入 Celery + Redis。
- 幂等：对 `(incident_id, status ∈ {pending, running})` 建部分唯一索引，重复请求返回既有诊断 ID；同事件并发请求不重复调用模型。
- 状态机：`pending → running → succeeded | degraded | failed`；进程重启时把残留 `running` 置为 `failed` 并允许重试。
- 重跑：`POST /diagnose?rerun=true` 创建 `version + 1` 的新记录，历史版本保留。
- 备选：同步阻塞调用——LLM 超时会拖垮请求；Celery——演示环境多一层 Redis 依赖。

### D8. 报告 = 确定性骨架 + LLM 增强，缺一不可

- 先生成确定性骨架：摘要、影响范围、根因、证据、风险提示由诊断结果直接映射，保证任何情况下章节完整。
- 再让 LLM 生成"分步排查与修复"步骤（结构化 JSON：动作、预期结果、风险等级、回滚动作）；校验失败或 LLM 不可用时使用每个故障类别的内置步骤模板。
- 高风险操作（重启、删除、改配置）由规则检测关键词后强制标注高风险与人工确认提示，模型无权降低风险等级。
- 版本：`reports(incident_id, diagnosis_id, version)`，重新生成递增版本；导出 Markdown/JSON 时按版本输出，文件名含事件 ID 与版本号。

### D9. 前端 Vue 3 + ECharts，轮询刷新

- 技术：Vue 3 Composition API + Vite + TypeScript + Vue Router + Pinia（仅存筛选条件与轮询状态），图表用 ECharts（严重级别分布、Top 主机降噪、拓扑关系图）。
- 页面：概览、告警列表、事件列表、事件详情（含拓扑图与诊断面板）、报告页、评估页、模拟注入页。
- 刷新：默认 3 秒轮询，列表接口支持 `updated_since` 增量参数；后端后续可平滑替换为 SSE，前端封装在 `useLiveQuery` 中隔离。
- 构建产物由 FastAPI `StaticFiles` 托管，`/` 返回 SPA，`/api/*` 走接口，单命令启动；开发态 Vite 代理到 8000 端口。
- 备选：React + Ant Design——组件更全，但 ECharts 与轻量看板用 Vue 足够；SSE——演示环境代理配置易出问题，先用轮询。

### D10. 场景库与评估使用虚拟时钟与确定性诊断

- 场景 YAML 字段：`id`、`name`、`category`、`compression`、`topology`（节点/边）、`alerts[]`（相对秒偏移、host、service、rule、severity、message）、`logs[]`（相对偏移、level、message、stack、trace_id）、`expected`（root_cause_category、root_cause_node、affected_nodes）。
- 回放：以批次开始时间为基准，按压缩倍率换算真实时间戳注入；告警与日志写入时携带 `batch_id`，事件与批次关联，互不污染历史数据。
- 评估：回放后等待事件关联完成，对每个事件调用诊断；评估模式强制 `LLM_MODE=mock` 且固定随机种子，保证同场景集结果可复现。LLM 真实模式也可评估，但结果单独标注、不承诺一致。
- 指标：Top-1/Top-3 根因类别命中、根因节点命中、平均诊断耗时、平均压缩比；逐用例保存期望值与实际值，支持导出 JSON。
- 内置场景 ≥ 6：连接池耗尽、网络分区、磁盘写满、CPU 过载、服务进程崩溃、配置错误。每个场景同时提供正常与干扰告警，避免"只给关键告警"导致虚高准确率。

### D11. 配置、启动与可观测性

- `pydantic-settings` 统一读取 `.env`：窗口、阈值、LLM、CORS、Webhook token 等。
- 提供 `backend/app/cli.py`：`seed`（载入拓扑/场景/Prompt）、`demo`（注入一次演示故障）、`evaluate`（跑全部场景并打印指标）；Makefile 包装常用命令。
- 结构化日志（JSON 可选），关键链路打印 `incident_id`、`diagnosis_id`、耗时与降级原因；`/healthz` 返回数据库与大模型配置状态。
- Webhook 可选共享令牌校验（`X-Webhook-Token`），默认关闭；CORS 默认仅允许本地前端端口。

## Risks / Trade-offs

- **SQLite 写锁在批量注入时成为瓶颈** → 单 worker + WAL + 批量 `executemany`，注入接口按 200 条分批提交；预留 PostgreSQL 迁移路径。
- **固定时间桶在边界处拆散同一故障** → 查询时合并前一个桶，并把窗口配置为 5 分钟；评估场景覆盖边界时间点，必要时切换为"滑动窗口 + 桶锚点"实现。
- **拓扑配置与真实环境不符导致错误因果** → 只在存在明确路径时关联，未知节点一律降级标记，事件详情展示关联路径供人工判断。
- **LLM 超时、限流或输出漂移** → 强 schema 校验 + 一次修复重试 + 自动降级 + Mock 兜底；演示前预热一次调用。
- **无网络/无 API Key 的现场环境** → 默认 `auto` 模式自动落到 Mock，README 明确说明两种模式差异，避免现场措手不及。
- **评估准确率被内置场景"喂题"而虚高** → 场景与 Prompt 分离，保留 hold-out 场景；指标只用于横向比较优化前后，不作为绝对能力宣称。
- **后台诊断任务在进程重启时丢失** → 启动时回收 `running` 状态为 `failed`，页面提供重试；演示脚本不依赖长时间运行任务。
- **轮询带来额外请求** → 3 秒间隔 + 增量参数 + 事件详情页按需拉取；演示规模下单机压力可忽略。
- **前端构建产物与后端版本不一致** → 构建脚本把版本号写入静态资源目录，`/healthz` 与页面页脚均显示版本，README 给出一键重建命令。

## Migration Plan

本项目为绿地开发，无历史数据迁移。部署与回滚策略：

1. 初始化 Python 虚拟环境与前端依赖，`make seed` 载入拓扑、场景与 Prompt。
2. `make dev` 启动 uvicorn（单 worker）与静态前端；`make demo` 注入演示故障并打印事件与诊断结果。
3. 数据库为单文件 SQLite，出现问题时删除 `data/aiops.db` 后重新 `make seed` 即可回到干净状态。
4. 配置以 `.env` 为准，切换 LLM 模式只需改环境变量并重启，不影响已落库数据。
5. 前端可独立回滚：保留上一版构建产物目录，FastAPI 指向不同静态目录即可切换。

## Open Questions

- 最终现场演示是否配置真实大模型（DeepSeek/OpenAI）以展示真实推理效果，还是固定 Mock 保证可复现？当前设计两者皆可，演示前确认即可。
- 是否需要支持评审现场自定义上传故障场景（当前通过 YAML 文件与 API 写入，页面只读展示），可在演示后扩展。
- 拓扑视图是否需要展示历史告警热度叠加，现有拓扑图仅展示依赖关系与当前事件涉及节点，后续可加。
