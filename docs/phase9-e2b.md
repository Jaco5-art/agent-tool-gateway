# Policy-Aware Agent Tool Gateway — Phase 9 upgraded

新增 **Agent 生成 Python → MCP → 数据集授权 → E2B 沙箱 → 结果校验**。模型按币种汇总模拟订单，代码只在远程沙箱执行。无须安装 Docker。

本地 200 项测试通过；真实 E2B 和模型调用尚未运行。开发环境没有 E2B_API_KEY/OPENAI_API_KEY，不能据此宣称云端隔离或真实模型成功。请按下方步骤生成本机报告。

## Windows 覆盖与安装

把包内 agent-tool-gateway 的内容覆盖原项目，保留现有 .env 和 .venv。

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[e2b]"
.\.venv\Scripts\python.exe -m agent_gateway.analysis_check --output results/local-phase9-e2b-check.json
```

此命令只运行本地测试，没有外部 API 调用或费用。预期 status=local_passed_live_pending、200 tests、0 failures/errors。它不等于 Phase 9 完整验收。

## 配置密钥

在 [E2B 官网](https://e2b.dev/) 注册，在控制台获取 API Key；将其仅填入本机 .env。保留已有 OPENAI_API_KEY，添加：

```dotenv
E2B_API_KEY=填入你的E2B密钥
OPENAI_MODEL=gpt-5-mini
```

不要把 .env、密钥或包含密钥的截图上传。不要用示例 .env.example 覆盖已有 .env。E2B 是独立服务，OpenAI 密钥不能替代它。托管沙箱有独立用量/账户额度；联网真实验收会产生沙箱用量。

## 分层真实验收

先验证真实 E2B（约创建 5 个短时沙箱，不调用模型）：

```powershell
.\.venv\Scripts\python.exe -m agent_gateway.analysis_check --live --output results/local-phase9-e2b-live-check.json
```

成功状态为 e2b_passed_model_pending。再运行完整验收（会重复 E2B 探针，额外调用模型，模型最多两次沙箱尝试）：

```powershell
.\.venv\Scripts\python.exe -m agent_gateway.analysis_check --live-model --output results/local-phase9-e2b-model-check.json
```

--live-model 隐含 --live，需要两个密钥。成功状态才是 passed。报告保留实际生成代码、工具执行结果、沙箱 ID、代码/数据摘要、审计事件、模型最终回答和 token 用量。请把报告发回，确认后再继续后续阶段。

也可以配置两个密钥后直接执行 --live-model，省去单独的 --live 轮次。

## 演示任务与评分

授权 customer_001 的最多 100 条订单，按币种计算订单数量、总额、已退款额和净额。数据使用整数最小货币单位，不混合币种。

| 币种 | 订单数 | 总额 | 已退款 | 净额 |
| --- | ---: | ---: | ---: | ---: |
| GBP | 2 | 125000 | 5000 | 120000 |
| USD | 2 | 30000 | 3000 | 27000 |

另有 customer_002 的异租户订单，用于验证不能传入沙箱。真实模型只收到任务、工具 schema 和工具返回值；不会收到参考代码或预期答案。评分由宿主将工具计算结果和模型最终 JSON 分别与确定性预期比较。此为一个受控演示，不是正式模型能力基准或安全阻止率。

## 权限与工具接入

analysis.run 是独立 capability，绑定 dataset:orders_customer_001。它明确表示“允许将这个数据集的有限副本交给隔离代码分析”，不是沿用 database.query 的读取授权。旧会话不会自动获得新权限。

新工具接入原 PolicyEngine：校验租户、用户/Agent capability、Agent 权限上限、任务范围、委托链，再读取/上传数据。客户端自动创建的分析演示会话只授予 analysis.run，其他业务工具不可见。手工传入 registry/token 时，客户端不会自动加权。

本场景 risk=MEDIUM：限定模拟只读数据、无联网、无宿主写回。它不触发人工审批/JIT；原高风险退款流程继续保留。没有声称“生成代码已经走过人工审批”。后续如果开放网络、业务写回或敏感数据，应重新定义风险等级和审批规则。

- 新 MCP 入口：server --e2b-analysis（仅由可信控制端开启）。
- Python client：connect(..., e2b_analysis=True)。
- 工具参数：customer_id、limit、code。模型不能提交身份、API Key、镜像、网络策略或远程 Shell 命令。
- 最多 100 行、上传 JSON 最多 64 KiB、代码最多 12000 字符。
- 工具结果是数据，不具有指令优先级；模型系统指令明确要求不执行其中的指令。但这不是已证明的提示注入免疫。

## E2B 执行边界

锁定 e2b==2.50.0。已核对安装 SDK 的 create/files.write/commands.run/kill 接口；已确认其原生依赖有 Windows x64 / Python 3.12 wheel。

每次调用创建新沙箱，secure=True、allow_internet_access=False，同时配置 deny_out 和关闭未认证公共访问；不传入宿主凭据或挂载数据库。代码及授权数据通过文件 API 写入固定路径，不插入 Shell 字符串。

可信 supervisor 由 root 启动；生成代码降为 UID/GID 65534，清空附加组、设置 no-new-privileges，并使用最小环境变量。数据及代码文件只读，supervisor 文件对生成代码不可读。代码只在沙箱内启动，宿主绝不 exec/eval 生成代码。

| 限制 | 值与范围 |
| --- | --- |
| 沙箱生命周期 | 60 秒自动到期；正常/失败均 finally kill |
| 代码墙钟时间 | 8 秒 |
| CPU 时间 | 每进程 4 秒 |
| 虚拟地址空间 | 每进程 256 MiB，不代表整台 VM 内存 |
| 进程数量 | UID 的 RLIMIT_NPROC 16 |
| 文件 | 每文件最大 1 MiB；不是整个 VM 磁盘配额 |
| 文件描述符 | 32 |
| stdout + stderr | 总计 64 KiB，超限终止 |
| 输出格式 | 必须是一个 JSON 对象，不接受 NaN/Infinity |

监督进程和 E2B 微虚拟机承担不同职责：进程限制控制资源使用，VM/供应商网络边界隔离宿主和外部访问。没有通过 AST 黑名单或替换 Python builtins 冒充沙箱。整台 VM 的 CPU、内存、磁盘配置使用 E2B 默认模板/账户配置；此版本未自行配置这些总量。

可查看 [E2B 官方文档](https://docs.e2b.dev/)。云端 API 可达性、账户额度、模板实际行为需真实验收；SDK 合约测试和测试替身不能替代它。

## 测试证据与状态

200 项 = Phase 1–8 的 163 项 + 旧 Docker 适配器 16 项 + 新 E2B/授权/MCP 21 项。E2B adapter 的本地测试使用明确的替身；真实 MCP 子进程测试只验证工具可见性和缺密钥时拒绝。

真实 E2B 探针另外核验：参考分析正确、有限数据、非 root、凭据缺失、只读数据、root 文件隔离、网络阻断、资源限制读数、超时、输出超限、非法 JSON、跨租户拒绝。网络失败单次观察不是全面网络审计；资源读数不是所有耗尽行为都已实测。

| 总状态 | 含义 |
| --- | --- |
| local_passed_live_pending | 只有本地测试通过 |
| e2b_passed_model_pending | 本地 + 真实沙箱通过，模型未测 |
| passed | 本地 + 真实沙箱 + 模型结果均通过 |
| blocked | 缺少需要的密钥 |
| failed | 有实际检查未通过 |

缺密钥不跳过后声称成功。发生 E2B 失败时没有本地执行回退。原始供应商异常文本可能含连接信息，因此报告只暴露稳定错误码，不打印原始凭据或 URL。

## 本阶段边界

这是个人工程项目的真实链路原型：没有做生产级并发、恢复调度或大规模安全基准。审批/JIT/委托模块已复用，但新演示会话是直接授权单 Agent，并未新增多个真实模型 handoff。权限检查与上传之间仍非原子事务。

旧 Docker 后端作为可选基线留在包中；文档见 docs/phase9-docker-legacy.md。不要再使用旧 sandbox_check 命令作为升级版验收。旧 results/phase9-check.json 保留原 blocked 证据，新报告是 results/phase9-e2b-check.json。

策略版本已更新为 phase9-e2b-v1，旧待执行审批/JIT 需重新申请。Phase 1–8 的历史报告原样保留。
