# Policy-Aware Agent Tool Gateway — Phase 5

完成 Dynamic Tool Binding：MCP tools/list 根据可信身份、Agent 上限、任务动作/资源范围、用户与 Agent 有效 capability 过滤工具。tools/call 继续经过 Phase 4 独立授权，列表过滤不替代执行控制。

## Windows 升级

将本包 agent-tool-gateway 文件夹内的内容覆盖到原项目目录，保留 .env 和 .venv。

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m agent_gateway.binding_check --output results/local-phase5-check.json
```

该命令运行 90 项测试与四个真实 MCP 场景，不调用模型、无需 Key。开发环境实际结果在 results/phase5-check.json；你的本地结果在 results/local-phase5-check.json。

## 行为

- 每次 MCP tools/list 重新解析会话和有效授权，无长期 manifest 缓存。
- 工具只有在某个当前任务资源上存在满足基础授权条件的调用时才显示。
- 需审批的高风险工具保留可见性，调用时仍 REQUIRE_APPROVAL，不执行业务。
- 身份停用/会话撤销后返回空列表；未知策略异常使发现请求报错，不返回完整工具目录。
- SDK FunctionTool 的 is_enabled 在模型调用前复查当前 manifest。已经缓存的工具对象被直接调用，仍无法绕过 tools/call 授权。
- 在一次运行中新增授权带来的新工具，需要重新绑定/启动新 run 才会加入最初的工具对象集合；撤销已有工具可通过 is_enabled 复查移除。不声称实现 list_changed 推送。

## 可见性判定的具体语义

过滤使用同一个 PolicyEngine，查找任务范围内至少一个可授权资源。退款使用 1 个最小货币单位和任务币种作为权限存在性探测，因为当前条件语法只有金额上限；未来加入最小金额、不同条件或新工具时必须扩展此判定。探测只做查询和策略计算，从不调用 backend.execute。

可见性仅代表存在基础权限，不保证任意参数都可执行；具体参数、工单当前版本、退款余额和审批仍在调用时验证。比如关闭工单的工具可能可见，但再次关闭已关闭工单仍会失败。

## 实际测量（四个人工定义场景）

| 场景 | 静态工具总数 | 动态可见工具数 |
|---|---:|---:|
| 客户只读 | 5 | 1 |
| 工单操作 | 5 | 2 |
| 常规支持 | 5 | 3 |
| 完整演示 | 5 | 5 |

平均可见工具 2.75，相对于完整静态目录减少 45%。四个场景中 10 次合法可执行调用成功，1 次退款停在待审批。所有数字来自 results/phase5-check.json 的 binding_benchmark。

这些是场景级确定性数据，不是 100–200 条正式实验，也不是静态工具与动态工具的真实模型成功率对照。真实模型任务成功率以及静态基线任务成功率均标记 not_measured，后续实验阶段再测。

## 测试与审计

新增 16 项测试：任务动作/资源、用户/Agent 授权、身份停用、撤销、过期、币种冲突、零金额范围、Agent 上限、跨租户资源、审批工具可见、发现零副作用、真实 MCP 隐藏工具直接调用及 SDK 失效工具复查。

工具发现记录写入独立 audit.binding.jsonl（沿用实际 audit 文件名的 stem），包含可信身份、任务、可见工具数和列表；不把工具列表查询混入业务执行次数。执行审计仍写 audit.jsonl。

性能限制：当前 is_enabled 对每个候选工具单独获取列表，会有重复查询；尚未进行延迟优化或生产规模验证。所有之前的本地认证、TOCTOU、持久审批与 Sandbox 限制仍适用，详见 docs/phase4.md。

历史文档与报告保留对应阶段含义，当前版本以此 README 为准。默认本地演示会话拥有全量任务范围，所以通用 check 命令仍显示五个工具；binding_check 内的受限场景才展示 1/2/3 个工具。

下一阶段：Phase 6 Risk-Aware Approval（审批状态机与可信审批入口），当前尚未实现审批恢复。
