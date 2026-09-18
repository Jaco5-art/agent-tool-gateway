# Policy-Aware Agent Tool Gateway — Phase 4

完成 Policy Decision Engine 并接入真实 MCP tools/call 入口。所有公开 MCP 工具均经过身份、参数、资源归属、用户授权、Agent 授权、任务范围、业务条件与风险检查。

## Windows 升级及验收

把本包 agent-tool-gateway 文件夹内的内容复制到原项目目录，覆盖同名文件。保留 .env、.venv；不需要删除旧数据库，新验收使用独立临时数据库。

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m agent_gateway.policy_check --output results/local-phase4-check.json
```

本轮无 API 费用。开发环境 74 项测试通过，包括 17 项新增策略/真实 MCP 测试和此前回归。请在 Windows 上复验并查看结果 JSON。

可额外运行五类 MCP 检查（同样不调用模型）：

```powershell
.\.venv\Scripts\python.exe -m agent_gateway.check --output results/local-phase4-mcp.json
```

## 三种决策

| 决策 | 实际执行 |
|---|---|
| ALLOW | 执行业务，记录输出/失败 |
| DENY | 不调用业务执行方法 |
| REQUIRE_APPROVAL | 不调用业务执行方法；尚无审批恢复功能 |

合法读取和关闭工单可执行。默认模拟退款需要审批：基础权限通过后返回 REQUIRE_APPROVAL，退款表与退款余额均保持不变。基础权限不通过时直接 DENY，不进入审批。工具参数添加 approved=true 无法绕过检查。

**行为变更：Phase 1 的直接模拟退款不再执行，这是预期变化。** check.py 已将退款验收改为检查待审批且零副作用。历史报告不代表当前退款行为。不要用删除权限检查的方式让历史预期重新通过。

## 执行链

可信会话 → 身份校验 → 静态工具 schema 校验 → 服务端资源归属查询 → 用户与 Agent capability 匹配 → Agent 上限与任务范围检查 → 业务预检查 → 风险决策 → 仅 ALLOW 执行 → 审计。

Agent allowed_capabilities / allowed_resource_scope 现在作为权限上限生效。会话增加 task_actions、精确 task_scope、task_refund_limit_minor 和 task_refund_currency。每次执行读取当前授权，因此撤销与过期会影响下一次调用。

资源归属来自 resource_owners 表，不信任模型声明的 tenant。database.query 的权限资源绑定为 orders_customer_001 之类的每客户数据集，防止通过修改 customer_id 扩大查询范围。既有模拟数据库初始化时为已知固定演示资源补充归属记录；未知资源不会自动获得归属或权限。

本地启动器仍是显式演示配置，创建用户、Agent、任务范围和各自 grant。没有生产身份提供者、管理后台或签发者权限校验。手动构建的旧会话若缺少 task_actions 等字段会按默认空范围拒绝，不隐式补权。

## 审计

记录 user_id、agent_id、tenant_id、task_id、session_id、task_scope、参数摘要、policy_version、policy_decision、reason_code、risk_level（完成策略判定时）、匹配授权 ID 及 execution_status。

拒绝与待审批均记录 blocked。ALLOW 后业务失败记录 failed，不把执行失败当作授权拒绝。身份/参数/业务前置校验失败记录 DENY。未知策略异常不进入执行，MCP 返回错误；不会在策略异常时默认允许。

## 测试与限制

results/phase4-check.json 为全部测试记录；results/phase4-mcp-check.json 为真实 MCP 五类调用输出。新增测试验证缺用户/Agent授权、任务范围、金额上限、过期授权、跨租户资源、查询资源替换、伪造批准参数、策略异常及 ALLOW/DENY/REQUIRE_APPROVAL 的实际副作用。

此前业务层退款单元测试仍直接测试模拟后端的余额与事务正确性，不代表 Gateway 允许退款。没有提供公开的后端绕过端点；持有宿主机代码/数据库访问权限的本地用户仍属于可信边界，可以直接操作后端，项目不声称进行 OS 级隔离。

当前仍存在权限检查与业务写入之间的并发状态变更窗口，未实现事务级授权快照、完整委托链、持久审批队列、审批恢复、JIT 或 Sandbox。REQUIRE_APPROVAL 仅是停止执行的决策，不能描述为完整人工审批系统。只覆盖当前五个工具及 LOW/MEDIUM/HIGH 场景，CRITICAL 工具尚未加入。

本轮未重新运行真实模型；可用旧入口 --live 自行做单独的付费验收，退款将按待审批判定。历史真实模型报告只适用于对应历史版本。

下一阶段为 Phase 5 Dynamic Tool Binding；当前仍向 Agent 暴露五个工具，执行授权与工具可见性分开处理。
