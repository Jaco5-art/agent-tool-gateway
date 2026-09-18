# Policy-Aware Agent Tool Gateway — Phase 8

新增 Delegation-Aware Authorization，并接入 Policy Engine、动态工具列表、审批和 JIT。多跳委托不会扩大有效权限。

## Windows 升级与验收

将包内 agent-tool-gateway 的内容覆盖原项目，保留 .env 和 .venv。

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m agent_gateway.delegation_check --output results/local-phase8-check.json
```

无需 API Key，不调用模型。测试包含前七阶段回归、25 项新增委托测试及四个动态工具场景。开发环境结果为 results/phase8-check.json；上传你新生成的 local-phase8-check.json 进行本地验收。

## 委托模型

DelegationLink 持久化字段：delegation_id、parent_id、issuer_id、subject_id、user_id、tenant_id、task_id、session_id、actions、resources、refund_limit_minor、refund_currency、not_before、expires_at、remaining_depth。

会话中的 delegation_leaf_id 来自可信上下文，不是模型工具参数。根链接由用户指向第一个 Agent；后续发行主体必须等于上一跳接收主体；末端主体必须与当前 Agent 一致。完整链从 leaf 逐级回溯，拒绝断链、记录循环、重复主体、任务/会话/租户不匹配及过长链。

默认无 delegation_leaf_id 的会话仍是用户直接委托单个 Agent 的旧模式；已有 Phase 1–7 演示继续工作。只有可信控制端可以创建这种会话，Agent 无法通过省略工具参数删除已有链。

## 权限规则

有效执行范围 = 用户当前授权 ∩ 用户任务范围 ∩ 每一跳委托范围 ∩ 链上每个 Agent 的权限上限和当前 capability。

- 子动作集合和资源集合必须是父集合子集。
- 退款币种不变，金额上限只能减小。
- 生效时间不能提前，到期时间不能延长。
- remaining_depth 必须递减，0 时禁止继续委托。
- 用户及非末端 Agent 必须持有匹配该调用且 delegable=true 的授权；末端 Agent 只需普通执行授权。
- 每次工具调用/可见性计算重新检查所有上游主体的状态、有效期及授权。父链接撤销、上游 Agent 停用或 capability 撤销均影响下一次执行。

链记录写入是本地可信控制操作：add 检查结构和父范围收窄；不会在签发时逐项验证所有业务授权组合。最终调用时对请求逐跳校验，不能把一条存在的链记录直接当作可执行授权。没有面向模型的自行签发委托工具。

## 审批与 JIT 绑定

审批记录与 JIT 令牌增加 delegation_leaf_id/链摘要绑定。链变更后即使新的范围仍允许本次动作，也不能复用旧批准或令牌；需要重新申请。链撤销或上游失权在执行前被拒绝，令牌不因此被消费。

已通过策略判定的审计事件含 delegation_chain ID 列表；所有调用保留可信 delegation_leaf_id，可回查持久链。损坏/撤销链被拒绝时不保证返回完整有效链列表，完整 provenance 展示留到 Phase 11。

## 实测内容

测试夹具为 User → Agent A → Agent B → Agent C；使用确定性身份与真实 MCP 子进程，不启动三个 LLM。

- 合法三 Agent 链可以读取工单，链 ID 写入审计。
- 全链仅允许 ticket.read 时，MCP 只暴露 ticket.read；直接调用隐藏 ticket.close 被拒绝，工单保持 open。
- 下游有完整能力也无法突破上游动作、资源或退款金额限制。
- 扩大动作/资源/金额/有效期或超出委托层数被拒绝。
- 断链、循环、跨租户、错误 issuer/task、上游禁用及过期被拒绝。
- 合法退款链经过批准与 JIT 后执行；签发后撤销父链或修改链，退款不执行。

测试数是功能覆盖，不是大规模越权阻止率或模型成功率。此前 45% 工具暴露减少仍只对应四个预设任务场景。

## 限制与后续

本地认证、可信控制端、数据库权限与之前各阶段相同。未实现生产 IdP、远程委托签名、跨进程不可抵赖证明或完整 SDK 多 Agent handoff。权限读取不是覆盖全部身份/授权表与业务写入的原子快照，TOCTOU 窗口仍存在；跨不同审批的业务幂等、崩溃对账尚未实现。

当前策略版本 phase8-v1；旧版待执行审批/JIT 会因版本不匹配拒绝，需重新申请。没有修改历史证据为新版本结果。

下一阶段：Phase 9 Sandboxed Execution。届时需要真实隔离执行环境；未验证隔离效果前不会把宿主机 subprocess 包装成 Sandbox。
