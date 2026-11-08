# 完整工作流索引

先运行 `capabilities <area>` 选择命令，再按本表只读相关流程。开发状态见 [STATUS](../docs/STATUS.md)，不是通过命令数量推断完成率。

## 按任务读取

| 任务 | 命令/文档 |
| --- | --- |
| 资源类型/枚举、脚本签名、瞬发/持续宿主、开局与跨Mod标签契约 | [资源与玩法检查点](authoring-checks.md)；按所改领域选择小节 |
| MO命名空间、实际语言装载顺序、富文本与二级提示入口 | [本地化检查点](localization-checks.md) |
| CLB依赖、图标运行时表/mip、动画与源数据核对 | [素材检查点](asset-checks.md) |
| 安装哈希、启动器数据库、封面、工坊重试与证据清理 | [安装发布检查点](deployment-checks.md) |
| 运行环境、独立仓库、测试、工作区兼容入口 | [运行与仓库](runtime-workflows.md) |
| 当前安装版/Steam分支BuildID、真实在线查询与旧日志比较 | `game-version`；[版本范围](game-version-workflows.md) |
| 中文/词典/文本键/DataStoreTag、当前魔典成员与有界多跳引用 | `loc-catalogs`、`loc-search`、`search`、`describe`、`trace`；[内容调查](content-research-workflows.md) |
| 实际执行链、费用与条件解释、共享影响、嵌套原生值和内容快照 | `explain`、`impact`、`native-inspect --object`、`content-snapshot/compare`；[玩法调查](gameplay-analysis-workflows.md) |
| 加载成功但运行失败、脚本条件/结果归属、时序与用户复测取证 | [运行时排错](runtime-diagnosis-workflows.md)；方法说明，不代表新增存档或通用原生接口支持 |
| 查询、引用、快照、三方比较、冲突、暂存编辑、迁移 | [CLI 工作流](cli-workflows.md) |
| 多资源选定字段复制/恢复、显式原版省略值、嵌套差异和受限原生排序采用 | `field-copy-plan/apply/canonicalize`、`resource-diff`；[字段恢复](field-copy-workflows.md) |
| 现有 Mod 的统一更新检查、暂存、原生构建和安装/发布准备 | `maintenance-start/assess/stage/build/status/prepare-release`；[维护工作流](maintenance-workflows.md) |
| 同身份维护副本的真实启用、重启观察和完整撤回 | `scripts/maintenance_preview.py prepare/apply/observe/withdraw/status`；[维护预览](maintenance-preview-workflows.md) |
| 新项目安装、官方启动器重启观察与完整撤回 | `scripts/project_preview.py prepare/apply/observe/withdraw/verify-withdrawal/status`；[新项目预览](project-preview-workflows.md) |
| 维护审查结论、未解决项及预览原始证据绑定 | `maintenance-review`；[审查证据](maintenance-review-workflows.md) |
| 创建/导入项目、原生新资源、覆盖、克隆、分类、生命周期、构建、本地化 | [作者工作流](authoring-workflows.md) |
| 装备示例、命名奖励集合、本地化键及文本目录上下文 | `scripts/recipes/prepare_equipment_example.py`、`localized-link-plan/apply`；[装备工作流](equipment-workflows.md) |
| 普通单位变体、文化单位登记、原生内嵌数值和独立标签 | `scripts/recipes/prepare_unit_example.py`、`native-edit-plan --embedded`；[单位工作流](unit-workflows.md) |
| 普通城市建筑、研究解锁和原生收入行编辑 | `scripts/recipes/prepare_building_example.py`、`income-plan/apply`；[建筑工作流](building-workflows.md) |
| 复制有效资源模板/包内引用闭包、空引用初始化 | `resource-copy-plan/apply/map`、`link-init-plan/apply`；[模板工作流](template-workflows.md) |
| 字段、存储格式、枚举来源 | `schema-search`、`schema-extract --refresh`；[字段工作流](schema-workflows.md) |
| 官方类型/构造默认值、文件实际标量、RPK 原生另存 | `native-status/types/properties/inspect/check/roundtrip`；[原生接口](native-workflows.md) |
| 官方 setter 标量编辑及独立进程读回 | `native-edit-plan/apply`；[原生编辑](native-edit-workflows.md) |
| ACP 覆盖的显式顺序应用、原生构建证据 | `native-merge-check`；[ACP 应用](overlay-workflows.md) |
| 显式 Mod 集合、DLC、素材、循环导入 | `dependencies`、`dlc --steam`；[环境检查](environment-workflows.md) |
| CLB/YAML、库内依赖、选择性模板提取、PNG 图集、图标连接 | `clb-*`、`asset-extract`/`asset-*`、`image-*`；[素材工作流](asset-workflows.md) |
| 静态 FBX 草稿库、模型/材质创建、实际模型链接 | `mesh-import-plan/apply`、`model-bind-plan/apply`、`native-model-check`；[网格工作流](mesh-workflows.md) |
| 单 take 骨骼动作导入为独立动画 CLB、完整共享关键帧核验 | `animation-import-plan/apply`；[动画库工作流](animation-workflows.md)；模型绑定/完整姿态尚未完成 |
| 线性 FBX 均匀烘焙、局部动画通道采样与离线复核 | `animation-import-plan --bake-fps`、`animation-tracks`、`native-animation-poses`、`animation-poses-verify`；[烘焙和局部通道](animation-pose-workflows.md)；不认证完整层级姿态 |
| 既有骨架模型的同库默认动作绑定、模型局部层级及骨骼姿态 | `model-animation-plan/apply`、`native-model-poses`、`model-poses-verify`；[模型动作工作流](model-animation-workflows.md)；尚不认证蒙皮/游戏挂接 |
| 顶点骨骼权重编辑、原生部件映射及离线 LBS 位置参考 | `skin-weights-plan/apply`、`model-skinning-check/verify`、`shader-toolchain`；[权重与位置参考](skinning-workflows.md)；不执行 GPU、不认证运行时变体 |
| 指定动作姿态烘焙为新静态模型、可选切线重建/同材质部件合并及城市部件构建示例 | `model-pose-bake-plan/apply`、`model-skinning-check --directions`、`scripts/recipes/prepare_pose_bake_example.py`；[姿态烘焙](pose-baking-workflows.md)；保留渲染/变体门槛 |
| 从 FBX 创建新骨架模型/网格/Default 材质/动作草稿，显式权重处理及完整原生/CPU 读回 | `skeletal-import-plan/apply`；[骨架作者流程](skeletal-authoring-workflows.md)；局部几何/权重已有源核对；世界/绑定空间、动态包围盒、实际变体与动画入口仍未认证 |
| 源FBX与实际CLB的局部几何/权重/默认层级核对、源层级修正及构建/冻结重放 | `skeletal-source-check/verify`；[源文件核对](source-correspondence-workflows.md)；不认证源动画、世界/绑定空间或渲染 |
| 源动画关键帧/帧间FK与原生矩阵对照、带末端保护的float32旋转 | `source-motion-check/verify`、`skeletal-import-plan --rotation-storage float32-guarded`；[源动作核对](source-motion-workflows.md)；仅证明采样误差预算，不认证连续轨迹或根运动/渲染 |
| 材质到效果的实际连接、独立对象读回和构建门槛 | `native-material-check`；[材质连接](material-workflows.md) |
| 材质到静态纹理的实际指针、独立纹理对象及冻结核验 | `native-material-textures`；[材质连接](material-workflows.md) |
| 独立 PNG 纹理、按名称编辑材质参数及静态素材完整示例 | `texture-plan/apply`、`material-parameters`、`material-plan/apply`、`scripts/recipes/prepare_static_asset_example.py`；[材质作者流程](material-authoring-workflows.md) |
| 色彩空间正确的 mip 链、数值纹理和 RGB XYZ 法线贴图 | `texture-plan --role --mips`；[纹理 mip/法线](texture-level-workflows.md) |
| BC1/BC3/BC7 颜色、BC5 有符号法线压缩和 DDS 导入 | `texture-toolchain`、`texture-dds-plan/apply`、`texture-compress-plan`；[DDS/压缩](compressed-texture-workflows.md) |
| 单位/建筑 TGCM 网格部件入口与默认材质 | `component-model-plan/apply`；[网格工作流](mesh-workflows.md) |
| 特效声明、Pass 程序关联、DXIL 输入及原生 CPU 证据 | `effect-variants`、`effect-programs`、`dxil-inspect`、`native-effect-programs`；[着色器检查](shader-workflows.md) |
| 材质条件的官方 CPU 求值、动态默认值假设及条件程序查询 | `material-conditions`；[条件求值](material-conditions-workflows.md) |
| 战术地图 TAM、网格/已知世界实体/实际坐标、部署区复制、逃跑点移动及地形纹理权重 | `map-inspect`、`map-entities`/`map-world-entities`、`native-map-*`、`map-metadata/direction/position-plan/apply`、`map-entity-copy-plan/apply`、`map-cell-move-plan/apply`、`map-terrain`/`native-map-terrain`、`terrain-weights-plan/apply`；[地图工作流](map-workflows.md) |
| 未覆盖资产的官方编辑器工作副本与结果收集 | `editor-prepare/start/status/collect`；[编辑器桥接](editor-workflows.md) |
| 发布预检/更新/首次发布/恢复/下载核验，本地同步/回滚，启动器播放集 | [发布工作流](release-workflows.md) |
| 候选官方启动器的副本迁移、界面、重启及回滚验收 | `scripts/launcher_acceptance.py prepare/run`；[启动器验收](launcher-acceptance.md) |
| 官方安装包只读解包、用户级运行时、启动器入口切换/回滚 | `scripts/launcher_runtime.py`、`scripts/launcher_location.py`；[运行时工作流](launcher-runtime-workflows.md) |
| 真实 Steam 启动器迁移、安装/重启/完整撤回验收 | `scripts/launcher_session.py prepare/run`、`launcher-plan --unregister --registration-receipt`；[真实会话](launcher-session-workflows.md) |
| 二进制解析扩展、未知格式 | [格式证据](format.md) |
