# Authoring workflows

For resource types/defaults, spell hosts, starting grants and compatibility tags, read the relevant section of [authoring checks](authoring-checks.md). Use this page for commands and their supported scope.

Use the target workspace's Python; `aow4mod` below abbreviates `python <skill>/scripts/aow4_mod_audit.py`. Install the Skill's `localization,analysis` extras when needed for gettext and schema extraction; no parent-workspace wrapper is required.

## Start a project

```powershell
aow4mod project-import "<existing Mod>" --output "<new workspace>"
aow4mod project-new --name "My Mod" --author "Author" --output "<new workspace>"
aow4mod project-new --template "<known Mod ACP>" --name "My Mod" --author "Author" --output "<new workspace>"
aow4mod project-check "<workspace>"
```

Import preserves the existing package identity and source files. New without `--template` uses the official native `TEContentPackage` constructor, assigns a new package UUID and verifies the edited metadata through a fresh native object. It creates no gameplay resources or Workshop identity. This requires a supported native DLL profile. `--template` explicitly retains the earlier path: clear overrides/registered RPKs/preview and inherit verified editor flags; unclassified template root fields block that path. These checks do not constitute an in-game playtest.

The initial version is 0.1. Current native serialization omits minor=1 and major=0; this is verified against the deserialized package's actual version members. The authoring config and metadata JSON record the explicit version. A plain structural `inspect` can still show missing serialized version fields; use `native-inspect` for the native interpretation. Constructor defaults must not be substituted for serializer defaults.

The workspace has `aow4mod.project.json` and a `mod/` source directory. Configure `include` as runtime-file globs, `version` as `major.minor`, and optional project-relative `description_file` / `change_notes_file`. `workshop` is initially empty. All build paths stay separate from sources.

## Create a resource from its native class

```powershell
aow4mod native-types StructureSingle --limit 5
aow4mod native-properties TStructureSingleTargeter --query "Friendly Fire" --schema .aow4mod/schema.json
aow4mod resource-new-plan "<workspace/mod>" --type TStructureSingleTargeter --name "My Targeter" --pack My_Targeter --values-file "<values.json>" --output "<new plan.json>"
aow4mod resource-new-apply "<plan.json>" --output "<workspace/stages/new-resource>"
aow4mod project-use "<workspace>" --source "stages/new-resource"
```

`values.json` can be `{"Friendly Fire Type": 1, "Effect Multiplier": 1.25}`. For repeated names, use a list of `{"name": "…", "category": "…", "value": …}` entries. Integers, finite float32, bools, strings and verified enum values are supported. Enum assignments require the matching offline schema; complex object/resource/list fields are refused by the scalar setter. Use staged `link-plan`, `array-plan` and other existing editors to supply relationships afterward.

The class must occur in an indexed original RPK; type evidence is reread and hash checked. The native constructor produces its body, setters are read back, a second native object rereads the serialized body, and the RPK is independently loaded/saved by the official writer. The plan includes a collision-checked ID, pack bytes, category metadata, provenance and native diagnostics. `--category` names the editor group; `--dependency` may repeat for explicit pack/asset imports. Occurrence in original data does not guarantee a complete default constructor: the current blank `TGameIconR` fails native serialization before a plan or Mod stage is committed. Use the verified `resource-copy-plan/apply` route for icon creation; [template workflows](template-workflows.md) also cover grouped copying and empty direct reference initialization.

To add another native resource to an existing local pack:

```powershell
aow4mod pack-layout "<staged Mod>/Packs/Own.rpk"
aow4mod resource-new-plan "<staged Mod>" --type TStructureSingleTargeter --name "Second targeter" --into-pack "Packs/Own.rpk" --category "Resources" --output "<new append-plan.json>"
aow4mod resource-new-apply "<append-plan.json>" --output "<new staged Mod>"
```

`--pack` creates a new pack; `--into-pack` selects an existing RPK by absolute path or relative to the Mod root. The existing file must be inside that Mod, have a unique filename there, and be reachable from its ACP/import graph. Appending supports the generic resource container `0400017F`; use `--container <key>` from `pack-layout` when selection is ambiguous. An exact unique category name selects its container; otherwise a single eligible container is required. Duplicate category names within one container are refused.

Category IDs and resource positions are scoped to their container. Existing arrays, resource bytes, locator gaps, category records (including legacy `00400044`), sibling containers and unknown metadata are preserved. A named existing category gets the next unused locator position; a new category gets an ID above the declared and observed IDs in that container. Original resources with undeclared category IDs remain reported and unchanged. The command does not repair those records or merge categories from a clone.

Planning includes bundled CLBs in the native loading context and requires complete native RPK data preservation. Applying rechecks the constructor artifact, original inventory, builder/native binding and IDs; it rebuilds the expected append from the original RPK before comparing it with the plan and independent native saved pack. Even a valid native roundtrip cannot authorize changes to old resources. ACP registration remains byte-identical for an append. All results are still drafts until required relationships and entry points are authored.

Apply rechecks source inventory, generator/native backend, native artifacts and current IDs, then writes a separate Mod copy and registers the pack without changing existing overrides. Native drafts can still need required references, localization, a domain-specific configuration and a game entry point. Successful serialization does not make a newly empty targeter, skill or unit complete. No creation command publishes it.

For an isolated state artifact before adding a Mod resource, `native-construct <type> --values-file <values.json> --output <new directory>` writes `state.bin` and proof. Package/pack container classes are also available through this lower-level command. See [native workflows](native-workflows.md).

## Add an original-resource override

For native scalar changes to an existing staged object, use `native-edit-plan/apply` as described in [native edit workflows](native-edit-workflows.md). It verifies the native property-to-field association and handles requested default omission through actual setter/readback evidence. Static `edit-plan` remains useful for known codecs, nested structures and explicit opaque edits.

```powershell
aow4mod override-plan "<workspace/mod>" --id "<resource ID>" --output "<new override-plan.json>"
aow4mod override-apply "<override-plan.json>" --output "<workspace/stages/with-override>"
aow4mod project-use "<workspace>" --source "stages/with-override"
```

The resource index must identify one original resource; `--source-file` can select an exact RPK instead. Plans bind both source files and the resource's exact bytes/locator. Applying adds an exact `0x40` override, preserving existing objects. Then use `edit-plan`/`edit-apply` to change properties, and `project-use` to select that staged directory. No original game/Mod files are overwritten.

Each `project-use` writes rollback history. `project-rollback <workspace> --history <history.json>` restores the prior config/source selection only if both the config and old source still match recorded hashes. Stage files are retained.

## Clone a resource pack and connect it

```powershell
aow4mod clone-rpk-plan "<Mod>" --template "<template.rpk>" --name My_New_Pack.rpk --name-prefix "My - " --output "<new clone-plan.json>"
aow4mod clone-rpk-apply "<clone-plan.json>" --output "<workspace/stages/cloned>"
aow4mod refs "<source ID>" --file "<exact indexed file>"
aow4mod link-plan "<staged ACP or RPK>" --id "<source ID>" --property "<exact path from refs>" --target-id "<new ID>" --target-file "<cloned RPK>" --output "<new link-plan.json>"
aow4mod link-apply "<link-plan.json>" --output "<workspace/stages/connected>"
```

Cloning generates unique 64-bit IDs, retains resource types/categories, rewrites recognized internal links and registers the new RPK in the staged ACP. External assets/localization are reused. Old IDs in unrecognized data block cloning; the tool does not blindly replace binary patterns. ID collision checks cover the supplied index and destination project, not every Mod ever published.

Use repeated `--resource-id <ID>` to select one or more source resources. `--with-local-dependencies` adds their recognized reference closure within that template RPK. Unselected source resources retain their original IDs, and a source-pack import is added when the clone omits resources. If the selected closure includes the entire pack, that extra import is unnecessary. Sparse locator indices are retained; original game packs demonstrate that gaps are valid. Serialized array indices remain contiguous. Categories and unrelated pack metadata are preserved. Selection creates a new RPK; this clone command does not append to an existing pack. Use `resource-copy-plan/apply` for explicit template definitions copied into one destination category; it preserves the target pack and does not merge source category/pack metadata.

The clone plan's `id_map` connects original IDs to new IDs. A clone alone does not wire a game entry point to the new definitions. Add an original-resource override and use `link-plan` on its existing reference as needed. `--target-file` permits an unindexed new RPK; `--old-target-file` disambiguates the previous target. Otherwise both must resolve uniquely in the index.

Link paths may traverse nested arrays/objects; use the exact path emitted by `refs`. ID, label and locator are rebuilt together. Same target type is required by default; `--allow-type-change` is an explicit review-required exception, not an assignability proof. Field-specific target constraints remain unverified. For RPK sources use `--source-root <whole Mod>` at planning time to retain the project tree. All target files are hash-pinned and rechecked before applying.

## New reference elements and imports

```powershell
aow4mod link-array-plan "<own.rpk>" --id "<own ID>" --property "<array path>" --template-file "<original.rpk>" --template-id "<template resource ID>" --template-property "<array element path>" --target-id "<new target ID>" --target-file "<target.rpk>" --old-target-file "<original target.rpk>" --source-root "<whole Mod>" --output "<append-plan.json>"
aow4mod link-array-apply "<append-plan.json>" --output "<new staged Mod>"
aow4mod pack-import-plan "<own.rpk>" --add "OTHER.RPK" --source-root "<whole Mod>" --output "<imports.json>"
aow4mod pack-import-apply "<imports.json>" --output "<new staged Mod>"
```

The array command copies one verified serialized element and replaces its recognized reference. For a wrapper element, `--element-link-path '["0x7"]'` selects the reference inside it while preserving siblings. Source/template/old target/new target hashes are rechecked. Default target types must match. Exact requested keys disambiguate native empty map layouts; ambiguous layouts remain refused.

Both direct and array links from one RPK to another add the selected target pack's import atomically when absent. Same-name, different-data RPK inputs are refused as ambiguous. Only the selected reference's import is managed: opaque wrapper data, other preserved references, dependency cycles and field-specific legality still require `dependencies` and review. Explicit import edits preserve all resource bytes/order and unrelated pack fields; they do not prove that an imported file exists.

The reproducible current-build example is:

```powershell
python "<skill>/scripts/recipes/prepare_cli_incite_example.py" --output "<new project directory>"
```

It pins the reviewed Witch 1.1 native library and two original RPK hashes, refusing a changed build before creating a project. It creates a targeter through the official native constructor, attaches the original City Riot effect, redirects the original Incite Revolution spell, changes research AI priority to `Never (Campaign Only)`, reads all three objects independently, performs native RPK save comparison and builds an allowlisted release. The targeter's only nonmetadata difference from the original is Friendly Fire Type (`Everything`). AI research priority is not proof that AI never casts the spell.

Plans, stages and evidence remain under the new project, with `pipeline.json` showing completion or failure. No existing project is overwritten and a failed pipeline is not automatically retried into the same directory. Current inherited missing voice URI and native asset diagnostics remain review-required. This demonstrates authored entry-point wiring, not effective ACP merge or gameplay. It never installs, activates or publishes.

## Rename, remove or revert a resource

```powershell
aow4mod resource-plan "<Mod>" --id "<ID>" --action rename --name "My internal name" --output "<rename.json>"
aow4mod resource-plan "<Mod>" --id "<ID>" --action remove --output "<remove.json>"
aow4mod resource-plan "<Mod>" --id "<original ID>" --action revert --output "<revert.json>"
aow4mod resource-apply "<plan.json>" --output "<new staged Mod>"
```

Rename changes the internal editor name and recognized reference labels throughout the project, preserving IDs and locators. It does not change localization entries or promise that arbitrary name-based scripts are unaffected. Remove deletes a local RPK definition. Known incoming references block removal until retargeted; `--allow-dangling` explicitly stages a review-required exception. Unrecognized occurrences of the ID block removal even with that flag. External checks cover the supplied index snapshot only, so refresh the index and inspect other dependent Mods when relevant.

Revert removes an ACP override; it does not delete the original resource or invent an unverified engine delete opcode. Existing pack metadata and unrelated resource bytes are preserved. All operations create a separate Mod copy, recheck source hashes and verify the resulting resource set. Duplicate definitions of the selected ID within one project require disambiguation before these operations.

## Rebase an update

```powershell
aow4mod rebase-plan "<previous snapshot-v2.json>" "<current Mod>" --output "<new rebase-plan.json>"
aow4mod rebase-apply "<rebase-plan.json>" --output "<new staged Mod>"
```

Only fields unchanged by the author are copied from the new original. Author/upstream overlaps, metadata changes, new class sections, missing originals and unsupported captures require review. `--allow-partial` explicitly permits applying safe candidates while retaining unresolved findings. Inputs are hash-bound; apply refuses changed originals as well as changed Mod files. Raw copy proposals remain structural validation, not universal semantic proof.

## Localization

For actual MO selection, ZIP/loose loading order, Hyperlink parameters and UI-specific description fields, use [localization checks](localization-checks.md). Successful compilation is a separate claim from runtime language/tooltip behavior.

Existing resource text links can be retargeted with `localized-link-plan/apply`, preserving their parameter objects and verifying native readback. See [the equipment/localization workflow](equipment-workflows.md) for assignment JSON, language-context behavior and a complete staged equipment example.

```powershell
aow4mod loc-lint "<catalog.po>" --limit 10
aow4mod loc-compile "<catalog.po>" --output "<new catalog.mo>" --require-complete
```

`msgid` mode expects IDs in msgid and no context. `--key-mode context` explicitly moves msgctxt IDs into msgid at compilation; the source PO stays unchanged. Context-to-ID plural conversion is rejected. Fuzzy/untranslated entries are reported and omitted by gettext; strict completeness blocks them. `--reference` checks simple brace placeholders against reference text; this is not complete Lingoona grammar validation.

Project `localization` entries contain `source` and `output` relative to the Mod source, optional `key_mode`/`require_complete`, and an optional project-relative `reference`. Compilation writes only into the staged build. PO source files are excluded from the default runtime allowlist.

## Build

```powershell
aow4mod build "<workspace>" --version 1.2 --output "<new release directory>"
aow4mod build "<workspace>" --native --output "<new release with native evidence>"
aow4mod release-check "<release/manifest.json>"
```

Build validates declared/dependent local packs, required previews, compiled localization, the static audit and a bounded resource/asset environment graph. It updates version/description without changing override object bytes. The manifest pins source config, files, external text inputs, payload hashes, audit and environment reports. Reports use committed output paths. Internal edit receipts, backups and source artwork are not implicitly included.

Optional `validation` configuration controls `mods` (dependency Mod paths relative to the project, or explicit absolute paths), `disabled_dlc`, `depth` (default 2), `max_resources` (default 2000), and `index` (default `.aow4mod/validation.sqlite` under this project). The separate cache contains original-game metadata and does not evict the main agent resource/link index. Dependency Mods are inspected but not copied into the payload. `build --steam` adds a read-only Steam DLC eligibility check; it never installs DLC or changes the launcher playset. The current launcher playset is not implicitly used by builds.

`environment.json` records unresolved references, selected/installed content, inherited original findings and loose asset files. Its actionable findings enter the release gate. Localization variants and byte-identical duplicate assets are distinguished from conflicting files. A missing inherited asset is still reviewable: inheritance does not prove the engine ignores it. Resource references satisfied by explicitly selected dependency Mods are recorded as environment resolutions; the original narrower audit remains unchanged for traceability. See [environment-workflows.md](environment-workflows.md).

A localization-only package may pass localization checks while the resource audit remains `not_evaluated`; the release gate states that scope explicitly. Unregistered RPKs and unresolved findings require review. Full CLB/TAM/texture semantics and real gameplay are not claimed.

## Native evidence in builds

New native builds use protocol 7, retaining protocol 3's ACP context checks: every ACP target pack adds an `acp_apply` witness including native dependency collection when needed. Bases resolve from the current original-game index, reachable bundled packs and declared dependency Mods. Different data under the same internal name is ambiguous; `validation.native_merge_bases` maps a pack name to an explicit base file (project-relative or absolute). The internal pack name and captured hash still have to match. Frozen original/prepared/baseline/merged/Mod-data snapshots and local RPK context are stored beside the release, outside runtime payloads. Preparation may append only the declared required/optional lists, preserving exact order and native duplicate behavior; all resources and other metadata must remain unchanged. Publication staging copies and rechecks these artifacts without the installed DLL or cache. Protocols 1–6 retain their historical scopes, while new manifests reject downgraded evidence. Protocol 4 adds local model-link checks; protocol 5 adds automatic TAM file/object witnesses and protocol 6 adds complete batched known-world/cell witnesses and raw logs (see [maps](map-workflows.md)).

`scripts/recipes/prepare_cli_incite_example.py` now creates a native targeter and copies the reviewed, reference-free City Riot leaf into a second local RPK. It removes only that audited leaf clone's conservative Mayhem import, retains other imports, connects the spell/research, checks the Everything value and produces a protocol-3 build. This prevents the original-pack↔new-targeter-pack cycle of the earlier recipe. Original source hashes and the leaf's lack of references/assets are asserted before editing. It is one reviewed spell recipe, with no game test, installation, activation or publication.

`build --native` collects native binary-readback evidence for every bundled ACP (metadata plus each override), RPK and CLB. Alternatively set `validation.native=true`; projects created through the native constructor now start with this setting. Existing imported/template projects retain their earlier policy. `validation.native_cache` defaults to the project-relative `.aow4mod/native`, and `validation.native_timeout` is an integer 1–180 seconds per operation (default 60). Cache paths inside source, dependencies, game or release output are refused.

`native-validation.json` records expected targets, payload hashes, per-check evidence and diagnostics, and an explicit list of files outside this native scope. `native-evidence/` contains object binary bodies, native-saved RPKs, CLBs/YAML and detail reports. It is release-side verification material, excluded from the Workshop payload. Unsupported DLLs, worker failure, data differences and native diagnostic candidates remain review-required. A narrow zero-difference comparison is never upgraded to gameplay or effective ACP-merge certification.

`release-check` verifies the complete target coverage, evidence inventory and hashes, then independently redoes object/RPK/CLB comparisons from the saved binaries. It does not require launching the installed native library again. Rehashing an altered saved binary does not make an old comparison valid. Missing requested evidence, a changed summary or hidden findings are rejected. Publication freezes this evidence beside the upload payload and repeats verification before submitting or resuming an upload.

Protocol 7 adds automatic known material/effect pointer checks and independent file-loaded object witnesses; see [material connections](material-workflows.md).
