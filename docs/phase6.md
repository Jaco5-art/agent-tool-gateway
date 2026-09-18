# Policy-Aware Agent Tool Gateway — Phase 6

新增持久化审批记录、独立审批者凭证、批准/拒绝入口，以及批准后的 Gateway 操作恢复。全部为本地模拟业务。

## Windows 升级与验收

将包内 agent-tool-gateway 的内容覆盖到原项目目录，保留 .env 和 .venv。

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m agent_gateway.approval_check --output results/local-phase6-check.json
```

验收包括前五阶段回归、审批测试与四个工具过滤场景。无需 API Key，没有模型费用。自动化测试中的审批者是测试夹具，不算真实人工审批。results/phase6-check.json 为开发环境结果；results/phase6-cli-smoke.json 为脚本驱动 CLI 检查。

## 手动演示（可选）

所有命令运行在项目目录。prepare 创建一个 GBP 12.00 模拟退款的待审批记录，并显示完整工具参数、身份、审批 ID 与有效期。

```powershell
.\.venv\Scripts\python.exe -m agent_gateway.approval_demo prepare
.\.venv\Scripts\python.exe -m agent_gateway.approval_demo show
```

查看 show 中的订单、金额、币种及原因后，由你手动选择批准或拒绝。批准示例：

```powershell
.\.venv\Scripts\python.exe -m agent_gateway.approval_demo approve
.\.venv\Scripts\python.exe -m agent_gateway.approval_demo resume
.\.venv\Scripts\python.exe -m agent_gateway.approval_demo show
```

最后状态应为 executed，result 为 simulated 退款。重复 resume 将返回 APPROVAL_NOT_EXECUTABLE，不能重复执行同一审批。

若选择拒绝，用 reject 替代 approve；之后 resume 应被阻止。审批记录默认 5 分钟过期（且不超过会话有效期），过期不能执行。需要重新演示时给每条命令添加相同的新目录，例如 --workdir results/local-approval-demo-2；不要覆盖旧记录。

演示会生成 requester.secret.json 和 reviewer.secret.json，分别代表请求会话和审批者凭证；请勿上传。它们已加入 .gitignore。本地同一个操作系统用户可读两者，因此这是身份隔离流程演示，不是实际多用户认证或生产安全隔离。

## 执行规则

1. 原始调用先通过当前身份、用户/Agent capability、任务范围、资源归属及业务预检查。
2. 只有 REQUIRE_APPROVAL 创建 pending 记录并返回 approval_id。DENY 不生成可批准请求。
3. 审批入口验证独立 reviewer token、有效期、启用状态、租户、动作与资源范围。请求用户和 Agent 不允许自批。
4. approve 只将记录改为 approved，不执行退款；reject 为终态。
5. Service.resume(approval_id) 读取原始参数，经同一个 Service.call 再次验证身份、权限与业务状态，再核对审批绑定的用户/Agent/任务/会话、完整参数摘要和策略版本。
6. 在 SQLite 写事务内原子认领 approved → executing，成功后 executed。同一审批并发恢复只有一个能认领。
7. 执行异常标记 execution_uncertain，禁止自动重试；进程崩溃留在 executing 时也不自动重试，以免重复副作用。

审批绑定完整参数，包括 reason；批准后修改金额、订单或原因都不能复用批准。审批者被停用或过期，恢复也拒绝。

## 存储与审计

身份数据库新增 reviewers、approvals、approval_events；仅存审批者凭证摘要。审批记录保留恢复所需的完整模拟参数，不存请求会话 token；生产接入时需另设敏感参数保留/加密策略。

执行审计关联 approval_id、policy_decision、reason_code 和 execution_status。审批事件表记录 requested/approved/rejected/executing/executed 等状态变化及审批者 ID。未实现完整失败审批尝试日志或防篡改审计。

## 当前边界

- 这是 Gateway 业务动作恢复，不是 OpenAI Agents SDK RunState 会话暂停/恢复。模型收到 pending 后停止；独立本地控制入口完成审核和恢复。尚未运行真实 LLM→人工批准→SDK 恢复的端到端实验。
- 五个 MCP 工具仍只提供业务操作；没有把批准或审批者凭证暴露给模型。单独的 Service.resume 是本地可信控制接口，并要求原请求会话匹配。
- 尚无 JIT 执行 token、完整请求幂等或崩溃后结果对账。当前单次认领只防止同一 approval_id 重复执行；再次提出相同请求会得到新的审批记录，不能宣称跨请求 exactly-once。
- 审批记录与业务库并非一个原子事务。权限检查和执行之间仍存在并发撤销窗口；审批者初始化是可信管理员操作，未对接生产 IdP。
- 风险等级沿用服务端工具定义，当前实测 LOW/MEDIUM/HIGH。未加入 CRITICAL 导出工具及参数驱动的风险升级规则。
- 历史 docs/ 与 results/ 保留各阶段证据，当前行为以本 README 为准。

下一阶段 Phase 7：JIT Authorization（短期、精确范围、单次使用的执行授权与更完整幂等机制）。
