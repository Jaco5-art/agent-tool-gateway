# 上传 GitHub

本交付包包含源代码、测试、文档和验收证据；尚未替你创建仓库或上传。

1. 将完整包解压到一个新目录，避免把旧项目中的本地凭证和数据库一起上传。
2. 在 GitHub 创建空仓库，例如 agent-tool-gateway。不要在网页端预生成 README。
3. 在新目录中的 agent-tool-gateway 文件夹打开 PowerShell：

```powershell
git init
git add .
git status --short
```

确认暂存列表没有 .env、.venv、数据库或 *.secret.json。项目提供 .gitignore；如果你额外添加了文件，也要检查其内容。

```powershell
git commit -m "Add evaluated agent tool gateway prototype"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/agent-tool-gateway.git
git push -u origin main
```

将 YOUR_USERNAME 换为自己的 GitHub 用户名。以上命令用于新仓库；已有仓库不要直接重复设置远端。

仓库首页展示 README。建议演示顺序：架构 → 审批追踪 → E2B 历史证据 → 实验边界。需要开源许可时自行选择并添加 LICENSE；当前交付没有替你指定许可。
