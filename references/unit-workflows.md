# Regular unit variant workflow

```powershell
python "<skill>/scripts/recipes/prepare_unit_example.py" --output "<new project>" --index "<catalog.sqlite>" --schema "<schema.json>" --cache "<native cache>"
```

This reviewed Witch 1.1/build 25750198 recipe creates **Frost Sentinel / 霜寒卫士**, a variant of the original internal `White Witch - Unit` definition. It is a three-definition project: new regular unit, new `TCultureUnitLinkData` registration and a copied original icon. IDs and locators are fresh. The unit receives a unique DataStoreTag through the official setter, English/Chinese name and description, and a Hit Points **stat modifier** change from 5 to 20. This does not mean final combat health equals 20.

Original abilities, costs, tier, visuals, culture/subculture constraints and recruitment prerequisites remain inherited. The recipe separately verifies:

1. Research `0000041B000006C3` grants subskill `0000041B000006C5`.
2. That subskill grants city property `000003E300006083`.
3. The new unit retains exactly the original required-structure-property links.
4. The copied culture-unit registration points exclusively to the new unit, preserving the original record's other constraints.

The original `TCultureUnitLinkData` record is essential entry data; copying just the unit and its prerequisite links is not the full example. The earlier two-definition experiment remains preliminary evidence only. The final recipe copies the registration together with the unit so recognized internal references are remapped atomically. It adds no original overrides and creates no import back edge into the new RPK. Original random encounter unit sets are not extended by this recipe.

All source hashes and native types are pinned. Native setters, differential field mappings, separate reader processes, full RPK read/save and build gates verify the data. Original templates and the output variant may differ only in explicitly allowed identity/tag/name/description/icon/stat fields. The independent native localization witness recognizes both retargeted text keys. A separate Penguin-template control exercises an actually omitted description key and verifies its initialization. Internal receipts and PO files do not enter the five-file runtime payload.

The candidate remains review-required with 57 inherited diagnostic candidates. The workflow does **not** execute the game's production-list query, recruitment, final stat calculation, rendering or combat, and it performs no Workshop submission. User-waived gameplay tests must not be reported as performed. It is a regular-unit template variant, not a general new race/hero/animation or all-unit authoring system. The [regular building workflow](building-workflows.md) has its own scoped example; general building rules and the outstanding equipment-manager context remain separate work.

## Native edits to existing unit stat modifiers

```powershell
aow4mod native-edit-plan "<Mod>/Packs/Units.rpk" --id "<unit ID>" --source-root "<Mod>" --embedded "0x00471428/0x61" --name "Hit Points" --property "0x0047142D/0x9" --value 20 --output "<new plan.json>"
aow4mod native-edit-apply "<plan.json>" --output "<new staged Mod>"
```

Only the reviewed existing `TBaseUnitR` → `TUnitStats` member is allowed. The selected stat body must contain exactly its expected class scope. The official factory constructs this value object; the tool does not fabricate pointers or resource identities. Supported scalar getters/setters retain the normal differential mapping and separate-reader checks. Only the selected inline body is replaced in the parent resource, and unrelated parent/sibling data is preserved. Hero subclasses, missing inline members, other inline types, defense/damage-reduction tables and complex list editors remain unsupported by this path.

The native DataStoreTag property uses `meta/0x12C` in a normal `native-edit-plan` values file. Other identity/name/locator metadata remains excluded. Changing an existing tag does not rewrite arbitrary script lookups by name; the example assigns a unique tag to a new unit instead of renaming the original.
