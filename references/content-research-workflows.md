# 原版和 Mod 内容调查

本页定位内容与词典来源；实际语言装载、翻译覆盖或二级提示问题见[本地化检查点](localization-checks.md)。

`aow4mod` 表示 Skill 入口 `python <skill>/scripts/aow4_mod_audit.py`。以下命令只读取内容并更新工作区缓存，不修改游戏/Mod。先发现词典，再建立所需范围，随后从中文进入资源、文本和引用链。

## 发现与建立索引

```powershell
aow4mod loc-catalogs --limit 10
aow4mod index --links --locale ZH --locale EN
```

`loc-catalogs --mod <folder>` 也列出 Mod 的 MO 候选文件，但不凭文件夹名称断言它的语言或运行时优先级。原版默认目录是 `Language/<语言>/*.MO`；扫描兼容大写扩展名。`index --locale` 读取相应的原版词典，需要可选依赖 `polib`（`.[localization]`）。只索引资源/键/标签时仍不依赖 polib。

Mod 或外部词典用显式语言分配：

```powershell
aow4mod index --links --locale ZH --locale EN --mod "<Mod>" --catalog "ZH=<Mod>/Text/MyText.mo"
```

**每次 index 的参数都是完整范围**：重复全部需要的 `--mod`、`--locale`、`--catalog`，未再指定的缓存输入会移除。首次新版索引会升级本工具拥有的 schema 1 缓存并重建元数据；拒绝修改其他应用数据库。需要独立原版调查时使用 `--index <new.sqlite>`，避免改变维护索引的 Mod 集合。

词典按文件、语言、上下文、复数分支分别保存，源哈希参与增量刷新。目录语言与显式分配仅用于查询；同语言同键的多个来源全部保留，不自动判断游戏选中哪一份。

## 从中文和文本键定位资源

```powershell
aow4mod search "大法师魔典" --locale ZH --exact --origin game
aow4mod search "TOME_ARCH_MAGE@TOME@NAME" --field key --exact
aow4mod search "COSMIC_OVERDRIVE_DAMAGE_MULTIPLIER" --field tag --exact
aow4mod search "Arch Mage" --field name --limit 10
aow4mod loc-search "TOME_ARCH_MAGE@PASSIVE" --field key --locale ZH
```

`search` 默认联合内部名、ID、非空本地化键、DataStoreTag 和已索引文本，按资源分页；`--field name|id|key|tag|text` 缩小范围。`--type`、`--origin`、`--id` 保持支持。`--exact` 是大小写折叠后的完整匹配，`%`/`_` 按字面字符处理。结果 `matches` 给出命中来源和属性路径，文本命中保留词典路径/语言/哈希；摘要最多 20 条，长文本有长度提示。

`loc-search` 按词典条目分页，可找到没有已识别资源使用者的旧文本。`recognized_use_count=0` 只表示当前索引没有识别到使用者，不能证明引擎/脚本永远不使用它。反查某个键的全部已识别资源使用者，用 `search <key> --field key --exact`。原版和 Mod 中同 ID 的定义会分别返回，不选择运行时胜者。

未索引的语言会明确报错；不指定语言时可搜索已索引的全部语言。`coverage.text_search_status=not_indexed` 表示没有词典范围，不能把中文零命中当作内容不存在。畸形词典进入失败记录，绝不沿用旧条目冒充新内容。

## 文本、成员和原始字段

```powershell
aow4mod describe 0000041B000012C4 --locale ZH
aow4mod describe 0000041B000012C4 --section members
aow4mod describe 0000041B00001546 --section texts --locale ZH
aow4mod describe 0000041B0000154F --section fields --limit 10
```

资源 ID 有多个来源时用 `--file <exact.rpk>`，不会根据加载顺序提示猜选。`describe` 按 `summary|texts|members|references|fields` 限制输出；各列表支持 `--limit/--offset`。

- 文本：显示原始键、序列化的占位符/显式替换值、候选译文和未解析表达式。`rendered_tooltip=not_evaluated`，不把这些材料冒称最终游戏提示。
- 魔典成员：只对已识别 `TGridSkillGroup` 使用 `TGridSkill.GroupLink` 规则，成员来自整个索引；同文件未链接此魔典的其他研究分列，不能直接称为废弃。其他资源类型保持 `not_evaluated`。
- 标签：DataStoreTag 的重复定义有总数与分页候选；不能用第一个同名对象填充持续时间等参数。
- 字段：沿用 `show` 的版本绑定 schema 和默认值规则；不会把任意 4 字节猜成浮点效果，也不将未读取的嵌套对象补成 0。

## 引用和有界追踪

```powershell
aow4mod refs 0000041B0000154B
aow4mod refs 0000041B0000154C --reverse
aow4mod trace 0000041B0000154B --depth 3 --max-nodes 40 --max-edges 100
aow4mod trace 0000041B000012C4 --direction incoming --depth 1
aow4mod trace 0000041B00001547 --depth 2 --output "<new-report.json>"
```

平铺原生字段中的已识别链接也进入 `refs`，例如 `meta/0x02`。该路径是 `show` 的结构地址，**不表示玩法无关的元数据**。`link-plan`现已接受这些精确路径，核对目标类型并原子补导入；见[玩法调查与重连](gameplay-analysis-workflows.md)。

`trace` 以文件中的具体资源定义为节点，保留每条引用的字段路径、方向和出现序号。同 ID 多定义以多个候选输出，缺失目标标记 missing。重复的五个状态引用是五条边；循环不会无限展开。深度/节点/边限制分别产生边界记录，不暗示完整闭包。incoming 图的 `from_node` 是当前查询节点、`to_nodes` 是引用它的来源节点，字段路径属于实际引用方。

返回节点、匹配资源、译文及反向引用来源会核对文件哈希，陈旧输入拒绝读取；总数和零命中仍属于索引快照。源文件新增/删除、未返回文件的变化，以及实际播放集装载状态，需重新索引或相应维护/原生流程。

## 保持可见的限制

文本扫描仅识别字符串键加 TEStringKeyReplacer 包络；复杂容器歧义或深度/节点限制会记录在 `text_scan_issues`，已识别部分仍可检索。原生文本见证与检索共用此包络识别，原生见证的遍历上限仍按失败处理。任意含 `@` 字符串不算本地化键。

资源图仍只识别支持的链接编码；裸整数不能自动当资源 ID。查询不证明实际字典选中、Mod胜出顺序、DLC挂载、脚本可达性或玩法。`explain`、`impact`、`native-inspect --object`及内容快照已覆盖部分后续调查，见[玩法调查](gameplay-analysis-workflows.md)；复杂列表/表达式、最终结算和真实二级提示仍未完成。
