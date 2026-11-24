# C 版 2026 试卷处理控制器

`run_2026_all.exe` 是 Windows 原生 C 调度器，保持现有 `status.json`、任务清单、输出目录与验收流程。支持 1–96 路并发，默认 **3 路**；每卷本地 OCR 使用 4 线程。

首次运行前按[方法说明](../README.md#默认识别方案local-hybrid-v1) 准备 `pipeline/models/question_ocr/` 和固定版本 Python 依赖。PDF 提取 Agent 启动前，控制器通过 `bridge.py` 的 `local_prepare` 调用 `local_extraction.py`：校验原卷和模型哈希、按文本层/PP-OCRv5 路由识别、检测版面、生成 210 dpi 页图和候选裁剪。Agent 只读取精简 `agent_bundle.json` 做语义结构化，不重复 OCR 或裁图。提取成功后 `prune_assets` 只删除本阶段创建、未被引用且未改变的候选；DOCX 主文件显式走 `agent_only`。本地准备失败会中止该卷，不静默回退。

## 运行

从项目根目录运行：

```bash
pipeline/native/run_2026_all.exe --parallel 3
```

仅准备任务清单，不修改试卷和状态：

```bash
pipeline/native/run_2026_all.exe --prepare-only
```

只处理指定卷目，参数必须带引号：

```bash
pipeline/native/run_2026_all.exe --parallel 4 --keys '2026|零模|北师大实验中学|数学|'
```

与原脚本一样支持 `--exclude-keys` 和 `--allow-unverified-pilot`。可以用 `--python` 指定 Python 的 exe 路径、`--opencode` 指定 OpenCode 的**原生 exe** 路径。默认自动查找当前 npm 安装中的原生 OpenCode，避免经由 `.cmd` 启动；调用仍使用 `--standalone` 和固定模型 `opencode-go/deepseek-v4.1-flash#high`。`--timeout` 指定每个 OpenCode 阶段的超时秒数，默认 14400。

`pipeline/supervise_2026.py` 下次启动时优先调用本程序；exe 不存在时使用原 Python 控制器。已经启动并加载了旧代码的控制器、守护进程不会因文件更新自动切换。本次开发没有中断或重启正在处理的批次。

不要同时运行新旧控制器。两者共享 `controller.lock`、`status.write.lock` 和逐卷锁，发生冲突会拒绝启动或等待已有写入结束。

## 改写范围

使用 C 实现的部分：

- Win32 工作线程并发调度，提取、复核、返修、暂停和断点续跑。
- JSON 状态更新、日志逐行解析、明确判词识别。
- SHA-256、目录快照、复核者改写检测、资源和页图读取证据检查。
- UTF-8 中文路径、原子写入、与 Python 兼容的跨进程文件锁。
- 原生子进程启动、实时记录 PID、Job Object 管理整棵进程树；阶段超时、取消或控制器退出时清理子进程。

Python 兼容边界在 `bridge.py`：原始清单/哈希盘点、启动时恢复分类、提示词模板、PDF 渲染及 Schema/路径校验继续复用原脚本和现有库。它不是完全去除 Python 依赖的版本。启动验证复用已加载的 Schema 校验器，避免逐卷再启动一个 Python 校验进程；命令行校验器仍保留原用法。

提取代理不能自行将卷子标为 verified。只有独立会话明确 accept、输出快照未变、资源和全部指定页图均有实际读取记录、哈希无变化，并通过最终校验后，控制器才设置 verified。损坏的 JSON 日志事件会作为执行失败处理，避免错误沿用先前的 accept。

## 构建

需要 MinGW-w64 GCC。Git Bash：

```bash
bash pipeline/native/build.sh
```

Windows 终端也可运行 `pipeline\native\build.cmd`。脚本优先使用 `GCC_BIN` 指定的编译器目录，其次使用本机 `C:\mingw64\bin`。构建启用 `-O3 -Wall -Wextra -Wpedantic -Werror`；链接 Windows 自带的 BCrypt，不需下载第三方 C 库。

源文件：`controller.c`（工作流）、`json.c/.h`（JSON 树和解析）、`platform.c/.h`（Windows 文件、锁、哈希、进程）。

## 验证与测速

不调用模型、不修改真实试卷的隔离测试：

```bash
python -I -X utf8 pipeline/native/test_native.py
```

平台层测试（在临时目录编译 fixture）：

```bash
python -I -X utf8 pipeline/native/test_platform.py
```

原 Python 控制器回归：

```bash
python -I -X utf8 pipeline/check_controller_regressions.py
```

历史日志解析测速脚本和结果已归档到 `backup/orchestration/native/`，不属于当前处理入口。
