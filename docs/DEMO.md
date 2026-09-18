# 三分钟演示

## 0:00–0:40：问题与架构

“这个项目解决 Agent 调用工具时的权限边界问题。模型负责提出调用；网关根据可信身份、用户权限、Agent 上限和任务范围判断是否执行。”

展示 README 架构图。说明业务为模拟客服与订单数据，MCP 是工具协议，E2B 用于生成代码执行。

## 0:40–1:40：审批与证据页面

打开 results/phase11-provenance.html。搜索 refund.issue，展开 REQUIRE_APPROVAL 和后续 ALLOW；对照 trace_id、不同的 request_id、approval_id、reviewer_id 和 jit_token_id。查看审批时间线。

“这里的审核者由验收脚本模拟。一次性执行凭证绑定参数与上下文，授权执行后不能重放。”

## 1:40–2:20：真实 E2B 记录

搜索 analysis.run。指明 historical 来源，查看 provider、sandbox_id、code_sha256、dataset_sha256、rows_supplied。

“这是此前真实运行的证据：模型生成代码，对授权的四行数据进行分析。今天展示历史结果，不把它说成实时调用。”

## 2:20–3:00：实验结果

展示 docs/EVALUATION.md。分别介绍 8 次强制越权回放和 10 次模型运行。说明后者没有产生越权调用，所以不能声称网关拦截率 100%。

需要现场重跑本地演示：

```powershell
.\.venv\Scripts\python.exe -m agent_gateway.provenance_check --output results/local-phase11-check.json
Start-Process .\results\phase11-provenance.html
```

此命令包含全量测试，需等待完成。面试前可提前运行，现场直接打开页面。真实模型/E2B 重跑命令见 README，会产生 API 用量。
