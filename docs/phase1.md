# Policy-Aware Agent Tool Gateway — Phase 1

面向 Agent Runtime / Tool Governance 的项目。当前仅完成最小 Agent + MCP Tool Layer，**尚未实现授权、动态工具过滤、JIT、审批、委托或 Sandbox**。所有客户、订单、退款和工单均为本地模拟数据，不连接企业 SaaS。

## 当前调用链

用户 → OpenAI Agents SDK → FunctionTool 薄适配器 → MCP Client → 独立 stdio MCP Server → SQLite 模拟业务。

适配器从 MCP tools/list 获取 schema，并调用 MCP tools/call，不在 Agent 侧直接执行业务函数。规范工具名为 `crm.get_customer` 等；模型侧使用 `crm__get_customer` 等明确映射，避免 API 工具名字符兼容问题。Phase 1 对所有工具可见，不把该适配器称为 Dynamic Tool Binding。

## Windows PowerShell 安装

建议使用 Python 3.12。在解压后的项目目录运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m agent_gateway.check --output results/local-mcp-check.json
```

无需激活虚拟环境，避免 PowerShell 执行策略问题。Linux/macOS 将上面的 Python 路径替换为 `.venv/bin/python`。

`pyproject.toml` 锁定直接依赖；`requirements-tested.txt` 记录本次完整测试环境的依赖版本。如需复现完整环境，先安装该文件，再 `pip install -e . --no-deps`。版本来自实际安装，不以年份冒充软件版本。

## 真实模型验收（需要 API Key，会产生 API 费用）

复制 `.env.example` 为 `.env`，本地填写 `OPENAI_API_KEY`。默认模型为 `gpt-5-mini`，可改为你账户可用的支持工具调用的模型。不要上传或发送密钥。

```powershell
Copy-Item .env.example .env
notepad .env
.\.venv\Scripts\python.exe -m agent_gateway.check --live --output results/local-live-check.json
```

该命令运行五类任务，每个任务使用独立临时数据库，检查实际调用记录及工单/退款业务状态。检查要求指定 ID、金额、币种和原因与任务一致，判定失败时查看 JSON。

单条交互：

```powershell
.\.venv\Scripts\python.exe -m agent_gateway.runtime "查询 customer_001 的客户信息"
.\.venv\Scripts\python.exe -m agent_gateway.runtime "读取 ticket_101，然后以 Resolved 为处理说明关闭它"
```

单条交互默认保留 `results/local-demo/` 数据，重复运行会保留工单关闭、退款余额等状态。想用新数据可指定新的 `--workdir results/local-demo-2`。自动验收则每次使用新数据。

## 五个工具

| Canonical MCP tool | 模型侧别名 | 功能 |
|---|---|---|
| crm.get_customer | crm__get_customer | 读取模拟客户 |
| ticket.read | ticket__read | 读取工单及版本 |
| ticket.close | ticket__close | 检查版本并关闭工单 |
| refund.issue | refund__issue | 记录模拟退款，检查币种和余额 |
| database.query | database__query | 预定义 orders_by_customer 参数化查询 |

金额为整数最小单位，GBP 12.00 = 1200 pence。工具输入拒绝多余字段、浮点金额和布尔金额。工单使用版本检查；退款采用 SQLite 写事务避免并发超退。这些属于业务正确性检查，**不是用户权限控制**。

每个工具提供输入输出 schema，以及 action、resource_type、risk_level 和 required_capability 元数据。元数据当前不参与权限判定。

## 测试证据与边界

- `results/unit-tests.txt`：业务与 MCP/SDK 适配测试的真实输出。
- `results/mcp-check.json`：五类真实 MCP 协议调用，含协议协商结果和业务输出。
- `results/live-check.json`：当前环境真实模型验收状态。缺少 API Key 时记录 `not_run`，不会用模拟模型冒充。
- 尚不能声称 Phase 1 的“模型识别意图、选择工具、生成参数”验收通过，直到本地 live check 通过。

本次 MCP SDK 为 2.2.0；使用 initialize 兼容路径，实际协商协议记录为 `2025-11-25`。这不是声称实现全部 2026 协议特性。

审计记录 request_id、session_id、工具名、参数 hash、结果状态、耗时和时间戳。授权字段明确记录 `NOT_IMPLEMENTED_PHASE_1`；不记录完整参数、API Key 或退款原因原文。未知工具及参数错误也记录失败。SDK hosted tracing 默认关闭。

剩余限制：没有身份认证和租户隔离；没有退款幂等键，重复提交合法退款可能重复记账；本地 JSONL 审计不具备崩溃恢复和防篡改保证；模拟后端不适用于生产。后续按阶段补齐，而非在 Phase 1 宣称已具备。

## 后续阶段

2 Identity → 3 Capability → 4 Policy Engine → 5 Dynamic Tool Binding → 6 Approval → 7 JIT/幂等 → 8 Delegation → 9 Sandbox → 10 对照与注入测试 → 11 完整审计与 Demo。

每阶段：设计 → 实现 → 测试 → 验收。当前不进入 Phase 2。

## 参考

- OpenAI Agents SDK: https://openai.github.io/openai-agents-python/
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk
