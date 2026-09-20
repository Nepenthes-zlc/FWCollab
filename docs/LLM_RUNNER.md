# 双 LLM 单步运行

运行器为 F/W 分别创建策略对象和历史。每轮两个策略收到同一个冻结公开状态的独立副本，并行返回一个单格动作；消息到下一轮才进入对方 inbox。

## 启动本地 Copilot API

GitHub token 是 Copilot API 的上游认证，不是 FWCollab 请求头里的网关 key。不要把 token 写进命令参数、配置或轨迹。PowerShell 可在当前会话中安全读取文件并通过环境变量传给子进程：

```powershell
$env:COPILOT_API_GITHUB_TOKEN = [IO.File]::ReadAllText((Resolve-Path '..\token')).Trim()
$env:COPILOT_API_HOME = (Resolve-Path '.tools').Path + '\copilot-api-data'
& '..\.tools\bun\bun-windows-x64\bun.exe' x --bun '@jeffreycao/copilot-api@latest' start --port 4141
```

本机回环地址且未配置网关 API key 时，FWCollab 不需要 `--api-key-file`。若管理员另外配置了网关 key，才通过 `FWCOLLAB_API_KEY` 提供该网关 key。

## 运行、验证和查看

```powershell
python -m fwcollab.cli symbol-run `
  --map maps/symbolic/S02.fwmap `
  --provider openai --allow-network `
  --endpoint http://127.0.0.1:4141 `
  --fire-model gpt-5.4-mini --water-model gpt-5.4-mini `
  --max-rounds 80 `
  --output artifacts/runs/S02.json

python -m fwcollab.cli symbol-replay `
  --map maps/symbolic/S02.fwmap --trace artifacts/runs/S02.json

python -m fwcollab.cli symbol-render-trace `
  --trace artifacts/runs/S02.json --output artifacts/runs/S02.html
```

轨迹记录模型名、每轮冻结观察、双方合法化后的动作、简短理由、公开消息、模型原始输出、错误、延迟、前后状态哈希及协作指标，但不记录 API 凭据。模型调用或格式解析失败时，该角色本轮替换为 WAIT，episode 继续。
