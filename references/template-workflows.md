# Resource templates and empty references

Use explicit templates when a resource's native empty constructor cannot supply required nested objects, or when creating a variant of working content. This route successfully creates `TGameIconR` objects; its blank constructor remains unsupported. It is separate from whole/selected RPK cloning into a new pack.

## Copy into an existing pack

```powershell
aow4mod resource-copy-plan "<Mod>" --template "<source.rpk>" --resource-id "<ID>" --into-pack "Packs/Own.rpk" --category "My icons" --name "My icon" --output "<new plan.json>"
aow4mod resource-copy-map "<plan.json>" --limit 20
aow4mod resource-copy-apply "<plan.json>" --output "<new staged Mod>"
```

Repeat `--resource-id` to copy a group. `--with-local-dependencies` follows recognized links only within the one template RPK, including cycles. The limit is 256 definitions per plan. `--name` requires exactly one final selected resource; otherwise `--name-prefix` (default `Copy - `) supplies internal names, with explicit generated names for unnamed originals. Localized display text remains inherited from the template.

The destination must already be reachable from the selected Mod's ACP/import graph. Its existing generic resource container (`0400017F`) uses `--category` and optional `--container`, as in the native-new append workflow. Every selected resource goes into that category. Source category/pack metadata is not merged. Existing destination objects, categories, sibling containers, imports and ACP bytes are preserved except for the explicit append/import additions.

Each copy receives a collision-checked ID and new locator. Recognized links between selected resources update their ID, locator and label together; links to unselected definitions remain original. An occurrence of a selected old ID in an opaque string or unrecognized binary field refuses copying. Do not bypass this by replacing bytes manually.

Copying part of a source pack imports that original pack, which retains access to its other definitions/dependencies. Copying all its definitions retains the original external imports instead. Copying within the same pack adds no self-import. Different data under the same pack name is refused; a byte-identical alias is allowed. Assets, localization catalogs and other source files are **not copied**. For templates from another Mod, declare that dependency or separately stage needed assets and rerun `dependencies`/build checks; a template copy is not an asset compiler.

Planning remaps the definitions, deserializes each into a fresh official native object, compares all serialized fields, and then independently loads/saves the complete destination RPK with bundled CLB context. Apply rehashes the template, source tree, generator and native binding; it rebuilds the copy from the template and checks every object/pack artifact. It does not trust a plan's success flag or saved binary by itself. Old resources cannot be changed by refreshing forged artifact hashes. Changed inputs, late ID collisions, partial native coverage and different native profiles are refused.

Plans include full mappings and proof; normal CLI output shows at most five map entries. `resource-copy-map` pages either a plan or the staged `.aow4mod-resource-copy.json` receipt. Other development changes can expire generator-bound plans; generate a fresh plan rather than editing its hash.

## Initialize an empty direct reference

```powershell
aow4mod link-init-plan "<Mod>/Packs/Own.rpk" --id "<new research ID>" --property "0x00473702/0x4" --template-file "<original.rpk>" --template-id "<original research ID>" --target-id "<new icon ID>" --target-file "<Mod>/Packs/Own.rpk" --old-target-file "<original.rpk>" --source-root "<Mod>" --output "<new init-plan.json>"
aow4mod link-init-apply "<init-plan.json>" --output "<new staged Mod>"
```

Select an exact RPK for resources stored there; passing a Mod directory to link commands selects its ACP overrides. `--source-root` preserves the complete Mod rather than only the pack's parent directory.

The destination and template must have the same resource class and use the **same direct class/property field**. The template must serialize a recognized nonzero reference; its old target is resolved and must have the same type as the new target. There is no cross-class/type bypass. The destination may omit that field, contain an empty map or a recognized zero-ID/empty-locator reference. Nonempty slots, opaque payloads, new class scopes and nested paths are refused. Use `link-plan` for existing references and `link-array-plan` for new array elements.

Initialization copies the proven field encoding, retargets it, records the prior empty form and atomically adds the selected target pack import when needed. Apply reconstructs the change from hash-bound source/template/target evidence. This is a constrained structural operation; run native inspection/build afterward. It does not configure research costs, localized names, grants or a research-tree entry point automatically.

## Acceptance and remaining limits

A real workflow copied an original icon, linked it to local PNG sprite 2, initialized a native research object's icon reference, copied the targeter/City Riot pair with internal remapping, and redirected the existing spell. Five selected objects preserve their data through independent native reads; the six-resource RPK and four-file native build preserve all checked fields. The research object remains a draft without an entry point. Native diagnostics and the inherited voice URI remain review-required. No live game or new Workshop submission occurred.

The run also caught explicit default normalization: storing targeter Friendly Fire=2 explicitly is read back as 2 but omitted by the native writer, so the strict field comparison reports a difference. The current pinned profile independently verifies omitted Friendly Fire as Everything=2; that example uses the omission and verifies the actual value again. `native-edit-plan/apply` now proves requested scalar edits and canonical omission through a differential setter and a separate reader process; prefer this workflow over manually deleting fields. Do not generalize either example to unknown fields or suppress comparison findings. See [native edit workflows](native-edit-workflows.md).
