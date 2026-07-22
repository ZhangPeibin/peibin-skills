# peibin-skills

个人 AI Agent Skills 集合，用于 Cursor / Claude Code 等支持 Skills 的编码助手。

## Skills

| Skill | 说明 | 触发场景 |
|-------|------|----------|
| [rubber-duck](./skills/rubber-duck/) | 小黄鸭调试伙伴——通过提问引导你自己找到答案，而不是直接给解法 | 卡在某个 bug、设计拿不定主意、想理清思路，或明确说「rubber duck」「小黄鸭」 |
| [to-commits](./skills/to-commits/) | 按逻辑关注点把未提交改动拆成多个 Conventional Commits | 准备提交、希望按改动类型拆 commit |
| [to-pr](./skills/to-pr/) | 基于当前分支相对 base 的 commits/diff 写 PR 描述并创建 PR | 分支已有 commits、要开 pull request |
| [to-mermaid](./skills/to-mermaid/) | 写出能在 Markdown 里正确渲染的 Mermaid 图 | 画 flowchart / sequence 等，尤其标签含路径或特殊字符 |
| [to-feature-doc](./skills/to-feature-doc/) | 基于真实代码为现有功能模块生成新人 onboarding 文档 | 需要为某个功能/模块写设计说明文档 |
| [debug-flow](./skills/debug-flow/) | 结构化 bug 修复流程：先用 rubber-duck 理清问题，用户自己修，最后用 to-commits 写提交信息 | 有 bug 想走完整流程：理解 → 修复 → 提交 |
| [init-flutter-app](./skills/app/init-flutter-app/) | 从 flutter-template（GitHub 或本地）脚手架新 Flutter 应用 | 要用模板初始化 Flutter 项目 |

## 安装

### Cursor

将需要的 skill 目录复制或软链到个人 skills 目录：

```bash
# 示例：安装全部 skills
ln -s /path/to/my-skills/skills/rubber-duck ~/.cursor/skills/rubber-duck
ln -s /path/to/my-skills/skills/to-commits ~/.cursor/skills/to-commits
ln -s /path/to/my-skills/skills/to-pr ~/.cursor/skills/to-pr
ln -s /path/to/my-skills/skills/to-mermaid ~/.cursor/skills/to-mermaid
ln -s /path/to/my-skills/skills/to-feature-doc ~/.cursor/skills/to-feature-doc
ln -s /path/to/my-skills/skills/debug-flow ~/.cursor/skills/debug-flow
ln -s /path/to/my-skills/skills/app/init-flutter-app ~/.cursor/skills/init-flutter-app
```

也可以使用 `npx skills add /path/to/my-skills --full-depth` 安装到项目的 `.agents/skills/`（由 `skills-lock.json` 锁定）。

### Claude Code Plugin

本仓库包含 [`.claude-plugin/plugin.json`](./.claude-plugin/plugin.json)，可作为 Claude Code 插件加载，一次性启用全部 skills。

## 目录结构

```
my-skills/
├── .claude-plugin/
│   └── plugin.json          # Claude Code 插件配置
├── skills/
│   ├── rubber-duck/
│   ├── to-commits/
│   ├── to-pr/
│   ├── to-mermaid/
│   ├── to-feature-doc/
│   ├── debug-flow/
│   └── app/
│       └── init-flutter-app/
├── skills-lock.json
└── README.md
```

## 添加新 Skill

1. 在 `skills/` 下新建目录，例如 `skills/my-skill/`
2. 编写 `SKILL.md`（含 YAML frontmatter：`name`、`description`）
3. 在 `.claude-plugin/plugin.json` 的 `skills` 数组中注册路径
4. 如需同步 `npx skills` 安装结果：重新 `npx skills add` 并更新 `skills-lock.json`

## License

MIT
