# Policy-Aware Agent Tool Gateway — Phase 3

本阶段实现 Capability 数据模型、确定性匹配与持久化撤销。**匹配器尚未接入工具执行入口**，不能声称已拦截未经授权的业务操作。MCP 执行仍沿用 Phase 2 身份检查，审计中的 NOT_IMPLEMENTED_PHASE_2 表示执行入口尚无 Capability Policy。

## Windows 升级

将本包 agent-tool-gateway 内的内容复制到原项目目录，覆盖同名文件，保留 .env 和 .venv。

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m agent_gateway.capability_check --output results/local-phase3-check.json
```

无需 API Key，无模型费用。运行的是 Phase 1–3 的全部测试，包括真实 MCP 子进程回归。results/phase3-check.json 是开发环境实际测试记录；本地运行产生 local-phase3-check.json。

## Schema

CapabilityGrant：grant_id、subject_type(user/agent)、subject_id、tenant_id、action、resource_scope、conditions、not_before、expires_at、delegable、status、issued_by。

ResourceScope：resource_type + 精确 resource_ids 列表；不支持隐式通配符。
Conditions：可选 currency 与 max_amount_minor；金额上限必须明确币种。金额为最小货币单位整数，拒绝布尔值、字符串和浮点金额。当前金额条件只适用于 refund.issue。
CapabilityRequest：主体、租户、动作、资源类型与 ID，退款还需要金额和币种。Phase 4 应从可信上下文与已校验的业务参数构造请求，不能信任模型提供的主体或资源归属。

时间采用有限、非负的 UTC Unix 时间戳，生效区间为 [not_before, expires_at)，过期边界严格拒绝。匹配时间由可信调用端提供；测试使用固定时间以复现边界。

## 匹配与持久化

match_grant 返回 matched、reason_code 和 grant_id。这是授权条件匹配结果，**不是**最终 ALLOW/DENY/REQUIRE_APPROVAL 策略决策。

CapabilityStore 提供 add/get/revoke/match；SQLite 持久化，显式关闭连接。授权 ID 不允许重复覆盖。撤销只影响对应授权，不是对所有其他独立授权的全局拒绝。

每条 grant 必须独立满足所有条件，同一主体可有多条授权，但不能将 A 的资源范围与 B 的金额上限拼成更大权限。没有任何匹配 grant 时 matched=false。

主体注册与授权签发是可信管理端操作，本阶段 store 不验证签发者权限，也不自动创建/授予身份注册表中声明的 allowed_capabilities。delegable 仅保留字段，完整委托链在 Phase 8 实现。

## 例子

同一 agent 的退款授权限定 order_438、GBP、max_amount_minor=50000、有效期 300 秒：
- 对 order_438 退款 GBP 500.00：匹配。
- 对 order_999 退款 GBP 12.00：RESOURCE_MISMATCH。
- 对 order_438 退款 GBP 500.01：AMOUNT_OVER_LIMIT。
- 改为 USD：CURRENCY_MISMATCH。
- 到达 expires_at：GRANT_EXPIRED。
- 撤销：GRANT_REVOKED。

## 测试范围

28 项新增测试覆盖资源、动作、主体、租户、金额/币种、时间边界、撤销、非法类型、持久化、重复覆盖及多授权错误拼接；加上之前 29 项，共 57 项。测试量是功能覆盖数量，不是模型准确率或大规模安全结论。

历史记录保留在 docs/phase1.md、docs/phase2.md 和 results/ 中；此前真实模型验收仅适用于 Phase 1，不冒充本阶段结果。

下一阶段 Phase 4：把用户授权、Agent 授权及任务范围接入统一 Policy Decision Engine，并在真实 MCP 调用执行前强制检查。当前未进入 Phase 4。
