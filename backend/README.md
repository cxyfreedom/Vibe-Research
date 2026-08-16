# Vibe-Research Backend

A股数据层 + 可插拔 AI 层。全部只读、无状态；不预置任何标的、不推荐、不预测。

## 安装

```bash
docker compose up --build -d
docker compose logs -f backend
```

后端源码映射到容器 `/workspace/backend`，用户文件映射到项目 `data/user/`，
PostgreSQL 数据映射到 `data/postgres/`。修改源码会自动重载，修改依赖后需要重新构建镜像。

## 1. HTTP API（给网页前端 + 系统 AI）

宿主机访问地址为 <http://127.0.0.1:8900>。

| 端点 | 说明 | 依赖 |
|---|---|---|
| `GET /api/health` | 健康检查 | — |
| `GET /api/indices` | 大盘指数实时行情 | stdlib |
| `GET /api/quote?codes=600519,000858` | 实时行情（PE/PB/市值/涨跌停…） | stdlib |
| `GET /api/valuation?code=600519` | 完整估值（前向PE/PEG/消化年数） | requests+akshare |
| `GET /api/valuation/percentile?code=600519` | 估值历史分位（近5年·百度股市通） | akshare |
| `GET /api/financials?code=600519` | 财务关键指标（同花顺摘要，最新报告期，前端个股页用） | akshare |
| `GET /api/reports?code=600519` | 个股研报列表（含 PDF 链接） | requests |
| `GET /api/announcements?code=600519` | 近期公告（东财） | requests |
| `GET /api/news?code=600519` | 个股新闻 | akshare |
| `GET /api/kline?code=600519` | K线 | mootdx |
| — | *（AI 工具层走腾讯 K 线，mootdx 仅作备份：mootdx 是 TCP 7709，部分网络连不通要等十几秒超时）* | — |
| `GET /api/finance?code=600519` | 季报财务快照（mootdx，前端未用 / 备用） | mootdx |
| **资金面·筹码·信号（v3.3）** | `/api/margin` · `/block-trade` · `/holders` · `/dividend` · `/fund-flow` · `/dragon-tiger` · `/lockup` · `/blocks` · `/hot-concepts` · `/investor-qa` · `/industry` | requests |
| `GET /api/market/overview` · `/api/radar` | 市场情绪+板块资金 · 资讯雷达 | akshare / stdlib |
| `POST /api/chat` | 系统 AI 对话（function calling，AI 自己调数据工具） | requests |
| `POST /api/debate` | **多空辩论**（多 agent，流式 NDJSON）：事实底稿 → 多方 / 空方 →（可选反驳）→ 中立主持 | requests |
| `POST /api/reflect` | **反思审计**（流式 NDJSON）：对一段已写好的分析做推理审计 | requests |
| `GET /api/market-history/{kind}` | 市场快讯、热榜、资金流、行情池、技术扫描、板块、龙虎榜历史 | PostgreSQL |
| `GET /api/stocks` | PostgreSQL 股票列表（搜索 / 分页） | PostgreSQL |
| `GET /api/stock-data/{code}/history` · `/dates` | 个股 17 类日快照与可回溯日期 | PostgreSQL |
| `GET /api/system/collectors` | 采集任务运行状态与最近结果 | PostgreSQL |
| `POST /api/system/jobs/{job}/run` | 手动提交一次采集任务（异步） | PostgreSQL + akshare |

后台调度默认自动执行：资讯每分钟、热榜每半小时；交易时段资金流与东方财富概念板块每分钟；交易日 15:05 执行日终数据，16:00 检查漏采并补跑，18:30 采龙虎榜，18:45 补齐个股席位详情，19:00 断点归档全部个股页面数据。调度使用交易所日历和 PostgreSQL advisory lock，多进程不会重复执行同名任务。

`/api/debate` 请求体：`{"code": "600519", "rounds": 1, "llm": {...}}`（`rounds=2` 加一轮交叉反驳）。
事件类型：`status` · `dossier_progress`（底稿逐项进度）· `dossier` · `stage`（角色开始）·
`delta`（增量文本）· `stage_done`（角色完成，失败时带 `failed: true`）· `done` · `error`。

`/api/reflect` 请求体：`{"source": "待审的分析文本", "title": "可选标题", "llm": {...}}`。

> 两个端点都**不产出买卖结论**：辩论终点是「分歧点 + 验证清单」，反思终点是「怎么继续验证」。

> 上表为主要端点；完整路由清单见 `app.py`。要更全量的 A 股数据（打板 / ETF期权 / 全市场行业排名等），用根目录 [`a-stock-data/`](../a-stock-data/SKILL.md) 工具箱。

`/api/chat` 请求体：
```json
{
  "messages": [{"role": "user", "content": "茅台估值贵不贵？"}],
  "context": "本页上下文（可空）",
  "llm": {"baseURL": "https://api.deepseek.com", "apiKey": "sk-…", "model": "deepseek-chat"}
}
```
`llm` 由前端从本地配置随请求带上，后端不持久化 key。

## 2. MCP Server（给 Claude Code / 高手 agent）

零第三方依赖，复用同一套数据工具。挂进 Claude Code：

```bash
claude mcp add vibe-research -- \
  "$(pwd)/.venv/bin/python" "$(pwd)/mcp_server.py"
```

挂上后，你的 agent 直接拥有 `query_quote / query_valuation / query_reports / query_news` 四个工具，
用你自己的订阅额度调数据、多步分析——无需 API key、不占本产品成本。

### 完整 A 股数据工具箱（随仓库自带）

MCP 的 4 个工具是「零配置、开箱即用」的常用项。若 agent 需要更全的 A 股数据（龙虎榜 / 融资融券 / 大宗交易 / 股东户数 / 分红 / 资金流 / 解禁 / 概念板块 / 打板情绪 / ETF 期权 / 互动易 / 全市场行业排名 …共 **47 个端点**），本仓库根目录**自带完整数据源** [`a-stock-data/`](../a-stock-data/SKILL.md)（a-stock-data v3.6.0）：

- 要调哪个接口，直接看 [`a-stock-data/SKILL.md`](../a-stock-data/SKILL.md)——每个端点都有 copy-paste 即用的代码（内嵌全部调用逻辑，零第三方数据封装依赖，东财接口已内置限流防封）。
- 运行依赖：`pip install mootdx requests pandas stockstats`（自包含，v3.0 起已移除 akshare）。
- 上游与更新：[github.com/simonlin1212/a-stock-data](https://github.com/simonlin1212/a-stock-data)（不更新也能一直用，自带的是固定可用快照）。
- 分工：**MCP 4 工具** = 网页 / 轻量常用；**自带数据源 40+ 端点** = agent 深度自助调研的全量工具箱。二者同源，按需取用。

## 合规

- 数据端点只返回客观行情/研报/财报/新闻，不含任何建议、排名、预测。
- `/api/chat` 的 system prompt 内置中立红线：不荐股、不预测涨跌、不给买卖时机、不构成投资建议。
- 分析结论一律由用户配置的模型 / agent 给出，本产品只提供数据与工具。
