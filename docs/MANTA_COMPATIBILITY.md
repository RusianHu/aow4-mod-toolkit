# Manta版本兼容性

本页列出源码配置的支持范围，不是Steam服务器最新版本查询或所有Mod的兼容证书。源码 **0.4.0.dev8** 沿用对 **Manta 1.1 Hotfix / Steam Build26482553** 的限定资源配置；参见[官方更新公告](https://steamcommunity.com/app/1669000/announcements/)。

**版本变化可能要求二次适配，热修复也不例外。** 游戏BuildID、编辑器DLL、ResourceEd、启动器与SDK是不同输入。相同展示版本或BuildID不能替代文件哈希，已找到配置也不能代替具体操作可用。

## 支持矩阵

| 领域 | 配置范围 | 保留边界 |
| --- | --- | --- |
| 原生资源 | types、properties、localization-witness、roundtrip、construct、object-roundtrip、object-edit、package-apply、package-dependencies、income-read、income-edit | 每个输入仍需上下文、完整数据比较与独立读回；不是任意类/复杂字段支持 |
| schema与解释 | 当前ResourceEd提取配置及限定玩法规则 | 未解析枚举、嵌套储存、复杂选择及最终提示不自动获得支持 |
| ACP默认省略 | TEContentPackage的空描述/作者/注册包列表、minor=1、major=0 | 仅该类/字段/值；minor=0、非默认值、UUID、未知字段及资源数据继续严格比较 |
| 原生素材/地图 | Manta当前未审核 | CLB、纹理、FBX、骨架、动画、材质、地图原生调用保持拒绝 |
| 官方编辑器桥接 | 当前可执行文件桥接未审核 | DLL资源支持不授权GUI命令行/收集流程 |
| 启动器 | 2026.13的应用/schema/迁移配对 | 指定操作、官方重启保持及最终启用须在对应机器核对 |

`native-status.available_operations`给出本机允许的操作。父进程与子进程都执行允许列表检查，缓存不能绕过。Witch与较早Manta配置保留各自授权，不继承后续版本的新增规则。

## 文件身份

| 文件 | SHA-256 |
| --- | --- |
| DotNetBindings.dll | `9f7ff162d817073eb04a1b514f8e51ec0233a7ce48c586f2433940130de0036e` |
| ResourceEd.exe | `16e19fa50154c243f92333e795ae2f789a4c952588aa42cafc911d48af82f896` |
| ContentEd.exe（桥接未审核） | `d794a753985e764ec7892654e90d639927a320d5e6b92d5c5f2f2ab1dc85ebe8` |
| LevelEd.exe（桥接未审核） | `bafeae2bd4686936355d91ff810dd5709dc905673dbc9b872117fdcf13a5f940` |
| launcher 2026.13 app.asar | `b10d6cab49e7b6154723efa42cf2bd281746ca25cba464a11ef3952aa7c5e226` |

启动器schema：`2dda38e45475a91928ebe772d9f6864897a1c6c57220dec7fc0270af62f2982c`，对应37项已审核迁移。未知应用/迁移拒绝写入，不删除迁移记录或降级数据库。

## 保留的检查与限制

显式MO/RPK上下文绑定名称和哈希；不能暗中导入用户ACP。嵌套偏移编码只有在完整无歧义比较相等时才单列为编码差异；资源身份、数组顺序、引用、缺字段和不透明字节仍参与比较。准备阶段改变不透明粒子/元数据时拒绝继续，并将差异保存在执行者的私有运行目录，不能因为原版也发生变化而忽略。

生成的新schema、索引及计划须对应当前输入。已审核原生接口不等于所有诊断已解决；原生素材/地图、完整内容、最终挂载和玩法仍按各自范围验证。公开源码不包含本机调试记录；验证方法见[VALIDATION](VALIDATION.md)，更新步骤见[版本流程](../references/game-version-workflows.md)。
