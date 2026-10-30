# Mercury Stock Agent

按 [POC 施工手册](personal_trading_agent_poc.md) 实现的美股与 ETF 个人决策辅助服务。Python 3.12、FastAPI、OpenAI Agents SDK、SQLAlchemy/Alembic、APScheduler、TA-Lib、PostgreSQL。

**当前可运行：离线合成数据流程、本地 Qwen 模型接入、四类策略、确定性风控、管理 API、定时与事件通知、审计、前瞻性收益跟踪。** Schwab 实际授权、真实账户对账、生产服务器部署和策略有效性评估需要真实配置与后续观察，未被离线测试替代。详见 [验收记录](docs/acceptance.md)。

系统不包含提交、撤销、修改订单的实现。模型不能修改风险配置。任何缺失、过期或异常数据都会阻止依赖该数据的交易动作。

## 本地启动（Windows）

```powershell
.\scripts\bootstrap.ps1
.\.venv\Scripts\trading-agent.exe demo
.\.venv\Scripts\trading-agent.exe doctor
```

首次启动时复制 `.env.example` 为 `.env`，并设置 `ADMIN_API_KEY`（至少 32 个随机字符）：

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

```powershell
.\.venv\Scripts\trading-agent.exe serve
```

默认监听 `http://127.0.0.1:8000`，健康检查 `/health` 可公开读取；所有账户、分析、决策和任务接口需要 `Authorization: Bearer <ADMIN_API_KEY>`。未配置密钥时拒绝访问管理接口，接口文档默认关闭。服务启动前必须执行 `init-db`，不在启动时隐式建表。

## 本地 LLM

模型适配器支持 Chat Completions / Responses；当前实测采用本机 llama.cpp 的 Qwen3.8-27B：

```dotenv
LLM_BASE_URL=http://127.0.0.1:8081/v1
LLM_MODEL=qwen3.8-27b-q5_k_m
LLM_API_KEY=<在本地填写>
LLM_ENABLED=true
```

```powershell
.\.venv\Scripts\trading-agent.exe probe-llm --output reports/local/llm-compatibility.json
.\.venv\Scripts\trading-agent.exe demo --llm
.\.venv\Scripts\trading-agent.exe report --output reports/local/daily.md
```

业务流程将只读风险工具调用和最终 JSON Schema 输出分成两个受控 SDK 步骤，避免当前 llama.cpp 同时启用两种语法时的错误，也避免无边界工具循环。`probe-llm` 另行验证连续两轮工具调用、实际参数传递、JSON Schema 和文本流式响应。业务使用非流式工具调用，不宣称流式工具分片已经验证。SDK Trace 上传默认关闭。

模型只选择有效证据 ID 和可验证的解释代码。最终报告中的金额、数量、价格与动作均由程序提供，模型文本不能创建新交易条件。输出解析失败、证据未知或与风控冲突时记录 `NO_ACTION`。

## 配置与真实数据接入

- `config/risk.yaml` 的百分比使用小数（`0.01` 表示 1%），`null` 表示未确认。不会自动代填账户风险偏好。
- `config/portfolio.yaml` 的 GOOG、QQQ 是初始测试观察名单，SPY 用于市场状态；真实账户持仓自动加入扫描。新标的必须明确配置资产类型、是否杠杆及行业，缺失时不能新增风险。
- Demo 中 GOOG 10 股、成本 289.60；QQQ 20 股、成本未知。现金和行情均为 `SYNTHETIC_FIXTURE`，不能作为账户事实。
- 真实数据模式设置 `DATA_MODE=live`；建议使用独立数据库与数据目录，避免混合 demo 和真实快照。
- 设置 Schwab 应用 ID、Secret、Redirect URI、Token 加密密钥。加密密钥用 `Fernet.generate_key()` 生成。
- 登录开发者门户核实权限、Refresh Token 期限和历史行情复权口径，分别填写 `SCHWAB_REFRESH_TOKEN_MAX_AGE_SECONDS`、`SCHWAB_HISTORY_ADJUSTMENT`。默认不猜测期限及口径。
- 执行 `trading-agent schwab-auth`，在浏览器完成授权，再在隐藏输入框粘贴完整回调 URL。校验 state、回调地址与十分钟时限，Token 加密落盘，刷新轮换不自动延长首次授权期限。
- 运行 `trading-agent sync` 检查账户同步，再运行 `trading-agent run-daily`。

Schwab Connector 只使用 GET 读取交易与行情端点，OAuth Token 交换单独使用 POST。应用在代码层限制只读，但不能代替开发者门户的账户权限配置。账户编号和账户 hash 的映射只存于加密文件，不写普通日志或 API 响应。

实际财报/新闻供应商尚未配置，事件风险明确为 UNKNOWN；开启财报屏蔽时会阻止新增仓位。持仓补仓次数、保护止损、行业、高水位基准等未知信息同样阻止依赖它们的精确仓位建议。当前多账户可同步，但精确下单账户分配尚未实现，统一降级为人工复核。减仓、清仓只作人工复核，不猜测清算数量。

Schwab 账户市值、现金与总权益进行对账，成本基础、税务成本与手工成本保留独立字段。当前订单和成交读取窗口为 60 天，真实接入前需核验是否存在更早仍有效的订单；订单覆盖范围未确认时，`ORDER_COVERAGE_UNVERIFIED` 阻止动作建议。项目暂未声称完成真实券商验收。

确需补充券商未提供的风险事实时，可在本地 `data/risk-context.json` 中明确记录 `account_ref`、`actor`、`reason`、`as_of`、`valid_until`、`peak_equity`、`order_coverage_complete` 及 `holdings`。每个持仓条目包含 `quantity`、`sector`、`protective_stop`、`add_count`、`manual_average_cost`。数量仅用于对账，不覆盖券商数量；手工成本保留独立字段。该文件必须由用户确认真实值，时间过期、账户不匹配或持仓变化即失效。每次变更保存修改人、原因、时间及前后值，Agent 无权写入。所有字段定义见 `portfolio/risk_context.py`。

## 管理 API

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/health` | 存储健康、数据模式 |
| GET | `/readiness` | 未配置风险项与连接状态 |
| POST | `/portfolio/sync` | 只读同步账户 |
| GET | `/portfolio/snapshot` | 最近账户快照 |
| GET | `/market/{symbol}` | 带来源、时间戳的行情 |
| GET | `/analysis/{symbol}` | 日线、小时线分析 |
| GET | `/signals/{symbol}` | 四类策略最近信号 |
| GET | `/decisions/latest` | 最近决策 |
| GET | `/decisions/{id}/audit` | 配置、数据与决策证据 |
| POST | `/jobs/run-daily` | 日常工作流 |
| POST | `/jobs/run-events` | 事件检查 |
| POST | `/feedback` | 采纳、拒绝、修改记录 |

反馈示例：`{"decision_id":"...","response":"REJECTED","note":"..."}`。反馈作为审计数据，不会直接更改账户持仓或风险规则。

## 分析、调度与审计

日线和小时线均计算 SMA、EMA、RSI、MACD、ATR、布林带、相对 SPY 强弱和成交量比率；盘中 VWAP 按纽约交易日重置。指标参数、代码指纹、依赖版本、完整历史序列、市场状态、原始信号、风险结果和配置快照随运行保存。K 线时间统一表示**已完成周期的结束时刻**；小时线从开盘锚定，半日市与末尾半小时由交易日历决定。

S01 趋势、S02 突破、S03 回调、S04 均值回归分别独立运行。先过滤适用环境，再按固定策略 ID 选择同向候选；相反信号输出 REVIEW，不做多数投票。策略强度不代表胜率。市场状态采用 SPY 趋势及 ATR 波动率代理，广度数据不可用时明确标记，不伪装成完整市场评估。

`SCHEDULER_ENABLED=true` 启用交易日历调度。盘前、盘后、每周最后一个交易日的复盘时间由实际交易时段推导；盘中每十分钟只检查确定性事件，状态变化且符合冷却规则才调用模型。PostgreSQL advisory lock / SQLite 文件锁阻止多进程重复工作；Docker 单 worker 运行。通知默认仅保存到数据库并输出不含账户细节的控制台摘要，没有连接第三方发送渠道。

通知 outbox 与决策在一个事务中保存，失败重试、每个 Provider 交付状态、稳定去重键都有持久化记录。未来外部 Provider 应使用 `notification_id` 作为幂等键；外部发送和本地提交无法保证网络故障时的严格 exactly-once。发送成功也不等同于用户已阅读。

## Docker / PostgreSQL

```bash
# 先填写 .env，包括 ADMIN_API_KEY 与 POSTGRES_PASSWORD
docker compose up -d --build
docker compose logs --tail=100 app
curl http://127.0.0.1:8000/health
sh scripts/backup.sh
sh scripts/verify-restore.sh backups/<备份文件>.dump
```

数据库不映射主机端口，管理 API 只映射回环地址。跨服务器访问使用受控网络或 HTTPS 代理；单独保护 `.env`、数据库备份和加密密钥。备份脚本可纳入服务器 cron；恢复检查使用独立数据库，不覆盖生产数据库。开启主机 NTP。日志自动轮转，容器自动重启。

Docker 容器内的 `127.0.0.1` 指向容器自身；连接宿主模型需显式调整 `LLM_BASE_URL`（Windows Docker Desktop 常用 `host.docker.internal`，Linux 需配置主机网关或内网域名），并为该通道配置鉴权。

## 验证与前瞻性评估

```powershell
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\trading-agent.exe paper-evaluate --cost-bps 0 10 25
```

`--cost-bps` 是显式输入的研究假设，单位为双边总成本基点，不是已核实的实际费用。前瞻性模块从信号之后的下一交易日开盘建立虚拟参考，计算 1/3/5/10 个交易日后的净收益、MAE/MFE、正收益比例及收益损失比，按市场状态分组；不会把重叠收益样本当作独立成交或伪造组合回撤。无后续行情时返回 `INSUFFICIENT_FORWARD_DATA`。完整样本外策略评估、真实成交归因与组合权益曲线仍需要持续收集数据。

CI 使用 Linux、Python 3.12 与独立 PostgreSQL 服务执行测试。Windows 本地若没有 `TEST_DATABASE_URL`，PostgreSQL 集成测试会明确跳过。

## 参考

- [OpenAI Agents SDK 模型与 Provider](https://developers.openai.com/api/docs/guides/agents/models)
- [Schwab Developer Portal](https://developer.schwab.com/products/trader-api--individual)（账户专属 API 规则需登录核验）
- [APScheduler 3.x](https://apscheduler.readthedocs.io/en/3.x/userguide.html)
- [exchange_calendars](https://github.com/gerrymanoim/exchange_calendars)
- [TA-Lib Python](https://ta-lib.github.io/ta-lib-python/)
