---
name: aow4-mod-audit
description: 创建、检查和维护奇迹时代4 Mod，处理资源、素材、本地化、兼容性、构建、安装及Steam工坊发布。使用暂存计划与原生证据；不处理CE表或内存作弊。
---

# AoW4 Mod Toolkit

在用户工作区运行独立 CLI；使用 Python 3.10+，将 `<skill>` 替换为本 Skill 的实际路径：

```powershell
python "<skill>/scripts/aow4_mod_audit.py" doctor
python "<skill>/scripts/aow4_mod_audit.py" capabilities authoring
```

下文 `aow4mod` 代表此前缀。相对路径和缓存属于工作区；无需全局安装。新环境按需查[运行方式](references/runtime-workflows.md)或[部署](docs/DEPLOYMENT.md)。

## 按任务加载

只打开命中的文档/小节，不预加载全部引用。检查点集中维护跨作品经验；需要实施命令时再沿其链接读取流程。命令发现用 `capabilities <resources|authoring|maintenance|assets|release>`，精确查找再用[完整索引](references/workflow-index.md)。

| 当前任务 | 首选参考 |
| --- | --- |
| 从中文、文本键、内部名或标签定位原版/Mod内容 | [内容调查](references/content-research-workflows.md) |
| 创建/修改资源、法术、研究、开局特质、作用域和组合兼容 | [资源与玩法检查点](references/authoring-checks.md)；已有执行链分析用[玩法调查](references/gameplay-analysis-workflows.md) |
| 文本、语言装载、Hyperlink与二级提示 | [本地化检查点](references/localization-checks.md) |
| 图标、CLB依赖、模型、动画与材质 | [素材检查点](references/asset-checks.md) |
| 法术缺失、脚本不触发、生成结果/移动显示异常 | [运行时排错](references/runtime-diagnosis-workflows.md) |
| 原版升级、现有Mod迁移与审查 | [维护流程](references/maintenance-workflows.md)；字段规则见[资源检查点](references/authoring-checks.md) |
| 本地安装、播放集、启动器、工坊发布/恢复 | [安装发布检查点](references/deployment-checks.md) |
| 地图、部署区、地形或实体局部编辑 | [地图流程](references/map-workflows.md) |

## 共同边界

- 先生成绑定输入哈希的可审阅计划，再应用到新暂存；原版、生产 Mod 和工坊下载目录不作开发测试输出。保留未选字段、未知字节、失败诊断和未评估项。
- 结构读回、原生宿主校验、最终 ACP 应用、实际玩法是不同范围。回调、应用标记或退出码不能代替对应证据；安装后核对实际文件。遵守当前任务的实测安排，不把某作品免测/通过推广到其他任务。
- 原生操作依赖匹配的版本与加载上下文。游戏/编辑器的热修复也可能需要重新适配：先看 `doctor.readiness` / `capabilities.current_environment`，再看 `native-status.available_operations`；配置存在不代表全部操作可用。未知版本拒绝执行，不只换哈希沿用旧偏移。更新后按[版本流程](references/game-version-workflows.md)重建索引/schema及计划，再按对应操作验证。
- 安装、提交与可见性变更遵守当前已获授权范围。工坊默认预检，历史授权不自动扩展到新发布；未知上传结果先核对，法律协议由用户本人处理。

## 开发本 CLI/Skill 时

再读[需求](docs/REQUIREMENTS.md)、[路线](docs/ROADMAP.md)、[状态](docs/STATUS.md)及[开发约定](AGENTS.md)。唯一后端为 `scripts/aow4mod/`；普通 Mod 任务无需加载开发历史。作品版本、资源身份和私有证据留在作品目录，不作为 Skill 的外部运行依赖。
