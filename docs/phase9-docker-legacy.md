# Policy-Aware Agent Tool Gateway — Phase 9

Docker 沙箱执行后端已实现。开发环境 179 项自动化测试通过；这里没有 Docker，真实隔离验收为 **blocked**，必须以本机新生成的报告为准。测试替身不作为容器隔离证据。

## Windows 升级与验收

把包内 agent-tool-gateway 的内容覆盖到原项目，保留 .env 和 .venv。

安装并启动 [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)，使用 Linux containers。然后在原项目目录的 PowerShell 运行：

```powershell
docker version
docker pull python:3.12-slim
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m agent_gateway.sandbox_check --output results/local-phase9-check.json
```

docker version 应同时显示 Client 和 Server。镜像下载需要联网；检查本身不调用 LLM，不需要 API Key。容器启动和清理会比以前慢，超时探针会故意等待约 15 秒。

报告含 regression_status 和 sandbox.status；只有两部分都通过，总 status 才为 passed。Docker 缺失、镜像未下载或服务不可用会显示 blocked（退出码 2），不会伪装成通过。实际隔离探针不满足条件则 failed（退出码 1）。请上传新生成的 results/local-phase9-check.json。

## 本阶段范围

接入现有 database.query。策略判定通过后，可信宿主后端按 customer_id 和 limit 提取最多 100 行数据；经 stdin 交给容器，在容器的内存 SQLite 中执行固定 orders_by_customer 查询，再由宿主校验输出 schema 与预期行是否一致。

这一步演示数据最小化和隔离执行链路：宿主仍承担原始数据库读取，不声称所有数据库计算都已经迁出。未开放任意 Python、Shell 或 SQL 工具，也没有把写操作放入一次性容器。ticket.close、refund.issue 和其审批/JIT 语义保持原样。全项目不等于全沙箱化。

模型不能选择 Docker 参数、镜像或 worker 代码。它们只来自可信控制端。使用方式：

- MCP 服务启动参数：--sandbox-query [--sandbox-image python:3.12-slim]
- Python client：connect(..., sandbox_query=True)
- 真实模型演示（会产生 API 费用）：

```powershell
.\.venv\Scripts\python.exe -m agent_gateway.runtime --sandbox-query "查询 customer_001 的订单，最多10条"
```

为兼容旧阶段，默认不带 --sandbox-query 时沿用原后端。开启后，查询失败不会回退宿主执行。Phase 9 验收中的真实 MCP 查询会明确开启此选项。

## 隔离配置

| 维度 | 配置 |
| --- | --- |
| 网络 | network=none，不发布端口 |
| 文件 | 只读根文件系统，无宿主目录、数据库或 Docker socket 挂载 |
| 临时空间 | /tmp 独立 tmpfs，16 MiB，noexec/nosuid/nodev |
| 身份 | UID/GID 65534，cap-drop=ALL，no-new-privileges |
| 资源 | 内存 128 MiB、禁额外 swap、0.5 CPU、最多 32 个进程 |
| 输出 | stdout + stderr 合计最多 128 KiB；超限终止 |
| 输入 | JSON 最多 64 KiB |
| 时间 | docker start 执行阶段最多 15 秒；准备/清理各命令另有 20 秒上限 |
| 生命周期 | 每次独立创建，finally 强制删除；清理失败不能返回成功 |
| 镜像 | 先读取已下载镜像的不可变 ID，以 ID 创建，禁隐式拉取；拒绝 Windows 镜像和声明 volume 的镜像 |

Docker 镜像/daemon/宿主及网关控制进程属于可信边界。容器继承镜像默认环境，不注入宿主 API Key 或网关会话凭据。使用官方 Python 镜像；不要选含秘密或恶意配置的镜像。配置参考 [Docker 运行文档](https://docs.docker.com/engine/containers/run/)。

## 验收证据

179 项自动化测试 = 前八阶段 163 项 + 本阶段 16 项。新测试包括真实宿主进程的输出/时间限制、Docker 命令配置与清理控制流（替身）、授权先于 worker、数据范围、输出篡改拒绝和失败时无回退。

另外单独运行真实 Docker 探针，检查非 root、有效 capabilities 为零、no-new-privileges、seccomp、根目录只读、临时文件写入及容量限制、网络不可达、cgroup 内存/CPU/进程限制值、凭据和 socket 不存在，以及真实 MCP 查询和超时/输出溢出。资源读数不等于每种耗尽攻击都已实测；未进行逃逸攻击测试或多租户压力测试。

当前开发报告 results/phase9-check.json 的 sandbox.status 为 blocked / SANDBOX_DOCKER_NOT_FOUND。不要把该报告当成本地通过证据。

## 故障处理与限制

- SANDBOX_DOCKER_NOT_FOUND：安装 Docker Desktop 后重新打开 PowerShell。
- SANDBOX_IMAGE_OR_DAEMON_UNAVAILABLE：启动 Docker Desktop；确认 docker version 的 Server 正常；重新拉取镜像。
- SANDBOX_UNSUPPORTED_IMAGE：切换 Linux containers，使用上面的官方 Python 镜像。
- SANDBOX_CLEANUP_FAILED：Docker daemon 无法确认删除；先恢复服务，再用下方命令查看本项目遗留容器，核对后按具体 ID 删除。

```powershell
docker ps -a --filter label=agent-gateway.phase=9
# 仅当存在确认属于本项目的遗留容器时执行，用实际 ID 替换：
docker rm -f <container-id>
```

本阶段按需新建容器，未做池化或并发配额。网关进程被强杀/宿主崩溃时 finally 不能保证运行，尚未实现后台遗留容器回收。Docker 容器共享内核，不能描述成虚拟机级隔离或已证明防逃逸。前几阶段的授权检查与业务执行之间仍有 TOCTOU 窗口。

完成本机 Phase 9 验收后再推进后续阶段。
