# fcskills 开发与兼容标准

## 适用范围

本标准适用于 `CantonFocus/fcskills` 中所有新建和修改的公开 Skill。

默认目标：

- Windows 与 macOS 均可安装、发现和运行。
- 默认通过 `npx skills add ... -g --all` 全局安装。
- 不要求用户理解 Skill 内部代码。
- 不读取作者私有项目和本机路径。

## 两类 Skill

### 纯规则 Skill

不包含需要执行的脚本和本地二进制程序。

必须检查：

- 示例路径不能只适用于 macOS。
- 示例命令不能只适用于 Bash。
- 不假设所有 Agent 都支持相同的工具。
- 缺少必要能力时，应清楚告诉用户，不得假装已经执行。

### 可执行 Skill

包含 Python、Node.js、PowerShell、Shell、FFmpeg、Whisper 或其他本地程序。

必须提供：

- 操作系统识别。
- CPU 架构识别。
- 依赖检查。
- 无网络 `preflight`。
- Windows 与 macOS 对应运行路径。
- 明确错误信息。
- 可复现的自动测试。

## 跨平台实现要求

### 路径

- Python 使用 `pathlib.Path`。
- Node.js 使用 `node:path`。
- 不手工拼接 `/` 或 `\`。
- 不硬编码 `/Users/...`、`/tmp/...`、盘符或桌面目录。
- 测试包含中文、空格和较长路径。

### Python 启动

用户文档不得假设所有 Windows 电脑都有 `python3` 命令。

Agent 应先复用当前可用且版本符合要求的 Python 解释器；需要查找外部启动器时，按系统分别检测：

- Windows：`py -3`、`python`、`python3`。
- macOS：`python3`、`python`。

Skill 内部调用其他 Python 脚本时，应使用 `sys.executable`。

### 下载

- 优先使用 Python 或 Node.js 的跨平台下载能力。
- 不把 `curl` 作为唯一下载方式。
- 下载文件必须校验 SHA-256。
- 下载、安装和编译前必须取得用户授权。
- 不将模型、二进制程序和构建产物提交到 GitHub。

### 外部程序

- 使用 `shutil.which()` 或等效方式发现系统程序。
- Windows 必须处理 `.exe`。
- 不假设 `make`、GCC、Homebrew 或 Xcode 已安装；如构建系统需要生成器，必须显式选择并在运行前检测。
- 需要本地编译时优先采用跨平台构建系统，并在 `preflight` 中说明所需工具。
- 如果提供预编译程序，必须为每个系统和架构建立明确映射与校验值。
- Windows 预编译程序依赖 VC++ Runtime 等系统运行库时，必须在下载前检测并给出官方来源，不得等到启动失败后才暴露。
- 固定版本依赖不得静默回退到系统中的任意同名程序。

### 平台专属功能

禁止直接把平台专属能力写进公共主流程。

正确结构：

```text
公共流程 → 平台检测 → 平台适配器 → 统一输出
```

例如，缩略图生成应优先使用 FFmpeg 或跨平台图片库，不直接依赖 macOS 的 `qlmanage`。

## 测试要求

每个 Skill 至少验证：

- Skill 能被安装工具发现。
- frontmatter 合法。
- Windows 能运行 `preflight`。
- macOS 能运行 `preflight`。
- 路径中包含中文和空格时可以运行。
- 缺少依赖时给出可理解的错误。
- 无用户授权时不自动下载。
- `--dry-run` 不修改真实文件。
- 执行中断后不会留下半完成状态。

## 发布状态

兼容状态分为：

- `未验证`：只有代码或理论判断。
- `自动测试通过`：GitHub Actions 已通过。
- `真实环境验证`：已经在真实 Windows 或 macOS 设备运行。
- `完整支持`：自动测试和真实环境验证都通过。

`README.md` 只能使用实际达到的状态。
