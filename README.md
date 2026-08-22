# fcskills｜试试就知道了 Skills

这是「试试就知道了」的免费开源 Skill 包 `fcskills`，帮助内容创作者使用 AI 整理素材。

## fcs-rename｜素材改名

本地分析视频和图片的画面与原声，先生成重命名预览，获得同意后再改名。素材不上传。

当前代码的兼容目标是 Apple Silicon macOS、Intel macOS 和 Windows x64。每个平台都必须分别完成单元测试、固定依赖安装和真实素材烟雾测试，才能标记为「完整支持」；结果尚未记录时统一标记为「待验证」。Windows ARM64 和 Linux 尚未支持。

## 安装

GitHub 仓库：<https://github.com/CantonFocus/fcskills>

适用于所有支持 Agent Skills、能够访问本地文件并执行命令的桌面 Agent，包括但不限于豆包桌面版、WorkBuddy、Claude Code、Codex、Cursor、Cline、Gemini CLI、GitHub Copilot、OpenCode、OpenHands、Kiro、Qwen Code 和 Grok。

在终端执行一条命令，安装「试试就知道了」全部 Skills：

```bash
npx -y skills add CantonFocus/fcskills -g --all
```

`--all` 会把当前仓库的全部 Skills 安装到 `skills` CLI 支持的全部 Agent 入口，并写入通用 Agent Skills 入口。当前仓库只有 `fcs-rename`；以后新增的 Skill 也会一起通过这条命令安装。

安装后重启对应 Agent，再输入：

```text
使用 $fcs-rename 先预览这个素材目录的重命名方案。
```

## 首次使用

需要 Python 3.9 或更高版本；首次准备依赖时，该 Python 还需要可用的 `pip`。Windows x64 需要 Microsoft Visual C++ Runtime，Skill 会在下载 Whisper 前检查，缺失时只提示使用[微软官方 x64 安装包](https://aka.ms/vc14/vc_redist.x64.exe)，不会代替用户修改系统。首次分析前，Skill 会检查本地的 Whisper、语音模型和固定版本 FFmpeg；如果缺失，Agent 应先说明下载内容并征得同意，不会默认联网安装。

## 许可证

项目代码使用 MIT License。第三方工具和模型使用各自许可证，详见 `THIRD_PARTY_NOTICES.md`。
