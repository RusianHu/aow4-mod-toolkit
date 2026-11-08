# 分发策略与公开范围

原创工具代码和文档采用[MIT](../LICENSE)。[第三方声明](../THIRD_PARTY_NOTICES.md)列出单独安装的依赖与官方游戏/SDK的权利边界；项目不是官方产品，不提供厂商背书或对专有内容的额外许可。

## 公开资料

- 保留源码、合成测试、版本配置、可重现命令及脱敏经验。维护者的GitHub作者姓名和邮箱可以保留。
- 原始本机调试记录、私有案例包/身份绑定、历史验收流水、数据库、收据、游戏/SDK二进制及凭据不分发。运行命令生成的诊断仍由执行者在私有工作区保留，不能因此改写失败或清除门槛。
- 安装包必须包含双语README、MIT许可证和第三方声明；分发检查拒绝原始报告/缓存及游戏二进制。

## Git与托管平台

公开仓库为独立创建的 [RusianHu/aow4-mod-toolkit](https://github.com/RusianHu/aow4-mod-toolkit)，使用无父提交的干净源码基线，不通过fork或旧仓库模板建立。Skill名称及安装目录仍为`aow4-mod-audit`。新旧仓库对象隔离和匿名访问应单独验证，不将旧分支、tag或私有证据推入公开仓库。

清理必须覆盖可达Git历史、tag、旧发布附件和历史Actions记录；只修改当前文件或`.gitignore`不足以清理旧副本。历史被重写后，持有旧克隆的维护者应重新克隆，不把旧分支/tag合回公开基线。

GitHub可能保留未引用对象、缓存或其他人的克隆；工具不能保证服务器物理擦除，参见[GitHub清理说明](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)。仓库可见性和清理可达内容是不同操作，本页不表示GitHub可见性已变更。

## 版本与验证

**AoW4、编辑器、启动器或SDK更新后，可能需要重新适配，即使只是热修复。** 在重新审核前，未知文件哈希保持拒绝。用户应运行doctor/native-status，重建索引/schema及计划，并验证实际需要的操作；不能替换哈希后直接沿用旧偏移或把旧版验收推广到新版。

源码开发预览、安装包版本、游戏BuildID和各原生配置分别标记。详见[当前状态](STATUS.md)、[兼容性](MANTA_COMPATIBILITY.md)及[验证方法](VALIDATION.md)。CI只证明其覆盖的合成/分发检查，不证明游戏加载、渲染、存档或多人。
