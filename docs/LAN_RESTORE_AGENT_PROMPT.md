# 在另一台电脑恢复高考题库项目：Agent 提示词

你在一台新的 Windows 电脑上工作。请实际完成项目下载、文件大小检查、环境准备和断点恢复，不要只给出操作说明。

## 一、下载源与安全约束

旧电脑已停止原批处理，并通过只读局域网网关提供文件。网关只暴露清单内的文件，没有目录列表，也不会生成压缩包。

- 健康检查：`http://192.168.31.75:8765/health/KK5M3iBZRX6NCb6Jpuf7BJFYPFlnLXef`
- 文件清单：`http://192.168.31.75:8765/manifest/KK5M3iBZRX6NCb6Jpuf7BJFYPFlnLXef`
- 下载器地址：`http://192.168.31.75:8765/file/KK5M3iBZRX6NCb6Jpuf7BJFYPFlnLXef/scripts/download_lan_transfer.py`
- 快照规模：9,437 个文件，共 15,993,426,472 字节（约 14.9 GiB）
- 网关进程 PID：`11024`
- 网关在两小时后自动停止；若已过期，报告需要旧电脑重新启动网关，不要改用不受信任的镜像。

只在同一可信局域网内使用这些地址。不要把下载令牌、API 密钥或登录信息发布到外部服务。

## 二、逐文件下载并保留目录结构

1. 确认新电脑能访问健康检查地址，返回 `ok: true` 且 `files` 为 9437。
2. 创建两个独立目录：
   - 工具目录：`C:\gaokao-exam-transfer`
   - 项目目标目录：`D:\gaokao-exam`
3. 将下载器保存到工具目录，不要把下载脚本放进项目目标目录。可用：

```bat
curl.exe --fail --location "http://192.168.31.75:8765/file/KK5M3iBZRX6NCb6Jpuf7BJFYPFlnLXef/scripts/download_lan_transfer.py" --output "C:\gaokao-exam-transfer\download_lan_transfer.py"
```

4. 先阅读下载器，确认它只下载清单内文件、拒绝 `..` 路径、使用 `.part` 临时文件并检查下载后的文件大小。然后运行：

```bat
python -I C:\gaokao-exam-transfer\download_lan_transfer.py --manifest-url "http://192.168.31.75:8765/manifest/KK5M3iBZRX6NCb6Jpuf7BJFYPFlnLXef" --target "D:\gaokao-exam"
```

下载器会按原相对路径重建 `docs/`、`raw/`、`parsed/`、`data/` 和 `scripts/` 等目录。若中断，可用同一命令续传；已存在且大小一致的文件会跳过。不要执行下载的试卷、DOCX 或其他原始附件。

## 三、准备运行环境

1. 安装 Python 3.11+、uv 和 OpenCode CLI；Git 可选。
2. 在项目根目录创建虚拟环境并安装校验依赖：

```bat
cd /d D:\gaokao-exam
```

```bat
py -m venv .venv
```

```bat
.\.venv\Scripts\activate.bat
```

```bat
python -I -m pip install jsonschema
```

3. 配置新电脑自己的 OpenCode 登录或模型服务，不要假设旧电脑的凭据已随项目迁移。运行：

```bat
opencode models --standalone
```

必须确认存在 `opencode-go/deepseek-v4.1-flash`。若不可用，停止并如实报告，不得改用其他模型冒充。

## 四、核对快照并恢复断点

1. 阅读：
   - `docs/PDF_TO_JSON_ORCHESTRATOR_2026.md`
   - `data/question_extraction/2026/status.json`
   - `scripts/run_2026_chinese_batch.py`
2. 快照保存时语文批次为 32 份 `verified`、1 份暂停。`paper.json` 的原始附件和资源路径均为相对路径，不要批量替换成旧电脑绝对路径。
3. 确认旧电脑没有继续运行批处理，避免两台电脑同时写入不同副本后继续处理同一任务。
4. 从项目根目录恢复：

```bat
python -I scripts\run_2026_chinese_batch.py
```

保持脚本中的现有模型和并行配置，不擅自提高并行度。已 `verified` 的试卷必须跳过；暂停卷从对应阶段继续；`blocked` 卷不得盲目增加重试。提取和独立复核均使用 `opencode-go/deepseek-v4.1-flash#high`，禁止 Web Search/Web Fetch 和联网补答案。

## 五、完成后报告

简要报告：下载文件数和总字节数、文件大小检查结果、实际 OpenCode 模型、恢复命令是否成功启动，以及最新 `verified`、`assigned`、`in_review`、`paused`、`blocked` 数量。若任何一步未完成，明确说明具体错误，不能声称迁移或恢复已完成。
