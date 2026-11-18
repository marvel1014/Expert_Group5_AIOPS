# Proposal

## Why

机房告警风暴下，值班人员面对的是数百条彼此重复、互相牵连的告警，真正的故障根因被淹没在噪声中；现有监控平台只做阈值触发与告警展示，不负责"把一堆告警收敛成一次可执行的故障结论"。本项目要在可演示的范围内补齐这条链路：从接警、降噪关联、根因定位到自动生成排查报告，并用故障场景库量化诊断准确率，让人从"看告警"转变为"看结论"。

## What Changes

- 新增 FastAPI 后端，提供 Webhook 接警接口、告警/事件查询接口、诊断与报告接口、场景评估接口，以及可选的 SSE 实时推送。
- 新增模拟源建设：承接 Webhook 告警的模拟器，以及生成带错误堆栈日志的 Python 脚本，用于复现多类机房故障。
- 新增基础降噪：按主机、时间窗、规则对连带告警做指纹聚类与合并，输出聚合告警（事件）。
- 新增拓扑感知关联：维护服务/主机上下游拓扑，识别同一次故障沿拓扑扩散的告警，把上百条告警压缩为一个故障事件。
- 新增根因分析：先以轻量 decision model 做快速故障分类与候选根因排序，再将核心告警与筛选后的日志片段交给 LLM 产出根因结论；LLM 以 OpenAI 兼容适配器接入，并在不可用时回退到确定性规则/Mock 结论，保证演示可复现。
- 新增 AI 诊断报告：基于根因结论自动生成分步排查步骤、修复建议与验证方式，支持 Markdown 导出。
- 新增 Vue 3 + ECharts 单页看板：告警列表、事件详情、拓扑/聚类视图、诊断报告展示与模拟触发入口。
- 新增故障场景库与量化评估：内置多个故障场景，支持回放注入并比对"期望根因 vs 诊断根因"，输出准确率、平均定位耗时等指标。
- 新增项目 README 与一页 AI 复盘材料（关键 Prompt、踩坑记录、后续优化方向）。

**范围约定（非目标）**：不做多租户与复杂 RBAC，不做真实监控系统对接，不做生产级高可用与横向扩展；演示规模假设为单实例、万级告警、SQLite 落库。这些约定在 design.md 中展开。

## Capabilities

### New Capabilities

- `alert-ingestion`: Webhook 接警、告警规范化与幂等落库、模拟告警与带堆栈日志的故障注入。
- `noise-reduction`: 基于主机/时间窗/规则的连带告警指纹聚类与合并，形成聚合事件。
- `topology-correlation`: 服务与主机拓扑建模、上下游因果判定、跨告警压缩为单一故障事件。
- `root-cause-analysis`: decision model 快速故障检测、证据（日志片段）选择、LLM 根因分析编排与无 LLM 时的确定性回退。
- `diagnosis-report`: 基于根因结论自动生成分步排查/修复报告并支持导出。
- `ops-dashboard`: 告警列表、事件详情、拓扑视图与实时刷新等看板行为。
- `fault-library-evaluation`: 多故障场景库、场景回放注入与诊断准确率等量化评估。

### Modified Capabilities

无（本仓库此前没有 OpenSpec 能力定义，全部为新增能力）。

## Impact

- 代码：新建 `backend/`（FastAPI、SQLAlchemy/SQLite、诊断算法与 LLM 适配器）、`frontend/`（Vue 3 + Vite + ECharts）、`scripts/`（模拟源）、`scenarios/`（故障场景库）。
- API：新增 `/api/v1/alerts/webhook`、`/api/v1/alerts`、`/api/v1/incidents`、`/api/v1/incidents/{id}/diagnose`、`/api/v1/reports/{id}`、`/api/v1/scenarios`、`/api/v1/evaluations` 等接口。
- 依赖：FastAPI、Pydantic、SQLAlchemy、httpx、pytest；前端 Vue 3、Vite、ECharts。LLM 通过环境变量配置 OpenAI 兼容端点，默认使用 Mock。
- 配置：`.env` 中提供 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、聚类时间窗、拓扑文件路径等。
- 交付物：可运行后端 + 可选前端 + README + 一页 AI 复盘。
