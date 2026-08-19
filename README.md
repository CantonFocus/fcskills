# fcskills｜试试就知道了 Skills

这是「试试就知道了」的免费开源 Skill 包 `fcskills`，帮助内容创作者使用 AI 整理素材。

## fcs-rename｜素材改名

本地分析视频和图片的画面与原声，先生成重命名预览，获得同意后再改名。素材不上传。

当前版本优先支持搭载 Apple Silicon 的 macOS。Windows、Linux 和 Intel Mac 尚未完整验证。

## 安装

发布 GitHub 仓库后，将下面的 `<GitHub用户名>` 替换为实际用户名：

安装「试试就知道了」全部 Skills：

```bash
npx -y skills add <GitHub用户名>/fcskills -g --all
```

当前仓库只有 `fcs-rename`；以后新增的 Skill 也会一起通过这条命令安装。

只安装 `fcs-rename` 到 Codex：

```bash
npx skills add <GitHub用户名>/fcskills --skill fcs-rename -g -a codex -y
```

只安装 `fcs-rename` 到 Claude Code：

```bash
npx skills add <GitHub用户名>/fcskills --skill fcs-rename -g -a claude-code -y
```

安装后重启对应 Agent，再输入：

```text
使用 $fcs-rename 先预览这个素材目录的重命名方案。
```

## 首次使用

首次分析前，Skill 会检查本地的 Whisper、语音模型和 FFmpeg。如果缺失，Agent 应先说明下载内容并征得同意，不会默认联网安装。

## 许可证

项目代码使用 MIT License。第三方工具和模型使用各自许可证，详见 `THIRD_PARTY_NOTICES.md`。
