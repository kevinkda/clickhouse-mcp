# clickhouse-mcp

[![test](https://github.com/kevinkda/clickhouse-mcp/actions/workflows/test.yml/badge.svg)](https://github.com/kevinkda/clickhouse-mcp/actions/workflows/test.yml)

> [English README](./README.md)

一个**只读** [Model Context Protocol](https://modelcontextprotocol.io)（MCP）
服务器，查询美股 **ClickHouse** 数据仓库——**14.9 亿**行 1 分钟 K 线、覆盖
**1388 个标的**（1991–2026），外加 L1 多档预聚合与 L2 物化技术指标——并将其暴露给
支持 MCP 的 agent，用于**大规模、横截面量化分析**（单标的 REST MCP 做不到）。

它是 kevinkda MCP 生态中第 6 个只读服务器（schwab-marketdata / schwab-positions /
sec-edgar / polygon-news / yfinance）。它**独立成仓**：其认证方式（ClickHouse
host/user/password）与数据形态（列式深历史）与基于 REST 的兄弟仓差异很大。

## 工具（7 个，全部只读）

| 工具 | 用途 |
| --- | --- |
| `get_ohlcv` | 单标的 OHLCV K 线，支持 1m/5m/15m/1h/1d/1w |
| `get_indicators` | 单条物化/运行时指标序列（ma20、rsi14、macd_hist…） |
| `screen_stocks` | 全市场技术指标扫描（如"今日 RSI<30 超卖榜"） |
| `get_correlation_matrix` | 2–50 标的收益率相关性矩阵（Pearson / Spearman） |
| `run_safe_sql` | **默认禁用**的只读 SQL 逃生舱（仅单条只读 SELECT） |
| `health_check` | 本地探活 + 可选 ClickHouse 连通性检查 |
| `get_server_info` | 版本 + 工具列表（离线） |

## 快速开始

```bash
git clone https://github.com/kevinkda/clickhouse-mcp
cd clickhouse-mcp
uv sync --extra dev
cp .env.example .env   # 然后填入只读 ClickHouse 连接信息
```

在 `.env` 中配置连接（见[安全](#安全)）：

```bash
CLICKHOUSE_MCP_HOST=your-clickhouse-host
CLICKHOUSE_MCP_HTTP_PORT=8123
CLICKHOUSE_MCP_USER=mcp_readonly
CLICKHOUSE_MCP_PASSWORD=...
CLICKHOUSE_MCP_DATABASE=usa
```

运行：

```bash
uv run clickhouse-mcp
```

## 安全

本服务器处理**金融数据**，安全加固到位。详见 [`docs/SECURITY.md`](./docs/SECURITY.md)
与 [`docs/THREAT_MODEL.md`](./docs/THREAT_MODEL.md)。

- **独立只读 ClickHouse 账户**——`CLICKHOUSE_MCP_USER` 应指向一个以 `readonly = 1` +
  仅 `GRANT SELECT` 创建的账户，绝不能用 admin。纵深防御：每条查询**同时**带上
  ClickHouse 会话设置 `readonly=1` 以及 `max_execution_time` / `max_result_rows` 护栏。
- **参数化，绝不拼接**——`get_ohlcv` / `get_indicators` / `screen_stocks` /
  `get_correlation_matrix` 通过锚定正则 + 白名单严格校验每个 symbol / indicator /
  date，并作为 ClickHouse 查询参数绑定；用户输入绝不进入 SQL 字符串。
- **`run_safe_sql` 默认禁用**——需 `CLICKHOUSE_MCP_ALLOW_RAW_SQL=true` 显式开启；
  即便开启也强制单条只读 SELECT、拒绝 DDL/DML + 注释、强制 `LIMIT`，并继承 `readonly=1`
  + 资源护栏。
- **SSRF 安全**——ClickHouse host 在启动时从 env 读取，绝不由工具入参派生；任何参数都
  无法重定向出站连接。
- **凭证脱敏**——每个异常的文本都经过 `redact_secrets`，密码 / DSN 绝不会通过
  `repr(exc)` 或日志泄漏。

## 解锁的量化用例

CH 是"全市场 + 深历史 + 批量"引擎；REST MCP 兄弟仓是"实时 + 基本面 + 新闻"。二者闭环：

- **全市场技术扫描**——全 1388 标的批量 RSI/MACD/布林/均线信号。
- **横截面因子排名**——value/momentum/quality 跨 universe 横截面打分。
- **多标的相关性矩阵**——组合/配对前置；CH 批量读完胜逐个拉取。
- **跨源量化 playbook**——CH 历史信号 → schwab 实时价 → sec-edgar 基本面 → polygon 新闻。

## 开发

```bash
bash scripts/local-ci.sh   # ruff + mypy + bandit + pip-audit + pytest（100% 覆盖）
```

测试**绝不**连真实 ClickHouse——`clickhouse_connect` 全程 mock。

## 许可

MIT —— 见 [LICENSE](./LICENSE)。
