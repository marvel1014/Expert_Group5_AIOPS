# 机房智能告警诊断平台（AIOps）

> 故障告警 + 智能根因分析：把上百条连带告警收敛为一个可解释的故障事件，自动给出根因、证据与分步修复报告，并用故障场景库量化诊断准确率。

## 1. 项目定位

值班人员面对的真实问题不是"看不到告警"，而是告警太多、彼此牵连、根因被淹没。本平台把链路补全为：

```
Webhook 接警 ──► 降噪聚类 ──► 拓扑关联 ──► 决策模型 + LLM 根因分析 ──► 分步排查/修复报告
                                │                                        │
                                └────────── 可视化看板 ◄─────────────────┘
                                             │
                                    故障场景库 + 量化评估
```

### 已实现能力

- **告警接入**：Webhook 单条/批量接警、字段校验、指纹幂等去重、带错误堆栈日志接入与查询。
- **基础降噪**：按主机 + 规则 + 时间窗聚类合并，聚合告警生命周期（打开/确认/解决/复发重开），输出压缩比与 Top 降噪来源。
- **拓扑感知关联**：基于 `config/topology.yaml` 的有向拓扑识别上下游传播关系，将多节点告警压缩为故障事件，输出根因候选排序与排序依据。
- **快速故障检测**：可解释的决策模型（规则打分）先给出候选故障类别，毫秒级、确定性、无外部依赖。
- **LLM 根因分析**：OpenAI 兼容适配器 + 证据预算 + 日志脱敏 + 结构化输出校验 + 超时/非法输出/无凭据自动降级 + Prompt 版本与 token 审计。
- **AI 诊断报告**：自动生成摘要、影响范围、根因、证据、分步排查、修复建议、验证与回滚，标注风险等级，支持 Markdown/JSON 导出与版本化。
- **可视化看板**：Vue 3 + ECharts，告警/事件列表、事件详情与拓扑图、诊断面板、报告、评估、模拟注入，3 秒轮询。
- **故障库与量化评估**：内置 6 个故障场景，支持回放与 Top-1/Top-3 根因类别准确率、根因节点准确率、平均耗时、平均压缩比，重复评估可复现。
- **模拟源**：`scripts/simulate_alerts.py` 生成带堆栈日志的多主机关联告警。

## 2. 技术栈与架构

| 层 | 技术 |
| --- | --- |
| 后端 | Python 3.11+、FastAPI、SQLAlchemy 2.x、SQLite（WAL）、Pydantic v2 |
| 诊断 | 规则打分决策模型 + OpenAI 兼容 LLM（httpx），Mock 兜底 |
| 前端 | Vue 3 + Vite + TypeScript + ECharts |
| 测试 | pytest（后端 50+ 用例）、TypeScript 构建校验 |
| 部署 | 单进程 uvicorn + 静态前端，无需 Redis/消息队列 |

```
backend/app/
├── core/            # 配置、日志、统一错误
├── ingestion/       # 接警、日志、查询
├── dedup/           # 规则归一化、指纹聚类、降噪统计
├── topology/        # 拓扑加载、可达性、事件关联、根因候选
├── diagnosis/       # 决策模型、LLM 适配、诊断编排、Prompt
├── reporting/       # 报告生成、版本、导出
├── evaluation/      # 场景库、回放、准确率评估
├── api/             # 事件、看板、模拟注入路由
├── main.py          # 应用入口、lifespan、静态托管
├── models.py        # ORM 模型
└── cli.py           # seed / demo / evaluate / replay
config/              # 拓扑、规则族、决策模型配置
scenarios/           # 故障场景库（YAML）
frontend/            # Vue 3 看板
scripts/             # 模拟告警源
docs/                # API 契约、AI 复盘
```

## 3. 快速开始

### 3.1 安装依赖

```bash
# 推荐：虚拟环境
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 前端
cd frontend && pnpm install --registry=https://registry.npmmirror.com && pnpm build && cd ..
```

> 受限环境（home 目录只读）可把后端依赖装到仓库内：
> `pip install --target .deps -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple`
> Makefile 会自动把 `.deps/` 加入 `PYTHONPATH`。

### 3.2 初始化并启动

```bash
make seed        # 建库，载入拓扑与 6 个故障场景
make serve       # 启动 http://127.0.0.1:8000
```

打开 `http://127.0.0.1:8000` 查看看板，`http://127.0.0.1:8000/docs` 查看 OpenAPI 文档。

开发模式（前后端热更新）：

```bash
make dev                     # 终端 1：后端 :8000
cd frontend && pnpm dev      # 终端 2：前端 :5173（Vite 代理 /api 到 8000）
```

### 3.3 三步演示

```bash
# 1) 一条命令注入故障 + 诊断 + 生成报告
make demo

# 2) 回放全部场景并输出准确率
make evaluate

# 3) 手动注入（等价于 Webhook 接警）
curl -X POST http://127.0.0.1:8000/api/v1/alerts/webhook \
  -H 'Content-Type: application/json' \
  -d '{
    "host": "db-host-01",
    "service": "mysql-primary",
    "rule": "MySQL connection pool exhausted 151/151",
    "severity": "critical",
    "message": "Too many connections"
  }'
```

在 UI 上也可以进入「模拟注入」页选择场景一键注入，随后在事件详情里触发诊断、生成报告。

## 4. LLM 配置

默认 `LLM_MODE=auto`：配置了 `LLM_API_KEY` 就调用真实模型，否则自动使用 Mock（确定性输出），**无网络/无密钥也能完整演示**。

```bash
cp .env.example .env
# 例如 DeepSeek
LLM_MODE=auto
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=sk-xxx
LLM_MODEL=deepseek-chat
```

- `LLM_MODE=llm`：只用真实模型，失败自动降级；
- `LLM_MODE=mock`：只用 Mock，保证评估可复现；
- 超时、重试、证据预算、脱敏、置信度阈值均可在 `.env` 配置，字段见 `.env.example`。

诊断记录会保存 `model`、`prompt_version`、`latency_ms`、`token_usage` 与降级原因，便于复盘。

## 5. API 与数据

- 完整接口契约：[docs/api-contract.md](docs/api-contract.md)
- 交互式文档：`/docs`
- 主要接口：
  - `POST /api/v1/alerts/webhook`、`GET /api/v1/alerts`、`POST/GET /api/v1/logs`
  - `GET /api/v1/clusters`、`GET /api/v1/stats/noise`
  - `GET /api/v1/incidents`、`GET /api/v1/incidents/{id}`
  - `POST /api/v1/incidents/{id}/diagnose`、`GET /api/v1/diagnoses/{id}`
  - `POST /api/v1/incidents/{id}/reports`、`GET /api/v1/reports/{id}/export`
  - `GET /api/v1/scenarios`、`POST /api/v1/scenarios/{id}/replay`
  - `POST /api/v1/evaluations`、`GET /api/v1/evaluations/{id}`
  - `POST /api/v1/simulate`、`GET /api/v1/stats/overview`

拓扑文件 `config/topology.yaml` 中边的方向表示**故障传播方向**（被依赖组件 → 依赖它的组件），因此关联后入度为 0 的节点是根因候选。修改后调用 `POST /api/v1/topology/reload` 生效。

故障场景位于 `scenarios/*.yaml`，字段包含相对时间的告警序列、堆栈日志、拓扑节点与期望根因，新增场景后 `make seed` 即可载入。

报告导出示例：

```bash
# 为事件生成报告（可指定 diagnosis_id）
curl -X POST http://127.0.0.1:8000/api/v1/incidents/1/reports \
  -H 'Content-Type: application/json' -d '{}'

# 导出 Markdown / JSON
curl -OJ 'http://127.0.0.1:8000/api/v1/reports/1/export?format=markdown'
curl -OJ 'http://127.0.0.1:8000/api/v1/reports/1/export?format=json'
```

## 6. 评估指标

`make evaluate` 或 `POST /api/v1/evaluations` 会回放全部场景、执行诊断并与期望值比对：

| 指标 | 含义 |
| --- | --- |
| `top1_accuracy` | 根因类别 Top-1 命中率 |
| `top3_accuracy` | 根因类别 Top-3 命中率 |
| `node_accuracy` | 根因节点定位准确率 |
| `avg_latency_ms` | 平均诊断耗时 |
| `avg_compression_ratio` | 平均告警压缩比 |

评估模式固定随机种子与 Mock 模型，同一场景集重复执行结果一致，适合做 Prompt / 决策模型调优的前后对比。

### 6.1 决策模型调权

`config/decision_model.yaml` 定义故障类别、特征权重与错误签名正则。每个类别由若干特征项加权求和：

```yaml
categories:
  - category: db_connection_pool_exhausted
    label: 数据库连接池耗尽
    weights:
      rule_keywords: 1.2      # 规则名命中 "connection pool/too many connections"
      log_signature: 1.5      # 日志命中错误签名
      node_role_db: 0.8       # 根因候选节点是 db 类型
```

修改权重或正则后重启服务（或重新 `make seed`），`GET /api/v1/incidents/{id}/fast-diagnosis` 的候选排序会随之变化；建议先 `make evaluate` 记录修改前后的准确率，再决定是否采纳。

## 7. 测试

```bash
make test
# 或
PYTHONPATH=.deps:backend python3 -m pytest backend/tests -q
```

覆盖：接警校验与幂等、日志检索、降噪边界与生命周期、拓扑关联与根因排序、降级/脱敏/证据预算、报告版本与导出、场景回放与确定性评估、端到端链路。

## 8. 配置项（节选）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | 空（`data/aiops.db`） | SQLAlchemy 数据库 URL |
| `DEDUP_WINDOW_SECONDS` | 300 | 告警幂等去重窗口 |
| `CLUSTER_WINDOW_SECONDS` | 300 | 降噪聚类窗口 |
| `CLUSTER_COOLDOWN_SECONDS` | 600 | 聚合告警自动解决冷却时间 |
| `CORRELATION_WINDOW_SECONDS` | 180 | 拓扑关联时间窗 |
| `INCIDENT_COOLDOWN_SECONDS` | 600 | 事件自动解决冷却时间 |
| `LLM_MODE` | auto | auto / llm / mock |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | DeepSeek 默认 | OpenAI 兼容端点 |
| `EVIDENCE_MAX_ALERTS` | 20 | 送模核心告警上限 |
| `EVIDENCE_MAX_LOG_LINES` | 40 | 送模日志行上限 |
| `REVIEW_CONFIDENCE_THRESHOLD` | 0.45 | 低于该值标记人工复核 |
| `WEBHOOK_TOKEN` | 空 | 可选 Webhook 共享令牌 |

## 9. 故障排查

| 现象 | 处理 |
| --- | --- |
| 启动报缺少依赖 | 确认 `pip install -r requirements.txt` 或 `.deps` 存在；`make test` 可验证环境 |
| 首页提示"前端尚未构建" | `make build` 后重启后端（静态目录在启动时检测） |
| 诊断一直是 mock 结论 | 未配置 `LLM_API_KEY`，或 `LLM_MODE=mock`；查看 `/healthz` 的 `llm_mode` |
| 诊断一直 pending | 查看后端日志；单进程部署下任务为进程内 asyncio，重启会回收为 failed |
| 评估准确率偏低 | 检查场景 `expected` 与拓扑节点 id 是否一致；查看逐用例 `cases` 差异 |
| 告警没有合并 | 检查主机、归一化规则、时间桶是否一致；`GET /api/v1/clusters` 查看 `merge_reason` |
| 数据想重置 | 停止服务，删除 `data/aiops.db*`，重新 `make seed` |

## 10. 交付物

- 可运行后端（FastAPI + SQLite）与可选前端（Vue 3 + ECharts）
- 模拟源：`scripts/simulate_alerts.py`
- 故障场景库：`scenarios/`（6 个场景）
- 测试：`backend/tests/`
- 接口契约：[docs/api-contract.md](docs/api-contract.md)
- AI 复盘一页：[docs/retrospective.md](docs/retrospective.md)
- OpenSpec 变更与规格：[openspec/changes/add-aiops-alert-diagnosis-platform/](openspec/changes/add-aiops-alert-diagnosis-platform/)


## 11. 验收与实测基线

本仓库在演示环境（Python 3.14 + SQLite + Mock LLM）实测结果：

| 项目 | 结果 |
| --- | --- |
| 后端测试 | `80 passed`（含端到端集成测试） |
| 前端测试 | `13 passed`（API/格式化/7 页面冒烟） |
| 前端构建 | `pnpm build` 成功，FastAPI 静态托管可访问 |
| 突发演示 | 30 条告警 → 1 个事件（30× 压缩），诊断数十毫秒返回，报告 4 步 |
| 场景评估 | 6 个场景，Top-1 / Top-3 / 根因节点准确率均 1.0，平均诊断 ~9ms |
| 一键端到端 | `bash scripts/e2e_demo.sh` 退出码 0 |

> 准确率是内置场景的自洽结果，不代表真实机房泛化能力；详见 [docs/retrospective.md](docs/retrospective.md)。

### 验收清单

- [x] 可运行后端（FastAPI + SQLite）与前端看板（Vue 3 + ECharts）
- [x] Webhook 接警 + Python 模拟源（告警与带堆栈日志）
- [x] 按主机/时间窗/规则的告警聚类降噪
- [x] 拓扑感知关联，百条告警压缩为事件
- [x] 决策模型快速故障检测 + LLM 根因分析（可降级）
- [x] AI 自动生成分步排查与修复报告（Markdown/JSON 导出）
- [x] 告警列表、事件详情、拓扑与诊断可视化看板
- [x] 多故障场景库 + 诊断准确率量化评估
- [x] README、API 契约与一页 AI 复盘
