# 2026 年模拟卷续跑

固定使用 OpenCode 的 `opencode-go/deepseek-v4.1-flash#high`。提取、返修和独立复核均创建新会话，不调用其他模型。

- 任务包：`_orchestration/tasks_2026.json`，按清单中的 sequence 升序调度，共 284 份有原始附件的试卷。
- 当前状态：`data/question_extraction/2026/status.json` 的 `full_batch`。原 `tasks`（试跑）和 `chinese_batch`（语文）保留历史记录。
- 每卷会话、提示词、复核报告、校验报告：`_orchestration/runs/<运行时间>/<sequence>/`。
- 输出：`parsed/学科/年份/批次/<标准化原卷文件名>/paper.json` 及 `assets/`，命名规则见根目录 `README.md`。

从项目根目录启动：

```bash
_orchestration/native/run_2026_all.exe --parallel 24
```

现默认提供 Windows 原生 C 调度器；构建方法、改写范围和测速见 [native/README.md](native/README.md)。`supervise_2026.py` 下次启动时也会优先选择 C 版，未构建时回退 Python。已有运行中的控制器不会自动切换。原 Python 命令仍可使用：

```bash
python -I -X utf8 -u _orchestration/run_2026_all.py --parallel 24
```

该命令会先验证三份试跑均已验收、旧语文控制器没有活动任务，再开始全学科处理。不要同时运行旧的 `scripts/run_2026_chinese_batch.py`。新控制器通过操作系统文件锁避免重复启动。

每卷必须依次完成：提取、结构/路径/哈希校验、另一会话独立复核、必要返修和再次复核。只有明确 accept、具备实际读图日志并再次通过校验，才会标为 verified。先检查已有输出，已 verified 且来源哈希未变化的卷子会跳过；已有草稿优先复核。blocked 会保留，不盲目重试。普通任务最多返修两次，连续八个工具/服务错误会暂停后续分派（单卷超时不计入）。

禁止联网补答案、自行解题、擅改原卷、修改原始附件或降低验收标准。58 个没有原始附件的卷目列为 skipped_no_original。不要把语文批次完成误报为整个 2026 年完成。

每个 `opencode run` 必须带 `--standalone`。共用后台服务时，杀掉客户端进程并不会结束服务端会话：被超时放弃的会话会继续执行，甚至在其他卷复核期间改写 `parsed/` 下的资源（2026-11-14 实测出现过英语卷复核改写输出、化学 a020/a022 被旧会话回写）。同时这些僵尸会话会占满 provider 配额，表现为大量 `provider.transport: Timed out waiting for response headers` 重试。改用独立服务后，同一 24 路并发从 4.9 steps/min 恢复到 30.7 steps/min，重试归零。重启批次前先 `opencode service stop` 并确认没有残留 `opencode.exe`。

仅准备/校验任务清单：

```bash
_orchestration/native/run_2026_all.exe --prepare-only
```

检查状态时，以 `full_batch.status_counts` 和各任务的 `blocked_reason`、`review_report` 为准。完整结束后还会生成 `_orchestration/latest_summary.json`。
