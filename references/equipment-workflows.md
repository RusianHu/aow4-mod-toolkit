# Equipment example and localized text links

This workflow is a reproducible, unpublished example for the pinned Witch 1.1 / Steam build 25750198. It supplements the general [authoring commands](authoring-workflows.md). It does not cover every equipment type or certify actual item drops.

## Build the example

```powershell
python "<skill>/scripts/recipes/prepare_equipment_example.py" --output "<new workspace>" --index "<catalog.sqlite>" --schema "<schema.json>" --cache "<native cache>"
```

The recipe creates a blank native project, copies five original definitions with fresh IDs (item, icon, property, modifier and equipment set), and gives the item a unique DataStoreTag. The actual native getter verifies that tag. A native setter changes Critical Chance Bonus from 0.05 to 0.10; the official hint defines 1 as +100%, so the example adds ten percentage points. Differential field evidence and another process verify the value.

The item reuses the original Lucky Ring artwork, tier, slots, tags and attack filters. It has its own English and Chinese name/description. Both catalogs are compiled and independently checked with GNU gettext. Literal descriptions avoid interpolating the original template's preserved text parameters. This example creates no new bitmap or model.

The copied equipment set retains only the new item. Its SetName becomes `RING_T1_SET`, and its actual native Override Priority remains 0. The original reward chain is captured as:

`Battlefield Remnants Pickup Reward → Items Loot → Small Item Reward Category → SMALL_HIGHLANDER_SET → RING_T1_SET → item`

Two transitions use serialized names rather than resource IDs. The recipe verifies both and refuses missing edges. It also records the official base/Empires and Ashes pattern: two nonempty equipment sets have the same `HIGHLANDER_EQUIPMENT_SET` name and default priority 0. The example follows that additive pattern and has **no original resource overrides**. This is source-pattern evidence; it does not directly execute or prove the engine's set union, reward probability, map spawn or forge eligibility. Do not label R03 complete from this example alone. The DLC file is a read-only template-pattern witness, not content bundled into the Mod or proof of DLC ownership requirements.

Each source file and the native DLL are pinned. A changed game build requires review and adaptation rather than silently reusing old assumptions. Intermediate stages, plans and independent witnesses remain in the workspace. The release contains ACP, RPK, metadata and two MO files. PO files and internal receipts are excluded. It is not installed, activated or uploaded by the recipe. Exit code 1 with `built_review_required` means the candidate was built with findings, not that it was published or approved.

## Retarget existing localization links

Save explicit assignments as JSON:

```json
[
  {"id":"0123456789ABCDEF","property":"0x00471D18/0x1","key":"MY_MOD@RING@NAME"},
  {"id":"0123456789ABCDEF","property":"0x00471D18/0xF","key":"MY_MOD@RING@DESCRIPTION"}
]
```

```powershell
aow4mod localized-link-plan "<Mod>/Packs/Items.rpk" --source-root "<Mod>" --values-file "<assignments.json>" --output "<new plan.json>"
aow4mod localized-link-apply "<plan.json>" --output "<new staged Mod>"
```

The commands accept an exact ACP/RPK file and 1–32 direct fields. By default each must already contain the observed nonempty string-key + TEStringKeyReplacer envelope. New keys use up to 240 uppercase ASCII identifier characters. The replacer object, resource identity, unrelated fields and resources remain unchanged. This command changes keys; it neither creates a catalog nor edits its translations or replacement parameters.

An assignment may explicitly use `"initialize": true` for an existing empty localized link: either an omitted key with its original replacer object, or a serialized empty key. The new key is added while the replacer bytes remain exact. Nonempty links reject initialization; missing whole fields or unknown envelopes still require a reviewed template. A separate original-Penguin control exercises this path and independently verifies the new description key through the native localization enumerator; the White Witch variant already has a nonempty description key and uses ordinary retargeting.

For the observed omitted-key form, the pinned native reader only preserved the initialized link when key 1 preceded replacer 2 in the encoding. A direct append after key 2 passed the Python offset-map parser but failed native readback. Initialization therefore emits the reviewed 1→2 order, while retaining the exact replacer payload. This is a localized-link rule, not a claim that every AoW4 offset map may be reordered globally. The failed append control is retained with the passing ordered control.

Planning requires native object readback and, for RPK, whole-pack save comparison. Apply derives the edits again from the original source, checks the complete Mod inventory, generator/native bindings and independent artifacts, and writes a new directory. Missing/ambiguous IDs, plain strings masquerading as localized links, duplicate fields, changed parameter objects, unrelated candidate edits, missing witnesses and source changes fail.

## Native language context

New native RPK builds select default `Language/*.mo` and English `Language/EN/*.mo` catalogs. Other languages are compiled/checked by gettext but are not simultaneously loaded into the English native host. Different catalogs sharing one filename are ambiguous and refused. `EN.MO` is reserved for the original game catalog.

The worker validates MO syntax and hashes, stages exact bytes under uppercase filenames in its private run directory (required by the native content namespace), loads them alongside the original English catalog, and retains the source hash bindings. Frozen RPK evidence records the selected payload-relative catalogs and rechecks those hashes offline. This is language lookup context plus RPK data preservation; it is not a rendered-text or complete Lingoona grammar check. Historical reports without language context retain their previous scope.

## Open findings

The accepted example retains 57 original asset/bone diagnostic candidates and remains review-required. No diagnostic is dismissed merely because it was inherited. The earlier broad reward-pack override attempt failed: the native loader/save dropped the original Guarded Cage set's Lightbringer reference. Read-only preloading of Tome_Inquisition did not restore it; the cause remains unresolved. That experimental preload code was not retained as a supported feature, and the failed attempts remain separate evidence. The additive example does not repair or certify that original reward package.

## Declared sets and current lifecycle result

`equipment-sets RING_T1_SET --mod <candidate> --required-item <new item ID> --output <new report.json>` reviews original declarations from the hash-checked game index plus explicitly selected Mods. Repeat `--mod` for other enabled Mods. `--limit/--offset` page the expected union IDs; the full file keeps source hashes and the selected/shadowed named sets. ACP inputs also protect their containing Mod directory from report output. Conflicting same-ID definitions from multiple Mods are refused rather than resolved by an assumed load order.

The rules follow the pinned DLL's `FC1170`/`FC1210` functions: case-sensitive names, maximum priority with ties, and recursive subsets. Missing subsets, cycles and an absent required item remain findings. This command is **declaration review**, with `engine_lookup_executed=false`; the engine's allowed-resource context and DLC eligibility are not inferred. Native-factory equipment-manager/hash-map controls are empty. A legitimate populated manager/context ABI is still required for direct positive engine execution; do not forge nodes, pointers or object IDs to bypass this gap.
