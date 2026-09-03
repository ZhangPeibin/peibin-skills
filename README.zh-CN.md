# peibin-skills

[English](./README.md) | [简体中文](./README.zh-CN.md)

一套为 Codex、Claude Code 和 Cursor 准备的、可复用的 AI 编码 Agent Skills 与工程工作流。

仓库按所有权清晰分层：

- `skills/personal/` 存放本仓库自行维护的 Skills。
- `skills/vendor/` 存放精选的第三方 Skills，并固定到有记录的上游 revision。
- [`.claude-plugin/plugin.json`](./.claude-plugin/plugin.json) 定义 Claude Code 插件默认启用的 Skills。

## 工程工作流

核心交付流程把需求、规划、实现、Ticket 状态、提交和 Pull Request 分成独立职责：

```text
$requirements-analysis
        |
        v
$to-spec -> $to-tickets
                 |
                 v
          $tickets next
                 |
                 v
            $implement
                 |
                 v
          $tickets done
                 |
                 v
           $to-commits -> $to-pr
```

`$tickets` 管理由仓库配置选择的本地 Markdown 或 GitHub Issue ticket DAG，不实现或验证产品代码；`$implement` 负责实现、测试、代码审查和实现提交。

[![Skill 工作流](./docs/diagrams/skill-workflow.png)](./docs/diagrams/skill-workflow.excalidraw)

[编辑 Excalidraw 源文件](./docs/diagrams/skill-workflow.excalidraw) · [查看 SVG](./docs/diagrams/skill-workflow.svg)

## Personal Skills

| Skill | 用途 | 使用场景 |
|---|---|---|
| [to-commits](./skills/personal/to-commits/) | 把未提交改动拆成聚焦的 Conventional Commits | 改动已经可以提交 |
| [to-pr](./skills/personal/to-pr/) | 根据已提交改动生成 PR 描述并创建 Pull Request | 分支已经可以进入审查 |
| [to-mermaid](./skills/personal/to-mermaid/) | 生成能在 Markdown 中稳定渲染的 Mermaid 图 | 路径或特殊字符让图表容易出错 |
| [to-feature-doc](./skills/personal/to-feature-doc/) | 根据真实实现为现有功能生成工程文档 | 模块需要设计说明或新人指南 |
| [requirements-analysis](./skills/personal/requirements-analysis/) | 逐项澄清 Feature，并持久保存讨论过程 | 需求仍然模糊 |
| [setup-engineering-workflow](./skills/personal/setup-engineering-workflow/) | 配置 issue tracker、标签、领域文档、pre-PRD 文档和 worktree | 仓库准备采用这套工作流 |
| [tickets](./skills/personal/tickets/) | 管理本地 Markdown 或 GitHub Issue ticket DAG | 需要查看、领取、完成或暂停 Ticket |
| [implement](./skills/personal/implement/) | 在仓库配置的 worktree 中实现 spec 或 ticket | 已规划的工作可以开始实现 |
| [handoff](./skills/personal/handoff/) | 使用 `$handoff` 生成可持久恢复的版本化 Markdown 交接物 | 工作需要在另一个会话继续 |
| [code-review](./skills/personal/code-review/) | 审查指定改动并报告有明确证据的回归 | 日常本地改动、commit、分支或 PR 需要审查 |
| [to-audit](./skills/personal/to-audit/) | 以高置信 release gate 深度审计指定改动 | 发版或敏感改动需要深度 pre-merge 审计 |
| [init-flutter-app](./skills/personal/flutter/init-flutter-app/) | 从 `flutter-template` 初始化 Flutter 应用 | 创建新的 Flutter 项目 |
| [add-flutter-feature](./skills/personal/flutter/add-flutter-feature/) | 给 `flutter-template` 项目增加分层 Feature | 扩展已有 Flutter 应用 |

`setup-engineering-workflow` 改编自 [mattpocock/skills](https://github.com/mattpocock/skills) 的 `setup-matt-pocock-skills`，revision 为 `2ab958093e83e0ec752e6c1c5932da465bf23e0c`。它保留上游 MIT 许可证，并增加了 Feature 文档和 worktree 配置。

## Vendor Skills

本仓库中的 [Matt Pocock Skills](./skills/vendor/mattpocock/) 是一个经过筛选的本地 fork。来源 revision 记录在 [`SOURCE.json`](./skills/vendor/mattpocock/SOURCE.json)，上游 MIT 许可证也保存在同一目录。

Claude Code 插件当前启用了 vendor 工作流阶段 `grill-with-docs`、`to-spec`、`to-tickets` 和 `tdd`，以及 personal 的 `implement`、`code-review` 和 `to-audit`。插件同时启用其他 personal 工作流 Skills，以及 `writing-great-skills`、`grilling` 和 `domain-modeling` 依赖。其他 vendor Skills 保留源码，但默认不加载。

personal 和 vendor 中都存在名为 `handoff` 和 `code-review` 的 Skill。personal 版本是 canonical 版本，也是插件中唯一注册的版本；vendor 版本仅作为参考源码保留。

`$handoff` 默认生成完整交接；高频续接可使用 `$handoff --compact`。两种档位都支持 artifact-only 输出：`$handoff --artifact-only` 和 `$handoff --compact --artifact-only`。

## 安装

使用已经配置 GitHub SSH 凭据的环境克隆私有仓库：

```bash
git clone git@github.com:ZhangPeibin/peibin-skills.git
cd peibin-skills
```

### Codex

把需要的 personal Skills 复制或软链接到共享的 Agent Skills 目录：

```bash
mkdir -p ~/.agents/skills
ln -s /path/to/peibin-skills/skills/personal/to-commits ~/.agents/skills/to-commits
ln -s /path/to/peibin-skills/skills/personal/to-pr ~/.agents/skills/to-pr
ln -s /path/to/peibin-skills/skills/personal/to-mermaid ~/.agents/skills/to-mermaid
ln -s /path/to/peibin-skills/skills/personal/to-feature-doc ~/.agents/skills/to-feature-doc
ln -s /path/to/peibin-skills/skills/personal/requirements-analysis ~/.agents/skills/requirements-analysis
ln -s /path/to/peibin-skills/skills/personal/setup-engineering-workflow ~/.agents/skills/setup-engineering-workflow
ln -s /path/to/peibin-skills/skills/personal/tickets ~/.agents/skills/tickets
ln -s /path/to/peibin-skills/skills/personal/implement ~/.agents/skills/implement
ln -s /path/to/peibin-skills/skills/personal/handoff ~/.agents/skills/handoff
ln -s /path/to/peibin-skills/skills/personal/code-review ~/.agents/skills/code-review
ln -s /path/to/peibin-skills/skills/personal/to-audit ~/.agents/skills/to-audit
ln -s /path/to/peibin-skills/skills/personal/flutter/init-flutter-app ~/.agents/skills/init-flutter-app
ln -s /path/to/peibin-skills/skills/personal/flutter/add-flutter-feature ~/.agents/skills/add-flutter-feature
```

### Claude Code Plugin

通过 [`.claude-plugin/plugin.json`](./.claude-plugin/plugin.json) 将仓库作为 Claude Code 插件加载，即可一起启用所有已注册 Skills。

### Cursor

把需要的 personal Skills 复制或软链接到 Cursor Skills 目录：

```bash
ln -s /path/to/peibin-skills/skills/personal/to-commits ~/.cursor/skills/to-commits
ln -s /path/to/peibin-skills/skills/personal/to-pr ~/.cursor/skills/to-pr
ln -s /path/to/peibin-skills/skills/personal/to-mermaid ~/.cursor/skills/to-mermaid
ln -s /path/to/peibin-skills/skills/personal/to-feature-doc ~/.cursor/skills/to-feature-doc
ln -s /path/to/peibin-skills/skills/personal/requirements-analysis ~/.cursor/skills/requirements-analysis
ln -s /path/to/peibin-skills/skills/personal/setup-engineering-workflow ~/.cursor/skills/setup-engineering-workflow
ln -s /path/to/peibin-skills/skills/personal/tickets ~/.cursor/skills/tickets
ln -s /path/to/peibin-skills/skills/personal/implement ~/.cursor/skills/implement
ln -s /path/to/peibin-skills/skills/personal/handoff ~/.cursor/skills/handoff
ln -s /path/to/peibin-skills/skills/personal/code-review ~/.cursor/skills/code-review
ln -s /path/to/peibin-skills/skills/personal/to-audit ~/.cursor/skills/to-audit
ln -s /path/to/peibin-skills/skills/personal/flutter/init-flutter-app ~/.cursor/skills/init-flutter-app
ln -s /path/to/peibin-skills/skills/personal/flutter/add-flutter-feature ~/.cursor/skills/add-flutter-feature
```

### `npx skills`

直接安装 canonical 的 personal `handoff` 和 `code-review`，避免名称去重选择 vendor 副本：

```bash
npx skills add /path/to/peibin-skills/skills/personal/handoff --agent codex --copy -y
npx skills add /path/to/peibin-skills/skills/personal/code-review --agent codex --copy -y
```

不要通过仓库根目录的 `--full-depth` discovery 安装 `handoff` 或 `code-review`。installer 会按名称去重，因此其列表无法证明最终选择了哪个副本。根目录 discovery 仍适合安装没有同名冲突的明确 Skill。

安装主要工程工作流及其依赖：

```bash
npx skills add /path/to/peibin-skills --full-depth --skill setup-engineering-workflow requirements-analysis grill-with-docs to-spec to-tickets implement tdd grilling domain-modeling
```

installer 可能在消费项目中创建或更新 `skills-lock.json`；本源码仓库不维护该锁文件。

## 开发与验证

运行与 CI 完全相同的隔离检查：

```bash
uv run --isolated --no-project --no-config --no-env-file --with PyYAML==6.0.2 -- python3 -I skills/personal/to-audit/scripts/run_release_check.py
```

launcher 会检查插件注册、Skill metadata、本地文档链接、workflow 资产、所有 personal
Skill 测试，以及 to-audit release gate。直接运行 `make check` 会主动失败，因为 GNU
Make 在解析 Makefile 前就可能处理继承的 Make 选项，仓库内的 Makefile 无法先行清洗它们。
标准检查以及 to-audit 内置的 scope 和 workflow CLI 仅支持 POSIX runtime；在不支持的
平台上，它们会在检查仓库前 fail closed。launcher 还会在每个仓库检查阶段的前后重新验证
原始 Python entrypoint 及其解析后的 executable target。Python 的绝对路径即使包含空格也受
支持，因为 launcher 会把它作为正确引用的环境变量传递，而不是交给 Make 按路径列表解析。
空格路径回归测试从隔离 launcher 的 `sys.executable` 创建解释器副本，不通过环境 `PATH`
选择另一个 Python。若有意不提供已暂存的
`tests/independent_agent_evaluation.json`，上述精确命令必须只因缺少独立评估证据而失败，
不能因解释器选择或空格路径处理失败。
to-audit probes 还会冻结以下行为：contract 版本必须是 canonical `major.minor`，嵌套
Git 边界必须通过结构验证且保留外层 index 的 ownership，以及 entry budget 必须流式执行，
不能先完整物化超限目录再报错。

## 仓库结构

```text
peibin-skills/
├── .claude-plugin/
│   └── plugin.json
├── .github/workflows/ci.yml
├── docs/diagrams/
├── scripts/validate_repo.py
├── skills/
│   ├── personal/
│   └── vendor/mattpocock/
├── README.md
├── README.zh-CN.md
└── Makefile
```

## 添加 Skill

1. 在 `skills/personal/` 下创建目录，例如 `skills/personal/my-skill/`。
2. 添加带有 `name` 和 `description` frontmatter 的 `SKILL.md`。
3. 添加 `agents/openai.yaml`；显式调用的 Skill 应设置 `allow_implicit_invocation: false`。
4. 在 [`.claude-plugin/plugin.json`](./.claude-plugin/plugin.json) 中注册路径。
5. 同时更新两份 README。

第三方 Skills 应放在 `skills/vendor/<owner>/` 下，同时保留许可证，并记录上游仓库和准确 revision。

## License

[MIT](./LICENSE)
