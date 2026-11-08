# Regular city building and income workflow

```powershell
python "<skill>/scripts/recipes/prepare_building_example.py" --output "<new project>" --index "<catalog.sqlite>" --schema "<schema.json>" --cache "<native cache>"
```

The reviewed Witch 1.1/build 25750198 recipe creates **Frost Archive / 霜寒档案馆**, a regular city building. Five definitions have independent IDs: building, income property, identity property, icon and property-unlock settings. It provides 15 flat Knowledge and uses the original tier-one cost data, research-building requisite and research category. A unique Upgrade Name/DataStoreTag derives from a fresh ID. Random-start and free-building-pickup eligibility are explicitly disabled. Native getters distinguish it from a sector improvement/enhancement; it cannot be built repeatedly by default.

The declared entry has two matching sides:

1. Original White Witch research `0000041B000006C3` grants subskill `0000041B000006C5`, which grants city property `000003E300006083`.
2. The building requires that property in Must Have Structure Properties. A new `TPropertyUnlockSettings` points from the same property to the new building for the unlock declaration.

The recipe retains original resources and adds no ACP overrides. Costs, requisite and unlock reference shapes are taken from same-class original templates and their target types are checked. It does not modify the research itself or assert an executed production-list query. Name/description have separate English and Chinese catalogs. The original Library icon is reused; there is no new bitmap/model, boost entry, or copied Library callback logic.

## Empty building callback initialization

```powershell
aow4mod resource-new-plan "<Mod>" --type 004714AD --name "<building>" --into-pack "Packs/Buildings.rpk" --initialize-empty-script --output "<plan.json>"
```

This explicit option is restricted to newly appended `TStructureUpgradeR` objects. The factory's four empty `TTriggerSettings` callbacks lack owner bindings. Official first RPK loading supplies the new building ID and changes the callback handle kind. The planner accepts only the reviewed B/C/D/E callback set, unchanged expressions/trigger values, exact empty function GUID/sentinel bytes and the new resource's own ID. Other parent fields, resources, categories and ordering must be preserved. A second independent RPK load/save must be stable. Apply reconstructs the constructor append and rechecks the exact initialization before staging. The first difference is recorded explicitly, rather than treated as an arbitrary normalization exception.

Cloning the complete original Library is currently refused because its script callback owner IDs are opaque to the clone rewriter. The recipe creates a new ordinary building through the official factory and authors its fields intentionally; it does not clear or silently discard the original Library script. It also does not use the old Convent research chain, which is no longer the current Tome entry.

## Existing flat-income rows

```powershell
aow4mod income-plan "<Mod>/Packs/Buildings.rpk" --id "<income property ID>" --resource 000003D400000363 --amount 15 --source-root "<Mod>" --output "<plan.json>"
aow4mod income-apply "<plan.json>" --output "<new staged Mod>"
```

Supported scope is an existing `TColonyIncomeProperty` Income Flat member, containing 1–128 reviewed `TIncomeResourceValue` rows. Select one unique global resource ID already in that table. Values are signed 32-bit integers; zero follows the native default omission. Adding/removing rows, percent/multiplier tables and other income classes are outside this command.

The public primitive property API does not expose these rows. A child constructs a legitimate parent, deserializes its body, observes the owned block/row types and signed 32-bit values, and edits the selected row through official object IO. It does not fabricate pointers, write object memory directly or claim use of a public scalar setter. A separate child reads the final body without editing. Exact row/resource-handle/parent comparisons, whole-RPK read/save and Mod/context hashes bind the plan; apply reconstructs all changes. Diagnostics remain review findings.

## Acceptance boundary

The recipe produces native object/pack and declared-entry evidence. Installation/restart/withdrawal use [project preview](project-preview-workflows.md). These checks do not execute production-list lookup, research progression, final income application, rendering or gameplay; the recipe performs no Workshop upload.
