# peibin-skills

个人 AI Agent Skills 集合，用于 Cursor / Claude Code 等支持 Skills 的编码助手。

## Skills

| Skill | 说明 | 触发场景 |
|-------|------|----------|
| [rubber-duck](./skills/rubber-duck/) | 小黄鸭调试伙伴——通过提问引导你自己找到答案，而不是直接给解法 | 卡在某个 bug、设计拿不定主意、想理清思路，或明确说「rubber duck」「小黄鸭」 |
| [to-commit](./skills/to-commit/) | 根据当前未提交的改动，生成符合 Conventional Commits 规范的 commit message | 准备提交代码、需要写 commit message |
| [debug-flow](./skills/debug-flow/) | 结构化 bug 修复流程：先用 rubber-duck 理清问题，用户自己修，最后用 to-commit 写提交信息 | 有 bug 想走完整流程：理解 → 修复 → 提交 |
| [to-feature-doc](./skills/to-feature-doc/) | 基于真实代码为现有功能模块生成新人 onboarding 文档 | 需要为某个功能/模块写设计说明文档 |

## 安装

### Cursor

将需要的 skill 目录复制或软链到个人 skills 目录：

```bash
# 示例：安装全部 skills
ln -s /path/to/my-skills/skills/rubber-duck ~/.cursor/skills/rubber-duck
ln -s /path/to/my-skills/skills/to-commit ~/.cursor/skills/to-commit
ln -s /path/to/my-skills/skills/debug-flow ~/.cursor/skills/debug-flow
ln -s /path/to/my-skills/skills/to-feature-doc ~/.cursor/skills/to-feature-doc
```

也可以使用 `~/.agents/skills/` 目录，效果相同。

### Claude Code Plugin

本仓库包含 [`.claude-plugin/plugin.json`](./.claude-plugin/plugin.json)，可作为 Claude Code 插件加载，一次性启用全部 skills。

## 目录结构

```
my-skills/
├── .claude-plugin/
│   └── plugin.json          # Claude Code 插件配置
├── skills/
│   ├── rubber-duck/
│   │   └── SKILL.md
│   ├── to-commit/
│   │   └── SKILL.md
│   ├── debug-flow/
│   │   └── SKILL.md
│   └── to-feature-doc/
│       └── SKILL.md
└── README.md
```

## 添加新 Skill

1. 在 `skills/` 下新建目录，例如 `skills/my-skill/`
2. 编写 `SKILL.md`（含 YAML frontmatter：`name`、`description`）
3. 在 `.claude-plugin/plugin.json` 的 `skills` 数组中注册路径

## License

MIT
