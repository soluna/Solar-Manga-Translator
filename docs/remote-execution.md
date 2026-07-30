# 持久远程任务节点

持久远程任务节点用于让可信的局域网客户端在 Windows 电脑上上传测试包、执行 CUDA 或其他命令、读取日志并下载结果。它与临时、只读的远程诊断服务相互独立。

## Windows 端只需做一次

1. 更新并启动 Solar Manga Translator。
2. 打开“设置 → 持久远程任务节点”，点击“启用并自动启动”。
3. Windows 防火墙首次询问时，允许专用网络访问。
4. 点击“复制给 Codex”，把地址和 Token 发给可信的 Codex 任务。

启用状态和 Token 保存在应用数据目录。以后只要 Solar Manga Translator 正在运行，任务节点就会自动恢复；无需反复点击或生成新 Token。默认从 `8800` 开始选择空闲端口。

## HTTP 协议

所有请求都使用：

```http
Authorization: Bearer <Token>
```

核心入口：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/v1/status` | 节点能力、Python 路径和任务统计 |
| `POST` | `/v1/bundles` | 上传单个文件、ZIP 或 CBZ；压缩包会解压到隔离目录 |
| `GET` | `/v1/bundles` | 查看已上传测试包和文件 |
| `POST` | `/v1/jobs` | 提交任务 |
| `GET` | `/v1/jobs` | 查看最近任务 |
| `GET` | `/v1/jobs/{job_id}` | 查看状态、实时日志尾部和产物列表 |
| `POST` | `/v1/jobs/{job_id}/cancel` | 停止排队中或运行中的任务 |
| `GET` | `/v1/jobs/{job_id}/artifacts/{artifact_id}` | 下载日志或结果文件 |

节点一次只执行一个任务，避免多个模型同时抢占显存。任务、日志、上传包和结果会持久保存在状态响应中的 `worker_root` 下，应用重启后仍可读取；重启时尚未完成的任务会标记为失败，而不会被误判成仍在执行。

## 内置任务

### 运行环境诊断

```json
{
  "task": "runtime-diagnostics",
  "parameters": {}
}
```

生成脱敏后的 `runtime-diagnostics.json`。

### CUDA 冒烟测试

```json
{
  "task": "cuda-smoke-test",
  "parameters": {}
}
```

检查 PyTorch、CUDA、显卡型号和一次真实 CUDA 张量计算，生成 `cuda-smoke-test.json`。

### 执行命令

```json
{
  "task": "command",
  "parameters": {
    "argv": ["python", "benchmark.py", "--input", "dataset"],
    "cwd": "bundle:0123456789abcdef0123456789abcdef",
    "timeout_seconds": 14400,
    "env": {
      "HF_HOME": "D:\\model-cache"
    },
    "artifacts": ["reports/**/*.json", "outputs/**/*.png"]
  }
}
```

- `argv` 直接传给进程，不经过 shell；管道、重定向或 `&&` 不会被解释。
- `cwd` 支持 `job`、`code`、`data` 或 `bundle:<bundle_id>`。
- `env` 可为本任务追加环境变量。
- `artifacts` 是相对工作目录的 glob；匹配文件会复制到任务产物区，随后可直接下载。
- 单个任务最长可设置为 7 天。错误和退出码会用自然语言写入任务状态，完整输出保存在 `job.log`。

## 典型自动测试流程

1. `POST /v1/bundles` 上传包含测试图片和运行脚本的 ZIP。
2. `POST /v1/jobs`，以 `bundle:<id>` 为工作目录启动脚本。
3. 周期性读取 `GET /v1/jobs/{id}`；`log_tail` 是当前日志，状态终止时会给出退出码或错误。
4. 从 `artifacts` 中取得 ID 并下载所有评测结果。
5. 需要提前结束时调用 `/cancel`。

节点暴露的是受 Token 保护的本机程序执行能力。“更换令牌”会立即让旧 Token 失效；“关闭并取消自动启动”会停止当前任务和网络监听，并阻止下次启动时恢复。
