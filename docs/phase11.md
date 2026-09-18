# Phase 11 · Agent Action Provenance

将网关行动的身份、权限判定、审批和执行证据关联起来，提供离线 JSON / HTML 查看器。

## Windows 验收

将本包内 agent-tool-gateway 的内容覆盖到原项目目录，保留本地 .env 和 .venv。

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[e2b]"
.\.venv\Scripts\python.exe -m agent_gateway.provenance_check --output results/local-phase11-check.json
Start-Process .\results\phase11-provenance.html
```

无需模型或 E2B API 调用。检查运行全部回归测试，以及真实本地网关的读取、拒绝、申请退款、模拟审核、JIT 授权和执行。上传 local-phase11-check.json 验收。

## 新增内容

- 每次调用独立 request_id；新记录 trace_id 默认等于 request_id。
- 审批成功续执行通过原始申请 request_id 关联 trace_id / parent_request_id。只有 JIT 验证成功后建立该关联，伪造审批 ID 不会伪造已授权链路。
- 执行成功记录结果 SHA-256；保留身份、命中授权 ID、委托链、策略版本、原因、风险、耗时，以及 E2B 沙箱与代码/数据摘要。
- JSON 导出仅投影指定审计字段，不复制请求参数、业务结果、模型消息或令牌。
- HTML 支持文本搜索、逐条展开和审批时间线，全部本地加载，无 CDN。导入内容经过 HTML 转义。
- results 中已通过的 Phase 9/10 报告作为 historical 来源导入；没有 trace_id 的旧事件标为 legacy_unlinked，不补造历史链路。

查看任意 JSONL 审计文件：

```powershell
.\.venv\Scripts\python.exe -m agent_gateway.provenance --audit results/audit.jsonl --output results/provenance.json
```

独立导出命令只读取日志，不连接审批数据库；完整审批时间线由验收场景中的可信导出函数提供。

## 边界

这是网关行动证据，不是模型思维链、完整分布式追踪或防篡改审计系统。命中授权 ID 是决策时的引用，不包含完整历史授权快照。每次独立调用有自己的 trace；尚未把一次模型运行的多个工具调用统一为一个运行级 trace。执行摘要用于对照，不证明日志真实性。

本地验收中的审核者是脚本模拟，不能描述成真实人工审核。历史 Phase 9/10 的模型、E2B 调用发生在此前验收中，不算本轮重新调用。拒绝发生在参数校验前时，行动和资源等字段可能缺失；不会编造未知信息。所有导出仍可能包含业务标识符，适合本地演示。

历史阶段说明见 docs/phase10.md 及其他 docs 文件。
