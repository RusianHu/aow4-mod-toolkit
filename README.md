# AoW4 Mod Toolkit

[English README](README.en.md)

项目级Codex Skill及Python CLI，当前源码 **0.4.0.dev8（开发预览）**。可用于范围明确的正式Mod项目，配合官方编辑器工作；尚未完整替代官方编辑器。支持[中文内容调查](references/content-research-workflows.md)、[批量字段恢复](references/field-copy-workflows.md)及[开局授予声明、效果/费用解释、嵌套原生读取、共享影响和内容快照](references/gameplay-analysis-workflows.md)；已发布安装包的版本仍以 Release 清单为准。

**当前适配（2026-11-08）：本机 Manta 1.1 Hotfix Build26482553 的资源核心、schema/限定玩法解释和启动器 2026.13 CLI 数据操作可用；原生素材、地图及官方编辑器桥接仍未适配。** 详见[兼容性与验收边界](docs/MANTA_COMPATIBILITY.md)。`doctor.readiness` 和 `capabilities.current_environment` 报告本机版本状态，不能代替内容或玩法验收。

| 内容 | 入口 |
| --- | --- |
| Agent指令 | [SKILL.md](SKILL.md) |
| 当前真实能力与限制 | [docs/STATUS.md](docs/STATUS.md) |
| 多台电脑的安装、更新、工作区与发布规则 | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| 开发需求、路线、贡献约定 | [REQUIREMENTS](docs/REQUIREMENTS.md)、[ROADMAP](docs/ROADMAP.md)、[AGENTS.md](AGENTS.md) |
| 全部操作流程 | [references/workflow-index.md](references/workflow-index.md) |
| 仓库公开前的评估 | [docs/PUBLIC_RELEASE_REVIEW.md](docs/PUBLIC_RELEASE_REVIEW.md) |

## 作者发布与维护的 Mod

- [Create an intruder](https://steamcommunity.com/sharedfiles/filedetails/?id=3366718295)
- [大地崩解：地下基岩 | Earth Shatter: Bedrock](https://steamcommunity.com/sharedfiles/filedetails/?id=3825464145)
- [开局种族转换·中文拓展版 | Starting Transformations CN](https://steamcommunity.com/sharedfiles/filedetails/?id=3826138175)
- [龙族转化能飞 | Draconian Transformation Can Fly](https://steamcommunity.com/sharedfiles/filedetails/?id=3828760081)

这些是独立的工坊作品；各作品的依赖、适用版本和署名以对应工坊页为准。

## 在另一台电脑开始

访问受限时，在该电脑用 `gh auth login` 登录有权访问仓库的GitHub账号；不要复制其他电脑的凭据。

```powershell
# 在目标Mod项目根目录运行：实际仓库保存在本项目内
$skillDir = Join-Path (Get-Location).Path '.agents/skills/aow4-mod-audit'
gh repo clone RusianHu/aow4-mod-toolkit $skillDir -- --branch main

# 独立Python环境；不把虚拟环境或缓存放进分发包
$runtimeDir = Join-Path $env:LOCALAPPDATA 'AoW4ModToolkit/venv'
py -3.10 -m venv $runtimeDir
$pythonExe = Join-Path $runtimeDir 'Scripts/python.exe'
& $pythonExe -m pip install -e "${skillDir}[localization,analysis,assets]"

# 在目标Mod工作区运行，先检查本机
& $pythonExe "$skillDir/scripts/aow4_mod_audit.py" doctor
& $pythonExe "$skillDir/scripts/aow4_mod_audit.py" native-status
```

推荐直接安装在 `<Mod工作区>/.agents/skills/aow4-mod-audit`。整个目录同时是Codex项目级Skill和独立Git开发仓库，保留根SKILL.md、agents、scripts、references、tests等相对结构。开发、测试和Git更新在这一份源码中进行；Mod源码、构建产物和验收材料放在目标Mod工作区。

Codex当前官方文档列出的项目自动发现位置是 `.agents/skills`，上述目录直接参与发现。参见[官方Skill文档](https://learn.chatgpt.com/docs/build-skills)。默认允许自动匹配，也可显式使用 `$aow4-mod-audit`。

其他电脑按同样相对路径克隆；需要稳定基线时检出已发布tag。仓库访问权限与Python/游戏环境在每台电脑上独立配置。跨项目共用可选择用户级 `~/.agents/skills`，避免同名用户级/项目级副本同时安装。

下载Release ZIP时，把其中的 `aow4-mod-audit` 整个文件夹解压到本项目 `.agents/skills`。ZIP不包含Git历史，更新时使用新目录替换；长期开发优先Git克隆。

## 版本敏感性

**游戏、编辑器、启动器或SDK更新，包括小型热修复，都可能需要二次适配。** 支持绑定精确文件哈希与具体操作，不是对同名版本或所有Mod的通用兼容承诺。未知版本保持拒绝；不要只替换哈希沿用旧偏移。更新后先运行 `doctor`、`native-status`，再重建索引/schema与计划，并核验实际所需流程。详见[版本工作流](references/game-version-workflows.md)。

## 运行要求

- 核心查询：Python3.10+，仅标准库；本项目实际回归环境为Windows/Python3.10。
- 原生编辑/构建：Windows x64、自行安装的AoW4及哈希匹配的官方编辑器/SDK。Witch历史配置与Manta的资源配置分别授权；以 `native-status.available_operations` 和[当前兼容性](docs/MANTA_COMPATIBILITY.md)为准，未知版本明确拒绝。
- 可选Python依赖由 `pyproject.toml` 的localization/analysis/assets组固定版本；安装全部组可运行完整合成回归。
- 启动器真实界面验收另需Node.js22+；Steam工坊实际操作需本机Steam账号与条目权限。纹理压缩、着色器分析、在线版本查询等工具按相应reference单独准备。

每台电脑都需重新运行环境/版本/启动器检查。相同源码版本不证明不同机器环境已经通过。[多机部署](docs/DEPLOYMENT.md)列出了哪些可以同步、哪些必须本地重建。

## 分发与开发

仓库：[RusianHu/aow4-mod-toolkit](https://github.com/RusianHu/aow4-mod-toolkit)。`main`是分发基线。原创代码与文档采用[MIT](LICENSE)，范围与依赖见[第三方声明](THIRD_PARTY_NOTICES.md)。这是独立社区工具；官方游戏、DLL/SDK和第三方可执行文件不随包提供。

Release ZIP及SHA-256从已提交的Git快照生成：

```powershell
python scripts/package_skill.py --output "<仓库之外的新目录>"
```

公开资料只保留脱敏经验，不分发本机调试记录、私有案例、原生缓存、数据库或收据。仓库历史清理后，旧克隆应重新克隆，不要合回旧分支/tag；运行时产生的证据留在使用者的私有工作区。

测试：`python -m unittest discover -s tests -p 'test_*.py'`。回归使用合成/隔离输入和模拟传输；GitHub CI不携带游戏，也不证明实际游戏、编辑器UI或Steam发布通过。
