# POC 实施与验收记录

日期：2026-10-30。依据：`personal_trading_agent_poc.md` V1.0。仓库：`MercuryYang/Mercury_Stock_Agent`。

## 已实现与本地验证

| 施工阶段 | 已落地内容 | 验证方式 |
|---|---|---|
| Phase 0 | 锁定 SDK，Chat Completions 自建模型适配，工具和 JSON Schema 分步执行 | 本地 Qwen3.8-27B 实际对话、两轮工具调用、参数校验、独立 JSON Schema、文本流式通过 |
| Phase 1 | 配置、加密 OAuth、只读 Schwab 连接器、账户对账、行情标准化、20 张业务表与 Alembic | Mock HTTP、Token 刷新轮换、账户匿名映射、SQLite 迁移及重开、故障降级测试 |
| Phase 2 | 日线/小时线 TA-Lib 指标、支撑阻力、市场状态、S01–S04 策略 | 独立数学参考、数据质量边界、四策略固定样例 |
| Phase 3 | Position/Portfolio/Execution 风控、白名单工具、Schema 复核、证据链 | 超限拒绝、风险金额/数量、无鉴权阻断、无下单能力、非法模型输出与注入拒绝；真实模型业务流程通过 |
| Phase 4 | NYSE 日历、半日市/DST、定时/事件流、冷却与去重、outbox、Compose、备份/恢复脚本 | 日历、调度幂等、通知故障重试、事件升级/解除、API 集成测试 |
| Phase 5 | 用户反馈、1/3/5/10 日前瞻性 markout、成本敏感性、市场状态分组 | 下一交易日开盘执行基准测试；无真实样本时明确输出不足 |

使用 Python 3.12.9；`uv.lock` 锁定全部传递依赖。核心实测版本：OpenAI Agents SDK 0.20.0、OpenAI Python 2.54.0、TA-Lib 0.6.8、pandas 2.3.3、exchange_calendars 4.13.2。

测试命令：`ruff check .` 与 `pytest -q`。Windows 本地验证为 64 项通过、1 项 PostgreSQL 测试因缺少服务而跳过；推送后 CI 另行验证 Linux 与 PostgreSQL。Windows 本机无 Docker、PostgreSQL 可执行程序；CI 提供独立 PostgreSQL 服务，并验证 Compose 配置和构建镜像。备份/恢复脚本没有在本机 PostgreSQL 上实测。

本机真实模型：`qwen3.8-27b-q5_k_m`，回环端口 8081。`reports/local/llm-compatibility.json` 保存探测结果；`reports/local/daily.md` 是合成数据示例报告。账户密钥、模型密钥、数据库和报告均被 Git 忽略。本地服务的启动参数和 PID 留在 `logs/`、`data/`，不推送。

## 重要实现选择

1. 当前 llama.cpp 同时使用工具语法与 JSON Schema 时返回 `Failed to initialize samplers: failed to parse grammar`。因此业务 Analyst 先读取风险工具，再在无工具的请求中输出严格 Schema。实测两个标的均为 `generator=agents_sdk`，没有用规则降级结果冒充模型成功。
2. 自由工具循环曾出现 MaxTurnsExceeded。最终采用有界两步执行；任一阶段失败或输出违背硬约束都会弃用模型结果，记录原因与 NO_ACTION。
3. LLM 只选择已有解释代码、置信等级和证据引用；报告中的价格与数量来自确定性计算。发布前再执行一次实时风险/时效校验，防止模型响应期间行情过期。
4. 关键风险参数全部保留 null。测试里的风险阈值只存在于测试夹具，未作为真实账户配置。
5. 历史 K 线按结束时间归一化；小时线使用 30 分钟数据按交易日开盘聚合，缺失子 K 线直接拒绝。未知复权口径不生成动作建议。
6. 数据源失败时也保存固定 Decision Schema、诊断行情快照及拒绝风险记录，证据链不会因缺失 quote 而消失。
7. 真实风险补充信息仅从用户确认的本地 `risk-context.json` 读取，绑定账户、持仓数量及有效时间，修改前后值审计留存。

## 尚不能勾选的外部验收

- **Schwab 真实授权及 GOOG/QQQ 对账**：未提供已获批应用、账户授权及真实成本/余额。现有测试不是实际券商成功证明。
- **Schwab 当前权限、Token 期限、复权口径及订单历史覆盖**：门户公开页面无法读取账户专属文档，待登录核验。60 日窗口之外的有效订单未知时，风险闸门保持关闭。
- **正式风险偏好与事件数据**：用户尚未配置风险阈值，财报/新闻 feed 仍为 UNKNOWN。新增仓位保持 NO_ACTION。
- **服务器常驻生产部署及备份恢复演练**：Compose、健康检查、单实例锁、日志轮转及恢复脚本已提供；目标服务器与访问条件未指定，本机未安装 Docker。
- **策略有效性、样本外与前瞻性观察**：没有足够真实未来样本，未宣称收益、胜率、实际组合回撤或适合实盘。当前评估属于信号参考收益跟踪，非完整成交回测引擎。
- **最终通知渠道**：按手册实现 Console / Database，未向 Telegram、微信、邮件或其他接收者发送消息。

这些项目是可运行 POC 到真实账户验收的剩余条件，不影响离线运行、接口测试与本地模型接入。
