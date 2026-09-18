# Policy-Aware Agent Tool Gateway — Phase 2

当前完成：Phase 1 五个模拟 MCP 工具 + Phase 2 Agent Identity Registry。

## 升级（Windows）

将此包中的 `agent-tool-gateway` 文件夹内容复制到原项目文件夹，覆盖同名源码和测试。包中不含 `.env` 或 `.venv`，保留原来的 Key 和环境。不要复制到 src 内部。

在含 pyproject.toml 的项目目录运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m agent_gateway.identity_check --output results/local-phase2-check.json
```

此验收无需 API Key，不产生模型费用。把 `results/local-phase2-check.json` 作为本地验收证据。

新目录安装：先运行 `py -3.12 -m venv .venv`。直接依赖未变，版本锁定在 pyproject.toml。

## 本阶段设计

- AgentIdentity：独立 agent_id、owner_id、tenant_id、purpose、状态、能力和资源范围声明、创建与过期时间。
- ExecutionContext：user_id、agent_id、tenant_id、task_id、task_scope、session_id、认证时间和会话有效期。
- SQLite 注册表持久保存主体与会话；随机会话令牌仅保存 SHA-256 摘要。
- 本地可信启动器创建模拟身份，并将会话令牌通过子进程环境交给 MCP Server；令牌不在模型上下文、工具 schema 或审计中。
- Service 必须接收注册表和令牌，缺少上下文时拒绝创建。每次 tools/call 都解析注册表并重新检查状态、有效期及租户一致性。
- 未知、过期、停用或撤销的身份不能执行业务。工具参数试图指定 user_id/agent_id 会因额外字段被拒绝。
- 审计包含身份、任务、会话、task_scope、identity_status；基础权限判定字段仍明确为 NOT_IMPLEMENTED_PHASE_2。
- 已合并 Windows SQLite 连接修复，连接在事务退出后显式关闭，并有连接关闭回归测试。

## 边界

这是本地模拟认证。启动器、注册表文件和宿主机属于可信边界；可修改这些文件的人可以修改身份，不声称有生产级认证隔离。演示启动器每次创建新的模拟主体，避免覆盖已有身份或复活已撤销会话。注册表本身可复用持久主体。

allowed_capabilities、allowed_resource_scope 和 task_scope 当前只是记录，不执行能力/资源授权。租户检查仅检查身份上下文一致性；业务表仍是单租户模拟数据。没有实现委托链、审批、JIT、Sandbox 或事务级审计。身份检查和业务写入尚非一个原子事务，不能声称解决并发撤销窗口。

## 测试结果

`results/phase2-check.json`：本轮本地身份、业务回归与真实 MCP 测试。
`results/phase2-mcp-check.json`：五类真实 MCP 调用及带身份的审计。
`results/phase1-live-user.json`：用户此前提交的 Phase 1 真实模型结果（历史证据，不能当成 Phase 2 模型结果）。

默认 Phase 2 验收不重复花费 API 费用。若要单独运行带身份的模型链路：

```powershell
.\.venv\Scripts\python.exe -m agent_gateway.check --live --output results/local-phase2-live.json
```

模型入口、五个业务工具和 .env 配置方式参见 docs/phase1.md（历史 Phase 1 文档）。当前源码版本以本 README 为准。

下一阶段：Phase 3 Capability 数据模型与匹配；尚未进入。
