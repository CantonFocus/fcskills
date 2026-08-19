# 数据权威索引

## 快速查找

| 要找什么 | 权威位置 |
| --- | --- |
| `fcskills` Skill 包名称与安装命令 | `README.md` |
| 公开 Skill 执行规则 | `skills/fcs-rename/SKILL.md` |
| 素材分析和改名代码 | `skills/fcs-rename/scripts/` |
| Codex 界面信息 | `skills/fcs-rename/agents/openai.yaml` |
| 安装与使用说明 | `README.md` |
| 第三方许可说明 | `THIRD_PARTY_NOTICES.md` |
| 自动测试 | `tests/` |

## 目录职责

- `skills/`：可被 Agent 安装的 Skill 真源。
- `tests/`：不接触真实素材的自动测试。
- `.github/workflows/`：GitHub 自动校验配置。

## 冲突规则

如说明文档与 Skill 行为冲突，以 `skills/fcs-rename/SKILL.md` 和其实际脚本行为为准，并立即修正说明文档。
