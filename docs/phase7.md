# Policy-Aware Agent Tool Gateway — Phase 7

新增 JIT Authorization：批准后签发短期、不透明、精确绑定操作的执行令牌。当前 138 项自动化测试通过，包含前六阶段回归、20 项 JIT 测试以及四个 MCP 工具过滤场景。

## Windows 升级与验收

把本包 agent-tool-gateway 内的内容覆盖原项目目录，保留 .env 和 .venv。

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m agent_gateway.jit_check --output results/local-phase7-check.json
```

无需 API Key，不产生模型费用。请提交新生成的 local-phase7-check.json；phase7-check.json 是包内已有的开发环境报告。

## JIT 设计

- 使用高熵随机不透明 token，数据库只存 SHA-256 摘要。token_id 可用于审计，不是执行凭证。
- 绑定 user_id、agent_id、tenant_id、task_id、session_id、tool、action、resource、完整参数摘要、原始参数约束、approval_id 和 policy_version。
- 完整参数精确绑定：金额增加或减少、币种、订单、退款原因变化都不能复用 token。
- TTL 允许 1–300 秒，实际过期时间同时受审批记录和会话有效期限制。审批记录从创建起也仅 5 分钟，因此迟批准不会自动得到新的完整 5 分钟窗口。
- 每个审批只能签发一个 token；撤销、过期、丢失之后不自动重签，需要新的请求与审批。
- 执行前重新验证用户/Agent capability、任务范围、资源归属、审批者、业务状态与策略版本。
- token active→consumed 与 approval approved→executing 在同一 SQLite 写事务内完成。失败校验回滚，令牌不会被部分消费。
- 成功执行后审批变 executed；令牌保持 consumed，作为不可重放的审计记录，不物理删除。执行失败或结果未知仍保持 consumed，审批标为 execution_uncertain，不盲目重试。

Service.call 的高风险恢复入口需要同时传 approval_id 与 jit_token。保留 Service.resume(approval_id) 的可信编排便利方法：内部先签发再立即消费，并非绕过 JIT。已签发 token 时，必须显式传入现有 token，不静默补发。

## 手动演示（新目录，全部模拟）

```powershell
.\.venv\Scripts\python.exe -m agent_gateway.approval_demo prepare --workdir results/local-jit-demo
.\.venv\Scripts\python.exe -m agent_gateway.approval_demo show --workdir results/local-jit-demo
```

查看订单、金额、币种、原因后，如决定批准，再执行：

```powershell
.\.venv\Scripts\python.exe -m agent_gateway.approval_demo approve --workdir results/local-jit-demo
.\.venv\Scripts\python.exe -m agent_gateway.approval_demo issue --workdir results/local-jit-demo
.\.venv\Scripts\python.exe -m agent_gateway.approval_demo inspect-token --workdir results/local-jit-demo
.\.venv\Scripts\python.exe -m agent_gateway.approval_demo resume --workdir results/local-jit-demo
.\.venv\Scripts\python.exe -m agent_gateway.approval_demo inspect-token --workdir results/local-jit-demo
```

预期：active → 模拟退款成功 → consumed。再次 resume 返回 JIT_NOT_ACTIVE。若要测试撤销，在执行前用 revoke-token，然后 resume 应被拒绝。另一轮使用新的目录。旧 Phase 6 待审批记录因 policy_version 改变会被拒绝，应重新创建请求。

演示为本地用户操作，reviewer 与 requester 是独立应用凭证，但同一操作系统用户可访问它们。execution.secret.json 保存本地原始 token，不输出到终端；所有 *.secret.json 已被 gitignore 排除，请勿上传或分享。数据库与审计不保存原始 token。

## 测试证据

results/phase7-check.json：138 项测试、0 失败、0 错误、0 跳过。
results/phase7-cli-smoke.json：脚本执行 CLI 的检查，不计作人工验收或真实模型测试。

测试包括无 token、伪造 token、TTL 边界、撤销、禁止重签、错误审批/工具/资源/Agent、金额替换、权限变化、重放、并发单次消费、事务回滚、持久化及令牌不泄漏。

## 仍然存在的边界

- 是审批/JIT 控制层和 Gateway 操作恢复，尚非完整 SDK RunState 恢复；原始令牌不进入模型上下文或工具业务参数。
- 保证同一审批/token 单次认领，不保证不同审批之间的相同业务请求去重。未实现端到端 exactly-once、业务幂等键或崩溃结果对账。
- 权限检查与业务写入之间仍有并发状态变化窗口；身份/审批/令牌库与业务库不是同一个事务。崩溃后可能停留 executing，需人工核对，不能直接恢复令牌。
- JITStore 与 ApprovalStore 都是本地可信内部接口；真正执行入口是 Service。没有 OS 隔离、生产认证、完整委托链或 Sandbox。
- 令牌签发与撤销事件写入 approval_events；执行审计关联 jit_token_id 和 approval_id。完整失败尝试审计留待 Phase 11。
- 本轮没有新的真实模型实验，测试报告不代表模型准确率或人工审核效果。

下一阶段：Phase 8 Delegation-Aware Authorization，将多跳委托范围逐级收窄并绑定到执行授权。
